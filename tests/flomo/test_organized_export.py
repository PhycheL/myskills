"""Exercise organized exports with one-level, verbatim memo quotations."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "myskills/flomo-export-memos/scripts"
sys.path.insert(0, str(SCRIPTS))
from memo_references import find_references
from verify_export import verify_export


class OrganizedExportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="flomo-organized-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.output = self.root / "output"
        self.report = self.root / "export.json"
        self.ids = self.write_json("ids.json", ["selected"])
        self.raw = self.output / "2026-10-12.md"
        self.organized = self.output / "2026-10-12-整理版.md"
        self.relation = r"关联自： https://v.flomoapp.com/mine/?memo\_id=related"

    def write_json(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    def run_script(self, script, *args, success=True):
        result = subprocess.run([sys.executable, "-B", str(SCRIPTS / script), *map(str, args)],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return result

    def export(self, body, voice=False):
        source = self.write_json("day.json", [{
            "id": "selected", "created_at": "2026-10-12T21:23:00+08:00",
            "updated_at": "2026-10-12T21:23:00+08:00", "has_voice": voice,
            "content": "#流水账\n\n" + body, "content_truncated": False,
        }])
        self.run_script("build_day.py", source, self.output, "--report", self.report)

    def prepare(self, related):
        related_path = self.write_json("related.json", related)
        return self.run_script("prepare_organized.py", self.report, "2026-10-12", "--related", related_path)

    def record(self):
        return json.loads(self.report.read_text())["days"]["2026-10-12"]

    def test_text_reference_requires_organized_and_preserves_original_and_scope(self):
        self.export(self.relation + "\n\n我的行动清单。")
        original = self.raw.read_bytes()
        self.assertEqual(self.record()["organized_file"], self.organized.name)
        with self.assertRaises(ValueError):
            verify_export(self.report, self.ids)
        # Merely copying the raw file cannot silently skip reference handling.
        self.organized.write_bytes(original)
        with self.assertRaisesRegex(ValueError, "关联笔记尚未处理"):
            verify_export(self.report, self.ids)
        self.prepare([{
            "id": "related", "created_at": "2026-09-01T08:00:00+08:00", "has_voice": True,
            "content": "#旧标签\n\n嗯，这是关联原文。\n\n- 第一项\n  - 子项\n\n> 原有引用\n\n## 原有标题",
            "files": [{"type": "recorded", "url": "https://invalid.example/audio.m4a"}],
        }])
        text = self.organized.read_text()
        self.assertIn("> 嗯，这是关联原文。\n>\n> - 第一项\n>   - 子项\n>\n> > 原有引用", text)
        self.assertIn("> ## 原有标题", text)
        self.assertIn("关联笔记：[查看原文](https://v.flomoapp.com/mine/?memo_id=related)", text)
        self.assertEqual(self.raw.read_bytes(), original)
        self.assertFalse((self.output / "audio").exists())
        self.assertEqual(verify_export(self.report, self.ids), 1)
        receipt = json.loads(self.report.read_text())
        self.assertEqual([item["id"] for item in receipt["memos"]], ["selected"])
        self.assertEqual({path.name for path in self.output.glob("*.md")}, {self.raw.name, self.organized.name})

    def test_only_explicit_associations_outside_code_and_quotes_are_expanded(self):
        url = "https://v.flomoapp.com/mine/?memo_id=related"
        body = "\n".join([
            self.relation, f"关联自: [原文]({url})", f"关联自：<{url}>",
            f"普通链接：{url}", f"> 关联自：{url}", f"    关联自：{url}",
            "```markdown", self.relation, "", f"关联笔记：[查看原文]({url})", "", "> 示例", "```",
            "~~~", self.relation, "~~~", "关联自：https://example.com/mine/?memo_id=other",
        ])
        self.assertEqual([item["target_id"] for item in find_references(body)], ["related"] * 3)
        self.export(body)
        self.prepare([{"id": "related", "content": "引用内容"}])
        self.assertEqual(self.organized.read_text().count("> 引用内容"), 3)
        self.assertIn("```markdown\n" + self.relation, self.organized.read_text())
        self.assertEqual(verify_export(self.report, self.ids), 1)

    def test_nested_and_circular_links_stay_as_links_without_more_exports(self):
        self.export(self.relation)
        target_body = "关联自：https://v.flomoapp.com/mine/?memo_id=selected\n下一层原文保持链接。"
        self.prepare([{"id": "related", "content": target_body},
                      {"id": "selected", "content": self.relation}])
        self.assertIn("> 关联自：https://v.flomoapp.com/mine/?memo_id=selected", self.organized.read_text())
        self.assertEqual(len(self.record()["references"]), 1)
        self.assertEqual(verify_export(self.report, self.ids), 1)

    def test_missing_truncated_and_empty_targets_keep_links_and_report_warnings(self):
        cases = [[], [{"id": "related", "content": "不完整", "content_truncated": True}],
                 [{"id": "related", "content": "#标签\n\n", "has_image": True}]]
        for targets in cases:
            with self.subTest(targets=targets):
                self.export(self.relation + "\n\n文字正文")
                result = self.prepare(targets)
                self.assertIn("警告：related", result.stderr)
                self.assertEqual(self.raw.read_bytes(), self.organized.read_bytes())
                self.assertEqual(self.record()["reference_results"][0]["status"], "unavailable")
                self.assertEqual(verify_export(self.report, self.ids), 1)

    def test_voice_can_be_edited_but_quoted_text_and_metadata_cannot(self):
        self.export(self.relation + "\n\n嗯，我来补充。", voice=True)
        self.prepare([{"id": "related", "content": "嗯，原文保留。\n来源：原文中的文字"}])
        draft = self.organized.read_text()
        edited = draft.replace("嗯，我来补充。", "我来补充。")
        self.organized.write_text(edited, encoding="utf-8")
        self.assertEqual(verify_export(self.report, self.ids), 1)
        for broken in [edited.replace("嗯，原文保留。", "原文保留。"),
                       edited.replace("> 来源：[flomo 原文]", "> 来源：[错误来源]"),
                       edited.replace("> 嗯，原文保留。\n", "")]:
            with self.subTest(broken=broken):
                self.organized.write_text(broken, encoding="utf-8")
                with self.assertRaises(ValueError):
                    verify_export(self.report, self.ids)
                self.assertFalse(json.loads(self.report.read_text())["complete"])

    def test_text_body_cannot_be_rewritten_while_expanding_reference(self):
        self.export(self.relation + "\n\n纯文字正文。")
        self.prepare([{"id": "related", "content": "关联原文。"}])
        self.organized.write_text(self.organized.read_text().replace("纯文字正文。", "擅自润色。"))
        with self.assertRaisesRegex(ValueError, "纯文字整理版"):
            verify_export(self.report, self.ids)

    def test_rebuilding_draft_invalidates_receipt_and_uses_current_reference_text(self):
        self.export(self.relation)
        self.prepare([{"id": "related", "content": "首次取得的原文。"}])
        verify_export(self.report, self.ids)
        self.prepare([{"id": "related", "content": "再次取得的原文。"}])
        receipt = json.loads(self.report.read_text())
        self.assertFalse(receipt["complete"])
        self.assertNotIn("memos", receipt)
        self.assertNotIn("organized_sha256", self.record())
        self.assertNotIn("首次取得", self.organized.read_text())
        self.assertEqual(verify_export(self.report, self.ids), 1)


if __name__ == "__main__":
    unittest.main()
