import copy
import json
import os
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
import server
import rival_visibility as rv
from feishu_writeback import WritebackError
import test_studio


class RivalVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, 'status.json')
        self.target = {'base_token': 'local-test', 'table_ids': {'rival_struct': 'table-test'}}
        self.cache = test_studio.StudioTests.material_fixture()
        self.cache['rival_shots'][0]['关联竞对视频'] = [{'id': 'r0'}]
        self.cache['rival_shots'][1]['关联竞对视频'] = ['r1', 'r0']
        self.cache['rival_comp'][0]['关联竞对视频'] = ['r0']
        self.snapshot = {'target': rv.target_key(self.target), 'checked_at': time.time(),
                         'updated_at': '2026-10-20T10:00:00+08:00',
                         'statuses': {'r0': ['已使用'], 'r1': ['已确认'], 'r2': [], 'r3': ['待确认']}}
        for p in [patch.object(server, 'DATA_DIR', self.tmp.name),
                  patch.object(server, 'load_cache', side_effect=lambda n: self.cache.get(n, [])),
                  patch.object(server, 'visibility_snapshot', return_value=self.snapshot),
                  patch.object(server, 'calculate_all_usage', return_value=({}, {}, {}, {}, {}))]:
            p.start()
            self.addCleanup(p.stop)
        self.client = TestClient(server.app)

    def test_filter_cascades_and_toggle_restores_without_mutation(self):
        before = copy.deepcopy(self.cache)
        blocked = rv.hidden_ids(self.snapshot, server.load_cache)
        self.assertEqual(blocked, {'rivals': {'r0'}, 'shots': {'s0', 's1'}, 'components': {'c0'}})
        filtered = self.client.get('/api/overview?hide_used_rivals=true').json()
        self.assertEqual(filtered['counts']['rival_struct'], 3)
        self.assertEqual(filtered['visibility']['hidden'], {'rivals': 1, 'shots': 2, 'components': 1})
        self.assertEqual(self.client.get('/api/overview').json()['counts']['rival_struct'], 4)
        self.assertEqual(self.cache, before)

    def test_sampling_excludes_all_three_pools(self):
        for _ in range(30):
            slot = server.build_slot(hide_used_rivals=True)
            self.assertNotIn('r0', [r['id'] for r in slot['rivals']])
            self.assertFalse({'s0', 's1'} & {r['id'] for r in slot['shots']})
            self.assertNotIn('c0', [r['id'] for r in slot['components']])

    def test_explicit_hidden_selection_and_pinned_rival_are_rejected(self):
        for key, value in [('rivals','r0'), ('shots','s0'), ('comps','c0')]:
            response = self.client.post('/api/batches', json={'hide_used_rivals': True, 'selection': {key:[value]}})
            self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.get('/api/slot-machine?hide_used_rivals=true&rival_id=r0').status_code, 422)

    def test_generate_rejects_old_combination_before_model_call(self):
        slot = server.build_slot(rival_id='r0')
        with patch.object(server, 'call_ai') as ai:
            response = self.client.post('/api/generate', json={**slot, 'hide_used_rivals':True})
        self.assertEqual(response.status_code, 422)
        ai.assert_not_called()

    def test_old_sources_preserved_but_new_revision_choices_rejected(self):
        selected = server.MaterialSelection(rivals=['r0'], shots=['s0'], comps=['c0'])
        old = server.revision_materials(selected)
        self.assertEqual(old['rivals'][0]['id'], 'r0')
        response = self.client.post('/api/rewrite/prepare', json={'script':'保留旧稿。', 'use_selection':True,
            'selection':selected.model_dump(), 'hide_used_rivals':True})
        self.assertEqual(response.status_code, 422)
        with patch.object(server, 'rewrite_source', return_value={'record': {'source_slot':old}}):
            payload, slot, _ = server.prepare_revision(server.RevisionRequest(
                script='保留旧稿。', source_kind='draft', source_id='old.json', hide_used_rivals=True))
        self.assertEqual(slot['rivals'][0]['id'], 'r0')
        self.assertFalse(payload.hide_used_rivals)

    def test_accepted_batch_execution_keeps_source_snapshot(self):
        slot = server.build_slot(rival_id='r0')
        job = {'id':'a'*32, 'items':[{'index':1,'code':'TEST','status':'pending','slot':slot}],
               'settings':{'model':'mock','hide_used_rivals':True}, 'cancel_requested':False}
        with patch.dict(server.batch_jobs, {job['id']:job}), patch.object(server, 'persist_job'), \
             patch.object(server, 'generate_script', return_value={'ok':False,'error':'stop'}) as generate:
            server.run_batch(job['id'])
        self.assertFalse(generate.call_args.args[0].hide_used_rivals)
        self.assertEqual(generate.call_args.args[0].rivals, slot['rivals'])

    def test_exact_status_and_unlinked_materials(self):
        self.snapshot['statuses']['r0'] = ['未使用']
        self.assertEqual(rv.hidden_ids(self.snapshot, server.load_cache)['components'], set())
        self.assertEqual(rv.status_labels(' 已使用 '), ['已使用'])
        for bad in ({'name':'已使用'}, [1]):
            with self.assertRaises(WritebackError):
                rv.status_labels(bad)

    def fake_client(self, pages):
        schema = {'fields':[{'name':'状态','type':'select','options':[{'name':'已使用'}]}]}
        return Mock(run=Mock(side_effect=[schema, *pages]))

    @staticmethod
    def page(ids, values, more=False, rev=1):
        return {'fields':['状态'], 'data':[[v] for v in values], 'record_id_list':ids, 'has_more':more, 'rev':rev}

    def test_refresh_full_pagination_target_binding_and_no_token_in_snapshot(self):
        client = self.fake_client([self.page(['r0'], [['已使用']], True), self.page(['r1'], [['待确认']])])
        result = rv.refresh_snapshot(client, self.path, self.target)
        self.assertEqual(len(result['statuses']), 2)
        self.assertIn('--offset', client.run.call_args.kwargs['extra'])
        self.assertNotIn('local-test', open(self.path, encoding='utf-8').read())
        self.assertIsNone(rv.read_snapshot(self.path, {**self.target,'base_token':'different'}))
        for changes in ({'statuses': {'r0': [1]}}, {'updated_at': None},
                        {'checked_at': float('nan')}, {'checked_at': time.time() + 3600}):
            with open(self.path, 'w', encoding='utf-8') as f:
                json.dump({**self.snapshot, **changes}, f)
            self.assertIsNone(rv.read_snapshot(self.path, self.target))

    def test_refresh_failures_preserve_snapshot(self):
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(self.snapshot, f)
        before = open(self.path, 'rb').read()
        bad_pages = [
            [self.page(['r0'], [['已使用']], True), self.page([], [], True)],
            [self.page(['r0'], [['已使用']], True), self.page(['r0'], [[]])],
            [self.page(['r0'], [['已使用']], True), self.page(['r1'], [[]], rev=2)],
            [{'fields':['状态'], 'data':[], 'record_id_list':[]}],
            [WritebackError('timeout')],
        ]
        for pages in bad_pages:
            with self.assertRaises(WritebackError):
                rv.refresh_snapshot(self.fake_client(pages), self.path, self.target)
            self.assertEqual(open(self.path,'rb').read(), before)

    def test_empty_eligible_pool_has_actionable_error(self):
        self.snapshot['statuses'] = {f'r{i}':['已使用'] for i in range(4)}
        response = self.client.get('/api/slot-machine?hide_used_rivals=true')
        self.assertEqual(response.status_code, 400)
        self.assertNotIn('同步飞书', response.json()['detail'])


if __name__ == '__main__':
    unittest.main()
