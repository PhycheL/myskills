# Platform Acquisition Notes

Use this reference after the source platform is known. Prefer durable video backup methods that preserve the target video only. Use screen recording only when a direct media backup is unavailable or explicitly requested.

## Common Sequence

1. Confirm authorization to save or record the video.
2. Open online sources in a new tab or isolated browser context.
3. Prefer a direct video backup:
   - official export/download when available;
   - `yt-dlp` for YouTube/Bilibili when appropriate;
   - media URL extraction from the rendered page for Douyin when visible in DOM.
4. If direct backup fails, use bounded screen recording of only the target player/window after explicit confirmation.
5. Verify with:

```bash
ffprobe -v error \
  -show_entries format=duration,size:stream=index,codec_type,codec_name,width,height,duration \
  -of json path/to/video.mp4
```

## Douyin

Use browser automation because static `curl` often receives anti-bot bootstrap pages.

Recommended path:

1. Open the full `https://www.douyin.com/video/<id>` URL in a new browser/CDP tab.
2. Extract page text, chapter summaries, and media source from the rendered DOM:

```javascript
(() => ({
  title: document.title,
  text: document.body?.innerText || "",
  videos: [...document.querySelectorAll("video")].map(v => ({
    src: v.currentSrc || v.src || "",
    duration: v.duration,
    width: v.videoWidth,
    height: v.videoHeight
  }))
}))()
```

3. Download the first non-empty MP4 source with a Douyin referer and browser-like user agent.
4. If the URL expires, reopen the page and extract `currentSrc` again.

Douyin may expose chapter summaries in page text. Use those summaries to improve Markdown section headings, but treat them as secondary to the actual transcript.

## Bilibili

Prefer `yt-dlp` when installed:

```bash
yt-dlp \
  --merge-output-format mp4 \
  -o "recordings/%(id)s.%(ext)s" \
  "https://www.bilibili.com/video/BV..."
```

For multi-part videos, inspect the playlist behavior before bulk downloading. If login-only content is required and the user has authorized use of their browser session, use browser cookies carefully:

```bash
yt-dlp --cookies-from-browser chrome ...
```

Confirm before using browser cookies, because this reads local browser session data.

## YouTube

Prefer `yt-dlp` when installed:

```bash
yt-dlp \
  --merge-output-format mp4 \
  --write-auto-subs --sub-langs "zh.*,en.*" --convert-subs srt \
  -o "recordings/%(id)s.%(ext)s" \
  "https://www.youtube.com/watch?v=..."
```

If subtitles are downloaded, use them as an additional reference. If subtitles are missing, transcribe the backed-up video with `mlx_whisper`.

Do not attempt to bypass age gates, premium-only restrictions, DRM, or paywalls.

## Local Files

For existing local videos, skip acquisition and run the transcription script directly. Verify the file first with `ffprobe`.

## Actual Screen Recording Fallback

Use only when a direct backup is unavailable or the user explicitly wants a screen recording. Confirm:

- which screen/window/region to capture;
- whether system audio is required;
- that unrelated sensitive pages are closed.

On macOS, list capture devices:

```bash
ffmpeg -f avfoundation -list_devices true -i ""
```

Then record a bounded capture. Device indexes vary by machine, so do not hard-code them in the skill. Stop recording promptly after the target video ends and verify the resulting file.
