"""Persistent, sequential intake queues. Remote writes reuse per-source receipts."""
import copy
import json
import threading
import uuid
from material_intake import atomic_json, now, normalized


class IntakeBatches:
    def __init__(self, service):
        self.service=service
        self.directory=service.cache/'intake_batches'
        self.lock=threading.RLock(); self.running=set()
        for path in self.directory.glob('*.json'):
            batch=json.loads(path.read_text(encoding='utf-8'))
            if batch['status']=='运行中':
                batch['status']='已中断';batch['stop']=True;self.save(batch)

    def save(self,batch):
        batch['updated_at']=now();atomic_json(self.directory/(batch['id']+'.json'),batch)
        return batch

    def get(self,batch_id):
        if not isinstance(batch_id,str) or len(batch_id)!=32 or any(c not in '0123456789abcdef' for c in batch_id):raise ValueError('批量任务编号无效')
        path=self.directory/(batch_id+'.json')
        if not path.exists():raise ValueError('批量任务不存在')
        return json.loads(path.read_text(encoding='utf-8'))

    def list(self):
        return [self.get(p.stem) for p in sorted(self.directory.glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True)[:30]]

    def create(self,sources,model='',effort=''):
        if not 1<=len(sources)<=500:raise ValueError('每批请选择1–500条文案')
        rows=[];seen=set()
        for source in sources:
            key=normalized(source['script'])
            if key in seen:continue
            seen.add(key)
            # Reuse the newest identical intake task, including interrupted writes.
            previous=next((self.service.load(p.stem) for p in sorted(self.service.jobs.glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True) if normalized(self.service.load(p.stem)['source']['script'])==key),None)
            job=previous or self.service.create(source)
            rows.append({'job_id':job['id'],'title':source.get('title',''),'rank':source.get('rank',''),'status':'待处理','error':''})
        return self.save({'id':uuid.uuid4().hex,'status':'待开始','rows':rows,'model':model,'reasoning_effort':effort,'stop':False,'created_at':now()})

    def start(self,batch_id,action):
        if action not in ('analyze','all'):raise ValueError('批量操作无效')
        with self.lock:
            batch=self.get(batch_id)
            if batch_id in self.running:return batch
            if self.running:raise ValueError('已有批次运行，请等待完成或停止后再开始')
            batch.update(status='运行中',action=action,stop=False)
            self.save(batch);self.running.add(batch_id)
            threading.Thread(target=self.worker,args=(batch_id,),daemon=True).start()
            return batch

    def stop(self,batch_id):
        with self.lock:
            batch=self.get(batch_id);batch['stop']=True
            if batch_id not in self.running:batch['status']='已停止'
            return self.save(batch)

    def worker(self,batch_id):
        try:
            batch=self.get(batch_id)
            for index in range(len(batch['rows'])):
                with self.lock:
                    batch=self.get(batch_id)
                    if batch['stop']:break
                    item=batch['rows'][index];item.update(status='处理中',error='');self.save(batch)
                try:
                    job=self.service.load(item['job_id'])
                    if not job.get('analysis'):job=self.service.analyze(job['id'],job['revision'],batch['model'],batch['reasoning_effort'])
                    if batch['action']=='all':
                        job=self.service.write_all(job['id'],job['revision'])
                    status='三库已核验' if job['status']=='三库已入库并读回核验' else '待审核'
                    error=''
                except Exception as exc:
                    # Persist an individual failure; never discard remaining work.
                    status='失败';error=str(exc)
                with self.lock:
                    batch=self.get(batch_id);batch['rows'][index].update(status=status,error=error);self.save(batch)
            with self.lock:
                batch=self.get(batch_id)
                batch['status']='已停止' if batch['stop'] else ('部分失败' if any(r['status']=='失败' for r in batch['rows']) else '已完成')
                self.save(batch)
        finally:
            with self.lock:self.running.discard(batch_id)
