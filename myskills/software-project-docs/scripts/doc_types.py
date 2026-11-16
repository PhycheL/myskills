"""文档类型登记表与各脚本共用的解析：标题、图注、表注、图片行、需求编号。

新增文档类型：在 DOC_TYPES 中加一项（键为类型名），写 references/<类型>.md，
并在 SKILL.md 的文档类型表加一行。本文件不被直接运行。
"""
import re
from pathlib import Path

# 字段：
#   name 文档名称；file 文件名；prefix 图源与截图的文件名前缀；forbidden 该类型的禁用词；
#   check_impl 检查技术产品名与实现标识；check_requirements 检查需求编号；
#   mapping 要求最后一章为需求与设计映射表（映射的来源文档类型见 mapping_source）；
#   chapter_one_map 映射表开头的三行：来源文档第 1 章的小节标题 → 本文档第 1 章的小节标题。
# 登记顺序即系列顺序：前面的文档是后面文档的依据。
DOC_TYPES = {
    'srs': {
        'name': '需求规格说明',
        'file': '需求规格说明.md',
        'prefix': 'srs-',
        'forbidden': ('技术要求', '设计方案', '用户手册'),
        'check_impl': True,
        'check_requirements': True,
        'mapping': False,
    },
    'design': {
        'name': '设计方案',
        'file': '设计方案.md',
        'prefix': 'design-',
        'forbidden': ('技术要求', '用户手册'),
        'check_impl': True,
        'check_requirements': False,
        'mapping': True,
        'mapping_source': 'srs',
        'chapter_one_map': (('用户与角色', '安全设计'), ('运行环境', '部署设计'), ('功能组成', '总体架构')),
    },
}

# 所有类型共用的禁用词：正文不标注实现状态
COMMON_FORBIDDEN = ('未实现', '待实现', '待开发')
# 登记性小节（不要求配图）的标题关键词
ADMIN_KEYWORDS = ('标识', '文档概述', '术语', '引用文档', '修订记录', '目录', '映射表', '追踪表')
MAPPING_TITLE = '需求与设计映射表'

HEADING_RE = re.compile(r'^(#{1,6}) (\S+)(?: (.*))?$')
SECTION_NUMBER_RE = re.compile(r'^\d+(?:\.\d+)*$')
REQ_ID_RE = re.compile(r'XQ-(\d+(?:\.\d+)*)-(\d{2})(?!\d)')
REQ_ROW_RE = re.compile(r'^\|\s*(XQ-\d+(?:\.\d+)*-\d{2})\s*\|\s*([^|]+?)\s*\|')
FIGURE_CAPTION_RE = re.compile(r'^图 (\d+)-(\d+) (\S.*)$')
TABLE_CAPTION_RE = re.compile(r'^表 (\d+)-(\d+) (\S.*)$')
IMAGE_RE = re.compile(r'!\[([^\]]*)\]\(images/([^)\s]+)\)')
NUMBER_REF_RE = re.compile(r'(图|表)\s?(\d+)-(\d+)(?!\d)')


def read_lines(path):
    return Path(path).read_text(encoding='utf-8').splitlines()


def doc_path(doc_dir, kind):
    return Path(doc_dir) / DOC_TYPES[kind]['file']


def parse_headings(lines):
    """返回 [(行号, 级别, 编号, 标题)]，跳过代码块内的行。不带编号的标题，编号位置是标题的第一个词。"""
    heading_list = []
    in_code = False
    for index, line in enumerate(lines):
        if line.startswith('```'):
            in_code = not in_code
            continue
        if in_code:
            continue
        match = HEADING_RE.match(line)
        if match:
            heading_list.append((index, len(match.group(1)), match.group(2), match.group(3) or ''))
    return heading_list


def is_numbered(heading):
    return bool(SECTION_NUMBER_RE.match(heading[2]))


def numbered_headings(lines):
    return [h for h in parse_headings(lines) if is_numbered(h)]


def requirement_section(requirement_id):
    """XQ-2.1.2-03 → 2.1.2"""
    return requirement_id[3:requirement_id.rfind('-')]


def collect_requirements(lines):
    """收集需求条目表中定义的需求：[(编号, 名称, 行号)]。"""
    requirement_list = []
    for index, line in enumerate(lines):
        match = REQ_ROW_RE.match(line)
        if match:
            requirement_list.append((match.group(1), match.group(2).strip(), index))
    return requirement_list


def iter_captions(lines):
    """逐个给出图注与表注：(类别, 章号, 序号, 行号)。

    图注必须紧跟在图片行之后；表注的下一个非空行必须是表格行。正文中以“图 X-Y”开头的普通句子不算图注。
    """
    for index, line in enumerate(lines):
        match = FIGURE_CAPTION_RE.match(line)
        if match and index > 0 and IMAGE_RE.search(lines[index - 1]):
            yield '图', match.group(1), match.group(2), index
            continue
        match = TABLE_CAPTION_RE.match(line)
        if match:
            following = next((x for x in lines[index + 1:] if x.strip()), '')
            if following.startswith('|'):
                yield '表', match.group(1), match.group(2), index


def parse_kind_argument(doc_dir, kind):
    """把命令行的类型参数展开成类型列表；all 表示目录中存在的全部已登记类型。未知类型返回 None。"""
    if kind == 'all':
        return [k for k in DOC_TYPES if doc_path(doc_dir, k).exists()]
    if kind in DOC_TYPES:
        return [kind]
    return None
