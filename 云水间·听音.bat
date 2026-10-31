@echo off
title 云水间 · 听音
cd /d "%~dp0"

set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

set "PYW=C:/Users/吴宇皓/AppData/Local/Programs/Python/Python313/pythonw.exe"
set "PY=C:/Users/吴宇皓/AppData/Local/Programs/Python/Python313/python.exe"

if not exist "%PYW%" goto :direct
start "" "%PYW%" launch.py
exit

:direct
if not exist "%PY%" (
  echo.
  echo   未找到 Python，无法启动。
  echo.
  pause
  exit /b
)
"%PY%" launch.py
pause
