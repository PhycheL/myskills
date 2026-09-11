#!/usr/bin/env python3
"""Export flomo memos that were EDITED on a scan date, grouped by creation date.

Usage:
  build_edited.py <edited_memos.json> <OUTPUT_DIR> <SCAN_DATE>

- edited_memos.json : JSON array (or {"memos": [...]}) of full memo objects.
                      Every memo should satisfy: created_at date < SCAN_DATE
                      and updated_at date == SCAN_DATE (filter upstream).
- OUTPUT_DIR        : directory where per-creation-date files are written.
- SCAN_DATE         : YYYY-MM-DD, the date these memos were modified on.

Output (per creation date C found in the input):
  <OUTPUT_DIR>/<C>-修改.md

Each file contains every memo created on C but edited on SCAN_DATE:
  - day header  : ## <C> 星期X（<SCAN_DATE> 修改）
  - memo block  : ### HH:MM  (the MODIFICATION time)
  - meta lines  : > 创建于：... ｜ 修改于：...   and   > 来源：[flomo 原文](url)
  - audio       : voice attachments download to <OUTPUT_DIR>/audio/<C>/,
                  same naming/idempotency as flomo-tag-diaries' build_day.py,
                  and are linked via `> 音频：[..](audio/<C>/..)`.

VERBATIM rules (same as flomo-tag-diaries): memo bodies are copied
character-for-character; the ONLY transform is stripping flomo's
auto-prepended `#tag` line(s) plus the single blank line after them.

Audio download:
  - Signed OSS URLs EXPIRE within hours — run this script in the same turn
    the memos were fetched.
  - Existing files are skipped (idempotent re-runs).
  - A download failure warns but never aborts the .md write.
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


def parse_dt(ts):
    s = ts.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def download_audio(memo, day, out_dir, used_names):
    """Download a memo's recorded audio into <out_dir>/audio/<day>/.

    `day` is the memo's CREATION date, keeping audio naming identical to
    flomo-tag-diaries (<day>_<HH-MM>.m4a, HH-MM from created_at) so the two
    skills share the same audio store without collisions. Never raises.
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
        except Exception as e:  # noqa: BLE001 — never abort the write
            print(f"警告：音频下载失败（{e}）：{url}", file=sys.stderr)
            if os.path.exists(dest):
                os.remove(dest)
    return saved


def strip_leading_tags(content):
    """Remove ONLY flomo's auto-prepended tag line(s) like '#流水账'.

    Everything else is returned VERBATIM (only trailing newlines trimmed).
    """
    lines = content.split("\n")
    idx = 0
    while idx < len(lines) and re.match(r"^#[^ \n]", lines[idx]):
        idx += 1
    if idx < len(lines) and lines[idx].strip() == "":
        idx += 1
    return "\n".join(lines[idx:]).rstrip("\n")


def main():
    if len(sys.argv) < 4:
        print("Usage: build_edited.py <edited_memos.json> <OUTPUT_DIR> <SCAN_DATE>")
        sys.exit(1)

    src, out_dir, scan_date = sys.argv[1], sys.argv[2], sys.argv[3]
    os.makedirs(out_dir, exist_ok=True)

    memos = load_memos(src)

    # group by creation date
    groups = {}
    for m in memos:
        ca = m.get("created_at")
        ua = m.get("updated_at")
        if not ca or not ua:
            continue
        c_day = parse_dt(ca).strftime("%Y-%m-%d")
        groups.setdefault(c_day, []).append(m)

    if not groups:
        print("没有可写入的修改笔记。")
        return

    for c_day in sorted(groups):
        day_memos = groups[c_day]
        used_names = set()
        audio_count = 0
        entries = []
        for m in day_memos:
            c_dt = parse_dt(m["created_at"])
            u_dt = parse_dt(m["updated_at"])
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
            audio_rel = download_audio(m, c_day, out_dir, used_names)
            audio_count += len(audio_rel)
            meta = (
                f"> 创建于：{c_dt.strftime('%Y-%m-%d %H:%M')} ｜ "
                f"修改于：{u_dt.strftime('%Y-%m-%d %H:%M')}\n"
                f"> 来源：[flomo 原文]({link})"
            )
            if audio_rel:
                audio_links = " ".join(
                    f"[{os.path.splitext(os.path.basename(p))[0]}]({p})" for p in audio_rel
                )
                meta += f"\n> 音频：{audio_links}"
            entry = f"### {u_dt.strftime('%H:%M')}\n\n{marker}{body}\n\n{meta}\n\n---"
            entries.append((u_dt.strftime("%H:%M"), entry))

        if not entries:
            continue

        d = date.fromisoformat(c_day)
        weekday = WEEKDAYS[d.weekday()]
        entries.sort(key=lambda x: x[0])
        text = (
            f"## {c_day} {weekday}（{scan_date} 修改）\n\n"
            + "\n".join(e for _, e in entries)
            + "\n"
        )
        out_path = os.path.join(out_dir, f"{c_day}-修改.md")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"已写入：{out_path}（{len(entries)} 条，音频 {audio_count} 个）")


if __name__ == "__main__":
    main()
