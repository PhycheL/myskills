#!/usr/bin/env python3
"""Save a public article URL or HTML file as local Markdown with images."""

from __future__ import annotations

import argparse
import datetime as dt
import html
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def fetch_text(url: str, timeout: int = 40) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def fetch_binary(url: str, referer: str | None = None, timeout: int = 40) -> tuple[bytes, str]:
    headers = {"User-Agent": USER_AGENT, "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"}
    if referer:
        headers["Referer"] = referer
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip()
        return response.read(), content_type


def first_match(patterns: list[str], text: str) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I | re.S)
        if match:
            return clean_text(match.group(1))
    return ""


def clean_text(value: str) -> str:
    value = html.unescape(value)
    value = re.sub(r"<[^>]+>", "", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def js_string(patterns: list[str], text: str) -> str:
    raw = first_match(patterns, text)
    return raw.replace("\\x26", "&").replace("\\/", "/").strip()


def extract_metadata(document: str, source_url: str) -> dict[str, str]:
    title = first_match(
        [
            r'<meta\s+property=["\']og:title["\']\s+content=["\']([^"\']+)["\']',
            r'<meta\s+name=["\']twitter:title["\']\s+content=["\']([^"\']+)["\']',
            r"var\s+msg_title\s*=\s*'([^']*)'",
            r"<title[^>]*>(.*?)</title>",
            r"<h1[^>]*>(.*?)</h1>",
        ],
        document,
    )
    author = first_match(
        [
            r'<meta\s+name=["\']author["\']\s+content=["\']([^"\']+)["\']',
            r'<meta\s+property=["\']og:article:author["\']\s+content=["\']([^"\']+)["\']',
            r'var\s+author\s*=\s*"([^"]*)"',
            r'var\s+nickname\s*=\s*htmlDecode\("([^"]*)"\)',
        ],
        document,
    )
    description = js_string(
        [
            r'<meta\s+property=["\']og:description["\']\s+content=["\']([^"\']+)["\']',
            r'<meta\s+name=["\']description["\']\s+content=["\']([^"\']+)["\']',
            r'var\s+msg_desc\s*=\s*htmlDecode\("([^"]*)"\)',
        ],
        document,
    )
    published_at = first_match(
        [
            r'<meta\s+property=["\']article:published_time["\']\s+content=["\']([^"\']+)["\']',
            r'<time[^>]+datetime=["\']([^"\']+)["\']',
            r'var\s+ct\s*=\s*"(\d{10})"',
            r'"publish_time"\s*:\s*(\d{10})',
            r"publish_time%22%3A(\d{10})",
        ],
        document,
    )
    if re.fullmatch(r"\d{10}", published_at):
        published_at = dt.datetime.fromtimestamp(int(published_at)).strftime("%Y-%m-%d %H:%M:%S %Z").strip()
    return {
        "title": title or "article",
        "author": author,
        "description": description,
        "published_at": published_at,
        "source": source_url,
        "saved_at": dt.date.today().isoformat(),
    }


def extract_article_html(document: str) -> str:
    document = document.replace("&nbsp;", " ")
    document = re.sub(r"\sdata-src=", " src=", document)
    selectors = [
        r'(<div[^>]+id=["\']js_content["\'][\s\S]*?</div>)',
        r'(<article\b[\s\S]*?</article>)',
        r'(<main\b[\s\S]*?</main>)',
    ]
    for pattern in selectors:
        match = re.search(pattern, document, flags=re.I)
        if match:
            return match.group(1)
    body = re.search(r"<body[^>]*>([\s\S]*?)</body>", document, flags=re.I)
    if body:
        return body.group(1)
    return document


def run_pandoc(article_html: str) -> str:
    if not shutil.which("pandoc"):
        raise SystemExit("pandoc is required for conversion but was not found in PATH")
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".html", delete=False) as temp:
        temp.write(article_html)
        temp_path = temp.name
    try:
        result = subprocess.run(
            ["pandoc", "-f", "html-native_divs-native_spans", "-t", "gfm-raw_html", "--wrap=none", temp_path],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    finally:
        os.unlink(temp_path)
    return result.stdout


def clean_markdown(markdown: str) -> str:
    markdown = re.sub(r"^\\\s*$", "", markdown, flags=re.M)
    markdown = re.sub(r"^-{10,}$", "---", markdown, flags=re.M)
    markdown = re.sub(r"(^```[^\n]*\n)\d+(?=\S)", r"\1", markdown, flags=re.M)
    markdown = re.sub(r"\n{3,}", "\n\n", markdown)
    return markdown.strip() + "\n"


def slugify(value: str) -> str:
    value = clean_text(value).lower()
    value = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value[:80] or "article"


def yaml_quote(value: str) -> str:
    value = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{value}"'


def frontmatter(meta: dict[str, str]) -> str:
    keys = ["title", "author", "description", "published_at", "source", "saved_at"]
    lines = ["---"]
    for key in keys:
        value = meta.get(key, "")
        if value:
            lines.append(f"{key}: {yaml_quote(value)}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def localize_images(markdown: str, output_dir: Path, source_url: str) -> str:
    assets_dir = output_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    image_pattern = re.compile(r"!\[([^\]]*)\]\((https?://[^)]+)\)")
    seen: dict[str, str] = {}

    def replace(match: re.Match[str]) -> str:
        alt, url = match.group(1), match.group(2)
        if url in seen:
            return f"![{alt}]({seen[url]})"
        try:
            data, content_type = fetch_binary(url, referer=source_url)
        except Exception as exc:
            print(f"warning: failed to download image {url}: {exc}", file=sys.stderr)
            return match.group(0)
        ext = mimetypes.guess_extension(content_type) or Path(urllib.parse.urlparse(url).path).suffix or ".jpg"
        if ext == ".jpe":
            ext = ".jpg"
        filename = f"image-{len(seen) + 1}{ext}"
        local_path = assets_dir / filename
        local_path.write_bytes(data)
        rel = f"assets/{filename}"
        seen[url] = rel
        return f"![{alt}]({rel})"

    return image_pattern.sub(replace, markdown)


def main() -> int:
    parser = argparse.ArgumentParser(description="Save an article URL or HTML file as local Markdown.")
    parser.add_argument("url", nargs="?", help="Article URL to fetch")
    parser.add_argument("--html", help="Path to an already downloaded/rendered HTML file")
    parser.add_argument("--source-url", default="", help="Original URL when using --html")
    parser.add_argument("--out-dir", default="output/articles", help="Base output directory")
    parser.add_argument("--no-images", action="store_true", help="Do not download images locally")
    args = parser.parse_args()

    if not args.url and not args.html:
        parser.error("provide a URL or --html")
    if args.url and args.html:
        parser.error("provide either URL or --html, not both")

    source_url = args.source_url or args.url or ""
    if args.html:
        document = Path(args.html).read_text(encoding="utf-8", errors="replace")
    else:
        document = fetch_text(args.url)

    meta = extract_metadata(document, source_url)
    article_html = extract_article_html(document)
    markdown = clean_markdown(run_pandoc(article_html))

    slug = slugify(meta["title"])
    article_dir = Path(args.out_dir) / slug
    article_dir.mkdir(parents=True, exist_ok=True)
    if not args.no_images:
        markdown = localize_images(markdown, article_dir, source_url)

    output_file = article_dir / f"{slug}.md"
    output_file.write_text(frontmatter(meta) + markdown, encoding="utf-8")
    print(output_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
