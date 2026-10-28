@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo 缺少 Python 虚拟环境，请先在 PowerShell 运行 scripts\setup.ps1。
    pause
    exit /b 1
)
set "PYTHONUTF8=1"
".venv\Scripts\python.exe" "start_gui.py" %*
if errorlevel 1 (
    echo.
    echo 测试上位机未启动，请根据上方错误处理后重试。
    pause
    exit /b 1
)
