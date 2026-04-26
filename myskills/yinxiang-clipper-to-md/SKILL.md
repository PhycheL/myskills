---
name: yinxiang-clipper-to-md
description: Use when exporting 印象剪藏/剪识 web collector items from clipper.yinxiang.com to local Markdown files, especially when the content exists in the 剪藏网页 rather than the Yinxiang desktop app local note cache.
---

# Yinxiang Clipper To Markdown

## Overview

Export items from the 印象剪藏网页 (`https://clipper.yinxiang.com/collectors/all?view=all`) into local Markdown. This is different from `yinxiang-note-to-md`: do not read `LocalNoteStore.sqlite`, ENML, or desktop client caches for this workflow.

## Workflow

1. Open the collectors page in a browser context that is already logged in.
2. Verify the page shows the account and item count. If API calls return `status.code=4001`, the browser context is not logged in.
3. Capture a batch with `scripts/capture_yinxiang_clipper_collectors_in_browser.js`.
4. Move the downloaded JSON from `~/Downloads` into the working project, usually under `.clipper_probe/`.
5. Convert the JSON with `scripts/convert_yinxiang_clipper_capture_to_md.py`.
6. Verify `.md` count, image asset count, empty image links, and missing local assets before claiming success.

## Capture

The browser script uses the logged-in page to call these Clipper APIs:

- `POST /third/ever-collector/v2/getCollectionItemList`
- `POST /third/ever-collector/v2/getCollectionItemMate`
- `POST /third/ever-collector/v2/getCollectionItemContent`

Run the script in the DevTools console on the collectors page, or adapt it for the active browser automation tool. The script downloads a JSON file named like:

```text
yinxiang_clipper_collectors_p0001_n10_2026-04-25T04-38-02.json
```

Edit the final options block in the script to change batch size:

```js
})({
  pageNumber: 1,
  pageSize: 50,
});
```

Avoid posting captured data from the HTTPS page to `http://127.0.0.1`; Chrome may block loopback access from the page's address space. Prefer the Blob download approach already built into the script.

## Convert

After moving the JSON into the project:

```bash
python3 /path/to/skill/scripts/convert_yinxiang_clipper_capture_to_md.py \
  --input .clipper_probe/collectors_p0001_n50.json \
  --output-dir yinxiang_clipper_collectors_p0001_n50
```

The converter:

- uses `content.cleanedHtml` as the primary article body
- converts HTML to GitHub-flavored Markdown with `pandoc`
- downloads images into `assets/<item-stem>/`
- infers image extensions from file bytes instead of trusting response headers
- writes one `.md` per item plus `README.md`

If a remote image fails and the user accepts remote fallbacks, add `--keep-remote-images`; otherwise fix the download issue before continuing.

## Validation

Run these checks on every test batch:

```bash
find OUTPUT_DIR -maxdepth 1 -name '*.md' | wc -l
find OUTPUT_DIR/assets -type f | wc -l
find OUTPUT_DIR/assets -type f -name '*.bin' | wc -l
rg -n '!\[[^\]]*\]\(\s*\)|!\[\]\(\)|\.bin\)|<div|<span' OUTPUT_DIR || true
```

Expected for a clean batch:

- `.md` count equals exported items plus `README.md`
- image count is nonzero for image-heavy batches
- `.bin` count is `0`
- `.json` image assets should be `0`
- `rg` returns no empty image links, no `.bin` links, and no obvious `<div>/<span>` HTML noise

Spot-check at least one Markdown file for:

- title and `## 元数据`
- `item_guid`, `item_type`, `collection_time`, `source_url`
- readable body text
- local image links under `assets/`

## Batch Strategy

Start with 10 items. If clean, export 50 items per batch until unusual item types appear. Watch for `IMAGE`, `FILE`, `VIDEO`, `FRAGMENT`, `VOICE`, and `SHORTHAND`; these may need type-specific handling beyond `cleanedHtml`.

Observed rules:

- `IMAGE` items may not have `source_url`; do not require source URL metadata for non-`WEB_PAGE` items.
- `IMAGE` items can still return short HTML from the content API and should be converted to Markdown with local image assets.
- Some `WEB_PAGE` items return empty `cleanedHtml`; fall back to source URL and only include thumbnails that download as real image bytes.
- Some thumbnails are bare collector hashes. Bare hashes can resolve to missing-file JSON responses, so the converter must not save non-image responses as Markdown image assets.
- Some collector resource URLs with `hash-size` suffixes can also return JSON missing-file responses. Treat JSON responses from image downloads as unavailable images and skip them.
- Some clipped pages include ad/tracking pixel `<img>` URLs such as Google ads, DoubleClick, BlueKai, Criteo, PubMatic, and similar user-sync endpoints. Skip these instead of failing the export or preserving them as Markdown images.
- Some historical source images return HTTP 404/410 or time out while reading. Skip unavailable or timed-out remote images rather than blocking the whole batch.
- Use a short image request timeout for batch export so a few slow historical resources do not stall large batches.
- Some clipped chat/history pages contain malformed image URLs such as bare `https:` without a host. Skip malformed no-host image URLs rather than blocking the whole batch.
- Some `WEB_PAGE` chat/history records may have real `cleanedHtml` but no `source_url` (`websiteType=UNKNOWN`, `sourceType=OTHER`). Do not fail validation solely because `source_url` metadata is absent.
- If `VIDEO` or `VOICE` appears, use `video-to-md-archive` for that item: visit the source/media URL with browser/web access, create a durable local media backup when authorized, transcribe audio, and write the transcript/notes Markdown. Keep the Clipper item Markdown as the metadata/index entry and link to the media transcript output.

Use stable output names such as:

```text
yinxiang_clipper_collectors_p0001_n50/
yinxiang_clipper_collectors_p0002_n50/
```

Keep raw JSON files until the Markdown and assets are verified.
