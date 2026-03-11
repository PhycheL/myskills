"""
根据失效检查报告，从书签 HTML 中删除无法访问的书签。

用法:
    python remove_dead_bookmarks.py <书签HTML文件> <失效检查报告.md> [输出HTML] [输出报告.md]

示例:
    python remove_dead_bookmarks.py "精致素材_去重.html" "精致素材_去重_失效检查.md"
"""

import sys
import os
import re
from urllib.parse import urlparse, urlunparse


def normalize_url(url):
    """归一化 URL（与其他脚本保持一致）。"""
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip('/')
    return urlunparse((scheme, netloc, path, parsed.params, parsed.query, ''))


def parse_report(report_content):
    """
    从失效检查报告中提取所有无法访问的 URL。

    报告格式为 Markdown 表格，URL 在第三列（不加反引号）。
    支持两个表格（"确认无法访问"和"反爬拦截"）。
    """
    dead_urls = set()
    # 匹配表格行中的 URL：| # | 标题 | URL | 原因 |
    pattern = re.compile(
        r'^\|\s*\d+\s*\|[^|]*\|\s*(https?://\S+?)\s*\|',
        re.MULTILINE
    )
    for match in pattern.finditer(report_content):
        url = match.group(1).replace('%7C', '|')
        dead_urls.add(url)
    return dead_urls


def process_html(html_lines, dead_urls):
    """
    处理 HTML 行列表，删除无法访问的书签。

    返回:
        clean_lines: 清理后的行列表
        removed_entries: [(url, title), ...]
    """
    bookmark_pattern = re.compile(
        r'<A\s+HREF="([^"]+)"[^>]*>(.*?)</A>',
        re.IGNORECASE | re.DOTALL
    )

    lines_to_remove = set()
    removed_entries = []

    for i, line in enumerate(html_lines):
        match = bookmark_pattern.search(line)
        if not match:
            continue
        url = match.group(1)
        if url in dead_urls:
            title = re.sub(r'<[^>]+>', '', match.group(2)).strip()
            lines_to_remove.add(i)
            removed_entries.append((url, title))

    clean_lines = [line for i, line in enumerate(html_lines)
                   if i not in lines_to_remove]
    return clean_lines, removed_entries


def count_bookmarks(html_lines):
    """统计 HTML 中书签 <A> 标签数量。"""
    pattern = re.compile(r'<A\s+HREF="[^"]+"', re.IGNORECASE)
    return sum(1 for line in html_lines if pattern.search(line))


def build_report(input_html, report_md, output_html,
                 total_before, total_after, removed_entries):
    """生成清理操作报告的 Markdown 内容。"""
    lines = []
    lines.append('# 失效书签清理报告\n')
    lines.append(f'- **源书签文件**: `{input_html}`')
    lines.append(f'- **失效检查报告**: `{report_md}`')
    lines.append(f'- **输出文件**: `{output_html}`')
    lines.append(f'- **清理前书签数**: {total_before}')
    lines.append(f'- **清理后书签数**: {total_after}')
    lines.append(f'- **删除书签数**: {len(removed_entries)}')
    lines.append('')

    if not removed_entries:
        lines.append('> 无需删除，所有链接均可访问。\n')
        return '\n'.join(lines)

    lines.append('## 已删除的失效书签\n')
    lines.append('| # | 标题 | URL |')
    lines.append('|---|------|-----|')

    for i, (url, title) in enumerate(removed_entries, 1):
        safe_title = title.replace('|', '\\|')
        safe_url = url.replace('|', '%7C')
        lines.append(f'| {i} | {safe_title} | `{safe_url}` |')

    lines.append('')
    return '\n'.join(lines)


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    input_html = sys.argv[1]
    report_md = sys.argv[2]

    base = os.path.splitext(os.path.basename(input_html))[0]
    output_html = sys.argv[3] if len(sys.argv) >= 4 else f'{base}_清理.html'
    output_report = sys.argv[4] if len(sys.argv) >= 5 else f'{base}_失效清理报告.md'

    # 读取失效检查报告
    with open(report_md, 'r', encoding='utf-8') as f:
        report_content = f.read()
    dead_urls = parse_report(report_content)

    if not dead_urls:
        print('报告中未找到失效 URL，无需处理。')
        return

    print(f'从报告中提取到 {len(dead_urls)} 个失效 URL')

    # 读取书签 HTML
    with open(input_html, 'r', encoding='utf-8') as f:
        html_lines = f.read().splitlines()

    total_before = count_bookmarks(html_lines)

    # 清理处理
    clean_lines, removed_entries = process_html(html_lines, dead_urls)

    total_after = count_bookmarks(clean_lines)

    # 写入清理后的 HTML
    with open(output_html, 'w', encoding='utf-8') as f:
        f.write('\n'.join(clean_lines) + '\n')

    # 写入清理报告
    md = build_report(input_html, report_md, output_html,
                      total_before, total_after, removed_entries)
    with open(output_report, 'w', encoding='utf-8') as f:
        f.write(md)

    print(f'清理完成：{total_before} → {total_after}'
          f'（删除 {len(removed_entries)} 条失效书签）')
    print(f'清理后书签: {output_html}')
    print(f'操作报告:   {output_report}')


if __name__ == '__main__':
    main()
