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

from scan_state import BEIJING_TZ, load_baseline, metadata, select_memos, write_json

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


async def enumerate_memos(search, start, end, concurrency=3):
    """Bisect saturated date ranges; a saturated single day is incomplete."""
    semaphore = asyncio.Semaphore(concurrency)

    async def visit(first, last):
        async with semaphore:
            page = await search({"start_date": first.isoformat(), "end_date": last.isoformat(), "limit": PAGE_LIMIT})
        if not isinstance(page, list) or len(page) > PAGE_LIMIT:
            raise ScanError("memo_search 返回了无效的分页结果")
        items = [metadata(item) for item in page]
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


async def scan(args):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    manifest_path = Path(args.manifest).expanduser()
    if manifest_path.exists():
        raise ScanError("清单路径已存在，请为本轮选择新路径，避免误用旧结果")
    output = Path(args.output_dir).expanduser().resolve()
    baseline = load_baseline(output)
    cutoff = datetime.now(BEIJING_TZ)
    if baseline is not None and cutoff <= baseline:
        raise ValueError("本轮扫描时间必须晚于现有基线")
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

            index = await enumerate_memos(search, date(2000, 1, 1), cutoff.date(), args.concurrency)
    manifest = {
        "schema_version": 1,
        "scan_id": uuid.uuid4().hex,
        "scan_complete": True,
        "baseline_at": baseline.isoformat() if baseline is not None else None,
        "scan_started_at": cutoff.isoformat(),
        "output_dir": str(output),
        "memos": select_memos(index, baseline, cutoff),
    }
    write_json(manifest_path, manifest)
    print(f"扫描完成：{len(manifest['memos'])} 条待导出笔记；清单：{manifest_path}")
    if baseline is None:
        print("首次扫描只建立基线，请运行 finish_scan.py 完成初始化。")
    else:
        print("请交给 flomo-tag-diaries 按 ID 清单导出；扫描阶段未推进基线。")


def main():
    parser = argparse.ArgumentParser(description="并发扫描 flomo，仅生成需要导出的笔记 ID 清单")
    parser.add_argument("output_dir", help="已有笔记输出目录，用于读取统一基线")
    parser.add_argument("--manifest", required=True, help="待导出清单 JSON 的保存位置")
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
