"""Run with: python3 -B -m unittest discover -s tests -v."""
import asyncio
import importlib.util
import json
import sys
import tempfile
import unittest
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import scan_memos as scanner
import scan_state as state

BASELINE = "2026-10-12T09:00:00+08:00"
CUTOFF = "2026-10-19T09:00:00+08:00"


def memo(key="edited", **fields):
    value = {"id": key, "created_at": "2026-08-25T11:31:00+08:00",
             "updated_at": "2026-10-13T23:03:00+08:00", "content": "不应进入清单的正文",
             "tags": ["流水账"],
             "files": [{"url": "不应进入清单的附件"}]}
    return dict(value, **fields)


class SelectionTests(unittest.TestCase):
    def select(self, memos, baseline=BASELINE, cutoff=CUTOFF):
        return state.select_memos(memos, state.parse_dt(baseline) if baseline else None,
                                  state.parse_dt(cutoff))

    def test_weekly_selection_uses_creation_baseline_and_cutoff(self):
        selected = self.select([
            memo("old-edited"), memo("at-baseline", updated_at=BASELINE),
            memo("before", updated_at="2026-10-11T23:59:59+08:00"),
            memo("new-untouched", created_at="2026-10-14T12:00:00+08:00",
                 updated_at="2026-10-14T12:00:00+08:00"),
            memo("new-edited", created_at="2026-10-14T12:00:00+08:00",
                 updated_at="2026-10-15T12:00:00+08:00"),
            memo("at-cutoff", updated_at=CUTOFF),
            memo("during-scan", updated_at="2026-10-19T09:00:01+08:00"),
        ])
        self.assertEqual({m["id"] for m in selected}, {"old-edited", "new-edited", "at-cutoff"})
        self.assertTrue(all(set(m) == {"id", "created_at", "updated_at"} for m in selected))

    def test_monthly_selection_and_changes_during_previous_scan(self):
        selected = self.select([
            memo("old-edit"), memo("during-last-scan", updated_at="2026-10-19T09:00:01+08:00"),
            memo("month-edit", updated_at="2026-11-02T10:00:00+08:00"),
        ], baseline=CUTOFF, cutoff="2026-11-19T09:00:00+08:00")
        self.assertEqual({m["id"] for m in selected}, {"during-last-scan", "month-edit"})

    def test_equivalent_timezones_and_initialization(self):
        memos = [memo("untouched", created_at="2026-10-14T01:00:00Z",
                      updated_at="2026-10-14T09:00:00+08:00"),
                 memo("baseline", updated_at="2026-10-12T01:00:00Z"),
                 memo("edited", created_at="2026-08-25T18:00:00Z",
                      updated_at="2026-10-13T15:03:00Z")]
        result = self.select(memos)
        self.assertEqual([m["id"] for m in result], ["edited"])
        self.assertTrue(result[0]["created_at"].startswith("2026-08-26"))
        self.assertEqual(self.select(memos, baseline=None), [])
        self.assertEqual(state.parse_dt("2026-10-12 09:00:00"), state.parse_dt(BASELINE))

    def test_duplicate_ids_keep_latest_and_count_once(self):
        selected = self.select([memo(), memo(updated_at="2026-10-15T12:00:00+08:00"), memo()])
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["updated_at"], "2026-10-15T12:00:00+08:00")
        self.assertEqual(self.select([memo(), memo(updated_at="2026-10-19T09:00:01+08:00")]), [])

    def test_invalid_metadata_is_not_silently_omitted(self):
        for bad in (memo(id=""), memo(updated_at=None), memo(created_at="2026-08-25")):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.select([bad])

    def test_created_mode_uses_only_creation_time_and_half_open_window(self):
        start, end = state.parse_dt("2026-10-12T00:00:00+08:00"), state.parse_dt("2026-10-13T00:00:00+08:00")
        result = state.select_memos([
            memo("at-start", created_at=start.isoformat(), updated_at=start.isoformat()),
            memo("new-edited-later", created_at="2026-10-12T10:00:00+08:00", updated_at=CUTOFF),
            memo("at-end", created_at=end.isoformat(), updated_at=end.isoformat()),
            memo("old-edited", updated_at="2026-10-12T10:00:00+08:00"),
        ], start, end, mode="created")
        self.assertEqual({m["id"] for m in result}, {"at-start", "new-edited-later"})

    def test_optional_tag_works_in_both_modes_without_changing_output_shape(self):
        values = [memo("matching"), memo("other", tags=["其他"]), memo("untagged", tags=[])]
        for mode, start in (("modified", BASELINE), ("created", "2026-08-25T00:00:00+08:00")):
            with self.subTest(mode=mode):
                all_memos = state.select_memos(values, state.parse_dt(start), state.parse_dt(CUTOFF), mode=mode)
                tagged = state.select_memos(values, state.parse_dt(start), state.parse_dt(CUTOFF), mode=mode, tag="#流水账")
                self.assertEqual(len(all_memos), 3)
                self.assertEqual(tagged, [state.metadata(values[0])])

    def test_tag_filter_applies_to_latest_duplicate_and_requires_tags(self):
        values = [memo(tags=["流水账"]), memo(tags=["其他"], updated_at="2026-10-15T12:00:00+08:00")]
        self.assertEqual(state.select_memos(values, state.parse_dt(BASELINE), state.parse_dt(CUTOFF), tag="流水账"), [])
        value = memo()
        value.pop("tags")
        self.assertEqual(len(self.select([value])), 1)
        with self.assertRaisesRegex(ValueError, "tags"):
            state.select_memos([value], state.parse_dt(BASELINE), state.parse_dt(CUTOFF), tag="流水账")

    def test_response_formats_and_tool_errors(self):
        values = [memo()]
        for payload in (values, {"memos": values}):
            self.assertEqual(scanner.result_memos(SimpleNamespace(structuredContent=payload)), values)
            response = SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(payload))])
            self.assertEqual(scanner.result_memos(response), values)
        self.assertEqual(scanner.result_memos(SimpleNamespace(structuredContent={"memos": []})), [])
        for response in (SimpleNamespace(isError=True, structuredContent={"memos": []}),
                         SimpleNamespace(structuredContent={"error": "failed"}),
                         SimpleNamespace(content=[SimpleNamespace(type="text", text="not JSON")])):
            with self.assertRaises(ValueError):
                scanner.result_memos(response)

    def test_diagnostics_hide_credentials_but_show_local_failures(self):
        http_error = RuntimeError("https://host/secret-token")
        http_error.response = SimpleNamespace(status_code=401)
        self.assertEqual(scanner.failure_label(http_error), "HTTP 401")
        self.assertEqual(scanner.failure_label(RuntimeError("secret-token")), "RuntimeError")
        self.assertIn("单日仍满", scanner.failure_label(scanner.ScanError("单日仍满 50 条")))


class EnumerationTests(unittest.IsolatedAsyncioTestCase):
    async def test_saturated_ranges_are_complete_with_bounded_concurrency(self):
        values = [memo(str(i), created_at=f"2026-10-{12 + i // 20:02}T10:00:00+08:00")
                  for i in range(160)]
        active = maximum = 0
        calls = []

        async def search(args):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            calls.append(args)
            await asyncio.sleep(0.005)
            active -= 1
            return [m for m in values if args["start_date"] <= m["created_at"][:10] <= args["end_date"]][:50]

        result = await scanner.enumerate_memos(search, date(2026, 10, 12), date(2026, 10, 19), 3)
        self.assertEqual({m["id"] for m in result}, {m["id"] for m in values})
        self.assertGreater(maximum, 1)
        self.assertLessEqual(maximum, 3)
        self.assertTrue(all(args["limit"] == 50 for args in calls))
        self.assertTrue(all("content" not in m and "files" not in m for m in result))

    async def test_saturated_single_day_and_failed_branch_are_incomplete(self):
        with self.assertRaisesRegex(ValueError, "单日仍满 50"):
            await scanner.enumerate_memos(mock.AsyncMock(return_value=[memo(str(i)) for i in range(50)]),
                                          date(2026, 10, 12), date(2026, 10, 12))
        with self.assertRaisesRegex(OSError, "offline"):
            await scanner.enumerate_memos(mock.AsyncMock(side_effect=OSError("offline")),
                                          date(2026, 10, 12), date(2026, 10, 19))


@unittest.skipUnless(importlib.util.find_spec("mcp"), "MCP SDK 未安装；用 uv run --with 'mcp>=1.12,<2' 运行协议测试")
class MCPProtocolTests(unittest.IsolatedAsyncioTestCase):
    """Real SDK client/server over memory streams; no live flomo data or network."""
    async def asyncSetUp(self):
        from mcp.server import Server
        from mcp.shared.memory import create_client_server_memory_streams
        from mcp.types import TextContent, Tool
        import anyio

        self.tmp = tempfile.TemporaryDirectory(prefix="flomo-mcp-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.output = self.root / "output"
        self.output.mkdir()
        self.baseline = self.output / state.BASELINE_FILE
        state.write_json(self.baseline, {"baseline_at": BASELINE})
        self.config = self.root / "mcp.json"
        state.write_json(self.config, {"mcpServers": {"flomo": {
            "type": "http", "url": "https://fixture.invalid/mcp/secret", "headers": {"X-Test": "yes"}}}})
        self.args = SimpleNamespace(state_dir=str(self.output), manifest=str(self.root / "selection.json"),
                                    mode="modified", tag=None, date=None, start_at=None, end_at=None,
                                    baseline=None, ids_file=None,
                                    mcp_config=str(self.config), server="flomo", concurrency=3, timeout=2, retries=0)
        self.values = [memo()]
        self.failures = 0
        self.calls = []
        self.delay = 0
        self.active = self.maximum = 0
        server = Server("offline-flomo")

        @server.list_tools()
        async def list_tools():
            return [Tool(name="memo_search", description="fixture search", inputSchema={"type": "object"})]

        @server.call_tool(validate_input=False)
        async def call_tool(name, arguments):
            self.calls.append((name, arguments))
            if self.failures:
                self.failures -= 1
                raise ValueError("fixture tool failure")
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            try:
                await asyncio.sleep(self.delay)
                values = [m for m in self.values
                          if arguments["start_date"] <= m["created_at"][:10] <= arguments["end_date"]][:50]
                return [TextContent(type="text", text=json.dumps({"memos": values}))]
            finally:
                self.active -= 1

        @asynccontextmanager
        async def transport(url, headers, timeout):
            self.assertEqual(url, "https://fixture.invalid/mcp/secret")
            self.assertEqual(headers, {"X-Test": "yes"})
            async with create_client_server_memory_streams() as (client_streams, server_streams):
                async with anyio.create_task_group() as group:
                    group.start_soon(server.run, *server_streams, server.create_initialization_options())
                    try:
                        yield (*client_streams, lambda: None)
                    finally:
                        group.cancel_scope.cancel()

        patcher = mock.patch("mcp.client.streamable_http.streamablehttp_client", transport)
        patcher.start()
        self.addCleanup(patcher.stop)
        clock = mock.patch.object(scanner, "datetime")
        clock.start().now.return_value = datetime.fromisoformat(CUTOFF)
        self.addCleanup(clock.stop)

    async def test_scan_calls_only_search_and_writes_metadata_without_state_changes(self):
        await scanner.scan(self.args)
        data = state.load_manifest(self.args.manifest)
        self.assertEqual(data["memos"], [state.metadata(memo())])
        self.assertEqual(data["query"]["start_at"], BASELINE)
        self.assertEqual(data["scan_started_at"], CUTOFF)
        self.assertEqual(self.calls, [("memo_search", {"start_date": "2000-01-01", "end_date": "2026-10-19", "limit": 50})])
        self.assertEqual(state.load_baseline(self.output), state.parse_dt(BASELINE))
        self.assertEqual({p.name for p in self.output.iterdir()}, {state.BASELINE_FILE})
        self.assertEqual(json.loads(Path(data["ids_file"]).read_text()), ["edited"])
        self.assertNotIn("output_dir", data)

    async def test_initial_scan_requires_explicit_finish(self):
        self.baseline.unlink()
        await scanner.scan(self.args)
        manifest = state.load_manifest(self.args.manifest)
        self.assertIsNone(manifest["query"]["start_at"])
        self.assertEqual(manifest["memos"], [])
        self.assertFalse(self.baseline.exists())
        self.assertFalse((self.output / state.RUNS_FILE).exists())

    async def test_failed_scan_writes_neither_manifest_nor_success(self):
        self.failures = 1
        with self.assertRaises(Exception):
            await scanner.scan(self.args)
        self.assertFalse(Path(self.args.manifest).exists())
        self.assertEqual(state.load_baseline(self.output), state.parse_dt(BASELINE))
        self.assertFalse((self.output / state.RUNS_FILE).exists())

    async def test_retry_recovers_without_calling_export_tools(self):
        self.failures, self.args.retries = 1, 1
        await scanner.scan(self.args)
        self.assertEqual([name for name, _ in self.calls], ["memo_search", "memo_search"])
        self.assertEqual(len(state.load_manifest(self.args.manifest)["memos"]), 1)

    async def test_mcp_session_supports_concurrent_date_queries(self):
        self.values = [memo(str(i), created_at=f"2026-08-{12 + i // 20:02}T10:00:00+08:00")
                       for i in range(160)]
        self.delay = 0.005
        await scanner.scan(self.args)
        selected = state.load_manifest(self.args.manifest)["memos"]
        self.assertEqual({m["id"] for m in selected}, {m["id"] for m in self.values})
        self.assertGreater(self.maximum, 1)
        self.assertLessEqual(self.maximum, self.args.concurrency)

    async def test_timeout_cannot_be_recorded_as_zero_changes(self):
        self.delay, self.args.timeout = 0.2, 0.05
        with self.assertRaises(Exception):
            await scanner.scan(self.args)
        self.assertFalse(Path(self.args.manifest).exists())
        self.assertEqual(state.load_baseline(self.output), state.parse_dt(BASELINE))

    async def test_existing_manifest_is_not_overwritten_or_reused(self):
        Path(self.args.manifest).write_text("old result", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "清单路径已存在"):
            await scanner.scan(self.args)
        self.assertEqual(Path(self.args.manifest).read_text(), "old result")
        self.assertEqual(self.calls, [])

    async def test_created_query_only_requests_selected_day_and_keeps_modified_progress(self):
        self.args.mode, self.args.date = "created", "2026-10-12"
        self.values = [memo("new", created_at="2026-10-12T10:00:00+08:00"),
                       memo("old", updated_at="2026-10-12T11:00:00+08:00")]
        await scanner.scan(self.args)
        data = state.load_manifest(self.args.manifest)
        self.assertEqual([m["id"] for m in data["memos"]], ["new"])
        self.assertEqual(self.calls, [("memo_search", {"start_date": "2026-10-12", "end_date": "2026-10-12", "limit": 50})])
        self.assertEqual(state.load_baseline(self.output), state.parse_dt(BASELINE))
        self.assertEqual({p.name for p in self.output.iterdir()}, {state.BASELINE_FILE})

    async def test_standalone_tagged_query_needs_no_output_or_state_directory(self):
        self.args.state_dir, self.args.baseline, self.args.tag = None, BASELINE, "#流水账"
        self.values = [memo("matching"), memo("other", tags=["其他"])]
        await scanner.scan(self.args)
        data = state.load_manifest(self.args.manifest)
        self.assertIsNone(data["state_dir"])
        self.assertEqual(json.loads(Path(data["ids_file"]).read_text()), ["matching"])
        self.assertEqual(data["query"]["tag"], "流水账")
        self.assertEqual(data["memos"], [state.metadata(self.values[0])])
        with self.assertRaisesRegex(ValueError, "独立检索"):
            state.finish_scan(self.args.manifest)

    async def test_tagged_daily_scan_checks_saturation_before_filtering(self):
        self.args.mode, self.args.date, self.args.tag = "created", "2026-10-12", "流水账"
        self.values = [memo(str(i), created_at="2026-10-12T10:00:00+08:00",
                            tags=["流水账"] if i == 0 else ["其他"]) for i in range(50)]
        with self.assertRaises(Exception) as caught:
            await scanner.scan(self.args)
        self.assertIn("单日仍满", scanner.failure_label(caught.exception))
        self.assertFalse(Path(self.args.manifest).exists())
        self.assertFalse(Path(self.args.manifest).with_suffix(".ids.json").exists())

    async def test_unfinished_date_or_missing_created_window_fails_before_requests(self):
        self.args.mode = "created"
        for day in (None, "2026-10-19"):
            self.args.date = day
            with self.subTest(day=day), self.assertRaises(ValueError):
                await scanner.scan(self.args)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
