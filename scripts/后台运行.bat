@echo off
chcp 65001 >nul
cd /d "%~dp0.."
if not exist ".venv\Scripts\pythonw.exe" (
    echo [错误] 没有找到虚拟环境 .venv
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -m moocpy
