# 本体智能体设计与实现

最后更新：2026-09-02

状态：阶段 3 第一个可运行增量已实现。管理、配置、会话、发布和受凭证保护的外部调用已接入真实 FastAPI 后端；当前绑定已完成的 ontology job 作为稳定本体版本。

## 产品形态

- 顶部全局“本体智能体”入口不再依赖当前画布是否有浏览器草稿。
- 管理弹窗展示全部历史智能体、发布状态、绑定本体、协同成员和外部调用状态。
- 每个智能体可修改配置、开始独立对话、管理会话历史和发布/重新发布。
- 新建配置分为基本信息、本体上下文、协同成员和外部调用四个部分。
- 视觉继续使用 LOM 深色工作台的蓝绿强调色、紧凑卡片和分层边框，参考图只用于信息层级。

## 配置契约

```json
{
  "agentId": "agent_xxx",
  "name": "客户洞察智能体团队",
  "description": "识别价值群、流失风险与行动机会",
  "collaborationPrompt": "先分配成员分析，再依据本体证据综合",
  "ontologyContext": {
    "jobId": "job_xxx",
    "ontologyId": "1",
    "ontologyName": "客户运营本体",
    "version": "job_xxx"
  },
  "members": [
    {"agentId": "insight-analysis", "responsibility": "对象画像与关系洞察"}
  ],
  "externalCallEnabled": true,
  "backend": "deepseek",
  "status": "draft",
  "revision": 1,
  "publishedVersion": 0,
  "invokeName": "ontology-agent-xxxxxxxx"
}
```

智能体是独立资源，不内嵌到 `FormattedOntology`。`ontologyContext` 保存创建/修改时的本体快照摘要，运行时仍以 `jobId` 下的 `formatted_ontology.json` 为事实来源。

## 内置原子 Agent

首期官方目录由后端统一提供，前端不能注入未登记的成员：

- `insight-analysis`：洞察分析 Agent，负责对象画像、关系洞察和异常分析。
- `location-analysis`：区位分析 Agent，负责空间分布、区域差异和区位影响。
- `data-questioning`：问数分析 Agent，负责指标口径、属性统计和数据缺口。
- `rule-reasoning`：规则推理 Agent，负责规则命中、行动建议和风险约束。

## Pi Agent 运行层

实现遵循 Pi Agent Core 的轻量边界，不引入 LangGraph 或其他图工作流运行时：

```text
用户消息 + 应用层持久化的近期会话
  -> transform context：注入协同提示词与本体摘要
  -> 选中的原子 Agent 作为 Agent Tool 并行执行
  -> 将 tool result 标记为“建议上下文”回填当前 turn
  -> 现有 Graph RAG / 全量本体问答
  -> 返回答案、节点/关系高亮、Pi Agent 协同轨迹
  -> 应用层写入 assistant message 和证据摘要
```

原子 Agent 建议不是事实来源。最终回答仍通过现有本体 QA 证据契约；运行元数据标记 `agent_runtime=pi-agent` 和 `tool_execution=parallel`。

## 生命周期与安全

- `draft`：可配置、可平台内对话，不可外部调用。
- `published`：发布版本号递增；开启外部调用时生成稳定 invoke name 和访问凭证。
- 修改已发布配置会回到 `draft`，清除旧凭证并要求重新发布。
- 配置更新使用 `revision` 乐观锁，旧修订号返回 HTTP 409。
- 外部访问凭证只在发布响应中显示一次，磁盘只保存 SHA-256 哈希和遮罩预览；调用使用 `X-Agent-Key`。
- 本轮不执行有业务副作用的行动；规则原子 Agent 只能提出建议。

## 已实现 API

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/ontology/agent-catalog` | 官方原子 Agent 目录 |
| `GET/POST` | `/ontology/agents` | 列表/创建智能体 |
| `GET/PUT` | `/ontology/agents/{agentId}` | 读取/按 revision 修改配置 |
| `POST` | `/ontology/agents/{agentId}/publish` | 发布或重新发布 |
| `GET/POST` | `/ontology/agents/{agentId}/sessions` | 列表/新建会话 |
| `GET` | `/ontology/agents/{agentId}/sessions/{sessionId}` | 读取完整会话 |
| `POST` | `/ontology/agents/{agentId}/sessions/{sessionId}/messages` | 平台内 Pi Agent 对话 |
| `POST` | `/v1/ontology-agents/{invokeName}/invoke` | 发布后的外部调用 |

## 已知边界与后续

- 当前用 completed ontology job 作为稳定版本；第二阶段正式本体发布完成后，应把绑定目标迁移为不可变的 ontology release ID。
- 文件存储适用于单机演示；多实例部署需迁移数据库、集中密钥服务、分布式锁和审计检索。
- 外部调用已有身份凭证，但尚未增加租户/用户权限、配额、限流、计费、凭证轮换界面和结构化审计后台。
- 尚未提供流式事件、用户 steering/follow-up 消息和对话压缩；这些可按 Pi Agent Core 的对应 hook 继续扩展。
