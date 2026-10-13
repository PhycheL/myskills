---
name: flomo-edited-memos
metadata:
  agent_created: true
description: 用 Python 并发扫描 flomo，找出创建后被修改且修改时间晚于统一基线的笔记 ID，再交给 flomo-tag-diaries 导出。支持隔天、隔周、隔月运行，完成后记录运行日期和改动笔记数。
---

# Flomo Edited Memos（修改笔记筛选与导出调度）

## 分工与判断规则

Python **只确定需要导出哪些笔记**：读取基线、调用 flomo MCP、全量枚举、时间比较和 ID 去重，输出仅含 ID 与时间的清单。获取完整正文、原始版导出、音频下载和语音书面语版生成，全部使用相邻的 **flomo-tag-diaries** skill。

只保存一个统一的基线时间 `baseline_at`，两个条件同时满足才算命中：

```text
updated_at != created_at
并且 updated_at > baseline_at
```

比较完整时间戳，统一为北京时间（UTC+08:00）。不保存、比较每条笔记上次的 updated_at。基线之后新建又修改过的笔记也纳入，同一笔记修改多次算 1 条。

扫描器在请求开始前固定 `scan_started_at`，本轮只选择 `updated_at <= scan_started_at` 的笔记；更晚的修改留到下一轮。间隔一天、一周或一个月使用同一套规则。

## 依赖与 API 约束

- Python 3.10+ 与官方 MCP Python SDK 1.x；推荐用 `uv run`，脚本内声明了依赖。
- 默认读取 `~/.workbuddy/mcp.json` 中的 `flomo` HTTP MCP 配置，可用 `--mcp-config` 和 `--server` 指定其他配置。不把带认证信息的 URL 写入 skill、清单或日志。
- `memo_search` 日期参数过滤 **created_at**，没有按 updated_at 查询的入口。因此扫描 `[2000-01-01, 本轮北京时间日期]` 的全部笔记，包含当天与基线之后创建的笔记。
- 使用 `limit=50` 和日期区间二分：返回满 50 条就继续拆分；单日仍满 50 条时无法确认完整，报告失败。默认并发 3，可用 `--concurrency` 调整；请求超时、有限重试由脚本处理。
- flomo 只有最后修改时间，没有历史版本。只导出最新内容；正文编辑、改标签等都会刷新 updated_at。

## 工作流程

### 1. 运行 Python 筛选器

从当前 skill 目录执行：

```bash
SCAN_DIR=$(mktemp -d /tmp/flomo-edited.XXXXXX)
uv run scripts/scan_edited.py "<OUTPUT_DIR>" \
  --manifest "$SCAN_DIR/selection.json"
```

每轮使用新临时目录；扫描器拒绝覆盖已有清单。只有命令成功退出才能继续导出。记下本轮目录的绝对路径；后续如果换了终端调用，用实际路径替换 `$SCAN_DIR`，不假定 shell 变量会跨调用保留。

扫描器只调用 `memo_search`，丢弃正文和附件，仅保留元数据。全部日期区间成功后才写清单；扫描阶段不导出文件、不记录成功、不推进基线。

清单示例（实际由脚本生成，不手写）：

```json
{
  "schema_version": 1,
  "scan_id": "本轮唯一标识",
  "scan_complete": true,
  "baseline_at": "2026-10-12T09:00:00+08:00",
  "scan_started_at": "2026-10-19T09:00:00+08:00",
  "output_dir": "/实际输出目录",
  "memos": [
    {
      "id": "memo-id",
      "created_at": "2026-08-25T11:31:00+08:00",
      "updated_at": "2026-10-13T23:03:00+08:00"
    }
  ]
}
```

没有基线时首次完整扫描的 `baseline_at` 为 null、`memos` 为空，表示初始化，不把存量笔记算作改动。基线损坏或扫描不完整时停止，不能当作零条结果继续。

### 2. 使用 flomo-tag-diaries 按 ID 导出

有命中笔记时，从清单中提取普通 ID 列表，再读取相邻 `../flomo-tag-diaries/SKILL.md`，使用它的通用 ID 导出能力。

向它提供 ID 列表、输出目录以及以下通用导出选项：

- 文件名后缀 `-修改`（`--suffix=-修改`），与完整日记分开。
- 时间显示字段 `updated_at`（`--time-field updated_at`）。
- 日期标题元信息（`--title-note`）：本轮基线、截止时间及“扫描发现修改”。
- 本轮独立的结果报告路径（`--report "$SCAN_DIR/export.json"`）。

扫描清单、基线与本轮完成条件由本 skill 管理，不传给导出 skill，也不要求导出 skill 识别它们。导出 skill 仅根据普通 ID 列表获取全文，使用自身脚本导出原始版和音频，并按语音规则生成书面语版。

此处必须执行完整的 flomo-tag-diaries 流程，包括语音书面语版及 ASR 纠正汇报。不能用筛选脚本代替导出，也不能只运行原始版脚本后就宣布全部完成。

### 3. 全部完成后记录结果、推进基线

所有创建日期的原始版及需要的书面语版完成后执行：

```bash
python3 scripts/finish_scan.py "$SCAN_DIR/selection.json" \
  --report "$SCAN_DIR/export.json"
```

该步骤只做完成检查和状态记录，不获取或导出笔记。它根据导出报告检查全部 ID、实际导出时间、原始版完整性及必要书面语版的结构，书面语版必须在本轮原始版之后生成。然后追加运行记录并把基线推进到扫描开始时固定的截止时间。中途失败保留旧基线，重复完成同一轮会被拒绝，不重复记账。

首次初始化或确实零条命中时无需导出，直接运行 `finish_scan.py <清单>`（不传报告）。首次改动数留空，表示初始化；后续零条记 `0`。

如果实际导出的 updated_at 晚于本轮截止时间，完成检查会拒绝推进基线。由本 skill 使用新临时目录重新扫描后再导出，最多重来一次；仍有并发修改时报告未完成，保留旧基线。时间窗口的判断不交给导出 skill。

## 本地文件与汇报

定期运行复用同一 `<OUTPUT_DIR>`：

- `.flomo_edited_baseline.json`：仅保存一个 `baseline_at`。
- `flomo-edited-memos-runs.csv`：只有 `运行日期,改动笔记数` 两列；日期按完成脚本实际运行的北京时间日期，同日多次运行保留多行。
- `<创建日期>-修改.md`：flomo-tag-diaries 生成的原始版，同名覆盖，不覆盖完整日记 `<创建日期>.md`。
- `<创建日期>-修改-书面语.md`：有语音笔记的日期才生成，保留原始版结构和元信息，只清理语音正文。
- `audio/<创建日期>/`：沿用 flomo-tag-diaries 的音频仓库和命名。

汇报扫描范围、命中/导出数量、文件与记录路径、音频警告及 ASR 纠正；首次说明“已建立基线，改动数未统计”。扫描、正文导出或书面语版未完成时，不宣布成功、不推进基线。音频失败沿用 flomo-tag-diaries 的警告行为，并明确汇报。

## 资源

- `scripts/scan_edited.py`：MCP 并发扫描与 ID 清单生成。
- `scripts/scan_state.py`：本 skill 的元数据、时间判断、清单/导出结果检查与本地状态操作。
- `scripts/finish_scan.py`：导出完成后的记录与基线提交。
- `../flomo-tag-diaries/SKILL.md`：具体 memo 导出与语音书面语处理。
