"""
根据重复检查报告，从书签 HTML 中删除重复书签，保留标题更完整的那条。

用法:
    python remove_duplicate_bookmarks.py <书签HTML文件> <重复检查报告.md> [输出HTML] [输出报告.md]

示例:
    python remove_duplicate_bookmarks.py "精致素材_bookmarks.html" "精致素材_bookmarks_重复检查.md"
"""

import sys
import os
import re
from urllib.parse import urlparse, urlunparse


def normalize_url(url):
    """归一化 URL（与 check_duplicate_urls.py 保持一致）。"""
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip('/')
    return urlunparse((scheme, netloc, path, parsed.params, parsed.query, ''))


def parse_report(report_content):
    """从重复检查报告中提取所有重复的归一化 URL 集合。"""
    pattern = re.compile(r'^\*\*URL\*\*:\s*`(.+?)`', re.MULTILINE)
    return set(match.group(1) for match in pattern.finditer(report_content))


def process_html(html_lines, dup_urls):
    """
    处理 HTML 行列表，去除重复书签。

    返回:
        clean_lines: 去重后的行列表
        report_entries: [(归一化URL, 保留标题, [删除标题, ...])]
    """
    bookmark_pattern = re.compile(
        r'<A\s+HREF="([^"]+)"[^>]*>(.*?)</A>',
        re.IGNORECASE | re.DOTALL
    )

    # 第一遍：收集每个重复 URL 对应的所有行号及其标题
    # {normalized_url: [(line_index, original_url, title), ...]}
    dup_groups = {}
    for i, line in enumerate(html_lines):
        match = bookmark_pattern.search(line)
        if not match:
            continue
        url = match.group(1)
        norm = normalize_url(url)
        if norm in dup_urls:
            title = re.sub(r'<[^>]+>', '', match.group(2)).strip()
            dup_groups.setdefault(norm, []).append((i, url, title))

    # 第二遍：对每组决定保留哪一条（标题最长的），标记其余行号待删除
    lines_to_remove = set()
    report_entries = []

    for norm_url, entries in dup_groups.items():
        if len(entries) < 2:
            continue

        # 选标题最长的保留；长度相同时保留第一条
        best_idx = 0
        best_len = len(entries[0][2])
        for j, (_, _, title) in enumerate(entries):
            if len(title) > best_len:
                best_len = len(title)
                best_idx = j

        kept = entries[best_idx]
        removed = []
        for j, entry in enumerate(entries):
            if j != best_idx:
                lines_to_remove.add(entry[0])
                removed.append(entry[2])

        report_entries.append((norm_url, kept[2], removed))

    # 生成清理后的行
    clean_lines = [line for i, line in enumerate(html_lines) if i not in lines_to_remove]

    return clean_lines, report_entries


def build_report(input_html, report_md, output_html, total_before, total_after, report_entries):
    """生成去重操作报告的 Markdown 内容。"""
    lines = []
    lines.append('# 书签去重操作报告\n')
    lines.append(f'- **源书签文件**: `{input_html}`')
    lines.append(f'- **重复检查报告**: `{report_md}`')
    lines.append(f'- **输出文件**: `{output_html}`')
    lines.append(f'- **去重前书签数**: {total_before}')
    lines.append(f'- **去重后书签数**: {total_after}')
    lines.append(f'- **删除书签数**: {total_before - total_after}')
    lines.append(f'- **处理组数**: {len(report_entries)}')
    lines.append('')

    if not report_entries:
        lines.append('> 无需删除，未发现重复。\n')
        return '\n'.join(lines)

    lines.append('## 处理详情\n')
    lines.append('| # | URL | 保留标题 | 删除条数 |')
    lines.append('|---|-----|----------|----------|')

    for i, (norm_url, kept_title, removed_titles) in enumerate(report_entries, 1):
        safe_title = kept_title.replace('|', '\\|')
        safe_url = norm_url.replace('|', '%7C')
        lines.append(f'| {i} | `{safe_url}` | {safe_title} | {len(removed_titles)} |')

    lines.append('')
    lines.append('## 删除明细\n')

    for i, (norm_url, kept_title, removed_titles) in enumerate(report_entries, 1):
        safe_kept = kept_title.replace('|', '\\|')
        lines.append(f'### 第 {i} 组\n')
        lines.append(f'**URL**: `{norm_url}`\n')
        lines.append(f'- **保留**: {safe_kept}')
        for rt in removed_titles:
            safe_rt = rt.replace('|', '\\|')
            lines.append(f'- **删除**: {safe_rt}')
        lines.append('')

    return '\n'.join(lines)


def count_bookmarks(html_lines):
    """统计 HTML 中书签 <A> 标签数量。"""
    pattern = re.compile(r'<A\s+HREF="[^"]+"', re.IGNORECASE)
    return sum(1 for line in html_lines if pattern.search(line))


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    input_html = sys.argv[1]
    report_md = sys.argv[2]

    base = os.path.splitext(os.path.basename(input_html))[0]
    output_html = sys.argv[3] if len(sys.argv) >= 4 else f'{base}_去重.html'
    output_report = sys.argv[4] if len(sys.argv) >= 5 else f'{base}_去重报告.md'

    # 读取重复检查报告
    with open(report_md, 'r', encoding='utf-8') as f:
        report_content = f.read()
    dup_urls = parse_report(report_content)

    if not dup_urls:
        print('报告中未找到重复 URL，无需处理。')
        return

    # 读取书签 HTML
    with open(input_html, 'r', encoding='utf-8') as f:
        html_lines = f.read().splitlines()

    total_before = count_bookmarks(html_lines)

    # 去重处理
    clean_lines, report_entries = process_html(html_lines, dup_urls)

    total_after = count_bookmarks(clean_lines)

    # 写入去重后的 HTML
    with open(output_html, 'w', encoding='utf-8') as f:
        f.write('\n'.join(clean_lines) + '\n')

    # 写入去重报告
    md = build_report(input_html, report_md, output_html, total_before, total_after, report_entries)
    with open(output_report, 'w', encoding='utf-8') as f:
        f.write(md)

    removed = total_before - total_after
    print(f'去重完成：{total_before} → {total_after}（删除 {removed} 条）')
    print(f'去重后书签: {output_html}')
    print(f'操作报告:   {output_report}')


if __name__ == '__main__':
    main()
