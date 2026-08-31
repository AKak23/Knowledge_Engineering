(() => {
  'use strict';

  const WORKING_COPY_KEY = 'lomOntologyWorkbenchWorkingCopyV1';
  const SVG_NS = 'http://www.w3.org/2000/svg';
  const KIND_LABELS = {object:'对象', rule:'规则', action:'行动'};
  const KIND_COLORS = {object:'#5ab0ff', rule:'#c792ea', action:'#ff7aa8'};
  const NODE_SIZES = {object:[26,26], rule:[26,26], action:[26,26]};
  const CANVAS_ZOOM_MIN = .2;
  const CANVAS_ZOOM_MAX = 4;
  const CANVAS_ZOOM_STEP = .15;
  const VIEWPORT_PAN_PADDING_X = 520;
  const VIEWPORT_PAN_PADDING_Y = 420;
  const LABEL_LAYOUT_VERSION = 'readable-edges-v8';

  const workbenchDom = {
    left: document.querySelector('.left'),
    toolbar: document.getElementById('ontologyWorkbenchToolbar'),
    canvas: document.getElementById('ontologyCanvas2d'),
    canvasStage: document.getElementById('ontologyCanvasStage'),
    canvasEdges: document.getElementById('ontologyCanvasEdges'),
    canvasNodes: document.getElementById('ontologyCanvasNodes'),
    canvasEmpty: document.getElementById('ontologyCanvasEmpty'),
    instance: document.getElementById('ontologyInstance2d'),
    instanceStage: document.getElementById('instance2dStage'),
    instanceEdges: document.getElementById('instance2dEdges'),
    instanceNodes: document.getElementById('instance2dNodes'),
    instanceEmpty: document.getElementById('instance2dEmpty'),
    modal: document.getElementById('workbenchModal'),
    form: document.getElementById('workbenchForm'),
    modalBody: document.getElementById('workbenchModalBody'),
    modalTitle: document.getElementById('workbenchModalTitle'),
    modalSubtitle: document.getElementById('workbenchModalSubtitle'),
    modalKicker: document.getElementById('workbenchModalKicker'),
    modalHint: document.getElementById('workbenchModalHint'),
    modalDelete: document.getElementById('workbenchModalDelete'),
    modalClose: document.getElementById('workbenchModalClose'),
    modalCancel: document.getElementById('workbenchModalCancel'),
    buildWorkflow: document.getElementById('ontologyBuildWorkflow'),
    buildWorkflowParking: document.getElementById('ontologyBuildWorkflowParking'),
    toast: document.getElementById('workbenchToast'),
    rulePanel: document.getElementById('ontologyRulePanel'),
    ruleList: document.getElementById('ontologyRuleList'),
    ruleCount: document.getElementById('ontologyRuleCount'),
    rulePanelToggle: document.getElementById('ontologyRulePanelToggle'),
    canvasLegendPanel: document.getElementById('ontologyCanvasLegendPanel'),
    canvasZoomControls: document.getElementById('canvasZoomControls'),
    canvasZoomOut: document.getElementById('canvasZoomOut'),
    canvasZoomValue: document.getElementById('canvasZoomValue'),
    canvasZoomIn: document.getElementById('canvasZoomIn'),
    viewCanvas: document.getElementById('viewSchema'),
    viewCanvas3d: document.getElementById('viewCanvas3d'),
    viewInstance2d: document.getElementById('viewInstance'),
    viewInstance3d: document.getElementById('viewInstance3d')
  };

  if (!workbenchDom.toolbar || !workbenchDom.canvas || !workbenchDom.modal) return;

  let workbenchView = 'canvas';
  let restorableWorkingCopy = loadWorkingCopy();
  let workbenchDraft = emptyDraft();
  let instanceGraph = {ontology:{}, nodes:[], edges:[]};
  let selectedDraftNodeId = null;
  let selectedDraftEdgeId = null;
  let selectedInstanceNodeId = null;
  let relationMode = {active:false, sourceId:null};
  let modalContext = null;
  let pendingNewEdge = null;
  let showNodeDescriptions = true;
  let activeRuleId = null;
  let highlightedInstanceNodeIds = new Set();
  let matchedInstanceNodeIds = new Set();
  let highlightedInstanceEdgeKeys = new Set();
  let toastTimer = null;
  let actionHydrationJobId = null;
  let suppressCanvasClick = false;
  let suppressInstanceClick = false;
  let instanceLayoutCache = {key:'',width:0,height:0,positions:{}};
  let canvasZoom = 1;
  let instanceZoom = 1;
  let pendingBuildMeta = null;
  let canvasLayoutNeedsSpread = true;
  let canvasViewportNeedsFit = false;

  const legacyResetHandler = els.btnReset.onclick;
  const legacyRotateHandler = els.btnRotate.onclick;
  const legacyLabelsHandler = els.btnLabels.onclick;
  const legacyClearHandler = els.btnClear.onclick;

  function workbenchId(prefix){
    return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2,8)}`;
  }

  function emptyDraft(overrides={}){
    return {
      contractVersion:'ontology-workbench-v1',
      layoutVersion:'compact-v2',
      id:null,
      name:'未命名本体',
      description:'',
      status:'draft',
      sourceJobId:null,
      sourceMode:'mixed',
      nodes:[],
      edges:[],
      agents:[],
      dirty:false,
      savedAt:null,
      ...overrides
    };
  }

  function loadWorkingCopy(){
    try{
      const stored = JSON.parse(localStorage.getItem(WORKING_COPY_KEY) || 'null');
      if (!stored || stored.contractVersion !== 'ontology-workbench-v1') return null;
      return normalizeDraft(stored);
    }catch(_error){
      return null;
    }
  }

  function normalizeDraft(value){
    const normalized = emptyDraft({
      ...value,
      nodes:Array.isArray(value?.nodes) ? value.nodes : [],
      edges:Array.isArray(value?.edges) ? value.edges : [],
      agents:Array.isArray(value?.agents) ? value.agents : []
    });
    if (!['compact-v2',LABEL_LAYOUT_VERSION].includes(value?.layoutVersion)){
      normalized.nodes = compactNodePositions(normalized.nodes);
      normalized.layoutVersion = 'compact-v2';
    }
    return normalized;
  }

  function compactNodePositions(nodes){
    if (!nodes.length) return nodes;
    const xs = nodes.map(node => Number(node.position?.x)).filter(Number.isFinite);
    const ys = nodes.map(node => Number(node.position?.y)).filter(Number.isFinite);
    if (xs.length !== nodes.length || ys.length !== nodes.length){
      return nodes.map((node,index) => ({...node,position:{x:310 + (index % 5) * 94,y:150 + Math.floor(index / 5) * 82}}));
    }
    const minX = Math.min(...xs);
    const minY = Math.min(...ys);
    return nodes.map(node => ({
      ...node,
      position:{
        x:310 + (Number(node.position.x) - minX) * .52,
        y:150 + (Number(node.position.y) - minY) * .52
      }
    }));
  }

  function segmentCrosses(a,b,c,d){
    const orient = (p,q,r) => (q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x);
    const abC = orient(a,b,c);
    const abD = orient(a,b,d);
    const cdA = orient(c,d,a);
    const cdB = orient(c,d,b);
    if ([abC,abD,cdA,cdB].some(value => Math.abs(value) < .001)) return false;
    return (abC > 0) !== (abD > 0) && (cdA > 0) !== (cdB > 0);
  }

  function pointSegmentDistance(point,start,end){
    const dx = end.x - start.x;
    const dy = end.y - start.y;
    const lengthSquared = dx * dx + dy * dy;
    if (!lengthSquared) return Math.hypot(point.x - start.x,point.y - start.y);
    const ratio = Math.max(0,Math.min(1,((point.x - start.x) * dx + (point.y - start.y) * dy) / lengthSquared));
    return Math.hypot(point.x - (start.x + dx * ratio),point.y - (start.y + dy * ratio));
  }

  function canvasLayoutScore(state,links){
    let score = 0;
    const kindRank = {object:0,rule:1,action:2};
    const xs = state.map(item => item.x);
    const minX = Math.min(...xs);
    const spanX = Math.max(1,Math.max(...xs) - minX);
    const degrees = state.map(() => 0);
    links.forEach(link => {
      degrees[link.source] += 1;
      degrees[link.target] += 1;
      const source = state[link.source];
      const target = state[link.target];
      const distance = Math.hypot(target.x - source.x,target.y - source.y);
      score += distance * 5;
      const sourceRank = kindRank[source.kind] ?? 0;
      const targetRank = kindRank[target.kind] ?? 0;
      if (targetRank > sourceRank) score += Math.max(0,source.x + 150 - target.x) * 180;
    });
    state.forEach((item,index) => {
      const anchor = item.kind === 'rule' ? .68 : (item.kind === 'action' ? .9 : .3);
      score += Math.abs((item.x - minX) / spanX - anchor) * (degrees[index] + 1) * 4200;
    });
    const overlapArea = (a,b,padding=5) => {
      const width = Math.min(a.right,b.right) - Math.max(a.left,b.left) + padding * 2;
      const height = Math.min(a.bottom,b.bottom) - Math.max(a.top,b.top) + padding * 2;
      return width > 0 && height > 0 ? width * height : 0;
    };
    const nodeBoxes = state.map(item => centeredBox(item.x,item.y + 30,194,96));
    const edgeBoxes = links.map(link => {
      const source = state[link.source];
      const target = state[link.target];
      return centeredBox((source.x + target.x) / 2,(source.y + target.y) / 2,link.width,link.height);
    });
    for (let left = 0; left < nodeBoxes.length; left += 1){
      for (let right = left + 1; right < nodeBoxes.length; right += 1) score += overlapArea(nodeBoxes[left],nodeBoxes[right],7) * 900;
    }
    edgeBoxes.forEach(edgeBox => {
      nodeBoxes.forEach(nodeBox => {score += overlapArea(edgeBox,nodeBox,6) * 760;});
    });
    for (let left = 0; left < edgeBoxes.length; left += 1){
      for (let right = left + 1; right < edgeBoxes.length; right += 1) score += overlapArea(edgeBoxes[left],edgeBoxes[right],6) * 680;
    }
    for (let left = 0; left < links.length; left += 1){
      const first = links[left];
      const firstSource = state[first.source];
      const firstTarget = state[first.target];
      state.forEach((node,nodeIndex) => {
        if (nodeIndex === first.source || nodeIndex === first.target) return;
        const distance = pointSegmentDistance(node,firstSource,firstTarget);
        if (distance < 88) score += (88 - distance) * 180000;
      });
      for (let right = left + 1; right < links.length; right += 1){
        const second = links[right];
        if ([first.source,first.target].includes(second.source) || [first.source,first.target].includes(second.target)) continue;
        if (segmentCrosses(firstSource,firstTarget,state[second.source],state[second.target])) score += 50000000;
      }
    }
    return score;
  }

  function improveCanvasLayoutBySwapping(state,links,passLimit){
    let currentScore = canvasLayoutScore(state,links);
    for (let pass = 0; pass < passLimit; pass += 1){
      let improved = false;
      for (let left = 0; left < state.length; left += 1){
        for (let right = left + 1; right < state.length; right += 1){
          const leftPoint = {x:state[left].x,y:state[left].y};
          state[left].x = state[right].x;
          state[left].y = state[right].y;
          state[right].x = leftPoint.x;
          state[right].y = leftPoint.y;
          const nextScore = canvasLayoutScore(state,links);
          if (nextScore + .01 < currentScore){
            currentScore = nextScore;
            improved = true;
          }else{
            const rightPoint = {x:state[right].x,y:state[right].y};
            state[right].x = state[left].x;
            state[right].y = state[left].y;
            state[left].x = rightPoint.x;
            state[left].y = rightPoint.y;
          }
        }
      }
      if (!improved) break;
    }
  }

  function optimizeCanvasNodeCenters(nodes,edges){
    const nodeCount = nodes.length;
    const columns = Math.max(2,Math.ceil(Math.sqrt(nodeCount * 1.45)));
    const rows = Math.ceil(nodeCount / columns);
    const gapX = 286;
    const gapY = 174;
    const slots = [];
    for (let row = 0; row < rows; row += 1){
      const rowCount = Math.min(columns,nodeCount - row * columns);
      const offset = (columns - rowCount) * gapX / 2;
      for (let column = 0; column < rowCount; column += 1){
        slots.push({x:176 + offset + column * gapX,y:144 + row * gapY});
      }
    }
    const degreeById = Object.fromEntries(nodes.map(node => [String(node.id),0]));
    edges.forEach(edge => {
      if (degreeById[String(edge.source)] !== undefined) degreeById[String(edge.source)] += 1;
      if (degreeById[String(edge.target)] !== undefined) degreeById[String(edge.target)] += 1;
    });
    const kindRank = {object:0,rule:1,action:2};
    const ordered = [...nodes].sort((left,right) => {
      const rankDelta = (kindRank[left.kind] ?? 0) - (kindRank[right.kind] ?? 0);
      if (rankDelta) return rankDelta;
      const degreeDelta = degreeById[String(right.id)] - degreeById[String(left.id)];
      return degreeDelta || String(left.id).localeCompare(String(right.id));
    });
    const maxX = Math.max(...slots.map(slot => slot.x));
    const minX = Math.min(...slots.map(slot => slot.x));
    const centerX = (minX + maxX) / 2;
    const available = [...slots];
    const state = [];
    ordered.forEach(node => {
      const anchor = node.kind === 'rule' ? .68 : (node.kind === 'action' ? .9 : .3);
      const targetX = minX + (maxX - minX) * anchor;
      available.sort((left,right) => {
        const leftCost = Math.abs(left.x - targetX) * 5 + Math.abs(left.x - centerX) / (degreeById[String(node.id)] + 1);
        const rightCost = Math.abs(right.x - targetX) * 5 + Math.abs(right.x - centerX) / (degreeById[String(node.id)] + 1);
        return leftCost - rightCost || left.y - right.y || left.x - right.x;
      });
      const slot = available.shift();
      state.push({id:String(node.id),kind:node.kind,x:slot.x,y:slot.y});
    });
    const indexById = Object.fromEntries(state.map((item,index) => [item.id,index]));
    const links = edges.map(edge => ({
      source:indexById[String(edge.source)],
      target:indexById[String(edge.target)],
      width:Math.max(54,String(edge.label || '关系').length * 13 + 18),
      height:24,
      id:String(edge.id || `${edge.source}:${edge.target}`)
    })).filter(link => Number.isInteger(link.source) && Number.isInteger(link.target) && link.source !== link.target);
    const passes = nodeCount > 48 ? 2 : 8;
    improveCanvasLayoutBySwapping(state,links,passes);
    return {state,links};
  }

  function spreadNodePositionsForLabels(nodes,edges=[]){
    if (nodes.length < 2) return nodes;
    const {state:centeredState,links:anchoredLinks} = optimizeCanvasNodeCenters(nodes,edges);
    relaxAnchoredLabelCollisions(centeredState,anchoredLinks,{
      nodeWidth:194,
      nodeHeight:96,
      nodeOffsetY:30,
      minX:150,
      maxX:Number.POSITIVE_INFINITY,
      minY:118,
      maxY:Number.POSITIVE_INFINITY
    });
    improveCanvasLayoutBySwapping(centeredState,anchoredLinks,nodes.length > 48 ? 1 : 5);
    const centerById = Object.fromEntries(centeredState.map(item => [item.id,item]));
    nodes.forEach(node => {
      const center = centerById[String(node.id)];
      const size = NODE_SIZES[node.kind] || [26,26];
      node.position = {x:center.x - size[0] / 2,y:center.y - size[1] / 2};
    });
    return nodes;
  }

  function centeredBox(x,y,width,height){
    return {left:x - width / 2,right:x + width / 2,top:y - height / 2,bottom:y + height / 2};
  }

  function persistWorkingCopy(){
    localStorage.setItem(WORKING_COPY_KEY, JSON.stringify(workbenchDraft));
  }

  function markDraftDirty(){
    workbenchDraft.dirty = true;
    persistWorkingCopy();
    syncWorkbenchControls();
  }

  function showWorkbenchToast(message){
    if (!workbenchDom.toast) return;
    window.clearTimeout(toastTimer);
    workbenchDom.toast.textContent = message;
    workbenchDom.toast.classList.add('show');
    toastTimer = window.setTimeout(() => workbenchDom.toast.classList.remove('show'), 2600);
  }

  function semanticKind(node){
    const explicit = node?.workbench?.kind || node?.kind || node?.attrs?.__workbenchKind;
    if (KIND_LABELS[explicit]) return explicit;
    const text = `${node?.type || ''} ${node?.label || ''}`.toLowerCase();
    if (/规则|约束|rule|constraint|policy/.test(text)) return 'rule';
    if (/行动|动作|执行|action|operation/.test(text)) return 'action';
    return 'object';
  }

  function workbenchDetailsForNode(node,kind,attrs){
    const provided = node?.workbench?.details || {};
    const common = {
      code:String(provided.code || attrs['对象编码'] || attrs['规则编码'] || attrs['行动编码'] || attrs.code || node.id),
      description:String(provided.description || attrs['说明'] || attrs.description || ''),
      status:String(provided.status || 'draft'),
      sourceChunks:Array.isArray(provided.sourceChunks) ? provided.sourceChunks.map(String) : []
    };
    if (kind === 'object'){
      const autoAttributes = Object.entries(attrs)
        .filter(([key]) => !['对象编码','规则编码','行动编码','说明','__workbenchKind'].includes(key))
        .map(([name,value]) => ({id:workbenchId('attr'),name,type:inferValueType(value),value:value ?? '',origin:'auto'}));
      return {
        ...common,
        attributes:Array.isArray(provided.attributes) ? provided.attributes.map(item => ({...item})) : autoAttributes,
        dataBindings:Array.isArray(provided.dataBindings) ? provided.dataBindings.map(item => ({...item})) : [],
        instanceIds:Array.isArray(provided.instanceIds) ? [...provided.instanceIds] : [],
        instanceCount:Number(provided.instanceCount ?? provided.instanceIds?.length ?? 0) || 0
      };
    }
    if (kind === 'rule'){
      return {
        ...common,
        logic:String(provided.logic || 'AND'),
        scopeObjectId:String(provided.scopeObjectId || ''),
        conditions:Array.isArray(provided.conditions) ? provided.conditions.map(item => ({...item})) : []
      };
    }
    return {
      ...common,
      actionType:String(provided.actionType || 'manual'),
      triggerRuleId:String(provided.triggerRuleId || ''),
      targetObjectId:String(provided.targetObjectId || ''),
      executor:String(provided.executor || ''),
      actionPlan:String(provided.actionPlan || ''),
      feedback:String(provided.feedback || ''),
      inputObjects:Array.isArray(provided.inputObjects) ? [...provided.inputObjects] : [],
      outputObjects:Array.isArray(provided.outputObjects) ? [...provided.outputObjects] : []
    };
  }

  function draftFromSchemaGraph(data, jobId){
    const graphNodes = data?.nodes || [];
    const kindCounts = {object:0, rule:0, action:0};
    const mappedNodes = graphNodes.map((node, index) => {
      const kind = semanticKind(node);
      const kindIndex = kindCounts[kind]++;
      const generatedPosition = generatedNodePosition(kind, kindIndex, graphNodes.length);
      const position = node?.workbench?.position
        ? {x:Number(node.workbench.position.x ?? generatedPosition.x),y:Number(node.workbench.position.y ?? generatedPosition.y)}
        : generatedPosition;
      const attrs = node.attrs || {};
      return {
        id:String(node.id),
        kind,
        label:String(node.label || node.id),
        position,
        origin:String(node?.workbench?.origin || 'auto'),
        details:workbenchDetailsForNode(node,kind,attrs)
      };
    });
    const validIds = new Set(mappedNodes.map(node => node.id));
    const mappedEdges = (data?.edges || [])
      .filter(edge => validIds.has(String(edge.source)) && validIds.has(String(edge.target)))
      .map((edge,index) => ({
        id:String(edge.id || `generated-edge-${index}-${String(edge.source)}-${String(edge.target)}`),
        source:String(edge.source),
        target:String(edge.target),
        label:String(edge.rel || '关联'),
        cardinality:String(edge?.workbench?.cardinality || 'N:N'),
        description:String(edge?.workbench?.description || ''),
        dataMapping:String(edge?.workbench?.dataMapping || ''),
        origin:String(edge?.workbench?.origin || 'auto')
      }));
    const positionedNodes = compactNodePositions(mappedNodes);
    return emptyDraft({
      id:jobId || null,
      name:String(data?.ontology?.ontologyName || '未命名本体'),
      description:String(data?.ontology?.ontologyDesc || ''),
      sourceJobId:jobId || null,
      nodes:positionedNodes,
      edges:mappedEdges,
      dirty:false,
      savedAt:jobId ? new Date().toISOString() : null
    });
  }

  function refreshDraftProvenanceFromSchema(data){
    const schemaNodeById = Object.fromEntries((data?.nodes || []).map(node => [String(node.id),node]));
    let changed = false;
    workbenchDraft.nodes.forEach(node => {
      const sourceDetails = schemaNodeById[node.id]?.workbench?.details;
      if (!sourceDetails) return;
      const nextSourceChunks = Array.isArray(sourceDetails.sourceChunks) ? sourceDetails.sourceChunks.map(String) : [];
      if (JSON.stringify(node.details?.sourceChunks || []) !== JSON.stringify(nextSourceChunks)){
        node.details = {...node.details,sourceChunks:nextSourceChunks};
        changed = true;
      }
      if (node.kind !== 'object') return;
      const nextInstanceIds = Array.isArray(sourceDetails.instanceIds) ? [...sourceDetails.instanceIds] : [];
      const nextInstanceCount = Number(sourceDetails.instanceCount ?? nextInstanceIds.length) || 0;
      if (JSON.stringify(node.details?.instanceIds || []) !== JSON.stringify(nextInstanceIds) || Number(node.details?.instanceCount || 0) !== nextInstanceCount){
        node.details = {...node.details,instanceIds:nextInstanceIds,instanceCount:nextInstanceCount};
        changed = true;
      }
    });
    if (changed) persistWorkingCopy();
  }

  function generatedNodePosition(kind, index, total){
    if (kind === 'rule') return {x:690 + (index % 2) * 190, y:170 + Math.floor(index / 2) * 150};
    if (kind === 'action') return {x:1040 + (index % 2) * 170, y:190 + Math.floor(index / 2) * 150};
    const columns = total > 14 ? 4 : 3;
    return {x:160 + (index % columns) * 170, y:160 + Math.floor(index / columns) * 150};
  }

  function inferValueType(value){
    if (typeof value === 'number') return 'number';
    if (typeof value === 'boolean') return 'boolean';
    return 'string';
  }

  async function hydrateExistingActions(jobId){
    if (!jobId || jobId.startsWith('mock:') || actionHydrationJobId === jobId) return;
    actionHydrationJobId = jobId;
    try{
      const response = await fetch(apiUrl(`/ontology/jobs/${jobId}/result`));
      if (!response.ok) return;
      const result = await response.json();
      if (workbenchDraft.sourceJobId !== jobId) return;
      let changed = false;
      (result.actions || []).forEach((action,index) => {
        const id = `action:${action.actionId ?? index}`;
        if (workbenchDraft.nodes.some(node => node.id === id)) return;
        const actionNode = {
          id,
          kind:'action',
          label:String(action.actionName || `行动${index + 1}`),
          position:generatedNodePosition('action', workbenchDraft.nodes.filter(node => node.kind === 'action').length, workbenchDraft.nodes.length),
          origin:'auto',
          details:{
            code:String(action.actionCode || id),
            description:String(action.description || ''),
            status:'draft',
            actionType:'ontology',
            triggerRuleId:'',
            targetObjectId:'',
            executor:'',
            actionPlan:String(action.description || ''),
            feedback:'',
            inputObjects:Array.isArray(action.inputObjects) ? action.inputObjects : [],
            outputObjects:Array.isArray(action.outputObjects) ? action.outputObjects : []
          }
        };
        workbenchDraft.nodes.push(actionNode);
        changed = true;
      });
      if (changed){
        canvasLayoutNeedsSpread = true;
        persistWorkingCopy();
        renderCanvas();
      }
    }catch(_error){
      // Existing action hydration is best-effort in the frontend-only phase.
    }
  }

  function handleRenderedGraph(detail){
    const data = detail?.data || {ontology:{},nodes:[],edges:[]};
    const backendView = detail?.backendView || data.ontology?.graphView || 'schema';
    const jobId = detail?.jobId || null;
    if (backendView === 'instance'){
      const previousKey = instanceGraphSignature(instanceGraph.nodes || [],instanceGraph.edges || []);
      instanceGraph = data;
      const nextKey = instanceGraphSignature(instanceGraph.nodes || [],instanceGraph.edges || []);
      renderInstance2d({center:nextKey !== previousKey});
      renderRuleList();
      syncWorkbenchControls();
      return;
    }
    const graphHasNodes = Boolean((data.nodes || []).length);
    if (graphHasNodes && (workbenchDraft.sourceJobId !== jobId || !workbenchDraft.nodes.length)){
      const storedMatchesJob = restorableWorkingCopy?.sourceJobId === jobId;
      workbenchDraft = storedMatchesJob ? normalizeDraft(restorableWorkingCopy) : draftFromSchemaGraph(data, jobId);
      canvasLayoutNeedsSpread = workbenchDraft.layoutVersion !== LABEL_LAYOUT_VERSION;
      if (pendingBuildMeta){
        workbenchDraft.name = pendingBuildMeta.name || workbenchDraft.name;
        workbenchDraft.description = pendingBuildMeta.description || workbenchDraft.description;
        workbenchDraft.sourceMode = pendingBuildMeta.sourceMode || workbenchDraft.sourceMode;
        pendingBuildMeta = null;
      }
      selectedDraftNodeId = null;
      selectedDraftEdgeId = null;
      activeRuleId = null;
      highlightedInstanceNodeIds = new Set();
      matchedInstanceNodeIds = new Set();
      highlightedInstanceEdgeKeys = new Set();
      canvasZoom = 1;
      canvasViewportNeedsFit = true;
      persistWorkingCopy();
      window.requestAnimationFrame(() => centerCanvasViewport({behavior:'auto'}));
    }
    if (graphHasNodes && workbenchDraft.sourceJobId === jobId) refreshDraftProvenanceFromSchema(data);
    renderCanvas();
    syncWorkbenchHeader();
    syncWorkbenchControls();
    hydrateExistingActions(jobId);
  }

  function syncWorkbenchHeader(){
    if (isComparePage()) return;
    const canvasView = workbenchView === 'canvas' || workbenchView === 'canvas3d';
    const viewLabel = workbenchView === 'canvas'
      ? '本体画布'
      : (workbenchView === 'canvas3d' ? '本体画布 3D' : (workbenchView === 'instance2d' ? '本体实例 2D' : '本体实例 3D'));
    const count = canvasView ? workbenchDraft.nodes.length : (instanceGraph.nodes || []).length;
    const edgeCount = canvasView ? workbenchDraft.edges.length : (instanceGraph.edges || []).length;
    const hasOntology = Boolean(workbenchDraft.id || workbenchDraft.sourceJobId || workbenchDraft.nodes.length || workbenchDraft.dirty);
    const ontologyLabel = hasOntology ? (workbenchDraft.name || '未命名本体') : '尚未加载本体';
    els.ontologyName.textContent = `${ontologyLabel} · ${viewLabel}（${count} 节点 / ${edgeCount} 关系）`;
  }

  function syncWorkbenchControls(){
    const compareMode = isComparePage();
    if (compareMode){
      workbenchDom.viewCanvas.textContent = '本体层';
      workbenchDom.viewInstance2d.textContent = '实例层';
      workbenchDom.viewCanvas3d.hidden = true;
      workbenchDom.viewInstance3d.hidden = true;
      return;
    }
    workbenchDom.viewCanvas.textContent = '本体画布';
    workbenchDom.viewCanvas3d.textContent = '本体画布 3D';
    workbenchDom.viewInstance2d.textContent = '本体实例 2D';
    workbenchDom.viewInstance3d.textContent = '本体实例 3D';
    workbenchDom.viewCanvas3d.hidden = false;
    workbenchDom.viewInstance3d.hidden = false;
    workbenchDom.left.dataset.workbenchView = workbenchView;
    workbenchDom.left.classList.toggle('node-descriptions-on',showNodeDescriptions);
    workbenchDom.canvas.hidden = workbenchView !== 'canvas';
    workbenchDom.instance.hidden = workbenchView !== 'instance2d';
    workbenchDom.viewCanvas.classList.toggle('active', workbenchView === 'canvas');
    workbenchDom.viewCanvas3d.classList.toggle('active', workbenchView === 'canvas3d');
    workbenchDom.viewInstance2d.classList.toggle('active', workbenchView === 'instance2d');
    workbenchDom.viewInstance3d.classList.toggle('active', workbenchView === 'instance3d');
    workbenchDom.viewCanvas3d.disabled = false;
    workbenchDom.viewInstance2d.disabled = false;
    workbenchDom.viewInstance3d.disabled = false;
    workbenchDom.toolbar.querySelectorAll('[data-workbench-command="new-object"],[data-workbench-command="new-relation"],[data-workbench-command="new-rule"],[data-workbench-command="new-action"]').forEach(button => {
      button.disabled = workbenchView !== 'canvas';
      button.title = button.disabled ? '仅可在本体画布中编辑本体结构' : '';
    });
    const relationButton = workbenchDom.toolbar.querySelector('[data-workbench-command="new-relation"]');
    relationButton?.classList.toggle('active', relationMode.active);
    const agentButton = workbenchDom.toolbar.querySelector('[data-workbench-command="new-agent"]');
    if (agentButton){
      agentButton.disabled = !workbenchDraft.id || !workbenchDraft.savedAt;
      agentButton.title = agentButton.disabled ? '请先保存当前本体，再创建本体智能体' : '';
    }
    const saveButton = workbenchDom.toolbar.querySelector('[data-workbench-command="save"]');
    saveButton?.classList.toggle('dirty', Boolean(workbenchDraft.dirty));
    els.btnRotate.disabled = workbenchView !== 'canvas3d' && workbenchView !== 'instance3d';
    els.btnLabels.disabled = false;
    els.btnLabels.textContent = `节点描述：${showNodeDescriptions ? '开' : '关'}`;
    els.btnLabels.classList.toggle('active',showNodeDescriptions);
    if (workbenchDom.rulePanel) workbenchDom.rulePanel.hidden = workbenchView === 'canvas' || workbenchView === 'canvas3d';
    if (workbenchDom.canvasLegendPanel) workbenchDom.canvasLegendPanel.hidden = workbenchView !== 'canvas';
    const active2dZoom = workbenchView === 'instance2d' ? instanceZoom : canvasZoom;
    const has2dZoom = workbenchView === 'canvas' || workbenchView === 'instance2d';
    if (workbenchDom.canvasZoomControls){
      workbenchDom.canvasZoomControls.hidden = !has2dZoom;
      workbenchDom.canvasZoomControls.setAttribute('aria-label',workbenchView === 'instance2d' ? '本体实例 2D 缩放' : '本体画布缩放');
    }
    if (workbenchDom.canvasZoomValue) workbenchDom.canvasZoomValue.textContent = `${Math.round(active2dZoom * 100)}%`;
    if (workbenchDom.canvasZoomOut){
      workbenchDom.canvasZoomOut.disabled = active2dZoom <= CANVAS_ZOOM_MIN + .001;
      workbenchDom.canvasZoomOut.setAttribute('aria-label',workbenchView === 'instance2d' ? '缩小本体实例 2D' : '缩小本体画布');
    }
    if (workbenchDom.canvasZoomIn){
      workbenchDom.canvasZoomIn.disabled = active2dZoom >= CANVAS_ZOOM_MAX - .001;
      workbenchDom.canvasZoomIn.setAttribute('aria-label',workbenchView === 'instance2d' ? '放大本体实例 2D' : '放大本体画布');
    }
    syncWorkbenchHeader();
  }

  async function switchWorkbenchView(nextView){
    if (isComparePage()){
      if (nextView === 'canvas') await switchGraphView('schema');
      else await switchGraphView('instance');
      return;
    }
    if (!['canvas','canvas3d','instance2d','instance3d'].includes(nextView)) return;
    workbenchView = nextView;
    cancelRelationMode(false);
    hideInfo();
    syncWorkbenchControls();
    if (nextView === 'canvas' || nextView === 'canvas3d'){
      if (currentJobId && currentGraphView !== 'schema') await switchGraphView('schema');
      if (nextView === 'canvas') renderCanvas();
      else renderCanvas3d();
    }else{
      if (currentJobId && currentGraphView !== 'instance') await switchGraphView('instance');
      if (nextView === 'instance2d') renderInstance2d();
      if (nextView === 'instance3d'){
        syncLegacyLabelState();
        applyRuleHighlightTo3d();
        window.setTimeout(resizeGraphs, 30);
      }
    }
    syncWorkbenchControls();
  }

  function surfacePanPadding(surface){
    return {
      x:Math.max(VIEWPORT_PAN_PADDING_X,(surface?.clientWidth || 0) * .72),
      y:Math.max(VIEWPORT_PAN_PADDING_Y,(surface?.clientHeight || 0) * .64)
    };
  }

  function canvasExtent(){
    const minX = Math.min(0,...workbenchDraft.nodes.map(node => Number(node.position?.x || 0)));
    const minY = Math.min(0,...workbenchDraft.nodes.map(node => Number(node.position?.y || 0)));
    const maxX = Math.max(980,...workbenchDraft.nodes.map(node => Number(node.position?.x || 0) + 150));
    const maxY = Math.max(720,...workbenchDraft.nodes.map(node => Number(node.position?.y || 0) + 130));
    return {minX,minY,maxX,maxY,width:maxX - minX,height:maxY - minY};
  }

  function canvasStageMetrics(zoom=canvasZoom){
    const extent = canvasExtent();
    const padding = surfacePanPadding(workbenchDom.canvas);
    return {
      extent,
      paddingX:padding.x - extent.minX * zoom,
      paddingY:padding.y - extent.minY * zoom,
      width:padding.x * 2 + extent.width * zoom,
      height:padding.y * 2 + extent.height * zoom,
      zoom
    };
  }

  function syncCanvasStageExtent(){
    const metrics = canvasStageMetrics();
    workbenchDom.canvasStage.style.width = `${metrics.width}px`;
    workbenchDom.canvasStage.style.height = `${metrics.height}px`;
    return metrics;
  }

  function canvasStagePoint(point,metrics=canvasStageMetrics()){
    return {x:metrics.paddingX + point.x * metrics.zoom,y:metrics.paddingY + point.y * metrics.zoom};
  }

  function canvasGraphBounds(){
    if (!workbenchDraft.nodes.length){
      const extent = canvasExtent();
      return {minX:0,minY:0,maxX:extent.width,maxY:extent.height};
    }
    const minX = Math.min(...workbenchDraft.nodes.map(node => Number(node.position?.x || 0)));
    const minY = Math.min(...workbenchDraft.nodes.map(node => Number(node.position?.y || 0)));
    const maxX = Math.max(...workbenchDraft.nodes.map(node => Number(node.position?.x || 0) + (NODE_SIZES[node.kind]?.[0] || 26)));
    const maxY = Math.max(...workbenchDraft.nodes.map(node => Number(node.position?.y || 0) + (NODE_SIZES[node.kind]?.[1] || 26)));
    return {minX,minY,maxX,maxY};
  }

  function canvasReadableBounds(){
    if (!workbenchDraft.nodes.length) return canvasGraphBounds();
    const minX = Math.min(...workbenchDraft.nodes.map(node => Number(node.position?.x || 0) - 88));
    const minY = Math.min(...workbenchDraft.nodes.map(node => Number(node.position?.y || 0) - 18));
    const maxX = Math.max(...workbenchDraft.nodes.map(node => Number(node.position?.x || 0) + 114));
    const maxY = Math.max(...workbenchDraft.nodes.map(node => Number(node.position?.y || 0) + 92));
    return {minX,minY,maxX,maxY,width:maxX - minX,height:maxY - minY};
  }

  function fittedCanvasZoom(){
    const bounds = canvasReadableBounds();
    const availableWidth = Math.max(360,(workbenchDom.canvas.clientWidth || 1040) - 110);
    const availableHeight = Math.max(260,(workbenchDom.canvas.clientHeight || 720) - 170);
    const zoom = Math.min(1,availableWidth / Math.max(1,bounds.width),availableHeight / Math.max(1,bounds.height)) * .96;
    return Math.min(1,Math.max(.35,zoom));
  }

  function canvasViewportFocus(){
    return {
      x:Math.max(workbenchDom.canvas.clientWidth / 2,Math.min(workbenchDom.canvas.clientWidth - 120,460)),
      y:workbenchDom.canvas.clientHeight / 2
    };
  }

  function centerCanvasViewport({behavior='smooth'}={}){
    if (!workbenchDraft.nodes.length){
      workbenchDom.canvas.scrollTo({left:0,top:0,behavior});
      return;
    }
    const metrics = canvasStageMetrics();
    const bounds = canvasGraphBounds();
    const focus = canvasViewportFocus();
    const graphCenter = canvasStagePoint({x:(bounds.minX + bounds.maxX) / 2,y:(bounds.minY + bounds.maxY) / 2},metrics);
    workbenchDom.canvas.scrollTo({
      left:Math.max(0,graphCenter.x - focus.x),
      top:Math.max(0,graphCenter.y - focus.y),
      behavior
    });
  }

  function spreadCanvasForDescriptions(){
    if (!canvasLayoutNeedsSpread || !workbenchDraft.nodes.length) return;
    spreadNodePositionsForLabels(workbenchDraft.nodes,workbenchDraft.edges);
    workbenchDraft.layoutVersion = LABEL_LAYOUT_VERSION;
    canvasLayoutNeedsSpread = false;
    persistWorkingCopy();
  }

  function renderCanvas(){
    spreadCanvasForDescriptions();
    if (canvasViewportNeedsFit){
      canvasZoom = fittedCanvasZoom();
      canvasViewportNeedsFit = false;
    }
    const metrics = syncCanvasStageExtent();
    workbenchDom.canvasNodes.innerHTML = '';
    workbenchDom.canvasEmpty.hidden = workbenchDraft.nodes.length > 0;
    workbenchDraft.nodes.forEach(node => {
      const element = document.createElement('article');
      const ruleRelated = false;
      const ruleDimmed = false;
      element.className = `ontologyNode ${node.kind}${selectedDraftNodeId === node.id ? ' selected' : ''}${relationMode.sourceId === node.id ? ' relation-source' : ''}${ruleRelated ? ' rule-related' : ''}${ruleDimmed ? ' dimmed' : ''}`;
      element.dataset.nodeId = node.id;
      const stagePosition = canvasStagePoint({x:Number(node.position?.x || 0),y:Number(node.position?.y || 0)},metrics);
      element.style.left = `${stagePosition.x}px`;
      element.style.top = `${stagePosition.y}px`;
      element.style.transform = `scale(${canvasZoom})`;
      element.style.transformOrigin = '0 0';
      const meta = nodeMetaText(node);
      const description = node.details?.description || meta;
      element.title = `${node.label} · 拖动可自由移动；创建关系请先点击顶部“新建关系”`;
      element.innerHTML = `<div class="ontologyNodeContent"><span class="ontologyNodeKind">${KIND_LABELS[node.kind] || '元素'}</span><strong class="ontologyNodeLabel">${escapeHtml(node.label)}</strong><small class="ontologyNodeMeta">${escapeHtml(description)}</small></div>`;
      bindDraftNodeInteractions(element, node);
      workbenchDom.canvasNodes.appendChild(element);
    });
    window.requestAnimationFrame(() => renderCanvasEdges());
    buildWorkbenchLegend();
    renderRuleList();
  }

  function nodeMetaText(node){
    if (node.kind === 'object'){
      const instances = node.details?.instanceCount || node.details?.instanceIds?.length || 0;
      const chunks = node.details?.sourceChunks?.length || 0;
      return `${instances} 个实例 · ${chunks} 个来源分块`;
    }
    if (node.kind === 'rule') return `${node.details?.conditions?.length || 0} 个条件 · ${node.details?.logic || 'AND'}`;
    return node.details?.actionType === 'api' ? 'API 行动' : (node.details?.actionType === 'task' ? '任务行动' : '业务行动');
  }

  function syncCanvasSelectionClasses(){
    workbenchDom.canvasNodes.querySelectorAll('.ontologyNode').forEach(element => {
      element.classList.toggle('selected', element.dataset.nodeId === selectedDraftNodeId);
    });
    workbenchDom.canvasEdges.querySelectorAll('.ontologyEdgeGroup').forEach(element => {
      element.classList.toggle('selected', element.dataset.edgeId === selectedDraftEdgeId);
    });
  }

  function syncCanvasNodePositions(metrics=canvasStageMetrics()){
    workbenchDom.canvasNodes.querySelectorAll('.ontologyNode').forEach(element => {
      const node = workbenchDraft.nodes.find(item => item.id === element.dataset.nodeId);
      if (!node) return;
      const stagePosition = canvasStagePoint(node.position || {x:0,y:0},metrics);
      element.style.left = `${stagePosition.x}px`;
      element.style.top = `${stagePosition.y}px`;
    });
  }

  function bindDraftNodeInteractions(element, node){
    let dragState = null;
    let dragged = false;
    let clickTimer = null;
    element.addEventListener('pointerdown', event => {
      if (event.button > 0 || relationMode.active) return;
      dragState = {
        pointerId:event.pointerId,
        x:event.clientX,
        y:event.clientY,
        left:Number(node.position?.x || 0),
        top:Number(node.position?.y || 0)
      };
      dragged = false;
      element.setPointerCapture?.(event.pointerId);
      window.addEventListener('pointerup',finishDrag);
      window.addEventListener('pointercancel',finishDrag);
    });
    element.addEventListener('pointermove', event => {
      if (!dragState || event.pointerId !== dragState.pointerId) return;
      const dx = (event.clientX - dragState.x) / canvasZoom;
      const dy = (event.clientY - dragState.y) / canvasZoom;
      if (!dragged && Math.hypot(dx,dy) < 4) return;
      dragged = true;
      const previousMetrics = canvasStageMetrics();
      node.position = {x:dragState.left + dx,y:dragState.top + dy};
      const metrics = syncCanvasStageExtent();
      workbenchDom.canvas.scrollLeft += metrics.paddingX - previousMetrics.paddingX;
      workbenchDom.canvas.scrollTop += metrics.paddingY - previousMetrics.paddingY;
      syncCanvasNodePositions(metrics);
      renderCanvasEdges(metrics);
    });
    const finishDrag = event => {
      if (!dragState || event.pointerId !== dragState.pointerId) return;
      window.removeEventListener('pointerup',finishDrag);
      window.removeEventListener('pointercancel',finishDrag);
      if (element.hasPointerCapture?.(event.pointerId)) element.releasePointerCapture(event.pointerId);
      dragState = null;
      if (dragged) markDraftDirty();
      window.setTimeout(() => {dragged = false;}, 0);
    };
    element.addEventListener('pointerup', finishDrag);
    element.addEventListener('pointercancel', finishDrag);
    element.addEventListener('click', event => {
      event.stopPropagation();
      if (dragged) return;
      if (relationMode.active){
        handleRelationNodeSelection(node);
        return;
      }
      if (event.detail > 1){
        window.clearTimeout(clickTimer);
        return;
      }
      window.clearTimeout(clickTimer);
      clickTimer = window.setTimeout(() => {
        selectedDraftNodeId = node.id;
        selectedDraftEdgeId = null;
        syncCanvasSelectionClasses();
        showDraftNodeInfo(node);
      },220);
    });
    element.addEventListener('dblclick', event => {
      event.preventDefault();
      event.stopPropagation();
      if (relationMode.active) return;
      window.clearTimeout(clickTimer);
      openNodeModal(node.id);
    });
  }

  function beginRelationBetween(source,target){
    if (!source || !target || source.id === target.id) return;
    pendingNewEdge = {source:source.id,target:target.id};
    relationMode = {active:false,sourceId:null};
    if (els.graphHint.textContent.startsWith('新建关系')) els.graphHint.textContent = '';
    renderCanvas();
    syncWorkbenchControls();
    openRelationModal('');
  }

  function nodeCenter(node){
    const size = NODE_SIZES[node.kind] || [26,26];
    return {x:Number(node.position?.x || 0) + size[0] / 2, y:Number(node.position?.y || 0) + size[1] / 2};
  }

  function renderCanvasEdges(providedMetrics=null){
    const metrics = providedMetrics || syncCanvasStageExtent();
    const {width,height} = metrics;
    workbenchDom.canvasEdges.setAttribute('viewBox', `0 0 ${width} ${height}`);
    workbenchDom.canvasEdges.innerHTML = `<defs>
      <marker id="ontologyArrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#6479b0"></path></marker>
      <marker id="ontologyArrowObjectObject" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#8ea1c7"></path></marker>
      <marker id="ontologyArrowObjectRule" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#5ab0ff"></path></marker>
      <marker id="ontologyArrowRuleAction" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#55d98a"></path></marker>
      <marker id="ontologyArrowActionObject" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#ff7aa8"></path></marker>
      <marker id="ontologyArrowOther" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#c792ea"></path></marker>
    </defs>`;
    const nodeById = Object.fromEntries(workbenchDraft.nodes.map(node => [node.id,node]));
    workbenchDraft.edges.forEach(edge => {
      const source = nodeById[edge.source];
      const target = nodeById[edge.target];
      if (!source || !target) return;
      const logicalPoints = straightEdgePoints(source,target);
      const start = canvasStagePoint(logicalPoints.start,metrics);
      const end = canvasStagePoint(logicalPoints.end,metrics);
      const mid = canvasStagePoint(logicalPoints.mid,metrics);
      const ruleRelated = false;
      const ruleDimmed = false;
      const group = document.createElementNS(SVG_NS,'g');
      group.setAttribute('class', `ontologyEdgeGroup ${canvasRelationClass(source,target)}${selectedDraftEdgeId === edge.id ? ' selected' : ''}${ruleRelated ? ' rule-related' : ''}${ruleDimmed ? ' dimmed' : ''}`);
      group.dataset.edgeId = edge.id;
      group.dataset.sourceId = edge.source;
      group.dataset.targetId = edge.target;
      const line = document.createElementNS(SVG_NS,'line');
      line.setAttribute('class','ontologyEdgeLine');
      line.setAttribute('x1',String(start.x));
      line.setAttribute('y1',String(start.y));
      line.setAttribute('x2',String(end.x));
      line.setAttribute('y2',String(end.y));
      const hit = document.createElementNS(SVG_NS,'line');
      hit.setAttribute('class','ontologyEdgeHit');
      hit.setAttribute('x1',String(start.x));
      hit.setAttribute('y1',String(start.y));
      hit.setAttribute('x2',String(end.x));
      hit.setAttribute('y2',String(end.y));
      const labelGroup = document.createElementNS(SVG_NS,'g');
      labelGroup.setAttribute('class','ontologyEdgeLabel');
      const labelWidth = Math.max(54, String(edge.label || '关系').length * 13 + 18);
      labelGroup.setAttribute('transform',`translate(${mid.x},${mid.y}) scale(${canvasZoom})`);
      const rect = document.createElementNS(SVG_NS,'rect');
      rect.setAttribute('x',String(-labelWidth / 2));
      rect.setAttribute('y','-12');
      rect.setAttribute('width',String(labelWidth));
      rect.setAttribute('height','24');
      const textNode = document.createElementNS(SVG_NS,'text');
      textNode.textContent = edge.label || '关系';
      labelGroup.append(rect,textNode);
      group.append(line,hit,labelGroup);
      group.addEventListener('click', event => {
        if (relationMode.active) return;
        event.preventDefault();
        event.stopPropagation();
        selectedDraftEdgeId = edge.id;
        selectedDraftNodeId = null;
        syncCanvasSelectionClasses();
        showDraftEdgeInfo(edge);
        openRelationModal(edge.id);
      });
      workbenchDom.canvasEdges.appendChild(group);
    });
  }

  function canvasRelationClass(source,target){
    const pair = `${source.kind}-${target.kind}`;
    if (pair === 'object-object') return 'relation-object-object';
    if (pair === 'object-rule') return 'relation-object-rule';
    if (pair === 'rule-action') return 'relation-rule-action';
    if (pair === 'action-object') return 'relation-action-object';
    if (pair === 'object-action') return 'relation-object-action';
    return 'relation-other';
  }

  function straightEdgePoints(source,target){
    const sourceCenter = nodeCenter(source);
    const targetCenter = nodeCenter(target);
    const dx = targetCenter.x - sourceCenter.x;
    const dy = targetCenter.y - sourceCenter.y;
    const distance = Math.hypot(dx,dy) || 1;
    const ux = dx / distance;
    const uy = dy / distance;
    const sourceRadius = source.kind === 'rule' ? 11 : 9;
    const targetRadius = target.kind === 'rule' ? 14 : 12;
    const start = {x:sourceCenter.x + ux * sourceRadius,y:sourceCenter.y + uy * sourceRadius};
    const end = {x:targetCenter.x - ux * targetRadius,y:targetCenter.y - uy * targetRadius};
    return {start,end,mid:{x:(start.x + end.x) / 2,y:(start.y + end.y) / 2}};
  }

  function buildWorkbenchLegend(){
    if (workbenchView !== 'canvas') return;
    const counts = {object:0,rule:0,action:0};
    workbenchDraft.nodes.forEach(node => {if (counts[node.kind] !== undefined) counts[node.kind] += 1;});
    els.legendRows.innerHTML = '';
    Object.entries(counts).forEach(([kind,count]) => {
      const row = document.createElement('div');
      row.className = 'leg-row';
      row.innerHTML = `<span class="dot workbenchLegendShape ${kind}" style="background:${KIND_COLORS[kind]};color:${KIND_COLORS[kind]}"></span><span>${KIND_LABELS[kind]}</span><small>${count}</small>`;
      els.legendRows.appendChild(row);
    });
    els.nCount.textContent = String(workbenchDraft.nodes.length);
    els.eCount.textContent = String(workbenchDraft.edges.length);
  }

  function renderRuleList(){
    if (!workbenchDom.ruleList || !workbenchDom.ruleCount) return;
    const rules = workbenchDraft.nodes.filter(node => node.kind === 'rule');
    workbenchDom.ruleCount.textContent = String(rules.length);
    if (!rules.length){
      workbenchDom.ruleList.innerHTML = '<div class="ontologyRuleEmpty">当前本体尚未配置规则<br>可从顶部“新建规则”开始。</div>';
      return;
    }
    workbenchDom.ruleList.innerHTML = '';
    rules.forEach(rule => {
      const detail = rule.details || {};
      const scope = draftNode(detail.scopeObjectId);
      const actions = workbenchDraft.nodes.filter(node => node.kind === 'action' && node.details?.triggerRuleId === rule.id);
      const button = document.createElement('button');
      button.type = 'button';
      button.className = `ontologyRuleItem${activeRuleId === rule.id ? ' active' : ''}`;
      button.dataset.ruleId = rule.id;
      button.innerHTML = `<strong>${escapeHtml(rule.label)}</strong><small>${escapeHtml(scope?.label || '全部对象')} · ${escapeHtml(detail.logic || 'AND')} · ${detail.conditions?.length || 0} 个条件</small><p>${escapeHtml(ruleConditionsText(rule))}</p><em>${actions.length ? `触发 ${actions.length} 个行动` : '尚未关联行动'} · 点击定位关联实例</em>`;
      button.addEventListener('click', () => activateRule(rule.id));
      workbenchDom.ruleList.appendChild(button);
    });
  }

  function ruleConditionsText(rule){
    const conditions = rule.details?.conditions || [];
    if (!conditions.length) return rule.details?.description || '尚未配置判定条件';
    return conditions.map(condition => `${condition.field || '字段'} ${condition.operator || '='} ${condition.value ?? ''}`).join(` ${rule.details?.logic || 'AND'} `);
  }

  async function activateRule(ruleId){
    if (activeRuleId === ruleId){
      clearRuleHighlight(true);
      return;
    }
    const rule = draftNode(ruleId);
    if (!rule || rule.kind !== 'rule') return;
    activeRuleId = ruleId;
    if (instanceGraph.nodes?.length){
      applyRuleInstanceHighlight(rule);
      if (workbenchView === 'instance2d') renderInstance2d();
      if (workbenchView === 'instance3d') applyRuleHighlightTo3d();
      showWorkbenchToast(`已定位“${rule.label}”命中的 ${matchedInstanceNodeIds.size} 个实例，并高亮一跳关联节点`);
    }else{
      renderRuleList();
      showWorkbenchToast(`当前暂无实例数据，已高亮“${rule.label}”关联的本体元素`);
    }
  }

  function applyRuleInstanceHighlight(rule){
    const matches = matchingInstancesForRule(rule);
    matchedInstanceNodeIds = new Set(matches.map(node => String(node.id)));
    highlightedInstanceNodeIds = new Set(matchedInstanceNodeIds);
    highlightedInstanceEdgeKeys = new Set();
    (instanceGraph.edges || []).forEach(edge => {
      const source = String(edge.source);
      const target = String(edge.target);
      if (!matchedInstanceNodeIds.has(source) && !matchedInstanceNodeIds.has(target)) return;
      highlightedInstanceNodeIds.add(source);
      highlightedInstanceNodeIds.add(target);
      highlightedInstanceEdgeKeys.add(workbenchEdgeKey(source,target));
    });
    renderRuleList();
  }

  function matchingInstancesForRule(rule){
    const scopeCandidates = new Set();
    if (rule.details?.scopeObjectId) scopeCandidates.add(rule.details.scopeObjectId);
    workbenchDraft.edges.forEach(edge => {
      if (edge.target === rule.id && draftNode(edge.source)?.kind === 'object') scopeCandidates.add(edge.source);
    });
    const scopeTokens = [...scopeCandidates]
      .flatMap(id => {
        const object = draftNode(id);
        return [id,object?.label,object?.details?.code];
      })
      .filter(Boolean)
      .map(normalizedToken);
    const conditions = rule.details?.conditions || [];
    const logic = rule.details?.logic === 'OR' ? 'OR' : 'AND';
    const allNodes = instanceGraph.nodes || [];
    const scopedNodes = allNodes.filter(node => {
      const typeToken = normalizedToken(node.type || '');
      return !scopeTokens.length || scopeTokens.some(token => token === typeToken || token.includes(typeToken) || typeToken.includes(token));
    });
    const candidates = scopeTokens.length && scopedNodes.length ? scopedNodes : allNodes;
    const matches = candidates.filter(node => {
      if (!conditions.length) return true;
      const results = conditions.map(condition => conditionMatches(node.attrs || {},condition));
      return logic === 'OR' ? results.some(Boolean) : results.every(Boolean);
    });
    return matches.length ? matches : scopedNodes;
  }

  function normalizedToken(value){
    return String(value || '').trim().toLowerCase().replace(/[\s:_-]+/g,'');
  }

  function conditionMatches(attrs,condition){
    const operator = String(condition.operator || '=');
    const actual = attrs[condition.field];
    if (operator === '不为空' || operator === '非空') return actual !== undefined && actual !== null && String(actual).trim() !== '';
    if (operator === '为空' || operator === '空') return actual === undefined || actual === null || String(actual).trim() === '';
    if (actual === undefined || actual === null) return false;
    const expected = String(condition.value ?? '').trim() === '当前日期'
      ? new Date().toISOString().slice(0,10)
      : condition.value;
    const actualText = String(actual).trim();
    const expectedText = String(expected ?? '').trim();
    if (operator === '包含') return actualText.includes(expectedText);
    if (operator === '不包含') return !actualText.includes(expectedText);
    if (operator === '=') return normalizedToken(actualText) === normalizedToken(expectedText);
    if (operator === '≠' || operator === '!=') return normalizedToken(actualText) !== normalizedToken(expectedText);
    const actualNumber = comparableNumber(actualText);
    const expectedNumber = comparableNumber(expectedText);
    const left = actualNumber ?? actualText;
    const right = expectedNumber ?? expectedText;
    if (operator === '>') return left > right;
    if (operator === '≥' || operator === '>=') return left >= right;
    if (operator === '<') return left < right;
    if (operator === '≤' || operator === '<=') return left <= right;
    return false;
  }

  function comparableNumber(value){
    if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return Date.parse(`${value}T00:00:00Z`);
    const number = Number(String(value).replace(/[%,$，\s]/g,''));
    return Number.isFinite(number) ? number : null;
  }

  function clearRuleHighlight(notify=false){
    const hadActiveRule = Boolean(activeRuleId);
    activeRuleId = null;
    highlightedInstanceNodeIds = new Set();
    matchedInstanceNodeIds = new Set();
    highlightedInstanceEdgeKeys = new Set();
    if (workbenchView === 'canvas') renderCanvas();
    if (workbenchView === 'instance2d') renderInstance2d();
    if (workbenchView === 'instance3d' && typeof clearHighlight === 'function') clearHighlight(true);
    renderRuleList();
    if (notify && hadActiveRule) showWorkbenchToast('已清除规则实例高亮');
  }

  function applyRuleHighlightTo3d(){
    if (workbenchView !== 'instance3d' || typeof applyHighlight !== 'function') return;
    if (!activeRuleId){
      if (typeof clearHighlight === 'function') clearHighlight(true);
      return;
    }
    const edges = (instanceGraph.edges || [])
      .filter(edge => highlightedInstanceEdgeKeys.has(workbenchEdgeKey(edge.source,edge.target)))
      .map(edge => [String(edge.source),String(edge.target)]);
    applyHighlight({nodes:[...highlightedInstanceNodeIds],edges,fit:true});
  }

  function draftGraphFor3d(){
    const nodes = workbenchDraft.nodes.map(node => {
      const codeLabel = node.kind === 'rule' ? '规则编码' : (node.kind === 'action' ? '行动编码' : '对象编码');
      const attrs = {
        [codeLabel]:node.details?.code || node.id,
        说明:node.details?.description || '',
        状态:node.details?.status || 'draft'
      };
      if (node.kind === 'object'){
        (node.details?.attributes || []).forEach(attribute => {
          if (attribute.name) attrs[attribute.name] = attribute.value ?? '';
        });
        attrs['实例数量'] = node.details?.instanceCount || node.details?.instanceIds?.length || 0;
      }
      if (node.details?.sourceChunks?.length) attrs['来源分块'] = node.details.sourceChunks.join('、');
      return {
        id:node.id,
        label:node.label,
        type:KIND_LABELS[node.kind] || '本体元素',
        attrs,
        workbench:{kind:node.kind,position:{...node.position},details:{...node.details}}
      };
    });
    const edges = workbenchDraft.edges.map(edge => ({
      id:edge.id,
      source:edge.source,
      target:edge.target,
      rel:edge.label || '关联',
      workbench:{
        cardinality:edge.cardinality || 'N:N',
        description:edge.description || '',
        dataMapping:edge.dataMapping || '',
        origin:edge.origin || 'manual'
      }
    }));
    return {
      ontology:{
        ontologyId:workbenchDraft.id || workbenchDraft.sourceJobId || 'workbench-draft',
        ontologyName:workbenchDraft.name || '未命名本体',
        ontologyDesc:workbenchDraft.description || '',
        graphView:'schema',
        availableViews:['schema','instance'],
        workbench3d:true
      },
      nodes,
      edges
    };
  }

  function renderCanvas3d(){
    if (typeof renderGraph !== 'function') return;
    renderGraph(draftGraphFor3d());
    syncLegacyLabelState();
    window.setTimeout(() => {
      if (typeof resizeGraphs === 'function') resizeGraphs();
    },30);
  }

  function syncLegacyLabelState(){
    try{
      if (typeof showAllLabels !== 'undefined' && showAllLabels !== showNodeDescriptions) legacyLabelsHandler?.();
    }catch(_error){
      // 3D label state is best-effort when the legacy renderer is unavailable.
    }
  }

  function toggleNodeDescriptions(){
    showNodeDescriptions = !showNodeDescriptions;
    workbenchDom.left.classList.toggle('node-descriptions-on',showNodeDescriptions);
    if (workbenchView === 'canvas3d' || workbenchView === 'instance3d') syncLegacyLabelState();
    syncWorkbenchControls();
  }

  function instanceGraphSignature(nodes,edges){
    return `${nodes.map(node => String(node.id)).sort().join('|')}::${edges.map(edge => workbenchEdgeKey(edge.source,edge.target)).sort().join('|')}`;
  }

  function instanceLogicalExtent(){
    const clientWidth = workbenchDom.instance?.clientWidth || 1040;
    const clientHeight = workbenchDom.instance?.clientHeight || 720;
    const nodeCount = Math.max(1,(instanceGraph.nodes || []).length);
    return {
      width:Math.max(1260,clientWidth,620 + Math.ceil(Math.sqrt(nodeCount)) * 165),
      height:Math.max(860,clientHeight,360 + Math.ceil(nodeCount / 4) * 125)
    };
  }

  function instanceStageMetrics(zoom=instanceZoom,positions=instanceLayoutCache.positions){
    const extent = instanceLogicalExtent();
    const padding = surfacePanPadding(workbenchDom.instance);
    const values = Object.values(positions || {});
    const minX = Math.min(0,...values.map(point => Number(point.x || 0) - 100));
    const minY = Math.min(0,...values.map(point => Number(point.y || 0) - 30));
    const maxX = Math.max(extent.width,...values.map(point => Number(point.x || 0) + 100));
    const maxY = Math.max(extent.height,...values.map(point => Number(point.y || 0) + 90));
    return {
      extent,
      paddingX:padding.x - minX * zoom,
      paddingY:padding.y - minY * zoom,
      width:padding.x * 2 + (maxX - minX) * zoom,
      height:padding.y * 2 + (maxY - minY) * zoom,
      zoom
    };
  }

  function syncInstanceStageExtent(positions=instanceLayoutCache.positions){
    const metrics = instanceStageMetrics(instanceZoom,positions);
    workbenchDom.instanceStage.style.width = `${metrics.width}px`;
    workbenchDom.instanceStage.style.height = `${metrics.height}px`;
    return metrics;
  }

  function instanceStagePoint(point,metrics=instanceStageMetrics()){
    return {x:metrics.paddingX + point.x * metrics.zoom,y:metrics.paddingY + point.y * metrics.zoom};
  }

  function instanceViewportFocus(){
    return {x:Math.max(workbenchDom.instance.clientWidth / 2,Math.min(workbenchDom.instance.clientWidth - 110,500)),y:workbenchDom.instance.clientHeight / 2};
  }

  function centerInstanceViewport(positions,{behavior='smooth'}={}){
    const values = Object.values(positions || {});
    if (!values.length){
      workbenchDom.instance.scrollTo({left:0,top:0,behavior});
      return;
    }
    const minX = Math.min(...values.map(point => point.x));
    const minY = Math.min(...values.map(point => point.y));
    const maxX = Math.max(...values.map(point => point.x));
    const maxY = Math.max(...values.map(point => point.y));
    const metrics = instanceStageMetrics(instanceZoom,positions);
    const focus = instanceViewportFocus();
    const center = instanceStagePoint({x:(minX + maxX) / 2,y:(minY + maxY) / 2},metrics);
    workbenchDom.instance.scrollTo({left:Math.max(0,center.x - focus.x),top:Math.max(0,center.y - focus.y),behavior});
  }

  function renderInstance2dEdges(edges,positions,metrics){
    workbenchDom.instanceEdges.innerHTML = '<defs><marker id="instanceArrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#536792"></path></marker></defs>';
    workbenchDom.instanceEdges.setAttribute('viewBox',`0 0 ${metrics.width} ${metrics.height}`);
    edges.forEach(edge => {
      const start = positions[String(edge.source)];
      const end = positions[String(edge.target)];
      if (!start || !end) return;
      const logicalPoints = instanceEdgePoints(start,end);
      const points = {
        start:instanceStagePoint(logicalPoints.start,metrics),
        end:instanceStagePoint(logicalPoints.end,metrics),
        mid:instanceStagePoint(logicalPoints.mid,metrics)
      };
      const key = workbenchEdgeKey(edge.source,edge.target);
      const ruleRelated = highlightedInstanceEdgeKeys.has(key);
      const ruleDimmed = Boolean(activeRuleId) && !ruleRelated;
      const line = document.createElementNS(SVG_NS,'line');
      line.setAttribute('class',`instance2dEdge${ruleRelated ? ' rule-related' : ''}${ruleDimmed ? ' dimmed' : ''}`);
      line.setAttribute('x1',String(points.start.x));
      line.setAttribute('y1',String(points.start.y));
      line.setAttribute('x2',String(points.end.x));
      line.setAttribute('y2',String(points.end.y));
      workbenchDom.instanceEdges.appendChild(line);
      const labelText = String(edge.rel || '关系');
      const labelWidth = Math.max(46,labelText.length * 10 + 16);
      const label = document.createElementNS(SVG_NS,'g');
      label.setAttribute('class',`instance2dEdgeLabel${ruleRelated ? ' rule-related' : ''}`);
      label.setAttribute('transform',`translate(${points.mid.x},${points.mid.y}) scale(${instanceZoom})`);
      const labelBackground = document.createElementNS(SVG_NS,'rect');
      labelBackground.setAttribute('x',String(-labelWidth / 2));
      labelBackground.setAttribute('y','-10');
      labelBackground.setAttribute('width',String(labelWidth));
      labelBackground.setAttribute('height','20');
      const labelValue = document.createElementNS(SVG_NS,'text');
      labelValue.textContent = labelText;
      label.append(labelBackground,labelValue);
      workbenchDom.instanceEdges.appendChild(label);
    });
  }

  function syncInstance2dNodePositions(positions,metrics){
    workbenchDom.instanceNodes.querySelectorAll('.instance2dNode').forEach(element => {
      const position = positions[element.dataset.instanceNodeId];
      if (!position) return;
      const stagePosition = instanceStagePoint(position,metrics);
      element.style.left = `${stagePosition.x - 9 * instanceZoom}px`;
      element.style.top = `${stagePosition.y - 9 * instanceZoom}px`;
    });
  }

  function renderInstance2d({center=false}={}){
    const nodes = instanceGraph.nodes || [];
    const edges = instanceGraph.edges || [];
    workbenchDom.instanceNodes.innerHTML = '';
    workbenchDom.instanceEmpty.hidden = nodes.length > 0;
    const {width,height} = instanceLogicalExtent();
    const positions = instancePositions(nodes,edges,width,height);
    const metrics = syncInstanceStageExtent(positions);
    const nodeById = Object.fromEntries(nodes.map(node => [String(node.id),node]));
    renderInstance2dEdges(edges,positions,metrics);
    nodes.forEach(node => {
      const position = positions[String(node.id)];
      const stagePosition = instanceStagePoint(position,metrics);
      const element = document.createElement('article');
      const nodeId = String(node.id);
      const ruleRelated = highlightedInstanceNodeIds.has(nodeId);
      const ruleMatch = matchedInstanceNodeIds.has(nodeId);
      const ruleDimmed = Boolean(activeRuleId) && !ruleRelated;
      element.className = `instance2dNode${selectedInstanceNodeId === nodeId ? ' selected' : ''}${ruleRelated ? ' rule-related' : ''}${ruleMatch ? ' rule-match' : ''}${ruleDimmed ? ' dimmed' : ''}`;
      element.style.left = `${stagePosition.x - 9 * instanceZoom}px`;
      element.style.top = `${stagePosition.y - 9 * instanceZoom}px`;
      element.style.setProperty('--instance-zoom',String(instanceZoom));
      element.style.setProperty('--node-color',colorForType(node.type || 'Instance'));
      element.dataset.instanceNodeId = nodeId;
      element.title = `${String(node.label || node.id)} · ${String(node.type || '实例')} · 拖动可调整二维位置`;
      element.innerHTML = `<div class="instance2dNodeContent"><strong>${escapeHtml(String(node.label || node.id))}</strong><small>${escapeHtml(String(node.type || '实例'))}</small></div>`;
      let dragState = null;
      let dragged = false;
      let clickTimer = null;
      element.addEventListener('pointerdown', event => {
        if (event.button > 0) return;
        dragState = {
          pointerId:event.pointerId,
          x:event.clientX,
          y:event.clientY,
          left:position.x,
          top:position.y
        };
        dragged = false;
        element.setPointerCapture?.(event.pointerId);
        window.addEventListener('pointerup',finishDrag);
        window.addEventListener('pointercancel',finishDrag);
      });
      element.addEventListener('pointermove', event => {
        if (!dragState || event.pointerId !== dragState.pointerId) return;
        const dx = (event.clientX - dragState.x) / instanceZoom;
        const dy = (event.clientY - dragState.y) / instanceZoom;
        if (!dragged && Math.hypot(dx,dy) < 4) return;
        dragged = true;
        element.classList.add('dragging');
        const previousMetrics = instanceStageMetrics(instanceZoom,positions);
        position.x = dragState.left + dx;
        position.y = dragState.top + dy;
        const nextMetrics = syncInstanceStageExtent(positions);
        workbenchDom.instance.scrollLeft += nextMetrics.paddingX - previousMetrics.paddingX;
        workbenchDom.instance.scrollTop += nextMetrics.paddingY - previousMetrics.paddingY;
        syncInstance2dNodePositions(positions,nextMetrics);
        renderInstance2dEdges(edges,positions,nextMetrics);
      });
      const finishDrag = event => {
        if (!dragState || event.pointerId !== dragState.pointerId) return;
        window.removeEventListener('pointerup',finishDrag);
        window.removeEventListener('pointercancel',finishDrag);
        if (element.hasPointerCapture?.(event.pointerId)) element.releasePointerCapture(event.pointerId);
        dragState = null;
        element.classList.remove('dragging');
        window.setTimeout(() => {dragged = false;},0);
      };
      element.addEventListener('pointerup',finishDrag);
      element.addEventListener('pointercancel',finishDrag);
      element.addEventListener('click', event => {
        event.stopPropagation();
        if (dragged) return;
        if (event.detail > 1){
          window.clearTimeout(clickTimer);
          return;
        }
        window.clearTimeout(clickTimer);
        clickTimer = window.setTimeout(() => {
          selectedInstanceNodeId = String(node.id);
          workbenchDom.instanceNodes.querySelectorAll('.instance2dNode').forEach(item => {
            item.classList.toggle('selected', item.dataset.instanceNodeId === selectedInstanceNodeId);
          });
          showInstanceNodeInfo(node,nodeById,edges);
        },220);
      });
      element.addEventListener('dblclick', event => {
        event.preventDefault();
        event.stopPropagation();
        if (dragged) return;
        window.clearTimeout(clickTimer);
        openInstanceReadOnlyModal(node,nodeById,edges);
      });
      workbenchDom.instanceNodes.appendChild(element);
    });
    renderRuleList();
    if (workbenchView === 'instance2d'){
      els.nCount.textContent = String(nodes.length);
      els.eCount.textContent = String(edges.length);
    }
    if (center) window.requestAnimationFrame(() => centerInstanceViewport(positions,{behavior:'auto'}));
  }

  function labelBoxOverlap(a,b,padding=5){
    const overlapX = Math.min(a.right,b.right) - Math.max(a.left,b.left) + padding * 2;
    const overlapY = Math.min(a.bottom,b.bottom) - Math.max(a.top,b.top) + padding * 2;
    return {x:overlapX,y:overlapY,area:overlapX > 0 && overlapY > 0 ? overlapX * overlapY : 0};
  }

  function relaxAnchoredLabelCollisions(state,links,{nodeWidth,nodeHeight,nodeOffsetY=0,minX,maxX,minY,maxY}){
    if (!state.length) return;
    const clamp = item => {
      item.x = Math.min(maxX,Math.max(minX,item.x));
      item.y = Math.min(maxY,Math.max(minY,item.y));
    };
    for (let iteration = 0; iteration < 320; iteration += 1){
      const forces = state.map(() => ({x:0,y:0}));
      const nodeBoxes = state.map(item => centeredBox(item.x,item.y + nodeOffsetY,nodeWidth,nodeHeight));
      let collisionCount = 0;
      for (let left = 0; left < state.length; left += 1){
        for (let right = left + 1; right < state.length; right += 1){
          const overlap = labelBoxOverlap(nodeBoxes[left],nodeBoxes[right],7);
          if (!overlap.area) continue;
          collisionCount += 1;
          const dx = state[right].x - state[left].x;
          const dy = state[right].y - state[left].y;
          const direction = (stableLayoutHash(`${state[left].id}:${state[right].id}`) & 1) ? 1 : -1;
          if (overlap.x / nodeWidth < overlap.y / nodeHeight){
            const sign = dx === 0 ? direction : Math.sign(dx);
            const push = Math.min(14,overlap.x * .3 + 1);
            forces[left].x -= sign * push;
            forces[right].x += sign * push;
          }else{
            const sign = dy === 0 ? direction : Math.sign(dy);
            const push = Math.min(14,overlap.y * .3 + 1);
            forces[left].y -= sign * push;
            forces[right].y += sign * push;
          }
        }
      }
      const edgeBoxes = links.map(link => {
        const source = state[link.source];
        const target = state[link.target];
        const x = (source.x + target.x) / 2;
        const y = (source.y + target.y) / 2;
        return {...link,x,y,box:centeredBox(x,y,link.width,link.height)};
      });
      edgeBoxes.forEach(edge => {
        nodeBoxes.forEach((nodeBox,nodeIndex) => {
          const overlap = labelBoxOverlap(edge.box,nodeBox,6);
          if (!overlap.area) return;
          collisionCount += 1;
          const source = state[edge.source];
          const target = state[edge.target];
          if (nodeIndex === edge.source || nodeIndex === edge.target){
            const dx = target.x - source.x;
            const dy = target.y - source.y;
            const distance = Math.max(1,Math.hypot(dx,dy));
            const push = Math.min(13,2 + Math.sqrt(overlap.area) * .08);
            forces[edge.source].x -= dx / distance * push;
            forces[edge.source].y -= dy / distance * push;
            forces[edge.target].x += dx / distance * push;
            forces[edge.target].y += dy / distance * push;
            return;
          }
          let dx = state[nodeIndex].x - edge.x;
          let dy = state[nodeIndex].y + nodeOffsetY - edge.y;
          if (Math.abs(dx) + Math.abs(dy) < .01){
            const angle = (stableLayoutHash(`${edge.id}:${state[nodeIndex].id}`) % 360) * Math.PI / 180;
            dx = Math.cos(angle);
            dy = Math.sin(angle);
          }
          const distance = Math.max(1,Math.hypot(dx,dy));
          const push = Math.min(12,2 + Math.sqrt(overlap.area) * .07);
          const ux = dx / distance;
          const uy = dy / distance;
          forces[nodeIndex].x += ux * push;
          forces[nodeIndex].y += uy * push;
          forces[edge.source].x -= ux * push * .28;
          forces[edge.source].y -= uy * push * .28;
          forces[edge.target].x -= ux * push * .28;
          forces[edge.target].y -= uy * push * .28;
        });
      });
      for (let left = 0; left < edgeBoxes.length; left += 1){
        for (let right = left + 1; right < edgeBoxes.length; right += 1){
          const a = edgeBoxes[left];
          const b = edgeBoxes[right];
          const overlap = labelBoxOverlap(a.box,b.box,6);
          if (!overlap.area) continue;
          collisionCount += 1;
          let dx = b.x - a.x;
          let dy = b.y - a.y;
          if (Math.abs(dx) + Math.abs(dy) < .01){
            const angle = (stableLayoutHash(`${a.id}:${b.id}`) % 360) * Math.PI / 180;
            dx = Math.cos(angle);
            dy = Math.sin(angle);
          }
          const distance = Math.max(1,Math.hypot(dx,dy));
          const push = Math.min(9,2 + Math.sqrt(overlap.area) * .05);
          const ux = dx / distance;
          const uy = dy / distance;
          [a.source,a.target].forEach(index => {
            forces[index].x -= ux * push * .5;
            forces[index].y -= uy * push * .5;
          });
          [b.source,b.target].forEach(index => {
            forces[index].x += ux * push * .5;
            forces[index].y += uy * push * .5;
          });
        }
      }
      if (!collisionCount) break;
      state.forEach((item,index) => {
        const force = forces[index];
        const distance = Math.max(1,Math.hypot(force.x,force.y));
        const scale = distance > 16 ? 16 / distance : 1;
        item.x += force.x * scale;
        item.y += force.y * scale;
        clamp(item);
      });
    }
  }

  function instancePositions(nodes,edges,width,height){
    const visibleWidth = workbenchDom.instance?.clientWidth || width;
    const key = `${nodes.map(node => String(node.id)).sort().join('|')}::${edges.map(edge => workbenchEdgeKey(edge.source,edge.target)).sort().join('|')}::${visibleWidth}`;
    if (instanceLayoutCache.key === key && instanceLayoutCache.width === width && instanceLayoutCache.height === height){
      return instanceLayoutCache.positions;
    }
    const positions = {};
    if (!nodes.length) return positions;
    const center = {x:width / 2,y:height / 2};
    if (nodes.length === 1){
      positions[String(nodes[0].id)] = center;
      instanceLayoutCache = {key,width,height,positions};
      return positions;
    }
    const marginX = Math.min(120,Math.max(70,width * .09));
    const marginY = Math.min(110,Math.max(76,height * .12));
    const usableWidth = Math.max(220,width - marginX * 2);
    const usableHeight = Math.max(220,height - marginY * 2);
    const columns = Math.max(2,Math.ceil(Math.sqrt(nodes.length * usableWidth / usableHeight)));
    const rows = Math.max(1,Math.ceil(nodes.length / columns));
    const ordered = [...nodes].sort((left,right) => String(left.id).localeCompare(String(right.id)));
    const state = ordered.map((node,index) => {
      const column = index % columns;
      const row = Math.floor(index / columns);
      const jitter = stableLayoutHash(node.id);
      return {
        id:String(node.id),
        x:marginX + usableWidth * (column + .5) / columns + ((jitter % 19) - 9),
        y:marginY + usableHeight * (row + .5) / rows + ((Math.floor(jitter / 19) % 19) - 9),
        vx:0,
        vy:0
      };
    });
    const indexById = Object.fromEntries(state.map((item,index) => [item.id,index]));
    const layoutLinks = edges.map(edge => ({
      source:indexById[String(edge.source)],
      target:indexById[String(edge.target)],
      width:Math.max(46,String(edge.rel || '关系').length * 10 + 16),
      height:20,
      id:workbenchEdgeKey(edge.source,edge.target)
    })).filter(link => Number.isInteger(link.source) && Number.isInteger(link.target) && link.source !== link.target);
    const links = layoutLinks.map(link => [link.source,link.target]);
    const iterations = Math.min(360,180 + nodes.length * 8);
    for (let iteration = 0; iteration < iterations; iteration += 1){
      const forces = state.map(() => ({x:0,y:0}));
      for (let left = 0; left < state.length; left += 1){
        for (let right = left + 1; right < state.length; right += 1){
          const dx = state[right].x - state[left].x;
          const dy = state[right].y - state[left].y;
          const distanceSquared = Math.max(64,dx * dx + dy * dy);
          const distance = Math.sqrt(distanceSquared);
          const strength = Math.min(1.7,22000 / distanceSquared);
          const fx = dx / distance * strength;
          const fy = dy / distance * strength;
          forces[left].x -= fx;
          forces[left].y -= fy;
          forces[right].x += fx;
          forces[right].y += fy;
        }
      }
      links.forEach(([sourceIndex,targetIndex]) => {
        const source = state[sourceIndex];
        const target = state[targetIndex];
        const dx = target.x - source.x;
        const dy = target.y - source.y;
        const distance = Math.max(1,Math.hypot(dx,dy));
        const strength = (distance - 218) * .012;
        const fx = dx / distance * strength;
        const fy = dy / distance * strength;
        forces[sourceIndex].x += fx;
        forces[sourceIndex].y += fy;
        forces[targetIndex].x -= fx;
        forces[targetIndex].y -= fy;
      });
      state.forEach((item,index) => {
        forces[index].x += (center.x - item.x) * .0035;
        forces[index].y += (center.y - item.y) * .0035;
        item.vx = (item.vx + forces[index].x) * .76;
        item.vy = (item.vy + forces[index].y) * .76;
        const speed = Math.max(1,Math.hypot(item.vx,item.vy));
        const scale = speed > 6 ? 6 / speed : 1;
        item.x = Math.min(width - marginX,Math.max(marginX,item.x + item.vx * scale));
        item.y = Math.min(height - marginY,Math.max(marginY,item.y + item.vy * scale));
      });
    }
    const leftBound = Math.min(310,Math.max(292,visibleWidth * .32));
    const rightBound = Math.max(leftBound + 520,width - 96);
    const minX = Math.min(...state.map(item => item.x));
    const maxX = Math.max(...state.map(item => item.x));
    const span = Math.max(1,maxX - minX);
    const availableSpan = Math.max(1,rightBound - leftBound);
    state.forEach(item => {
      item.x = span > availableSpan
        ? leftBound + (item.x - minX) * availableSpan / span
        : item.x + Math.min(rightBound - maxX,Math.max(leftBound - minX,0));
    });
    relaxAnchoredLabelCollisions(state,layoutLinks,{
      nodeWidth:174,
      nodeHeight:76,
      nodeOffsetY:34,
      minX:leftBound,
      maxX:rightBound,
      minY:marginY,
      maxY:height - marginY
    });
    state.forEach(item => {positions[item.id] = {x:item.x,y:item.y};});
    instanceLayoutCache = {key,width,height,positions};
    return positions;
  }

  function stableLayoutHash(value){
    let hash = 2166136261;
    for (const character of String(value)){
      hash ^= character.charCodeAt(0);
      hash = Math.imul(hash,16777619);
    }
    return hash >>> 0;
  }

  function instanceEdgePoints(source,target){
    const dx = target.x - source.x;
    const dy = target.y - source.y;
    const distance = Math.max(1,Math.hypot(dx,dy));
    const ux = dx / distance;
    const uy = dy / distance;
    const start = {x:source.x + ux * 11,y:source.y + uy * 11};
    const end = {x:target.x - ux * 14,y:target.y - uy * 14};
    return {start,end,mid:{x:(start.x + end.x) / 2,y:(start.y + end.y) / 2}};
  }

  function workbenchEdgeKey(source,target){
    return `${String(source)}→${String(target)}`;
  }

  function showDraftNodeInfo(node){
    const outbound = workbenchDraft.edges.filter(edge => edge.source === node.id);
    const inbound = workbenchDraft.edges.filter(edge => edge.target === node.id);
    const detail = node.details || {};
    const attrs = node.kind === 'object'
      ? {'来源':originLabel(node.origin),'对象编码':detail.code || '—','对象属性':detail.attributes?.length || 0,'关联实例数':detail.instanceCount || detail.instanceIds?.length || 0,'关联实例':instanceReferences(detail.instanceIds),'来源分块数':detail.sourceChunks?.length || 0,'来源分块':detail.sourceChunks || [],'数据挂载':detail.dataBindings?.length || 0,'状态':statusLabel(detail.status)}
      : (node.kind === 'rule'
        ? {'来源':originLabel(node.origin),'规则编码':detail.code || '—','条件逻辑':detail.logic || 'AND','条件数量':detail.conditions?.length || 0,'来源分块':detail.sourceChunks || [],'状态':statusLabel(detail.status)}
        : {'来源':originLabel(node.origin),'行动编码':detail.code || '—','行动类型':actionTypeLabel(detail.actionType),'触发规则':nodeLabel(detail.triggerRuleId) || '未配置','来源分块':detail.sourceChunks || [],'状态':statusLabel(detail.status)});
    populateInfoCard({badge:KIND_LABELS[node.kind],color:KIND_COLORS[node.kind],title:node.label,sub:`${node.id} · ${outbound.length} 条出向 / ${inbound.length} 条入向`,attrs,outbound,inbound});
  }

  function showDraftEdgeInfo(edge){
    const source = draftNode(edge.source);
    const target = draftNode(edge.target);
    populateInfoCard({
      badge:'关系',color:'#8ef0c5',title:edge.label || '关系',sub:`${source?.label || edge.source} → ${target?.label || edge.target}`,
      attrs:{'来源':originLabel(edge.origin),'基数':edge.cardinality || 'N:N','数据映射':edge.dataMapping || '未配置','说明':edge.description || '—'},
      outbound:[],inbound:[]
    });
  }

  function showInstanceNodeInfo(node,nodeById,edges){
    const outbound = edges.filter(edge => String(edge.source) === String(node.id)).map(edge => ({...edge,source:String(edge.source),target:String(edge.target)}));
    const inbound = edges.filter(edge => String(edge.target) === String(node.id)).map(edge => ({...edge,source:String(edge.source),target:String(edge.target)}));
    populateInfoCard({badge:node.type || '实例',color:colorForType(node.type || 'Instance'),title:node.label || node.id,sub:`${node.id} · ${outbound.length} 条出向 / ${inbound.length} 条入向`,attrs:node.attrs || {},outbound,inbound,nodeById});
  }

  function populateInfoCard({badge,color,title,sub,attrs,outbound,inbound,nodeById=null}){
    els.infoCard.classList.add('show');
    els.icBadge.textContent = badge || '-';
    els.icBadge.style.color = color;
    els.icBadge.style.borderColor = hex2rgba(color,.45);
    els.icTitle.textContent = title || '-';
    els.icSub.textContent = sub || '';
    els.icAttrs.innerHTML = '';
    const entries = Object.entries(attrs || {}).filter(([,value]) => value !== undefined && value !== null && value !== '');
    if (!entries.length){
      const empty = document.createElement('div');
      empty.className = 'v';
      empty.textContent = '无属性';
      els.icAttrs.appendChild(empty);
    }else{
      entries.forEach(([key,value]) => {
        const keyElement = document.createElement('div');
        keyElement.className = 'k';
        keyElement.textContent = key;
        const valueElement = document.createElement('div');
        valueElement.className = 'v';
        valueElement.textContent = Array.isArray(value) ? value.join('、') : String(value);
        els.icAttrs.append(keyElement,valueElement);
      });
    }
    renderWorkbenchEdgeList(els.icOut,outbound || [],'out',nodeById);
    renderWorkbenchEdgeList(els.icIn,inbound || [],'in',nodeById);
  }

  function renderWorkbenchEdgeList(container,edges,direction,nodeById){
    container.innerHTML = '';
    if (!edges.length){
      container.innerHTML = '<span style="color:var(--mute);font-size:12px">（无）</span>';
      return;
    }
    edges.forEach(edge => {
      const peerId = direction === 'out' ? String(edge.target) : String(edge.source);
      const peer = nodeById ? nodeById[peerId] : draftNode(peerId);
      const item = document.createElement('a');
      item.textContent = `${direction === 'out' ? '→' : '←'} ${peer?.label || peerId}`;
      const rel = document.createElement('span');
      rel.className = 'etype';
      rel.textContent = edge.label || edge.rel || '关系';
      item.appendChild(rel);
      item.onclick = () => {
        if (nodeById && peer) showInstanceNodeInfo(peer,nodeById,instanceGraph.edges || []);
        else if (peer){selectedDraftNodeId = peer.id;renderCanvas();showDraftNodeInfo(peer);}
      };
      container.appendChild(item);
    });
  }

  function originLabel(origin){
    return origin === 'manual' ? '人工创建' : (origin === 'corrected' ? '人工修正' : '自动识别');
  }

  function statusLabel(status){
    return ({draft:'草稿',review:'待审核',published:'已发布',disabled:'已停用'})[status] || '草稿';
  }

  function actionTypeLabel(type){
    return ({api:'API 调用',task:'调度任务',manual:'人工行动',ontology:'本体行动'})[type] || '业务行动';
  }

  function instanceReferences(instanceIds){
    return Array.isArray(instanceIds) ? instanceIds.map(id => `inst:${id}`) : [];
  }

  function draftNode(id){return workbenchDraft.nodes.find(node => node.id === id) || null;}
  function draftEdge(id){return workbenchDraft.edges.find(edge => edge.id === id) || null;}
  function nodeLabel(id){return draftNode(id)?.label || '';}

  function toggleRelationMode(){
    if (workbenchView !== 'canvas') return;
    if (relationMode.active){
      cancelRelationMode();
      return;
    }
    relationMode = {active:true,sourceId:null};
    selectedDraftNodeId = null;
    selectedDraftEdgeId = null;
    els.graphHint.textContent = '新建关系：请依次点击起点和终点 · 无需拖动节点 · 点击画布空白或按 Esc 可取消';
    renderCanvas();
    syncWorkbenchControls();
  }

  function cancelRelationMode(notify=true){
    const wasActive = relationMode.active;
    relationMode = {active:false,sourceId:null};
    pendingNewEdge = null;
    if (els.graphHint.textContent.startsWith('新建关系')) els.graphHint.textContent = '';
    renderCanvas();
    syncWorkbenchControls();
    if (notify && wasActive) showWorkbenchToast('已取消新建关系');
  }

  function handleRelationNodeSelection(node){
    if (!relationMode.sourceId){
      relationMode.sourceId = node.id;
      els.graphHint.textContent = `新建关系：已选择“${node.label}”作为起点，请选择终点`;
      renderCanvas();
      return;
    }
    if (relationMode.sourceId === node.id){
      showWorkbenchToast('起点和终点不能是同一个节点');
      return;
    }
    const source = draftNode(relationMode.sourceId);
    if (!source) return;
    beginRelationBetween(source,node);
  }

  function addDraftNode(kind){
    if (workbenchView !== 'canvas') return;
    const defaultLabel = {object:'新建对象',rule:'新建规则',action:'新建行动'}[kind];
    const id = workbenchId(kind);
    const slot = workbenchDraft.nodes.length;
    const columnGap = 94;
    const rowGap = 82;
    const metrics = canvasStageMetrics();
    const viewportLogicalWidth = workbenchDom.canvas.clientWidth / canvasZoom;
    const visibleLeft = (workbenchDom.canvas.scrollLeft - metrics.paddingX) / canvasZoom;
    const visibleTop = (workbenchDom.canvas.scrollTop - metrics.paddingY) / canvasZoom;
    const columns = Math.max(1,Math.min(6,Math.floor(Math.max(columnGap,viewportLogicalWidth - 120) / columnGap)));
    const centerX = Math.max(24,visibleLeft + viewportLogicalWidth * .36 + (slot % columns) * columnGap);
    const centerY = Math.max(112,visibleTop + 170 + Math.floor(slot / columns) * rowGap);
    const node = {
      id,kind,label:defaultLabel,origin:'manual',position:{x:centerX,y:centerY},
      details:kind === 'object'
        ? {code:id,description:'',status:'draft',attributes:[],dataBindings:[]}
        : (kind === 'rule'
          ? {code:id,description:'',status:'draft',logic:'AND',scopeObjectId:'',conditions:[]}
          : {code:id,description:'',status:'draft',actionType:'manual',triggerRuleId:'',targetObjectId:'',executor:'',actionPlan:'',feedback:''})
    };
    workbenchDraft.nodes.push(node);
    canvasLayoutNeedsSpread = true;
    selectedDraftNodeId = id;
    renderCanvas();
    openNodeModal(id,true);
  }

  function resetCanvasViewport(){
    canvasZoom = 1;
    selectedDraftNodeId = null;
    selectedDraftEdgeId = null;
    cancelRelationMode(false);
    hideInfo();
    renderCanvas();
    syncWorkbenchControls();
    window.requestAnimationFrame(() => centerCanvasViewport({behavior:'smooth'}));
  }

  function setCanvasZoom(nextZoom,{clientX,clientY}={}){
    const clampedZoom = Math.min(CANVAS_ZOOM_MAX,Math.max(CANVAS_ZOOM_MIN,Number(nextZoom) || 1));
    if (Math.abs(clampedZoom - canvasZoom) < .001) return;
    const rect = workbenchDom.canvas.getBoundingClientRect();
    const defaultFocus = canvasViewportFocus();
    const focusX = Number.isFinite(clientX) ? clientX - rect.left : defaultFocus.x;
    const focusY = Number.isFinite(clientY) ? clientY - rect.top : defaultFocus.y;
    const oldMetrics = canvasStageMetrics();
    const logicalFocusX = (workbenchDom.canvas.scrollLeft + focusX - oldMetrics.paddingX) / canvasZoom;
    const logicalFocusY = (workbenchDom.canvas.scrollTop + focusY - oldMetrics.paddingY) / canvasZoom;
    canvasZoom = clampedZoom;
    renderCanvas();
    syncWorkbenchControls();
    window.requestAnimationFrame(() => {
      const metrics = canvasStageMetrics();
      workbenchDom.canvas.scrollLeft = Math.max(0,metrics.paddingX + logicalFocusX * canvasZoom - focusX);
      workbenchDom.canvas.scrollTop = Math.max(0,metrics.paddingY + logicalFocusY * canvasZoom - focusY);
    });
  }

  function resetInstanceViewport(){
    instanceZoom = 1;
    selectedInstanceNodeId = null;
    hideInfo();
    renderInstance2d({center:true});
    syncWorkbenchControls();
  }

  function setInstanceZoom(nextZoom,{clientX,clientY}={}){
    const clampedZoom = Math.min(CANVAS_ZOOM_MAX,Math.max(CANVAS_ZOOM_MIN,Number(nextZoom) || 1));
    if (Math.abs(clampedZoom - instanceZoom) < .001) return;
    const rect = workbenchDom.instance.getBoundingClientRect();
    const defaultFocus = instanceViewportFocus();
    const focusX = Number.isFinite(clientX) ? clientX - rect.left : defaultFocus.x;
    const focusY = Number.isFinite(clientY) ? clientY - rect.top : defaultFocus.y;
    const oldMetrics = instanceStageMetrics();
    const logicalFocusX = (workbenchDom.instance.scrollLeft + focusX - oldMetrics.paddingX) / instanceZoom;
    const logicalFocusY = (workbenchDom.instance.scrollTop + focusY - oldMetrics.paddingY) / instanceZoom;
    instanceZoom = clampedZoom;
    renderInstance2d();
    syncWorkbenchControls();
    window.requestAnimationFrame(() => {
      const metrics = instanceStageMetrics();
      workbenchDom.instance.scrollLeft = Math.max(0,metrics.paddingX + logicalFocusX * instanceZoom - focusX);
      workbenchDom.instance.scrollTop = Math.max(0,metrics.paddingY + logicalFocusY * instanceZoom - focusY);
    });
  }

  function bindScrollablePan(surface,{blockedSelector,isDisabled,setSuppress}){
    if (!surface) return;
    let panState = null;
    surface.addEventListener('pointerdown', event => {
      if (event.button !== 0 || isDisabled?.() || event.target.closest?.(blockedSelector)) return;
      panState = {
        pointerId:event.pointerId,
        x:event.clientX,
        y:event.clientY,
        scrollLeft:surface.scrollLeft,
        scrollTop:surface.scrollTop,
        moved:false
      };
      surface.setPointerCapture?.(event.pointerId);
    });
    surface.addEventListener('pointermove', event => {
      if (!panState || event.pointerId !== panState.pointerId) return;
      const dx = event.clientX - panState.x;
      const dy = event.clientY - panState.y;
      if (!panState.moved && Math.hypot(dx,dy) < 4) return;
      panState.moved = true;
      surface.classList.add('panning');
      surface.scrollLeft = panState.scrollLeft - dx;
      surface.scrollTop = panState.scrollTop - dy;
      event.preventDefault();
    });
    const finishPan = event => {
      if (!panState || event.pointerId !== panState.pointerId) return;
      const moved = panState.moved;
      surface.releasePointerCapture?.(event.pointerId);
      panState = null;
      surface.classList.remove('panning');
      if (!moved) return;
      setSuppress(true);
      window.setTimeout(() => setSuppress(false),80);
    };
    surface.addEventListener('pointerup',finishPan);
    surface.addEventListener('pointercancel',finishPan);
  }

  function saveDraft(){
    if (!workbenchDraft.id) workbenchDraft.id = workbenchDraft.sourceJobId || workbenchId('ontology');
    workbenchDraft.savedAt = new Date().toISOString();
    workbenchDraft.dirty = false;
    persistWorkingCopy();
    syncWorkbenchControls();
    showWorkbenchToast(`“${workbenchDraft.name}”已保存到前端本体草稿`);
  }

  function parkBuildWorkflow(){
    if (!workbenchDom.buildWorkflow || !workbenchDom.buildWorkflowParking) return;
    workbenchDom.buildWorkflow.hidden = true;
    workbenchDom.buildWorkflowParking.appendChild(workbenchDom.buildWorkflow);
  }

  function openModal({context,kicker,title,subtitle,body,hint='第一阶段仅保存前端草稿，不修改后端构建结果。',deletable=false,submitLabel='保存配置'}){
    parkBuildWorkflow();
    modalContext = context;
    workbenchDom.modalKicker.textContent = kicker;
    workbenchDom.modalTitle.textContent = title;
    workbenchDom.modalSubtitle.textContent = subtitle;
    workbenchDom.modalHint.textContent = hint;
    workbenchDom.modalBody.innerHTML = body;
    const submitButton = workbenchDom.form.querySelector('button[type="submit"]');
    if (submitButton){
      submitButton.hidden = false;
      submitButton.textContent = submitLabel;
    }
    workbenchDom.modalDelete.hidden = !deletable;
    workbenchDom.modal.classList.toggle('new-ontology',context?.type === 'new-ontology');
    workbenchDom.modal.setAttribute('aria-modal',context?.type === 'new-ontology' ? 'false' : 'true');
    workbenchDom.modal.hidden = false;
    bindModalBodyInteractions();
    window.setTimeout(() => workbenchDom.modalBody.querySelector('input:not([type="hidden"]),textarea,select')?.focus({preventScroll:true}),60);
  }

  function closeModal(){
    if (modalContext?.type === 'node' && modalContext.isNew){
      const transientNodeId = modalContext.nodeId;
      workbenchDraft.nodes = workbenchDraft.nodes.filter(item => item.id !== transientNodeId);
      workbenchDraft.edges = workbenchDraft.edges.filter(edge => edge.source !== transientNodeId && edge.target !== transientNodeId);
      if (selectedDraftNodeId === transientNodeId) selectedDraftNodeId = null;
      renderCanvas();
    }
    parkBuildWorkflow();
    workbenchDom.modal.hidden = true;
    workbenchDom.modal.classList.remove('new-ontology');
    workbenchDom.modal.setAttribute('aria-modal','true');
    modalContext = null;
    if (pendingNewEdge) cancelRelationMode(false);
  }

  function openNewOntologyModal(){
    openModal({
      context:{type:'new-ontology'},kicker:'ONTOLOGY CREATION',title:'新建本体',subtitle:'新建空白本体，或从文档、结构化配置、已有本体和历史结果开始',
      hint:'选择文档后“开始构建本体”会自动可用；底部按钮用于直接创建空白本体。',
      submitLabel:'创建空白本体',
      body:`<div class="workbenchFormGrid">
        ${fieldHtml('本体名称','ontologyName','新建业务本体','full')}
        ${selectFieldHtml('数据形态来源','sourceMode',[['mixed','结构化与非结构化兼容'],['structured','结构化数据'],['unstructured','非结构化文档']],'mixed','full')}
        <div class="workbenchField full" id="ontologyBuildWorkflowHost"></div>
      </div>`
    });
    const host = document.getElementById('ontologyBuildWorkflowHost');
    if (host && workbenchDom.buildWorkflow){
      host.appendChild(workbenchDom.buildWorkflow);
      workbenchDom.buildWorkflow.hidden = false;
    }
  }

  function openNodeModal(nodeId,isNew=false){
    const node = draftNode(nodeId);
    if (!node) return;
    if (node.kind === 'object') openObjectModal(node,isNew);
    if (node.kind === 'rule') openRuleModal(node,isNew);
    if (node.kind === 'action') openActionModal(node,isNew);
  }

  function openObjectModal(node,isNew=false){
    const detail = node.details || {};
    const attributes = detail.attributes || [];
    const bindings = detail.dataBindings || [];
    const sourceChunks = detail.sourceChunks || [];
    const instanceIds = instanceReferences(detail.instanceIds);
    openModal({
      context:{type:'node',nodeId:node.id,kind:'object',isNew},kicker:'OBJECT CONFIGURATION',title:'对象配置',subtitle:'维护对象定义、属性以及自动或人工数据挂载',deletable:!isNew,
      body:`<section class="workbenchSection"><div class="workbenchFormGrid">
        ${fieldHtml('对象名称','name',node.label)}
        ${fieldHtml('对象编码','code',detail.code || node.id)}
        ${selectFieldHtml('状态','status',statusOptions(),detail.status || 'draft')}
        ${readOnlyFieldHtml('创建来源',originLabel(node.origin))}
        ${textareaFieldHtml('对象描述','description',detail.description || '','full')}
      </div></section>
      ${provenanceSectionHtml(sourceChunks,instanceIds)}
      <section class="workbenchSection"><div class="workbenchSectionTitle"><div><span>对象属性</span><p>属性可来自 LOM 自动识别，也可由用户补充和修正。</p></div><button type="button" data-add-row="attribute">＋ 添加属性</button></div>
        <div class="workbenchRows" data-row-list="attribute">${attributes.length ? attributes.map(attributeRowHtml).join('') : emptyRowsHtml('尚未配置对象属性')}</div>
      </section>
      <section class="workbenchSection"><div class="workbenchSectionTitle"><div><span>挂载数据</span><p>同时保留自动识别、人工新增和人工修正的数据来源。</p></div><button type="button" data-add-row="binding">＋ 添加挂载</button></div>
        <div class="workbenchRows" data-row-list="binding">${bindings.length ? bindings.map(bindingRowHtml).join('') : emptyRowsHtml('当前对象尚未挂载数据')}</div>
      </section>`
    });
  }

  function openRuleModal(node,isNew=false){
    const detail = node.details || {};
    const objectOptions = [['','请选择圈定对象'],...workbenchDraft.nodes.filter(item => item.kind === 'object').map(item => [item.id,item.label])];
    const conditions = detail.conditions || [];
    openModal({
      context:{type:'node',nodeId:node.id,kind:'rule',isNew},kicker:'RULE CONFIGURATION',title:'规则配置',subtitle:'第一阶段保存结构化规则条件，第二阶段接入规则判定',deletable:!isNew,
      body:`<section class="workbenchSection"><div class="workbenchFormGrid">
        ${fieldHtml('规则名称','name',node.label)}
        ${fieldHtml('规则编码','code',detail.code || node.id)}
        ${selectFieldHtml('状态','status',statusOptions(),detail.status || 'draft')}
        ${selectFieldHtml('圈定对象','scopeObjectId',objectOptions,detail.scopeObjectId || '')}
        ${selectFieldHtml('条件逻辑','logic',[['AND','AND · 全部满足'],['OR','OR · 满足任一']],detail.logic || 'AND')}
        ${readOnlyFieldHtml('创建来源',originLabel(node.origin))}
        ${textareaFieldHtml('规则说明','description',detail.description || '','full')}
      </div></section>
      ${provenanceSectionHtml(detail.sourceChunks || [])}
      <section class="workbenchSection"><div class="workbenchSectionTitle"><div><span>判定条件</span><p>条件采用结构化字段保存，不直接保存自由 SQL。</p></div><button type="button" data-add-row="condition">＋ 添加条件</button></div>
        <div class="workbenchRows" data-row-list="condition">${conditions.length ? conditions.map(conditionRowHtml).join('') : emptyRowsHtml('尚未配置判定条件')}</div>
      </section>
      <section class="workbenchSection"><div class="workbenchSectionTitle"><div><span>后续行动</span><p>规则与行动之间通过画布关系或行动配置建立触发关系。</p></div></div>
        <div class="workbenchPills">${connectedActionPills(node.id)}</div>
      </section>`
    });
  }

  function openActionModal(node,isNew=false){
    const detail = node.details || {};
    const ruleOptions = [['','请选择触发规则'],...workbenchDraft.nodes.filter(item => item.kind === 'rule').map(item => [item.id,item.label])];
    const objectOptions = [['','请选择目标对象'],...workbenchDraft.nodes.filter(item => item.kind === 'object').map(item => [item.id,item.label])];
    openModal({
      context:{type:'node',nodeId:node.id,kind:'action',isNew},kicker:'ACTION CONFIGURATION',title:'行动配置',subtitle:'定义规则命中后需要采取的业务行动',deletable:!isNew,
      body:`<section class="workbenchSection"><div class="workbenchFormGrid">
        ${fieldHtml('行动名称','name',node.label)}
        ${fieldHtml('行动编码','code',detail.code || node.id)}
        ${selectFieldHtml('状态','status',statusOptions(),detail.status || 'draft')}
        ${selectFieldHtml('行动类型','actionType',[['manual','人工行动'],['api','API 调用'],['task','调度任务'],['ontology','本体行动']],detail.actionType || 'manual')}
        ${selectFieldHtml('触发规则','triggerRuleId',ruleOptions,detail.triggerRuleId || '')}
        ${selectFieldHtml('目标对象','targetObjectId',objectOptions,detail.targetObjectId || '')}
        ${fieldHtml('执行器或调用标识','executor',detail.executor || '','full')}
        ${textareaFieldHtml('行动计划','actionPlan',detail.actionPlan || '','full')}
        ${textareaFieldHtml('反馈回流说明','feedback',detail.feedback || '','full')}
        ${textareaFieldHtml('行动描述','description',detail.description || '','full')}
      </div></section>
      ${provenanceSectionHtml(detail.sourceChunks || [])}`
    });
  }

  function openRelationModal(edgeId){
    const edge = edgeId ? draftEdge(edgeId) : null;
    const source = draftNode(edge?.source || pendingNewEdge?.source);
    const target = draftNode(edge?.target || pendingNewEdge?.target);
    if (!source || !target) return;
    openModal({
      context:{type:'edge',edgeId:edge?.id || null},kicker:'RELATION CONFIGURATION',title:edge ? '关系配置' : '新建关系',subtitle:`${source.label} → ${target.label}`,deletable:Boolean(edge),
      body:`<section class="workbenchSection"><div class="workbenchFormGrid">
        ${fieldHtml('关系名称','name',edge?.label || defaultRelationLabel(source,target))}
        ${fieldHtml('关系编码','code',edge?.code || workbenchId('relation'))}
        ${readOnlyFieldHtml('起点',`${KIND_LABELS[source.kind]} · ${source.label}`)}
        ${readOnlyFieldHtml('终点',`${KIND_LABELS[target.kind]} · ${target.label}`)}
        ${selectFieldHtml('关系基数','cardinality',[['1:1','1:1'],['1:N','1:N'],['N:1','N:1'],['N:N','N:N']],edge?.cardinality || 'N:N')}
        ${readOnlyFieldHtml('创建来源',originLabel(edge?.origin || 'manual'))}
        ${textareaFieldHtml('数据关联或字段映射','dataMapping',edge?.dataMapping || '','full')}
        ${textareaFieldHtml('关系说明','description',edge?.description || '','full')}
      </div></section>`
    });
  }

  function openAgentModal(){
    const existing = workbenchDraft.agents || [];
    openModal({
      context:{type:'agent'},kicker:'ONTOLOGY AGENT',title:'新建本体智能体',subtitle:`当前本体：${workbenchDraft.name}`,
      hint:'第一阶段保存智能体配置；专业推理、发布与外部调用在第三阶段接入。',
      body:`<div class="agentTemplateGrid">
        <button class="agentTemplate active" type="button" data-agent-template="professional"><span>✦</span><div><strong>专业回答推理智能体</strong><small>围绕当前本体提供专业解释、推理与业务建议。</small></div></button>
        <button class="agentTemplate" type="button" data-agent-template="knowledge"><span>◇</span><div><strong>本体知识问答</strong><small>复用现有本体推理问答模型，强调证据和关系路径。</small></div></button>
      </div>
      <input type="hidden" name="agentMode" value="professional">
      <div class="workbenchFormGrid">
        ${fieldHtml('系统名称','name',`agent-${String(workbenchDraft.name || 'ontology').replace(/\s+/g,'-').toLowerCase()}`)}
        ${fieldHtml('显示名称','displayName',`${workbenchDraft.name}专业智能体`)}
        ${selectFieldHtml('状态','status',[['draft','草稿'],['published','发布占位']], 'draft')}
        ${fieldHtml('外部调用标识','invokeName','')}
        ${textareaFieldHtml('智能体描述','description',`基于${workbenchDraft.name}提供专业回答、关系推理和业务解释。`,'full')}
        ${textareaFieldHtml('专业提示词','instructions','优先依据当前本体结构、实例数据和来源证据回答；证据不足时明确说明。','full')}
      </div>
      <section class="workbenchSection"><div class="workbenchSectionTitle"><div><span>当前本体下的智能体</span><p>一个本体可以创建一个或多个专业智能体。</p></div></div>
        <div class="agentExisting">${existing.length ? existing.map(agent => `<span>${escapeHtml(agent.displayName)} · ${statusLabel(agent.status)}</span>`).join('') : '<span>暂无智能体</span>'}</div>
      </section>`
    });
  }

  function openInstanceReadOnlyModal(node,nodeById,edges){
    const outgoing = edges.filter(edge => String(edge.source) === String(node.id));
    const incoming = edges.filter(edge => String(edge.target) === String(node.id));
    const attributes = Object.entries(node.attrs || {}).map(([key,value]) => `<div class="workbenchPill">${escapeHtml(key)}：${escapeHtml(String(value))}</div>`).join('');
    const relationText = [...outgoing.map(edge => `→ ${nodeById[String(edge.target)]?.label || edge.target} · ${edge.rel}`),...incoming.map(edge => `← ${nodeById[String(edge.source)]?.label || edge.source} · ${edge.rel}`)];
    openModal({
      context:{type:'instance-readonly'},kicker:'ONTOLOGY INSTANCE',title:String(node.label || node.id),subtitle:`${node.type || '实例'} · 只读详情`,
      hint:'实例视图只用于浏览。请切换到本体画布修改本体结构。',
      body:`<section class="workbenchSection"><div class="workbenchSectionTitle"><div><span>实例属性</span></div></div><div class="workbenchPills">${attributes || '<span class="workbenchPill">暂无属性</span>'}</div></section>
        <section class="workbenchSection"><div class="workbenchSectionTitle"><div><span>实例关系</span></div></div><div class="workbenchReadOnly">${relationText.length ? relationText.map(escapeHtml).join('<br>') : '暂无实例关系'}</div></section>`
    });
    workbenchDom.form.querySelector('button[type="submit"]').hidden = true;
    workbenchDom.modalDelete.hidden = true;
  }

  function fieldHtml(label,name,value='',extraClass=''){
    return `<label class="workbenchField ${extraClass}"><span>${escapeHtml(label)}</span><input name="${escapeHtml(name)}" value="${escapeHtml(String(value ?? ''))}" autocomplete="off"></label>`;
  }

  function textareaFieldHtml(label,name,value='',extraClass=''){
    return `<label class="workbenchField ${extraClass}"><span>${escapeHtml(label)}</span><textarea name="${escapeHtml(name)}">${escapeHtml(String(value ?? ''))}</textarea></label>`;
  }

  function selectFieldHtml(label,name,options,selected='',extraClass=''){
    return `<label class="workbenchField ${extraClass}"><span>${escapeHtml(label)}</span><select name="${escapeHtml(name)}">${options.map(([value,text]) => `<option value="${escapeHtml(String(value))}"${String(value) === String(selected) ? ' selected' : ''}>${escapeHtml(String(text))}</option>`).join('')}</select></label>`;
  }

  function readOnlyFieldHtml(label,value){
    return `<label class="workbenchField"><span>${escapeHtml(label)}</span><div class="workbenchReadOnly">${escapeHtml(String(value ?? ''))}</div></label>`;
  }

  function provenanceSectionHtml(sourceChunks=[],instanceIds=[]){
    const sourceItems = sourceChunks.length ? sourceChunks.map(item => `<span class="workbenchPill">${escapeHtml(String(item))}</span>`).join('') : '<span class="workbenchPill">暂无来源分块</span>';
    const instanceItems = instanceIds.length ? instanceIds.map(item => `<span class="workbenchPill">${escapeHtml(String(item))}</span>`).join('') : '<span class="workbenchPill">暂无关联实例</span>';
    return `<section class="workbenchSection"><div class="workbenchSectionTitle"><div><span>来源追溯</span><p>自动保留构建该节点的 chunk；对象同时关联实例层节点。</p></div></div>
      <div class="workbenchFormGrid">
        ${readOnlyFieldHtml('来源分块数',sourceChunks.length)}
        ${readOnlyFieldHtml('关联实例数',instanceIds.length)}
      </div>
      <div class="workbenchSectionTitle"><div><span>Chunk ID</span></div></div><div class="workbenchPills">${sourceItems}</div>
      ${instanceIds.length ? `<div class="workbenchSectionTitle"><div><span>实例节点 ID</span></div></div><div class="workbenchPills">${instanceItems}</div>` : ''}
    </section>`;
  }

  function statusOptions(){return [['draft','草稿'],['review','待审核'],['published','已发布'],['disabled','已停用']];}
  function emptyRowsHtml(text){return `<div class="workbenchEmptyRows">${escapeHtml(text)}</div>`;}
  function attributeRowHtml(attribute={}){
    return `<div class="workbenchRow" data-row="attribute" data-row-id="${escapeHtml(attribute.id || workbenchId('attr'))}">
      <input data-field="name" value="${escapeHtml(attribute.name || '')}" placeholder="属性名称">
      <select data-field="type">${[['string','文本'],['number','数值'],['integer','整数'],['boolean','布尔'],['date','日期']].map(([value,text]) => `<option value="${value}"${value === attribute.type ? ' selected' : ''}>${text}</option>`).join('')}</select>
      <input data-field="value" value="${escapeHtml(String(attribute.value ?? ''))}" placeholder="示例值或说明">
      <button type="button" data-remove-row aria-label="删除属性">×</button>
    </div>`;
  }

  function bindingRowHtml(binding={}){
    return `<div class="workbenchRow binding" data-row="binding" data-row-id="${escapeHtml(binding.id || workbenchId('binding'))}">
      <select data-field="kind"><option value="structured"${binding.kind === 'structured' ? ' selected' : ''}>结构化</option><option value="document"${binding.kind === 'document' ? ' selected' : ''}>非结构化</option></select>
      <select data-field="origin"><option value="auto"${binding.origin === 'auto' ? ' selected' : ''}>自动识别</option><option value="manual"${binding.origin === 'manual' ? ' selected' : ''}>人工新增</option><option value="corrected"${binding.origin === 'corrected' ? ' selected' : ''}>人工修正</option></select>
      <input data-field="source" value="${escapeHtml(binding.source || '')}" placeholder="数据源或文件">
      <input data-field="location" value="${escapeHtml(binding.location || '')}" placeholder="表名或片段位置">
      <input data-field="mapping" value="${escapeHtml(binding.mapping || '')}" placeholder="主体字段或片段 ID">
      <button type="button" data-remove-row aria-label="删除挂载">×</button>
    </div>`;
  }

  function conditionRowHtml(condition={}){
    const operators = ['不为空','为空','包含','不包含','>','≥','<','≤','=','≠'];
    return `<div class="workbenchRow condition" data-row="condition" data-row-id="${escapeHtml(condition.id || workbenchId('condition'))}">
      <input data-field="field" value="${escapeHtml(condition.field || '')}" placeholder="属性或字段">
      <select data-field="operator">${operators.map(value => `<option value="${escapeHtml(value)}"${value === condition.operator ? ' selected' : ''}>${escapeHtml(value)}</option>`).join('')}</select>
      <input data-field="value" value="${escapeHtml(String(condition.value ?? ''))}" placeholder="判定值">
      <button type="button" data-remove-row aria-label="删除条件">×</button>
    </div>`;
  }

  function bindModalBodyInteractions(){
    workbenchDom.modalBody.querySelectorAll('[data-add-row]').forEach(button => {
      button.addEventListener('click', () => addModalRow(button.dataset.addRow));
    });
    workbenchDom.modalBody.querySelectorAll('[data-remove-row]').forEach(button => {
      button.addEventListener('click', () => removeModalRow(button));
    });
    workbenchDom.modalBody.querySelectorAll('[data-agent-template]').forEach(button => {
      button.addEventListener('click', () => applyAgentTemplate(button.dataset.agentTemplate));
    });
  }

  function addModalRow(type){
    const list = workbenchDom.modalBody.querySelector(`[data-row-list="${type}"]`);
    if (!list) return;
    list.querySelector('.workbenchEmptyRows')?.remove();
    const template = type === 'attribute' ? attributeRowHtml() : (type === 'binding' ? bindingRowHtml({origin:'manual'}) : conditionRowHtml());
    list.insertAdjacentHTML('beforeend',template);
    const row = list.lastElementChild;
    row.querySelector('[data-remove-row]')?.addEventListener('click', event => removeModalRow(event.currentTarget));
    row.querySelector('input')?.focus({preventScroll:true});
  }

  function removeModalRow(button){
    const row = button.closest('[data-row]');
    const list = row?.parentElement;
    row?.remove();
    if (list && !list.querySelector('[data-row]')) list.innerHTML = emptyRowsHtml('当前列表为空');
  }

  function applyAgentTemplate(mode){
    workbenchDom.modalBody.querySelectorAll('[data-agent-template]').forEach(button => button.classList.toggle('active',button.dataset.agentTemplate === mode));
    const modeInput = workbenchDom.form.elements.agentMode;
    if (modeInput) modeInput.value = mode;
    const displayName = workbenchDom.form.elements.displayName;
    const description = workbenchDom.form.elements.description;
    const instructions = workbenchDom.form.elements.instructions;
    if (mode === 'knowledge'){
      if (displayName) displayName.value = `${workbenchDraft.name}知识问答智能体`;
      if (description) description.value = `基于${workbenchDraft.name}提供可追溯的本体知识问答。`;
      if (instructions) instructions.value = '复用当前本体推理问答模型；优先引用对象、关系、实例和来源证据，证据不足时明确说明。';
    }else{
      if (displayName) displayName.value = `${workbenchDraft.name}专业智能体`;
      if (description) description.value = `基于${workbenchDraft.name}提供专业回答、关系推理和业务解释。`;
      if (instructions) instructions.value = '优先依据当前本体结构、实例数据和来源证据回答；结合专业领域给出推理与建议。';
    }
  }

  function formValue(name){return String(workbenchDom.form.elements[name]?.value || '').trim();}

  function newOntologyFormMeta(){
    return {
      name:formValue('ontologyName') || '未命名本体',
      description:formValue('description'),
      sourceMode:formValue('sourceMode') || 'mixed',
      status:'draft'
    };
  }

  function prepareNewOntologyDraft(meta,{persist=true}={}){
    workbenchDraft = emptyDraft({...meta,dirty:true});
    instanceGraph = {ontology:{},nodes:[],edges:[]};
    canvasLayoutNeedsSpread = true;
    instanceLayoutCache = {key:'',width:0,height:0,positions:{}};
    actionHydrationJobId = null;
    activeRuleId = null;
    selectedDraftNodeId = null;
    selectedDraftEdgeId = null;
    selectedInstanceNodeId = null;
    highlightedInstanceNodeIds = new Set();
    matchedInstanceNodeIds = new Set();
    highlightedInstanceEdgeKeys = new Set();
    canvasZoom = 1;
    instanceZoom = 1;
    currentGraphView = 'schema';
    currentJobId = null;
    graphReady = false;
    els.send.disabled = true;
    if (persist) persistWorkingCopy();
    workbenchView = 'canvas';
    if (typeof renderGraph === 'function') renderGraph({ontology:{},nodes:[],edges:[]});
    renderCanvas();
    syncWorkbenchControls();
    window.requestAnimationFrame(() => workbenchDom.canvas.scrollTo({left:0,top:0}));
  }

  function collectRows(type){
    return [...workbenchDom.modalBody.querySelectorAll(`[data-row="${type}"]`)].map(row => {
      const value = field => row.querySelector(`[data-field="${field}"]`)?.value?.trim() || '';
      if (type === 'attribute') return {id:row.dataset.rowId || workbenchId('attr'),name:value('name'),type:value('type') || 'string',value:value('value'),origin:'manual'};
      if (type === 'binding') return {id:row.dataset.rowId || workbenchId('binding'),kind:value('kind') || 'structured',origin:value('origin') || 'manual',source:value('source'),location:value('location'),mapping:value('mapping')};
      return {id:row.dataset.rowId || workbenchId('condition'),field:value('field'),operator:value('operator') || '=',value:value('value')};
    }).filter(item => type === 'binding' ? (item.source || item.location || item.mapping) : (item.name || item.field));
  }

  function saveModal(event){
    event.preventDefault();
    if (!modalContext || modalContext.type === 'instance-readonly') return;
    if (modalContext.type === 'new-ontology'){
      const meta = newOntologyFormMeta();
      const name = meta.name;
      prepareNewOntologyDraft(meta);
      currentJobId = null;
      graphReady = false;
      currentGraphView = 'schema';
      workbenchDom.canvas.scrollTo({left:0,top:0});
      els.send.disabled = true;
      closeModal();
      showWorkbenchToast(`已创建空白本体“${name}”`);
      return;
    }
    if (modalContext.type === 'node'){
      const node = draftNode(modalContext.nodeId);
      if (!node) return;
      modalContext.isNew = false;
      node.label = formValue('name') || node.label;
      const base = {code:formValue('code') || node.id,description:formValue('description'),status:formValue('status') || 'draft'};
      if (node.kind === 'object') node.details = {...node.details,...base,attributes:collectRows('attribute'),dataBindings:collectRows('binding')};
      if (node.kind === 'rule'){
        node.details = {...node.details,...base,scopeObjectId:formValue('scopeObjectId'),logic:formValue('logic') || 'AND',conditions:collectRows('condition')};
        ensureSemanticEdge(node.details.scopeObjectId,node.id,'供给规则');
      }
      if (node.kind === 'action'){
        node.details = {...node.details,...base,actionType:formValue('actionType') || 'manual',triggerRuleId:formValue('triggerRuleId'),targetObjectId:formValue('targetObjectId'),executor:formValue('executor'),actionPlan:formValue('actionPlan'),feedback:formValue('feedback')};
        ensureSemanticEdge(node.details.triggerRuleId,node.id,'触发行动');
      }
      if (node.origin === 'auto') node.origin = 'corrected';
      markDraftDirty();
      renderCanvas();
      showDraftNodeInfo(node);
      closeModal();
      showWorkbenchToast(`${KIND_LABELS[node.kind]}配置已保存到前端草稿`);
      return;
    }
    if (modalContext.type === 'edge'){
      let edge = modalContext.edgeId ? draftEdge(modalContext.edgeId) : null;
      const isNewEdge = !edge;
      if (!edge && pendingNewEdge){
        edge = {id:workbenchId('edge'),source:pendingNewEdge.source,target:pendingNewEdge.target,origin:'manual'};
        workbenchDraft.edges.push(edge);
      }
      if (!edge) return;
      edge.label = formValue('name') || '关系';
      edge.code = formValue('code') || edge.id;
      edge.cardinality = formValue('cardinality') || 'N:N';
      edge.dataMapping = formValue('dataMapping');
      edge.description = formValue('description');
      if (edge.origin === 'auto') edge.origin = 'corrected';
      pendingNewEdge = null;
      relationMode = {active:false,sourceId:null};
      els.graphHint.textContent = '';
      markDraftDirty();
      selectedDraftEdgeId = edge.id;
      renderCanvas();
      showDraftEdgeInfo(edge);
      closeModal();
      showWorkbenchToast(isNewEdge ? '关系已创建并保存到前端草稿' : '关系配置已更新到前端草稿');
      return;
    }
    if (modalContext.type === 'agent'){
      const agent = {id:workbenchId('agent'),mode:formValue('agentMode') || 'professional',name:formValue('name') || 'ontology-agent',displayName:formValue('displayName') || '未命名智能体',status:formValue('status') || 'draft',invokeName:formValue('invokeName'),description:formValue('description'),instructions:formValue('instructions'),ontologyId:workbenchDraft.id,ontologyName:workbenchDraft.name,createdAt:new Date().toISOString()};
      workbenchDraft.agents.unshift(agent);
      markDraftDirty();
      closeModal();
      showWorkbenchToast(`智能体“${agent.displayName}”已保存为前端配置`);
    }
  }

  function ensureSemanticEdge(sourceId,targetId,label){
    if (!sourceId || !targetId) return;
    const existing = workbenchDraft.edges.find(edge => edge.source === sourceId && edge.target === targetId);
    if (existing) return;
    workbenchDraft.edges.push({id:workbenchId('edge'),source:sourceId,target:targetId,label,cardinality:'N:N',description:'由节点配置自动建立',dataMapping:'',origin:'manual'});
  }

  function defaultRelationLabel(source,target){
    if (source.kind === 'object' && target.kind === 'rule') return '供给规则';
    if (source.kind === 'rule' && target.kind === 'action') return '触发行动';
    return '关联';
  }

  function connectedActionPills(ruleId){
    const actions = workbenchDraft.edges.filter(edge => edge.source === ruleId).map(edge => draftNode(edge.target)).filter(node => node?.kind === 'action');
    return actions.length ? actions.map(node => `<span class="workbenchPill">${escapeHtml(node.label)}</span>`).join('') : '<span class="workbenchPill">暂未连接行动</span>';
  }

  function deleteModalTarget(){
    if (!modalContext) return;
    if (modalContext.type === 'node'){
      const node = draftNode(modalContext.nodeId);
      workbenchDraft.nodes = workbenchDraft.nodes.filter(item => item.id !== modalContext.nodeId);
      workbenchDraft.edges = workbenchDraft.edges.filter(edge => edge.source !== modalContext.nodeId && edge.target !== modalContext.nodeId);
      selectedDraftNodeId = null;
      markDraftDirty();
      renderCanvas();
      closeModal();
      hideInfo();
      showWorkbenchToast(`${KIND_LABELS[node?.kind] || '节点'}已从前端草稿删除`);
    }else if (modalContext.type === 'edge'){
      workbenchDraft.edges = workbenchDraft.edges.filter(edge => edge.id !== modalContext.edgeId);
      selectedDraftEdgeId = null;
      markDraftDirty();
      renderCanvas();
      closeModal();
      hideInfo();
      showWorkbenchToast('关系已从前端草稿删除');
    }
  }

  function handleToolbarCommand(command){
    if (command === 'new-ontology') openNewOntologyModal();
    if (command === 'new-object') addDraftNode('object');
    if (command === 'new-relation') toggleRelationMode();
    if (command === 'new-rule') addDraftNode('rule');
    if (command === 'new-action') addDraftNode('action');
    if (command === 'new-agent') openAgentModal();
    if (command === 'save') saveDraft();
  }

  workbenchDom.toolbar.addEventListener('click', event => {
    const button = event.target.closest('[data-workbench-command]');
    if (!button || button.disabled) return;
    handleToolbarCommand(button.dataset.workbenchCommand);
  });
  workbenchDom.viewCanvas.onclick = () => switchWorkbenchView('canvas');
  workbenchDom.viewCanvas3d.onclick = () => switchWorkbenchView('canvas3d');
  workbenchDom.viewInstance2d.onclick = () => switchWorkbenchView('instance2d');
  workbenchDom.viewInstance3d.onclick = () => switchWorkbenchView('instance3d');
  if (workbenchDom.canvasZoomOut) workbenchDom.canvasZoomOut.onclick = () => workbenchView === 'instance2d'
    ? setInstanceZoom(instanceZoom - CANVAS_ZOOM_STEP)
    : setCanvasZoom(canvasZoom - CANVAS_ZOOM_STEP);
  if (workbenchDom.canvasZoomIn) workbenchDom.canvasZoomIn.onclick = () => workbenchView === 'instance2d'
    ? setInstanceZoom(instanceZoom + CANVAS_ZOOM_STEP)
    : setCanvasZoom(canvasZoom + CANVAS_ZOOM_STEP);
  workbenchDom.canvas.addEventListener('wheel', event => {
    if (workbenchView !== 'canvas') return;
    event.preventDefault();
    setCanvasZoom(canvasZoom * Math.exp(-event.deltaY * .0015),{clientX:event.clientX,clientY:event.clientY});
  },{passive:false});
  workbenchDom.instance.addEventListener('wheel', event => {
    if (workbenchView !== 'instance2d') return;
    event.preventDefault();
    setInstanceZoom(instanceZoom * Math.exp(-event.deltaY * .0015),{clientX:event.clientX,clientY:event.clientY});
  },{passive:false});
  if (workbenchDom.rulePanelToggle){
    workbenchDom.rulePanelToggle.onclick = () => {
      const collapsed = workbenchDom.rulePanel.classList.toggle('collapsed');
      workbenchDom.rulePanelToggle.textContent = collapsed ? '›' : '‹';
      workbenchDom.rulePanelToggle.title = collapsed ? '展开规则列表' : '收起规则列表';
      workbenchDom.rulePanelToggle.setAttribute('aria-label',collapsed ? '展开规则列表' : '收起规则列表');
    };
  }
  workbenchDom.canvas.addEventListener('click', event => {
    if (suppressCanvasClick){
      suppressCanvasClick = false;
      return;
    }
    if (event.target.closest('.ontologyNode') || event.target.closest('.ontologyEdgeGroup')) return;
    selectedDraftNodeId = null;
    selectedDraftEdgeId = null;
    hideInfo();
    if (relationMode.active) cancelRelationMode();
    else renderCanvas();
  });
  workbenchDom.instance.addEventListener('click', event => {
    if (suppressInstanceClick){
      suppressInstanceClick = false;
      return;
    }
    if (event.target.closest('.instance2dNode')) return;
    selectedInstanceNodeId = null;
    hideInfo();
    renderInstance2d();
  });
  bindScrollablePan(workbenchDom.canvas,{
    blockedSelector:'.ontologyNode,.ontologyEdgeGroup',
    isDisabled:() => relationMode.active,
    setSuppress:value => {suppressCanvasClick = value;}
  });
  bindScrollablePan(workbenchDom.instance,{
    blockedSelector:'.instance2dNode',
    isDisabled:() => false,
    setSuppress:value => {suppressInstanceClick = value;}
  });
  workbenchDom.form.addEventListener('submit',saveModal);
  workbenchDom.modalClose.onclick = closeModal;
  workbenchDom.modalCancel.onclick = closeModal;
  workbenchDom.modalDelete.onclick = deleteModalTarget;
  workbenchDom.modal.addEventListener('click', event => {
    const target = event.target.closest?.('button');
    if (modalContext?.type === 'new-ontology' && target?.id === 'startBuildBtn' && !target.disabled){
      pendingBuildMeta = newOntologyFormMeta();
      prepareNewOntologyDraft(pendingBuildMeta);
    }
  },true);
  workbenchDom.modal.addEventListener('click', event => {
    const target = event.target.closest?.('button');
    if (modalContext?.type === 'new-ontology' && ['historyLoadBtn','historyReplayBtn'].includes(target?.id) && !target.disabled){
      window.setTimeout(closeModal,0);
      return;
    }
    if (event.target === workbenchDom.modal) closeModal();
  });
  const structuredConfigInput = document.getElementById('structuredConfigInput');
  structuredConfigInput?.addEventListener('change', () => {
    if (modalContext?.type !== 'new-ontology' || !structuredConfigInput.files?.length) return;
    pendingBuildMeta = newOntologyFormMeta();
    pendingBuildMeta.sourceMode = 'structured';
    prepareNewOntologyDraft(pendingBuildMeta);
  });
  window.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    if (!workbenchDom.modal.hidden) closeModal();
    else if (relationMode.active) cancelRelationMode();
  });
  window.addEventListener('lom:graph-rendered', event => handleRenderedGraph(event.detail));
  window.addEventListener('lom:ontology-import-started', () => {
    if (modalContext?.type === 'new-ontology') closeModal();
  });
  window.addEventListener('lom:ontology-load-started', () => {
    if (modalContext?.type === 'new-ontology') closeModal();
  });
  window.addEventListener('lom:ontology-replay-started', () => {
    if (modalContext?.type === 'new-ontology') closeModal();
  });
  window.addEventListener('lom:ontology-build-completed', () => {
    if (modalContext?.type === 'new-ontology') closeModal();
  });
  window.addEventListener('lom:page-changed', () => syncWorkbenchControls());
  window.addEventListener('resize', () => {
    if (workbenchView === 'canvas') renderCanvas();
    if (workbenchView === 'instance2d') renderInstance2d();
  });

  els.btnReset.onclick = () => {
    if (isComparePage()) return legacyResetHandler?.();
    if (workbenchView === 'canvas') resetCanvasViewport();
    else if (workbenchView === 'instance2d') resetInstanceViewport();
    else legacyResetHandler?.();
  };
  els.btnRotate.onclick = () => (workbenchView === 'canvas3d' || workbenchView === 'instance3d') && legacyRotateHandler?.();
  els.btnLabels.onclick = () => {
    if (isComparePage()) return legacyLabelsHandler?.();
    toggleNodeDescriptions();
  };
  els.btnClear.onclick = () => {
    if (isComparePage()) return legacyClearHandler?.();
    const hadRuleHighlight = Boolean(activeRuleId);
    if (hadRuleHighlight) clearRuleHighlight(false);
    if (workbenchView === 'canvas'){
      selectedDraftNodeId = null;selectedDraftEdgeId = null;cancelRelationMode(false);hideInfo();renderCanvas();
    }else if (workbenchView === 'instance2d'){
      selectedInstanceNodeId = null;hideInfo();renderInstance2d();
    }else legacyClearHandler?.();
    if (hadRuleHighlight) showWorkbenchToast('已清除规则实例高亮');
  };

  if (workbenchDraft.nodes.length) renderCanvas();
  handleRenderedGraph({data:currentGraph,backendView:currentGraphView,jobId:currentJobId});
  syncWorkbenchControls();
})();
