@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 守桥脚本工作室 - 重启后端服务
echo 请先保存所有页面修改，并等待生成、改写、补库和飞书写入等操作全部结束。
echo 脚本只能检查批次状态，不能检测页面未保存内容和所有单次请求。
echo 不准备重启请直接关闭窗口；准备好后按任意键继续。
pause >nul
python launch_studio.py --restart --no-browser %*
if errorlevel 1 goto failed
echo 服务已就绪。请回到原页面普通刷新：http://127.0.0.1:8501/
echo 日志位于 output/runtime/server-8501.log
pause
exit /b 0
:failed
echo 重启未完成，请查看上方提示或错误弹窗。请勿反复点击或清空缓存。
pause
exit /b 1
