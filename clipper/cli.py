"""Command line entry point: python -m clipper <url or file>"""

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

from . import pipeline

ROOT = Path(__file__).resolve().parent.parent


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="clipper",
        description="Find the best moments in a stream and render them as vertical clips.")
    p.add_argument("source", help="YouTube/Twitch/Kick URL, or a local video file")
    p.add_argument("--top", type=int, default=3, help="how many clips to make (default 3)")
    p.add_argument("--min", type=float, default=20, dest="min_len", help="shortest clip in seconds")
    p.add_argument("--max", type=float, default=50, dest="max_len", help="longest clip in seconds")
    p.add_argument("--picker", choices=["claude", "heuristic"], default="claude",
                   help="claude reranks and writes titles with the claude CLI; heuristic is offline")
    p.add_argument("--transcribe", choices=["auto", "full", "peaks"], default="auto",
                   help="full transcribes everything; peaks only the loud/busy parts. "
                        "auto uses full under 40 minutes")
    p.add_argument("--model", default="small.en", help="faster-whisper model (default small.en)")
    p.add_argument("--layout", choices=["auto", "stack", "crop", "blur"], default="auto")
    p.add_argument("--style", choices=["bold", "neon", "box", "minimal", "lower", "none"],
                   default="bold", help="caption preset")
    p.add_argument("--credit", help="credit text on the clip (default: the source channel)")
    p.add_argument("--out", type=Path, default=ROOT / "out")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            sys.exit(f"{tool} not found. Install it with: brew install ffmpeg")
    use_claude = args.picker == "claude"
    if use_claude and not shutil.which("claude"):
        print("claude CLI not found, falling back to the heuristic picker")
        use_claude = False

    t0 = time.time()
    opts = {"top": args.top, "min_len": args.min_len, "max_len": args.max_len,
            "picker": "claude" if use_claude else "heuristic", "transcribe": args.transcribe,
            "model": args.model, "layout": args.layout, "style": args.style}
    meta, clips, words = pipeline.analyze(
        args.source, opts, lambda stage, msg="": print(f"{stage}: {msg}", flush=True))
    if not clips:
        sys.exit("No usable moments found. Try --min 10 or --transcribe full.")
    if not meta["local"]:
        print("Only post clips of creators who allow clipping. See README, 'Before you post'.")

    out = args.out / pipeline.work_dir(meta).name
    out.mkdir(parents=True, exist_ok=True)
    for n, c in enumerate(clips, 1):
        if args.credit is not None:
            c["credit"] = args.credit
        mp4 = out / f"clip_{n:02d}.mp4"
        pipeline.render_clip(meta, c, words, mp4)
        (out / f"clip_{n:02d}.txt").write_text(
            c["title"] + "\n\n" + pipeline.description(meta, c) + "\n")
        print(f"  {mp4.name}  {c['end'] - c['start']:.0f}s  {c['layout_used']:5}  {c['title']}")

    (out / "clips.json").write_text(json.dumps(
        [{k: c.get(k) for k in ("start", "end", "score", "title", "caption", "why")}
         for c in clips], indent=2))
    print(f"Done in {time.time() - t0:.0f}s. Clips are in {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
