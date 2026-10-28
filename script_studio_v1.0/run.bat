@echo off
chcp 65001 >nul
echo ========================================================
echo   守桥文案库 · 智能洗稿与创意碰撞工作台 (Feishu Base Studio)
echo ========================================================
echo.
echo 正在启动可视化服务...
echo 启动成功后请在浏览器访问: http://localhost:8501
echo.
cd /d "%~dp0"
python launch_studio.py
pause
