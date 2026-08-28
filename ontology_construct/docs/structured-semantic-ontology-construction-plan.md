# 结构化数据库语义本体构建施工设计

## 背景

当前结构化数据库构建路径以数据库表为主要建模单元：表生成对象类型，字段生成属性，外键和元数据关系生成 schema/实例关系。该策略适合快速展示数据库结构，但在需要推理的场景下容易出现两个问题：

- schema 停留在“表级对象”，例如“设备台账表”“备件库存表”，而不是“设备”“备件”“维修工单”等领域概念。
- 实例关系只在采样到的行之间建立，未采样目标不会自动补齐，后续只能依赖低置信度的“语义关联”补边。

本次改造只作用于结构化 YAML 上传构建。非结构化文档构建、已有本体导入、问答、Graph RAG、子图裁剪和兜底回答策略不改。

## 目标与非目标

目标：

- 增加可选 `semantic_mapping`，让用户或导入脚本显式声明领域类、字段角色、关系表、事件/事实表和采样策略。
- 保持旧结构化 YAML 兼容；没有 `semantic_mapping` 时仍走原有表级构建。
- 语义模式下按领域类生成 schema 和实例，关系表直接生成实例边，FK/join 关系可自动补齐目标实体。
- 输出 `structured_quality.json`，量化孤立实例、fallback 边、可验证边和按类/按谓词分布。
- 为 `mock_data` 提供 `mock_data_semantic.yaml` 示例。

非目标：

- 不支持 YAML 自由 SQL。
- 不改变数据库原始业务数据。
- 不改非结构化构建链路。
- 不改 QA/推理链路。

## 配置 Schema

`semantic_mapping` 是 `StructuredBuildConfig` 的可选字段。

```yaml
semantic_mapping:
  classes:
    - id: device
      name: 设备
      description: 设备主数据
      source_tables: [eam_device_master]
      primary_table: eam_device_master
      primary_key: device_id
      label_column: device_name
      attributes:
        - source_column: manufacturer
          name: 制造商
        - source_column: commission_date
          name: 投用日期

  relations:
    - source_class: telemetry_record
      predicate: 采集自设备
      target_class: device
      source_table: iot_device_telemetry
      target_table: eam_device_master
      source_column: device_id
      target_column: device_id
      join_type: fk

  relation_tables:
    - table: eam_device_bom
      source_class: device
      predicate: 包含
      target_class: spare_part
      source_column: parent_device_id
      target_column: child_part_id
      materialize_as_class: false

  event_tables:
    - table: eam_repair_work_order
      class_id: repair_work_order

  sampling:
    default_per_class: 50
    class_limits:
      device: 200
      spare_part: 200
    table_limits:
      iot_device_telemetry: 80
    auto_include_related: true
```

校验要求：

- 所有表名必须存在于数据库 metadata。
- 所有字段名必须存在于对应表 metadata。
- `source_class` 和 `target_class` 必须在 `classes` 中定义。
- 禁止明文数据库密码和自由 SQL 的既有规则保持不变。

## 构建数据流

1. 读取 PostgreSQL metadata、meta 表、注释、约束和采样数据。
2. 若未配置 `semantic_mapping`，走旧表级构建。
3. 若配置 `semantic_mapping`：
   - 校验 semantic mapping 与 metadata 一致。
   - 按 `classes` 生成 `Entity`，`id` 使用领域类 id。
   - 按 `relations` 和 `relation_tables` 生成 schema triples。
   - 按类采样 primary table，生成领域类实例。
   - 使用 FK/join 和关系表生成实例边。
   - 当目标实体缺失且 `auto_include_related` 为 true 时，从数据库按主键补齐目标实例。
4. 保存结构化中间产物和 `structured_quality.json`。
5. 语义模式下不依赖实例后处理的 fallback “语义关联”来消除孤立节点。

## mock_data 映射

`mock_data` 原始 SQL 不修改。转换后的 PostgreSQL 脚本和语义 YAML 负责说明如何解释这些表：

- `eam_device_master -> 设备`
- `erp_inventory -> 备件`
- `eam_device_bom -> 设备 包含 备件`
- `iot_device_telemetry -> 测点记录`
- `iot_alarm_config -> 报警规则`
- `iot_device_status -> 运行状态记录`
- `eam_repair_work_order -> 维修工单`
- `kb_fault_sop -> 故障SOP`
- `mes_production_order -> 生产订单`
- `mes_device_oee -> OEE记录`
- `erp_purchase_order -> 采购订单`

## 质量指标

`structured_quality.json` 输出：

- `objectCount`
- `schemaRelationCount`
- `instanceCount`
- `instanceLinkCount`
- `isolatedInstanceCount`
- `isolatedInstanceRatio`
- `fallbackLinkCount`
- `fallbackLinkRatio`
- `verifiedLinkCount`
- `verifiedLinkRatio`
- `instancesByClass`
- `linksByPredicate`

可验证边按 `source_chunks` 判断，包含 `structured:semantic:*`、`structured:fk:*`、`structured:relations`、`structured:event:*` 的边视为可验证边。

## 测试清单

- 旧 YAML 不含 `semantic_mapping` 时现有测试保持通过。
- `semantic_mapping` 非法表名、非法字段、非法 class 引用会报错。
- 关系表生成 schema 关系和实例边，默认不生成关系表对象。
- 事件/事实表实例 class_id 使用领域类 id。
- FK/join 生成边时能自动补齐未采样目标实体。
- `structured_quality.json` 指标正确输出。
- `mock_data_semantic.yaml` 构建结果中 schema 名称为领域类，不是表级对象。
