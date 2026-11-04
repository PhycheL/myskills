#!/usr/bin/env python3
"""Export GetNote notes in a fixed time interval without rewriting their text."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

AUDIO_TYPES = {
    "audio",
    "local_audio",
    "class_audio",
    "meeting",
    "internal_record",
    "recorder_audio",
    "recorder_flash_audio",
}
BEIJING = timezone(timedelta(hours=8))
TIME_FLAGS = {
    "created": ("--created-from", "--created-to"),
    "edited": ("--edited-from", "--edited-to"),
    "record-updated": ("--record-updated-from", "--record-updated-to"),
}


class CliError(RuntimeError):
    pass


def parse_rfc3339(value):
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError(f"Time must include a timezone: {value}")
    return parsed


def run_cli(executable, args):
    result = subprocess.run(
        [executable, *args, "-o", "json"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    output = result.stdout.strip() or result.stderr.strip()
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        raise CliError(
            f"getnote {' '.join(args)} returned non-JSON output: "
            f"{output[:500]}"
        ) from exc
    if result.returncode != 0 or not payload.get("success"):
        error = payload.get("error") or {}
        message = error.get("message") or error.get("reason") or result.stderr or "unknown error"
        request_id = payload.get("request_id") or error.get("request_id")
        suffix = f" (request_id={request_id})" if request_id else ""
        raise CliError(f"getnote {' '.join(args)}: {message}{suffix}")
    return payload


def digest(path):
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def atomic_write_bytes(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".getnote-export-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_write_text(path, text):
    atomic_write_bytes(path, text.encode("utf-8"))


def file_info(path, root, kind):
    return {
        "kind": kind,
        "path": str(path.relative_to(root)),
        "bytes": path.stat().st_size,
        "sha256": digest(path),
    }


def save_text(path, text, root, kind):
    atomic_write_text(path, text)
    if path.read_text(encoding="utf-8") != text:
        raise RuntimeError(f"Text verification failed: {path}")
    return file_info(path, root, kind)


def safe_name(value, limit=90):
    value = re.sub(r"[\x00-\x1f\\/:*?\"<>|]", "-", value or "未命名")
    value = value.strip().rstrip(".") or "未命名"
    return value[:limit].rstrip()


def note_base(note, used_bases, day):
    created = parse_rfc3339(note["timestamps"]["created_at"]).astimezone(BEIJING)
    time_label = created.strftime("%H时%M分%S秒")
    title = note.get("title") or note.get("display_title") or "未命名"
    base = safe_name(f"{time_label}-{title}")
    candidate = base
    index = 2
    while (day, candidate) in used_bases:
        candidate = f"{base}-{index}"
        index += 1
    used_bases.add((day, candidate))
    return candidate


def extension_for(attachment):
    suffix = Path(unquote(urlparse(attachment["url"]).path)).suffix
    if re.fullmatch(r"\.[A-Za-z0-9]{1,10}", suffix):
        return suffix.lower()
    return ".mp3" if attachment.get("type") == "audio" else ".bin"


def download_attachment(attachment, path):
    request = urllib.request.Request(
        attachment["url"], headers={"User-Agent": "getnote-export-notes/1.0"}
    )
    temporary = path.with_name(path.name + ".part")
    try:
        with urllib.request.urlopen(request, timeout=180) as response, temporary.open("wb") as handle:
            shutil.copyfileobj(response, handle)
        if temporary.stat().st_size == 0:
            raise RuntimeError("Downloaded attachment is empty")
        with temporary.open("rb") as handle:
            prefix = handle.read(128).lstrip().lower()
        if prefix.startswith(b"<html") or prefix.startswith(b"<!doctype") or prefix.startswith(b"{\"success\""):
            raise RuntimeError("Downloaded attachment is an API/HTML response, not media")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def list_notes(executable, start, end, time_field):
    from_flag, to_flag = TIME_FLAGS[time_field]
    return run_cli(
        executable,
        ["notes", from_flag, start, to_flag, end, "--page-size", "100", "--all"],
    )


def validate_listing(payload):
    data = payload.get("data") or {}
    notes = data.get("notes")
    problems = []
    if not isinstance(notes, list):
        return ["List response does not contain data.notes[]"], []
    if data.get("has_more") or data.get("next_cursor"):
        problems.append("Pagination is incomplete")
    traversal = data.get("traversal") or {}
    if traversal and (
        traversal.get("completed") is not True
        or traversal.get("pagination_verified") is not True
    ):
        problems.append("Traversal did not complete with verified pagination")
    if data.get("partial") is True:
        problems.append("List response is partial")
    if data.get("warnings"):
        problems.append(f"List response contains warnings: {data['warnings']}")
    ids = [str(note.get("note_id") or "") for note in notes]
    if any(not note_id for note_id in ids):
        problems.append("A listed note has no note_id")
    if len(ids) != len(set(ids)):
        problems.append("List response contains duplicate note IDs")
    total = data.get("total")
    if isinstance(total, int) and total != len(notes):
        problems.append(f"List total {total} differs from returned count {len(notes)}")
    return problems, notes


def main():
    parser = argparse.ArgumentParser(
        description="Export complete GetNote source material for a fixed RFC3339 interval."
    )
    parser.add_argument("--from", dest="start", required=True, help="Inclusive RFC3339 start")
    parser.add_argument("--to", dest="end", required=True, help="Exclusive RFC3339 end")
    parser.add_argument("--output", required=True, help="Backup root directory")
    parser.add_argument(
        "--time-field",
        choices=tuple(TIME_FLAGS),
        default="created",
        help="Filter timeline; files are always grouped by created date",
    )
    parser.add_argument(
        "--date-style",
        choices=("month-day", "iso"),
        default="month-day",
        help="Date directory name: 11月04 or 2026-11-04",
    )
    parser.add_argument(
        "--cli",
        default=os.environ.get("GETNOTE_CLI", "getnote"),
        help="GetNote CLI executable, primarily for testing",
    )
    args = parser.parse_args()
    start_dt = parse_rfc3339(args.start)
    end_dt = parse_rfc3339(args.end)
    if start_dt >= end_dt:
        parser.error("--from must be earlier than --to")

    root = Path(args.output).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    report = {
        "complete": False,
        "range": {"from": args.start, "to": args.end, "time_field": args.time_field},
        "errors": [],
        "notes": [],
    }

    try:
        listing = list_notes(args.cli, args.start, args.end, args.time_field)
        listing_problems, notes = validate_listing(listing)
        report["errors"].extend(
            {"step": "list", "message": problem} for problem in listing_problems
        )
    except Exception as exc:
        report["errors"].append({"step": "list", "message": str(exc)})
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2

    notes.sort(key=lambda note: (note["timestamps"]["created_at"], str(note["note_id"])))
    used_bases = set()
    for listed in notes:
        note_id = str(listed["note_id"])
        record = {
            "note_id": note_id,
            "title": listed.get("title") or listed.get("display_title"),
            "note_type": listed.get("note_type"),
            "created_at": listed.get("timestamps", {}).get("created_at"),
            "files": [],
            "errors": [],
        }
        report["notes"].append(record)

        def fail(step, exc):
            problem = {"step": step, "note_id": note_id, "message": str(exc)}
            record["errors"].append(problem)
            report["errors"].append(problem)

        try:
            detail_payload = run_cli(args.cli, ["note", note_id])
            note = (detail_payload.get("data") or {})["note"]
            if str(note.get("note_id")) != note_id:
                raise RuntimeError("Detail response note_id does not match the requested ID")
            record["title"] = note.get("title") or note.get("display_title") or "未命名"
            record["note_type"] = note.get("note_type")
            record["created_at"] = note["timestamps"]["created_at"]
            created = parse_rfc3339(record["created_at"]).astimezone(BEIJING)
            day = created.strftime("%m月%d" if args.date_style == "month-day" else "%Y-%m-%d")
            record["day"] = day
            directory = root / "resource" / day
            directory.mkdir(parents=True, exist_ok=True)
            base = note_base(note, used_bases, day)
            prefix = directory / base
        except Exception as exc:
            fail("detail", exc)
            continue

        for field, state in (note.get("metadata_state") or {}).items():
            if state == "partial":
                fail("metadata", f"metadata_state.{field}=partial")

        try:
            content = note.get("content")
            if not isinstance(content, str):
                raise RuntimeError("Converted content is missing")
            record["files"].append(
                save_text(
                    Path(f"{prefix}-得到大脑笔记内容.md"),
                    content,
                    root,
                    "converted_content",
                )
            )
            if (note.get("content_state") or {}).get("content") != "full":
                raise RuntimeError(
                    f"Converted content is not full: {note.get('content_state')}"
                )
        except Exception as exc:
            fail("converted_content", exc)

        if note["note_type"] == "link":
            try:
                source_url = (note.get("web_page") or {}).get("url")
                if not isinstance(source_url, str) or not source_url.strip():
                    raise RuntimeError("Link source URL is missing")
                record["files"].append(
                    save_text(Path(f"{prefix}-原始URL.txt"), source_url, root, "source_url")
                )
            except Exception as exc:
                fail("source_url", exc)

        try:
            if note["note_type"] in AUDIO_TYPES:
                original_payload = run_cli(args.cli, ["note", "transcript", note_id])
            else:
                original_payload = run_cli(args.cli, ["note", "original", note_id])
            original_key = "transcript" if note["note_type"] in AUDIO_TYPES else "original"
            original = (original_payload.get("data") or {})[original_key]
            if not isinstance(original, str):
                raise RuntimeError("Original text is missing")
            record["files"].append(
                save_text(Path(f"{prefix}-原始原文.txt"), original, root, "original")
            )
        except Exception as exc:
            fail("original", exc)

        if note["note_type"] in AUDIO_TYPES:
            try:
                attachments_payload = run_cli(args.cli, ["note", "attachments", note_id])
                attachments = (attachments_payload.get("data") or {})["attachments"]
                if not isinstance(attachments, list):
                    raise RuntimeError("Attachment response is not a list")
                audio_attachments = [item for item in attachments if item.get("type") == "audio"]
                if not audio_attachments:
                    raise RuntimeError("Recording note has no audio attachment")
                for count, attachment in enumerate(audio_attachments, start=1):
                    try:
                        if not isinstance(attachment.get("url"), str):
                            raise RuntimeError("Audio attachment has no download URL")
                        suffix = "" if count == 1 else f"-{count}"
                        path = Path(f"{prefix}-原始录音{suffix}{extension_for(attachment)}")
                        download_attachment(attachment, path)
                        record["files"].append(file_info(path, root, "audio"))
                    except Exception as exc:
                        fail("audio_download", exc)
            except Exception as exc:
                fail("attachments", exc)

        record["status"] = "complete" if not record["errors"] else "incomplete"

    all_files = [item for record in report["notes"] for item in record["files"]]
    report["counts"] = {
        "listed_notes": len(notes),
        "complete_notes": sum(record.get("status") == "complete" for record in report["notes"]),
        "originals": sum(item["kind"] == "original" for item in all_files),
        "converted_contents": sum(item["kind"] == "converted_content" for item in all_files),
        "audio_files": sum(item["kind"] == "audio" for item in all_files),
        "source_urls": sum(item["kind"] == "source_url" for item in all_files),
    }
    report["complete"] = not report["errors"]
    summary = {
        "complete": report["complete"],
        "counts": report["counts"],
        "errors": len(report["errors"]),
        "output": str(root),
    }
    if report["errors"]:
        summary["error_details"] = report["errors"]
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if report["complete"] else 2


if __name__ == "__main__":
    sys.exit(main())
