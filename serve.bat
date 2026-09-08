@echo off
chcp 65001 >nul
cd /d "%~dp0"
uv run coweread serve --port 8480
pause
