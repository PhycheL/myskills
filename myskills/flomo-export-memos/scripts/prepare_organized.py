#!/usr/bin/env python3
"""Build a fresh organized draft and expand one level of memo associations."""
import argparse
import json
import sys
from pathlib import Path

from build_day import atomic_write, load_memos, strip_leading_tags
from memo_references import find_references, reference_inputs, render_reference, text_digest
from verify_export import file_digest, output_file


def prepare_organized(report_path, day, related_path=None):
    path = Path(report_path)
    report = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(report, dict) or report.get("schema_version") != 1:
        raise ValueError("导出报告格式无效")
    record = report["days"][day]
    output = Path(report["output_dir"])
    raw = output_file(output, record["raw_file"])
    if file_digest(raw) != record["raw_sha256"]:
        raise ValueError("原始版已被改写，请重新导出")
    text = raw.read_text(encoding="utf-8")
    references = find_references(text)
    if reference_inputs(references) != record.get("references", []):
        raise ValueError("报告中的关联与原始版不一致")
    required = bool(references) or any(item["has_voice"] for item in record["memos"])
    name = raw.stem + "-整理版.md" if required else None
    if record.get("organized_file") != name:
        raise ValueError("整理版文件名与原始版不一致")
    if not required:
        print("当天没有语音或关联，原始版即可。")
        return
    if references and related_path is None:
        raise ValueError("有关联时需传入 --related；补拉后仍全部不可用时传入空数组 JSON")

    related = load_memos(related_path) if related_path else []
    if not isinstance(related, list):
        raise ValueError("关联笔记输入必须是对象数组")
    lookup = {}
    for memo in related:
        if not isinstance(memo, dict) or not isinstance(memo.get("id"), str) or not memo["id"]:
            raise ValueError("关联笔记缺少 ID")
        if memo["id"] in lookup:
            raise ValueError("关联笔记 ID 重复")
        lookup[memo["id"]] = memo

    report["complete"] = False
    report.pop("memos", None)
    record.pop("organized_sha256", None)
    record.pop("reference_results", None)
    atomic_write(path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    pieces, results, cursor = [], [], 0
    for reference in references:
        target = lookup.get(reference["target_id"])
        reason, body = None, None
        if target is None:
            reason = "未取得关联笔记"
        elif not isinstance(target.get("content"), str) or target.get("content_truncated"):
            reason = "关联笔记正文不完整"
        else:
            body = strip_leading_tags(target["content"])
            if not body.strip():
                reason = "关联笔记没有可引用的文字正文"
        replacement = reference["line"]
        if reason:
            result = {"status": "unavailable", "reason": reason}
            print(f"警告：{reference['target_id']}：{reason}，保留原链接。", file=sys.stderr)
        else:
            replacement = render_reference(reference, body)
            result = {"status": "expanded", "sha256": text_digest(replacement)}
        pieces.extend((text[cursor:reference["start"]], replacement))
        cursor = reference["end"]
        results.append(result)
    pieces.append(text[cursor:])
    organized = output / name
    atomic_write(organized, "".join(pieces))
    record["reference_results"] = results
    atomic_write(path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"已生成整理版底稿：{organized}；随后仅整理本条语音正文，关联引用保持原文。")


def main():
    parser = argparse.ArgumentParser(description="从原始版生成整理版底稿，并展开一层关联原文")
    parser.add_argument("report", help="本轮 export.json")
    parser.add_argument("date", help="要处理的创建日期 YYYY-MM-DD")
    parser.add_argument("--related", help="按关联 ID 获取的完整笔记 JSON，可包含本轮已获取的笔记")
    args = parser.parse_args()
    prepare_organized(args.report, args.date, args.related)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"整理版尚未完成：{exc}", file=sys.stderr)
        sys.exit(1)
