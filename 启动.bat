@echo off
chcp 65001 >/dev/null
cd /d %~dp0
if exist "PyPyDance Video Cache.exe" (
  start "" "PyPyDance Video Cache.exe"
  exit /b 0
)
python dance_cache_gui.py
if errorlevel 1 (
  echo.
  echo 程序异常退出，错误码 %errorlevel%
)
pause
