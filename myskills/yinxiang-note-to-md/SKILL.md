---
name: yinxiang-note-to-md
description: Export notes from Yinxiang Biji or Evernote local caches into Markdown files with copied image or PDF attachments. Use when the user wants to batch-convert a Yinxiang notebook, a teamspace notebook, or cached local notes into `.md` files, especially on macOS where the Yinxiang desktop app has already synced the content locally.
---

# Yinxiang Note To Md

## Overview

Use this skill to export a synced 印象笔记/印象团队空间笔记本 from the local macOS cache into Markdown files. Prefer the bundled script instead of re-deriving the SQL, cache paths, resource mapping, and ENML-to-Markdown conversion logic each time.

## Workflow

1. Confirm the target notebook has already been synced locally by the 印象笔记客户端.
2. If the notebook GUID is unknown, list available teamspace notebooks with the bundled script.
3. Run the export script with the notebook GUID and output directory.
4. Verify the generated `README.md`, `.md` files, and `assets/` directory.
5. If needed, spot-check a few notes that contain images, PDFs, or imported web content.

## Quick Start

List available teamspace notebooks:

```bash
python3 scripts/export_teamspace_notebook_to_md.py --list-teamspace-notebooks
```

Export a notebook:

```bash
python3 scripts/export_teamspace_notebook_to_md.py \
  --notebook-guid 18974c67-d279-468e-9929-0d8e574c454a \
  --output-dir "/Users/you/Documents/印象笔记导出/云端数据同步/md"
```

Smoke-test only a few notes:

```bash
python3 scripts/export_teamspace_notebook_to_md.py \
  --notebook-guid 18974c67-d279-468e-9929-0d8e574c454a \
  --output-dir /tmp/yinxiang-md \
  --limit 3
```

## When To Use The Script

Use `scripts/export_teamspace_notebook_to_md.py` when:

- the user asks to export a whole 团队空间笔记本 to Markdown
- the user already has the desktop app open and synced
- the target notes exist in the local SQLite cache and `content/<LOCALUUID>/content.enml`
- the export should preserve inline images and copy non-image attachments

## Operational Notes

- The script auto-discovers the local Yinxiang account database under `~/Library/Containers/com.yinxiang.Mac/.../localNoteStore/LocalNoteStore.sqlite`.
- It targets `ZENTEAMSPACENOTEBOOK` and `ZENTEAMSPACENOTE`, so it is designed for teamspace notebooks rather than arbitrary personal notebooks.
- It converts ENML to Markdown with `pandoc`. If `pandoc` is unavailable, install it or stop and report the blocker.
- It copies discovered resources into `assets/<note-stem>/`.
- It writes one Markdown file per note and a top-level `README.md` index.

## Verification

After export, check:

- the output directory exists
- `README.md` links to all exported notes
- image-heavy notes render local image links under `assets/`
- PDF notes include a relative attachment link
- note count roughly matches the notebook note count when `--limit` is not used

## Failure Handling

- If no database is found, ensure the user has opened the macOS client and waited for sync.
- If the notebook GUID cannot be found, run the list command first and match by notebook name.
- If notes export but formatting is imperfect, keep the generated Markdown and report the specific note types that need post-processing instead of discarding the batch result.

## Resources

### scripts/export_teamspace_notebook_to_md.py

Use this script as the default implementation. Only replace it with manual SQL plus ad-hoc conversion when the environment is unusual and the script cannot be adapted quickly.
