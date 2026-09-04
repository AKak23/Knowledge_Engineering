(() => {
  'use strict';

  const SCOPE_META = {
    objects:{label:'实体对象',description:'补充对象定义、属性、实例和来源证据'},
    relations:{label:'关系',description:'补充对象之间的关系、基数和证据'},
    rules:{label:'规则与指标',description:'补充判定条件、指标口径和逻辑'},
    actions:{label:'行动与下游影响',description:'识别行动依据、任务引用和反馈策略'}
  };
  const KIND_LABELS = {object:'对象',relation:'关系',rule:'规则',action:'行动'};
  const STEPS = [
    ['补充更新资料','确认文档与更新范围'],
    ['确认变更方案','逐项查看节点与关系影响'],
    ['应用到当前本体','预览后生成新版本']
  ];

  const dom = {
    trigger:document.getElementById('ontologySourcesButton'),
    modal:document.getElementById('ontologyUpdateModal'),
    close:document.getElementById('ontologyUpdateClose'),
    title:document.getElementById('ontologyUpdateTitle'),
    subtitle:document.getElementById('ontologyUpdateSubtitle'),
    steps:document.getElementById('ontologyUpdateSteps'),
    body:document.getElementById('ontologyUpdateBody'),
    footerHint:document.getElementById('ontologyUpdateFooterHint'),
    secondary:document.getElementById('ontologyUpdateSecondary'),
    primary:document.getElementById('ontologyUpdatePrimary'),
    previewBar:document.getElementById('ontologyUpdatePreviewBar'),
    previewSummary:document.getElementById('ontologyUpdatePreviewSummary'),
    previewBack:document.getElementById('ontologyUpdatePreviewBack'),
    confirm:document.getElementById('ontologyUpdateConfirm')
  };
  if (!dom.trigger || !dom.modal) return;

  const updateState = {
    baseJobId:null,
    sourceData:null,
    files:[],
    builtinIds:new Set(),
    scopes:new Set(Object.keys(SCOPE_META)),
    updateId:null,
    plan:null,
    selected:new Set(),
    previewApplied:[],
    busy:false,
    analysisToken:0
  };

  function escapeHtml(value){
    return String(value ?? '').replace(/[&<>"]/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[character]));
  }

  function formatSize(bytes){
    const value = Number(bytes);
    if (!Number.isFinite(value)) return '已登记';
    if (value < 1024) return `${value} B`;
    if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
    return `${(value / 1024 / 1024).toFixed(1)} MB`;
  }

  async function errorMessage(response){
    try{
      const payload = await response.json();
      if (typeof payload.detail === 'string') return payload.detail;
      if (payload.detail?.message) return payload.detail.message;
      return JSON.stringify(payload.detail || payload);
    }catch(_error){
      return `${response.status} ${response.statusText}`;
    }
  }

  function syncTrigger(){
    const ready = Boolean(currentJobId && graphReady && !isMockJob(currentJobId) && !isComparePage() && dom.previewBar.hidden);
    dom.trigger.disabled = !ready;
    dom.trigger.title = ready ? '查看当前业务文档并追加文档更新本体' : '请先加载或构建一个后端本体';
  }

  function renderSteps(activeIndex){
    dom.steps.innerHTML = STEPS.map((step,index) => `<div class="ontologyUpdateStep${index === activeIndex ? ' active' : ''}${index < activeIndex ? ' done' : ''}">
      <i>${index < activeIndex ? '✓' : index + 1}</i><div><strong>${step[0]}</strong><small>${step[1]}</small></div>
    </div>`).join('');
  }

  function setBusy(busy){
    updateState.busy = Boolean(busy);
    dom.close.disabled = updateState.busy;
    dom.secondary.disabled = updateState.busy;
    dom.primary.disabled = updateState.busy || dom.primary.disabled;
  }

  function resetForJob(jobId){
    updateState.baseJobId = jobId;
    updateState.sourceData = null;
    updateState.files = [];
    updateState.builtinIds = new Set();
    updateState.scopes = new Set(Object.keys(SCOPE_META));
    updateState.updateId = null;
    updateState.plan = null;
    updateState.selected = new Set();
    updateState.previewApplied = [];
    updateState.busy = false;
    updateState.analysisToken += 1;
  }

  async function openSources(){
    syncTrigger();
    if (dom.trigger.disabled) return;
    const jobId = currentJobId;
    resetForJob(jobId);
    dom.modal.hidden = false;
    dom.title.textContent = '本体数据源';
    dom.subtitle.textContent = '选择当前本体使用的业务文档与补充数据源。';
    renderSteps(0);
    renderLoading('读取当前本体数据源','正在获取已用文档与可选的内置业务材料…',8);
    dom.secondary.hidden = true;
    dom.primary.textContent = '分析更新影响';
    dom.primary.disabled = true;
    dom.footerHint.textContent = '请选择至少一份补充文档。';
    try{
      const response = await fetch(apiUrl(`/ontology/jobs/${jobId}/sources`));
      if (!response.ok) throw new Error(await errorMessage(response));
      if (jobId !== updateState.baseJobId) return;
      updateState.sourceData = await response.json();
      renderSourceStep();
    }catch(error){
      renderError(`数据源读取失败：${error.message || error}`);
    }
  }

  function currentDocumentHtml(document,index){
    const suffix = String(document.name || '').split('.').pop().slice(0,5).toUpperCase() || 'DOC';
    return `<div class="ontologyUpdateDoc">
      <span aria-hidden="true">✓</span><span class="ontologyUpdateDocType">${escapeHtml(suffix)}</span>
      <div><strong>${escapeHtml(document.name || `业务文档 ${index + 1}`)}</strong><small>当前版本已使用的业务文档</small></div>
      <em>已使用</em>
    </div>`;
  }

  function builtinDocumentHtml(document){
    const selected = updateState.builtinIds.has(String(document.documentId));
    return `<label class="ontologyUpdateDoc selectable${selected ? ' selected' : ''}">
      <input type="checkbox" data-builtin-document="${escapeHtml(document.documentId)}"${selected ? ' checked' : ''}${document.available === false ? ' disabled' : ''}>
      <span class="ontologyUpdateDocType">${escapeHtml(document.fileType || 'TXT')}</span>
      <div><strong>${escapeHtml(document.name)}</strong><small>${escapeHtml(document.description || '内置业务文档')}</small></div>
      <em>${selected ? '已选择' : (document.available === false ? '不可用' : '可选择')}</em>
    </label>`;
  }

  function uploadedDocumentHtml(file,index){
    const suffix = String(file.name || '').split('.').pop().slice(0,5).toUpperCase() || 'DOC';
    return `<div class="ontologyUpdateDoc selected">
      <span aria-hidden="true">✓</span><span class="ontologyUpdateDocType">${escapeHtml(suffix)}</span>
      <div><strong>${escapeHtml(file.name)}</strong><small>用户追加文档 · ${formatSize(file.size)}</small></div>
      <button type="button" data-remove-upload="${index}" aria-label="移除 ${escapeHtml(file.name)}">×</button>
    </div>`;
  }

  function renderSourceStep(){
    const data = updateState.sourceData || {ontology:{},documents:[],builtinDocuments:[]};
    const ontology = data.ontology || {};
    const currentDocs = data.documents || [];
    const builtinDocs = data.builtinDocuments || [];
    dom.title.textContent = '本体数据源';
    dom.subtitle.textContent = '选择当前本体使用的业务文档与补充数据源。';
    renderSteps(0);
    dom.body.innerHTML = `<section class="ontologyUpdateSection">
      <div class="ontologyUpdateCurrent"><div><span>当前本体</span><strong>${escapeHtml(ontology.ontologyName || '未命名本体')}</strong><code>${escapeHtml(updateState.baseJobId)}</code></div><span class="ontologyUpdateReadonlyBadge">预览阶段只读</span></div>
    </section>
    <section class="ontologyUpdateSection">
      <div class="ontologyUpdateSectionHead"><span class="ontologyUpdateSectionIndex">01</span><div><h3>业务文档</h3><p>已有文档用于说明当前版本；可在右侧上传本地文件，或从下方选择内置材料。</p></div><div class="ontologyUpdateSectionActions"><span class="ontologyUpdateRequired">必选</span><label class="ontologyUpdateHeaderUpload">＋ 上传更多文档<input id="ontologyUpdateFileInput" type="file" accept=".txt,.md,.markdown,.pdf,.docx,.log,.json,.jsonl" multiple hidden></label></div></div>
      <div class="ontologyUpdateDocGroup"><div class="ontologyUpdateDocGroupHead"><strong>当前版本文档</strong><span>${currentDocs.length} 份 · 只读</span></div><div class="ontologyUpdateDocs">${currentDocs.length ? currentDocs.map(currentDocumentHtml).join('') : '<div class="ontologyUpdateEmpty">当前版本未保留原文档名称</div>'}</div></div>
      ${updateState.files.length ? `<div class="ontologyUpdateDocGroup added"><div class="ontologyUpdateDocGroupHead"><strong>本次上传</strong><span>${updateState.files.length} 份 · 待分析</span></div><div class="ontologyUpdateDocs">${updateState.files.map(uploadedDocumentHtml).join('')}</div></div>` : ''}
      ${builtinDocs.length ? `<div class="ontologyUpdateDocGroup selectable"><div class="ontologyUpdateDocGroupHead"><strong>可选内置文档</strong><span>选择后作为本次补充资料</span></div><div class="ontologyUpdateDocs">${builtinDocs.map(builtinDocumentHtml).join('')}</div></div>` : ''}
    </section>
    <section class="ontologyUpdateSection">
      <div class="ontologyUpdateSectionHead"><span class="ontologyUpdateSectionIndex">02</span><div><h3>选择本体更新范围</h3><p>后端将以该范围约束变更方案；未选类型不会进入预览或新版本。</p></div><span class="ontologyUpdateRequired">可多选</span></div>
      <div class="ontologyUpdateScopeGrid">${Object.entries(SCOPE_META).map(([key,item]) => `<label class="ontologyUpdateScope"><input type="checkbox" data-update-scope="${key}"${updateState.scopes.has(key) ? ' checked' : ''}><span><strong>${item.label}</strong><small>${item.description}</small></span></label>`).join('')}</div>
      <div class="ontologyUpdateSafety"><span aria-hidden="true">◇</span><div><strong>更新安全边界</strong><br>不会自动删除已有节点，也不会直接覆盖已有值；预览确认后才会生成新的本体版本。</div></div>
    </section>`;
    dom.secondary.hidden = true;
    dom.primary.textContent = '分析更新影响';
    bindSourceStep();
    syncSourceFooter();
  }

  function bindSourceStep(){
    const input = document.getElementById('ontologyUpdateFileInput');
    input?.addEventListener('change',() => addFiles(input.files));
    dom.body.querySelectorAll('[data-builtin-document]').forEach(inputElement => {
      inputElement.addEventListener('change',() => {
        const id = String(inputElement.dataset.builtinDocument);
        if (inputElement.checked) updateState.builtinIds.add(id);
        else updateState.builtinIds.delete(id);
        renderSourceStep();
      });
    });
    dom.body.querySelectorAll('[data-update-scope]').forEach(inputElement => {
      inputElement.addEventListener('change',() => {
        const key = String(inputElement.dataset.updateScope);
        if (inputElement.checked) updateState.scopes.add(key);
        else updateState.scopes.delete(key);
        syncSourceFooter();
      });
    });
    dom.body.querySelectorAll('[data-remove-upload]').forEach(button => {
      button.addEventListener('click',() => {
        updateState.files.splice(Number(button.dataset.removeUpload),1);
        renderSourceStep();
      });
    });
  }

  function addFiles(fileList){
    const allowed = new Set(['txt','md','markdown','pdf','docx','log','json','jsonl']);
    [...(fileList || [])].forEach(file => {
      const suffix = String(file.name || '').split('.').pop().toLowerCase();
      if (!allowed.has(suffix)) return;
      const duplicate = updateState.files.some(item => item.name === file.name && item.size === file.size && item.lastModified === file.lastModified);
      if (!duplicate) updateState.files.push(file);
    });
    renderSourceStep();
  }

  function syncSourceFooter(){
    const documentCount = updateState.files.length + updateState.builtinIds.size;
    const scopeCount = updateState.scopes.size;
    dom.primary.disabled = updateState.busy || !documentCount || !scopeCount;
    dom.footerHint.textContent = !documentCount
      ? '请选择至少一份补充文档。'
      : (!scopeCount ? '请选择至少一项本体更新范围。' : `已选 ${documentCount} 份补充文档 · ${scopeCount} 项更新范围`);
  }

  function renderLoading(title,description,percent=5,stage=''){
    dom.body.innerHTML = `<div class="ontologyUpdateLoading"><div><div class="ontologyUpdateLoadingRing"></div><h3>${escapeHtml(title)}</h3><p>${escapeHtml(description)}</p><div class="ontologyUpdateProgress"><i style="width:${Math.max(4,Math.min(100,Number(percent) || 0))}%"></i></div><p id="ontologyUpdateLoadingStage">${escapeHtml(stage)}</p></div></div>`;
  }

  function renderError(message){
    dom.body.innerHTML = `<div class="ontologyUpdateError">${escapeHtml(message)}</div>`;
    dom.footerHint.textContent = '本次更新未修改当前本体。';
    dom.secondary.hidden = false;
    dom.secondary.textContent = '返回资料选择';
    dom.primary.disabled = true;
    setBusy(false);
  }

  async function analyzeUpdate(){
    if (dom.primary.disabled || updateState.busy) return;
    const token = ++updateState.analysisToken;
    setBusy(true);
    renderSteps(0);
    dom.secondary.hidden = true;
    dom.primary.disabled = true;
    dom.footerHint.textContent = '文档拆分与影响分析进行中，当前画布保持不变。';
    renderLoading('正在拆分补充文档','后端会按所选范围抽取候选对象、关系、规则与行动，并与当前本体比对。',6,'创建更新分析任务');
    try{
      const form = new FormData();
      updateState.files.forEach(file => form.append('files',file,file.name));
      form.append('builtin_document_ids',JSON.stringify([...updateState.builtinIds]));
      form.append('scopes',JSON.stringify([...updateState.scopes]));
      form.append('backend',els.backendSelect?.value || '');
      form.append('use_embedding',String(Boolean(buildOptions?.semanticClustering)));
      const response = await fetch(apiUrl(`/ontology/jobs/${updateState.baseJobId}/updates`),{method:'POST',body:form});
      if (!response.ok) throw new Error(await errorMessage(response));
      const created = await response.json();
      updateState.updateId = created.updateId;
      const plan = await pollAnalysis(created.updateId,token);
      if (token !== updateState.analysisToken) return;
      updateState.plan = plan;
      updateState.selected = new Set((plan.changes || []).filter(item => item.selectedByDefault !== false).map(item => String(item.changeId)));
      setBusy(false);
      renderPlanStep();
    }catch(error){
      if (token !== updateState.analysisToken) return;
      renderError(`更新影响分析失败：${error.message || error}`);
    }
  }

  async function pollAnalysis(updateId,token){
    while (token === updateState.analysisToken){
      const response = await fetch(apiUrl(`/ontology/jobs/${updateId}/status`));
      if (!response.ok) throw new Error(await errorMessage(response));
      const status = await response.json();
      const percent = Number(status.progress?.overallPercent ?? status.progress?.percent ?? 0);
      const progress = dom.body.querySelector('.ontologyUpdateProgress>i');
      const stage = document.getElementById('ontologyUpdateLoadingStage');
      if (progress) progress.style.width = `${Math.max(4,Math.min(100,percent))}%`;
      if (stage) stage.textContent = [status.stage,status.stageHint].filter(Boolean).join(' · ');
      if (status.status === 'completed'){
        const planResponse = await fetch(apiUrl(`/ontology/jobs/${updateState.baseJobId}/updates/${updateId}/plan`));
        if (!planResponse.ok) throw new Error(await errorMessage(planResponse));
        return (await planResponse.json()).plan;
      }
      if (status.status === 'failed') throw new Error(status.error || '后端更新分析失败');
      await new Promise(resolve => window.setTimeout(resolve,1200));
    }
    throw new Error('更新分析已取消');
  }

  function renderPlanStep(){
    const plan = updateState.plan || {counts:{},changes:[]};
    const counts = plan.counts || {};
    dom.title.textContent = '确认本体变更方案';
    dom.subtitle.textContent = '勾选要应用的节点级更新；依赖项会随后端安全校验一起应用。';
    renderSteps(1);
    dom.body.innerHTML = `<div class="ontologyUpdateStats">
      ${statHtml('新增项',counts.added || 0)}${statHtml('修改项',counts.modified || 0)}${statHtml('关系 / 规则',(counts.relations || 0) + (counts.rules || 0))}${statHtml('行动',counts.actions || 0)}
    </div>
    <div class="ontologyUpdateSafety"><span aria-hidden="true">◇</span><div><strong>治理影响：当前本体保持原版本</strong><br>本次变更仅会在确认后生成新版本；不删除已有节点，不直接覆盖已有值。</div></div>
    <div class="ontologyUpdatePlanHead"><div><h3>节点级变更清单</h3><p>可以取消无需进入新版本的候选项。</p></div><button class="ontologyUpdateInlineButton" id="ontologyUpdateSelectAll" type="button">全选</button><button class="ontologyUpdateInlineButton" id="ontologyUpdateSelectNone" type="button">取消全选</button></div>
    <div class="ontologyUpdateChangeList">${(plan.changes || []).length ? plan.changes.map(changeHtml).join('') : '<div class="ontologyUpdateEmpty">所选文档与更新范围中没有发现可应用的新增或修改。<br>可返回更换文档或调整范围后重新分析。</div>'}</div>`;
    dom.secondary.hidden = false;
    dom.secondary.textContent = '返回资料选择';
    dom.primary.textContent = '应用已选更新';
    dom.body.querySelectorAll('[data-change-id]').forEach(input => input.addEventListener('change',() => updateSelection(String(input.dataset.changeId),input.checked)));
    document.getElementById('ontologyUpdateSelectAll')?.addEventListener('click',() => {
      updateState.selected = new Set((plan.changes || []).map(item => String(item.changeId)));
      renderPlanStep();
    });
    document.getElementById('ontologyUpdateSelectNone')?.addEventListener('click',() => {
      updateState.selected = new Set();
      renderPlanStep();
    });
    syncPlanFooter();
  }

  function statHtml(label,value){return `<div class="ontologyUpdateStat"><span>${escapeHtml(label)}</span><strong>${Number(value) || 0}</strong></div>`;}

  function changeHtml(change){
    const selected = updateState.selected.has(String(change.changeId));
    const sources = (change.sourceChunks || []).slice(0,4).join('、');
    const dependencies = (change.dependsOn || []).length ? ` · ${(change.dependsOn || []).length} 个依赖项` : '';
    const beforeText = change.before ? existingChangeSummary(change) : '当前本体中不存在该元素';
    return `<label class="ontologyUpdateChange${selected ? ' selected' : ''}">
      <input type="checkbox" data-change-id="${escapeHtml(change.changeId)}"${selected ? ' checked' : ''}>
      <div><div class="ontologyUpdateChangeTitle"><span class="ontologyUpdateChip ${escapeHtml(change.operation)}">${change.operation === 'added' ? '新增' : '修改'}</span><span class="ontologyUpdateChip">${escapeHtml(KIND_LABELS[change.kind] || change.kind)}</span><strong>${escapeHtml(change.title)}</strong></div><div class="ontologyUpdateChangeDiff"><span><b>更新前</b>${escapeHtml(beforeText)}</span><i>→</i><span><b>确认后</b>${escapeHtml(change.summary || '补充文档证据')}</span></div><small>${sources ? `来源 ${escapeHtml(sources)}` : '来源见补充文档'}${dependencies}</small></div>
      <span class="ontologyUpdateChangeArrow" aria-hidden="true">→</span>
    </label>`;
  }

  function existingChangeSummary(change){
    const before = change.before || {};
    if (change.kind === 'object') return `保留已有 ${Number(before.attributes?.length) || 0} 个属性与实例引用`;
    if (change.kind === 'rule') return `保留已有 ${Number(before.conditions?.length) || 0} 个判定条件`;
    if (change.kind === 'action') return '保留已有执行器、行动计划与人工配置';
    return '保留已有关系定义与来源证据';
  }

  function updateSelection(changeId,checked){
    const changes = updateState.plan?.changes || [];
    const byId = Object.fromEntries(changes.map(item => [String(item.changeId),item]));
    if (checked){
      const addWithDependencies = id => {
        if (updateState.selected.has(id)) return;
        updateState.selected.add(id);
        (byId[id]?.dependsOn || []).forEach(value => addWithDependencies(String(value)));
      };
      addWithDependencies(changeId);
    }else{
      updateState.selected.delete(changeId);
      let changed = true;
      while (changed){
        changed = false;
        [...updateState.selected].forEach(id => {
          if ((byId[id]?.dependsOn || []).some(value => !updateState.selected.has(String(value)))){
            updateState.selected.delete(id);
            changed = true;
          }
        });
      }
    }
    renderPlanStep();
  }

  function syncPlanFooter(){
    const count = updateState.selected.size;
    dom.primary.disabled = updateState.busy || !count;
    dom.footerHint.textContent = count ? `已选 ${count} 项更新；下一步仅生成画布预览。` : '请至少选择一项变更。';
  }

  async function applyPreview(){
    if (!updateState.selected.size || updateState.busy) return;
    setBusy(true);
    dom.primary.disabled = true;
    dom.secondary.disabled = true;
    dom.footerHint.textContent = '正在生成合并预览…';
    try{
      const response = await fetch(apiUrl(`/ontology/jobs/${updateState.baseJobId}/updates/${updateState.updateId}/preview`),{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({changeIds:[...updateState.selected]})
      });
      if (!response.ok) throw new Error(await errorMessage(response));
      const payload = await response.json();
      updateState.previewApplied = payload.appliedChangeIds || [...updateState.selected];
      updateState.selected = new Set(updateState.previewApplied);
      currentGraphView = 'schema';
      renderGraph(payload.graph);
      window.dispatchEvent(new CustomEvent('lom:ontology-update-preview',{detail:{data:payload.graph,baseJobId:updateState.baseJobId,updateId:updateState.updateId}}));
      dom.modal.hidden = true;
      dom.previewSummary.textContent = `已预览 ${updateState.previewApplied.length} 项变更；确认后将生成新本体版本。`;
      dom.previewBar.hidden = false;
      syncTrigger();
      setBusy(false);
      renderSteps(2);
    }catch(error){
      renderError(`画布预览生成失败：${error.message || error}`);
    }
  }

  async function restoreBaseGraph({showPlan=true}={}){
    dom.previewBar.hidden = true;
    syncTrigger();
    try{
      const graph = await graphDataForJob(updateState.baseJobId,'schema');
      currentGraphView = 'schema';
      renderGraph(graph);
      window.dispatchEvent(new CustomEvent('lom:ontology-update-preview-cancel',{detail:{data:graph,jobId:updateState.baseJobId}}));
    }catch(error){
      setStatus('恢复原本体预览失败','bad');
    }
    if (showPlan){
      dom.modal.hidden = false;
      renderPlanStep();
    }
  }

  async function confirmUpdate(){
    if (updateState.busy || !updateState.previewApplied.length) return;
    setBusy(true);
    dom.confirm.disabled = true;
    dom.previewBack.disabled = true;
    dom.previewSummary.textContent = '正在生成新本体版本…';
    try{
      const response = await fetch(apiUrl(`/ontology/jobs/${updateState.baseJobId}/updates/${updateState.updateId}/confirm`),{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({changeIds:updateState.previewApplied})
      });
      if (!response.ok) throw new Error(await errorMessage(response));
      const payload = await response.json();
      dom.previewBar.hidden = true;
      window.dispatchEvent(new CustomEvent('lom:ontology-update-confirmed',{detail:{jobId:payload.jobId,baseJobId:updateState.baseJobId}}));
      await loadExistingJob(payload.jobId);
      await loadHistoryJobs(payload.jobId);
      setStatus('本体已更新 · 新版本已加载');
    }catch(error){
      dom.previewSummary.textContent = `确认失败：${error.message || error}`;
      setStatus('本体更新确认失败','bad');
    }finally{
      setBusy(false);
      dom.confirm.disabled = false;
      dom.previewBack.disabled = false;
      syncTrigger();
    }
  }

  function closeModal(){
    if (updateState.busy) return;
    updateState.analysisToken += 1;
    dom.modal.hidden = true;
  }

  dom.trigger.addEventListener('click',openSources);
  dom.close.addEventListener('click',closeModal);
  dom.modal.addEventListener('click',event => {if (event.target === dom.modal) closeModal();});
  dom.primary.addEventListener('click',() => updateState.plan ? applyPreview() : analyzeUpdate());
  dom.secondary.addEventListener('click',() => {
    if (updateState.busy) return;
    updateState.plan = null;
    updateState.updateId = null;
    updateState.selected = new Set();
    renderSourceStep();
  });
  dom.previewBack.addEventListener('click',() => restoreBaseGraph({showPlan:true}));
  dom.confirm.addEventListener('click',confirmUpdate);
  window.addEventListener('keydown',event => {
    if (event.key !== 'Escape' || updateState.busy) return;
    if (!dom.previewBar.hidden) restoreBaseGraph({showPlan:true});
    else if (!dom.modal.hidden) closeModal();
  });
  window.addEventListener('lom:graph-rendered',() => window.setTimeout(syncTrigger,0));
  window.addEventListener('lom:page-changed',syncTrigger);
  window.addEventListener('lom:ontology-build-completed',syncTrigger);
  syncTrigger();
})();
