---
name: bookmark-organizer
description: >
  Organize Chrome bookmarks exported as HTML files. Use this skill whenever the user mentions
  bookmarks, bookmark cleanup, bookmark organization, bookmark deduplication, Chrome bookmark export,
  or wants to sort/categorize/tidy their browser bookmarks. Also trigger when the user provides
  an HTML file that looks like a Chrome bookmark export, or mentions importing bookmarks back into Chrome.
  Even if the user just says "help me organize these bookmarks" or "clean up my bookmarks", use this skill.
---

# Chrome Bookmark Organizer

Organize Chrome-exported bookmark HTML files by extracting categories, removing duplicates, identifying near-duplicates, and re-categorizing bookmarks through collaborative discussion with the user.

## Core Principle: Scenario-Driven Classification

Categorize bookmarks by **usage scenario** ("when would I open this?"), not by **topic** ("what is this about?") or even **purpose** ("what do I want to do with this?").

The key question is: **"在什么情境下我会打开它？"** — a person is only in one state at a time, so scenarios are naturally mutually exclusive. This eliminates ambiguity and reduces classification discussion to 1-2 rounds.

## Workflow Overview

The full process has 7 steps. Always follow them in order.

```
Backup → Extract → Deduplicate → Check Dead Links → Analyze Near-Duplicates → Discuss Categories → Clean Up
```

## Step 1: Backup the Original File

Before any processing, copy the original HTML file to create a backup:

```bash
cp "<original>.html" "<original>_backup.html"
```

Confirm the backup was created successfully before proceeding.

## Step 2: Extract the Target Category

Ask the user which bookmark folder/category they want to organize. Then run:

```bash
python <skill-path>/scripts/extract_bookmarks.py "<original>.html" "<category-name>" "<category>_extracted.html"
```

If the user isn't sure which categories exist, read the HTML file and list all top-level folder names (look for `<H3>` tags) so they can choose.

## Step 3: Deduplicate

Run these two scripts in sequence. The first script outputs a Markdown report, and the second script reads that report to know which URLs to remove:

```bash
# Step 3a: Check for exact URL duplicates → produces a report
python <skill-path>/scripts/check_duplicate_urls.py "<category>_extracted.html"
# Output: <category>_extracted_重复检查.md

# Step 3b: Remove duplicates using the report (keeps the entry with the longest title)
python <skill-path>/scripts/remove_duplicate_bookmarks.py "<category>_extracted.html" "<category>_extracted_重复检查.md"
# Output: <category>_extracted_去重.html + <category>_extracted_去重报告.md
```

Report the results to the user: how many bookmarks total, how many duplicates found, how many removed. Use the deduplicated file (`_去重.html`) for all subsequent steps.

## Step 4: Check Dead Links

Run the accessibility checker to verify all URLs are still reachable. This uses concurrent requests for speed:

```bash
# Step 4a: Check all URLs for accessibility → produces a report
python <skill-path>/scripts/check_url_accessibility.py "<category>_extracted_去重.html"
# Output: <category>_extracted_去重_失效检查.md
```

**Important**: This script requires the `requests` library and optionally `playwright` for browser-based detection:
```bash
pip install requests
# 可选：安装 playwright 以启用浏览器检测（大幅减少误报）
pip install playwright && python -m playwright install chromium
```

The script uses a two-pass detection strategy:
1. **First pass**: `requests` concurrent checks (fast, but some sites block non-browser requests)
2. **Second pass**: Playwright headed Chrome retries failed URLs (catches Cloudflare/WAF false positives)

The report splits results into two tables: "确认无法访问的链接" (truly dead, recommend deletion) and "被反爬机制拦截的链接" (anti-crawl blocked, recommend manual check).

Use `--skip-browser` to skip the Playwright pass (faster, but more false positives):
```bash
python <skill-path>/scripts/check_url_accessibility.py "<file>.html" --skip-browser
```

### 4b: Let the user review the MD file directly

After the script finishes, do NOT read the report content or present it in the conversation. Instead:

1. Tell the user the **full file path** of the generated `_失效检查.md` report
2. Explain that the report contains a table of all inaccessible URLs, and the user should **open and edit the MD file directly** — delete any rows for URLs they want to keep (e.g., temporarily down sites, sites behind auth)
3. Tell the user: "编辑完成后，请回来告诉我'继续'，我会根据修改后的文件删除失效链接。"
4. **Stop and wait.** Do NOT proceed until the user explicitly says to continue (e.g., "继续", "好了", "go", "continue", etc.)

This approach is important because the MD file may contain many URLs, and editing it directly in a text editor is much more convenient than discussing each one in the conversation. The user can use find/replace, bulk delete, and review at their own pace.

### 4c: Remove dead bookmarks based on the edited report

Only after the user confirms they are done editing, run:

```bash
python <skill-path>/scripts/remove_dead_bookmarks.py "<category>_extracted_去重.html" "<category>_extracted_去重_失效检查.md"
# Output: <category>_extracted_去重_清理.html + <category>_extracted_去重_失效清理报告.md
```

The removal script reads the MD report as-is — it only removes URLs that still appear in the table. Since the user has already deleted any rows they want to keep, the script will do the right thing.

Use the cleaned file (`_清理.html`) for all subsequent steps. If the report shows all URLs are accessible (no table in the MD file), skip this step and continue using the `_去重.html` file.

## Step 5: Analyze Near-Duplicates

This step goes beyond exact URL matching. Read the deduplicated HTML file and analyze the bookmarks to find entries where:

- URLs differ only in query parameters, fragments, or trailing paths (e.g., `example.com/article` vs `example.com/article?ref=twitter`)
- URLs point to different pages on the same domain with very similar titles
- Different URLs point to mirrors or reposts of the same content (e.g., same article on Medium and a personal blog)
- Shortened URLs that likely point to the same destination as a full URL in the list

Present the near-duplicate groups to the user in a clear format:

```
Group 1:
  - "Article Title A" → https://example.com/post/123
  - "Article Title A - Shared" → https://example.com/post/123?utm_source=twitter

Group 2:
  - "Python Tutorial" → https://blog.example.com/python-intro
  - "Introduction to Python" → https://medium.com/@author/python-intro
```

Ask the user which ones to merge or remove. Wait for their decision on each group before making changes.

## Step 6: Scenario-Driven Category Discussion

This is the most important step. Do NOT skip the discussion or auto-apply categories.

### Core Principle: Scenario-Driven Classification

Do NOT ask "what is this website?" — ask **"when would I open this?"**

The key insight: a person is only in one state at any moment, so usage scenarios are naturally mutually exclusive. Stack Overflow is technically a "community" site, but you open it 90% of the time because you hit a bug — so it goes under "查阅参考", not "社区". This eliminates the "could go in either category" problem.

### How to approach the discussion

#### Phase 1: Define 3-5 Usage Scenarios

Analyze the bookmarks and propose 3-5 scenarios. Scenarios describe **a state the user is in**, not a type of website:

Good scenarios (state-based, mutually exclusive):
- "我在写代码，需要一个工具" → **开发工具** (editors, CLI tools, APIs I actively use)
- "我遇到问题，需要查资料" → **查阅参考** (docs, Q&A, references I search when stuck)
- "我想系统学点东西" → **学习提升** (courses, tutorials, books I study intentionally)
- "我在刷信息流/逛社区" → **浏览发现** (news, feeds, communities I browse casually)
- "我有个想法，需要找素材/灵感" → **灵感素材** (galleries, showcases, examples I browse for inspiration)

Bad scenarios (these are topic labels, NOT scenarios):
- "JavaScript", "Machine Learning", "Design" — describes *what*, not *when*
- "社区", "博客", "文档" — describes *site type*, not *user state*

Present each scenario with: the trigger state, 2-3 example bookmarks that clearly belong there, and the reasoning.

#### Phase 2: User Confirms Scenario List

Wait for the user to confirm the scenario list. Since scenarios are mutually exclusive by nature, this usually requires only 1 round of adjustment. Do NOT proceed until the user explicitly agrees.

#### Phase 3: Assign Bookmarks Using the Judgment Question

For each bookmark, apply this single judgment question:

> **"我最常在哪个场景下点开它？"**

Key rules:
- One bookmark → one scenario, no exceptions
- Go by actual usage frequency, not what the site "is"
- When genuinely ambiguous, pick the scenario where the user would reach for it FIRST

Show the full mapping grouped by scenario. Let the user review and adjust individual assignments.

#### Phase 4: Split Large Scenarios

If any scenario has more than 25 bookmarks, split it into sub-scenarios. Sub-scenarios should still follow the same principle — they describe a more specific state, not a topic:

Good split: "学习提升" → "跟课程学" + "刷题练习"
Bad split: "学习提升" → "Python学习" + "前端学习"

#### Phase 5: Apply Changes

Only after the user confirms the final mapping, generate the organized HTML file.

### Generating the Final Output

The output MUST be a valid Netscape Bookmark HTML file that Chrome can import. Use this exact format:

```html
<!DOCTYPE NETSCAPE-Bookmark-file-1>
<!-- This is an automatically generated file.
     It will be read and overwritten.
     DO NOT EDIT! -->
<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">
<TITLE>Bookmarks</TITLE>
<H1>Bookmarks</H1>
<DL><p>
    <DT><H3>Category Name</H3>
    <DL><p>
        <DT><A HREF="url" ADD_DATE="timestamp" ICON="...">Title</A>
    </DL><p>
</DL><p>
```

Preserve all original attributes on `<A>` tags (ADD_DATE, ICON, etc.). Only change the folder structure.

## Step 7: Clean Up

After generating the final output, delete all intermediate files:
- The extracted HTML
- The duplicate check report (`.md` files)
- The deduplicated intermediate HTML
- The dead link check report and cleaned HTML
- Any other temporary files created during the process

Only two files should remain:
1. `<original>_backup.html` — the untouched backup
2. `<final-output>.html` — the organized, categorized bookmarks ready for Chrome import

Tell the user the final file path and remind them they can import it into Chrome via: Settings → Bookmarks and Lists → Import bookmarks and settings → Bookmarks HTML File.

## Important Notes

- Always work on copies, never modify the original file (that's what the backup is for)
- If the user provides an HTML file that isn't in Chrome bookmark format, let them know and ask if they exported it correctly
- The scenario confirmation in Step 6 typically takes 1-2 rounds since scenarios are naturally mutually exclusive — be efficient but still responsive to the user's preferences
- If the bookmark count is very large (500+), consider suggesting to process in batches by sub-category
- Keep all intermediate files in the same directory as the input HTML file
- Communicate with the user in the same language they use (likely Chinese)
