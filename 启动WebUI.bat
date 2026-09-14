@echo off
cd /d "%~dp0"
uv run python webui.py
if errorlevel 1 pause
