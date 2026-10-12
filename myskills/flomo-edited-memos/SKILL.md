---
name: flomo-edited-memos
metadata:
  agent_created: true
description: 全量扫描 flomo，导出修改时间与创建时间不同、且修改时间晚于本地基线的笔记；支持隔天、隔周或隔月运行，并在本地记录运行日期和改动笔记数。
---

# Flomo Edited Memos（修改笔记导出）

## Overview

本 skill 定期扫描 flomo，把基线之后修改过的笔记最新全文导出到本地。扫描间隔可以是一天、一周或一个月。按创建日期分组写入 `<OUTPUT_DIR>/<创建日期>-修改.md`，语音附件下载到 `<OUTPUT_DIR>/audio/<创建日期>/`。

只保存一个统一的基线时间 `baseline_at`，命中条件是：

```text
updated_at != created_at
并且 updated_at > baseline_at
```

比较完整时间戳，统一按北京时间（UTC+08:00）处理。**不保存、不比较每条笔记上次的 updated_at**。基线之后新建、又被修改过的笔记也纳入；创建后未修改的笔记不纳入。同一笔记修改多次算 1 条，只导出最新版本。

扫描开始前记录本轮截止时间 `scan_started_at`；晚于此时间的修改留到下一轮。成功后把基线推进到这个固定时间，避免扫描过程中发生的修改被遗漏。

需要 **flomo** MCP 连接器（`memo_search`、`memo_batch_get`）。

## 已验证的 API 事实（2026-08-26 实测，直接采信）

- 每条 memo 都有 `updated_at` 字段，编辑正文、改标签、批量整理都会刷新它。flomo **没有历史版本**，只有"最后修改时间"。
- `memo_search` 的 `start_date`/`end_date` 过滤的是 **created_at**，不是 updated_at。实测：查 8-24 只返回 8-24 创建的笔记；一条 8-22 创建、8-24 修改的笔记不出现。
- 无参数查询返回**最新创建**的 50 条（created_at 倒序），不是按 updated_at 排序。API 没有"最近修改"入口。
- 因此“哪些笔记在基线之后被修改”**无法直接查询，只能全量枚举后自行筛选**。这是本 skill 的核心约束。
- 枚举方法与 flomo-tag-diaries 相同：`limit=50` + 日期区间二分，凡返回恰好 50 条的区间必须二分重查，直到所有子区间 <50。不传 tag/keywords 即覆盖全部笔记（tag 参数偶发返回空，勿用）。
- 音频 OSS 签名 URL 几小时内过期：**拿到 memo 的同一轮内必须跑 build_edited.py**。

## ⛔ Strict Constraints（与 flomo-tag-diaries 相同的铁律）

1. **逐字转储**：memo 正文一字不改，保留 flomo 自己的转义、链接、emoji、换行、错别字。
2. **禁止总结/改写/润色/翻译**，不写任何编者按。
3. 唯一允许的变换：删除 flomo 自动加在文首的 `#tag` 行（及其后一个空行）。
4. 每条笔记一个 `### YYYY-MM-DD HH:MM` 块（这里是**真实修改日期和时间**），不合并。
5. 只允许结构性包装：注明创建日期和扫描区间的大标题、修改时间小标题、`> 创建于：… ｜ 修改于：…`、`> 来源：…`、`> 音频：…`、`---`、`🎤 语音记录`/`📷 图片`。

## 本地状态与记录

所有数据保存在用户指定的 `<OUTPUT_DIR>`，定期运行时复用同一个目录：

- `.flomo_edited_baseline.json`：只保存统一基线，例如 `{"baseline_at": "2026-10-12T09:00:00+08:00"}`。
- `flomo-edited-memos-runs.csv`：每次成功运行追加一行，只有 `运行日期,改动笔记数` 两列。日期是脚本实际运行的北京时间日期；数量按本轮命中的笔记 ID 去重，与文件数、修改次数无关。同日多次运行保留多行。
- `<创建日期>-修改.md`：按创建日期分组的最新导出；同名文件覆盖写入。
- `audio/<创建日期>/`：语音附件，与 flomo-tag-diaries 共用命名，已存在则跳过下载。

没有基线时，首次完整扫描只建立基线，运行记录的改动数留空，表示“初始化”，不把全部存量笔记算作改动，也不误记为 0。后续完整扫描没有命中时记录 `0`，仍推进基线。

扫描、补全文、正文写入或运行记录失败时，保留原基线，不追加成功记录。音频下载沿用原有行为：失败警告，正文仍导出，汇报时列明失败项。

## Workflow

### Step 1 — 读取基线，记录扫描截止时间

读取 `<OUTPUT_DIR>/.flomo_edited_baseline.json`；不存在则本轮为初始化。现有基线损坏时应报告错误，不把它当成首次运行。

在第一次 API 请求**之前**取得北京时间完整 ISO 时间戳，作为 `scan_started_at`，例如：

```bash
python3 -c 'from datetime import datetime, timedelta, timezone; print(datetime.now(timezone(timedelta(hours=8))).isoformat())'
```

后面全程复用这个值，不用扫描结束时间替代。

### Step 2 — 全量枚举并筛选

用 `memo_search(start_date, end_date, limit=50)` 二分枚举 `[2000-01-01, scan_started_at 的北京时间日期]` 的所有笔记，只需 `id/created_at/updated_at`。**必须包含本轮当天和基线之后创建的笔记**，不能只查创建于基线之前的笔记。

枚举量大时可委派子 agent，将索引写到临时 JSON；它不是持久化的逐条基线。按 ID 去重后，筛选 `updated_at != created_at` 且 `baseline_at < updated_at <= scan_started_at` 的候选。首次初始化仍确认枚举完整，但不导出历史修改。

单天区间仍返回 50 条时，结果可能不完整：如无法确认已取全，汇报缺口，**不推进基线、不记录成功**。

### Step 3 — 补全候选笔记

用 `memo_batch_get(ids)` 获取命中笔记的最新全文（单次不超过 10 条）；若 `omitted_ids` 非空则补拉。重拉后再次检查时间条件；若最新修改已晚于本轮截止时间，留给下次扫描。

仍有缺失或正文被截断时，汇报失败，不标记完整、不推进基线。禁止把接口失败解释成“零条改动”。

### Step 4 — 导出、记录并推进基线（同一轮内！）

把完整候选保存为临时 `scan.json`：

```json
{
  "scan_started_at": "2026-10-19T09:00:00+08:00",
  "scan_complete": true,
  "memos": []
}
```

`memos` 填入真实的完整候选对象。只有确认全量枚举及补全成功后，才设置 `scan_complete: true`；初始化或真正没有命中时可以是空数组。这两种情况也必须运行脚本，才能留下记录。

从当前 skill 的目录执行（`scripts/build_edited.py` 相对于本 SKILL.md）：

```bash
python3 scripts/build_edited.py /tmp/flomo_edited_scan.json <OUTPUT_DIR>
```

脚本再次执行时间筛选、ID 去重和全文校验，按创建日期写 Markdown、下载音频，然后追加 CSV 记录并更新统一基线。旧的 `<JSON> <OUTPUT_DIR> <SCAN_DATE>` 调用方式由上述扫描对象替代。

### Step 5 — 汇报

汇报本轮基线与截止时间、改动笔记数、写出的文件及条数、音频情况和本地记录路径。首次说明“已建立基线，改动数未统计”；失败或结果不完整时说明基线未推进。有 `present_files` 工具时展示导出文件。

## Output Format（per file）

```markdown
## 2026-08-25 星期二（2026-10-12 09:00:00 至 2026-10-19 09:00:00 扫描发现修改）

### 2026-10-13 23:03

<cleaned memo body, leading #tag line removed>

> 创建于：2026-08-25 11:31 ｜ 修改于：2026-10-13 23:03
> 来源：[flomo 原文](https://v.flomoapp.com/mine/?memo_id=MjUzNDc2MDky)

---
```

- 文件名 `<创建日期>-修改.md`；大标题注明扫描区间；小标题是实际修改日期和时间；同一创建日期的多条按完整修改时间排序，避免跨周、跨月时只按时分排序。
- 语音条前缀 `🎤 语音记录`，音频链接在来源链接下一行。

## Resources

- `scripts/build_edited.py` — 筛选、去重、导出、音频下载、本地基线及运行记录。
- API 与正文清洗细节可参考相邻 skill 的 `../flomo-tag-diaries/references/flomo_api.md`；枚举范围和修改判断以本 skill 为准。
