"""Cheap signals that say "something happened here" without understanding the words."""

import wave
from pathlib import Path

import numpy as np

SR = 16000


def load_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        assert w.getframerate() == SR and w.getnchannels() == 1
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def zscore(x: np.ndarray) -> np.ndarray:
    if len(x) == 0:
        return x
    sd = x.std()
    return (x - x.mean()) / sd if sd > 1e-9 else np.zeros_like(x)


def loudness_per_second(audio: np.ndarray) -> np.ndarray:
    """Loudness in dB for each second, as a z-score against the whole stream.

    A streamer who shouts all stream has a high mean, so only the moments that
    are loud *for them* stand out.
    """
    n = len(audio) // SR
    if n == 0:
        return np.zeros(0)
    chunks = audio[: n * SR].reshape(n, SR)
    rms = np.sqrt((chunks ** 2).mean(axis=1) + 1e-10)
    return zscore(20 * np.log10(rms))


def chat_rate_per_second(times: list[float], duration: int, window: int = 10) -> np.ndarray:
    """Chat messages per second, smoothed over `window` seconds, as a z-score.

    Chat reacts a few seconds after the moment, so the result is shifted back
    by half the window.
    """
    if not times or duration <= 0:
        return np.zeros(max(duration, 0))
    counts = np.bincount(np.clip(np.array(times, dtype=int), 0, duration - 1),
                         minlength=duration).astype(np.float32)
    smooth = np.convolve(counts, np.ones(window) / window, mode="same")
    shifted = np.roll(smooth, -window // 2)
    shifted[-window // 2:] = 0
    return zscore(shifted)


def top_peaks(signal: np.ndarray, count: int, min_gap: int = 60) -> list[int]:
    """Indices of the `count` highest points that are at least `min_gap` apart."""
    order = np.argsort(signal)[::-1]
    picked: list[int] = []
    for i in order:
        if all(abs(int(i) - p) >= min_gap for p in picked):
            picked.append(int(i))
            if len(picked) == count:
                break
    return sorted(picked)
