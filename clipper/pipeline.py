"""The two jobs the CLI and the web app both run: analyse a source, render a clip."""

import re
from pathlib import Path

import numpy as np

from . import fetch, pick, render, signals, transcribe, tts

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "work"
MUSIC = ROOT / "music"
PAD = 12.0  # extra seconds downloaded each side so trims don't need a new download

CLIP_DEFAULTS = {"style": "bold", "layout": "auto", "title_mode": "always",
                 "progress_bar": False, "music": None, "music_vol": 0.15,
                 "vo_text": "", "vo_voice": None, "vo_start": 0.0,
                 "vo_vol": 1.0, "vo_duck": 0.2, "vo_captions": True}


def work_dir(meta: dict) -> Path:
    d = WORK / re.sub(r"[^\w-]", "_", str(meta["id"]))[:60]
    d.mkdir(parents=True, exist_ok=True)
    return d


def analyze(source: str, opts: dict, report=lambda stage, msg="": None) -> tuple[dict, list, list]:
    """Find the best moments. Returns (meta, clips, every transcribed word)."""
    report("probe", "Looking up the video")
    meta = fetch.probe(source)
    work = work_dir(meta)

    report("audio", "Downloading audio")
    audio = signals.load_wav(fetch.download_audio(meta, work))
    duration = len(audio) // signals.SR

    report("signals", "Measuring loudness and chat")
    loud = signals.loudness_per_second(audio)
    chat_times = fetch.download_chat(meta, work)
    chat = signals.chat_rate_per_second(chat_times, duration)
    meta["chat_messages"] = len(chat_times)

    mode = opts.get("transcribe", "auto")
    if mode == "auto":
        mode = "full" if duration <= 40 * 60 else "peaks"
    top, max_len = int(opts.get("top", 3)), float(opts.get("max_len", 50))
    if mode == "full":
        regions = [(0, duration)]
    else:
        smooth = np.convolve(loud + 0.8 * chat, np.ones(15) / 15, mode="same")
        peaks = signals.top_peaks(smooth, count=max(12, top * 6), min_gap=90)
        regions = transcribe.merge_regions(peaks, pad=int(max_len), duration=duration)
    model = opts.get("model", "small.en")
    report("transcript", f"Transcribing {sum(e - s for s, e in regions) // 60 + 1} min of speech")
    segments = transcribe.transcribe(audio, regions, model, work / f"transcript_{mode}_{model}.json")

    report("pick", "Picking the best moments")
    chosen = pick.pick(segments, loud, chat, meta, top=top,
                       min_len=float(opts.get("min_len", 20)), max_len=max_len,
                       use_claude=opts.get("picker", "claude") == "claude")

    defaults = {**CLIP_DEFAULTS, **{k: opts[k] for k in CLIP_DEFAULTS if k in opts}}
    clips = [{"id": f"c{n}", "start": c["start"], "end": c["end"], "score": c["score"],
              "title": c["title"], "caption": c.get("caption", ""), "why": c.get("why", ""),
              "edits": {}, "status": "pending", "version": 0, **defaults}
             for n, c in enumerate(chosen, 1)]
    words = [w for s in segments for w in s["words"]]
    return meta, clips, words


def render_window(clip: dict) -> tuple[float, float]:
    """A little air before the first word and after the last."""
    return max(0.0, clip["start"] - 0.3), clip["end"] + 0.5


def clip_words(all_words: list[dict], clip: dict) -> list[dict]:
    """Words inside the clip, with the user's text edits applied.

    Times are relative to the start of the rendered clip. An edit to "" hides the word.
    """
    t0, _ = render_window(clip)
    out = []
    for w in all_words:
        if w["start"] < clip["start"] - 0.05 or w["end"] > clip["end"] + 0.05:
            continue
        text = clip.get("edits", {}).get(f"{w['start']:.2f}", w["word"])
        if text.strip():
            out.append({"word": text.strip(), "start": w["start"] - t0, "end": w["end"] - t0})
    return out


def ensure_source(meta: dict, clip: dict) -> tuple[Path, float]:
    """Return (video file, offset of the clip's start inside it)."""
    t0, t1 = render_window(clip)
    if meta["local"]:
        return Path(meta["url"]), t0
    work = work_dir(meta)
    src = clip.get("src")
    if not (src and src["t0"] <= t0 and src["t1"] >= t1 and (work / src["file"]).exists()):
        a = max(0.0, t0 - PAD)
        b = min(float(meta["duration"] or t1 + PAD), t1 + PAD)
        name = f"src_{int(a * 10)}_{int(b * 10)}.mp4"
        fetch.download_section(meta, a, b, work / name)
        src = clip["src"] = {"file": name, "t0": a, "t1": b}
    return work / src["file"], t0 - src["t0"]


def credit_text(meta: dict, clip: dict) -> str:
    if "credit" in clip:
        return clip["credit"]
    return f"clip: {meta['channel']}" if meta.get("channel") else ""


def voiceover(clip: dict, clip_len: float) -> dict | None:
    """Generate the voiceover audio for a clip, if it has any text."""
    text = (clip.get("vo_text") or "").strip()
    if not text:
        return None
    wav = tts.synthesize(text, clip.get("vo_voice") or tts.default_voice(), WORK / "voice")
    start = min(max(0.0, float(clip.get("vo_start", 0))), max(0.0, clip_len - 1))
    length = min(tts.duration(wav), clip_len - start)
    return {"file": wav, "start": start, "length": length, "text": text,
            "vol": float(clip.get("vo_vol", 1.0)), "duck": float(clip.get("vo_duck", 0.2))}


def captions_with_voiceover(words: list[dict], vo: dict) -> list[dict]:
    """Swap the streamer's captions for the voiceover's words while the voice speaks."""
    a, b = vo["start"], vo["start"] + vo["length"]
    kept = [w for w in words if w["end"] <= a or w["start"] >= b]
    spoken = [{"word": w["word"], "start": w["start"] + a, "end": w["end"] + a}
              for w in tts.word_times(vo["file"], vo["text"]) if w["start"] < vo["length"]]
    return sorted(kept + spoken, key=lambda w: w["start"])


def render_clip(meta: dict, clip: dict, all_words: list[dict], out: Path,
                on_progress=None) -> None:
    src, offset = ensure_source(meta, clip)
    t0, t1 = render_window(clip)
    music = MUSIC / clip["music"] if clip.get("music") else None
    if music and not music.exists():
        music = None
    words = clip_words(all_words, clip)
    vo = voiceover(clip, t1 - t0)
    if vo and clip.get("vo_captions", True):
        words = captions_with_voiceover(words, vo)
    clip["layout_used"] = render.render(
        src, out, words, clip["title"], credit_text(meta, clip),
        clip.get("layout", "auto"), ss=offset, dur=t1 - t0,
        style=clip.get("style", "bold"), title_mode=clip.get("title_mode", "always"),
        progress_bar=bool(clip.get("progress_bar")), music=music,
        music_vol=float(clip.get("music_vol", 0.15)), vo=vo, on_progress=on_progress)


def description(meta: dict, clip: dict) -> str:
    """Post description: caption, then a credit link to the exact moment."""
    lines = [clip.get("caption") or clip["title"]]
    if not meta["local"]:
        url = meta["url"]
        if "youtu" in url:
            url += f"&t={int(clip['start'])}s"
        lines += ["", f"Clipped from {meta['channel']}: {url}"]
    return "\n".join(lines)
