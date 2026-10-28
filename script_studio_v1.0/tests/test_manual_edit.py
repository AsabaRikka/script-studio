import copy
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
import server
from feishu_writeback import build_plan, execute_plan, WritebackError
from manual_edit import validate_manual
import test_writeback as writeback_fixture


class ManualEditTests(unittest.TestCase):
    def setUp(self):
        fixture = writeback_fixture.WritebackTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        self.request = copy.deepcopy(fixture.request)
        self.cache = fixture.cache
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.record = self.request['data']
        self.record['source_slot'] = {'weapon':{'id':'w1'}, 'rivals':[{'id':'r1'},{'id':'r2'}],
            'shots':[{'id':'s1'}], 'components':[{'id':'c1'}], 'mode':'多篇竞对-多向融合', 'emotions':['爽感']}
        self.manual = [dict(text='再给他一次机会。', visual='怪群靠近桥头', game_action='战车射击', duration=4),
                       dict(text='没办法，赌一手强化。', visual='强化卡片被选中', game_action='选择强化', duration=4)]

    def plan(self):
        return build_plan(self.request, lambda name:self.cache.get(name, []))

    def test_draft_save_reopen_conflict_and_ai_preservation(self):
        api = TestClient(server.app)
        with patch.object(server, 'DATA_DIR', self.tmp.name):
            filename = server.save_draft(self.record)['filename']
            url = '/api/draft-editor/' + filename
            self.assertEqual(api.get(url).json()['record']['shots'], self.record['shots'])
            script = '\n'.join(s['text'] for s in self.manual)
            payload = dict(manual_script=script, manual_shots=self.manual, revision=0)
            result = api.put(url, json=payload)
            self.assertEqual(result.status_code, 200)
            reopened = api.get(url).json()['record']
            self.assertEqual(reopened['manual_script'], script)
            self.assertEqual(reopened['manual_shots'], self.manual)
            self.assertEqual(reopened['shots'], self.record['shots'])
            self.assertEqual(reopened['full_script'], self.record['full_script'])
            self.assertEqual(reopened['source_slot'], self.record['source_slot'])
            self.assertEqual(api.put(url, json=payload).status_code, 409)
            self.assertEqual(api.post('/api/save-draft', json={**self.record, **payload}).status_code, 409)
            server.save_draft(copy.deepcopy(self.record))
            self.assertEqual(api.get(url).json()['record']['manual_script'], script)
            cleared = api.put(url, json=dict(manual_script='', manual_shots=None, revision=1))
            self.assertEqual(cleared.status_code, 200)
            self.assertEqual(cleared.json()['record']['manual_script'], '')
            self.assertEqual(api.get('/api/draft-editor/..%5Cprivate.json').status_code, 422)

    def test_free_text_and_legacy_without_scenes_remain_editable(self):
        api = TestClient(server.app)
        with patch.object(server, 'DATA_DIR', self.tmp.name), patch.object(server, 'JOBS_DIR', os.path.join(self.tmp.name,'missing')):
            filename = server.save_draft({'素材编号':'OLD', '二创文案':'旧稿全文', '核心卖点':'旧草稿', '人工修改稿':'之前修改'})['filename']
            url = '/api/draft-editor/' + filename
            self.assertEqual(api.get(url).json()['record']['manual_script'], '之前修改')
            result = api.put(url, json=dict(manual_script='手动填写的新稿', revision=0))
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()['record']['full_script'], '旧稿全文')
            self.assertEqual(result.json()['record']['shots'], [])
            self.assertEqual(api.post('/api/writeback-draft-preview', json={'filename':filename}).status_code, 422)

    def test_manual_validation_and_invalid_draft_is_not_written(self):
        for shots, script in [(self.manual[:1], '不一致'),
                              ([{**s,'duration':float('nan')} for s in self.manual], '\n'.join(s['text'] for s in self.manual)),
                              ([{**s,'text':''} for s in self.manual], '')]:
            with self.assertRaises(ValueError): validate_manual(self.record, script, shots)
        with patch.object(server, 'DATA_DIR', self.tmp.name):
            filename = server.save_draft(self.record)['filename']
            result = TestClient(server.app).put('/api/draft-editor/' + filename, json={'manual_script':'错误', 'manual_shots':self.manual[:1]})
            self.assertEqual(result.status_code, 422)
            with open(os.path.join(self.tmp.name,'saved_records',filename),encoding='utf8') as f:
                self.assertNotIn('manual_script', json.load(f))

    def test_feishu_manual_mapping_update_same_records_and_clear(self):
        initial = self.plan()
        self.record.update(manual_script='\n'.join(s['text'] for s in self.manual), manual_shots=self.manual)
        revised = self.plan()
        client = writeback_fixture.FakeBase(revised)
        first = execute_plan(initial, client, self.tmp.name)
        result = execute_plan(revised, client, self.tmp.name)
        self.assertEqual(first['record_id'], result['record_id'])
        self.assertEqual(first['child_record_ids'], result['child_record_ids'])
        self.assertEqual(len(client.rows),3)
        parent = client.rows[result['record_id']]['fields']
        self.assertEqual(parent['二创文案'], self.record['full_script'])
        self.assertEqual(parent['人工修改稿'], self.record['manual_script'])
        for index, rid in enumerate(result['child_record_ids']):
            child = client.rows[rid]['fields']
            self.assertEqual(child['分镜文案'], self.record['shots'][index]['text'])
            self.assertEqual(child['人工修改稿'], self.manual[index]['text'])
            self.assertEqual(child['分镜画面描述'], self.manual[index]['visual'])
            self.assertEqual(child['分镜时长(秒)'], self.manual[index]['duration'])
        self.record.update(manual_script='整篇单独修改。', manual_shots=None)
        execute_plan(self.plan(), client, self.tmp.name)
        for index, rid in enumerate(result['child_record_ids']):
            self.assertIsNone(client.rows[rid]['fields']['人工修改稿'])
            self.assertEqual(client.rows[rid]['fields']['分镜时长(秒)'], self.record['shots'][index]['duration'])
        self.record['manual_script'] = ''
        execute_plan(self.plan(), client, self.tmp.name)
        self.assertIsNone(client.rows[result['record_id']]['fields']['人工修改稿'])

    def test_writeback_limits_and_changed_original_block_overwrite(self):
        self.record.update(manual_script='字'*169)
        self.assertEqual(self.plan()['parent']['人工修改稿'], '字'*169)
        self.record.update(manual_script='\n'.join(s['text'] for s in self.manual), manual_shots=self.manual)
        self.manual[0]['duration'] = 30
        self.assertEqual(self.plan()['total_duration'],34)
        self.manual[0]['duration'] = .1
        self.assertEqual(self.plan()['children'][0]['分镜时长(秒)'], .1)
        self.manual[0]['duration'] = 4
        plan = self.plan()
        client = writeback_fixture.FakeBase(plan)
        written = execute_plan(plan, client, self.tmp.name)
        client.rows[written['child_record_ids'][0]]['fields']['分镜文案'] = '远端原稿已被改变'
        self.manual[0]['visual'] = '另一个画面'
        with self.assertRaisesRegex(WritebackError,'远端分镜原稿'): execute_plan(self.plan(), client, self.tmp.name)

    def test_unsynced_fulltext_preserves_scenes_and_clears_child_dialogue(self):
        self.record.update(manual_script='自由全文，不按镜头分段。'*100, manual_shots=self.manual)
        self.manual[0]['duration'] = None
        plan = self.plan()
        self.assertEqual(plan['parent']['人工修改稿'], self.record['manual_script'])
        self.assertIsNone(plan['children'][0]['人工修改稿'])
        self.assertEqual(plan['children'][0]['分镜画面描述'], self.manual[0]['visual'])
        self.assertEqual(plan['children'][0]['分镜时长(秒)'], 0)
        validate_manual(self.record, self.record['manual_script'], self.manual)

    def test_actual_zero_or_multiple_weapon_links(self):
        self.cache['weapons'] = [{'_record_id':'w1', '武器名称':'篮球'}, {'_record_id':'w2', '武器名称':'水滴'}]
        self.record.update(manual_script='篮球和水滴一起攻击。')
        plan = self.plan()
        self.assertEqual(plan['parent']['关联武器'], [{'id':'w1'},{'id':'w2'}])
        client = writeback_fixture.FakeBase(plan)
        first = execute_plan(plan, client, self.tmp.name)
        self.record['manual_script'] = '给他一次机会，怪潮终于退了。'
        second = execute_plan(self.plan(), client, self.tmp.name)
        self.assertEqual(first['record_id'], second['record_id'])
        self.assertEqual(client.rows[first['record_id']]['fields']['关联武器'], [])


if __name__ == '__main__': unittest.main()
