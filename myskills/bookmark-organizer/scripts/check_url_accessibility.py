"""
检查书签 HTML 文件中所有 URL 的可访问性，将无法访问的链接输出到 Markdown 报告。

分层检测策略：
  第一轮：requests 并发检测（快速批量）
  第二轮：对失败 URL 使用 Playwright headed Chrome 重试（真实浏览器）

用法:
    python check_url_accessibility.py <书签HTML文件> [输出md文件] [--timeout 秒数] [--workers 并发数] [--skip-browser]

示例:
    python check_url_accessibility.py "精致素材_去重.html"
    python check_url_accessibility.py "精致素材_去重.html" report.md --timeout 15 --workers 20
    python check_url_accessibility.py "精致素材_去重.html" --skip-browser
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


# Cloudflare 等挑战页的 title 关键词
CHALLENGE_TITLES = [
    "just a moment",
    "请稍候",
    "attention required",
    "checking your browser",
    "one more step",
    "verify you are human",
    "access denied",
]

# 真正的不可达错误原因关键词
TRULY_DEAD_REASONS = [
    "连接失败", "连接超时", "重定向次数过多", "SSL证书错误",
    "HTTP 404", "HTTP 410", "HTTP 500", "HTTP 502", "HTTP 503", "HTTP 504",
]


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
    第一轮：用 requests 检查单个 URL 的可访问性。

    返回:
        (url, title, is_accessible, status_code, error_message)
    """
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        return (url, title, True, None, f"跳过非HTTP协议: {parsed.scheme}")

    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                       'AppleWebKit/537.36 (KHTML, like Gecko) '
                       'Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,'
                  'image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Accept-Encoding': 'gzip, deflate, br',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Sec-CH-UA': '"Not_A Brand";v="8", "Chromium";v="120", '
                     '"Google Chrome";v="120"',
        'Sec-CH-UA-Mobile': '?0',
        'Sec-CH-UA-Platform': '"macOS"',
        'Upgrade-Insecure-Requests': '1',
        'Cache-Control': 'max-age=0',
    }

    try:
        session = requests.Session()
        # 先尝试 HEAD 请求
        resp = session.head(url, headers=headers, timeout=timeout,
                            allow_redirects=True)
        # HEAD 返回 >= 400 时一律回退到 GET
        if resp.status_code >= 400:
            resp = session.get(url, headers=headers, timeout=timeout,
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


def check_urls_with_browser(failed_results, timeout=30):
    """
    第二轮：用 Playwright headed Chrome 逐个重试失败的 URL。

    返回:
        truly_dead: [(url, title, status, error), ...]  — 确认无法访问
        anti_crawl:  [(url, title, status, error), ...]  — 被反爬拦截（可能有效）
        recovered:   [(url, title), ...]                  — 实际可以访问
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("\n⚠ 未安装 playwright，跳过浏览器检测。")
        print("  安装方式: pip install playwright && python -m playwright install chromium")
        # 全部归为 truly_dead
        truly_dead = [(url, title, status, err)
                      for url, title, _, status, err in failed_results]
        return truly_dead, [], []

    truly_dead = []
    anti_crawl = []
    recovered = []

    total = len(failed_results)
    print(f"\n第二轮：使用浏览器重试 {total} 个失败 URL...")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            channel='chrome',
            args=['--disable-blink-features=AutomationControlled'],
        )
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                       'AppleWebKit/537.36 (KHTML, like Gecko) '
                       'Chrome/120.0.0.0 Safari/537.36',
            viewport={'width': 1280, 'height': 720},
            locale='zh-CN',
        )

        for i, (url, title, _, status, err) in enumerate(failed_results, 1):
            print(f"  [{i}/{total}] {url[:80]}...", end=" ", flush=True)
            page = context.new_page()
            try:
                resp = page.goto(url, wait_until='domcontentloaded',
                                 timeout=timeout * 1000)
                page_title = (page.title() or "").lower()

                # 检查是否是 Cloudflare 等挑战页
                is_challenge = any(kw in page_title for kw in CHALLENGE_TITLES)

                if is_challenge:
                    # 等待 JS Challenge 完成
                    print("挑战页，等待5s...", end=" ", flush=True)
                    page.wait_for_timeout(5000)
                    page_title = (page.title() or "").lower()
                    is_challenge = any(kw in page_title
                                       for kw in CHALLENGE_TITLES)

                resp_status = resp.status if resp else None

                if not is_challenge and (resp_status is None or resp_status < 400):
                    # 页面正常加载
                    print("✓ 可访问")
                    recovered.append((url, title))
                elif is_challenge or resp_status in (403, 429):
                    # 反爬拦截
                    reason = "反爬拦截（Cloudflare/WAF）" if is_challenge else f"HTTP {resp_status}（可能反爬）"
                    print(f"⚠ {reason}")
                    anti_crawl.append((url, title, resp_status, reason))
                else:
                    # 真正不可达
                    reason = f"HTTP {resp_status}" if resp_status else (err or "页面加载失败")
                    print(f"✗ {reason}")
                    truly_dead.append((url, title, resp_status, reason))

            except Exception as e:
                err_msg = str(e)[:80]
                if 'timeout' in err_msg.lower() or 'Timeout' in err_msg:
                    print(f"✗ 浏览器超时")
                    truly_dead.append((url, title, None, "浏览器访问超时"))
                elif 'net::ERR_' in err_msg:
                    print(f"✗ {err_msg}")
                    truly_dead.append((url, title, None, err_msg))
                else:
                    print(f"✗ {err_msg}")
                    truly_dead.append((url, title, None, err_msg))
            finally:
                page.close()

        browser.close()

    return truly_dead, anti_crawl, recovered


def build_markdown(input_file, total, accessible, inaccessible, skipped,
                   truly_dead, anti_crawl, browser_used):
    """生成 Markdown 格式的可访问性检查报告（分两个表格）。"""
    lines = []
    lines.append('# 书签链接可访问性检查报告\n')
    lines.append(f'- **源文件**: `{input_file}`')
    lines.append(f'- **书签总数**: {total}')
    lines.append(f'- **可访问**: {accessible}')
    lines.append(f'- **确认无法访问**: {len(truly_dead)}')
    if browser_used:
        lines.append(f'- **反爬拦截（可能有效）**: {len(anti_crawl)}')
    lines.append(f'- **跳过（非HTTP）**: {skipped}')
    lines.append('')

    if not truly_dead and not anti_crawl:
        lines.append('> 所有链接均可正常访问，无需处理。\n')
        return '\n'.join(lines)

    # 表格1：确认无法访问
    if truly_dead:
        lines.append('## 确认无法访问的链接\n')
        lines.append('以下链接确认无法访问（404、连接失败等），建议删除：\n')
        lines.append('| # | 标题 | URL | 原因 |')
        lines.append('|---|------|-----|------|')
        for i, (url, title, status, err) in enumerate(truly_dead, 1):
            safe_title = title.replace('|', '\\|')
            safe_url = url.replace('|', '%7C')
            safe_err = (err or '未知错误').replace('|', '\\|')
            lines.append(f'| {i} | {safe_title} | {safe_url} | {safe_err} |')
        lines.append('')

    # 表格2：反爬拦截
    if anti_crawl:
        lines.append('## 被反爬机制拦截的链接\n')
        lines.append('以下链接被 Cloudflare/WAF 等反爬机制拦截，网站可能仍然有效，建议手动确认：\n')
        lines.append('| # | 标题 | URL | 原因 |')
        lines.append('|---|------|-----|------|')
        for i, (url, title, status, err) in enumerate(anti_crawl, 1):
            safe_title = title.replace('|', '\\|')
            safe_url = url.replace('|', '%7C')
            safe_err = (err or '未知错误').replace('|', '\\|')
            lines.append(f'| {i} | {safe_title} | {safe_url} | {safe_err} |')
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
    parser.add_argument('--skip-browser', action='store_true',
                        help='跳过 Playwright 浏览器检测，仅用 requests')
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

    # ── 第一轮：requests 并发检测 ──
    print(f'\n第一轮：requests 并发检测...')
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
            if checked % 10 == 0 or checked == total:
                elapsed = time.time() - start_time
                print(f'  进度: {checked}/{total} '
                      f'({checked*100//total}%) - 已用时 {elapsed:.1f}s')

    # 统计第一轮结果
    accessible_results = [r for r in results if r[2]]
    failed_results = [r for r in results if not r[2]]
    skipped_results = [r for r in accessible_results
                       if r[4] and r[4].startswith('跳过')]

    accessible_count = len(accessible_results) - len(skipped_results)
    skipped_count = len(skipped_results)

    elapsed = time.time() - start_time
    print(f'\n第一轮完成（用时 {elapsed:.1f}s）:')
    print(f'  可访问: {accessible_count}')
    print(f'  失败: {len(failed_results)}')
    print(f'  跳过: {skipped_count}')

    # ── 第二轮：Playwright 浏览器检测 ──
    browser_used = False
    truly_dead = []
    anti_crawl = []

    if failed_results and not args.skip_browser:
        browser_used = True
        truly_dead, anti_crawl, recovered = check_urls_with_browser(
            failed_results, timeout=args.timeout * 3)

        # 如果 playwright 未安装导致全部归入 truly_dead 且没有 recovered
        if not recovered and not anti_crawl and truly_dead:
            # 检查是否实际运行了浏览器（如果 truly_dead 数量等于 failed_results 说明可能没有）
            pass

        accessible_count += len(recovered)

        print(f'\n第二轮完成:')
        print(f'  恢复为可访问: {len(recovered)}')
        print(f'  确认无法访问: {len(truly_dead)}')
        print(f'  反爬拦截: {len(anti_crawl)}')
    elif failed_results:
        # --skip-browser 模式：所有失败都归为 truly_dead
        truly_dead = [(url, title, status, err)
                      for url, title, _, status, err in failed_results]

    inaccessible_count = len(truly_dead) + len(anti_crawl)

    print(f'\n最终结果:')
    print(f'  可访问: {accessible_count}')
    print(f'  确认无法访问: {len(truly_dead)}')
    if browser_used:
        print(f'  反爬拦截（可能有效）: {len(anti_crawl)}')
    print(f'  跳过: {skipped_count}')

    md_content = build_markdown(input_file, total, accessible_count,
                                inaccessible_count, skipped_count,
                                truly_dead, anti_crawl, browser_used)
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(md_content)

    print(f'\n报告已保存到: {output_file}')


if __name__ == '__main__':
    main()
