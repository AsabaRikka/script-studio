import copy
import json
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
import server
from script_sources import normalize_source_ids
from feishu_writeback import build_plan, execute_plan, link_ids
import test_writeback


class SourceIdTests(unittest.TestCase):
    def test_only_unique_selected_codes_resolve_and_ids_are_deduplicated(self):
        rows = [{'id':'rec-one','code':'CTA-064'}, {'id':'rec-outside','code':'SD-091'}]
        self.assertEqual(normalize_source_ids([' CTA-064 ', 'rec-one'], ['rec-one'], rows, '第7镜参考组件'), ['rec-one'])
        self.assertEqual(normalize_source_ids(None, ['rec-one'], rows, '参考组件'), [])
        for value in (['SD-091'], ['unknown'], {}, 'CTA-064', [1], ['']):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_source_ids(value, ['rec-one'], rows, '第7镜参考组件')
        rows.append({'id':'rec-two','code':'CTA-064'})
        with self.assertRaisesRegex(ValueError, '对应多条'):
            normalize_source_ids(['CTA-064'], ['rec-one','rec-two'], rows, '第7镜参考组件')
        self.assertEqual(normalize_source_ids(['rec-one'], ['rec-one','rec-two'], rows, '参考组件'), ['rec-one'])

    def test_legacy_plan_resolves_snapshot_without_mutating_draft_and_reuses_ids(self):
        fixture = test_writeback.WritebackTests()
        fixture.setUp()
        try:
            request = copy.deepcopy(fixture.request)
            request['data']['source_slot'] = {
                'shots':[{'id':'s1','code':'SHOT-001'}],
                'components':[{'id':'c1','code':'CTA-064'}]}
            request['data']['shots'][0]['source_shot_ids'] = ['SHOT-001']
            request['data']['shots'][1]['source_comp_ids'] = ['CTA-064']
            before = copy.deepcopy(request)
            plan = build_plan(request, lambda name:fixture.cache.get(name, []))
            self.assertEqual(request, before)
            self.assertEqual(link_ids(plan['children'][0]['参考竞对分镜']), ['s1'])
            self.assertEqual(link_ids(plan['children'][1]['参考组件']), ['c1'])
            fake = test_writeback.FakeBase(plan)
            first = execute_plan(plan, fake, fixture.tmp.name)
            second = execute_plan(plan, fake, fixture.tmp.name)
            self.assertEqual(first['record_id'], second['record_id'])
            self.assertEqual(first['child_record_ids'], second['child_record_ids'])
            self.assertEqual(len(fake.rows), 3)
            # Current preview/cache cannot expand a persisted manuscript's range.
            fixture.cache['rival_comp'] = [{'_record_id':'c2','组件编号':'SD-091'}]
            request['comp_ids'] = ['c1','c2']
            request['data']['shots'][1]['source_comp_ids'] = ['SD-091']
            with self.assertRaisesRegex(ValueError, '第2镜参考组件.*范围外.*SD-091'):
                build_plan(request, lambda name:fixture.cache.get(name, []))
            # Legacy requests without snapshots use cache metadata only for selected IDs.
            request['data'].pop('source_slot')
            fixture.cache['rival_shots'] = [{'_record_id':'s1','分镜编号':'SHOT-001'}]
            fixture.cache['rival_comp'] = [{'_record_id':'c1','组件编号':'CTA-064'}]
            request['comp_ids'] = ['c1']
            request['data']['shots'][1]['source_comp_ids'] = ['CTA-064']
            self.assertEqual(link_ids(build_plan(request, lambda name:fixture.cache.get(name, []))['children'][1]['参考组件']), ['c1'])
        finally:
            fixture.tearDown()

    def test_generation_maps_display_codes_and_repairs_only_real_invalid_references(self):
        slot = server.build_slot(mode='单篇竞对-定向仿写')
        trace_temp = tempfile.TemporaryDirectory()
        self.addCleanup(trace_temp.cleanup)
        storage = patch.object(server, 'DATA_DIR', trace_temp.name)
        storage.start(); self.addCleanup(storage.stop)
        slot['shots'] = [{'id':'rec-shot','code':'SHOT-001','text':'参考句','desc':'画面'}]
        slot['components'] = [{'id':'rec-comp','code':'CTA-064','text':'参考结尾','type':'CTA'}]
        valid = {'full_script':'给他一个机会。','shots':[{'text':'给他一个机会。','duration':3,
                 'source_shot_ids':['SHOT-001'], 'source_comp_ids':['CTA-064']}]}
        client = TestClient(server.app)
        with patch.object(server, 'call_ai', return_value=json.dumps(valid, ensure_ascii=False)) as ai:
            response = client.post('/api/generate', json=slot).json()
        self.assertTrue(response['ok'], response)
        self.assertEqual(ai.call_count, 1)
        self.assertIn('记录ID: rec-comp', ai.call_args.args[0])
        self.assertEqual(response['result']['shots'][0]['source_shot_ids'], ['rec-shot'])
        self.assertEqual(response['result']['shots'][0]['source_comp_ids'], ['rec-comp'])
        invalid = copy.deepcopy(valid)
        invalid['shots'][0]['source_comp_ids'] = ['SD-091']
        with patch.object(server, 'call_ai', side_effect=[json.dumps(invalid),json.dumps(valid)]) as ai:
            repaired = client.post('/api/generate', json=slot).json()
        self.assertTrue(repaired['ok'], repaired)
        self.assertEqual(ai.call_count, 2)
        self.assertIn('第1镜参考组件', ai.call_args.args[0])
        self.assertIn('SD-091', ai.call_args.args[0])
        with patch.object(server, 'call_ai', return_value=json.dumps(invalid)) as ai:
            rejected = client.post('/api/generate', json=slot).json()
        self.assertFalse(rejected['ok'])
        self.assertEqual(ai.call_count, 2)
        self.assertIn('素材引用错误', rejected['error'])
        self.assertIn('第1镜参考组件', rejected['error'])
