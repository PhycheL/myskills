"""
从 Chrome 导出的书签 HTML 中，按文件夹名称提取指定分类（含所有子级）并保存为新的书签 HTML 文件。

用法:
    python extract_bookmarks.py <输入文件> <分类名称> [输出文件]

示例:
    python extract_bookmarks.py "bookmarks_2026_2_27 ori.html" "Research"
    python extract_bookmarks.py "bookmarks_2026_2_27 ori.html" "常看期刊" output.html
"""

import sys
import re


def find_folder(lines, folder_name):
    """
    在书签 HTML 的行列表中查找指定名称的文件夹，
    返回该文件夹 <DT><H3>...</H3> 行及其对应 <DL><p>...</DL><p> 块的所有行。
    """
    # 找到文件夹标题所在行
    folder_start = None
    for i, line in enumerate(lines):
        # 匹配 <H3 ...>文件夹名</H3>
        match = re.search(r'<H3[^>]*>(.*?)</H3>', line, re.IGNORECASE)
        if match and match.group(1).strip() == folder_name:
            folder_start = i
            break

    if folder_start is None:
        return None

    # 从文件夹标题行开始，找到紧随其后的 <DL><p> 作为内容起始
    dl_start = None
    for i in range(folder_start + 1, len(lines)):
        stripped = lines[i].strip()
        if stripped == '':
            continue
        if '<DL>' in stripped.upper():
            dl_start = i
            break
        else:
            # 没有紧跟 <DL>，说明不是文件夹
            return None

    if dl_start is None:
        return None

    # 用计数器匹配 <DL> / </DL> 找到结束位置
    depth = 0
    dl_end = None
    for i in range(dl_start, len(lines)):
        line_upper = lines[i].upper()
        # 计算本行中 <DL> 和 </DL> 的数量
        depth += line_upper.count('<DL>') - line_upper.count('</DL>')
        if depth <= 0:
            dl_end = i
            break

    if dl_end is None:
        dl_end = len(lines) - 1

    return lines[folder_start: dl_end + 1]


def dedent_lines(lines):
    """去掉公共前导空白，保持相对缩进。"""
    if not lines:
        return lines
    # 找到最小非空行的前导空白数
    min_indent = float('inf')
    for line in lines:
        stripped = line.lstrip()
        if stripped:
            indent = len(line) - len(stripped)
            min_indent = min(min_indent, indent)
    if min_indent == float('inf'):
        min_indent = 0
    return [line[min_indent:] for line in lines]


def build_output(folder_lines):
    """将提取的文件夹内容包装为完整的 Netscape 书签 HTML。"""
    header = (
        '<!DOCTYPE NETSCAPE-Bookmark-file-1>\n'
        '<!-- This is an automatically generated file.\n'
        '     It will be read and overwritten.\n'
        '     DO NOT EDIT! -->\n'
        '<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">\n'
        '<TITLE>Bookmarks</TITLE>\n'
        '<H1>Bookmarks</H1>\n'
        '<DL><p>\n'
    )
    footer = '</DL><p>\n'

    # 去掉公共缩进后，添加统一的 4 空格缩进
    dedented = dedent_lines(folder_lines)
    body = '\n'.join('    ' + line for line in dedented) + '\n'

    return header + body + footer


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    input_file = sys.argv[1]
    folder_name = sys.argv[2]
    output_file = sys.argv[3] if len(sys.argv) > 3 else None

    with open(input_file, 'r', encoding='utf-8') as f:
        lines = f.read().splitlines()

    folder_lines = find_folder(lines, folder_name)

    if folder_lines is None:
        print(f'错误: 未找到名为 "{folder_name}" 的文件夹/分类。')
        sys.exit(1)

    result = build_output(folder_lines)

    if output_file is None:
        output_file = f'{folder_name}_bookmarks.html'

    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(result)

    print(f'已提取分类 "{folder_name}" 到文件: {output_file}')


if __name__ == '__main__':
    main()
