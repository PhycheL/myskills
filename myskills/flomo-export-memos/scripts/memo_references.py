"""Recognize explicit flomo associations and preserve their quoted text."""
import hashlib
import re
from urllib.parse import parse_qs, urlsplit


ASSOCIATION = re.compile(r"^ {0,3}关联自[：:][ \t]*(.+?)[ \t]*$")
LINK = re.compile(r"(?:<([^<>]+)>|\[[^\]\n]*\]\(([^()]+)\)|(https?://\S+))")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
EXPANDED = re.compile(
    r"^\n关联笔记：\[查看原文\]\([^\n)]+\)\n\n(?:>[^\n]*\n)+", re.MULTILINE
)


def prose_lines(text):
    """Yield line positions outside fenced code."""
    offset, fence = 0, None
    for line_with_end in text.splitlines(keepends=True):
        line = line_with_end.rstrip("\r\n")
        marker = FENCE.match(line)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= fence[1] and not marker[2].strip():
                fence = None
        elif marker:
            fence = (marker[1][0], len(marker[1]))
        else:
            yield offset, line
        offset += len(line_with_end)


def find_references(text):
    """Find standalone association lines outside quotes and fenced code."""
    found = []
    for offset, line in prose_lines(text):
        association = ASSOCIATION.fullmatch(line)
        link = LINK.fullmatch(association[1]) if association else None
        if link:
            url = next(value for value in link.groups() if value).replace("\\_", "_")
            parsed = urlsplit(url)
            ids = parse_qs(parsed.query).get("memo_id", [])
            if (parsed.scheme in ("http", "https") and parsed.netloc == "v.flomoapp.com"
                    and parsed.path.rstrip("/") == "/mine" and len(ids) == 1
                    and re.fullmatch(r"[A-Za-z0-9_-]+=*", ids[0])):
                found.append({"start": offset, "end": offset + len(line),
                              "line": line, "target_id": ids[0], "url": url})
    return found


def reference_inputs(references):
    return [{key: item[key] for key in ("line", "target_id", "url")} for item in references]


def text_digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def render_reference(reference, body):
    quoted = "\n".join("> " + line if line else ">" for line in body.split("\n"))
    # Surround the insertion with blank lines, even when the source had none.
    return f"\n关联笔记：[查看原文]({reference['url']})\n\n{quoted}\n"


def restore_references(text, references, results):
    """Check quoted blocks, then restore source lines for structural checks."""
    if not isinstance(results, list) or len(results) != len(references):
        raise ValueError("关联笔记尚未处理，请先生成整理版底稿")
    expanded = []
    for reference, result in zip(references, results):
        if not isinstance(result, dict) or result.get("status") not in ("expanded", "unavailable"):
            raise ValueError("关联处理记录无效")
        if result["status"] == "expanded":
            expanded.append((reference, result))
        elif not result.get("reason"):
            raise ValueError("关联未展开时需要记录原因")
    prose_positions = {offset for offset, _ in prose_lines(text)}
    blocks = [block for block in EXPANDED.finditer(text) if block.start() in prose_positions]
    if len(blocks) != len(expanded):
        raise ValueError("整理版的关联引用缺失或多出")
    pieces, cursor = [], 0
    for block, (reference, result) in zip(blocks, expanded):
        if text_digest(block[0]) != result.get("sha256"):
            raise ValueError("整理版的关联原文引用已被改写")
        pieces.extend((text[cursor:block.start()], reference["line"]))
        cursor = block.end()
    pieces.append(text[cursor:])
    restored = "".join(pieces)
    if reference_inputs(find_references(restored)) != references:
        raise ValueError("整理版的关联链接或顺序与原始版不一致")
    return restored
