"""Simple product workflow around the real GEPA engine."""
import copy
import json
import zipfile
import io
from pathlib import Path

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field
import quality_jobs
from quality_store import fingerprint
from prompt_optimizer import (OptimizationInput, StartOptimizationInput, engine_info, case_catalog,
                              prepare_dataset, public_dataset, optimization_work, diagnose_script)
from quality_jobs import public_connection


class ApplyInput(BaseModel):
    prompt_revision: int = Field(ge=0)
    state_revision: int = Field(ge=0)
    retire_previous: bool = True


class DiagnoseInput(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    request: dict = Field(default_factory=dict)
    reference_script: str = Field(default='', max_length=20000)


def export_promptfoo(job):
    rows = job.get('items', [])
    hashes = sorted({r['candidate_hash'] for r in rows})
    cases = sorted({r['filename'] for r in rows})
    data = {}
    for row in rows:
        data[row['candidate_hash'] + ':' + row['filename']] = {
            'script': row.get('output', {}).get('result', {}).get('full_script', ''),
            'hard_passed': row.get('hard_passed', False), 'score': row.get('score', 0),
            'review': row.get('review') or {'reason': row.get('reason')}, 'split': row['split']}
    config = {'description': '守桥提示词优化 · 已执行结果离线回放（不会调用模型，不重新评审）',
              'prompts': ['{{case_id}}'],
              'providers': [{'id': 'file://replay.cjs', 'label': h[:12], 'config': {'candidate': h}} for h in hashes],
              'tests': [{'vars': {'case_id': name}} for name in cases],
              'defaultTest': {'assert': [{'type': 'javascript',
                  'value': '(() => { const r = JSON.parse(output); return {pass:r.hard_passed && r.score > 0, score:r.score, reason:JSON.stringify(r.review)}; })()'}]}}
    provider = """const fs=require('node:fs'); const path=require('node:path');
module.exports=class Replay {
 constructor(options){this.config=options.config;}
 id(){return 'studio-replay-'+this.config.candidate;}
 async callApi(prompt,context){const rows=JSON.parse(fs.readFileSync(path.join(__dirname,'results.json'),'utf8'));
 const row=rows[this.config.candidate+':'+context.vars.case_id];
 if(!row)return {error:'本候选未评测此样例；不生成、不补跑'};
 return {output:JSON.stringify(row),cached:true,metadata:{replay:true}};}
};
"""
    readme = ('这是已执行GEPA运行的离线回放，包含草稿正文和评语，请按本地资料保管。\n'
              '解压后执行：npx --yes promptfoo@0.125.1 eval -c promptfooconfig.json --no-cache --no-share -o report.html\n'
              '无需API Key，不调用工作室/模型，不上传或分享报告；未评测组合明确显示缺失。\n'
              '质量分沿用本次冻结评审，Promptfoo负责矩阵与断言回放，不是第二次独立质量验收。\n')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, value in [('promptfooconfig.json', json.dumps(config, ensure_ascii=False, indent=2)),
                            ('results.json', json.dumps(data, ensure_ascii=False, indent=2)),
                            ('replay.cjs', provider), ('README.txt', readme)]:
            archive.writestr(name, value)
    return buffer.getvalue()


def build_optimizer_router():
    router = APIRouter(prefix='/api/quality/optimizer')
    from quality_api import store, versions

    @router.get('/overview')
    def overview():
        import server
        settings, state, entries = versions()
        active = next(v for v in entries.values() if v['active'])
        runs = [j for j in store().list('jobs', 1000) if j['kind'] == 'optimization']
        cases = [{k: value for k, value in row.items() if k != 'full_script'} for row in case_catalog(server)]
        return {'ok': True, 'engine': engine_info(), 'active': {'id': active['id'], 'name': active['name']},
                'prompt_revision': settings['revision'], 'state_revision': state['revision'],
                'cases': cases,
                'feedback_count': sum(e.get('reviewer', 'human') == 'human' for e in store().list('feedback', 10000)),
                'runs': [{'id': j['id'], 'status': quality_jobs.get_job(store(), j['id'])['status'],
                          'created_at': j['created_at'], 'mode': j['payload']['settings']['mode'],
                          'objective': j['payload']['settings']['objective'], 'call_count': len(j['calls']),
                          'result': j.get('result')} for j in runs[:30]]}

    @router.post('/diagnose')
    def diagnose(request: DiagnoseInput):
        return {'ok': True, 'diagnostics': diagnose_script(request.text, request.request, request.reference_script)}

    @router.post('/preview')
    def preview(request: OptimizationInput):
        import server
        try:
            _, _, entries = versions(); dataset = prepare_dataset(server, store(), request, entries)
            return {'ok': True, 'dataset': public_dataset(dataset)}
        except (ValueError, RuntimeError) as error:
            raise HTTPException(422, str(error))

    @router.post('/runs')
    def start(request: StartOptimizationInput):
        import server
        if not engine_info()['available']:
            raise HTTPException(422, 'GEPA引擎缺失或版本不符；安装requirements.txt中的固定依赖后再运行')
        try:
            dataset = store().read('datasets', request.dataset_id)
            if not dataset:
                raise ValueError('样例快照不存在，请先预览')
            # Settings changes require an explicit new preview; no silent substitution.
            settings = request.model_dump(exclude={'dataset_id'})
            settings['model'] = settings['model'] or server.model_connection().get('default_model', '')
            for key in ('judge_model', 'reflection_model'):
                settings[key] = settings[key] or settings['model']
            if settings != dataset['settings']:
                raise ValueError('优化设置已变化，请重新预览后开始')
            if public_connection(server.model_connection()) != dataset['connection']:
                raise ValueError('模型连接已变化，请重新预览')
            if fingerprint(server.get_prompt_settings()['templates']) != dataset['base']['id']:
                raise ValueError('生效提示词已变化，请重新预览')
            payload = copy.deepcopy(dataset)
            job = quality_jobs.start(store(), 'optimization', payload, request.max_calls,
                                     optimization_work(server, store(), payload), server)
            return {'ok': True, 'job': job}
        except (ValueError, RuntimeError) as error:
            raise HTTPException(409, str(error))

    @router.post('/runs/{job_id}/apply')
    def apply(job_id: str, request: ApplyInput):
        import server
        import prompt_settings
        try:
            with prompt_settings.lock:
                job = quality_jobs.get_job(store(), job_id)
                if job['kind'] != 'optimization' or job['status'] != 'completed' or not job.get('result', {}).get('recommended'):
                    raise ValueError('仅已完成且独立验收支持的修订可从这里采用；本次保持原版')
                settings, state, entries = versions(); result = job['result']
                if request.prompt_revision != settings['revision'] or request.state_revision != state['revision']:
                    raise RuntimeError('版本状态已更新，请重新读取后再采用')
                if fingerprint(settings['templates']) != result['base_id']:
                    raise RuntimeError('当前版已变化，不能覆盖后来修改；请按当前版本重新优化')
                candidate = entries[result['candidate_id']]
                if candidate['retired']:
                    raise ValueError('修订版已停用，不能直接采用')
                previous = entries[result['base_id']]
                store().bundle(previous['templates'], previous['name'])
                server.save_prompt_settings(candidate['templates'], settings['revision'], quality_root=Path(server.DATA_DIR) / 'quality')
                if request.retire_previous:
                    store().change_version_state(previous['id'], True, state['revision'])
                job['deployment'] = {'candidate_id': candidate['id'], 'previous_id': previous['id'], 'retired_previous': request.retire_previous}
                store().write('jobs', job_id, job)
                return {'ok': True, 'deployment': job['deployment']}
        except RuntimeError as error:
            raise HTTPException(409, str(error))
        except ValueError as error:
            raise HTTPException(422, str(error))

    @router.get('/runs/{job_id}/promptfoo')
    def promptfoo(job_id: str):
        try:
            job = quality_jobs.get_job(store(), job_id)
            if job['kind'] != 'optimization' or job['status'] in ('queued', 'running'):
                raise ValueError('请选择已结束的提示词优化任务')
            return Response(export_promptfoo(job), media_type='application/zip',
                            headers={'Content-Disposition': 'attachment; filename="promptfoo-replay.zip"'})
        except ValueError as error:
            raise HTTPException(422, str(error))
    return router
