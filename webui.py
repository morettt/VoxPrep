#!/usr/bin/env python3
"""Minimal FastAPI WebUI for the Bilibili dataset pipeline."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel


APP_DIR = Path(__file__).resolve().parent
PIPELINE = APP_DIR / "pipeline.py"
PYTHON = Path(sys.executable)
SETTINGS = APP_DIR / "webui_settings.json"
HOST = "127.0.0.1"
PORT = 8765
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

app = FastAPI(title="B站视频数据集系统")
lock = threading.Lock()
job = {
    "state": "idle",
    "stage": "等待任务",
    "output": "",
    "logs": [],
    "process": None,
}


class StartRequest(BaseModel):
    url: str
    output_root: str


def load_output_root() -> str:
    default = str((APP_DIR / "输出").resolve())
    try:
        data = json.loads(SETTINGS.read_text(encoding="utf-8"))
        return str(data.get("output_root") or default)
    except (OSError, ValueError, TypeError):
        return default


def save_output_root(value: str) -> None:
    temporary = SETTINGS.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"output_root": value}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, SETTINGS)


def add_log(line: str) -> None:
    with lock:
        job["logs"].append(line)
        if len(job["logs"]) > 10000:
            del job["logs"][:1000]


def update_stage(line: str) -> None:
    with lock:
        if "1/3 下载" in line:
            job["stage"] = "正在下载音频"
        elif "2/3 VAD" in line:
            job["stage"] = "正在进行 VAD 切片"
        elif "3/3 ASR" in line:
            job["stage"] = "正在进行 ASR 转录"


def run_pipeline(command: list[str]) -> None:
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=environment,
            creationflags=CREATE_NO_WINDOW,
        )
        with lock:
            job["process"] = process
        assert process.stdout is not None
        buffer = ""
        last_line = ""
        while True:
            character = process.stdout.read(1)
            if not character:
                break
            if character in "\r\n":
                line = buffer.strip()
                buffer = ""
                if line and line != last_line:
                    add_log(line)
                    update_stage(line)
                    last_line = line
            else:
                buffer += character
        line = buffer.strip()
        if line and line != last_line:
            add_log(line)
            update_stage(line)
        return_code = process.wait()
        with lock:
            job["process"] = None
            if return_code == 0:
                job["state"] = "done"
                job["stage"] = "全部完成"
            else:
                job["state"] = "error"
                job["stage"] = f"处理停止（退出码 {return_code}）"
    except Exception as exc:
        add_log(f"启动失败：{exc}")
        with lock:
            job["process"] = None
            job["state"] = "error"
            job["stage"] = "启动失败"


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return PAGE.replace("__DEFAULT_OUTPUT__", json.dumps(load_output_root(), ensure_ascii=False))


@app.post("/api/start")
def start(request: StartRequest) -> dict[str, str]:
    url = request.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="请填写 B 站链接")
    output_root_text = request.output_root.strip()
    if not output_root_text:
        raise HTTPException(status_code=400, detail="请填写保存根目录")
    if not PYTHON.is_file() or not PIPELINE.is_file():
        raise HTTPException(status_code=500, detail="找不到 Python 或 pipeline.py")

    with lock:
        if job["state"] == "running":
            raise HTTPException(status_code=409, detail="已有任务正在运行")

    try:
        output_root = Path(output_root_text).expanduser().resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        job_dir = output_root / datetime.now().strftime("任务_%Y%m%d_%H%M%S_%f")[:-3]
        job_dir.mkdir()
        save_output_root(str(output_root))
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"无法创建保存目录：{exc}") from exc

    command = [str(PYTHON), "-u", str(PIPELINE), url, "-o", str(job_dir)]
    with lock:
        job.update(
            state="running",
            stage="正在启动",
            output=str(job_dir),
            logs=[],
            process=None,
        )
    threading.Thread(target=run_pipeline, args=(command,), daemon=True).start()
    return {"state": "running", "output": str(job_dir)}


@app.get("/api/status")
def status(since: int = Query(0, ge=0)) -> dict[str, object]:
    with lock:
        logs = list(job["logs"])
        return {
            "state": job["state"],
            "stage": job["stage"],
            "output": job["output"],
            "logs": logs[since:],
            "cursor": len(logs),
        }


PAGE = r'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>B站视频数据集系统</title>
  <style>
    *{box-sizing:border-box}body{margin:0;background:#f5f6f8;color:#202124;font-family:"Microsoft YaHei UI",sans-serif}
    main{width:min(880px,calc(100% - 32px));margin:36px auto}.card{background:#fff;border:1px solid #e3e6ea;border-radius:12px;padding:24px;box-shadow:0 5px 20px #0000000a}
    h1{font-size:24px;margin:0 0 22px}label{display:block;font-size:14px;margin:15px 0 7px;color:#4d5156}
    input,select,button{width:100%;height:42px;border-radius:7px;border:1px solid #c9cdd3;font:inherit;padding:0 12px}
    input:focus,select:focus{outline:2px solid #00aeec33;border-color:#00aeec}button{margin-top:20px;border:0;background:#00aeec;color:#fff;font-weight:600;cursor:pointer}
    button:disabled{background:#9fa8ad;cursor:not-allowed}.status{margin:20px 0 10px;padding:12px 14px;background:#f2f8fb;border-radius:7px;color:#087aa3}
    .output{font-size:13px;color:#666;word-break:break-all;margin-bottom:12px}pre{height:340px;margin:0;padding:14px;background:#16181c;color:#d8dee9;border-radius:8px;overflow:auto;white-space:pre-wrap;word-break:break-word;font:13px/1.55 Consolas,"Microsoft YaHei UI",monospace}
  </style>
</head>
<body><main><section class="card">
  <h1>B站视频数据集系统</h1>
  <label for="url">B站视频或合集链接</label><input id="url" placeholder="粘贴链接">
  <label for="output">保存根目录</label><input id="output">
  <button id="start">开始：下载 → VAD → ASR</button>
  <div class="status" id="status">等待任务</div><div class="output" id="taskOutput"></div><pre id="logs">尚未开始。</pre>
</section></main>
<script>
const defaultOutput=__DEFAULT_OUTPUT__;const urlEl=document.querySelector('#url'),outputEl=document.querySelector('#output'),startEl=document.querySelector('#start'),statusEl=document.querySelector('#status'),taskOutputEl=document.querySelector('#taskOutput'),logsEl=document.querySelector('#logs');
outputEl.value=defaultOutput;let cursor=0;
async function poll(){try{const r=await fetch('/api/status?since='+cursor);const d=await r.json();statusEl.textContent=d.stage;taskOutputEl.textContent=d.output?'任务目录：'+d.output:'';if(d.logs.length){if(cursor===0)logsEl.textContent='';logsEl.textContent+=d.logs.join('\n')+'\n';logsEl.scrollTop=logsEl.scrollHeight}cursor=d.cursor;startEl.disabled=d.state==='running'}catch(e){statusEl.textContent='无法连接服务器'}setTimeout(poll,700)}
startEl.addEventListener('click',async()=>{startEl.disabled=true;cursor=0;logsEl.textContent='正在启动……\n';try{const r=await fetch('/api/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:urlEl.value,output_root:outputEl.value})});const d=await r.json();if(!r.ok)throw new Error(d.detail||'启动失败');taskOutputEl.textContent='任务目录：'+d.output}catch(e){statusEl.textContent=e.message;startEl.disabled=false}});poll();
</script></body></html>'''


def main() -> None:
    parser = argparse.ArgumentParser(description="启动 B站视频数据集系统 WebUI")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if not args.no_browser:
        threading.Thread(
            target=lambda: (time.sleep(1.2), webbrowser.open(f"http://{HOST}:{PORT}")),
            daemon=True,
        ).start()
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
