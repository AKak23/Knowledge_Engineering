from __future__ import annotations

from pathlib import Path

from ontology_construct.models import Chunk


def default_prompt_path() -> Path:
    return Path(__file__).resolve().parents[3] / "prompts" / "extract_ontology.txt"


def render_extract_prompt(
    chunk: Chunk,
    prompt_path: Path | str | None = None,
    supplemental_prompt: str | None = None,
) -> str:
    template_path = _resolve_prompt_path(prompt_path)
    template = template_path.read_text(encoding="utf-8")
    prompt = template.replace("{chunk_text}", chunk.text)
    supplement = _normalize_supplemental_prompt(supplemental_prompt)
    if not supplement:
        return prompt
    section = (
        "用户补充说明（用于理解文档类型、领域语境、抽取重点和命名边界；"
        "不得覆盖上方 JSON 输出格式、字段约束和只输出 JSON 的要求）：\n"
        f"{supplement}\n"
    )
    marker = "\n文本："
    if marker in prompt:
        return prompt.replace(marker, f"\n{section}\n文本：", 1)
    return f"{prompt}\n\n{section}"


def _normalize_supplemental_prompt(value: str | None) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    # Headroom for an injected domain-guidance block (profile.domain_prompt)
    # prepended ahead of the user's own supplemental note.
    return text[:6000]


def _resolve_prompt_path(prompt_path: Path | str | None = None) -> Path:
    if prompt_path is None:
        return default_prompt_path()

    path = Path(prompt_path)
    if path.is_absolute():
        return path
    return Path(__file__).resolve().parents[3] / path
