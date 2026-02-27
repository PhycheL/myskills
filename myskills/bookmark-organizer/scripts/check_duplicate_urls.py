"""
检查书签 HTML 文件中是否存在重复的 URL，结果输出到 Markdown 文件。

用法:
    python check_duplicate_urls.py <书签HTML文件> [输出md文件]

示例:
    python check_duplicate_urls.py "精致素材_bookmarks.html"
    python check_duplicate_urls.py "精致素材_bookmarks.html" report.md
"""

import sys
import os
import re
from urllib.parse import urlparse, urlunparse


def normalize_url(url):
    """
    对 URL 做归一化处理，以便更准确地判断重复:
    - 统一转小写 scheme 和 host
    - 去掉末尾的 /
    - 去掉 fragment
    """
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip('/')
    return urlunparse((scheme, netloc, path, parsed.params, parsed.query, ''))


def extract_bookmarks(html_content):
    """从书签 HTML 中提取所有 <A> 标签的 HREF 和标题。"""
    pattern = re.compile(
        r'<A\s+HREF="([^"]+)"[^>]*>(.*?)</A>',
        re.IGNORECASE | re.DOTALL
    )
    bookmarks = []
    for match in pattern.finditer(html_content):
        url = match.group(1)
        title = re.sub(r'<[^>]+>', '', match.group(2)).strip()
        bookmarks.append((url, title))
    return bookmarks


def find_duplicates(bookmarks):
    """查找重复的 URL，返回 {normalized_url: [(原始url, title), ...]}。"""
    grouped = {}
    for url, title in bookmarks:
        key = normalize_url(url)
        grouped.setdefault(key, []).append((url, title))

    return {k: v for k, v in grouped.items() if len(v) > 1}


def build_markdown(input_file, bookmarks, duplicates):
    """生成 Markdown 格式的报告。"""
    lines = []
    lines.append(f'# 书签重复 URL 检查报告\n')
    lines.append(f'- **源文件**: `{input_file}`')
    lines.append(f'- **书签总数**: {len(bookmarks)}')

    if not duplicates:
        lines.append(f'\n> 未发现重复的 URL。\n')
        return '\n'.join(lines)

    total_dup = sum(len(v) - 1 for v in duplicates.values())
    lines.append(f'- **重复组数**: {len(duplicates)}')
    lines.append(f'- **可删除的重复书签数**: {total_dup}')
    lines.append('')

    lines.append('## 重复详情\n')

    for i, (norm_url, entries) in enumerate(duplicates.items(), 1):
        lines.append(f'### 第 {i} 组（出现 {len(entries)} 次）\n')
        lines.append(f'**URL**: `{norm_url}`\n')
        lines.append('| # | 标题 | 原始 URL |')
        lines.append('|---|------|----------|')
        for j, (url, title) in enumerate(entries, 1):
            # 转义 Markdown 中的 | 字符
            safe_title = title.replace('|', '\\|')
            safe_url = url.replace('|', '%7C')
            lines.append(f'| {j} | {safe_title} | `{safe_url}` |')
        lines.append('')

    return '\n'.join(lines)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    input_file = sys.argv[1]
    if len(sys.argv) >= 3:
        output_file = sys.argv[2]
    else:
        base = os.path.splitext(os.path.basename(input_file))[0]
        output_file = f'{base}_重复检查.md'

    with open(input_file, 'r', encoding='utf-8') as f:
        content = f.read()

    bookmarks = extract_bookmarks(content)
    duplicates = find_duplicates(bookmarks)
    md_content = build_markdown(input_file, bookmarks, duplicates)

    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(md_content)

    # 控制台简要提示
    if not duplicates:
        print(f'共 {len(bookmarks)} 个书签，未发现重复。')
    else:
        total_dup = sum(len(v) - 1 for v in duplicates.values())
        print(f'共 {len(bookmarks)} 个书签，发现 {len(duplicates)} 组重复（{total_dup} 个可删除）。')
    print(f'报告已保存到: {output_file}')


if __name__ == '__main__':
    main()
