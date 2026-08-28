# imported_ontologies

从 `ontology_model/evaluations/qwen_eval/eval_in_dist.jsonl` 提取的 **24 个可直接上传的本体 JSON**。

每个文件对应一个 `schema_id`,符合本项目的 `FormattedOntology` 结构,可经
**前端「上传已有本体」** 或 **`POST /ontology/jobs/import`** 直接导入,导入后
本体层(objects/links)和实例层(instances/linkValues)都能在 3D 图谱中渲染。

**显示名称已中文化**:对象/属性/关系/动作/实例的展示名均为中文;原始英文标识
保留在 `objectCode` / `attributeCode` / `actionCode` / `instanceCode` 和关系的
`description` 字段中,便于回溯。

## 来源与映射

源文件每行是一条评测任务,本体以自定义 DSL 嵌在 `input` 字段里。提取规则:

| 源(DSL) | 目标(FormattedOntology) |
|---|---|
| `[SCHEMA] ObjectType` | `objects`(属性从实例反推,`dataType` 多数表决,`sampleValue` 取样例) |
| `[SCHEMA] RelationType` | `links`(类间关系) |
| `[SCHEMA] 类型层次 is-a` | `links`(`linkInfo="是一种"`) |
| `[SCHEMA] ActionType` | `actions`(actor→input,target→output) |
| `[GRAPH] 节点` | `instances`(+ `attributeValues`) |
| `[GRAPH] 边` | 实例的 `linkValues` |
| `[GRAPH] 动作事件` | `actionInvocations` |

每个 schema 在源文件里有多条记录(完整度不一,部分子图记录缺 ActionType/事件段),
脚本会**解析全部记录后,选「含动作 ∧ 含事件 ∧ 节点最多」的最完整一条**。

## 中文化

- **对象名 / 关系名**:直接取源 DSL 自带的中文描述(并清洗掉嵌入的英文类型名与字面 `is-a`)。
- **属性名 / 动作名**:由 `_zh.py` 的确定性词典(词元 + 后缀规则 + 覆盖表)翻译,
  零英文残留;`A/B`、`HR`、`KPI`、`eSIM` 等标准术语按惯例保留。
- **实例名**:`<中文类型>_<序号>`(如 `营销活动_001`);真实名称仍在 `名称` 属性里。
- 属性的**取值**(如 `status=completed`)是原始数据,默认不翻译。

## 重新生成

```bash
python3 _convert_from_eval.py   # 仅用标准库;翻译词典见同目录 _zh.py;输出覆盖 *.json
```

## 校验状态

24 个文件已全部通过 `FormattedOntology` 模型校验,经导入端点
(`_load_imported_ontology`)解析与图谱端点(`formatted_ontology_to_graph`)渲染:
**本体层 + 实例层均非空(24/24)**,且展示字段无英文残留(标准缩写除外)。
