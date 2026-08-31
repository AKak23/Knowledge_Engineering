# 本体编辑领域数据契约草案

最后更新：2026-08-31

> 状态：**草案，未冻结**。本文用于与团队接口文档逐字段对齐，不是现有 API 的承诺。接口文档到位后应记录差异、确定迁移方案并发布带版本号的正式契约。

## 为什么需要单独契约

现有后端 `FormattedOntology` 面向“构建结果和问答”，字段为 `objects`、`links`、`actions`、`instances`、`actionInvocations`。新的本体工作台还需要规则、数据挂载、编辑状态、版本和画布布局。若直接把前端 DOM 或 `localStorage` 结构当数据库格式，后续会把 UI 状态、领域状态和构建输出耦合在一起。

建议分成三层：

1. **构建结果**：继续使用现有 `FormattedOntology`，保持旧任务和问答兼容。
2. **本体编辑聚合**：对象、关系、规则、行动、数据挂载、状态和修订号，是阶段 2 的公共契约。
3. **用户视图状态**：节点坐标、面板折叠等，单独保存，可失败而不损坏本体。

## 已实现的构建结果扩展（2026-08-31）

> 这一节是已落地的 `FormattedOntology` 构建结果，不等于下文尚待冻结的工作区修订/发布契约。

构建结果现在为：

```json
{
  "ontology": {},
  "objects": [
    {
      "objectId": 1,
      "objectName": "设备",
      "instanceIds": [1, 2],
      "instanceCount": 2
    }
  ],
  "links": [],
  "rules": [
    {
      "ruleId": 1,
      "ruleName": "高温冷却规则",
      "ruleCode": "object_1",
      "scopeObjectId": 1,
      "scopeObjectName": "设备",
      "logic": "AND",
      "conditions": [
        {
          "conditionId": "c1",
          "field": "温度",
          "operator": ">",
          "value": 30,
          "valueType": "number"
        }
      ],
      "actionIds": [1],
      "actionNames": ["启动水冷"],
      "confidence": 0.95,
      "sourceChunks": ["chunk_0001"]
    }
  ],
  "actions": [
    {
      "actionId": 1,
      "actionName": "启动水冷",
      "triggerRuleIds": [1],
      "triggerRules": ["高温冷却规则"],
      "targetObjectIds": [1],
      "targetObjects": ["设备"],
      "actionType": "control",
      "executor": "plc.start_water_cooling",
      "actionPlan": "启动水冷",
      "feedback": ""
    }
  ],
  "instances": [
    {
      "instanceId": 1,
      "instanceName": "设备01",
      "objectId": 1,
      "objectName": "设备"
    }
  ],
  "actionInvocations": []
}
```

- `instances[].objectId` 是实例归属的真值；`objects[].instanceIds` 是为画布导航提供的反向索引。
- 规则只存储单层 `AND | OR` 与结构化条件；当前还未执行规则或自动调用 executor。
- 旧 JSON 不含 `rules`、`instanceIds` 或新行动字段时按默认空值加载，无需重写历史 job。

## 建议的编辑聚合 v1

```json
{
  "contractVersion": "ontology-editor-v1",
  "ontology": {
    "id": "ont_xxx",
    "code": "industrial_quality",
    "name": "工业质量本体",
    "description": "",
    "status": "draft",
    "sourceMode": "mixed",
    "revision": 3,
    "sourceJobId": "job_xxx"
  },
  "objects": [],
  "relations": [],
  "rules": [],
  "actions": [],
  "agents": [],
  "view": {
    "layoutVersion": "compact-v2",
    "positions": {}
  }
}
```

`agents` 是否与本体编辑聚合一起返回可以讨论，但智能体运行配置应有独立资源和版本，不应内嵌在 `FormattedOntology`。

## 公共字段

对象、关系、规则和行动建议共享：

| 字段 | 含义 |
|---|---|
| `id` | 服务端稳定字符串 ID，不因名称修改而改变 |
| `code` | 业务可读且在本体内唯一的编码 |
| `name` | 展示名称 |
| `description` | 业务说明 |
| `status` | `draft | review | published | disabled` |
| `origin` | `auto | manual | corrected` |
| `sourceRefs` | 来源文档、chunk、表字段等证据引用 |

数值型构建结果 ID 可在适配层转换，但编辑 API 建议统一字符串 ID，避免不同资源类型碰撞。

## 对象、属性与数据挂载

```json
{
  "id": "obj_order",
  "code": "production_order",
  "name": "生产订单",
  "attributes": [
    {
      "id": "attr_status",
      "code": "status",
      "name": "状态",
      "dataType": "string",
      "sampleValue": "执行中",
      "origin": "auto"
    }
  ],
  "dataBindings": [
    {
      "id": "binding_order_table",
      "kind": "structured",
      "origin": "corrected",
      "source": "mes_prod",
      "location": "public.production_order",
      "mapping": "order_id",
      "sourceRefs": []
    }
  ]
}
```

`kind` 初步为 `structured | document`。正式契约需要进一步拆出连接标识、schema/table/column、文档 ID/chunk ID、刷新策略和校验状态，且不得保存明文数据库密码。

## 关系

```json
{
  "id": "rel_order_work_order",
  "code": "creates",
  "name": "生成",
  "sourceId": "obj_order",
  "targetId": "obj_work_order",
  "cardinality": "1:N",
  "dataMapping": "production_order.order_id = work_order.order_id",
  "origin": "auto"
}
```

`sourceId`/`targetId` 当前允许指向对象、规则或行动。正式契约可增加 `sourceKind`/`targetKind`，或把对象关系、规则输入、行动触发拆成不同资源；需要结合团队接口决定。

## 规则

```json
{
  "id": "rule_final_inspection",
  "code": "final_inspection_abnormal",
  "name": "终检异常判定",
  "scopeObjectId": "obj_inspection",
  "logic": "AND",
  "conditions": [
    {
      "id": "cond_1",
      "field": "result",
      "operator": "=",
      "value": "不合格",
      "valueType": "string"
    }
  ],
  "status": "draft",
  "origin": "manual"
}
```

v1 可先支持单层 `AND | OR` 与 `为空、不为空、包含、不包含、=、≠、>、≥、<、≤`。正式规则引擎必须定义类型转换、缺失字段、日期/时区、百分数和多值字段语义，并返回 `matchedInstanceIds`、关联边、解释和证据。

## 行动

```json
{
  "id": "action_rework",
  "code": "create_rework_task",
  "name": "创建返工任务",
  "actionType": "task",
  "triggerRuleId": "rule_final_inspection",
  "targetObjectId": "obj_work_order",
  "executor": "mes.create_rework_task",
  "actionPlan": "创建返工工单并通知质量负责人",
  "feedback": "执行结果回写返工状态",
  "status": "draft"
}
```

现有 `FormattedAction` 的 `inputObjects`、`outputObjects` 和 `ActionInvocation` 必须保留。编辑行动可以扩展这些字段，但不应在没有迁移规则时替换现有模型。

## 版本、保存与发布

- 每次读取返回 `revision`；更新请求携带期望修订号，冲突时返回 `409`。
- 自动保存草稿与发布本体是不同动作。发布前执行引用完整性、编码唯一性、规则类型和行动配置校验。
- 删除默认采用资源状态或显式删除列表，不能仅凭数组中缺失就物理删除。
- 本体发布产生不可变版本号；智能体绑定发布版本，而不是浮动草稿。
- `dirty`、当前选中节点、临时高亮不得进入服务端领域数据。

## 与现有格式的适配

| 现有 `FormattedOntology` | 编辑契约 | 说明 |
|---|---|---|
| `ontology` | `ontology` | 增加 code/status/revision |
| `objects` | `objects` | 数字 ID 转稳定编辑 ID；保留属性来源 |
| `links` | `relations` | 映射 source/target、基数和置信度 |
| `actions` | `actions` | 保留 input/output objects，再增加触发规则等编辑字段 |
| 无 | `rules` | 新领域模型 |
| `instances` | 单独实例资源 | 不随 schema 保存请求整体回写 |
| `actionInvocations` | 单独执行/事件资源 | 与行动定义分离 |
| 无 | `view` | 只保存布局 |

建议建立显式适配器，不直接修改旧 formatter 的返回结构；待所有消费者迁移后再考虑统一。

## 接口文档到位后的核对清单

- 资源名称、字段名、ID 类型、必填项、枚举和时间格式。
- 本体/节点/规则/行动是整包保存还是细粒度 CRUD。
- 修订号、幂等键、错误格式、批量操作和删除语义。
- 数据挂载的连接资源如何引用，是否已有数据目录/血缘模型。
- 规则表达式格式及后端执行引擎。
- 旧 `FormattedOntology` 的兼容期限和迁移责任方。
- 智能体是否属于同一服务、如何绑定本体版本。
