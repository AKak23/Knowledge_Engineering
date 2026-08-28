from __future__ import annotations

import json
from pathlib import Path

from ontology_construct.config import ConceptConfig
from ontology_construct.models import Chunk


def render_concept_candidate_prompt(
    chunk: Chunk,
    config: ConceptConfig,
    supplemental_prompt: str | None = None,
) -> str:
    template = _resolve_prompt_path(config.candidate_prompt).read_text(encoding="utf-8")
    values = {
        "chunk_text": chunk.text,
        "max_candidates": str(config.max_candidates_per_chunk),
        "abstraction_level": config.abstraction_level,
        "relation_vocab": "、".join(config.relation_vocab),
        "supplemental_prompt": _supplemental_section(supplemental_prompt),
    }
    return _format_template(template, values)


def render_concept_generalize_prompt(
    payload: dict,
    config: ConceptConfig,
    supplemental_prompt: str | None = None,
) -> str:
    template = _resolve_prompt_path(config.generalize_prompt).read_text(encoding="utf-8")
    values = {
        "candidate_json": json.dumps(payload, ensure_ascii=False, indent=2),
        "max_concepts": str(config.max_concepts),
        "min_evidence": str(config.min_evidence),
        "abstraction_level": config.abstraction_level,
        "relation_vocab": "、".join(config.relation_vocab),
        "supplemental_prompt": _supplemental_section(supplemental_prompt),
    }
    return _format_template(template, values)


def render_concept_relation_prompt(
    payload: dict,
    config: ConceptConfig,
    supplemental_prompt: str | None = None,
) -> str:
    template = _resolve_prompt_path(config.relation_prompt).read_text(encoding="utf-8")
    values = {
        "relation_json": json.dumps(payload, ensure_ascii=False, indent=2),
        "relation_vocab": "、".join(config.relation_vocab),
        "supplemental_prompt": _supplemental_section(supplemental_prompt),
    }
    return _format_template(template, values)


def render_concept_instance_prompt(
    payload: dict,
    config: ConceptConfig,
    supplemental_prompt: str | None = None,
) -> str:
    template = _resolve_prompt_path(config.instance_prompt).read_text(encoding="utf-8")
    values = {
        "instance_json": json.dumps(payload, ensure_ascii=False, indent=2),
        "max_instances_per_concept": str(config.max_instances_per_concept),
        "supplemental_prompt": _supplemental_section(supplemental_prompt),
    }
    return _format_template(template, values)


def render_chunk_instance_prompt(
    payload: dict,
    config: ConceptConfig,
    supplemental_prompt: str | None = None,
) -> str:
    template = _resolve_prompt_path(config.chunk_instance_prompt).read_text(
        encoding="utf-8"
    )
    values = {
        "instance_json": json.dumps(payload, ensure_ascii=False, indent=2),
        "max_instances_per_concept": str(config.max_instances_per_concept),
        "relation_vocab": "、".join(config.relation_vocab),
        "supplemental_prompt": _supplemental_section(supplemental_prompt),
    }
    return _format_template(template, values)


def render_instance_relation_prompt(
    payload: dict,
    config: ConceptConfig,
    supplemental_prompt: str | None = None,
) -> str:
    template = _resolve_prompt_path(config.instance_relation_prompt).read_text(
        encoding="utf-8"
    )
    values = {
        "instance_relation_json": json.dumps(payload, ensure_ascii=False, indent=2),
        "relation_vocab": "、".join(config.relation_vocab),
        "supplemental_prompt": _supplemental_section(supplemental_prompt),
    }
    return _format_template(template, values)


def _resolve_prompt_path(prompt_path: Path | str) -> Path:
    path = Path(prompt_path)
    if path.is_absolute():
        return path
    return Path(__file__).resolve().parents[3] / path


def _supplemental_section(value: str | None) -> str:
    text = (value or "").strip()
    if not text:
        return "无"
    # Headroom for an injected domain-guidance block (profile.domain_prompt)
    # prepended ahead of the user's own supplemental note.
    return text[:6000]


def _format_template(template: str, values: dict[str, str]) -> str:
    result = template
    for key, value in values.items():
        result = result.replace("{" + key + "}", value)
    return result
