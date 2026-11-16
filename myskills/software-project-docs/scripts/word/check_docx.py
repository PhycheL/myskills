"""检查 build_docx.py 生成的 Word 文档：包结构、嵌入的 Visio 对象、与 md 的逐块对照、样式与页面设置。

用法：python3 check_docx.py <md 文件> [<docx 文件>]
不给 docx 时检查 md 同目录的同名 docx。只用标准库，与生成脚本相互独立：md 按行自行解析，docx 直接读 XML。
图片与 Visio 文件取 md 同目录的 images/、vsdx/。任一项不通过时列出问题并以非零码退出。
"""
import posixpath
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
V = '{urn:schemas-microsoft-com:vml}'
O = '{urn:schemas-microsoft-com:office:office}'
CT = '{http://schemas.openxmlformats.org/package/2006/content-types}'
REL = '{http://schemas.openxmlformats.org/package/2006/relationships}'
VISIO = '{http://schemas.microsoft.com/office/visio/2012/main}'
DC = '{http://purl.org/dc/elements/1.1/}'
CP = '{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}'
RT_PACKAGE = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/package'
RT_IMAGE = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/image'

PAGE_W, PAGE_H, MARGIN_TB, MARGIN_LR = 11906, 16838, 1440, 1587
CONTENT_W = PAGE_W - 2 * MARGIN_LR
MAX_FIGURE_H_PT = 22 / 2.54 * 72
FRONT_HEADING_LIST = ['修订记录', '目录']

# 样式期望：东亚字体、西文字体、字号（半磅）、加粗、颜色、对齐、首行缩进字符数、行距、大纲级别
STYLE_EXPECT = {
    'Normal': dict(east_asia='宋体', ascii='Times New Roman', size='24', bold=False, jc='both', first_line_chars=None, line='360'),
    'BodyText': dict(east_asia='宋体', size='24', jc='both', first_line_chars='200', line='360'),
    'Title': dict(east_asia='黑体', size='36', bold=True, color='000000', jc='center'),
    'Heading1': dict(east_asia='黑体', ascii='Times New Roman', size='32', bold=False, color='000000', outline='0', keep_next=True, first_line_chars=None),
    'Heading2': dict(east_asia='黑体', size='28', bold=False, color='000000', outline='1', keep_next=True, first_line_chars=None),
    'Heading3': dict(east_asia='黑体', size='24', bold=False, color='000000', outline='2', keep_next=True, first_line_chars=None),
    'Heading4': dict(east_asia='宋体', size='24', bold=True, color='000000', outline='3', keep_next=True, first_line_chars=None),
    'TOCHeading': dict(east_asia='黑体', color='000000', jc='center', outline='9'),
    'Caption': dict(east_asia='黑体', size='21', bold=False, color='000000', jc='center', first_line_chars=None),
    'TableText': dict(east_asia='宋体', size='21', jc='left', line='300', first_line_chars=None),
    'TableHeading': dict(east_asia='黑体', size='21', bold=True, jc='center', first_line_chars=None),
    'Figure': dict(jc='center', keep_next=True, line='240', first_line_chars=None),
    'TOC1': dict(first_line_chars=None),
    'TOC2': dict(first_line_chars=None),
    'BulletList': dict(first_line_chars=None, hanging='360'),
    'OrderedList': dict(first_line_chars=None, hanging='360'),
}


def parse_md(md_path):
    """把 md 按行解析成期望的块序列：(段落样式或 table/figure, 内容)。"""
    line_list = md_path.read_text(encoding='utf-8').split('\n')
    expect_list = []
    in_toc = False
    i = 0
    while i < len(line_list):
        line = line_list[i]
        if line == '':
            i += 1
            continue
        heading = re.match(r'^(#{1,5}) (.+)$', line)
        if heading:
            level = len(heading.group(1))
            text = heading.group(2)
            in_toc = text == '目录'
            if level == 1:
                expect_list.append(('Title', text))
            elif level == 2 and text in FRONT_HEADING_LIST:
                expect_list.append(('TOCHeading', text))
            else:
                expect_list.append((f'Heading{level - 1}', text))
            i += 1
            continue
        if in_toc:
            item = re.match(r'^( *)- (.+)$', line)
            expect_list.append(('TOC1' if item.group(1) == '' else 'TOC2', item.group(2)))
            i += 1
            continue
        if line.startswith('|'):
            row_list = []
            while i < len(line_list) and line_list[i].startswith('|'):
                cell_list = [c.strip() for c in line_list[i].strip('|').split('|')]
                if not all(re.match(r'^:?-{3,}:?$', c) for c in cell_list):
                    row_list.append(cell_list)
                i += 1
            expect_list.append(('table', row_list))
            continue
        figure = re.match(r'^!\[(.+)\]\(images/(.+)\.png\)$', line)
        if figure:
            expect_list.append(('figure', figure.group(2)))
            expect_list.append(('Caption', line_list[i + 1]))
            i += 2
            continue
        ordered = re.match(r'^(\d+)\. (.+)$', line)
        if line.startswith('- '):
            expect_list.append(('BulletList', line[2:]))
        elif ordered:
            expect_list.append(('OrderedList', f'{ordered.group(1)}.\t{ordered.group(2)}'))
        elif i + 2 < len(line_list) and line_list[i + 1] == '' and line_list[i + 2].startswith('|'):
            expect_list.append(('Caption', line))
        else:
            expect_list.append(('BodyText', line))
        i += 1
    return expect_list


def read_body(document):
    """把 docx 正文读成块序列，元素保留供后续检查。"""
    body = document.find(W + 'body')
    block_list = []
    for child in body:
        if child.tag == W + 'p':
            style = child.find(f'{W}pPr/{W}pStyle')
            style_id = style.get(W + 'val') if style is not None else 'Normal'
            if child.find(f'.//{W}object') is not None:
                block_list.append(('figure', child))
                continue
            text = ''
            for run in child.iter(W + 'r'):
                for node in run:
                    if node.tag == W + 't':
                        text += node.text or ''
                    elif node.tag == W + 'tab':
                        text += '\t'
            block_list.append((style_id, text, child))
        elif child.tag == W + 'tbl':
            row_list = [[''.join(t.text or '' for t in tc.iter(W + 't')) for tc in tr.findall(W + 'tc')] for tr in child.findall(W + 'tr')]
            block_list.append(('table', row_list, child))
    return block_list


class Checker:
    """逐项检查，问题收集在 error_list。"""

    def __init__(self, md_path, docx_path):
        self.md_path = md_path
        self.docx_path = docx_path
        self.error_list = []
        self.package = zipfile.ZipFile(docx_path)
        self.name_list = [n for n in self.package.namelist() if not n.endswith('/')]
        self.tree = {}

    def error(self, message):
        """记一条问题。"""
        self.error_list.append(message)

    def check_package(self):
        """部件都有内容类型，关系目标存在，XML 良构。"""
        types = ET.fromstring(self.package.read('[Content_Types].xml'))
        default_set = {d.get('Extension').lower() for d in types.iter(CT + 'Default')}
        override_set = {o.get('PartName').lstrip('/') for o in types.iter(CT + 'Override')}
        for name in self.name_list:
            if name != '[Content_Types].xml' and name not in override_set and name.rsplit('.', 1)[-1].lower() not in default_set:
                self.error(f'部件没有内容类型：{name}')
        for name in override_set:
            if name not in self.name_list:
                self.error(f'内容类型登记的部件不存在：{name}')
        for name in self.name_list:
            if name.endswith(('.xml', '.rels')):
                try:
                    self.tree[name] = ET.fromstring(self.package.read(name))
                except ET.ParseError as error:
                    self.error(f'XML 不良构：{name}：{error}')
        for name, tree in self.tree.items():
            if not name.endswith('.rels'):
                continue
            base = posixpath.dirname(posixpath.dirname(name))
            for rel in tree.iter(REL + 'Relationship'):
                if rel.get('TargetMode') == 'External':
                    continue
                target = posixpath.normpath(posixpath.join(base, rel.get('Target'))).lstrip('/')
                if target not in self.name_list:
                    self.error(f'关系目标不存在：{name} -> {rel.get("Target")}')

    def document_rels(self):
        """正文部件的关系：rId -> (类型, 部件名)。"""
        rels = self.tree.get('word/_rels/document.xml.rels')
        return {r.get('Id'): (r.get('Type'), posixpath.normpath(posixpath.join('word', r.get('Target')))) for r in rels.iter(REL + 'Relationship')}

    def read_part(self, name):
        """读部件字节；不存在时返回 None。"""
        return self.package.read(name) if name in self.name_list else None

    def check_figure(self, index, name, paragraph, rels, seen):
        """检查一个嵌入对象：程序标识、编号唯一、预览图与数据同字节、原始尺寸与显示比例。"""
        label = f'第 {index} 个图 {name}'
        md_dir = self.md_path.parent
        png_path = md_dir / 'images' / f'{name}.png'
        vsdx_path = md_dir / 'vsdx' / f'{name}.vsdx'
        style = paragraph.find(f'{W}pPr/{W}pStyle')
        if style is None or style.get(W + 'val') != 'Figure':
            self.error(f'{label}：段落样式不是“图”')
        obj = paragraph.find(f'.//{W}object')
        shape = obj.find(V + 'shape')
        ole = obj.find(O + 'OLEObject')
        imagedata = shape.find(V + 'imagedata') if shape is not None else None
        if shape is None or ole is None or imagedata is None:
            self.error(f'{label}：对象缺少 v:shape、v:imagedata 或 o:OLEObject')
            return
        if ole.get('ProgID') != 'Visio.Drawing.15' or ole.get('Type') != 'Embed' or ole.get('DrawAspect') != 'Content':
            self.error(f'{label}：OLEObject 属性不对：{ole.attrib}')
        if ole.get('ShapeID') != shape.get('id'):
            self.error(f'{label}：ShapeID 与 v:shape 的 id 不同')
        for key, value in (('shape', shape.get('id')), ('object', ole.get('ObjectID'))):
            if value in seen[key]:
                self.error(f'{label}：{key} 编号重复 {value}')
            seen[key].add(value)
        if not re.fullmatch(r'_\d{10}', ole.get('ObjectID') or ''):
            self.error(f'{label}：ObjectID 不是 _ 加 10 位数字')
        image_rel = rels.get(imagedata.get(R + 'id'))
        object_rel = rels.get(ole.get(R + 'id'))
        if image_rel is None or image_rel[0] != RT_IMAGE:
            self.error(f'{label}：预览图关系缺失或类型不对')
        elif self.read_part(image_rel[1]) != png_path.read_bytes():
            self.error(f'{label}：预览图 {image_rel[1]} 与 images/{name}.png 不同')
        if object_rel is None or object_rel[0] != RT_PACKAGE:
            self.error(f'{label}：嵌入包关系缺失或类型不对')
            return
        if not re.fullmatch(r'word/embeddings/Microsoft_Visio_Drawing\d+\.vsdx', object_rel[1]):
            self.error(f'{label}：嵌入部件名不对：{object_rel[1]}')
        data = self.read_part(object_rel[1])
        if data is None:
            self.error(f'{label}：嵌入部件 {object_rel[1]} 不存在')
            return
        if data != vsdx_path.read_bytes():
            self.error(f'{label}：嵌入数据与 vsdx/{name}.vsdx 不同')
        with zipfile.ZipFile(vsdx_path) as visio:
            cell = {c.get('N'): float(c.get('V')) for c in ET.fromstring(visio.read('visio/pages/pages.xml')).iter(VISIO + 'Cell')
                    if c.get('N') in ('PageWidth', 'PageHeight')}
        page_w, page_h = cell['PageWidth'], cell['PageHeight']
        if abs(int(obj.get(W + 'dxaOrig')) - page_w * 1440) > 1 or abs(int(obj.get(W + 'dyaOrig')) - page_h * 1440) > 1:
            self.error(f'{label}：原始尺寸 {obj.get(W + "dxaOrig")}x{obj.get(W + "dyaOrig")} 与页面 {page_w}x{page_h} 英寸不符')
        size = dict(re.findall(r'(width|height):([\d.]+)pt', shape.get('style', '')))
        width, height = float(size.get('width', 0)), float(size.get('height', 0))
        if not width or abs((width / height) / (page_w / page_h) - 1) > 0.005:
            self.error(f'{label}：显示宽高比 {width}x{height} 与页面不一致')
        if width > CONTENT_W / 20 + 0.1 or height > MAX_FIGURE_H_PT + 0.1:
            self.error(f'{label}：显示尺寸 {width}x{height} 磅超出版心或最大图高')
        if width < page_w * 72 - 0.1 and width < CONTENT_W / 20 - 0.1 and height < MAX_FIGURE_H_PT - 0.1:
            self.error(f'{label}：显示尺寸未按版心等比缩放到上限')
        if width > page_w * 72 + 0.1:
            self.error(f'{label}：显示尺寸大于页面原大')

    def check_body(self):
        """与 md 逐块对照，并检查每块的格式与嵌入对象。"""
        expect_list = parse_md(self.md_path)
        block_list = read_body(self.tree['word/document.xml'])
        rels = self.document_rels()
        seen = {'shape': set(), 'object': set()}
        figure_index = 0
        if len(expect_list) != len(block_list):
            self.error(f'块数不同：md {len(expect_list)}，docx {len(block_list)}')
        mismatch = 0
        for position, (expect, block) in enumerate(zip(expect_list, block_list)):
            kind = expect[0]
            if kind != block[0]:
                self.error(f'第 {position + 1} 块类型不同：md {kind} “{str(expect[1])[:30]}”，docx {block[0]}')
                mismatch += 1
            elif kind == 'figure':
                figure_index += 1
                self.check_figure(figure_index, expect[1], block[1], rels, seen)
            elif kind == 'table':
                if expect[1] != block[1]:
                    self.error(f'第 {position + 1} 块表格内容不同（md {len(expect[1])}x{len(expect[1][0])}，docx {len(block[1])}x{len(block[1][0])}）')
                    mismatch += 1
                self.check_table(position + 1, block[2])
            elif expect[1] != block[1]:
                self.error(f'第 {position + 1} 块文字不同：md “{expect[1][:40]}”，docx “{block[1][:40]}”')
                mismatch += 1
            if mismatch >= 10:
                self.error('不一致过多，停止对照')
                break
        self.stats = {
            'figure': sum(1 for b in block_list if b[0] == 'figure'),
            'table': sum(1 for b in block_list if b[0] == 'table'),
            'heading': sum(1 for b in block_list if b[0].startswith('Heading')),
            'expect_figure': sum(1 for e in expect_list if e[0] == 'figure'),
        }
        if self.stats['figure'] != self.stats['expect_figure']:
            self.error(f'嵌入对象 {self.stats["figure"]} 个，md 图片 {self.stats["expect_figure"]} 张')
        object_count = len(list(self.tree['word/document.xml'].iter(W + 'object')))
        embed_count = sum(1 for n in self.name_list if n.startswith('word/embeddings/'))
        if object_count != self.stats['expect_figure'] or embed_count != self.stats['expect_figure']:
            self.error(f'对象 {object_count} 个、嵌入部件 {embed_count} 个，应各为 {self.stats["expect_figure"]}')
        self.check_layout(block_list)

    def check_table(self, position, table):
        """通栏、固定布局、列宽一致、表头底纹与跨页重复、行不跨页、边框。"""
        label = f'第 {position} 块表格'
        tbl_pr = table.find(W + 'tblPr')
        if tbl_pr.find(f'{W}tblW').get(W + 'w') != str(CONTENT_W) or tbl_pr.find(f'{W}tblLayout').get(W + 'type') != 'fixed':
            self.error(f'{label}：不是通栏固定布局')
        grid = [int(g.get(W + 'w')) for g in table.find(W + 'tblGrid').findall(W + 'gridCol')]
        if sum(grid) != CONTENT_W:
            self.error(f'{label}：列宽合计 {sum(grid)} 不等于版心 {CONTENT_W}')
        for side in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
            border = tbl_pr.find(f'{W}tblBorders/{W}{side}')
            if border is None or border.get(W + 'sz') != '4' or border.get(W + 'color') != '8C9BB0' or border.get(W + 'val') != 'single':
                self.error(f'{label}：{side} 边框不是 0.5 磅 8C9BB0')
        for row_index, row in enumerate(table.findall(W + 'tr')):
            tr_pr = row.find(W + 'trPr')
            if tr_pr is None or tr_pr.find(W + 'cantSplit') is None:
                self.error(f'{label} 第 {row_index + 1} 行：可跨页断开')
            if (tr_pr is not None and tr_pr.find(W + 'tblHeader') is not None) != (row_index == 0):
                self.error(f'{label} 第 {row_index + 1} 行：标题行重复设置不对')
            for cell, width in zip(row.findall(W + 'tc'), grid):
                if cell.find(f'{W}tcPr/{W}tcW').get(W + 'w') != str(width):
                    self.error(f'{label} 第 {row_index + 1} 行：单元格宽度与 gridCol 不同')
                fill = cell.find(f'{W}tcPr/{W}shd')
                if (fill is not None and fill.get(W + 'fill') == 'DCE6F4') != (row_index == 0):
                    self.error(f'{label} 第 {row_index + 1} 行：表头底纹不对')
                for p in cell.findall(W + 'p'):
                    style = p.find(f'{W}pPr/{W}pStyle').get(W + 'val')
                    if style != ('TableHeading' if row_index == 0 else 'TableText'):
                        self.error(f'{label} 第 {row_index + 1} 行：单元格段落样式 {style}')

    def check_layout(self, block_list):
        """分页、表注与表格同页、目录域、列表编号、正文无直接缩进。"""
        paragraph_list = [b for b in block_list if b[0] != 'table' and b[0] != 'figure']
        for index, block in enumerate(block_list):
            if block[0] == 'table':
                caption = block_list[index - 1]
                if caption[0] == 'Caption' and caption[2].find(f'{W}pPr/{W}keepNext') is None:
                    self.error(f'表注“{caption[1]}”没有与表格同页')
        toc_heading = next((b for b in paragraph_list if b[0] == 'TOCHeading' and b[1] == '目录'), None)
        first_heading = next((b for b in paragraph_list if b[0] == 'Heading1'), None)
        for block, name in ((toc_heading, '目录标题'), (first_heading, '第 1 章标题')):
            if block is None or block[2].find(f'{W}pPr/{W}pageBreakBefore') is None:
                self.error(f'{name}没有另起一页')
        instr = ''.join(t.text or '' for t in self.tree['word/document.xml'].iter(W + 'instrText'))
        if 'TOC \\o "1-2" \\h \\z \\u' not in instr:
            self.error(f'没有标题 1 至 2 的目录域：{instr[:60]}')
        numbering = self.tree['word/numbering.xml']
        for block in paragraph_list:
            style_id, text, p = block
            ppr = p.find(W + 'pPr')
            if style_id == 'BulletList':
                num_id = ppr.find(f'{W}numPr/{W}numId')
                if num_id is None:
                    self.error(f'无序列表项没有项目符号：{text[:20]}')
                    continue
                num = next(n for n in numbering.findall(W + 'num') if n.get(W + 'numId') == num_id.get(W + 'val'))
                abstract_id = num.find(W + 'abstractNumId').get(W + 'val')
                abstract = next(a for a in numbering.findall(W + 'abstractNum') if a.get(W + 'abstractNumId') == abstract_id)
                level = abstract.find(W + 'lvl')
                if level.find(W + 'numFmt').get(W + 'val') != 'bullet' or level.find(f'{W}pPr/{W}ind').get(W + 'hanging') is None:
                    self.error('项目符号编号不是悬挂缩进的 bullet')
            if style_id in ('BodyText', 'BulletList', 'OrderedList', 'Caption') and ppr.find(W + 'ind') is not None:
                self.error(f'{style_id} 段落有直接缩进：{text[:20]}')

    def resolve_style(self, styles, style_id):
        """沿 basedOn 链合成样式属性（含文档默认值）。"""
        chain = []
        current = style_id
        while current:
            style = next((s for s in styles.findall(W + 'style') if s.get(W + 'styleId') == current), None)
            if style is None:
                self.error(f'缺少样式 {current}')
                return {}
            chain.append(style)
            based = style.find(W + 'basedOn')
            current = based.get(W + 'val') if based is not None else None
        default_rpr = styles.find(f'{W}docDefaults/{W}rPrDefault/{W}rPr')
        default_ppr = styles.find(f'{W}docDefaults/{W}pPrDefault/{W}pPr')
        prop = {}
        for ppr, rpr in [(default_ppr, default_rpr)] + [(s.find(W + 'pPr'), s.find(W + 'rPr')) for s in reversed(chain)]:
            if rpr is not None:
                fonts = rpr.find(W + 'rFonts')
                if fonts is not None:
                    prop['east_asia'] = fonts.get(W + 'eastAsia', prop.get('east_asia'))
                    prop['ascii'] = fonts.get(W + 'ascii', prop.get('ascii'))
                for tag, key in (('sz', 'size'), ('color', 'color')):
                    node = rpr.find(W + tag)
                    if node is not None:
                        prop[key] = node.get(W + 'val')
                bold = rpr.find(W + 'b')
                if bold is not None:
                    prop['bold'] = bold.get(W + 'val') not in ('0', 'false')
            if ppr is not None:
                for tag, attr, key in (('jc', 'val', 'jc'), ('outlineLvl', 'val', 'outline'), ('spacing', 'line', 'line'),
                                       ('ind', 'firstLineChars', 'first_line_chars'), ('ind', 'hanging', 'hanging')):
                    node = ppr.find(W + tag)
                    if node is not None and node.get(W + attr) is not None:
                        prop[key] = node.get(W + attr)
                if ppr.find(W + 'keepNext') is not None:
                    prop['keep_next'] = True
        prop.setdefault('bold', False)
        prop.setdefault('keep_next', False)
        return prop

    def check_styles_and_settings(self):
        """样式、页面、设置、语言与文档属性。"""
        styles = self.tree['word/styles.xml']
        for fonts in styles.iter(W + 'rFonts'):
            theme = [k for k in fonts.attrib if 'heme' in k]
            if theme:
                self.error(f'样式中仍有主题字体属性：{theme}')
                break
        for style_id, expect in STYLE_EXPECT.items():
            prop = self.resolve_style(styles, style_id)
            for key, value in expect.items():
                if prop.get(key) != value:
                    self.error(f'样式 {style_id} 的 {key} 为 {prop.get(key)}，应为 {value}')
        builtin_name_set = {e.get(W + 'name').lower() for e in styles.iter(W + 'lsdException')}
        for style in styles.findall(W + 'style'):
            name = style.find(W + 'name').get(W + 'val')
            if name.lower() in builtin_name_set and style.get(W + 'customStyle') in ('1', 'true'):
                self.error(f'样式 {name} 是 Word 内置样式名，却标为自定义样式')
        lang = styles.find(f'{W}docDefaults/{W}rPrDefault/{W}rPr/{W}lang')
        if lang is None or lang.get(W + 'eastAsia') != 'zh-CN':
            self.error('文档默认东亚语言不是中文')
        sect = self.tree['word/document.xml'].find(f'{W}body/{W}sectPr')
        size, margin = sect.find(W + 'pgSz'), sect.find(W + 'pgMar')
        if (size.get(W + 'w'), size.get(W + 'h')) != (str(PAGE_W), str(PAGE_H)):
            self.error('纸张不是 A4')
        if [margin.get(W + k) for k in ('top', 'bottom', 'left', 'right')] != [str(MARGIN_TB)] * 2 + [str(MARGIN_LR)] * 2:
            self.error('页边距不是上下 2.54 cm、左右 2.8 cm')
        footer_ref = sect.find(W + 'footerReference')
        footer_name = self.document_rels().get(footer_ref.get(R + 'id'), (None, ''))[1] if footer_ref is not None else ''
        footer = self.tree.get(footer_name)
        footer_text = ''.join(t.text or '' for t in footer.iter(W + 't')) if footer is not None else ''
        footer_instr = ''.join(t.text or '' for t in footer.iter(W + 'instrText')) if footer is not None else ''
        if 'PAGE' not in footer_instr or not footer_text.startswith('— ') or not footer_text.endswith(' —'):
            self.error(f'页脚不是“— 页码 —”：{footer_text}')
        settings = self.tree['word/settings.xml']
        mode = [c.get(W + 'val') for c in settings.iter(W + 'compatSetting') if c.get(W + 'name') == 'compatibilityMode']
        if mode != ['15']:
            self.error(f'compatibilityMode 为 {mode}，应为 15')
        update = settings.find(W + 'updateFields')
        if update is None or update.get(W + 'val') != 'true':
            self.error('没有设置打开时更新域')
        theme_lang = settings.find(W + 'themeFontLang')
        if theme_lang is None or theme_lang.get(W + 'eastAsia') != 'zh-CN':
            self.error('themeFontLang 的东亚语言不是中文')
        core = self.tree['docProps/core.xml']
        title = self.md_path.read_text(encoding='utf-8').split('\n')[0][2:]
        if core.findtext(DC + 'title') != title:
            self.error(f'文档属性标题为 {core.findtext(DC + "title")}，应为 {title}')
        for tag in (DC + 'creator', DC + 'description', CP + 'lastModifiedBy', DC + 'subject', CP + 'keywords', CP + 'category'):
            if core.findtext(tag):
                self.error(f'文档属性 {tag.split("}")[1]} 未清空：{core.findtext(tag)}')
        for name in self.name_list:
            if 'thumbnail' in name or 'stylesWithEffects' in name:
                self.error(f'模板残留部件：{name}')

    def run(self):
        """执行全部检查。"""
        self.check_package()
        if 'word/document.xml' not in self.tree:
            self.error('缺少 word/document.xml')
            return
        self.check_body()
        self.check_styles_and_settings()


def main(argv):
    """命令行入口。"""
    if not argv or len(argv) > 2:
        print('用法：python3 check_docx.py <md 文件> [<docx 文件>]', file=sys.stderr)
        return 2
    md_path = Path(argv[0]).resolve()
    docx_path = Path(argv[1]).resolve() if len(argv) == 2 else md_path.with_suffix('.docx')
    checker = Checker(md_path, docx_path)
    checker.run()
    if checker.error_list:
        print(f'{docx_path.name}：不通过，{len(checker.error_list)} 项')
        for message in checker.error_list:
            print('  ' + message)
        return 1
    stats = checker.stats
    print(f'{docx_path.name}：通过（嵌入对象 {stats["figure"]}，表格 {stats["table"]}，标题 {stats["heading"]}，'
          f'部件 {len(checker.name_list)}）')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
