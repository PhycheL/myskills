# GetNote 导出接口参考

## 时间与分页

- `getnote notes --created-from <RFC3339> --created-to <RFC3339> --page-size 100 --all -o json`
- `created` 是创建时间，`edited` 是正文编辑时间，`record-updated` 是更宽泛的记录更新时间；三者不能混用，`updated_at` 不等于正文编辑时间。
- `from` 包含、`to` 不包含。`--all` 遍历后仍须核对 `has_more`、`next_cursor`、`traversal.completed`、`traversal.pagination_verified`、`partial` 和 `warnings`。
- 列表只用于在内存中定位；`preview` 可能截断，不能作为全文，也不保存列表 JSON。ID 全程按字符串处理。

## 每条笔记的读取

| 数据 | 命令 | 使用方式 |
|---|---|---|
| 完整详情与笔记内容 | `getnote note <id> -o json` | 从 `data.note` 读取完整 `content`，确认 `content_state.content=full`；详情 JSON 不落盘 |
| 录音类原始转写 | `getnote note transcript <id> -o json` | 将 `data.transcript` 逐字保存为原始原文 TXT；响应 JSON 不落盘 |
| 非录音类原文 | `getnote note original <id> -o json` | 将 `data.original` 逐字保存为原始原文 TXT；响应 JSON 不落盘 |
| 录音附件 | `getnote note attachments <id> -o json` | 仅录音类调用，只下载 `type=audio`；附件 JSON 不落盘 |

所有命令先检查进程退出码和顶层 `success=true`。失败时保留 `error.message/reason/request_id` 供命令行错误摘要使用，不能把 HTTP 200 当作业务成功。CLI 已对已知只读瞬时 429 做有限重试，调用方不再叠加。

## 类型差异

录音类类型包括：

- `audio`
- `local_audio`
- `class_audio`
- `meeting`
- `internal_record`
- `recorder_audio`
- `recorder_flash_audio`

这些类型使用 `transcript` 保存原始转写，并要求至少存在一个音频附件。`original` 对部分录音类型可用，但会议等类型会明确不支持，因此统一走专用转写接口。

其他类型使用 `original`：

- `link`：网页抓取原文；`content` 是得到大脑笔记内容，不是原文。详情中的 `web_page.url` 单独保存为 `原始URL.txt`，不下载网页，也不保存详情 JSON。
- `ref`：被引用原文；用户评论和引用原文不能混淆。
- `plain_text`：用户正文。
- 未识别的新类型：仍先尝试 `original`，失败则如实记录，不用 `content` 补写原文。

## 保存边界与失败语义

核心保存内容只有原始原文、完整笔记内容、录音类的实际音频，以及链接类的原始 URL。任何核心步骤失败都会使命令行最终摘要为 `complete=false`。

不调用时间线、章节、标记、快捷笔记和会议待办接口，不下载非录音附件，不保存任何接口响应 JSON。接口使用 JSON 作为传输格式，不代表需要把 JSON 作为备份文件交付。

## 默认文件布局

```text
<输出目录>/
└── resource/
    ├── 11月04/
    │   ├── 08时37分36秒-标题-原始原文.txt
    │   ├── 08时37分36秒-标题-得到大脑笔记内容.md
    │   └── 08时37分36秒-标题-原始录音.mp3
    └── 11月05/
        ├── 09时00分00秒-链接标题-原始原文.txt
        ├── 09时00分00秒-链接标题-得到大脑笔记内容.md
        └── 09时00分00秒-链接标题-原始URL.txt
```

日期目录内保持平铺，输出根目录不生成列表或报告 JSON。多个录音使用 `-2`、`-3` 后缀。文件正文逐字保存；签名 URL 可能过期，录音必须与列表和详情在同一轮获取并下载。
