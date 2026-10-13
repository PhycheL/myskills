#!/usr/bin/env python3
"""Commit query progress from a caller-supplied completion record."""
import argparse
import sys

from scan_state import finish_scan


def main():
    parser = argparse.ArgumentParser(description="核对本轮完成记录，提交对应检索任务的进度")
    parser.add_argument("manifest", help="scan_memos.py 生成的检索运行清单")
    parser.add_argument("--report", help="调用方提供的本轮完成记录 JSON；有命中时必填")
    args = parser.parse_args()
    count = finish_scan(args.manifest, args.report)
    label = "已建立基线，改动数未统计" if count is None else f"{count} 条已完成笔记"
    print(f"本轮已完成：{label}；对应任务的运行记录和进度已更新。")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"未完成：{exc}。任务进度未更新。", file=sys.stderr)
        sys.exit(1)
