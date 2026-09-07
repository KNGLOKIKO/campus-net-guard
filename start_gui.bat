@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem 优先用 Python Launcher 的 pythonw（无控制台窗口），找不到再退回 PATH 里的 pythonw
where py >nul 2>nul
if %errorlevel%==0 (
  start "" py -3w "%~dp0campus_net_guard.py"
) else (
  start "" pythonw "%~dp0campus_net_guard.py"
)
