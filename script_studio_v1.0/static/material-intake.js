// Source text, review drafts and remote receipts are separate from generated scripts.
let intakeJob = null, intakeFile = null, intakeRows = [], intakeBusy = false, intakeDirty = false;
let intakeSelected = new Set(), intakeBatch = null, intakeBatchTimer = null;
const intakeEl = id => document.getElementById('intake-' + id);
const intakeLabels = {structure:'结构与风格', components:'可复用组件', shots:'文字分镜'};
const intakeKeys = {structure:'rival_struct', components:'rival_comp', shots:'rival_shots'};
const intakeHidden = new Set(['素材编号','组件编号','分镜编号','口播文案','采集日期','状态','信息流标题','信息来源链接','产品','视频时长(秒)']);
function buildIntakeDialog() {
  if (intakeEl('dialog')) return;
  const dialog = document.createElement('dialog'); dialog.id = 'intake-dialog';
  dialog.innerHTML = [
    '<div class="intake-head"><div><h2>补充素材库</h2><p>本地文案 → 结构与风格库 → 组件库与分镜库</p></div><button class="btn-sm" id="intake-close">关闭</button></div>',
    '<div class="intake-toolbar"><label class="btn-sm intake-upload">选择 Excel / CSV<input type="file" id="intake-file" accept=".xlsx,.csv"></label><select id="intake-sheet" aria-label="工作表"><option value="">先选择文件</option></select>',
    '<select id="intake-grade" aria-label="评级筛选"><option value="">全部评级</option><option>S</option><option selected>A</option><option>B</option><option>C</option><option>D</option></select><input id="intake-search" class="search-input" placeholder="检索标题、排序或原文" aria-label="检索导入文案"><button class="btn-sm" id="intake-library">从结构库载入</button><button class="btn-sm" id="intake-blank">粘贴文案</button></div>',
    '<div class="intake-pickers"><div><label for="intake-row">文件中的文案 <span id="intake-count"></span></label><select id="intake-row"><option>选择文件后展示文案</option></select></div><div><label for="intake-recent">继续已有入库任务</label><select id="intake-recent"><option value="">选择任务…</option></select></div></div>',
    '<div class="intake-body"><section class="intake-source"><h3>来源原文</h3><p id="intake-provenance" class="batch-note">选择表格中的一条，或粘贴全文。</p><label for="intake-title">信息流标题</label><input id="intake-title">',
    '<div class="intake-source-meta"><div><label for="intake-product">游戏名称</label><input id="intake-product"></div><div><label for="intake-duration">原视频时长（秒，可空）</label><input id="intake-duration" type="number" min="0.1" max="86400" step="0.1"></div></div><label for="intake-url">来源链接</label><input id="intake-url" type="url">',
    '<label for="intake-script">口播全文（保留 ASR 原句）</label><textarea id="intake-script" rows="15"></textarea><p id="intake-notes" class="batch-note"></p><p class="batch-note">仅有文字时，分镜为语义分段，画面与时间待视频核对。原文没有的爽点、福利或 CTA 不补写。</p><button class="btn btn-primary" id="intake-analyze">1. 分析并预览</button></section>',
    '<section class="intake-review"><div class="intake-review-head"><h3>入库预览</h3><span id="intake-state" class="badge">尚未拆解</span></div><div class="intake-review-tabs"><button class="btn-sm" data-intake-tab="structure">结构与风格</button><button class="btn-sm" data-intake-tab="components">可复用组件<span id="intake-n-components"></span></button><button class="btn-sm" data-intake-tab="shots">文字分镜<span id="intake-n-shots"></span></button></div><div id="intake-preview"><p class="batch-note">分析后可逐项修改，再分步入库。</p></div></section></div>',
    '<div class="intake-footer"><p id="intake-status" role="status">选择一条文案开始。</p><div><button class="btn-sm" id="intake-save" disabled>保存预览修改</button><button class="btn-sm" id="intake-write-structure" disabled>2. 写入结构库</button><button class="btn btn-primary" id="intake-write-details" disabled>3. 写入组件与分镜</button></div></div>'
  ].join('');
  document.body.append(dialog);
  const bulk=document.createElement('details');bulk.className='intake-bulk';bulk.id='intake-bulk';
  bulk.innerHTML='<summary>批量导入 <span id="intake-selected-count">已选 0 条</span></summary><div class="intake-bulk-actions"><button class="btn-sm" id="intake-select-filtered">全选筛选结果</button><button class="btn-sm" id="intake-clear-selected">清空选择</button><button class="btn-sm" id="intake-batch-analyze">批量分析预览</button><button class="btn btn-primary" id="intake-batch-all">一键全部写入三库</button><button class="btn-sm" id="intake-batch-stop">停止后续</button><select id="intake-batch-recent" aria-label="恢复批量任务"><option value="">恢复批量任务…</option></select></div><p class="batch-note">按勾选文案逐条处理；一键写入会自动分析尚未拆解的文案。可先批量分析并逐条审核，再一键入库。停止会完成当前一条，刷新后可恢复。</p><div id="intake-bulk-rows"></div><div id="intake-batch-progress" role="status"></div>';
  dialog.querySelector('.intake-pickers').after(bulk);
  const all=document.createElement('button');all.id='intake-write-all';all.className='btn btn-primary';all.textContent='一键写入三库';
  intakeEl('write-details').after(all);all.onclick=()=>writeIntake('all');
  intakeEl('select-filtered').onclick=()=>{filteredIntakeRows().forEach(({index})=>intakeSelected.add(index));renderIntakeSelection();};
  intakeEl('clear-selected').onclick=()=>{intakeSelected.clear();renderIntakeSelection();};
  intakeEl('batch-analyze').onclick=()=>startIntakeBatch('analyze');intakeEl('batch-all').onclick=()=>startIntakeBatch('all');
  intakeEl('batch-stop').onclick=async()=>{if(intakeBatch){await postStudio('/api/intake/batches/'+intakeBatch.id+'/stop',{});await pollIntakeBatch();}};
  intakeEl('batch-recent').onchange=async()=>{if(intakeEl('batch-recent').value){intakeBatch=(await studioRequest('/api/intake/batches/'+intakeEl('batch-recent').value)).job;localStorage.setItem('studio-intake-batch',intakeBatch.id);renderIntakeBatch();scheduleIntakeBatch();}};
  intakeEl('close').onclick=closeMaterialIntake;
  dialog.addEventListener('cancel', event=>{if(event.target!==dialog)return;event.preventDefault();closeMaterialIntake();});
  intakeEl('file').onchange=async event=>{
    if(!canLeaveIntake()){event.target.value='';return;}const file=event.target.files[0];if(!file)return;
    if(file.size>20*1024*1024){intakeStatus('文件最多20MB');return;}
    intakeFile={filename:file.name,content:await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=reject;reader.readAsDataURL(file);})};
    event.target.value='';await loadIntakeFile('');
  };
  intakeEl('sheet').onchange=()=>{if(canLeaveIntake())loadIntakeFile(intakeEl('sheet').value);};
  intakeEl('grade').onchange=filterIntakeRows;intakeEl('search').oninput=filterIntakeRows;
  intakeEl('row').onchange=()=>{const row=intakeRows[Number(intakeEl('row').value)];if(row&&canLeaveIntake())fillIntakeSource(row);};
  intakeEl('recent').onchange=async()=>{if(intakeEl('recent').value&&canLeaveIntake())await runIntake(async()=>{intakeJob=(await studioRequest('/api/intake/jobs/'+intakeEl('recent').value)).job;showIntakeJob();});};
  intakeEl('blank').onclick=()=>{if(canLeaveIntake())fillIntakeSource({script:'',title:'',product:'',url:'',duration:null});};
  intakeEl('library').onclick=()=>{if(canLeaveIntake())runIntake(async()=>{const data=await studioRequest('/api/intake/sources');intakeRows=data.rows;intakeSelected.clear();intakeFile=null;intakeEl('sheet').replaceChildren(new Option('结构与风格库（本地缓存）',''));intakeEl('grade').value='';intakeEl('search').value='';intakeDirty=false;filterIntakeRows();intakeStatus('已载入 '+data.count+' 条结构库文案，写入前会重新核对远端原记录。');});};
  intakeEl('analyze').onclick=analyzeIntake;intakeEl('save').onclick=()=>runIntake(saveIntakeAnalysis);
  intakeEl('write-structure').onclick=()=>writeIntake('structure');intakeEl('write-details').onclick=()=>writeIntake('details');
  dialog.querySelectorAll('[data-intake-tab]').forEach(button=>button.onclick=()=>renderIntakePreview(button.dataset.intakeTab));
  dialog.querySelectorAll('.intake-source input,.intake-source textarea').forEach(input=>input.addEventListener('input',()=>{intakeDirty=true;}));
  window.addEventListener('beforeunload',event=>{if(intakeDirty||intakeBusy){event.preventDefault();event.returnValue='';}});
}
function intakeStatus(text){intakeEl('status').textContent=text;}
function canLeaveIntake(){return !intakeBusy&&(!intakeDirty||confirm('当前有未保存的输入或预览修改，确定切换？'));}
async function closeMaterialIntake(){
  if(!canLeaveIntake())return;
  if(intakeDirty){
    try{
      if(intakeJob){intakeJob=(await studioRequest('/api/intake/jobs/'+intakeJob.id)).job;showIntakeJob();}
      else fillIntakeSource({script:'',title:'',product:'',url:'',duration:null});
    }catch(error){intakeStatus('尚未恢复已保存内容：'+error.message);return;}
  }
  intakeDirty=false;intakeEl('dialog').close();
}
async function openMaterialIntake(){
  buildIntakeDialog();if(!intakeEl('dialog').open)intakeEl('dialog').showModal();
  await runIntake(async()=>{
    await refreshIntakeJobs();const saved=localStorage.getItem('studio-intake-job');
    if(!intakeJob&&saved){intakeJob=(await studioRequest('/api/intake/jobs/'+saved)).job;showIntakeJob();}
    const data=await studioRequest('/api/intake/batches');intakeEl('batch-recent').replaceChildren(new Option('恢复批量任务…',''),...data.batches.map(b=>new Option(b.created_at+' · '+b.rows.length+'条 · '+b.status,b.id)));
    const batchId=localStorage.getItem('studio-intake-batch');if(batchId){intakeBatch=(await studioRequest('/api/intake/batches/'+batchId)).job;renderIntakeBatch();scheduleIntakeBatch();}
  });
}
async function refreshIntakeJobs(){
  try{const data=await studioRequest('/api/intake/jobs');intakeEl('recent').replaceChildren(new Option('选择任务…',''),...data.jobs.map(job=>new Option((job.source.rating||'')+' '+(job.source.rank||'')+' · '+(job.source.title||job.source.script.slice(0,25))+' · '+job.status,job.id)));if(intakeJob)intakeEl('recent').value=intakeJob.id;}
  catch(error){intakeStatus(error.message);}
}
async function runIntake(action){
  if(intakeBusy)return;intakeBusy=true;setIntakeButtons();
  try{await action();}catch(error){intakeStatus(error.message);}finally{intakeBusy=false;setIntakeButtons();}
}
function setIntakeButtons(){
  if(!intakeEl('dialog'))return;
  const analyzed=!!intakeJob?.analysis,parent=!!intakeJob?.receipts?.structure?.verified,details=!!intakeJob?.receipts?.details;
  intakeEl('dialog').querySelectorAll('button,input,select,textarea').forEach(el=>{el.disabled=intakeBusy;});
  intakeEl('save').disabled=intakeBusy||!analyzed||details;intakeEl('write-structure').disabled=intakeBusy||!analyzed;intakeEl('write-details').disabled=intakeBusy||!parent;
  intakeEl('analyze').disabled=intakeBusy||!!Object.keys(intakeJob?.receipts||{}).length;
  intakeEl('write-all').disabled=intakeBusy||!analyzed;
  const running=intakeBatch?.status==='运行中';intakeEl('batch-analyze').disabled=intakeBusy||running;intakeEl('batch-all').disabled=intakeBusy||running;intakeEl('batch-stop').disabled=!running;
  intakeEl('dialog').querySelectorAll('.intake-source input,.intake-source textarea').forEach(input=>input.readOnly=!!intakeJob);
  const selected=intakeEl('dialog').querySelector('[data-intake-tab].active')?.dataset.intakeTab;
  if(selected){const locked=details||(selected==='structure'&&!!intakeJob?.receipts?.structure);intakeEl('preview').querySelectorAll('input,textarea,select').forEach(el=>{el.disabled=intakeBusy||locked||el.dataset.locked==='true';});}
}
async function loadIntakeFile(sheet){
  if(!intakeFile)return;
    await runIntake(async()=>{intakeStatus('正在读取表格，不会写入飞书…');const data=await postStudio('/api/intake/files',{...intakeFile,sheet});intakeRows=data.rows;intakeSelected.clear();intakeEl('sheet').replaceChildren(...data.sheets.map(name=>new Option(name,name)));intakeEl('sheet').value=data.sheet;if(/^[SABCD]级/.test(data.sheet))intakeEl('grade').value=data.sheet[0];intakeDirty=false;filterIntakeRows();intakeStatus('已读取 '+data.count+' 条文案；当前工作表：'+data.sheet);});
}
function filteredIntakeRows(){
  const grade=intakeEl('grade').value,query=intakeEl('search').value.trim().toLowerCase();
  return intakeRows.map((source,index)=>({source,index})).filter(({source})=>(!grade||source.rating===grade)&&(!query||(source.title+' '+source.script+' '+source.rank).toLowerCase().includes(query)));
}
function filterIntakeRows(){
  const rows=filteredIntakeRows();
  intakeEl('row').replaceChildren(...rows.map(({source,index})=>new Option((source.rating||'')+' · 排序'+(source.rank||'-')+' · '+(source.title||source.script.slice(0,50)),String(index))));
  intakeEl('count').textContent=rows.length+' 条';if(rows.length&&!intakeDirty)fillIntakeSource(rows[0].source);
  renderIntakeSelection();
}
function fillIntakeSource(source,keepJob=false){
  if(!keepJob){intakeJob=null;localStorage.removeItem('studio-intake-job');}
  for(const key of ['script','title','product','url'])intakeEl(key).value=source[key]||'';intakeEl('duration').value=source.duration??'';
  intakeEl('script').dataset.source=JSON.stringify(source);const p=source.provenance||{};
  intakeEl('provenance').textContent=(source.rating?source.rating+' 级 · ':'')+(source.rank?'排序 '+source.rank+' · ':'')+(p.file||'手动粘贴')+(p.sheet?' / '+p.sheet:'')+(p.row?' / 第 '+p.row+' 行':'');
  intakeEl('notes').textContent=[source.notes,source.mapping?'家园映射建议：'+source.mapping:''].filter(Boolean).join('\n');
  intakeDirty=false;if(!keepJob){intakeEl('preview').replaceChildren();intakeEl('state').textContent='尚未拆解';for(const key of ['components','shots'])intakeEl('n-'+key).textContent='';}setIntakeButtons();
}
async function analyzeIntake(){
  await runIntake(async()=>{
    if(!intakeJob){const source={...JSON.parse(intakeEl('script').dataset.source||'{}'),script:intakeEl('script').value,title:intakeEl('title').value,product:intakeEl('product').value||'未知游戏',url:intakeEl('url').value,duration:intakeEl('duration').value?Number(intakeEl('duration').value):null};if(!source.script.trim())throw new Error('请先输入口播全文');intakeJob=(await postStudio('/api/intake/jobs',source)).job;intakeDirty=false;localStorage.setItem('studio-intake-job',intakeJob.id);}
    intakeStatus('正在读取线上字段并拆解文案；调用当前模型，尚未写入飞书…');
    try{intakeJob=(await postStudio('/api/intake/jobs/'+intakeJob.id+'/analyze',{revision:intakeJob.revision,model:document.getElementById('model-select').value,reasoning_effort:document.getElementById('reasoning-effort').value})).job;}
    catch(error){intakeJob=(await studioRequest('/api/intake/jobs/'+intakeJob.id)).job;showIntakeJob();throw error;}
    showIntakeJob();await refreshIntakeJobs();intakeStatus('拆解已保存。请检查原句、标签和分段后分步入库。');
  });
}
function showIntakeJob(){
  localStorage.setItem('studio-intake-job',intakeJob.id);fillIntakeSource(intakeJob.source,true);intakeEl('state').textContent=intakeJob.status;
  if(intakeJob.analysis){renderIntakePreview('structure');for(const key of ['components','shots'])intakeEl('n-'+key).textContent=' ('+intakeJob.analysis[key].length+')';}
  const r=intakeJob.receipts||{};intakeStatus(r.structure?.verified?'结构记录 '+r.structure.record_id+(r.structure.reused?'（复用已有素材）':'')+'；'+intakeJob.status:intakeJob.status);setIntakeButtons();
}
function renderIntakePreview(group){
  const host=intakeEl('preview');host.replaceChildren();if(!intakeJob?.analysis)return;
  const schema=intakeJob.schemas[intakeKeys[group]].fields,records=group==='structure'?[intakeJob.analysis.structure]:intakeJob.analysis[group];
  const locked=!!intakeJob.receipts?.details||(group==='structure'&&!!intakeJob.receipts?.structure);
  if(group==='structure'){
    const card=document.createElement('div');card.className='intake-card';
    const heading=document.createElement('h4');heading.textContent='爽点 / 吸引力峰值时间';card.append(heading);
    const peak=intakeJob.analysis.peak ||= {mode:'none',anchor:'',reason:'尚未填写依据',seconds:null};
    const fields=[['mode','时间依据'],['anchor','峰值起句（原文）'],['seconds','时间（秒）'],['reason','判断说明']];
    for(const [key,title] of fields){const label=document.createElement('label');label.textContent=title;let input;
      if(key==='mode'){input=document.createElement('select');[['estimate','按文案位置估算，待视频核对'],['verified','已人工核实视频'],['none','无可辨峰值']].forEach(([v,t])=>input.append(new Option(t,v,false,peak.mode===v)));}
      else {input=document.createElement(key==='seconds'?'input':'textarea');if(key==='seconds'){input.type='number';input.min=0;input.step=.1;}input.value=peak[key]??'';}
      input.disabled=locked;input.dataset.locked=String(locked);input.onchange=()=>{peak[key]=key==='seconds'?(input.value===''?null:Number(input.value)):input.value;intakeDirty=true;};
      if(key!=='mode')input.oninput=input.onchange;label.append(input);card.append(label);
    }
    const note=document.createElement('p');note.className='batch-note';note.textContent='估算 = 峰值起句前的字数 ÷ 全文字数 × 视频时长。修改后保存会重算；无正向爽点可记录冲突峰值并说明。';card.append(note);host.append(card);
  }
  intakeEl('dialog').querySelectorAll('[data-intake-tab]').forEach(b=>b.classList.toggle('active',b.dataset.intakeTab===group));
  records.forEach((record,index)=>{
    const card=document.createElement('div');card.className='intake-card';const heading=document.createElement('h4');heading.textContent=group==='structure'?record.素材编号:(group==='components'?'组件':'文字分镜')+' '+(index+1)+' · '+(record.组件编号||record.分镜编号);card.append(heading);
    for(const field of schema){
      if(intakeHidden.has(field.name)||field.name==='爽点时间点(秒)'||['link','attachment','datetime'].includes(field.type)||!(field.name in record))continue;
      const label=document.createElement('label');label.textContent=field.name;let input;
      if(field.type==='select'){input=document.createElement('select');input.multiple=!!field.multiple;if(!field.multiple)input.append(new Option('不适用 / 未确认',''));for(const option of field.options||[]){const name=typeof option==='string'?option:option.name;input.append(new Option(name,name,false,(record[field.name]||[]).includes(name)));}input.onchange=()=>{record[field.name]=[...input.selectedOptions].map(o=>o.value).filter(Boolean);intakeDirty=true;};}
      else{input=document.createElement('textarea');input.value=record[field.name]??'';input.rows=Math.min(10,Math.max(2,Math.ceil(input.value.length/45)));input.oninput=()=>{record[field.name]=input.value;intakeDirty=true;};}
      input.dataset.locked=String(group==='shots'&&['时间区间','画面类型','字幕/文案包装'].includes(field.name));input.disabled=locked||intakeBusy||input.dataset.locked==='true';label.append(input);card.append(label);
    }host.append(card);
  });
}
async function saveIntakeAnalysis(){
  if(!intakeJob?.analysis)return;
  intakeJob=(await studioRequest('/api/intake/jobs/'+intakeJob.id,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:intakeJob.revision,analysis:intakeJob.analysis})})).job;
  const group=intakeEl('dialog').querySelector('[data-intake-tab].active')?.dataset.intakeTab||'structure';
  const review=intakeEl('dialog').querySelector('.intake-review'),scroll=review.scrollTop;
  intakeDirty=false;renderIntakePreview(group);review.scrollTop=scroll;intakeEl('state').textContent=intakeJob.status;intakeStatus('预览修改已保存。');
}
async function writeIntake(stage){
  if(!intakeJob?.analysis)return;const title=intakeJob.source.title||intakeJob.source.script.slice(0,40);
  const text=stage==='all'?'将“'+title+'”一键写入结构、组件和分镜三库？\n会依次核对、写入并读回，重复执行沿用已有记录。':stage==='structure'?'将“'+title+'”写入结构与风格库？\n会核对重复文案；已有记录保留并复用。':'将“'+title+'”的 '+intakeJob.analysis.components.length+' 条组件、'+intakeJob.analysis.shots.length+' 条文字分镜写入飞书？\n全部关联到已核验的结构库记录。';
  if(!confirm(text))return;
  await runIntake(async()=>{
    if(intakeDirty)await saveIntakeAnalysis();intakeStatus('正在核对身份、目标字段与重复记录，写入后读回…');
    try{intakeJob=(await postStudio('/api/intake/jobs/'+intakeJob.id+'/write-'+stage,{revision:intakeJob.revision})).job;}
    catch(error){intakeJob=(await studioRequest('/api/intake/jobs/'+intakeJob.id)).job;showIntakeJob();throw error;}
    showIntakeJob();await refreshIntakeJobs();const data=await studioRequest('/api/overview');overviewData=data;
    document.getElementById('count-r').textContent=data.counts.rival_struct;document.getElementById('count-s').textContent=data.counts.rival_shots;document.getElementById('count-c').textContent=data.counts.rival_comp;if(typeof renderCurrentTab==='function')renderCurrentTab();
  });
}

function renderIntakeSelection(){
  if(!intakeEl('bulk-rows'))return;
  intakeEl('selected-count').textContent='已选 '+intakeSelected.size+' 条';
  const host=intakeEl('bulk-rows');host.replaceChildren();
  for(const {source,index} of filteredIntakeRows()){
    const row=document.createElement('label'),check=document.createElement('input');check.type='checkbox';check.checked=intakeSelected.has(index);
    check.onchange=()=>{check.checked?intakeSelected.add(index):intakeSelected.delete(index);intakeEl('selected-count').textContent='已选 '+intakeSelected.size+' 条';};
    const text=document.createElement('span');text.textContent=(source.rating||'')+' · 排序'+(source.rank||'-')+' · '+(source.title||source.script.slice(0,50));row.append(check,text);host.append(row);
  }
}
async function startIntakeBatch(action){
  if(intakeDirty){intakeStatus('请先保存当前预览修改，再开始批量操作。');return;}
  let sources=[...intakeSelected].map(i=>intakeRows[i]).filter(Boolean);
  if(!sources.length&&!intakeBatch){intakeStatus('请先展开批量导入并勾选文案。');return;}
  const count=sources.length||intakeBatch.rows.length;
  if(!confirm(action==='all'?'将选中的 '+count+' 条文案依次一键写入三库？尚未分析的会调用当前模型自动拆解；估算时间将标注为待核对。':'对选中的 '+count+' 条文案进行批量分析预览？会调用当前模型，暂不写入飞书。'))return;
  await runIntake(async()=>{
    if(sources.length)intakeBatch=(await postStudio('/api/intake/batches',{sources,model:document.getElementById('model-select').value,reasoning_effort:document.getElementById('reasoning-effort').value})).job;
    intakeBatch=(await postStudio('/api/intake/batches/'+intakeBatch.id+'/run',{action})).job;
    localStorage.setItem('studio-intake-batch',intakeBatch.id);intakeEl('bulk').open=true;renderIntakeBatch();scheduleIntakeBatch();
  });
}
function renderIntakeBatch(){
  if(!intakeBatch)return;const host=intakeEl('batch-progress');host.replaceChildren();
  const title=document.createElement('p');title.textContent='批次 '+intakeBatch.status+' · '+intakeBatch.rows.filter(r=>['待审核','三库已核验'].includes(r.status)).length+'/'+intakeBatch.rows.length;host.append(title);
  for(const item of intakeBatch.rows){const row=document.createElement('div');row.className='intake-batch-row';const text=document.createElement('span');text.textContent='排序'+item.rank+' · '+item.title+' · '+item.status+(item.error?'：'+item.error:'');const open=document.createElement('button');open.className='btn-sm';open.textContent='查看 / 审核';open.onclick=()=>{if(canLeaveIntake())runIntake(async()=>{intakeJob=(await studioRequest('/api/intake/jobs/'+item.job_id)).job;showIntakeJob();});};row.append(text,open);host.append(row);}setIntakeButtons();
}
function scheduleIntakeBatch(){clearTimeout(intakeBatchTimer);if(intakeBatch?.status==='运行中')intakeBatchTimer=setTimeout(pollIntakeBatch,1500);}
async function pollIntakeBatch(){
  if(!intakeBatch)return;
  try{intakeBatch=(await studioRequest('/api/intake/batches/'+intakeBatch.id)).job;renderIntakeBatch();if(intakeBatch.status!=='运行中'){await refreshIntakeJobs();if(intakeJob&&!intakeDirty){intakeJob=(await studioRequest('/api/intake/jobs/'+intakeJob.id)).job;showIntakeJob();}const data=await studioRequest('/api/overview');overviewData=data;for(const [id,key] of [['r','rival_struct'],['s','rival_shots'],['c','rival_comp']])document.getElementById('count-'+id).textContent=data.counts[key];}}
  catch(error){intakeStatus(error.message);}finally{scheduleIntakeBatch();}
}
