"""Offline handoff checks using the actual flomo-tag-diaries exporter CLI."""
import csv
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / "scripts"))
import scan_state as state

EXPORT = SKILL.parent / "flomo-tag-diaries" / "scripts" / "build_day.py"
FINISH = SKILL / "scripts" / "finish_scan.py"
BASELINE = "2026-10-12T09:00:00+08:00"
CUTOFF = "2026-10-19T09:00:00+08:00"


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="flomo-workflow-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.output = self.root / "output"
        self.output.mkdir()
        self.baseline = self.output / state.BASELINE_FILE
        self.log = self.output / state.RUNS_FILE
        self.manifest = self.root / "selection.json"
        self.report = self.root / "export.json"
        state.write_json(self.baseline, {"baseline_at": BASELINE})

    def memo(self, key="edited", **fields):
        return dict({"id": key, "created_at": "2026-08-25T11:31:00+08:00",
                     "updated_at": "2026-10-13T23:03:00+08:00",
                     "content": "#流水账\n\n原文 memo\\_id 🙂\n第二行", "content_truncated": False}, **fields)

    def selection(self, memos, baseline=BASELINE, cutoff=CUTOFF, **fields):
        value = {"schema_version": 1, "scan_id": "fixture", "scan_complete": True,
                 "baseline_at": baseline, "scan_started_at": cutoff, "output_dir": str(self.output),
                 "memos": state.select_memos(memos, state.parse_dt(baseline) if baseline else None,
                                              state.parse_dt(cutoff))}
        state.write_json(self.manifest, dict(value, **fields))

    def export(self, memos, success=True, options=None):
        source = self.root / "day.json"
        state.write_json(source, memos)
        if options is None:
            options = ["--suffix=-修改", "--time-field", "updated_at", "--title-note",
                       "2026-10-12 09:00:00 至 2026-10-19 09:00:00 扫描发现修改", "--report", str(self.report)]
        result = subprocess.run([sys.executable, "-B", str(EXPORT), str(source), str(self.output), *options],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return result

    def rows(self):
        with self.log.open(encoding="utf-8", newline="") as handle:
            return list(csv.reader(handle))

    def assert_unfinished(self, report=True):
        before = self.baseline.read_bytes() if self.baseline.exists() else None
        with self.assertRaises((ValueError, OSError)):
            state.finish_scan(self.manifest, self.report if report else None)
        self.assertEqual(self.baseline.read_bytes() if self.baseline.exists() else None, before)
        self.assertFalse(self.log.exists())

    def test_handoff_exports_only_selected_ids_and_preserves_full_diary(self):
        full = self.output / "2026-08-25.md"
        full.write_text("已有完整日记", encoding="utf-8")
        memos = [self.memo("later", updated_at="2026-10-14T01:00:00+08:00"), self.memo("earlier")]
        self.selection(memos)
        self.export(memos)
        text = (self.output / "2026-08-25-修改.md").read_text()
        self.assertLess(text.index("### 2026-10-13 23:03"), text.index("### 2026-10-14 01:00"))
        self.assertIn("原文 memo\\_id 🙂\n第二行", text)
        self.assertNotIn("#流水账", text)
        self.assertEqual(full.read_text(), "已有完整日记")
        self.assertFalse(self.log.exists())
        result = subprocess.run([sys.executable, "-B", str(FINISH), str(self.manifest), "--report", str(self.report)],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state.load_baseline(self.output), state.parse_dt(CUTOFF))
        self.assertEqual(self.rows()[-1][1], "2")
        self.assertEqual(set(json.loads(self.baseline.read_text())), {"baseline_at"})
        self.assertNotIn("content", self.report.read_text())

    def test_default_tag_export_still_uses_creation_time_and_filename(self):
        memo = self.memo()
        memo.pop("updated_at")
        self.export([memo], options=[])
        text = (self.output / "2026-08-25.md").read_text()
        self.assertIn("### 11:31", text)
        self.assertNotIn("修改于", text)
        self.assertFalse(self.report.exists())

    def test_first_scan_records_blank_and_zero_runs_record_actual_dates(self):
        self.baseline.unlink()
        self.selection([self.memo()], baseline=None, cutoff=BASELINE)
        self.assertIsNone(state.finish_scan(self.manifest))
        self.selection([])
        self.assertEqual(state.finish_scan(self.manifest), 0)
        self.selection([], baseline=CUTOFF, cutoff="2026-11-19T09:00:00+08:00")
        self.assertEqual(state.finish_scan(self.manifest), 0)
        today = datetime.now(state.BEIJING_TZ).date().isoformat()
        self.assertEqual(self.rows(), [state.RUNS_HEADER, [today, ""], [today, "0"], [today, "0"]])
        self.assertEqual(list(self.output.glob("*.md")), [])

    def test_each_day_must_be_exported_before_finishing(self):
        first, second = self.memo(), self.memo("second", created_at="2026-08-26T10:00:00+08:00")
        self.selection([first, second])
        self.export([first])
        self.assert_unfinished()
        self.export([second])
        self.assertEqual(state.finish_scan(self.manifest, self.report), 2)

    def test_wrong_missing_or_duplicate_exported_ids_do_not_advance(self):
        self.selection([self.memo()])
        self.assert_unfinished(report=False)
        self.export([self.memo("unrequested")])
        self.assert_unfinished()
        self.export([self.memo()])
        report = json.loads(self.report.read_text())
        report["days"]["2026-08-25"]["memos"] *= 2
        state.write_json(self.report, report)
        self.assert_unfinished()

    def test_export_cannot_use_older_or_post_cutoff_revision(self):
        self.selection([self.memo()])
        for updated in ("2026-10-13T23:02:59+08:00", "2026-10-19T09:00:01+08:00"):
            with self.subTest(updated=updated):
                self.export([self.memo(updated_at=updated)])
                self.assert_unfinished()

    def test_current_revision_inside_window_can_finish(self):
        self.selection([self.memo()])
        self.export([self.memo(updated_at="2026-10-15T12:00:00+08:00")])
        self.assertEqual(state.finish_scan(self.manifest, self.report), 1)

    def test_missing_stale_or_damaged_formal_diary_blocks_finish(self):
        voice = self.memo(has_voice=True, content="嗯，今天我完成了修改。")
        self.selection([voice])
        self.export([voice])
        self.assert_unfinished()
        raw = self.output / "2026-08-25-修改.md"
        formal = self.output / "2026-08-25-修改-书面语.md"
        formal.write_text(raw.read_text().replace("嗯，", ""), encoding="utf-8")
        old_time = raw.stat().st_mtime_ns - 1_000_000_000
        os.utime(formal, ns=(old_time, old_time))
        self.assert_unfinished()
        formal.write_text(raw.read_text().replace("### 2026-10-13 23:03", "### 错误时间"), encoding="utf-8")
        self.assert_unfinished()
        formal.write_text(raw.read_text().replace("嗯，", ""), encoding="utf-8")
        self.assertEqual(state.finish_scan(self.manifest, self.report), 1)

    def test_empty_voice_keeps_audio_and_requires_formal(self):
        audio = self.root / "source.m4a"
        audio.write_bytes(b"offline audio fixture")
        voice = self.memo(content="", has_voice=True, files=[{"type": "recorded", "url": audio.as_uri()}])
        self.selection([voice])
        self.export([voice])
        self.assert_unfinished()
        saved = self.output / "audio/2026-08-25/2026-08-25_11-31.m4a"
        self.assertEqual(saved.read_bytes(), b"offline audio fixture")
        raw = self.output / "2026-08-25-修改.md"
        self.assertIn("🎤 语音记录", raw.read_text())
        self.assertIn("audio/2026-08-25/2026-08-25_11-31.m4a", raw.read_text())
        (self.output / "2026-08-25-修改-书面语.md").write_text(raw.read_text(), encoding="utf-8")
        self.assertEqual(state.finish_scan(self.manifest, self.report), 1)

    def test_truncated_or_duplicate_fulltext_is_rejected_before_export(self):
        cases = [[self.memo(content_truncated=True)], [self.memo(content=None)],
                 [self.memo(), self.memo()], [self.memo(), self.memo("other", created_at="2026-08-26T10:00:00+08:00")]]
        for memos in cases:
            with self.subTest(memos=memos):
                self.export(memos, success=False)
                self.assertEqual(list(self.output.glob("*.md")), [])
                self.assertFalse(self.report.exists())

    def test_tampered_raw_or_incomplete_scan_keeps_baseline(self):
        self.selection([self.memo()])
        self.export([self.memo()])
        (self.output / "2026-08-25-修改.md").write_text("被改写的原文", encoding="utf-8")
        self.assert_unfinished()
        self.selection([], scan_complete=False)
        self.assert_unfinished(report=False)

    def test_repeated_finish_does_not_duplicate_log(self):
        self.selection([])
        state.finish_scan(self.manifest)
        before = self.log.read_bytes()
        with self.assertRaisesRegex(ValueError, "基线已改变"):
            state.finish_scan(self.manifest)
        self.assertEqual(self.log.read_bytes(), before)

    def test_invalid_baseline_or_backwards_cutoff_cannot_reset_state(self):
        self.selection([], cutoff="2026-10-11T09:00:00+08:00")
        self.assert_unfinished(report=False)
        self.selection([])
        self.baseline.write_text("invalid JSON", encoding="utf-8")
        self.assert_unfinished(report=False)

    def test_baseline_write_failure_rolls_back_log(self):
        self.selection([])
        original = state.atomic_write

        def fail_baseline(path, text):
            if Path(path).name == state.BASELINE_FILE:
                raise OSError("模拟基线写入失败")
            original(path, text)

        with mock.patch.object(state, "atomic_write", side_effect=fail_baseline):
            self.assert_unfinished(report=False)


if __name__ == "__main__":
    unittest.main()
