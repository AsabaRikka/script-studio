// AI suggestions are opt-in; the saved AI original and its sources stay intact.
function editorAISettings() {
  if (!sceneEditor) {
    const {model,reasoning_effort,max_duration,enable_game_profile}=generationSettings();
    return {model,reasoning_effort,max_duration,enable_game_profile};
  }
  return {model:document.getElementById('editor-ai-model').value,
    reasoning_effort:document.getElementById('editor-ai-effort').value,
    max_duration:Number(document.getElementById('editor-ai-duration').value),
    enable_game_profile:document.getElementById('editor-ai-profile').checked};
}
async function openRevisionStudio() {
  if (sceneEditor) { showRevision(); return; }
  if (!await preserveHumanChanges()) return;
  if (currentGenerated) openManualEditor();
  else {
    const restored=structuredClone(revisionState);
    if(restored.editor_bound) {
      restored.source_kind=null;restored.source_id=null;restored.editor_bound=false;
      restored.label='直接粘贴文案开始';restored.shots=[];restored.custom_shots=false;
    }
    openManualEditor({_rewrite_workspace:true,title:'已有文案改写',full_script:'',shots:[]});
    revisionState=restored; displayRevision(); persistRevision();
  }
  showRevision();
}
function editorScript() {
  return sceneEditor.pending !== null ? sceneEditor.pending : sceneEditor.shots.map(s=>s.text).join('\n');
}
function editorInputShots() {
  return sceneEditor.shots.map((s,i)=>({...sceneEditor.record.shots[i],text:s.text,visual:s.visual,game_action:s.game_action,duration:s.duration}));
}
function seedEditorRevision() {
  if (!sceneEditor) return;
  const record=sceneEditor.record, settings=generationSettings(), script=editorScript();
  revisionState={script,shots:editorInputShots(),reference:referenceText(record),source_kind:record._filename?'draft':null,
    source_id:record._filename||null,label:'当前精修稿 · '+(record.material_code||record.title||'粘贴新文案'),
    operation:'润色优化','scene-policy':sceneEditor.shots.length?'preserve':'rebuild',instruction:'',
    editor_bound:true,editor_seed:script,custom_shots:false};
  revisionCandidate=null; revisionElement('candidate').hidden=true;
  displayRevision();persistRevision();revisionElement('status').textContent='';
  const model=document.getElementById('editor-ai-model');
  model.replaceChildren(...[...document.getElementById('model-select').options].map(o=>new Option(o.text,o.value,false,o.value===settings.model)));
  document.getElementById('editor-ai-effort').value=settings.reasoning_effort;
  document.getElementById('editor-ai-duration').value=String(settings.max_duration);
  document.getElementById('editor-ai-profile').checked=settings.enable_game_profile;
  sceneEditor.selection=structuredClone(batchSelectionPayload()); renderEditorMaterials();
  detectShotRegeneration(sceneEditor);
}
async function detectShotRegeneration(editor) {
  try {
    const api=await (await fetch('/openapi.json')).json();
    if(sceneEditor!==editor)return;
    editor.shotAPI=!!api.paths?.['/api/draft-editor/{filename}/regenerate-shot'];
    document.getElementById('manual-editor-error').textContent=editor.shotAPI?'':'单镜重生成需重启后端服务后使用；整篇改写仍可使用。请先保存页面修改。';
    updateEditorPreview();
  } catch(e) { if(sceneEditor===editor)document.getElementById('manual-editor-error').textContent='无法确认单镜生成接口，请检查本地服务。'; }
}
function syncRevisionEditorText() {
  if (!sceneEditor || !revisionState.editor_bound) return;
  if (revisionElement('script').value===revisionState.editor_seed) {
    revisionElement('script').value=editorScript();
    revisionState.editor_seed=editorScript();
  }
  if (!revisionState.custom_shots) revisionState.shots=editorInputShots();
  persistRevision();
}
function renderEditorMaterials() {
  const host=document.getElementById('editor-material-pickers');host.replaceChildren();
  for(const [key,label] of Object.entries(materialLabels)) {
    const field=document.createElement('label'),title=document.createElement('span'),select=document.createElement('select');
    select.multiple=true;select.size=5;select.setAttribute('aria-label','精修替换'+label);
    const rows=overviewData?.[key==='comps'?'components':key]||[];
    for(const row of rows) select.append(new Option(row.name||row.title||row.code||row.id,row.id,false,sceneEditor.selection[key].includes(row.id)));
    const update=()=>{sceneEditor.selection[key]=[...select.selectedOptions].map(o=>o.value);title.textContent=label+' · 已选'+sceneEditor.selection[key].length;};
    select.onchange=update;update();
    const clear=document.createElement('button');clear.className='btn-sm';clear.textContent='清空'+label;
    clear.onclick=()=>{[...select.options].forEach(o=>o.selected=false);update();};
    field.append(title,select,clear);host.append(field);
  }
}
function setEditorAIWorking(value) {
  if(!sceneEditor)return;
  sceneEditor.aiRunning=value;
  if(value) {
    sceneEditor.lockedControls=[...document.querySelectorAll('#manual-editor button,#manual-editor input,#manual-editor select,#manual-editor textarea')].map(el=>({el,disabled:el.disabled}));
    sceneEditor.lockedControls.forEach(({el})=>el.disabled=true);
    document.getElementById('manual-editor-state').textContent='AI生成中，当前稿保留…';
  } else {
    (sceneEditor.lockedControls||[]).forEach(({el,disabled})=>el.disabled=disabled);
    updateEditorPreview();
  }
}
function regenerateEditorManuscript() {
  if(!sceneEditor || sceneEditor.aiRunning || sceneEditor.saving)return;
  // Load the live manuscript without discarding the user's rewrite options.
  const script=editorScript();revisionState.editor_bound=true;revisionState.editor_seed=script;
  revisionState.source_kind=sceneEditor.record._filename?'draft':null;revisionState.source_id=sceneEditor.record._filename||null;
  revisionState.label='当前精修稿 · '+(sceneEditor.record.material_code||sceneEditor.record.title||'');
  revisionElement('script').value=script;revisionElement('source-label').textContent=revisionState.label;
  if(!revisionState.custom_shots)revisionState.shots=editorInputShots();
  persistRevision();showRevision();generateRevision();
}
function discardRevisionCandidate() {
  if(revisionRunning)return;
  revisionCandidate=null;revisionElement('candidate').hidden=true;
  revisionElement('status').textContent='继续保留当前精修稿；刚才的候选仍可从草稿箱打开。';
}
async function adoptRevisionCandidate() {
  if(!revisionCandidate || sceneEditor?.aiRunning || sceneEditor?.saving)return;
  const candidate=revisionCandidate;
  if(sceneEditor && !sceneEditor.record._rewrite_workspace && sceneEditor.dirty && !await saveSceneEditor())return;
  currentGenerated=candidate;generatedSlot=candidate.source_slot;
  renderResult(candidate);document.getElementById('empty-hint').style.display='none';
  document.getElementById('manual-editor').close();sceneEditor=null;openManualEditor();
  toast('已打开候选新稿，来源稿和刚才的人工修改均保留。');
}
function editorSnapshot() {
  return JSON.stringify([sceneEditor.record._filename,sceneEditor.record._edit_revision||0,editorScript(),sceneEditor.shots.map(({text,visual,game_action,duration})=>({text,visual,game_action,duration}))]);
}
async function regenerateEditorShot(index) {
  if(!sceneEditor || sceneEditor.aiRunning || sceneEditor.saving || busy)return;
  if(sceneEditor.shotAPI===false){toast('请先保存修改并重启后端服务。');return;}
  if(sceneEditor.pending!==null) { toast('请先按换行同步全文到分镜。');return; }
  if(!sceneEditor.record._filename && !await saveSceneEditor())return;
  const editor=sceneEditor, snapshot=editorSnapshot();
  const payload={revision:editor.record._edit_revision||0,shot_index:index,manual_script:editorScript(),
    manual_shots:editor.shots.map(({text,visual,game_action,duration})=>({text,visual,game_action,duration})),
    instruction:document.getElementById('editor-shot-instruction-'+index).value,...editorAISettings()};
  setGenerationBusy(true);setEditorAIWorking(true);document.getElementById('manual-editor-error').textContent='';
  try {
    const data=await postStudio('/api/draft-editor/'+encodeURIComponent(editor.record._filename)+'/regenerate-shot',payload);
    if(sceneEditor!==editor || editorSnapshot()!==snapshot)throw new Error('生成期间稿件已变化，请重新生成。');
    editor.suggestions[index]={text:data.candidate.text,snapshot};
    renderSceneCards();
  } catch(e) { document.getElementById('manual-editor-error').textContent='本镜生成失败：'+e.message; }
  finally { setGenerationBusy(false);setEditorAIWorking(false); }
}
function renderShotSuggestion(host,index) {
  const suggestion=sceneEditor.suggestions[index],box=document.createElement('section');box.className='editor-candidate';
  const label=document.createElement('label');label.textContent='本镜候选台词 · 采用后才修改';
  const text=document.createElement('textarea');text.readOnly=true;text.rows=4;text.value=suggestion.text;text.setAttribute('aria-label','第'+(index+1)+'镜候选台词');label.append(text);
  const actions=document.createElement('div');actions.className='rewrite-actions';
  const adopt=document.createElement('button');adopt.className='btn-sm';adopt.textContent='采用本镜候选';
  adopt.onclick=()=>{
    if(sceneEditor.aiRunning||sceneEditor.saving)return;
    if(editorSnapshot()!==suggestion.snapshot){document.getElementById('manual-editor-error').textContent='稿件已变化，请重新生成本镜，避免覆盖刚才的编辑。';return;}
    sceneEditor.shots[index].text=suggestion.text;delete sceneEditor.suggestions[index];sceneEditor.dirty=true;
    renderSceneCards();updateEditorPreview();
  };
  const discard=document.createElement('button');discard.className='btn-sm';discard.textContent='保留原台词';
  discard.onclick=()=>{if(!sceneEditor.aiRunning){delete sceneEditor.suggestions[index];renderSceneCards();}};
  actions.append(adopt,discard);box.append(label,actions);host.append(box);
}
