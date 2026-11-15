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

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m clipper "https://www.youtube.com/watch?v=VIDEO_ID"
```

Clips land in `out/<video id>/` as `clip_01.mp4` with a matching `clip_01.txt` holding the title, a caption and a credit link back to the exact timestamp.

Useful flags:

```
--top 5               how many clips
--min 20 --max 50     clip length in seconds
--picker heuristic    no Claude, fully offline
--transcribe full     transcribe everything, slower and better on long streams
--layout stack        force facecam on top (also: crop, blur)
--credit "@name"      text shown at the bottom of the clip
```

It also takes a local file, which is the route for your own gameplay recordings:

```bash
.venv/bin/python -m clipper ~/Movies/my-gta6-session.mp4 --credit "@mychannel"
```

## Before you post

The tool will happily clip anyone. That doesn't mean you're allowed to post it.

Clip your own footage, or creators who say they allow it. Plenty do, and many run clipping programs that pay per view. If a streamer hasn't said yes, ask. Keep the credit line on the clip and the source link in the description.

YouTube won't monetize channels made of other people's footage with nothing added. Add commentary, context or real editing if ad revenue is the goal.

## What's tested and what isn't

Tested on an 11 minute YouTube video on an M-series Mac: 59 seconds end to end for two clips with the offline picker, 103 seconds with Claude ranking. The full and peaks transcription modes and the blur layout all ran clean.

Not yet tested on a real multi-hour stream with a facecam, so the stacked layout and the chat signal have only been exercised in code, not on real footage. Twitch and Kick should download through yt-dlp but get no chat signal.

## Built with Claude Code

Claude Code wrote the first version. What it got wrong and what I changed is in [NOTES-AI.md](NOTES-AI.md).
