#!/usr/bin/env python3
"""Bilibili audio -> Silero VAD clips -> SiliconFlow ASR."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from bilibili_downloader import dependency_error, download_video, human_bytes


APP_DIR = Path(__file__).resolve().parent
VAD_PYTHON = Path(sys.executable)
VAD_SCRIPT = APP_DIR / "vad_split.py"
ASR_SCRIPT = APP_DIR / "siliconflow_asr.py"
AUDIO_SUFFIXES = {".mp3", ".m4a", ".wav", ".flac", ".aac", ".ogg", ".opus"}


def configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


def make_job_dir(output: Path | None) -> Path:
    if output is None:
        output = APP_DIR / "输出" / datetime.now().strftime("任务_%Y%m%d_%H%M%S")
    output = output.expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        raise RuntimeError(f"输出目录不是空的，已停止：{output}")
    output.mkdir(parents=True, exist_ok=True)
    return output


def find_audio(folder: Path) -> list[Path]:
    return sorted(
        (
            path.resolve()
            for path in folder.iterdir()
            if path.is_file() and path.suffix.lower() in AUDIO_SUFFIXES
        ),
        key=lambda path: path.name.lower(),
    )


def progress(data: dict[str, Any]) -> None:
    if data.get("status") == "downloading":
        downloaded = data.get("downloaded_bytes", 0)
        total = data.get("total_bytes") or data.get("total_bytes_estimate")
        percent = downloaded / total * 100 if total else 0
        print(
            f"\r下载：{percent:5.1f}%  {human_bytes(data.get('speed'))}/s  "
            f"剩余 {data.get('eta', '--')} 秒",
            end="",
            flush=True,
        )
    elif data.get("status") == "finished":
        print("\n单个音频下载完成，正在整理……", flush=True)


def run_stage(command: list[str], stage: str, environment: dict[str, str]) -> None:
    print(f"\n===== {stage} =====", flush=True)
    result = subprocess.run(command, env=environment)
    if result.returncode != 0:
        raise RuntimeError(f"{stage}失败，退出码：{result.returncode}")


def main() -> int:
    configure_console()
    parser = argparse.ArgumentParser(description="下载 B 站音频并自动执行 VAD 和 ASR")
    parser.add_argument("url", help="B 站视频、分P视频或空间合集链接")
    parser.add_argument("-o", "--output", type=Path, help="本次任务的空输出目录")
    parser.add_argument(
        "--cookies-from-browser",
        choices=("edge", "chrome"),
        help="可选：读取浏览器中的 B 站登录状态",
    )
    args = parser.parse_args()

    error = dependency_error()
    if error:
        print(error, file=sys.stderr)
        return 2
    required = (VAD_SCRIPT, ASR_SCRIPT)
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        print("缺少运行文件：\n" + "\n".join(missing), file=sys.stderr)
        return 2

    try:
        job_dir = make_job_dir(args.output)
        original_dir = job_dir / "原始音频"
        vad_dir = job_dir / "VAD人声"

        print(f"任务目录：{job_dir}")
        print("\n===== 1/3 下载 B 站音频 =====", flush=True)
        download_video(
            args.url,
            original_dir,
            progress_hook=progress,
            cookie_browser=args.cookies_from_browser,
        )
        sources = find_audio(original_dir)
        if not sources:
            raise RuntimeError("下载结束，但没有找到音频文件")
        print(f"下载完成：{len(sources)} 个音频", flush=True)

        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "utf-8"
        run_stage(
            [
                str(VAD_PYTHON),
                "-B",
                str(VAD_SCRIPT),
                *map(str, sources),
                "--output",
                str(vad_dir),
            ],
            "2/3 VAD 人声切片",
            environment,
        )
        run_stage(
            [str(VAD_PYTHON), "-B", str(ASR_SCRIPT), str(vad_dir)],
            "3/3 ASR 转录",
            environment,
        )
        print("\n全部完成")
        print(f"VAD 切片：{vad_dir}")
        print(f"转写文本：{vad_dir / '转写结果.txt'}")
        return 0
    except Exception as exc:
        print(f"\n处理停止：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
