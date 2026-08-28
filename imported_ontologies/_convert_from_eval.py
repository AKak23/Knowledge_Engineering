#!/usr/bin/env python3
"""Convert qwen_eval/eval_in_dist.jsonl into importable FormattedOntology JSON.

Source records embed an ontology DSL inside the `input` field:
  [SCHEMA] ObjectType / RelationType / ActionType / 类型层次(is-a)
  [GRAPH]  节点 / 边 / 动作事件

We pick, per schema_id, the record with the most complete graph, parse it,
and emit one FormattedOntology JSON file (consumable by POST /ontology/jobs/import).
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _zh  # noqa: E402  sibling module: English→Chinese term maps

SRC = Path("/Users/shuaichen/Documents/data_intelligence/ontology_model/evaluations/qwen_eval/eval_in_dist.jsonl")
OUT_DIR = Path("/Users/shuaichen/Documents/data_intelligence/LOM前端/imported_ontologies")

NOW = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

_DOMAIN = {
    "crm_": "客户关系管理(CRM)", "hr_": "人力资源(HR)", "factory_": "智能工厂",
    "supply_chain_": "供应链", "telecom_": "电信",
}


# ----------------------------- value typing -----------------------------
def _typed(raw: str):
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        return raw[1:-1], "string"
    if re.fullmatch(r"-?\d+", raw):
        return int(raw), "integer"
    if re.fullmatch(r"-?\d+\.\d+", raw):
        return float(raw), "number"
    if raw in ("true", "false", "True", "False"):
        return raw.lower() == "true", "boolean"
    if raw in ("null", "None"):
        return None, "string"
    return raw, "string"


_KW = re.compile(r'(\w+)\s*=\s*("(?:[^"\\]|\\.)*"|[^,]+?)(?=\s*,\s*\w+\s*=|\s*$)')


def parse_kwargs(s: str):
    """Return list of (key, value, dataType) preserving order."""
    out = []
    for m in _KW.finditer(s):
        val, dt = _typed(m.group(2))
        out.append((m.group(1), val, dt))
    return out


# ----------------------------- section slicing -----------------------------
def _slice(text: str, start_anchor: str, end_anchors: list[str]) -> str:
    i = text.find(start_anchor)
    if i < 0:
        return ""
    i += len(start_anchor)
    end = len(text)
    for a in end_anchors:
        j = text.find(a, i)
        if 0 <= j < end:
            end = j
    return text[i:end]


def parse_input(text: str) -> dict:
    schema = _slice(text, "[SCHEMA]", ["[GRAPH]"])
    graph = text[text.find("[GRAPH]"):] if "[GRAPH]" in text else ""

    obj_block = _slice(schema, "ObjectType:", ["RelationType:", "ActionType:", "类型层次"])
    rel_block = _slice(schema, "RelationType:", ["ActionType:", "类型层次"])
    act_block = _slice(schema, "ActionType:", ["类型层次"])
    isa_block = _slice(schema, "类型层次", [])

    # ObjectType: "  Name: {...} — desc"
    object_types = []  # (name, desc)
    for m in re.finditer(r"^\s{2}(\w+):\s*\{[^}]*\}\s*(?:[—\-]\s*(.*))?$", obj_block, re.M):
        object_types.append((m.group(1), (m.group(2) or "").strip()))

    # RelationType: "  rel: Src -> Tgt (card) — desc"
    relations = []
    for m in re.finditer(
        r"^\s{2}(\w+):\s*(\w+)\s*->\s*(\w+)\s*\(([^)]*)\)\s*(?:[—\-]\s*(.*))?$", rel_block, re.M
    ):
        relations.append(
            dict(name=m.group(1), src=m.group(2), tgt=m.group(3),
                 card=m.group(4).strip(), desc=(m.group(5) or "").strip())
        )

    # ActionType blocks
    actions = []
    cur = None
    for line in act_block.splitlines():
        m = re.match(r"^\s{2}(\w+):\s*$", line)
        if m:
            cur = dict(name=m.group(1), actor="", target="")
            actions.append(cur)
            continue
        if cur is not None:
            ma = re.match(r"^\s{4}actor_type:\s*(\w+)", line)
            mt = re.match(r"^\s{4}target_type:\s*(\w+)", line)
            if ma:
                cur["actor"] = ma.group(1)
            elif mt:
                cur["target"] = mt.group(1)

    # is-a hierarchy
    isa = []
    for m in re.finditer(r"^\s{2}(\w+)\s+is-a\s+(\w+)\s*$", isa_block, re.M):
        isa.append((m.group(1), m.group(2)))

    # GRAPH sections
    node_block = _slice(graph, "节点", ["边（", "边(", "动作事件"])
    edge_block = _slice(graph, "边", ["动作事件"])
    event_block = _slice(graph, "动作事件", [])

    nodes = []  # (nodeId, type, kwargs)
    for m in re.finditer(r"^\s{2}(\w+):\s*(\w+)\((.*)\)\s*$", node_block, re.M):
        nodes.append((m.group(1), m.group(2), parse_kwargs(m.group(3))))

    edges = []  # (src, rel, tgt)
    for m in re.finditer(r"^\s{2}(\w+)\s*--(\w+)-->\s*(\w+)\s*$", edge_block, re.M):
        edges.append((m.group(1), m.group(2), m.group(3)))

    events = []  # (eventId, action, actor, target, ts)
    for m in re.finditer(
        r"^\s{2}(\w+):\s*(\w+)\s+actor=(\w+)\s+target=(\w+)\s+(\S+)\s*$", event_block, re.M
    ):
        events.append((m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)))

    return dict(object_types=object_types, relations=relations, actions=actions,
                isa=isa, nodes=nodes, edges=edges, events=events)


# ----------------------------- build FormattedOntology -----------------------------
def _clean_desc(text: str, type_names_zh: dict[str, str]) -> str:
    """Polish a Chinese description used as a display name: drop literal is-a markers
    and replace any embedded English type name with its Chinese."""
    text = re.sub(r"[（(]\s*is[-_]a\s*[)）]", "", text)
    text = re.sub(r"\bis[-_]a\b", "继承", text)
    for en in sorted(type_names_zh, key=len, reverse=True):
        zh = type_names_zh[en]
        if zh and zh != en:
            text = re.sub(rf"\b{re.escape(en)}\b", zh, text)
    return text.strip()


def build_ontology(schema_id: str, graph_id: str, parsed: dict) -> dict:
    # object ids
    type_desc = {n: d for n, d in parsed["object_types"]}
    type_order = [n for n, _ in parsed["object_types"]]
    # include any node type not declared in schema
    for _, t, _ in parsed["nodes"]:
        if t not in type_desc:
            type_desc[t] = ""
            type_order.append(t)
    obj_id = {t: i + 1 for i, t in enumerate(type_order)}

    # attributes per type (inferred from instances): majority dataType + sample value
    attr_seen: dict[tuple[str, str], dict] = {}
    attr_order: dict[str, list[str]] = {t: [] for t in type_order}
    for node_id, t, kvs in parsed["nodes"]:
        for k, v, dt in kvs:
            key = (t, k)
            if key not in attr_seen:
                attr_seen[key] = dict(dt_counts={}, sample=v)
                attr_order[t].append(k)
            attr_seen[key]["dt_counts"][dt] = attr_seen[key]["dt_counts"].get(dt, 0) + 1

    attr_id: dict[tuple[str, str], int] = {}
    next_attr = 1
    # Chinese display name for each object type comes from the DSL description,
    # then polished to drop embedded English type names / literal is-a markers.
    type_zh_raw = {t: (type_desc.get(t) or t) for t in type_order}
    type_zh = {t: _clean_desc(raw, type_zh_raw) for t, raw in type_zh_raw.items()}

    objects = []
    for t in type_order:
        attributes = []
        for k in attr_order[t]:
            key = (t, k)
            dt = max(attr_seen[key]["dt_counts"].items(), key=lambda x: x[1])[0]
            attr_id[key] = next_attr
            attributes.append(dict(
                attributeId=next_attr, attributeName=_zh.attr_zh(k),
                attributeCode=f"{t}.{k}", dataType=dt, description="",
                sampleValue=attr_seen[key]["sample"], sourceChunks=[],
            ))
            next_attr += 1
        objects.append(dict(
            objectId=obj_id[t], objectName=type_zh[t], objectCode=t,
            description=type_desc.get(t, ""), attributes=attributes, sourceChunks=[],
        ))

    # links from relations (+ is-a). linkInfo uses the Chinese relation description
    # (the edge label shown in the graph); English relation name kept in `description`.
    links = []
    rel_link_id: dict[str, int] = {}
    rel_desc = {r["name"]: (r["desc"] or r["name"]) for r in parsed["relations"]}
    next_link = 1
    for r in parsed["relations"]:
        if r["src"] not in obj_id or r["tgt"] not in obj_id:
            continue
        rel_link_id[r["name"]] = next_link
        links.append(dict(
            linkId=next_link,
            sourceObjectId=obj_id[r["src"]], sourceObjectName=type_zh[r["src"]],
            targetObjectId=obj_id[r["tgt"]], targetObjectName=type_zh[r["tgt"]],
            linkInfo=_clean_desc(r["desc"] or r["name"], type_zh_raw),
            linkType=r["card"] or "N:N",
            description=r["name"], confidence=1.0, sourceChunks=[],
        ))
        next_link += 1
    for sub, sup in parsed["isa"]:
        if sub not in obj_id or sup not in obj_id:
            continue
        links.append(dict(
            linkId=next_link,
            sourceObjectId=obj_id[sub], sourceObjectName=type_zh[sub],
            targetObjectId=obj_id[sup], targetObjectName=type_zh[sup],
            linkInfo="是一种", linkType="is-a", description=f"{sub} is-a {sup}",
            confidence=1.0, sourceChunks=[],
        ))
        next_link += 1

    # actions. actionName is the Chinese verb-phrase; English code kept in actionCode.
    actions = []
    act_id: dict[str, int] = {}
    for i, a in enumerate(parsed["actions"], start=1):
        act_id[a["name"]] = i
        desc_bits = []
        if a["actor"]:
            desc_bits.append(f"执行者：{type_zh.get(a['actor'], a['actor'])}")
        if a["target"]:
            desc_bits.append(f"对象：{type_zh.get(a['target'], a['target'])}")
        actions.append(dict(
            actionId=i, actionName=_zh.action_zh(a["name"]), actionCode=a["name"],
            description="；".join(desc_bits),
            inputObjects=[type_zh[a["actor"]]] if a["actor"] in type_zh else (
                [a["actor"]] if a["actor"] else []),
            outputObjects=[type_zh[a["target"]]] if a["target"] in type_zh else (
                [a["target"]] if a["target"] else []),
            sourceChunks=[],
        ))

    # instances. instanceName = "<中文类型>_<序号>"; English node id kept in instanceCode.
    inst_id: dict[str, int] = {}
    node_type: dict[str, str] = {}
    inst_name_zh: dict[str, str] = {}
    instances_by_id: dict[int, dict] = {}
    instances = []
    for i, (node_id, t, kvs) in enumerate(parsed["nodes"], start=1):
        inst_id[node_id] = i
        node_type[node_id] = t
        m = re.search(r"_(\d+)$", node_id)
        zh_name = (
            f"{type_zh.get(t, t)}_{m.group(1)}" if m else f"{type_zh.get(t, t)}:{node_id}"
        )
        inst_name_zh[node_id] = zh_name
        attribute_values = []
        for k, v, dt in kvs:
            attribute_values.append(dict(
                attributeId=attr_id.get((t, k)), attributeName=_zh.attr_zh(k),
                attributeCode=f"{t}.{k}", dataType=dt, value=v, sourceChunks=[],
            ))
        inst = dict(
            instanceId=i, instanceCode=node_id, instanceName=zh_name,
            objectId=obj_id.get(t, 0), objectName=type_zh.get(t, t),
            attributeValues=attribute_values, linkValues=[], generated=False,
            sourceChunks=[],
        )
        instances.append(inst)
        instances_by_id[i] = inst

    # edges -> linkValues on source instance (Chinese labels)
    for src, rel, tgt in parsed["edges"]:
        if src not in inst_id or tgt not in inst_id:
            continue
        tgt_type = node_type.get(tgt)
        instances_by_id[inst_id[src]]["linkValues"].append(dict(
            linkId=rel_link_id.get(rel), linkInfo=rel_desc.get(rel, rel),
            targetInstanceId=inst_id[tgt], targetInstanceName=inst_name_zh.get(tgt, tgt),
            targetObjectId=obj_id.get(tgt_type),
            targetObjectName=type_zh.get(tgt_type, tgt_type),
            confidence=1.0, sourceChunks=[],
        ))

    # action invocations (Chinese action + instance names)
    invocations = []
    for i, (event_id, action, actor, target, ts) in enumerate(parsed["events"], start=1):
        invocations.append(dict(
            invocationId=i, actionId=act_id.get(action),
            actionName=_zh.action_zh(action),
            inputInstances=[inst_id[actor]] if actor in inst_id else [],
            outputInstances=[inst_id[target]] if target in inst_id else [],
            inputInstanceNames=[inst_name_zh[actor]] if actor in inst_id else [],
            outputInstanceNames=[inst_name_zh[target]] if target in inst_id else [],
            sourceChunks=[],
        ))

    desc = (
        f"从 qwen_eval 评测集抽取（schema={schema_id}，graph={graph_id}）："
        f"{len(objects)} 个对象类型 / {len(links)} 个关系 / {len(actions)} 个动作 / "
        f"{len(instances)} 个实例 / {len(invocations)} 个动作调用。"
    )
    domain_zh = next(
        (zh for pre, zh in _DOMAIN.items() if schema_id.startswith(pre)), ""
    )
    onto_name = f"{domain_zh}·{schema_id}" if domain_zh else schema_id
    return dict(
        ontology=dict(
            ontologyId=1, ontologyName=onto_name, ontologyDesc=desc,
            profile="default", createdAt=NOW, updatedAt=NOW,
        ),
        objects=objects, links=links, actions=actions,
        instances=instances, actionInvocations=invocations,
    )


def main() -> None:
    recs = [json.loads(l) for l in SRC.open(encoding="utf-8") if l.strip()]

    # Group by schema, then parse every record and pick the most COMPLETE one:
    # records vary in how much they inject (some are partial subgraphs without the
    # ActionType / 动作事件 sections), so we score by
    #   (has_actions, has_events, node_count, relation_count, event_count)
    # and take the max — guaranteeing schema + richest graph + events when available.
    by_schema: dict[str, list] = {}
    for r in recs:
        by_schema.setdefault(r["schema_id"], []).append(r)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary = []
    for sid in sorted(by_schema):
        scored = []
        for r in by_schema[sid]:
            p = parse_input(r["input"])
            key = (len(p["actions"]) > 0, len(p["events"]) > 0,
                   len(p["nodes"]), len(p["relations"]), len(p["events"]))
            scored.append((key, r, p))
        scored.sort(key=lambda x: x[0])
        _, rec, parsed = scored[-1]
        onto = build_ontology(sid, rec["graph_id"], parsed)
        out_path = OUT_DIR / f"{sid}.json"
        out_path.write_text(
            json.dumps(onto, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        summary.append((
            sid, rec["graph_id"], len(onto["objects"]), len(onto["links"]),
            len(onto["actions"]), len(onto["instances"]),
            len(onto["actionInvocations"]),
        ))

    print(f"wrote {len(summary)} ontology files to {OUT_DIR}")
    print(f"{'schema_id':<32}{'objs':>5}{'links':>6}{'acts':>5}{'insts':>6}{'invos':>6}")
    for sid, gid, no, nl, na, ni, nv in summary:
        print(f"{sid:<32}{no:>5}{nl:>6}{na:>5}{ni:>6}{nv:>6}")


if __name__ == "__main__":
    main()
