"""Exercise each skill from a copy with no sibling skill installed."""
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILLS = Path(__file__).resolve().parents[2] / "myskills"


class IndependentSkillTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="flomo-independent-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def install(self, name):
        return shutil.copytree(SKILLS / name, self.root / "skill")

    def write_json(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    def run_script(self, skill, name, *args):
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        result = subprocess.run(
            [sys.executable, "-B", str(skill / "scripts" / name), *map(str, args)],
            cwd=self.root, env=env, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def test_find_accepts_generic_completion_without_export_files(self):
        skill = self.install("flomo-find-memos")
        self.run_script(skill, "scan_memos.py", "--help")
        baseline = "2026-10-12T09:00:00+08:00"
        cutoff = "2026-10-19T09:00:00+08:00"
        memo = {"id": "selected", "created_at": "2026-08-25T11:31:00+08:00",
                "updated_at": "2026-10-13T23:03:00+08:00"}
        state_dir = self.root / "state"
        progress = self.write_json("state/.flomo_edited_baseline.json", {"baseline_at": baseline})
        ids = self.write_json("ids.json", [memo["id"]])
        selection = self.write_json("selection.json", {
            "schema_version": 2, "scan_id": "independent", "scan_complete": True,
            "query": {"mode": "modified", "tag": None, "start_at": baseline, "end_at": cutoff},
            "scan_started_at": cutoff, "state_dir": str(state_dir), "ids_file": str(ids),
            "memos": [memo],
        })
        completion = self.write_json("completion.json", {
            "schema_version": 1, "complete": True, "memos": [memo],
        })

        self.run_script(skill, "finish_scan.py", selection, "--report", completion)

        self.assertEqual(json.loads(progress.read_text()), {"baseline_at": cutoff})
        with (state_dir / "flomo-edited-memos-runs.csv").open(encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle))
        self.assertEqual(rows[0], ["运行日期", "改动笔记数"])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][1], "1")
        self.assertEqual(list(state_dir.glob("*.md")), [])

    def test_export_uses_plain_ids_without_query_or_baseline(self):
        skill = self.install("flomo-export-memos")
        audio = self.root / "source.m4a"
        audio.write_bytes(b"offline audio fixture")
        ids = self.write_json("ids.json", ["provided", "provided"])
        source = self.write_json("memos.json", [{
            "id": "provided", "created_at": "2026-10-12T11:31:00+08:00",
            "updated_at": "2026-10-12T11:31:00+08:00", "content": "嗯，今天完成了记录。",
            "content_truncated": False, "has_voice": True,
            "files": [{"type": "recorded", "url": audio.as_uri()}],
        }])
        output = self.root / "output"
        report = self.root / "export.json"

        self.run_script(skill, "build_day.py", source, output, "--report", report)
        self.run_script(skill, "prepare_organized.py", report, "2026-10-12")
        raw = output / "2026-10-12.md"
        organized = output / "2026-10-12-整理版.md"
        organized.write_text(organized.read_text(encoding="utf-8").replace("嗯，", ""), encoding="utf-8")
        self.run_script(skill, "verify_export.py", report, "--ids", ids)

        self.assertIn("嗯，今天完成了记录。", raw.read_text(encoding="utf-8"))
        self.assertIn("今天完成了记录。", organized.read_text(encoding="utf-8"))
        self.assertEqual((output / "audio/2026-10-12/2026-10-12_11-31.m4a").read_bytes(), audio.read_bytes())
        receipt = json.loads(report.read_text())
        self.assertIs(receipt["complete"], True)
        self.assertEqual([memo["id"] for memo in receipt["memos"]], ["provided"])


if __name__ == "__main__":
    unittest.main()
