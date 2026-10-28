"""Start the workbench quietly, reuse its server, and open the default browser."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import urlopen
import webbrowser

ROOT = Path(__file__).resolve().parent
TITLE = "守桥文案库 · 智能洗稿与全要素正交碰撞工作台"

def is_studio(port):
    try:
        with urlopen(f"http://127.0.0.1:{port}/openapi.json", timeout=1) as response:
            api = json.load(response)
        return api.get("info", {}).get("title") == TITLE and "/api/draft-editor/{filename}" in api.get("paths", {})
    except (OSError, ValueError):
        return False

def port_in_use(port):
    with socket.socket() as connection:
        connection.settimeout(.5)
        return connection.connect_ex(("127.0.0.1", port)) == 0

def ensure_server(port):
    if is_studio(port):
        return "reused"
    if port_in_use(port):
        raise RuntimeError(f"端口 {port} 被其他程序占用，请关闭占用程序后重试。")
    logs = ROOT / "output" / "runtime"
    logs.mkdir(parents=True, exist_ok=True)
    python = Path(sys.executable).with_name("python.exe") if os.name == "nt" else Path(sys.executable)
    log_path = logs / f"server-{port}.log"
    with log_path.open("ab") as log:
        process = subprocess.Popen(
            [str(python), "-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        if is_studio(port):
            return "started"
        if process.poll() is not None:
            raise RuntimeError(f"服务启动失败，请查看 {log_path} 并确认已安装 requirements.txt 中的依赖。")
        time.sleep(.25)
    raise RuntimeError("服务启动超过25秒，请稍后重试。日志位于项目 output/runtime。")

def main():
    parser = argparse.ArgumentParser(description="启动守桥脚本工作室")
    parser.add_argument("--port", type=int, default=8501)
    parser.add_argument("--no-browser", action="store_true", help="仅启动或检查服务，供验证使用")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--restart", action="store_true", help="保存页面并等待所有操作完成后，检查并重启本项目服务")
    action.add_argument("--check-restart", action="store_true", help="只检查重启条件，不停止或启动服务")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("端口须在1024至65535之间")
    mutex = None
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        kernel.CreateMutexW.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel.ReleaseMutex.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        mutex = kernel.CreateMutexW(None, False, f"Local\\ScriptStudioLauncher-{args.port}")
        if not mutex:
            raise RuntimeError("无法创建启动锁")
        if kernel.WaitForSingleObject(mutex, 30000) not in (0, 0x80):
            kernel.CloseHandle(mutex)
            raise RuntimeError("另一个启动请求仍在处理，请稍后再试")
    try:
        if args.restart or args.check_restart:
            from studio_service import check_restart, restart_server
            if args.check_restart:
                process = check_restart(ROOT, args.port, is_studio)
                print(json.dumps({"ok": True, "server": "checked" if process else "stopped",
                                  "port": args.port, "pid": process.pid if process else None}))
                return
            state = restart_server(ROOT, args.port, is_studio, ensure_server)
        else:
            state = ensure_server(args.port)
        if not args.no_browser and not webbrowser.open(f"http://127.0.0.1:{args.port}/"):
            raise RuntimeError(f"无法打开默认浏览器，请手动访问 http://127.0.0.1:{args.port}/")
        if sys.stdout:
            print(json.dumps({"ok":True, "server":state, "port":args.port}))
    finally:
        if mutex:
            kernel.ReleaseMutex(mutex)
            kernel.CloseHandle(mutex)

if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        if os.name == "nt":
            ctypes.windll.user32.MessageBoxW(None, str(error), "守桥脚本工作室 · 启动失败", 0x10)
        elif sys.stderr:
            print(str(error), file=sys.stderr)
        sys.exit(1)
