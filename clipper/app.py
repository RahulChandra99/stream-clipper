"""Local web app. Run with: python -m clipper.app

Single user, on this machine only. Projects are JSON files on disk, jobs run
one at a time on a background thread. No database, no accounts.
"""

import json
import queue
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import segno
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

from . import pipeline
from .render import STYLES

PROJECTS = pipeline.ROOT / "projects"
UPLOADS = PROJECTS / "_uploads"
WEB = Path(__file__).parent / "web"
PORT = 8787
EDITABLE = {"title", "caption", "start", "end", "style", "layout", "title_mode",
            "progress_bar", "music", "music_vol", "edits", "credit"}

app = FastAPI(title="stream-clipper")
lock = threading.RLock()
jobs: "queue.Queue" = queue.Queue()


# ---------- project storage ----------

def pdir(pid: str) -> Path:
    if not pid.isalnum():
        raise HTTPException(400, "bad project id")
    return PROJECTS / pid


def load(pid: str) -> dict:
    f = pdir(pid) / "project.json"
    if not f.exists():
        raise HTTPException(404, "project not found")
    return json.loads(f.read_text())


def save(project: dict) -> None:
    with lock:
        f = pdir(project["id"]) / "project.json"
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(project, indent=1))
        tmp.replace(f)


def load_words(pid: str) -> list[dict]:
    f = pdir(pid) / "words.json"
    return json.loads(f.read_text()) if f.exists() else []


def find_clip(project: dict, cid: str) -> dict:
    for c in project["clips"]:
        if c["id"] == cid:
            return c
    raise HTTPException(404, "clip not found")


def update(pid: str, fn) -> dict:
    """Read, change and write a project under the lock."""
    with lock:
        project = load(pid)
        fn(project)
        save(project)
        return project


# ---------- background jobs ----------

def worker() -> None:
    while True:
        fn = jobs.get()
        try:
            fn()
        except Exception:
            traceback.print_exc()


def set_job(pid: str, **fields) -> None:
    update(pid, lambda p: p["job"].update(fields))


def job_analyze(pid: str) -> None:
    project = load(pid)
    try:
        meta, clips, words = pipeline.analyze(
            project["source"], project["opts"],
            lambda stage, msg="": set_job(pid, state="running", stage=stage, message=msg))
        (pdir(pid) / "words.json").write_text(json.dumps(words))

        def done(p):
            p.update(meta=meta, clips=clips)
            p["job"].update(stage="render", message="Rendering clips")
        update(pid, done)
        if not clips:
            raise RuntimeError("No usable moments found. Try a shorter minimum length.")
        for c in clips:
            job_render(pid, c["id"])
        set_job(pid, state="done", stage="done", message="")
    except Exception as e:
        traceback.print_exc()
        set_job(pid, state="error", message=str(e)[:300])


def job_render(pid: str, cid: str) -> None:
    project = load(pid)
    clip = find_clip(project, cid)

    def mark(**fields):
        update(pid, lambda p: find_clip(p, cid).update(fields))

    mark(status="rendering", progress=0, error=None)
    try:
        version = clip.get("version", 0) + 1
        out = pdir(pid) / f"{cid}_v{version}.mp4"
        last = [0.0]

        def on_progress(frac):
            if time.time() - last[0] > 0.7:
                last[0] = time.time()
                mark(progress=round(frac, 2))

        pipeline.render_clip(project["meta"], clip, load_words(pid), out, on_progress)
        for old in pdir(pid).glob(f"{cid}_v*.mp4"):
            if old != out:
                old.unlink()
        mark(status="ready", dirty=False, version=version, file=out.name, progress=1,
             src=clip.get("src"), layout_used=clip.get("layout_used"))
    except Exception as e:
        traceback.print_exc()
        mark(status="error", error=str(e)[:300])


# ---------- API ----------

@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (WEB / "index.html").read_text()


@app.get("/api/config")
def config() -> dict:
    pipeline.MUSIC.mkdir(exist_ok=True)
    tracks = sorted(f.name for f in pipeline.MUSIC.iterdir()
                    if f.suffix.lower() in {".mp3", ".m4a", ".wav", ".aac", ".ogg"})
    return {"styles": {k: v["label"] for k, v in STYLES.items()}, "music": tracks,
            "music_dir": str(pipeline.MUSIC), "claude": bool(shutil.which("claude"))}


@app.get("/api/projects")
def list_projects() -> list:
    out = []
    for f in PROJECTS.glob("*/project.json"):
        p = json.loads(f.read_text())
        ready = [c for c in p["clips"] if c.get("file")]
        out.append({"id": p["id"], "created": p["created"], "source": p["source"],
                    "title": (p.get("meta") or {}).get("title") or p["source"],
                    "channel": (p.get("meta") or {}).get("channel"),
                    "clips": len(p["clips"]), "job": p["job"],
                    "thumb": f"/media/{p['id']}/{ready[0]['file']}" if ready else None})
    return sorted(out, key=lambda p: p["created"], reverse=True)


@app.post("/api/projects")
async def create_project(req: Request) -> dict:
    body = await req.json()
    source = str(body.get("source", "")).strip()
    if not source:
        raise HTTPException(400, "Paste a link or drop a video file")
    is_url = source.startswith(("http://", "https://"))
    if is_url and not body.get("rights"):
        raise HTTPException(400, "Confirm you own this or have permission to clip it")
    if not is_url and not Path(source).expanduser().is_file():
        raise HTTPException(400, "That is not a link or a file on this Mac")

    pid = time.strftime("%Y%m%d%H%M%S") + secrets.token_hex(2)
    opts = {k: body[k] for k in ("top", "min_len", "max_len", "picker", "style", "layout")
            if k in body}
    project = {"id": pid, "created": time.time(), "source": source, "opts": opts,
               "meta": None, "clips": [],
               "job": {"state": "running", "stage": "queued", "message": "Waiting to start"}}
    pdir(pid).mkdir(parents=True)
    save(project)
    jobs.put(lambda: job_analyze(pid))
    return project


@app.put("/api/upload")
async def upload(req: Request, name: str) -> dict:
    """Receive a dropped file as a raw request body and keep it in projects/_uploads."""
    UPLOADS.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch for ch in Path(name).name if ch.isalnum() or ch in "._- ") or "video.mp4"
    dest = UPLOADS / f"{secrets.token_hex(3)}_{safe}"
    with dest.open("wb") as f:
        async for chunk in req.stream():
            f.write(chunk)
    return {"path": str(dest)}


@app.get("/api/projects/{pid}")
def get_project(pid: str) -> dict:
    project = load(pid)
    words = load_words(pid)
    for c in project["clips"]:
        c["words"] = [{"t": f"{w['start']:.2f}", "word": w["word"]} for w in words
                      if w["start"] >= c["start"] - 0.05 and w["end"] <= c["end"] + 0.05]
        if project.get("meta"):
            c["description"] = pipeline.description(project["meta"], c)
    return project


@app.delete("/api/projects/{pid}")
def delete_project(pid: str) -> dict:
    load(pid)
    shutil.rmtree(pdir(pid))
    return {"ok": True}


@app.patch("/api/projects/{pid}/clips/{cid}")
async def patch_clip(pid: str, cid: str, req: Request) -> dict:
    body = await req.json()
    changes = {k: v for k, v in body.items() if k in EDITABLE}

    def apply(p):
        clip = find_clip(p, cid)
        if "start" in changes or "end" in changes:
            start = float(changes.get("start", clip["start"]))
            end = float(changes.get("end", clip["end"]))
            if start < 0 or end - start < 3 or end - start > 180:
                raise HTTPException(400, "A clip has to be between 3 and 180 seconds")
        # caption and description are metadata, they don't change the video
        if any(k not in ("caption",) and clip.get(k) != v for k, v in changes.items()):
            clip["dirty"] = True
        clip.update(changes)

    update(pid, apply)
    return get_project(pid)


@app.post("/api/projects/{pid}/clips/{cid}/render")
def render_clip(pid: str, cid: str) -> dict:
    update(pid, lambda p: find_clip(p, cid).update(status="queued"))
    jobs.put(lambda: job_render(pid, cid))
    return {"ok": True}


@app.delete("/api/projects/{pid}/clips/{cid}")
def delete_clip(pid: str, cid: str) -> dict:
    def apply(p):
        find_clip(p, cid)
        p["clips"] = [c for c in p["clips"] if c["id"] != cid]
    update(pid, apply)
    for f in pdir(pid).glob(f"{cid}_v*.mp4"):
        f.unlink()
    return {"ok": True}


@app.get("/media/{pid}/{name}")
def media(pid: str, name: str):
    f = pdir(pid) / Path(name).name
    if not f.exists() or f.suffix != ".mp4":
        raise HTTPException(404)
    return FileResponse(f, media_type="video/mp4")


def clip_file(pid: str, cid: str) -> Path:
    clip = find_clip(load(pid), cid)
    if not clip.get("file"):
        raise HTTPException(400, "Render the clip first")
    return pdir(pid) / clip["file"]


@app.post("/api/projects/{pid}/clips/{cid}/reveal")
def reveal(pid: str, cid: str) -> dict:
    """Select the clip in Finder so it can be dragged into an upload page or AirDropped."""
    f = clip_file(pid, cid)
    if sys.platform == "darwin":
        subprocess.run(["open", "-R", str(f)], check=False)
    return {"path": str(f)}


# ---------- send to phone ----------
# The app itself only listens on 127.0.0.1. Sharing starts a second, tiny server
# on the LAN that serves exactly one file behind a random token for ten minutes.

share_server: ThreadingHTTPServer | None = None


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


@app.post("/api/projects/{pid}/clips/{cid}/share")
def share(pid: str, cid: str) -> dict:
    global share_server
    f = clip_file(pid, cid)
    token = secrets.token_urlsafe(12)
    if share_server:
        share_server.shutdown()
        share_server.server_close()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != f"/{token}/clip.mp4":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(f.stat().st_size))
            self.send_header("Content-Disposition", 'attachment; filename="clip.mp4"')
            self.end_headers()
            with f.open("rb") as fh:
                shutil.copyfileobj(fh, self.wfile)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
    share_server = server
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def expire():
        global share_server
        if share_server is server:
            server.shutdown()
            server.server_close()
            share_server = None
    timer = threading.Timer(600, expire)
    timer.daemon = True
    timer.start()

    url = f"http://{lan_ip()}:{server.server_address[1]}/{token}/clip.mp4"
    qr = segno.make(url, error="m").svg_inline(scale=5, dark="#111", light="#fff", border=2)
    return {"url": url, "qr": qr, "minutes": 10}


def main() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            sys.exit(f"{tool} not found. Install it with: brew install ffmpeg")
    PROJECTS.mkdir(exist_ok=True)
    # a job that was running when the app last quit is never going to finish
    for f in PROJECTS.glob("*/project.json"):
        p = json.loads(f.read_text())
        changed = False
        if p["job"].get("state") == "running":
            p["job"].update(state="error", message="Interrupted when the app was closed")
            changed = True
        for c in p["clips"]:
            if c.get("status") in ("rendering", "queued"):
                c["status"] = "ready" if c.get("file") else "pending"
                changed = True
        if changed:
            f.write_text(json.dumps(p, indent=1))

    threading.Thread(target=worker, daemon=True).start()
    if "--no-open" not in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()
    print(f"stream-clipper is running at http://127.0.0.1:{PORT}  (Ctrl+C to stop)")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
