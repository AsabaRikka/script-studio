import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from intake_api import build_intake_router
from material_intake import IntakeService, read_workbook, prepare_analysis, normalized, normalize_analysis, source_row, KEYS
from feishu_writeback import WritebackError
from intake_batches import IntakeBatches


def sample_source():
    return {'script':'先给他一个机会。但快顶不住了，赶紧刷新。这怎么玩啊，兄弟。',
            'title':'原始标题','product':'未知游戏','duration':54,'rating':'A','rank':'107',
            'notes':'原表评级依据','mapping':'原表映射建议','url':'https://example.test/plan/123',
            'provenance':{'file':'example.xlsx','sheet':'A级','row':2}}


def sample_analysis():
    return {'structure':{'开头钩子':'先给他一个机会。','结尾CTA':'','视频概括摘要':'刷新救场但结果不佳',
                         '详细结构拆分':'先尝试，遇险刷新，吐槽收尾。','正文爆点分析':'危机中的选择；没有清屏结果。',
                         '情绪触发':['焦虑'],'结尾CTA类型':['无CTA']},
            'components':[{'组件类型':['痛点/冲突'],'语料内容':'但快顶不住了，赶紧刷新。','句式模板':'但快[撑不住]了，赶紧[操作]。'}],
            'shots':[{'组件类型':['过程/机制'],'对应口播':'先给他一个机会。','镜头作用分析':'尝试'},
                     {'组件类型':['痛点/冲突'],'对应口播':'但快顶不住了，赶紧刷新。这怎么玩啊，兄弟。','镜头作用分析':'危机和吐槽'}]}


def fake_schemas():
    structure=sample_analysis()['structure']
    fields={**structure,'素材编号':'','口播文案':'','信息流标题':'','产品':'','信息来源链接':'',
            '状态':['待确认'],'采集日期':'2026-10-15','视频时长(秒)':54,'爽点时间点(秒)':20,'节奏结构':['痛点→方案→结果']}
    comp={**sample_analysis()['components'][0],'组件编号':'','关联竞对视频':[]}
    shots={**sample_analysis()['shots'][0],'分镜编号':'','关联竞对视频':[],'镜头画面描述':'',
           '时间区间':'','画面类型':[],'字幕/文案包装':''}
    schemas={}
    for key,values in zip(KEYS,(fields,comp,shots)):
        fs=[]
        for name,value in values.items():
            kind='link' if name=='关联竞对视频' else 'datetime' if name=='采集日期' else 'select' if isinstance(value,list) else 'number' if isinstance(value,(int,float)) else 'text'
            f={'name':name,'type':kind}
            if kind=='link':f['link_table']='tblstruct'
            if kind=='select':f.update(multiple=name in ('画面类型',),options=[{'name':x} for x in set(value+['开头钩子','痛点/冲突','过程/机制'])])
            fs.append(f)
        schemas[key]={'fields':fs}
    return schemas


class FakeIntakeBase:
    def __init__(self):
        self.target={'base_token':'test-base','table_ids':dict(zip(KEYS,('tblstruct','tblcomp','tblshots')))}
        self.tables={table:{} for table in self.target['table_ids'].values()}
        self.schemas=fake_schemas();self.creates=[];self.fail=None;self.corrupt=False

    def run(self,action,payload=None,table=None,extra=()):
        key=next(k for k,v in self.target['table_ids'].items() if v==table)
        if action=='+field-list':return copy.deepcopy(self.schemas[key])
        if action=='+record-list':
            rows=list(self.tables[table].values())
            if payload:
                def matches(row,condition):
                    name,op,value=condition
                    if op=='intersects':return row['fields'].get(name)==value
                    return row['fields'].get(name)==value
                rows=[row for row in rows if (any if payload['logic']=='or' else all)(matches(row,c) for c in payload['conditions'])]
            flags=list(extra);offset=int(flags[flags.index('--offset')+1]) if '--offset' in flags else 0
            return {'records':copy.deepcopy(rows[offset:offset+200]),'has_more':offset+200<len(rows)}
        if action=='+record-get':
            rows=copy.deepcopy([self.tables[table][rid] for rid in payload['record_id_list'] if rid in self.tables[table]])
            if self.corrupt and rows:rows[0]['fields']['口播文案']='被篡改'
            return {'records':rows,'has_more':False}
        if action=='+record-batch-create':
            self.creates.append(key)
            if self.fail==('empty',key):
                self.fail=None;raise WritebackError('超时',uncertain=True)
            ids=[]
            for record in payload['create_records']:
                rid='rec'+str(sum(len(x) for x in self.tables.values())+1);ids.append(rid)
                self.tables[table][rid]={'record_id':rid,'fields':copy.deepcopy(record)}
            if self.fail==('committed',key):
                self.fail=None;raise WritebackError('超时但已提交',uncertain=True)
            return {'record_id_list':ids}
        if action=='+record-batch-update':
            for rid,fields in payload['update_records'].items():self.tables[table][rid]['fields'].update(copy.deepcopy(fields))
            return {}
        raise AssertionError(action)


class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.fake=FakeIntakeBase()
        self.service=IntakeService(self.root,lambda:{'feishu':self.fake.target},'unused',
                                   lambda *args:json.dumps(sample_analysis(),ensure_ascii=False))
        self.service.client=lambda:self.fake;self.service.identity=lambda:'测试用户'
        self.source=sample_source()

    def tearDown(self):self.tmp.cleanup()
    def analyzed(self):
        job=self.service.create(self.source)
        return self.service.analyze(job['id'],job['revision'])
    def write(self,job,stage):
        current=self.service.load(job['id']);return self.service.write(job['id'],current['revision'],stage)

    def test_two_stages_idempotent_and_cache_preserves_existing_rows(self):
        cache=self.root/'data_cache';cache.mkdir(exist_ok=True)
        old={'_record_id':'rec-old','口播文案':'人工资料','手工字段':'保留'}
        (cache/'rival_struct.json').write_text(json.dumps([old]),encoding='utf-8')
        job=self.analyzed();job=self.write(job,'structure');job=self.write(job,'details')
        ids=copy.deepcopy(job['receipts']);job=self.write(job,'structure');job=self.write(job,'details')
        self.assertEqual(self.fake.creates,['rival_struct','rival_comp','rival_shots'])
        self.assertEqual(job['receipts']['details'],ids['details'])
        self.assertEqual(job['status'],'三库已入库并读回核验')
        parent=job['receipts']['structure']['record_id']
        for key in ('rival_comp','rival_shots'):
            for row in self.fake.tables[self.fake.target['table_ids'][key]].values():
                self.assertEqual(row['fields']['关联竞对视频'],[{'id':parent}])
        rows=json.loads((cache/'rival_struct.json').read_text(encoding='utf-8'))
        self.assertEqual(rows[0],old);self.assertEqual(len(rows),2)
        manifest=json.loads((cache/'rival_struct.manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['records_count'],2);self.assertIn('columns',manifest)

    def test_reuse_existing_parent_keeps_analysis_and_child_numbering(self):
        self.fake.tables['tblstruct']['rec-existing']={'record_id':'rec-existing','fields':{
            '素材编号':'OLD-101','口播文案':self.source['script'],'信息来源链接':self.source['url'],'视频概括摘要':'人工分析保留'}}
        job=self.analyzed();job=self.write(job,'structure');job=self.write(job,'details')
        self.assertTrue(job['receipts']['structure']['reused'])
        self.assertNotIn('rival_struct',self.fake.creates)
        self.assertEqual(self.fake.tables['tblstruct']['rec-existing']['fields']['视频概括摘要'],'人工分析保留')
        self.assertEqual(next(iter(self.fake.tables['tblshots'].values()))['fields']['分镜编号'],'OLD-101-S01')

    def test_timeout_after_commit_recovers_without_duplicate_creation(self):
        job=self.write(self.analyzed(),'structure');self.fake.fail=('committed','rival_shots')
        with self.assertRaises(WritebackError):self.write(job,'details')
        result=self.write(job,'details')
        self.assertEqual(self.fake.creates,['rival_struct','rival_comp','rival_shots'])
        self.assertEqual(len(self.fake.tables['tblshots']),2)
        self.assertEqual(result['status'],'三库已入库并读回核验')

    def test_uncertain_uncommitted_write_blocks_blind_retry(self):
        job=self.analyzed();self.fake.fail=('empty','rival_struct')
        with self.assertRaises(WritebackError):self.write(job,'structure')
        with self.assertRaisesRegex(WritebackError,'阻止重复新增'):self.write(job,'structure')
        self.assertEqual(self.fake.creates,['rival_struct'])

    def test_readback_failure_does_not_enter_cache(self):
        job=self.analyzed();self.fake.corrupt=True
        with self.assertRaisesRegex(WritebackError,'读回字段不一致'):self.write(job,'structure')
        self.assertFalse((self.root/'data_cache/rival_struct.json').exists())
        self.fake.corrupt=False;self.write(job,'structure')
        self.assertEqual(self.fake.creates,['rival_struct'])

    def test_source_is_authoritative_and_coverage_and_tags_are_enforced(self):
        raw=sample_analysis();raw['structure'].update({'口播文案':'模型改写','素材编号':'假编号','爽点时间点(秒)':20})
        result=prepare_analysis(self.source,raw,fake_schemas(),'tblstruct')
        self.assertEqual(result['structure']['口播文案'],self.source['script'])
        self.assertNotEqual(result['structure']['爽点时间点(秒)'],20)
        self.assertEqual(result['peak']['mode'],'estimate')
        self.assertEqual(result['shots'][0]['时间区间'],'待视频核对（文字分段）')
        self.assertEqual(result['shots'][0]['画面类型'],[])
        for mutate in ('coverage','substring','tag'):
            bad=sample_analysis()
            if mutate=='coverage':bad['shots'][0]['对应口播']='偷偷改写。'
            elif mutate=='substring':bad['components'][0]['语料内容']='新增不存在的福利'
            else:bad['structure']['情绪触发']=['不存在的标签']
            with self.assertRaises(ValueError):prepare_analysis(self.source,bad,fake_schemas(),'tblstruct')

    def test_stale_revision_changed_target_and_existing_children_stop_writes(self):
        job=self.analyzed()
        with self.assertRaisesRegex(ValueError,'其他页面更新'):self.service.edit(job['id'],0,job['analysis'])
        self.fake.target['base_token']='changed'
        with self.assertRaisesRegex(ValueError,'配置已变化'):self.write(job,'structure')
        self.fake.target['base_token']='test-base'
        job=self.write(job,'structure');parent=job['receipts']['structure']['record_id']
        self.fake.tables['tblcomp']['rec-manual']={'record_id':'rec-manual','fields':{'组件编号':'MANUAL','关联竞对视频':[{'id':parent}]}}
        with self.assertRaisesRegex(ValueError,'已有其他组件'):self.write(job,'details')
        self.assertEqual(self.fake.creates,['rival_struct'])

    def test_parent_needed_and_link_target_is_checked(self):
        job=self.analyzed()
        with self.assertRaisesRegex(ValueError,'先写入'):self.write(job,'details')
        field=next(f for f in self.fake.schemas['rival_comp']['fields'] if f['name']=='关联竞对视频');field['link_table']='tblwrong'
        job=self.write(job,'structure')
        with self.assertRaisesRegex(ValueError,'字段值或类型'):self.write(job,'details')
        self.assertEqual(self.fake.creates,['rival_struct'])

    def test_serial_codes_reserved_and_peak_edit_validation(self):
        self.fake.tables['tblstruct']['rec-old']={'record_id':'rec-old','fields':{'素材编号':'OTHER-320-GGSJ','口播文案':'另一个稿'}}
        self.fake.tables['tblcomp']['rec-comp']={'record_id':'rec-comp','fields':{'组件编号':'CT-087'}}
        job=self.analyzed()
        self.assertEqual(job['analysis']['structure']['素材编号'],'WZYX-321-TFJ')
        self.assertEqual(job['analysis']['components'][0]['组件编号'],'CT-088')
        second=self.analyzed()
        self.assertEqual(second['analysis']['structure']['素材编号'],'WZYX-322-TFJ')
        peak=job['analysis']['peak'];self.assertGreater(peak['seconds'],0)
        edited=copy.deepcopy(job['analysis']);edited['peak']={'mode':'verified','seconds':12,'reason':'视频实测','anchor':'但快顶不住了'}
        job=self.service.edit(job['id'],job['revision'],edited)
        self.assertEqual(job['analysis']['structure']['爽点时间点(秒)'],12)
        edited['peak']['seconds']=1000
        with self.assertRaisesRegex(ValueError,'视频时长范围'):self.service.edit(job['id'],job['revision'],edited)

    def test_batch_preview_write_all_retry_and_recovery(self):
        source2=copy.deepcopy(self.source);source2['script']='不同开场。'+source2['script'];source2['url']='https://example.test/2'
        original=self.service.call_ai
        def model(prompt,*args):
            source=json.loads(prompt)['source'];result=sample_analysis()
            if source['script'].startswith('不同开场。'):result['shots'][0]['对应口播']='不同开场。'+result['shots'][0]['对应口播']
            return json.dumps(result,ensure_ascii=False)
        self.service.call_ai=model
        batches=IntakeBatches(self.service)
        batch=batches.create([self.source,self.source,source2]);self.assertEqual(len(batch['rows']),2)
        batch.update(action='analyze',status='运行中');batches.save(batch);batches.worker(batch['id'])
        self.assertEqual([r['status'] for r in batches.get(batch['id'])['rows']],['待审核','待审核'])
        self.assertEqual(self.fake.creates,[])
        batch=batches.get(batch['id']);batch.update(action='all',status='运行中');batches.save(batch)
        self.fake.fail=('committed','rival_shots');batches.worker(batch['id'])
        self.assertEqual(batches.get(batch['id'])['status'],'部分失败')
        batches.worker(batch['id'])
        self.assertEqual([r['status'] for r in batches.get(batch['id'])['rows']],['三库已核验','三库已核验'])
        self.assertEqual(len(self.fake.tables['tblstruct']),2);self.assertEqual(len(self.fake.tables['tblshots']),4)
        batch=batches.get(batch['id']);batch['status']='运行中';batches.save(batch)
        restarted=IntakeBatches(self.service);self.assertEqual(restarted.get(batch['id'])['status'],'已中断')

    def test_legacy_repair_keeps_ids_and_unrelated_cells(self):
        job=self.write(self.write(self.analyzed(),'structure'),'details')
        parent=job['receipts']['structure'];old=parent['code'];legacy='LR-ABCDEF123456'
        self.fake.tables['tblstruct'][parent['record_id']]['fields']['素材编号']=legacy
        self.fake.tables['tblstruct'][parent['record_id']]['fields']['手工备注']='保留'
        parent['code']=legacy;job['analysis']['structure']['素材编号']=legacy;job['analysis']['_material_code']=legacy
        self.service.save(job);ids=copy.deepcopy(job['receipts']['details']);creates=list(self.fake.creates)
        repaired=self.service.repair_legacy(job['id'])
        self.assertTrue(repaired['legacy_repair']['verified']);self.assertEqual(repaired['receipts']['structure']['record_id'],parent['record_id'])
        self.assertRegex(repaired['receipts']['structure']['code'],r'^WZYX-\d+-TFJ$')
        self.assertEqual(self.fake.tables['tblstruct'][parent['record_id']]['fields']['手工备注'],'保留')
        self.assertEqual(self.fake.creates,creates)
        self.service.repair_legacy(job['id'])
        for key in ('rival_comp','rival_shots'):self.assertEqual(repaired['receipts']['details'][key]['record_ids'],ids[key]['record_ids'])

    def test_csv_and_xlsx_default_A_hyperlink_target_and_original_row(self):
        csv='洗稿评级,素材文案,脚本原文,时长\nA,标题,原文,00:54\n'.encode('utf-8-sig')
        result=read_workbook(csv,'测试.csv');self.assertEqual(result['rows'][0]['duration'],54)
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as z:
            z.writestr('xl/workbook.xml','<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="S级" r:id="r1"/><sheet name="A级" r:id="r2"/></sheets></workbook>')
            z.writestr('xl/_rels/workbook.xml.rels','<Relationships><Relationship Id="r1" Target="worksheets/s.xml"/><Relationship Id="r2" Target="worksheets/a.xml"/></Relationships>')
            xml='<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheetData><row r="1"><c r="L1" t="inlineStr"><is><t>脚本原文</t></is></c><c r="M1" t="inlineStr"><is><t>查看详情链接</t></is></c></row><row r="2"><c r="L2" t="inlineStr"><is><t>完整原文</t></is></c><c r="M2" t="inlineStr"><is><t>查看详情</t></is></c></row></sheetData><hyperlinks><hyperlink ref="M2" r:id="link1"/></hyperlinks></worksheet>'
            z.writestr('xl/worksheets/a.xml',xml);z.writestr('xl/worksheets/s.xml',xml)
            z.writestr('xl/worksheets/_rels/a.xml.rels','<Relationships><Relationship Id="link1" Target="https://example.test/actual"/></Relationships>')
        result=read_workbook(stream.getvalue(),'sample.xlsx')
        self.assertEqual(result['sheet'],'A级');self.assertEqual(result['rows'][0]['url'],'https://example.test/actual')
        self.assertEqual(result['rows'][0]['provenance']['row'],2)

    def test_api_file_errors_and_local_job_creation_do_not_call_external_services(self):
        app=FastAPI();app.include_router(build_intake_router(self.root,lambda:{},'unused',lambda *a:None))
        with TestClient(app) as client:
            self.assertEqual(client.post('/api/intake/files',json={'filename':'x.xlsx','content':'invalid'}).status_code,422)
            response=client.post('/api/intake/jobs',json=self.source)
            self.assertEqual(response.status_code,200);self.assertEqual(response.json()['job']['status'],'待拆解')
            self.assertEqual(client.get('/api/intake/jobs/../../config').status_code,404)
            self.assertEqual(len(client.get('/api/intake/jobs').json()['jobs']),1)

    def test_nested_analysis_is_rendered_and_invalid_output_is_repaired_once(self):
        raw=sample_analysis()
        raw['structure']['详细结构拆分']=[{'原句':'先给他一个机会。','作用':'尝试'}]
        raw['structure']['情绪触发']='焦虑'
        normalized_result=normalize_analysis(raw,fake_schemas())
        self.assertIn('作用：尝试',normalized_result['structure']['详细结构拆分'])
        self.assertEqual(normalized_result['structure']['情绪触发'],['焦虑'])
        with patch.object(self.service,'call_ai',side_effect=['{}',json.dumps(raw,ensure_ascii=False)]) as model:
            job=self.analyzed()
        self.assertEqual(model.call_count,2)
        self.assertEqual(len(job['analysis_attempts']),2)
        self.assertIn('error',job['analysis_attempts'][0])
        self.assertTrue(job['analysis_attempts'][1]['validated'])
        self.assertEqual(self.fake.creates,[])

    def test_invalid_repair_persists_output_and_does_not_write(self):
        with patch.object(self.service,'call_ai',return_value='not JSON') as model:
            job=self.service.create(self.source)
            with self.assertRaisesRegex(ValueError,'修正后仍未通过'):
                self.service.analyze(job['id'],job['revision'])
        self.assertEqual(model.call_count,2)
        saved=self.service.load(job['id'])
        self.assertEqual(len(saved['analysis_attempts']),2)
        self.assertGreater(saved['revision'],job['revision'])
        self.assertEqual(self.fake.creates,[])

    def test_markdown_link_roundtrip_and_original_asr_case_is_preserved(self):
        source=source_row({'口播文案':'原文','信息来源链接':'[查看详情](https://example.test/123)'},'cache','sheet',1)
        self.assertEqual(source['url'],'https://example.test/123')
        job=self.analyzed(); original=self.fake.run
        def markdown_response(action,payload=None,table=None,extra=()):
            data=original(action,payload,table,extra)
            if action=='+record-get':
                for row in data['records']:
                    url=row['fields'].get('信息来源链接')
                    if url:row['fields']['信息来源链接']='['+url+']('+url+')'
            return data
        self.fake.run=markdown_response
        self.assertTrue(self.write(job,'structure')['receipts']['structure']['verified'])
        source=sample_source();source['script']='ASR。'+source['script']
        result=sample_analysis();result['shots'][0]['对应口播']='asr。'+result['shots'][0]['对应口播']
        with self.assertRaisesRegex(ValueError,'覆盖全部原文'):
            prepare_analysis(source,result,fake_schemas(),'tblstruct')

    def test_readback_retries_eventual_cells_without_recreating(self):
        job=self.analyzed();original=self.fake.run;reads=[]
        def eventual(action,payload=None,table=None,extra=()):
            data=original(action,payload,table,extra)
            if action=='+record-get':
                reads.append(1)
                if len(reads)==1:data['records'][0]['fields']['口播文案']=''
            return data
        self.fake.run=eventual
        with patch('material_intake.time.sleep') as pause:
            result=self.write(job,'structure')
        self.assertTrue(result['receipts']['structure']['verified'])
        self.assertEqual(len(reads),2);pause.assert_called_once_with(1)
        self.assertEqual(self.fake.creates,['rival_struct'])


if __name__=='__main__':unittest.main()
