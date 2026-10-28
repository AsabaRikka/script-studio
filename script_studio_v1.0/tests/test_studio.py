import io
import json
import os
import random
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
import server
from script_fusion import coherent_shot_chain


class StudioTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.slot = server.slot_machine(mode="本体事实-自主原创")
        self.cfg = {"model_service": {"api_url": "http://test.local/v1", "api_key": "local-test-key", "default_model": "configured-model"}}
        self.patches = [patch.object(server, "DATA_DIR", self.tmp.name),
                        patch.object(server, "JOBS_DIR", os.path.join(self.tmp.name, "batch_jobs")),
                        patch.object(server, "CONFIG_FILE", os.path.join(self.tmp.name, "config.json")),
                        patch.dict(os.environ, {}, clear=True),
                        patch.object(server, "batch_jobs", {})]
        for p in self.patches:
            p.start()
        real_build_slot = server.build_slot
        self.patches.append(patch.object(server, "build_slot", side_effect=lambda *args, **kwargs: real_build_slot(*args, **kwargs) if server.load_cache('weapons') else self.slot))
        self.patches[-1].start()
        server.save_config(self.cfg)
        self.client = TestClient(server.app)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def wait_job(self, job_id):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = self.client.get('/api/batches/' + job_id).json()['job']
            if job['status'] not in ('queued', 'running'):
                return job
            time.sleep(.01)
        self.fail('batch did not finish')

    @staticmethod
    def result():
        return json.dumps({"title": "测试口播", "material_code": "repeated-model-code", "full_script": "给他一个机会。",
                           "shots": [{"shot_num": 1, "text": "给他一个机会。", "duration": 3, "visual": "三选一", "game_action": "选择强化"}]}, ensure_ascii=False)

    def test_draft_usage_is_optional_and_synced_material_code_is_not_counted_twice(self):
        caches = {
            'main': [{'素材编号':'SYNCED-1', '关联武器':[{'id':'w1'}], '参考竞对':[{'id':'r1'}],
                      '参考竞对分镜':[{'id':'s1'}], '参考组件':[{'id':'c1'}], '关联福利点':[{'id':'f1'}]}],
            'weapons':[{'_record_id':'w1'}, {'_record_id':'w2'}],
            'rival_struct':[{'_record_id':'r1'}, {'_record_id':'r2'}],
            'rival_shots':[{'_record_id':'s1'}, {'_record_id':'s2'}],
            'rival_comp':[{'_record_id':'c1'}, {'_record_id':'c2'}],
            'welfare':[{'_record_id':'f1'}, {'_record_id':'f2'}],
        }
        slot = {'weapon':{'id':'w2'}, 'rivals':[{'id':'r2'}], 'shots':[{'id':'s2'}],
                'components':[{'id':'c2'}], 'welfare':{'id':'f2'}}
        saved = os.path.join(self.tmp.name, 'saved_records')
        os.makedirs(saved)
        for name, code in [('LOCAL-1.json','LOCAL-1'), ('SYNCED-1.json','SYNCED-1')]:
            with open(os.path.join(saved, name), 'w', encoding='utf-8') as fp:
                json.dump({'material_code':code, 'source_slot':slot, 'used_weapon_ids':['w2']}, fp)
        with patch.object(server, 'load_cache', side_effect=lambda name:caches.get(name, [])):
            feishu_only = server.calculate_all_usage()
            with_drafts = server.calculate_all_usage(include_drafts=True)
            overview = self.client.get('/api/overview?hide_used_rivals=false&include_drafts=true').json()
        self.assertEqual(feishu_only[0]['w2'], 0)
        self.assertEqual(with_drafts[0]['w2'], 1)
        self.assertEqual(with_drafts[1]['r2'], 1)
        self.assertEqual(with_drafts[2]['s2'], 1)
        self.assertEqual(with_drafts[3]['c2'], 1)
        self.assertEqual(with_drafts[4]['f2'], 1)
        self.assertEqual(next(row for row in overview['weapons'] if row['id']=='w2')['used_count'], 1)
        # The synced draft shares its material code with the Feishu parent.
        for usage in with_drafts:
            self.assertEqual(sum(usage.values()), 2)

    def test_generation_normalizes_scalar_emotion_labels(self):
        raw=json.loads(self.result());raw['shots'][0]['emotions']=' 焦虑、爽感、焦虑 '
        with patch.object(server,'call_ai',return_value=json.dumps(raw,ensure_ascii=False)):
            data=self.client.post('/api/generate',json=self.slot).json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['result']['shots'][0]['emotions'],['焦虑','爽感'])

    def test_config_masks_and_preserves_key_and_effective_connection(self):
        config = self.client.get('/api/config').json()['config']['model_service']
        self.assertNotIn('api_key', config)
        self.assertTrue(config['api_key_configured'])
        response = self.client.post('/api/config', json={"model_service": {"api_url": "http://new.local", "api_key": "", "default_model": "other-model"}})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(server.get_config()['model_service']['api_key'], 'local-test-key')
        with patch.dict(os.environ, {'OPENAI_BASE_URL':'http://env.local/v1', 'OPENAI_API_KEY':'env-key'}):
            self.assertEqual(server.model_connection()['api_url'], 'http://env.local/v1')
            self.assertEqual(server.model_connection()['api_key'], 'env-key')

    def test_test_and_generation_share_model_and_endpoint(self):
        calls = []
        def transport(req, **kwargs):
            calls.append((req.full_url, json.loads(req.data), req.headers))
            return io.BytesIO(json.dumps({'choices':[{'message':{'content':'pong'}}]}).encode())
        with patch('server.urllib.request.urlopen', side_effect=transport):
            self.assertEqual(server.call_ai('hello'), 'pong')
            self.assertTrue(self.client.post('/api/test-model', json={}).json()['ok'])
        self.assertEqual([url for url, _, _ in calls], ['http://test.local/v1/chat/completions'] * 2)
        self.assertEqual([body['model'] for _, body, _ in calls], ['configured-model'] * 2)
        self.assertTrue(all('Mozilla/5.0' in headers.get('User-agent', '') for _, _, headers in calls))

    def test_model_request_timeout_is_configurable_and_timeout_is_explained(self):
        response = self.client.post('/api/config', json={'model_service': {
            'api_url':'http://test.local/v1', 'default_model':'configured-model',
            'request_timeout_seconds':420
        }})
        self.assertEqual(response.status_code, 200)
        calls = []
        def transport(req, timeout):
            calls.append(timeout)
            return io.BytesIO(json.dumps({'choices':[{'message':{'content':'pong'}}]}).encode())
        with patch('server.urllib.request.urlopen', side_effect=transport):
            self.assertEqual(server.call_ai('hello'), 'pong')
        self.assertEqual(calls, [420])
        self.assertEqual(self.client.post('/api/config', json={'model_service': {
            'api_url':'http://test.local', 'default_model':'configured-model', 'request_timeout_seconds':30
        }}).status_code, 422)
        with patch('server.urllib.request.urlopen', side_effect=TimeoutError('late')):
            with self.assertRaisesRegex(RuntimeError, '超过420秒'):
                server.call_ai('hello')

    def test_partial_batch_retry_preserves_success_and_persists_sources(self):
        with patch.object(server, 'call_ai', side_effect=[self.result(), RuntimeError('temporary failure'), self.result()]):
            job_id = self.client.post('/api/batches', json={'count':3}).json()['job_id']
            job = self.wait_job(job_id)
        self.assertEqual(job['status'], 'partial')
        self.assertEqual([x['status'] for x in job['items']], ['succeeded','failed','succeeded'])
        original = job['items'][0]['filename']
        with patch.object(server, 'call_ai', return_value=self.result()) as ai:
            self.client.post('/api/batches/' + job_id + '/retry')
            retried = self.wait_job(job_id)
            self.assertEqual(ai.call_count, 1)
        self.assertEqual(retried['status'], 'completed')
        self.assertEqual(retried['items'][0]['filename'], original)
        self.assertEqual(len({x['filename'] for x in retried['items']}), 3)
        records = self.client.get('/api/history').json()['records']
        self.assertEqual(len(records), 3)
        self.assertTrue(all(x['source_slot']['weapon'] == self.slot['weapon'] for x in records))
        server.batch_jobs.clear()
        self.assertEqual(self.client.get('/api/batches/' + job_id).json()['job']['status'], 'completed')

    def test_cancel_duplicate_job_and_retry_remaining(self):
        started, release = threading.Event(), threading.Event()
        def slow_ai(*args, **kwargs):
            started.set()
            release.wait(3)
            return self.result()
        with patch.object(server, 'call_ai', side_effect=slow_ai) as ai:
            job_id = self.client.post('/api/batches', json={'count':3}).json()['job_id']
            self.assertTrue(started.wait(2))
            self.assertEqual(self.client.post('/api/batches', json={'count':3}).status_code, 409)
            self.client.post('/api/batches/' + job_id + '/cancel')
            release.set()
            job = self.wait_job(job_id)
            self.assertEqual(ai.call_count, 1)
        self.assertEqual(job['status'], 'cancelled')
        with patch.object(server, 'call_ai', return_value=self.result()) as ai:
            self.client.post('/api/batches/' + job_id + '/retry')
            self.assertEqual(self.wait_job(job_id)['status'], 'completed')
            self.assertEqual(ai.call_count, 2)

    def test_limits_and_restart_recovery(self):
        for count in (0, 101, 1.5):
            self.assertEqual(self.client.post('/api/batches', json={'count':count}).status_code, 422)
        self.assertEqual(self.client.get('/api/batches/invalid-id').status_code, 404)
        job_id = 'a' * 32
        server.persist_job({'id':job_id, 'status':'running', 'items':[{'status':'running'},{'status':'succeeded'}]})
        job = self.client.get('/api/batches/' + job_id).json()['job']
        self.assertEqual(job['status'], 'interrupted')
        self.assertEqual([x['status'] for x in job['items']], ['failed','succeeded'])

    def test_reasoning_config_and_transport(self):
        self.cfg['model_service']['reasoning_effort'] = 'high'
        server.save_config(self.cfg)
        calls = []
        def transport(req, **kwargs):
            calls.append(json.loads(req.data))
            return io.BytesIO(json.dumps({'choices':[{'message':{'content':'pong'}}]}).encode())
        with patch('server.urllib.request.urlopen', side_effect=transport):
            server.call_ai('hello')
            self.client.post('/api/test-model', json={'reasoning_effort':'low'})
            server.call_ai('hello', reasoning_effort='auto')
        self.assertEqual(calls[0]['reasoning_effort'], 'high')
        self.assertEqual(calls[1]['reasoning_effort'], 'low')
        self.assertNotIn('reasoning_effort', calls[2])
        self.assertEqual(self.client.post('/api/batches', json={'reasoning_effort':'invalid'}).status_code, 422)
        cfg = {**self.cfg['model_service'], 'reasoning_effort':'invalid'}
        self.assertEqual(self.client.post('/api/config', json={'model_service':cfg}).status_code, 422)

    def test_duration_repair_and_failure_and_limits(self):
        long = json.dumps({'title':'太长', 'full_script':'字' * 180, 'shots':[{'text':'字' * 180, 'duration':33}]}, ensure_ascii=False)
        for limit in (30, 40, 50, 60):
            with patch.object(server, 'call_ai', return_value=self.result()) as ai:
                response = self.client.post('/api/generate', json={**self.slot, 'max_duration':limit, 'reasoning_effort':'medium'}).json()
            self.assertTrue(response['ok'])
            self.assertEqual(response['result']['audit']['max_duration'], limit)
            self.assertIn(f'不超过{limit}秒', ai.call_args.args[0])
            self.assertEqual(ai.call_args.kwargs['reasoning_effort'], 'medium')
        with patch.object(server, 'call_ai', side_effect=[long, self.result()]):
            repaired = self.client.post('/api/generate', json={**self.slot, 'max_duration':30}).json()
        self.assertTrue(repaired['result']['audit']['duration_repaired'])
        with patch.object(server, 'call_ai', return_value=long) as ai:
            failed = self.client.post('/api/generate', json={**self.slot, 'max_duration':30}).json()
        self.assertFalse(failed['ok'])
        self.assertEqual(ai.call_count, 2)
        self.assertIn('全文去空白180字', failed['error'])
        self.assertIn('分镜时长合计33.0秒', failed['error'])
        self.assertEqual(self.client.post('/api/generate', json={**self.slot, 'max_duration':45}).status_code, 422)
        # Timeline limit must also be enforced when the text itself is short.
        timeline = json.loads(self.result()); timeline['shots'][0]['duration'] = 31
        with patch.object(server, 'call_ai', return_value=json.dumps(timeline)) as ai:
            result = self.client.post('/api/generate', json={**self.slot, 'max_duration':30}).json()['result']
        self.assertEqual(ai.call_count, 1)
        self.assertTrue(result['audit']['duration_reestimated_from_text'])
        self.assertLessEqual(result['audit']['total_shot_seconds'], 30)

    def test_custom_count_and_settings_persist_in_batch(self):
        with patch.object(server, 'call_ai', return_value=self.result()):
            job_id = self.client.post('/api/batches', json={'count':7, 'max_duration':40, 'reasoning_effort':'low'}).json()['job_id']
            job = self.wait_job(job_id)
        self.assertEqual(len(job['items']), 7)
        self.assertEqual(job['settings']['max_duration'], 40)
        self.assertTrue(all(x['result']['reasoning_effort'] == 'low' for x in job['items']))

    def test_draft_collection_delete_restore_and_path_validation(self):
        record = json.loads(self.result()); record['material_code'] = '草稿测试'
        filename = self.client.post('/api/save-draft', json=record).json()['filename']
        url = '/api/drafts/' + filename
        self.assertTrue(self.client.patch(url, json={'collected':True}).json()['ok'])
        self.client.post('/api/save-draft', json=record)
        self.assertTrue(self.client.get('/api/history').json()['records'][0]['_collected'])
        token = self.client.delete(url).json()['undo_token']
        self.assertEqual(self.client.get('/api/history').json()['records'], [])
        self.assertEqual(self.client.delete(url).status_code, 404)
        self.assertTrue(self.client.post('/api/drafts/restore/' + token).json()['ok'])
        self.assertTrue(self.client.get('/api/history').json()['records'][0]['_collected'])
        self.assertEqual(self.client.post('/api/drafts/restore/bad').status_code, 422)
        self.assertEqual(self.client.delete('/api/drafts/C:outside.json').status_code, 422)

    @staticmethod
    def material_fixture():
        return {
            'weapons': [{'_record_id':f'w{i}', '武器名称':f'武器{i}'} for i in range(4)],
            'skills': [],
            'rival_struct': [{'_record_id':f'r{i}', '素材编号':f'R{i}', '口播文案':'给他一个机会。怪潮来了。点击下方试玩。'} for i in range(4)],
            'rival_shots': [{'_record_id':f's{i}', '分镜编号':f'TEST-S{i+1:02d}', '时间区间':f'{i*5}-{(i+1)*5}s',
                '关联竞对视频':[{'id':'r3'}], '对应口播':f'连续分镜{i+1}。'} for i in range(4)],
            'rival_comp': [{'_record_id':f'c{i}', '组件编号':f'{stage}-{i}', '组件类型':[stage]} for i, stage in enumerate(['CT','CTA','GZ','SD'])],
            'welfare': [{'_record_id':f'f{i}', '福利名称':f'福利{i}'} for i in range(4)],
            'gameplay': [{'_record_id':f'g{i}', '知识标题':title} for i, title in enumerate(['武器槽位','普通规则一','普通规则二','其他规则'])],
        }

    def test_coherent_shot_chain_stays_in_one_video_and_time_order(self):
        rows = [
            {'_record_id':'a2','分镜编号':'A-S02','时间区间':'5-10s','关联竞对视频':[{'id':'A'}]},
            {'_record_id':'b1','分镜编号':'B-S01','时间区间':'0-5s','关联竞对视频':[{'id':'B'}]},
            {'_record_id':'a1','分镜编号':'A-S01','时间区间':'0-5s','关联竞对视频':[{'id':'A'}]},
            {'_record_id':'a3','分镜编号':'A-S03','时间区间':'10-15s','关联竞对视频':[{'id':'A'}]},
            {'_record_id':'a4','分镜编号':'A-S04','时间区间':'40-45s','关联竞对视频':[{'id':'A'}]},
        ]
        with patch.object(server.random, 'choice', side_effect=lambda choices: choices[0]):
            chain = coherent_shot_chain('A', rows, limit=3)
        self.assertEqual([row['_record_id'] for row in chain], ['a1','a2','a3'])
        self.assertTrue(all('A' in {link['id'] for link in row['关联竞对视频']} for row in chain))

    def test_multirival_draw_does_not_fill_primary_shot_gaps_from_other_videos(self):
        cache = self.material_fixture()
        for i, rival in enumerate(cache['rival_struct']):
            rival['口播文案'] = f'来源{i}：遇到怪潮先稳住阵脚，再观察局势选择强化。'
        cache['rival_shots'] = [
            {'_record_id':'primary-1','分镜编号':'P-S01','时间区间':'0-5s','关联竞对视频':[{'id':'r0'}]},
            {'_record_id':'other-1','分镜编号':'O-S01','时间区间':'0-5s','关联竞对视频':[{'id':'r1'}]},
            {'_record_id':'other-2','分镜编号':'O-S02','时间区间':'5-10s','关联竞对视频':[{'id':'r1'}]},
        ]
        with patch.object(server, 'load_cache', side_effect=lambda name: cache.get(name, [])), \
             patch.object(server, 'calculate_all_usage', return_value=({}, {}, {}, {}, {})):
            slot = server.build_slot(mode='多篇竞对-多向融合', rival_id='r0')
        self.assertEqual([shot['id'] for shot in slot['shots']], ['primary-1'])
        self.assertEqual({shot['source_rival_code'] for shot in slot['shots']}, {'R0'})
        self.assertEqual(slot['stitched_blueprint']['shot_anchor_code'], 'R0')
        selected_codes = {rival['code'] for rival in slot['rivals']}
        selected_scripts = {rival['raw_script'] for rival in slot['rivals']}
        self.assertEqual({source['code'] for source in slot['stitched_blueprint']['sources']}, selected_codes)
        self.assertEqual(len(selected_codes), 3)
        for script in selected_scripts:
            self.assertIn(script, slot['stitched_ref_script'])
        self.assertNotIn('O-S01', slot['stitched_ref_script'])

        preview = self.client.post('/api/prompts/preview', json=slot).json()
        self.assertTrue(preview['ok'])
        for script in selected_scripts:
            self.assertIn(script, preview['user'])
        self.assertIn('各取至少一项融入新稿', preview['user'])

    def test_selected_ranges_small_pools_and_component_stages(self):
        cache = self.material_fixture()
        for shot in cache['rival_shots'][:2]:
            shot['关联竞对视频'] = [{'id':'r0'}, {'id':'r1'}]
        selected = server.MaterialSelection(weapons=['w1','w2'], rivals=['r0','r1'], shots=['s0','s1'],
                                            comps=['c0','c1'], welfares=['f1','f2'], gameplays=['g0','g1','g2'])
        seen_gameplay = set()
        with patch.object(server, 'load_cache', side_effect=lambda name: cache.get(name, [])), \
             patch.object(server, 'calculate_all_usage', return_value=({}, {}, {}, {}, {})):
            for seed in range(30):
                with patch.object(server, 'random', random.Random(seed)):
                    slot = server.build_slot(selection=selected)
                self.assertIn(slot['weapon']['id'], selected.weapons)
                self.assertEqual({r['id'] for r in slot['rivals']}, {'r0','r1'})
                self.assertEqual({s['id'] for s in slot['shots']}, {'s0','s1'})
                self.assertEqual([c['id'] for c in slot['components']], ['c0','c1'])
                self.assertIn(slot['welfare']['id'], selected.welfares)
                ids = {g['id'] for g in slot['gameplay_facts']}
                self.assertLessEqual(ids, set(selected.gameplays))
                seen_gameplay.update(ids)
            self.assertEqual(seen_gameplay, set(selected.gameplays))
            selected.rivals = ['r1']
            selected.comps = ['c1']
            slot = server.build_slot(selection=selected)
            self.assertEqual([r['id'] for r in slot['rivals']], ['r1'])
            self.assertEqual([c['id'] for c in slot['components']], ['c1'])

    def test_unselected_tabs_random_and_invalid_selection_rejected(self):
        cache = self.material_fixture()
        seen = {'weapons':set(), 'rivals':set(), 'shots':set(), 'welfares':set(), 'gameplays':set()}
        with patch.object(server, 'load_cache', side_effect=lambda name: cache.get(name, [])), \
             patch.object(server, 'calculate_all_usage', return_value=({}, {}, {}, {}, {})):
            for seed in range(30):
                with patch.object(server, 'random', random.Random(seed)):
                    slot = server.build_slot(selection=server.MaterialSelection(comps=['c1']))
                seen['weapons'].add(slot['weapon']['id'])
                seen['rivals'].update(r['id'] for r in slot['rivals'])
                seen['shots'].update(s['id'] for s in slot['shots'])
                seen['welfares'].add(slot['welfare']['id'])
                seen['gameplays'].update(g['id'] for g in slot['gameplay_facts'])
            for key in ['weapons','rivals','welfares']:
                self.assertEqual(len(seen[key]), 4)
            # Shot references are intentionally limited to the selected primary rival's
            # own continuous sequence; unrelated videos are no longer random fillers.
            self.assertTrue(seen['shots'])
            self.assertLessEqual(seen['shots'], {f's{i}' for i in range(4)})
            self.assertEqual(seen['gameplays'], set())
            opted_slot = server.build_slot(selection=server.MaterialSelection(comps=['c1'], gameplays=['g1']))
            self.assertEqual([g['id'] for g in opted_slot['gameplay_facts']], ['g1'])
            for key in server.MaterialSelection.model_fields:
                response = self.client.post('/api/batches', json={'selection':{key:['missing']}})
                self.assertEqual(response.status_code, 422)
            self.assertEqual(server.batch_jobs, {})
            cache['rival_struct'] = []
            self.assertEqual(server.build_slot(mode='本体事实-自主原创')['rivals'], [])

    def test_unused_weapon_does_not_lock_out_used_weapons(self):
        cache = self.material_fixture()
        usage = {'w0': 0, 'w1': 1, 'w2': 3, 'w3': 9}
        with patch.object(server, 'load_cache', side_effect=lambda name: cache.get(name, [])), \
             patch.object(server, 'calculate_all_usage', return_value=(usage, {}, {}, {}, {})):
            rng = random.Random(20261021)
            with patch.object(server.random, 'choices', wraps=rng.choices) as choice:
                slot = server.build_slot(mode='本体事实-自主原创')
            candidates = choice.call_args.args[0]
            self.assertEqual({r['_record_id'] for r in candidates}, set(usage))
            self.assertEqual(choice.call_args.kwargs['weights'], [1, .5, .25, .1])
            self.assertIn(slot['weapon']['id'], usage)
            with patch.object(server, 'random', rng):
                seen = {server.build_slot(mode='本体事实-自主原创')['weapon']['id'] for _ in range(100)}
            self.assertEqual(seen, set(usage))

    def test_reroll_avoids_previous_weapon_and_respects_explicit_selection(self):
        cache = self.material_fixture()
        usage = {'w0': 0, 'w1': 1, 'w2': 3, 'w3': 9}
        with patch.object(server, 'load_cache', side_effect=lambda name: cache.get(name, [])), \
             patch.object(server, 'calculate_all_usage', return_value=(usage, {}, {}, {}, {})):
            for mode in ['多篇竞对-多向融合', '单篇竞对-定向仿写', '竞对骨架-原创内容', '本体事实-自主原创']:
                response = self.client.get('/api/slot-machine', params={'mode':mode, 'avoid_weapon_id':'w0'})
                self.assertEqual(response.status_code, 200)
                self.assertNotEqual(response.json()['weapon']['id'], 'w0')
            slot = server.build_slot(weapon_id='w0', avoid_weapon_id='w0')
            self.assertEqual(slot['weapon']['id'], 'w0')
            selected = server.MaterialSelection(weapons=['w2'])
            slot = server.build_slot(selection=selected, avoid_weapon_id='w2')
            self.assertEqual(slot['weapon']['id'], 'w2')
            self.assertEqual([w['id'] for w in slot['weapon_candidates']], ['w2'])
            selected.weapons = ['w1', 'w2']
            slot = server.build_slot(selection=selected, avoid_weapon_id='w1')
            self.assertEqual(slot['weapon']['id'], 'w2')
            self.assertEqual({w['id'] for w in slot['weapon_candidates']}, {'w1', 'w2'})

    def test_batch_exhausts_weapon_pool_with_one_unused_weapon(self):
        cache = self.material_fixture()
        with patch.object(server, 'load_cache', side_effect=lambda name: cache.get(name, [])), \
             patch.object(server, 'calculate_all_usage', return_value=({'w0':0, 'w1':1, 'w2':3, 'w3':9}, {}, {}, {}, {})):
            draw = {}
            weapons = [server.build_slot(batch_draw=draw)['weapon']['id'] for _ in range(12)]
            self.assertTrue(all(len(set(weapons[i:i+4])) == 4 for i in range(0, 12, 4)))

    def test_slot_api_obeys_all_selected_ranges_and_rejects_pins_outside(self):
        cache = self.material_fixture()
        cache['rival_shots'][1]['关联竞对视频'] = [{'id':'r1'}]
        selection = dict(weapons=['w1','w2'], rivals=['r1'], shots=['s1'], comps=['c1'], welfares=['f1'], gameplays=['g1'])
        with patch.object(server, 'load_cache', side_effect=lambda name: cache.get(name, [])), \
             patch.object(server, 'calculate_all_usage', return_value=({}, {}, {}, {}, {})):
            for _ in range(10):
                response = self.client.post('/api/slot-machine', json={'selection':selection})
                self.assertEqual(response.status_code, 200)
                slot = response.json()
                self.assertIn(slot['weapon']['id'], selection['weapons'])
                self.assertEqual({w['id'] for w in slot['weapon_candidates']}, set(selection['weapons']))
                for key, field in [('rivals','rivals'), ('shots','shots'), ('comps','components'), ('gameplays','gameplay_facts')]:
                    self.assertEqual([r['id'] for r in slot[field]], selection[key])
                self.assertEqual(slot['welfare']['id'], 'f1')
            for key, pin in [('weapon_id','w0'), ('rival_id','r0')]:
                self.assertEqual(self.client.post('/api/slot-machine', json={'selection':selection,key:pin}).status_code, 422)
            self.assertEqual(self.client.post('/api/slot-machine', json={'selection':{'weapons':['missing']}}).status_code, 422)
            one = self.client.post('/api/slot-machine', json={'selection':{'weapons':['w2']},'avoid_weapon_id':'w2'}).json()
            self.assertEqual(one['weapon']['id'], 'w2')

    def test_level_is_opt_in_and_is_present_in_prompt_only_when_selected(self):
        cache = self.material_fixture()
        cache['levels'] = [{'_record_id':'lv231', '关卡号':231, '关卡名称':'孤胆岩谷', '关卡特性':'道路狭窄'}]
        with patch.object(server, 'load_cache', side_effect=lambda name: cache.get(name, [])), \
             patch.object(server, 'calculate_all_usage', return_value=({}, {}, {}, {}, {})):
            plain = server.build_slot(mode='本体事实-自主原创')
            self.assertIsNone(plain['level'])
            selected = server.build_slot(mode='本体事实-自主原创', selection=server.MaterialSelection(levels=['lv231']))
            self.assertEqual(selected['level']['id'], 'lv231')
            self.assertEqual(self.client.post('/api/prompts/preview', json=plain).status_code, 200)
            no_level_prompt = self.client.post('/api/prompts/preview', json=plain).json()['user']
            self.assertIn('不得出现具体关卡名称', no_level_prompt)
            self.assertNotIn('孤胆岩谷', no_level_prompt)
            level_prompt = self.client.post('/api/prompts/preview', json=selected).json()['user']
            self.assertIn('孤胆岩谷', level_prompt)
            self.assertIn('道路狭窄', level_prompt)
            invalid = {**plain, 'level':selected['level'], 'selection':{'levels':[]}}
            self.assertEqual(self.client.post('/api/generate', json=invalid).status_code, 422)

    def test_generate_and_prompt_preview_reject_stale_selection_before_model(self):
        cache = self.material_fixture()
        with patch.object(server, 'load_cache', side_effect=lambda name: cache.get(name, [])), \
             patch.object(server, 'calculate_all_usage', return_value=({}, {}, {}, {}, {})):
            slot = server.build_slot(mode='本体事实-自主原创', weapon_id='w0')
            with patch.object(server, 'call_ai') as ai:
                for endpoint in ['/api/generate', '/api/prompts/preview']:
                    self.assertEqual(self.client.post(endpoint, json={**slot,'selection':{'weapons':['w1']}}).status_code, 422)
                ai.assert_not_called()
            narrow = server.build_slot(mode='本体事实-自主原创', selection=server.MaterialSelection(weapons=['w1']))
            with patch.object(server, 'call_ai', return_value=self.result()):
                self.assertTrue(self.client.post('/api/generate', json={**narrow,'selection':{'weapons':['w1']}}).json()['ok'])

    def test_batch_draw_exhausts_pool_before_reuse(self):
        cache = self.material_fixture()
        with patch.object(server, 'load_cache', side_effect=lambda name:cache.get(name, [])), patch.object(server, 'calculate_all_usage', return_value=({}, {}, {}, {}, {})):
            for mode, size in [('单篇竞对-定向仿写',1), ('多篇竞对-多向融合',3)]:
                draw = {}
                slots = [server.build_slot(mode=mode, batch_draw=draw) for _ in range(12)]
                weapons = [s['weapon']['id'] for s in slots]
                self.assertEqual(len(set(weapons[:4])),4)
                self.assertEqual(set(draw['weapons'].values()),{3})
                rivals = [r['id'] for s in slots for r in s['rivals']]
                self.assertEqual(len(set(rivals[:4])),4)
                self.assertEqual(set(draw['rivals'].values()),{12*size//4})
                for s in slots:
                    self.assertEqual(len({r['id'] for r in s['rivals']}),size)
            with patch.object(server, 'call_ai', return_value=self.result()):
                job = self.wait_job(self.client.post('/api/batches',json={'count':4,'mode':'单篇竞对-定向仿写','selection':{'weapons':['w0','w1','w2','w3'],'rivals':['r0','r1','r2','r3']}}).json()['job_id'])
                self.assertEqual(len({s['slot']['weapon']['id'] for s in job['items']}),4)
                self.assertEqual(len({s['slot']['rivals'][0]['id'] for s in job['items']}),4)
                pools = [{w['id'] for w in s['slot']['weapon_candidates']} for s in job['items']]
                self.assertTrue(all(len(pool) == 1 for pool in pools))
                job = self.wait_job(self.client.post('/api/batches',json={'count':2,'mode':'单篇竞对-定向仿写'}).json()['job_id'])
                pools = [{w['id'] for w in s['slot']['weapon_candidates']} for s in job['items']]
                self.assertFalse(pools[0] & pools[1])
                self.assertEqual(pools[0] | pools[1], {'w0','w1','w2','w3'})
                job = self.wait_job(self.client.post('/api/batches',json={'count':6,'mode':'单篇竞对-定向仿写'}).json()['job_id'])
                self.assertTrue(all(len(s['slot']['weapon_candidates']) == 4 for s in job['items']))

    def test_generation_source_reference_and_actual_weapon_annotation(self):
        cache = self.material_fixture()
        with patch.object(server, 'load_cache', side_effect=lambda name:cache.get(name, [])), patch.object(server, 'calculate_all_usage', return_value=({}, {}, {}, {}, {})):
            slot = server.build_slot(mode='单篇竞对-定向仿写', selection=server.MaterialSelection(weapons=['w1','w2']))
            slot['rivals'][0]['raw_script'] = '真正的来源文案。'
            for text, ids in [('不讲武器，守住这波怪。',[]),('武器1和武器2一起攻击。',['w1','w2'])]:
                result = json.loads(self.result())
                result.update(full_script=text, ref_script='模型错误的原文', used_weapon_ids=['fake'])
                result['shots'][0].update(text=text, duration=10)
                with patch.object(server, 'call_ai',return_value=json.dumps(result,ensure_ascii=False)) as ai:
                    output = server.generate_script(server.GenerateRequest(**slot))['result']
                self.assertEqual(output['used_weapon_ids'],ids)
                self.assertIn('真正的来源文案。', output['ref_script'])
                self.assertNotIn('模型错误',output['ref_script'])
                self.assertIn('零把武器也完全有效',ai.call_args.args[0])
            slot['weapon_candidates'] = [w for w in slot['weapon_candidates'] if w['id'] == 'w1']
            result = json.loads(self.result())
            result.update(full_script='武器2来了。')
            result['shots'][0].update(text='武器2来了。', duration=10)
            with patch.object(server, 'call_ai', return_value=json.dumps(result, ensure_ascii=False)) as ai:
                output = server.generate_script(server.GenerateRequest(**slot))
            self.assertFalse(output['ok'])
            self.assertEqual(ai.call_count, 2)
            self.assertIn('候选范围外武器', output['error'])

    def test_multi_rival_generation_repairs_missing_source_contribution_coverage(self):
        cache = self.material_fixture()
        with patch.object(server, 'load_cache', side_effect=lambda name:cache.get(name, [])), \
             patch.object(server, 'calculate_all_usage', return_value=({}, {}, {}, {}, {})):
            slot = server.build_slot(mode='多篇竞对-多向融合')
            self.assertGreaterEqual(len(slot['rivals']), 2)
            codes = [r['code'] for r in slot['rivals']]
            first = json.loads(self.result())
            first['shots'][0]['ref_text'] = f'{codes[0]}：提供开场悬念'
            repaired = json.loads(self.result())
            repaired_text = '怪群压上来了，战车立刻开火。'
            repaired['full_script'] = repaired_text
            repaired['shots'][0].update(text=repaired_text, duration=8)
            repaired['shots'][0]['ref_text'] = '；'.join(f'{code}：来源独特动作转述→我方对应选择动作' for code in codes)
            with patch.object(server, 'call_ai', side_effect=[json.dumps(first, ensure_ascii=False), json.dumps(repaired, ensure_ascii=False)]) as ai:
                response = server.generate_script(server.GenerateRequest(**slot))
            self.assertTrue(response['ok'], response)
            self.assertEqual(ai.call_count, 2)
            self.assertEqual(response['result']['mode'], '多篇竞对-多向融合')
            self.assertTrue(all(code in response['result']['shots'][0]['ref_text'] for code in codes))

            with patch.object(server, 'call_ai', return_value=json.dumps(first, ensure_ascii=False)) as ai:
                failed = server.generate_script(server.GenerateRequest(**slot))
            self.assertFalse(failed['ok'])
            self.assertEqual(ai.call_count, 2)
            self.assertIn('多篇融合来源未在分镜中标注贡献', failed['error'])

            mapping_only = json.loads(self.result())
            mapping_only['shots'][0]['ref_text'] = '；'.join(f'{code}：只写风格和爽感' for code in codes)
            with patch.object(server, 'call_ai', return_value=json.dumps(mapping_only, ensure_ascii=False)) as ai:
                failed = server.generate_script(server.GenerateRequest(**slot))
            self.assertFalse(failed['ok'])
            self.assertEqual(ai.call_count, 2)
            self.assertIn('缺少来源独特内容到我方台词的逐项映射', failed['error'])

    def test_multi_rival_generation_repairs_verbatim_five_character_copy(self):
        cache = self.material_fixture()
        with patch.object(server, 'load_cache', side_effect=lambda name:cache.get(name, [])), \
             patch.object(server, 'calculate_all_usage', return_value=({}, {}, {}, {}, {})):
            slot = server.build_slot(mode='多篇竞对-多向融合')
            slot['rivals'][0]['raw_script'] += '撑不过三秒'
            copied = json.loads(self.result())
            copied_text = '撑不过三秒。'
            copied['full_script'] = copied_text
            copied['shots'][0].update(text=copied_text, duration=8)
            copied['shots'][0]['ref_text'] = '；'.join(f"{r['code']}：来源独特动作转述→我方对应选择动作" for r in slot['rivals'])
            repaired = json.loads(self.result())
            repaired_text = '火力逐步压住不断逼近的怪群。'
            repaired['full_script'] = repaired_text
            repaired['shots'][0].update(text=repaired_text, duration=8)
            repaired['shots'][0]['ref_text'] = copied['shots'][0]['ref_text']
            with patch.object(server, 'call_ai', side_effect=[json.dumps(copied, ensure_ascii=False), json.dumps(repaired, ensure_ascii=False)]) as ai:
                response = server.generate_script(server.GenerateRequest(**slot))
            self.assertTrue(response['ok'], response)
            self.assertEqual(ai.call_count, 2)
            self.assertNotIn('撑不过三秒', response['result']['full_script'])
            self.assertIn('连续5字原句', ai.call_args_list[1].args[0])

    def test_multi_rival_generation_repairs_unsupported_numeric_claims(self):
        cache = self.material_fixture()
        with patch.object(server, 'load_cache', side_effect=lambda name:cache.get(name, [])), \
             patch.object(server, 'calculate_all_usage', return_value=({}, {}, {}, {}, {})):
            slot = server.build_slot(mode='多篇竞对-多向融合')
            self.assertEqual(server.multi_rival_unverified_quantities('我再试一点。', server.GenerateRequest(**slot)), [])
            codes = [r['code'] for r in slot['rivals']]
            invented = json.loads(self.result())
            invented_text = '敌人移速翻倍，战车继续开火。'
            invented['full_script'] = invented_text
            invented['shots'][0].update(text=invented_text, duration=8)
            invented['shots'][0]['ref_text'] = '；'.join(f'{code}：来源因果转述→本镜升级动作' for code in codes)
            repaired = json.loads(self.result())
            repaired_text = '敌群靠近，战车继续开火。'
            repaired['full_script'] = repaired_text
            repaired['shots'][0].update(text=repaired_text, duration=8)
            repaired['shots'][0]['ref_text'] = invented['shots'][0]['ref_text']
            with patch.object(server, 'call_ai', side_effect=[json.dumps(invented, ensure_ascii=False), json.dumps(repaired, ensure_ascii=False)]) as ai:
                response = server.generate_script(server.GenerateRequest(**slot))
            self.assertTrue(response['ok'], response)
            self.assertEqual(ai.call_count, 2)
            self.assertNotIn('翻倍', response['result']['full_script'])
            self.assertIn('翻倍', ai.call_args_list[1].args[0])

    def test_batch_selection_sources_persist_and_single_mode_references(self):
        cache = self.material_fixture()
        cache['rival_shots'][1]['关联竞对视频'] = [{'id':'r1'}]
        selected = {'weapons':['w1'], 'rivals':['r1'], 'shots':['s1'], 'comps':['c1'], 'welfares':['f1'], 'gameplays':['g1'], 'levels':[]}
        with patch.object(server, 'load_cache', side_effect=lambda name: cache.get(name, [])), \
             patch.object(server, 'calculate_all_usage', return_value=({}, {}, {}, {}, {})), \
             patch.object(server, 'call_ai', return_value=self.result()) as ai:
            response = self.client.post('/api/batches', json={'count':4, 'mode':'单篇竞对-定向仿写', 'selection':selected})
            self.assertEqual(response.status_code, 200)
            job_id = response.json()['job_id']
            job = self.wait_job(job_id)
            self.assertEqual(job['status'], 'completed')
            self.assertIn('分镜与组件表达参考', ai.call_args.args[0])
            for item in job['items']:
                slot = item['result']['source_slot']
                self.assertEqual(slot['weapon']['id'], 'w1')
                self.assertEqual([x['id'] for x in slot['rivals']], ['r1'])
                self.assertEqual([x['id'] for x in slot['shots']], ['s1'])
                self.assertEqual([x['id'] for x in slot['components']], ['c1'])
                self.assertEqual(slot['welfare']['id'], 'f1')
                self.assertEqual([x['id'] for x in slot['gameplay_facts']], ['g1'])
            server.batch_jobs.clear()
            self.assertEqual(self.client.get('/api/batches/' + job_id).json()['job']['selection'], selected)
            self.assertEqual(len(self.client.get('/api/history').json()['records']), 4)
            response = self.client.post('/api/batches', json={'mode':'本体事实-自主原创', 'selection':selected})
            self.assertEqual(response.status_code, 422)


if __name__ == '__main__':
    unittest.main()
