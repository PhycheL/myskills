# 按 ID 获取笔记

使用 flomo MCP 的 `memo_batch_get(ids)`，每批最多 10 个 ID。该调用只取回已确定的笔记，用于获取导出正文、补取直接关联的原文或刷新音频链接。

典型响应对象中的 `memos` 元素：

```json
{
  "id": "memo-id",
  "created_at": "2026-10-12T10:00:00+08:00",
  "updated_at": "2026-10-13T09:00:00+08:00",
  "content": "#流水账\n\n原始正文",
  "content_truncated": false,
  "url": "https://v.flomoapp.com/mine/?memo_id=memo-id",
  "has_voice": true,
  "has_image": false,
  "files": [{"type": "recorded", "url": "<本轮取得的音频签名链接>"}]
}
```

按 ID 核对响应与请求范围。对 `omitted_ids`、未返回的 ID 或 `content_truncated: true` 的笔记，单独补拉，最多两次。导出清单内的笔记仍不完整时汇报缺失，导出保持未完成；仅作为上下文补取的关联目标仍不完整时，保留原链接并告警，处理见 [associated-memos.md](associated-memos.md)。正文为空但包含媒体的导出笔记也需要保留。

音频签名链接会过期，获取全文后在同一轮运行 `build_day.py`。需要刷新时仍然使用相同 ID。
