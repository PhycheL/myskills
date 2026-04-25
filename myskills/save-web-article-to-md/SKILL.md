---
name: save-web-article-to-md
description: Use when saving a public web article, WeChat Official Account page, blog post, documentation page, or newsletter URL as a local Markdown file with images and source metadata.
---

# Save Web Article To MD

Use this skill to create a durable local Markdown copy of an article page. Prefer preserving the original title, author, publish time, source URL, article body, code blocks, and images.

## Workflow

1. If network access is needed, use the available web-access/browser skill first, especially for WeChat, login-dependent pages, dynamic rendering, or anti-bot pages.
2. Try the bundled script against the URL:

```bash
python3 /Users/bemied/.codex/skills/save-web-article-to-md/scripts/save_article_to_md.py \
  "https://example.com/article" \
  --out-dir output/articles
```

3. If direct fetching returns a verification, login, or blocked page, obtain the rendered/original HTML with the browser workflow, then run:

```bash
python3 /Users/bemied/.codex/skills/save-web-article-to-md/scripts/save_article_to_md.py \
  --html /path/to/article.html \
  --source-url "https://example.com/article" \
  --out-dir output/articles
```

4. Verify the Markdown manually enough to catch common conversion errors: missing title/body, verification-page content, broken image paths, code block line-number prefixes, and unrelated navigation/footer text.

## Output Layout

Use a stable article slug under the requested output directory:

```text
output/articles/<article-slug>/
  <article-slug>.md
  assets/
    image-1.jpeg
    image-2.png
```

The Markdown should include YAML frontmatter:

```yaml
---
title: ...
author: ...
published_at: ...
source: ...
saved_at: ...
---
```

## WeChat Notes

- WeChat articles often keep the body in `#js_content`; direct conversion of the whole HTML includes scripts and UI chrome.
- The article images usually live in `data-src`; normalize them to `src` before conversion.
- Jina or generic fetchers may return `环境异常`/verification content even when raw HTML contains the article. Treat that as a failed extraction, not as the article.
- If Chrome CDP cannot connect because of a remote-debugging authorization prompt, fall back to direct HTML only if it contains `#js_content`.

## Script Behavior

`scripts/save_article_to_md.py`:

- fetches a URL or reads `--html`;
- extracts metadata from Open Graph, WeChat globals, and common meta tags;
- extracts WeChat `#js_content`, `<article>`, `<main>`, or the largest text-like body fallback;
- converts HTML to GitHub-flavored Markdown via `pandoc`;
- downloads Markdown image links into `assets/` and rewrites links locally;
- cleans common WeChat conversion artifacts such as standalone backslashes and code-block line-number prefixes.

If `pandoc` is missing, install it or perform conversion with another reliable HTML-to-Markdown tool. Do not hand-roll large conversions unless the article is very small.
