---
name: flomo-find-memos
description: 检索 flomo 在指定时间内新增或在基线之后修改的笔记，可选按标签筛选，输出去重的 memo ID 清单和数量。
metadata:
  agent_created: true
---

# Flomo 笔记检索

Python 负责 MCP 并发枚举、时间判断、标签筛选和去重。输入是检索条件，输出是符合条件的普通 ID 数组、数量和运行清单。

## 1. 确定检索条件

时间统一为北京时间，支持两种模式：

| 模式 | 入选规则 | 起止时间 |
| --- | --- | --- |
| `modified` 修改 | `updated_at != created_at`，且 `基线 < updated_at <= 本轮扫描开始时间` | 读取任务基线，或用 `--baseline` 指定独立查询的起点 |
| `created` 新增 | `开始时间 <= created_at < 结束时间` | 用 `--date` 指定已结束的自然日，或用 `--start-at`、`--end-at` 指定区间 |

新增模式只按创建时间入选，当天创建后又修改的笔记仍属于新增。修改模式也包含基线之后创建、随后又修改过的笔记，同一 ID 只计一次。

两个模式都接受可选的 `--tag`，按笔记当前 `tags` 精确匹配。标签可以带前导 `#`；省略时，对全体笔记应用时间条件。标签只用于筛选 ID。

运行时间与目标日期分别确定。比如凌晨 1 点的任务，要明确传入需要检查的日期；“当天”含义不明确时先确认日期，脚本不会自动推断昨天或最近 24 小时。

## 2. 执行检索

从当前 skill 目录运行。每轮使用新的清单路径；脚本会拒绝覆盖已有结果。

按已有基线检查修改，`STATE_DIR` 可沿用原来保存基线的目录：

```bash
uv run scripts/scan_memos.py --mode modified --state-dir "<STATE_DIR>" \
  --manifest "<本轮临时目录>/selection.json"
```

检查某日新增，并按指定标签筛选；全标签检索时省略 `--tag`：

```bash
uv run scripts/scan_memos.py --mode created --date 2026-10-12 --tag 流水账 \
  --manifest "<本轮临时目录>/selection.json"
```

独立修改检索可用 `--baseline "2026-10-12T09:00:00+08:00"` 替代 `--state-dir`。需要记录任务进度时也传入 `--state-dir`。检索本身只读状态。

脚本通过本地 MCP 配置连接 flomo，支持指定配置位置、服务名和并发数。依赖与 API 的完整性限制见 [references/retrieval.md](references/retrieval.md)；参数以 `scan_memos.py --help` 为准。

## 3. 交付清单

成功退出后产生两份文件：

- `selection.ids.json`：去重后的普通 ID 数组。
- `selection.json`：查询条件、扫描时间、ID 与创建/修改时间的运行清单，供任务收尾核对。

完成标准：全部查询区间取全、时间和可选标签筛选完成、ID 去重并写入文件。汇报检索模式、时间范围、标签范围、命中数量和 ID 文件路径。缺失响应或无法取全的区间按失败处理。

修改任务没有对应基线时，首次完整检索用于初始化，ID 数组为空；汇报“改动数未统计，基线待提交”，与已有基线下的零条结果区分。

用户只要求查看时，到此完成。需要记录本轮处理完成状态或提交初始化基线时，读取 [references/run-state.md](references/run-state.md)，按本 skill 的状态接口提交进度。
