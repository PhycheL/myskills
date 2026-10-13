#!/usr/bin/env python3
"""Verify an ID-based export and issue its completion receipt."""
import argparse
import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path

from build_day import atomic_write, parse_dt
from memo_references import find_references, reference_inputs, restore_references


def load_ids(path):
    ids = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(ids, list) or not all(isinstance(key, str) and key for key in ids):
        raise ValueError("导出输入必须是非空字符串 ID 的 JSON 数组")
    return list(dict.fromkeys(ids))


def output_file(output, name):
    if not isinstance(name, str) or Path(name).name != name or not name.endswith(".md"):
        raise ValueError("报告中的文件名无效")
    path = output / name
    if not path.is_file():
        raise ValueError(f"导出文件尚未完成：{name}")
    return path


def file_digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_export(report_path, ids_path):
    expected = set(load_ids(ids_path))
    path = Path(report_path)
    report = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(report, dict) or report.get("schema_version") != 1:
        raise ValueError("导出报告格式无效")
    report["complete"] = False
    report.pop("memos", None)
    atomic_write(path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    if not isinstance(report.get("output_dir"), str) or not Path(report["output_dir"]).is_absolute():
        raise ValueError("导出报告缺少绝对输出目录")
    output = Path(report["output_dir"])
    if not isinstance(report.get("days"), dict):
        raise ValueError("导出报告缺少逐日结果")
    actual, seen = [], set()
    for day, record in report["days"].items():
        date.fromisoformat(day)
        if not isinstance(record, dict) or not isinstance(record.get("memos"), list) or not record["memos"]:
            raise ValueError("逐日报告缺少已导出笔记")
        for item in record["memos"]:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
                raise ValueError("报告缺少笔记 ID")
            key = item["id"]
            if key in seen or key not in expected:
                raise ValueError("已导出笔记 ID 重复或超出请求范围")
            if not isinstance(item.get("has_voice"), bool):
                raise ValueError("导出报告缺少语音标记")
            if not isinstance(item.get("created_at"), str) or not isinstance(item.get("updated_at"), str):
                raise ValueError("导出报告缺少笔记时间")
            created, updated = parse_dt(item["created_at"]), parse_dt(item["updated_at"])
            if created.date().isoformat() != day:
                raise ValueError("笔记创建日期与导出日期分组不一致")
            actual.append({"id": key, "created_at": created.isoformat(), "updated_at": updated.isoformat()})
            seen.add(key)
        raw = output_file(output, record.get("raw_file"))
        if not raw.name.startswith(day) or record.get("raw_sha256") != file_digest(raw):
            raise ValueError("原始版日期不一致或文件已被改写")
        raw_text = raw.read_text(encoding="utf-8")
        references = reference_inputs(find_references(raw_text))
        if record.get("references", []) != references:
            raise ValueError("报告中的关联与原始版不一致")
        has_voice = any(item["has_voice"] for item in record["memos"])
        needs_organized = bool(references) or has_voice
        organized_name = raw.stem + "-整理版.md" if needs_organized else None
        if record.get("organized_file") != organized_name:
            raise ValueError("整理版文件名与原始版不一致")
        if needs_organized:
            organized = output_file(output, organized_name)
            if organized.stat().st_mtime_ns < raw.stat().st_mtime_ns:
                raise ValueError("整理版早于本轮原始版，请重新生成")
            organized_text = organized.read_text(encoding="utf-8")
            if references:
                organized_text = restore_references(organized_text, references, record.get("reference_results"))
            if not has_voice and organized_text != raw_text:
                raise ValueError("纯文字整理版除关联展开外必须保留原文")
            structural = re.compile(r"^(?:#{2,3} |>[ ](?:创建于：|来源：|音频：)|---$|[🎤📷])")
            raw_lines = [line for line in raw_text.splitlines() if structural.match(line)]
            organized_lines = [line for line in organized_text.splitlines() if structural.match(line)]
            if raw_lines != organized_lines:
                raise ValueError("整理版的结构或元信息与原始版不一致")
            record["organized_sha256"] = file_digest(organized)
    if seen != expected:
        raise ValueError("导出结果未覆盖全部请求 ID")
    report["memos"] = actual
    report["complete"] = True
    atomic_write(path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return len(actual)


def main():
    parser = argparse.ArgumentParser(description="核对指定 ID 的全部导出文件并标记报告完成")
    parser.add_argument("report", help="build_day.py 累积生成的本轮导出报告")
    parser.add_argument("--ids", required=True, help="本轮请求导出的普通 ID 数组 JSON")
    args = parser.parse_args()
    count = verify_export(args.report, args.ids)
    print(f"导出验证通过：{count} 条笔记；完成报告：{args.report}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"导出尚未完成：{exc}", file=sys.stderr)
        sys.exit(1)
