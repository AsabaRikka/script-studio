import copy
import io
import json
import unittest
import zipfile
from unittest.mock import patch

import server
from prompt_optimizer import SCORE_POLICY, PROPOSAL_POLICY, diagnose_script, partition_cases
import test_quality_mvp as legacy


class PromptOptimizerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = legacy.QualityMVPTests('test_generation_trace_captures_real_render_and_immutable_template')
        self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        self.client, self.store = self.fixture.client, self.fixture.store
        self.records=[]
        for index in range(3):
            self.fixture.slot = server.build_slot(rival_id='r'+str(index), mode='竞对骨架-原创内容',
                selection=server.MaterialSelection(weapons=['w0'], rivals=['r'+str(index)]), hide_used_rivals=False)
            self.fixture.slot['weapon_candidates']=[self.fixture.slot['weapon']]
            self.records.append(self.fixture.saved_record('OPT-'+str(index)))
        self.settings={'mode':'竞对骨架-原创内容','objective':'保留玩家当场动作和选择，避免说明书与无依据清场保证。',
                       'model':'test-model','sample_count':3,'max_proposals':1,'max_calls':40,
                       'filenames':[r['_filename'] for r in self.records]}
        self.original=self.fixture.respond
        self.requests=[]
        self.patch=patch('server.urllib.request.urlopen',side_effect=self.respond);self.patch.start();self.addCleanup(self.patch.stop)

    def respond(self, request, **kwargs):
        body=json.loads(request.data);self.requests.append(body)
        system=body['messages'][0]['content'];user=body['messages'][-1]['content']
        if system==PROPOSAL_POLICY:
            data=json.loads(user);text=data['current']['mode_2']+'\n优化后看玩家眼前动作和选择。'
            result={'text':text,'reason':'修复说明书式介绍','risk':'可能减少必要的事实解释'}
        elif system==SCORE_POLICY:
            data=json.loads(user);improved='我看到了' in data['script']
            result={'scores':{k:4 if improved else 2 for k in ['口语','开头','推进','来源迁移','风格']},
                    'usable':True,'evidence':data['script'][:5],'reason':'动作推进较明确' if improved else '原稿仍像说明书','fact_concerns':[]}
        elif system.startswith('你是中文游戏买量口播编辑'):
            data=json.loads(user);winner=next(side for side,text in data['texts'].items() if '我看到了' in text)
            result={'decision':winner,'reason':'眼前动作更明确','evidence_A':data['texts']['A'][:5],
                    'evidence_B':data['texts']['B'][:5],'scores':{'A':{'口语':3},'B':{'口语':3}},'fact_concerns':[]}
        else:
            improved='优化后看玩家眼前动作和选择' in system+user
            text='我看到了桥口的怪，等它靠近才出手。' if improved else '我盯住桥口，等它靠近才出手。'
            result={'full_script':text,'shots':[{'text':text,'duration':5,'visual':'桥口','game_action':'等待'}]}
        return io.BytesIO(json.dumps({'choices':[{'message':{'content':json.dumps(result,ensure_ascii=False)}}]}).encode())

    def run_optimization(self):
        preview=self.client.post('/api/quality/optimizer/preview',json=self.settings)
        self.assertEqual(preview.status_code,200,preview.text)
        dataset=preview.json()['dataset']
        response=self.client.post('/api/quality/optimizer/runs',json={**self.settings,'dataset_id':dataset['id']})
        self.assertEqual(response.status_code,200,response.text)
        job=self.fixture.wait(response.json()['job']['id'])
        return dataset,job

    def test_focus_diagnostics_are_structured_and_reference_is_not_a_gold_copy(self):
        bad='打这关千万别大意！直接上三眼射线！光束瞬间穿透全场，甚至疯狂清屏！'
        reference='打这关千万别大意。接着敌人登场了，但是问题不大。好在我们还有侧面火力。'
        diagnostics=diagnose_script(bad, {'weapon': {'name': '三眼射线', 'feature': '三条射线汇聚穿透'}}, reference)
        self.assertEqual(diagnostics['exclamation_count'],3)
        self.assertGreaterEqual(diagnostics['intensifier_count'],4)
        self.assertEqual(diagnostics['reference']['exclamation_count'],0)
        self.assertIn('感叹号过密', diagnostics['flags'])
        self.assertIn('强程度副词堆叠', diagnostics['flags'])

    def test_editorial_score_survives_a_safety_flag_for_gepa_learning(self):
        original=self.respond
        def flagged(request,**kwargs):
            body=json.loads(request.data); system=body['messages'][0]['content']
            if system==SCORE_POLICY:
                data=json.loads(body['messages'][-1]['content'])
                return io.BytesIO(json.dumps({'choices':[{'message':{'content':json.dumps({'scores':{k:5 for k in ['口语','开头','推进','来源迁移','风格']},'usable':True,'evidence':data['script'][:5],'reason':'风格改进但仍需核实','fact_concerns':[]},ensure_ascii=False)}}]}).encode())
            return original(request,**kwargs)
        with patch('server.urllib.request.urlopen',side_effect=flagged):
            _,job=self.run_optimization()
        scored=[row for row in job['items'] if row.get('review')]
        self.assertTrue(scored)
        self.assertTrue(all('editorial_score' in row for row in scored))
        self.assertTrue(all('safe_for_adoption' in row for row in scored))

    def test_real_gepa_search_independent_holdout_apply_and_promptfoo_export(self):
        before={p.name:p.read_bytes() for p in (self.fixture.root/'saved_records').glob('*.json')}
        dataset,job=self.run_optimization()
        self.assertEqual(job['status'],'completed',job.get('error'))
        self.assertEqual(job['engine']['version'],'0.1.4')
        self.assertGreaterEqual(len(job['gepa_result']['candidates']),2)
        self.assertTrue(job['result']['recommended'],job.get('result'))
        self.assertEqual(job['result']['wins'],1)
        self.assertEqual(len(job['calls']),14, [(c['phase'], c['model']) for c in job['calls']])
        self.assertEqual(before,{p.name:p.read_bytes() for p in (self.fixture.root/'saved_records').glob('*.json')})
        reflective=[r for r in self.requests if r['messages'][0]['content']==PROPOSAL_POLICY]
        self.assertEqual(len(reflective),1)
        holdout=dataset['splits']['holdout'][0]
        self.assertNotIn(holdout,reflective[0]['messages'][-1]['content'])
        frozen=self.store.read('datasets',dataset['id'])
        held=next(c for c in frozen['cases'] if c['filename']==holdout)
        for source in held['request']['rivals']:
            self.assertNotIn(source['raw_script'],reflective[0]['messages'][-1]['content'])
        state=self.client.get('/api/quality/versions').json()
        applied=self.client.post('/api/quality/optimizer/runs/'+job['id']+'/apply',json={
            'prompt_revision':state['prompt_revision'],'state_revision':state['state_revision']})
        self.assertEqual(applied.status_code,200,applied.text)
        current=self.client.get('/api/quality/versions').json()
        self.assertEqual(next(v['id'] for v in current['versions'] if v['active']),job['result']['candidate_id'])
        self.assertTrue(next(v['retired'] for v in current['versions'] if v['id']==job['result']['base_id']))
        exported=self.client.get('/api/quality/optimizer/runs/'+job['id']+'/promptfoo')
        archive=zipfile.ZipFile(io.BytesIO(exported.content))
        self.assertEqual(set(archive.namelist()),{'promptfooconfig.json','results.json','replay.cjs','README.txt'})
        target=self.fixture.root/'promptfoo';archive.extractall(target)
        # Retain an isolated package for actual CLI replay, containing mock examples only.
        import shutil
        from pathlib import Path
        evidence=Path(server.__file__).parent/'output/prompt-optimizer-rebuild-20261026/promptfoo-smoke'
        evidence.mkdir(parents=True,exist_ok=True)
        for path in target.iterdir():shutil.copy2(path,evidence/path.name)

    def test_shared_source_families_cannot_cross_splits(self):
        cases=[{'filename':'a','group_keys':['r0']},{'filename':'b','group_keys':['r0','r1']},
               {'filename':'c','group_keys':['r1']},{'filename':'d','group_keys':['r2']}]
        with self.assertRaises(ValueError):partition_cases(cases)
        cases.append({'filename':'e','group_keys':['r3']});splits,count=partition_cases(cases)
        self.assertEqual(count,3)
        family=next(names for names in splits.values() if 'a' in names)
        self.assertTrue({'a','b','c'}.issubset(family))

    def test_preview_has_no_model_calls_and_changed_settings_are_rejected(self):
        self.settings['rewrite_mode']='sentence_by_sentence'
        count=len(self.fixture.calls);preview=self.client.post('/api/quality/optimizer/preview',json=self.settings)
        self.assertEqual(preview.status_code,200,preview.text);self.assertEqual(len(self.fixture.calls),count)
        frozen=self.store.read('datasets',preview.json()['dataset']['id'])
        self.assertTrue(all(c['request']['rewrite_mode']=='sentence_by_sentence' for c in frozen['cases']))
        response=self.client.post('/api/quality/optimizer/runs',json={**self.settings,'dataset_id':preview.json()['dataset']['id'],'objective':'改成完全不同的优化目标和写作要求。'})
        self.assertEqual(response.status_code,409)

    def test_invalid_candidate_is_eliminated_without_weakening_templates(self):
        original=self.respond
        def invalid(request,**kwargs):
            body=json.loads(request.data)
            if body['messages'][0]['content']==PROPOSAL_POLICY:
                return io.BytesIO(json.dumps({'choices':[{'message':{'content':json.dumps({'text':'{{bad_variable}}','reason':'test','risk':'test'})}}]}).encode())
            return original(request,**kwargs)
        with patch('server.urllib.request.urlopen',side_effect=invalid):
            _,job=self.run_optimization()
        self.assertEqual(job['status'],'completed',job.get('error'))
        self.assertEqual(job['proposals'][0]['status'],'eliminated')
        self.assertFalse(job['result']['recommended'])
        self.assertEqual(job['result']['outcome'],'no_improvement')

    def test_withdrawn_creative_actions_do_not_accept_paid_work(self):
        count=len(self.fixture.calls)
        for route in ('/creative/plan','/creative/batches'):
            self.assertEqual(self.client.post('/api/quality'+route,json={}).status_code,410)
        self.assertEqual(len(self.fixture.calls),count)

    def test_call_budget_stops_and_interrupted_job_never_resubmits(self):
        self.settings['max_calls']=12
        _,job=self.run_optimization()
        self.assertLessEqual(len(job['calls']),12)
        fake={**job,'id':'e'*32,'status':'running'};self.store.write('jobs',fake['id'],fake)
        count=len(self.requests)
        response=self.client.get('/api/quality/jobs/'+fake['id']).json()
        self.assertEqual(response['job']['status'],'interrupted')
        self.assertEqual(len(self.requests),count)

    def test_stop_is_terminal_and_does_not_repeat_inflight_generation(self):
        import threading
        entered=threading.Event();release=threading.Event();original=self.respond
        def slow(request,**kwargs):
            entered.set();release.wait(4);return original(request,**kwargs)
        preview=self.client.post('/api/quality/optimizer/preview',json=self.settings).json()['dataset']
        with patch('server.urllib.request.urlopen',side_effect=slow):
            started=self.client.post('/api/quality/optimizer/runs',json={**self.settings,'dataset_id':preview['id']}).json()
            job_id=started['job']['id'];self.assertTrue(entered.wait(2))
            self.assertEqual(self.client.post('/api/quality/optimizer/runs',json={**self.settings,'dataset_id':preview['id']}).status_code,409)
            self.client.post('/api/quality/jobs/'+job_id+'/stop',json={});release.set();job=self.fixture.wait(job_id)
        self.assertEqual(job['status'],'stopped',job)
        self.assertEqual(len(job['calls']),1)


if __name__=='__main__':unittest.main()
