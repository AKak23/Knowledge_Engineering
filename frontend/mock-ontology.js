(function(){
  'use strict';

  // Curated from ontology_construct/outputs/api_jobs/job_b58e2654d68b4ebe819d73f96246af94.
  // The original result contains 50 objects and 1,227 instances. This compact snapshot keeps
  // one production-quality-rework chain so every workbench interaction remains easy to inspect.
  const jobId = 'mock:industrial-manufacturing-demo-v1';
  const sourceJobId = 'job_b58e2654d68b4ebe819d73f96246af94';

  const schemaNodes = [
    {
      id:'object:production-order',label:'生产订单',type:'对象',attrs:{对象编码:'object_39',说明:'下达给产线的正式制造任务，规定产品、数量、交付要求等。'},
      workbench:{kind:'object',origin:'auto',position:{x:260,y:160},details:{
        code:'object_39',description:'下达给产线的正式制造任务，规定产品、数量、交付要求等。',status:'published',
        attributes:[
          {id:'attr:order-code',name:'订单编号',type:'string',value:'0001',origin:'auto'},
          {id:'attr:product',name:'产品',type:'string',value:'数控机床',origin:'corrected'},
          {id:'attr:delivery',name:'交付要求',type:'string',value:'按计划交付',origin:'manual'}
        ],
        dataBindings:[
          {id:'binding:order-quality',kind:'structured',origin:'auto',source:'input_001_quality.log',location:'订单字段',mapping:'订单 → 生产订单.订单编号'},
          {id:'binding:order-mes',kind:'structured',origin:'corrected',source:'input_002_mes_process.log',location:'生产订单字段',mapping:'订单编号 → 生产订单.订单编号'}
        ]
      }}
    },
    {
      id:'object:work-order',label:'生产工单',type:'对象',attrs:{对象编码:'object_34',说明:'将生产订单分解为可执行单元的任务单，绑定具体物料、工序与资源。'},
      workbench:{kind:'object',origin:'auto',position:{x:260,y:370},details:{
        code:'object_34',description:'将生产订单分解为可执行单元的任务单，绑定具体物料、工序与资源。',status:'published',
        attributes:[
          {id:'attr:work-order-code',name:'工单编号',type:'string',value:'0001',origin:'auto'},
          {id:'attr:work-order-state',name:'工单状态',type:'string',value:'执行中',origin:'auto'}
        ],
        dataBindings:[{id:'binding:work-order',kind:'structured',origin:'auto',source:'input_002_mes_process.log',location:'工单字段',mapping:'工单 → 生产工单.工单编号'}]
      }}
    },
    {
      id:'object:process',label:'加工工序',type:'对象',attrs:{对象编码:'object_1',说明:'具有明确输入输出和质量要求的最小可执行制造单元。'},
      workbench:{kind:'object',origin:'auto',position:{x:260,y:580},details:{
        code:'object_1',description:'在工艺路线中定义的、具有明确输入输出和质量要求的最小可执行制造单元。',status:'published',
        attributes:[
          {id:'attr:process-name',name:'工序名称',type:'string',value:'整机终检',origin:'auto'},
          {id:'attr:process-sequence',name:'工序序号',type:'integer',value:'32',origin:'auto'},
          {id:'attr:process-state',name:'执行状态',type:'string',value:'已完成',origin:'corrected'}
        ],
        dataBindings:[
          {id:'binding:process-mes',kind:'structured',origin:'auto',source:'input_002_mes_process.log',location:'工序字段',mapping:'工序 → 加工工序.工序名称'},
          {id:'binding:process-doc',kind:'document',origin:'auto',source:'input_007_整机终检.md',location:'终检工序定义',mapping:'chunk:process-definition'}
        ]
      }}
    },
    {
      id:'object:inspection',label:'检测工序',type:'对象',attrs:{对象编码:'object_2',说明:'以质量判定为目的，执行探伤、尺寸测量等检验活动。'},
      workbench:{kind:'object',origin:'auto',position:{x:260,y:790},details:{
        code:'object_2',description:'以质量判定为目的的加工工序子类，执行探伤、尺寸测量等检验活动。',status:'published',
        attributes:[
          {id:'attr:inspection-item',name:'检测项目',type:'string',value:'整机终检',origin:'auto'},
          {id:'attr:inspection-result',name:'检测结果',type:'string',value:'不合格',origin:'manual'}
        ],
        dataBindings:[
          {id:'binding:inspection-quality',kind:'structured',origin:'auto',source:'input_001_quality.log',location:'质量结果字段',mapping:'质量结果 → 检测工序.检测结果'},
          {id:'binding:inspection-doc',kind:'document',origin:'corrected',source:'input_007_整机终检.md',location:'质量要求',mapping:'chunk:inspection-criteria'}
        ]
      }}
    },
    {
      id:'object:resource',label:'制造资源',type:'对象',attrs:{对象编码:'object_5',说明:'执行工序所需的设备、工位或班组等物理和组织单元。'},
      workbench:{kind:'object',origin:'auto',position:{x:470,y:580},details:{
        code:'object_5',description:'执行工序所需的物理或组织单元，包括设备、工位、班组等。',status:'published',
        attributes:[
          {id:'attr:resource-name',name:'资源名称',type:'string',value:'检测A组',origin:'auto'},
          {id:'attr:resource-type',name:'资源类型',type:'string',value:'班组',origin:'auto'}
        ],
        dataBindings:[{id:'binding:resource',kind:'structured',origin:'auto',source:'input_002_mes_process.log',location:'资源字段',mapping:'资源 → 制造资源.资源名称'}]
      }}
    },
    {
      id:'object:production-event',label:'生产事件',type:'对象',attrs:{对象编码:'object_3',说明:'制造过程中具有时间性、参与者和对象的离散行为或状态变化。'},
      workbench:{kind:'object',origin:'auto',position:{x:470,y:370},details:{
        code:'object_3',description:'工业制造过程中具有时间性、参与者和对象的离散行为或状态变化，如开工、入队、完工等。',status:'published',
        attributes:[
          {id:'attr:event-code',name:'事件编号',type:'string',value:'事件000154',origin:'auto'},
          {id:'attr:event-time',name:'发生时间',type:'datetime',value:'来自事件日志',origin:'auto'}
        ],
        dataBindings:[
          {id:'binding:event-log',kind:'structured',origin:'auto',source:'input_001_quality.log',location:'事件字段',mapping:'事件 → 生产事件.事件编号'},
          {id:'binding:event-doc',kind:'document',origin:'auto',source:'input_029_事件000154.md',location:'事件全文',mapping:'chunk:event-000154'}
        ]
      }}
    },
    {
      id:'object:inspected-entity',label:'被检实体',type:'对象',attrs:{对象编码:'object_7',说明:'质量检验所作用的物料、半成品、成品或其组成部分。'},
      workbench:{kind:'object',origin:'auto',position:{x:470,y:790},details:{
        code:'object_7',description:'质量检验所作用的对象，可以是物料、半成品、成品或其组成部分。',status:'published',
        attributes:[
          {id:'attr:entity-code',name:'实体编号',type:'string',value:'0001',origin:'auto'},
          {id:'attr:defect-name',name:'异常现象',type:'string',value:'面漆流挂',origin:'corrected'}
        ],
        dataBindings:[{id:'binding:entity-quality',kind:'structured',origin:'auto',source:'input_001_quality.log',location:'实体字段',mapping:'实体 → 被检实体.实体编号'}]
      }}
    },
    {
      id:'object:rework',label:'返工',type:'对象',attrs:{对象编码:'object_8',说明:'对不合格品执行修正性加工，使其满足质量要求。'},
      workbench:{kind:'object',origin:'auto',position:{x:260,y:1000},details:{
        code:'object_8',description:'对不合格品执行的修正性加工或处理动作，以使其满足质量要求。',status:'review',
        attributes:[
          {id:'attr:rework-type',name:'返工类型',type:'string',value:'整机终检返工',origin:'auto'},
          {id:'attr:rework-state',name:'返工状态',type:'string',value:'待复检',origin:'manual'}
        ],
        dataBindings:[
          {id:'binding:rework-mes',kind:'structured',origin:'auto',source:'input_002_mes_process.log',location:'返工工序字段',mapping:'返工工序 → 返工.返工类型'},
          {id:'binding:rework-doc',kind:'document',origin:'auto',source:'input_008_总装调试返工.md',location:'返工要求',mapping:'chunk:rework-procedure'}
        ]
      }}
    },
    {
      id:'object:disposition',label:'不合格品处置',type:'对象',attrs:{对象编码:'object_10',说明:'对不可返工或返工无效的不合格品采取最终流向决策。'},
      workbench:{kind:'object',origin:'auto',position:{x:470,y:1000},details:{
        code:'object_10',description:'对不可返工或返工无效的不合格品所采取的最终流向决策与审批机制。',status:'review',
        attributes:[
          {id:'attr:disposition-type',name:'处置方式',type:'string',value:'返工/让步/报废',origin:'manual'},
          {id:'attr:review-form',name:'评审单',type:'string',value:'不合格品评审单',origin:'auto'}
        ],
        dataBindings:[{id:'binding:disposition-doc',kind:'document',origin:'manual',source:'input_008_总装调试返工.md',location:'不合格处置章节',mapping:'chunk:nonconformance-disposition'}]
      }}
    },
    {
      id:'rule:final-inspection-anomaly',label:'终检异常判定',type:'规则',attrs:{规则编码:'RULE_FINAL_INSPECTION_ANOMALY',说明:'整机终检不合格或存在缺陷描述时识别质量异常。'},
      workbench:{kind:'rule',origin:'manual',position:{x:680,y:700},details:{
        code:'RULE_FINAL_INSPECTION_ANOMALY',description:'整机终检不合格或存在明确缺陷描述时识别质量异常。',status:'published',logic:'OR',scopeObjectId:'object:inspection',conditions:[
          {id:'condition:result-failed',field:'检测结果',operator:'=',value:'不合格'},
          {id:'condition:defect-exists',field:'异常现象',operator:'非空',value:'true'}
        ]
      }}
    },
    {
      id:'rule:rework-required',label:'返工判定',type:'规则',attrs:{规则编码:'RULE_REWORK_REQUIRED',说明:'异常可修复且未超过返工次数上限时进入返工。'},
      workbench:{kind:'rule',origin:'manual',position:{x:680,y:900},details:{
        code:'RULE_REWORK_REQUIRED',description:'异常可修复且未超过返工次数上限时进入返工流程。',status:'published',logic:'AND',scopeObjectId:'object:inspected-entity',conditions:[
          {id:'condition:repairable',field:'可修复',operator:'=',value:'是'},
          {id:'condition:rework-count',field:'返工次数',operator:'<',value:'2'}
        ]
      }}
    },
    {
      id:'rule:disposition-escalation',label:'处置升级判定',type:'规则',attrs:{规则编码:'RULE_DISPOSITION_ESCALATION',说明:'不可修复或重复返工失败时升级到不合格品评审。'},
      workbench:{kind:'rule',origin:'manual',position:{x:680,y:1100},details:{
        code:'RULE_DISPOSITION_ESCALATION',description:'不可修复或重复返工失败时升级到不合格品评审。',status:'review',logic:'OR',scopeObjectId:'object:rework',conditions:[
          {id:'condition:not-repairable',field:'可修复',operator:'=',value:'否'},
          {id:'condition:rework-limit',field:'返工次数',operator:'>=',value:'2'}
        ]
      }}
    },
    {
      id:'action:execute-inspection',label:'执行检测工序',type:'行动',attrs:{行动编码:'ACTION_EXECUTE_INSPECTION',说明:'将检测工序分派到指定制造资源并记录结果。'},
      workbench:{kind:'action',origin:'auto',position:{x:890,y:700},details:{
        code:'ACTION_EXECUTE_INSPECTION',description:'源本体动作“执行”的业务化展示：将加工或检测工序分派到指定制造资源。',status:'published',actionType:'task',triggerRuleId:'',targetObjectId:'object:inspection',executor:'mes.process.execute',
        actionPlan:'向检测A组下发整机终检任务，采集检验结果与缺陷描述。',feedback:'将执行状态和检测结果写回 MES 与质量日志。'
      }}
    },
    {
      id:'action:start-rework',label:'执行返工',type:'行动',attrs:{行动编码:'ACTION_START_REWORK',说明:'创建返工工序并分派责任班组。'},
      workbench:{kind:'action',origin:'manual',position:{x:890,y:900},details:{
        code:'ACTION_START_REWORK',description:'基于源本体“处理”动作扩展：创建返工工序并分派责任班组。',status:'published',actionType:'api',triggerRuleId:'rule:rework-required',targetObjectId:'object:rework',executor:'mes.rework.create',
        actionPlan:'创建整机终检返工工序，冻结当前批次并通知总装班组。',feedback:'返工完成后回写工序状态，并重新进入终检。'
      }}
    },
    {
      id:'action:create-review',label:'发起不合格品评审',type:'行动',attrs:{行动编码:'ACTION_CREATE_NCR_REVIEW',说明:'创建不合格品评审单并等待审批。'},
      workbench:{kind:'action',origin:'manual',position:{x:890,y:1100},details:{
        code:'ACTION_CREATE_NCR_REVIEW',description:'创建不合格品评审单，形成返工、让步接收或报废决策。',status:'review',actionType:'workflow',triggerRuleId:'rule:disposition-escalation',targetObjectId:'object:disposition',executor:'qms.nonconformance.review',
        actionPlan:'提交质量、工艺和生产负责人联合评审，记录最终处置意见。',feedback:'将评审结论回写不合格品处置对象并关闭或重开工单。'
      }}
    }
  ];

  const schemaEdges = [
    {id:'edge:order-work-order',source:'object:production-order',target:'object:work-order',rel:'生成',workbench:{cardinality:'1:N',description:'生产订单生成一个或多个生产工单。',dataMapping:'生产订单.订单编号 = 生产工单.订单编号',origin:'auto'}},
    {id:'edge:work-order-process',source:'object:work-order',target:'object:process',rel:'包含工序',workbench:{cardinality:'1:N',description:'生产工单包含可执行的加工工序。',dataMapping:'生产工单.工单编号 = 加工工序.工单编号',origin:'auto'}},
    {id:'edge:process-inspection',source:'object:process',target:'object:inspection',rel:'包含检测',workbench:{cardinality:'1:N',description:'加工流程包含检测工序。',dataMapping:'加工工序.工序编号 = 检测工序.前序工序编号',origin:'auto'}},
    {id:'edge:process-resource',source:'object:process',target:'object:resource',rel:'执行于',workbench:{cardinality:'N:1',description:'加工工序执行于指定制造资源。',dataMapping:'加工工序.资源编码 = 制造资源.资源编码',origin:'auto'}},
    {id:'edge:process-entity',source:'object:process',target:'object:inspected-entity',rel:'处理',workbench:{cardinality:'N:N',description:'加工或检测工序处理被检实体。',dataMapping:'加工工序.实体编号 = 被检实体.实体编号',origin:'auto'}},
    {id:'edge:event-process',source:'object:production-event',target:'object:process',rel:'关联工序',workbench:{cardinality:'N:1',description:'生产事件关联发生所在的加工工序。',dataMapping:'生产事件.工序编号 = 加工工序.工序编号',origin:'auto'}},
    {id:'edge:event-order',source:'object:production-event',target:'object:production-order',rel:'关联订单',workbench:{cardinality:'N:1',description:'生产事件可追溯到生产订单。',dataMapping:'生产事件.订单编号 = 生产订单.订单编号',origin:'auto'}},
    {id:'edge:inspection-rule',source:'object:inspection',target:'rule:final-inspection-anomaly',rel:'适用规则',workbench:{cardinality:'1:N',description:'检测数据进入终检异常判定。',dataMapping:'检测工序.* → RULE_FINAL_INSPECTION_ANOMALY',origin:'manual'}},
    {id:'edge:entity-rule',source:'object:inspected-entity',target:'rule:rework-required',rel:'适用规则',workbench:{cardinality:'1:N',description:'异常实体进入返工判定。',dataMapping:'被检实体.* → RULE_REWORK_REQUIRED',origin:'manual'}},
    {id:'edge:rule-rework',source:'rule:rework-required',target:'action:start-rework',rel:'触发行动',workbench:{cardinality:'1:N',description:'符合返工条件后创建返工任务。',dataMapping:'rule.result → action.input',origin:'manual'}},
    {id:'edge:action-rework',source:'action:start-rework',target:'object:rework',rel:'创建',workbench:{cardinality:'1:N',description:'执行返工行动创建返工对象。',dataMapping:'action.output → 返工',origin:'manual'}},
    {id:'edge:rework-inspection',source:'object:rework',target:'object:inspection',rel:'返回复检',workbench:{cardinality:'N:1',description:'返工完成后重新进入检测工序。',dataMapping:'返工.工序编号 = 检测工序.前序工序编号',origin:'corrected'}},
    {id:'edge:rework-escalation',source:'object:rework',target:'rule:disposition-escalation',rel:'适用规则',workbench:{cardinality:'1:N',description:'返工结果进入处置升级判定。',dataMapping:'返工.* → RULE_DISPOSITION_ESCALATION',origin:'manual'}},
    {id:'edge:rule-review',source:'rule:disposition-escalation',target:'action:create-review',rel:'触发行动',workbench:{cardinality:'1:N',description:'重复返工失败后发起不合格品评审。',dataMapping:'rule.result → action.input',origin:'manual'}},
    {id:'edge:review-disposition',source:'action:create-review',target:'object:disposition',rel:'创建',workbench:{cardinality:'1:N',description:'评审行动创建不合格品处置记录。',dataMapping:'action.output → 不合格品处置',origin:'manual'}},
    {id:'edge:inspection-action',source:'object:inspection',target:'action:execute-inspection',rel:'执行行动',workbench:{cardinality:'1:N',description:'检测工序由执行行动分派给制造资源。',dataMapping:'检测工序.* → ACTION_EXECUTE_INSPECTION',origin:'auto'}}
  ];

  const instanceNodes = [
    {id:'instance:12',label:'0001号机床订单',type:'生产订单',attrs:{实例编码:'0001_2',来源:'input_001_quality.log:4'}},
    {id:'instance:249',label:'0001号生产工单',type:'生产工单',attrs:{实例编码:'0001_5',来源:'input_002_mes_process.log:1'}},
    {id:'instance:13',label:'整机终检·工序实例32',type:'加工工序',attrs:{完整名称:'0001号生产工单-整机终检-工序实例32',工序序号:32,来源:'input_001_quality.log:4'}},
    {id:'instance:251',label:'检测A组',type:'制造资源',attrs:{实例编码:'a',资源类型:'班组',来源:'input_002_mes_process.log:1'}},
    {id:'instance:14',label:'0001号机床批',type:'被检实体',attrs:{实例编码:'0001_3',实体类型:'机床批',来源:'input_001_quality.log:4'}},
    {id:'instance:7',label:'面漆流挂0002',type:'被检实体',attrs:{实例编码:'0002',异常现象:'面漆流挂',来源:'input_001_quality.log:2'}},
    {id:'instance:15',label:'事件000154',type:'生产事件',attrs:{实例编码:'000154',关联阶段:'整机终检',来源:'input_001_quality.log:4'}},
    {id:'instance:18',label:'整机终检·返工工序1-01',type:'加工工序',attrs:{完整名称:'0001号生产工单-整机终检-工序实例返工1-01',状态:'待复检',来源:'input_001_quality.log:7'}},
    {id:'instance:379',label:'总装C组',type:'制造资源',attrs:{实例编码:'c_2',资源类型:'班组',来源:'input_002_mes_process.log:67'}},
    {id:'instance:427',label:'总装调试·返工工序1-01',type:'加工工序',attrs:{完整名称:'0001号生产工单-总装调试返工-工序实例返工1-01',来源:'input_002_mes_process.log:99'}},
    {id:'instance:1101',label:'整机终检返工1-01',type:'返工',attrs:{实例编码:'0001_1_01_5',状态:'执行中'}},
    {id:'instance:742',label:'不合格品评审单',type:'不合格品处置',attrs:{实例编码:'object_742',状态:'待评审'}}
  ];

  const instanceEdges = [
    {source:'instance:12',target:'instance:249',rel:'包含'},
    {source:'instance:249',target:'instance:13',rel:'包含工序'},
    {source:'instance:13',target:'instance:14',rel:'处理'},
    {source:'instance:13',target:'instance:7',rel:'发现异常'},
    {source:'instance:13',target:'instance:251',rel:'执行于'},
    {source:'instance:15',target:'instance:13',rel:'关联工序'},
    {source:'instance:15',target:'instance:12',rel:'关联订单'},
    {source:'instance:249',target:'instance:18',rel:'包含返工工序'},
    {source:'instance:18',target:'instance:14',rel:'处理'},
    {source:'instance:249',target:'instance:427',rel:'包含返工工序'},
    {source:'instance:427',target:'instance:379',rel:'执行于'},
    {source:'instance:427',target:'instance:14',rel:'处理'},
    {source:'instance:13',target:'instance:1101',rel:'触发返工'},
    {source:'instance:1101',target:'instance:742',rel:'异常升级'}
  ];

  window.LOM_MOCK_ONTOLOGY = {
    job:{
      jobId,status:'completed',mock:true,hasReplay:false,sourceJobId,
      ontologyName:'工业制造流程演示本体',updatedAt:'',inputName:'job_b58e… · 真实结果精简样例',
      objectCount:9,ruleCount:3,actionCount:3,linkCount:schemaEdges.length,instanceCount:instanceNodes.length,instanceLinkCount:instanceEdges.length
    },
    graphs:{
      schema:{ontology:{ontologyId:jobId,sourceOntologyId:sourceJobId,ontologyName:'工业制造流程演示本体',ontologyDesc:'从“工业制造流程本体”真实构建结果中截取订单、工单、终检异常、返工与处置链，并补充可编辑规则用于工作台调试。',graphView:'schema',availableViews:['schema','instance'],mock:true},nodes:schemaNodes,edges:schemaEdges},
      instance:{ontology:{ontologyId:jobId,sourceOntologyId:sourceJobId,ontologyName:'工业制造流程演示本体',ontologyDesc:'0001号机床订单的整机终检异常与返工处置实例。',graphView:'instance',availableViews:['schema','instance'],mock:true},nodes:instanceNodes,edges:instanceEdges}
    },
    qa:{
      schema:{
        think:['定位生产订单、工单与终检对象','沿终检异常判定追踪返工规则和行动','检查重复返工后的不合格品处置路径'],
        highlight:{nodes:['object:inspection','rule:rework-required','action:start-rework','object:rework'],edges:[['object:inspected-entity','rule:rework-required'],['rule:rework-required','action:start-rework'],['action:start-rework','object:rework']]},
        answer:{summary:'本体画布中，生产订单生成生产工单，工单包含加工与检测工序。终检发现异常后，被检实体进入“返工判定”；命中后触发“执行返工”并创建返工对象。不可修复或重复返工失败时，再触发“不合格品评审”。',path:['object:inspection','object:inspected-entity','rule:rework-required','action:start-rework','object:rework'],edges:[['object:inspected-entity','rule:rework-required','适用规则'],['rule:rework-required','action:start-rework','触发行动'],['action:start-rework','object:rework','创建']]}
      },
      instance:{
        think:['定位0001号机床订单和对应生产工单','检查整机终检工序关联的实体、班组与事件','沿异常关系追踪返工和评审节点'],
        highlight:{nodes:['instance:12','instance:249','instance:13','instance:7','instance:1101'],edges:[['instance:12','instance:249'],['instance:249','instance:13'],['instance:13','instance:7'],['instance:13','instance:1101']]},
        answer:{summary:'实例中，“0001号机床订单”包含“0001号生产工单”，其整机终检工序由检测A组执行，并发现“面漆流挂0002”。该异常触发整机终检返工1-01；若返工仍不满足要求，则进入不合格品评审单。',path:['instance:12','instance:249','instance:13','instance:7','instance:1101','instance:742'],edges:[['instance:12','instance:249','包含'],['instance:249','instance:13','包含工序'],['instance:13','instance:7','发现异常'],['instance:13','instance:1101','触发返工'],['instance:1101','instance:742','异常升级']]}
      }
    }
  };
})();
