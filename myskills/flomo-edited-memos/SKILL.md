---
name: flomo-edited-memos
agent_created: true
description: 导出"创建早于某日、但在该日被修改过"的 flomo 笔记，按创建日期分组写入 <创建日期>-修改.md。当用户要定期扫描 flomo 笔记的最后修改时间（updated_at）、把两次扫描之间被修改的旧笔记重新导出时使用。与 flomo-tag-diaries 互补：那个管"当天创建"，这个管"当天修改"。
---

# Flomo Edited Memos（修改笔记导出）

## Overview

flomo-tag-diaries 按**创建时间**导出日记；但旧笔记之后还会被修改。本 skill 负责另一半：给定扫描日 D，找出所有 **created_at < D 且 updated_at 的日期 == D** 的笔记，把它们的最新全文导出到 `<OUTPUT_DIR>/<创建日期>-修改.md`（按**创建日期**分组，一个创建日期一个文件），语音附件下载到 `<OUTPUT_DIR>/audio/<创建日期>/`。

需要 **flomo** MCP 连接器（`memo_search`、`memo_batch_get`）。

## 已验证的 API 事实（2026-08-26 实测，直接采信）

- 每条 memo 都有 `updated_at` 字段，编辑正文、改标签、批量整理都会刷新它。flomo **没有历史版本**，只有"最后修改时间"。
- `memo_search` 的 `start_date`/`end_date` 过滤的是 **created_at**，不是 updated_at。实测：查 8-24 只返回 8-24 创建的笔记；一条 8-22 创建、8-24 修改的笔记不出现。
- 无参数查询返回**最新创建**的 50 条（created_at 倒序），不是按 updated_at 排序。API 没有"最近修改"入口。
- 因此"哪些旧笔记在 D 被修改"**无法直接查询，只能全量枚举后自行筛选**。这是本 skill 的核心约束。
- 枚举方法与 flomo-tag-diaries 相同：`limit=50` + 日期区间二分，凡返回恰好 50 条的区间必须二分重查，直到所有子区间 <50。不传 tag/keywords 即覆盖全部笔记（tag 参数偶发返回空，勿用）。
- 音频 OSS 签名 URL 几小时内过期：**拿到 memo 的同一轮内必须跑 build_edited.py**。

## ⛔ Strict Constraints（与 flomo-tag-diaries 相同的铁律）

1. **逐字转储**：memo 正文一字不改，保留 flomo 自己的转义、链接、emoji、换行、错别字。
2. **禁止总结/改写/润色/翻译**，不写任何编者按。
3. 唯一允许的变换：删除 flomo 自动加在文首的 `#tag` 行（及其后一个空行）。
4. 每条笔记一个 `### HH:MM` 块（这里是**修改时间**），不合并。
5. 只允许结构性包装：`## <创建日期> 星期X（<扫描日> 修改）` 大标题、`### 时间` 小标题、`> 创建于：… ｜ 修改于：…`、`> 来源：…`、`> 音频：…`、`---`、`🎤 语音记录`/`📷 图片`。

## Workflow（扫描日 D）

### Step 1 — 全量枚举 created_at ≤ D-1 的笔记（只要 id/created_at/updated_at）

- 用 `memo_search(start_date, end_date, limit=50)` 按创建时间区间二分枚举 `[2000-01-01, D-1]` 的全部笔记。内容可截断，只需要三个字段。
- **强烈建议委派给子 agent 做**（general-purpose，后台运行）：枚举要几十次调用、响应体巨大，在主上下文做会爆。让子 agent 把全量索引写成 `/tmp/flomo_memo_index.json`（数组：`{"id","created_at","updated_at"}`），并把 `updated_at` 日期 == D 的命中笔记完整 JSON 写成 `/tmp/flomo_hits_<D>.json`。
- 密度参考（该用户实测）：2026 年约 50 条/2 周，2025 年约 50 条/2 月，2023-2024 更稀。初始可按年/季度切，遇 50 继续二分。
- 单天区间仍返回 50 条时无法再二分，记入报告作为可能不完整项（极罕见）。

### Step 2 — 筛选命中

命中条件（两个同时满足）：
- `created_at` 日期 < D（当天创建的归 flomo-tag-diaries 管，跳过）
- `updated_at` 日期 == D

### Step 3 — 补全全文

命中笔记若 `content_truncated=true`（或数量少时一律），用 `memo_batch_get(ids)` 补全（单次 ≤10 条；顶层 `omitted_ids` 非空则单独再拉）。

### Step 4 — 写文件 + 下载音频（同一轮内！）

把命中 memo 数组存为 JSON，然后：

```bash
/Users/bemied/.workbuddy/binaries/python/versions/3.13.12/bin/python3 \
  ~/.workbuddy/skills/flomo-edited-memos/scripts/build_edited.py \
  /tmp/flomo_hits_<D>.json <OUTPUT_DIR> <D>
```

脚本按创建日期 C 分组，写 `<OUTPUT_DIR>/<C>-修改.md`，并把语音下载到 `<OUTPUT_DIR>/audio/<C>/`（命名与 flomo-tag-diaries 一致、幂等跳过已存在文件——两个 skill 共享同一 audio 仓库不冲突）。

### Step 5 — 汇报

列出写出的文件、各文件条数、音频数、可能不完整项，present_files 展示。

## 增量语义（"两次扫描之间的修改"）

用户的意图是定期扫描、导出新发生的修改。每天定时跑时，「updated_at == 昨天」就是天然增量。若运行间隔可能超过一天，用索引做精确增量：

- 在 `<OUTPUT_DIR>/.flomo_memo_index.json` 持久化上次的 `{id: updated_at}`。
- 本次枚举后对比：`updated_at` 与索引不同（或 id 不在索引中且 created < D）即为"两次扫描之间被修改"，全部导出（同一笔记多次修改只导最新版——flomo 无版本历史）。
- 导出后用新枚举结果整体覆盖索引。

## Output Format（per file）

```markdown
## 2026-08-25 星期二（2026-08-26 修改）

### 23:03

<cleaned memo body, leading #tag line removed>

> 创建于：2026-08-25 11:31 ｜ 修改于：2026-08-26 23:03
> 来源：[flomo 原文](https://v.flomoapp.com/mine/?memo_id=MjUzNDc2MDky)

---
```

- 文件名 `<创建日期>-修改.md`；大标题注明扫描日；`### HH:MM` 是修改时间；同一创建日期的多条按修改时间排序。
- 语音条前缀 `🎤 语音记录`，音频链接在来源链接下一行。

## Resources

- `scripts/build_edited.py` — 把命中 memo JSON 按创建日期分组写 `<C>-修改.md` 并下载音频（OSS URL 易过期，必须同轮运行）。
- 参考实现与更多 API 细节见 `~/.workbuddy/skills/flomo-tag-diaries/references/flomo_api.md`（二分枚举策略、内容清洗规则与本 skill 完全相同）。
