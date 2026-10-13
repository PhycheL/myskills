#!/usr/bin/env python3
"""Record a completed selection/export run. Does not fetch or export memos."""
import argparse
import sys

from scan_state import finish_scan


def main():
    parser = argparse.ArgumentParser(description="检查导出结果，记录运行结果并推进统一基线")
    parser.add_argument("manifest", help="scan_edited.py 生成的待导出清单")
    parser.add_argument("--report", help="通用导出结果报告；有命中时必填")
    args = parser.parse_args()
    count = finish_scan(args.manifest, args.report)
    label = "已建立基线，改动数未统计" if count is None else f"{count} 条已导出笔记"
    print(f"本轮已完成：{label}；运行记录和统一基线已更新。")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"未完成：{exc}。基线未推进。", file=sys.stderr)
        sys.exit(1)
