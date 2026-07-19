---
name: flomo-tag-diaries
description: Collect flomo memos under a given tag and export them as one Markdown diary file per day, named by date (YYYY-MM-DD.md). This skill should be used when the user asks to export, dump, or back up flomo notes by tag into dated diary files, or to turn a flomo tag (e.g. #流水账) into a daily journal.
agent_created: true
---

# Flomo Tag Diaries

## Overview

Export every flomo memo carrying a specific tag into dated Markdown diary files — **one file per calendar day, named `YYYY-MM-DD.md`**.

Design principle (important): **do NOT pull everything down and split afterwards.** Instead:
1. First discover *which days* have memos for the tag.
2. Then fetch that day's memos and write them straight into that day's file — one day at a time.

This keeps memory small (only one day is held at a time) and scopes the 50-per-page limit to a single day.

Requires the **flomo** MCP connector to be connected (provides `memo_search`, `memo_batch_get`). All data is fetched through the MCP tools; no direct API token is needed. See `references/flomo_api.md` for the memo object shape and pagination details.

## ⛔ Strict Constraints (HARD RULES — never violate)

This skill is a **verbatim transcriber**, NOT a writer. The output must be an exact, byte-faithful copy of flomo's content.

1. **逐字转储 (verbatim).** Copy each memo's `content` character-for-character. Preserve flomo's own escaping (e.g. `memo\_id`), links, emojis, line breaks, punctuation, typos, and original formatting. Do NOT "fix" anything.
2. **禁止总结 / 转写 / 改写 / 润色 / 翻译 (no generation).** Never summarize, paraphrase, rewrite, polish, correct, translate, or add explanatory text to a memo's body. The model must not author a single sentence of the diary content.
3. **每条笔记用 `### 时间` 区分.** Every individual memo becomes its own `### HH:MM` block. Do NOT merge multiple memos of a day into one paragraph or one narrative. One memo = one `###` block.
4. **唯一允许的变换：** 仅删除 flomo 自动加在正文最前面的 `#tag` 标签行（及其后紧接的一个空行）。除此之外，正文内容一律原样保留。
5. **允许的结构性包装（非内容、不算发挥）：** 当天的 `## 日期 星期` 大标题、每条的 `### 时间` 小标题、`> 来源：[flomo 原文](url)` 溯源链接、`---` 分隔符、以及由 `has_voice`/`has_image` 字段生成的 `🎤 语音记录` / `📷 图片` 标签。这些只是元信息，绝不可改动 memo 正文本身。
6. **不添加评论。** 不要在文件里写任何「说明 / 摘要 / 编者按」之类的话。

If a memo's content looks messy, incomplete, or grammatically off — that is the user's own data. Transcribe it exactly as-is.

## Workflow

### Step 1 — Enumerate the days ("先拆解出来有几天")

Goal: build the complete set of calendar days that have at least one memo with the tag. Content may stay truncated here — only the dates matter.

- Call `mcp__flomo__memo_search` with `tag` (no `#`), `limit: 50`, and a wide `start_date`/`end_date` (e.g. `2000-01-01` → today).
- For every returned memo, record the **date part** of `created_at` (YYYY-MM-DD) into a set.
- **Pagination (critical):** `memo_search` has no reliable `from` cursor. If a range returns **exactly 50** results it may hide more than 50 memos — **bisect** at the midpoint date and re-query each half (`[start, mid]`, `[mid+1, end]`), repeating until no sub-range returns 50.
- When the whole span is covered, the set of days is complete.

### Step 2 — For each day, fetch + write ("按天获取 → 当天写入 md")

Loop over the sorted day set. For each day:

1. **Fetch that single day:** `memo_search` with `tag`, `start_date = end_date = <day>`, `limit: 50`. This returns only that day's memos.
   - If it returns exactly 50, bisect *within the day* (e.g. morning vs afternoon) and merge — a single day can still exceed 50.
2. **Complete truncated memos:** for any `content_truncated: true`, re-fetch full text via `mcp__flomo__memo_batch_get` with the batch of `ids`.
3. **Stage the day's memos:** write that day's memo array to a temp JSON, e.g. `/tmp/flomo_day_<day>.json` (bare array form).
4. **Write the day's file:** run the bundled script with the managed Python interpreter:
   ```bash
   /Users/bemied/.workbuddy/binaries/python/versions/3.13.12/bin/python3 \
     ~/.workbuddy/skills/flomo-tag-diaries/scripts/build_day.py \
     /tmp/flomo_day_<day>.json <OUTPUT_DIR> <day>
   ```
   This writes `<OUTPUT_DIR>/<day>.md`.
5. Optionally present the file as it is produced.

Processing one day fully before moving to the next avoids holding all memos at once.

### Step 3 — Verify

- Count generated `YYYY-MM-DD.md` files; it should equal the number of days discovered in Step 1.
- Present the result files to the user.

## Output Format (per file)

```markdown
## 2026-07-19 星期日

### 15:54

<cleaned memo body, leading #tag line removed>

> 来源：[flomo 原文](https://v.flomoapp.com/mine/?memo_id=MjQ3NTI1NTA4)

---

### 15:17

🎤 语音记录（if has_voice）
...
```

- Date header includes the Chinese weekday, auto-computed.
- Memos within a day are sorted by creation time and separated by `---`.
- Voice memos are prefixed with `🎤 语音记录`, image memos with `📷 图片`.

## Resources

- `scripts/build_day.py` — writes ONE day's `<DATE>.md` from that day's memo JSON. Run as shown in Step 2.
- `references/flomo_api.md` — memo object field reference and the date-range pagination / day-enumeration strategy.
