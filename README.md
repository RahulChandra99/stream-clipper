# stream-clipper

Give it a stream link or a video file. It finds the best moments and renders them as vertical 1080x1920 clips with a hook title and word-by-word captions, ready for Shorts, Reels and TikTok.

I built it for the GTA 6 launch, but nothing in it is specific to one game.

## How it works

1. Downloads the audio only. A four hour stream is a few hundred MB that way, not 30 GB.
2. Measures loudness per second, and on YouTube also how fast chat was moving. Both are compared against that stream's own average, so a streamer who shouts all night doesn't flag every minute.
3. Transcribes locally with faster-whisper. Short videos get a full transcript. Long streams only get the loud and busy parts transcribed, which is what keeps a four hour stream from taking an hour.
4. Scores every window that starts and ends on a sentence. The best ones go to Claude through the `claude` CLI, which throws out anything that needs context and writes the titles. No API key, it runs on a normal Claude subscription. `--picker heuristic` skips Claude and works offline.
5. Downloads only the seconds it needs, lays them out vertically, and draws the title and captions.

Three layouts. A small face that stays in one corner is treated as a facecam and goes on top with gameplay below. A big face gets a centre crop that follows it. Anything else gets the full frame over a blurred background.

## Run it

Needs Python 3.10+ and ffmpeg (`brew install ffmpeg`).

Double-click `Clipper.command` in Finder. The first run installs everything, then the app opens in your browser at http://127.0.0.1:8787.

Or from a terminal:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m clipper.app
```

## Using the app

1. Paste a YouTube, Twitch or Kick link, or drop a video file. Pick how many clips, how long, and a caption style.
2. Wait. The page shows each stage. Clips appear as they finish rendering.
3. Edit a clip if it needs it. Change the hook title, nudge the start and end, switch layout or caption style, fix a mistranscribed word, add music, then hit Render changes.
4. Post. Each platform button copies the description, selects the file in Finder and opens that platform's upload page. Send to phone shows a QR code that downloads the clip on your phone over Wi-Fi, which is the easiest way to post a Reel.

Music is whatever you put in the `music/` folder. It loops under the clip and ducks while someone is talking. Only use tracks you have the rights to. The YouTube Audio Library is free.

Voiceover is on its own tab. Type what you want said, pick a voice, press Listen to hear it, then render. The original audio drops while the voice speaks, and the voice's words replace the captions for that stretch, timed to each spoken word.

### Voices

The good voices come from Kokoro, a small neural speech model that runs on your Mac. Nothing you type is sent anywhere. It needs two files, about 350 MB, that aren't in this repo:

```bash
mkdir -p models && cd models
curl -LO https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx
curl -LO https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin
```

Without them the app falls back to the voices built into macOS, which work but sound like a screen reader.

Caption styles are Bold Pop, Neon, Karaoke Box, Minimal, Lower Third, or none.

There's still a command line version:

```bash
.venv/bin/python -m clipper "https://www.youtube.com/watch?v=VIDEO_ID" --top 5 --style neon
```

## Why posting isn't one click

YouTube locks videos uploaded through an unverified API project to private, and getting a project verified means an audit. Instagram's API only takes a video from a public URL and needs a business account and a Meta app. For a tool only I use, neither is worth it yet, so the app does everything up to the upload and hands over. If that changes, the YouTube Data API is the first one to add.

## What it is and isn't

A local app for one person. It listens on 127.0.0.1 only. Projects are JSON files in `projects/`, jobs run one at a time on a background thread. No database, no accounts, no cloud storage. The one exception to local-only is Send to phone, which starts a second tiny server on the LAN that serves a single file behind a random token for ten minutes.

Left out on purpose for now: speaker diarization, jump cuts, b-roll, zoom punch-ins, scheduling, and a live canvas preview. Edits need a re-render, which takes about 15 seconds for a 50 second clip.

## Before you post

The tool will happily clip anyone. That doesn't mean you're allowed to post it.

Clip your own footage, or creators who say they allow it. Plenty do, and many run clipping programs that pay per view. If a streamer hasn't said yes, ask. Keep the credit line on the clip and the source link in the description.

YouTube won't monetize channels made of other people's footage with nothing added. Add commentary, context or real editing if ad revenue is the goal.

## What's tested and what isn't

Tested on an 11 minute YouTube video on an M-series Mac. From the command line, two clips took 59 seconds with the offline picker and 103 seconds with Claude ranking. In the app I ran a link through to three rendered clips, edited one (title, style, progress bar) and re-rendered it, mixed in a test music track, uploaded a file, generated the phone QR code, and added a typed voiceover that I confirmed by transcribing the rendered clip.

Not tested yet: a real multi-hour stream with a facecam, so the stacked layout and the chat signal have only run in code. Twitch and Kick links. Actually scanning the QR code from a phone. The platform buttons open the upload pages but I haven't posted anything with them.

## Built with Claude Code

Claude Code wrote the first version. What it got wrong and what I changed is in [NOTES-AI.md](NOTES-AI.md).
