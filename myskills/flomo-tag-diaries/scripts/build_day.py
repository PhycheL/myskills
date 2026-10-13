#!/usr/bin/env python3
"""Write ONE day's flomo diary Markdown file from that day's memos,
and download any attached audio recordings.

Usage:
  build_day.py <day_memos.json> <OUTPUT_DIR> [DATE]
  build_day.py <day_memos.json> <OUTPUT_DIR> <DATE> --suffix=-选取 --report <report.json>

The input may contain all memos for a tag or any explicitly chosen subset.
Optional filename, time-display and report settings are independent of how
the memos were selected. This script never selects IDs or rewrites prose.

- day_memos.json : JSON for a SINGLE day. Either a bare array
                   [ {memo}, ... ] or { "memos": [ {memo}, ... ] }.
                   Every memo should belong to the same calendar day.
- OUTPUT_DIR      : directory where <DATE>.md is written (created if missing).
- DATE (optional) : YYYY-MM-DD. If omitted, derived from the first
                     memo's `created_at`.

Each memo object is expected to have at least:
  - content      : str (may start with "#tag\\n\\n")
  - created_at   : ISO 8601 timestamp, e.g. "2026-07-19T15:54:41+08:00"
  - updated_at   : ISO 8601 timestamp (required with --report or --time-field updated_at)
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
import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import urllib.request
from datetime import datetime, date, timedelta, timezone
from pathlib import Path

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
    dt = datetime.fromisoformat(s)
    tz = timezone(timedelta(hours=8))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


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


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".flomo-export-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def validate_memos(memos, day, needs_updated):
    if not isinstance(memos, list):
        raise ValueError("全文输入必须是笔记数组")
    ids = set()
    for memo in memos:
        if not isinstance(memo, dict) or not isinstance(memo.get("created_at"), str):
            raise ValueError("笔记缺少创建时间")
        if parse_dt(memo["created_at"]).date().isoformat() != day:
            raise ValueError("输入笔记必须属于同一创建日期")
        if not isinstance(memo.get("content"), str) or memo.get("content_truncated"):
            raise ValueError("笔记全文尚未补全")
        key = memo.get("id")
        if not isinstance(key, str) or not key or key in ids:
            raise ValueError("笔记 ID 必须非空且不能重复")
        ids.add(key)
        if needs_updated:
            if not isinstance(memo.get("updated_at"), str):
                raise ValueError("笔记缺少修改时间")
            parse_dt(memo["updated_at"])


def load_report(path, out_dir):
    output = str(Path(out_dir).expanduser().resolve())
    if not Path(path).exists():
        return {"schema_version": 1, "output_dir": output, "days": {}}
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(report, dict) or report.get("schema_version") != 1 or report.get("output_dir") != output or not isinstance(report.get("days"), dict):
        raise ValueError("导出报告格式或输出目录不一致")
    return report


def main():
    parser = argparse.ArgumentParser(description="flomo-tag-diaries 原始版导出与音频下载")
    parser.add_argument("source")
    parser.add_argument("output_dir")
    parser.add_argument("date", nargs="?")
    parser.add_argument("--suffix", default="", help="日期后的文件名后缀，例如 -选取")
    parser.add_argument("--time-field", choices=("created_at", "updated_at"), default="created_at", help="条目排序和时间显示字段")
    parser.add_argument("--title-note", default="", help="可选的日期标题元信息，例如导出范围")
    parser.add_argument("--report", help="可选的导出结果 JSON，逐日记录 ID、时间和文件信息")
    args = parser.parse_args()
    if any(char in args.suffix for char in ("/", "\\", "\n", "\r")) or any(char in args.title_note for char in ("\n", "\r")):
        parser.error("文件名后缀不能含路径分隔符，标题元信息不能换行")
    src, out_dir, forced_date = args.source, args.output_dir, args.date
    out_dir = str(Path(out_dir).expanduser())

    memos = load_memos(src)
    if not isinstance(memos, list) or not all(isinstance(memo, dict) for memo in memos):
        raise ValueError("全文输入必须是笔记对象数组")

    # date for filenames, audio dir and header — determine once, up front
    if forced_date:
        day = forced_date
    else:
        first_ca = next((m.get("created_at") for m in memos if m.get("created_at")), None)
        if not first_ca:
            print("当天没有可写入的 memo，跳过。")
            return
        day = parse_dt(first_ca).strftime("%Y-%m-%d")

    date.fromisoformat(day)
    validate_memos(memos, day, bool(args.report) or args.time_field == "updated_at")
    report = load_report(args.report, out_dir) if args.report else None
    os.makedirs(out_dir, exist_ok=True)
    used_names = set()
    audio_count = 0
    entries = []
    for m in memos:
        dt = parse_dt(m["created_at"])
        timestamp = parse_dt(m[args.time_field])
        time = timestamp.strftime("%Y-%m-%d %H:%M") if args.time_field == "updated_at" else timestamp.strftime("%H:%M")
        body = strip_leading_tags(m.get("content", ""))
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
        if args.time_field == "updated_at":
            meta = (
                f"> 创建于：{dt.strftime('%Y-%m-%d %H:%M')} ｜ "
                f"修改于：{timestamp.strftime('%Y-%m-%d %H:%M')}\n" + meta
            )
        if audio_rel:
            audio_links = " ".join(
                f"[{os.path.splitext(os.path.basename(p))[0]}]({p})" for p in audio_rel
            )
            meta += f"\n> 音频：{audio_links}"
        entry = f"### {time}\n\n{marker}{body}\n\n{meta}\n\n---"
        entries.append((timestamp, entry))

    if not entries:
        print("当天没有可写入的 memo，跳过。")
        return

    d = date.fromisoformat(day)
    weekday = WEEKDAYS[d.weekday()]

    entries.sort(key=lambda x: x[0])
    title = f"## {day} {weekday}"
    if args.title_note:
        title += f"（{args.title_note}）"
    text = title + "\n\n" + "\n".join(e for _, e in entries) + "\n"
    stem = day + args.suffix
    out_path = Path(out_dir) / (stem + ".md")
    atomic_write(out_path, text)
    print(f"已写入：{out_path}（{len(entries)} 条，音频 {audio_count} 个）")
    if report is not None:
        report["days"][day] = {
            "memos": [{
                "id": memo["id"],
                "created_at": parse_dt(memo["created_at"]).isoformat(),
                "updated_at": parse_dt(memo["updated_at"]).isoformat(),
                "has_voice": bool(memo.get("has_voice")),
            } for memo in memos],
            "raw_file": out_path.name,
            "raw_sha256": hashlib.sha256(out_path.read_bytes()).hexdigest(),
            "formal_file": stem + "-书面语.md" if any(memo.get("has_voice") for memo in memos) else None,
        }
        atomic_write(args.report, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(f"已更新导出报告：{args.report}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"导出失败：{exc}", file=sys.stderr)
        sys.exit(1)
