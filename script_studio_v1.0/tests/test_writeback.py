import copy
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
import server
from feishu_writeback import build_plan, execute_plan, WritebackError, LarkClient, link_ids, rows_from_response


class FakeBase:
    def __init__(self, plan):
        self.target = {'base_token':'local-base', 'table_ids':{'main':'tblmain', 'weapons':'tblw',
            'rival_struct':'tblr', 'rival_shots':'tbls', 'rival_comp':'tblc', 'welfare':'tblf'}}
        self.rows = {}
        self.calls = []
        self.fail_children = False
        self.corrupt_readback = False
        table_keys = {'关联武器':'weapons','参考竞对':'rival_struct','参考竞对分镜':'rival_shots',
                      '参考组件':'rival_comp','关联福利点':'welfare','父记录':'main'}
        fields = {k:v for row in [plan['parent'],*plan['children'],{'父记录':[{'id':'rec-parent'}]}] for k,v in row.items()}
        self.schema = []
        for name,value in fields.items():
            kind = 'link' if name in table_keys else 'select' if isinstance(value,list) else 'number' if isinstance(value,(int,float)) else 'datetime' if name == '日期' else 'text'
            field = {'name':name,'type':kind}
            if kind == 'link': field['link_table'] = self.target['table_ids'][table_keys[name]]
            if kind == 'select': field.update(options=[{'name':v} for v in value], multiple=name=='情绪触发')
            self.schema.append(field)
        for name in ['参考原文','分镜序号','分镜文案','分镜时长(秒)','分镜画面描述','对应游戏画面','BGM']:
            if not any(f['name']==name for f in self.schema): self.schema.append({'name':name,'type':'text'})

    def run(self, action, payload=None, table=None, extra=()):
        self.calls.append((action,copy.deepcopy(payload)))
        if action == '+field-list': return {'fields':self.schema}
        if action == '+record-get':
            ids = payload['record_id_list']
            if table and table != self.target['table_ids']['main']:
                return {'fields':['素材编号'], 'data':[[rid] for rid in ids], 'record_id_list':ids}
            rows = [copy.deepcopy(self.rows[rid]) for rid in ids if rid in self.rows]
            if self.corrupt_readback and rows: rows[0]['fields']['二创文案'] = 'wrong'
            return {'records':rows}
        if action == '+record-list':
            conditions = payload['conditions']
            if conditions[0][0] == '父记录':
                parent = conditions[0][2][0]['id']
                rows = [r for r in self.rows.values() if link_ids(r['fields'].get('父记录')) == [parent]]
            else:
                codes = [c[2] for c in conditions]
                rows = [r for r in self.rows.values() if r['fields'].get('素材编号') in codes]
            return {'records':copy.deepcopy(rows), 'has_more':False}
        if action == '+record-batch-update':
            for rid,fields in payload['update_records'].items(): self.rows[rid]['fields'].update(copy.deepcopy(fields))
            return {}
        if action == '+record-batch-create':
            children = any(r.get('父记录') for r in payload['create_records'])
            if children and self.fail_children:
                self.fail_children = False
                raise WritebackError('child failure')
            ids = []
            for fields in payload['create_records']:
                rid = f'rec{len(self.rows)+1}'
                self.rows[rid] = {'record_id':rid,'fields':copy.deepcopy(fields)}
                ids.append(rid)
            return {'record_id_list':ids}
        raise AssertionError(action)


class WritebackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = {'main':[{'二创节奏结构':['钩子→过程→爽点→结果']}],
                      'rival_struct':[{'_record_id':'r1','素材编号':'R1','口播文案':'完整原稿一。'},
                                      {'_record_id':'r2','素材编号':'R2','口播文案':'完整原稿二。'}]}
        self.request = {'weapon_id':'w1','rival_ids':['r1','r2'],'shot_ids':['s1'],'comp_ids':['c1'],
                        'mode':'多篇竞对-多向融合','emotions':['爽感'], 'data':{
                            'material_code':'TEST-01','title':'具体选题','full_script':'给他一个机会。没办法，只能赌一手。',
                            'dup_summary':'钩子→过程→爽点→结果｜钩子替换｜兑现｜结尾',
                            'rhythm_desc':'0-3s钩子 → 3-6s选择', 'max_duration':30,
                            'shots':[{'text':'给他一个机会。','ref_text':'原句一。','duration':3,'visual':'怪潮压近战车','game_action':'战车开火','source_shot_ids':['s1']},
                                     {'text':'没办法，只能赌一手。','ref_text':'原句二。','duration':3,'visual':'选中强化，清空怪群','game_action':'选择强化','source_comp_ids':['c1']}]}}
        self.plan = build_plan(self.request, lambda name:self.cache.get(name,[]))
        self.client = FakeBase(self.plan)

    def tearDown(self): self.tmp.cleanup()

    def test_parent_child_contract_and_full_reference_sources(self):
        p = self.plan['parent']
        self.assertNotIn('参考原文',p)
        self.assertNotIn('分镜文案',p)
        self.assertEqual(p['状态'], ['待制作'])
        self.assertEqual(p['二创节奏结构'], ['钩子→过程→爽点→结果'])
        self.assertIn('【参考来源1 R1】：\n完整原稿一。',p['参考文案'])
        self.assertIn('【参考来源2 R2】：\n完整原稿二。',p['参考文案'])
        self.assertEqual([s['素材编号'] for s in self.plan['children']], ['TEST-01-S01','TEST-01-S02'])
        self.assertEqual(self.plan['children'][0]['参考竞对分镜'], [{'id':'s1'}])
        self.assertNotIn('参考竞对分镜',self.plan['children'][1])
        for row in [p,*self.plan['children']]:
            self.assertNotIn('预估时长(秒)',row)
            self.assertNotIn('分镜语速校验',row)
        bad = copy.deepcopy(self.request); bad['data']['shots'][0]['text'] = '不一致。'
        with self.assertRaises(WritebackError): build_plan(bad,lambda name:self.cache.get(name,[]))

    def test_string_and_array_emotions_normalized_without_changing_manuscript(self):
        request = copy.deepcopy(self.request)
        request['data']['source_slot'] = {'emotions':' 爽感、焦虑、爽感 '}
        for value,expected in [('爽感',['爽感']),(' 焦虑，爽感/焦虑 ',['焦虑','爽感']),
                ([' 焦虑 ','爽感','焦虑'],['焦虑','爽感']),('["焦虑","爽感"]',['焦虑','爽感']),
                (None,[]),('',[])]:
            with self.subTest(value=value):
                request['data']['shots'][0]['emotions']=value
                before=copy.deepcopy(request)
                plan=build_plan(request,lambda name:self.cache.get(name,[]))
                self.assertEqual(plan['parent']['情绪触发'],['爽感','焦虑'])
                self.assertEqual(plan['children'][0].get('情绪触发',[]),expected)
                self.assertEqual(request,before)
                client=FakeBase(plan)
                next(f for f in client.schema if f['name']=='情绪触发')['options']=[{'name':'焦虑'},{'name':'爽感'}]
                first=execute_plan(plan,client,self.tmp.name); again=execute_plan(plan,client,self.tmp.name)
                self.assertEqual(first['record_id'],again['record_id'])
                self.assertEqual(len(client.rows),3)
                # Each case uses independent receipts for its normalized labels.
                for path in os.scandir(self.tmp.name): os.remove(path.path)

    def test_malformed_and_unknown_emotions_still_block_before_remote_creation(self):
        for value in ({'name':'爽感'},[{'name':'爽感'}],['爽感',123],123):
            request=copy.deepcopy(self.request);request['data']['shots'][0]['emotions']=value
            with self.assertRaisesRegex(WritebackError,'第1镜情绪标签格式错误'):
                build_plan(request,lambda name:self.cache.get(name,[]))
        request=copy.deepcopy(self.request);request['data']['shots'][0]['emotions']='不存在的标签'
        plan=build_plan(request,lambda name:self.cache.get(name,[]));client=FakeBase(plan)
        next(f for f in client.schema if f['name']=='情绪触发')['options']=[{'name':'爽感'}]
        with self.assertRaisesRegex(WritebackError,'不存在的标签'):
            execute_plan(plan,client,self.tmp.name)
        self.assertEqual(client.rows,{})

    def test_create_verify_idempotent_and_partial_retry(self):
        self.client.fail_children = True
        with self.assertRaises(WritebackError): execute_plan(self.plan,self.client,self.tmp.name)
        self.assertEqual(len(self.client.rows),1)
        result = execute_plan(self.plan,self.client,self.tmp.name)
        self.assertTrue(result['verified']); self.assertEqual(len(self.client.rows),3)
        again = execute_plan(self.plan,self.client,self.tmp.name)
        self.assertEqual(again['record_id'],result['record_id']); self.assertEqual(len(self.client.rows),3)
        self.assertEqual(again['child_record_ids'],result['child_record_ids'])
        for rid in result['child_record_ids']:
            self.assertEqual(self.client.rows[rid]['fields']['父记录'], [{'id':result['record_id']}])

    def test_existing_flat_record_repaired_without_losing_human_fields(self):
        parent = copy.deepcopy(self.plan['parent']); parent.update({'参考原文':'错误首镜','状态':['制作中'],'人工修改稿':'人工保留','日期':'2026-09-01'})
        self.client.schema.append({'name':'状态','type':'select','options':[{'name':'待制作'},{'name':'制作中'}]})
        self.client.rows['rec1'] = {'record_id':'rec1','fields':parent}
        result = execute_plan(self.plan,self.client,self.tmp.name)
        fields = self.client.rows['rec1']['fields']
        self.assertEqual(result['record_id'],'rec1')
        self.assertIsNone(fields['参考原文']); self.assertEqual(fields['人工修改稿'],'人工保留')
        self.assertEqual(fields['状态'],['制作中']); self.assertEqual(fields['日期'],'2026-09-01')
        self.assertEqual(len(self.client.rows),3)

    def test_wrong_schema_duplicates_and_unverified_response_fail(self):
        bad = copy.deepcopy(self.client)
        next(f for f in bad.schema if f['name']=='父记录')['link_table'] = 'other'
        with self.assertRaises(WritebackError): execute_plan(self.plan,bad,self.tmp.name)
        self.assertEqual(bad.rows,{})
        for rid in ['rec1','rec2']: self.client.rows[rid] = {'record_id':rid,'fields':copy.deepcopy(self.plan['parent'])}
        with self.assertRaises(WritebackError): execute_plan(self.plan,self.client,self.tmp.name)
        self.assertEqual(len(self.client.rows),2)
        self.client.rows.clear(); self.client.corrupt_readback = True
        with patch('feishu_writeback.time.sleep'):
            with self.assertRaisesRegex(WritebackError,'读回未一致'): execute_plan(self.plan,self.client,self.tmp.name)

    def test_transport_relative_payload_and_non_json_is_not_success(self):
        from subprocess import CompletedProcess
        client = LarkClient('fake-cli',self.client.target,self.tmp.name)
        calls = []
        def run(argv,**kwargs):
            arg = argv[argv.index('--json')+1]
            self.assertFalse(os.path.isabs(arg[1:]))
            self.assertEqual(kwargs['cwd'],self.tmp.name)
            with open(os.path.join(self.tmp.name,arg[1:]),encoding='utf8') as f:
                self.assertEqual(json.load(f),{'create_records':[{'素材编号':'TEST'}]})
            calls.append(argv)
            return CompletedProcess(argv,0,b'not json',b'')
        with patch('feishu_writeback.subprocess.run',side_effect=run):
            with self.assertRaises(WritebackError) as error: client.run('+record-batch-create',{'create_records':[{'素材编号':'TEST'}]})
        self.assertTrue(error.exception.uncertain)
        self.assertEqual(os.listdir(self.tmp.name),[])

    def test_create_timeout_after_remote_commit_recovers_without_duplicates(self):
        original = self.client.run
        interrupted = False
        def run(action, payload=None, **kwargs):
            nonlocal interrupted
            result = original(action, payload, **kwargs)
            if action == '+record-batch-create' and any(r.get('父记录') for r in payload['create_records']) and not interrupted:
                interrupted = True
                raise WritebackError('timeout after remote commit', uncertain=True)
            return result
        self.client.run = run
        with self.assertRaises(WritebackError): execute_plan(self.plan,self.client,self.tmp.name)
        self.assertEqual(len(self.client.rows),3)
        result = execute_plan(self.plan,self.client,self.tmp.name)
        self.assertTrue(result['verified'])
        self.assertEqual(len(self.client.rows),3)
        self.assertEqual(sum(action == '+record-batch-create' for action,_ in self.client.calls),2)

    def test_unknown_creation_result_blocks_duplicate_and_wrong_parent_blocks_write(self):
        original = self.client.run
        def run(action, payload=None, **kwargs):
            if action == '+record-batch-create': raise WritebackError('unknown result', uncertain=True)
            return original(action,payload,**kwargs)
        self.client.run = run
        with self.assertRaises(WritebackError): execute_plan(self.plan,self.client,self.tmp.name)
        self.client.run = original
        with self.assertRaisesRegex(WritebackError,'暂不重复创建'): execute_plan(self.plan,self.client,self.tmp.name)
        self.assertFalse(self.client.rows)
        for filename in os.listdir(self.tmp.name): os.remove(os.path.join(self.tmp.name,filename))
        self.client.rows['rec1'] = {'record_id':'rec1','fields':copy.deepcopy(self.plan['parent'])}
        self.client.rows['rec2'] = {'record_id':'rec2','fields':{**self.plan['children'][0],'父记录':[{'id':'rec-other'}]}}
        with self.assertRaisesRegex(WritebackError,'其他主记录'): execute_plan(self.plan,self.client,self.tmp.name)
        self.assertFalse(any(action in ('+record-batch-create','+record-batch-update') for action,_ in self.client.calls))

    def test_matrix_response_preserves_parent_links_and_rejects_incomplete_query(self):
        rows = rows_from_response({'fields':['素材编号','父记录'], 'data':[['TEST-S01',[{'id':'rec-parent'}]]], 'record_id_list':['rec-child'], 'has_more':False})
        self.assertEqual(rows[0]['父记录'],[{'id':'rec-parent'}])
        for bad in ({'records':[{'record_id':'rec1'}]}, {'records':[], 'has_more':True},
                    {'fields':['素材编号'], 'data':[['TEST','extra']], 'record_id_list':['rec1']}):
            with self.assertRaises(WritebackError): rows_from_response(bad)

    def test_preview_and_legacy_batch_recovery_and_draft_preserved(self):
        data_dir = os.path.join(self.tmp.name,'data'); jobs_dir = os.path.join(data_dir,'batch_jobs')
        os.makedirs(jobs_dir)
        original = copy.deepcopy(self.request['data'])
        original['source_slot'] = {'weapon':{'id':'w1'},'rivals':[{'id':'r1'},{'id':'r2'}], 'shots':[{'id':'s1'}],
                                   'components':[{'id':'c1'}], 'mode':'多篇竞对-多向融合','emotions':['爽感']}
        with open(os.path.join(jobs_dir,'job.json'),'w',encoding='utf8') as f: json.dump({'items':[{'result':original}]},f)
        cfg = {'feishu':self.client.target}
        api = TestClient(server.app)
        with patch.object(server,'DATA_DIR',data_dir), patch.object(server,'JOBS_DIR',jobs_dir), \
             patch.object(server,'load_cache',side_effect=lambda name:self.cache.get(name,[])), \
             patch.object(server,'get_config',return_value=cfg), patch.object(server,'LarkClient',return_value=self.client):
            filename = server.save_draft({**self.plan['parent'],'_collected':True})['filename']
            preview = api.post('/api/writeback-draft-preview',json={'filename':filename}).json()
            self.assertTrue(preview['recovered']); self.assertEqual(preview['plan']['shot_count'],2)
            result = api.post('/api/writeback-draft',json={'filename':filename}).json()
            self.assertTrue(result['ok']); self.assertTrue(result['verified'])
            with open(os.path.join(data_dir,'saved_records',filename),encoding='utf8') as f:
                stored = json.load(f)
            self.assertEqual(len(stored['shots']),2); self.assertTrue(stored['_collected'])
            self.assertIn('source_slot',stored)
            self.assertEqual(api.post('/api/writeback-draft',json={'filename':'../other.json'}).status_code,422)
            original['shots'][0]['text'] = '不一致。'
            self.assertEqual(api.post('/api/writeback-preview',json={**self.request,'data':original}).status_code,422)


if __name__ == '__main__': unittest.main()
