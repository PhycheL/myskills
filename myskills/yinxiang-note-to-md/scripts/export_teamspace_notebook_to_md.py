#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import re
import shutil
import sqlite3
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path


CONTAINER_ROOT = Path.home() / "Library/Containers/com.yinxiang.Mac/Data/Library/Application Support/com.yinxiang.Mac/accounts/app.yinxiang.com"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export a synced Yinxiang teamspace notebook to Markdown."
    )
    parser.add_argument("--db", type=Path, help="Path to LocalNoteStore.sqlite")
    parser.add_argument(
        "--list-teamspace-notebooks",
        action="store_true",
        help="List available teamspace notebooks and exit",
    )
    parser.add_argument(
        "--notebook-guid",
        help="GUID from ZENTEAMSPACENOTEBOOK.ZGUID for the target notebook",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Directory where Markdown files will be written",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Export only the first N notes for smoke testing",
    )
    return parser.parse_args()


def find_db(explicit: Path | None) -> Path:
    if explicit:
        if not explicit.exists():
            raise FileNotFoundError(f"database not found: {explicit}")
        return explicit

    matches = sorted(CONTAINER_ROOT.glob("*/localNoteStore/LocalNoteStore.sqlite"))
    if not matches:
        raise FileNotFoundError("could not locate LocalNoteStore.sqlite under Yinxiang container")
    return max(matches, key=lambda p: p.stat().st_mtime)


def account_root_from_db(db_path: Path) -> Path:
    return db_path.parents[1]


def sanitize_component(text: str, limit: int = 32) -> str:
    text = re.sub(r"\s+", "_", text.strip())
    text = text.replace("/", "／").replace(":", "：")
    text = re.sub(r"[^\w\u4e00-\u9fff\-_.()（）]+", "_", text, flags=re.UNICODE)
    text = re.sub(r"_+", "_", text).strip("._")
    if not text:
        text = "note"
    return text[:limit].rstrip("._") or "note"


def ensure_pandoc() -> None:
    if shutil.which("pandoc") is None:
        raise RuntimeError("pandoc is required but was not found in PATH")


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def list_teamspace_notebooks(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT ZGUID, ZNAME, ZSPACEID, ZNOTECOUNT
        FROM ZENTEAMSPACENOTEBOOK
        WHERE ZISACTIVE = 1 OR ZISACTIVE IS NULL
        ORDER BY ZNAME
        """
    ).fetchall()


def load_notes(conn: sqlite3.Connection, notebook_guid: str, limit: int | None) -> list[sqlite3.Row]:
    sql = """
        SELECT n.Z_PK AS pk, n.ZTITLE AS title, n.ZGUID AS guid, n.ZLOCALUUID AS localuuid
        FROM ZENNOTE n
        JOIN ZENTEAMSPACENOTE sn ON sn.ZNOTE = n.Z_PK
        WHERE sn.ZNOTEBOOKID = ?
        ORDER BY n.ZDATEUPDATED DESC
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql, (notebook_guid,)).fetchall()
    if not rows:
        raise RuntimeError(f"no notes found for notebook guid: {notebook_guid}")
    return rows


def load_resources(conn: sqlite3.Connection, note_ids: list[int]) -> dict[int, list[dict]]:
    if not note_ids:
        return {}
    placeholders = ",".join("?" for _ in note_ids)
    rows = conn.execute(
        f"""
        SELECT ZNOTE AS note_pk, ZGUID AS guid, ZLOCALUUID AS localuuid, ZFILENAME AS filename,
               ZMIME AS mime, lower(hex(ZDATAHASH)) AS datahash
        FROM ZENRESOURCE
        WHERE ZNOTE IN ({placeholders})
        ORDER BY ZNOTE, Z_PK
        """,
        note_ids,
    ).fetchall()
    grouped: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["note_pk"]].append(dict(row))
    return grouped


def source_file_for_resource(content_dir: Path, localuuid: str) -> Path | None:
    for candidate in sorted(content_dir.glob(f"{localuuid}.*")):
        if candidate.suffix.lower() in {".en-reco", ".en-pdf-text"}:
            continue
        if candidate.is_file():
            return candidate
    return None


def prepare_resource_map(
    content_root: Path,
    note: sqlite3.Row,
    resources: list[dict],
    output_dir: Path,
    stem: str,
) -> tuple[dict[str, dict], list[dict]]:
    note_dir = content_root / note["localuuid"]
    assets_dir = output_dir / "assets" / stem
    assets_dir.mkdir(parents=True, exist_ok=True)

    mapping: dict[str, dict] = {}
    exported: list[dict] = []

    for idx, resource in enumerate(resources, start=1):
        src = source_file_for_resource(note_dir, resource["localuuid"])
        if src is None:
            continue

        raw_name = resource["filename"] or f"resource_{idx}{src.suffix.lower()}"
        if "." in raw_name:
            base, ext = raw_name.rsplit(".", 1)
            ext = "." + ext
        else:
            base, ext = raw_name, src.suffix.lower()

        safe_name = sanitize_component(base, 48) + ext
        dest = assets_dir / safe_name
        counter = 2
        while dest.exists() and dest.stat().st_size != src.stat().st_size:
            dest = assets_dir / f"{sanitize_component(base, 40)}_{counter}{ext}"
            counter += 1

        if not dest.exists():
            shutil.copy2(src, dest)

        enriched = dict(resource)
        enriched.update(
            {
                "src": src,
                "dest": dest,
                "rel": dest.relative_to(output_dir).as_posix(),
                "display_name": dest.name,
            }
        )
        mapping[resource["datahash"]] = enriched
        exported.append(enriched)

    return mapping, exported


def parse_attrs(tag: str) -> dict[str, str]:
    return {
        match.group(1).lower(): html.unescape(match.group(2))
        for match in re.finditer(r'(\w+)="([^"]*)"', tag)
    }


def convert_enml_to_markdown(
    enml_path: Path,
    title: str,
    resource_map: dict[str, dict],
    attachments: list[dict],
) -> str:
    raw = enml_path.read_text(encoding="utf-8")

    def replace_media(match: re.Match[str]) -> str:
        attrs = parse_attrs(match.group(0))
        resource = resource_map.get(attrs.get("hash", "").lower())
        if resource is None:
            return "[缺失附件]"
        href = html.escape(resource["rel"], quote=True)
        label = html.escape(resource.get("filename") or resource["display_name"])
        if (resource.get("mime") or "").startswith("image/"):
            return f'<img src="{href}" alt="{label}" />'
        return f'<a href="{href}">{label}</a>'

    def replace_todo(match: re.Match[str]) -> str:
        attrs = parse_attrs(match.group(0))
        return "[x] " if attrs.get("checked") == "true" else "[ ] "

    def replace_codeblock(match: re.Match[str]) -> str:
        inner = match.group(2)
        text_only = html.unescape(re.sub(r"<[^>]+>", "", inner)).replace("\xa0", " ")
        return f"<pre><code>{html.escape(text_only)}</code></pre>"

    def replace_blockquote(match: re.Match[str]) -> str:
        return f"<blockquote>{match.group(2)}</blockquote>"

    raw = re.sub(r"<!DOCTYPE[^>]*>", "", raw, flags=re.I)
    raw = re.sub(r"<en-note[^>]*>", "<div>", raw, flags=re.I)
    raw = re.sub(r"</en-note>", "</div>", raw, flags=re.I)
    raw = re.sub(r"<en-media\b[^>]*/>", replace_media, raw, flags=re.I)
    raw = re.sub(r"<en-todo\b[^>]*/>", replace_todo, raw, flags=re.I)
    raw = re.sub(
        r'<div([^>]*)style="[^"]*--en-codeblock:true[^"]*"[^>]*>(.*?)</div>',
        replace_codeblock,
        raw,
        flags=re.I | re.S,
    )
    raw = re.sub(
        r'<div([^>]*)style="[^"]*--en-blockquote:true[^"]*"[^>]*>(.*?)</div>',
        replace_blockquote,
        raw,
        flags=re.I | re.S,
    )
    raw = re.sub(r'<img\b[^>]*src="data:image[^"]*"[^>]*>', "", raw, flags=re.I)
    raw = re.sub(r"<a\b([^>]*)>\s*</a>", "", raw, flags=re.I | re.S)
    raw = re.sub(r'\srev="[^"]*"', "", raw, flags=re.I)
    raw = re.sub(r'\starget="[^"]*"', "", raw, flags=re.I)
    raw = re.sub(r'\sstyle="[^"]*"', "", raw, flags=re.I)
    raw = re.sub(r'\s(?:class|width|height|data-[\w-]+)="[^"]*"', "", raw, flags=re.I)
    raw = re.sub(r"<div\b[^>]*>", "<p>", raw, flags=re.I)
    raw = re.sub(r"</div>", "</p>", raw, flags=re.I)
    raw = raw.replace("\xa0", " ")

    html_doc = f"<html><head><meta charset='utf-8'></head><body>{raw}</body></html>"
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as tmp:
        tmp.write(html_doc)
        html_path = Path(tmp.name)

    try:
        proc = subprocess.run(
            ["pandoc", "-f", "html", "-t", "gfm", "--wrap=none", str(html_path)],
            capture_output=True,
            text=True,
            check=True,
        )
    finally:
        html_path.unlink(missing_ok=True)

    markdown = proc.stdout.replace("\xa0", " ").strip()
    markdown = re.sub(r"(?m)^\s*\\\s*$", "", markdown)
    markdown = re.sub(r"\n{3,}", "\n\n", markdown).strip()
    markdown = markdown.replace("\\. ", ". ")

    parts = [f"# {title}"]
    non_images = [item for item in attachments if not (item.get("mime") or "").startswith("image/")]
    if non_images:
        parts.append("## 附件")
        parts.extend(f"- [{item['display_name']}]({item['rel']})" for item in non_images)

    if len(non_images) == 1 and markdown == f"[{non_images[0]['display_name']}]({non_images[0]['rel']})":
        markdown = ""

    if markdown:
        parts.append(markdown)

    return "\n\n".join(parts).strip() + "\n"


def export_notebook(db_path: Path, notebook_guid: str, output_dir: Path, limit: int | None) -> None:
    ensure_pandoc()
    output_dir.mkdir(parents=True, exist_ok=True)

    conn = connect(db_path)
    try:
        notes = load_notes(conn, notebook_guid, limit)
        resources_by_note = load_resources(conn, [row["pk"] for row in notes])
    finally:
        conn.close()

    content_root = account_root_from_db(db_path) / "content"
    index_lines = ["# 笔记导出", ""]

    for idx, note in enumerate(notes, start=1):
        short_title = sanitize_component(note["title"], 24)
        stem = f"{idx:02d}-{note['pk']}-{short_title}"
        enml_path = content_root / note["localuuid"] / "content.enml"
        if not enml_path.exists():
            raise FileNotFoundError(f"missing ENML for note {note['title']}: {enml_path}")

        resource_map, attachments = prepare_resource_map(
            content_root=content_root,
            note=note,
            resources=resources_by_note.get(note["pk"], []),
            output_dir=output_dir,
            stem=stem,
        )
        markdown = convert_enml_to_markdown(enml_path, note["title"], resource_map, attachments)
        md_name = stem + ".md"
        (output_dir / md_name).write_text(markdown, encoding="utf-8")
        index_lines.append(f"- [{note['title']}]({md_name})")

    (output_dir / "README.md").write_text("\n".join(index_lines).strip() + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    db_path = find_db(args.db)

    conn = connect(db_path)
    try:
        if args.list_teamspace_notebooks:
            notebooks = list_teamspace_notebooks(conn)
            for row in notebooks:
                print(f"{row['ZGUID']}\t{row['ZNAME']}\tspace={row['ZSPACEID']}\tcount={row['ZNOTECOUNT']}")
            return 0
    finally:
        conn.close()

    if not args.notebook_guid:
        raise SystemExit("--notebook-guid is required unless --list-teamspace-notebooks is used")
    if not args.output_dir:
        raise SystemExit("--output-dir is required when exporting notes")

    export_notebook(db_path, args.notebook_guid, args.output_dir, args.limit)
    print(f"exported notebook {args.notebook_guid} -> {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
