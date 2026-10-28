"""Conservative local service restart; never kill an arbitrary port owner."""
import json
from pathlib import Path
from urllib.request import urlopen

import psutil


def read_api(port, path):
    with urlopen(f'http://127.0.0.1:{port}{path}', timeout=5) as response:
        return json.load(response)


def service_process(root, port):
    owners = {c.pid for c in psutil.net_connections(kind='tcp')
              if c.status == psutil.CONN_LISTEN and c.laddr.port == port}
    if not owners:
        return None
    if None in owners or len(owners) != 1:
        raise RuntimeError('无法唯一确认端口进程，未停止任何程序。')
    process = psutil.Process(owners.pop())
    command = process.cmdline()
    expected = ['-m', 'uvicorn', 'server:app', '--host', '127.0.0.1', '--port', str(port)]
    direct = len(command) == 2 and Path(command[1]).name == 'server.py' and port == 8501
    if (Path(process.cwd()).resolve() != Path(root).resolve()
            or not Path(command[0]).name.lower().startswith('python')
            or not (command[1:] == expected or direct)):
        raise RuntimeError('端口进程的项目目录或启动命令不符，未停止任何程序。')
    if direct and Path(command[1]).is_absolute() and Path(command[1]).resolve() != Path(root).resolve() / 'server.py':
        raise RuntimeError('server.py 路径不属于本项目，未停止任何程序。')
    return process


def check_batches(root, port):
    active = []
    try:
        for path in sorted((Path(root) / 'data_cache' / 'batch_jobs').glob('*.json')):
            job = read_api(port, '/api/batches/' + path.stem)['job']
            status = job['status']
            if status in ('running', 'queued'):
                active.append(path.stem)
            elif status not in ('completed', 'partial', 'failed', 'cancelled', 'interrupted'):
                raise ValueError('未知生成批次状态')
        batches = read_api(port, '/api/intake/batches')['batches']
        if not isinstance(batches, list):
            raise ValueError('入库批次列表格式错误')
        # The list endpoint returns only the newest 30; query older local IDs too.
        listed = {batch['id']: batch for batch in batches}
        for path in sorted((Path(root) / 'data_cache' / 'intake_batches').glob('*.json')):
            if path.stem not in listed:
                listed[path.stem] = read_api(port, '/api/intake/batches/' + path.stem)['job']
        for batch in listed.values():
            if batch['status'] not in ('运行中', '待开始', '已中断', '已停止', '部分失败', '已完成'):
                raise ValueError('未知入库批次状态')
            if batch['status'] == '运行中':
                active.append(batch['id'])
        for path in sorted((Path(root) / 'data_cache' / 'quality' / 'jobs').glob('*.json')):
            job = read_api(port, '/api/quality/jobs/' + path.stem)['job']
            if job['status'] in ('queued', 'running'):
                active.append(path.stem)
            elif job['status'] not in ('completed', 'partial', 'failed', 'stopped', 'interrupted'):
                raise ValueError('未知提示词实验/创意任务状态')
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise RuntimeError('无法可靠读取实时批次状态，取消重启；请确认服务可访问。') from error
    if active:
        raise RuntimeError('仍有运行或排队批次，请等完成后再重启：' + ', '.join(active))


def check_restart(root, port, is_studio):
    process = service_process(root, port)
    if process is None:
        return None
    if not is_studio(port):
        raise RuntimeError('端口上的接口不属于工作室或无法访问，未停止服务。')
    check_batches(root, port)
    return process


def restart_server(root, port, is_studio, ensure_server):
    process = check_restart(root, port, is_studio)
    if process is not None:
        # Recheck ownership and creation time immediately before terminating.
        current = service_process(root, port)
        if current is None or (current.pid, current.create_time()) != (process.pid, process.create_time()):
            raise RuntimeError('服务进程已变化，取消重启，请重新检查。')
        process.terminate()
        try:
            process.wait(timeout=15)
        except psutil.TimeoutExpired as error:
            raise RuntimeError('旧进程未在15秒内结束，未强制杀进程或启动第二个服务。') from error
    result = ensure_server(port)
    return 'restarted' if process is not None and result == 'started' else result
