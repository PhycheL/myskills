#!/usr/bin/env python3
"""Write ONE day's flomo diary Markdown file from that day's memos.

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
"""
import json
import os
import re
import sys
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

    entries = []
    for m in memos:
        ca = m.get("created_at")
        if not ca:
            continue
        dt = parse_dt(ca)
        if forced_date:
            day = forced_date
        else:
            day = dt.strftime("%Y-%m-%d")
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
        entry = f"### {time}\n\n{marker}{body}\n\n> 来源：[flomo 原文]({link})\n\n---"
        entries.append((time, entry))

    if not entries:
        print("当天没有可写入的 memo，跳过。")
        return

    # date for filename + header
    if forced_date:
        day = forced_date
    else:
        day = parse_dt(memos[0]["created_at"]).strftime("%Y-%m-%d")
    d = date.fromisoformat(day)
    weekday = WEEKDAYS[d.weekday()]

    entries.sort(key=lambda x: x[0])
    text = f"## {day} {weekday}\n\n" + "\n".join(e for _, e in entries) + "\n"
    out_path = os.path.join(out_dir, day + ".md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"已写入：{out_path}（{len(entries)} 条）")


if __name__ == "__main__":
    main()
