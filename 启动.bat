@echo off
chcp 65001 >/dev/null
cd /d %~dp0
if exist "VRChat PyPyDance助手.exe" (
  start "" "VRChat PyPyDance助手.exe"
  exit /b 0
)
python dance_cache_gui.py
if errorlevel 1 (
  echo.
  echo 程序异常退出，错误码 %errorlevel%
)
pause
