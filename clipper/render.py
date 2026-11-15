"""Turn a horizontal source clip into a 1080x1920 short with a title and word captions.

Homebrew's ffmpeg ships without libass and drawtext, so text can't be burned in
with a filter. ffmpeg does the layout and streams raw frames to Python, Pillow
draws the text, and a second ffmpeg encodes the result.
"""

import subprocess
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

W, H, FPS = 1080, 1920, 30
TOP_H = 640  # facecam panel height in the stacked layout
FONT_PATHS = ["/System/Library/Fonts/Supplemental/Impact.ttf",
              "/System/Library/Fonts/Supplemental/Arial Black.ttf",
              "C:/Windows/Fonts/impact.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
YELLOW, WHITE, BLACK = (255, 230, 0), (255, 255, 255), (0, 0, 0)


def font(size: int) -> ImageFont.FreeTypeFont:
    for p in FONT_PATHS:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size)


def even(x: float) -> int:
    return int(x) // 2 * 2


# ---------- layout ----------

def find_face(src: Path) -> tuple[tuple[int, int, int, int] | None, int, int]:
    """Look for a face that stays in the same place, which is what a facecam is.

    Returns ((x, y, w, h) or None, frame_width, frame_height).
    """
    cap = cv2.VideoCapture(str(src))
    fw, fh = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

    samples = 15
    buckets: dict[tuple[int, int], list] = {}
    for i in range(samples):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * (i + 0.5) / samples))
        ok, frame = cap.read()
        if not ok:
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        for (x, y, w, h) in cascade.detectMultiScale(gray, 1.1, 6, minSize=(fh // 18, fh // 18)):
            key = (int((x + w / 2) / fw * 8), int((y + h / 2) / fh * 8))
            buckets.setdefault(key, []).append((x, y, w, h))
    cap.release()

    if not buckets:
        return None, fw, fh
    best = max(buckets.values(), key=len)
    if len(best) < samples * 0.4:  # a face that only shows up now and then is in-game
        return None, fw, fh
    x, y, w, h = (int(v) for v in np.median(np.array(best), axis=0))
    return (x, y, w, h), fw, fh


def layout_filter(layout: str, face, fw: int, fh: int) -> tuple[str, str]:
    """Return (ffmpeg filter graph, layout actually used)."""
    if layout == "auto":
        if face is None:
            layout = "blur"
        elif face[2] / fw < 0.18:
            layout = "stack"   # small fixed face in a corner: a facecam
        else:
            layout = "crop"    # big face: a talking head, just follow it

    if layout == "stack" and face is not None:
        x, y, w, h = face
        aspect = W / TOP_H
        cw = even(min(fw, w * 3.2, fh * aspect))
        ch = even(cw / aspect)
        cx = even(min(max(x + w / 2 - cw / 2, 0), fw - cw))
        cy = even(min(max(y + h * 0.6 - ch / 2, 0), fh - ch))
        gw = even(min(fw, fh * W / (H - TOP_H)))
        gx = even((fw - gw) / 2)
        return (f"[0:v]split=2[a][b];"
                f"[a]crop={cw}:{ch}:{cx}:{cy},scale={W}:{TOP_H}[top];"
                f"[b]crop={gw}:{fh}:{gx}:0,scale={W}:{H - TOP_H}[bot];"
                f"[top][bot]vstack,fps={FPS},format=rgb24[v]"), "stack"

    if layout == "crop" and face is not None:
        x, _, w, _ = face
        cw = even(min(fw, fh * W / H))
        cx = even(min(max(x + w / 2 - cw / 2, 0), fw - cw))
        return f"[0:v]crop={cw}:{fh}:{cx}:0,scale={W}:{H},fps={FPS},format=rgb24[v]", "crop"

    return (f"[0:v]split=2[a][b];"
            f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=20:2[bg];"
            f"[b]scale={W}:-2[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,fps={FPS},format=rgb24[v]"), "blur"


# ---------- text overlays ----------

class Overlay:
    """An RGBA image plus where its top-left corner goes on the frame."""

    def __init__(self, img: Image.Image, cx: int, cy: int):
        arr = np.asarray(img, dtype=np.uint16)
        self.rgb, self.alpha = arr[..., :3], arr[..., 3:4]
        self.h, self.w = arr.shape[:2]
        self.x = max(0, min(W - self.w, cx - self.w // 2))
        self.y = max(0, min(H - self.h, cy - self.h // 2))

    def draw(self, frame: np.ndarray) -> None:
        region = frame[self.y:self.y + self.h, self.x:self.x + self.w]
        blended = (region.astype(np.uint16) * (255 - self.alpha) + self.rgb * self.alpha) // 255
        region[:] = blended.astype(np.uint8)


def wrap(words: list[str], f: ImageFont.FreeTypeFont, max_w: int) -> list[list[int]]:
    """Greedy line wrap. Returns lines as lists of word indices."""
    lines: list[list[int]] = [[]]
    for i, w in enumerate(words):
        trial = " ".join(words[j] for j in lines[-1] + [i])
        if lines[-1] and f.getlength(trial) > max_w:
            lines.append([i])
        else:
            lines[-1].append(i)
    return lines


def caption_image(words: list[str], active: int) -> Image.Image:
    f, stroke, pad = font(92), 8, 16
    lines = wrap(words, f, W - 140)
    space = f.getlength(" ")
    line_h = f.size + 18
    width = int(max(f.getlength(" ".join(words[i] for i in ln)) for ln in lines)) + 2 * (pad + stroke)
    img = Image.new("RGBA", (width, line_h * len(lines) + 2 * pad), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for row, ln in enumerate(lines):
        x = (width - f.getlength(" ".join(words[i] for i in ln))) / 2
        for i in ln:
            d.text((x, pad + row * line_h), words[i], font=f,
                   fill=YELLOW if i == active else WHITE,
                   stroke_width=stroke, stroke_fill=BLACK)
            x += f.getlength(words[i]) + space
    return img


def title_image(title: str) -> Image.Image:
    f, pad = font(60), 28
    words = title.split()
    lines = [" ".join(words[i] for i in ln) for ln in wrap(words, f, W - 220)]
    line_h = f.size + 14
    width = int(max(f.getlength(ln) for ln in lines)) + 2 * pad
    img = Image.new("RGBA", (width, line_h * len(lines) + 2 * pad - 14), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, img.width - 1, img.height - 1), radius=26, fill=(255, 255, 255, 240))
    for row, ln in enumerate(lines):
        d.text(((width - f.getlength(ln)) / 2, pad - 8 + row * line_h), ln, font=f, fill=BLACK)
    return img


def credit_image(text: str) -> Image.Image:
    f = font(34)
    img = Image.new("RGBA", (int(f.getlength(text)) + 16, f.size + 16), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((8, 4), text, font=f, fill=(255, 255, 255, 200),
                             stroke_width=3, stroke_fill=(0, 0, 0, 200))
    return img


def caption_chunks(words: list[dict], max_words: int = 3) -> list[list[dict]]:
    """Group words into short on-screen chunks, breaking on pauses and sentence ends."""
    chunks: list[list[dict]] = [[]]
    for w in words:
        cur = chunks[-1]
        if cur and (len(cur) >= max_words or w["start"] - cur[-1]["end"] > 0.6
                    or cur[-1]["word"][-1:] in ".!?"):
            chunks.append([])
        chunks[-1].append(w)
    return [c for c in chunks if c]


# ---------- render ----------

def render(src: Path, out: Path, words: list[dict], title: str, credit: str,
           layout: str = "auto") -> str:
    """words have times relative to the start of `src`. Returns the layout used."""
    face, fw, fh = find_face(src)
    graph, used = layout_filter(layout, face, fw, fh)
    caption_y = TOP_H if used == "stack" else int(H * 0.68)

    static = [Overlay(title_image(title), W // 2, 190),
              Overlay(credit_image(credit), W // 2, H - 90)]
    chunks = caption_chunks(words)
    cache: dict[tuple[int, int], Overlay] = {}

    decode = subprocess.Popen(
        ["ffmpeg", "-loglevel", "error", "-i", str(src), "-filter_complex", graph,
         "-map", "[v]", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        stdout=subprocess.PIPE)
    encode = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
         "-i", str(src), "-map", "0:v", "-map", "1:a?",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", str(out)],
        stdin=subprocess.PIPE)

    size, n, ci = W * H * 3, 0, 0
    while True:
        buf = decode.stdout.read(size)
        if len(buf) < size:
            break
        frame = np.frombuffer(buf, dtype=np.uint8).reshape(H, W, 3).copy()
        t = n / FPS

        while ci < len(chunks) and t > chunks[ci][-1]["end"] + 0.25:
            ci += 1
        if ci < len(chunks) and t >= chunks[ci][0]["start"] - 0.05:
            chunk = chunks[ci]
            active = max((i for i, w in enumerate(chunk) if w["start"] <= t), default=0)
            key = (ci, active)
            if key not in cache:
                cache[key] = Overlay(caption_image([w["word"] for w in chunk], active),
                                     W // 2, caption_y)
            cache[key].draw(frame)
        for o in static:
            o.draw(frame)

        encode.stdin.write(frame.tobytes())
        n += 1

    encode.stdin.close()
    decode.wait()
    if encode.wait() != 0 or n == 0:
        raise RuntimeError(f"render failed for {src.name}")
    return used
