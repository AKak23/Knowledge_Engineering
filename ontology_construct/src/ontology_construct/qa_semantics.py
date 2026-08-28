"""Relation direction semantics (§6).

Loads the traversal policy (how influence propagates along a relation) from a
single data source and builds directed propagation graphs for impact / trace
reasoning. The policy is data, never hard-coded logic.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml

Traversal = Literal["forward", "reverse", "both", "exclude"]

_DEFAULT_YAML = Path(__file__).parent / "relation_semantics_default.yaml"


class SemanticPolicy:
    def __init__(
        self,
        rules: list[tuple[str, Traversal]],
        default_traversal: Traversal = "exclude",
    ) -> None:
        self._rules = [(re.compile(pattern), traversal) for pattern, traversal in rules]
        self._default = default_traversal
        self._cache: dict[str, Traversal] = {}

    def traversal(self, rel: str) -> Traversal:
        rel = str(rel or "")
        cached = self._cache.get(rel)
        if cached is not None:
            return cached
        result: Traversal = self._default
        for pattern, traversal in self._rules:
            if pattern.search(rel):
                result = traversal
                break
        self._cache[rel] = result
        return result


def _policy_from_data(data: dict[str, Any]) -> SemanticPolicy:
    rules: list[tuple[str, Traversal]] = []
    for rule in data.get("rules") or []:
        pattern = rule.get("pattern")
        traversal = rule.get("traversal")
        if pattern and traversal:
            rules.append((str(pattern), traversal))
    default = data.get("default_traversal", "exclude")
    return SemanticPolicy(rules, default)


def load_default_policy() -> SemanticPolicy:
    with open(_DEFAULT_YAML, encoding="utf-8") as handle:
        return _policy_from_data(yaml.safe_load(handle) or {})


def load_policy(job_dir: str | Path | None = None) -> SemanticPolicy:
    """3-level load (§6.2): job-dir override > packaged default.

    (The highest tier — impactDirection produced by ontology construction — is
    consulted per-edge in ``build_impact_digraph`` when present.)
    """
    if job_dir:
        override = Path(job_dir) / "relation_semantics.yaml"
        if override.exists():
            with open(override, encoding="utf-8") as handle:
                return _policy_from_data(yaml.safe_load(handle) or {})
    return load_default_policy()


def build_impact_digraph(
    edges: list[Any],
    policy: SemanticPolicy,
    *,
    view: Literal["impact", "trace"] = "impact",
) -> dict[str, list[tuple[str, str]]]:
    """Directed propagation adjacency: node_id -> [(next_id, rel)].

    An edge's ``impactDirection`` (if the ontology produced one) overrides the
    policy; otherwise the policy's traversal decides. The trace view is the
    impact digraph reversed.
    """
    directed: list[tuple[str, str, str]] = []
    for edge in edges:
        source = str(edge.source)
        target = str(edge.target)
        rel = str(edge.rel)
        traversal = _edge_direction(edge) or policy.traversal(rel)
        if traversal == "exclude":
            continue
        if traversal in ("forward", "both"):
            directed.append((source, target, rel))
        if traversal in ("reverse", "both"):
            directed.append((target, source, rel))

    if view == "trace":
        directed = [(target, source, rel) for source, target, rel in directed]

    adjacency: dict[str, list[tuple[str, str]]] = {}
    for source, target, rel in directed:
        adjacency.setdefault(source, []).append((target, rel))
    return adjacency


def _edge_direction(edge: Any) -> Traversal | None:
    value = getattr(edge, "impact_direction", None)
    if value in ("forward", "reverse", "both", "exclude"):
        return value  # type: ignore[return-value]
    return None
