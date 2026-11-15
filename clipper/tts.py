"""Text to speech for voiceovers.

Uses the macOS `say` command: free, offline, nothing to install. The voices are
not as natural as a neural model. `synthesize` is the only function the rest of
the app calls, so swapping in another engine means changing this file only.
"""

import hashlib
import re
import subprocess
import sys
from pathlib import Path

# say also ships joke voices (Bells, Zarvox, Bad News). These are the usable ones.
GOOD = ["Samantha", "Daniel", "Karen", "Moira", "Rishi", "Tessa", "Aman", "Tara",
        "Reed", "Eddy", "Flo", "Sandy", "Shelley", "Rocko", "Fred", "Albert", "Kathy", "Ralph"]
DEFAULT_VOICE = "Samantha"


def voices() -> list[dict]:
    """Installed English voices as [{"id": name passed to say, "label": shown in the app}]."""
    if sys.platform != "darwin":
        return []
    listing = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
    found: dict[str, dict] = {}
    for line in listing.splitlines():
        m = re.match(r"^(.+?)\s+(en[_-][A-Z]{2})\s+#", line)
        if not m:
            continue
        vid, locale = m.group(1).strip(), m.group(2)
        base = vid.split(" (")[0]
        if base not in GOOD:
            continue
        # "Enhanced" and "Premium" downloads are much better, show them first
        better = "Premium" in vid or "Enhanced" in vid
        label = f"{base}, {locale[-2:]}" + (" (high quality)" if better else "")
        key = f"{base}-{locale}-{better}"
        found.setdefault(key, {"id": vid, "label": label, "rank": (not better, GOOD.index(base))})
    ordered = sorted(found.values(), key=lambda v: v["rank"])
    return [{"id": v["id"], "label": v["label"]} for v in ordered]


def synthesize(text: str, voice: str, out_dir: Path, rate: int = 185) -> Path:
    """Speak `text` to a wav file and return its path. Same text and voice reuse the file."""
    text = " ".join(text.split())
    if not text:
        raise ValueError("Voiceover text is empty")
    if sys.platform != "darwin":
        raise RuntimeError("Voiceover uses the macOS say command and only works on a Mac")
    key = hashlib.sha1(f"{voice}|{rate}|{text}".encode()).hexdigest()[:12]
    out = out_dir / f"vo_{key}.wav"
    if not out.exists():
        out_dir.mkdir(parents=True, exist_ok=True)
        # text goes in as an argument after "--", never through a shell
        subprocess.run(["say", "-v", voice or DEFAULT_VOICE, "-r", str(rate), "-o", str(out),
                        "--data-format=LEI16@44100", "--", text], check=True, timeout=120)
    return out


def duration(wav: Path) -> float:
    res = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(wav)], capture_output=True, text=True, check=True)
    return float(res.stdout.strip())


def spread_words(text: str, start: float, length: float) -> list[dict]:
    """Rough caption timings for the voiceover: time is shared out by word length.

    say gives no word timestamps. Long words take longer to speak, so this lands
    close enough for three-word caption chunks.
    """
    words = text.split()
    total = sum(len(w) + 1 for w in words) or 1
    out, t = [], start
    for w in words:
        span = length * (len(w) + 1) / total
        out.append({"word": w, "start": t, "end": t + span})
        t += span
    return out
