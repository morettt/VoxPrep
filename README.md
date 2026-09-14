# VoxPrep
web操作。一键化下载网页视频转音频。asr转录+提取人声。整理成可直接训练的tts数据集

# B站视频数据集自动处理

## WebUI

双击：

```text
uv run python webui.py
```

浏览器会自动打开 `http://127.0.0.1:8765`。粘贴 B 站链接、填写保存根目录并点击开始即可。页面会实时显示下载、VAD 和 ASR 日志，保存根目录会自动记住。

每次任务都会在保存根目录下创建独立的时间目录，不会覆盖已有数据。

## 命令行

处理顺序：

```text
B站链接 → 下载音频 → Silero VAD 人声切片 → 硅基流动 ASR → 转写结果.txt
```

## 运行

在 CMD 或 PowerShell 中执行：

```bat
uv run python pipeline.py "B站链接"
```

程序会在 `B站视频数据集系统\输出` 下创建一个带时间的任务目录。

指定任务目录：

```bat
uv run python pipeline.py "B站链接" -o "D:\数据集\角色名"
```

指定的目录必须为空，防止覆盖已有数据。

需要读取 B 站登录状态时，可在命令末尾添加：

```text
--cookies-from-browser edge
```

或：

```text
--cookies-from-browser chrome
```

## 输出结构

```text
任务目录\
├─ 原始音频\
├─ VAD人声\
│  ├─ 1.mp3
│  ├─ 2.mp3
│  └─ 转写结果.txt
```

多个视频或分P会依次进行 VAD，切片在同一个文件夹中连续编号。

## ASR 失败后重试

ASR 偶尔可能出现空文本或服务器错误。重新运行下面的命令，只会跳过已经成功的文件并重试失败项：

```bat
uv run python siliconflow_asr.py "任务目录\VAD人声"
```

## 运行环境

- 所有 Python 程序都使用本项目由 `uv` 管理的 `.venv`。
- Silero VAD 使用 `D:\肥6` 中已有的本地模型。
- FFmpeg 由项目依赖 `imageio-ffmpeg` 提供。
- ASR 使用 `SILICONFLOW_API_KEY` 环境变量；未设置时沿用现有 ASR 脚本中的本地配置。

安装下载阶段依赖：

```bat
uv sync
```

## 文件说明

- `pipeline.py`：一键执行完整流程。
- `bilibili_downloader.py`：B站音频下载模块。
- `vad_split.py`：本地 Silero VAD 切片模块。
- `siliconflow_asr.py`：硅基流动 ASR 模块。
- `pyproject.toml`：由 `uv` 管理的项目依赖。
