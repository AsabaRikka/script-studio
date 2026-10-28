import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
import server
import prompt_settings
from feishu_writeback import build_plan, execute_plan
from test_writeback import FakeBase

class PromptRevisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = {name:server.load_cache(name) for name in ('main','weapons','skills','rival_struct','rival_shots','rival_comp','welfare','gameplay')}
        self.patches = [patch.object(server,'DATA_DIR',self.tmp.name),patch.object(server,'JOBS_DIR',str(Path(self.tmp.name)/'jobs')),
            patch.object(server,'load_cache',side_effect=lambda name:self.cache.get(name,[])),patch.object(server,'batch_jobs',{}),
            patch.object(prompt_settings,'OVERRIDE_FILE',Path(self.tmp.name)/'prompts.json')]
        for p in self.patches:p.start()
        self.client = TestClient(server.app)
        self.result = json.dumps({'title':'优化稿','full_script':'给他一个机会。','shots':[{'text':'给他一个机会。','duration':3,'visual':'怪潮靠近','game_action':'战车开火'}]},ensure_ascii=False)
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()
    def test_prompt_edits_preview_validation_conflict_and_generation(self):
        settings=self.client.get('/api/prompts').json()
        settings['templates']['system']+='\n测试编辑标记'
        self.assertEqual(self.client.put('/api/prompts',json={'templates':settings['templates'],'revision':0}).status_code,200)
        self.assertEqual(self.client.put('/api/prompts',json={'templates':settings['templates'],'revision':0}).status_code,409)
        broken=copy.deepcopy(settings['templates']); broken['user']=broken['user'].replace('{{gameplay}}','')
        self.assertEqual(self.client.put('/api/prompts',json={'templates':broken,'revision':1}).status_code,422)
        req=server.GenerateRequest(**server.build_slot(mode='本体事实-自主原创')).model_dump()
        with patch.object(server,'call_ai',return_value=self.result) as ai:
            preview=self.client.post('/api/prompts/preview',json=req).json()
            self.assertTrue(preview['ok']); self.assertIn('测试编辑标记',preview['system']); ai.assert_not_called()
            self.assertTrue(self.client.post('/api/generate',json=req).json()['ok'])
            self.assertEqual(ai.call_args.args,(preview['user'],preview['system']))
    def test_revision_uses_manual_source_and_saves_distinct_draft_with_reference(self):
        original={'material_code':'ORIGINAL','full_script':'原来的文案。','manual_script':'人工润色原稿。',
            'shots':[{'text':'原来的文案。','duration':3}], 'source_slot':server.build_slot(mode='本体事实-自主原创')}
        saved=server.save_draft(original); path=Path(self.tmp.name)/'saved_records'/saved['filename']; before=path.read_bytes()
        source=self.client.get('/api/rewrite-source',params={'kind':'draft','source_id':saved['filename']}).json()['record']
        self.assertEqual(source['manual_script'],'人工润色原稿。')
        req={'script':source['manual_script'],'source_kind':'draft','source_id':saved['filename'],'reference':'补充对照','shots':source['shots'],'scene_policy':'preserve'}
        with patch.object(server,'call_ai',return_value=self.result): response=self.client.post('/api/rewrite',json=req).json()
        self.assertTrue(response['ok']); result=response['result']; self.assertTrue(result['material_code'].startswith('REV-'))
        self.assertNotEqual(response['filename'],saved['filename']); self.assertEqual(path.read_bytes(),before)
        self.assertIn('人工润色原稿。',result['source_reference'])
        plan=build_plan({'data':result},server.load_cache); self.assertIn('补充对照',plan['parent']['参考文案'])
        fake=FakeBase(plan); first=execute_plan(plan,fake,self.tmp.name); second=execute_plan(plan,fake,self.tmp.name)
        self.assertEqual(first['record_id'],second['record_id']); self.assertEqual(len(fake.rows),2)
    def test_selection_replaces_all_requested_materials_without_random_sources(self):
        selected={key:[r['_record_id'] for r in self.cache[table][:4]] for key,table in [('weapons','weapons'),('rivals','rival_struct'),('shots','rival_shots'),('comps','rival_comp')]}
        payload,slot,_=server.prepare_revision(server.RevisionRequest(script='给他一个机会。',use_selection=True,selection=selected))
        for key,field in [('weapons','weapon_candidates'),('rivals','rivals'),('shots','shots'),('comps','components')]:
            self.assertEqual({r['id'] for r in slot[field]},set(selected[key]))
        self.assertEqual(slot['stitched_ref_script'],'')
        plain=server.prepare_revision(server.RevisionRequest(script='给他一个机会。'))[1]
        self.assertEqual(plain['weapon'],{}); self.assertEqual(plain['rivals'],[]); self.assertEqual(len(plain['weapon_candidates']),35)
    def test_library_links_and_manual_text_survive_without_random_reconstruction(self):
        rids=[r['_record_id'] for r in self.cache['rival_struct'][:4]]
        self.cache['main']=[{'_record_id':'parent','素材编号':'LIB','二创文案':'原文','人工修改稿':'人工稿','参考竞对':rids,'创作模式':'多篇竞对-多向融合'},
            {'_record_id':'child','父记录':['parent'],'分镜序号':1,'分镜文案':'原文','人工修改稿':'人工稿','分镜时长(秒)':3}]
        record=server.cached_manuscript('parent'); self.assertEqual(record['manual_script'],'人工稿')
        self.assertEqual(record['shots'][0]['text'],'人工稿'); self.assertEqual({r['id'] for r in record['source_slot']['rivals']},set(rids))
        self.assertEqual(record['source_slot']['weapon'],{}); self.assertIsNone(record['source_slot']['welfare'])
    def test_preserve_scene_count_rejects_invalid_ai_output(self):
        req={'script':'全文','scene_policy':'preserve','shots':[{'text':'一'},{'text':'二'}]}
        with patch.object(server,'call_ai',return_value=self.result) as ai:
            response=self.client.post('/api/rewrite',json=req).json()
            self.assertFalse(response['ok']); self.assertEqual(ai.call_count,2)
        self.assertEqual(server.get_history()['records'],[])
    def test_gameplay_stays_background_across_modes_and_profile_options(self):
        gp_id = self.cache['gameplay'][0]['_record_id']
        gp_title = self.cache['gameplay'][0]['知识标题']
        for mode in server.MODES:
            for profile in (False, True):
                slot=server.build_slot(mode=mode)
                self.assertEqual(slot['gameplay_facts'], [])
                preview=self.client.post('/api/prompts/preview',json={**slot,'enable_game_profile':profile}).json()
                self.assertTrue(preview['ok'])
                self.assertIn('不是必须依次经历的剧情',preview['system'])
                self.assertIn('先保留原句的叙事作用',preview['system'])
                self.assertNotIn('必须置换为',preview['system'])
                self.assertIn('本次未勾选玩法库，不注入玩法条目',preview['user'])
                self.assertNotIn(gp_title, preview['user'])
                if profile:
                    self.assertIn('画像开启不增加台词必写项',preview['user'])
                if mode=='本体事实-自主原创':
                    self.assertIn('仅在用户要求教学时写教学',preview['system'])
        selected_slot=server.build_slot(mode='本体事实-自主原创',selection=server.MaterialSelection(gameplays=[gp_id]))
        self.assertEqual([g['id'] for g in selected_slot['gameplay_facts']], [gp_id])
        selected_preview=self.client.post('/api/prompts/preview',json={**selected_slot,'selection':{'gameplays':[gp_id]}}).json()
        self.assertTrue(selected_preview['ok'])
        self.assertIn('【本次明确勾选的玩法条目：必须在口播正文中体现】',selected_preview['user'])
        self.assertIn(gp_title,selected_preview['user'])

    def test_prompt_version_switcher_presets_and_custom_versions(self):
        data = self.client.get('/api/prompts').json()
        self.assertTrue(data['ok'])
        version_ids = [v['id'] for v in data['versions']]
        self.assertIn('v5_scene_flow', version_ids)
        self.assertIn('v4_dynamic_story', version_ids)
        self.assertIn('v3_sample_master', version_ids)
        self.assertIn('v2_player_voice', version_ids)
        self.assertIn('v1_backup', version_ids)
        v1 = next(v for v in data['versions'] if v['id'] == 'v1_backup')
        v5 = next(v for v in data['versions'] if v['id'] == 'v5_scene_flow')
        self.assertEqual(data['active_version'], 'v5_scene_flow')
        # Switch to v1 backup
        switched = self.client.put('/api/prompts', json={'templates': v1['templates'], 'revision': data['revision']}).json()
        self.assertTrue(switched['ok'])
        self.assertEqual(switched['active_version'], 'v1_backup')
        # Save custom version v6
        custom_templates = copy.deepcopy(v5['templates'])
        custom_templates['system'] += '\n【v6测试标记】'
        saved_custom = self.client.put('/api/prompts', json={
            'templates': custom_templates,
            'revision': switched['revision'],
            'version_name': 'v6 · 测试自定义版'
        }).json()
        self.assertTrue(saved_custom['ok'])
        custom_entry = next(v for v in saved_custom['versions'] if v['name'] == 'v6 · 测试自定义版')
        self.assertFalse(custom_entry['builtin'])
        self.assertEqual(saved_custom['active_version'], custom_entry['id'])
        # Delete custom version, built-in cannot be deleted
        self.assertEqual(self.client.put('/api/prompts', json={
            'templates': v5['templates'],
            'revision': saved_custom['revision'],
            'delete_version_id': 'v1_backup'
        }).status_code, 422)
        deleted = self.client.put('/api/prompts', json={
            'templates': v5['templates'],
            'revision': saved_custom['revision'],
            'delete_version_id': custom_entry['id']
        }).json()
        self.assertTrue(deleted['ok'])
        self.assertNotIn(custom_entry['id'], [v['id'] for v in deleted['versions']])

    def test_multi_rival_rewrite_never_promotes_a_single_source_to_master(self):
        for rewrite_mode in ('structural', 'sentence_by_sentence'):
            slot=server.build_slot(mode='多篇竞对-多向融合')
            self.assertGreaterEqual(len(slot['rivals']),2)
            preview=self.client.post('/api/prompts/preview',json={**slot,'rewrite_mode':rewrite_mode}).json()
            self.assertTrue(preview['ok'])
            self.assertIn('不设主稿',preview['user'])
            self.assertIn('每项都能在新稿正文中辨认',preview['user'])
            self.assertIn('至少一个彼此不同的完整分句或完整叙事动作',preview['user'])
            self.assertIn('只借一个通用词、套话或尾句不能算融合',preview['user'])
            self.assertIn('重新安排钩子、压力、选择、转折和结果',preview['user'])
            for index,rival in enumerate(slot['rivals'],1):
                self.assertIn(f'融合来源{index} · {rival["code"]}',preview['user'])
                self.assertIn(rival['raw_script'],preview['user'])
            self.assertNotIn('保留原稿钩子、冲突、选择、兑现的顺序',preview['user'])

    def test_multi_rival_prompt_uses_auditable_source_contributions_not_sentence_pairs(self):
        slot=server.build_slot(mode='多篇竞对-多向融合')
        preview=self.client.post('/api/prompts/preview',json=slot).json()
        self.assertTrue(preview['ok'])
        self.assertIn('逐镜ref_text改为可审查的来源贡献标注',preview['user'])
        self.assertIn('禁止填竞对原句',preview['user'])
        self.assertIn('每篇已选来源都须在至少一镜标注',preview['user'])
        self.assertIn('不得只标注“帅气/有趣/高潮/满屏/收尾/爽感”',preview['user'])
        self.assertIn('连续5个汉字',preview['user'])
        self.assertIn('不得为体现来源贡献而移植其游戏数值',preview['user'])
        self.assertIn('原稿独特具体动作/因果（转述）→本镜我方对应台词或动作',preview['user'])
        self.assertNotIn('原句和改写句一一对应',preview['user'])
        self.assertNotIn('保留参考的句式和转折',preview['user'])
        self.assertTrue(all(r['code'] in preview['user'] for r in slot['rivals']))

    def test_single_rival_prompt_keeps_original_sentence_reference(self):
        slot=server.build_slot(mode='单篇竞对-定向仿写')
        preview=self.client.post('/api/prompts/preview',json=slot).json()
        self.assertTrue(preview['ok'])
        self.assertIn('单篇模式填写对应原句',preview['user'])
        self.assertIn('参考竞对原稿全文（待洗稿仿写蓝本）',preview['user'])

    def test_level_rule_is_opt_in_and_compatible_with_saved_prompt_overrides(self):
        self.cache['levels']=[{'_record_id':'lv231','关卡号':231,'关卡名称':'孤胆岩谷','关卡特性':'道路狭窄'}]
        plain=server.build_slot(mode='本体事实-自主原创')
        selected=server.build_slot(mode='本体事实-自主原创',selection=server.MaterialSelection(levels=['lv231']))
        no_level=self.client.post('/api/prompts/preview',json=plain).json()
        with_level=self.client.post('/api/prompts/preview',json=selected).json()
        self.assertTrue(no_level['ok']); self.assertIn('不得出现具体关卡名称',no_level['user'])
        self.assertNotIn('孤胆岩谷',no_level['user'])
        self.assertTrue(with_level['ok']); self.assertIn('孤胆岩谷',with_level['user'])
        self.assertIn('道路狭窄',with_level['user'])
        settings=prompt_settings.get_prompt_settings()
        self.assertNotIn('{{level}}',settings['templates']['user'])

    def test_batch_keeps_prompt_snapshot(self):
        with patch.object(server,'start_batch_thread'):
            response=self.client.post('/api/batches',json={'count':1,'mode':'本体事实-自主原创'}).json()
        job=server.batch_jobs[response['job_id']]; before=job['settings']['prompt_templates']['system']
        settings=prompt_settings.get_prompt_settings(); settings['templates']['system']+='更新后的规则'
        prompt_settings.save_prompt_settings(settings['templates'],settings['revision'])
        self.assertEqual(job['settings']['prompt_templates']['system'],before)

if __name__=='__main__':unittest.main()
