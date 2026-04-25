#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import html
import json
import mimetypes
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, parse_qs, urljoin, urlparse, urlsplit, urlunsplit
from urllib.request import Request, urlopen


CLIPPER_BASE = "https://clipper.yinxiang.com"
IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.I)
ATTR_RE = re.compile(r"([\w:-]+)\s*=\s*(\"([^\"]*)\"|'([^']*)'|([^\s\"'>/]+))")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert Yinxiang Clipper collector JSON to Markdown.")
    parser.add_argument("--input", type=Path, required=True, help="Captured JSON from the web app API")
    parser.add_argument("--output-dir", type=Path, required=True, help="Directory where Markdown files are written")
    parser.add_argument("--limit", type=int, default=0, help="Optional maximum number of items to convert")
    parser.add_argument(
        "--keep-remote-images",
        action="store_true",
        help="Do not fail conversion when an image cannot be downloaded; keep the remote URL in Markdown",
    )
    return parser.parse_args()


def ensure_pandoc() -> None:
    if shutil.which("pandoc") is None:
        raise RuntimeError("pandoc is required but was not found in PATH")


def sanitize_component(text: str | None, limit: int = 64) -> str:
    text = re.sub(r"\s+", "_", (text or "").strip())
    text = text.replace("/", "／").replace(":", "：")
    text = text.translate(str.maketrans({"(": "_", ")": "_", "（": "_", "）": "_"}))
    text = re.sub(r"[^\w\u4e00-\u9fff\-_.]+", "_", text, flags=re.UNICODE)
    text = re.sub(r"_+", "_", text).strip("._")
    if not text:
        text = "collector"
    return text[:limit].rstrip("._") or "collector"


def parse_attrs(tag: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for match in ATTR_RE.finditer(tag):
        attrs[match.group(1).lower()] = html.unescape(
            match.group(3) or match.group(4) or match.group(5) or ""
        )
    return attrs


def is_bare_collector_hash(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Fa-f0-9]{32}(?:-\d+)?", value or ""))


def normalize_resource_url(url: str) -> str:
    url = html.unescape((url or "").strip())
    if not url or url.startswith("data:") or url.startswith("blob:"):
        return ""
    if is_bare_collector_hash(url):
        return f"{CLIPPER_BASE}/third/collector-res/v1/download-without-auth?hash={url}"
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return urljoin(CLIPPER_BASE, url)
    return url


def content_type_to_ext(content_type: str | None, fallback: str = ".bin") -> str:
    ctype = (content_type or "").split(";", 1)[0].strip().lower()
    if ctype == "image/jpeg":
        return ".jpg"
    if ctype == "image/svg+xml":
        return ".svg"
    ext = mimetypes.guess_extension(ctype or "")
    return ext or fallback


def sniff_image_ext(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    if data.lstrip().lower().startswith(b"<svg"):
        return ".svg"
    return ""


def url_fallback_ext(url: str) -> str:
    path = urlparse(url).path
    suffix = Path(path).suffix.lower()
    if suffix and len(suffix) <= 8:
        return suffix
    return ".bin"


def hash_hint_from_url(url: str) -> str:
    query = parse_qs(urlparse(url).query)
    hash_values = query.get("hash")
    if hash_values and hash_values[0]:
        return sanitize_component(hash_values[0], 40)
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def quote_url_for_request(url: str) -> str:
    parts = urlsplit(url)
    path = quote(parts.path, safe="/:%@+~#=,;!$&'()*[]")
    query = quote(parts.query, safe="=&%/:?+~#,-.;!$'()*[]")
    fragment = quote(parts.fragment, safe="=&%/:?+~#,-.;!$'()*[]")
    return urlunsplit((parts.scheme, parts.netloc, path, query, fragment))


def download_file(url: str, dest_without_ext: Path) -> tuple[Path, str]:
    request = Request(
        quote_url_for_request(url),
        headers={
            "User-Agent": "Mozilla/5.0 YinxiangClipperMarkdownExporter/0.1",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        },
    )
    with urlopen(request, timeout=30) as response:
        data = response.read()
        content_type = response.headers.get("Content-Type")

    ext = sniff_image_ext(data)
    if not ext:
        raise RuntimeError(f"download did not return an image: content_type={content_type or ''}")
    dest = dest_without_ext.with_suffix(ext)
    dest.write_bytes(data)
    return dest, content_type or ""


def should_skip_image_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    skip_hosts = {
        "pagead2.googlesyndication.com",
        "googleads.g.doubleclick.net",
        "cm.g.doubleclick.net",
        "tags.bluekai.com",
        "id5-sync.com",
        "j.mrpdata.net",
        "pixel-sync.sitescout.com",
        "apsoutheast-match.deepintent.com",
        "openx2-match.dotomi.com",
        "c.bing.com",
        "bk-sync.metadsp.co.uk",
        "pt.ispot.tv",
        "dt.scanscout.com",
        "bluekai-sync.dotomi.com",
        "ad.atdmt.com",
        "px.surveywall-api.survata.com",
        "sync.ipredictive.com",
        "cms.analytics.yahoo.com",
        "s.amazon-adsystem.com",
        "pm.w55c.net",
        "usermatch.krxd.net",
        "sync.sharethis.com",
        "p.rfihub.com",
        "gum.criteo.com",
        "image6.pubmatic.com",
        "sync.crwdcntrl.net",
    }
    if host in skip_hosts:
        return True
    if any(part in host for part in ("doubleclick.net", "googlesyndication.com")):
        return True
    return False


def should_skip_failed_image_error(exc: Exception) -> bool:
    message = str(exc)
    if any(token in message for token in ("HTTP Error 404", "HTTP Error 410")):
        return True
    return False


def localize_images(html_body: str, assets_dir: Path, rel_assets_dir: str, failures: list[dict]) -> str:
    cache: dict[str, str] = {}
    counter = 0

    def replace_img(match: re.Match[str]) -> str:
        nonlocal counter
        tag = match.group(0)
        attrs = parse_attrs(tag)
        raw_src = attrs.get("data-src") or attrs.get("data-original") or attrs.get("src") or ""
        url = normalize_resource_url(raw_src)
        alt = attrs.get("alt") or attrs.get("title") or ""
        if not url:
            return ""
        if should_skip_image_url(url):
            return ""

        if url not in cache:
            counter += 1
            stem = f"image_{counter:03d}_{hash_hint_from_url(url)}"
            try:
                dest, _content_type = download_file(url, assets_dir / stem)
                cache[url] = f"{rel_assets_dir}/{dest.name}"
            except Exception as exc:
                if should_skip_image_url(url) or should_skip_failed_image_error(exc):
                    cache[url] = ""
                else:
                    failures.append({"url": url, "error": str(exc)})
                    cache[url] = url

        if not cache[url]:
            return ""

        src = html.escape(cache[url], quote=True)
        alt_value = html.escape(alt, quote=True)
        return f'<img src="{src}" alt="{alt_value}" />'

    return IMG_TAG_RE.sub(replace_img, html_body)


def html_to_markdown(html_body: str) -> str:
    html_body = re.sub(r"<span\b[^>]*>", "", html_body, flags=re.I)
    html_body = re.sub(r"</span>", "", html_body, flags=re.I)
    html_doc = f"<html><head><meta charset='utf-8'></head><body>{html_body}</body></html>"
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as tmp:
        tmp.write(html_doc)
        html_path = Path(tmp.name)

    try:
        proc = subprocess.run(
            ["pandoc", "-f", "html-native_divs-native_spans", "-t", "gfm", "--wrap=none", str(html_path)],
            capture_output=True,
            text=True,
            check=True,
        )
    finally:
        html_path.unlink(missing_ok=True)

    markdown = proc.stdout.replace("\xa0", " ").strip()
    markdown = re.sub(r"<span\b[^>]*>(.*?)</span>", r"\1", markdown, flags=re.I | re.S)
    markdown = re.sub(r"</?span\b[^>]*>", "", markdown, flags=re.I)
    markdown = re.sub(r"(?m)^\s*\\\s*$", "", markdown)
    markdown = re.sub(r"\n{3,}", "\n\n", markdown).strip()
    return markdown.replace("\\. ", ". ")


def timestamp_ms_to_local(value: object) -> str:
    if value in (None, ""):
        return ""
    try:
        number = int(str(value))
    except ValueError:
        return str(value)
    if number <= 0:
        return ""
    if number < 10_000_000_000:
        number *= 1000
    dt = datetime.fromtimestamp(number / 1000, tz=timezone.utc).astimezone()
    return dt.strftime("%Y-%m-%d %H:%M:%S %Z")


def tag_names(item: dict) -> str:
    values: list[str] = []
    for tag in item.get("itemTags") or []:
        if isinstance(tag, str):
            values.append(tag)
        elif isinstance(tag, dict):
            values.append(tag.get("tagName") or tag.get("name") or tag.get("title") or "")
    return ", ".join(value for value in values if value)


def item_attrs(item: dict) -> dict:
    attrs = item.get("attrs") or {}
    return attrs if isinstance(attrs, dict) else {}


def extract_cleaned_html(entry: dict) -> str:
    content = entry.get("content") or {}
    if isinstance(content, dict):
        for candidate in (
            content.get("cleanedHtml"),
            (content.get("content") or {}).get("cleanedHtml") if isinstance(content.get("content"), dict) else None,
        ):
            if candidate:
                return str(candidate)

    raw = entry.get("contentRaw")
    if isinstance(raw, list) and raw:
        raw = raw[0]
    if isinstance(raw, dict):
        raw_content = ((raw.get("data") or {}).get("content")) if isinstance(raw.get("data"), dict) else None
        if raw_content:
            try:
                parsed = json.loads(raw_content)
                body = parsed.get("content") if isinstance(parsed, dict) else None
                if isinstance(body, dict) and body.get("cleanedHtml"):
                    return str(body["cleanedHtml"])
            except json.JSONDecodeError:
                return str(raw_content)
    return ""


def primary_item(entry: dict) -> dict:
    for key in ("mateItem", "item", "listItem"):
        value = entry.get(key)
        if isinstance(value, dict):
            return value
    mate = entry.get("mate") or {}
    if isinstance(mate, dict):
        data = mate.get("data") if isinstance(mate.get("data"), dict) else mate
        item = data.get("item") if isinstance(data, dict) else None
        if isinstance(item, dict):
            return item
    return {}


def metadata_for_item(item: dict) -> dict[str, str]:
    attrs = item_attrs(item)
    return {
        "item_guid": str(item.get("itemGuid") or ""),
        "item_type": str(item.get("itemType") or ""),
        "collection_time": timestamp_ms_to_local(item.get("collectionTime")),
        "update_time": timestamp_ms_to_local(item.get("updateTime")),
        "source_url": str(attrs.get("sourceUrl") or attrs.get("originUrl") or ""),
        "source_name": str(attrs.get("sourceName") or attrs.get("domain") or ""),
        "website_type": str(attrs.get("websiteType") or ""),
        "article_word_count": str(attrs.get("articleWordCount") or ""),
        "tags": tag_names(item),
        "comment_count": str(item.get("commentCount") or ""),
        "read_total": str(item.get("readTotal") or ""),
        "like_total": str(item.get("likeTotal") or ""),
        "favorite_total": str(item.get("favoriteTotal") or ""),
    }


def title_for_item(item: dict, index: int) -> str:
    return str(item.get("title") or item_attrs(item).get("title") or f"剪藏 {index}")


def fallback_html_for_item(item: dict) -> str:
    pieces: list[str] = []
    desc = item.get("itemDesc") or item.get("content") or ""
    if desc:
        pieces.append(f"<p>{html.escape(str(desc))}</p>")
    raw_thumbnail = str(item.get("thumbnail") or "")
    thumbnail = ""
    if raw_thumbnail and not (item.get("itemType") == "WEB_PAGE" and is_bare_collector_hash(raw_thumbnail)):
        thumbnail = normalize_resource_url(raw_thumbnail)
    if thumbnail:
        pieces.append(f'<p><img src="{html.escape(thumbnail, quote=True)}" alt="" /></p>')
    attrs = item_attrs(item)
    source_url = attrs.get("sourceUrl")
    if source_url:
        escaped = html.escape(str(source_url), quote=True)
        pieces.append(f'<p>来源：<a href="{escaped}">{escaped}</a></p>')
    return "\n".join(pieces)


def write_markdown_for_entry(
    entry: dict,
    index: int,
    output_dir: Path,
    keep_remote_images: bool,
) -> tuple[str, list[dict]]:
    item = primary_item(entry)
    title = title_for_item(item, index)
    guid = item.get("itemGuid") or entry.get("itemGuid") or hashlib.sha1(title.encode("utf-8")).hexdigest()[:12]
    stem = f"{index:05d}-{sanitize_component(str(guid), 16)}-{sanitize_component(title, 40)}"
    assets_dir = output_dir / "assets" / stem
    assets_dir.mkdir(parents=True, exist_ok=True)
    rel_assets_dir = f"assets/{stem}"

    failures: list[dict] = []
    html_body = extract_cleaned_html(entry) or fallback_html_for_item(item)
    html_body = localize_images(html_body, assets_dir, rel_assets_dir, failures)
    if failures and not keep_remote_images:
        failed = "\n".join(f"- {item['url']}: {item['error']}" for item in failures)
        raise RuntimeError(f"image download failed for {title}\n{failed}")

    body_md = html_to_markdown(html_body) if html_body.strip() else ""
    meta = metadata_for_item(item)
    meta_lines = [f"- {key}: {value}" for key, value in meta.items() if value]
    sections = [f"# {title}"]
    if meta_lines:
        sections.append("## 元数据\n\n" + "\n".join(meta_lines))
    if body_md:
        sections.append(body_md)

    md_name = stem + ".md"
    (output_dir / md_name).write_text("\n\n".join(sections).strip() + "\n", encoding="utf-8")
    return md_name, failures


def validate_output(output_dir: Path, md_names: list[str]) -> list[str]:
    problems: list[str] = []
    for md_name in md_names:
        path = output_dir / md_name
        text = path.read_text(encoding="utf-8")
        if not text.startswith("# "):
            problems.append(f"{md_name}: missing title")
        if "item_type: WEB_PAGE" in text and "source_url:" not in text:
            problems.append(f"{md_name}: missing source_url metadata")
        if "![]()" in text or re.search(r"!\[[^\]]*\]\(\s*\)", text):
            problems.append(f"{md_name}: empty image link")
        for rel in re.findall(r"!\[[^\]]*\]\((assets/[^)]+)\)", text):
            target = output_dir / html.unescape(rel)
            if not target.exists():
                problems.append(f"{md_name}: missing asset {rel}")
            if target.suffix.lower() == ".bin":
                problems.append(f"{md_name}: image asset has generic .bin extension {rel}")
            if target.suffix.lower() in {".json", ".html", ".txt"}:
                problems.append(f"{md_name}: image asset has non-image extension {rel}")
    return problems


def convert_capture(input_path: Path, output_dir: Path, limit: int, keep_remote_images: bool) -> None:
    ensure_pandoc()
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    items = payload.get("items") or []
    if limit:
        items = items[:limit]
    if not items:
        raise RuntimeError("capture contains no items")

    output_dir.mkdir(parents=True, exist_ok=True)
    md_names: list[str] = []
    all_failures: list[dict] = []
    readme_lines = [
        "# 印象剪藏网页导出样例",
        "",
        f"- captured_at: {payload.get('capturedAt') or ''}",
        f"- source_page: {payload.get('sourcePage') or ''}",
        f"- paging_total: {(payload.get('paging') or {}).get('total') or ''}",
        f"- exported_count: {len(items)}",
        f"- raw_json: {input_path}",
        "",
        "## Items",
        "",
    ]

    for index, entry in enumerate(items, start=1):
        md_name, failures = write_markdown_for_entry(entry, index, output_dir, keep_remote_images)
        md_names.append(md_name)
        title = title_for_item(primary_item(entry), index)
        readme_lines.append(f"- [{title}]({md_name})")
        for failure in failures:
            all_failures.append({"item": title, **failure})

    problems = validate_output(output_dir, md_names)
    if all_failures:
        readme_lines.extend(["", "## Image Download Failures", ""])
        for failure in all_failures:
            readme_lines.append(f"- {failure['item']}: {failure['url']} ({failure['error']})")
    if problems:
        readme_lines.extend(["", "## Validation Problems", ""])
        readme_lines.extend(f"- {problem}" for problem in problems)
    (output_dir / "README.md").write_text("\n".join(readme_lines).strip() + "\n", encoding="utf-8")

    if problems:
        raise RuntimeError("validation failed:\n" + "\n".join(problems))
    print(f"exported {len(md_names)} items -> {output_dir}")


def main() -> int:
    args = parse_args()
    convert_capture(args.input, args.output_dir, args.limit, args.keep_remote_images)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
