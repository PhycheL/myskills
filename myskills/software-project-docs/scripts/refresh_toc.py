"""重建文档的“## 目录”；设计方案等要求映射表的类型另外重建最后一章“需求与设计映射表”。

用法：python3 refresh_toc.py <文档目录> <类型|all>

- 目录列出带编号的二级标题（“- N 标题”）与三级标题（“  - N.M 标题”）；没有目录节时插在“## 1 ”之前。
- 映射表章：从标题为“需求与设计映射表”的二级标题替换到文件末尾，没有就追加在最后；章号为前一章号加 1。
  行依次为：来源文档第 1 章按标题对应的几行（两边都有该标题才出这一行）；来源文档第 2 章起每个带编号的
  节和小节，以及没有带编号下级节的章，按同号找本文档的标题，找不到写“（缺少）”。
- 映射需要同目录下的来源文档（设计方案的来源是需求规格说明）。
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # 导入同目录模块时不在 skill 目录里生成 __pycache__
from doc_types import (
    DOC_TYPES, MAPPING_TITLE, collect_requirements, doc_path, is_numbered, numbered_headings, parse_headings,
    parse_kind_argument, read_lines, requirement_section,
)


def table_of_contents(lines):
    toc_lines = ['## 目录', '']
    for _, level, number, title in numbered_headings(lines):
        if level == 2:
            toc_lines.append(f'- {number} {title}')
        elif level == 3:
            toc_lines.append(f'  - {number} {title}')
    return toc_lines


def replace_toc(lines):
    toc_lines = table_of_contents(lines)
    start = next((i for i, line in enumerate(lines) if line == '## 目录'), None)
    if start is None:
        insert_at = next((i for i, line in enumerate(lines) if line.startswith('## 1 ')), len(lines))
        return lines[:insert_at] + toc_lines + [''] + lines[insert_at:]
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith('## ')), len(lines))
    return lines[:start] + toc_lines + [''] + lines[end:]


def mapping_rows(kind, source_lines, lines):
    source_heading_list = numbered_headings(source_lines)
    source_title = {h[2]: h[3] for h in source_heading_list}
    own_title = {h[2]: h[3] for h in numbered_headings(lines)}
    id_by_section = {}
    for requirement_id, _, _ in collect_requirements(source_lines):
        id_by_section.setdefault(requirement_section(requirement_id), []).append(requirement_id)

    def id_text(section):
        id_list = id_by_section.get(section, [])
        if not id_list:
            return '—'
        if len(id_list) == 1:
            return id_list[0]
        return f'{id_list[0]} 至 {id_list[-1]}（{len(id_list)} 条）'

    def number_of(title, heading_dict):
        return next((n for n, t in heading_dict.items() if n.startswith('1.') and t == title), None)

    row_list = []
    for source_name, own_name in DOC_TYPES[kind].get('chapter_one_map', ()):
        source_no, own_no = number_of(source_name, source_title), number_of(own_name, own_title)
        if source_no and own_no:
            row_list.append((source_no, own_no))
    chapter_list = [h for h in source_heading_list if h[1] == 2 and h[2] != '1']
    for chapter in chapter_list:
        child_list = [h for h in source_heading_list if h[2].startswith(chapter[2] + '.')]
        for heading in child_list or [chapter]:
            row_list.append((heading[2], heading[2]))
    table_lines = []
    for source_no, own_no in row_list:
        own_cell = f'{own_no} {own_title[own_no]}' if own_no in own_title else '（缺少）'
        table_lines.append(f'| {source_no} {source_title.get(source_no, "")} | {id_text(source_no)} | {own_cell} |')
    return table_lines


def replace_mapping(kind, doc_dir, lines):
    source_kind = DOC_TYPES[kind]['mapping_source']
    source_path = doc_path(doc_dir, source_kind)
    if not source_path.exists():
        print(f'重建映射表需要同目录下的{DOC_TYPES[source_kind]["file"]}')
        sys.exit(1)
    heading_list = parse_headings(lines)
    start = next((h[0] for h in heading_list if h[1] == 2 and h[3] == MAPPING_TITLE), None)
    body = lines if start is None else lines[:start]
    while body and not body[-1].strip():
        body = body[:-1]
    chapter_number_list = [int(h[2]) for h in parse_headings(body) if h[1] == 2 and is_numbered(h) and '.' not in h[2]]
    chapter = (max(chapter_number_list) if chapter_number_list else 0) + 1
    source_name = DOC_TYPES[source_kind]['name']
    own_name = DOC_TYPES[kind]['name']
    chapter_lines = [
        f'## {chapter} {MAPPING_TITLE}',
        '',
        f'本章以表 {chapter}-1 列出{source_name}各章节及其需求编号与本文档设计章节的对应关系。',
        '',
        f'表 {chapter}-1 {MAPPING_TITLE}',
        '',
        f'| {source_name}章节 | 需求编号 | {own_name}章节 |',
        '|---|---|---|',
    ] + mapping_rows(kind, read_lines(source_path), body)
    return body + [''] + chapter_lines


def refresh(kind, doc_dir):
    path = doc_path(doc_dir, kind)
    lines = read_lines(path)
    if DOC_TYPES[kind]['mapping']:
        lines = replace_mapping(kind, doc_dir, lines)
    lines = replace_toc(lines)
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    missing = sum(1 for line in lines if '（缺少）' in line)
    note = f'，映射表中 {missing} 行找不到对应章节' if missing else ''
    print(f'已刷新：{path}{note}')
    return missing


def main():
    if len(sys.argv) != 3:
        print('用法：python3 refresh_toc.py <文档目录> <类型|all>')
        sys.exit(2)
    doc_dir, kind = sys.argv[1], sys.argv[2]
    kind_list = parse_kind_argument(doc_dir, kind)
    if kind_list is None:
        print(f'未知类型 {kind}，可用：{"、".join(DOC_TYPES)}、all')
        sys.exit(2)
    missing_doc = [k for k in kind_list if not doc_path(doc_dir, k).exists()]
    if not kind_list or missing_doc:
        print(f'{doc_dir} 中没有要刷新的文档')
        sys.exit(1)
    missing = sum(refresh(item, doc_dir) for item in kind_list)
    sys.exit(1 if missing else 0)


if __name__ == '__main__':
    main()
