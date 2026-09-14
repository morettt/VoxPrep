"""Split one audio file into speech-only clips with the existing local Silero VAD."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import torch
import imageio_ffmpeg


VAD_ROOT = Path(r"D:\肥6\full-hub\asr-hub\model\torch_hub\snakers4_silero-vad_master")
FFMPEG = Path(imageio_ffmpeg.get_ffmpeg_exe())
SAMPLE_RATE = 16000
FRAME_SAMPLES = 512
THRESHOLD = 0.7
MIN_SILENCE_MS = 500
SPEECH_PAD_MS = 200
MAX_CLIP_SECONDS = 10.0
MIN_CLIP_SECONDS = 2.0
MERGE_GAP_SECONDS = 0.8
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
EXTRACT_WORKERS = 8


def audio_duration(path: Path) -> float:
    result = subprocess.run(
        [str(FFMPEG), "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=CREATE_NO_WINDOW,
    )
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr)
    if not match:
        raise RuntimeError("无法读取音频时长")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def load_vad():
    loaded = torch.hub.load(
        repo_or_dir=str(VAD_ROOT),
        model="silero_vad",
        force_reload=False,
        onnx=True,
        trust_repo=True,
        source="local",
    )
    model, utils = loaded
    return model, utils[3]


def detect_speech(source: Path) -> list[tuple[float, float]]:
    duration = audio_duration(source)
    model, vad_iterator_class = load_vad()
    iterator = vad_iterator_class(
        model,
        threshold=THRESHOLD,
        sampling_rate=SAMPLE_RATE,
        min_silence_duration_ms=MIN_SILENCE_MS,
        speech_pad_ms=SPEECH_PAD_MS,
    )
    command = [
        str(FFMPEG), "-hide_banner", "-loglevel", "error", "-i", str(source),
        "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "pipe:1",
    ]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW,
    )
    ranges: list[tuple[float, float]] = []
    speech_start: float | None = None
    processed_samples = 0
    last_percent = -1
    assert process.stdout is not None
    while True:
        raw = process.stdout.read(FRAME_SAMPLES * 4)
        if not raw:
            break
        samples = np.frombuffer(raw, dtype=np.float32)
        actual_samples = len(samples)
        if actual_samples < FRAME_SAMPLES:
            samples = np.pad(samples, (0, FRAME_SAMPLES - actual_samples))
        event = iterator(torch.from_numpy(samples.copy()), return_seconds=True)
        processed_samples += actual_samples
        if event and "start" in event:
            speech_start = float(event["start"])
        if event and "end" in event and speech_start is not None:
            ranges.append((speech_start, min(float(event["end"]), duration)))
            speech_start = None
        percent = min(int(processed_samples / SAMPLE_RATE / duration * 100), 100)
        if percent != last_percent:
            print(f"\rVAD 人声检测：{percent:3d}%", end="", flush=True)
            last_percent = percent
    if speech_start is not None:
        ranges.append((speech_start, duration))
    return_code = process.wait()
    if return_code != 0:
        raise RuntimeError(f"音频解码失败：{source.name}")
    print("\rVAD 人声检测：100%")
    return ranges


def merge_ranges(ranges: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for start, end in ranges:
        if end <= start:
            continue
        if not merged:
            merged.append((start, end))
            continue
        previous_start, previous_end = merged[-1]
        if start - previous_end <= MERGE_GAP_SECONDS and end - previous_start <= MAX_CLIP_SECONDS:
            merged[-1] = (previous_start, end)
        else:
            merged.append((start, end))

    # 极短语气词单独送入 ASR 容易得到空文本。将不足 2 秒的片段
    # 优先并入距离最近的相邻片段，不再受普通的 0.8 秒间隔限制。
    while True:
        changed = False
        for index, (start, end) in enumerate(merged):
            if end - start >= MIN_CLIP_SECONDS:
                continue

            choices: list[tuple[float, str]] = []
            if index > 0:
                previous_start, previous_end = merged[index - 1]
                if end - previous_start <= MAX_CLIP_SECONDS:
                    choices.append((max(0.0, start - previous_end), "previous"))
            if index + 1 < len(merged):
                next_start, next_end = merged[index + 1]
                if next_end - start <= MAX_CLIP_SECONDS:
                    choices.append((max(0.0, next_start - end), "next"))

            if not choices:
                continue

            direction = min(choices, key=lambda item: item[0])[1]
            if direction == "previous":
                previous_start, _ = merged[index - 1]
                merged[index - 1] = (previous_start, end)
                del merged[index]
            else:
                _, next_end = merged[index + 1]
                merged[index] = (start, next_end)
                del merged[index + 1]
            changed = True
            break

        if not changed:
            break
    return merged


def timestamp(value: float) -> str:
    milliseconds = int(round(value * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}{minutes:02d}{seconds:02d}.{milliseconds:03d}"


def extract_clips(
    source: Path,
    ranges: list[tuple[float, float]],
    output: Path,
    start_index: int = 1,
) -> int:
    output.mkdir(parents=True, exist_ok=True)

    def extract_one(index: int, start: float, end: float) -> None:
        destination = output / f"{index}.mp3"
        command = [
            str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(source),
            "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k", str(destination),
        ]
        result = subprocess.run(command, creationflags=CREATE_NO_WINDOW)
        if result.returncode != 0:
            raise RuntimeError(f"截取失败：{destination.name}")

    with ThreadPoolExecutor(max_workers=EXTRACT_WORKERS) as executor:
        futures = [
            executor.submit(extract_one, index, start, end)
            for index, (start, end) in enumerate(ranges, start_index)
        ]
        for completed, future in enumerate(as_completed(futures), 1):
            future.result()
            print(f"\r正在保存人声片段：{completed}/{len(ranges)}", end="", flush=True)
    print()
    return start_index + len(ranges)


def main() -> int:
    parser = argparse.ArgumentParser(description="使用本地 Silero VAD 截取人声音频")
    parser.add_argument("audio", type=Path, nargs="*", help="输入一个或多个音频文件")
    parser.add_argument("--output", type=Path, help="指定统一输出文件夹")
    parser.add_argument("--append", action="store_true", help="接着输出文件夹中的现有编号继续保存")
    parser.add_argument(
        "--base-range",
        type=int,
        nargs=2,
        metavar=("开始编号", "结束编号"),
        help="批量处理峰哥目录中的 base-编号.m4a",
    )
    args = parser.parse_args()
    range_output: Path | None = None
    if args.base_range:
        if args.audio:
            print("使用 --base-range 时不要再填写音频路径。")
            return 1
        start_number, end_number = args.base_range
        if start_number < 1 or end_number < start_number:
            print("编号范围不正确。")
            return 1
        base_folder = Path(__file__).resolve().parent / "峰哥"
        sources = [
            (base_folder / f"base-{number}.m4a").resolve()
            for number in range(start_number, end_number + 1)
        ]
        range_output = base_folder / f"VAD_{start_number}_{end_number}"
    else:
        if not args.audio:
            parser.error("请填写音频路径，或使用 --base-range 指定编号范围")
        sources = [path.expanduser().resolve() for path in args.audio]
    for source in sources:
        if not source.is_file():
            print(f"找不到音频：{source}")
            return 1
    if not VAD_ROOT.is_dir() or not FFMPEG.is_file():
        print("找不到指定的本地 VAD 模型或 FFmpeg。")
        return 1
    if args.output:
        output = args.output.expanduser().resolve()
    elif range_output:
        output = range_output.resolve()
    elif len(sources) == 1:
        output = sources[0].parent / f"{sources[0].stem}_VAD人声"
    else:
        print("批量处理时必须使用 --output 指定统一输出文件夹。")
        return 1
    if output.exists() and any(output.iterdir()) and not args.append:
        print(f"输出文件夹不是空的，已停止：{output}")
        return 1
    try:
        next_index = 1
        if args.append and output.exists():
            existing_numbers = [
                int(path.stem)
                for path in output.glob("*.mp3")
                if path.stem.isdigit()
            ]
            if existing_numbers:
                next_index = max(existing_numbers) + 1
        total_clips = 0
        for source_number, source in enumerate(sources, 1):
            print(f"\n[{source_number}/{len(sources)}] 正在处理：{source.name}")
            raw_ranges = detect_speech(source)
            ranges = merge_ranges(raw_ranges)
            print(f"检测到 {len(raw_ranges)} 段人声，整理为 {len(ranges)} 个短音频。")
            next_index = extract_clips(source, ranges, output, next_index)
            total_clips += len(ranges)
        print(f"处理完成：共生成 {total_clips} 个切片")
        print(f"保存位置：{output}")
        return 0
    except Exception as exc:
        print(f"处理失败：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
