#!/usr/bin/env python3
"""Transcribe a local video with mlx_whisper and render a Markdown transcript."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path


def require_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise SystemExit(f"Missing required tool: {name}")
    return path


def run(cmd: list[str]) -> None:
    print("+ " + " ".join(cmd))
    subprocess.run(cmd, check=True)


def ffprobe_duration(video: Path) -> float | None:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video),
            ],
            check=True,
            text=True,
            capture_output=True,
        )
        return float(result.stdout.strip())
    except Exception:
        return None


def fmt_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes:02d}:{secs:02d}"


def load_transcript(json_path: Path) -> tuple[str, list[dict]]:
    with json_path.open(encoding="utf-8") as f:
        data = json.load(f)
    return data.get("text", "").strip(), data.get("segments", [])


def render_markdown(
    *,
    markdown_path: Path,
    title: str,
    source_url: str,
    video: Path,
    duration: float | None,
    model: str,
    language: str,
    transcript_text: str,
    segments: list[dict],
) -> None:
    lines: list[str] = []
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"> 来源视频：`{video.name}`  ")
    if source_url:
        lines.append(f"> 视频链接：{source_url}  ")
    if duration is not None:
        lines.append(f"> 视频时长：约 {duration / 60:.1f} 分钟  ")
    lines.append(f"> 转写模型：`{model}`  ")
    lines.append(f"> 语言：`{language}`  ")
    lines.append(f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    lines.append("说明：本文档由本地语音识别自动转写生成，可能包含识别误差；如内容涉及医疗、法律或金融，请勿将其作为专业建议。")
    lines.append("")
    lines.append("## 摘要")
    lines.append("")
    lines.append("请根据完整转写补充 3-5 句摘要。")
    lines.append("")
    lines.append("## 完整转写")
    lines.append("")

    if segments:
        for segment in segments:
            start = fmt_time(segment.get("start", 0))
            end = fmt_time(segment.get("end", 0))
            text = str(segment.get("text", "")).strip()
            if text:
                lines.append(f"- `{start}-{end}` {text}")
    elif transcript_text:
        lines.append(transcript_text)
    else:
        lines.append("_未识别到转写文本。_")

    lines.append("")
    lines.append("## 原始文件")
    lines.append("")
    lines.append(f"- 本地视频：`{video}`")
    lines.append(f"- 原始转写 JSON：`{markdown_path.parent / 'transcript_raw.json'}`")
    lines.append("")
    markdown_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path, help="Local video file")
    parser.add_argument("--out-dir", type=Path, default=None, help="Output directory")
    parser.add_argument("--source-url", default="", help="Original video URL")
    parser.add_argument("--title", default="", help="Markdown title")
    parser.add_argument("--language", default="zh", help="Whisper language")
    parser.add_argument("--model", default="mlx-community/whisper-large-v3-turbo")
    parser.add_argument("--initial-prompt", default="", help="Optional Whisper prompt")
    parser.add_argument("--transcript-json", type=Path, default=None, help="Render from an existing Whisper JSON")
    parser.add_argument("--force", action="store_true", help="Overwrite audio/transcript outputs")
    args = parser.parse_args()

    video = args.video.expanduser().resolve()
    if not video.exists():
        raise SystemExit(f"Video not found: {video}")

    require_tool("ffmpeg")
    require_tool("ffprobe")
    if not args.transcript_json:
        require_tool("mlx_whisper")

    job_id = video.stem
    out_dir = (args.out_dir or Path("output/transcribe") / job_id).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    duration = ffprobe_duration(video)
    audio = out_dir / "audio.wav"
    raw_json = out_dir / "transcript_raw.json"

    if args.transcript_json:
        source_json = args.transcript_json.expanduser().resolve()
        if not source_json.exists():
            raise SystemExit(f"Transcript JSON not found: {source_json}")
        if source_json != raw_json:
            raw_json.write_text(source_json.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        if args.force or not audio.exists():
            run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(video),
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-c:a",
                    "pcm_s16le",
                    str(audio),
                ]
            )

        if args.force or not raw_json.exists():
            cmd = [
                "mlx_whisper",
                str(audio),
                "--model",
                args.model,
                "--language",
                args.language,
                "--task",
                "transcribe",
                "--output-format",
                "all",
                "--output-name",
                "transcript_raw",
                "--output-dir",
                str(out_dir),
            ]
            if args.initial_prompt:
                cmd.extend(["--initial-prompt", args.initial_prompt])
            run(cmd)

    transcript_text, segments = load_transcript(raw_json)
    markdown_path = out_dir / f"{job_id}-transcript.md"
    render_markdown(
        markdown_path=markdown_path,
        title=args.title or job_id,
        source_url=args.source_url,
        video=video,
        duration=duration,
        model=args.model,
        language=args.language,
        transcript_text=transcript_text,
        segments=segments,
    )
    print(markdown_path)


if __name__ == "__main__":
    main()
