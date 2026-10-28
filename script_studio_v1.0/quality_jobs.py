"""Bounded local quality jobs. No automatic resubmission after a restart."""
import copy
import threading
import uuid
from urllib.parse import urlsplit, urlunsplit

import quality_runtime as runtime
from quality_store import now

LOCK = threading.RLock()


def public_connection(connection):
    url = urlsplit(connection.get('api_url', ''))
    host = url.hostname or ''
    if url.port:
        host += ':' + str(url.port)
    return {'endpoint': urlunsplit((url.scheme, host, url.path, '', '')),
            'default_model': connection.get('default_model', ''), 'temperature': 0.8,
            'request_timeout_seconds': connection.get('request_timeout_seconds', 300)}


class TaskBudget:
    def __init__(self, store, job):
        self.store, self.job = store, job
        self.experiment = job['kind'] in ('experiment', 'optimization')

    def check(self):
        state = self.store.read('jobs', self.job['id'])
        if state and state.get('stop_requested'):
            self.job['stop_requested'] = True
            raise RuntimeError('已停止后续调用；当前已提交请求不会自动重发')

    def before_call(self, model, effort, prompt='', system_prompt=''):
        with LOCK:
            self.check()
            if len(self.job['calls']) >= self.job['max_calls']:
                raise RuntimeError('已达到本次模型调用上限，剩余内容未执行')
            index = len(self.job['calls'])
            self.job['calls'].append({'index': index + 1, 'at': now(), 'model': model,
                                      'reasoning_effort': effort, 'phase': self.job.get('phase'),
                                      'status': 'submitted', 'user': prompt, 'system': system_prompt})
            self.save()
            return index

    def after_call(self, index, status, response=None):
        with LOCK:
            self.job['calls'][index].update(status=status, finished_at=now())
            if response is not None:
                self.job['calls'][index]['response'] = response
            self.save()

    def save(self):
        old = self.store.read('jobs', self.job['id'])
        if old and old.get('stop_requested'):
            self.job['stop_requested'] = True
        self.store.write('jobs', self.job['id'], self.job)


def get_job(store, job_id):
    with LOCK:
        job = store.read('jobs', job_id)
        if not job:
            raise ValueError('质量任务不存在')
        if job['status'] in ('queued', 'running') and runtime.ACTIVE_JOB != job_id:
            job.update(status='interrupted', error='任务中断；未自动恢复，已提交调用可能已计费。', finished_at=now())
            store.write('jobs', job_id, job)
        return job


def start(store, kind, payload, max_calls, work, server):
    with LOCK:
        if any(job['status'] in ('queued', 'running') for job in server.batch_jobs.values()):
            raise RuntimeError('生成批次正在执行，请等待完成或停止后续')
        for path in (__import__('pathlib').Path(server.DATA_DIR) / 'intake_batches').glob('*.json'):
            import json
            if json.loads(path.read_text(encoding='utf8')).get('status') == '运行中':
                raise RuntimeError('补库批次正在执行，请等待完成')
        job_id = uuid.uuid4().hex
        runtime.reserve(job_id)
        connection = copy.deepcopy(server.model_connection())
        job = {'id': job_id, 'kind': kind, 'status': 'queued', 'created_at': now(),
               'max_calls': max_calls, 'calls': [], 'stop_requested': False,
               'connection': public_connection(connection), 'payload': copy.deepcopy(payload),
               'items': []}
        try:
            store.write('jobs', job_id, job)
            thread = threading.Thread(target=_run, args=(store, job, work, connection), daemon=True)
            thread.start()
        except Exception:
            runtime.release(job_id)
            raise
        return copy.deepcopy(job)


def _run(store, job, work, connection):
    budget = TaskBudget(store, job)
    try:
        with runtime.task_scope(job['id'], budget, connection, job['payload'].get('cache_snapshot')):
            job.update(status='running', started_at=now())
            budget.save()
            budget.check()
            result = work(job, budget)
            status = 'partial' if result.get('partial') else 'completed'
            if result.get('succeeded') == 0 and result.get('failed', 0) > 0:
                status = 'failed'
            job.update(result=result, status=status)
    except Exception as error:
        message = str(error)
        for secret in (connection.get('api_key'),):
            if secret:
                message = message.replace(secret, '[密钥]')
        job.update(status='stopped' if job.get('stop_requested') else 'failed', error=message[:3000])
    finally:
        job['finished_at'] = now()
        try:
            budget.save()
        finally:
            runtime.release(job['id'])
