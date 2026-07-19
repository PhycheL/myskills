# flomo MCP API Reference (for tag diary export)

## memo_search

Search memos by tag and/or date range.

Parameters:
- `tag` (str): tag name **without** the leading `#`, e.g. `"流水账"`.
- `limit` (int): max results per call. Use `50` (the practical max).
- `start_date` / `end_date` (str, `YYYY-MM-DD`): inclusive date range filter.
- `from` (str): **NOT reliably supported** as a cursor — do not rely on it for pagination.

Returns:
```json
{
  "memos": [
    {
      "id": "MjQ3NTI1NTA4",
      "content": "#流水账\n\n重启日记\n\n...",
      "content_truncated": false,
      "created_at": "2026-07-19T15:54:41+08:00",
      "updated_at": "2026-07-19T16:39:23+08:00",
      "url": "https://v.flomoapp.com/mine/?memo_id=MjQ3NTI1NTA4",
      "tags": ["流水账"],
      "has_image": false,
      "has_voice": false,
      "has_link": false,
      "from": "human",
      "word_count": 82
    }
  ]
}
```

## memo_batch_get

Re-fetch full content for memos that were truncated.

Parameters:
- `ids` (list[str]): memo slug ids, e.g. `["MjQ3NTIwOTg2", "MjQ2Nj..."]`. Batch several at once.

Returns the same memo object shape with `content_truncated: false` and complete `content`.

## Pagination strategy (two phases, date-range bisection)

`memo_search` has no working `from` cursor. The skill works in two phases; both rely on date-range bisection to guarantee completeness.

### Phase 1 — Enumerate days
Goal: find every distinct calendar day that has a memo with the tag (content may stay truncated).

1. Query the full span: `start_date="2000-01-01"`, `end_date=<today>`, `limit=50`, `tag=<tag>`.
2. If result count `< 50`: range is complete.
3. If result count `== 50`: range is saturated — it may hide more than 50 memos. **Bisect** at the midpoint date and query each half (`[start, mid]`, `[mid+1, end]`) with `limit=50`.
4. Recurse on any sub-range returning exactly 50.
5. Collect the `created_at` date of every memo into a day set.

### Phase 2 — Fetch + write, one day at a time
For each discovered day:

1. `memo_search` with `start_date = end_date = <day>`, `limit=50`. This returns only that day's memos.
2. If it returns exactly 50, bisect *within the day* (e.g. morning vs afternoon) and merge — a single busy day can still exceed 50.
3. For any `content_truncated: true`, `memo_batch_get(ids)` to get full text.
4. Write that day's `<DATE>.md` (see `scripts/build_day.py`).

Because Phase 2 scopes the 50-limit to one day, bisection there is rare and cheap.

## Content cleaning (VERBATIM — critical)

This skill is a transcriber, not a writer. The memo body MUST be copied exactly.

- **Only allowed transform:** flomo auto-prepends the tag as `#tag\n\n`. Strip the leading `#tag` line(s) (regex `^#[^ \n]`, i.e. a hashtag with no space) and the single blank line immediately after. **Do not** strip lines that are real markdown headings (`# Heading` with a space).
- **Preserve everything else exactly:** flomo's own escaping (e.g. `memo\_id`), links, emojis, line breaks, punctuation, typos, and original formatting. Never "fix", rewrite, summarize, translate, or polish.
- One memo = one `### HH:MM` block. Never merge multiple memos into one narrative or paragraph.
- Permitted structural wrappers only (not content): the `## 日期 星期` day header, the `### 时间` per-memo header, the `> 来源：[flomo 原文](url)` link, the `---` separator, and the `🎤 语音记录` / `📷 图片` media label derived from `has_voice`/`has_image`. These are metadata — they never alter the memo body.

## Weekday

Compute the Chinese weekday from the date: `date.fromisoformat(day).weekday()` → 0=星期一 … 6=星期日.
