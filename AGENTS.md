# LOM 仓库协作约定

开始工作前先读 `PLANS.md`、`PROGRESS.md` 和 `docs/README.md`；以代码和测试为事实来源，计划文档不得冒充已实现功能。

## 结构

- `index_V0427.html`、`frontend/`：无构建步骤的原生 HTML/CSS/JS 前端。
- `ontology_construct/`：Python 3.12、FastAPI、CLI、构建流水线与测试。
- `imported_ontologies/`：内置演示本体；`docs/`：产品改造与跨会话设计。

## 命令

```bash
cd ontology_construct
unset UV_PROJECT_ENVIRONMENT
uv sync --extra dev --python 3.12
uv run ontology-construct-api --host 127.0.0.1 --port 8000
uv run pytest
```

浏览器访问 `http://127.0.0.1:8000`。前端 JS 修改后回到仓库根目录，至少执行 `node --check frontend/app.js` 与 `node --check frontend/ontology-workbench.js`。

## 规范

保持现有 LOM 深色视觉与原生 JS 架构，不复制产品原型源码。Python 使用类型标注、Pydantic 模型和 pytest；前端状态、后端领域数据与视图布局分层。不得提交 `.env`、密钥、原始数据、运行输出或虚拟环境。

长任务开始前更新 `PLANS.md`，每次完成可验证增量后更新 `PROGRESS.md` 和对应 `docs/`；只把经过测试或人工验收的内容标为完成，并记录未决问题与下一步。
