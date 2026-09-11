---
name: flomo-tag-diaries
description: Collect flomo memos under a given tag and export them as dated
  Markdown diary files (YYYY-MM-DD.md). Produces two documents per day — a
  verbatim raw diary and, for days with voice memos, a formal diary
  (YYYY-MM-DD-书面语.md) whose voice transcriptions are polished into written
  Chinese per bundled rules. Use when the user asks to export, dump, or back up
  flomo notes by tag into dated diary files.
agent_created: true
---

# Flomo Tag Diaries

## Overview

Export every flomo memo carrying a specific tag into dated Markdown diary files and download every attached **voice memo audio** into `<OUTPUT_DIR>/audio/<DATE>/`.

**每个有笔记的日期产出两份文档：**

1. **原始版 `<day>.md`** — 逐字转储。录音转文字的原始版本，一字不改。
2. **正式版 `<day>-书面语.md`** — 仅当当天含有语音笔记（`has_voice: true`）时生成。把语音笔记的口语化正文按 `references/kouyu-to-shumian.md` 的规则转为通顺书面语；纯文字笔记原样保留。结构与元信息（日期标题、`### 时间` 小标题、来源/音频链接）与原始版完全一致。

Design principle (important): **do NOT pull everything down and split afterwards.** Instead:
1. First discover *which days* have memos for the tag.
2. Then fetch that day's memos and write them straight into that day's file — one day at a time.

This keeps memory small (only one day is held at a time) and scopes the 50-per-page limit to a single day.

Requires the **flomo** MCP connector to be connected (provides `memo_search`, `memo_batch_get`). All data is fetched through the MCP tools; no direct API token is needed. See `references/flomo_api.md` for the memo object shape and pagination details.

## ⛔ Strict Constraints (HARD RULES — never violate)

This skill has **two faces**，约束分别适用：

- **原始版 `<day>.md` 是 verbatim transcriber**，NOT a writer. The output must be an exact, byte-faithful copy of flomo's content.
- **正式版 `<day>-书面语.md` 是唯一允许改写的文件**，且只允许对语音笔记正文按 `references/kouyu-to-shumian.md` 做语言清洁，不得自由发挥。

1. **逐字转储 (verbatim) — 仅约束原始版.** Copy each memo's `content` character-for-character into `<day>.md`. Preserve flomo's own escaping (e.g. `memo\_id`), links, emojis, line breaks, punctuation, typos, and original formatting. Do NOT "fix" anything.
2. **禁止总结 / 转写 / 改写 / 润色 / 翻译 (no generation).** Never summarize, paraphrase, rewrite, polish, correct, translate, or add explanatory text to a memo's body. The model must not author a single sentence of the diary content.
3. **每条笔记用 `### 时间` 区分.** Every individual memo becomes its own `### HH:MM` block. Do NOT merge multiple memos of a day into one paragraph or one narrative. One memo = one `###` block.
4. **唯一允许的变换：** 仅删除 flomo 自动加在正文最前面的 `#tag` 标签行（及其后紧接的一个空行）。除此之外，正文内容一律原样保留。
5. **允许的结构性包装（非内容、不算发挥）：** 当天的 `## 日期 星期` 大标题、每条的 `### 时间` 小标题、`> 来源：[flomo 原文](url)` 溯源链接、`> 音频：[..](audio/...)` 本地音频链接、`---` 分隔符、以及由 `has_voice`/`has_image` 字段生成的 `🎤 语音记录` / `📷 图片` 标签。这些只是元信息，绝不可改动 memo 正文本身。
6. **不添加评论。** 不要在任何产出文件里写任何「说明 / 摘要 / 编者按」之类的话（正式版的语言清洁不算评论，它是规则允许的转换）。
7. **正式版的改写边界。** `<day>-书面语.md` 中：语音笔记（🎤 语音记录）的正文按 `references/kouyu-to-shumian.md` 整理为书面语；纯文字笔记正文逐字照抄；日期大标题、`### 时间` 小标题、`🎤/📷` 标记、`> 来源`/`> 音频` 链接、`---` 分隔符一律与原始版保持一致，不得改动。

If a memo's content looks messy, incomplete, or grammatically off — that is the user's own data. Transcribe it exactly as-is in the raw file; polish it only in the formal file.

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
4. **Write the day's file + download audio:** run the bundled script with the managed Python interpreter:
   ```bash
   /Users/bemied/.workbuddy/binaries/python/versions/3.13.12/bin/python3 \
     ~/.workbuddy/skills/flomo-tag-diaries/scripts/build_day.py \
     /tmp/flomo_day_<day>.json <OUTPUT_DIR> <day>
   ```
   This writes `<OUTPUT_DIR>/<day>.md` AND downloads every memo's audio (`files[].type == "recorded"`) into `<OUTPUT_DIR>/audio/<day>/<day>_<HH-MM>.m4a`, adding a `> 音频：[..](audio/<day>/..)` link next to each source link in the .md.

   ⚠️ **Audio URL 时效（关键）：** flomo 的音频是阿里云 OSS 签名 URL，通常几小时内过期。**必须在获取 memo 的同一轮运行内立即执行 build_day.py**——先把 memo JSON 存盘、隔天再跑脚本会拿到过期链接导致下载失败（失败只会告警，不影响 .md 生成，但音频就丢了）。若要补下载历史音频，须重新调用 `memo_search`/`memo_batch_get` 拿新签名 URL。已存在的同名音频文件会跳过（幂等，可安全重跑）。
5. **生成正式版 `<day>-书面语.md`（仅当天含语音笔记时）：** 若当天 memo 中有任何 `has_voice: true`：
   - 读取刚写好的原始版 `<day>.md`；
   - 对每个标有 `🎤 语音记录` 的 `### HH:MM` 块，按 `references/kouyu-to-shumian.md` 的规则把正文整理为书面语（去语气词/填充词、合并口语重复、删正文内时间戳、精简冗余表达、纠正有把握的 ASR 错字）；
   - 纯文字笔记的块逐字照抄；所有结构行（`## 日期 星期`、`### 时间`、`🎤/📷` 标记、`> 来源`、`> 音频`、`---`）与原始版逐字一致；
   - 写入 `<OUTPUT_DIR>/<day>-书面语.md`；
   - 记录本轮所做的 ASR 纠正（供 Step 3 汇报）。
   - 若当天没有语音笔记，不生成正式版（避免产生与原始版完全相同的冗余文件）。
6. Optionally present the file as it is produced.

Processing one day fully before moving to the next avoids holding all memos at once.

### Step 3 — Verify & report

- Count generated `YYYY-MM-DD.md` files; it should equal the number of days discovered in Step 1.
- Count generated `YYYY-MM-DD-书面语.md` files; it should equal the number of days that had at least one voice memo.
- 在汇报中按 `references/kouyu-to-shumian.md` 的格式列出本次所有正式版日记的**主要语音识别纠正**（按日期分组）；若全部没有发现，说明"未发现明显的语音识别错误"。
- Present the result files to the user.

## Output Format (per file)

原始版 `<day>.md`：

```markdown
## 2026-07-19 星期日

### 15:54

<cleaned memo body, leading #tag line removed>

> 来源：[flomo 原文](https://v.flomoapp.com/mine/?memo_id=MjQ3NTI1NTA4)

---

### 15:17

🎤 语音记录（if has_voice）
...

> 来源：[flomo 原文](https://v.flomoapp.com/mine/?memo_id=MjQ3NTI1NTA4)
> 音频：[2026-07-19_15-17](audio/2026-07-19/2026-07-19_15-17.m4a)（仅当音频成功下载）
```

- Date header includes the Chinese weekday, auto-computed.
- Memos within a day are sorted by creation time and separated by `---`.
- Voice memos are prefixed with `🎤 语音记录`, image memos with `📷 图片`.
- Audio files live at `<OUTPUT_DIR>/audio/<DATE>/<DATE>_<HH-MM>.m4a`; same-minute collisions get `-1`, `-2` suffixes; re-runs skip already-downloaded files.

正式版 `<day>-书面语.md`：结构与上面完全相同（同样的日期标题、时间小标题、标记、来源/音频链接、分隔符），唯一区别是 `🎤 语音记录` 块的正文已按 `references/kouyu-to-shumian.md` 整理为书面语。仅当天含语音笔记时生成。

## Resources

- `scripts/build_day.py` — writes ONE day's raw `<DATE>.md` from that day's memo JSON AND downloads all recorded audio into `<OUTPUT_DIR>/audio/<DATE>/` (OSS signed URLs expire fast — run it in the same turn the memos were fetched). Run as shown in Step 2.
- `references/flomo_api.md` — memo object field reference and the date-range pagination / day-enumeration strategy.
- `references/kouyu-to-shumian.md` — 口语转书面语规则：正式版 `<day>-书面语.md` 中语音笔记正文的清理规则（语气词、口语重复、时间戳、冗余精简、ASR 纠正）及纠正结果汇报格式。
