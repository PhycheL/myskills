"""Metadata-only selection and successful-run bookkeeping; never exports memos."""
import csv
import hashlib
import io
import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

BEIJING_TZ = timezone(timedelta(hours=8))
BASELINE_FILE = ".flomo_edited_baseline.json"
RUNS_FILE = "flomo-edited-memos-runs.csv"
RUNS_HEADER = ["运行日期", "改动笔记数"]


def parse_dt(value):
    if not isinstance(value, str) or not re.match(
        r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}", value.strip()
    ):
        raise ValueError("时间必须包含完整日期和时分秒")
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        result = result.replace(tzinfo=BEIJING_TZ)
    return result.astimezone(BEIJING_TZ)


def metadata(memo):
    if not isinstance(memo, dict) or not isinstance(memo.get("id"), str) or not memo["id"]:
        raise ValueError("每条笔记必须有非空字符串 id")
    return {
        "id": memo["id"],
        "created_at": parse_dt(memo.get("created_at")).isoformat(),
        "updated_at": parse_dt(memo.get("updated_at")).isoformat(),
    }


def select_memos(memos, baseline, cutoff):
    latest = {}
    for memo in memos:
        item = metadata(memo)
        previous = latest.get(item["id"])
        if previous is None or parse_dt(item["updated_at"]) > parse_dt(previous["updated_at"]):
            latest[item["id"]] = item
    if baseline is None:
        return []
    selected = [
        item for item in latest.values()
        if parse_dt(item["updated_at"]) != parse_dt(item["created_at"])
        and baseline < parse_dt(item["updated_at"]) <= cutoff
    ]
    return sorted(selected, key=lambda item: (item["created_at"], item["updated_at"], item["id"]))


def load_baseline(output_dir):
    path = Path(output_dir) / BASELINE_FILE
    if not path.exists():
        return None
    state = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(state, dict):
        raise ValueError("基线文件必须包含 baseline_at")
    return parse_dt(state.get("baseline_at"))


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".flomo-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def write_json(path, value):
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def load_manifest(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1 or data.get("scan_complete") is not True:
        raise ValueError("清单无效或扫描不完整")
    if not isinstance(data.get("scan_id"), str) or not data["scan_id"]:
        raise ValueError("清单缺少 scan_id")
    if not isinstance(data.get("output_dir"), str) or not Path(data["output_dir"]).is_absolute():
        raise ValueError("清单缺少绝对输出目录")
    cutoff = parse_dt(data.get("scan_started_at"))
    baseline = parse_dt(data["baseline_at"]) if data.get("baseline_at") is not None else None
    if baseline is not None and cutoff <= baseline:
        raise ValueError("扫描截止时间必须晚于基线")
    if not isinstance(data.get("memos"), list):
        raise ValueError("清单的 memos 必须是数组")
    items = [metadata(item) for item in data["memos"]]
    if len({item["id"] for item in items}) != len(items):
        raise ValueError("清单中有重复笔记 ID")
    if items != select_memos(items, baseline, cutoff):
        raise ValueError("清单含有不符合时间条件的笔记或顺序无效")
    data["memos"] = items
    return data


def file_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_export_report(manifest, report_path):
    """Apply this workflow's selection rules to a generic export result."""
    if not manifest["memos"]:
        return 0
    if report_path is None:
        raise ValueError("有命中笔记时，必须提供导出结果报告")
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    if not isinstance(report, dict) or report.get("schema_version") != 1:
        raise ValueError("导出报告格式或输出目录不一致")
    report_output = report.get("output_dir")
    if not isinstance(report_output, str) or not Path(report_output).is_absolute() or Path(report_output).resolve() != Path(manifest["output_dir"]).resolve():
        raise ValueError("导出报告格式或输出目录不一致")
    expected = {}
    for item in manifest["memos"]:
        day = parse_dt(item["created_at"]).date().isoformat()
        expected.setdefault(day, {})[item["id"]] = item
    days = report.get("days")
    if not isinstance(days, dict) or set(days) != set(expected):
        raise ValueError("导出报告尚未覆盖全部创建日期")
    count = 0
    output = Path(manifest["output_dir"])
    cutoff = parse_dt(manifest["scan_started_at"])
    for day, selected in expected.items():
        record = days[day]
        if not isinstance(record, dict):
            raise ValueError("逐日导出报告格式无效")
        exported = record.get("memos")
        if not isinstance(exported, list):
            raise ValueError("报告缺少已导出笔记元数据")
        actual = [metadata(item) for item in exported]
        ids = [item["id"] for item in actual]
        if len(set(ids)) != len(ids) or set(ids) != set(selected):
            raise ValueError("已导出笔记 ID 与待导出清单不一致")
        for item in actual:
            original = selected[item["id"]]
            updated = parse_dt(item["updated_at"])
            if item["created_at"] != original["created_at"] or updated < parse_dt(original["updated_at"]):
                raise ValueError("已导出笔记时间与扫描清单不一致或不是最新版本")
            if updated > cutoff:
                raise ValueError("导出期间笔记再次修改，请重新扫描；本轮基线不推进")
        if not all(isinstance(item.get("has_voice"), bool) for item in exported):
            raise ValueError("导出报告缺少语音标记")
        has_voice = any(item["has_voice"] for item in exported)
        raw_name = day + "-修改.md"
        formal_name = day + "-修改-书面语.md" if has_voice else None
        if record.get("raw_file") != raw_name or record.get("formal_file") != formal_name:
            raise ValueError("导出文件名与本轮约定不一致")
        raw = output / raw_name
        if not raw.is_file() or record.get("raw_sha256") != file_digest(raw):
            raise ValueError("原始版缺失或已被改写")
        if has_voice:
            formal = output / formal_name
            if not formal.is_file():
                raise ValueError("语音书面语版尚未完成")
            if formal.stat().st_mtime_ns < raw.stat().st_mtime_ns:
                raise ValueError("语音书面语版早于本轮原始版，请按当前原始版重新生成")
            structural = re.compile(r"^(?:#{2,3} |>[ ](?:创建于：|来源：|音频：)|---$|[🎤📷])")
            raw_lines = [line for line in raw.read_text(encoding="utf-8").splitlines() if structural.match(line)]
            formal_lines = [line for line in formal.read_text(encoding="utf-8").splitlines() if structural.match(line)]
            if raw_lines != formal_lines:
                raise ValueError("语音书面语版的结构或元信息与原始版不一致")
        count += len(exported)
    return count


def record_success(output_dir, cutoff, count):
    log_path = Path(output_dir) / RUNS_FILE
    previous_log = log_path.read_text(encoding="utf-8") if log_path.exists() else None
    rows = list(csv.reader(io.StringIO(previous_log))) if previous_log is not None else [RUNS_HEADER]
    if not rows or rows[0] != RUNS_HEADER or any(len(row) != 2 for row in rows):
        raise ValueError("运行记录的列应为：运行日期、改动笔记数")
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerows(rows)
    writer.writerow([datetime.now(BEIJING_TZ).date().isoformat(), "" if count is None else count])
    atomic_write(log_path, buffer.getvalue())
    try:
        write_json(Path(output_dir) / BASELINE_FILE, {"baseline_at": cutoff.isoformat()})
    except Exception:
        if previous_log is None:
            log_path.unlink()
        else:
            atomic_write(log_path, previous_log)
        raise


def finish_scan(manifest_path, report_path=None):
    manifest = load_manifest(manifest_path)
    baseline = load_baseline(manifest["output_dir"])
    expected = parse_dt(manifest["baseline_at"]) if manifest["baseline_at"] is not None else None
    cutoff = parse_dt(manifest["scan_started_at"])
    if baseline != expected:
        raise ValueError("基线已改变；本轮可能已完成，不能重复记录或覆盖新基线")
    count = verify_export_report(manifest, report_path)
    record_success(manifest["output_dir"], cutoff, None if expected is None else count)
    return None if expected is None else count
