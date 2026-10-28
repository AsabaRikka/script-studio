// One workflow: explain the problem -> freeze examples -> GEPA search -> review evidence -> apply.
let optimizerData=null, optimizerDataset=null, optimizerTimer=null, optimizerJob=null;
let optimizerSupported=false;
const optimizerModeKeys={'多篇竞对-多向融合':'mode_0','单篇竞对-定向仿写':'mode_1','竞对骨架-原创内容':'mode_2','本体事实-自主原创':'mode_3'};
const optimizerStatuses={queued:'等待开始',running:'优化中',completed:'已完成',failed:'未完成',stopped:'已停止',interrupted:'中断',partial:'部分完成',model_unavailable:'模型未返回有效结果'};
const optimizerSplitNames={train:'优化用',validation:'筛选用',holdout:'独立验收'};

async function optimizerAPI(path,body){
  if(!optimizerSupported){const schema=await studioRequest('/openapi.json');
    if(!schema.paths?.['/api/quality/optimizer/overview'])throw new Error('新的提示词优化已准备好，后端尚待安全加载。请先保存页面编辑，加载后普通刷新。');
    optimizerSupported=true;}
  return qualityAPI('/optimizer'+path,body);
}
function optimizerNotice(message,error=false){qualityStatus(document.getElementById('prompt-optimizer'),message,error);}
function optimizerDetails(title){const d=qualityElement('details');d.append(qualityElement('summary',title));return d;}
function optimizerSettings(){
  return {...qualitySettings(),rewrite_mode:document.getElementById('rewrite-mode-select').value,
    mode:document.getElementById('optimizer-mode').value,
    objective:document.getElementById('optimizer-objective').value.trim(),
    focus_filename:document.getElementById('optimizer-focus-filename')?.value||'',
    focus_script:document.getElementById('optimizer-focus-script')?.value.trim()||'',
    reference_script:document.getElementById('optimizer-reference-script')?.value.trim()||'',
    sample_count:Number(document.getElementById('optimizer-samples').value),
    max_proposals:Number(document.getElementById('optimizer-rounds').value),
    max_calls:Number(document.getElementById('optimizer-budget').value),
    judge_model:document.getElementById('optimizer-judge').value,
    reflection_model:document.getElementById('optimizer-reflector').value,filenames:[]};
}
function saveOptimizerForm(){
  try{sessionStorage.setItem('studio-optimizer-form',JSON.stringify(optimizerSettings()));}catch(error){}
  optimizerDataset=null;document.getElementById('optimizer-preview')?.replaceChildren();
}
function optimizerModelPicker(id,value){
  const choices=[['','沿用写稿模型'],...Array.from(document.querySelector('#model-select')?.options||[]).map(o=>[o.value,o.text])];
  return qualitySelect(id,choices,value);
}
async function openPromptOptimizer(record=null){
  const dialog=qualityDialog('prompt-optimizer','提示词优化');dialog.classList.add('optimizer-workspace');
  if(!dialog.open)dialog.showModal();optimizerNotice('读取当前提示词和样例…');
  try{
    optimizerData=await optimizerAPI('/overview');qualityVersionData=await qualityAPI('/versions');
    renderOptimizer(dialog,record);optimizerNotice('先说清文案哪里不好，再预览样例。预览不调用模型。');
    const active=optimizerData.runs.find(r=>['queued','running'].includes(r.status));
    const last=active?.id||sessionStorage.getItem('studio-optimizer-job');if(last)watchOptimizer(last);
  }catch(error){optimizerNotice(error.message,true);}
}
function renderOptimizer(dialog,record){
  const body=dialog.querySelector('.quality-body');body.replaceChildren();
  let saved={};try{saved=JSON.parse(sessionStorage.getItem('studio-optimizer-form'))||{};}catch(error){}
  const intro=qualityElement('div',null,'optimizer-intro');intro.append(
    qualityElement('h3','让写稿提示词越用越好'),
    qualityElement('p','系统根据具体问题试改提示词，用相同素材比较，最后用未参与修改的来源验收。没有变好，就保留原版。'),
    qualityElement('p',`优化引擎：GEPA ${optimizerData.engine.version||'未安装'} · ${optimizerData.engine.available?'已接入':'当前不可运行'}`,'optimizer-engine'),
    qualityElement('small',`当前：${optimizerData.active.name} · 人工反馈 ${optimizerData.feedback_count} 条`));body.append(intro);
  const first=qualityElement('section',null,'optimizer-step');first.append(qualityElement('h3','1 · 说清你想改进什么'));
  const mode=qualitySelect('optimizer-mode',Object.keys(optimizerModeKeys).map(k=>[k,k]),record?.source_slot?.mode||saved.mode||'单篇竞对-定向仿写');
  first.append(qualityField('优化哪一种写稿方式',mode));
  const objective=qualityElement('textarea');objective.id='optimizer-objective';objective.rows=4;objective.maxLength=2000;
  objective.placeholder='例如：现在像武器说明书。我想保留竞对的口语节奏，多写玩家眼前的动作和选择，少报技能名，不要编造清屏或通关结果。';objective.value=saved.objective||'';
  first.append(qualityField('现在的文案哪里不好？你希望怎样写？',objective));
  const focusChoices=[['','不指定重点样例'],...optimizerData.cases.filter(c=>c.mode===mode.value).map(c=>[c.filename,`${c.code} · ${c.title}`])];
  const focusFilename=qualitySelect('optimizer-focus-filename',focusChoices,saved.focus_filename||'');
  const focusScript=qualityElement('textarea');focusScript.id='optimizer-focus-script';focusScript.rows=4;focusScript.maxLength=20000;
  focusScript.placeholder='可选：粘贴一条你认为最差的成稿。若它与样例正文一致，系统会把它作为重点问题样例。';focusScript.value=saved.focus_script||'';
  const referenceScript=qualityElement('textarea');referenceScript.id='optimizer-reference-script';referenceScript.rows=4;referenceScript.maxLength=20000;
  referenceScript.placeholder='可选：粘贴你认可的改法。系统只提取节拍、视角和转折，不把它当成所有稿件的标准答案。';referenceScript.value=saved.reference_script||'';
  first.append(qualityField('重点问题样例（可选）',focusFilename),qualityField('重点问题原稿（可选）',focusScript),qualityField('你的参考改写（可选）',referenceScript));
  const advanced=optimizerDetails('运行设置与模型');const grid=qualityElement('div',null,'quality-grid');
  grid.append(qualityField('测试样例数量',qualitySelect('optimizer-samples',[[6,'6篇 · 建议先用'],[3,'3篇 · 仅流程试跑']],saved.sample_count||6)),
    qualityField('最多试改几轮',qualityInput('optimizer-rounds',saved.max_proposals||2,'number')),
    qualityField('所有模型调用总上限',qualityInput('optimizer-budget',saved.max_calls||64,'number')),
    qualityField('评价模型',optimizerModelPicker('optimizer-judge',saved.judge_model||'')),
    qualityField('反思修订模型',optimizerModelPicker('optimizer-reflector',saved.reflection_model||'')));
  advanced.append(grid,qualityElement('p',`写稿模型、仿写方式、时长和思考程度沿用创作台设置；修复、评价、反思全部计入上限。未配置单价，金额未知。`,'batch-note'));first.append(advanced);
  const preview=qualityButton('预览测试样例 →',previewOptimizer);preview.classList.add('optimizer-primary','optimizer-start');first.append(preview);body.append(first);
  first.querySelectorAll('input,select,textarea').forEach(node=>node.addEventListener('input',saveOptimizerForm));
  const sampleBox=qualityElement('section',null,'optimizer-step');sampleBox.id='optimizer-preview';body.append(sampleBox);
  const result=qualityElement('section',null,'optimizer-result');result.id='optimizer-results';body.append(result);
  const history=optimizerDetails(`历史优化 · ${optimizerData.runs.length} 次`);history.id='optimizer-history';
  optimizerData.runs.forEach(run=>history.append(qualityButton(`${optimizerStatuses[run.status]||run.status} · ${run.mode} · ${new Date(run.created_at).toLocaleString()} · ${run.call_count}次调用`,()=>watchOptimizer(run.id))));body.append(history);
  const versions=optimizerDetails('历史提示词与回滚');versions.id='optimizer-versions';renderOptimizerVersions(versions);body.append(versions);
  const engine=optimizerDetails('开源引擎与本次方法');const link=qualityElement('a',`GEPA ${optimizerData.engine.version||'未安装'} · ${optimizerData.engine.available?'实际接入':'不可运行'}`);link.href=optimizerData.engine.url;link.target='_blank';link.rel='noopener';
  engine.append(link,qualityElement('p','直接调用 gepa.optimize：根据失败轨迹反思，测试候选，用验证集和Pareto结果保留有优势的修订。素材范围、调用预算和生成仍由工作室控制。'),
    qualityElement('p','优化集用于改提示词，验证集用于筛选；独立验收组不提供给GEPA。评分是编辑质量代理，小样例不能证明投放收益。'),
    qualityElement('p','运行结束可导出Promptfoo回放包，核对已经生成的文案、断言和评分；回放不重新付费生成，也不冒充第二次质量验收。'));
  body.append(engine);
}
async function previewOptimizer(){
  try{
    const settings=optimizerSettings();if(settings.objective.length<8)throw new Error('请先写一句具体问题和改进目标，至少8个字。');
    optimizerNotice('冻结当前提示词、素材和测试分组…');
    const data=await optimizerAPI('/preview',settings);optimizerDataset=data.dataset;
    const box=document.getElementById('optimizer-preview');box.replaceChildren(qualityElement('h3','2 · 确认样例，开始自动优化'));
    box.append(qualityElement('p',`${data.dataset.catalog.length}篇 / ${data.dataset.source_group_count}个独立来源组 · 已冻结当前提示词与素材`));
    const lookup=Object.fromEntries(data.dataset.catalog.map(c=>[c.filename,c]));
    const grid=qualityElement('div',null,'optimizer-splits');
    Object.entries(data.dataset.splits).forEach(([split,names])=>{const column=qualityElement('div',null,'quality-card');column.append(qualityElement('strong',`${optimizerSplitNames[split]} · ${names.length}篇`));names.forEach(name=>{
      const details=optimizerDetails(lookup[name].title);details.append(qualityElement('p',lookup[name].code),qualityElement('pre',lookup[name].text,'quality-copy'));column.append(details);});grid.append(column);});
    box.append(grid,qualityElement('p',`写稿：${data.dataset.settings.model}；评价：${data.dataset.settings.judge_model}；反思：${data.dataset.settings.reflection_model}。已有${data.dataset.human_feedback_count}条当前正文的人工反馈可用于改进。`,'batch-note'));
    const start=qualityButton(`开始自动优化 · 最多${settings.max_calls}次模型调用`,startOptimizer);start.classList.add('optimizer-primary','optimizer-start');box.append(start);
    optimizerNotice('样例已准备好。点击“开始自动优化”后才会调用模型。');box.scrollIntoView({block:'start'});
  }catch(error){optimizerNotice(error.message,true);}
}
async function startOptimizer(){
  if(!optimizerDataset)return;const dialog=document.getElementById('prompt-optimizer');
  try{
    dialog.querySelectorAll('.optimizer-start').forEach(b=>b.disabled=true);
    const response=await optimizerAPI('/runs',{...optimizerSettings(),dataset_id:optimizerDataset.id});
    await watchOptimizer(response.job.id);
  }catch(error){optimizerNotice(error.message,true);dialog.querySelectorAll('.optimizer-start').forEach(b=>b.disabled=false);}
}
function optimizerReviewRow(row,title){
  const card=qualityElement('article',null,'quality-card');card.append(qualityElement('strong',title),
    qualityElement('pre',row?.output?.result?.full_script||row?.reason||'未生成成功','quality-copy'));
  if(row?.review){card.append(qualityElement('p',`${Math.round(row.score*100)} / 100 · ${row.review.reason}`),qualityElement('p',`证据：「${row.review.evidence}」`));
    if(row.review.fact_concerns.length)card.append(qualityElement('p','事实疑点：'+row.review.fact_concerns.join('；'),'quality-error'));}
  if(row?.diagnostics){const d=row.diagnostics;card.append(qualityElement('p',`编辑诊断：感叹号 ${d.exclamation_count} 个 · 强程度词 ${d.intensifier_count} 个 · 转折 ${d.transition_count} 处 · ${d.flags?.length?d.flags.join('、'):'未发现结构性问题'}`,'batch-note'));}
  (row?.fact_flags||[]).forEach(flag=>card.append(qualityElement('p',`待核对：「${flag.evidence}」${flag.reason}`,'quality-error')));return card;
}
async function watchOptimizer(id){
  const changed=optimizerJob!==id;optimizerJob=id;clearTimeout(optimizerTimer);sessionStorage.setItem('studio-optimizer-job',id);
  try{
    const {job}=await qualityAPI('/jobs/'+id);if(optimizerJob!==id)return;
    if(job.kind!=='optimization')return;
    const running=['queued','running'].includes(job.status), dialog=document.getElementById('prompt-optimizer');
    dialog.querySelectorAll('.optimizer-start').forEach(b=>b.disabled=running);
    const box=document.getElementById('optimizer-results');box.replaceChildren(qualityElement('h3',`3 · 查看结果 · ${optimizerStatuses[job.status]||job.status}`),
      qualityElement('p',`${(job.phase||'等待开始').replace('validation','筛选').replace('train','优化').replace('holdout','独立验收')} · 已调用${job.calls.length}/${job.max_calls}次 · GEPA ${job.engine?.version||job.payload.engine.version}`));
    if(running)box.append(qualityButton('停止后续调用',async()=>{await qualityAPI('/jobs/'+id+'/stop',{});watchOptimizer(id);}));
    if(job.error)box.append(qualityElement('p',job.error,'quality-error'));
    if(job.result){
      box.append(qualityElement('div',job.result.summary,'optimizer-verdict'),qualityElement('p',`试改${job.result.proposal_count}轮，GEPA候选池共${job.result.gepa_candidates}版（含原版）。${job.result.holdout_count?`独立验收：新版胜${job.result.wins}、旧版胜${job.result.losses}、不确定${job.result.uncertain}。`:''}`));
      if(job.result.recommended&&!job.deployment)box.append(qualityButton('采用这次修订，旧版保留可回滚',()=>applyOptimizer(id)));
      if(job.deployment)box.append(qualityElement('p','已采用。旧版已停用并保留，下面“历史提示词与回滚”可恢复。'));
      const exportLink=qualityElement('a','下载 Promptfoo 离线回放包');exportLink.href='/api/quality/optimizer/runs/'+id+'/promptfoo';exportLink.download='promptfoo-replay.zip';box.append(exportLink);
    }
    if(job.proposals?.length){
      const proposals=qualityElement('div',null,'quality-body');proposals.append(qualityElement('h4','每轮改了什么，为什么留下或淘汰'));
      job.proposals.forEach(p=>{const card=qualityElement('article',null,'quality-card');const state=p.status==='selected'?'验证集优选':p.status==='eliminated'?'已淘汰':'测试中';
        card.append(qualityElement('strong',`第${p.round}轮 · ${state}`),qualityElement('p',p.reason),qualityElement('p','可能退步：'+p.risk));
        if(p.selection_reason)card.append(qualityElement('p',p.selection_reason));
        if(p.candidate){
          const trial=job.items.find(item=>item.candidate_hash===p.candidate_hash&&item.split!=='holdout');
          const parent=trial&&job.items.find(item=>item.candidate_hash===p.parent_hash&&item.filename===trial.filename);
          if(parent&&trial){const samples=optimizerDetails('查看这轮新旧文案');samples.open=true;const pair=qualityElement('div',null,'quality-grid');pair.append(optimizerReviewRow(parent,'修订前'),optimizerReviewRow(trial,'本轮修订后'));samples.append(pair);card.append(samples);}
          const d=optimizerDetails('查看修订前后提示词');const grid=qualityElement('div',null,'quality-grid');const key=optimizerModeKeys[job.payload.settings.mode];
          [['原版',job.payload.base.templates[key]],['本轮修订',p.candidate[key]]].forEach(([label,text])=>{const col=qualityElement('div');col.append(qualityElement('strong',label),qualityElement('pre',text,'quality-copy'));grid.append(col);});d.append(grid);card.append(d);}proposals.append(card);});box.append(proposals);
    }
    if(job.holdout?.length){box.append(qualityElement('h4','未参与优化的来源 · 新旧文案验收'));
      job.holdout.forEach(pair=>{const section=qualityElement('section',null,'optimizer-step');section.append(qualityElement('strong',pair.filename));
        const grid=qualityElement('div',null,'quality-grid');grid.append(optimizerReviewRow(pair.base,'原版提示词写出的文案'),optimizerReviewRow(pair.candidate,'修订版提示词写出的文案'));section.append(grid);
        section.append(qualityElement('p',`${{new:'新版更好',old:'原版更好',tie:'相当',uncertain:'证据不足',both_bad:'均未通过'}[pair.winner]} · ${pair.judgement.reason}`));
        if(pair.judgement.scores)renderQualityJudgement(section,pair.judgement,'原始匿名评审');box.append(section);});}
    if(job.items.length){const details=optimizerDetails(`全部生成、评分与失败 · ${job.items.length}项`);
      job.items.forEach(item=>details.append(optimizerReviewRow(item,`${optimizerSplitNames[item.split]} · ${item.filename} · ${item.status} · ${item.candidate_hash.slice(0,8)}`)));box.append(details);}
    if(changed)box.scrollIntoView({block:'start'});
    if(running&&dialog.open)optimizerTimer=setTimeout(()=>watchOptimizer(id),1500);
  }catch(error){optimizerNotice(error.message,true);}
}
async function applyOptimizer(id){
  try{
    const versions=await qualityAPI('/versions');await optimizerAPI('/runs/'+id+'/apply',{
      prompt_revision:versions.prompt_revision,state_revision:versions.state_revision,retire_previous:true});
    if(promptEditorState&&!promptEditorState.dirty)promptEditorState=null;
    await refreshOptimizerVersions();await watchOptimizer(id);optimizerNotice('新版已生效。旧版保留，可随时恢复。');
  }catch(error){optimizerNotice(error.message,true);}
}
async function refreshOptimizerVersions(){qualityVersionData=await qualityAPI('/versions');renderOptimizerVersions(document.getElementById('optimizer-versions'));}
function renderOptimizerVersions(container){
  container.querySelectorAll('article').forEach(n=>n.remove());
  qualityVersionData.versions.forEach(version=>{const row=qualityElement('article',null,'optimizer-version');row.append(qualityElement('span',`${version.name}${version.active?' · 当前生效':version.retired?' · 已停用':''}`));
    if(!version.active)row.append(qualityButton(version.retired?'恢复并切回':'切回此版',async()=>{
      try{let state=await qualityAPI('/versions');
        if(version.retired)state=await qualityAPI('/versions/'+version.id,{action:'restore',prompt_revision:state.prompt_revision,state_revision:state.state_revision});
        await qualityAPI('/versions/'+version.id,{action:'activate',prompt_revision:state.prompt_revision,state_revision:state.state_revision});
        if(promptEditorState&&!promptEditorState.dirty)promptEditorState=null;
        await refreshOptimizerVersions();optimizerNotice('已切回所选版本。');
      }catch(error){optimizerNotice(error.message,true);}}));
    if(!version.active&&!version.retired)row.append(qualityButton('停用',async()=>{
      try{const state=await qualityAPI('/versions');await qualityAPI('/versions/'+version.id,{action:'retire',prompt_revision:state.prompt_revision,state_revision:state.state_revision});await refreshOptimizerVersions();optimizerNotice('已停用，历史记录保留。');}
      catch(error){optimizerNotice(error.message,true);}}));container.append(row);});
}
