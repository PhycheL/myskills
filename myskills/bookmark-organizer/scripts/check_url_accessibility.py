"""
检查书签 HTML 文件中所有 URL 的可访问性，将无法访问的链接输出到 Markdown 报告。

用法:
    python check_url_accessibility.py <书签HTML文件> [输出md文件] [--timeout 秒数] [--workers 并发数]

示例:
    python check_url_accessibility.py "精致素材_去重.html"
    python check_url_accessibility.py "精致素材_去重.html" report.md --timeout 15 --workers 20
"""

import sys
import os
import re
import time
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

try:
    import requests
except ImportError:
    print("错误: 需要 requests 库。请运行: pip install requests")
    sys.exit(1)


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


def check_url(url, title, timeout=10):
    """
    检查单个 URL 的可访问性。

    返回:
        (url, title, is_accessible, status_code, error_message)
    """
    # 跳过非 HTTP(S) 协议的 URL（如 javascript:, data:, file: 等）
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        return (url, title, True, None, f"跳过非HTTP协议: {parsed.scheme}")

    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                       'AppleWebKit/537.36 (KHTML, like Gecko) '
                       'Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    }

    try:
        # 先尝试 HEAD 请求（更快）
        resp = requests.head(url, headers=headers, timeout=timeout,
                             allow_redirects=True)
        # 某些服务器不支持 HEAD，如果返回 405 则改用 GET
        if resp.status_code == 405:
            resp = requests.get(url, headers=headers, timeout=timeout,
                                allow_redirects=True, stream=True)
            resp.close()

        if resp.status_code < 400:
            return (url, title, True, resp.status_code, None)
        else:
            return (url, title, False, resp.status_code,
                    f"HTTP {resp.status_code}")
    except requests.exceptions.Timeout:
        return (url, title, False, None, "连接超时")
    except requests.exceptions.TooManyRedirects:
        return (url, title, False, None, "重定向次数过多")
    except requests.exceptions.ConnectionError:
        return (url, title, False, None, "连接失败")
    except requests.exceptions.SSLError:
        return (url, title, False, None, "SSL证书错误")
    except requests.exceptions.RequestException as e:
        return (url, title, False, None, str(e)[:100])


def build_markdown(input_file, total, accessible, inaccessible, skipped, results):
    """生成 Markdown 格式的可访问性检查报告。"""
    lines = []
    lines.append('# 书签链接可访问性检查报告\n')
    lines.append(f'- **源文件**: `{input_file}`')
    lines.append(f'- **书签总数**: {total}')
    lines.append(f'- **可访问**: {accessible}')
    lines.append(f'- **无法访问**: {inaccessible}')
    lines.append(f'- **跳过（非HTTP）**: {skipped}')
    lines.append('')

    # 筛选出无法访问的结果
    dead_results = [(url, title, status, err)
                    for url, title, ok, status, err in results if not ok]

    if not dead_results:
        lines.append('> 所有链接均可正常访问，无需处理。\n')
        return '\n'.join(lines)

    lines.append('## 无法访问的链接\n')
    lines.append('以下链接无法正常访问，建议检查后决定是否删除：\n')
    lines.append('| # | 标题 | URL | 原因 |')
    lines.append('|---|------|-----|------|')

    for i, (url, title, status, err) in enumerate(dead_results, 1):
        safe_title = title.replace('|', '\\|')
        safe_url = url.replace('|', '%7C')
        safe_err = (err or '未知错误').replace('|', '\\|')
        lines.append(f'| {i} | {safe_title} | `{safe_url}` | {safe_err} |')

    lines.append('')
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(
        description='检查书签 HTML 中所有 URL 的可访问性')
    parser.add_argument('input_file', help='书签 HTML 文件路径')
    parser.add_argument('output_file', nargs='?', default=None,
                        help='输出的 Markdown 报告路径（可选）')
    parser.add_argument('--timeout', type=int, default=10,
                        help='每个 URL 的请求超时秒数（默认 10）')
    parser.add_argument('--workers', type=int, default=10,
                        help='并发检查线程数（默认 10）')
    args = parser.parse_args()

    input_file = args.input_file
    if args.output_file:
        output_file = args.output_file
    else:
        base = os.path.splitext(os.path.basename(input_file))[0]
        output_file = f'{base}_失效检查.md'

    with open(input_file, 'r', encoding='utf-8') as f:
        content = f.read()

    bookmarks = extract_bookmarks(content)
    total = len(bookmarks)
    print(f'共发现 {total} 个书签，开始检查可访问性...')

    results = []
    checked = 0
    start_time = time.time()

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(check_url, url, title, args.timeout): (url, title)
            for url, title in bookmarks
        }
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            checked += 1
            url, title, ok, status, err = result
            status_str = '✓' if ok else '✗'
            if checked % 10 == 0 or checked == total:
                elapsed = time.time() - start_time
                print(f'  进度: {checked}/{total} '
                      f'({checked*100//total}%) - 已用时 {elapsed:.1f}s')

    # 统计
    accessible = sum(1 for _, _, ok, _, _ in results if ok)
    skipped = sum(1 for _, _, ok, _, err in results
                  if ok and err and err.startswith('跳过'))
    accessible -= skipped
    inaccessible = total - accessible - skipped

    elapsed = time.time() - start_time
    print(f'\n检查完成（用时 {elapsed:.1f}s）:')
    print(f'  可访问: {accessible}')
    print(f'  无法访问: {inaccessible}')
    print(f'  跳过: {skipped}')

    md_content = build_markdown(input_file, total, accessible, inaccessible,
                                skipped, results)
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(md_content)

    print(f'报告已保存到: {output_file}')


if __name__ == '__main__':
    main()
