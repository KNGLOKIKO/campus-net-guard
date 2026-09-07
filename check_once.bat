@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem 检测一次：无论结果如何都打开登录页，网络异常时顺带自动修复
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 "%~dp0campus_net_guard.py" --once --force-open --fix
) else (
  python "%~dp0campus_net_guard.py" --once --force-open --fix
)
echo.
echo 日志见 campus_net_guard.log
pause
