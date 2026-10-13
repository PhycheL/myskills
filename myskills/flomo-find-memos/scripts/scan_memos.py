#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.12,<2"]
# ///
"""Read flomo MCP concurrently and write only a metadata/ID selection manifest."""
import argparse
import asyncio
import json
import logging
import sys
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from scan_state import BEIJING_TZ, load_baseline, metadata, memo_tags, normalize_tag, parse_dt, select_memos, write_json

PAGE_LIMIT = 50


class ScanError(ValueError):
    """A locally generated diagnostic that never includes connection secrets."""


def failure_label(error):
    """Report transport status without exposing credential-bearing URLs."""
    pending, seen = [error], set()
    while pending:
        item = pending.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        if isinstance(item, ScanError):
            return str(item)
        status = getattr(getattr(item, "response", None), "status_code", None)
        if isinstance(status, int):
            return f"HTTP {status}"
        pending.extend(getattr(item, "exceptions", []))
        pending.extend(child for child in (item.__cause__, item.__context__) if child is not None)
    return type(error).__name__


def result_memos(result):
    if getattr(result, "isError", False):
        raise ScanError("memo_search 返回工具错误")
    payloads = [getattr(result, "structuredContent", None)]
    for part in getattr(result, "content", []):
        if getattr(part, "type", None) == "text":
            try:
                payloads.append(json.loads(part.text))
            except (ValueError, TypeError):
                continue
    for payload in payloads:
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict) and isinstance(payload.get("memos"), list):
            return payload["memos"]
    raise ScanError("memo_search 响应中缺少 memos 数组；不能当成零条结果")


async def enumerate_memos(search, start, end, concurrency=3, keep_tags=False):
    """Bisect saturated date ranges; a saturated single day is incomplete."""
    semaphore = asyncio.Semaphore(concurrency)

    async def visit(first, last):
        async with semaphore:
            page = await search({"start_date": first.isoformat(), "end_date": last.isoformat(), "limit": PAGE_LIMIT})
        if not isinstance(page, list) or len(page) > PAGE_LIMIT:
            raise ScanError("memo_search 返回了无效的分页结果")
        items = [metadata(item) for item in page]
        if keep_tags:
            for item, original in zip(items, page):
                item["tags"] = memo_tags(original)
        del page
        if len(items) == PAGE_LIMIT:
            if first == last:
                raise ScanError(f"{first.isoformat()} 单日仍满 50 条，无法确认扫描完整")
            middle = first + (last - first) // 2
            left, right = await asyncio.gather(
                visit(first, middle), visit(middle + timedelta(days=1), last)
            )
            return left + right
        return items

    return await visit(start, end)


def load_connection(config_path, server_name):
    config = json.loads(Path(config_path).expanduser().read_text(encoding="utf-8"))
    server = config.get("mcpServers", {}).get(server_name)
    if not isinstance(server, dict) or server.get("disabled"):
        raise ValueError("MCP 配置中没有启用的指定服务")
    if server.get("type", "http") not in ("http", "streamable-http", "streamable_http"):
        raise ValueError("扫描入口需要 HTTP MCP 服务配置")
    if not isinstance(server.get("url"), str) or not server["url"].startswith(("https://", "http://")):
        raise ValueError("MCP 服务缺少 HTTP URL")
    headers = server.get("headers") or {}
    if not isinstance(headers, dict):
        raise ValueError("MCP 请求头配置无效")
    return server["url"], headers


def query_window(args, scanned):
    tag = normalize_tag(args.tag)
    if args.mode == "modified":
        if args.date or args.start_at or args.end_at:
            raise ScanError("修改模式使用 --baseline 或状态目录中的基线")
        if args.baseline and args.state_dir:
            raise ScanError("--baseline 用于独立检索；记录进度时使用 --state-dir 中的基线")
        if not args.baseline and not args.state_dir:
            raise ScanError("修改检索需要 --baseline 或 --state-dir")
        start = parse_dt(args.baseline) if args.baseline else load_baseline(Path(args.state_dir).expanduser(), tag)
        end = scanned
    else:
        if args.baseline:
            raise ScanError("新增检索使用 --date 或 --start-at/--end-at")
        if args.date:
            if args.start_at or args.end_at:
                raise ScanError("--date 与 --start-at/--end-at 选择一组")
            start = parse_dt(date.fromisoformat(args.date).isoformat() + "T00:00:00+08:00")
            end = start + timedelta(days=1)
        elif args.start_at:
            start = parse_dt(args.start_at)
            end = parse_dt(args.end_at) if args.end_at else scanned
        else:
            raise ScanError("新增检索需要明确指定 --date 或 --start-at")
    if end > scanned:
        raise ScanError("目标时间窗口尚未结束，请指定已结束的日期或截止时间")
    if start is not None and start >= end:
        raise ScanError("查询截止时间必须晚于开始时间")
    return {"mode": args.mode, "tag": tag,
            "start_at": start.isoformat() if start else None, "end_at": end.isoformat()}


async def scan(args):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    manifest_path = Path(args.manifest).expanduser().resolve()
    ids_path = Path(args.ids_file).expanduser().resolve() if args.ids_file else manifest_path.with_suffix(".ids.json")
    if manifest_path == ids_path:
        raise ScanError("ID 文件和运行清单必须使用不同路径")
    if manifest_path.exists() or ids_path.exists():
        raise ScanError("清单路径已存在，请为本轮选择新路径，避免误用旧结果")
    cutoff = datetime.now(BEIJING_TZ)
    query = query_window(args, cutoff)
    start = parse_dt(query["start_at"]) if query["start_at"] else None
    end = parse_dt(query["end_at"])
    first = date(2000, 1, 1) if args.mode == "modified" else start.date()
    last = cutoff.date() if args.mode == "modified" else (end - timedelta(microseconds=1)).date()
    url, headers = load_connection(args.mcp_config, args.server)
    async with streamablehttp_client(url, headers=headers, timeout=timedelta(seconds=args.timeout)) as (read, write, _):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), args.timeout)

            async def search(arguments):
                for attempt in range(args.retries + 1):
                    try:
                        result = await asyncio.wait_for(session.call_tool("memo_search", arguments), args.timeout)
                        return result_memos(result)
                    except Exception as exc:
                        if attempt == args.retries:
                            raise RuntimeError("memo_search 在有限重试后仍失败") from exc
                        await asyncio.sleep(min(2 ** attempt, 4))

            index = await enumerate_memos(search, first, last, args.concurrency, keep_tags=query["tag"] is not None)
    selected = select_memos(index, start, end, mode=args.mode, tag=query["tag"])
    manifest = {
        "schema_version": 2,
        "scan_id": uuid.uuid4().hex,
        "scan_complete": True,
        "query": query,
        "scan_started_at": cutoff.isoformat(),
        "state_dir": str(Path(args.state_dir).expanduser().resolve()) if args.state_dir else None,
        "ids_file": str(ids_path),
        "memos": selected,
    }
    write_json(ids_path, [item["id"] for item in selected])
    try:
        write_json(manifest_path, manifest)
    except Exception:
        ids_path.unlink()
        raise
    print(f"检索完成：{len(selected)} 条；ID 清单：{ids_path}；运行清单：{manifest_path}")
    if args.mode == "modified" and start is None:
        print("首次完整检索，改动数未统计；尚未提交新基线。")


def main():
    parser = argparse.ArgumentParser(description="按时间和可选标签检索 flomo，生成笔记 ID 清单")
    parser.add_argument("--mode", choices=("modified", "created"), required=True, help="修改检索或新增检索")
    parser.add_argument("--tag", help="可选标签，按当前 tags 精确匹配；省略时覆盖全部标签")
    parser.add_argument("--baseline", help="独立修改检索的起点；与 --state-dir 二选一")
    parser.add_argument("--state-dir", help="可选的任务状态目录；修改模式从中读取对应范围的基线")
    parser.add_argument("--date", help="新增检索的北京时间自然日 YYYY-MM-DD，要求该日已结束")
    parser.add_argument("--start-at", help="新增检索的开始时间，包含该时刻")
    parser.add_argument("--end-at", help="新增检索的结束时间，不含该时刻；默认本次扫描开始时间")
    parser.add_argument("--manifest", required=True, help="检索运行清单 JSON；用于收尾记账")
    parser.add_argument("--ids-file", help="纯 ID 数组 JSON，默认 <manifest文件名>.ids.json")
    parser.add_argument("--mcp-config", default="~/.workbuddy/mcp.json", help="本地 MCP 配置文件")
    parser.add_argument("--server", default="flomo", help="配置中的 MCP 服务名")
    parser.add_argument("--concurrency", type=int, default=3, help="最大并发查询数，默认 3")
    parser.add_argument("--timeout", type=float, default=60, help="单次请求超时秒数")
    parser.add_argument("--retries", type=int, default=2, help="单次请求最多重试次数")
    args = parser.parse_args()
    if args.concurrency < 1 or args.timeout <= 0 or args.retries < 0:
        parser.error("并发数和超时必须大于零，重试次数不能为负")
    asyncio.run(scan(args))


if __name__ == "__main__":
    # SDK log tracebacks can include the MCP URL and its embedded credentials.
    logging.disable(logging.CRITICAL)
    try:
        main()
    except Exception as exc:
        # SDK/network exceptions can contain the credential-bearing MCP URL.
        print(f"扫描失败（{failure_label(exc)}）；请检查 MCP 连接、响应完整性和依赖。未写入成功清单、运行记录或新基线。", file=sys.stderr)
        sys.exit(1)
