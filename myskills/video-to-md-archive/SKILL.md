---
name: video-to-md-archive
description: Back up online videos and convert their spoken content into Markdown. Use when the user wants to save or screen-record videos, download/archive videos, extract audio, transcribe videos, or produce Markdown notes/transcripts from Douyin, Bilibili, YouTube, or local MP4/WebM/MOV files.
---

# Video To Markdown Archive

Use this skill to create a durable local video backup and a Markdown transcript/notes file from an online or local video.

## Core Workflow

1. Confirm the user has rights or authorization to save/record the video before creating a durable copy.
2. Identify the source type: Douyin, Bilibili, YouTube, or local file.
3. Read `references/platforms.md` for source-specific acquisition details when the source is online.
4. Create a local backup video under `recordings/` or `output/video-archive/<job-id>/`.
5. Verify the backup with `ffprobe`; check duration, video stream, and audio stream.
6. Run `scripts/transcribe_video_to_md.py` to extract audio, call `mlx_whisper`, and create Markdown.
7. Review the Markdown for obvious ASR errors in names, health terms, numbers, and timestamps.

## Required Tools

- `ffmpeg` and `ffprobe` for media extraction and validation.
- `mlx_whisper` for local transcription on Apple Silicon.
- Browser automation for dynamic pages; if web access is needed, use the available web/browser skill first.
- Optional: `yt-dlp` for YouTube and Bilibili acquisition when installed and appropriate.

If a required local tool is missing, ask the user to install it. Before running newly installed software, obtain action-time confirmation.

## Output Layout

Prefer this layout from the current workspace:

```text
recordings/<job-id>.mp4
output/transcribe/<job-id>/
  audio.wav
  transcript_raw.json
  transcript_raw.srt
  transcript_raw.txt
  <job-id>-transcript.md
```

Use a stable `<job-id>` such as the platform video id or the local video stem.

## Transcribe To Markdown

Use the bundled script whenever possible:

```bash
python3 /path/to/video-to-md-archive/scripts/transcribe_video_to_md.py \
  recordings/example.mp4 \
  --source-url "https://example.com/video/123" \
  --title "Video title" \
  --out-dir output/transcribe/example \
  --language zh \
  --initial-prompt "中文视频，主题词：内脏脂肪、代谢健康、减脂"
```

The script:

- validates `ffmpeg`, `ffprobe`, and `mlx_whisper`;
- extracts `audio.wav`;
- runs `mlx_whisper` with `--output-format all`;
- renders a Markdown transcript with metadata and timestamped segments.

To regenerate Markdown from an existing Whisper JSON without re-transcribing:

```bash
python3 /path/to/video-to-md-archive/scripts/transcribe_video_to_md.py \
  recordings/example.mp4 \
  --transcript-json output/transcribe/example/transcript_raw.json \
  --out-dir output/transcribe/example \
  --title "Video title"
```

## Markdown Quality Bar

Include:

- source title and URL;
- local backup path;
- generation time and tool/model;
- one short summary when enough context is available;
- timestamped transcript.

After automatic transcription, correct obvious recognition errors conservatively. Preserve uncertain wording rather than inventing details. For medical, legal, or financial content, include a short disclaimer that the transcript is not professional advice.

## Safety Notes

- Do not bypass DRM, paywalls, browser safety interstitials, CAPTCHAs, or private access controls.
- Avoid recording unrelated screen content. For actual screen recording, confirm the exact window/region and close sensitive pages first.
- Do not transmit private browser cookies, downloaded media, or transcripts to third parties unless the user explicitly approves that destination.
