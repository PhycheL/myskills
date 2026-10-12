"""Offline behavior checks: python3 -B -m unittest discover -s tests -v."""
import csv
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_edited.py"
BASELINE_FILE = ".flomo_edited_baseline.json"
RUNS_FILE = "flomo-edited-memos-runs.csv"
BASELINE = "2026-10-12T09:00:00+08:00"
CUTOFF = "2026-10-19T09:00:00+08:00"
BEIJING_TZ = timezone(timedelta(hours=8))


class ExportBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="flomo-edited-test-", dir="/tmp")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.output = self.root / "output"
        self.output.mkdir()
        self.state = self.output / BASELINE_FILE
        self.log = self.output / RUNS_FILE
        self.state.write_text(json.dumps({"baseline_at": BASELINE}), encoding="utf-8")

    def memo(self, memo_id="edited", **fields):
        memo = {
            "id": memo_id,
            "created_at": "2026-08-25T11:31:00+08:00",
            "updated_at": "2026-10-13T23:03:00+08:00",
            "content": "#流水账\n\n原文 memo\\_id 🙂\n第二行",
            "content_truncated": False,
        }
        memo.update(fields)
        return memo

    def run_scan(self, memos, cutoff=CUTOFF, complete=True, success=True):
        scan = self.root / "scan.json"
        scan.write_text(json.dumps({
            "scan_started_at": cutoff,
            "scan_complete": complete,
            "memos": memos,
        }, ensure_ascii=False), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-B", str(SCRIPT), str(scan), str(self.output)],
            capture_output=True, text=True, check=False,
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def rows(self):
        with self.log.open(encoding="utf-8", newline="") as handle:
            return list(csv.reader(handle))

    def baseline(self):
        return json.loads(self.state.read_text(encoding="utf-8"))["baseline_at"]

    def test_first_scan_initializes_without_claiming_zero_changes(self):
        self.state.unlink()
        result = self.run_scan([self.memo()], cutoff=BASELINE)
        self.assertIn("首次完整扫描", result.stdout)
        self.assertEqual(self.baseline(), BASELINE)
        self.assertEqual(self.rows()[1][1], "")
        self.assertEqual(list(self.output.glob("*.md")), [])

    def test_weekly_scan_uses_creation_and_baseline_conditions(self):
        memos = [
            self.memo("old-edited"),
            self.memo("at-baseline", updated_at=BASELINE),
            self.memo("before-baseline", updated_at="2026-10-11T23:59:59+08:00"),
            self.memo("new-untouched", created_at="2026-10-14T12:00:00+08:00",
                      updated_at="2026-10-14T12:00:00+08:00"),
            self.memo("new-edited", created_at="2026-10-14T12:00:00+08:00",
                      updated_at="2026-10-15T12:00:00+08:00"),
            self.memo("today-edited", created_at="2026-10-19T08:00:00+08:00",
                      updated_at="2026-10-19T08:30:00+08:00"),
            self.memo("at-cutoff", updated_at=CUTOFF),
        ]
        self.run_scan(memos)
        self.assertEqual(self.rows()[-1][1], "4")
        self.assertEqual(self.baseline(), CUTOFF)
        text = "\n".join(path.read_text(encoding="utf-8") for path in self.output.glob("*.md"))
        for memo_id in ("old-edited", "new-edited", "today-edited", "at-cutoff"):
            self.assertIn("memo_id=" + memo_id + ")", text)
        for memo_id in ("at-baseline", "before-baseline", "new-untouched"):
            self.assertNotIn("memo_id=" + memo_id + ")", text)

    def test_monthly_scan_reads_only_the_single_saved_baseline(self):
        self.run_scan([self.memo()])
        self.assertEqual(set(json.loads(self.state.read_text())), {"baseline_at"})
        self.run_scan([
            self.memo(),
            self.memo("month-edit", updated_at="2026-11-02T10:00:00+08:00"),
        ], cutoff="2026-11-19T09:00:00+08:00")
        self.assertEqual([row[1] for row in self.rows()[1:]], ["1", "1"])
        self.assertEqual(self.baseline(), "2026-11-19T09:00:00+08:00")

    def test_duplicate_ids_export_only_latest_complete_content(self):
        self.run_scan([
            self.memo(content="旧版本"),
            self.memo(updated_at="2026-10-15T12:00:00+08:00", content="最新版本"),
            self.memo(updated_at="2026-10-15T12:00:00+08:00", content="截断",
                      content_truncated=True),
        ])
        text = (self.output / "2026-08-25-修改.md").read_text(encoding="utf-8")
        self.assertIn("最新版本", text)
        self.assertNotIn("旧版本", text)
        self.assertNotIn("截断", text)
        self.assertEqual(text.count("memo_id=edited)"), 1)
        self.assertEqual(self.rows()[-1][1], "1")

    def test_cross_day_order_and_verbatim_body(self):
        self.run_scan([
            self.memo("later", updated_at="2026-10-14T01:00:00+08:00"),
            self.memo("earlier"),
        ])
        text = (self.output / "2026-08-25-修改.md").read_text(encoding="utf-8")
        self.assertLess(text.index("### 2026-10-13 23:03"), text.index("### 2026-10-14 01:00"))
        self.assertIn("2026-10-12 09:00:00 至 2026-10-19 09:00:00", text)
        self.assertIn("原文 memo\\_id 🙂\n第二行", text)
        self.assertNotIn("#流水账", text)

    def test_equivalent_timestamps_are_compared_as_instants(self):
        self.run_scan([
            self.memo("timezone-untouched", created_at="2026-10-14T01:00:00Z",
                      updated_at="2026-10-14T09:00:00+08:00"),
            self.memo("timezone-baseline", updated_at="2026-10-12T01:00:00Z"),
            self.memo("timezone-edited", created_at="2026-08-25T18:00:00Z",
                      updated_at="2026-10-13T15:03:00Z"),
        ])
        self.assertEqual(self.rows()[-1][1], "1")
        self.assertTrue((self.output / "2026-08-26-修改.md").exists())

    def test_zero_change_runs_append_actual_run_date_and_advance_baseline(self):
        self.run_scan([])
        self.run_scan([], cutoff="2026-10-26T09:00:00+08:00")
        rows = self.rows()
        self.assertEqual(rows[0], ["运行日期", "改动笔记数"])
        today = datetime.now(BEIJING_TZ).date().isoformat()
        self.assertEqual(rows[1:], [[today, "0"], [today, "0"]])
        self.assertEqual(self.baseline(), "2026-10-26T09:00:00+08:00")

    def test_edits_during_scan_are_exported_next_time(self):
        memo = self.memo(updated_at="2026-10-19T09:00:01+08:00")
        result = self.run_scan([memo])
        self.assertIn("留待下次扫描", result.stdout)
        self.assertEqual(self.rows()[-1][1], "0")
        self.run_scan([memo], cutoff="2026-10-26T09:00:00+08:00")
        self.assertEqual(self.rows()[-1][1], "1")

    def test_incomplete_scan_cannot_initialize_or_advance(self):
        before = self.state.read_bytes()
        self.run_scan([], complete=False, success=False)
        self.assertEqual(self.state.read_bytes(), before)
        self.assertFalse(self.log.exists())
        self.state.unlink()
        self.run_scan([], complete=False, success=False)
        self.assertFalse(self.state.exists())
        self.assertFalse(self.log.exists())

    def test_missing_or_truncated_candidate_data_keeps_baseline(self):
        cases = [
            self.memo(content_truncated=True),
            self.memo(content=None),
            self.memo(id=""),
            self.memo(updated_at="not-a-timestamp"),
        ]
        before = self.state.read_bytes()
        for memo in cases:
            with self.subTest(memo=memo):
                self.run_scan([memo], success=False)
                self.assertEqual(self.state.read_bytes(), before)
                self.assertFalse(self.log.exists())
                self.assertEqual(list(self.output.glob("*.md")), [])

    def test_invalid_or_backward_baseline_cannot_reset_state(self):
        before = self.state.read_bytes()
        self.run_scan([], cutoff="2026-10-11T09:00:00+08:00", success=False)
        self.assertEqual(self.state.read_bytes(), before)
        self.state.write_text("invalid JSON", encoding="utf-8")
        self.run_scan([], success=False)
        self.assertEqual(self.state.read_text(), "invalid JSON")
        self.assertFalse(self.log.exists())

    def test_export_failure_does_not_record_success_or_advance(self):
        (self.output / "2026-08-25-修改.md").mkdir()
        before = self.state.read_bytes()
        self.run_scan([self.memo()], success=False)
        self.assertEqual(self.state.read_bytes(), before)
        self.assertFalse(self.log.exists())

    def test_log_failure_keeps_old_baseline(self):
        self.log.mkdir()
        before = self.state.read_bytes()
        self.run_scan([self.memo()], success=False)
        self.assertEqual(self.state.read_bytes(), before)
        self.assertTrue(self.log.is_dir())

    def test_baseline_commit_failure_rolls_back_success_log(self):
        self.run_scan([])
        before_state, before_log = self.state.read_bytes(), self.log.read_bytes()
        spec = importlib.util.spec_from_file_location("flomo_export_under_test", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        real_write = module.atomic_write

        def fail_baseline(path, text):
            if Path(path).name == BASELINE_FILE:
                raise OSError("模拟基线写入失败")
            real_write(path, text)

        with mock.patch.object(module, "atomic_write", side_effect=fail_baseline):
            with self.assertRaises(OSError):
                module.record_success(str(self.output), module.parse_dt("2026-10-26T09:00:00+08:00"),
                                      2, "2026-10-26")
        self.assertEqual(self.state.read_bytes(), before_state)
        self.assertEqual(self.log.read_bytes(), before_log)

    def test_empty_voice_note_downloads_locally_and_counts_as_modified(self):
        source = self.root / "source.m4a"
        source.write_bytes(b"offline voice fixture")
        self.run_scan([self.memo(content="", has_voice=True, files=[
            {"type": "recorded", "url": source.as_uri()},
        ])])
        self.assertEqual(self.rows()[-1][1], "1")
        audio = self.output / "audio" / "2026-08-25" / "2026-08-25_11-31.m4a"
        self.assertEqual(audio.read_bytes(), b"offline voice fixture")
        text = (self.output / "2026-08-25-修改.md").read_text(encoding="utf-8")
        self.assertIn("🎤 语音记录", text)
        self.assertIn("audio/2026-08-25/2026-08-25_11-31.m4a", text)


if __name__ == "__main__":
    unittest.main()
