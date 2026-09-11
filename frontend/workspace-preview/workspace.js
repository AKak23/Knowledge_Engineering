(() => {
  'use strict';
  const $ = s => document.querySelector(s);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const read = (key, fallback) => { try { return JSON.parse(localStorage.getItem(key)) || fallback; } catch { return fallback; } };
  const PREFIX = 'knovaWorkspacePreview';
  let drafts = read(PREFIX+'Drafts', {}), sessions = read(PREFIX+'Sessions', {}), page = 'create', jobs = [], files = [], query = '', activeSession = null;
  let building = false;
  const save = () => { try { localStorage.setItem(PREFIX+'Drafts', JSON.stringify(drafts)); localStorage.setItem(PREFIX+'Sessions', JSON.stringify(sessions)); } catch { notice('浏览器存储已满，请导出重要本体后释放空间。'); } };
  const date = s => s ? new Date(s).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : '尚未保存';
  document.body.insertAdjacentHTML('afterbegin', `<aside class="ws-sidebar"><div class="ws-brand"><span>◈</span> KnoVa<small>梧桐 · 本体智能平台</small></div><div class="ws-nav-label">工作空间</div><nav aria-label="工作空间导航"><button data-page="create">＋ <span>新建本体</span></button><button data-page="projects">◇ <span>我的本体管理</span></button><button data-page="files">▤ <span>文件库</span></button><button data-page="sessions">◷ <span>最近会话</span></button></nav><div class="ws-nav-label">最近打开</div><div id="wsRecent"></div><div class="ws-sidebar-footer"><span class="ws-live"></span> 独立体验版<small>从资料出发，让知识彼此连接</small><a href="/index_V0427.html" target="_blank" rel="noopener">打开原版 ↗</a></div></aside><section id="wsPage" class="ws-page" hidden></section><div id="wsNotice" class="ws-notice" role="status" hidden></div><dialog id="wsLibrary" class="ws-dialog"><header><h2>从文件库选择</h2><button data-close>×</button></header><p>选择已保存在此浏览器中的资料，用于本次构建。</p><div id="wsLibraryRows"></div><footer><button data-close>取消</button><button class="ws-primary" id="wsApply">添加到本次构建</button></footer></dialog><input id="wsFileInput" type="file" multiple accept=".txt,.md,.markdown,.pdf,.docx,.log,.json,.jsonl" hidden>`);
  const notice = text => { $('#wsNotice').textContent=text; $('#wsNotice').hidden=false; clearTimeout(notice.timer); notice.timer=setTimeout(()=>$('#wsNotice').hidden=true,4200); };
  if(location.port !== '8765') $('.ws-sidebar-footer a').href='/';
  const settingsFooter = $('.ws-sidebar-footer');
  settingsFooter.replaceChildren($('#buildPanelToggle'));
  $('#buildPanelToggle').textContent='⚙ 构建设置';
  const settingsPanel=$('.buildPanel');
  settingsPanel.classList.add('ws-settings-panel');
  document.body.appendChild(settingsPanel);
  setBuildPanelOpen(false);
  document.addEventListener('click',e=>{if(!settingsPanel.contains(e.target)&&!$('#buildPanelToggle').contains(e.target))setBuildPanelOpen(false);});
  const db = new Promise((resolve,reject) => { const req=indexedDB.open(PREFIX+'Files',1); req.onupgradeneeded=()=>req.result.createObjectStore('files',{keyPath:'id'}); req.onsuccess=()=>resolve(req.result); req.onerror=()=>reject(req.error); });
  async function dbAction(mode, callback) { const database=await db; return new Promise((resolve,reject)=>{ const tx=database.transaction('files',mode), req=callback(tx.objectStore('files')); tx.oncomplete=()=>resolve(req.result); tx.onerror=()=>reject(tx.error); tx.onabort=()=>reject(tx.error); }); }
  async function refreshFiles() { try { files=await dbAction('readonly',s=>s.getAll()); } catch { notice('文件库暂时不可用，请检查浏览器存储权限。'); } }
  async function addFiles(list) { try { for(const file of list) await dbAction('readwrite',s=>s.put({id:`${file.name}:${file.size}:${file.lastModified}`,name:file.name,size:file.size,date:new Date().toISOString(),blob:file})); await refreshFiles(); if(page==='files') render(); } catch { notice('文件保存失败，可能是浏览器空间不足。'); } }
  const size = n => n>1048576 ? (n/1048576).toFixed(1)+' MB' : Math.max(1,Math.round(n/1024))+' KB';
  function recent() { $('#wsRecent').innerHTML=Object.entries(sessions).sort((a,b)=>b[1].date.localeCompare(a[1].date)).slice(0,4).map(([id,s])=>`<button data-session="${esc(id)}"><i></i>${esc(s.name)}</button>`).join('')||'<p class="ws-muted">打开本体后显示在这里</p>'; }
  function go(next) { if(building && next==='create') { page='create'; $('#wsPage').hidden=true; $('#appRoot').classList.add('ws-background'); $('#workbenchModal').hidden=false; } else { if(building && next!=='workspace') { notice('构建正在进行，请等待完成后再切换。'); return; } page=next; window.previewWorkbench.close(); $('#wsPage').hidden=next==='workspace'||next==='create'; $('#appRoot').classList.toggle('ws-background',next!=='workspace'); if(next==='create') window.previewWorkbench.openNew(); else if(next!=='workspace') render(); }
    document.body.dataset.workspacePage=page; document.querySelectorAll('.ws-sidebar [data-page]').forEach(b=>{b.classList.toggle('active',b.dataset.page===(page==='workspace'?'projects':page)); b.setAttribute('aria-current',b.dataset.page===(page==='workspace'?'projects':page)?'page':'false');}); window.dispatchEvent(new Event('resize')); recent(); }
  function header(title, sub, action='') { return `<header class="ws-page-header"><div><span class="ws-eyebrow">KNOWLEDGE WORKSPACE</span><h1>${title}</h1><p>${sub}</p></div>${action}</header>`; }
  const search = placeholder => `<input class="ws-search" id="wsSearch" type="search" value="${esc(query)}" placeholder="${placeholder}" aria-label="${placeholder}">`;
  function render() {
    let html='';
    if(page==='projects') {
      const list=[...Object.values(drafts).map(d=>({id:d.id,name:d.name,type:'draft',count:d.nodes?.length||0,date:d.savedAt||d.updatedAt})),...jobs.filter(j=>!Object.values(drafts).some(d=>d.sourceJobId===j.jobId)).map(j=>({id:j.jobId,name:j.ontologyName||j.jobId,type:j.mock?'demo':'job',count:j.objectCount||0,date:j.updatedAt}))].filter(d=>d.name.toLowerCase().includes(query.toLowerCase()));
      html=header('我的本体管理','每一份知识积累，都从这里继续。','<button class="ws-primary" data-page="create">＋ 新建本体</button>')+`<div class="ws-list-toolbar">${search('搜索本体名称')}<span>${list.length} 个本体</span></div><div class="ws-project-grid">${list.map(d=>`<article class="ws-project"><div class="ws-project-top"><span class="ws-project-symbol">◇</span><span class="ws-badge">${d.type==='draft'?'本地工作副本':d.type==='demo'?'内置演示':'构建完成'}</span></div><h2>${esc(d.name)}</h2><p>${d.count} ${d.type==='draft'?'个元素':'个对象类型'} · ${d.type==='draft'?'浏览器保存':'本体与实例'}</p><div class="ws-project-foot"><small>${date(d.date)}</small><button data-open="${esc(d.id)}" data-type="${d.type}">打开本体 ↗</button></div></article>`).join('')||'<div class="ws-empty">没有匹配的本体。新建一份，或调整搜索条件。</div>'}</div><p class="ws-muted">${jobs.some(j=>!j.mock)?'已连接历史构建结果。':'当前展示本地工作副本与内置演示；真实历史结果需要连接 API 服务。'}</p>`;
    } else if(page==='files') {
      const list=files.filter(f=>f.name.toLowerCase().includes(query.toLowerCase()));
      html=header('文件库','把分散的业务资料，整理为可复用的知识来源。','<button class="ws-primary" id="wsUpload">＋ 添加文件</button>')+`<div class="ws-list-toolbar">${search('搜索文件名称')}<span>${files.length} 份资料 · 本浏览器保存</span></div><div class="ws-table"><div class="ws-table-head"><span>文件名称</span><span>大小</span><span>添加时间</span><span>操作</span></div>${list.map(f=>`<div class="ws-file-row"><strong>▤ &nbsp;${esc(f.name)}</strong><span>${size(f.size)}</span><span>${date(f.date)}</span><button data-download="${esc(f.id)}">下载</button></div>`).join('')||'<div class="ws-empty">暂时没有资料。添加文档后，可在新建本体时直接复用。</div>'}</div><p class="ws-muted">支持 TXT、Markdown、PDF、Word、LOG、JSON / JSONL。选择“开始构建”时，所选资料才会提交到构建服务。</p>`;
    } else {
      html=header('最近会话','回到上次的本体，继续探索问题与关系。')+`<div class="ws-session-list">${Object.entries(sessions).sort((a,b)=>b[1].date.localeCompare(a[1].date)).map(([id,s])=>`<button class="ws-session" data-session="${esc(id)}"><span class="ws-project-symbol">◷</span><div><h2>${esc(s.name)}</h2><p>${esc(s.text?.slice(-180)||'已打开本体，尚未开始问答')}</p></div><small>${date(s.date)}</small><span>↗</span></button>`).join('')||'<div class="ws-empty">打开或创建本体后，会话会显示在这里。</div>'}</div>`;
    }
    $('#wsPage').innerHTML=html;
    $('#wsSearch')?.addEventListener('input',e=>{const pos=e.target.selectionStart;query=e.target.value;render();$('#wsSearch').focus();try{$('#wsSearch').setSelectionRange(pos,pos);}catch{}});
    $('#wsUpload')?.addEventListener('click',()=>$('#wsFileInput').click());
  }
  async function refreshJobs() { try { const res=await fetch(apiUrl('/ontology/jobs')); if(!res.ok)throw Error(); const result=await res.json();jobs=historyJobsWithMock(result.jobs||[]); } catch { jobs=historyJobsWithMock([]); } if(page==='projects')render(); }
  function track() { const draft=window.previewWorkbench.snapshot(); const id=draft.id||draft.sourceJobId; if(!id)return; activeSession=id; sessions[id]={...sessions[id],name:draft.name,jobId:draft.sourceJobId,date:new Date().toISOString()}; save(); recent(); }
  async function open(id,type) {
    activeSession=null;
    currentGraphView='schema';
    if(type==='draft' && drafts[id]) { const d=drafts[id]; if(d.sourceJobId){await loadExistingJob(d.sourceJobId);if(!graphReady){notice('无法加载本体数据，请恢复 API 连接后重试。');return;}} window.previewWorkbench.restore(d); } else await loadExistingJob(id);
    if(type!=='draft'&&!graphReady){notice('本体加载失败，请检查 API 服务后重试。');return;}
    go('workspace');track();$('#msgs').replaceChildren();
    const previous=sessions[activeSession]?.text;
    if(previous){const el=document.createElement('div');el.className='ws-transcript';el.textContent='上次会话记录\n\n'+previous;$('#msgs').appendChild(el);}
  }
  function decorateCreation() {
    activeSession=null;
    page='create'; document.body.dataset.workspacePage='create'; $('#appRoot').classList.add('ws-background'); $('#wsPage').hidden=true;
    document.querySelectorAll('.ws-sidebar [data-page]').forEach(b=>b.classList.toggle('active',b.dataset.page==='create'));
    $('#buildStageLog').hidden=true;
    $('#buildStageLog').replaceChildren();
    $('#progressWrap').classList.remove('show');
    $('#jobMeta').textContent=selectedFiles.length?formatFileSelection(selectedFiles):'选择业务文档，或从已有资料开始。';
    $('#workbenchModal').classList.remove('ws-build-active');
    $('#ontologyBuildWorkflow').classList.remove('ws-building');
    for(const b of document.querySelectorAll('#workbenchModalClose,#workbenchModalCancel,#workbenchForm button[type=submit]')) b.disabled=false;
    $('#workbenchModalSubtitle').textContent='资料、构建与本体，在同一个工作台中展开。';
    const main=$('#workbenchModalBody');
    main.querySelector('[name="sourceMode"]').closest('.workbenchField').hidden=true;
    $('#workbenchModalHint').textContent='构建完成后自动进入本体工作台。也可以直接创建空白本体。';
    $('#wsPickLibrary')?.remove();
    $('#uploadBtn').insertAdjacentHTML('afterend','<button type="button" class="ws-library-btn" id="wsPickLibrary">▤ 从文件库中选择</button>');
    $('#wsPickLibrary').onclick=async()=>{await refreshFiles();$('#wsLibraryRows').innerHTML=files.map(f=>`<label class="ws-check"><input type="checkbox" value="${esc(f.id)}"><span>${esc(f.name)}</span><small>${size(f.size)}</small></label>`).join('')||'<p class="ws-empty">文件库为空，请先选择上传文件。</p>';$('#wsLibrary').showModal();};
    $('#wsSample')?.remove();
    main.insertAdjacentHTML('beforeend','<button class="ws-sample" type="button" id="wsSample"><span>✧</span><div><strong>先用一份资料试试看</strong><small>载入冷却系统业务资料，可查看内容后开始构建</small></div><span>→</span></button>');
    $('#wsSample').onclick=()=>{const sample=new File(['冷却系统运维资料\n生产线由冷却设备、水冷泵和温度传感器组成。水冷泵向冷却设备供水，温度传感器监测出水温度。\n一号生产线配置冷却设备 C01、水冷泵 P01、温度传感器 T01。C01 的额定功率为 120kW，P01 流量为每小时 30 立方米。\n当 T01 测得出水温度超过 35 摄氏度时，触发高温告警，由运维人员检查冷却设备和水泵；温度恢复至 30 摄氏度以下时关闭告警。\n运维人员张明负责一号生产线，每周检查水泵压力并记录维护结果。'], '冷却系统运维示例.txt',{type:'text/plain'});updateSelectedFiles([sample]);addFiles([sample]);main.querySelector('[name="ontologyName"]').value='冷却系统运维本体';notice('已添加示例资料，点击“开始构建本体”进行真实构建。');};


    const workflow = $('#ontologyBuildWorkflow');
    const previousCards = workflow.querySelector('.ws-creation-cards');
    const cards = document.createElement('div');
    cards.className = 'ws-creation-cards';
    cards.innerHTML = `<section class="ws-source-card ws-creation-card"><div class="ws-card-heading"><b>01</b><div><h3>文件</h3><p>准备与管理知识来源</p></div></div><div class="ws-card-content" id="wsSources"></div></section>
      <div class="ws-flow-arrow" aria-label="文件送入构建"><svg viewBox="0 0 32 24" aria-hidden="true"><path d="M1 8h17V2l13 10-13 10v-6H1z" fill="currentColor"/></svg><small>解析资料</small></div><section class="ws-build-card ws-creation-card"><div class="ws-card-heading"><b>02</b><div><h3>构建</h3><p>从资料中提取对象与关系</p></div></div><div class="ws-card-content" id="wsConstruction"><p class="ws-build-intro">选好资料后启动构建。阶段明细、处理进度与校验结果会持续显示在这里。</p></div></section>
      <div class="ws-flow-arrow" aria-label="构建生成本体"><svg viewBox="0 0 32 24" aria-hidden="true"><path d="M1 8h17V2l13 10-13 10v-6H1z" fill="currentColor"/></svg><small>生成本体</small></div><section class="ws-ontology-card ws-creation-card"><div class="ws-card-heading"><b>03</b><div><h3>本体</h3><p>直接创建，或继续已有知识</p></div></div><div class="ws-card-content" id="wsOntologyActions"><button type="button" class="ws-blank-button">＋ 创建空白本体<small>从对象、关系、规则和行动开始</small></button><div class="ws-static-ontology"><span>本体结构示意</span><svg viewBox="0 0 260 180" role="img" aria-label="设备、传感器、规则与行动的本体结构示意"><g stroke="#94afcc" stroke-width="1.6"><path d="M62 50L191 53L129 123L62 50M62 50L48 143M129 123L225 143" fill="none"/></g><g fill="#e1eeff" stroke="#5c8fc5"><circle cx="62" cy="50" r="21"/><circle cx="191" cy="53" r="21"/></g><path d="M129 103L150 140H108Z" fill="#eee4ff" stroke="#9470b6"/><path d="M225 125L242 143L225 161L208 143Z" fill="#ffebe7" stroke="#c98171"/><circle cx="48" cy="143" r="15" fill="#e1eeff" stroke="#5c8fc5"/><g fill="#344f70" font-size="11" text-anchor="middle"><text x="62" y="54">设备</text><text x="191" y="57">传感器</text><text x="129" y="132">规则</text><text x="225" y="147">行动</text><text x="48" y="147">实例</text></g></svg><p>连接对象、属性与业务关系<br>构建完成后进入可编辑画布</p></div></div></section>`;
    workflow.appendChild(cards);
    const source = cards.querySelector('#wsSources');
    const build = cards.querySelector('#wsConstruction');
    const ontology = cards.querySelector('#wsOntologyActions');
    for (const id of ['uploadBtn','fileInput','wsPickLibrary','fileListWrap','structuredConfigUploadBtn','structuredConfigInput','wsSample']) source.appendChild(document.getElementById(id));
    build.prepend(main.querySelector('[name="ontologyName"]').closest('.workbenchField'),$('#supplementalPrompt').closest('.ontologyDescriptionField'));
    for (const id of ['startBuildBtn','jobMeta','progressWrap','buildStageLog']) build.appendChild(document.getElementById(id));
    ontology.insertBefore($('#ontologyUploadBtn'),ontology.querySelector('.ws-static-ontology'));
    ontology.appendChild($('#ontologyInput'));
    ontology.querySelector('.ws-blank-button').onclick=()=>$('#workbenchForm').requestSubmit();
    previousCards?.remove();
    workflow.querySelector('.ontologyBuildWorkflowGrid')?.remove();

    // Match the reference's compact sources and alternative-creation menus.
    source.insertAdjacentHTML('afterbegin','<div class="ws-source-links"><button type="button" class="ws-text-link" id="wsAddMore">＋ 继续添加</button></div>');
    source.querySelector('.ws-source-links').appendChild($('#wsPickLibrary'));
    $('#wsPickLibrary').textContent='▤ 从文件库选择';
    $('#wsAddMore').onclick=()=>$('#uploadBtn').click();
    $('#uploadBtn').innerHTML='<span>选择文件，或拖放到这里</span><small>支持 PDF、Word、TXT、Markdown 等业务资料</small>';
    const below=document.createElement('div');below.className='ws-below-source';
    below.innerHTML='<details class="ws-more-sources"><summary>更多来源⌄</summary><div class="ws-menu-items"></div></details>';
    below.prepend($('#wsSample'));source.appendChild(below);
    $('#wsSample').innerHTML='✧ 没有准备资料？先用示例试试';
    below.querySelector('.ws-menu-items').appendChild($('#structuredConfigUploadBtn'));
    const description=$('#supplementalPrompt').closest('.ontologyDescriptionField');
    description.querySelector('span').textContent='业务说明';
    description.querySelector('span').hidden=true;
    description.insertAdjacentHTML('beforebegin','<button type="button" class="ws-text-link ws-description-toggle" aria-expanded="false" aria-controls="supplementalPrompt">＋ 补充业务说明 <small>可选</small></button>');
    const toggle=build.querySelector('.ws-description-toggle');
    $('#supplementalPrompt').hidden=true;
    $('#supplementalPrompt').placeholder='例如：关注冷却设备、传感器与故障排查规则。';
    toggle.onclick=()=>{const expanded=toggle.getAttribute('aria-expanded')!=='true';toggle.setAttribute('aria-expanded',String(expanded));$('#supplementalPrompt').hidden=!expanded;if(expanded)$('#supplementalPrompt').focus();};
    build.querySelector('.ws-build-intro')?.remove();
    const oldMethods=$('#wsOtherMethods');
    const methods=document.createElement('details');methods.id='wsOtherMethods';methods.className='ws-other-methods';
    methods.innerHTML='<summary>其他创建方式⌄</summary><div class="ws-menu-items"></div>';
    methods.querySelector('.ws-menu-items').append(ontology.querySelector('.ws-blank-button'),$('#ontologyUploadBtn'));
    methods.querySelector('.ws-blank-button').textContent='创建空白本体';
    methods.querySelector('#ontologyUploadBtn').innerHTML='<span>导入已有本体</span>';
    $('#workbenchModalClose').before(methods);oldMethods?.remove();
    $('#startBuildBtn').before(Object.assign(document.createElement('p'),{className:'ws-selection-hint',textContent:'添加资料后即可开始'}));
    build.insertAdjacentHTML('beforeend','<p class="ws-result-help">构建完成后进入可编辑画布，可查看本体与实例的二维／三维视图，并继续追加资料。</p>');
    $('#workbenchModalSubtitle').textContent='选择业务文档，构建对象与关系，在本体画布中继续完善。';
    cards.querySelector('.ws-source-card h3').textContent='文档';
    cards.querySelector('.ws-source-card .ws-card-heading p').textContent='准备本次构建使用的资料';
    cards.querySelector('.ws-build-card .ws-card-heading p').textContent='提取业务对象、关系与规则';
    cards.querySelector('.ws-ontology-card .ws-card-heading p').textContent='查看结果，继续编辑与使用';
    ontology.querySelector('.ws-static-ontology>span').hidden=true;
    ontology.querySelector('.ws-static-ontology>p').innerHTML='本体将在这里生成<br><small>完成后可查看二维／三维画布与实例</small>';
    // Focused single-column creation surface; keep all original API controls mounted.
    cards.classList.add('ws-focused-form');
    cards.querySelectorAll('.ws-card-heading,.ws-flow-arrow').forEach(el=>el.remove());
    const alternatives=document.createElement('div');alternatives.className='ws-focused-alternatives';
    alternatives.innerHTML='<span>其他创建方式</span><div></div>';
    alternatives.querySelector('div').append(methods.querySelector('.ws-blank-button'),methods.querySelector('#ontologyUploadBtn'));
    build.appendChild(alternatives);methods.remove();
    // Retain hidden import input when removing the obsolete preview region.
    build.appendChild($('#ontologyInput'));cards.querySelector('.ws-ontology-card').remove();
    const links=source.querySelector('.ws-source-links');
    links.insertAdjacentHTML('afterbegin','<span class="ws-source-label">构建资料</span>');
    $('#uploadBtn').innerHTML='<span class="ws-upload-symbol" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 16V3M7 8l5-5 5 5M4 16v5h16v-5"/></svg></span><span class="ws-upload-copy"><strong>选择文件，或拖放到这里</strong><small>支持 PDF、Word、TXT、Markdown 等业务资料</small></span>';
    $('#workbenchModalSubtitle').textContent='从业务资料开始，在画布中逐步完善。';
    const nameInput=main.querySelector('[name="ontologyName"]');
    nameInput.placeholder='选择资料后自动建议，也可以自行填写';
    nameInput.value=selectedFiles[0]?.name.replace(/\.[^.]+$/,'') || '';
    let nameEdited=false;
    nameInput.addEventListener('input',()=>{nameEdited=true;});
    nameInput.parentElement.insertAdjacentHTML('beforeend','<p class="ws-field-help">用于在工作空间中识别这份本体，之后可以修改。</p>');
    const primary=document.createElement('div');primary.className='ws-focused-primary';
    primary.append(build.querySelector('.ws-selection-hint'),$('#startBuildBtn'));
    build.insertBefore(primary,$('#jobMeta'));
    primary.after(build.querySelector('.ws-result-help'));
    const refreshSelection=()=>{if(!nameEdited)nameInput.value=selectedFiles[0]?.name.replace(/\.[^.]+$/,'') || '';primary.querySelector('.ws-selection-hint').textContent=selectedFiles.length?`已选择 ${selectedFiles.length} 份资料`:'添加资料后即可开始';};
    if(window.previewSelectionObserver)window.previewSelectionObserver.disconnect();
    window.previewSelectionObserver=new MutationObserver(refreshSelection);
    window.previewSelectionObserver.observe($('#fileList'),{childList:true});refreshSelection();


  }
  $('#startBuildBtn').addEventListener('click',()=>{building=true;$('#wsBuildStep')?.classList.add('active');$('#ontologyBuildWorkflow').classList.add('ws-building');$('#buildStageLog').scrollIntoView({behavior:'smooth',block:'nearest'});});
  window.addEventListener('preview:create-open',decorateCreation);
  window.addEventListener('preview:draft',e=>{const d=e.detail;if(d.id){drafts[d.id]=d;save();}});
  window.addEventListener('preview:ready',()=>{$('#msgs').replaceChildren();go('workspace');track();});
  window.addEventListener('lom:ontology-build-completed',()=>{building=false;go('workspace');track();refreshJobs();});
  window.addEventListener('lom:graph-rendered',e=>{if(e.detail?.data?.nodes?.length && page==='create'){building=false;window.previewWorkbench.close();go('workspace');track();}});
  new MutationObserver(()=>{
    if(page!=='create')return;
    building=uploadControlsLocked;
    $('#ontologyBuildWorkflow').classList.toggle('ws-building',building);
    $('#workbenchModal').classList.toggle('ws-build-active',building);
    $('#wsBuildStep')?.classList.toggle('active',building);
    for(const b of document.querySelectorAll('#workbenchModalClose,#workbenchModalCancel,#workbenchForm button[type=submit],#workbenchForm [name=ontologyName],.ws-blank-button,#wsSample,#wsPickLibrary'))b.disabled=building;
  }).observe($('#startBuildBtn'),{attributes:true,attributeFilter:['disabled']});
  new MutationObserver(()=>{if($('#workbenchModal').hidden&&page==='create'&&!building)go('workspace');}).observe($('#workbenchModal'),{attributes:true,attributeFilter:['hidden']});
  document.addEventListener('keydown',e=>{if(building&&e.key==='Escape'){e.preventDefault();e.stopImmediatePropagation();}},true);
  let chatTimer;new MutationObserver(()=>{clearTimeout(chatTimer);chatTimer=setTimeout(()=>{if(activeSession&&sessions[activeSession]){const text=[...$('#msgs').children].filter(n=>!n.classList.contains('ws-transcript')).map(n=>n.innerText).join('\n');if(text){sessions[activeSession].text=text;sessions[activeSession].date=new Date().toISOString();save();}}},500);}).observe($('#msgs'),{childList:true,subtree:true,characterData:true});
  $('#fileInput').addEventListener('change',e=>addFiles([...e.target.files]));
  $('#wsFileInput').onchange=e=>addFiles([...e.target.files]);
  $('#wsApply').onclick=()=>{const ids=[...$('#wsLibraryRows').querySelectorAll('input:checked')].map(i=>i.value);if(!ids.length){notice('请至少选择一份资料。');return;}updateSelectedFiles(files.filter(f=>ids.includes(f.id)).map(f=>new File([f.blob],f.name,{lastModified:f.blob.lastModified,type:f.blob.type})));$('#wsLibrary').close();};
  document.addEventListener('click',async e=>{const b=e.target.closest('button');if(!b)return;if(b.dataset.page){query='';go(b.dataset.page);if(b.dataset.page==='projects')refreshJobs();}if(b.hasAttribute('data-close'))$('#wsLibrary').close();if(b.dataset.open)open(b.dataset.open,b.dataset.type);if(b.dataset.session){const id=b.dataset.session,s=sessions[id];await open(drafts[id]?id:s.jobId,drafts[id]?'draft':'job');}if(b.dataset.download){const f=files.find(f=>f.id===b.dataset.download);const url=URL.createObjectURL(f.blob),a=document.createElement('a');a.href=url;a.download=f.name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}});
  document.addEventListener('click',e=>{for(const menu of document.querySelectorAll('.ws-more-sources,.ws-other-methods')){if(!menu.contains(e.target)||e.target.closest('button'))menu.open=false;}});
  refreshFiles();refreshJobs();go('create');
})();
