"""检查文档目录中的文档：结构、配图、图片与图源、图文结合、表注、图表编号、文字、需求编号、需求与设计映射。

用法：
  python3 check_docs.py <文档目录> <类型|all> [--forbid 词,词] [--allow 词,词]
  python3 check_docs.py <文档目录> <类型|all> --vocab      只列出正文中的英文词供人工检查
  python3 check_docs.py --file <草稿文件> <类型> [--forbid …] [--allow …]   只做文字检查

类型见 doc_types.py（srs、design）；all 检查目录中存在的全部已登记类型。
--forbid 追加项目禁用词（如上游材料里的系统名称）；--allow 追加允许的英文词（项目专有的领域词）。
图片在 <文档目录>/images/，图源在 <文档目录>/diagrams/。有问题时逐条列出并以非零码退出。
"""
import argparse
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # 导入同目录模块时不在 skill 目录里生成 __pycache__
from doc_types import (
    ADMIN_KEYWORDS, COMMON_FORBIDDEN, DOC_TYPES, FIGURE_CAPTION_RE, IMAGE_RE, MAPPING_TITLE, NUMBER_REF_RE,
    REQ_ID_RE, TABLE_CAPTION_RE, collect_requirements, doc_path, is_numbered, iter_captions,
    parse_headings, parse_kind_argument, read_lines, requirement_section,
)

# 不得出现的技术产品、框架、库与工具名（不区分大小写）
TECH_TERM_LIST = (
    'vue', 'typescript', 'javascript', 'vite', 'node.js', 'nodejs', 'express', 'sqlite', 'sequelize',
    'socket.io', 'websocket', 'log4js', 'swagger', 'openapi', 'ziim', 'libuv', 'libpcap', 'npcap', 'cmake',
    'makefile', 'clang', 'shadcn', 'reka', 'tailwind', 'pinia', 'axios', 'playwright', 'mermaid', 'jwt',
    'bcrypt', 'nunjucks', 'ajv', 'jszip', 'archiver', 'multer', 'pnpm', 'npm', 'eslint', 'vitest',
    'prettier', 'uuid', 'ed25519', 'sha-256', 'pkg', 'mingw', 'gcc', 'glibc', 'lsof',
)
TECH_TERM_RE = re.compile(r'(?<![A-Za-z0-9])(' + '|'.join(re.escape(t) for t in TECH_TERM_LIST) + r')(?![A-Za-z0-9])', re.I)
# 实现形态的标识：接口路径、代码文件名、蛇形、驼峰、类名式与连字符标识符
IMPL_PATTERN_LIST = (
    ('接口路径', re.compile(r'/api/')),
    ('代码文件名', re.compile(r'[\w-]+\.(?:js|mjs|ts|vue|h|hpp|cpp|cc|py|yaml|yml|sh|sql|html|css|bin|njk)(?![A-Za-z])')),
    ('蛇形标识符', re.compile(r'(?<![A-Za-z0-9])[a-z][a-z0-9]*_[a-z0-9_]+(?![A-Za-z0-9])')),
    ('驼峰标识符', re.compile(r'(?<![A-Za-z0-9])[a-z]+[A-Z][A-Za-z0-9]*(?![A-Za-z0-9])')),
    ('类名式标识符', re.compile(r'(?<![A-Za-z0-9])[A-Z][a-z]+[A-Z][A-Za-z0-9]*(?![A-Za-z0-9])')),
    ('连字符标识符', re.compile(r'(?<![A-Za-z0-9-])[a-z]+(?:-[a-z]+)+(?![A-Za-z0-9-])')),
)
# 形似标识符、但属于领域术语或平台名的通用写法；项目专有的用 --allow 传入
ALLOWED_TOKENS = {'QoS', 'x86_64', 'MiB', 'GiB', 'macOS', 'IPv4', 'IPv6', 'aarch64'}
ASCII_WORD_RE = re.compile(r'[A-Za-z][A-Za-z0-9+]*(?:[._-][A-Za-z0-9+]+)*')
EMOJI_RE = re.compile('[\U0001F300-\U0001FAFF☀-➿⭐⭕]')
# 图源里不允许的样式写法：样式只来自 assets
DIAGRAM_STYLE_LIST = (
    ('<style', re.compile(r'<style', re.I)),
    ('%%{init', re.compile(r'%%\{\s*init')),
    ('classDef', re.compile(r'(?m)^\s*classDef\s')),
    ('style', re.compile(r'(?m)^\s*style\s')),
    ('linkStyle', re.compile(r'(?m)^\s*linkStyle\s')),
)
DIAGRAM_ASSET_LIST = ('src="assets/mermaid.min.js"', 'src="assets/diagram.js"', 'href="assets/diagram.css"')
REQ_ID_FULL_RE = re.compile(r'XQ-\d+(?:\.\d+)*-\d{2}')


def own_content_range(lines, heading_list, position):
    """本节自身内容的行范围：到下一个同级或更高级标题、或下一个带编号的标题为止；不编号的下级标题算本节内容。"""
    start = heading_list[position][0] + 1
    level = heading_list[position][1]
    end = len(lines)
    for heading in heading_list[position + 1:]:
        if heading[1] <= level or is_numbered(heading):
            end = heading[0]
            break
    return start, end


def has_numbered_child(heading_list, position):
    level = heading_list[position][1]
    for heading in heading_list[position + 1:]:
        if heading[1] <= level:
            return False
        if is_numbered(heading):
            return True
    return False


def is_admin(title):
    return any(keyword in title for keyword in ADMIN_KEYWORDS)


def check_structure(kind, heading_list, problem_list):
    chapter_list = [h for h in heading_list if h[1] == 2 and is_numbered(h)]
    if DOC_TYPES[kind]['mapping']:
        if not chapter_list or chapter_list[-1][3] != MAPPING_TITLE:
            problem_list.append(f'最后一章应为“{MAPPING_TITLE}”')
    else:
        for _, _, number, title in chapter_list:
            if '映射表' in title or '追踪表' in title:
                problem_list.append(f'{DOC_TYPES[kind]["name"]}不设映射表或追踪表章：{number} {title}')


def check_figures_per_section(lines, heading_list, problem_list):
    for position, heading in enumerate(heading_list):
        _, level, number, title = heading
        if not is_numbered(heading) or is_admin(title):
            continue
        if '.' not in number and has_numbered_child(heading_list, position):
            continue
        start, end = own_content_range(lines, heading_list, position)
        if not any(IMAGE_RE.search(line) for line in lines[start:end]):
            problem_list.append(f'{number} {title} 没有配图')


def check_images(kind, doc_dir, lines, problem_list):
    image_dir = Path(doc_dir) / 'images'
    diagram_dir = Path(doc_dir) / 'diagrams'
    prefix = DOC_TYPES[kind]['prefix']
    referenced = set()
    for index, line in enumerate(lines):
        match = IMAGE_RE.search(line)
        if not match:
            if '![' in line:
                problem_list.append(f'第 {index + 1} 行图片不在 images/ 下')
            continue
        alt, file_name = match.group(1), match.group(2)
        referenced.add(file_name)
        if not file_name.startswith(prefix):
            problem_list.append(f'图片 {file_name} 的文件名不以 {prefix} 开头')
        if not (image_dir / file_name).exists():
            problem_list.append(f'图片不存在：{file_name}')
        html_name = file_name[:-4] + '.html'
        if not (diagram_dir / html_name).exists():
            problem_list.append(f'图片缺少同名图源：{html_name}')
        caption = lines[index + 1].strip() if index + 1 < len(lines) else ''
        if not FIGURE_CAPTION_RE.match(caption):
            problem_list.append(f'图片 {file_name} 下一行不是图注')
        elif caption != alt.strip():
            problem_list.append(f'图片说明文字与图注不一致：{alt} / {caption}')
    for path in sorted(image_dir.glob(prefix + '*.png')):
        if path.name not in referenced:
            problem_list.append(f'截图未被引用：{path.name}')
    for path in sorted(diagram_dir.glob(prefix + '*.html')):
        if path.stem + '.png' not in referenced:
            problem_list.append(f'图源未被引用：{path.name}')
        text = path.read_text(encoding='utf-8')
        if re.search(r'<(title|h1)>\s*图\s?\d', text):
            problem_list.append(f'图源标题不应带图号：{path.name}')
        for label, pattern in DIAGRAM_STYLE_LIST:
            if pattern.search(text):
                problem_list.append(f'图源 {path.name} 含 {label}，样式只来自 assets')
        for asset in DIAGRAM_ASSET_LIST:
            if asset not in text:
                problem_list.append(f'图源 {path.name} 没有引用 {asset.split("=")[1]}')


def check_figure_text(lines, heading_list, problem_list):
    """图文结合：本节正文在图片之前引出该图；图注之后紧接说明段落。"""
    numbered_line_list = [h[0] for h in heading_list if is_numbered(h)]
    for index, line in enumerate(lines):
        if not IMAGE_RE.search(line) or index + 1 >= len(lines):
            continue
        caption = FIGURE_CAPTION_RE.match(lines[index + 1].strip())
        if not caption:
            continue
        figure_number = f'{caption.group(1)}-{caption.group(2)}'
        section_start = max((i for i in numbered_line_list if i < index), default=0)
        ref_re = re.compile(r'图\s?' + re.escape(figure_number) + r'(?!\d)')
        if not any(ref_re.search(x) for x in lines[section_start + 1:index]):
            problem_list.append(f'图 {figure_number} 之前没有引出句')
        following = next((x.strip() for x in lines[index + 2:] if x.strip()), '')
        if not following or following.startswith(('#', '![', '|')) or TABLE_CAPTION_RE.match(following):
            problem_list.append(f'图 {figure_number} 的图注之后没有说明段落')


def check_table_captions(lines, problem_list):
    body_start = next((i for i, line in enumerate(lines) if line.startswith('## 1 ')), len(lines))
    for index in range(body_start, len(lines)):
        if not lines[index].startswith('|') or lines[index - 1].startswith('|'):
            continue
        previous = next((x.strip() for x in reversed(lines[:index]) if x.strip()), '')
        if not TABLE_CAPTION_RE.match(previous):
            problem_list.append(f'第 {index + 1} 行的表格上方没有表注')


def check_numbering(kind, lines, problem_list):
    defined = {'图': [], '表': []}
    for label, chapter, number, _ in iter_captions(lines):
        defined[label].append((int(chapter), int(number)))
    for label, number_list in defined.items():
        expected = {}
        seen = set()
        for chapter, number in number_list:
            if (chapter, number) in seen:
                problem_list.append(f'{label} {chapter}-{number} 编号重复')
            seen.add((chapter, number))
            expected[chapter] = expected.get(chapter, 0) + 1
            if number != expected[chapter]:
                problem_list.append(f'{label} {chapter}-{number} 编号不连续（应为 {chapter}-{expected[chapter]}）')
    defined_set = {(label, f'{c}-{n}') for label, lst in defined.items() for c, n in lst}
    reported = set()
    for line in lines:
        for match in NUMBER_REF_RE.finditer(line):
            key = (match.group(1), f'{match.group(2)}-{match.group(3)}')
            if key not in defined_set and key not in reported:
                reported.add(key)
                problem_list.append(f'引用了不存在的{match.group(1)} {key[1]}')
    other_name_list = [t['name'] for k, t in DOC_TYPES.items() if k != kind]
    for index, line in enumerate(lines, start=1):
        for name in other_name_list:
            match = re.search(re.escape(name) + r'[^。；！？]*?((?:图|表)\s?\d+-\d+)', line)
            if match:
                problem_list.append(f'第 {index} 行引用了{name}的编号“{match.group(1)}”，应写章节号或表名')


def check_text(kind, lines, problem_list, forbid_list=(), allow_set=frozenset()):
    word_list = COMMON_FORBIDDEN + tuple(DOC_TYPES[kind]['forbidden']) + tuple(forbid_list)
    allowed = ALLOWED_TOKENS | set(allow_set)
    allowed_lower = {word.lower() for word in allow_set}
    check_impl = DOC_TYPES[kind]['check_impl']
    for index, line in enumerate(lines, start=1):
        if line.startswith('```'):
            problem_list.append(f'第 {index} 行有代码块，文档中不应出现代码')
        elif '`' in line:
            problem_list.append(f'第 {index} 行有行内代码（反引号）')
        if '**' in line:
            problem_list.append(f'第 {index} 行使用了加粗')
        if EMOJI_RE.search(line):
            problem_list.append(f'第 {index} 行含 emoji')
        for word in word_list:
            if word in line:
                problem_list.append(f'第 {index} 行含禁用词“{word}”')
        if not check_impl:
            continue
        for match in TECH_TERM_RE.finditer(line):
            if match.group(0).lower() not in allowed_lower:
                problem_list.append(f'第 {index} 行含技术产品名“{match.group(0)}”')
        if line.startswith('![') or line.startswith('|---'):
            continue
        for label, pattern in IMPL_PATTERN_LIST:
            for match in pattern.finditer(line):
                if match.group(0) not in allowed:
                    problem_list.append(f'第 {index} 行含{label}“{match.group(0)}”')


def vocabulary(lines):
    """统计正文中出现的英文词（图片行除外），供人工检查。"""
    counter = {}
    for line in lines:
        if line.startswith('!['):
            continue
        for match in ASCII_WORD_RE.finditer(line):
            word = match.group(0)
            if word.startswith('XQ'):
                continue
            counter[word] = counter.get(word, 0) + 1
    return sorted(counter.items(), key=lambda item: (-item[1], item[0]))


def check_requirements(lines, heading_list, problem_list):
    for index, line in enumerate(lines, start=1):
        first_cell = line.split('|')[1].strip() if line.startswith('|') and line.count('|') > 1 else ''
        if first_cell.startswith('XQ') and not REQ_ID_FULL_RE.fullmatch(first_cell):
            problem_list.append(f'第 {index} 行需求编号格式不对：{first_cell}（应为 XQ-<章节号>-<两位序号>）')
    requirement_list = collect_requirements(lines)
    if not requirement_list:
        problem_list.append('没有找到需求条目')
        return
    numbered_list = [h for h in heading_list if is_numbered(h)]
    seen = set()
    sequence = {}
    for requirement_id, _, index in requirement_list:
        if requirement_id in seen:
            problem_list.append(f'需求编号重复：{requirement_id}')
        seen.add(requirement_id)
        section = requirement_section(requirement_id)
        number = int(requirement_id[-2:])
        sequence[section] = sequence.get(section, 0) + 1
        if number != sequence[section]:
            problem_list.append(f'{requirement_id} 序号不连续（应为第 {sequence[section]:02d} 条）')
        owner = [h for h in numbered_list if h[0] <= index]
        owner_number = owner[-1][2] if owner else ''
        if owner_number != section:
            problem_list.append(f'{requirement_id} 写在 {owner_number} 节，与编号不符')


def check_mapping(kind, doc_dir, lines, heading_list, problem_list):
    """对应关系只写在映射表：映射表之前的正文不出现需求编号；映射表覆盖全部需求；每个带需求的小节有同号小节。"""
    source_kind = DOC_TYPES[kind]['mapping_source']
    source_path = doc_path(doc_dir, source_kind)
    if not source_path.exists():
        problem_list.append(f'检查映射需要同目录下的{DOC_TYPES[source_kind]["file"]}')
        return
    requirement_list = collect_requirements(read_lines(source_path))
    mapping_start = next((h[0] for h in heading_list if h[1] == 2 and h[3] == MAPPING_TITLE), len(lines))
    for index, line in enumerate(lines[:mapping_start], start=1):
        if REQ_ID_RE.search(line):
            problem_list.append(f'第 {index} 行正文含需求编号，对应关系只写在{MAPPING_TITLE}')
        for phrase in ('覆盖需求', '对应需求'):
            if re.search(phrase + r'(?!规格说明)', line):
                problem_list.append(f'第 {index} 行正文含“{phrase}”，对应关系只写在{MAPPING_TITLE}')
    table_text = '\n'.join(lines[mapping_start:])
    covered = set()
    for match in re.finditer(r'(XQ-(\d+(?:\.\d+)*)-(\d{2}))(?: 至 XQ-\2-(\d{2}))?', table_text):
        first, section, first_no, last_no = match.group(1), match.group(2), int(match.group(3)), match.group(4)
        if last_no is None:
            covered.add(first)
        else:
            covered.update(f'XQ-{section}-{n:02d}' for n in range(first_no, int(last_no) + 1))
    for requirement_id, _, _ in requirement_list:
        if requirement_id not in covered:
            problem_list.append(f'映射表缺少 {requirement_id}')
    if '（缺少）' in table_text:
        problem_list.append('映射表中有找不到对应设计章节的行（缺少）')
    number_set = {h[2] for h in heading_list if is_numbered(h)}
    for section in sorted({requirement_section(r[0]) for r in requirement_list}):
        if section not in number_set:
            problem_list.append(f'缺少与{DOC_TYPES[source_kind]["name"]} {section} 同号的小节')


def check_document(kind, doc_dir, forbid_list, allow_set):
    path = doc_path(doc_dir, kind)
    lines = read_lines(path)
    heading_list = parse_headings(lines)
    problem_list = []
    check_structure(kind, heading_list, problem_list)
    check_figures_per_section(lines, heading_list, problem_list)
    check_images(kind, doc_dir, lines, problem_list)
    check_figure_text(lines, heading_list, problem_list)
    check_table_captions(lines, problem_list)
    check_numbering(kind, lines, problem_list)
    check_text(kind, lines, problem_list, forbid_list, allow_set)
    if DOC_TYPES[kind]['check_requirements']:
        check_requirements(lines, heading_list, problem_list)
    if DOC_TYPES[kind]['mapping']:
        check_mapping(kind, doc_dir, lines, heading_list, problem_list)
    return problem_list


def report(label, problem_list):
    print(f'[{label}] {"通过" if not problem_list else f"{len(problem_list)} 个问题"}')
    for problem in problem_list:
        print('  - ' + problem)


def split_words(text):
    return [word.strip() for word in (text or '').split(',') if word.strip()]


def main():
    parser = argparse.ArgumentParser(description='检查需求规格说明、设计方案等文档')
    parser.add_argument('positional', nargs='*', help='<文档目录> <类型|all>，或与 --file 连用时的 <类型>')
    parser.add_argument('--file', help='只对一个草稿文件做文字检查')
    parser.add_argument('--forbid', default='', help='追加的禁用词，逗号分隔')
    parser.add_argument('--allow', default='', help='追加的允许英文词，逗号分隔')
    parser.add_argument('--vocab', action='store_true', help='只列出正文中的英文词')
    args = parser.parse_args()
    forbid_list, allow_set = split_words(args.forbid), set(split_words(args.allow))

    if args.file:
        if len(args.positional) != 1 or args.positional[0] not in DOC_TYPES:
            parser.error('--file 之后要给出一个类型：' + '、'.join(DOC_TYPES))
        problem_list = []
        check_text(args.positional[0], read_lines(args.file), problem_list, forbid_list, allow_set)
        report(args.file, problem_list)
        sys.exit(1 if problem_list else 0)

    if len(args.positional) != 2:
        parser.error('需要 <文档目录> <类型|all>')
    doc_dir, kind = args.positional
    kind_list = parse_kind_argument(doc_dir, kind)
    if kind_list is None:
        parser.error(f'未知类型 {kind}，可用：{"、".join(DOC_TYPES)}、all')
    if not kind_list:
        print(f'{doc_dir} 中没有已登记类型的文档')
        sys.exit(1)
    missing = [k for k in kind_list if not doc_path(doc_dir, k).exists()]
    if missing:
        print('文档不存在：' + '、'.join(str(doc_path(doc_dir, k)) for k in missing))
        sys.exit(1)

    if args.vocab:
        for item in kind_list:
            print(f'[{DOC_TYPES[item]["file"]}] 英文词：')
            print('  ' + '、'.join(f'{w}({c})' for w, c in vocabulary(read_lines(doc_path(doc_dir, item)))))
        return
    total = 0
    for item in kind_list:
        problem_list = check_document(item, doc_dir, forbid_list, allow_set)
        total += len(problem_list)
        report(DOC_TYPES[item]['file'], problem_list)
    sys.exit(1 if total else 0)


if __name__ == '__main__':
    main()
