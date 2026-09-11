#!/usr/bin/env python3
"""Write ONE day's flomo diary Markdown file from that day's memos,
and download any attached audio recordings.

Usage:
  build_day.py <day_memos.json> <OUTPUT_DIR> [DATE]

- day_memos.json : JSON for a SINGLE day. Either a bare array
                   [ {memo}, ... ] or { "memos": [ {memo}, ... ] }.
                   Every memo should belong to the same calendar day.
- OUTPUT_DIR      : directory where <DATE>.md is written (created if missing).
- DATE (optional) : YYYY-MM-DD. If omitted, derived from the first
                     memo's `created_at`.

Each memo object is expected to have at least:
  - content      : str (may start with "#tag\\n\\n")
  - created_at   : ISO 8601 timestamp, e.g. "2026-07-19T15:54:41+08:00"
  - url          : str (flomo source link)
  - id           : str (slug)
  - has_voice    : bool (optional)
  - has_image    : bool (optional)
  - files        : list (optional), items like
                   {"type": "recorded", "url": "https://...m4a?Expires=..."}

Audio download:
  - Voice memos carry signed OSS URLs that EXPIRE (typically within hours).
    This script downloads immediately, so the memo JSON must be freshly
    fetched in the same run (do not stage and download much later).
  - Files are saved to <OUTPUT_DIR>/audio/<DATE>/<DATE>_<HH-MM>.m4a
    (a "-1", "-2" ... suffix is appended on same-minute collisions).
  - Existing files with the same name are NOT re-downloaded (skipped).
  - A download failure prints a warning but never aborts the .md write.
  - Each downloaded audio is referenced in the .md via a relative link
    after the source link — metadata only, the memo body stays verbatim.
"""
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, date

WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]


def load_memos(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        if "memos" in data:
            return data["memos"]
        for v in data.values():
            if isinstance(v, list):
                return v
        raise ValueError("Could not find a memo list in the JSON object.")
    if isinstance(data, list):
        return data
    raise ValueError("Unsupported JSON top-level type: %s" % type(data))


def parse_dt(created_at):
    s = created_at.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def download_audio(memo, day, out_dir, used_names):
    """Download a memo's recorded audio file(s) into <out_dir>/audio/<day>/.

    Returns the list of relative paths (relative to out_dir) of the saved
    files — empty if the memo has no audio or all downloads were skipped /
    failed. Never raises: failures are reported as warnings on stderr.
    """
    audio_urls = [
        f.get("url") for f in (memo.get("files") or [])
        if f.get("type") == "recorded" and f.get("url")
    ]
    if not audio_urls:
        return []

    dt = parse_dt(memo["created_at"])
    audio_dir = os.path.join(out_dir, "audio", day)
    os.makedirs(audio_dir, exist_ok=True)

    saved = []
    for url in audio_urls:
        base = f"{day}_{dt.strftime('%H-%M')}"
        name = base + ".m4a"
        n = 1
        while name in used_names:
            name = f"{base}-{n}.m4a"
            n += 1
        used_names.add(name)
        dest = os.path.join(audio_dir, name)
        if os.path.exists(dest):
            print(f"音频已存在，跳过：{dest}")
            saved.append(os.path.join("audio", day, name))
            continue
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as fh:
                fh.write(resp.read())
            saved.append(os.path.join("audio", day, name))
            print(f"音频已下载：{dest}")
        except Exception as e:  # noqa: BLE001 — never abort the diary write
            print(f"警告：音频下载失败（{e}）：{url}", file=sys.stderr)
            if os.path.exists(dest):
                os.remove(dest)  # drop partial file
    return saved


def strip_leading_tags(content):
    """Remove ONLY flomo's auto-prepended tag line(s) like '#流水账'.

    flomo auto-prepends '#tag\\n\\n'. Strip those leading tag lines and the
    single blank line immediately after them, then return the body VERBATIM.
    The body is NOT .strip()'d, NOT reformatted, NOT rewritten — only
    trailing newlines are trimmed so the file ends cleanly.
    """
    lines = content.split("\n")
    idx = 0
    while idx < len(lines) and re.match(r"^#[^ \n]", lines[idx]):
        idx += 1
    if idx < len(lines) and lines[idx].strip() == "":
        idx += 1
    return "\n".join(lines[idx:]).rstrip("\n")


def main():
    if len(sys.argv) < 3:
        print("Usage: build_day.py <day_memos.json> <OUTPUT_DIR> [DATE]")
        sys.exit(1)

    src = sys.argv[1]
    out_dir = sys.argv[2]
    forced_date = sys.argv[3] if len(sys.argv) > 3 else None
    os.makedirs(out_dir, exist_ok=True)

    memos = load_memos(src)

    # date for filenames, audio dir and header — determine once, up front
    if forced_date:
        day = forced_date
    else:
        first_ca = next((m.get("created_at") for m in memos if m.get("created_at")), None)
        if not first_ca:
            print("当天没有可写入的 memo，跳过。")
            return
        day = parse_dt(first_ca).strftime("%Y-%m-%d")

    used_names = set()
    audio_count = 0
    entries = []
    for m in memos:
        ca = m.get("created_at")
        if not ca:
            continue
        dt = parse_dt(ca)
        time = dt.strftime("%H:%M")
        body = strip_leading_tags(m.get("content", ""))
        if not body:
            continue
        marker = ""
        if m.get("has_voice"):
            marker = "🎤 语音记录\n\n"
        elif m.get("has_image"):
            marker = "📷 图片\n\n"
        link = m.get("url") or (
            "https://v.flomoapp.com/mine/?memo_id=" + m.get("id", "")
            if m.get("id") else ""
        )
        audio_rel = download_audio(m, day, out_dir, used_names)
        audio_count += len(audio_rel)
        meta = f"> 来源：[flomo 原文]({link})"
        if audio_rel:
            audio_links = " ".join(
                f"[{os.path.splitext(os.path.basename(p))[0]}]({p})" for p in audio_rel
            )
            meta += f"\n> 音频：{audio_links}"
        entry = f"### {time}\n\n{marker}{body}\n\n{meta}\n\n---"
        entries.append((time, entry))

    if not entries:
        print("当天没有可写入的 memo，跳过。")
        return

    d = date.fromisoformat(day)
    weekday = WEEKDAYS[d.weekday()]

    entries.sort(key=lambda x: x[0])
    text = f"## {day} {weekday}\n\n" + "\n".join(e for _, e in entries) + "\n"
    out_path = os.path.join(out_dir, day + ".md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"已写入：{out_path}（{len(entries)} 条，音频 {audio_count} 个）")


if __name__ == "__main__":
    main()
