"""Selection rules and progress tracking for completed memo batches."""
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


def normalize_tag(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("标签必须是字符串")
    tag = value.strip().removeprefix("#")
    if not tag or any(char.isspace() for char in tag):
        raise ValueError("标签不能为空或包含空白")
    return tag


def memo_tags(memo):
    tags = memo.get("tags")
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        raise ValueError("按标签检索需要每条笔记返回 tags 数组")
    return [normalize_tag(tag) for tag in tags]


def select_memos(memos, baseline, cutoff, mode="modified", tag=None):
    if mode not in ("modified", "created"):
        raise ValueError("未知检索模式")
    if mode == "created" and baseline is None:
        raise ValueError("新增检索需要明确的开始时间")
    tag = normalize_tag(tag)
    latest = {}
    for memo in memos:
        item = metadata(memo)
        if tag is not None:
            item["tags"] = memo_tags(memo)
        previous = latest.get(item["id"])
        if previous is None or parse_dt(item["updated_at"]) > parse_dt(previous["updated_at"]):
            latest[item["id"]] = item
    if baseline is None:
        return []
    selected = []
    for item in latest.values():
        created, updated = parse_dt(item["created_at"]), parse_dt(item["updated_at"])
        matches_time = (created != updated and baseline < updated <= cutoff) if mode == "modified" else baseline <= created < cutoff
        if matches_time and (tag is None or tag in item["tags"]):
            selected.append(metadata(item))
    return sorted(selected, key=lambda item: (item["created_at"], item["updated_at"], item["id"]))


def scope_paths(state_dir, mode="modified", tag=None):
    tag = normalize_tag(tag)
    suffix = "-" + hashlib.sha256(tag.encode("utf-8")).hexdigest()[:16] if tag else ""
    if mode == "modified":
        progress, runs = ".flomo_edited_baseline", "flomo-edited-memos-runs"
    elif mode == "created":
        progress, runs = ".flomo_created_windows", "flomo-created-memos-runs"
    else:
        raise ValueError("未知检索模式")
    root = Path(state_dir)
    return root / (progress + suffix + ".json"), root / (runs + suffix + ".csv")


def load_baseline(state_dir, tag=None):
    path, _ = scope_paths(state_dir, tag=tag)
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
    if not isinstance(data, dict) or data.get("schema_version") != 2 or data.get("scan_complete") is not True:
        raise ValueError("清单无效或扫描不完整")
    if not isinstance(data.get("scan_id"), str) or not data["scan_id"]:
        raise ValueError("清单缺少 scan_id")
    state_dir = data.get("state_dir")
    if state_dir is not None and (not isinstance(state_dir, str) or not Path(state_dir).is_absolute()):
        raise ValueError("状态目录必须是绝对路径")
    query = data.get("query")
    if not isinstance(query, dict) or query.get("mode") not in ("modified", "created"):
        raise ValueError("清单缺少检索条件")
    if normalize_tag(query.get("tag")) != query.get("tag"):
        raise ValueError("清单的标签格式无效")
    scanned = parse_dt(data.get("scan_started_at"))
    cutoff = parse_dt(query.get("end_at"))
    baseline = parse_dt(query["start_at"]) if query.get("start_at") is not None else None
    if cutoff > scanned or (query["mode"] == "modified" and cutoff != scanned):
        raise ValueError("查询截止时间与扫描开始时间不一致")
    if baseline is not None and cutoff <= baseline:
        raise ValueError("查询截止时间必须晚于开始时间")
    if not isinstance(data.get("memos"), list):
        raise ValueError("清单的 memos 必须是数组")
    items = [metadata(item) for item in data["memos"]]
    if len({item["id"] for item in items}) != len(items):
        raise ValueError("清单中有重复笔记 ID")
    if items != select_memos(items, baseline, cutoff, mode=query["mode"]):
        raise ValueError("清单含有不符合时间条件的笔记或顺序无效")
    data["memos"] = items
    return data


def verify_completion_report(manifest, report_path):
    """Check a caller-supplied completion record against IDs and query times."""
    if not manifest["memos"]:
        return 0
    if report_path is None:
        raise ValueError("有命中笔记时，必须提供本轮完成记录")
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    if not isinstance(report, dict) or report.get("schema_version") != 1 or report.get("complete") is not True:
        raise ValueError("本轮处理尚未确认完成")
    if not isinstance(report.get("memos"), list):
        raise ValueError("完成报告缺少笔记元数据")
    actual = [metadata(item) for item in report["memos"]]
    selected = {item["id"]: item for item in manifest["memos"]}
    ids = [item["id"] for item in actual]
    if len(set(ids)) != len(ids) or set(ids) != set(selected):
        raise ValueError("已完成笔记 ID 与检索清单不一致")
    for item in actual:
        original = selected[item["id"]]
        updated = parse_dt(item["updated_at"])
        if item["created_at"] != original["created_at"] or updated < parse_dt(original["updated_at"]):
            raise ValueError("已完成笔记时间与检索清单不一致或不是最新版本")
        if manifest["query"]["mode"] == "modified" and updated > parse_dt(manifest["query"]["end_at"]):
            raise ValueError("实际处理版本已超过检索截止时间，请重新检索；本轮基线不推进")
    return len(actual)


def record_success(state_dir, progress, count, mode="modified", tag=None):
    state_path, log_path = scope_paths(state_dir, mode, tag)
    header = RUNS_HEADER if mode == "modified" else ["运行日期", "新增笔记数"]
    previous_log = log_path.read_text(encoding="utf-8") if log_path.exists() else None
    rows = list(csv.reader(io.StringIO(previous_log))) if previous_log is not None else [header]
    if not rows or rows[0] != header or any(len(row) != 2 for row in rows):
        raise ValueError("运行记录的列与任务类型不一致")
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerows(rows)
    writer.writerow([datetime.now(BEIJING_TZ).date().isoformat(), "" if count is None else count])
    atomic_write(log_path, buffer.getvalue())
    try:
        write_json(state_path, progress)
    except Exception:
        if previous_log is None:
            log_path.unlink()
        else:
            atomic_write(log_path, previous_log)
        raise


def finish_scan(manifest_path, report_path=None):
    manifest = load_manifest(manifest_path)
    state_dir = manifest["state_dir"]
    if state_dir is None:
        raise ValueError("本次为独立检索，没有配置用于收尾记账的状态目录")
    query = manifest["query"]
    mode, tag = query["mode"], query.get("tag")
    initial = mode == "modified" and query["start_at"] is None
    if mode == "modified":
        baseline = load_baseline(state_dir, tag)
        expected = parse_dt(query["start_at"]) if not initial else None
        if baseline != expected:
            raise ValueError("基线已改变；本轮可能已完成，不能重复记录或覆盖新基线")
        progress = {"baseline_at": query["end_at"]}
    else:
        path, _ = scope_paths(state_dir, mode, tag)
        progress = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"completed_windows": []}
        if not isinstance(progress, dict) or not isinstance(progress.get("completed_windows"), list):
            raise ValueError("新增任务的完成窗口记录损坏")
        window = [query["start_at"], query["end_at"]]
        if window in progress["completed_windows"]:
            raise ValueError("这个新增时间窗口已完成，不能重复记录")
        progress["completed_windows"].append(window)
    count = verify_completion_report(manifest, report_path)
    record_success(state_dir, progress, None if initial else count, mode, tag)
    return None if initial else count
