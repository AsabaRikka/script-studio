"""One local paid-work gate and an explicit per-task model-call ledger."""
import contextvars
import functools
import threading
from contextlib import contextmanager

PAID_LOCK = threading.RLock()
STATE_LOCK = threading.RLock()
ACTIVE_JOB = None
OWNER = contextvars.ContextVar('quality_owner', default=None)
BUDGET = contextvars.ContextVar('quality_budget', default=None)
TRACE = contextvars.ContextVar('quality_trace', default=None)
TRACE_SAVE = contextvars.ContextVar('quality_trace_save', default=None)
CONNECTION = contextvars.ContextVar('quality_connection', default=None)
FROZEN_CACHES = contextvars.ContextVar('quality_caches', default=None)


def ensure_idle():
    with STATE_LOCK:
        if ACTIVE_JOB and OWNER.get() != ACTIVE_JOB:
            raise RuntimeError('提示词优化或评测正在执行，请等待完成或停止后续')


def exclusive_operation(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        from fastapi import HTTPException
        try:
            ensure_idle()
        except RuntimeError as error:
            raise HTTPException(409, str(error))
        if not PAID_LOCK.acquire(blocking=False):
            raise HTTPException(409, '已有模型请求正在执行，请等待完成')
        try:
            return function(*args, **kwargs)
        finally:
            PAID_LOCK.release()
    return wrapped


def reserve(job_id):
    global ACTIVE_JOB
    with STATE_LOCK:
        ensure_idle()
        if not PAID_LOCK.acquire(blocking=False):
            raise RuntimeError('已有模型请求正在执行，请等待完成')
        PAID_LOCK.release()
        ACTIVE_JOB = job_id


def release(job_id):
    global ACTIVE_JOB
    with STATE_LOCK:
        if ACTIVE_JOB == job_id:
            ACTIVE_JOB = None


@contextmanager
def task_scope(job_id, budget, connection=None, caches=None):
    owner = OWNER.set(job_id)
    token = BUDGET.set(budget)
    connection_token = CONNECTION.set(connection)
    cache_token = FROZEN_CACHES.set(caches)
    try:
        with PAID_LOCK:
            yield
    finally:
        BUDGET.reset(token)
        CONNECTION.reset(connection_token)
        FROZEN_CACHES.reset(cache_token)
        OWNER.reset(owner)


def paid_call(function):
    @functools.wraps(function)
    def wrapped(prompt, system_prompt='', model='', reasoning_effort=''):
        ensure_idle()
        if not PAID_LOCK.acquire(blocking=False):
            raise RuntimeError('已有模型请求正在执行，请等待完成')
        step = None
        call_index = None
        budget = BUDGET.get()
        try:
            if budget:
                call_index = budget.before_call(model, reasoning_effort, prompt, system_prompt)
            trace = TRACE.get()
            if trace is not None:
                step = {'user': prompt, 'system': system_prompt, 'model': model,
                        'reasoning_effort': reasoning_effort, 'status': 'submitted'}
                trace['calls'].append(step)
                if TRACE_SAVE.get():
                    TRACE_SAVE.get()()
            value = function(prompt, system_prompt, model=model, reasoning_effort=reasoning_effort)
            if step is not None:
                step.update(status='succeeded', response=value)
            if budget and call_index is not None:
                budget.after_call(call_index, 'succeeded', value)
            if step is not None and TRACE_SAVE.get():
                TRACE_SAVE.get()()
            return value
        except Exception as error:
            if step is not None:
                step.update(status='failed', error_type=type(error).__name__)
            if budget and call_index is not None:
                budget.after_call(call_index, 'failed')
            raise
        finally:
            PAID_LOCK.release()
    return wrapped
