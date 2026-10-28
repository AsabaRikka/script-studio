"""Quality lab HTTP boundary; all storage follows server.DATA_DIR at call time."""
import json
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from quality_store import QualityStore, body_identity, fingerprint
from creative_service import CreativeSettings, PlanInput, CreativeBatchInput, prepare_plan, verify_source, sources, plan_work, creative_work, validate_briefs
from evaluation_service import CandidateInput, ExperimentInput, EDITABLE, candidate_work, prepare_experiment, experiment_work
import quality_jobs

REASONS = ['口语别扭', '像说明书', '开头弱', '事件不推进', '事实无依据', '来源利用不足', '与其他稿重复']


def store():
    import server
    return QualityStore(Path(server.DATA_DIR) / 'quality')


def versions():
    import server
    settings = server.get_prompt_settings()
    entries = {}
    for version in settings.get('versions', []):
        key = fingerprint(version['templates'])
        entries.setdefault(key, {**version, 'id': key})
    for bundle in store().list('bundles', 500):
        entries.setdefault(bundle['id'], bundle)
    active = fingerprint(settings['templates'])
    entries.setdefault(active, {'id': active, 'name': '当前自定义版本', 'templates': settings['templates']})
    state = store().version_state()
    for key, entry in entries.items():
        entry.update(active=key == active, retired=state['versions'].get(key, {}).get('retired', False))
    return settings, state, entries


class ReviewInput(BaseModel):
    record: dict[str, Any]
    target: Literal['ai', 'manual'] = 'ai'


class FeedbackInput(ReviewInput):
    reviewer: Literal['human', 'agent'] = 'human'
    decision: Literal['accepted', 'revise', 'rejected']
    reasons: list[Literal['口语别扭', '像说明书', '开头弱', '事件不推进', '事实无依据', '来源利用不足', '与其他稿重复']] = Field(default_factory=list, max_length=7)
    note: str = Field(default='', max_length=2000)
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex, pattern=r'^[a-f0-9]{32}$')


class VersionAction(BaseModel):
    action: Literal['activate', 'retire', 'restore']
    prompt_revision: int = Field(ge=0)
    state_revision: int = Field(ge=0)


class AdoptionInput(BaseModel):
    case_index: int = Field(ge=1, le=6)
    side: Literal['A', 'B']


def checked_record(request):
    import server
    record = request.record
    if len(json.dumps(record, ensure_ascii=False)) > 1500000:
        raise ValueError('稿件过大，无法评价')
    identity = body_identity(record, request.target)
    if record.get('_filename'):
        current = server.get_draft_editor(record['_filename'])['record']
        latest = body_identity(current, request.target)
        if identity != latest:
            raise RuntimeError('正文或编辑版本已变化，请先保存并重新读取后评价')
        return current
    return record


def build_quality_router():
    router = APIRouter(prefix='/api/quality')

    @router.get('/capabilities')
    def capabilities():
        return {'ok': True, 'version': 2, 'features': ['provenance', 'feedback', 'prompt_optimization', 'experiments'],
                'feedback_reasons': REASONS}

    @router.get('/versions')
    def list_versions():
        settings, state, entries = versions()
        return {'ok': True, 'prompt_revision': settings['revision'], 'state_revision': state['revision'],
                'versions': [{k: v for k, v in entry.items() if k not in ('templates', 'defaults', 'tokens')}
                             for entry in entries.values()]}

    @router.post('/versions/{version_id}')
    def change_version(version_id: str, request: VersionAction):
        import server
        import prompt_settings
        try:
            with prompt_settings.lock:
                settings, state, entries = versions()
                if request.prompt_revision != settings['revision'] or request.state_revision != state['revision']:
                    raise RuntimeError('版本已变化，请重新读取后操作')
                entry = entries.get(version_id)
                if not entry:
                    raise ValueError('提示词版本不存在')
                store().bundle(entry['templates'], entry['name'])
                if request.action == 'activate':
                    if entry['retired']:
                        raise ValueError('此版本已停用，请先恢复再采用')
                    server.save_prompt_settings(entry['templates'], settings['revision'], quality_root=Path(server.DATA_DIR) / 'quality')
                else:
                    if request.action == 'retire' and entry['active']:
                        raise ValueError('当前生效版本不能直接停用，请先切换其他版本')
                    store().change_version_state(version_id, request.action == 'retire', state['revision'])
                return list_versions()
        except RuntimeError as error:
            raise HTTPException(409, str(error))
        except ValueError as error:
            raise HTTPException(422, str(error))

    @router.post('/review')
    def review(request: ReviewInput):
        try:
            record = checked_record(request)
            return {'ok': True, 'generation_meta': record.get('generation_meta') or {'status': 'unknown'},
                    **store().feedback_for(record, request.target)}
        except RuntimeError as error:
            raise HTTPException(409, str(error))
        except ValueError as error:
            raise HTTPException(422, str(error))

    @router.post('/feedback')
    def feedback(request: FeedbackInput):
        try:
            record = checked_record(request)
            event = store().add_feedback(record, request.target, request.decision,
                                         list(dict.fromkeys(request.reasons)), request.note, request.event_id, request.reviewer)
            return {'ok': True, 'event': event}
        except RuntimeError as error:
            raise HTTPException(409, str(error))
        except ValueError as error:
            raise HTTPException(422, str(error))

    @router.get('/feedback')
    def list_feedback():
        return {'ok': True, 'events': store().list('feedback', 100)}

    @router.get('/traces/{trace_id}')
    def trace(trace_id: str):
        try:
            data = store().read('traces', trace_id)
            if not data:
                raise HTTPException(404, '生成溯源不存在，旧稿版本显示未知')
            return {'ok': True, 'trace': data}
        except ValueError as error:
            raise HTTPException(422, str(error))

    def invoke(function, *args):
        try:
            return function(*args)
        except RuntimeError as error:
            raise HTTPException(409, str(error))
        except (ValueError, OSError) as error:
            raise HTTPException(422, str(error))

    @router.get('/jobs')
    def list_jobs():
        rows = [quality_jobs.get_job(store(), j['id']) for j in store().list('jobs', 100)]
        return {'ok': True, 'jobs': [{k: row.get(k) for k in ('id', 'kind', 'status', 'created_at', 'max_calls', 'error', 'phase')}
                                    | {'call_count': len(row['calls'])} for row in rows]}

    @router.get('/jobs/{job_id}')
    def read_job(job_id: str):
        return {'ok': True, 'job': invoke(quality_jobs.get_job, store(), job_id)}

    @router.post('/jobs/{job_id}/stop')
    def stop_job(job_id: str):
        with quality_jobs.LOCK:
            job = invoke(quality_jobs.get_job, store(), job_id)
            if job['status'] in ('queued', 'running'):
                job['stop_requested'] = True
                store().write('jobs', job_id, job)
            return {'ok': True, 'job': job}

    @router.get('/creative/sources')
    def creative_sources():
        import server
        return {'ok': True, 'sources': sources(server), 'status_note': '只读本地快照；规划/生成前核对远端可读性'}

    @router.post('/creative/plan')
    def creative_plan():
        raise HTTPException(410, "一源多创已撤下；请使用提示词优化工作流")

    @router.get('/creative/plans/{plan_id}')
    def read_plan(plan_id: str):
        plan = invoke(store().read, 'plans', plan_id)
        if not plan:
            raise HTTPException(404, '创意规划不存在')
        return {'ok': True, 'plan': plan}

    @router.post('/creative/batches')
    def creative_batch():
        raise HTTPException(410, "一源多创已撤下；请使用提示词优化工作流")

    @router.get('/creative/coverage/{source_id}')
    def coverage(source_id: str):
        import server
        feedback = store().list('feedback', 10000)
        variants = []
        for job in store().list('jobs', 1000):
            if job['kind'] != 'creative_generate' or job['payload']['plan']['source_id'] != source_id:
                continue
            for item in job['items']:
                decision = None
                if item.get('filename'):
                    try:
                        record = server.get_draft_editor(item['filename'])['record']
                        for event in feedback:
                            if event.get('reviewer', 'human') != 'human':
                                continue
                            if event['material_code'] != record.get('material_code'):
                                continue
                            identity = body_identity(record, event['target'])
                            if event['text_hash'] == identity['text_hash'] and event['edit_revision'] == identity['edit_revision']:
                                decision = event['decision']
                                break
                    except (HTTPException, ValueError, OSError):
                        pass
                variants.append({'job_id': job['id'], 'angle': item['brief']['angle'], 'style': item['brief']['style'],
                                 'status': item['status'], 'filename': item.get('filename'), 'decision': decision})
        return {'ok': True, 'variants': variants, 'generated': sum(v['status'] == 'succeeded' for v in variants),
                'accepted': sum(v['decision'] == 'accepted' for v in variants)}

    @router.post('/candidates')
    def create_candidate(request: CandidateInput):
        import server
        _, _, entries = versions()
        if request.base_id not in entries:
            raise HTTPException(422, '基础版本不存在')
        events = [invoke(store().read, 'feedback', key) for key in request.feedback_ids]
        if any(event is None for event in events):
            raise HTTPException(422, '部分审稿反馈不存在')
        payload = {'base': entries[request.base_id], 'feedback': events,
                   'allowed_sections': sorted(EDITABLE), 'purpose': request.purpose,
                   'model': request.model or server.model_connection().get('default_model', ''),
                   'reasoning_effort': request.reasoning_effort}
        job = invoke(quality_jobs.start, store(), 'candidate', payload, 1,
                     candidate_work(server, store(), payload), server)
        return {'ok': True, 'job': job}

    @router.post('/experiments')
    def create_experiment(request: ExperimentInput):
        import server
        _, _, entries = versions()
        payload = invoke(prepare_experiment, server, request, entries)
        job = invoke(quality_jobs.start, store(), 'experiment', payload, request.max_calls,
                     experiment_work(server, store(), payload), server)
        return {'ok': True, 'job': job}

    @router.get('/cases')
    def cases():
        import server
        rows = []
        for path in sorted((Path(server.DATA_DIR) / 'saved_records').glob('*.json')):
            try:
                record = json.loads(path.read_text(encoding='utf8'))
                if record.get('source_slot') and record.get('full_script'):
                    rows.append({'filename': path.name, 'code': record.get('material_code') or path.stem,
                                 'title': record.get('title') or path.stem,
                                 'source_ids': [r.get('id') for r in record['source_slot'].get('rivals', [])]})
            except (OSError, ValueError, TypeError):
                continue
        return {'ok': True, 'cases': rows, 'note': '同源样例不是独立证据；本页仅小样例筛查'}

    @router.post('/jobs/{job_id}/adopt')
    def adopt(job_id: str, request: AdoptionInput):
        import copy
        import server
        with quality_jobs.LOCK:
            job = invoke(quality_jobs.get_job, store(), job_id)
            if job['kind'] != 'experiment' or job['status'] in ('queued', 'running'):
                raise HTTPException(409, '请选择已结束的实验结果')
            key = f'{request.case_index}-{request.side}'
            if job.get('adoptions', {}).get(key):
                return {'ok': True, 'filename': job['adoptions'][key], 'already_saved': True}
            item = next((i for i in job['items'] if i['index'] == request.case_index), None)
            value = item.get('outputs', {}).get(request.side) if item else None
            if not value or not value.get('ok'):
                raise HTTPException(422, '该结果没有可采用的完整正文')
            record = copy.deepcopy(value['result'])
            record['material_code'] = 'EXP-' + job_id[:12] + '-' + key
            record['experiment_origin'] = {'job_id': job_id, 'case_index': request.case_index,
                                           'side': request.side, 'bundle_id': item['mapping'][request.side]}
            saved = server.save_draft(record)
            job.setdefault('adoptions', {})[key] = saved['filename']
            store().write('jobs', job_id, job)
            return {'ok': True, 'filename': saved['filename'], 'already_saved': False}

    return router
