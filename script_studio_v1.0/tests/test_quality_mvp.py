import copy
import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
import server
import prompt_settings
import quality_runtime
from quality_store import QualityStore, fingerprint
from creative_service import PLANNER_POLICY, validate_briefs
from test_random_sampling import material_fixture


class QualityMVPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = material_fixture()
        self.cache['main'] = []
        for i, row in enumerate(self.cache['rival_comp']):
            row['关联竞对视频'] = [{'id': 'r' + str(i % 12)}]
        for name, rows in self.cache.items():
            (self.root / (name + '.json')).write_text(json.dumps(rows, ensure_ascii=False), encoding='utf8')
        for name, value in [('DATA_DIR', self.temp.name), ('JOBS_DIR', str(self.root / 'batch_jobs')),
                            ('CONFIG_FILE', str(self.root / 'config.json')), ('batch_jobs', {})]:
            patcher = patch.object(server, name, value)
            patcher.start(); self.addCleanup(patcher.stop)
        patcher = patch.object(prompt_settings, 'OVERRIDE_FILE', self.root / 'prompt_overrides.json')
        patcher.start(); self.addCleanup(patcher.stop)
        self.blocked = {'rivals': {'r0'}, 'shots': set(), 'components': set()}
        patcher = patch.object(server, 'visibility_blocked', side_effect=lambda: (self.blocked, {
            'statuses': {r['_record_id']: ['已使用'] if r['_record_id'] == 'r0' else [] for r in self.cache['rival_struct']},
            'updated_at': '2026-10-26T00:00:00+08:00'}))
        patcher.start(); self.addCleanup(patcher.stop)
        server.save_config({'model_service': {'api_url': 'http://test.local/v1', 'api_key': 'test-secret',
                                             'default_model': 'test-model', 'request_timeout_seconds': 60}})
        self.client = TestClient(server.app)
        self.store = QualityStore(self.root / 'quality')
        self.calls = []
        self.generation_count = 0
        self.transport = patch('server.urllib.request.urlopen', side_effect=self.respond)
        self.transport.start(); self.addCleanup(self.transport.stop)
        self.slot = server.build_slot(rival_id='r0', mode='竞对骨架-原创内容',
                                     selection=server.MaterialSelection(weapons=['w0'], rivals=['r0']))
        self.slot['weapon_candidates'] = [self.slot['weapon']]

    def respond(self, request, **kwargs):
        body = json.loads(request.data)
        self.calls.append(body)
        system = body['messages'][0]['content'] if body['messages'][0]['role'] == 'system' else ''
        user = body['messages'][-1]['content']
        if system == PLANNER_POLICY:
            payload = json.loads(user)
            data = self.planning_data(payload['count_limit'])
        elif system.startswith('你是提示词改进编辑'):
            payload = json.loads(user)
            data = {'changes': [{'section': 'mode_2', 'text': payload['base']['templates']['mode_2'] + '\n按当前事件推进，少用说明书表达。',
                                 'reason': '针对反馈中的说明书段落', 'risk': '保留来源节拍但可能减少解释细节'}], 'summary': '调整事件推进'}
        elif system.startswith('你是中文游戏买量口播编辑'):
            payload = json.loads(user)
            data = {'decision': 'A', 'reason': '本例A的行动更清楚', 'evidence_A': payload['texts']['A'][:5],
                    'evidence_B': payload['texts']['B'][:5], 'scores': {'A': {'口语': 4}, 'B': {'口语': 3}}, 'fact_concerns': []}
        elif system.startswith('你是中文游戏口播审稿人'):
            payload = json.loads(user)
            data = {'items': [{'index': i['index'], 'usable': '待修改', 'evidence': i['script'][:5], 'reason': '模拟审稿'} for i in payload['items']],
                    'similar_pairs': [], 'summary': '模拟初评'}
        else:
            self.generation_count += 1
            text = '我盯住桥口，等它靠近才出手。' + '先稳住。' * self.generation_count
            data = {'title': '模拟稿', 'material_code': 'MODEL-CODE', 'full_script': text,
                    'shots': [{'shot_num': 1, 'text': text, 'duration': 5, 'visual': '桥口', 'game_action': '等待时机', 'ref_text': 'R0叙事转化'}]}
        return io.BytesIO(json.dumps({'choices': [{'message': {'content': json.dumps(data, ensure_ascii=False)}}]}).encode())

    def planning_data(self, count=3):
        quote = self.cache['rival_struct'][0]['口播文案'][:7]
        return {'analysis': {'hook': '判断局势', 'conflict': '时机选择', 'source_beats': [{'quote': quote, 'function': '进入判断'}], 'gaps': []},
                'briefs': [{'angle': f'角度{i}', 'style': f'口吻{i}', 'core_conflict': f'矛盾{i}', 'decision_reason': f'取舍{i}',
                    'opening': f'切入{i}', 'narrative': [f'节拍{i}起', f'节拍{i}落'], 'source_quote': quote,
                    'fact_refs': ['w0'], 'difference': f'区别{i}', 'gaps': []} for i in range(count)], 'unsupported_directions': []}

    def wait(self, job_id):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            response = self.client.get('/api/quality/jobs/' + job_id)
            self.assertEqual(response.status_code, 200, response.text)
            job = response.json()['job']
            if job['status'] not in ('queued', 'running') and quality_runtime.ACTIVE_JOB != job_id:
                return job
            time.sleep(.01)
        self.fail('quality task did not finish')

    def plan(self):
        response = self.client.post('/api/quality/creative/plan', json={'source_id': 'r0', 'allow_used': True,
            'selection': {'weapons': ['w0']}, 'model': 'test-model', 'count': 3})
        self.assertEqual(response.status_code, 200, response.text)
        if response.json().get('cached'):
            return response.json()['plan']
        job = self.wait(response.json()['job']['id'])
        self.assertEqual(job['status'], 'completed', job.get('error'))
        return job['result']['plan']

    def saved_record(self, code='CASE-1'):
        result = self.client.post('/api/generate', json=self.slot).json()
        self.assertTrue(result['ok'], result)
        record = result['result']; record['material_code'] = code
        filename = server.save_draft(record)['filename']
        return server.get_draft_editor(filename)['record']

    def test_generation_trace_captures_real_render_and_immutable_template(self):
        record = self.saved_record()
        meta = record['generation_meta']
        trace = self.store.read('traces', meta['trace_id'])
        self.assertEqual(trace['status'], 'succeeded')
        self.assertEqual(trace['calls'][0]['status'], 'succeeded')
        self.assertIn('玩法库使用规则', trace['calls'][0]['system'])
        bundle = self.store.read('bundles', meta['bundle_id'])
        self.store.bundle(bundle['templates'], 'another label')
        self.assertEqual(self.store.read('bundles', meta['bundle_id']), bundle)
        self.assertNotIn('test-secret', json.dumps(trace, ensure_ascii=False))

    def test_feedback_is_idempotent_revision_bound_and_does_not_rewrite_draft(self):
        record = self.saved_record()
        path = self.root / 'saved_records' / record['_filename']; before = path.read_bytes()
        payload = {'record': record, 'decision': 'rejected', 'reasons': ['像说明书'], 'event_id': 'a'*32}
        self.assertEqual(self.client.post('/api/quality/feedback', json=payload).status_code, 200)
        self.assertEqual(self.client.post('/api/quality/feedback', json=payload).status_code, 200)
        self.assertEqual(len(self.store.list('feedback')), 1)
        self.assertEqual(path.read_bytes(), before)
        altered = copy.deepcopy(record); altered['full_script'] += '后来变了。'
        self.assertEqual(self.client.post('/api/quality/feedback', json={**payload, 'record': altered}).status_code, 409)
        self.assertEqual(self.client.post('/api/quality/review', json={'record': record, 'target': 'manual'}).status_code, 422)
        unknown = {'material_code': 'OLD', 'full_script': '旧稿没有版本。'}
        self.assertEqual(self.client.post('/api/quality/review', json={'record': unknown}).json()['generation_meta']['status'], 'unknown')
        url = '/api/draft-editor/' + record['_filename']
        edited = self.client.put(url, json={'revision': 0, 'manual_script': '我等它贴近桥口才出手。', 'manual_shots': None})
        self.assertEqual(edited.status_code, 200, edited.text)
        manual = self.client.get(url).json()['record']
        self.assertEqual(manual['full_script'], record['full_script'])
        self.assertEqual(self.client.post('/api/quality/feedback', json={
            'record': manual, 'target': 'manual', 'decision': 'accepted'}).status_code, 200)
        reopened = self.client.post('/api/quality/review', json={'record': manual, 'target': 'manual'}).json()
        self.assertEqual(reopened['latest']['decision'], 'accepted')
        self.assertIsNone(self.client.post('/api/quality/review', json={'record': manual}).json()['latest'])
        self.client.put(url, json={'revision': 1, 'manual_script': '怪到了桥口，我再动手。', 'manual_shots': None})
        self.assertEqual(self.client.post('/api/quality/feedback', json={
            'record': manual, 'target': 'manual', 'decision': 'accepted'}).status_code, 409)
        latest = self.client.get(url).json()['record']
        review = self.client.post('/api/quality/review', json={'record': latest, 'target': 'manual'}).json()
        self.assertIsNone(review['latest']); self.assertEqual(review['stale_count'], 1)

    def test_versions_retirement_legacy_save_block_and_rollback(self):
        state = self.client.get('/api/quality/versions').json()
        active = next(v for v in state['versions'] if v['active'])
        other = next(v for v in state['versions'] if not v['active'])
        def action(key, kind, current):
            return self.client.post('/api/quality/versions/' + key, json={'action': kind,
                'prompt_revision': current['prompt_revision'], 'state_revision': current['state_revision']})
        self.assertEqual(action(active['id'], 'retire', state).status_code, 422)
        state = action(other['id'], 'activate', state).json()
        state = action(active['id'], 'retire', state).json()
        templates = next(v['templates'] for v in server.get_prompt_settings()['versions'] if fingerprint(v['templates']) == active['id'])
        blocked = self.client.put('/api/prompts', json={'templates': templates, 'revision': state['prompt_revision']})
        self.assertEqual(blocked.status_code, 422)
        state = action(active['id'], 'restore', state).json()
        state = action(active['id'], 'activate', state).json()
        self.assertTrue(next(v for v in state['versions'] if v['id'] == active['id'])['active'])



    def test_candidate_uses_feedback_and_never_activates_itself(self):
        record = self.saved_record()
        self.client.post('/api/quality/feedback', json={'record': record, 'decision': 'revise', 'reasons': ['像说明书'], 'event_id': 'c'*32})
        settings = server.get_prompt_settings(); base_id = fingerprint(settings['templates'])
        response = self.client.post('/api/quality/candidates', json={'base_id': base_id, 'feedback_ids': ['c'*32]})
        job = self.wait(response.json()['job']['id'])
        self.assertEqual(job['status'], 'completed', job.get('error'))
        self.assertFalse(job['result']['active_changed'])
        self.assertNotEqual(job['result']['candidate_id'], base_id)
        self.assertEqual(server.get_prompt_settings()['templates'], settings['templates'])
        self.assertEqual(len(job['calls']), 1)

    def test_frozen_experiment_keeps_drafts_isolated_and_adoption_is_idempotent(self):
        records = [self.saved_record('CASE-'+str(i)) for i in range(3)]
        versions = self.client.get('/api/quality/versions').json()['versions']
        before = {p.name: p.read_bytes() for p in (self.root/'saved_records').glob('*.json')}
        response = self.client.post('/api/quality/experiments', json={'version_ids': [v['id'] for v in versions[:2]],
            'filenames': [r['_filename'] for r in records], 'max_calls': 15})
        self.assertEqual(response.status_code, 200, response.text)
        job = self.wait(response.json()['job']['id'])
        self.assertEqual(job['status'], 'completed', job.get('error'))
        self.assertEqual(len(job['calls']), 9)
        self.assertEqual(before, {p.name: p.read_bytes() for p in (self.root/'saved_records').glob('*.json')})
        for item in job['items']:
            self.assertIn(item['judgement']['decision'], ('A', 'B'))
            for value in item['outputs'].values():
                self.assertEqual(value['result']['generation_meta']['purpose'], 'experiment')
        adopted = self.client.post('/api/quality/jobs/'+job['id']+'/adopt', json={'case_index': 1, 'side': 'A'}).json()
        self.assertTrue(adopted['ok'])
        repeated = self.client.post('/api/quality/jobs/'+job['id']+'/adopt', json={'case_index': 1, 'side': 'A'}).json()
        self.assertEqual(repeated['filename'], adopted['filename']); self.assertTrue(repeated['already_saved'])
        self.assertEqual(len(list((self.root/'saved_records').glob('*.json'))), 4)




    def test_experiment_connection_is_frozen_even_when_config_changes(self):
        records = [self.saved_record('FREEZE-'+str(i)) for i in range(3)]
        versions = self.client.get('/api/quality/versions').json()['versions']
        original = self.respond; urls = []
        def transport(request, **kwargs):
            urls.append(request.full_url)
            if len(urls) == 1:
                server.save_config({'model_service': {'api_url': 'http://changed.local/v1', 'api_key': 'changed-secret', 'default_model': 'changed-model'}})
            return original(request, **kwargs)
        with patch('server.urllib.request.urlopen', side_effect=transport):
            response = self.client.post('/api/quality/experiments', json={'version_ids': [v['id'] for v in versions[:2]],
                'filenames': [r['_filename'] for r in records], 'max_calls': 15})
            job = self.wait(response.json()['job']['id'])
        self.assertEqual(job['status'], 'completed', job.get('error'))
        self.assertTrue(all(url.startswith('http://test.local/') for url in urls))
        self.assertTrue(all(call['model'] == 'test-model' for call in job['calls']))
        self.assertEqual(job['result']['source_group_count'], 1)

    def test_invalid_judge_quote_is_uncertain_not_a_fake_win(self):
        records = [self.saved_record('QUOTE-'+str(i)) for i in range(3)]
        versions = self.client.get('/api/quality/versions').json()['versions']
        original = self.respond
        def transport(request, **kwargs):
            response = original(request, **kwargs)
            if json.loads(request.data)['messages'][0]['content'].startswith('你是中文游戏买量口播编辑'):
                data = json.loads(response.read()); result = json.loads(data['choices'][0]['message']['content'])
                result['evidence_A'] = '不存在于正文的裁判引文'
                data['choices'][0]['message']['content'] = json.dumps(result)
                return io.BytesIO(json.dumps(data).encode())
            return response
        with patch('server.urllib.request.urlopen', side_effect=transport):
            response = self.client.post('/api/quality/experiments', json={'version_ids': [v['id'] for v in versions[:2]],
                'filenames': [r['_filename'] for r in records], 'max_calls': 15})
            job = self.wait(response.json()['job']['id'])
        self.assertEqual(job['status'], 'partial')
        self.assertTrue(all(i['judgement']['decision'] == 'uncertain' for i in job['items']))
        self.assertIsNone(job['result']['recommended_version'])
        self.assertEqual(len(job['calls']), 9)

    def test_identical_text_is_tied_without_spending_on_judge(self):
        records = [self.saved_record('SAME-'+str(i)) for i in range(3)]
        versions = self.client.get('/api/quality/versions').json()['versions']; original = self.respond
        def transport(request, **kwargs):
            response = original(request, **kwargs)
            system = json.loads(request.data)['messages'][0]['content']
            self.assertFalse(system.startswith('你是中文游戏买量口播编辑'))
            text = '我等在桥口，等它靠近。'
            result = {'full_script': text, 'shots': [{'text': text, 'duration': 5, 'visual': '桥口', 'game_action': '等待'}]}
            return io.BytesIO(json.dumps({'choices': [{'message': {'content': json.dumps(result)}}]}).encode())
        with patch('server.urllib.request.urlopen', side_effect=transport):
            response = self.client.post('/api/quality/experiments', json={'version_ids': [v['id'] for v in versions[:2]],
                'filenames': [r['_filename'] for r in records], 'max_calls': 15})
            job = self.wait(response.json()['job']['id'])
        self.assertEqual(job['status'], 'completed', job.get('error'))
        self.assertTrue(all(i['judgement']['decision'] == 'tie' for i in job['items']))
        self.assertEqual(len(job['calls']), 6)
        self.assertIsNone(job['result']['recommended_version'])

    def test_similar_outputs_are_flagged_even_if_judge_misses_them(self):
        from creative_service import near_duplicates
        items = [{'index': i, 'script': '我盯住桥口，等它靠近才出手。'} for i in range(1, 4)]
        flags = near_duplicates(items, [{'code': 'PAST', 'text': items[0]['script']}])
        self.assertEqual(len(flags), 6)
        self.assertTrue(all(f['similarity'] == 1 for f in flags))


    def test_fact_flags_use_only_own_facts_and_uncertain_winner(self):
        from creative_service import certainty_flags
        self.assertTrue(certainty_flags('横扫全场，底线固若金汤。', {'rivals': [{'raw_script': '横扫全场'}]}))
        self.assertEqual(certainty_flags('横扫全场', {'weapon': {'feature': '攻击全屏'}}), [])
        records = [self.saved_record('FLAGS-'+str(i)) for i in range(3)]
        versions = self.client.get('/api/quality/versions').json()['versions']; original = self.respond
        def transport(request, **kwargs):
            response = original(request, **kwargs)
            system = json.loads(request.data)['messages'][0]['content']
            if system.startswith('你是中文游戏买量口播编辑'): return response
            data = json.loads(response.read()); result = json.loads(data['choices'][0]['message']['content'])
            result['full_script'] += '全场无敌。'; result['shots'][0]['text'] = result['full_script']
            data['choices'][0]['message']['content'] = json.dumps(result)
            return io.BytesIO(json.dumps(data).encode())
        with patch('server.urllib.request.urlopen', side_effect=transport):
            response = self.client.post('/api/quality/experiments', json={'version_ids': [v['id'] for v in versions[:2]],
                'filenames': [r['_filename'] for r in records], 'max_calls': 15, 'reverse_judge': True})
            job = self.wait(response.json()['job']['id'])
        self.assertTrue(all(i['fact_flags']['A'] and i['fact_flags']['B'] for i in job['items']))
        self.assertTrue(all(i['judgement']['decision'] == 'uncertain' for i in job['items']))
        judge_calls = [c for c in self.calls if c['messages'][0]['content'].startswith('你是中文游戏买量口播编辑')]
        self.assertEqual(len(judge_calls), 6)
        for left, right in zip(judge_calls[::2], judge_calls[1::2]):
            a=json.loads(left['messages'][-1]['content']); b=json.loads(right['messages'][-1]['content'])
            self.assertEqual(a['fact_expression_flags']['A'], b['fact_expression_flags']['B'])

    def test_irrelevant_template_change_is_skipped_without_paid_calls(self):
        records = [self.saved_record('NO-EFFECT-'+str(i)) for i in range(3)]
        active = next(v for v in self.client.get('/api/quality/versions').json()['versions'] if v['active'])
        changed = copy.deepcopy(server.get_prompt_settings()['templates']); changed['mode_0'] += '\n仅影响其他用途。'
        other = self.store.bundle(changed, '其他用途候选')
        count = len(self.calls)
        response = self.client.post('/api/quality/experiments', json={'version_ids': [active['id'], other['id']],
            'filenames': [r['_filename'] for r in records], 'max_calls': 15})
        job = self.wait(response.json()['job']['id'])
        self.assertEqual(len(self.calls), count)
        self.assertEqual(job['calls'], [])
        self.assertTrue(all(i['judgement']['basis']=='prompt_identity' for i in job['items']))
        self.assertIsNone(job['result']['recommended_version'])


if __name__ == '__main__':
    unittest.main()
