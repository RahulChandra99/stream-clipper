"""Command line entry point: python -m clipper <url or file>"""

import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path

import numpy as np

from . import fetch, pick, render, signals, transcribe

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
    meta = fetch.probe(args.source)
    slug = re.sub(r"[^\w-]", "_", str(meta["id"]))[:60]
    work, out = ROOT / "work" / slug, args.out / slug
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    print(f"{meta['title']} ({meta['duration'] // 60} min) by {meta['channel'] or 'local file'}")
    if not meta["local"]:
        print("Only post clips of creators who allow clipping. See README, 'Before you post'.")

    print("1/5 audio")
    audio = signals.load_wav(fetch.download_audio(meta, work))
    duration = len(audio) // signals.SR

    print("2/5 signals")
    loud = signals.loudness_per_second(audio)
    chat_times = fetch.download_chat(meta, work)
    chat = signals.chat_rate_per_second(chat_times, duration)
    print(f"  chat replay: {len(chat_times)} messages" if chat_times else "  no chat replay")

    print("3/5 transcript")
    mode = args.transcribe
    if mode == "auto":
        mode = "full" if duration <= 40 * 60 else "peaks"
    if mode == "full":
        regions = [(0, duration)]
    else:
        combined = loud + 0.8 * chat
        smooth = np.convolve(combined, np.ones(15) / 15, mode="same")
        peaks = signals.top_peaks(smooth, count=max(12, args.top * 6), min_gap=90)
        regions = transcribe.merge_regions(peaks, pad=int(args.max_len), duration=duration)
    segments = transcribe.transcribe(audio, regions, args.model,
                                     work / f"transcript_{mode}_{args.model}.json")

    print("4/5 picking moments")
    chosen = pick.pick(segments, loud, chat, meta, top=args.top, min_len=args.min_len,
                       max_len=args.max_len, use_claude=use_claude)
    if not chosen:
        sys.exit("No usable moments found. Try --min 10 or --transcribe full.")

    print("5/5 rendering")
    credit = args.credit or (f"clip: {meta['channel']}" if meta["channel"] else "")
    for n, c in enumerate(chosen, 1):
        start, end = max(0.0, c["start"] - 0.4), c["end"] + 0.6
        src = fetch.download_section(meta, start, end, work / f"src_{int(start)}.mp4")
        words = [{"word": w["word"], "start": w["start"] - start, "end": w["end"] - start}
                 for w in c["words"]]
        mp4 = out / f"clip_{n:02d}.mp4"
        used = render.render(src, mp4, words, c["title"], credit, args.layout)

        lines = [c["title"], "", c.get("caption") or c["title"].capitalize()]
        if not meta["local"]:
            lines += ["", f"Clipped from {meta['channel']}: {meta['url']}&t={int(start)}s"
                      if "youtu" in meta["url"] else f"Clipped from {meta['channel']}: {meta['url']}"]
        (out / f"clip_{n:02d}.txt").write_text("\n".join(lines) + "\n")
        print(f"  {mp4.name}  {end - start:.0f}s  {used:5}  {c['title']}")

    (out / "clips.json").write_text(json.dumps(
        [{k: c.get(k) for k in ("start", "end", "score", "title", "caption", "why", "text")}
         for c in chosen], indent=2))
    print(f"Done in {time.time() - t0:.0f}s. Clips are in {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
