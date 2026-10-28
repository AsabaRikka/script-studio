let modelSettings = {};
let generatedSlot = null;
let batchJob = null;
let batchTimer = null;
let busy = false;
let hideUsedRivals = localStorage.getItem('studio-hide-used-rivals') !== 'false';
let countDraftUsage = localStorage.getItem('studio-count-draft-usage') === 'true';
let visibilityReady = false;
let visibilityLoading = false;
let pureRandomSampling = localStorage.getItem('studio-pure-random') === 'true';
let randomSamplingSupported = null;
let randomSamplingCounts = {};
try { randomSamplingCounts = JSON.parse(sessionStorage.getItem('studio-random-counts')) || {}; } catch(e) {}

function renderRandomSampling() {
  document.getElementById('pure-random-sampling').checked = pureRandomSampling;
  document.getElementById('sampling-help').textContent = pureRandomSampling
    ? '已选板块在勾选范围内等概率抽取，其他未选板块从当前可用全库抽。玩法库与关卡库例外：未勾选均不注入，勾选后才体现本次所选条目。每篇随机1把武器；连续重抽先抽完再复用，每批独立去重。'
    : '当前按使用次数优先抽低使用素材；纯随机忽略次数，用尽后复用。玩法库与关卡库未勾选均不注入，勾选后才体现本次所选条目。';
  if (pureRandomSampling && randomSamplingSupported !== true) {
    document.getElementById('sampling-status').textContent = randomSamplingSupported === null
      ? '正在核对纯随机接口…'
      : '后端尚未加载纯随机抽取：请保存页面编辑、等待所有任务完成后，通过现有重启入口加载。';
  }
}

async function detectRandomSamplingSupport() {
  try {
    const api = await studioRequest('/openapi.json');
    randomSamplingSupported = !!(api.components?.schemas?.SlotRequest?.properties?.pure_random
      && api.components?.schemas?.BatchRequest?.properties?.pure_random);
  } catch(e) { randomSamplingSupported = false; }
  renderRandomSampling(); setGenerationBusy(busy);
}

function randomSamplingReady() {
  if (!pureRandomSampling || randomSamplingSupported === true) return true;
  renderRandomSampling();
  return false;
}

function changeRandomSampling() {
  pureRandomSampling = document.getElementById('pure-random-sampling').checked;
  localStorage.setItem('studio-pure-random', String(pureRandomSampling));
  document.getElementById('sampling-status').textContent = '';
  selectedWeaponId = null; selectedRivalId = null;
  renderRandomSampling(); invalidateMaterialSlot();
}

function acceptRandomSampling(sampling) {
  if (!pureRandomSampling || sampling?.strategy !== 'uniform') return;
  if (sampling.counts) {
    randomSamplingCounts = structuredClone(sampling.counts);
    sessionStorage.setItem('studio-random-counts', JSON.stringify(randomSamplingCounts));
  }
  document.getElementById('sampling-status').textContent = sampling.note
    || (sampling.reused?.length ? '已进入下一轮随机复用：' + sampling.reused.join('、') : '本批在候选足够时不重复，用尽后再随机复用。');
}

function resetRandomSamplingRound() {
  randomSamplingCounts = {};
  sessionStorage.removeItem('studio-random-counts');
  if (pureRandomSampling && randomSamplingSupported === true) {
    document.getElementById('sampling-status').textContent = '范围已更新，从新的一轮开始抽取。';
  }
}

function materialVisibilityReady() {
  if (!visibilityLoading && (!hideUsedRivals || visibilityReady)) return true;
  const error = document.getElementById('error-box');
  error.textContent = '已使用素材筛选尚未就绪，请刷新状态；后端未更新时需等待任务完成并保存页面后重启服务。';
  error.style.display = 'block';
  return false;
}

async function loadMaterialOverview(force = false) {
  visibilityLoading = true;
  setGenerationBusy(busy);
  const toggle = document.getElementById('hide-used-rivals');
  const draftUsageToggle = document.getElementById('count-draft-usage');
  draftUsageToggle.checked = countDraftUsage; draftUsageToggle.disabled = true;
  const refresh = document.getElementById('refresh-rival-status');
  const status = document.getElementById('material-visibility-status');
  toggle.checked = hideUsedRivals; toggle.disabled = true; refresh.disabled = true;
  status.textContent = '正在核对飞书状态…';
  const levelStatus = document.getElementById('level-refresh-status');
  let levelRefresh = null, levelError = '';
  if (force) levelStatus.textContent = '关卡库：正在从飞书读取最新内容…';
  try {
    if (force) {
      const [rivalResult, levelResult] = await Promise.allSettled([
        studioRequest('/api/material-visibility/refresh', {method:'POST'}),
        (async () => {
          const api = await studioRequest('/openapi.json');
          if (!api.paths?.['/api/levels/refresh']?.post) {
            throw new Error('后端尚未加载关卡刷新接口，请先保存页面、等待任务结束后重启服务');
          }
          return studioRequest('/api/levels/refresh', {method:'POST'});
        })()
      ]);
      if (levelResult.status === 'fulfilled') levelRefresh = levelResult.value;
      else levelError = ['Not Found', 'Method Not Allowed'].includes(levelResult.reason.message)
        ? '后端尚未加载关卡刷新接口，请先保存页面、等待任务结束后重启服务'
        : levelResult.reason.message;
      if (rivalResult.status === 'rejected') throw rivalResult.reason;
    }
    const data = await studioRequest('/api/overview?hide_used_rivals=' + hideUsedRivals + '&include_drafts=' + countDraftUsage);
    if (hideUsedRivals && (!data.visibility?.enabled || !data.visibility?.ready)) throw new Error('后端尚未加载筛选规则，请保存页面并等待任务完成后重启服务');
    overviewData = data; visibilityReady = true;
    document.getElementById('material-usage-status').textContent = countDraftUsage
      ? (data.usage?.include_drafts === true ? '使用次数：文案库 + 本地草稿（同编号不重复）' : '草稿计数尚未启用：请保存页面并等待任务结束后重启服务')
      : '使用次数：仅文案库';
    const hidden = data.visibility?.hidden;
    status.textContent = hideUsedRivals
      ? `已隐藏竞对 ${hidden.rivals} · 组件 ${hidden.components} · 分镜 ${hidden.shots}；${new Date(data.visibility.updated_at).toLocaleTimeString()} 更新`
      : '已关闭，显示本地完整素材范围';
  } catch(e) {
    visibilityReady = false;
    // Old backends and failed reads must never silently expose the three pools.
    const data = await studioRequest('/api/overview').catch(() => structuredClone(overviewData || {counts:{},weapons:[],welfares:[],gameplays:[]}));
    if (hideUsedRivals) {
      for (const key of ['rivals','shots','components']) data[key] = [];
      for (const key of ['rival_struct','rival_shots','rival_comp','rival_unused','shots_unused','comp_unused']) data.counts[key] = 0;
    }
    overviewData = data;
    status.textContent = '筛选未就绪：' + e.message;
    document.getElementById('material-usage-status').textContent = '使用次数未更新，请重试刷新状态';
  } finally {
    if (force) {
      levelStatus.textContent = levelRefresh
        ? `关卡库：已刷新 ${levelRefresh.record_count} 条；${new Date(levelRefresh.updated_at).toLocaleTimeString()}`
        : `关卡库未刷新，保留原缓存：${levelError || '请重试'}`;
    } else if (!levelStatus.textContent.includes('已刷新') && !levelStatus.textContent.includes('未刷新')) {
      levelStatus.textContent = `关卡库：${overviewData?.counts?.levels || 0} 条本地缓存，点击按钮读取飞书最新内容`;
    }
    visibilityLoading = false; toggle.disabled = false; refresh.disabled = false; draftUsageToggle.disabled = false;
    setGenerationBusy(busy);
  }
}

async function changeMaterialVisibility(force = false) {
  hideUsedRivals = document.getElementById('hide-used-rivals').checked;
  localStorage.setItem('studio-hide-used-rivals', String(hideUsedRivals));
  if (!force) resetRandomSamplingRound();
  currentSlot = null; selectedRivalId = null;
  await loadMaterialOverview(force);
  for (const [id,key] of [['w','weapons'],['r','rival_struct'],['s','rival_shots'],['c','rival_comp'],['wf','welfare'],['gp','gameplay'],['level','levels']]) {
    document.getElementById('count-'+id).textContent = overviewData.counts[key];
  }
  reconcileMaterialSelection(); renderCurrentTab();
  if (materialVisibilityReady()) await rollSlot(selectedWeaponId);
}

async function changeDraftUsageCounting() {
  countDraftUsage = document.getElementById('count-draft-usage').checked;
  localStorage.setItem('studio-count-draft-usage', String(countDraftUsage));
  await loadMaterialOverview();
  if (!overviewData) return;
  for (const [id,key] of [['w','weapons'],['r','rival_struct'],['s','rival_shots'],['c','rival_comp'],['wf','welfare'],['gp','gameplay'],['level','levels']]) {
    document.getElementById('count-'+id).textContent = overviewData.counts[key];
  }
  renderCurrentTab();
}
let activeResultCode = null;
let batchListCollapsed = localStorage.getItem('studio-batch-folded') === 'true';
let historyRecords = [];
let historyCollectedOnly = false;
const escapeText = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const materialLabels = {weapons:'武器', rivals:'竞对', shots:'分镜', comps:'组件', welfares:'福利', gameplays:'玩法', levels:'关卡'};
const materialSelection = Object.fromEntries(Object.keys(materialLabels).map(key => [key, new Set()]));
try {
  const saved = JSON.parse(localStorage.getItem('studio-material-selection')) || {};
  for (const key of Object.keys(materialLabels)) {
    if (Array.isArray(saved[key])) materialSelection[key] = new Set(saved[key].filter(id => typeof id === 'string'));
  }
} catch(e) {}

function batchSelectionPayload() {
  return Object.fromEntries(Object.entries(materialSelection).map(([key, ids]) => [key, [...ids]]));
}
let materialDrawRevision = 0;
function invalidateMaterialSlot() {
  materialDrawRevision++;
  currentSlot = null;
  document.getElementById('slot-summary').textContent = '范围已更新，请重新抽取';
  for (const id of ['slot-level','slot-gameplay','slot-weapon','slot-rivals','slot-shots','slot-comps','slot-welfare-emotion']) {
    const element = document.getElementById(id);
    if (element) element.replaceChildren();
  }
  if (materialSelection.weapons.size && !materialSelection.weapons.has(selectedWeaponId)) selectedWeaponId = null;
  if (materialSelection.rivals.size && !materialSelection.rivals.has(selectedRivalId)) selectedRivalId = null;
  setGenerationBusy(busy);
}
function saveMaterialSelection(changed = true) {
  localStorage.setItem('studio-material-selection', JSON.stringify(batchSelectionPayload()));
  renderMaterialSelection();
  if (changed) { resetRandomSamplingRound(); invalidateMaterialSlot(); }
}
function addMaterialCheckbox(item, key, id, name) {
  item.dataset.materialId = id;
  item.classList.toggle('batch-selected', materialSelection[key].has(id));
  const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.className = 'material-checkbox';
  checkbox.checked = materialSelection[key].has(id);
  checkbox.setAttribute('aria-label', '加入素材范围：' + name);
  checkbox.onclick = ev => ev.stopPropagation();
  checkbox.onchange = () => {
    if (checkbox.checked) materialSelection[key].add(id); else materialSelection[key].delete(id);
    item.classList.toggle('batch-selected', checkbox.checked); saveMaterialSelection();
  };
  item.prepend(checkbox);
}
function renderMaterialSelection() {
  const current = materialSelection[currentTab];
  const field = currentTab === 'comps' ? 'components' : currentTab;
  const visible = new Set((overviewData?.[field] || []).map(row => row.id));
  const unavailable = [...current].filter(id => !visible.has(id)).length;
  document.getElementById('selection-tab-count').textContent = current.size
    ? `本板块已选 ${current.size} 项${unavailable ? `，${unavailable} 项已隐藏或不可用，请清空后重选` : ''}`
    : (currentTab === 'gameplays' ? '玩法库未勾选 · 不注入脚本' : (currentTab === 'levels' ? '关卡库未勾选 · 不注入脚本' : '本板块未选 · 随机'));
  const summary = document.getElementById('batch-selection-summary'); summary.replaceChildren();
  for (const [key, label] of Object.entries(materialLabels)) {
    const button = document.createElement('button'); button.className = 'btn-sm selection-chip';
    button.classList.toggle('has-selection', materialSelection[key].size > 0);
    button.textContent = `${label} · ${materialSelection[key].size ? '已选 ' + materialSelection[key].size : ((key === 'gameplays' || key === 'levels') ? '不注入' : '随机')}`;
    button.onclick = () => switchTab(key); summary.append(button);
  }
}
function clearMaterialSelection(key) {
  if (key) materialSelection[key].clear(); else Object.values(materialSelection).forEach(ids => ids.clear());
  saveMaterialSelection(); renderCurrentTab();
}
function selectVisibleMaterials() {
  document.querySelectorAll('#tab-content-list [data-material-id]').forEach(item => materialSelection[currentTab].add(item.dataset.materialId));
  saveMaterialSelection(); renderCurrentTab();
}
function reconcileMaterialSelection() {
  // Keep explicit IDs, including hidden ones. Backend rejects conflicts instead
  // of turning an emptied range into an unrestricted random draw.
  saveMaterialSelection(false);
}

function applyModelConfig(config) {
  modelSettings = config.model_service || {};
  const model = modelSettings.default_model || '';
  document.getElementById('reasoning-effort').value = modelSettings.reasoning_effort || 'auto';
  const select = document.getElementById('model-select');
  select.replaceChildren(new Option(model, model, true, true));
  document.getElementById('header-model-badge').textContent = model;
  document.getElementById('model-endpoint').textContent = modelSettings.api_url || '尚未配置';
  document.getElementById('model-note').textContent = modelSettings.environment_override
    ? '当前连接由环境变量覆盖，测试和生成均使用此连接。' : '与模型配置同步，可选择服务提供的其他模型。';
}

async function refreshModels() {
  try {
    const res = await fetch('/api/models');
    const data = await res.json();
    if (!res.ok || !data.ok) return;
    const select = document.getElementById('model-select');
    select.replaceChildren(...data.models.map(m => new Option(m, m, false, m === data.default_model)));
    if (data.warning) document.getElementById('model-note').textContent = data.warning;
  } catch(e) { document.getElementById('model-note').textContent = '模型列表读取失败，仍可使用配置中的默认模型。'; }
}

function setGenerationBusy(value) {
  busy = value;
  for (const id of ['btn-generate', 'btn-batch', 'btn-config']) document.getElementById(id).disabled = value;
  if (visibilityLoading || (hideUsedRivals && !visibilityReady)) {
    document.getElementById('btn-generate').disabled = true;
    document.getElementById('btn-batch').disabled = true;
  }
  if (pureRandomSampling && randomSamplingSupported !== true) {
    document.getElementById('btn-generate').disabled = true;
    document.getElementById('btn-batch').disabled = true;
  }
  document.getElementById('pure-random-sampling').disabled = value;
  if (!currentSlot) document.getElementById('btn-generate').disabled = true;
}

function generationSettings() {
  return {
    pure_random: pureRandomSampling,
    hide_used_rivals: hideUsedRivals,
    mode: currentMode,
    rewrite_mode: document.getElementById('rewrite-mode-select').value,
    enable_game_profile: document.getElementById('toggle-game-profile').checked,
    custom_instruction: document.getElementById('custom-prompt').value,
    model: document.getElementById('model-select').value,
    reasoning_effort: document.getElementById('reasoning-effort').value,
    max_duration: Number(document.getElementById('max-duration').value)
  };
}

async function startBatch() {
  if (!materialVisibilityReady() || !randomSamplingReady()) return;
  for (const [key, field] of [['rivals','rivals'],['shots','shots'],['comps','components']]) {
    const visible = new Set((overviewData[field] || []).map(row => row.id));
    if ([...materialSelection[key]].some(id => !visible.has(id))) {
      const error = document.getElementById('error-box');
      error.textContent = '已选' + materialLabels[key] + '含已隐藏或不可用素材，请清空对应板块后重新选择，或关闭隐藏开关';
      error.style.display = 'block'; return;
    }
  }
  if (busy) return;
  const count = document.getElementById('batch-count');
  if (!count.reportValidity()) return;
  setGenerationBusy(true);
  const error = document.getElementById('error-box');
  error.style.display = 'none';
  try {
    const response = await fetch('/api/batches', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({
      ...generationSettings(), count:Number(document.getElementById('batch-count').value),
      selection:batchSelectionPayload()
    })});
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.detail || data.error || '创建批次失败');
    acceptRandomSampling(data.sampling);
    localStorage.setItem('studio-batch-id', data.job_id);
    await pollBatch(data.job_id);
  } catch(e) {
    error.textContent = e.message; error.style.display = 'block'; setGenerationBusy(false);
  }
}

async function pollBatch(id) {
  clearTimeout(batchTimer);
  try {
    const response = await fetch(`/api/batches/${id}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || '批次读取失败');
    batchJob = data.job;
    renderBatch();
    const active = ['queued','running'].includes(batchJob.status);
    setGenerationBusy(active);
    if (active) batchTimer = setTimeout(() => pollBatch(id), 1500);
    else refreshDraftCount();
  } catch(e) {
    document.getElementById('batch-area').style.display = 'block';
    document.getElementById('batch-status').textContent = `连接中断：${e.message}，正在重连…`;
    if (String(e.message).includes('不存在')) { localStorage.removeItem('studio-batch-id'); setGenerationBusy(false); }
    else batchTimer = setTimeout(() => pollBatch(id), 4000);
  }
}

function renderBatch() {
  document.getElementById('batch-area').style.display = 'block';
  const items = batchJob.items;
  const succeeded = items.filter(x => x.status === 'succeeded').length;
  const failed = items.filter(x => x.status === 'failed').length;
  const active = ['queued','running'].includes(batchJob.status);
  const states = {queued:'等待开始',running:'正在生成',completed:'已完成',partial:'部分完成',failed:'生成失败',cancelled:'已停止',interrupted:'服务重启中断'};
  document.getElementById('batch-status').textContent = `${states[batchJob.status]} · ${succeeded}/${items.length} 篇成功${failed ? ` · ${failed} 篇失败` : ''}`;
  document.getElementById('batch-model').textContent = batchJob.settings.model + ' · ' + (batchJob.sampling_note || '旧批次保留原组合');
  syncBatchFold();
  document.getElementById('btn-batch-stop').hidden = !active;
  document.getElementById('btn-batch-stop').disabled = batchJob.cancel_requested;
  document.getElementById('btn-batch-stop').textContent = batchJob.cancel_requested ? '等待当前篇结束' : '停止后续生成';
  document.getElementById('btn-batch-retry').hidden = active || succeeded === items.length;
  document.getElementById('btn-batch-export').disabled = !succeeded;
  const list = document.getElementById('batch-list');
  list.replaceChildren();
  const labels = {pending:'待生成',running:'生成中',succeeded:'已存草稿',failed:'失败'};
  items.forEach(item => {
    const row = document.createElement('div'); row.className = 'batch-item';
    const text = document.createElement('div');
    const title = document.createElement('div'); title.textContent = `${item.index}. ${item.result?.title || '待生成 · 武器候选 ' + item.slot.weapon.name}`;
    const detail = document.createElement('div'); detail.textContent = item.error || (item.status === 'pending' && !active ? '未生成' : labels[item.status]) + ' · 竞对：' + (item.slot.rivals || []).map(r => r.code).join('、') + (item.result ? ' · ' + weaponAnnotation(item.result) : '');
    detail.className = item.error ? 'batch-error' : 'item-sub';
    text.append(title, detail); row.append(text);
    if (item.result) {
      const selected = activeResultCode === item.result.material_code;
      row.classList.toggle('selected', selected);
      const button = document.createElement('button'); button.className = 'btn-sm'; button.textContent = selected ? '收起' : '查看';
      button.setAttribute('aria-expanded', String(selected));
      button.onclick = async () => {
        if (selected) { collapseResult(); return; }
        if (!await preserveHumanChanges()) return;
        await loadEditableDraft(item.filename || item.result.material_code + '.json');
      };
      row.append(button);
    }
    list.append(row);
  });
}

async function batchAction(action) {
  if (!batchJob) return;
  try {
    const res = await fetch(`/api/batches/${batchJob.id}/${action}`, {method:'POST'});
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || '操作失败');
    await pollBatch(batchJob.id);
  } catch(e) { document.getElementById('batch-status').textContent = e.message; }
}

function exportBatch() {
  if (!batchJob) return;
  const scripts = batchJob.items.filter(x => x.result).map(x => `# ${x.result.title}\n\n素材编号：${x.code}\n\n${x.result.full_script}\n\n`);
  const url = URL.createObjectURL(new Blob([scripts.join('\n---\n\n')], {type:'text/markdown;charset=utf-8'}));
  const a = document.createElement('a'); a.href = url; a.download = `口播批次-${batchJob.id.slice(0,12)}.md`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function toggleTheme() {
  const theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = theme;
  localStorage.setItem('studio-theme', theme);
  document.getElementById('btn-theme').textContent = theme === 'dark' ? '浅色' : '深色';
}

document.addEventListener('keydown', e => { if (e.key === 'Escape') { closeHistory(); closeConfigModal(); } });

function syncBatchFold() {
  document.getElementById('batch-list').hidden = batchListCollapsed;
  const button = document.getElementById('btn-fold-batch');
  button.textContent = batchListCollapsed ? '展开列表' : '收起列表';
  button.setAttribute('aria-expanded', String(!batchListCollapsed));
}
function toggleBatchList() {
  batchListCollapsed = !batchListCollapsed;
  localStorage.setItem('studio-batch-folded', String(batchListCollapsed));
  syncBatchFold();
}
function collapseResult() {
  activeResultCode = null;
  document.getElementById('result-container').style.display = 'none';
  document.querySelector('.radar-box').hidden = true;
  const hint = document.getElementById('empty-hint');
  hint.replaceChildren();
  const message = document.createElement('p'); message.textContent = '当前文案已收起。'; hint.append(message);
  if (currentGenerated) {
    const button = document.createElement('button'); button.className = 'btn-sm'; button.textContent = '展开文案';
    button.onclick = () => { renderResult(currentGenerated); hint.style.display = 'none'; };
    hint.append(button);
  }
  hint.style.display = 'block';
  if (batchJob) renderBatch();
}

async function studioRequest(url, options) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok || data.ok === false) throw new Error(typeof data.detail === 'string' ? data.detail : data.error || '操作失败');
  return data;
}
function toast(message, undo) {
  document.getElementById('studio-toast')?.remove();
  const box = document.createElement('div'); box.id = 'studio-toast'; box.className = 'studio-toast'; box.setAttribute('role', 'status');
  const text = document.createElement('span'); text.textContent = message; box.append(text);
  if (undo) {
    const button = document.createElement('button'); button.className = 'btn-sm'; button.textContent = '撤销删除';
    button.onclick = async () => { button.disabled = true; try { await undo(); box.remove(); } catch(e) { toast(e.message); } }; box.append(button);
  }
  const close = document.createElement('button'); close.className = 'btn-sm'; close.textContent = '关闭'; close.onclick = () => box.remove(); box.append(close);
  document.body.append(box);
  if (!undo) setTimeout(() => box.remove(), 4000);
}

async function collectCurrentResult() {
  if (!currentGenerated) return;
  const button = document.getElementById('btn-collect-result'); button.disabled = true;
  try {
    if (!await saveHumanDraft(currentGenerated, false)) return;
    const saved = {filename:currentGenerated._filename};
    const collected = !currentGenerated._collected;
    await studioRequest('/api/drafts/' + encodeURIComponent(saved.filename), {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({collected})});
    currentGenerated._collected = collected;
    button.textContent = collected ? '已收纳 · 取消收纳' : '收纳到草稿箱';
    refreshDraftCount(); toast(collected ? '已收纳，可在草稿箱「已收纳」中查找。' : '已取消收纳，草稿仍保留。');
  } catch(e) { toast(e.message); } finally { button.disabled = false; }
}
async function openHistory() {
  document.getElementById('drawer-overlay').style.display = 'block';
  document.getElementById('history-list').textContent = '加载中…';
  try {
    historyRecords = (await studioRequest('/api/history')).records || [];
    renderHistory();
  } catch(e) { document.getElementById('history-list').textContent = '读取失败：' + e.message; }
}
function setHistoryFilter(value) { historyCollectedOnly = value; renderHistory(); }
function renderHistory() {
  const list = document.getElementById('history-list'); list.replaceChildren();
  document.getElementById('history-all').classList.toggle('active', !historyCollectedOnly);
  document.getElementById('history-collected').classList.toggle('active', historyCollectedOnly);
  const query = document.getElementById('history-search').value.trim().toLowerCase();
  const records = historyRecords.filter(r => (!historyCollectedOnly || r._collected) && [r.title, r.核心卖点, r.material_code, r.素材编号, r.full_script, r.二创文案, r.manual_script, r.人工修改稿].join(' ').toLowerCase().includes(query));
  if (!records.length) { list.textContent = query ? '没有找到匹配草稿。' : historyCollectedOnly ? '暂无收纳，点击文案上的「收纳到草稿箱」。' : '暂无草稿。'; return; }
  records.forEach(r => {
    const card = document.createElement('article'); card.className = 'history-card';
    const title = r.title || r.核心卖点 || '未命名草稿';
    const code = r.material_code || r.素材编号 || '';
    const view = document.createElement('button'); view.className = 'history-preview';
    const heading = document.createElement('strong'); heading.textContent = title;
    const meta = document.createElement('div'); meta.className = 'item-sub'; meta.textContent = `${code}${r._collected ? ' · 已收纳' : ''}`;
    const snippet = document.createElement('p'); snippet.textContent = (r.manual_script || r.人工修改稿 || r.full_script || r.二创文案 || '').slice(0, 100); view.append(heading, meta, snippet);
    view.onclick = () => loadEditableDraft(r._filename);
    const actions = document.createElement('div'); actions.className = 'history-actions';
    const collect = document.createElement('button'); collect.className = 'btn-sm'; collect.textContent = r._collected ? '取消收纳' : '收纳';
    collect.onclick = async () => {
      collect.disabled = true;
      try {
        const data = await studioRequest('/api/drafts/' + encodeURIComponent(r._filename), {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({collected:!r._collected})});
        r._collected = data.collected;
        if (currentGenerated?.material_code === code) { currentGenerated._collected = data.collected; document.getElementById('btn-collect-result').textContent = data.collected ? '已收纳 · 取消收纳' : '收纳到草稿箱'; }
        renderHistory();
      } catch(e) { toast(e.message); collect.disabled = false; }
    };
    const write = document.createElement('button'); write.className = 'btn-sm'; write.textContent = '预览并写入飞书'; write.onclick = ev => writebackDraft(r._filename, ev);
    const remove = document.createElement('button'); remove.className = 'btn-sm danger-text'; remove.textContent = '删除';
    remove.onclick = async () => {
      remove.disabled = true;
      try {
        const data = await studioRequest('/api/drafts/' + encodeURIComponent(r._filename), {method:'DELETE'});
        historyRecords = historyRecords.filter(x => x._filename !== r._filename); renderHistory(); refreshDraftCount();
        toast('草稿已删除。', async () => { await studioRequest('/api/drafts/restore/' + data.undo_token, {method:'POST'}); await openHistory(); refreshDraftCount(); });
      } catch(e) { toast(e.message); remove.disabled = false; }
    };
    const edit = document.createElement('button'); edit.className = 'btn-sm'; edit.textContent = '编辑人工稿'; edit.onclick = () => loadEditableDraft(r._filename);
    const refine = document.createElement('button'); refine.className = 'btn-sm'; refine.textContent = '分镜精修'; refine.onclick = () => loadEditableDraft(r._filename, true);
    const revise = document.createElement('button'); revise.className = 'btn-sm'; revise.textContent = 'AI 改写'; revise.onclick = () => loadRevisionSource('draft', r._filename);
    actions.append(edit, refine, revise, collect, write, remove); card.append(view, actions); list.append(card);
  });
}

let pendingWriteback = null;
let writingFeishu = false;
async function openWritebackPreview(url, payload) {
  if (writingFeishu) return;
  try {
    const response = await studioRequest(url + '-preview', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
    const plan = response.plan;
    const body = document.getElementById('writeback-preview-body'); body.replaceChildren();
    const note = document.createElement('p'); note.className = 'batch-note';
    note.textContent = `1 条主记录 + ${plan.shot_count} 条分镜 · 分镜合计 ${plan.total_duration} 秒。同编号记录会先核对并补齐，完成后读回核验。${response.recovered ? ' 已从原批次恢复完整分镜。' : ''}`;
    body.append(note);
    const renderFields = (title, fields) => {
      const section = document.createElement('details'); section.open = true;
      const heading = document.createElement('summary'); heading.textContent = title; section.append(heading);
      const table = document.createElement('table');
      for (const [name,value] of Object.entries(fields)) {
        const row = document.createElement('tr'), label = document.createElement('th'), cell = document.createElement('td');
        label.textContent = name;
        cell.textContent = Array.isArray(value) ? value.map(v => typeof v === 'object' ? v.id : v).join('、') : String(value ?? '');
        row.append(label,cell); table.append(row);
      }
      section.append(table); body.append(section);
    };
    renderFields('主记录 · ' + plan.parent['素材编号'], plan.parent);
    for (const child of plan.children) renderFields(`第 ${child['分镜序号']} 镜 · ${child['素材编号']}`, {...child, '父记录':'关联上面的主记录'});
    pendingWriteback = {url, payload:JSON.parse(JSON.stringify(payload)), shotCount:plan.shot_count};
    const button = document.getElementById('confirm-writeback'); button.disabled = false; button.textContent = '写入并核验';
    document.getElementById('writeback-preview-status').textContent = '';
    document.getElementById('writeback-preview').showModal();
  } catch(e) { toast(e.message); }
}

async function confirmWriteback() {
  if (!pendingWriteback || writingFeishu) return;
  const request = pendingWriteback;
  writingFeishu = true;
  const button = document.getElementById('confirm-writeback'); button.disabled = true; button.textContent = '正在写入并核验…';
  const status = document.getElementById('writeback-status'); status.textContent = '正在写入主记录和分镜并读回核验…';
  const previewStatus = document.getElementById('writeback-preview-status'); previewStatus.textContent = status.textContent;
  try {
    const result = await studioRequest(request.url, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(request.payload)});
    if (!result.verified || result.shot_count !== request.shotCount) throw Error('写入结果尚未完整核验，请检查远端后重试');
    const message = `已写入并核验：1 条主记录 + ${result.shot_count} 条分镜（${result.record_id}）`;
    status.textContent = message; toast(message);
    document.getElementById('writeback-preview').close(); pendingWriteback = null;
    refreshDraftCount();
  } catch(e) {
    status.textContent = e.message; previewStatus.textContent = e.message; toast(e.message);
  } finally {
    writingFeishu = false; button.disabled = false; button.textContent = '写入并核验';
  }
}

function setFontSize(value) {
  const size = Math.max(13, Math.min(20, Number(value) || 15));
  document.documentElement.style.fontSize = size + 'px';
  document.getElementById('font-size').value = size;
  document.getElementById('font-size-value').textContent = size + 'px';
  localStorage.setItem('studio-font-size', String(size));
}
let layoutRatios = null;
function applyLayout() {
  const container = document.querySelector('.container');
  if (window.innerWidth < 1100) { container.style.gridTemplateColumns = ''; return; }
  const width = container.clientWidth - 14;
  const left = Math.max(220, Math.min(width - 660, (layoutRatios?.[0] || .2) * width));
  const middle = Math.max(320, Math.min(width - left - 340, (layoutRatios?.[1] || .4) * width));
  container.style.gridTemplateColumns = `${left}px 7px ${middle}px 7px minmax(0,1fr)`;
}
function resetLayout() { layoutRatios = null; localStorage.removeItem('studio-layout'); setFontSize(15); applyLayout(); }
function initLayout() {
  try { const saved = JSON.parse(localStorage.getItem('studio-layout')); if (Array.isArray(saved) && saved.length === 2 && saved.every(x => Number.isFinite(x) && x > 0 && x < 1)) layoutRatios = saved; } catch(e) {}
  const container = document.querySelector('.container');
  const panels = [...container.children];
  panels.forEach((p, i) => p.id = ['library-panel','creator-panel','output-panel'][i]);
  for (let i = 0; i < 2; i++) {
    const splitter = document.createElement('div'); splitter.className = 'splitter'; splitter.tabIndex = 0;
    splitter.setAttribute('role', 'separator'); splitter.setAttribute('aria-orientation', 'vertical'); splitter.setAttribute('aria-label', i ? '调整创作台与结果栏宽度' : '调整素材库与创作台宽度');
    container.insertBefore(splitter, panels[i + 1]);
    const change = (delta, left, middle) => {
      const width = container.clientWidth - 14;
      if (i === 0) { const next = Math.max(220, Math.min(left + middle - 320, left + delta)); middle += left - next; left = next; }
      else middle = Math.max(320, Math.min(width - left - 340, middle + delta));
      layoutRatios = [left / width, middle / width]; applyLayout();
    };
    splitter.addEventListener('pointerdown', e => {
      if (e.button !== 0) return;
      e.preventDefault(); splitter.setPointerCapture(e.pointerId);
      const start = e.clientX, left = panels[0].getBoundingClientRect().width, middle = panels[1].getBoundingClientRect().width;
      document.body.classList.add('resizing');
      const move = ev => change(ev.clientX - start, left, middle);
      const finish = () => { splitter.removeEventListener('pointermove', move); document.body.classList.remove('resizing'); localStorage.setItem('studio-layout', JSON.stringify(layoutRatios)); };
      splitter.addEventListener('pointermove', move); splitter.addEventListener('lostpointercapture', finish, {once:true});
    });
    splitter.addEventListener('keydown', e => { if (['ArrowLeft','ArrowRight'].includes(e.key)) { e.preventDefault(); change(e.key === 'ArrowLeft' ? -20 : 20, panels[0].clientWidth, panels[1].clientWidth); localStorage.setItem('studio-layout', JSON.stringify(layoutRatios)); } });
    splitter.addEventListener('dblclick', resetLayout);
  }
  setFontSize(localStorage.getItem('studio-font-size') || 15); applyLayout();
  window.addEventListener('resize', applyLayout);
}
document.addEventListener('DOMContentLoaded', initLayout);
