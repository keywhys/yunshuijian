@echo off
title 云水间 · 听音
cd /d "%~dp0"

set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

where pythonw >nul 2>nul
if not errorlevel 1 (
  start "" pythonw launch.py
  exit /b
)

where pyw >nul 2>nul
if not errorlevel 1 (
  start "" pyw launch.py
  exit /b
)

where python >nul 2>nul
if not errorlevel 1 (
  python launch.py
  pause
  exit /b
)

echo.
echo   未找到 Python，请先安装 Python 3.9 或更高版本。
echo   下载地址：https://www.python.org/downloads/
echo.
pause
