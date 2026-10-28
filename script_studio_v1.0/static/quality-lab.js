// Shared revision-bound feedback; automatic search lives in prompt-optimizer.js.
let qualitySupported=null;
let qualityReviewRecord=null;
let qualityVersionData=null;
const qualityReasons=['口语别扭','像说明书','开头弱','事件不推进','事实无依据','来源利用不足','与其他稿重复'];
const qualityDecisionLabels={accepted:'已采用',revise:'待修改',rejected:'已弃用'};
function qualityElement(tag, text, className) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = String(text);
  if (className) node.className = className;
  return node;
}
function qualityButton(text, action) {
  const button = qualityElement('button', text, 'btn-sm');
  button.type = 'button'; button.addEventListener('click', action); return button;
}
async function qualityAPI(path, body) {
  if (qualitySupported !== true) {
    const api = await studioRequest('/openapi.json');
    qualitySupported = !!api.paths?.['/api/quality/review'];
    if (!qualitySupported) throw new Error('当前后端尚未加载新功能。请保存页面修改、等待任务结束后使用既有重启入口加载。');
  }
  return studioRequest('/api/quality' + path, body === undefined ? undefined : {
    method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)
  });
}
function qualityDialog(id, title) {
  let dialog = document.getElementById(id);
  if (dialog) return dialog;
  dialog = qualityElement('dialog', null, 'quality-dialog'); dialog.id = id;
  const head = qualityElement('div', null, 'quality-head');
  const heading = qualityElement('h2', title); heading.id = id + '-title';
  dialog.setAttribute('aria-labelledby', heading.id);
  head.append(heading, qualityButton('关闭', () => dialog.close()));
  dialog.append(head, qualityElement('div', null, 'quality-body'));
  document.body.append(dialog); return dialog;
}
function qualityStatus(dialog, message, error=false) {
  let node = dialog.querySelector('.quality-status');
  if (!node) { node = qualityElement('p', null, 'quality-status'); node.setAttribute('role','status'); dialog.append(node); }
  node.textContent = message; node.classList.toggle('quality-error', error);
}
function qualityMetaText(record) {
  const meta = record.generation_meta;
  return meta?.bundle_id ? `提示词：${meta.prompt_name} · ${meta.bundle_id.slice(0,8)} · ${meta.model || '模型未记录'}` : '旧稿：提示词版本未知';
}
function renderQualitySummary(record) {
  const panel = document.getElementById('quality-summary');
  if (!panel) return;
  panel.replaceChildren(qualityElement('small', qualityMetaText(record)),
    qualityButton('评价文案', () => openQualityReview()), qualityButton('优化这类文案的提示词', () => openPromptOptimizer(record)));
  document.getElementById('btn-review-copy')?.removeAttribute('disabled');
}
async function openQualityReview(fromEditor=false) {
  try {
    if (fromEditor && sceneEditor) {
      if (sceneEditor.dirty && !await saveSceneEditor()) return;
    } else if (!await preserveHumanChanges()) return;
    const record = fromEditor ? sceneEditor?.record : currentGenerated;
    if (!record) throw new Error('请先打开一篇稿件');
    qualityReviewRecord = structuredClone(record);
    const dialog = qualityDialog('quality-review','评价文案');
    const body = dialog.querySelector('.quality-body'); body.replaceChildren();
    body.append(qualityElement('p', qualityMetaText(record), 'batch-note'));
    const label = qualityElement('label','评价对象');
    const target = qualityElement('select'); target.id='quality-review-target';
    target.append(new Option('AI 原稿','ai'));
    if (record.manual_script?.trim() || record.人工修改稿?.trim()) target.append(new Option('人工修改稿','manual'));
    label.append(target); body.append(label);
    const copy = qualityElement('pre', null, 'quality-copy'); copy.id='quality-review-copy'; body.append(copy);
    const choices = qualityElement('div',null,'quality-reasons');
    qualityReasons.forEach(reason => {
      const item=qualityElement('label'); const check=qualityElement('input'); check.type='checkbox';check.value=reason;
      item.append(check,document.createTextNode(reason));choices.append(item);
    });
    body.append(choices);
    const note=qualityElement('textarea'); note.id='quality-review-note';note.maxLength=2000;note.rows=3;note.placeholder='指出具体句子、修改原因或希望改善的地方（可选）';body.append(note);
    const actions=qualityElement('div',null,'quality-actions');
    Object.entries(qualityDecisionLabels).forEach(([decision,text])=>actions.append(qualityButton(text,()=>saveQualityFeedback(decision))));
    body.append(actions);
    target.onchange=()=>refreshQualityReview(dialog); dialog.showModal(); await refreshQualityReview(dialog);
  } catch(error) { toast(error.message); }
}
async function refreshQualityReview(dialog) {
  const target=document.getElementById('quality-review-target').value;
  document.getElementById('quality-review-copy').textContent=target==='manual' ? qualityReviewRecord.manual_script || qualityReviewRecord.人工修改稿 : qualityReviewRecord.full_script;
  try {
    const data=await qualityAPI('/review',{record:qualityReviewRecord,target});
    qualityStatus(dialog,data.latest ? `${data.latest.reviewer==='agent'?'验证反馈 · ':''}${qualityDecisionLabels[data.latest.decision]} · ${data.latest.created_at}${data.stale_count ? ' · 另有旧正文的历史反馈' : ''}` : `本版正文尚无反馈${data.stale_count ? '；旧正文反馈已分开保留' : ''}`);
  } catch(error) { qualityStatus(dialog,error.message,true); }
}
async function saveQualityFeedback(decision) {
  const dialog=document.getElementById('quality-review');
  const buttons=dialog.querySelectorAll('.quality-actions button');buttons.forEach(b=>b.disabled=true);
  try {
    await qualityAPI('/feedback',{record:qualityReviewRecord,target:document.getElementById('quality-review-target').value,
      decision,reasons:[...dialog.querySelectorAll('.quality-reasons input:checked')].map(n=>n.value),
      note:document.getElementById('quality-review-note').value,event_id:crypto.randomUUID().replaceAll('-','')});
    await refreshQualityReview(dialog); toast('审稿反馈已保存');
  } catch(error) { qualityStatus(dialog,error.message,true); }
  finally { buttons.forEach(b=>b.disabled=false); }
}

// The immersive surface shares the same revision-checked feedback dialog.
document.querySelector('#manual-editor .manual-editor-actions')?.prepend(qualityButton('评价文案',()=>openQualityReview(true)),
  qualityButton('优化提示词',()=>openPromptOptimizer(sceneEditor?.record)));

function qualityField(label, element) {
  const field=qualityElement('label',label);field.append(element);return field;
}
function qualityInput(id, value='', type='text') {
  const node=qualityElement('input');node.id=id;node.type=type;node.value=value;return node;
}
function qualitySelect(id, options, value) {
  const node=qualityElement('select');node.id=id;
  options.forEach(([key,label])=>node.append(new Option(label,key)));if(value)node.value=value;return node;
}
function qualitySettings() {
  const raw=generationSettings();
  return Object.fromEntries(['model','reasoning_effort','max_duration','enable_game_profile','custom_instruction'].map(k=>[k,raw[k]]));
}
function qualityTable(headers,rows) {
  const table=qualityElement('table',null,'quality-table');const head=qualityElement('tr');headers.forEach(label=>head.append(qualityElement('th',label)));table.append(head);
  rows.forEach(values=>{const row=qualityElement('tr');values.forEach(value=>row.append(qualityElement('td',value)));table.append(row);});return table;
}
function renderQualityJudgement(container,data,title='匿名对照结论') {
  const decisions={A:'A更好',B:'B更好',tie:'相当',both_bad:'两篇都不合格',uncertain:'证据不足/需人工核对'};
  container.append(qualityElement('strong',title+'：'+(decisions[data.decision]||'待核对')),qualityElement('p',data.reason||''));
  ['A','B'].forEach(side=>{if(data['evidence_'+side])container.append(qualityElement('p',`${side}正文证据：「${data['evidence_'+side]}」`));});
  if(data.scores){const keys=[...new Set([...Object.keys(data.scores.A||{}),...Object.keys(data.scores.B||{})])];container.append(qualityTable(['维度','A（1–5）','B（1–5）'],keys.map(key=>[key,data.scores.A?.[key]??'未评',data.scores.B?.[key]??'未评'])));}
  if(data.fact_concerns?.length)container.append(qualityElement('p','事实疑点：'+data.fact_concerns.join('；'),'quality-error'));
}
