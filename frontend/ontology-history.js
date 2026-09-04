(() => {
  'use strict';

  const dom = {
    app:document.getElementById('appRoot'),
    identity:document.getElementById('ontologyIdentity'),
    name:document.getElementById('globalOntologyName'),
    version:document.getElementById('globalOntologyVersion'),
    panel:document.getElementById('ontologyVersionPanel'),
    close:document.getElementById('ontologyVersionClose'),
    summary:document.getElementById('ontologyVersionSummary'),
    list:document.getElementById('ontologyVersionList')
  };
  if (!dom.identity || !dom.panel || !dom.list) return;

  const state = {
    jobId:null,
    ontologyName:'尚未加载本体',
    hasOntology:false,
    dirty:false,
    preview:false,
    requestToken:0,
    loadedJobId:null,
    loadingJobId:null,
    history:null
  };

  function escapeHtml(value){
    return String(value ?? '').replace(/[&<>'"]/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[character]));
  }

  function formatTime(value){
    if (!value) return '时间未记录';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '时间未记录';
    return new Intl.DateTimeFormat('zh-CN',{
      month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false
    }).format(date).replace('/', '-');
  }

  function setPanel(open){
    const nextOpen = Boolean(open) && dom.app?.dataset.page !== 'compare';
    dom.panel.hidden = !nextOpen;
    dom.identity.setAttribute('aria-expanded',String(nextOpen));
    if (nextOpen && !state.history && state.jobId) loadHistory(state.jobId);
  }

  function dispatchHighlight(nodeIds=[]){
    window.dispatchEvent(new CustomEvent('lom:ontology-version-highlight',{
      detail:{jobId:state.jobId,nodeIds:Array.isArray(nodeIds) ? nodeIds : []}
    }));
  }

  function renderUnavailable(message,detail='形成首个后端版本后，可在这里查看版本链。'){
    dom.summary.innerHTML = `<strong>${escapeHtml(state.ontologyName)}</strong><span>${escapeHtml(message)}</span>`;
    dom.list.innerHTML = `<div class="ontologyVersionEmpty"><span aria-hidden="true">◇</span><strong>${escapeHtml(message)}</strong><small>${escapeHtml(detail)}</small></div>`;
  }

  function changeSummary(version){
    if (version.changeType === 'initial') return '初始构建';
    const counts = version.changeCounts || {};
    const parts = [];
    if (counts.added) parts.push(`新增 ${counts.added}`);
    if (counts.modified) parts.push(`修改 ${counts.modified}`);
    return parts.join(' · ') || `应用 ${version.appliedChangeCount || 0} 项变更`;
  }

  function scopeSummary(scopes){
    const labels = {objects:'对象',relations:'关系 / 流程',rules:'规则',actions:'行动'};
    return (scopes || []).map(scope => labels[scope] || scope).join('、');
  }

  function renderHistory(payload){
    const versions = Array.isArray(payload?.versions) ? payload.versions : [];
    const current = versions.find(item => item.isCurrent) || versions.find(item => item.jobId === state.jobId);
    state.history = payload;
    if (!versions.length){
      renderUnavailable('尚无可用版本');
      dispatchHighlight();
      return;
    }
    dom.version.textContent = `${current?.versionLabel || `V${payload.currentVersion || versions.length}`}${state.dirty ? ' · 有本地修改' : ''}`;
    dom.summary.innerHTML = `<strong>${escapeHtml(payload.ontologyName || state.ontologyName)}</strong><span>共 ${versions.length} 个版本 · 当前 ${escapeHtml(current?.versionLabel || '')}</span>`;
    dom.list.innerHTML = versions.slice().reverse().map(version => {
      const active = version.jobId === state.jobId;
      const documents = (version.addedDocuments || []).map(item => item.name).filter(Boolean);
      const scopes = scopeSummary(version.updateScopes);
      const detail = version.changeType === 'initial'
        ? '建立首个可追溯版本'
        : (version.changeTitles?.length
          ? version.changeTitles.slice(0,2).join('、')
          : (documents.length ? `追加：${documents.join('、')}` : '已应用确认的更新方案'));
      return `<article class="ontologyVersionItem${active ? ' active' : ''}" data-version-job="${escapeHtml(version.jobId)}">
        <div class="ontologyVersionRail"><span></span></div>
        <div class="ontologyVersionCard">
          <header><div><b>${escapeHtml(version.versionLabel)}</b><strong>${version.changeType === 'initial' ? '初始本体' : '文档追加更新'}</strong></div>${active ? '<em>当前</em>' : ''}</header>
          <p>${escapeHtml(detail)}</p>
          <div class="ontologyVersionMetrics"><span>${version.nodeCount || 0} 节点</span><span>${version.relationCount || 0} 关系</span>${version.addedNodeIds?.length ? `<span class="added">＋${version.addedNodeIds.length} 新节点</span>` : ''}</div>
          <footer><span>${escapeHtml(formatTime(version.updatedAt || version.modifiedAt))}${scopes ? ` · ${escapeHtml(scopes)}` : ''}</span><b>${escapeHtml(changeSummary(version))}</b></footer>
          ${active ? '' : '<button type="button" data-version-load>查看此版本</button>'}
        </div>
      </article>`;
    }).join('');
    dispatchHighlight(current?.addedNodeIds || []);
  }

  async function loadHistory(jobId,force=false){
    if (!jobId || String(jobId).startsWith('mock-') || String(jobId).startsWith('preview:')) return;
    if (!force && state.loadingJobId === jobId) return;
    if (!force && state.loadedJobId === jobId && state.history){
      renderHistory(state.history);
      return;
    }
    const token = ++state.requestToken;
    state.loadedJobId = jobId;
    state.loadingJobId = jobId;
    state.history = null;
    dom.version.textContent = '读取版本…';
    renderUnavailable('正在读取修改历史','正在整理当前本体的版本链与节点变化。');
    try{
      const response = await fetch(apiUrl(`/ontology/jobs/${encodeURIComponent(jobId)}/versions`));
      if (!response.ok) throw new Error(await response.text());
      const payload = await response.json();
      if (token !== state.requestToken || state.jobId !== jobId) return;
      state.loadingJobId = null;
      renderHistory(payload);
    }catch(_error){
      if (token !== state.requestToken || state.jobId !== jobId) return;
      state.loadingJobId = null;
      dom.version.textContent = state.dirty ? '本地草稿 · 有修改' : '版本不可用';
      renderUnavailable('版本历史暂不可用','当前本体仍可正常编辑；请确认后端服务已更新并重新打开。');
      dispatchHighlight();
    }
  }

  function applyContext(detail={},force=false){
    const hasOntology = detail.hasOntology !== undefined
      ? Boolean(detail.hasOntology)
      : Boolean(detail.jobId || detail.ontologyName);
    const nextJobId = detail.jobId || null;
    const changedJob = String(nextJobId || '') !== String(state.jobId || '');
    state.jobId = nextJobId;
    state.ontologyName = hasOntology ? (detail.ontologyName || state.ontologyName || '未命名本体') : '尚未加载本体';
    state.hasOntology = hasOntology;
    state.dirty = Boolean(detail.dirty);
    state.preview = Boolean(detail.preview);
    dom.name.textContent = state.ontologyName;
    if (state.preview){
      state.requestToken += 1;
      state.loadingJobId = null;
      dom.version.textContent = '更新预览';
      setPanel(false);
      return;
    }
    if (!hasOntology){
      state.requestToken += 1;
      state.loadingJobId = null;
      dom.version.textContent = '未形成版本';
      state.loadedJobId = null;
      state.history = null;
      renderUnavailable('尚未加载本体');
      dispatchHighlight();
      return;
    }
    if (!nextJobId){
      state.requestToken += 1;
      state.loadingJobId = null;
      dom.version.textContent = state.dirty ? '本地草稿 · 有修改' : '本地草稿';
      state.loadedJobId = null;
      state.history = null;
      renderUnavailable('当前为本地草稿','保存到服务端版本工作区后，人工修改也会进入完整历史。');
      dispatchHighlight();
      return;
    }
    if (String(nextJobId).startsWith('mock-')){
      state.requestToken += 1;
      state.loadingJobId = null;
      dom.version.textContent = '演示版本';
      state.loadedJobId = null;
      state.history = null;
      renderUnavailable('当前为内置演示本体','演示数据不写入正式构建历史。');
      dispatchHighlight();
      return;
    }
    if (!changedJob && state.history){
      const current = state.history.versions?.find(item => item.jobId === state.jobId);
      dom.version.textContent = `${current?.versionLabel || `V${state.history.currentVersion || ''}`}${state.dirty ? ' · 有本地修改' : ''}`;
      return;
    }
    loadHistory(nextJobId,force || changedJob);
  }

  dom.identity.addEventListener('click',() => setPanel(dom.panel.hidden));
  dom.close?.addEventListener('click',event => {event.stopPropagation();setPanel(false);});
  dom.list.addEventListener('click',async event => {
    const button = event.target.closest('[data-version-load]');
    if (!button) return;
    const item = button.closest('[data-version-job]');
    const jobId = item?.dataset.versionJob;
    if (!jobId || typeof loadExistingJob !== 'function') return;
    button.disabled = true;
    button.textContent = '加载中…';
    setPanel(false);
    await loadExistingJob(jobId);
  });
  document.addEventListener('click',event => {
    if (!event.target.closest('.ontologyIdentityWrap')) setPanel(false);
  });
  window.addEventListener('keydown',event => {
    if (event.key === 'Escape' && !dom.panel.hidden) setPanel(false);
  });
  window.addEventListener('lom:ontology-context-changed',event => applyContext(event.detail || {}));
  window.addEventListener('lom:ontology-update-preview-cancel',() => {
    if (state.jobId) window.setTimeout(() => loadHistory(state.jobId,true),0);
  });
  window.addEventListener('lom:page-changed',event => {
    if (event.detail?.page === 'compare') setPanel(false);
  });

  const initialName = currentGraph?.ontology?.ontologyName || null;
  const initialJobId = currentJobId || null;
  applyContext({
    jobId:initialJobId,
    ontologyName:initialName,
    hasOntology:Boolean(initialJobId || currentGraph?.nodes?.length),
    dirty:false
  });
})();
