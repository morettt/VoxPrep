"""按数字顺序转录 VAD 切片，并汇总到同一个 TXT。"""

from __future__ import annotations

import argparse
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests


API_URL = "https://api.siliconflow.cn/v1/audio/transcriptions"
MODEL = "XingChenAGI/XingChenASR-V3.2-Ultra"
DEFAULT_API_KEY = ""
DEFAULT_FOLDER = Path(__file__).resolve().parent / "峰哥" / "1_VAD人声"
OUTPUT_NAME = "转写结果.txt"
AUDIO_SUFFIXES = {".mp3", ".m4a", ".wav", ".flac", ".aac", ".ogg", ".opus"}
PARALLEL_REQUESTS = 10
MAX_ATTEMPTS = 2


def number_key(path: Path) -> tuple[int, str]:
    """让 2.mp3 排在 10.mp3 前面。"""
    match = re.fullmatch(r"(\d+)", path.stem)
    if match:
        return int(match.group(1)), path.name.lower()
    return 10**18, path.name.lower()


def find_audio_files(folder: Path) -> list[Path]:
    return sorted(
        (
            path
            for path in folder.iterdir()
            if path.is_file() and path.suffix.lower() in AUDIO_SUFFIXES
        ),
        key=number_key,
    )


def transcribe(audio: Path, api_key: str) -> str:
    last_error = "未知错误"
    mime_types = {
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".wav": "audio/wav",
        ".flac": "audio/flac",
        ".ogg": "audio/ogg",
        ".opus": "audio/ogg",
        ".aac": "audio/aac",
    }

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with audio.open("rb") as handle:
                response = requests.post(
                    API_URL,
                    headers={"Authorization": f"Bearer {api_key}"},
                    data={"model": MODEL},
                    files={
                        "file": (
                            audio.name,
                            handle,
                            mime_types.get(audio.suffix.lower(), "application/octet-stream"),
                        )
                    },
                    timeout=(30, 300),
                )

            if response.status_code in {429, 500, 502, 503, 504}:
                last_error = f"HTTP {response.status_code}: {response.text[:300]}"
                if attempt < MAX_ATTEMPTS:
                    print("    请求失败，正在重试最后 1 次……", flush=True)
                    time.sleep(5 * attempt)
                    continue

            response.raise_for_status()
            result = response.json()
            text = str(result.get("text") or "").strip()
            if not text:
                raise RuntimeError("接口返回的识别文本为空")
            return text
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            last_error = str(exc)
            if attempt < MAX_ATTEMPTS:
                print("    识别失败，正在重试最后 1 次……", flush=True)
                time.sleep(5 * attempt)

    raise RuntimeError(last_error)


def append_result(output: Path, audio_name: str, text: str) -> None:
    with output.open("a", encoding="utf-8") as handle:
        one_line_text = " ".join(text.splitlines())
        handle.write(f"{audio_name}:{one_line_text}\n")
        handle.flush()
        os.fsync(handle.fileno())


def transcribe_safely(audio: Path, api_key: str) -> tuple[Path, str | None, str | None]:
    try:
        return audio, transcribe(audio, api_key), None
    except Exception as exc:
        return audio, None, str(exc)


def main() -> int:
    parser = argparse.ArgumentParser(description="转录 VAD 人声切片")
    parser.add_argument(
        "folder",
        nargs="?",
        default=str(DEFAULT_FOLDER),
        help="切片文件夹；不填写时使用 峰哥\\1_VAD人声",
    )
    args = parser.parse_args()

    folder = Path(args.folder.strip().strip('"')).resolve()
    if not folder.is_dir():
        print(f"找不到切片文件夹：{folder}")
        return 1

    files = find_audio_files(folder)
    if not files:
        print(f"文件夹内没有音频：{folder}")
        return 1

    api_key = os.environ.get("SILICONFLOW_API_KEY", DEFAULT_API_KEY).strip()
    if not api_key:
        print("没有设置 SiliconFlow API Key。")
        return 1

    output = folder / OUTPUT_NAME
    existing = output.read_text(encoding="utf-8") if output.exists() else ""
    completed_names = {
        line.split(":", 1)[0]
        for line in existing.splitlines()
        if ":" in line
    }
    completed = 0
    skipped = 0
    failed: list[str] = []

    pending = [audio for audio in files if audio.name not in completed_names]
    skipped = len(files) - len(pending)
    print(
        f"找到 {len(files)} 个音频，已完成 {skipped} 个，"
        f"现在并行转录 {PARALLEL_REQUESTS} 个。",
        flush=True,
    )
    print(f"结果保存到：{output}", flush=True)

    with ThreadPoolExecutor(max_workers=PARALLEL_REQUESTS) as executor:
        results = executor.map(
            lambda audio: transcribe_safely(audio, api_key),
            pending,
        )
        for position, (audio, text, error) in enumerate(results, skipped + 1):
            if error is not None or text is None:
                failed.append(audio.name)
                print(f"[{position}/{len(files)}] 失败：{audio.name}：{error}", flush=True)
                continue

            print(f"[{position}/{len(files)}] {audio.name}:{text}", flush=True)
            append_result(output, audio.name, text)
            completed_names.add(audio.name)
            completed += 1

    print(f"\n完成：新增 {completed} 个，跳过 {skipped} 个，失败 {len(failed)} 个。")
    print(f"文本文件：{output}")
    if failed:
        print("失败文件：" + "、".join(failed))
        print("重新运行脚本会跳过已完成的文件，只重试未完成的文件。")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
