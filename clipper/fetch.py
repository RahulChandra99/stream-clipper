"""Everything that talks to yt-dlp: audio, chat replay, and short video sections."""

import json
import subprocess
from pathlib import Path

import yt_dlp
from yt_dlp.utils import download_range_func

QUIET = {"quiet": True, "no_warnings": True, "noprogress": True}


def is_local(source: str) -> bool:
    return Path(source).expanduser().is_file()


def probe(source: str) -> dict:
    """Title, channel and length, without downloading anything."""
    if is_local(source):
        path = Path(source).expanduser()
        dur = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True)
        return {"id": path.stem, "title": path.stem, "channel": None, "channel_handle": None,
                "duration": int(float(dur.stdout.strip())), "url": str(path), "local": True}
    with yt_dlp.YoutubeDL(QUIET) as ydl:
        info = ydl.extract_info(source, download=False)
    return {"id": info.get("id"), "title": info.get("title"),
            "channel": info.get("channel") or info.get("uploader"),
            "channel_handle": info.get("uploader_id"),
            "duration": int(info.get("duration") or 0),
            "url": info.get("webpage_url") or source, "local": False}


def download_audio(meta: dict, work: Path) -> Path:
    """Get the audio as 16 kHz mono wav.

    A four hour stream is a few hundred MB as audio and tens of GB as video,
    so the video is never downloaded in full.
    """
    wav = work / "audio.wav"
    if wav.exists():
        return wav

    if meta["local"]:
        src, temp = Path(meta["url"]), False
    else:
        opts = {**QUIET, "format": "bestaudio[ext=m4a]/bestaudio/best",
                "outtmpl": str(work / "audio_src.%(ext)s")}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(meta["url"], download=True)
            src, temp = Path(ydl.prepare_filename(info)), True

    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
         "-vn", "-ac", "1", "-ar", "16000", str(wav)],
        check=True,
    )
    if temp:
        src.unlink(missing_ok=True)
    return wav


def download_chat(meta: dict, work: Path) -> list[float]:
    """Return the video offset (seconds) of every chat message, or [] if there is no replay.

    Only YouTube exposes chat replay through yt-dlp. Anything else just returns [].
    """
    cache = work / "chat.json"
    if cache.exists():
        return json.loads(cache.read_text())
    if meta["local"]:
        return []
    url = meta["url"]

    opts = {**QUIET, "skip_download": True, "writesubtitles": True,
            "subtitleslangs": ["live_chat"], "outtmpl": str(work / "chat_src")}
    times: list[float] = []
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
        for f in work.glob("chat_src*.json"):
            for line in f.read_text(errors="ignore").splitlines():
                try:
                    ms = json.loads(line)["replayChatItemAction"]["videoOffsetTimeMsec"]
                    times.append(int(ms) / 1000)
                except (KeyError, ValueError, TypeError):
                    continue
            f.unlink()
    except Exception:
        pass
    cache.write_text(json.dumps(times))
    return times


def download_section(meta: dict, start: float, end: float, out: Path) -> Path:
    """Get only [start, end] of the video, at up to 1080p."""
    if out.exists():
        return out
    url = meta["url"]
    if meta["local"]:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start:.2f}", "-i", url,
             "-t", f"{end - start:.2f}", "-c:v", "libx264", "-preset", "veryfast",
             "-crf", "18", "-c:a", "aac", str(out)], check=True)
        return out
    opts = {
        **QUIET,
        "format": "bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]/bv*[height<=1080]+ba/b",
        "merge_output_format": "mp4",
        "download_ranges": download_range_func(None, [(start, end)]),
        "force_keyframes_at_cuts": True,
        "outtmpl": str(out.with_suffix("")) + ".%(ext)s",
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    return out
