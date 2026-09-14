#!/usr/bin/env python3
"""A small command-line Bilibili audio downloader."""

from __future__ import annotations

import argparse
import queue
import re
import sys
import threading
from pathlib import Path
from typing import Any, Callable


APP_TITLE = "哔哩哔哩音频下载器"
APP_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
DEFAULT_OUTPUT = APP_DIR / "雪绘"
MIN_YTDLP_VERSION = (2026, 8, 19)
SUPPORTED_URL = re.compile(
    r"^https?://(?:"
    r"(?:(?:www|m)\.)?bilibili\.com/video/|"
    r"b23\.tv/|"
    r"space\.bilibili\.com/\d+/lists/\d+"
    r")",
    re.IGNORECASE,
)


def dependency_error() -> str | None:
    try:
        from importlib.metadata import version

        import yt_dlp  # noqa: F401
        import imageio_ffmpeg  # noqa: F401
    except ImportError:
        return "缺少运行组件。请双击“启动下载器.bat”，它会自动安装所需组件。"
    installed = tuple(int(part) for part in version("yt-dlp").split(".")[:3])
    if installed < MIN_YTDLP_VERSION:
        return "下载组件版本过旧。请关闭本窗口后重新双击“启动下载器.bat”更新。"
    return None


def ffmpeg_path() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def normalize_url(url: str) -> str:
    """Remove whitespace while retaining URL parameters understood by yt-dlp."""
    return url.strip()


def validate_url(url: str) -> None:
    if not url:
        raise ValueError("请先粘贴视频链接。")
    if not SUPPORTED_URL.match(url):
        raise ValueError("请输入 B 站视频链接或空间合集链接。")


def human_bytes(value: float | int | None) -> str:
    if value is None:
        return "--"
    number = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if number < 1024 or unit == "TB":
            return f"{number:.1f} {unit}"
        number /= 1024
    return "--"


def build_options(
    output_dir: Path,
    progress_hook: Callable[[dict[str, Any]], None] | None = None,
    cookie_browser: str | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    options: dict[str, Any] = {
        # Download the best audio-only stream, falling back to a combined
        # stream only when the page does not expose a separate audio stream.
        "format": "ba/b",
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "best",
            }
        ],
        "ffmpeg_location": ffmpeg_path(),
        "outtmpl": str(output_dir / "%(title).180B [%(id)s].%(ext)s"),
        # A multi-part Bilibili video is exposed as a playlist by yt-dlp.
        # Keep playlist processing enabled so every part is downloaded.
        "noplaylist": False,
        "windowsfilenames": True,
        "continuedl": True,
        # Conservative defaults for large collections: avoid sending many
        # rapid requests that may trigger Bilibili's HTTP 412 protection.
        "sleep_interval_requests": 1,
        "sleep_interval": 3,
        "max_sleep_interval": 8,
        "retries": 3,
        "fragment_retries": 3,
        "concurrent_fragment_downloads": 1,
        "quiet": True,
        "no_warnings": True,
    }
    if progress_hook:
        options["progress_hooks"] = [progress_hook]
    if cookie_browser:
        options["cookiesfrombrowser"] = (cookie_browser,)
    return options


def download_video(
    url: str,
    output_dir: Path,
    progress_hook: Callable[[dict[str, Any]], None] | None = None,
    cookie_browser: str | None = None,
) -> dict[str, Any]:
    import yt_dlp

    url = normalize_url(url)
    validate_url(url)
    with yt_dlp.YoutubeDL(
        build_options(output_dir, progress_hook, cookie_browser)
    ) as downloader:
        return downloader.extract_info(url, download=True)


def friendly_error(exc: Exception) -> str:
    message = str(exc)
    if "HTTP Error 412" in message or "request is blocked" in message.lower():
        return (
            "B 站暂时拒绝了下载请求（HTTP 412）。请先停止重试，等待一段时间；"
            "之后关闭代理/VPN或切换网络再试。"
        )
    if "Could not copy Chrome cookie database" in message:
        return "无法读取浏览器登录状态。请完全关闭所选浏览器（包括后台进程）后重试。"
    return message


def run_cli(url: str, output_dir: Path, cookie_browser: str | None = None) -> int:
    error = dependency_error()
    if error:
        print(error, file=sys.stderr)
        return 2

    last_line_length = 0

    def report(data: dict[str, Any]) -> None:
        nonlocal last_line_length
        if data.get("status") == "downloading":
            downloaded = data.get("downloaded_bytes", 0)
            total = data.get("total_bytes") or data.get("total_bytes_estimate")
            percent = downloaded / total * 100 if total else 0
            text = (
                f"下载中 {percent:5.1f}%  "
                f"{human_bytes(data.get('speed'))}/s  ETA {data.get('eta', '--')} 秒"
            )
            last_line_length = max(last_line_length, len(text))
            print("\r" + text.ljust(last_line_length), end="", flush=True)
        elif data.get("status") == "finished":
            print("\n音频下载完成，正在整理文件……")

    try:
        print(f"保存位置：{output_dir.resolve()}")
        info = download_video(url, output_dir, report, cookie_browser)
        entries = [entry for entry in (info.get("entries") or []) if entry]
        if entries:
            print(f"全部下载完成：共 {len(entries)} 个音频")
        else:
            print(f"下载完成：{info.get('title', info.get('id', '音频'))}")
        return 0
    except Exception as exc:
        # yt-dlp wraps network/extractor errors in its own exception hierarchy;
        # keeping this entry point concise gives terminal users a useful message.
        print(f"下载失败：{friendly_error(exc)}", file=sys.stderr)
        return 1


def run_gui() -> int:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.withdraw()
    root.title(APP_TITLE)
    window_width = 720
    window_height = 430
    screen_x = max((root.winfo_screenwidth() - window_width) // 2, 0)
    screen_y = max((root.winfo_screenheight() - window_height) // 2, 0)
    root.geometry(f"{window_width}x{window_height}+{screen_x}+{screen_y}")
    root.minsize(620, 400)

    events: queue.Queue[tuple[str, Any]] = queue.Queue()
    url_var = tk.StringVar()
    output_var = tk.StringVar(value=str(DEFAULT_OUTPUT))
    browser_var = tk.StringVar(value="不使用（公开视频）")
    status_var = tk.StringVar(value="粘贴一个 B 站视频链接即可开始")
    detail_var = tk.StringVar(value="")
    progress_var = tk.DoubleVar(value=0)

    style = ttk.Style(root)
    if "vista" in style.theme_names():
        style.theme_use("vista")

    outer = ttk.Frame(root, padding=24)
    outer.pack(fill="both", expand=True)
    outer.columnconfigure(0, weight=1)

    ttk.Label(outer, text=APP_TITLE, font=("Microsoft YaHei UI", 18, "bold")).grid(
        row=0, column=0, sticky="w", pady=(0, 18)
    )
    ttk.Label(outer, text="视频链接").grid(row=1, column=0, sticky="w")
    url_entry = ttk.Entry(outer, textvariable=url_var, font=("Microsoft YaHei UI", 10))
    url_entry.grid(row=2, column=0, sticky="ew", pady=(6, 16), ipady=6)

    ttk.Label(outer, text="保存到").grid(row=3, column=0, sticky="w")
    folder_row = ttk.Frame(outer)
    folder_row.grid(row=4, column=0, sticky="ew", pady=(6, 18))
    folder_row.columnconfigure(0, weight=1)
    ttk.Entry(folder_row, textvariable=output_var).grid(
        row=0, column=0, sticky="ew", ipady=5
    )

    def choose_folder() -> None:
        selected = filedialog.askdirectory(
            title="选择视频保存位置", initialdir=output_var.get()
        )
        if selected:
            output_var.set(selected)

    browse_button = ttk.Button(folder_row, text="选择文件夹", command=choose_folder)
    browse_button.grid(row=0, column=1, padx=(10, 0), ipady=3)

    login_row = ttk.Frame(outer)
    login_row.grid(row=5, column=0, sticky="ew", pady=(0, 18))
    ttk.Label(login_row, text="登录状态（可选）").pack(side="left")
    browser_box = ttk.Combobox(
        login_row,
        textvariable=browser_var,
        values=("不使用（公开视频）", "Microsoft Edge", "Google Chrome"),
        state="readonly",
        width=22,
    )
    browser_box.pack(side="right")

    progress = ttk.Progressbar(
        outer, variable=progress_var, maximum=100, mode="determinate"
    )
    progress.grid(row=6, column=0, sticky="ew", pady=(2, 10))
    ttk.Label(outer, textvariable=status_var, font=("Microsoft YaHei UI", 10)).grid(
        row=7, column=0, sticky="w"
    )
    ttk.Label(outer, textvariable=detail_var, foreground="#666666").grid(
        row=8, column=0, sticky="w", pady=(4, 18)
    )

    download_button: ttk.Button

    def set_busy(busy: bool) -> None:
        state = "disabled" if busy else "normal"
        download_button.configure(state=state)
        browse_button.configure(state=state)
        url_entry.configure(state=state)
        browser_box.configure(state="disabled" if busy else "readonly")

    def worker(url: str, output: Path, cookie_browser: str | None) -> None:
        def hook(data: dict[str, Any]) -> None:
            if data.get("status") == "downloading":
                downloaded = data.get("downloaded_bytes", 0)
                total = data.get("total_bytes") or data.get("total_bytes_estimate")
                percent = downloaded / total * 100 if total else 0
                events.put(
                    (
                        "progress",
                        (
                            percent,
                            f"{human_bytes(downloaded)} / {human_bytes(total)}   "
                            f"速度 {human_bytes(data.get('speed'))}/s   "
                            f"剩余 {data.get('eta', '--')} 秒",
                        ),
                    )
                )
            elif data.get("status") == "finished":
                events.put(("merging", None))

        try:
            info = download_video(url, output, hook, cookie_browser)
            events.put(("done", (info.get("title", "视频"), output)))
        except Exception as exc:
            events.put(("error", str(exc)))

    def begin_download() -> None:
        url = normalize_url(url_var.get())
        try:
            validate_url(url)
            output = Path(output_var.get()).expanduser()
            if not output_var.get().strip():
                raise ValueError("请选择保存文件夹。")
        except (ValueError, OSError) as exc:
            messagebox.showwarning(APP_TITLE, str(exc))
            return

        dep_error = dependency_error()
        if dep_error:
            messagebox.showerror(APP_TITLE, dep_error)
            return
        progress_var.set(0)
        status_var.set("正在读取视频信息……")
        detail_var.set("")
        set_busy(True)
        browser_names = {
            "Microsoft Edge": "edge",
            "Google Chrome": "chrome",
        }
        cookie_browser = browser_names.get(browser_var.get())
        threading.Thread(
            target=worker, args=(url, output, cookie_browser), daemon=True
        ).start()

    download_button = ttk.Button(outer, text="开始下载", command=begin_download)
    download_button.grid(row=9, column=0, sticky="ew", ipady=7)

    def poll_events() -> None:
        try:
            while True:
                event, payload = events.get_nowait()
                if event == "progress":
                    percent, detail = payload
                    progress_var.set(percent)
                    status_var.set(f"正在下载… {percent:.1f}%")
                    detail_var.set(detail)
                elif event == "merging":
                    progress_var.set(100)
                    status_var.set("下载完成，正在合并高清画面和音频……")
                    detail_var.set("")
                elif event == "done":
                    title, output = payload
                    set_busy(False)
                    status_var.set("下载完成")
                    detail_var.set(str(output.resolve()))
                    messagebox.showinfo(APP_TITLE, f"《{title}》已下载到：\n{output.resolve()}")
                elif event == "error":
                    set_busy(False)
                    status_var.set("下载失败")
                    detail_var.set("")
                    messagebox.showerror(
                        APP_TITLE,
                        "下载失败。请确认网络和链接有效后重试。\n\n详细信息：\n"
                        + friendly_error(Exception(payload)),
                    )
        except queue.Empty:
            pass
        root.after(150, poll_events)

    root.bind("<Return>", lambda _event: begin_download())
    root.after(150, poll_events)
    root.update_idletasks()
    root.deiconify()
    root.lift()
    # Windows may otherwise place the Tk window behind the batch console.
    root.attributes("-topmost", True)
    root.after(1200, lambda: root.attributes("-topmost", False))
    url_entry.focus_set()
    root.mainloop()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="下载哔哩哔哩视频中的最高质量音频")
    parser.add_argument("url", nargs="?", help="B 站视频链接；省略时在终端中粘贴")
    parser.add_argument(
        "-o", "--output", type=Path, default=DEFAULT_OUTPUT, help="视频保存目录"
    )
    parser.add_argument(
        "--cookies-from-browser",
        choices=("edge", "chrome"),
        help="读取 Edge 或 Chrome 的 B 站登录状态（运行前请关闭浏览器）",
    )
    args = parser.parse_args()
    if not args.url:
        print("=" * 52)
        print("哔哩哔哩音频下载器")
        print("=" * 52)
        try:
            args.url = input("请粘贴视频链接，然后按回车：\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消。")
            return 0
    return run_cli(args.url, args.output, args.cookies_from_browser)


if __name__ == "__main__":
    exit_code = main()
    if getattr(sys, "frozen", False):
        try:
            input("\n按回车键退出……")
        except (EOFError, KeyboardInterrupt):
            pass
    raise SystemExit(exit_code)
