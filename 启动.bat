@echo off
chcp 65001 >nul
cd /d "%~dp0"

if exist "PyPyDanceCache.exe" goto launch_local
if exist "dist\PyPyDanceCache\PyPyDanceCache.exe" goto launch_bundle

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "dance_cache_gui.py" %*
) else (
  python "dance_cache_gui.py" %*
)
set "APP_EXIT_CODE=%ERRORLEVEL%"
if not "%APP_EXIT_CODE%"=="0" (
  echo.
  echo 程序退出，错误码 %APP_EXIT_CODE%。请先按 README 安装 Python 3.10+ 与 requirements.txt。
  pause
)
exit /b %APP_EXIT_CODE%

:launch_local
start "" "PyPyDanceCache.exe" %*
exit /b

:launch_bundle
start "" "dist\PyPyDanceCache\PyPyDanceCache.exe" %*
exit /b
