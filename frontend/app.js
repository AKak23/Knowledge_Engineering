const API_BASE = window.ONTOLOGY_API_BASE || (window.location.protocol === 'file:' ? 'http://127.0.0.1:8000' : '');
const BUILT_IN_MOCK = window.LOM_MOCK_ONTOLOGY || null;
const MOCK_JOB_ID = BUILT_IN_MOCK?.job?.jobId || '';
const PALETTE = [
  '#5ab0ff', '#8ef0c5', '#ff7aa8', '#ffb86b', '#ffd166', '#c792ea',
  '#67e8f9', '#a7f0ba', '#7dd3fc', '#b8a8ff', '#ff5d8f', '#ff9a76',
  '#78e08f', '#fcd34d', '#4adecd', '#9aa9ff'
];
const GRAPH_VIEW_LABELS = {
  schema: '本体画布',
  instance: '本体实例'
};
const REPLAY_MAX_DURATION_MS = 55000;
const REPLAY_MAX_STEPS = 1200;
const DEFAULT_BUILD_OPTIONS = {
  replayEnabled: true,
  defaultProfile: 'default',
  defaultBackend: 'deepseek',
  constructionMode: 'schema-first',
  maxConcepts: 50,
  semanticClustering: true,
  instanceMode: 'real',
  instanceCount: 0,
  qaContextMode: 'graph_rag',
  qaFallbackEnabled: true,
  llmTimeout: null,
  llmRetries: null,
  llmConcurrency: null,
  minConceptEvidence: null,
  abstractionLevel: ''
};
let buildOptions = {...DEFAULT_BUILD_OPTIONS};
const BACKEND_LABELS = {
  jiutian: '九天LOM',
  'jiutian-35b': '九天大模型-35B',
  'qwen-plus': 'Qwen-Plus',
  'qwen-32b': 'Qwen-32B',
  'deepseek': 'DeepSeek-V4',
  kimi: 'Kimi K3'
};
const BACKEND_QA_STRATEGY_LABELS = {
  jiutian: 'LoM + 子图裁剪',
  'jiutian-35b': '原生九天 + 弱检索',
  deepseek: 'DeepSeek + 全量上下文',
  kimi: 'Kimi + 全量上下文'
};

const els = {
  appRoot: document.getElementById('appRoot'),
  graph: document.getElementById('graph'),
  dialogTitle: document.getElementById('dialogTitle'),
  legendRows: document.getElementById('legendRows'),
  nCount: document.getElementById('nCount'),
  eCount: document.getElementById('eCount'),
  graphHint: document.getElementById('graphHint'),
  serverStatus: document.getElementById('serverStatus'),
  ontologyName: document.getElementById('ontologyName'),
  chatPanel: document.getElementById('chatPanel'),
  chatPanelToggle: document.getElementById('chatPanelToggle'),
  buildPanel: document.querySelector('.buildPanel'),
  buildPanelToggle: document.getElementById('buildPanelToggle'),
  buildPanelClose: document.getElementById('buildPanelClose'),
  profileSelect: document.getElementById('profileSelect'),
  backendSelect: document.getElementById('backendSelect'),
  qaBackendSelect: document.getElementById('qaBackendSelect'),
  pageMainBtn: document.getElementById('pageMainBtn'),
  pageCompareBtn: document.getElementById('pageCompareBtn'),
  supplementalPrompt: document.getElementById('supplementalPrompt'),
  uploadBtn: document.getElementById('uploadBtn'),
  fileInput: document.getElementById('fileInput'),
  fileListWrap: document.getElementById('fileListWrap'),
  fileListSummary: document.getElementById('fileListSummary'),
  fileList: document.getElementById('fileList'),
  clearFilesBtn: document.getElementById('clearFilesBtn'),
  startBuildBtn: document.getElementById('startBuildBtn'),
  structuredConfigUploadBtn: document.getElementById('structuredConfigUploadBtn'),
  structuredConfigInput: document.getElementById('structuredConfigInput'),
  ontologyUploadBtn: document.getElementById('ontologyUploadBtn'),
  ontologyInput: document.getElementById('ontologyInput'),
  historySelect: document.getElementById('historySelect'),
  historyLoadBtn: document.getElementById('historyLoadBtn'),
  historyReplayBtn: document.getElementById('historyReplayBtn'),
  historyRefreshBtn: document.getElementById('historyRefreshBtn'),
  jobMeta: document.getElementById('jobMeta'),
  progressWrap: document.getElementById('progressWrap'),
  progressBar: document.getElementById('progressBar'),
  buildStageLog: document.getElementById('buildStageLog'),
  suggestions: document.getElementById('suggestions'),
  msgs: document.getElementById('msgs'),
  form: document.getElementById('form'),
  q: document.getElementById('q'),
  send: document.getElementById('send'),
  clearChatBtn: document.getElementById('clearChatBtn'),
  inputNote: document.getElementById('inputNote'),
  infoCard: document.getElementById('infoCard'),
  icBadge: document.getElementById('icBadge'),
  icTitle: document.getElementById('icTitle'),
  icSub: document.getElementById('icSub'),
  icAttrs: document.getElementById('icAttrs'),
  icOut: document.getElementById('icOut'),
  icIn: document.getElementById('icIn'),
  btnReset: document.getElementById('btnReset'),
  btnRotate: document.getElementById('btnRotate'),
  btnLabels: document.getElementById('btnLabels'),
  btnClear: document.getElementById('btnClear'),
  viewSchema: document.getElementById('viewSchema'),
  viewInstance: document.getElementById('viewInstance'),
  viewInstance3d: document.getElementById('viewInstance3d'),
  icClose: document.getElementById('icClose'),
  compareBackend0: document.getElementById('compareBackend0'),
  compareBackend1: document.getElementById('compareBackend1'),
  compareBackend2: document.getElementById('compareBackend2')
};

let Graph = null;
let activePage = 'main';
let chatPanelCollapsed = false;
let currentJobId = null;
let currentGraph = {ontology: {}, nodes: [], edges: []};
let graphReady = false;
let NODES = [];
let LINKS = [];
let NODE_BY_ID = {};
let OUT = {};
let IN = {};
let TYPE_META = {};
let currentGraphView = 'schema';
let selectedFiles = [];
let rotateOn = true;
let showAllLabels = false;
let highlightActive = false;
let rotateLastTs = performance.now();
let uploadControlsLocked = false;

const compareSlots = [0, 1, 2].map(index => ({
  index,
  graphEl: document.getElementById(`compareGraph${index}`),
  graphNameEl: document.getElementById(`compareGraphName${index}`),
  graphMetaEl: document.getElementById(`compareGraphMeta${index}`),
  chatNameEl: document.getElementById(`compareChatName${index}`),
  chatStatusEl: document.getElementById(`compareChatStatus${index}`),
  messagesEl: document.getElementById(`compareMsgs${index}`),
  backendSelect: document.getElementById(`compareBackend${index}`),
  backend: null,
  graph: null,
  jobId: null,
  ready: false,
  status: '等待构建',
  progress: 0,
  currentGraph: {ontology: {}, nodes: [], edges: []},
  nodes: [],
  links: [],
  nodeById: {},
  typeMeta: {},
  highlightActive: false
}));

function sleep(ms){return new Promise(resolve => setTimeout(resolve, ms));}
function apiUrl(path){return `${API_BASE}${path}`;}
function createSessionId(prefix='session'){
  return `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
}
function setBuildPanelOpen(open){
  els.buildPanel.classList.toggle('is-collapsed', !open);
  els.buildPanelToggle.classList.toggle('active', open);
  els.buildPanelToggle.setAttribute('aria-expanded', String(open));
}
function setChatPanelCollapsed(collapsed){
  chatPanelCollapsed = Boolean(collapsed);
  els.appRoot.classList.toggle('chat-collapsed', chatPanelCollapsed);
  if (els.chatPanelToggle){
    const label = chatPanelCollapsed ? '展开右侧对话栏' : '收起右侧对话栏';
    els.chatPanelToggle.textContent = chatPanelCollapsed ? '‹' : '›';
    els.chatPanelToggle.title = label;
    els.chatPanelToggle.setAttribute('aria-label', label);
    els.chatPanelToggle.setAttribute('aria-expanded', String(!chatPanelCollapsed));
  }
  window.requestAnimationFrame(resizeGraphs);
  window.setTimeout(() => window.dispatchEvent(new Event('resize')), 240);
}
function nodeId(value){return value && typeof value === 'object' ? value.id : value;}
function edgeKey(source, target){return `${source}->${target}`;}
function hex2rgba(hex, alpha){
  const h = String(hex || '#8ab').replace('#','');
  const n = parseInt(h.length === 3 ? h.split('').map(c => c + c).join('') : h, 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${alpha})`;
}
function formatFileSize(bytes){
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
function formatFileSelection(files){
  if (!files.length) return '尚未选择文档';
  const totalSize = files.reduce((sum, file) => sum + file.size, 0);
  if (files.length === 1){
    return `已选择 ${files[0].name}（${formatFileSize(files[0].size)}）`;
  }
  return `已选择 ${files.length} 个文档（合计 ${formatFileSize(totalSize)}）`;
}
function backendLabel(value){
  return BACKEND_LABELS[value || ''] || value || '配置默认';
}
function backendStrategyLabel(value){
  return BACKEND_QA_STRATEGY_LABELS[value || ''] || 'LoM 策略';
}
function backendDisplayLabel(value){
  const backend = value || '';
  return `${backendLabel(backend)} · ${backendStrategyLabel(backend)}`;
}
function slotBackendValue(slot){
  return slot.backend || slot.backendSelect?.value || '';
}
function slotBackendLabel(slot){
  return backendLabel(slotBackendValue(slot));
}
function slotBackendDisplayLabel(slot){
  return backendDisplayLabel(slotBackendValue(slot));
}
function isComparePage(){return activePage === 'compare';}
function setStatus(text, tone='ok'){
  els.serverStatus.textContent = text;
  els.serverStatus.classList.toggle('warn', tone === 'warn');
  els.serverStatus.classList.toggle('bad', tone === 'bad');
}
function updateHistoryActionButtons(){
  const selected = els.historySelect?.selectedOptions?.[0] || null;
  const disabled = uploadControlsLocked || !els.historySelect?.value;
  els.historyLoadBtn.disabled = disabled;
  if (els.historyReplayBtn){
    const hasReplay = selected?.dataset?.hasReplay === 'true';
    els.historyReplayBtn.disabled = disabled || !hasReplay;
    els.historyReplayBtn.title = hasReplay
      ? '回放历史构建过程'
      : (buildOptions.replayEnabled ? '该历史任务没有回放日志' : '回放日志记录已在 YAML 配置中关闭');
  }
}
function setUploadControlsDisabled(disabled){
  uploadControlsLocked = disabled;
  els.uploadBtn.disabled = disabled;
  els.startBuildBtn.disabled = disabled || !selectedFiles.length;
  if (els.structuredConfigUploadBtn) els.structuredConfigUploadBtn.disabled = disabled;
  els.ontologyUploadBtn.disabled = disabled;
  updateHistoryActionButtons();
}
function setBuildOptionsDisabled(disabled){
  els.profileSelect.disabled = disabled;
  els.backendSelect.disabled = disabled;
  if (els.qaBackendSelect) els.qaBackendSelect.disabled = disabled;
  els.supplementalPrompt.disabled = disabled;
  compareSlots.forEach(slot => {
    if (slot.backendSelect) slot.backendSelect.disabled = disabled;
  });
  syncConstructionModeControls(disabled);
}
function normalizeBuildOptions(raw){
  const merged = {...DEFAULT_BUILD_OPTIONS, ...(raw || {})};
  return {
    ...merged,
    maxConcepts: toPositiveInteger(merged.maxConcepts, DEFAULT_BUILD_OPTIONS.maxConcepts),
    instanceCount: toNonNegativeInteger(merged.instanceCount, DEFAULT_BUILD_OPTIONS.instanceCount),
    llmTimeout: toNullableInteger(merged.llmTimeout, {allowZero:false}),
    llmRetries: toNullableInteger(merged.llmRetries, {allowZero:true}),
    llmConcurrency: toNullableInteger(merged.llmConcurrency, {allowZero:false}),
    minConceptEvidence: toNullableInteger(merged.minConceptEvidence, {allowZero:true}),
    semanticClustering: Boolean(merged.semanticClustering),
    qaFallbackEnabled: merged.qaFallbackEnabled !== false,
    replayEnabled: merged.replayEnabled !== false,
    abstractionLevel: ['high', 'medium', 'low'].includes(merged.abstractionLevel) ? merged.abstractionLevel : '',
    constructionMode: ['standard', 'schema-first'].includes(merged.constructionMode)
      ? merged.constructionMode
      : DEFAULT_BUILD_OPTIONS.constructionMode,
    instanceMode: ['none', 'real', 'generated', 'both'].includes(merged.instanceMode)
      ? merged.instanceMode
      : DEFAULT_BUILD_OPTIONS.instanceMode
  };
}
function toPositiveInteger(value, fallback){
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? Math.round(number) : fallback;
}
function toNonNegativeInteger(value, fallback){
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 ? Math.round(number) : fallback;
}
function toNullableInteger(value, options={}){
  if (value === null || value === undefined || value === '') return null;
  const number = Number(value);
  const min = options.allowZero ? 0 : 1;
  return Number.isFinite(number) && number >= min ? Math.round(number) : null;
}
function appendOptionalInteger(form, name, value, options={}){
  const number = toNullableInteger(value, options);
  if (number !== null) form.append(name, String(number));
}
function syncConstructionModeControls(_forceDisabled=false){}
function scrollMessages(){els.msgs.scrollTop = els.msgs.scrollHeight;}
function graphViewLabel(view=currentGraphView){return GRAPH_VIEW_LABELS[view] || '图谱';}

function updateGraphViewControls(){
  const graphForControls = isComparePage()
    ? (compareReadySlots()[0]?.currentGraph || {ontology:{}})
    : currentGraph;
  const availableViews = graphForControls.ontology?.availableViews;
  const available = new Set(availableViews || ['schema', 'instance']);
  const buttons = {
    schema: els.viewSchema,
    instance: els.viewInstance,
    instance3d: els.viewInstance3d
  };
  Object.entries(buttons).forEach(([view, button]) => {
    if (!button) return;
    const backendView = view === 'instance3d' ? 'instance' : view;
    button.classList.toggle('active', currentGraphView === backendView);
    button.disabled = Boolean(availableViews) && !available.has(backendView);
  });
}

function colorForType(type){
  const key = type || 'Object';
  if (!TYPE_META[key]){
    const index = Object.keys(TYPE_META).length % PALETTE.length;
    TYPE_META[key] = {c: PALETTE[index], label: key};
  }
  return TYPE_META[key].c;
}

function compareColorForType(slot, type){
  const key = type || 'Object';
  if (!slot.typeMeta[key]){
    const index = Object.keys(slot.typeMeta).length % PALETTE.length;
    slot.typeMeta[key] = {c: PALETTE[index], label: key};
  }
  return slot.typeMeta[key].c;
}

function initGraph(){
  if (!window.ForceGraph3D || !window.THREE){
    els.graphHint.textContent = '3D 图谱库未加载，请确认网络可访问 CDN 或改为本地依赖';
    setStatus('图谱库未加载', 'bad');
    return;
  }

  Graph = ForceGraph3D()(els.graph)
    .graphData({nodes: NODES, links: LINKS})
    .backgroundColor('rgba(0,0,0,0)')
    .showNavInfo(false)
    .nodeRelSize(4.4)
    .nodeLabel(n => `<div style="font:12px -apple-system;padding:4px 8px;background:rgba(8,12,28,.92);border:1px solid #2a335e;border-radius:6px;color:#e8eeff"><b>${escapeHtml(n.label || n.id)}</b> <span style="color:#8ef0c5">${escapeHtml(n.type || '')}</span></div>`)
    .nodeVal(n => (n._hl ? 8 : 3))
    .nodeColor(n => {
      const base = colorForType(n.type);
      if (!highlightActive) return hex2rgba(base, 0.85);
      if (n._hl) return base;
      return hex2rgba(base, 0.18);
    })
    .nodeOpacity(0.95)
    .linkColor(l => {
      if (!highlightActive) return 'rgba(132,164,230,0.46)';
      if (l._hl) return 'rgba(142,240,197,0.95)';
      return 'rgba(96,116,158,0.14)';
    })
    .linkWidth(l => l._hl ? 1.4 : 0.75)
    .linkOpacity(1)
    .linkDirectionalParticles(l => l._hl ? 3 : 0)
    .linkDirectionalParticleWidth(1.8)
    .linkDirectionalParticleColor(() => '#8ef0c5')
    .linkDirectionalParticleSpeed(0.006)
    .onNodeClick(n => {
      showInfo(n);
      const dist = 180;
      const p = Math.hypot(n.x || 0, n.y || 0, n.z || 0) || 1;
      Graph.cameraPosition(
        {x:n.x * (1 + dist / p), y:n.y * (1 + dist / p), z:n.z * (1 + dist / p)},
        n,
        900
      );
    })
    .onBackgroundClick(() => hideInfo())
    .width(document.querySelector('.left').clientWidth)
    .height(document.querySelector('.left').clientHeight);

  Graph.nodeThreeObject(makeNodeObject);
  Graph.linkThreeObjectExtend(true);
  Graph.linkThreeObject(l => {
    if (!l._hl) return null;
    const group = new THREE.Group();
    group.add(makeLabel(l.rel, '#a9ecc9', {fontPx:22, scale:0.26, strong:false}));
    return group;
  });
  Graph.linkPositionUpdate((obj, {start, end}) => {
    if (!obj) return;
    obj.position.set((start.x + end.x) / 2, (start.y + end.y) / 2, (start.z + end.z) / 2);
  });
  Graph.d3Force('charge').strength(-180).distanceMax(420);
  Graph.d3Force('link').distance(42);
  addNebula(Graph, 1);
  autoRotateLoop();
}

const glowCache = {};
function makeGlow(color, size){
  const key = `${color}_${size}`;
  if (!glowCache[key]){
    const c = document.createElement('canvas');
    c.width = 128;
    c.height = 128;
    const g = c.getContext('2d');
    const grd = g.createRadialGradient(64,64,4,64,64,60);
    grd.addColorStop(0, hex2rgba(color,0.9));
    grd.addColorStop(0.4, hex2rgba(color,0.25));
    grd.addColorStop(1, hex2rgba(color,0));
    g.fillStyle = grd;
    g.fillRect(0,0,128,128);
    glowCache[key] = new THREE.CanvasTexture(c);
  }
  const mat = new THREE.SpriteMaterial({map:glowCache[key], blending:THREE.AdditiveBlending, depthWrite:false, transparent:true});
  const sprite = new THREE.Sprite(mat);
  sprite.scale.set(size, size, 1);
  return sprite;
}

const labelCache = {};
function roundRect(g, x, y, w, h, r){
  g.beginPath();
  g.moveTo(x + r, y);
  g.arcTo(x + w, y, x + w, y + h, r);
  g.arcTo(x + w, y + h, x, y + h, r);
  g.arcTo(x, y + h, x, y, r);
  g.arcTo(x, y, x + w, y, r);
  g.closePath();
}
function makeLabel(text, color, opts={}){
  const fontPx = opts.fontPx || 30;
  const strong = opts.strong !== false;
  const safeText = String(text || '');
  const key = `${safeText}|${color}|${fontPx}|${strong ? 1 : 0}`;
  if (labelCache[key]) return labelCache[key].clone();
  const pad = 10;
  const probe = document.createElement('canvas').getContext('2d');
  probe.font = `${strong ? 600 : 500} ${fontPx}px -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif`;
  const w = Math.ceil(probe.measureText(safeText).width) + pad * 2;
  const h = fontPx + pad * 2;
  const c = document.createElement('canvas');
  c.width = Math.max(w, 36);
  c.height = h;
  const g = c.getContext('2d');
  g.font = probe.font;
  g.textBaseline = 'middle';
  g.fillStyle = 'rgba(8,12,28,0.78)';
  roundRect(g, 0.5, 0.5, c.width - 1, h - 1, 12);
  g.fill();
  g.strokeStyle = hex2rgba(color, 0.75);
  g.lineWidth = 1.5;
  roundRect(g, 0.5, 0.5, c.width - 1, h - 1, 12);
  g.stroke();
  g.fillStyle = color;
  g.fillText(safeText, pad, h / 2 + 1);
  const tex = new THREE.CanvasTexture(c);
  tex.minFilter = THREE.LinearFilter;
  tex.anisotropy = 4;
  const mat = new THREE.SpriteMaterial({map:tex, depthWrite:false, depthTest:false, transparent:true});
  const sprite = new THREE.Sprite(mat);
  const scale = opts.scale || 0.32;
  sprite.scale.set(c.width * scale, h * scale, 1);
  sprite.renderOrder = 999;
  labelCache[key] = sprite;
  return sprite.clone();
}

function makeNodeObject(n){
  const base = colorForType(n.type);
  const group = new THREE.Group();
  const dim = highlightActive && !n._hl;
  const mat = new THREE.MeshBasicMaterial({color:base, transparent:true, opacity:dim ? 0.22 : 0.95});
  const semanticText = `${n.type || ''} ${n.label || ''}`.toLowerCase();
  const semanticKind = /规则|rule|constraint/.test(semanticText)
    ? 'rule'
    : (/行动|动作|action|operation/.test(semanticText) ? 'action' : 'object');
  const size = n._hl ? 5.1 : 3.2;
  const geo = semanticKind === 'rule'
    ? new THREE.ConeGeometry(size * 1.22, size * 1.9, 3)
    : (semanticKind === 'action'
      ? new THREE.OctahedronGeometry(size * 1.18, 0)
      : new THREE.SphereGeometry(size, 18, 18));
  group.add(new THREE.Mesh(geo, mat));
  if (n._hl) group.add(makeGlow(base, 20));
  const wantLabel = n._hl || (showAllLabels && (!highlightActive || n._hl));
  if (wantLabel){
    const label = makeLabel(n.label || n.id, base, {fontPx:n._hl ? 32 : 24, scale:n._hl ? 0.38 : 0.3});
    label.position.set(0, n._hl ? 9 : 6.5, 0);
    group.add(label);
  }
  return group;
}

function addNebula(graph, density=1){
  if (!graph || !window.THREE) return;
  const scene = graph.scene();
  function makeLayer({count, radius, size, opacity, palette, jitter=1}){
    const particleCount = Math.max(1, Math.round(count * density));
    const geo = new THREE.BufferGeometry();
    const pos = new Float32Array(particleCount * 3);
    const col = new Float32Array(particleCount * 3);
    for (let i = 0; i < particleCount; i++){
      const r = radius * Math.pow(Math.random(), jitter);
      const t = Math.random() * Math.PI * 2;
      const p = Math.acos(2 * Math.random() - 1);
      pos[i*3] = r * Math.sin(p) * Math.cos(t);
      pos[i*3+1] = r * Math.sin(p) * Math.sin(t);
      pos[i*3+2] = r * Math.cos(p);
      const c = palette[Math.floor(Math.random() * palette.length)];
      col[i*3] = c[0];
      col[i*3+1] = c[1];
      col[i*3+2] = c[2];
    }
    geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    geo.setAttribute('color', new THREE.BufferAttribute(col, 3));
    const mat = new THREE.PointsMaterial({size, sizeAttenuation:true, transparent:true, opacity, vertexColors:true, depthWrite:false, blending:THREE.AdditiveBlending});
    scene.add(new THREE.Points(geo, mat));
  }
  const cool = [[0.55,0.75,1.0],[0.60,0.95,0.85],[0.85,0.65,1.0],[0.70,0.82,1.0]];
  makeLayer({count:2200, radius:2200, size:1.4, opacity:0.55, palette:cool, jitter:0.55});
  makeLayer({count:1300, radius:1100, size:1.8, opacity:0.45, palette:cool, jitter:0.75});
  makeLayer({count:700, radius:620, size:2.2, opacity:0.6, palette:[[0.9,0.95,1.0],[0.7,0.9,1.0]], jitter:0.9});
}

function syncAutoRotate(){
  activeGraphInstances().forEach(graph => {
    const ctl = graph.controls();
    ctl.autoRotate = false;
    if (ctl.target) ctl.target.set(0,0,0);
  });
}
function activeGraphInstances(){
  if (isComparePage()){
    return compareSlots.map(slot => slot.graph).filter(Boolean);
  }
  return Graph ? [Graph] : [];
}
function rotateGraphInstance(graph, dt){
  const cam = graph.camera();
  const radius = Math.hypot(cam.position.x, cam.position.z);
  if (radius > 1){
    const theta = Math.atan2(cam.position.z, cam.position.x) + 0.09 * dt;
    cam.position.x = Math.cos(theta) * radius;
    cam.position.z = Math.sin(theta) * radius;
    cam.lookAt(0, 0, 0);
  }
  graph.controls().update();
}
function autoRotateLoop(ts=performance.now()){
  const graphs = activeGraphInstances();
  if (graphs.length && rotateOn){
    syncAutoRotate();
    const dt = Math.min(0.05, Math.max(0, (ts - rotateLastTs) / 1000));
    graphs.forEach(graph => rotateGraphInstance(graph, dt));
  }
  rotateLastTs = ts;
  requestAnimationFrame(autoRotateLoop);
}

function renderGraph(data){
  currentGraph = data || {ontology:{}, nodes:[], edges:[]};
  const graphView = currentGraph.ontology?.graphView;
  if (graphView === 'schema' || graphView === 'instance'){
    currentGraphView = graphView;
  }
  TYPE_META = {};
  NODES = (currentGraph.nodes || []).map(node => ({
    id:String(node.id),
    label:String(node.label || node.id),
    type:String(node.type || 'Object'),
    attrs:node.attrs || {},
    _hl:false
  }));
  NODE_BY_ID = Object.fromEntries(NODES.map(node => [node.id, node]));
  LINKS = (currentGraph.edges || []).map(edge => ({
    source:String(edge.source),
    target:String(edge.target),
    rel:String(edge.rel || 'related_to'),
    _hl:false
  }));
  NODES.forEach(node => colorForType(node.type));
  buildAdjacency();
  buildLegend();
  hideInfo();
  clearHighlight(false);
  if (Graph){
    Graph.graphData({nodes:NODES, links:LINKS});
    refreshGraphRender();
    setTimeout(() => Graph.cameraPosition({x:0,y:0,z:NODES.length ? 520 : 460}, {x:0,y:0,z:0}, 700), 100);
  }
  const name = currentGraph.ontology?.ontologyName;
  const viewName = graphViewLabel();
  if (!isComparePage()){
    els.ontologyName.textContent = name
      ? `${name} · ${viewName}（${NODES.length} 节点 / ${LINKS.length} 关系）`
      : '等待构建本体';
  }
  els.graphHint.textContent = '';
  updateGraphViewControls();
  refreshPageHeader();
  refreshSuggestions();
  window.dispatchEvent(new CustomEvent('lom:graph-rendered', {
    detail: {data: currentGraph, backendView: currentGraphView, jobId: currentJobId}
  }));
}

function compareGraphSize(slot){
  const left = document.querySelector('.left');
  const width = slot.graphEl?.clientWidth || left?.clientWidth || 360;
  const height = slot.graphEl?.clientHeight || Math.max(160, Math.floor((left?.clientHeight || 540) / 3));
  return {width, height};
}

function initCompareGraphs(){
  compareSlots.forEach(initCompareGraph);
  resizeGraphs();
}

function initCompareGraph(slot){
  if (slot.graph || !slot.graphEl) return;
  if (!window.ForceGraph3D || !window.THREE){
    setCompareSlotStatus(slot, '图谱库未加载', 'bad');
    return;
  }
  const {width, height} = compareGraphSize(slot);
  slot.graph = ForceGraph3D()(slot.graphEl)
    .graphData({nodes: slot.nodes, links: slot.links})
    .backgroundColor('rgba(0,0,0,0)')
    .showNavInfo(false)
    .nodeRelSize(3.8)
    .nodeLabel(n => `<div style="font:12px -apple-system;padding:4px 8px;background:rgba(8,12,28,.92);border:1px solid #2a335e;border-radius:6px;color:#e8eeff"><b>${escapeHtml(n.label || n.id)}</b> <span style="color:#8ef0c5">${escapeHtml(n.type || '')}</span></div>`)
    .nodeVal(n => (n._hl ? 6 : 2.6))
    .nodeColor(n => {
      const base = compareColorForType(slot, n.type);
      if (!slot.highlightActive) return hex2rgba(base, 0.82);
      if (n._hl) return base;
      return hex2rgba(base, 0.16);
    })
    .nodeOpacity(0.92)
    .linkColor(l => {
      if (!slot.highlightActive) return 'rgba(132,164,230,0.36)';
      if (l._hl) return 'rgba(142,240,197,0.95)';
      return 'rgba(96,116,158,0.12)';
    })
    .linkWidth(l => l._hl ? 1.25 : 0.55)
    .linkOpacity(1)
    .linkDirectionalParticles(l => l._hl ? 2 : 0)
    .linkDirectionalParticleWidth(1.5)
    .linkDirectionalParticleColor(() => '#8ef0c5')
    .linkDirectionalParticleSpeed(0.006)
    .onNodeClick(n => {
      const dist = 145;
      const p = Math.hypot(n.x || 0, n.y || 0, n.z || 0) || 1;
      slot.graph.cameraPosition(
        {x:n.x * (1 + dist / p), y:n.y * (1 + dist / p), z:n.z * (1 + dist / p)},
        n,
        700
      );
    })
    .width(width)
    .height(height);
  slot.graph.nodeThreeObject(n => makeCompareNodeObject(slot, n));
  slot.graph.linkThreeObjectExtend(true);
  slot.graph.linkThreeObject(l => {
    if (!l._hl) return null;
    const group = new THREE.Group();
    group.add(makeLabel(l.rel, '#a9ecc9', {fontPx:20, scale:0.22, strong:false}));
    return group;
  });
  slot.graph.linkPositionUpdate((obj, {start, end}) => {
    if (!obj) return;
    obj.position.set((start.x + end.x) / 2, (start.y + end.y) / 2, (start.z + end.z) / 2);
  });
  slot.graph.d3Force('charge').strength(-130).distanceMax(340);
  slot.graph.d3Force('link').distance(36);
  addNebula(slot.graph, 0.42);
}

function makeCompareNodeObject(slot, n){
  const base = compareColorForType(slot, n.type);
  const group = new THREE.Group();
  const dim = slot.highlightActive && !n._hl;
  const mat = new THREE.MeshBasicMaterial({color:base, transparent:true, opacity:dim ? 0.2 : 0.9});
  const geo = new THREE.SphereGeometry(n._hl ? 4.3 : 2.8, 16, 16);
  group.add(new THREE.Mesh(geo, mat));
  if (n._hl) group.add(makeGlow(base, 16));
  const wantLabel = n._hl || (showAllLabels && (!slot.highlightActive || n._hl));
  if (wantLabel){
    const label = makeLabel(n.label || n.id, base, {fontPx:n._hl ? 28 : 22, scale:n._hl ? 0.32 : 0.25});
    label.position.set(0, n._hl ? 8 : 6, 0);
    group.add(label);
  }
  return group;
}

function renderCompareGraph(slot, data){
  slot.currentGraph = data || {ontology:{}, nodes:[], edges:[]};
  const graphView = slot.currentGraph.ontology?.graphView;
  if (graphView === 'schema' || graphView === 'instance'){
    currentGraphView = graphView;
  }
  slot.typeMeta = {};
  slot.nodes = (slot.currentGraph.nodes || []).map(node => ({
    id:String(node.id),
    label:String(node.label || node.id),
    type:String(node.type || 'Object'),
    attrs:node.attrs || {},
    _hl:false
  }));
  slot.nodeById = Object.fromEntries(slot.nodes.map(node => [node.id, node]));
  slot.links = (slot.currentGraph.edges || []).map(edge => ({
    source:String(edge.source),
    target:String(edge.target),
    rel:String(edge.rel || 'related_to'),
    _hl:false
  }));
  slot.nodes.forEach(node => compareColorForType(slot, node.type));
  clearCompareHighlight(slot, false);
  initCompareGraph(slot);
  if (slot.graph){
    slot.graph.graphData({nodes:slot.nodes, links:slot.links});
    refreshCompareGraphRender(slot);
    setTimeout(() => {
      if (!slot.graph) return;
      slot.graph.cameraPosition({x:0,y:0,z:slot.nodes.length ? 360 : 320}, {x:0,y:0,z:0}, 600);
    }, 100);
  }
  const label = slotBackendLabel(slot);
  const name = slot.currentGraph.ontology?.ontologyName || label;
  slot.graphNameEl.textContent = label;
  slot.chatNameEl.textContent = label;
  slot.graphMetaEl.textContent = slot.nodes.length
    ? `${name} · ${graphViewLabel()} · ${slot.nodes.length} 节点 / ${slot.links.length} 关系`
    : slot.status;
  updateGraphViewControls();
  refreshPageHeader();
}

function refreshCompareGraphRender(slot){
  if (!slot.graph) return;
  slot.graph.nodeColor(slot.graph.nodeColor())
    .nodeThreeObject(slot.graph.nodeThreeObject())
    .linkColor(slot.graph.linkColor())
    .linkWidth(slot.graph.linkWidth())
    .linkDirectionalParticles(slot.graph.linkDirectionalParticles())
    .linkThreeObject(slot.graph.linkThreeObject());
}

function clearCompareHighlight(slot, render=true){
  slot.highlightActive = false;
  slot.nodes.forEach(node => node._hl = false);
  slot.links.forEach(link => link._hl = false);
  if (render) refreshCompareGraphRender(slot);
}

function clearAllCompareHighlights(render=true){
  compareSlots.forEach(slot => clearCompareHighlight(slot, render));
}

function applyCompareHighlight(slot, {nodes=[], edges=[], fit=true}={}){
  const nodeSet = new Set((nodes || []).map(String));
  const edgeSet = new Set();
  (edges || []).forEach(edge => {
    if (edge.length >= 2) edgeSet.add(edgeKey(String(edge[0]), String(edge[1])));
  });
  slot.links.forEach(link => {
    const source = nodeId(link.source);
    const target = nodeId(link.target);
    const selected = edgeSet.has(edgeKey(source, target)) || edgeSet.has(edgeKey(target, source));
    link._hl = selected;
    if (selected){
      nodeSet.add(source);
      nodeSet.add(target);
    }
  });
  slot.highlightActive = nodeSet.size > 0 || edgeSet.size > 0;
  slot.nodes.forEach(node => node._hl = nodeSet.has(node.id));
  refreshCompareGraphRender(slot);
  if (fit && nodeSet.size) setTimeout(() => fitCompareToHighlighted(slot), 220);
}

function fitCompareToHighlighted(slot){
  if (!slot.graph) return;
  const selected = slot.nodes.filter(node => node._hl && node.x != null);
  if (!selected.length) return;
  let cx = 0, cy = 0, cz = 0;
  selected.forEach(node => {cx += node.x; cy += node.y; cz += node.z;});
  cx /= selected.length;
  cy /= selected.length;
  cz /= selected.length;
  let radius = 0;
  selected.forEach(node => {
    radius = Math.max(radius, Math.hypot(node.x - cx, node.y - cy, node.z - cz));
  });
  const cam = slot.graph.camera();
  const dir = new THREE.Vector3(cam.position.x - cx, cam.position.y - cy, cam.position.z - cz);
  if (dir.lengthSq() < 1) dir.set(0,0,1);
  dir.normalize().multiplyScalar(Math.max(105, radius * 2.5 + 95));
  slot.graph.cameraPosition({x:cx + dir.x, y:cy + dir.y, z:cz + dir.z}, {x:cx, y:cy, z:cz}, 1000);
}

function buildAdjacency(){
  OUT = {};
  IN = {};
  NODES.forEach(node => {
    OUT[node.id] = [];
    IN[node.id] = [];
  });
  LINKS.forEach(link => {
    const source = nodeId(link.source);
    const target = nodeId(link.target);
    if (OUT[source]) OUT[source].push(link);
    if (IN[target]) IN[target].push(link);
  });
}

function buildLegend(){
  const counts = {};
  NODES.forEach(node => counts[node.type] = (counts[node.type] || 0) + 1);
  els.legendRows.innerHTML = '';
  Object.entries(counts)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], 'zh-CN'))
    .slice(0, 18)
    .forEach(([type, count]) => {
      const row = document.createElement('div');
      row.className = 'leg-row';
      const dot = document.createElement('span');
      dot.className = 'dot';
      dot.style.background = colorForType(type);
      dot.style.color = colorForType(type);
      const label = document.createElement('span');
      label.textContent = type;
      const small = document.createElement('small');
      small.textContent = count;
      row.append(dot, label, small);
      els.legendRows.appendChild(row);
    });
  els.nCount.textContent = NODES.length;
  els.eCount.textContent = LINKS.length;
}

function refreshGraphRender(){
  if (!Graph) return;
  Graph.nodeColor(Graph.nodeColor())
    .nodeThreeObject(Graph.nodeThreeObject())
    .linkColor(Graph.linkColor())
    .linkWidth(Graph.linkWidth())
    .linkDirectionalParticles(Graph.linkDirectionalParticles())
    .linkThreeObject(Graph.linkThreeObject());
}

function clearHighlight(render=true){
  highlightActive = false;
  NODES.forEach(node => node._hl = false);
  LINKS.forEach(link => link._hl = false);
  if (render) refreshGraphRender();
}

function applyHighlight({nodes=[], edges=[], fit=true}){
  const nodeSet = new Set((nodes || []).map(String));
  const edgeSet = new Set();
  (edges || []).forEach(edge => {
    if (edge.length >= 2) edgeSet.add(edgeKey(String(edge[0]), String(edge[1])));
  });
  LINKS.forEach(link => {
    const source = nodeId(link.source);
    const target = nodeId(link.target);
    const selected = edgeSet.has(edgeKey(source, target)) || edgeSet.has(edgeKey(target, source));
    link._hl = selected;
    if (selected){
      nodeSet.add(source);
      nodeSet.add(target);
    }
  });
  highlightActive = nodeSet.size > 0 || edgeSet.size > 0;
  NODES.forEach(node => node._hl = nodeSet.has(node.id));
  refreshGraphRender();
  if (fit && nodeSet.size) setTimeout(fitToHighlighted, 260);
}

function fitToHighlighted(){
  if (!Graph) return;
  const selected = NODES.filter(node => node._hl && node.x != null);
  if (!selected.length) return;
  let cx = 0, cy = 0, cz = 0;
  selected.forEach(node => {cx += node.x; cy += node.y; cz += node.z;});
  cx /= selected.length;
  cy /= selected.length;
  cz /= selected.length;
  let radius = 0;
  selected.forEach(node => {
    radius = Math.max(radius, Math.hypot(node.x - cx, node.y - cy, node.z - cz));
  });
  const cam = Graph.camera();
  const dir = new THREE.Vector3(cam.position.x - cx, cam.position.y - cy, cam.position.z - cz);
  if (dir.lengthSq() < 1) dir.set(0,0,1);
  dir.normalize().multiplyScalar(Math.max(140, radius * 2.8 + 120));
  Graph.cameraPosition({x:cx + dir.x, y:cy + dir.y, z:cz + dir.z}, {x:cx, y:cy, z:cz}, 1400);
}

function showInfo(node){
  els.infoCard.classList.add('show');
  const color = colorForType(node.type);
  els.icBadge.textContent = node.type || '-';
  els.icBadge.style.color = color;
  els.icBadge.style.borderColor = hex2rgba(color, 0.45);
  els.icTitle.textContent = node.label || node.id;
  els.icSub.textContent = `${node.id} · ${OUT[node.id]?.length || 0} 条出向 / ${IN[node.id]?.length || 0} 条入向`;
  els.icAttrs.innerHTML = '';
  const attrs = Object.entries(node.attrs || {});
  if (!attrs.length){
    const empty = document.createElement('div');
    empty.className = 'v';
    empty.textContent = '无属性';
    els.icAttrs.appendChild(empty);
  }else{
    attrs.forEach(([key, value]) => {
      const k = document.createElement('div');
      k.className = 'k';
      k.textContent = key;
      const v = document.createElement('div');
      v.className = 'v';
      v.textContent = String(value);
      els.icAttrs.append(k, v);
    });
  }
  renderEdgeList(els.icOut, OUT[node.id] || [], 'out');
  renderEdgeList(els.icIn, IN[node.id] || [], 'in');
}

function renderEdgeList(container, edges, direction){
  container.innerHTML = '';
  if (!edges.length){
    const empty = document.createElement('span');
    empty.style.cssText = 'color:var(--mute);font-size:12px';
    empty.textContent = '（无）';
    container.appendChild(empty);
    return;
  }
  edges.forEach(link => {
    const source = nodeId(link.source);
    const target = nodeId(link.target);
    const peerId = direction === 'out' ? target : source;
    const peer = NODE_BY_ID[peerId];
    const item = document.createElement('a');
    item.textContent = `${direction === 'out' ? '->' : '<-'} ${peer?.label || peerId}`;
    const rel = document.createElement('span');
    rel.className = 'etype';
    rel.textContent = link.rel;
    item.appendChild(rel);
    item.onclick = () => peer && showInfo(peer);
    container.appendChild(item);
  });
}

function hideInfo(){els.infoCard.classList.remove('show');}

function addUser(text){
  const msg = document.createElement('div');
  msg.className = 'msg user';
  const body = document.createElement('div');
  body.className = 'b';
  body.textContent = text;
  msg.appendChild(body);
  els.msgs.appendChild(msg);
  scrollMessages();
}

function addAiContainer(){
  const msg = document.createElement('div');
  msg.className = 'msg ai';
  const body = document.createElement('div');
  body.className = 'b';
  msg.appendChild(body);
  els.msgs.appendChild(msg);
  scrollMessages();
  return body;
}

function clearChatMessages(){
  if (isComparePage()){
    compareSlots.forEach(slot => {
      slot.messagesEl.innerHTML = '';
      scrollCompareMessages(slot);
    });
    clearAllCompareHighlights(true);
    return;
  }
  els.msgs.innerHTML = '';
  scrollMessages();
  clearHighlight(true);
}

function scrollCompareMessages(slot){
  slot.messagesEl.scrollTop = slot.messagesEl.scrollHeight;
}

function addCompareUser(slot, text){
  const msg = document.createElement('div');
  msg.className = 'msg user';
  const body = document.createElement('div');
  body.className = 'b';
  body.textContent = text;
  msg.appendChild(body);
  slot.messagesEl.appendChild(msg);
  scrollCompareMessages(slot);
}

function addCompareAiContainer(slot){
  const msg = document.createElement('div');
  msg.className = 'msg ai';
  const body = document.createElement('div');
  body.className = 'b';
  msg.appendChild(body);
  slot.messagesEl.appendChild(msg);
  scrollCompareMessages(slot);
  return body;
}

function addCompareSystemMessage(slot, text){
  const body = addCompareAiContainer(slot);
  const wrap = document.createElement('div');
  wrap.className = 'ans';
  wrap.innerHTML = '<div class="ans-head">状态</div><div class="ans-body"></div>';
  wrap.querySelector('.ans-body').textContent = text;
  body.appendChild(wrap);
  scrollCompareMessages(slot);
}

function addCompareError(slot, message){
  const body = addCompareAiContainer(slot);
  const wrap = document.createElement('div');
  wrap.className = 'ans error';
  wrap.innerHTML = '<div class="ans-head">请求失败</div><div class="ans-body"></div>';
  wrap.querySelector('.ans-body').textContent = message;
  body.appendChild(wrap);
  scrollCompareMessages(slot);
}

async function typeInto(el, text, ms=3){
  el.classList.add('cursor');
  for (const ch of String(text || '')){
    el.textContent += ch;
    if (ms) await sleep(ms);
  }
  el.classList.remove('cursor');
}

async function renderPlan(container, steps){
  const plan = document.createElement('div');
  plan.className = 'plan';
  const head = document.createElement('div');
  head.className = 'plan-head';
  head.innerHTML = '<svg class="chev" width="10" height="10" viewBox="0 0 10 10"><path d="M2 3 L5 7 L8 3" stroke="currentColor" stroke-width="1.4" fill="none" stroke-linecap="round"/></svg><span>推理计划</span><span class="dur"></span>';
  head.onclick = () => plan.classList.toggle('collapsed');
  const body = document.createElement('div');
  body.className = 'plan-body';
  plan.append(head, body);
  container.appendChild(plan);
  const t0 = performance.now();
  for (const step of steps || []){
    const row = document.createElement('div');
    row.className = 'step pending';
    row.innerHTML = '<span class="ic"></span><div class="txt"></div>';
    body.appendChild(row);
    await typeInto(row.querySelector('.txt'), step, 2);
    row.classList.remove('pending');
    row.classList.add('done');
    scrollMessages();
    await sleep(80);
  }
  head.querySelector('.dur').textContent = `${(steps || []).length} 步 · ${((performance.now() - t0) / 1000).toFixed(1)}s`;
  setTimeout(() => plan.classList.add('collapsed'), 700);
}

async function renderAnswer(container, ans, graphContext=null){
  const wrap = document.createElement('div');
  wrap.className = 'ans';
  wrap.innerHTML = '<div class="ans-head">最终回答</div><div class="ans-body"></div>';
  container.appendChild(wrap);
  const body = wrap.querySelector('.ans-body');
  if (ans?.summary){
    const p = document.createElement('p');
    body.appendChild(p);
    await typeInto(p, ans.summary, 2);
  }
  if (ans?.path?.length){
    body.appendChild(renderNodePath(ans.path, true, graphContext));
  }
  if (ans?.edges?.length){
    const list = document.createElement('div');
    list.className = 'edge-list';
    ans.edges.forEach(edge => {
      const row = document.createElement('div');
      const source = labelFor(edge[0], graphContext);
      const target = labelFor(edge[1], graphContext);
      row.innerHTML = `<b>${escapeHtml(source)}</b> <code>${escapeHtml(edge[2] || 'rel')}</code> <b>${escapeHtml(target)}</b>`;
      list.appendChild(row);
    });
    body.appendChild(list);
  }
  if (ans?.groups?.length){
    ans.groups.forEach(group => {
      const section = document.createElement('div');
      section.style.cssText = 'margin-top:6px';
      const title = document.createElement('div');
      title.style.cssText = 'color:var(--mute);font-size:12px;margin-bottom:3px';
      title.textContent = `${group.label}（${group.nodes.length}）`;
      section.append(title, renderNodePath(group.nodes, false, graphContext));
      body.appendChild(section);
    });
  }
  if (ans?.raw){
    const pre = document.createElement('pre');
    pre.textContent = ans.raw;
    body.appendChild(pre);
  }
  scrollMessages();
}

function renderNodePath(ids, arrows=true, graphContext=null){
  const row = document.createElement('div');
  row.className = 'path';
  ids.forEach((id, index) => {
    if (arrows && index > 0){
      const arrow = document.createElement('span');
      arrow.className = 'arrow';
      arrow.textContent = '->';
      row.appendChild(arrow);
    }
    const node = graphNodeById(id, graphContext);
    const chip = document.createElement('span');
    chip.className = 'chip';
    chip.textContent = node?.label || id;
    const color = contextColorForType(node?.type || 'Object', graphContext);
    chip.style.borderColor = hex2rgba(color, .45);
    chip.style.color = color;
    chip.onclick = () => node && showInfo(node);
    row.appendChild(chip);
  });
  return row;
}

function renderError(message){
  const body = addAiContainer();
  const wrap = document.createElement('div');
  wrap.className = 'ans error';
  wrap.innerHTML = '<div class="ans-head">请求失败</div><div class="ans-body"></div>';
  wrap.querySelector('.ans-body').textContent = message;
  body.appendChild(wrap);
  scrollMessages();
}

function graphNodeById(id, graphContext=null){
  return (graphContext?.nodeById || NODE_BY_ID)[id];
}
function contextColorForType(type, graphContext=null){
  return graphContext ? compareColorForType(graphContext, type) : colorForType(type);
}
function labelFor(id, graphContext=null){return graphNodeById(id, graphContext)?.label || id;}
function escapeHtml(value){
  return String(value ?? '').replace(/[&<>"']/g, ch => ({
    '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'
  }[ch]));
}

function refreshSuggestions(){
  els.suggestions.innerHTML = '';
  els.suggestions.classList.toggle('empty', !NODES.length);
  if (!NODES.length){
    els.suggestions.textContent = '本体加载后会根据当前图谱生成可追问主题';
    return;
  }
  const firstNode = NODES[0];
  const firstLink = LINKS[0];
  const topType = Object.entries(NODES.reduce((acc, node) => {
    acc[node.type] = (acc[node.type] || 0) + 1;
    return acc;
  }, {})).sort((a, b) => b[1] - a[1])[0]?.[0];
  const questions = currentGraphView === 'schema'
    ? [
        '概括当前本体层的主要对象、属性和关系',
        '当前 schema 中有哪些对象到对象的关键关系？',
        firstNode ? `解释「${firstNode.label}」这个对象类型的属性和关联` : null,
        firstLink ? `说明「${firstLink.rel}」关系连接的对象类型` : null
      ].filter(Boolean)
    : [
        '概括当前实例层的主要对象、属性和关系',
        '当前实例图谱里有哪些关键关系链路？',
        firstNode ? `围绕「${firstNode.label}」展开一跳邻域并说明关系` : null,
        topType ? `统计「${topType}」类型节点并说明它们的关联` : null
      ].filter(Boolean);
  questions.forEach(question => {
    const item = document.createElement('div');
    item.className = 'p';
    item.textContent = question;
    item.onclick = () => {
      els.q.value = question;
      els.form.requestSubmit();
    };
    els.suggestions.appendChild(item);
  });
}

function updateCompareModelLabels(){
  compareSlots.forEach(slot => {
    const label = slot.jobId ? slotBackendDisplayLabel(slot) : backendDisplayLabel(slot.backendSelect?.value);
    slot.graphNameEl.textContent = label;
    slot.chatNameEl.textContent = label;
    if (!slot.ready && !slot.jobId) slot.graphMetaEl.textContent = slot.status || '等待构建';
  });
}

function setCompareSlotStatus(slot, text, tone=''){
  slot.status = text;
  slot.graphMetaEl.textContent = slot.nodes.length
    ? `${slotBackendLabel(slot)} · ${graphViewLabel()} · ${slot.nodes.length} 节点 / ${slot.links.length} 关系`
    : text;
  slot.chatStatusEl.textContent = text;
  slot.chatStatusEl.classList.toggle('warn', tone === 'warn');
  slot.chatStatusEl.classList.toggle('bad', tone === 'bad');
}

function compareReadySlots(){
  return compareSlots.filter(slot => slot.ready && slot.jobId);
}

function refreshPageHeader(){
  els.pageMainBtn.classList.toggle('active', !isComparePage());
  els.pageCompareBtn.classList.toggle('active', isComparePage());
  els.dialogTitle.textContent = isComparePage() ? '模型对比' : '对话';
  if (isComparePage()){
    const ready = compareReadySlots().length;
    els.ontologyName.textContent = ready
      ? `模型对比 · ${graphViewLabel()} · ${ready}/3 已就绪`
      : `模型对比 · ${graphViewLabel()} · 等待构建`;
    els.q.placeholder = '构建完成后，输入一个问题同步发送给三个模型';
    els.inputNote.textContent = '同一个问题会横跨三个模型输出，可用底部图谱按钮统一切换和重置三层视图';
    els.send.disabled = !ready;
  }else{
    const name = currentGraph.ontology?.ontologyName;
    els.ontologyName.textContent = name
      ? `${name} · ${graphViewLabel()}（${NODES.length} 节点 / ${LINKS.length} 关系）`
      : '等待构建本体';
    els.q.placeholder = currentJobId && graphReady
      ? `围绕当前${graphViewLabel()}提问，例如：概括关键对象、追溯某个节点的关系链路`
      : '本体构建完成后，可围绕节点、关系或业务链路提问';
    els.inputNote.textContent = '主界面使用当前本体问答；对比页会把同一个问题同步发送到三个模型';
    els.send.disabled = !currentJobId || !graphReady;
  }
  updateStartBuildButton();
}

function setPage(page){
  activePage = page === 'compare' ? 'compare' : 'main';
  els.appRoot.dataset.page = activePage;
  updateCompareModelLabels();
  refreshPageHeader();
  if (isComparePage()){
    initCompareGraphs();
    setTimeout(resizeGraphs, 60);
  }else{
    setTimeout(resizeGraphs, 60);
  }
  window.dispatchEvent(new CustomEvent('lom:page-changed', {detail:{page:activePage}}));
}

async function loadProfiles(){
  try{
    const res = await fetch(apiUrl('/ontology/profiles'));
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    buildOptions = normalizeBuildOptions(data.buildDefaults);
    els.profileSelect.innerHTML = '';
    (data.profiles || []).forEach(profile => {
      const option = document.createElement('option');
      option.value = profile.name;
      option.textContent = profile.ontologyName || profile.name;
      option.title = profile.ontologyDesc || '';
      els.profileSelect.appendChild(option);
    });
    if ([...els.profileSelect.options].some(option => option.value === buildOptions.defaultProfile)){
      els.profileSelect.value = buildOptions.defaultProfile;
    }
    if ([...els.backendSelect.options].some(option => option.value === buildOptions.defaultBackend)){
      els.backendSelect.value = buildOptions.defaultBackend;
    }
    if (els.qaBackendSelect && [...els.qaBackendSelect.options].some(option => option.value === buildOptions.defaultBackend)){
      els.qaBackendSelect.value = buildOptions.defaultBackend;
    }
    updateHistoryActionButtons();
    setStatus('后端在线');
  }catch(err){
    setStatus(BUILT_IN_MOCK ? '后端未连接 · 演示可用' : '后端未连接', 'warn');
    els.jobMeta.textContent = BUILT_IN_MOCK
      ? `未连接到 API 服务；可从“历史构建结果”加载内置演示本体。`
      : `未连接到 API 服务：${apiUrl('') || window.location.origin}`;
  }
}

function historyJobLabel(job){
  const name = job.ontologyName || job.jobId;
  const time = job.updatedAt || '';
  const schema = `${job.objectCount || 0}类/${job.linkCount || 0}关系`;
  const instance = `${job.instanceCount || 0}实例/${job.instanceLinkCount || 0}边`;
  const prefix = job.mock ? '内置演示 · ' : '';
  const quality = job.qualityStatus === 'warning' ? ' · 质量告警' : '';
  return `${prefix}${time ? `${time} · ` : ''}${name} · ${schema} · ${instance}${quality}`;
}

function isMockJob(jobId){
  return Boolean(MOCK_JOB_ID) && jobId === MOCK_JOB_ID;
}

function cloneMockValue(value){
  return JSON.parse(JSON.stringify(value));
}

function historyJobsWithMock(jobs=[]){
  const completed = (jobs || []).filter(job => job.status === 'completed');
  if (!BUILT_IN_MOCK?.job) return completed;
  return [BUILT_IN_MOCK.job, ...completed.filter(job => job.jobId !== MOCK_JOB_ID)];
}

function renderHistoryJobs(jobs, previous=''){
  els.historySelect.innerHTML = '';
  if (!jobs.length){
    els.historySelect.innerHTML = '<option value="">暂无历史构建结果</option>';
    updateHistoryActionButtons();
    return;
  }
  jobs.forEach(job => {
    const option = document.createElement('option');
    option.value = job.jobId;
    option.textContent = historyJobLabel(job);
    option.dataset.hasReplay = job.hasReplay ? 'true' : 'false';
    option.dataset.isMock = job.mock ? 'true' : 'false';
    option.title = job.mock
      ? '无需后端即可加载；用于体验画布、实例、规则、行动和智能体配置。'
      : `${job.jobId}${job.inputName ? ` · ${job.inputName}` : ''}`;
    els.historySelect.appendChild(option);
  });
  if (previous && jobs.some(job => job.jobId === previous)) els.historySelect.value = previous;
  updateHistoryActionButtons();
}

async function graphDataForJob(jobId, view=currentGraphView){
  if (isMockJob(jobId)){
    const graph = BUILT_IN_MOCK?.graphs?.[view];
    if (!graph) throw new Error(`内置演示不支持视图：${view}`);
    return cloneMockValue(graph);
  }
  const res = await fetch(apiUrl(`/ontology/jobs/${jobId}/graph?view=${encodeURIComponent(view)}`));
  if (!res.ok) throw new Error(await responseError(res));
  return await res.json();
}

function mockQaResponse(view=currentGraphView, perspective=''){
  const response = cloneMockValue(BUILT_IN_MOCK?.qa?.[view] || {
    think:['读取当前演示图谱','查找相关节点与关系'],
    highlight:{nodes:[],edges:[]},
    answer:{summary:'当前问题已由内置演示问答返回。'}
  });
  if (perspective && response.answer?.summary){
    response.answer.summary = `【${perspective} · 演示回答】${response.answer.summary}`;
  }
  return response;
}

async function loadHistoryJobs(preferredJobId=null){
  if (!els.historySelect) return;
  const previous = preferredJobId || els.historySelect.value;
  els.historySelect.innerHTML = '<option value="">正在读取历史结果...</option>';
  updateHistoryActionButtons();
  try{
    const res = await fetch(apiUrl('/ontology/jobs'));
    if (!res.ok) throw new Error(await responseError(res));
    const data = await res.json();
    renderHistoryJobs(historyJobsWithMock(data.jobs || []), previous);
  }catch(err){
    const fallbackJobs = historyJobsWithMock([]);
    renderHistoryJobs(fallbackJobs, previous);
    els.jobMeta.textContent = fallbackJobs.length
      ? '后端历史结果暂不可用；当前可加载内置演示本体。'
      : `历史结果读取失败：${err.message || err}`;
  }
}

async function loadExistingJob(jobId){
  if (!jobId) return;
  window.dispatchEvent(new CustomEvent('lom:ontology-load-started', {detail:{jobId}}));
  const historyLabel = isMockJob(jobId) ? '内置演示本体' : `历史构建结果 ${jobId}`;
  currentJobId = jobId;
  graphReady = false;
  els.send.disabled = true;
  setUploadControlsDisabled(true);
  setBuildOptionsDisabled(true);
  els.progressWrap.classList.remove('show');
  els.jobMeta.textContent = `正在加载${historyLabel}...`;
  setStatus('加载历史本体中', 'warn');
  renderGraph({ontology:{}, nodes:[], edges:[]});
  try{
    await loadGraph(jobId, {announce:false});
    els.jobMeta.textContent = isMockJob(jobId)
      ? '已加载内置演示本体；可体验对象、规则、行动、数据挂载和实例视图。'
      : `已加载历史构建结果 ${jobId}`;
    els.q.placeholder = `围绕当前${graphViewLabel()}提问，例如：概括关键对象、追溯某个节点的关系链路`;
    els.send.disabled = false;
    setStatus(isMockJob(jobId) ? '演示本体已加载 · 可提问' : '本体已加载 · 可提问');
    setBuildPanelOpen(false);
  }catch(err){
    currentJobId = null;
    graphReady = false;
    els.send.disabled = true;
    setStatus('加载失败', 'bad');
    els.jobMeta.textContent = err.message || String(err);
    renderError(`加载历史构建结果失败：${err.message || err}`);
  }finally{
    setUploadControlsDisabled(false);
    setBuildOptionsDisabled(false);
  }
}

function replayEventState(event){
  return event && typeof event === 'object' ? (event.state || event) : {};
}

function replayStepDelay(events){
  return Math.min(1200, Math.max(20, Math.floor(REPLAY_MAX_DURATION_MS / Math.max(events.length, 1))));
}

function compactReplayEvents(events){
  if (events.length <= REPLAY_MAX_STEPS) return events;
  const step = events.length / REPLAY_MAX_STEPS;
  const compacted = [];
  for (let index = 0; index < REPLAY_MAX_STEPS; index += 1){
    compacted.push(events[Math.floor(index * step)]);
  }
  const last = events[events.length - 1];
  if (compacted[compacted.length - 1] !== last) compacted.push(last);
  return compacted;
}

function replayFileName(jobId, events){
  const firstState = replayEventState(events[0]);
  const inputNames = firstState.inputNames || [];
  if (Array.isArray(inputNames) && inputNames.length) return inputNames.join('、');
  const selected = els.historySelect?.selectedOptions?.[0]?.textContent || '';
  return selected || jobId;
}

async function replayExistingJob(jobId){
  if (!jobId) return;
  window.dispatchEvent(new CustomEvent('lom:ontology-replay-started', {detail:{jobId}}));
  setPage('main');
  currentJobId = jobId;
  graphReady = false;
  els.send.disabled = true;
  setUploadControlsDisabled(true);
  setBuildOptionsDisabled(true);
  els.progressWrap.classList.add('show');
  els.progressBar.style.width = '1%';
  els.q.placeholder = '正在回放历史构建日志，请稍候';
  setStatus('历史构建回放中', 'warn');
  renderGraph({ontology:{}, nodes:[], edges:[]});

  try{
    const res = await fetch(apiUrl(`/ontology/jobs/${jobId}/replay`));
    if (!res.ok) throw new Error(await responseError(res));
    const data = await res.json();
    const events = compactReplayEvents(Array.isArray(data.events) ? data.events : []);
    const fileName = replayFileName(jobId, events);
    const delay = replayStepDelay(events);
    let previousStage = '';
    let sameStageCount = 0;
    for (let index = 0; index < events.length; index += 1){
      const state = replayEventState(events[index]);
      const progress = state.progress || {};
      const stage = String(state.stage || '');
      sameStageCount = stage && stage === previousStage ? sameStageCount + 1 : 1;
      previousStage = stage;
      const playbackPercent = Math.round(((index + 1) / Math.max(events.length, 1)) * 100);
      els.progressBar.style.width = `${Math.max(4, Math.min(100, playbackPercent))}%`;
      els.jobMeta.textContent = buildReplayProgressText(
        fileName,
        state,
        progress,
        index + 1,
        events.length,
        sameStageCount,
      );
      renderBuildStageLog(state);
      if (state.status === 'failed'){
        setStatus('历史构建回放到失败状态', 'bad');
      }else{
        setStatus(state.status === 'completed' ? '回放即将完成' : '历史构建回放中', 'warn');
      }
      if (index < events.length - 1) await sleep(delay);
    }
    els.progressBar.style.width = '100%';
    await loadGraph(jobId, {actionLabel:'历史构建回放完成'});
    els.jobMeta.textContent = `历史构建回放完成：${jobId}`;
    els.q.placeholder = `围绕当前${graphViewLabel()}提问，例如：概括关键对象、追溯某个节点的关系链路`;
    els.send.disabled = false;
    setStatus('本体已回放 · 可提问');
    setBuildPanelOpen(false);
  }catch(err){
    currentJobId = null;
    graphReady = false;
    els.send.disabled = true;
    els.progressWrap.classList.remove('show');
    setStatus('回放失败', 'bad');
    els.jobMeta.textContent = err.message || String(err);
    renderError(`历史构建回放失败：${err.message || err}`);
  }finally{
    setUploadControlsDisabled(false);
    setBuildOptionsDisabled(false);
  }
}

async function loadExistingJobForCompare(jobId){
  if (!jobId) return;
  setPage('compare');
  initCompareGraphs();
  setUploadControlsDisabled(true);
  setBuildOptionsDisabled(true);
  els.send.disabled = true;
  els.progressWrap.classList.remove('show');
  els.jobMeta.textContent = `正在加载历史构建结果 ${jobId} 到模型对比页...`;
  setStatus('加载历史本体中', 'warn');
  compareSlots.forEach(slot => {
    slot.jobId = jobId;
    slot.ready = false;
    slot.backend = slot.backendSelect?.value || null;
    slot.progress = 100;
    slot.messagesEl.innerHTML = '';
    setCompareSlotStatus(slot, '加载中', 'warn');
  });

  try{
    const data = await graphDataForJob(jobId, currentGraphView);
    compareSlots.forEach(slot => {
      renderCompareGraph(slot, data);
      slot.ready = true;
      setCompareSlotStatus(slot, '已就绪');
      addCompareSystemMessage(slot, `${slotBackendLabel(slot)} 已加载历史本体：${data.ontology?.ontologyName || jobId}。`);
    });
    els.jobMeta.textContent = `已在模型对比页加载历史构建结果 ${jobId}`;
    els.q.placeholder = '围绕当前模型对比本体提问，同一个问题会同步发送给三个模型';
    els.send.disabled = false;
    setStatus('模型对比本体已加载 · 可提问');
    setBuildPanelOpen(false);
    refreshPageHeader();
  }catch(err){
    compareSlots.forEach(slot => {
      slot.ready = false;
      setCompareSlotStatus(slot, '加载失败', 'bad');
      addCompareError(slot, `加载历史构建结果失败：${err.message || err}`);
    });
    els.send.disabled = true;
    setStatus('加载失败', 'bad');
    els.jobMeta.textContent = err.message || String(err);
  }finally{
    setUploadControlsDisabled(false);
    setBuildOptionsDisabled(false);
  }
}

function createBuildForm(files, backendValue){
  const form = new FormData();
  files.forEach(file => form.append('files', file));
  form.append('profile', els.profileSelect.value || 'default');
  if (backendValue) form.append('backend', backendValue);
  const supplementalPrompt = els.supplementalPrompt.value.trim();
  if (supplementalPrompt) form.append('supplemental_prompt', supplementalPrompt);
  const constructionMode = buildOptions.constructionMode;
  form.append('construction_mode', constructionMode);
  const maxConcepts = Number(buildOptions.maxConcepts);
  if (constructionMode === 'schema-first' && Number.isFinite(maxConcepts) && maxConcepts > 0){
    form.append('max_concepts', String(Math.round(maxConcepts)));
  }
  if (buildOptions.semanticClustering){
    form.append('use_embedding', 'true');
  }
  appendOptionalInteger(form, 'llm_timeout', buildOptions.llmTimeout);
  appendOptionalInteger(form, 'llm_retries', buildOptions.llmRetries, {allowZero:true});
  appendOptionalInteger(form, 'llm_concurrency', buildOptions.llmConcurrency);
  appendOptionalInteger(form, 'min_concept_evidence', buildOptions.minConceptEvidence, {allowZero:true});
  if (buildOptions.abstractionLevel){
    form.append('abstraction_level', buildOptions.abstractionLevel);
  }
  const instanceMode = buildOptions.instanceMode;
  const instanceCount = Number(buildOptions.instanceCount);
  const generatedInstanceCount = ['generated', 'both'].includes(instanceMode)
    && Number.isFinite(instanceCount)
    && instanceCount > 0
      ? Math.round(instanceCount)
      : 0;
  form.append('instance_count', String(generatedInstanceCount));
  if (constructionMode === 'schema-first'){
    form.append('drop_instances', instanceMode === 'none' ? 'true' : 'false');
    form.append('extract_real_instances', 'false');
  }
  return {form, supplementalPrompt, constructionMode};
}

async function startBuild(files){
  files = Array.from(files || []);
  if (!files.length){
    els.jobMeta.textContent = '请先选择一个或多个文档。';
    return;
  }
  currentJobId = null;
  graphReady = false;
  setUploadControlsDisabled(true);
  setBuildOptionsDisabled(true);
  els.send.disabled = true;
  els.q.placeholder = '正在构建本体，请稍候';
  els.progressWrap.classList.add('show');
  els.progressBar.style.width = '1%';
  renderBuildStageLog({
    stage: '上传构建文档',
    stageHistory: [{stage:'上传构建文档', hint:'正在把所选文档提交到后端。', timestamp:new Date().toISOString()}]
  });
  els.jobMeta.textContent = `正在上传 ${formatFileSelection(files).replace('已选择 ', '')}...`;
  setStatus('构建任务提交中', 'warn');
  renderGraph({ontology:{}, nodes:[], edges:[]});

  const {form, supplementalPrompt, constructionMode} = createBuildForm(files, els.backendSelect.value);

  try{
    const res = await fetch(apiUrl('/ontology/jobs'), {method:'POST', body:form});
    if (!res.ok) throw new Error(await responseError(res));
    const data = await res.json();
    currentJobId = data.jobId;
    const modeLabel = constructionMode === 'schema-first' ? '概念优先' : '标准抽取';
    els.jobMeta.textContent = `任务 ${currentJobId} 已创建，正在抽取本体（${modeLabel}）${supplementalPrompt ? '（含构建说明）' : ''}...`;
    await pollJob(currentJobId, files.map(file => file.name).join('、'));
  }catch(err){
    setUploadControlsDisabled(false);
    setBuildOptionsDisabled(false);
    els.progressWrap.classList.remove('show');
    setStatus('构建失败', 'bad');
    els.jobMeta.textContent = err.message || String(err);
    renderError(`本体构建任务提交失败：${err.message || err}`);
  }
}

function resetCompareSlotForBuild(slot){
  slot.jobId = null;
  slot.ready = false;
  slot.backend = null;
  slot.progress = 0;
  slot.nodes = [];
  slot.links = [];
  slot.nodeById = {};
  slot.currentGraph = {ontology:{}, nodes:[], edges:[]};
  slot.messagesEl.innerHTML = '';
  setCompareSlotStatus(slot, '等待提交', 'warn');
  renderCompareGraph(slot, {ontology:{}, nodes:[], edges:[]});
}

async function startCompareBuild(files){
  files = Array.from(files || []);
  if (!files.length){
    els.jobMeta.textContent = '请先选择一个或多个文档。';
    return;
  }
  setPage('compare');
  initCompareGraphs();
  setUploadControlsDisabled(true);
  setBuildOptionsDisabled(true);
  els.send.disabled = true;
  els.q.placeholder = '正在并行构建模型对比本体，请稍候';
  els.progressWrap.classList.add('show');
  els.progressBar.style.width = '1%';
  els.jobMeta.textContent = `正在向三个模型上传 ${formatFileSelection(files).replace('已选择 ', '')}...`;
  setStatus('模型对比构建提交中', 'warn');
  compareSlots.forEach(resetCompareSlotForBuild);

  const results = await Promise.allSettled(compareSlots.map(slot => runCompareBuildSlot(slot, files)));
  const ready = compareReadySlots().length;
  setUploadControlsDisabled(false);
  setBuildOptionsDisabled(false);
  els.progressWrap.classList.toggle('show', ready > 0 && ready < 3);
  els.send.disabled = ready === 0;
  refreshPageHeader();
  loadHistoryJobs();
  if (ready === 3){
    els.progressBar.style.width = '100%';
    els.progressWrap.classList.remove('show');
    setStatus('模型对比本体已构建 · 可提问');
    els.jobMeta.textContent = `模型对比构建完成：${compareSlots.map(slot => `${slotBackendLabel(slot)} ${slot.nodes.length}节点/${slot.links.length}关系`).join(' · ')}`;
    setBuildPanelOpen(false);
    return;
  }
  const failed = results.length - ready;
  setStatus(ready ? '部分模型已构建 · 可提问' : '模型对比构建失败', ready ? 'warn' : 'bad');
  els.jobMeta.textContent = ready
    ? `已有 ${ready}/3 个模型构建完成，${failed} 个失败或中断。`
    : '三个模型均未构建成功，请检查后端日志或 API key 后重试。';
}

async function runCompareBuildSlot(slot, files){
  try{
    const backend = slot.backendSelect.value;
    slot.backend = backend;
    setCompareSlotStatus(slot, '上传中', 'warn');
    const {form, supplementalPrompt, constructionMode} = createBuildForm(files, backend);
    const res = await fetch(apiUrl('/ontology/jobs'), {method:'POST', body:form});
    if (!res.ok) throw new Error(await responseError(res));
    const data = await res.json();
    slot.jobId = data.jobId;
    slot.ready = false;
    const modeLabel = constructionMode === 'schema-first' ? '概念优先' : '标准抽取';
    setCompareSlotStatus(slot, `构建中 · ${modeLabel}`, 'warn');
    addCompareSystemMessage(slot, `任务 ${slot.jobId} 已创建${supplementalPrompt ? '（含构建说明）' : ''}。`);
    await pollCompareJob(slot, files.map(file => file.name).join('、'));
  }catch(err){
    if (slot.status !== '构建失败'){
      setCompareSlotStatus(slot, '构建失败', 'bad');
      addCompareError(slot, `本体构建任务失败：${err.message || err}`);
    }
    throw err;
  }
}

async function pollCompareJob(slot, fileName){
  while (slot.jobId && !slot.ready){
    const res = await fetch(apiUrl(`/ontology/jobs/${slot.jobId}/status`));
    if (!res.ok) throw new Error(await responseError(res));
    const state = await res.json();
    const progress = state.progress || {};
    const percent = Number(progress.overallPercent ?? progress.percent ?? 0);
    slot.progress = Math.max(0, Math.min(100, percent));
    updateCompareProgress();
    const stage = state.stage ? ` · ${state.stage}` : '';
    setCompareSlotStatus(slot, `${state.status}${stage}`, 'warn');
    if (state.status === 'completed'){
      slot.progress = 100;
      await loadCompareGraph(slot);
      slot.ready = true;
      setCompareSlotStatus(slot, '已就绪');
      addCompareSystemMessage(slot, `${slotBackendLabel(slot)} 构建完成：已加载${graphViewLabel()} ${slot.nodes.length} 个节点、${slot.links.length} 条关系。`);
      updateCompareProgress();
      return;
    }
    if (state.status === 'failed'){
      slot.ready = false;
      setCompareSlotStatus(slot, '构建失败', 'bad');
      addCompareError(slot, `本体构建失败：${state.error || '未知错误'}`);
      throw new Error(`${slotBackendLabel(slot)} 构建失败：${state.error || '未知错误'}`);
    }
    els.jobMeta.textContent = buildProgressText(`${slotBackendLabel(slot)} · ${fileName}`, state, progress);
    await sleep(1500);
  }
}

function updateCompareProgress(){
  const avg = compareSlots.reduce((sum, slot) => sum + (slot.progress || 0), 0) / compareSlots.length;
  els.progressBar.style.width = `${Math.max(4, Math.round(avg))}%`;
}

async function loadCompareGraph(slot, options={}){
  if (!slot.jobId) return;
  const announce = options.announce !== false;
  const data = await graphDataForJob(slot.jobId, currentGraphView);
  renderCompareGraph(slot, data);
  if (!announce) return;
  setCompareSlotStatus(slot, '已就绪');
}

async function uploadStructuredConfig(file){
  if (!file){
    els.jobMeta.textContent = '请先选择一个 YAML 或 YML 配置文件。';
    return;
  }
  setUploadControlsDisabled(true);
  setBuildOptionsDisabled(true);
  els.progressWrap.classList.remove('show');
  els.jobMeta.textContent = `正在上传结构化数据配置：${file.name}`;
  setStatus('结构化配置上传中', 'warn');

  const form = new FormData();
  form.append('file', file);

  try{
    renderBuildStageLog({
      stage: '上传结构化配置',
      stageHistory: [{stage:'上传结构化配置', hint:'正在校验并提交 YAML 配置。', timestamp:new Date().toISOString()}]
    });
    const res = await fetch(apiUrl('/ontology/structured-configs'), {method:'POST', body:form});
    if (!res.ok) throw new Error(await responseError(res));
    const data = await res.json();
    const keys = (data.configKeys || []).join('、');
    currentJobId = data.jobId;
    els.progressWrap.classList.add('show');
    els.progressBar.style.width = '1%';
    els.jobMeta.textContent = `结构化构建任务 ${currentJobId} 已创建：${data.inputName || file.name}${keys ? `（字段：${keys}）` : ''}`;
    await pollJob(currentJobId, data.inputName || file.name);
  }catch(err){
    setStatus('结构化配置上传失败', 'bad');
    els.jobMeta.textContent = err.message || String(err);
    els.progressWrap.classList.remove('show');
  }finally{
    setUploadControlsDisabled(false);
    setBuildOptionsDisabled(false);
  }
}

async function importOntology(file){
  if (!file){
    els.jobMeta.textContent = '请先选择一个本体 JSON 或 JSONL 文件。';
    return;
  }
  currentJobId = null;
  graphReady = false;
  setUploadControlsDisabled(true);
  setBuildOptionsDisabled(true);
  els.send.disabled = true;
  els.q.placeholder = '正在导入本体，请稍候';
  els.progressWrap.classList.remove('show');
  els.jobMeta.textContent = `正在上传本体文件：${file.name}`;
  setStatus('本体导入中', 'warn');
  renderGraph({ontology:{}, nodes:[], edges:[]});
  window.dispatchEvent(new CustomEvent('lom:ontology-import-started', {detail:{fileName:file.name}}));

  const form = new FormData();
  form.append('file', file);

  try{
    const res = await fetch(apiUrl('/ontology/jobs/import'), {method:'POST', body:form});
    if (!res.ok) throw new Error(await responseError(res));
    const data = await res.json();
    currentJobId = data.jobId;
    await loadGraph(currentJobId, {actionLabel:'本体导入完成'});
    els.jobMeta.textContent = `已导入本体文件：${file.name}`;
    els.q.placeholder = `围绕当前${graphViewLabel()}提问，例如：概括关键对象、追溯某个节点的关系链路`;
    els.send.disabled = false;
    setStatus('本体已导入 · 可提问');
    setBuildPanelOpen(false);
    loadHistoryJobs();
  }catch(err){
    currentJobId = null;
    graphReady = false;
    els.send.disabled = true;
    setStatus('导入失败', 'bad');
    els.jobMeta.textContent = err.message || String(err);
    renderError(`本体导入失败：${err.message || err}`);
  }finally{
    setUploadControlsDisabled(false);
    setBuildOptionsDisabled(false);
  }
}

async function pollJob(jobId, fileName){
  while (currentJobId === jobId){
    const res = await fetch(apiUrl(`/ontology/jobs/${jobId}/status`));
    if (!res.ok) throw new Error(await responseError(res));
    const state = await res.json();
    const progress = state.progress || {};
    const percent = Number(progress.overallPercent ?? progress.percent ?? 0);
    els.progressBar.style.width = `${Math.max(4, percent)}%`;
    els.jobMeta.textContent = buildProgressText(fileName, state, progress);
    renderBuildStageLog(state);
    if (state.status === 'completed'){
      els.progressBar.style.width = '100%';
      await loadGraph(jobId);
      window.dispatchEvent(new CustomEvent('lom:ontology-build-completed', {detail:{jobId}}));
      setUploadControlsDisabled(false);
      setBuildOptionsDisabled(false);
      els.send.disabled = false;
      els.q.placeholder = `围绕当前${graphViewLabel()}提问，例如：概括关键对象、追溯某个节点的关系链路`;
      const hasQualityWarnings = Array.isArray(state.qualityWarnings) && state.qualityWarnings.length > 0;
      setStatus(
        hasQualityWarnings ? '本体已构建 · 存在质量告警' : '本体已构建 · 可提问',
        hasQualityWarnings ? 'warn' : undefined
      );
      setBuildPanelOpen(false);
      loadHistoryJobs(jobId);
      return;
    }
    if (state.status === 'failed'){
      currentJobId = null;
      graphReady = false;
      setUploadControlsDisabled(false);
      setBuildOptionsDisabled(false);
      els.progressWrap.classList.remove('show');
      els.q.placeholder = '构建失败，请检查后端日志或 API key 后重试';
      setStatus('构建失败', 'bad');
      renderError(`本体构建失败：${state.error || '未知错误'}`);
      return;
    }
    setStatus(state.status === 'queued' ? '任务排队中' : '本体构建中', 'warn');
    await sleep(1500);
  }
}

function buildProgressText(fileName, state, progress){
  const overallPercent = Number(progress.overallPercent ?? progress.percent ?? 0);
  const lines = [
    `文档：${formatJobDocumentLabel(fileName, state)}`,
    `状态：${statusLabel(state.status)}`,
    `总体进度：${Math.max(0, Math.min(100, overallPercent)).toFixed(0)}%`,
    `进度：${formatChunkProgress(progress)}`
  ];
  if (state.stage) lines.push(`阶段：${state.stage}`);
  if (state.stageHint) lines.push(`说明：${state.stageHint}`);
  const embedding = formatEmbeddingStatus(state);
  if (embedding) lines.push(`向量聚类：${embedding}`);
  if (Array.isArray(state.qualityWarnings) && state.qualityWarnings.length){
    lines.push(`质量：${state.qualityWarnings.join('；')}`);
  }
  const task = stageTaskLabel(state.stage);
  if (task) lines.push(`任务：${task}`);
  const instanceParams = formatInstanceParams(state);
  if (instanceParams) lines.push(`实例参数：${instanceParams}`);
  const logSampling = formatLogSampling(state);
  if (logSampling) lines.push(`日志采样：${logSampling}`);
  const resultStats = formatResultStats(state);
  if (resultStats) lines.push(`统计：${resultStats}`);
  return lines.join('\n');
}

function renderBuildStageLog(state){
  if (!els.buildStageLog) return;
  const history = Array.isArray(state?.stageHistory) ? state.stageHistory : [];
  const items = history.length
    ? history.slice(-20)
    : (state?.stage ? [{stage:state.stage, hint:state.stageHint || '', timestamp:state.stageUpdatedAt || ''}] : []);
  els.buildStageLog.replaceChildren();
  els.buildStageLog.hidden = !items.length;
  items.forEach((item, index) => {
    const row = document.createElement('div');
    const isCurrent = index === items.length - 1 && state?.status !== 'completed';
    const isFailed = isCurrent && state?.status === 'failed';
    row.className = `buildStageItem${isCurrent ? ' current' : ''}${isFailed ? ' failed' : ''}`;
    const time = document.createElement('span');
    time.className = 'buildStageTime';
    time.textContent = formatStageTime(item.timestamp);
    const mark = document.createElement('span');
    mark.className = 'buildStageMark';
    mark.textContent = isFailed ? '×' : (isCurrent ? '●' : '✓');
    const label = document.createElement('span');
    label.textContent = String(item.stage || '处理中');
    row.append(time, mark, label);
    if (item.hint){
      const hint = document.createElement('span');
      hint.className = 'buildStageHint';
      hint.textContent = String(item.hint);
      row.appendChild(hint);
    }
    els.buildStageLog.appendChild(row);
  });
  els.buildStageLog.scrollTop = els.buildStageLog.scrollHeight;
}

function formatStageTime(value){
  if (!value) return '--:--:--';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? '--:--:--'
    : parsed.toLocaleTimeString('zh-CN', {hour12:false});
}

function formatEmbeddingStatus(state){
  const status = String(state.embeddingStatus || '');
  if (!status) return '';
  const model = state.embeddingModel || '未指定模型';
  if (status === 'enabled') return `已启用 ${model}`;
  if (status === 'skipped') return `已跳过 ${model}（${state.embeddingWarning || '未配置 key'}）`;
  return `未启用（${model}）`;
}

function buildReplayProgressText(fileName, state, progress, step, total, sameStageCount){
  const repeated = sameStageCount > 1 ? `，同阶段 ${sameStageCount}` : '';
  return [
    `回放：${step}/${total}${repeated}`,
    buildProgressText(fileName, state, progress),
  ].join('\n');
}

function formatJobDocumentLabel(fileName, state){
  const prefix = String(fileName || '').includes(' · ')
    ? String(fileName).split(' · ')[0]
    : '';
  const names = Array.isArray(state.inputNames) && state.inputNames.length
    ? state.inputNames
    : splitFileNameText(fileName);
  const summary = names.length > 1
    ? `${names[0]} 等 ${names.length} 个文件`
    : (names[0] || fileName || '-');
  return prefix ? `${prefix} · ${summary}` : summary;
}

function splitFileNameText(fileName){
  return String(fileName || '')
    .split(/[、,，]\s*/)
    .map(item => item.trim())
    .filter(Boolean);
}

function statusLabel(status){
  return {
    queued: '排队中',
    running: '运行中',
    completed: '已完成',
    failed: '失败'
  }[status] || status || '-';
}

function formatChunkProgress(progress){
  const done = progress.doneChunks || 0;
  const total = progress.totalChunks || 0;
  const failed = progress.failedChunks || 0;
  return failed ? `chunk ${done}/${total}，失败 ${failed}` : `chunk ${done}/${total}`;
}

function stageTaskLabel(stage){
  const value = String(stage || '');
  if (!value) return '';
  if (value.includes('加载配置')) return '读取构建配置';
  if (value.toLowerCase().includes('embedding')) return '候选概念向量化与语义聚类';
  if (value.includes('分块')) return '文本清洗、文档分块';
  if (value.includes('候选概念抽取')) return '候选概念抽取';
  if (value.includes('schema') || value.includes('概念')) return '本体类型、属性、关系构建';
  if (value.includes('chunk 本体抽取')) return 'chunk 级本体抽取';
  if (value.includes('合并')) return '局部本体合并';
  if (value.includes('实例连通性')) return '实例关系补全';
  if (value.includes('实例')) return '实例抽取、属性生成、实例关系构建';
  if (value.includes('动作')) return '动作层派生';
  if (value.includes('结构化')) return '结构化数据映射';
  if (value.includes('数据库')) return '数据库元信息读取';
  if (value.includes('格式化') || value.includes('写出')) return '结果文件生成';
  if (value.includes('完成')) return '结果加载就绪';
  if (value.includes('失败')) return '异常记录';
  return value;
}

function formatInstanceParams(state){
  const params = [];
  if (state.instanceBuildInputFiles != null) params.push(`输入文件 ${state.instanceBuildInputFiles}`);
  if (state.instanceBuildTextFiles != null) params.push(`文本文件 ${state.instanceBuildTextFiles}`);
  if (state.instanceBuildLlmEnabled != null) params.push(`LLM ${state.instanceBuildLlmEnabled ? '启用' : '未启用'}`);
  if (state.instanceBuildLlmChunkBatchSize != null) params.push(`批大小 ${state.instanceBuildLlmChunkBatchSize}`);
  if (state.instanceBuildLlmConcurrency != null) params.push(`并发 ${state.instanceBuildLlmConcurrency}`);
  if (state.instanceBuildMode) params.push(`模式 ${state.instanceBuildMode}`);
  return params.join('，');
}

function formatLogSampling(state){
  const sampling = [];
  if (state.instanceBuildMaxLogRecordsPerFile != null) sampling.push(`${state.instanceBuildMaxLogRecordsPerFile}/文件`);
  if (state.instanceBuildMaxLogRecordsPerTemplate != null) sampling.push(`${state.instanceBuildMaxLogRecordsPerTemplate}/模板`);
  return sampling.join('，');
}

function formatResultStats(state){
  const metrics = [];
  if (state.extracted != null) metrics.push(`抽取 ${state.extracted}`);
  if (state.instanceBuildCandidates != null) metrics.push(`实例候选 ${state.instanceBuildCandidates}`);
  if (state.instanceBuildInstances != null) metrics.push(`实例 ${state.instanceBuildInstances}`);
  if (state.instanceBuildLinks != null) metrics.push(`实例关系 ${state.instanceBuildLinks}`);
  if (state.instanceBuildFailedBatches != null) metrics.push(`失败批次 ${state.instanceBuildFailedBatches}`);
  if (state.instanceBuildSkippedCandidates != null) metrics.push(`跳过候选 ${state.instanceBuildSkippedCandidates}`);
  if (state.instancePostprocessLinks != null) metrics.push(`后处理补边 ${state.instancePostprocessLinks}`);
  if (state.instancePostprocessDropped != null) metrics.push(`后处理剔除 ${state.instancePostprocessDropped}`);
  if (state.actionBuildActions != null) metrics.push(`动作 ${state.actionBuildActions}`);
  if (state.actionBuildEvents != null) metrics.push(`动作事件 ${state.actionBuildEvents}`);
  return metrics.join('，');
}

function selectedFileKey(file){
  return `${file.name}:${file.size}:${file.lastModified}`;
}

function updateStartBuildButton(){
  els.startBuildBtn.textContent = isComparePage() ? '开始模型对比构建' : '开始构建本体';
  els.startBuildBtn.disabled = uploadControlsLocked || !selectedFiles.length;
}

function updateSelectedFiles(files){
  const unique = new Map();
  Array.from(files || []).forEach(file => unique.set(selectedFileKey(file), file));
  selectedFiles = Array.from(unique.values());
  updateStartBuildButton();
  renderSelectedFileList();
  els.jobMeta.textContent = formatFileSelection(selectedFiles);
}

function removeSelectedFile(index){
  selectedFiles.splice(index, 1);
  updateStartBuildButton();
  renderSelectedFileList();
  els.jobMeta.textContent = formatFileSelection(selectedFiles);
}

function clearSelectedFiles(){
  selectedFiles = [];
  if (els.fileInput) els.fileInput.value = '';
  updateStartBuildButton();
  renderSelectedFileList();
  els.jobMeta.textContent = formatFileSelection(selectedFiles);
}

function renderSelectedFileList(){
  const count = selectedFiles.length;
  els.fileListWrap.classList.toggle('show', count > 0);
  els.fileListWrap.open = count === 1 ? true : els.fileListWrap.open && count > 0;
  els.fileListSummary.textContent = formatFileSelection(selectedFiles);
  els.fileList.innerHTML = '';
  selectedFiles.forEach((file, index) => {
    const row = document.createElement('div');
    row.className = 'fileRow';
    const name = document.createElement('div');
    name.className = 'fileName';
    name.textContent = file.name;
    name.title = file.name;
    const size = document.createElement('div');
    size.className = 'fileSize';
    size.textContent = formatFileSize(file.size);
    const remove = document.createElement('button');
    remove.className = 'fileRemove';
    remove.type = 'button';
    remove.setAttribute('aria-label', `删除 ${file.name}`);
    remove.textContent = '×';
    remove.onclick = event => {
      event.preventDefault();
      event.stopPropagation();
      removeSelectedFile(index);
    };
    row.append(name, size, remove);
    els.fileList.appendChild(row);
  });
}

async function loadGraph(jobId, options={}){
  const announce = options.announce !== false;
  const data = await graphDataForJob(jobId, currentGraphView);
  renderGraph(data);
  graphReady = true;
  if (!announce) return;
  const actionLabel = options.actionLabel || '本体构建完成';
  const body = addAiContainer();
  await renderAnswer(body, {
    summary:`${actionLabel}：已加载${graphViewLabel()} ${data.nodes?.length || 0} 个节点、${data.edges?.length || 0} 条关系。可用左下角开关切换本体画布、二维实例和三维实例。`
  });
}

async function askQuestion(question){
  if (!currentJobId){
    renderError('请先上传文档构建本体，或上传已有本体。');
    return;
  }
  setBuildPanelOpen(false);
  addUser(question);
  const body = addAiContainer();
  const waiting = document.createElement('div');
  waiting.className = 'plan';
  waiting.innerHTML = '<div class="plan-head"><span>正在调用本体问答模型...</span></div>';
  body.appendChild(waiting);
  els.send.disabled = true;
  setStatus('大模型推理中', 'warn');
  try{
    let data;
    if (isMockJob(currentJobId)){
      await sleep(260);
      data = mockQaResponse(currentGraphView);
    }else{
      const res = await fetch(apiUrl(`/ontology/jobs/${currentJobId}/question`), {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({
          question,
          backend: els.qaBackendSelect?.value || els.backendSelect.value || null,
          graphView: currentGraphView,
          contextMode: buildOptions.qaContextMode,
          fallbackEnabled: buildOptions.qaFallbackEnabled
        })
      });
      if (!res.ok) throw new Error(await responseError(res));
      data = await res.json();
    }
    waiting.remove();
    await renderPlan(body, data.think || []);
    applyHighlight(data.highlight || {nodes:[], edges:[]});
    await sleep(180);
    await renderAnswer(body, data.answer || {summary:'模型未返回有效答案。'});
    setStatus(isMockJob(currentJobId) ? '演示问答完成 · 可继续体验' : '本体已加载 · 可提问');
  }catch(err){
    waiting.remove();
    renderError(`问答失败：${err.message || err}`);
    setStatus('问答失败', 'bad');
  }finally{
    els.send.disabled = !currentJobId || !graphReady;
    els.q.focus();
  }
}

async function askCompareQuestion(question){
  const readySlots = compareReadySlots();
  if (!readySlots.length){
    compareSlots.forEach(slot => addCompareError(slot, '请先在模型对比页上传文档并完成本体构建。'));
    return;
  }
  setBuildPanelOpen(false);
  readySlots.forEach(slot => addCompareUser(slot, question));
  els.send.disabled = true;
  setStatus('模型对比问答推理中', 'warn');
  const compareSessionId = createSessionId('compare');
  const results = await Promise.allSettled(
    readySlots.map(slot => askCompareSlotQuestion(slot, question, compareSessionId))
  );
  const failed = results.filter(result => result.status === 'rejected').length;
  setStatus(failed ? `模型对比问答完成 · ${failed} 个失败` : '模型对比问答完成 · 可继续提问', failed ? 'warn' : 'ok');
  els.send.disabled = !compareReadySlots().length;
  els.q.focus();
}

async function askCompareSlotQuestion(slot, question, compareSessionId){
  const body = addCompareAiContainer(slot);
  const waiting = document.createElement('div');
  waiting.className = 'plan';
  waiting.innerHTML = '<div class="plan-head"><span>正在调用本体问答模型...</span></div>';
  body.appendChild(waiting);
  setCompareSlotStatus(slot, '推理中', 'warn');
  try{
    let data;
    if (isMockJob(slot.jobId)){
      await sleep(180 + slot.index * 80);
      data = mockQaResponse(currentGraphView, slotBackendLabel(slot));
    }else{
      const res = await fetch(apiUrl(`/ontology/jobs/${slot.jobId}/question`), {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({
          question,
          backend: slotBackendValue(slot) || null,
          compareSessionId,
          compareSlot: slot.index,
          graphView: currentGraphView,
          contextMode: buildOptions.qaContextMode,
          fallbackEnabled: buildOptions.qaFallbackEnabled
        })
      });
      if (!res.ok) throw new Error(await responseError(res));
      data = await res.json();
    }
    waiting.remove();
    await renderPlan(body, data.think || []);
    applyCompareHighlight(slot, data.highlight || {nodes:[], edges:[]});
    await sleep(120);
    await renderAnswer(body, data.answer || {summary:'模型未返回有效答案。'}, slot);
    setCompareSlotStatus(slot, '已就绪');
  }catch(err){
    waiting.remove();
    addCompareError(slot, `问答失败：${err.message || err}`);
    setCompareSlotStatus(slot, '问答失败', 'bad');
    throw err;
  }
}

async function responseError(res){
  try{
    const data = await res.json();
    return data.detail || JSON.stringify(data);
  }catch(_err){
    return await res.text();
  }
}

async function switchGraphView(view){
  if (view !== 'schema' && view !== 'instance') return;
  if (view === currentGraphView){
    updateGraphViewControls();
    return;
  }
  const previousView = currentGraphView;
  currentGraphView = view;
  clearHighlight(false);
  hideInfo();
  updateGraphViewControls();
  refreshSuggestions();
  refreshPageHeader();
  if (isComparePage()){
    const readySlots = compareReadySlots();
    if (!readySlots.length) return;
    els.send.disabled = true;
    setStatus(`正在统一切换到${graphViewLabel()}`, 'warn');
    try{
      await Promise.all(readySlots.map(slot => loadCompareGraph(slot, {announce:false})));
      clearAllCompareHighlights(true);
      setStatus('模型对比本体已加载 · 可提问');
    }catch(err){
      currentGraphView = previousView;
      updateGraphViewControls();
      refreshPageHeader();
      addCompareError(readySlots[0], `视图切换失败：${err.message || err}`);
      setStatus('视图切换失败', 'bad');
    }finally{
      els.send.disabled = !compareReadySlots().length;
    }
    return;
  }
  if (!currentJobId || !graphReady) return;

  els.send.disabled = true;
  setStatus(`正在切换到${graphViewLabel()}`, 'warn');
  try{
    await loadGraph(currentJobId, {announce:false});
    els.q.placeholder = `围绕当前${graphViewLabel()}提问，例如：概括关键对象、追溯某个节点的关系链路`;
    setStatus('本体已加载 · 可提问');
  }catch(err){
    currentGraphView = previousView;
    updateGraphViewControls();
    refreshSuggestions();
    renderError(`视图切换失败：${err.message || err}`);
    setStatus('视图切换失败', 'bad');
  }finally{
    els.send.disabled = !currentJobId || !graphReady;
  }
}

function resizeGraphs(){
  const left = document.querySelector('.left');
  if (Graph && left){
    Graph.width(left.clientWidth);
    Graph.height(left.clientHeight);
  }
  compareSlots.forEach(slot => {
    if (!slot.graph) return;
    const {width, height} = compareGraphSize(slot);
    slot.graph.width(width);
    slot.graph.height(height);
  });
}

function wireEvents(){
  els.pageMainBtn.onclick = () => setPage('main');
  els.pageCompareBtn.onclick = () => setPage('compare');
  els.buildPanelToggle.onclick = () => {
    setBuildPanelOpen(els.buildPanel.classList.contains('is-collapsed'));
  };
  els.buildPanelClose.onclick = () => setBuildPanelOpen(false);
  if (els.chatPanelToggle){
    els.chatPanelToggle.onclick = () => setChatPanelCollapsed(!chatPanelCollapsed);
  }
  els.uploadBtn.onclick = () => {
    els.fileInput.value = '';
    els.fileInput.click();
  };
  els.fileInput.onchange = () => {
    updateSelectedFiles(els.fileInput.files || []);
  };
  ['dragenter', 'dragover'].forEach(eventName => {
    els.uploadBtn.addEventListener(eventName, event => {
      event.preventDefault();
      event.stopPropagation();
      if (!els.uploadBtn.disabled) els.uploadBtn.classList.add('dragging');
    });
  });
  ['dragleave', 'drop'].forEach(eventName => {
    els.uploadBtn.addEventListener(eventName, event => {
      event.preventDefault();
      event.stopPropagation();
      els.uploadBtn.classList.remove('dragging');
    });
  });
  els.uploadBtn.addEventListener('drop', event => {
    if (els.uploadBtn.disabled) return;
    const files = event.dataTransfer?.files || [];
    if (files.length) updateSelectedFiles(files);
  });
  els.clearFilesBtn.onclick = event => {
    event.preventDefault();
    event.stopPropagation();
    clearSelectedFiles();
  };
  els.startBuildBtn.onclick = () => isComparePage()
    ? startCompareBuild(selectedFiles)
    : startBuild(selectedFiles);
  if (els.structuredConfigUploadBtn && els.structuredConfigInput){
    els.structuredConfigUploadBtn.onclick = () => {
      els.structuredConfigInput.value = '';
      els.structuredConfigInput.click();
    };
    els.structuredConfigInput.onchange = () => {
      const file = els.structuredConfigInput.files && els.structuredConfigInput.files[0];
      if (file) uploadStructuredConfig(file);
    };
  }
  els.ontologyUploadBtn.onclick = () => {
    els.ontologyInput.value = '';
    els.ontologyInput.click();
  };
  els.ontologyInput.onchange = () => {
    const file = els.ontologyInput.files && els.ontologyInput.files[0];
    if (file) importOntology(file);
  };
  els.historySelect.onchange = () => {
    updateHistoryActionButtons();
  };
  els.historyLoadBtn.onclick = () => isComparePage()
    ? loadExistingJobForCompare(els.historySelect.value)
    : loadExistingJob(els.historySelect.value);
  if (els.historyReplayBtn){
    els.historyReplayBtn.onclick = () => replayExistingJob(els.historySelect.value);
  }
  els.historyRefreshBtn.onclick = () => loadHistoryJobs();
  els.form.addEventListener('submit', event => {
    event.preventDefault();
    const question = els.q.value.trim();
    if (!question || els.send.disabled) return;
    els.q.value = '';
    if (isComparePage()){
      askCompareQuestion(question);
    }else{
      askQuestion(question);
    }
  });
  els.icClose.onclick = hideInfo;
  els.viewSchema.onclick = () => switchGraphView('schema');
  els.viewInstance.onclick = () => switchGraphView('instance');
  els.btnReset.onclick = () => {
    if (isComparePage()){
      compareSlots.forEach(slot => slot.graph && slot.graph.cameraPosition({x:0,y:0,z:360}, {x:0,y:0,z:0}, 700));
      return;
    }
    if (Graph) Graph.cameraPosition({x:0,y:0,z:520}, {x:0,y:0,z:0}, 900);
  };
  els.btnRotate.onclick = () => {
    rotateOn = !rotateOn;
    syncAutoRotate();
    els.btnRotate.textContent = `自动旋转：${rotateOn ? '开' : '关'}`;
    els.btnRotate.classList.toggle('active', rotateOn);
  };
  els.btnLabels.onclick = () => {
    showAllLabels = !showAllLabels;
    els.btnLabels.textContent = `节点标签：${showAllLabels ? '开' : '关'}`;
    els.btnLabels.classList.toggle('active', showAllLabels);
    if (Graph) Graph.nodeThreeObject(Graph.nodeThreeObject());
    compareSlots.forEach(slot => slot.graph && slot.graph.nodeThreeObject(slot.graph.nodeThreeObject()));
  };
  els.btnClear.onclick = () => isComparePage() ? clearAllCompareHighlights(true) : clearHighlight(true);
  els.clearChatBtn.onclick = () => clearChatMessages();
  window.addEventListener('resize', () => {
    resizeGraphs();
  });
  compareSlots.forEach(slot => {
    if (!slot.backendSelect) return;
    slot.backendSelect.onchange = () => {
      updateCompareModelLabels();
      if (!slot.ready && !slot.jobId) setCompareSlotStatus(slot, '等待构建');
    };
  });
}

function welcome(){
  const body = addAiContainer();
  const wrap = document.createElement('div');
  wrap.className = 'ans';
  wrap.innerHTML = '<div class="ans-head">欢迎使用 OntoMind</div><div class="ans-body"><p>请上传待分析文档构建本体，或上传已有本体 JSON / JSONL 直接加载图谱。</p><p style="color:var(--mute);margin-top:4px">本体加载后，所有问题都会基于当前本体发送到后端问答接口，并回传可高亮的节点与关系。</p></div>';
  body.appendChild(wrap);
  refreshSuggestions();
}

wireEvents();
syncConstructionModeControls(false);
initGraph();
renderGraph({ontology:{}, nodes:[], edges:[]});
renderSelectedFileList();
updateCompareModelLabels();
welcome();
setPage('main');
loadProfiles();
loadHistoryJobs();
