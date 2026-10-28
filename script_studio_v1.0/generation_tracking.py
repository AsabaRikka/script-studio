"""Capture the exact prompt bundle and revision without changing old manuscripts."""
import functools
import hashlib
import uuid
from pathlib import Path

from quality_runtime import TRACE, TRACE_SAVE, BUDGET, CONNECTION
from quality_jobs import public_connection
from quality_store import QualityStore, fingerprint, now


def track_generation(function):
    @functools.wraps(function)
    def wrapped(req):
        import server
        server.validate_generation_selection(req)
        if req.hide_used_rivals:
            server.validate_visible_sources(req.model_dump())
        settings = server.get_prompt_settings()
        templates = req.prompt_templates or settings['templates']
        server.validate_templates(templates)
        req = server.constrain_gameplay(req)
        version = next((v for v in settings.get('versions', []) if v['templates'] == templates), {})
        store = QualityStore(Path(server.DATA_DIR) / 'quality')
        bundle = store.bundle(templates, version.get('name') or '本次自定义模板', settings['revision'])
        trace_id = uuid.uuid4().hex
        engine = {name: hashlib.sha256((Path(server.__file__).parent / name).read_bytes()).hexdigest()
                  for name in ('server.py', 'prompt_builder.py', 'script_sources.py', 'script_fusion.py')}
        model = req.model or server.model_connection().get('default_model', '')
        effort = req.reasoning_effort or server.model_connection().get('reasoning_effort', 'auto')
        meta = {'trace_id': trace_id, 'bundle_id': bundle['id'], 'prompt_name': bundle['name'],
                'template_revision': settings['revision'] if templates == settings['templates'] else None,
                'engine_hash': fingerprint(engine), 'model': model, 'reasoning_effort': effort,
                'created_at': now(), 'purpose': 'experiment' if BUDGET.get() and BUDGET.get().experiment else 'generation'}
        meta['connection'] = public_connection(CONNECTION.get() or server.model_connection())
        trace = {**meta, 'engine_files': engine, 'request': req.model_dump(),
                 'status': 'running', 'calls': []}
        store.write('traces', trace_id, trace)
        token = TRACE.set(trace)
        save_token = TRACE_SAVE.set(lambda: store.write('traces', trace_id, trace))
        try:
            response = function(req)
            trace['status'] = 'succeeded' if response.get('ok') else 'failed'
            if response.get('ok'):
                data = response['result']
                data['generation_meta'] = meta
                data.setdefault('source_slot', req.model_dump(include={
                    'weapon', 'weapon_candidates', 'rivals', 'shots', 'components', 'welfare',
                    'gameplay_facts', 'selected_gameplay_ids', 'selection', 'level', 'mode',
                    'stitched_ref_script', 'stitched_blueprint', 'emotions', 'pure_random'}))
                trace['result'] = data
                trace['text_hash'] = fingerprint(data.get('full_script', ''))
            else:
                trace['error'] = response.get('error')
            return response
        except Exception as error:
            trace.update(status='failed', error_type=type(error).__name__)
            raise
        finally:
            TRACE.reset(token)
            TRACE_SAVE.reset(save_token)
            trace['finished_at'] = now()
            store.write('traces', trace_id, trace)
    return wrapped
