"""Decide which moments are worth clipping.

Two stages. `score_window` is a cheap heuristic that ranks every possible
window. Then, optionally, the top of that list goes to Claude (through the
`claude` CLI, so it uses a normal subscription and no API key) to throw out
anything that needs context and to write the hook titles.
"""

import json
import re
import subprocess

import numpy as np

FILLER_OPENERS = {"uh", "um", "so", "yeah", "and", "but", "like", "okay", "ok",
                  "anyway", "alright", "well", "or"}
HYPE = ["no way", "oh my god", "oh my gosh", "what the", "holy", "let's go",
        "are you kidding", "insane", "crazy", "wait", "look at", "can't believe",
        "wow", "dude", "bro", "what is that", "did you see"]


def score_window(text: str, words: list[dict], loud: np.ndarray, chat: np.ndarray) -> float:
    """Score one candidate window. Higher is better.

    loud and chat are per-second z-scores already sliced to this window.
    This is the function to tune. Everything else in the tool is plumbing.
    """
    length = max(words[-1]["end"] - words[0]["start"], 1.0)

    loud_part = float(np.sort(loud)[-5:].mean()) if len(loud) else 0.0
    chat_part = float(chat.max()) if len(chat) else 0.0

    lower = text.lower()
    hype = sum(lower.count(p) for p in HYPE) + 0.3 * (text.count("!") + text.count("?"))
    density = len(words) / length  # words per second, dead air scores low

    opener = re.sub(r"[^a-z']", "", words[0]["word"].lower())
    opener_penalty = 1.0 if opener in FILLER_OPENERS else 0.0

    return (1.0 * loud_part
            + 0.8 * chat_part
            + 0.5 * min(hype, 4.0)
            + 0.5 * min(density / 3.0, 1.0)
            - opener_penalty)


def build_candidates(segments: list[dict], loud: np.ndarray, chat: np.ndarray,
                     min_len: float, max_len: float) -> list[dict]:
    """Every window that starts and ends on a sentence boundary and fits the length limits."""
    out = []
    for i, first in enumerate(segments):
        start = first["start"]
        j = i
        while (j + 1 < len(segments)
               and segments[j + 1]["end"] - start <= max_len
               and segments[j + 1]["start"] - segments[j]["end"] < 4.0):
            j += 1
        end = segments[j]["end"]
        if end - start < min_len:
            continue
        chunk = segments[i: j + 1]
        words = [w for s in chunk for w in s["words"]]
        text = " ".join(s["text"] for s in chunk)
        a, b = int(start), int(end) + 1
        out.append({
            "start": start, "end": end, "text": text, "words": words,
            "score": round(score_window(text, words, loud[a:b], chat[a:b]), 3),
        })
    return out


def keep_non_overlapping(cands: list[dict], count: int) -> list[dict]:
    kept: list[dict] = []
    for c in sorted(cands, key=lambda c: c["score"], reverse=True):
        if all(c["end"] <= k["start"] or c["start"] >= k["end"] for k in kept):
            kept.append(c)
            if len(kept) == count:
                break
    return kept


def fallback_title(text: str) -> str:
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if len(s.split()) >= 3]
    best = max(sentences, key=lambda s: s.count("!") + s.count("?"), default=text)
    return " ".join(best.split()[:7]).rstrip(".,").upper()


PROMPT = """You pick clips from a live stream for YouTube Shorts.
Stream: "{title}" by {channel}.

Below are {n} candidate moments with their transcripts. Be harsh. A good clip
makes sense with zero context, hooks in the first two seconds, and has a
reaction, a discovery, a fail, a joke or a strong opinion. Reject anything that
is filler, needs earlier context, or trails off.

Return ONLY a JSON array, best first, with at most {top} items:
[{{"id": <candidate id>, "title": "<hook title, max 8 words, no emojis>",
  "caption": "<one sentence post caption>", "why": "<short reason>"}}]

Candidates:
{candidates}
"""


def pick_with_claude(pool: list[dict], top: int, meta: dict) -> list[dict] | None:
    listing = "\n\n".join(f"[{i}] ({c['end'] - c['start']:.0f}s) {c['text']}"
                          for i, c in enumerate(pool))
    prompt = PROMPT.format(title=meta.get("title"), channel=meta.get("channel"),
                           n=len(pool), top=top, candidates=listing)
    try:
        res = subprocess.run(["claude", "-p", prompt], capture_output=True, text=True,
                             timeout=300)
        raw = res.stdout
        picks = json.loads(raw[raw.index("["): raw.rindex("]") + 1])
    except (OSError, ValueError, subprocess.TimeoutExpired) as e:
        print(f"  Claude picker failed ({type(e).__name__}), using the heuristic ranking")
        return None

    chosen = []
    for p in picks[:top]:
        if not isinstance(p.get("id"), int) or not 0 <= p["id"] < len(pool):
            continue
        c = dict(pool[p["id"]])
        c.update(title=p.get("title") or fallback_title(c["text"]),
                 caption=p.get("caption", ""), why=p.get("why", ""))
        chosen.append(c)
    return chosen or None


def pick(segments: list[dict], loud: np.ndarray, chat: np.ndarray, meta: dict, *,
         top: int, min_len: float, max_len: float, use_claude: bool) -> list[dict]:
    cands = build_candidates(segments, loud, chat, min_len, max_len)
    if not cands:
        return []
    pool = keep_non_overlapping(cands, top * 5 if use_claude else top)

    chosen = pick_with_claude(pool, top, meta) if use_claude else None
    if chosen is None:
        chosen = pool[:top]
        for c in chosen:
            c["title"] = fallback_title(c["text"])
            c["caption"] = ""
    return chosen
