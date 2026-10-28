@echo off
chcp 65001 >nul
call "%~dp0script_studio_v1.0\重启服务.bat" %*
exit /b %errorlevel%
