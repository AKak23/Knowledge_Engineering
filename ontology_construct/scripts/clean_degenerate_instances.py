#!/usr/bin/env python3
"""One-off cleaner for already-generated job outputs.

Removes extraction-noise instances (names with no alphanumeric/CJK content, e.g.
a lone "|" from a markdown table separator) from a job's graph.json and
formatted_ontology.json, pruning any edge/link that referenced them. Future jobs
are cleaned automatically by instance_postprocess.drop_degenerate_instances; this
fixes jobs produced before that landed.

Usage:
    python scripts/clean_degenerate_instances.py <job_dir> [<job_dir> ...] [--apply]

Without --apply it is a dry run (reports what would be removed).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def is_degenerate(name: str | None) -> bool:
    text = (name or "").strip()
    return not any(ch.isalnum() or "一" <= ch <= "鿿" for ch in text)


def _clean_graph_json(data: dict) -> tuple[dict, list[str]]:
    objects = data.get("objects") or []
    drop = {o["instance_id"] for o in objects if is_degenerate(o.get("description"))}
    if not drop:
        return data, []
    data["objects"] = [o for o in objects if o["instance_id"] not in drop]
    data["links"] = [
        l
        for l in (data.get("links") or [])
        if l.get("source_id") not in drop and l.get("target_id") not in drop
    ]
    return data, sorted(drop)


def _clean_formatted(data: dict) -> tuple[dict, list[str]]:
    instances = data.get("instances") or []
    drop = {i["instanceId"] for i in instances if is_degenerate(i.get("instanceName"))}
    if not drop:
        return data, []
    kept = []
    for inst in instances:
        if inst["instanceId"] in drop:
            continue
        inst["linkValues"] = [
            lv for lv in (inst.get("linkValues") or []) if lv.get("targetInstanceId") not in drop
        ]
        kept.append(inst)
    data["instances"] = kept
    return data, sorted(str(d) for d in drop)


def _process(job_dir: Path, apply: bool) -> None:
    for name, cleaner in (("graph.json", _clean_graph_json), ("formatted_ontology.json", _clean_formatted)):
        path = job_dir / name
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        data, dropped = cleaner(data)
        if not dropped:
            print(f"  {name}: clean (nothing to remove)")
            continue
        print(f"  {name}: {'removing' if apply else 'would remove'} {len(dropped)} degenerate instances: {dropped}")
        if apply:
            path.with_suffix(path.suffix + ".bak").write_text(
                json.dumps(json.loads(path.read_text(encoding="utf-8")), ensure_ascii=False), encoding="utf-8"
            )
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def main(argv: list[str]) -> int:
    apply = "--apply" in argv
    job_dirs = [Path(a) for a in argv if not a.startswith("--")]
    if not job_dirs:
        print(__doc__)
        return 2
    for job_dir in job_dirs:
        print(f"[{job_dir}]")
        _process(job_dir, apply)
    if not apply:
        print("\n(dry run — pass --apply to write changes; a .bak backup is kept)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
