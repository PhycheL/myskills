"""software-project-docs 脚本的回归测试：check_docs、refresh_toc、renumber（不需要浏览器的部分）。

运行：python3 -B -m unittest discover -s tests/software-project-docs -v
fixtures/clean 是一个干净的小样例文档目录；每个用例把它复制到临时目录，截图用空的 PNG 代替。
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / 'myskills' / 'software-project-docs' / 'scripts'
CLEAN = Path(__file__).resolve().parent / 'fixtures' / 'clean'
SRS = '需求规格说明.md'
DESIGN = '设计方案.md'
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')


def run(script, *args):
    return subprocess.run([sys.executable, str(SCRIPTS / script), *map(str, args)],
                          capture_output=True, text=True, env=ENV)


class DocDirTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='spd-test-')
        self.addCleanup(self.tmp.cleanup)
        self.doc_dir = Path(self.tmp.name) / 'docs'
        shutil.copytree(CLEAN, self.doc_dir)
        (self.doc_dir / 'images').mkdir()
        for html in (self.doc_dir / 'diagrams').glob('*.html'):
            (self.doc_dir / 'images' / (html.stem + '.png')).write_bytes(b'')

    def read(self, name):
        return (self.doc_dir / name).read_text(encoding='utf-8')

    def edit(self, name, old, new, count=1):
        text = self.read(name)
        self.assertEqual(text.count(old), count, f'{name} 中“{old}”的出现次数不对')
        (self.doc_dir / name).write_text(text.replace(old, new), encoding='utf-8')

    def check(self, *extra):
        return run('check_docs.py', self.doc_dir, 'all', *extra)


class CleanFixtureTests(DocDirTestCase):
    def test_clean_fixture_passes(self):
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('[需求规格说明.md] 通过', result.stdout)
        self.assertIn('[设计方案.md] 通过', result.stdout)

    def test_single_type(self):
        result = run('check_docs.py', self.doc_dir, 'srs')
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn('设计方案.md', result.stdout)

    def test_refresh_toc_keeps_content(self):
        before = {name: self.read(name) for name in (SRS, DESIGN)}
        result = run('refresh_toc.py', self.doc_dir, 'all')
        self.assertEqual(result.returncode, 0, result.stdout)
        for name, text in before.items():
            self.assertEqual(self.read(name), text, name)

    def test_renumber_keeps_content(self):
        for name in (SRS, DESIGN):
            before = self.read(name)
            result = run('renumber.py', self.doc_dir / name)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn('改号 0 个', result.stdout)
            self.assertEqual(self.read(name), before, name)

    def test_vocab_lists_words(self):
        result = self.check('--vocab')
        self.assertEqual(result.returncode, 0)
        self.assertIn('英文词', result.stdout)


class RefreshTests(DocDirTestCase):
    def test_rebuilds_toc_and_mapping(self):
        self.edit(DESIGN, '- 2 功能设计\n  - 2.1 资料管理设计\n', '')
        text = self.read(DESIGN)
        self.edit(DESIGN, text[text.index('## 4 需求与设计映射表'):], '')
        result = run('refresh_toc.py', self.doc_dir, 'design')
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.read(DESIGN), (CLEAN / DESIGN).read_text(encoding='utf-8'))

    def test_inserts_missing_toc(self):
        text = self.read(SRS)
        start = text.index('## 目录')
        self.edit(SRS, text[start:text.index('## 1 概述')], '')
        run('refresh_toc.py', self.doc_dir, 'srs')
        self.assertEqual(self.read(SRS), (CLEAN / SRS).read_text(encoding='utf-8'))

    def test_missing_design_section_marked(self):
        self.edit(DESIGN, '#### 2.1.2 资料查询设计', '#### 2.1.3 资料查询设计')
        result = run('refresh_toc.py', self.doc_dir, 'design')
        self.assertEqual(result.returncode, 1)
        self.assertIn('| 2.1.2 资料查询 | XQ-2.1.2-01 至 XQ-2.1.2-02（2 条） | （缺少） |', self.read(DESIGN))


class RenumberTests(DocDirTestCase):
    def test_renumbers_figures_and_references(self):
        self.edit(SRS, '图 2-3', '图 2-9', count=4)
        result = run('renumber.py', self.doc_dir / SRS)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.read(SRS), (CLEAN / SRS).read_text(encoding='utf-8'))

    def test_dangling_reference_fails_without_writing(self):
        self.edit(SRS, '查询用例如图 2-3 所示', '查询用例如图 2-7 所示')
        before = self.read(SRS)
        result = run('renumber.py', self.doc_dir / SRS)
        self.assertEqual(result.returncode, 1)
        self.assertIn('图 2-7', result.stdout)
        self.assertEqual(self.read(SRS), before)


class ViolationTests(DocDirTestCase):
    """每种违规单独注入，check_docs 应报出对应问题。"""

    def assert_reports(self, expected):
        result = self.check()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(expected, result.stdout)

    def test_missing_figure(self):
        text = self.read(SRS)
        block = text[text.index('查询用例如图 2-3 所示。'):text.index('资料查询的需求见表 2-2。')]
        self.edit(SRS, block, '')
        (self.doc_dir / 'images' / 'srs-2-1-2-query-usecase.png').unlink()
        (self.doc_dir / 'diagrams' / 'srs-2-1-2-query-usecase.html').unlink()
        self.assert_reports('2.1.2 资料查询 没有配图')

    def test_caption_differs_from_alt_text(self):
        self.edit(SRS, '![图 1-2 角色图]', '![图 1-2 用户角色图]')
        self.assert_reports('图片说明文字与图注不一致：图 1-2 用户角色图 / 图 1-2 角色图')

    def test_no_lead_in_sentence(self):
        self.edit(SRS, '本系统的系统上下文如图 1-1 所示。', '本系统的系统上下文见下图。')
        self.assert_reports('图 1-1 之前没有引出句')

    def test_no_description_after_caption(self):
        self.edit(DESIGN, '图 3-1 中，资料服务把资料保存在资料库中。\n', '')
        self.assert_reports('图 3-1 的图注之后没有说明段落')

    def test_table_without_caption(self):
        self.edit(SRS, '表 1-2 角色\n\n', '')
        self.edit(SRS, '各角色的职责见表 1-2。', '各角色的职责如下。')
        self.assert_reports('的表格上方没有表注')

    def test_bold(self):
        self.edit(SRS, '本系统用于录入和查询资料。', '本系统用于**录入和查询**资料。')
        self.assert_reports('使用了加粗')

    def test_code_block(self):
        self.edit(SRS, '- 标题为空时不保存，并提示补充标题。', '```\n保存\n```')
        self.assert_reports('有代码块')

    def test_inline_code(self):
        self.edit(SRS, '本系统用于录入和查询资料。', '本系统用于录入和查询`资料`。')
        self.assert_reports('有行内代码')

    def test_tech_product_name(self):
        self.edit(DESIGN, '资料管理由资料服务负责', '资料管理由基于 Express 的资料服务负责')
        self.assert_reports('含技术产品名“Express”')

    def test_snake_case_identifier(self):
        self.edit(DESIGN, '资料服务按条件查询', '资料服务按 query_records 查询')
        self.assert_reports('含蛇形标识符“query_records”')

    def test_requirement_sequence_gap(self):
        self.edit(SRS, '| XQ-2.1.2-02 |', '| XQ-2.1.2-03 |')
        self.edit(DESIGN, 'XQ-2.1.2-01 至 XQ-2.1.2-02', 'XQ-2.1.2-01 至 XQ-2.1.2-03')
        self.assert_reports('XQ-2.1.2-03 序号不连续')

    def test_requirement_in_wrong_section(self):
        self.edit(SRS, '| XQ-3-01 |', '| XQ-2.1.2-03 |')
        self.assert_reports('XQ-2.1.2-03 写在 3 节，与编号不符')

    def test_requirement_format(self):
        self.edit(SRS, '| XQ-3-01 |', '| XQ-3-1 |')
        self.assert_reports('需求编号格式不对：XQ-3-1')

    def test_requirement_id_in_design_body(self):
        self.edit(DESIGN, '资料录入由资料服务负责', '资料录入（XQ-2.1.1-01）由资料服务负责')
        self.assert_reports('正文含需求编号')

    def test_coverage_note_in_design_body(self):
        self.edit(DESIGN, '资料查询由资料服务负责', '本节覆盖需求 2.1.2。资料查询由资料服务负责')
        self.assert_reports('正文含“覆盖需求”')

    def test_mapping_missing_requirement(self):
        self.edit(DESIGN, '| 3 性能需求 | XQ-3-01 | 3 性能设计 |\n', '')
        self.assert_reports('映射表缺少 XQ-3-01')

    def test_mapping_missing_chapter(self):
        text = self.read(DESIGN)
        self.edit(DESIGN, text[text.index('\n## 4 需求与设计映射表'):], '\n')
        self.edit(DESIGN, '- 4 需求与设计映射表\n', '')
        self.assert_reports('最后一章应为“需求与设计映射表”')

    def test_srs_mapping_chapter(self):
        (self.doc_dir / SRS).write_text(self.read(SRS) + '\n## 4 需求追踪表\n\n本章列出追踪关系。\n', encoding='utf-8')
        self.assert_reports('需求规格说明不设映射表或追踪表章')

    def test_design_missing_same_number_section(self):
        self.edit(DESIGN, '#### 2.1.2 资料查询设计', '#### 2.1.3 资料查询设计')
        self.assert_reports('缺少与需求规格说明 2.1.2 同号的小节')

    def test_diagram_source_with_style(self):
        path = self.doc_dir / 'diagrams' / 'srs-1-3-roles.html'
        path.write_text(path.read_text(encoding='utf-8').replace('</head>', '<style>svg{color:red}</style>\n</head>'),
                        encoding='utf-8')
        self.assert_reports('图源 srs-1-3-roles.html 含 <style')

    def test_diagram_source_with_classdef(self):
        path = self.doc_dir / 'diagrams' / 'design-3-scale.html'
        path.write_text(path.read_text(encoding='utf-8').replace('  svc --> db', '  svc --> db\n  classDef red fill:#f00'),
                        encoding='utf-8')
        self.assert_reports('图源 design-3-scale.html 含 classDef')

    def test_diagram_title_with_number(self):
        path = self.doc_dir / 'diagrams' / 'srs-1-3-roles.html'
        path.write_text(path.read_text(encoding='utf-8').replace('<title>角色图', '<title>图 1-2 角色图'),
                        encoding='utf-8')
        self.assert_reports('图源标题不应带图号：srs-1-3-roles.html')

    def test_missing_png_and_unreferenced_source(self):
        (self.doc_dir / 'images' / 'srs-1-3-roles.png').unlink()
        (self.doc_dir / 'diagrams' / 'srs-9-extra.html').write_text('<title>多余的图</title>', encoding='utf-8')
        result = self.check()
        self.assertIn('图片不存在：srs-1-3-roles.png', result.stdout)
        self.assertIn('图源未被引用：srs-9-extra.html', result.stdout)

    def test_figure_number_gap(self):
        self.edit(DESIGN, '图 2-3', '图 2-4', count=4)
        self.assert_reports('图 2-4 编号不连续（应为 2-3）')

    def test_reference_to_other_document_number(self):
        self.edit(DESIGN, '依据需求规格说明编写。', '依据需求规格说明编写，角色见需求规格说明表 1-2。')
        self.assert_reports('引用了需求规格说明的编号“表 1-2”')

    def test_forbidden_words(self):
        self.edit(SRS, '本系统用于录入和查询资料。', '本系统用于录入和查询资料，详见设计方案，查询功能未实现。')
        result = self.check()
        self.assertIn('含禁用词“设计方案”', result.stdout)
        self.assertIn('含禁用词“未实现”', result.stdout)

    def test_forbid_and_allow_options(self):
        self.edit(SRS, '本系统用于录入和查询资料。', '本系统用于录入和查询资料，资料以 DocItem 为单位。')
        self.assertIn('类名式标识符“DocItem”', self.check().stdout)
        self.assertEqual(self.check('--allow', 'DocItem').returncode, 0)
        result = self.check('--allow', 'DocItem', '--forbid', '查阅人员')
        self.assertIn('含禁用词“查阅人员”', result.stdout)


class FileModeTests(unittest.TestCase):
    def test_file_mode_checks_text_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            draft = Path(tmp) / 'draft.md'
            draft.write_text('## 2.1 草稿\n\n本系统应使用 **SQLite** 保存 user_name。\n', encoding='utf-8')
            result = run('check_docs.py', '--file', draft, 'design')
            self.assertEqual(result.returncode, 1)
            for expected in ('使用了加粗', '技术产品名“SQLite”', '蛇形标识符“user_name”'):
                self.assertIn(expected, result.stdout)
            self.assertNotIn('没有配图', result.stdout)
            draft.write_text('本系统应保存资料。\n', encoding='utf-8')
            self.assertEqual(run('check_docs.py', '--file', draft, 'srs').returncode, 0)

    def test_usage_errors(self):
        self.assertEqual(run('check_docs.py').returncode, 2)
        self.assertEqual(run('check_docs.py', CLEAN, 'manual').returncode, 2)
        self.assertEqual(run('refresh_toc.py').returncode, 2)
        self.assertEqual(run('renumber.py').returncode, 2)


if __name__ == '__main__':
    unittest.main()
