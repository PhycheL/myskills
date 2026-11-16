"""把项目文档的 Markdown 正文渲染成同名 Word 文档（.docx），图以可编辑的 Visio 对象嵌入。

用法：python3 build_docx.py <md 文件> [<md 文件> ...]
依赖：Python 3 与 python-docx 1.2.0；只用 python-docx 与标准库，可以单独复制到文档目录的 tools/ 下使用。

约定（不符合即报错退出，不做兼容）：
- md 只含以下写法：第 1 行 `# 文档名`；`## 修订记录` 加一张表；`## 目录` 加两级无序列表（下级缩进 2 个空格），
  与正文的二、三级标题一一对应；正文中的 `##` 至 `#####` 标题、单行段落、一级无序列表、`1. ` 有序列表、表格、
  单独成行的图片。块与块之间空一行。
- 图片行为 `![图 x-y 名称](images/<名>.png)`，下一行是与替代文字相同的图注；图在 Word 中是 Visio 对象，
  数据取 md 同目录的 `vsdx/<名>.vsdx`，预览图取 `images/<名>.png`，二者宽高比相差不得超过 1%。
- 除修订记录表外，每张表格的前两行是表注（表注、空行、表格）。
- 不支持代码块、引用、嵌套列表（目录除外）、行内 HTML、加粗、行内代码。

版式：A4，上下边距 2.54 cm、左右 2.8 cm；正文宋体小四、1.5 倍行距、首行缩进 2 字符；
标题 1 至 4 依次为黑体三号、黑体四号、黑体小四、宋体小四加粗；图注、表注黑体五号居中；表头浅蓝底纹并跨页重复。
"""
import re
import struct
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from docx import Document
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.opc.packuri import PackURI
from docx.opc.part import Part
from docx.oxml import parse_xml
from docx.oxml.ns import qn
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, Twips

W_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
V_NS = 'urn:schemas-microsoft-com:vml'
O_NS = 'urn:schemas-microsoft-com:office:office'
NS_DECL = f'xmlns:w="{W_NS}" xmlns:r="{R_NS}" xmlns:v="{V_NS}" xmlns:o="{O_NS}"'
VISIO_NS = '{http://schemas.microsoft.com/office/visio/2012/main}'

# A4，上下 2.54 cm，左右 2.8 cm（单位：twip，1/1440 英寸）
PAGE_W, PAGE_H, MARGIN_TB, MARGIN_LR = 11906, 16838, 1440, 1587
CONTENT_W = PAGE_W - 2 * MARGIN_LR
MAX_FIGURE_H = round(22 / 2.54 * 1440)
BODY_FONT = '宋体'
HEI_FONT = '黑体'
LATIN_FONT = 'Times New Roman'
TABLE_HEADER_FILL = 'DCE6F4'
TABLE_BORDER_COLOR = '8C9BB0'
VISIO_PROG_ID = 'Visio.Drawing.15'
VISIO_CONTENT_TYPE = 'application/vnd.ms-visio.drawing'
RT_STYLES_WITH_EFFECTS = 'http://schemas.microsoft.com/office/2007/relationships/stylesWithEffects'
FRONT_HEADING_LIST = ['修订记录', '目录']

FIGURE_RE = re.compile(r'^!\[(图 \d+-\d+ [^\]]+)\]\(images/([a-z0-9-]+)\.png\)$')
TABLE_CAPTION_RE = re.compile(r'^表 \d+-\d+ \S')
HEADING_RE = re.compile(r'^(#{1,5}) (\S.*)$')
ORDERED_RE = re.compile(r'^(\d+)\. (\S.*)$')
BULLET_RE = re.compile(r'^- (\S.*)$')
TOC_ITEM_RE = re.compile(r'^( {0,2})- (\S.*)$')
TABLE_SEPARATOR_RE = re.compile(r'^:?-{3,}:?$')
FORBIDDEN_LIST = [
    (re.compile(r'\*\*'), '加粗'),
    (re.compile(r'`'), '行内代码或代码块'),
    (re.compile(r'<[A-Za-z/!]'), '行内 HTML'),
    (re.compile(r'^>'), '引用'),
    (re.compile(r'^\s'), '缩进的行（只有目录的下级条目可以缩进）'),
    (re.compile(r'^[*+] '), '以 * 或 + 开头的列表'),
    (re.compile(r'^#{6,} '), '六级及以下标题'),
]


class MarkdownError(Exception):
    """md 不符合约定的写法。"""


def fail(line_no, message):
    """抛出带行号的约定错误。"""
    raise MarkdownError(f'第 {line_no} 行：{message}')


def split_table_row(line, line_no):
    """把表格行拆成单元格文字列表。"""
    if not (line.startswith('|') and line.endswith('|')):
        fail(line_no, '表格行必须以 | 开头和结尾')
    return [cell.strip() for cell in line[1:-1].split('|')]


def parse_markdown(text):
    """把 md 解析为 (文档名, 块列表)。块是 dict，kind 取 heading、front_heading、toc、paragraph、bullet、
    ordered、table_caption、table、figure；figure 带图注。"""
    line_list = text.split('\n')
    if line_list and line_list[-1] == '':
        line_list.pop()
    first = HEADING_RE.match(line_list[0]) if line_list else None
    if not first or first.group(1) != '#':
        fail(1, '第 1 行必须是 # 文档名')
    title = first.group(2)
    block_list = []
    i = 1
    while i < len(line_list):
        line = line_list[i]
        line_no = i + 1
        if line == '':
            i += 1
            continue
        if i > 0 and line_list[i - 1] != '' and not (block_list and block_list[-1]['kind'] in ('bullet', 'ordered', 'toc')):
            fail(line_no, '块与块之间必须空一行（图注除外）')
        heading = HEADING_RE.match(line)
        if heading:
            level = len(heading.group(1)) - 1
            if level == 0:
                fail(line_no, '只能有一个 # 文档名')
            heading_text = heading.group(2)
            if level == 1 and heading_text in FRONT_HEADING_LIST:
                expect = FRONT_HEADING_LIST[sum(1 for b in block_list if b['kind'] == 'front_heading')]
                if heading_text != expect or any(b['kind'] == 'heading' for b in block_list):
                    fail(line_no, '修订记录、目录必须依次位于正文之前')
                block_list.append({'kind': 'front_heading', 'text': heading_text, 'line': line_no})
                if heading_text == '目录':
                    i = parse_toc(line_list, i + 1, block_list)
                    continue
            else:
                block_list.append({'kind': 'heading', 'level': level, 'text': heading_text, 'line': line_no})
            i += 1
            continue
        for pattern, name in FORBIDDEN_LIST:
            if pattern.search(line):
                fail(line_no, f'不支持的写法：{name}')
        if line.startswith('|'):
            i = parse_table(line_list, i, block_list)
            continue
        if line.startswith('!['):
            figure = FIGURE_RE.match(line)
            if not figure:
                fail(line_no, '图片行必须写成 ![图 x-y 名称](images/<名>.png)')
            caption = line_list[i + 1] if i + 1 < len(line_list) else ''
            if caption != figure.group(1):
                fail(line_no + 1, f'图注必须与替代文字相同：{figure.group(1)}')
            block_list.append({'kind': 'figure', 'caption': caption, 'name': figure.group(2), 'line': line_no})
            i += 2
            continue
        bullet = BULLET_RE.match(line)
        ordered = ORDERED_RE.match(line)
        if bullet:
            block_list.append({'kind': 'bullet', 'text': bullet.group(1), 'line': line_no})
        elif ordered:
            block_list.append({'kind': 'ordered', 'number': ordered.group(1), 'text': ordered.group(2), 'line': line_no})
        elif line.startswith('-') or line.startswith('!'):
            fail(line_no, '无法识别的列表或图片写法')
        else:
            block_list.append({'kind': 'paragraph', 'text': line, 'line': line_no})
        i += 1
    if not any(b['kind'] == 'heading' for b in block_list):
        fail(len(line_list), '没有正文标题')
    check_toc(block_list)
    return title, block_list


def parse_toc(line_list, start, block_list):
    """解析 ## 目录 下的两级列表，返回列表之后的行下标。"""
    i = start
    while i < len(line_list) and line_list[i] == '':
        i += 1
    entry_list = []
    while i < len(line_list) and line_list[i] != '':
        item = TOC_ITEM_RE.match(line_list[i])
        if not item:
            fail(i + 1, '目录条目必须写成 - 标题 或缩进 2 个空格的 - 标题')
        entry_list.append((1 if item.group(1) == '' else 2, item.group(2)))
        i += 1
    if not entry_list or entry_list[0][0] != 1:
        fail(start + 1, '目录必须以一级条目开始')
    block_list.append({'kind': 'toc', 'entry_list': entry_list, 'line': start + 1})
    return i


def check_toc(block_list):
    """目录条目必须与正文的二、三级标题（Word 标题 1、2）逐一相同。"""
    toc = next((b for b in block_list if b['kind'] == 'toc'), None)
    if toc is None:
        raise MarkdownError('缺少 ## 目录')
    heading_list = [(b['level'], b['text']) for b in block_list if b['kind'] == 'heading' and b['level'] <= 2]
    if toc['entry_list'] != heading_list:
        for index, (entry, heading) in enumerate(zip(toc['entry_list'], heading_list)):
            if entry != heading:
                raise MarkdownError(f'目录第 {index + 1} 条“{entry[1]}”与正文标题“{heading[1]}”不一致')
        raise MarkdownError(f'目录有 {len(toc["entry_list"])} 条，正文二、三级标题有 {len(heading_list)} 个')


def parse_table(line_list, start, block_list):
    """解析表格；除修订记录表外，表格前两行必须是表注。返回表格之后的行下标。"""
    row_list = []
    i = start
    while i < len(line_list) and line_list[i].startswith('|'):
        row_list.append(split_table_row(line_list[i], i + 1))
        i += 1
    if len(row_list) < 3 or not all(TABLE_SEPARATOR_RE.match(c) for c in row_list[1]):
        fail(start + 1, '表格至少有表头、分隔行和一行数据')
    row_list.pop(1)
    column_count = len(row_list[0])
    for offset, row in enumerate(row_list):
        if len(row) != column_count:
            fail(start + 1 + offset, f'列数 {len(row)} 与表头的 {column_count} 不同')
    previous = block_list[-1] if block_list else None
    is_revision = previous is not None and previous['kind'] == 'front_heading' and previous['text'] == '修订记录'
    if not is_revision:
        caption = line_list[start - 2] if start >= 2 else ''
        if not (line_list[start - 1] == '' and TABLE_CAPTION_RE.match(caption)):
            fail(start + 1, '表格前必须是“表注、空行”')
        if previous is None or previous['kind'] != 'paragraph' or previous['text'] != caption:
            fail(start - 1, '表注必须单独成段')
        previous['kind'] = 'table_caption'
    block_list.append({'kind': 'table', 'row_list': row_list, 'line': start + 1})
    return i


def column_widths(row_list):
    """按内容长度分配列宽（twip）：不超过 8 字的列按字数定宽（字数×220+400），其余列按内容长度（上限 60 字）分剩余宽度；
    长列分到的宽度不少于 8 字列的宽度，不足的定为该宽度，余下的再在其他长列中分配，以免 9 至 15 字的列被挤成几个字宽；
    各列的最小宽度合计超过版心时，全部按比例缩小。"""
    count = len(row_list[0])
    length_list = [max(2, *(len(row[c]) for row in row_list)) for c in range(count)]
    short_width = lambda length: length * 220 + 400
    width_list = [short_width(length) if length <= 8 else 0 for length in length_list]
    long_list = [c for c in range(count) if length_list[c] > 8]
    if long_list:
        while True:
            rest = CONTENT_W - sum(width_list[c] for c in range(count) if c not in long_list)
            long_total = sum(min(length_list[c], 60) for c in long_list)
            narrow_list = [c for c in long_list if rest * min(length_list[c], 60) / long_total < short_width(8)]
            if not narrow_list:
                break
            for c in narrow_list:
                width_list[c] = short_width(8)
                long_list.remove(c)
            if not long_list:
                break
        for c in long_list:
            width_list[c] = round(rest * min(length_list[c], 60) / long_total)
    elif sum(width_list) < CONTENT_W:
        rest = CONTENT_W - sum(width_list)
        width_list = [w + rest // count for w in width_list]
    total = sum(width_list)
    if total > CONTENT_W:
        width_list = [round(w * CONTENT_W / total) for w in width_list]
    width_list[-1] += CONTENT_W - sum(width_list)
    if min(width_list) < 600:
        raise MarkdownError(f'表格列宽不足：{width_list}（表头 {row_list[0]}）')
    return width_list


def read_visio_page(vsdx_path):
    """读 vsdx 第一页的页面宽高（英寸）。"""
    with zipfile.ZipFile(vsdx_path) as package:
        pages = package.read('visio/pages/pages.xml')
    root = parse_xml(pages)
    cell = {c.get('N'): float(c.get('V')) for c in root.iter(VISIO_NS + 'Cell') if c.get('N') in ('PageWidth', 'PageHeight')}
    return cell['PageWidth'], cell['PageHeight']


def read_png_size(png_path):
    """读 PNG 的像素宽高。"""
    with open(png_path, 'rb') as file:
        header = file.read(24)
    if header[:8] != b'\x89PNG\r\n\x1a\n':
        raise MarkdownError(f'不是 PNG 文件：{png_path}')
    return struct.unpack('>II', header[16:24])


def rpr_xml(east_asia, size, bold=False):
    """生成样式的字符属性：西文 Times New Roman，东亚字体与字号（半磅）给定，黑色。
    字体提示设为东亚，使中文引号等中西文共用的字符按东亚字体显示。"""
    bold_xml = '<w:b/><w:bCs/>' if bold else '<w:b w:val="0"/><w:bCs w:val="0"/>'
    return (f'<w:rPr {NS_DECL}><w:rFonts w:ascii="{LATIN_FONT}" w:hAnsi="{LATIN_FONT}" w:eastAsia="{east_asia}" w:cs="{LATIN_FONT}" w:hint="eastAsia"/>'
            f'{bold_xml}<w:i w:val="0"/><w:iCs w:val="0"/><w:color w:val="000000"/><w:spacing w:val="0"/><w:kern w:val="0"/>'
            f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/></w:rPr>')


def set_style(styles, style_id, name, ppr_inner, rpr, based_on='Normal', builtin=False):
    """新建或改写一个段落样式：保留名称等元信息，pPr 与 rPr 整体替换。
    新建时 builtin 为真表示 Word 内置样式（如目录 1、2），按内置样式写：不标自定义、不进快速样式库。"""
    style = styles.find(f'w:style[@w:styleId="{style_id}"]', {'w': W_NS})
    if style is None:
        if builtin:
            style = parse_xml(f'<w:style {NS_DECL} w:type="paragraph" w:styleId="{style_id}"><w:name w:val="{name}"/>'
                              f'<w:basedOn w:val="{based_on}"/><w:next w:val="Normal"/><w:uiPriority w:val="39"/><w:unhideWhenUsed/></w:style>')
        else:
            style = parse_xml(f'<w:style {NS_DECL} w:type="paragraph" w:customStyle="1" w:styleId="{style_id}">'
                              f'<w:name w:val="{name}"/><w:basedOn w:val="{based_on}"/><w:qFormat/></w:style>')
        styles.append(style)
    for tag in ('w:pPr', 'w:rPr'):
        for old in style.findall(qn(tag)):
            style.remove(old)
    style.append(parse_xml(f'<w:pPr {NS_DECL}>{ppr_inner}</w:pPr>'))
    if rpr is not None:
        style.append(parse_xml(rpr))


def setup_styles(document):
    """按版式设定文档默认值与各段落样式，去掉全部主题字体属性。"""
    styles = document.styles.element
    for fonts in styles.iter(qn('w:rFonts')):
        for attr in ('w:asciiTheme', 'w:hAnsiTheme', 'w:eastAsiaTheme', 'w:cstheme'):
            if fonts.get(qn(attr)) is not None:
                del fonts.attrib[qn(attr)]
    defaults = styles.find(qn('w:docDefaults'))
    rpr_default = defaults.find(qn('w:rPrDefault'))
    rpr_default.remove(rpr_default.find(qn('w:rPr')))
    rpr_default.append(parse_xml(
        f'<w:rPr {NS_DECL}><w:rFonts w:ascii="{LATIN_FONT}" w:hAnsi="{LATIN_FONT}" w:eastAsia="{BODY_FONT}" w:cs="{LATIN_FONT}" w:hint="eastAsia"/>'
        '<w:kern w:val="2"/><w:sz w:val="24"/><w:szCs w:val="24"/><w:lang w:val="en-US" w:eastAsia="zh-CN" w:bidi="ar-SA"/></w:rPr>'))
    ppr_default = defaults.find(qn('w:pPrDefault'))
    ppr_default.remove(ppr_default.find(qn('w:pPr')))
    ppr_default.append(parse_xml(f'<w:pPr {NS_DECL}><w:spacing w:after="0" w:line="240" w:lineRule="auto"/></w:pPr>'))

    body_rpr = rpr_xml(BODY_FONT, 24)
    set_style(styles, 'Normal', 'Normal', '<w:spacing w:after="60" w:line="360" w:lineRule="auto"/><w:jc w:val="both"/>', body_rpr, None)
    set_style(styles, 'BodyText', 'Body Text', '<w:ind w:firstLineChars="200" w:firstLine="480"/>', None)
    set_style(styles, 'Title', 'Title', '<w:spacing w:before="240" w:after="360" w:line="240" w:lineRule="auto"/><w:jc w:val="center"/>',
              rpr_xml(HEI_FONT, 36, bold=True))
    heading_list = [
        ('Heading1', HEI_FONT, 32, False, 360, 180),
        ('Heading2', HEI_FONT, 28, False, 240, 120),
        ('Heading3', HEI_FONT, 24, False, 180, 60),
        ('Heading4', BODY_FONT, 24, True, 120, 60),
    ]
    for level, (style_id, font, size, bold, before, after) in enumerate(heading_list):
        set_style(styles, style_id, f'heading {level + 1}',
                  f'<w:keepNext/><w:keepLines/><w:spacing w:before="{before}" w:after="{after}" w:line="360" w:lineRule="auto"/>'
                  f'<w:jc w:val="left"/><w:outlineLvl w:val="{level}"/>', rpr_xml(font, size, bold))
    set_style(styles, 'TOCHeading', 'TOC Heading',
              '<w:keepNext/><w:keepLines/><w:spacing w:before="240" w:after="240" w:line="360" w:lineRule="auto"/>'
              '<w:jc w:val="center"/><w:outlineLvl w:val="9"/>', rpr_xml(HEI_FONT, 32), based_on='Heading1')
    set_style(styles, 'Caption', 'caption', '<w:spacing w:before="0" w:after="200" w:line="240" w:lineRule="auto"/><w:jc w:val="center"/>',
              rpr_xml(HEI_FONT, 21))
    toc_tab = f'<w:tabs><w:tab w:val="right" w:leader="dot" w:pos="{CONTENT_W}"/></w:tabs>'
    set_style(styles, 'TOC1', 'toc 1', f'{toc_tab}<w:spacing w:after="0"/><w:jc w:val="left"/>', None, builtin=True)
    set_style(styles, 'TOC2', 'toc 2', f'{toc_tab}<w:spacing w:after="0"/><w:ind w:leftChars="200" w:left="480"/><w:jc w:val="left"/>', None, builtin=True)
    set_style(styles, 'Figure', '图', '<w:keepNext/><w:spacing w:before="120" w:after="60" w:line="240" w:lineRule="auto"/><w:jc w:val="center"/>', None)
    set_style(styles, 'TableText', '表格文字', '<w:spacing w:before="20" w:after="20" w:line="300" w:lineRule="auto"/><w:jc w:val="left"/>',
              rpr_xml(BODY_FONT, 21))
    set_style(styles, 'TableHeading', '表头文字', '<w:keepNext/><w:jc w:val="center"/>', rpr_xml(HEI_FONT, 21, bold=True), based_on='TableText')
    set_style(styles, 'BulletList', '无序列表', '<w:spacing w:after="40"/><w:ind w:left="840" w:hanging="360"/>', None)
    set_style(styles, 'OrderedList', '有序列表', '<w:spacing w:after="40"/><w:ind w:left="840" w:hanging="360"/>', None)


def add_bullet_numbering(document):
    """在编号部件中新增项目符号编号（●，悬挂缩进），返回 numId。"""
    numbering = document.part.numbering_part.element
    abstract_id = 1 + max(int(a.get(qn('w:abstractNumId'))) for a in numbering.findall(qn('w:abstractNum')))
    num_id = 1 + max(int(n.get(qn('w:numId'))) for n in numbering.findall(qn('w:num')))
    abstract = parse_xml(
        f'<w:abstractNum {NS_DECL} w:abstractNumId="{abstract_id}"><w:multiLevelType w:val="singleLevel"/>'
        '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="●"/><w:lvlJc w:val="left"/>'
        '<w:pPr><w:ind w:left="840" w:hanging="360"/></w:pPr>'
        f'<w:rPr><w:rFonts w:ascii="{BODY_FONT}" w:hAnsi="{BODY_FONT}" w:eastAsia="{BODY_FONT}" w:hint="eastAsia"/>'
        '<w:sz w:val="16"/><w:szCs w:val="16"/></w:rPr></w:lvl></w:abstractNum>')
    numbering.findall(qn('w:abstractNum'))[-1].addnext(abstract)
    numbering.append(parse_xml(f'<w:num {NS_DECL} w:numId="{num_id}"><w:abstractNumId w:val="{abstract_id}"/></w:num>'))
    return num_id


def setup_document(document, title):
    """页面、页脚、设置与文档属性；去掉模板带来的缩略图与 Word 2010 样式副本。"""
    section = document.sections[0]
    section.page_width, section.page_height = Twips(PAGE_W), Twips(PAGE_H)
    section.top_margin = section.bottom_margin = Twips(MARGIN_TB)
    section.left_margin = section.right_margin = Twips(MARGIN_LR)
    section.header_distance, section.footer_distance = Twips(851), Twips(992)
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer.paragraph_format.space_after = Pt(0)
    footer.paragraph_format.line_spacing = 1.0
    page_rpr = '<w:rPr><w:sz w:val="18"/><w:szCs w:val="18"/></w:rPr>'
    for run_inner in ('<w:t xml:space="preserve">— </w:t>', '<w:fldChar w:fldCharType="begin"/>',
                      '<w:instrText xml:space="preserve"> PAGE </w:instrText>', '<w:fldChar w:fldCharType="separate"/>',
                      '<w:t>1</w:t>', '<w:fldChar w:fldCharType="end"/>', '<w:t xml:space="preserve"> —</w:t>'):
        footer._p.append(parse_xml(f'<w:r {NS_DECL}>{page_rpr}{run_inner}</w:r>'))

    settings = document.settings.element
    for compat in settings.iter(qn('w:compatSetting')):
        if compat.get(qn('w:name')) == 'compatibilityMode':
            compat.set(qn('w:val'), '15')
    settings.find(qn('w:compat')).addprevious(parse_xml(f'<w:updateFields {NS_DECL} w:val="true"/>'))
    settings.find(qn('w:defaultTabStop')).set(qn('w:val'), '420')
    settings.find(qn('w:themeFontLang')).set(qn('w:eastAsia'), 'zh-CN')

    for rels, reltype in ((document.part.package.rels, RT.THUMBNAIL), (document.part.rels, RT_STYLES_WITH_EFFECTS)):
        for r_id in [k for k, rel in rels.items() if rel.reltype == reltype]:
            del rels[r_id]

    properties = document.core_properties
    now = datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)
    properties.title = title
    properties.author = ''
    properties.comments = ''
    properties.last_modified_by = ''
    properties.subject = ''
    properties.keywords = ''
    properties.category = ''
    properties.revision = 1
    properties.created = now
    properties.modified = now


class DocxBuilder:
    """把块列表依次写入 Word 文档。"""

    def __init__(self, document, md_dir):
        self.document = document
        self.md_dir = md_dir
        self.body = document.element.body
        self.bullet_num_id = add_bullet_numbering(document)
        self.figure_count = 0
        self.previous_kind = ''

    def add_paragraph(self, text, style):
        """按样式新增一段，返回段落。"""
        paragraph = self.document.add_paragraph(text, style=style)
        if self.previous_kind == 'table' and not style.startswith('Heading'):
            paragraph.paragraph_format.space_before = Pt(6)
        return paragraph

    def add_heading(self, block):
        """标题 1 至 4；正文第一个标题另起一页。"""
        paragraph = self.add_paragraph(block['text'], f'Heading {block["level"]}')
        if self.previous_kind in ('toc', 'front_heading'):
            paragraph.paragraph_format.page_break_before = True

    def add_front_heading(self, block):
        """修订记录、目录的标题：不进目录的标题样式；目录另起一页。"""
        paragraph = self.add_paragraph(block['text'], 'TOC Heading')
        if block['text'] == '目录':
            paragraph.paragraph_format.page_break_before = True

    def add_toc(self, block):
        """Word 目录域（标题 1 至 2），域结果预先写入底稿目录的条目，打开时由 Word 更新。"""
        entry_list = block['entry_list']
        for index, (level, text) in enumerate(entry_list):
            paragraph = self.document.add_paragraph(style=f'toc {level}')
            if index == 0:
                for inner in ('<w:fldChar w:fldCharType="begin"/>',
                              '<w:instrText xml:space="preserve"> TOC \\o "1-2" \\h \\z \\u </w:instrText>',
                              '<w:fldChar w:fldCharType="separate"/>'):
                    paragraph._p.append(parse_xml(f'<w:r {NS_DECL}>{inner}</w:r>'))
            paragraph.add_run(text)
            if index == len(entry_list) - 1:
                paragraph._p.append(parse_xml(f'<w:r {NS_DECL}><w:fldChar w:fldCharType="end"/></w:r>'))

    def add_bullet(self, block):
        """无序列表项：项目符号编号，悬挂缩进。"""
        paragraph = self.add_paragraph(block['text'], '无序列表')
        num_pr = paragraph._p.get_or_add_pPr().get_or_add_numPr()
        num_pr.get_or_add_ilvl().val = 0
        num_pr.get_or_add_numId().val = self.bullet_num_id

    def add_ordered(self, block):
        """有序列表项：保留 md 中的序号文字，序号后接制表符，悬挂缩进。"""
        self.add_paragraph(f'{block["number"]}.\t{block["text"]}', '有序列表')

    def add_table(self, block):
        """通栏表格：固定列宽，表头浅蓝底纹、黑体加粗居中并跨页重复，行不跨页断开。"""
        row_list = block['row_list']
        width_list = column_widths(row_list)
        border = f'w:val="single" w:sz="4" w:space="0" w:color="{TABLE_BORDER_COLOR}"'
        border_xml = ''.join(f'<w:{side} {border}/>' for side in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'))
        row_xml_list = []
        for row_index, row in enumerate(row_list):
            is_header = row_index == 0
            row_pr = '<w:trPr><w:cantSplit/><w:tblHeader/></w:trPr>' if is_header else '<w:trPr><w:cantSplit/></w:trPr>'
            shading = f'<w:shd w:val="clear" w:color="auto" w:fill="{TABLE_HEADER_FILL}"/>' if is_header else ''
            style = 'TableHeading' if is_header else 'TableText'
            cell_xml = ''.join(
                f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>{shading}<w:vAlign w:val="center"/></w:tcPr>'
                f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr><w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p></w:tc>'
                for width, text in zip(width_list, row))
            row_xml_list.append(f'<w:tr>{row_pr}{cell_xml}</w:tr>')
        grid = ''.join(f'<w:gridCol w:w="{width}"/>' for width in width_list)
        table = parse_xml(
            f'<w:tbl {NS_DECL}><w:tblPr><w:tblW w:w="{CONTENT_W}" w:type="dxa"/><w:tblInd w:w="0" w:type="dxa"/>'
            f'<w:tblBorders>{border_xml}</w:tblBorders><w:tblLayout w:type="fixed"/>'
            '<w:tblCellMar><w:top w:w="60" w:type="dxa"/><w:left w:w="100" w:type="dxa"/>'
            '<w:bottom w:w="60" w:type="dxa"/><w:right w:w="100" w:type="dxa"/></w:tblCellMar>'
            '<w:tblLook w:val="04A0" w:firstRow="1" w:lastRow="0" w:firstColumn="0" w:lastColumn="0" w:noHBand="0" w:noVBand="1"/>'
            f'</w:tblPr><w:tblGrid>{grid}</w:tblGrid>{"".join(row_xml_list)}</w:tbl>')
        self.body.sectPr.addprevious(table)

    def add_figure(self, block):
        """图：嵌入 Visio 对象（数据为 vsdx 原文件，预览为同名 PNG），下接图注。"""
        name = block['name']
        png_path = self.md_dir / 'images' / f'{name}.png'
        vsdx_path = self.md_dir / 'vsdx' / f'{name}.vsdx'
        for path in (png_path, vsdx_path):
            if not path.is_file():
                fail(block['line'], f'缺少文件 {path.relative_to(self.md_dir)}')
        page_w, page_h = read_visio_page(vsdx_path)
        png_w, png_h = read_png_size(png_path)
        if abs((png_w / png_h) / (page_w / page_h) - 1) > 0.01:
            fail(block['line'], f'{name}：预览图宽高比 {png_w}x{png_h} 与 Visio 页面 {page_w:.3f}x{page_h:.3f} 英寸相差超过 1%')
        scale = min(1, CONTENT_W / 1440 / page_w, MAX_FIGURE_H / 1440 / page_h)
        self.figure_count += 1
        n = self.figure_count
        image_rid, _ = self.document.part.get_or_add_image(str(png_path))
        package_part = Part(PackURI(f'/word/embeddings/Microsoft_Visio_Drawing{n}.vsdx'), VISIO_CONTENT_TYPE,
                            vsdx_path.read_bytes(), self.document.part.package)
        object_rid = self.document.part.relate_to(package_part, RT.PACKAGE)
        shape_id = f'_x0000_i{1024 + n}'
        shapetype = ''
        if n == 1:
            formula_list = ['if lineDrawn pixelLineWidth 0', 'sum @0 1 0', 'sum 0 0 @1', 'prod @2 1 2', 'prod @3 21600 pixelWidth',
                            'prod @3 21600 pixelHeight', 'sum @0 0 1', 'prod @6 1 2', 'prod @7 21600 pixelWidth', 'sum @8 21600 0',
                            'prod @7 21600 pixelHeight', 'sum @10 21600 0']
            shapetype = (
                '<v:shapetype id="_x0000_t75" coordsize="21600,21600" o:spt="75" o:preferrelative="t" '
                'path="m@4@5l@4@11@9@11@9@5xe" filled="f" stroked="f"><v:stroke joinstyle="miter"/><v:formulas>'
                + ''.join(f'<v:f eqn="{f}"/>' for f in formula_list)
                + '</v:formulas><v:path o:extrusionok="f" gradientshapeok="t" o:connecttype="rect"/>'
                '<o:lock v:ext="edit" aspectratio="t"/></v:shapetype>')
        object_xml = (
            f'<w:object {NS_DECL} w:dxaOrig="{round(page_w * 1440)}" w:dyaOrig="{round(page_h * 1440)}">{shapetype}'
            f'<v:shape id="{shape_id}" type="#_x0000_t75" style="width:{page_w * 72 * scale:.2f}pt;height:{page_h * 72 * scale:.2f}pt" o:ole="">'
            f'<v:imagedata r:id="{image_rid}" o:title=""/></v:shape>'
            f'<o:OLEObject Type="Embed" ProgID="{VISIO_PROG_ID}" ShapeID="{shape_id}" DrawAspect="Content" '
            f'ObjectID="_{1800000000 + n}" r:id="{object_rid}"/></w:object>')
        paragraph = self.add_paragraph('', '图')
        paragraph.add_run()._r.append(parse_xml(object_xml))
        self.previous_kind = 'figure'
        self.add_paragraph(block['caption'], 'Caption')

    def add_block(self, block):
        """按块类型写入。"""
        kind = block['kind']
        if kind == 'heading':
            self.add_heading(block)
        elif kind == 'front_heading':
            self.add_front_heading(block)
        elif kind == 'toc':
            self.add_toc(block)
        elif kind == 'paragraph':
            self.add_paragraph(block['text'], 'Body Text')
        elif kind == 'bullet':
            self.add_bullet(block)
        elif kind == 'ordered':
            self.add_ordered(block)
        elif kind == 'table_caption':
            self.add_paragraph(block['text'], 'Caption').paragraph_format.keep_with_next = True
        elif kind == 'table':
            self.add_table(block)
        elif kind == 'figure':
            self.add_figure(block)
        else:
            raise MarkdownError(f'未知的块类型 {kind}')
        self.previous_kind = kind


def build(md_path):
    """把一个 md 渲染成同名 docx，返回输出路径与统计。"""
    title, block_list = parse_markdown(md_path.read_text(encoding='utf-8'))
    document = Document()
    for paragraph in list(document.paragraphs):
        paragraph._p.getparent().remove(paragraph._p)
    setup_styles(document)
    setup_document(document, title)
    builder = DocxBuilder(document, md_path.parent)
    document.add_paragraph(title, style='Title')
    for block in block_list:
        builder.add_block(block)
    out_path = md_path.with_suffix('.docx')
    document.save(out_path)
    count = {kind: sum(1 for b in block_list if b['kind'] == kind) for kind in ('heading', 'table', 'figure')}
    return out_path, count


def main(argv):
    """命令行入口：逐个渲染，任一文件不符合约定时报错并以非零码退出。"""
    if not argv:
        print('用法：python3 build_docx.py <md 文件> [<md 文件> ...]', file=sys.stderr)
        return 2
    for arg in argv:
        md_path = Path(arg).resolve()
        try:
            out_path, count = build(md_path)
        except MarkdownError as error:
            print(f'{md_path.name}：{error}', file=sys.stderr)
            return 1
        print(f'已生成 {out_path.name}：标题 {count["heading"]}，表格 {count["table"]}，图 {count["figure"]}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
