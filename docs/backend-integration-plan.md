# 本体工作台后端接入方案

最后更新：2026-08-28

状态：阶段 2 规划，尚未开始实施。

## 当前后端事实

现有 FastAPI 以“构建 job”为中心，支持创建/导入任务、查询状态、读取 formatted/canonical/graph、问答和实例重建。`GlobalOntology` 已建模实体、三元组、行动、实例与行动调用，结构化数据链路已能从 PostgreSQL 生成本体。

当前缺口不是重写构建系统，而是增加“可编辑本体工作区”：

- 没有保存人工编辑后 schema 的公共 API。
- 没有业务规则模型、存储、抽取和执行结果。
- 行动没有与新业务规则形成稳定契约。
- 数据挂载没有编辑、校验和来源追踪 API。
- 没有本体草稿修订、发布版本和多人冲突处理。

## 推荐边界

保留 job 构建链路，把一次构建结果导入工作区，而不是让 job 本身变成长期可编辑资源：

```text
文档/数据库 -> 构建 job -> FormattedOntology
                           -> 编辑适配器 -> Ontology Workspace Draft
用户编辑 -> 校验/保存 -> 发布版本 -> 问答/规则/智能体消费
```

这样历史 job 仍可复现，人工修改不会覆盖原始构建证据，已有问答和导入接口也能继续工作。

## 建议模块

在正式契约确认后，可在 `ontology_construct/src/ontology_construct/` 下增加：

- `workbench_models.py`：编辑聚合、规则、数据挂载和布局模型。
- `workbench_repository.py`：草稿、修订和发布版本存储。
- `workbench_service.py`：导入构建结果、校验、保存、发布。
- `workbench_adapter.py`：`FormattedOntology` 与编辑模型之间的转换。
- `rule_engine.py`：结构化条件求值和命中解释。
- `agent_service.py`：第三阶段再加入，不与阶段 2 强耦合。

早期可沿用当前文件型 job store 做单机验证，但公共接口一旦支持多人编辑，应使用具备事务和唯一约束的持久化存储。

## 建议 API（待接口文档校准）

| 方法 | 路径 | 用途 |
|---|---|---|
| `POST` | `/ontologies` | 新建空白本体草稿 |
| `POST` | `/ontologies/from-job/{jobId}` | 从构建结果创建工作区草稿 |
| `GET` | `/ontologies/{ontologyId}` | 获取编辑聚合和 revision |
| `PUT` | `/ontologies/{ontologyId}` | 按 revision 保存整包草稿 |
| `POST` | `/ontologies/{ontologyId}/validate` | 校验引用、编码、规则和行动 |
| `POST` | `/ontologies/{ontologyId}/publish` | 生成不可变发布版本 |
| `GET` | `/ontologies/{ontologyId}/instances` | 分页读取实例，不随 schema 整包返回 |
| `POST` | `/ontologies/{ontologyId}/rules/{ruleId}/evaluate` | 返回命中实例、边、解释和证据 |

若团队已有细粒度 CRUD API，应优先适配现有接口；前端内部仍可维护整包工作副本，通过 repository/service 层屏蔽差异。

## 实施顺序

### 1. 契约与适配测试

- 根据接口文档冻结 `ontology-editor-v1`。
- 为现有工业制造 fixture 编写 `FormattedOntology -> workspace -> API JSON` golden test。
- 明确不可无损映射字段，并记录而不是静默丢弃。

### 2. 草稿持久化

- 新建/读取/保存本体草稿和布局。
- revision 乐观锁、编码唯一性和引用完整性校验。
- 前端保存从 `localStorage` 切到 API；本地存储仅作为未提交恢复缓存。

### 3. 规则与行动

- 新增业务规则 Pydantic 模型和序列化。
- 从构建材料抽取规则时保存 source chunks；人工修正保留 origin 和审计信息。
- 行动显式引用触发规则和目标对象，同时兼容现有 input/output objects 与 action invocations。

### 4. 数据挂载

- 结构化绑定引用已登记的数据连接、表和字段；凭据只通过服务端 secret/env 管理。
- 非结构化绑定引用文档与 chunk/evidence ID。
- 保存时校验引用存在，数据不可用时标记 stale/error，不能直接删除用户映射。

### 5. 规则求值与前端切换

- 后端实现与 v1 运算符一致的确定性求值。
- API 返回直接命中与关联节点，前端只负责展示。
- 保留前端匹配器作为 mock/demo fallback，并清晰标记结果来源。

### 6. 发布与兼容

- 发布生成不可变版本；现有 QA 可通过适配器读取发布版本。
- 旧 job、旧 formatted JSON 和内置本体继续可加载。
- 增加迁移脚本或惰性迁移，不修改历史输出文件。

## 测试与验收

- 模型测试：枚举、引用、重复 code、条件值类型和循环/悬空引用。
- API 测试：创建、更新、revision 冲突、校验错误、发布和权限。
- 适配测试：旧 formatted JSON 的对象、关系、行动和实例不丢失。
- 规则测试：AND/OR、空值、数字、日期、包含、缺失字段和解释文本。
- 前端集成：保存后刷新、换浏览器、重载版本，结果与保存前一致。
- 回归：运行 `uv run pytest`，并验证原有上传构建、历史任务、图谱切换和问答。

## 风险控制

- 不在契约冻结前改写现有 `FormattedOntology`。
- 不允许前端提交明文数据库密码或自由 SQL 规则。
- 不把画布坐标、选中态和高亮写入领域模型。
- 不让规则自动执行有副作用的行动；行动执行需要第三阶段的权限、审计和幂等设计。
