"""Local speech to text with word timestamps (faster-whisper, runs on CPU)."""

import json
from pathlib import Path

import numpy as np

from .signals import SR


def transcribe(audio: np.ndarray, regions: list[tuple[int, int]], model_name: str,
               cache: Path) -> list[dict]:
    """Transcribe the given (start, end) second ranges.

    Returns segments like {"start", "end", "text", "words": [{"start", "end", "word"}]}
    with times in seconds from the start of the stream.
    """
    if cache.exists():
        return json.loads(cache.read_text())

    from faster_whisper import WhisperModel

    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments: list[dict] = []
    for n, (start, end) in enumerate(regions, 1):
        print(f"  transcribing {start}s to {end}s ({n}/{len(regions)})", flush=True)
        piece = audio[start * SR: end * SR]
        result, _ = model.transcribe(piece, word_timestamps=True, vad_filter=True,
                                     language="en", beam_size=1)
        for seg in result:
            words = [{"start": round(w.start + start, 2), "end": round(w.end + start, 2),
                      "word": w.word.strip()} for w in (seg.words or [])]
            if not words:
                continue
            segments.append({"start": words[0]["start"], "end": words[-1]["end"],
                             "text": seg.text.strip(), "words": words})

    cache.write_text(json.dumps(segments))
    return segments


def merge_regions(peaks: list[int], pad: int, duration: int) -> list[tuple[int, int]]:
    """Turn peak seconds into padded, non-overlapping (start, end) ranges."""
    regions: list[tuple[int, int]] = []
    for p in sorted(peaks):
        s, e = max(0, p - pad), min(duration, p + pad)
        if regions and s <= regions[-1][1]:
            regions[-1] = (regions[-1][0], e)
        else:
            regions.append((s, e))
    return regions
