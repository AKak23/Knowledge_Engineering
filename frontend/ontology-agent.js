(() => {
  'use strict';

  const dom = {
    modal:document.getElementById('ontologyAgentModal'),
    body:document.getElementById('ontologyAgentBody'),
    footer:document.getElementById('ontologyAgentFooter'),
    title:document.getElementById('ontologyAgentTitle'),
    subtitle:document.getElementById('ontologyAgentSubtitle'),
    close:document.getElementById('ontologyAgentClose')
  };
  if (!dom.modal || !dom.body || !dom.footer) return;

  const state = {
    mode:'list',
    agents:[],
    catalog:[],
    jobs:[],
    preferredJobId:null,
    currentAgent:null,
    editDraft:null,
    editMembers:[],
    chatSessions:[],
    chatSession:null,
    publication:null,
    busy:false,
    error:''
  };

  const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g,char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const agentApiUrl = path => `${typeof API_BASE === 'string' ? API_BASE : ''}${path}`;
  const formatDate = value => {
    if (!value) return '尚未更新';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : new Intl.DateTimeFormat('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}).format(date);
  };
  const statusMeta = status => ({
    draft:['草稿','draft'],
    published:['已发布','published'],
    disabled:['已停用','disabled']
  }[status] || ['未知','draft']);

  async function fetchJson(path,options={}){
    const response = await fetch(agentApiUrl(path),options);
    let payload = null;
    try{payload = await response.json();}catch(_error){payload = null;}
    if (!response.ok){
      const detail = payload?.detail;
      const message = typeof detail === 'string' ? detail : (detail?.message || `请求失败（${response.status}）`);
      const error = new Error(message);
      error.status = response.status;
      error.payload = payload;
      throw error;
    }
    return payload;
  }

  function openManager(preferredJobId=null){
    state.preferredJobId = preferredJobId || (typeof currentJobId !== 'undefined' ? currentJobId : null);
    state.mode = 'list';
    state.publication = null;
    state.error = '';
    dom.modal.hidden = false;
    document.body.classList.add('ontology-agent-open');
    loadManager();
  }

  function closeManager(){
    dom.modal.hidden = true;
    document.body.classList.remove('ontology-agent-open');
    state.busy = false;
  }

  async function loadManager(){
    state.busy = true;
    renderLoading('正在同步本体智能体…');
    try{
      const [agents,catalog,jobs] = await Promise.all([
        fetchJson('/ontology/agents'),
        fetchJson('/ontology/agent-catalog'),
        fetchJson('/ontology/jobs')
      ]);
      state.agents = agents.agents || [];
      state.catalog = catalog.agents || [];
      state.jobs = (jobs.jobs || []).filter(job => job.status === 'completed');
      state.error = '';
      state.mode = 'list';
      renderList();
    }catch(error){
      state.error = error.message;
      renderError(error.message);
    }finally{
      state.busy = false;
    }
  }

  function renderLoading(message){
    setHeader('本体智能体','管理基于已构建本体的协同智能体');
    dom.body.innerHTML = `<div class="ontologyAgentLoading"><span></span><strong>${escapeHtml(message)}</strong></div>`;
    dom.footer.innerHTML = `<span>智能体配置与本体版本独立保存。</span><button type="button" data-agent-action="close">关闭</button>`;
  }

  function renderError(message){
    setHeader('本体智能体','暂时无法读取智能体服务');
    dom.body.innerHTML = `<div class="ontologyAgentEmpty error"><span>!</span><strong>智能体服务连接失败</strong><p>${escapeHtml(message)}</p><button type="button" data-agent-action="retry">重新加载</button></div>`;
    dom.footer.innerHTML = `<span>请确认 FastAPI 后端已启动。</span><button type="button" data-agent-action="close">关闭</button>`;
  }

  function renderList(){
    state.mode = 'list';
    state.currentAgent = null;
    setHeader('本体智能体',`共 ${state.agents.length} 个智能体，可开始对话、修改配置或发布调用`);
    const publishedCount = state.agents.filter(agent => agent.status === 'published').length;
    const memberCount = state.agents.reduce((sum,agent) => sum + (agent.members?.length || 0),0);
    const publication = state.publication ? publicationHtml(state.publication) : '';
    const cards = state.agents.length
      ? state.agents.map(agentCardHtml).join('')
      : `<div class="ontologyAgentEmpty"><span>✦</span><strong>还没有本体智能体</strong><p>选择一个已完成的本体，再组合官方原子 Agent 开始创建。</p></div>`;
    dom.body.innerHTML = `${publication}
      <div class="ontologyAgentStats">
        <div><span>全部智能体</span><strong>${state.agents.length}</strong></div>
        <div><span>已发布</span><strong>${publishedCount}</strong></div>
        <div><span>协同成员</span><strong>${memberCount}</strong></div>
      </div>
      <section class="ontologyAgentList">${cards}</section>`;
    dom.footer.innerHTML = `<span>对话会始终使用智能体绑定的本体版本。</span>
      <button type="button" data-agent-action="close">关闭</button>
      <button class="primary" type="button" data-agent-action="new"${state.jobs.length ? '' : ' disabled'}>＋ 新建智能体</button>`;
  }

  function agentCardHtml(agent){
    const [statusLabel,statusClass] = statusMeta(agent.status);
    const context = agent.ontologyContext || {};
    const memberNames = (agent.members || []).map(member => state.catalog.find(item => item.agentId === member.agentId)?.name || member.agentId);
    return `<article class="ontologyAgentCard" data-agent-id="${escapeHtml(agent.agentId)}">
      <div class="ontologyAgentCardIcon">✦</div>
      <div class="ontologyAgentCardContent">
        <div class="ontologyAgentCardTitle"><div><strong>${escapeHtml(agent.name)}</strong><code>${escapeHtml(agent.invokeName)}</code></div><span class="agentStatus ${statusClass}">${statusLabel}</span></div>
        <p>${escapeHtml(agent.description || '尚未补充智能体描述。')}</p>
        <div class="ontologyAgentContextLine"><span>本体上下文</span><b>${escapeHtml(context.ontologyName || '未知本体')}</b><code>${escapeHtml(context.version || '')}</code></div>
        <div class="ontologyAgentMemberPills">${memberNames.length ? memberNames.map(name => `<span>${escapeHtml(name)}</span>`).join('') : '<span>无协同成员</span>'}</div>
        <div class="ontologyAgentCardMeta"><span>协同成员 ${memberNames.length} 名</span><span>${agent.externalCallEnabled ? '已开启外部调用' : '仅平台内使用'}</span><span>更新于 ${formatDate(agent.updatedAt)}</span></div>
      </div>
      <div class="ontologyAgentCardActions">
        <button class="primary" type="button" data-agent-action="chat" data-agent-id="${escapeHtml(agent.agentId)}">开始对话</button>
        <button type="button" data-agent-action="configure" data-agent-id="${escapeHtml(agent.agentId)}">配置</button>
        <button type="button" data-agent-action="publish" data-agent-id="${escapeHtml(agent.agentId)}">${agent.status === 'published' ? '重新发布' : '发布'}</button>
      </div>
    </article>`;
  }

  function publicationHtml(publication){
    const endpoint = displayInvokeUrl(publication.invokePath);
    const credential = publication.accessKey || '';
    return `<section class="ontologyAgentCredential">
      <div><span>发布成功 · 请立即保存访问凭证</span><strong>${escapeHtml(publication.agent.name)} v${publication.agent.publishedVersion}</strong></div>
      ${endpoint ? `<label><span>调用地址</span><code>${escapeHtml(endpoint)}</code><button type="button" data-agent-action="copy" data-copy-value="${escapeHtml(endpoint)}">复制</button></label>` : '<p>当前智能体未开启外部调用，可在配置中开启后重新发布。</p>'}
      ${credential ? `<label><span>X-Agent-Key</span><code>${escapeHtml(credential)}</code><button type="button" data-agent-action="copy" data-copy-value="${escapeHtml(credential)}">复制</button></label><small>凭证仅在本次发布后展示，后端只保存哈希。</small>` : ''}
    </section>`;
  }

  function displayInvokeUrl(path){
    if (!path) return '';
    const base = typeof API_BASE === 'string' && API_BASE ? API_BASE.replace(/\/$/,'') : window.location.origin;
    return `${base}${path}`;
  }

  function openConfig(agent=null){
    const selectedJobId = agent?.ontologyContext?.jobId || state.preferredJobId || state.jobs[0]?.jobId || '';
    state.mode = 'config';
    state.currentAgent = agent;
    state.editDraft = {
      name:agent?.name || '',
      description:agent?.description || '',
      collaborationPrompt:agent?.collaborationPrompt || '请先分配协同成员分析，再以当前本体中的对象、关系、规则和证据综合回答；证据不足时明确说明。',
      ontologyJobId:selectedJobId,
      externalCallEnabled:Boolean(agent?.externalCallEnabled),
      backend:agent?.backend || (document.getElementById('qaBackendSelect')?.value || 'deepseek'),
      revision:agent?.revision || null
    };
    state.editMembers = agent?.members?.length
      ? agent.members.map(member => ({...member}))
      : (state.catalog[0] ? [{agentId:state.catalog[0].agentId,responsibility:state.catalog[0].defaultResponsibility}] : []);
    renderConfig();
  }

  function renderConfig(){
    const editing = Boolean(state.currentAgent);
    setHeader(editing ? '修改本体智能体' : '新建本体智能体','定义智能体的协作边界、成员角色与本体上下文');
    const selectedJob = selectedContextJob();
    dom.body.innerHTML = `<form class="ontologyAgentConfig" id="ontologyAgentConfigForm">
      <section class="ontologyAgentConfigSection">
        ${sectionTitleHtml('01','基本信息','定义智能体名称、用途和主智能体的协同策略。')}
        <div class="ontologyAgentFieldGrid">
          ${agentFieldHtml('智能体名称','name',state.editDraft.name,'例如：客户洞察智能体团队',true)}
          ${agentSelectHtml('默认推理模型','backend',[["deepseek","DeepSeek-V4"],["kimi","Kimi K3"],["qwen-plus","Qwen-Plus"],["jiutian","九天LOM"]],state.editDraft.backend)}
          ${agentTextareaHtml('智能体描述','description',state.editDraft.description,'说明智能体解决的业务问题和回答边界。')}
          ${agentTextareaHtml('协同提示词','collaborationPrompt',state.editDraft.collaborationPrompt,'定义主智能体如何调用和综合成员建议。','tall')}
        </div>
      </section>
      <section class="ontologyAgentConfigSection">
        ${sectionTitleHtml('02','本体上下文','智能体固定使用选定的已完成本体版本，不读取浮动草稿。')}
        <label class="ontologyAgentField full"><span>选择本体</span><select name="ontologyJobId" required>${contextOptionsHtml()}</select></label>
        <div id="ontologyAgentContextPreview">${contextPreviewHtml(selectedJob,state.currentAgent?.ontologyContext)}</div>
      </section>
      <section class="ontologyAgentConfigSection">
        <div class="ontologyAgentSectionHeading">${sectionTitleHtml('03','协同成员','选择官方原子 Agent，并为它们分配清晰的职责边界。')}<button type="button" data-agent-action="add-member"${state.editMembers.length >= Math.min(8,state.catalog.length) ? ' disabled' : ''}>＋ 添加成员</button></div>
        <div class="ontologyAgentMembers">${state.editMembers.length ? state.editMembers.map(memberRowHtml).join('') : '<div class="ontologyAgentInlineEmpty">可以不添加成员，由主智能体直接基于本体回答。</div>'}</div>
      </section>
      <section class="ontologyAgentConfigSection external">
        <div>${sectionTitleHtml('04','外部调用','发布后通过稳定接口与访问凭证调用当前智能体。')}</div>
        <label class="ontologyAgentSwitch"><input type="checkbox" name="externalCallEnabled"${state.editDraft.externalCallEnabled ? ' checked' : ''}><span></span><b>${state.editDraft.externalCallEnabled ? '已开启' : '未开启'}</b></label>
      </section>
    </form>`;
    dom.footer.innerHTML = `<span>${editing && state.currentAgent.status === 'published' ? '修改已发布配置后将回到草稿，需重新发布。' : '保存后可返回智能体管理进行对话或发布。'}</span>
      <button type="button" data-agent-action="back">取消</button>
      <button class="primary" type="button" data-agent-action="save-config"${state.editDraft.ontologyJobId ? '' : ' disabled'}>${state.busy ? '正在保存…' : '保存智能体'}</button>`;
  }

  function sectionTitleHtml(number,title,description){
    return `<div class="ontologyAgentSectionTitle"><span>${number}</span><div><strong>${escapeHtml(title)}</strong><p>${escapeHtml(description)}</p></div></div>`;
  }

  function agentFieldHtml(label,name,value,placeholder,required=false){
    return `<label class="ontologyAgentField"><span>${label}</span><input name="${name}" value="${escapeHtml(value)}" placeholder="${escapeHtml(placeholder)}"${required ? ' required' : ''}></label>`;
  }

  function agentTextareaHtml(label,name,value,placeholder,className=''){
    return `<label class="ontologyAgentField full ${className}"><span>${label}</span><textarea name="${name}" placeholder="${escapeHtml(placeholder)}">${escapeHtml(value)}</textarea></label>`;
  }

  function agentSelectHtml(label,name,options,selected){
    return `<label class="ontologyAgentField"><span>${label}</span><select name="${name}">${options.map(([value,text]) => `<option value="${value}"${value === selected ? ' selected' : ''}>${text}</option>`).join('')}</select></label>`;
  }

  function contextOptionsHtml(){
    const options = state.jobs.map(job => `<option value="${escapeHtml(job.jobId)}"${job.jobId === state.editDraft.ontologyJobId ? ' selected' : ''}>${escapeHtml(job.ontologyName)} · ${escapeHtml(job.jobId.slice(-10))}</option>`);
    if (state.currentAgent?.ontologyContext?.jobId && !state.jobs.some(job => job.jobId === state.currentAgent.ontologyContext.jobId)){
      options.unshift(`<option value="${escapeHtml(state.currentAgent.ontologyContext.jobId)}" selected>${escapeHtml(state.currentAgent.ontologyContext.ontologyName)} · 原绑定版本</option>`);
    }
    return options.length ? options.join('') : '<option value="">暂无已完成本体</option>';
  }

  function selectedContextJob(){
    return state.jobs.find(job => job.jobId === state.editDraft?.ontologyJobId) || null;
  }

  function contextPreviewHtml(job,snapshot=null){
    if (!job && !snapshot) return '<div class="ontologyAgentInlineEmpty">请先构建或导入一个本体。</div>';
    const data = job || {
      ontologyName:snapshot.ontologyName,jobId:snapshot.jobId,profile:snapshot.profile,
      objectCount:snapshot.objectCount,ruleCount:snapshot.ruleCount,instanceCount:snapshot.instanceCount
    };
    return `<div class="ontologyAgentContextPreview">
      <div><span>所属本体</span><strong>${escapeHtml(data.ontologyName)}</strong></div>
      <div><span>本体版本</span><code>${escapeHtml(data.jobId)}</code></div>
      <div><span>可用资产</span><strong>${Number(data.objectCount || 0)} 对象 · ${Number(data.ruleCount || 0)} 规则 · ${Number(data.instanceCount || 0)} 实例</strong></div>
    </div>`;
  }

  function memberRowHtml(member,index){
    const catalogItem = state.catalog.find(item => item.agentId === member.agentId);
    return `<div class="ontologyAgentMemberRow" data-member-index="${index}">
      <div class="ontologyAgentMemberIcon">✦</div>
      <label><span>成员 Agent</span><select data-member-field="agentId">${state.catalog.map(item => `<option value="${escapeHtml(item.agentId)}"${item.agentId === member.agentId ? ' selected' : ''}>${escapeHtml(item.name)}</option>`).join('')}</select><small>${escapeHtml(catalogItem?.description || '')}</small></label>
      <label><span>成员职责</span><input data-member-field="responsibility" value="${escapeHtml(member.responsibility)}" placeholder="说明该成员的交付边界"></label>
      <button type="button" data-agent-action="remove-member" data-member-index="${index}" aria-label="删除成员">×</button>
    </div>`;
  }

  async function saveConfig(){
    if (!state.editDraft?.name.trim()){
      document.querySelector('#ontologyAgentConfigForm [name="name"]')?.focus();
      return;
    }
    const memberIds = state.editMembers.map(member => member.agentId);
    if (new Set(memberIds).size !== memberIds.length){
      showInlineConfigError('同一个原子 Agent 不能重复添加。');
      return;
    }
    if (state.editMembers.some(member => !member.responsibility.trim())){
      showInlineConfigError('请为每个协同成员填写职责。');
      return;
    }
    const payload = {
      name:state.editDraft.name.trim(),
      description:state.editDraft.description.trim(),
      collaborationPrompt:state.editDraft.collaborationPrompt.trim(),
      ontologyJobId:state.editDraft.ontologyJobId,
      members:state.editMembers.map(member => ({agentId:member.agentId,responsibility:member.responsibility.trim()})),
      externalCallEnabled:state.editDraft.externalCallEnabled,
      backend:state.editDraft.backend || null
    };
    const editing = Boolean(state.currentAgent);
    if (editing) payload.revision = state.currentAgent.revision;
    state.busy = true;
    renderConfig();
    try{
      const saved = await fetchJson(editing ? `/ontology/agents/${state.currentAgent.agentId}` : '/ontology/agents',{
        method:editing ? 'PUT' : 'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify(payload)
      });
      const index = state.agents.findIndex(agent => agent.agentId === saved.agentId);
      if (index >= 0) state.agents[index] = saved;
      else state.agents.unshift(saved);
      state.currentAgent = null;
      state.publication = null;
      renderList();
    }catch(error){
      state.busy = false;
      if (error.status === 409) await loadManager();
      else{
        renderConfig();
        showInlineConfigError(error.message);
      }
      return;
    }
    state.busy = false;
  }

  function showInlineConfigError(message){
    document.querySelector('.ontologyAgentConfigError')?.remove();
    const error = document.createElement('div');
    error.className = 'ontologyAgentConfigError';
    error.textContent = message;
    dom.body.prepend(error);
  }

  async function publishAgent(agentId){
    const agent = state.agents.find(item => item.agentId === agentId);
    if (!agent || state.busy) return;
    state.busy = true;
    const button = dom.body.querySelector(`[data-agent-action="publish"][data-agent-id="${CSS.escape(agentId)}"]`);
    if (button){button.disabled = true;button.textContent = '发布中…';}
    try{
      const publication = await fetchJson(`/ontology/agents/${agentId}/publish`,{method:'POST'});
      const index = state.agents.findIndex(item => item.agentId === agentId);
      if (index >= 0) state.agents[index] = publication.agent;
      state.publication = publication;
      renderList();
    }catch(error){
      state.error = error.message;
      renderList();
      showListNotice(error.message,'error');
    }finally{
      state.busy = false;
    }
  }

  async function enterChat(agent){
    if (!agent) return;
    state.mode = 'chat';
    state.currentAgent = agent;
    state.busy = true;
    renderLoading('正在加载智能体对话…');
    try{
      const payload = await fetchJson(`/ontology/agents/${agent.agentId}/sessions`);
      state.chatSessions = payload.sessions || [];
      if (state.chatSessions.length) state.chatSession = state.chatSessions[0];
      else{
        state.chatSession = await fetchJson(`/ontology/agents/${agent.agentId}/sessions`,{method:'POST'});
        state.chatSessions = [state.chatSession];
      }
      state.busy = false;
      renderChat();
    }catch(error){
      state.busy = false;
      renderError(error.message);
    }
  }

  function renderChat(){
    const agent = state.currentAgent;
    if (!agent || !state.chatSession) return;
    setHeader(agent.name,`${agent.ontologyContext?.ontologyName || '未知本体'} · ${agent.members?.length || 0} 名协同成员 · ${statusMeta(agent.status)[0]}`);
    dom.body.innerHTML = `<div class="ontologyAgentChatLayout">
      <aside class="ontologyAgentSessions">
        <div><span>对话记录</span><button type="button" data-agent-action="new-session">＋</button></div>
        ${state.chatSessions.map(session => `<button class="${session.sessionId === state.chatSession.sessionId ? 'active' : ''}" type="button" data-agent-action="select-session" data-session-id="${escapeHtml(session.sessionId)}"><strong>${escapeHtml(session.title || '新对话')}</strong><small>${formatDate(session.updatedAt)}</small></button>`).join('')}
      </aside>
      <section class="ontologyAgentConversation">
        <div class="ontologyAgentMessages">${chatMessagesHtml(state.chatSession.messages || [])}</div>
        <form class="ontologyAgentChatForm" id="ontologyAgentChatForm">
          <textarea name="message" rows="2" maxlength="10000" placeholder="围绕当前本体的对象、关系、规则或行动提问…" required${state.busy ? ' disabled' : ''}></textarea>
          <button class="primary" type="submit"${state.busy ? ' disabled' : ''}>${state.busy ? '协同分析中…' : '发送'}</button>
        </form>
      </section>
    </div>`;
    dom.footer.innerHTML = `<span>回答受绑定本体版本与证据契约约束。</span><button type="button" data-agent-action="back-list">返回管理</button><button type="button" data-agent-action="configure" data-agent-id="${escapeHtml(agent.agentId)}">配置</button>`;
    requestAnimationFrame(() => {
      const messages = dom.body.querySelector('.ontologyAgentMessages');
      if (messages) messages.scrollTop = messages.scrollHeight;
      dom.body.querySelector('[name="message"]')?.focus({preventScroll:true});
    });
  }

  function chatMessagesHtml(messages){
    if (!messages.length){
      return `<div class="ontologyAgentChatEmpty"><span>✦</span><strong>开始一次基于本体的协同对话</strong><p>主智能体会调用已配置的原子 Agent，并以本体证据完成最终回答。</p></div>`;
    }
    return messages.map(message => {
      const collaboration = message.role === 'assistant' && message.collaboration?.length
        ? `<div class="ontologyAgentTrace"><span>Pi Agent 协同链路</span>${message.collaboration.map(item => `<b class="${item.status === 'completed' ? '' : 'degraded'}">✦ ${escapeHtml(item.name)}</b>`).join('')}</div>`
        : '';
      return `<article class="ontologyAgentMessage ${message.role}">
        <div>${message.role === 'assistant' ? '✦' : '你'}</div>
        <section><span>${message.role === 'assistant' ? escapeHtml(state.currentAgent?.name || '本体智能体') : '用户'} · ${formatDate(message.createdAt)}</span><p>${escapeHtml(message.content).replace(/\n/g,'<br>')}</p>${collaboration}</section>
      </article>`;
    }).join('');
  }

  async function sendChatMessage(message){
    const agent = state.currentAgent;
    const session = state.chatSession;
    if (!agent || !session || !message.trim() || state.busy) return;
    state.busy = true;
    session.messages = [...(session.messages || []),{role:'user',content:message.trim(),createdAt:new Date().toISOString(),messageId:'pending'}];
    renderChat();
    try{
      const result = await fetchJson(`/ontology/agents/${agent.agentId}/sessions/${session.sessionId}/messages`,{
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({message:message.trim(),backend:agent.backend || null,graphView:'auto'})
      });
      state.chatSession = result.session;
      const index = state.chatSessions.findIndex(item => item.sessionId === result.session.sessionId);
      if (index >= 0) state.chatSessions[index] = result.session;
      else state.chatSessions.unshift(result.session);
    }catch(error){
      try{
        state.chatSession = await fetchJson(`/ontology/agents/${agent.agentId}/sessions/${session.sessionId}`);
      }catch(_refreshError){/* Keep the optimistic message when refresh also fails. */}
      state.error = error.message;
    }finally{
      state.busy = false;
      renderChat();
      if (state.error){showChatError(state.error);state.error = '';}
    }
  }

  async function createChatSession(){
    const agent = state.currentAgent;
    if (!agent || state.busy) return;
    state.busy = true;
    try{
      const session = await fetchJson(`/ontology/agents/${agent.agentId}/sessions`,{method:'POST'});
      state.chatSessions.unshift(session);
      state.chatSession = session;
      state.busy = false;
      renderChat();
    }catch(error){state.busy = false;showChatError(error.message);}
  }

  function showChatError(message){
    const target = dom.body.querySelector('.ontologyAgentConversation');
    if (!target) return;
    const error = document.createElement('div');
    error.className = 'ontologyAgentChatError';
    error.textContent = message;
    target.prepend(error);
  }

  function showListNotice(message,type='info'){
    const notice = document.createElement('div');
    notice.className = `ontologyAgentListNotice ${type}`;
    notice.textContent = message;
    dom.body.prepend(notice);
  }

  function setHeader(title,subtitle){
    dom.title.textContent = title;
    dom.subtitle.textContent = subtitle;
  }

  function updateEditDraft(target){
    if (!state.editDraft || !target.name) return;
    if (target.name === 'externalCallEnabled') state.editDraft.externalCallEnabled = target.checked;
    else state.editDraft[target.name] = target.value;
    if (target.name === 'ontologyJobId'){
      const preview = document.getElementById('ontologyAgentContextPreview');
      if (preview) preview.innerHTML = contextPreviewHtml(selectedContextJob(),state.currentAgent?.ontologyContext);
    }
    if (target.name === 'externalCallEnabled'){
      target.closest('.ontologyAgentSwitch')?.querySelector('b')?.replaceChildren(document.createTextNode(target.checked ? '已开启' : '未开启'));
    }
  }

  dom.body.addEventListener('input',event => {
    const memberRow = event.target.closest('[data-member-index]');
    if (memberRow && event.target.dataset.memberField === 'responsibility'){
      state.editMembers[Number(memberRow.dataset.memberIndex)].responsibility = event.target.value;
      return;
    }
    updateEditDraft(event.target);
  });

  dom.body.addEventListener('change',event => {
    const memberRow = event.target.closest('[data-member-index]');
    if (memberRow && event.target.dataset.memberField === 'agentId'){
      const index = Number(memberRow.dataset.memberIndex);
      const previous = state.catalog.find(item => item.agentId === state.editMembers[index].agentId);
      const next = state.catalog.find(item => item.agentId === event.target.value);
      state.editMembers[index].agentId = event.target.value;
      if (!state.editMembers[index].responsibility || state.editMembers[index].responsibility === previous?.defaultResponsibility){
        state.editMembers[index].responsibility = next?.defaultResponsibility || '';
        memberRow.querySelector('[data-member-field="responsibility"]').value = state.editMembers[index].responsibility;
      }
      memberRow.querySelector('small').textContent = next?.description || '';
      return;
    }
    updateEditDraft(event.target);
  });

  dom.body.addEventListener('submit',event => {
    event.preventDefault();
    if (event.target.id === 'ontologyAgentChatForm'){
      const message = new FormData(event.target).get('message');
      sendChatMessage(String(message || ''));
    }
    if (event.target.id === 'ontologyAgentConfigForm') saveConfig();
  });

  async function handleAction(target){
    const action = target.dataset.agentAction;
    if (!action || target.disabled) return;
    if (action === 'close') closeManager();
    if (action === 'retry') loadManager();
    if (action === 'new') openConfig();
    if (action === 'back' || action === 'back-list') renderList();
    if (action === 'configure') openConfig(state.agents.find(agent => agent.agentId === target.dataset.agentId) || state.currentAgent);
    if (action === 'chat') enterChat(state.agents.find(agent => agent.agentId === target.dataset.agentId));
    if (action === 'publish') publishAgent(target.dataset.agentId);
    if (action === 'save-config') document.getElementById('ontologyAgentConfigForm')?.requestSubmit();
    if (action === 'add-member'){
      const used = new Set(state.editMembers.map(member => member.agentId));
      const next = state.catalog.find(item => !used.has(item.agentId));
      if (next) state.editMembers.push({agentId:next.agentId,responsibility:next.defaultResponsibility});
      renderConfig();
    }
    if (action === 'remove-member'){
      state.editMembers.splice(Number(target.dataset.memberIndex),1);
      renderConfig();
    }
    if (action === 'new-session') createChatSession();
    if (action === 'select-session'){
      const session = state.chatSessions.find(item => item.sessionId === target.dataset.sessionId);
      if (session){state.chatSession = session;renderChat();}
    }
    if (action === 'copy'){
      try{
        await navigator.clipboard.writeText(target.dataset.copyValue || '');
        const original = target.textContent;
        target.textContent = '已复制';
        window.setTimeout(() => {target.textContent = original;},1200);
      }catch(_error){showListNotice('复制失败，请手动选择内容。','error');}
    }
  }

  dom.body.addEventListener('click',event => {
    const target = event.target.closest('[data-agent-action]');
    if (target) handleAction(target);
  });
  dom.footer.addEventListener('click',event => {
    const target = event.target.closest('[data-agent-action]');
    if (target) handleAction(target);
  });
  dom.close.onclick = closeManager;
  dom.modal.addEventListener('click',event => {if (event.target === dom.modal) closeManager();});
  window.addEventListener('keydown',event => {if (event.key === 'Escape' && !dom.modal.hidden) closeManager();});
  window.addEventListener('lom:open-agent-manager',event => openManager(event.detail?.ontologyJobId || null));
})();
