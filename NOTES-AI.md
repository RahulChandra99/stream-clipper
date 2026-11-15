# What AI got wrong

A running log. What Claude generated, what was wrong with it, how it was caught, how it was fixed.

## 2026-11-15, first build

**Assumed ffmpeg could burn in captions.** The first plan was an ASS subtitle file and ffmpeg's `ass` filter. Homebrew's ffmpeg is built without libass and without `drawtext`, so neither filter exists on this machine. Caught by listing the filters before writing any render code (`ffmpeg -filters | grep ass` came back empty). Fix: ffmpeg only does the layout and pipes raw frames to Python, Pillow draws the text, a second ffmpeg encodes. Slower than a filter, but it works on any ffmpeg build.

**Used an OpenCV API that no longer exists.** `pip install opencv-python-headless` pulled OpenCV 5, which removed `cv2.CascadeClassifier`. The first end to end run crashed at face detection, after download, transcription and picking had all worked. Fix: pinned `opencv-python-headless<5` in requirements.txt. A better long term fix is moving to `cv2.FaceDetectorYN`, which needs a model file.

**Noisy but harmless warning.** OpenCV and PyAV (a faster-whisper dependency) each bundle their own ffmpeg libraries, so macOS prints "Class AVFFrameReceiver is implemented in both" on every run. Nothing breaks. Not fixed.

**Fallback titles are dumb.** With `--picker heuristic` the title is just the punchiest sentence in the clip, uppercased. It picked "HEY, WHAT'S UP, EVERYONE?" and once a sentence with swearing in it, which is bad for monetization. The Claude picker writes real titles. Not fixed.

**Claude's own pick said the clip needed a trim.** For one clip the picker's reason was that it should start a sentence later and end before a tangent. The tool can't act on that yet because Claude only chooses from fixed windows. Letting it return start and end offsets is the obvious next step.

## Mine to write

`score_window` in `clipper/pick.py`. The weights in there are guesses. Nobody has checked them against clips that actually did well.
