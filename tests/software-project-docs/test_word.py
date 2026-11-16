"""software-project-docs 生成 Word 的回归测试：build_docx.py 生成与拒绝，check_docx.py 通过与反例。

运行：python3 -B -m unittest discover -s tests/software-project-docs -v
用例需要 python-docx；运行的 Python 导入不到它时全部跳过（用装了 python-docx 1.2.0 的虚拟环境运行即可全部执行）。
fixtures/word 是一个小样例：md、两张图的图源、截图与 vsdx，后两者由本 skill 的 render.mjs、export-svg.mjs、html_to_vsdx.py 生成。
每个用例把它复制到临时目录。
"""
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

WORD_SCRIPTS = Path(__file__).resolve().parents[2] / 'myskills' / 'software-project-docs' / 'scripts' / 'word'
FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'word'
MD = '需求规格说明.md'
DOCX = '需求规格说明.docx'
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
HAS_DOCX = importlib.util.find_spec('docx') is not None


def run(script, *args):
    return subprocess.run([sys.executable, str(WORD_SCRIPTS / script), *map(str, args)],
                          capture_output=True, text=True, env=ENV)


def rewrite(source, target, change):
    """复制 docx，按 change(部件名, 字节, 全部部件) 改写部件；返回 None 表示删除该部件。"""
    with zipfile.ZipFile(source) as src:
        part_map = {n: src.read(n) for n in src.namelist()}
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as out:
        for name, data in part_map.items():
            data = change(name, data, part_map)
            if data is not None:
                out.writestr(name, data)


def preview_targets(part_map):
    """按正文顺序列出各嵌入对象的预览图部件名。"""
    document = part_map['word/document.xml'].decode('utf-8')
    rels = part_map['word/_rels/document.xml.rels'].decode('utf-8')
    rel_map = dict(re.findall(r'Id="(rId\d+)"[^>]*Target="([^"]+)"', rels))
    return ['word/' + rel_map[r] for r in re.findall(r'<v:imagedata r:id="(rId\d+)"', document)]


@unittest.skipUnless(HAS_DOCX, '当前 Python 没有 python-docx')
class WordTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='spd-word-')
        self.addCleanup(self.tmp.cleanup)
        self.doc_dir = Path(self.tmp.name) / 'docs'
        shutil.copytree(FIXTURE, self.doc_dir)
        self.md = self.doc_dir / MD
        self.docx = self.doc_dir / DOCX

    def edit(self, old, new):
        text = self.md.read_text(encoding='utf-8')
        self.assertEqual(text.count(old), 1, f'“{old}”的出现次数不对')
        self.md.write_text(text.replace(old, new), encoding='utf-8')

    def build(self):
        return run('build_docx.py', self.md)

    def assert_rejected(self, keyword):
        result = self.build()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(keyword, result.stderr)
        self.assertFalse(self.docx.exists())


class BuildTests(WordTestCase):
    def test_build_then_check_passes(self):
        result = self.build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('标题 7，表格 2，图 2', result.stdout)
        check = run('check_docx.py', self.md)
        self.assertEqual(check.returncode, 0, check.stdout)
        self.assertIn('通过（嵌入对象 2', check.stdout)


class CheckNegativeTests(WordTestCase):
    def setUp(self):
        super().setUp()
        result = self.build()
        self.assertEqual(result.returncode, 0, result.stderr)

    def assert_reported(self, change, keyword):
        target = self.doc_dir / 'changed.docx'
        rewrite(self.docx, target, change)
        result = run('check_docx.py', self.md, target)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(keyword, result.stdout)

    def test_dropped_embedding(self):
        drop = lambda name, data, part_map: None if name == 'word/embeddings/Microsoft_Visio_Drawing2.vsdx' else data
        self.assert_reported(drop, '嵌入部件 word/embeddings/Microsoft_Visio_Drawing2.vsdx 不存在')

    def test_wrong_caption(self):
        def change(name, data, part_map):
            if name != 'word/document.xml':
                return data
            return data.replace('<w:t>图 1-1 系统上下文图</w:t>'.encode(), '<w:t>图 1-1 系统上下文图甲</w:t>'.encode())
        self.assert_reported(change, '文字不同：md “图 1-1 系统上下文图”')

    def test_swapped_preview(self):
        def change(name, data, part_map):
            first, second = preview_targets(part_map)
            return part_map[second] if name == first else data
        self.assert_reported(change, '第 1 个图 srs-1-1-context：预览图')


class RejectTests(WordTestCase):
    def test_bold(self):
        self.edit('本系统用于录入和查询资料', '本系统用于**录入**和查询资料')
        self.assert_rejected('加粗')

    def test_paragraph_on_two_lines(self):
        self.edit('本系统用于录入和查询资料，系统上下文', '本系统用于录入和查询资料，\n系统上下文')
        self.assert_rejected('块与块之间必须空一行')

    def test_missing_blank_line_between_blocks(self):
        self.edit('### 1.1 系统概述\n\n', '### 1.1 系统概述\n')
        self.assert_rejected('块与块之间必须空一行')

    def test_caption_differs_from_alt_text(self):
        self.edit('\n图 2-1 资料录入活动图\n', '\n图 2-1 资料录入流程\n')
        self.assert_rejected('图注必须与替代文字相同')

    def test_toc_differs_from_headings(self):
        self.edit('  - 1.2 用户与角色', '  - 1.2 用户')
        self.assert_rejected('目录第 3 条“1.2 用户”与正文标题“1.2 用户与角色”不一致')

    def test_missing_vsdx(self):
        (self.doc_dir / 'vsdx' / 'srs-2-1-input-activity.vsdx').unlink()
        self.assert_rejected('缺少文件 vsdx/srs-2-1-input-activity.vsdx')

    def test_preview_aspect_differs_from_visio_page(self):
        images = self.doc_dir / 'images'
        shutil.copyfile(images / 'srs-1-1-context.png', images / 'srs-2-1-input-activity.png')
        self.assert_rejected('相差超过 1%')


if __name__ == '__main__':
    unittest.main()
