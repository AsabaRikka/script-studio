import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
import server

class ShotRegenerationTests(unittest.TestCase):
    def setUp(self):
        slot=server.build_slot(mode='本体事实-自主原创')
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage=patch.object(server,'DATA_DIR',self.tmp.name);self.storage.start();self.addCleanup(self.storage.stop)
        self.api=TestClient(server.app)
        self.record={'material_code':'SHOT-ORIGINAL','title':'单镜草稿','full_script':'给他一个机会。怪潮终于退了。',
            'shots':[{'text':'给他一个机会。','visual':'桥头','game_action':'等待','duration':4},
                     {'text':'怪潮终于退了。','visual':'怪潮退去','game_action':'开火','duration':4}],'source_slot':slot}
        self.filename=server.save_draft(self.record)['filename']
        self.path=Path(self.tmp.name)/'saved_records'/self.filename
        self.before=self.path.read_bytes()
        self.url='/api/draft-editor/'+self.filename+'/regenerate-shot'
        self.body={'manual_script':self.record['full_script'],'manual_shots':self.record['shots'],'shot_index':0,'revision':0,
                   'instruction':'保留犹豫，更口语','model':'test-model','reasoning_effort':'high'}
        self.output=json.dumps({'title':'本镜候选','full_script':'再给他一次机会。','shots':[{'text':'再给他一次机会。','duration':4,'visual':'不能覆盖画面','game_action':'不能覆盖动作'}]},ensure_ascii=False)

    def test_candidate_uses_exact_sources_context_and_does_not_save(self):
        with patch.object(server,'call_ai',return_value=self.output) as ai:
            response=self.api.post(self.url,json=self.body)
        self.assertEqual(response.status_code,200);self.assertTrue(response.json()['ok'])
        self.assertEqual(response.json()['candidate'],{'text':'再给他一次机会。'})
        prompt=ai.call_args.args[0];self.assertIn('怪潮终于退了。',prompt);self.assertIn('保留犹豫，更口语',prompt)
        self.assertEqual(ai.call_args.kwargs['model'],'test-model');self.assertEqual(ai.call_args.kwargs['reasoning_effort'],'high')
        self.assertEqual(self.path.read_bytes(),self.before)
        self.assertEqual(len(list(self.path.parent.glob('*.json'))),1)
        manual=copy.deepcopy(self.body['manual_shots']);manual[0]['text']=response.json()['candidate']['text']
        saved=self.api.put('/api/draft-editor/'+self.filename,json={'revision':0,'manual_script':'\n'.join(s['text'] for s in manual),'manual_shots':manual})
        self.assertEqual(saved.status_code,200)
        result=saved.json()['record'];self.assertEqual(result['shots'],self.record['shots'])
        self.assertEqual(result['full_script'],self.record['full_script']);self.assertEqual(result['source_slot'],self.record['source_slot'])
        self.assertEqual(result['manual_shots'][1],manual[1]);self.assertEqual(result['manual_shots'][0]['visual'],'桥头')

    def test_invalid_index_unsynced_and_stale_are_rejected_without_model(self):
        for change in ({'shot_index':9},{'manual_script':'自由全文未同步'},{'revision':99},{'manual_shots':None}):
            with patch.object(server,'call_ai') as ai:
                response=self.api.post(self.url,json={**self.body,**change})
            self.assertIn(response.status_code,(409,422));ai.assert_not_called()
        self.assertEqual(self.path.read_bytes(),self.before)

    def test_missing_sources_and_model_failure_do_not_change_draft(self):
        row=copy.deepcopy(self.record);row.pop('source_slot')
        self.path.write_text(json.dumps(row,ensure_ascii=False),encoding='utf-8')
        with patch.object(server,'call_ai') as ai:self.assertEqual(self.api.post(self.url,json=self.body).status_code,422)
        ai.assert_not_called()
        self.path.write_bytes(self.before)
        with patch.object(server,'call_ai',return_value='{}'):
            self.assertFalse(self.api.post(self.url,json=self.body).json()['ok'])
        self.assertEqual(self.path.read_bytes(),self.before)

    def test_one_shot_count_and_weapon_scope_remain_enforced(self):
        outputs=[
            {'full_script':'再给他一次机会。怪潮终于退了。','shots':[{'text':'再给他一次机会。','duration':4},{'text':'怪潮终于退了。','duration':4}]},
            {'full_script':'不存在武器开火。','shots':[{'text':'不存在武器开火。','duration':5}]}
        ]
        for output in outputs:
            with patch.object(server,'call_ai',return_value=json.dumps(output,ensure_ascii=False)), patch.object(server,'used_weapons',return_value=[{'id':'outside','name':'不存在武器'}] if len(output['shots'])==1 else []):
                self.assertFalse(self.api.post(self.url,json=self.body).json()['ok'])
        self.assertEqual(self.path.read_bytes(),self.before)

    def test_concurrent_saved_edit_during_model_call_is_detected(self):
        def ai(*args,**kwargs):
            self.api.put('/api/draft-editor/'+self.filename,json={'revision':0,'manual_script':'别人刚保存的稿','manual_shots':None})
            return self.output
        with patch.object(server,'call_ai',side_effect=ai):
            self.assertEqual(self.api.post(self.url,json=self.body).status_code,409)
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['manual_script'],'别人刚保存的稿')

if __name__=='__main__':unittest.main()
