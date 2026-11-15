"""Text to speech for voiceovers.

Two engines. Kokoro is a small neural model that runs locally and sounds
natural; it needs two files in models/ (see README). If they're missing the
app falls back to the macOS `say` command, which needs nothing but sounds like
a screen reader.

Voice ids are "kokoro:am_michael" or a bare `say` voice name.
"""

import difflib
import hashlib
import json
import re
import subprocess
import sys
import threading
import wave
from pathlib import Path

import numpy as np

MODELS = Path(__file__).resolve().parent.parent / "models"
KOKORO_MODEL = MODELS / "kokoro-v1.0.onnx"
KOKORO_VOICES = MODELS / "voices-v1.0.bin"

# Best-sounding Kokoro voices first. The rest follow alphabetically.
KOKORO_FIRST = ["af_heart", "am_michael", "af_bella", "am_fenrir", "bm_george", "bf_emma",
                "am_puck", "af_nicole"]
KOKORO_KIND = {"af": "US female", "am": "US male", "bf": "UK female", "bm": "UK male"}

# say also ships joke voices (Bells, Zarvox, Bad News). These are the usable ones.
SAY_GOOD = ["Samantha", "Daniel", "Karen", "Moira", "Rishi", "Tessa", "Aman", "Tara"]

_lock = threading.Lock()
_kokoro = None
_whisper = None


def kokoro():
    """The loaded Kokoro model, or None if its files or package are missing."""
    global _kokoro
    if _kokoro is None and KOKORO_MODEL.exists() and KOKORO_VOICES.exists():
        try:
            from kokoro_onnx import Kokoro
            _kokoro = Kokoro(str(KOKORO_MODEL), str(KOKORO_VOICES))
        except Exception as e:  # a broken install shouldn't take the whole app down
            print(f"Kokoro could not be loaded, using Mac voices: {e}")
    return _kokoro


def say_voices() -> list[dict]:
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
        if base in SAY_GOOD:
            found.setdefault(base, {"id": vid, "label": f"{base}, {locale[-2:]} (Mac, robotic)",
                                    "rank": SAY_GOOD.index(base)})
    return [{"id": v["id"], "label": v["label"]} for v in sorted(found.values(), key=lambda v: v["rank"])]


def voices() -> list[dict]:
    """Every usable English voice as [{"id", "label"}], best first."""
    out = []
    k = kokoro()
    if k:
        names = [v for v in k.get_voices() if v[:2] in KOKORO_KIND]
        names.sort(key=lambda v: (KOKORO_FIRST.index(v) if v in KOKORO_FIRST else 99, v))
        out += [{"id": f"kokoro:{v}", "label": f"{v[3:].capitalize()}, {KOKORO_KIND[v[:2]]}"}
                for v in names]
    return out + say_voices()


def default_voice() -> str:
    vs = voices()
    return vs[0]["id"] if vs else "Samantha"


def write_wav(path: Path, samples: np.ndarray, rate: int) -> None:
    pcm = (np.clip(samples, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


def synthesize(text: str, voice: str, out_dir: Path) -> Path:
    """Speak `text` to a wav file and return its path. Same text and voice reuse the file."""
    text = " ".join(text.split())
    if not text:
        raise ValueError("Voiceover text is empty")
    voice = voice or default_voice()
    key = hashlib.sha1(f"{voice}|{text}".encode()).hexdigest()[:12]
    out = out_dir / f"vo_{key}.wav"
    if out.exists():
        return out
    out_dir.mkdir(parents=True, exist_ok=True)

    if voice.startswith("kokoro:"):
        k = kokoro()
        if not k:
            raise RuntimeError("The Kokoro voice model is not installed. See README, 'Voices'.")
        with _lock:
            samples, rate = k.create(text, voice=voice.split(":", 1)[1], speed=1.05)
        write_wav(out, samples, rate)
    else:
        if sys.platform != "darwin":
            raise RuntimeError("Mac voices only work on a Mac")
        # text goes in as an argument after "--", never through a shell
        subprocess.run(["say", "-v", voice, "-r", "185", "-o", str(out),
                        "--data-format=LEI16@44100", "--", text], check=True, timeout=120)
    return out


def duration(wav: Path) -> float:
    with wave.open(str(wav), "rb") as w:
        return w.getnframes() / w.getframerate()


# ---------- caption timing ----------

def spread_words(words: list[str], start: float, length: float) -> list[dict]:
    """Share a stretch of time between words by their length. Used to fill gaps."""
    total = sum(len(w) + 1 for w in words) or 1
    out, t = [], start
    for w in words:
        span = length * (len(w) + 1) / total
        out.append({"word": w, "start": t, "end": t + span})
        t += span
    return out


def norm(word: str) -> str:
    return re.sub(r"[^a-z0-9]", "", word.lower())


def heard_words(wav: Path) -> list[dict]:
    """Run Whisper over the voiceover to find when each word is actually spoken."""
    global _whisper
    with wave.open(str(wav), "rb") as w:
        rate = w.getframerate()
        audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    # Whisper wants 16 kHz
    n = int(len(audio) * 16000 / rate)
    audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)
    with _lock:
        if _whisper is None:
            from faster_whisper import WhisperModel
            _whisper = WhisperModel("small.en", device="cpu", compute_type="int8")
        segments, _ = _whisper.transcribe(audio, word_timestamps=True, language="en", beam_size=1)
        return [{"word": w.word.strip(), "start": w.start, "end": w.end}
                for s in segments for w in (s.words or [])]


def word_times(wav: Path, text: str) -> list[dict]:
    """When each word of `text` is spoken in `wav`, in seconds from the start of the file.

    The words shown are the ones typed, not Whisper's spelling. Whisper only
    supplies the timing. Words it missed or misheard get a share of the gap
    between their matched neighbours.
    """
    cache = wav.with_suffix(".words.json")
    if cache.exists():
        return json.loads(cache.read_text())
    typed = text.split()
    total = duration(wav)
    try:
        heard = heard_words(wav)
    except Exception as e:
        print(f"Voiceover alignment failed, spreading words evenly: {e}")
        heard = []

    out: list[dict | None] = [None] * len(typed)
    matcher = difflib.SequenceMatcher(None, [norm(w) for w in typed], [norm(h["word"]) for h in heard],
                                      autojunk=False)
    for block in matcher.get_matching_blocks():
        for i in range(block.size):
            h = heard[block.b + i]
            out[block.a + i] = {"word": typed[block.a + i], "start": h["start"], "end": h["end"]}

    # fill runs of unmatched words from the time between their matched neighbours
    i = 0
    while i < len(typed):
        if out[i] is not None:
            i += 1
            continue
        j = i
        while j < len(typed) and out[j] is None:
            j += 1
        left = out[i - 1]["end"] if i else 0.0
        right = out[j]["start"] if j < len(typed) else total
        out[i:j] = spread_words(typed[i:j], left, max(0.05, right - left))
        i = j

    cache.write_text(json.dumps(out))
    return out
