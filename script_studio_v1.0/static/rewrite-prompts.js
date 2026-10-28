// Existing manuscripts and local, editable prompt templates. No source draft is overwritten.
let revisionState = {shots:[], source_kind:null, source_id:null, label:'直接粘贴文案开始'};
let revisionSources = [];
let revisionRunning = false;
let revisionCandidate = null;
let promptEditorState = null;
const revisionFields = ['script','reference','operation','scene-policy','instruction'];
const promptLabels = {system:'系统角色与通用规则', user:'任务模板与 JSON 输出格式', mode_0:'多篇竞对 · 多向融合', mode_1:'单篇竞对 · 定向仿写', mode_2:'竞对骨架 · 原创内容', mode_3:'本体事实 · 自主原创', rewrite_sentence:'逐句仿写规则', rewrite_structure:'结构仿写规则', duration:'AI 时长与语速规则', weapons:'零 / 一 / 多武器规则', record:'文案库与分镜记录规范', profile:'游戏画像注入模板', game_profile:'游戏全貌与特色画像', revision:'已有文案改写规则'};
const revisionElement = name => document.getElementById('revision-' + name);
const postStudio = (url, payload) => studioRequest(url, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
function persistRevision() {
  for (const field of revisionFields) revisionState[field] = revisionElement(field).value;
  revisionState.use_selection = revisionElement('use-selection').checked;
  try { localStorage.setItem('studio-revision-input', JSON.stringify(revisionState)); }
  catch(e) { revisionElement('status').textContent = '浏览器保存空间不足，请保留原文；刷新后可能丢失本次输入。'; }
}
function displayRevision() {
  for (const field of revisionFields) revisionElement(field).value = revisionState[field] ?? ({operation:'润色优化','scene-policy':'preserve'}[field] || '');
  revisionElement('use-selection').checked = !!revisionState.use_selection;
  revisionElement('source-label').textContent = revisionState.label;
  renderRevisionScenes();
}
function showRevision() {
  if (!sceneEditor) {
    const input = structuredClone(revisionState);
    if(input.editor_bound){input.source_kind=null;input.source_id=null;input.editor_bound=false;input.label='直接粘贴文案开始';input.shots=[];}
    openManualEditor({_rewrite_workspace:true,title:'已有文案改写',full_script:'',shots:[]});
    revisionState = input; displayRevision(); persistRevision();
  }
  const panel = document.getElementById('rewrite-panel'); panel.open = true; panel.scrollIntoView({behavior:'smooth', block:'start'});
}
function clearRevision() {
  if (revisionRunning) return;
  if ((revisionElement('script').value || revisionState.shots.length) && !confirm('清空改写区的输入？来源稿件仍保留。')) return;
  revisionState = {shots:[], source_kind:null, source_id:null, label:'直接粘贴文案开始'}; displayRevision(); persistRevision();
}
function fillRevision(record, kind, id) {
  const originalShots = record.shots || [];
  const scenes = originalShots.map((shot,i) => ({...shot, ...(record.manual_shots?.[i] || {}), shot_num:i+1}));
  revisionState = {script:record.manual_script?.trim() ? record.manual_script : record.人工修改稿 || record.full_script || record.二创文案 || '',
    reference:record.source_reference || record.ref_script || record.参考文案 || '', shots:scenes, source_kind:kind, source_id:id,
    label:`来源：${record.title || record.核心卖点 || '已有文案'} · ${record.material_code || record.素材编号 || ''}`, operation:'润色优化', 'scene-policy':scenes.length ? 'preserve' : 'rebuild'};
  displayRevision(); persistRevision(); showRevision(); revisionElement('status').textContent = '已载入。修改后另存新稿，原稿保留。';
}
async function loadCurrentRevision() {
  if (revisionRunning) return;
  if (sceneEditor) { seedEditorRevision(); showRevision(); return; }
  if (!currentGenerated) { toast('请先打开一份草稿，或直接粘贴全文。'); return; }
  if (!await preserveHumanChanges()) return;
  if (!currentGenerated._filename && !await saveHumanDraft(currentGenerated,false)) return;
  fillRevision(currentGenerated,'draft',currentGenerated._filename);
}
async function loadRevisionSource(kind,id) {
  if (revisionRunning) return;
  try {
    const data = await studioRequest(`/api/rewrite-source?kind=${encodeURIComponent(kind)}&source_id=${encodeURIComponent(id)}`);
    if (!sceneEditor) {
      if (!await preserveHumanChanges()) return;
      if (kind === 'draft') {
        currentGenerated=data.record; generatedSlot=data.record.source_slot; renderResult(data.record);
        document.getElementById('empty-hint').style.display='none';
      }
      openManualEditor(kind === 'library' ? {...data.record,_rewrite_workspace:true} : data.record);
    }
    fillRevision(data.record,kind,id); closeHistory();
  } catch(e) { toast('载入失败：' + e.message); }
}
async function refreshRevisionSources() {
  try {
    revisionSources = (await studioRequest('/api/rewrite-sources')).sources;
    revisionElement('source-picker').hidden = false; filterRevisionSources();
  } catch(e) { toast(e.message); }
}
function filterRevisionSources() {
  const query = revisionElement('source-search').value.trim().toLowerCase();
  const options = revisionSources.filter(s => s.label.toLowerCase().includes(query)).map(s => new Option(s.label,JSON.stringify([s.kind,s.id])));
  revisionElement('source-select').replaceChildren(...options);
  if (!options.length) revisionElement('source-select').append(new Option('没有匹配稿件',''));
}
async function loadSelectedRevision() {
  const value = revisionElement('source-select').value; if (value) await loadRevisionSource(...JSON.parse(value));
}
function addRevisionScene() {
  if (revisionRunning) return;
  if (revisionState.shots.length >= 199) { toast('最多199镜。'); return; }
  revisionState.custom_shots = true;
  revisionState.shots.push({text:'',visual:'',game_action:'',duration:3}); renderRevisionScenes(); persistRevision();
}
function renderRevisionScenes() {
  const container = revisionElement('scenes'); container.replaceChildren();
  revisionElement('scene-count').textContent = revisionState.shots.length + ' 镜';
  revisionState.shots.forEach((shot,index) => {
    const card = document.createElement('section'); card.className = 'revision-scene';
    const head = document.createElement('div'); head.className = 'rewrite-actions';
    const title = document.createElement('strong'); title.textContent = `第 ${index+1} 镜`;
    const remove = document.createElement('button'); remove.className = 'btn-sm'; remove.textContent = '删除本镜';
    remove.onclick = () => { if (!revisionRunning) { revisionState.custom_shots=true; revisionState.shots.splice(index,1); renderRevisionScenes(); persistRevision(); } };
    head.append(title,remove); card.append(head);
    for (const [key,label] of [['text','台词草案'],['visual','画面'],['game_action','游戏动作'],['duration','参考时长（秒）']]) {
      const field = document.createElement('label'); field.textContent = label;
      const input = document.createElement(key === 'duration' ? 'input' : 'textarea'); input.value = shot[key] ?? '';
      input.setAttribute('aria-label',`第${index+1}镜${label}`);
      if (key === 'duration') { input.type = 'number'; input.min = 0; input.step = '.1'; } else input.rows = key === 'text' ? 3 : 2;
      input.oninput = () => { revisionState.custom_shots=true; shot[key] = key === 'duration' ? Number(input.value) : input.value; persistRevision(); };
      field.append(input); card.append(field);
    }
    container.append(card);
  });
}
function revisionPayload() {
  if (!materialVisibilityReady()) throw new Error('请先完成竞对状态筛选核对');
  syncRevisionEditorText();
  persistRevision();
  if (!revisionState.script?.trim()) throw new Error('请输入待优化全文。');
  return {hide_used_rivals:hideUsedRivals, script:revisionState.script, shots:structuredClone(revisionState.shots), reference:revisionState.reference,
    source_kind:revisionState.source_kind, source_id:revisionState.source_id, use_selection:revisionState.use_selection,
    selection:sceneEditor ? sceneEditor.selection : batchSelectionPayload(), operation:revisionState.operation, scene_policy:revisionState['scene-policy'],
    instruction:revisionState.instruction, ...editorAISettings()};
}
async function generateRevision() {
  if (busy || revisionRunning || sceneEditor?.saving || sceneEditor?.aiRunning) return;
  if (revisionState.editor_bound && sceneEditor && !sceneEditor.record._filename && !sceneEditor.record._rewrite_workspace) {
    if (!await saveSceneEditor()) return;
    revisionState.source_kind='draft';revisionState.source_id=sceneEditor.record._filename;
  }
  let payload; try { payload = revisionPayload(); } catch(e) { revisionElement('status').textContent = e.message; return; }
  revisionRunning = true; setGenerationBusy(true); setEditorAIWorking(true);
  revisionElement('status').textContent = '正在生成整篇候选，成功后另存新稿，当前精修内容保留…';
  try {
    const data = await postStudio('/api/rewrite',payload);
    revisionCandidate = data.result; refreshDraftCount();
    revisionElement('candidate').hidden = false;
    revisionElement('candidate-name').textContent = data.result.material_code + ' · ' + (data.result.title || '候选新稿');
    revisionElement('candidate-text').value = data.result.full_script;
    revisionElement('status').textContent = '候选已另存草稿。请对比后采用，或继续保留当前稿。';
  } catch(e) { revisionElement('status').textContent = '改写失败：' + e.message; }
  finally { revisionRunning = false; setGenerationBusy(false); setEditorAIWorking(false); }
}
function currentPromptPayload() {
  if (!currentSlot) throw new Error('请先组装当前创作素材。');
  return {...structuredClone(currentSlot), ...generationSettings(), selection:batchSelectionPayload()};
}
function samePromptTemplates(a, b) {
  return Object.keys(promptLabels).every(key => (a?.[key] ?? '') === (b?.[key] ?? ''));
}
function loadLocalPromptVersions() {
  try {
    const raw = JSON.parse(localStorage.getItem('studio-prompt-custom-versions') || '[]');
    return Array.isArray(raw) ? raw.filter(v => v && v.id && v.name && v.templates) : [];
  } catch(e) { return []; }
}
function saveLocalPromptVersions(list) {
  try { localStorage.setItem('studio-prompt-custom-versions', JSON.stringify(list)); } catch(e) {}
}
function mergePromptVersions(serverVersions = []) {
  const merged = [];
  const seenIds = new Set();
  const seenNames = new Set();
  for (const item of serverVersions) {
    if (!item?.id || !item?.templates) continue;
    merged.push({...item, builtin: item.builtin !== false && !String(item.id).startsWith('custom_')});
    seenIds.add(item.id);
    seenNames.add(item.name);
  }
  for (const item of loadLocalPromptVersions()) {
    if (seenIds.has(item.id) || seenNames.has(item.name)) continue;
    merged.push({...item, builtin: false});
  }
  return merged;
}
function findMatchingPromptVersion(templates, versions = promptEditorState?.versions || []) {
  return versions.find(item => samePromptTemplates(item.templates, templates)) || null;
}
function renderPromptVersionSwitcher() {
  const state = promptEditorState;
  if (!state) return;
  const select = document.getElementById('prompt-version-select');
  const desc = document.getElementById('prompt-version-desc');
  const deleteBtn = document.getElementById('prompt-version-delete');
  if (!select || !desc) return;
  const activeVersion = findMatchingPromptVersion(state.savedTemplates, state.versions);
  const currentVersion = findMatchingPromptVersion(state.templates, state.versions);
  const options = state.versions.map(item => {
    const suffix = activeVersion?.id === item.id ? ' [当前生效]' : '';
    return new Option(item.name + suffix, item.id);
  });
  if (!currentVersion) {
    options.unshift(new Option('自定义修改中（未另存为独立版本）' + (!state.dirty ? ' [当前生效]' : ''), '__custom__'));
  }
  select.replaceChildren(...options);
  select.value = currentVersion ? currentVersion.id : '__custom__';
  if (currentVersion) {
    const activeNote = activeVersion?.id === currentVersion.id
      ? '状态：当前正在生效'
      : '状态：已载入编辑器（点击“切换并生效”或“保存并生效”后正式启用）';
    desc.textContent = `${currentVersion.description || '自定义提示词版本'} · ${activeNote}`;
  } else {
    desc.textContent = '当前内容已手动修改；可点击“保存并生效”直接应用，或在下方输入名称“另存为新版本并生效”。';
  }
  if (deleteBtn) deleteBtn.hidden = !currentVersion || !!currentVersion.builtin;
}
function selectPromptVersion() {
  if (!promptEditorState) return;
  syncPromptText();
  const chosenId = document.getElementById('prompt-version-select').value;
  const chosen = promptEditorState.versions.find(item => item.id === chosenId);
  if (!chosen) return;
  promptEditorState.templates = structuredClone(chosen.templates);
  showPromptSection();
  clearPromptPreview();
  promptEditorState.dirty = !samePromptTemplates(promptEditorState.templates, promptEditorState.savedTemplates);
  renderPromptVersionSwitcher();
  document.getElementById('prompt-status').textContent = promptEditorState.dirty
    ? `已载入版本「${chosen.name}」；可先预览，或点击“切换并生效”立即设为生效版本。`
    : `已切回当前正在生效的版本：「${chosen.name}」。`;
}
async function applySelectedPromptVersion() {
  await savePromptEditor();
}
async function savePromptAsNewVersion() {
  if (!promptEditorState || promptEditorState.saving) return;
  const input = document.getElementById('prompt-version-name');
  const cleanName = (input?.value || '').trim();
  if (!cleanName) {
    document.getElementById('prompt-status').textContent = '请先输入新提示词版本名称（例如：v3 短句反差版）。';
    input?.focus();
    return;
  }
  if (promptEditorState.versions.some(item => item.builtin && item.name === cleanName)) {
    document.getElementById('prompt-status').textContent = '不能覆盖内置版本名称，请换一个版本名称。';
    return;
  }
  const ok = await savePromptEditor(cleanName);
  if (ok && input) input.value = '';
}
async function deleteSelectedPromptVersion() {
  const state = promptEditorState;
  if (!state || state.saving) return;
  const currentVersion = findMatchingPromptVersion(state.templates, state.versions);
  if (!currentVersion || currentVersion.builtin) return;
  if (!confirm(`确定删除自定义提示词版本「${currentVersion.name}」吗？`)) return;
  const localList = loadLocalPromptVersions().filter(item => item.id !== currentVersion.id && item.name !== currentVersion.name);
  saveLocalPromptVersions(localList);
  try {
    const data = await studioRequest('/api/prompts', {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({templates: state.savedTemplates, revision: state.revision, delete_version_id: currentVersion.id})
    });
    state.revision = data.revision;
    state.versions = mergePromptVersions(data.versions || state.versions.filter(item => item.id !== currentVersion.id));
  } catch(e) {
    state.versions = state.versions.filter(item => item.id !== currentVersion.id);
  }
  state.templates = structuredClone(state.savedTemplates);
  state.dirty = false;
  showPromptSection();
  renderPromptVersionSwitcher();
  document.getElementById('prompt-status').textContent = `已删除自定义版本「${currentVersion.name}」，并恢复到当前生效版本。`;
}
async function openPromptEditor(forRevision = false) {
  try {
    if (!promptEditorState) {
      const data = await studioRequest('/api/prompts');
      const versions = mergePromptVersions(data.versions || []);
      promptEditorState = {
        ...data,
        versions,
        savedTemplates: structuredClone(data.templates),
        templates: structuredClone(data.templates),
        key: 'system',
        dirty: false
      };
      clearPromptPreview(); document.getElementById('prompt-status').textContent = '';
      document.getElementById('prompt-section').replaceChildren(...Object.keys(promptLabels).map(key => new Option(promptLabels[key],key)));
      document.getElementById('prompt-section').value = 'system';
      showPromptSection();
      renderPromptVersionSwitcher();
    }
    document.getElementById('prompt-preview-target').value = forRevision ? 'revision' : 'generate';
    document.getElementById('prompt-editor').showModal();
    if (forRevision) await previewFullPrompt();
  } catch(e) { toast('提示词读取失败：' + e.message); }
}
function syncPromptText() { if (promptEditorState) promptEditorState.templates[promptEditorState.key] = document.getElementById('prompt-text').value; }
function showPromptSection() {
  const state = promptEditorState; document.getElementById('prompt-text').value = state.templates[state.key];
  document.getElementById('prompt-text-label').textContent = promptLabels[state.key];
  document.getElementById('prompt-tokens').textContent = state.tokens[state.key].length ? '保留变量：' + state.tokens[state.key].map(t => '{{' + t + '}}').join('、') : '本板块没有动态变量，可直接修改文字。';
}
function changePromptSection() { syncPromptText(); promptEditorState.key = document.getElementById('prompt-section').value; showPromptSection(); }
function clearPromptPreview() { document.getElementById('prompt-preview-system').value = ''; document.getElementById('prompt-preview-user').value = ''; document.getElementById('prompt-preview').open = false; }
function markPromptDirty() {
  syncPromptText();
  promptEditorState.dirty = !samePromptTemplates(promptEditorState.templates, promptEditorState.savedTemplates);
  clearPromptPreview();
  renderPromptVersionSwitcher();
  document.getElementById('prompt-status').textContent = promptEditorState.dirty
    ? '有未保存修改；预览使用当前编辑内容，保存后用于生成。'
    : '当前内容与已生效版本一致。';
}
function closePromptEditor() {
  if (promptEditorState?.saving) return;
  if (promptEditorState?.dirty && !confirm('关闭并放弃尚未保存的提示词修改？')) return;
  document.getElementById('prompt-editor').close(); promptEditorState = null;
}
async function savePromptEditor(versionName = null) {
  syncPromptText(); const state = promptEditorState; if (state.saving) return false; state.saving = true;
  const controls = [...document.querySelectorAll('#prompt-editor button,#prompt-editor textarea,#prompt-editor select,#prompt-editor input')];
  controls.forEach(c => c.disabled = true);
  let succeeded = false;
  try {
    const payload = {templates: state.templates, revision: state.revision};
    if (versionName) payload.version_name = versionName;
    const data = await studioRequest('/api/prompts',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    state.revision = data.revision;
    state.savedTemplates = structuredClone(state.templates);
    state.dirty = false;
    if (versionName) {
      const localList = loadLocalPromptVersions();
      const existing = localList.find(item => item.name === versionName);
      if (existing) {
        existing.templates = structuredClone(state.templates);
      } else {
        localList.push({
          id: 'custom_' + Date.now().toString(36),
          name: versionName,
          description: '用户自定义保存版本',
          builtin: false,
          templates: structuredClone(state.templates)
        });
      }
      saveLocalPromptVersions(localList);
    }
    state.versions = mergePromptVersions(data.versions || state.versions);
    renderPromptVersionSwitcher();
    const matched = findMatchingPromptVersion(state.savedTemplates, state.versions);
    document.getElementById('prompt-status').textContent = versionName
      ? `已另存为新版本「${versionName}」并立即生效；后续生成和改写使用此版本。`
      : `已保存并生效${matched ? '（' + matched.name + '）' : ''}，后续生成和改写使用新规则；已启动批次保留原规则。`;
    succeeded = true;
  } catch(e) { document.getElementById('prompt-status').textContent = '保存失败：' + e.message; }
  finally { state.saving = false; controls.forEach(c => c.disabled = false); renderPromptVersionSwitcher(); }
  return succeeded;
}
function resetPromptSection() { promptEditorState.templates[promptEditorState.key] = promptEditorState.defaults[promptEditorState.key]; showPromptSection(); markPromptDirty(); }
function resetAllPrompts() {
  if (!confirm('将全部板块恢复为默认？点击“保存并生效”后才会更新。')) return;
  promptEditorState.templates = structuredClone(promptEditorState.defaults); showPromptSection(); markPromptDirty();
}
async function previewFullPrompt() {
  syncPromptText(); clearPromptPreview(); document.getElementById('prompt-preview-button').disabled = true;
  try {
    const payload = document.getElementById('prompt-preview-target').value === 'revision' ? (await postStudio('/api/rewrite/prepare',revisionPayload())).request : currentPromptPayload();
    payload.prompt_templates = structuredClone(promptEditorState.templates);
    const data = await postStudio('/api/prompts/preview',payload);
    document.getElementById('prompt-preview-system').value = data.system; document.getElementById('prompt-preview-user').value = data.user;
    document.getElementById('prompt-preview').open = true;
    document.getElementById('prompt-status').textContent = promptEditorState.dirty ? '预览使用尚未保存的编辑内容，未调用模型。' : '完整预览已更新，未调用模型。';
  } catch(e) { document.getElementById('prompt-status').textContent = '预览失败：' + e.message; }
  finally { document.getElementById('prompt-preview-button').disabled = false; }
}
async function copyFullPrompt() {
  const system = document.getElementById('prompt-preview-system').value, user = document.getElementById('prompt-preview-user').value;
  if (!system || !user) { toast('请先预览完整提示词。'); return; }
  try { await navigator.clipboard.writeText('【System】\n' + system + '\n\n【User】\n' + user); toast('完整提示词已复制。'); } catch(e) { toast('复制失败，可直接选中预览文本复制。'); }
}
document.getElementById('prompt-editor').addEventListener('cancel',event => {event.preventDefault(); closePromptEditor();});
window.addEventListener('beforeunload',event => { if (promptEditorState?.dirty || revisionRunning) { event.preventDefault(); event.returnValue = ''; } });
try { const stored = JSON.parse(localStorage.getItem('studio-revision-input')); if (stored && Array.isArray(stored.shots)) revisionState = stored; } catch(e) {}
displayRevision();
