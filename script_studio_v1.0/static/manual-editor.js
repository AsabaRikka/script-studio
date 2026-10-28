// Human revisions are separate from generated text. Existing drafts use revision-checked saves.
let humanDirty = false;
let humanSaving = false;
let sceneEditor = null;
let manualRenderedRecord = null;
const compactScript = text => String(text || '').replace(/\s/g, '');
const humanSignature = record => JSON.stringify([record.manual_script || '', record.manual_shots || null]);
function referenceText(record) {
  const rivals = record.source_slot?.rivals || [];
  const parts = rivals.filter(r => r.raw_script).map(r => `【来源 ${r.code || r.id}】\n${r.raw_script}`);
  return record.source_reference || (parts.length ? parts.join('\n\n') : record.ref_script || '未保存来源原文');
}
function weaponAnnotation(record) {
  const text = record.manual_script?.trim() ? record.manual_script : record.full_script || '';
  const candidates = [...(record.source_slot?.weapon_candidates || []), ...(overviewData?.weapons || []), ...(record.used_weapons || [])];
  const names = [...new Set(candidates.filter(w => w.name && text.includes(w.name)).map(w => w.name))];
  return names.length ? `${names.length > 1 ? '多武器' : '使用武器'}：${names.join('、')}` : '未提及具体武器';
}

function humanStats(script, shots, cap) {
  const chars = compactScript(script).length;
  const estimated = chars / 5.6;
  const duration = (shots || []).reduce((sum, shot) => sum + (Number(shot.duration) || 0), 0);
  return `${chars} 字 · 口播约 ${estimated.toFixed(1)} 秒${shots?.length ? ` · 分镜参考 ${duration.toFixed(1)} 秒` : ''}`;
}
function renderManualPanel(record) {
  document.getElementById('manual-script').value = record.manual_script ?? record.人工修改稿 ?? '';
  if (manualRenderedRecord !== record) humanDirty = false;
  manualRenderedRecord = record;
  refreshManualStatus();
}
function refreshManualStatus() {
  if (!currentGenerated) return;
  const message = document.getElementById('manual-script-info');
  const manual = currentGenerated.manual_script || '';
  message.textContent = `${humanDirty ? '有未保存修改' : '人工稿单独保存'} · ${humanStats(manual, currentGenerated.manual_shots, currentGenerated.max_duration)}${manual && !currentGenerated.manual_shots ? ' · 仅整篇修改，分镜沿用原稿' : ''}`;
  message.classList.remove('manual-warning');
  document.getElementById('res-weapons').textContent = weaponAnnotation(currentGenerated);
}
function updateManualScript(value) {
  if (!currentGenerated) return;
  currentGenerated.manual_script = value;
  if (currentGenerated.manual_shots && compactScript(value) !== compactScript(currentGenerated.manual_shots.map(s => s.text).join(''))) {
    toast('全文与分镜台词未同步，分镜精修保留，可按换行重新分配。');
  }
  humanDirty = true;
  refreshManualStatus();
}
async function saveHumanDraft(record = currentGenerated, notify = true) {
  if (!record || humanSaving) return false;
  humanSaving = true;
  const signature = humanSignature(record);
  const filename = record._filename;
  try {
    let data;
    if (filename) {
      data = await studioRequest('/api/draft-editor/' + encodeURIComponent(filename), {
        method:'PUT', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({manual_script:record.manual_script || '', manual_shots:record.manual_shots || null, revision:record._edit_revision || 0})
      });
    } else {
      data = await studioRequest('/api/save-draft', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(record)});
      data = await studioRequest('/api/draft-editor/' + encodeURIComponent(data.filename));
    }
    record._filename = data.record._filename || data.filename;
    record._edit_revision = data.record._edit_revision || 0;
    record._collected = data.record._collected;
    if (currentGenerated === record && humanSignature(record) === signature) humanDirty = false;
    if (currentGenerated === record) refreshManualStatus();
    refreshDraftCount();
    if (notify) toast('人工稿已保存到草稿箱。');
    return true;
  } catch(e) { toast('保存失败：' + e.message); return false; }
  finally { humanSaving = false; }
}
async function preserveHumanChanges() {
  if (humanSaving) { toast('正在保存，请稍候。'); return false; }
  return !humanDirty || await saveHumanDraft(currentGenerated, false);
}
async function loadEditableDraft(filename, immersive = false) {
  if (!await preserveHumanChanges()) return;
  try {
    const data = await studioRequest('/api/draft-editor/' + encodeURIComponent(filename));
    currentGenerated = data.record;
    generatedSlot = currentGenerated.source_slot || null;
    renderResult(currentGenerated);
    document.getElementById('empty-hint').style.display = 'none';
    closeHistory();
    if (immersive) openManualEditor();
    else document.getElementById('manual-script').focus();
  } catch(e) { toast('打开草稿失败：' + e.message); }
}
function originalSceneRows(record) {
  return (record.shots || []).map(s => ({text:s.text || '', visual:s.visual || '', game_action:s.game_action || '', duration:Number(s.duration) || 1}));
}
function openManualEditor(record = currentGenerated) {
  if (!record) return;
  document.getElementById('editor-manuscript').disabled = false;
  const shots = structuredClone(record.manual_shots || originalSceneRows(record));
  const manual = record.manual_script || '';
  const mismatch = (manual.trim() || record.manual_shots) && manual !== shots.map(s => s.text).join('\n');
  sceneEditor = {record, shots, initial:JSON.stringify(shots), dirty:humanDirty && record===currentGenerated, pending:mismatch ? manual : shots.length ? null : record.full_script || '', saving:false, aiRunning:false, suggestions:{}};
  document.getElementById('manual-editor-name').textContent = record.title || '口播文稿';
  document.getElementById('manual-editor-code').textContent = record.material_code || '';
  document.getElementById('editor-original').textContent = record.full_script || '';
  document.getElementById('editor-reference').textContent = referenceText(record);
  document.getElementById('manual-editor-mismatch').hidden = !mismatch;
  document.getElementById('manual-editor-error').textContent = '';
  renderSceneCards();
  updateEditorPreview();
  seedEditorRevision();
  document.getElementById('manual-editor').showModal();
}
function resolveSceneMismatch(useLines) {
  if (!sceneEditor || sceneEditor.saving || sceneEditor.aiRunning) return;
  if (useLines) {
    const lines = document.getElementById('editor-manuscript').value.split(/\n/).map(s => s.trim()).filter(Boolean);
    if (lines.length !== sceneEditor.shots.length) {
      document.getElementById('manual-editor-error').textContent = `请按一镜一段换行，需要 ${sceneEditor.shots.length} 段，当前 ${lines.length} 段。`;
      return;
    }
    sceneEditor.shots.forEach((s, i) => s.text = lines[i]);
  } else if (!confirm('用当前分镜台词替换修改稿全文？')) return;
  sceneEditor.pending = null;
  sceneEditor.dirty = true;
  document.getElementById('manual-editor-mismatch').hidden = true;
  document.getElementById('manual-editor-error').textContent = '';
  renderSceneCards();
  updateEditorPreview();
}
function renderSceneCards() {
  const list = document.getElementById('manual-editor-scenes');
  list.replaceChildren();
  const referenceLabel = sceneEditor.record.source_slot?.mode === '多篇竞对-多向融合' ? '融合来源贡献' : '参考原句';
  sceneEditor.shots.forEach((shot, index) => {
    const original = sceneEditor.record.shots[index];
    const card = document.createElement('article');
    card.className = 'editor-scene'; card.id = 'editor-scene-' + index;
    card.innerHTML = `<div class="editor-scene-head"><div><span class="scene-number">${String(index+1).padStart(2,'0')}</span><strong>第 ${index+1} 镜</strong><span class="editor-scene-stats"></span></div><button class="btn-sm" type="button">还原本镜</button></div>
      <div class="editor-scene-comparison"><div class="editor-scene-original"><div class="editor-copy-label">AI 原稿台词</div><p class="editor-ai-text editor-copy"></p><div class="editor-copy-label">${referenceLabel}</div><p class="editor-ref-text editor-copy"></p></div><div class="editor-scene-manual"><label class="editor-copy-label" for="editor-text-${index}">修改稿台词</label><textarea id="editor-text-${index}" class="editor-dialogue" rows="5" data-field="text"></textarea></div></div>
      <div class="editor-scene-fields"><div><label for="editor-visual-${index}">分镜画面</label><textarea id="editor-visual-${index}" rows="3" data-field="visual"></textarea></div><div><label for="editor-action-${index}">游戏动作</label><textarea id="editor-action-${index}" rows="3" data-field="game_action"></textarea></div></div>
      <div class="editor-scene-foot"><label for="editor-duration-${index}">参考时长（秒，可留空）</label><input id="editor-duration-${index}" type="number" min="0" step="any" data-field="duration"><span class="editor-speed-note"></span></div>`;
    for (const input of card.querySelectorAll('[data-field]')) {
      const key = input.dataset.field;
      input.value = shot[key];
      input.disabled = sceneEditor.pending !== null;
      input.addEventListener('input', () => {
        shot[key] = key === 'duration' ? (input.value === '' ? null : Number(input.value)) : input.value;
        sceneEditor.dirty = true;
        document.getElementById('manual-editor-error').textContent = '';
        updateEditorPreview();
      });
    }
    card.querySelector('button').disabled = sceneEditor.pending !== null;
    card.querySelector('button').onclick = () => {
      if (sceneEditor.saving) return;
      if (!confirm(`还原第 ${index+1} 镜的台词、画面和时长？`)) return;
      sceneEditor.shots[index] = originalSceneRows(sceneEditor.record)[index];
      sceneEditor.dirty = true; renderSceneCards(); updateEditorPreview();
    };
    card.querySelector('.editor-ai-text').textContent = original.text || '未保存 AI 原稿台词';
    card.querySelector('.editor-ref-text').textContent = original.ref_text || '无';
    const ai = document.createElement('div'); ai.className = 'editor-shot-ai';
    const label = document.createElement('label'); label.textContent = '本镜重生成要求（可留空）';
    const instruction = document.createElement('textarea'); instruction.rows = 2; instruction.id = 'editor-shot-instruction-' + index;
    instruction.value = shot._aiInstruction || ''; instruction.placeholder = '例如：保留动作，台词更口语、更有紧迫感';
    instruction.oninput = () => { shot._aiInstruction = instruction.value; };
    const regenerate = document.createElement('button'); regenerate.className = 'btn-sm'; regenerate.textContent = '重新生成本镜文案'; regenerate.dataset.shotRegenerate = index;
    regenerate.disabled = sceneEditor.pending !== null || !sceneEditor.record.source_slot;
    regenerate.onclick = () => regenerateEditorShot(index);
    label.append(instruction); ai.append(label,regenerate);
    if (sceneEditor.suggestions[index]) renderShotSuggestion(ai,index);
    card.append(ai);
    list.append(card);
  });
}
function updateEditorPreview() {
  const {record, shots, pending, saving} = sceneEditor;
  const preview = document.getElementById('editor-manuscript');
  const script = pending !== null ? pending : shots.map(s => s.text).join('\n');
  if (preview.value !== script) preview.value = script;
  document.getElementById('editor-total').textContent = humanStats(script, shots) + ' · 时长仅供参考';
  shots.forEach((shot, index) => {
    const card = document.getElementById('editor-scene-' + index);
    const chars = compactScript(shot.text).length;
    card.querySelector('.editor-scene-stats').textContent = `${chars} 字 · 约 ${(chars/5.6).toFixed(1)} 秒`;
    card.classList.remove('scene-warning');
    card.querySelector('.editor-speed-note').textContent = '时长参考，不限制人工稿';
  });
  document.getElementById('editor-warning').textContent = pending !== null ? '可直接保存全文。分镜精修保留；明确按换行分配后同步分镜台词。' : '全文随分镜实时更新；人工稿不受语速或总时长上限限制。';
  document.getElementById('manual-editor-save').disabled = saving || sceneEditor.aiRunning || record._rewrite_workspace;
  document.getElementById('manual-editor-preview').disabled = saving || sceneEditor.aiRunning || !record.source_slot || record._rewrite_workspace;
  document.getElementById('manual-editor-state').textContent = saving ? '保存中…' : sceneEditor.dirty ? '有未保存修改' : '逐镜编辑 · 全文联动';
  if (typeof syncRevisionEditorText === 'function') syncRevisionEditorText();
  document.querySelectorAll('[data-shot-regenerate]').forEach(button => button.disabled = saving || sceneEditor.aiRunning || pending !== null || !record.source_slot || sceneEditor.shotAPI===false);
}
async function saveSceneEditor(preview = false) {
  if (!sceneEditor || sceneEditor.saving || sceneEditor.aiRunning || sceneEditor.record._rewrite_workspace) return false;
  const editor = sceneEditor;
  const dialog = document.getElementById('manual-editor');
  for (const input of dialog.querySelectorAll('input,textarea')) if (!input.reportValidity()) return;
  if (editor.shots.some(s => !s.text.trim())) { document.getElementById('manual-editor-error').textContent = '每镜都需要填写台词。'; return; }
  editor.saving = true; updateEditorPreview();
  const candidate = {...editor.record, manual_shots:editor.shots.length ? editor.shots.map(({text,visual,game_action,duration})=>({text,visual,game_action,duration})) : null, manual_script:editor.pending !== null ? editor.pending : editor.shots.map(s => s.text).join('\n')};
  dialog.querySelectorAll('[data-field],#editor-manuscript').forEach(el => el.disabled = true);
  const success = await saveHumanDraft(candidate);
  if (success) {
    currentGenerated = candidate;
    editor.record = candidate; editor.dirty = false; editor.initial = JSON.stringify(editor.shots);
    generatedSlot = candidate.source_slot || generatedSlot;
    renderResult(candidate);
    if (preview) { document.getElementById('editor-manuscript').disabled = false; dialog.close(); sceneEditor = null; await writebackToFeishu(); return true; }
  }
  editor.saving = false;
  document.getElementById('editor-manuscript').disabled = false;
  dialog.querySelectorAll('[data-field]').forEach(el => el.disabled = editor.pending !== null);
  updateEditorPreview();
  return success;
}
function editManuscript(value) {
  if (!sceneEditor || sceneEditor.saving || sceneEditor.aiRunning) return;
  sceneEditor.pending = value === sceneEditor.shots.map(s => s.text).join('\n') ? null : value;
  sceneEditor.dirty = true;
  document.getElementById('manual-editor-mismatch').hidden = sceneEditor.pending === null;
  document.querySelectorAll('#manual-editor [data-field]').forEach(el => el.disabled = sceneEditor.pending !== null);
  updateEditorPreview();
}
function closeManualEditor() {
  if (sceneEditor?.saving || sceneEditor?.aiRunning) return;
  if (sceneEditor?.dirty && !confirm('精修修改尚未保存。放弃本次修改并关闭？')) return;
  document.getElementById('manual-editor').close(); sceneEditor = null;
}
document.getElementById('manual-editor').addEventListener('cancel', e => { e.preventDefault(); closeManualEditor(); });
document.getElementById('manual-editor').addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); saveSceneEditor(); }
});
window.addEventListener('beforeunload', e => {
  if (humanDirty || humanSaving || sceneEditor?.dirty || sceneEditor?.saving || sceneEditor?.aiRunning) { e.preventDefault(); e.returnValue = ''; }
});
