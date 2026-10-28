import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import server
from material_sampling import RandomCycleSampler
from script_fusion import coherent_shot_chain


def material_fixture():
    return {
        'weapons': [{'_record_id': f'w{i}', '武器名称': f'武器{i}'} for i in range(8)],
        'skills': [{'_record_id': f'k{i}-{j}', '所属武器': [{'id': f'w{i}'}],
                    '技能名称': f'强化{j}'} for i in range(8) for j in range(4)],
        'rival_struct': [{'_record_id': f'r{i}', '素材编号': f'R{i}',
                          '口播文案': f'第{i}篇参考，先看局势再作选择。'} for i in range(12)],
        'rival_shots': [{'_record_id': f's{i}-{j}', '分镜编号': f'R{i}-S{j:02d}',
                         '关联竞对视频': [{'id': f'r{i}'}], '对应口播': f'参考镜头{j}'}
                        for i in range(12) for j in range(1, 10)],
        'rival_comp': [{'_record_id': f'c{i}', '组件编号': f'GZ-{i}',
                        '组件类型': ['开头钩子'], '语料内容': f'组件{i}'} for i in range(20)],
        'welfare': [{'_record_id': f'f{i}', '福利名称': f'福利{i}'} for i in range(8)],
        'gameplay': [{'_record_id': f'g{i}', '知识标题': '武器槽位' if i == 0 else f'普通规则{i}'}
                     for i in range(12)],
        'levels': [{'_record_id': f'l{i}', '关卡号': i, '关卡名称': f'关卡{i}'} for i in range(8)],
    }


def slot_ids(slot):
    return {'weapons': [w['id'] for w in slot['weapon_candidates']],
            'rivals': [r['id'] for r in slot['rivals']], 'shots': [s['id'] for s in slot['shots']],
            'comps': [c['id'] for c in slot['components']],
            'welfares': [slot['welfare']['id']] if slot['welfare'] else [],
            'gameplays': [g['id'] for g in slot['gameplay_facts']],
            'levels': [slot['level']['id']] if slot['level'] else []}


class RandomSamplingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = material_fixture()
        for name, value in [('load_cache', lambda key: self.cache.get(key, [])),
                            ('calculate_all_usage', lambda: tuple({row['_record_id']: 10000 if i else 0
                                for i, row in enumerate(self.cache[key])}
                                for key in ('weapons', 'rival_struct', 'rival_shots', 'rival_comp', 'welfare')))]:
            mock = patch.object(server, name, side_effect=value)
            mock.start(); self.addCleanup(mock.stop)
        for name, value in [('DATA_DIR', self.temp.name), ('JOBS_DIR', str(Path(self.temp.name) / 'jobs')),
                            ('batch_jobs', {})]:
            mock = patch.object(server, name, value)
            mock.start(); self.addCleanup(mock.stop)
        self.client = TestClient(server.app)

    def test_uniform_ignores_all_usage_and_default_gameplay_preference(self):
        # Every eligible row reaches random.choice, including heavily used rows;
        # both gameplay and level remain strictly opt-in even in uniform mode.
        with patch('random.choices', side_effect=AssertionError('weighted draw in uniform mode')), \
             patch('random.choice', side_effect=lambda pool: pool[-1]):
            slot = server.build_slot(pure_random=True, mode='单篇竞对-定向仿写')
            opted = server.build_slot(
                pure_random=True,
                mode='单篇竞对-定向仿写',
                selection=server.MaterialSelection(
                    gameplays=[f'g{i}' for i in range(12)],
                    levels=[f'l{i}' for i in range(8)],
                ),
            )
        self.assertEqual(slot['weapon']['id'], 'w7')
        self.assertEqual(slot['rivals'][0]['id'], 'r11')
        self.assertEqual(slot['welfare']['id'], 'f7')
        self.assertIsNone(slot['level'])
        self.assertEqual(opted['level']['id'], 'l7')
        self.assertEqual(slot_ids(slot)['gameplays'], [])
        self.assertEqual(slot_ids(opted)['gameplays'], ['g11', 'g10'])
        self.assertEqual(slot_ids(slot)['comps'], ['c19', 'c18', 'c17', 'c16', 'c15'])
        self.assertEqual(slot_ids(slot)['weapons'], ['w7'])

    def test_single_draw_history_exhausts_selected_ranges_and_survives_api_roundtrip(self):
        selected = {'weapons': ['w2', 'w4'], 'rivals': ['r0', 'r1'],
                    'shots': ['s0-1', 's0-2', 's1-1', 's1-2'], 'comps': ['c4', 'c6'],
                    'welfares': ['f1', 'f5'], 'gameplays': ['g3', 'g4'], 'levels': ['l2', 'l4']}
        history = {}
        slots = []
        for _ in range(5):
            response = self.client.post('/api/slot-machine', json={'pure_random': True,
                'mode': '单篇竞对-定向仿写', 'selection': selected, 'sampling_counts': history})
            self.assertEqual(response.status_code, 200, response.text)
            slot = response.json(); slots.append(slot)
            history = slot['sampling']['counts']
            for key, ids in slot_ids(slot).items():
                self.assertLessEqual(set(ids), set(selected[key]))
                self.assertEqual(len(ids), len(set(ids)))
        for key in ('weapons', 'rivals', 'welfares', 'levels'):
            first = [slot_ids(slot)[key][0] for slot in slots]
            self.assertEqual(len(set(first[:2])), 2)
            self.assertEqual(len(set(first[2:4])), 2)
        self.assertTrue(slots[-1]['sampling']['reused'])
        # Both gameplay and levels stay empty when not explicitly selected, even in pure_random mode.
        random_slot = self.client.post('/api/slot-machine', json={'pure_random': True}).json()
        self.assertIsNone(random_slot['level'])
        self.assertEqual(random_slot['gameplay_facts'], [])
        request = server.GenerateRequest(**random_slot, selection=server.MaterialSelection())
        server.validate_generation_selection(request)
        request.level = {'id': 'l0', 'number': 0, 'name': '关卡0'}
        with self.assertRaises(server.HTTPException):
            server.validate_generation_selection(request)

    def test_batch_has_its_own_round_and_disjoint_actual_candidates(self):
        history = {'weapons': {'w0': 1}, 'comps': {'c0': 1}}
        with patch.object(server, 'start_batch_thread') as start:
            response = self.client.post('/api/batches', json={'pure_random': True, 'count': 4,
                                         'sampling_counts': history})
        self.assertEqual(response.status_code, 200, response.text)
        job = server.batch_jobs[response.json()['job_id']]
        self.assertTrue(job['settings']['pure_random'])
        self.assertNotIn('sampling_counts', job['settings'])
        self.assertEqual(start.call_count, 1)
        all_ids = {key: [] for key in slot_ids(job['items'][0]['slot'])}
        for item in job['items']:
            for key, ids in slot_ids(item['slot']).items():
                all_ids[key].extend(ids)
        # There are enough unused candidates for these categories in this batch.
        for key in ('weapons', 'rivals', 'shots', 'comps', 'welfares', 'gameplays', 'levels'):
            self.assertEqual(len(all_ids[key]), len(set(all_ids[key])), key)
        self.assertEqual(job['sampling']['counts'], response.json()['sampling']['counts'])
        self.assertEqual(set(all_ids['comps']), {f'c{i}' for i in range(20)})
        self.assertFalse(job['sampling']['reused'])

    def test_short_pools_reuse_only_after_exhaustion_and_never_duplicate_inside_slot(self):
        counts = {}
        for _ in range(8):
            slot = server.build_slot(pure_random=True, batch_draw=counts,
                selection=server.MaterialSelection(weapons=['w0', 'w1'], rivals=['r0', 'r1'],
                    comps=['c0', 'c1', 'c2'], welfares=['f0'], gameplays=['g1'], levels=['l0']))
            for ids in slot_ids(slot).values():
                self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(counts['weapons'], {'w0': 4, 'w1': 4})
        self.assertEqual(set(counts['comps'].values()), {8})
        self.assertIn('福利', slot['sampling']['reused'])

    def test_uniform_shots_keep_continuity_and_use_fresh_short_chain_before_repeat(self):
        rows = self.cache['rival_shots'][:9]
        counts = {}; seen = set()
        for _ in range(9):
            chain = coherent_shot_chain('r0', rows, {'s0-1': 9999}, draw_counts=counts)
            ids = {s['_record_id'] for s in chain}
            if ids & seen:
                self.assertEqual(len(seen), 9)
                break
            seen.update(ids)
            sequence = [int(s['分镜编号'].split('-S')[1]) for s in chain]
            self.assertEqual(sequence, list(range(sequence[0], sequence[0] + len(sequence))))
            RandomCycleSampler(counts={'shots': counts}).record(chain, 'shots')
        self.assertEqual(len(seen), 9)

    def test_invalid_and_hidden_ranges_still_reject_and_legacy_mode_stays_opt_in(self):
        before = copy.deepcopy(self.cache)
        response = self.client.post('/api/slot-machine', json={'pure_random': True,
                                  'selection': {'weapons': ['missing']}})
        self.assertEqual(response.status_code, 422)
        with patch.object(server, 'visibility_blocked', return_value=(
                {'rivals': {'r0'}, 'shots': set(), 'components': set()}, {})):
            response = self.client.post('/api/slot-machine', json={'pure_random': True,
                'hide_used_rivals': True, 'selection': {'rivals': ['r0']}})
        self.assertEqual(response.status_code, 422)
        slot = server.build_slot()
        self.assertFalse(slot['pure_random'])
        self.assertIsNone(slot['level'])
        self.assertEqual(self.cache, before)

    def test_selected_shots_determine_valid_anchor_without_escaping_either_range(self):
        for _ in range(5):
            slot = server.build_slot(pure_random=True,
                selection=server.MaterialSelection(shots=['s7-1', 's7-2']))
            self.assertEqual(slot['rivals'][0]['id'], 'r7')
            self.assertEqual(set(slot_ids(slot)['shots']), {'s7-1', 's7-2'})
        response = self.client.post('/api/slot-machine', json={'pure_random': True,
                                  'selection': {'rivals': ['r0'], 'shots': ['s7-1']}})
        self.assertEqual(response.status_code, 422)
        self.assertIn('共同来源', response.json()['detail'])


if __name__ == '__main__':
    unittest.main()
