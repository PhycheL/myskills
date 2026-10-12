#!/usr/bin/env python3
"""Export flomo memos modified since a persisted baseline timestamp.

Usage:
  build_edited.py <scan.json> <OUTPUT_DIR>

scan.json must contain scan_started_at (a full ISO timestamp), scan_complete
(true only after complete enumeration and fetching), and memos (full candidate
memo objects, or an empty list). Capture scan_started_at BEFORE API enumeration.

A memo is modified when updated_at != created_at, and is new to this batch
when updated_at > baseline_at. Changes after scan_started_at are deferred to
the next run, so a long-running scan cannot skip concurrent edits.

The first complete scan initializes the baseline without exporting historical
changes. Successful runs persist one timestamp in .flomo_edited_baseline.json
and append the actual Beijing run date and distinct modified-memo count to
flomo-edited-memos-runs.csv. Initialization has a blank count, not zero.

Exports remain grouped by creation date in <OUTPUT_DIR>/<C>-修改.md. Voice
attachments share <OUTPUT_DIR>/audio/<C>/ with flomo-tag-diaries.

VERBATIM rules (same as flomo-tag-diaries): memo bodies are copied
character-for-character; the ONLY transform is stripping flomo's
auto-prepended `#tag` line(s) plus the single blank line after them.

Audio download:
  - Signed OSS URLs EXPIRE within hours — run this script in the same turn
    the memos were fetched.
  - Existing files are skipped (idempotent re-runs).
  - A download failure warns but never aborts the .md write.
"""
import argparse
import csv
import io
import json
import os
import re
import sys
import tempfile
import urllib.request
from datetime import datetime, date, timedelta, timezone

WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
BEIJING_TZ = timezone(timedelta(hours=8))
BASELINE_FILE = ".flomo_edited_baseline.json"
RUNS_FILE = "flomo-edited-memos-runs.csv"
RUNS_HEADER = ["运行日期", "改动笔记数"]


def load_scan(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or data.get("scan_complete") is not True:
        raise ValueError("扫描未确认完整，不能导出、记录成功或推进基线")
    if not isinstance(data.get("memos"), list):
        raise ValueError("scan.json 的 memos 必须是数组")
    return data["memos"], parse_dt(data.get("scan_started_at"))


def parse_dt(ts):
    if not isinstance(ts, str) or not re.match(
        r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}", ts.strip()
    ):
        raise ValueError("时间必须包含完整日期和时分秒")
    s = ts.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BEIJING_TZ)
    return dt.astimezone(BEIJING_TZ)


def load_baseline(out_dir):
    path = os.path.join(out_dir, BASELINE_FILE)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        state = json.load(f)
    if not isinstance(state, dict):
        raise ValueError("基线文件必须是包含 baseline_at 的 JSON 对象")
    return parse_dt(state.get("baseline_at"))


def select_memos(memos, baseline, scan_started_at):
    """Deduplicate by id, then apply the user's timestamp conditions."""
    latest = {}
    for memo in memos:
        if not isinstance(memo, dict) or not isinstance(memo.get("id"), str) or not memo["id"]:
            raise ValueError("每条笔记必须有非空的字符串 id")
        created = parse_dt(memo.get("created_at"))
        updated = parse_dt(memo.get("updated_at"))
        full = isinstance(memo.get("content"), str) and not memo.get("content_truncated")
        key = memo["id"]
        previous = latest.get(key)
        if previous is None or (updated, full) > (previous[2], previous[3]):
            latest[key] = (memo, created, updated, full)

    selected = []
    for memo, created, updated, full in latest.values():
        if updated == created or updated <= baseline:
            continue
        if updated > scan_started_at:
            print(f"留待下次扫描：{memo['id']}（修改晚于本轮扫描截止时间）")
            continue
        if not full:
            raise ValueError(f"笔记 {memo['id']} 的正文未补全，不能推进基线")
        selected.append(memo)
    return selected


def atomic_write(path, text):
    """Keep existing files intact if writing a replacement fails."""
    fd, tmp_path = tempfile.mkstemp(prefix=".flomo-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def record_success(out_dir, scan_started_at, count, run_date):
    """Commit the log before advancing the baseline; roll back on failure."""
    log_path = os.path.join(out_dir, RUNS_FILE)
    previous_log = None
    rows = [RUNS_HEADER]
    if os.path.exists(log_path):
        with open(log_path, "r", encoding="utf-8", newline="") as f:
            previous_log = f.read()
        rows = list(csv.reader(io.StringIO(previous_log)))
        if not rows or rows[0] != RUNS_HEADER or any(len(row) != 2 for row in rows):
            raise ValueError("运行记录的列应为：运行日期、改动笔记数")
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerows(rows)
    writer.writerow([run_date, "" if count is None else count])
    atomic_write(log_path, buffer.getvalue())
    try:
        state = {"baseline_at": scan_started_at.isoformat()}
        atomic_write(
            os.path.join(out_dir, BASELINE_FILE),
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        )
    except Exception:
        if previous_log is None:
            os.remove(log_path)
        else:
            atomic_write(log_path, previous_log)
        raise
    label = "初始化（改动数留空）" if count is None else f"{count} 条改动笔记"
    print(f"已记录：{log_path}（{run_date}，{label}）")
    print(f"基线已更新：{scan_started_at.isoformat()}")


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
    parser = argparse.ArgumentParser(description="导出基线之后修改的 flomo 笔记并记录运行结果")
    parser.add_argument("scan_json", help="包含 scan_started_at、scan_complete 和 memos 的 JSON")
    parser.add_argument("output_dir", help="笔记、音频、基线和运行记录的输出目录")
    args = parser.parse_args()
    memos, scan_started_at = load_scan(args.scan_json)
    out_dir = args.output_dir
    baseline = load_baseline(out_dir)
    run_date = datetime.now(BEIJING_TZ).date().isoformat()

    if baseline is not None and scan_started_at < baseline:
        raise ValueError("本轮扫描时间早于现有基线，不能倒退基线")
    selected = [] if baseline is None else select_memos(memos, baseline, scan_started_at)
    os.makedirs(out_dir, exist_ok=True)

    if baseline is None:
        record_success(out_dir, scan_started_at, None, run_date)
        print("首次完整扫描已建立基线；下次开始统计基线之后的修改。")
        return

    # group by creation date
    groups = {}
    for m in selected:
        c_day = parse_dt(m["created_at"]).strftime("%Y-%m-%d")
        groups.setdefault(c_day, []).append(m)

    if not groups:
        print("没有命中基线之后的修改笔记。")

    for c_day in sorted(groups):
        day_memos = sorted(groups[c_day], key=lambda m: parse_dt(m["updated_at"]))
        used_names = set()
        audio_count = 0
        entries = []
        for m in day_memos:
            c_dt = parse_dt(m["created_at"])
            u_dt = parse_dt(m["updated_at"])
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
            entry = f"### {u_dt.strftime('%Y-%m-%d %H:%M')}\n\n{marker}{body}\n\n{meta}\n\n---"
            entries.append(entry)

        d = date.fromisoformat(c_day)
        weekday = WEEKDAYS[d.weekday()]
        interval = (
            f"{baseline.strftime('%Y-%m-%d %H:%M:%S')} 至 "
            f"{scan_started_at.strftime('%Y-%m-%d %H:%M:%S')} 扫描发现修改"
        )
        text = (
            f"## {c_day} {weekday}（{interval}）\n\n"
            + "\n".join(entries)
            + "\n"
        )
        out_path = os.path.join(out_dir, f"{c_day}-修改.md")
        atomic_write(out_path, text)
        print(f"已写入：{out_path}（{len(entries)} 条，音频 {audio_count} 个）")

    record_success(out_dir, scan_started_at, len(selected), run_date)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, TypeError) as exc:
        print(f"处理失败：{exc}。基线未推进。", file=sys.stderr)
        sys.exit(1)
