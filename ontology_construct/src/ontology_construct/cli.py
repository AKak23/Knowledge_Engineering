from __future__ import annotations

import json
from pathlib import Path
from typing import Callable
import asyncio

import click
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

from ontology_construct.concept import (
    ChunkConceptCandidates,
    ConceptCandidateExtractor,
    ConceptGeneralizer,
    ConceptClusterReport,
)
from ontology_construct.config import (
    AppConfig,
    OntologyProfileConfig,
    load_config,
    profile_domain_text,
)
from ontology_construct.embedding import create_embedding_client
from ontology_construct.extractor import LocalExtractor
from ontology_construct.input import read_text
from ontology_construct.instance_postprocess import (
    InstancePostprocessStats,
    needs_instance_connectivity_repair,
    repair_instance_connectivity,
)
from ontology_construct.action_builder import ActionBuildStats, build_actions_from_instances
from ontology_construct.instance_builder import InstanceBuildReport, InstanceBuilder
from ontology_construct.llm import aclose_llm_client, create_llm_client
from ontology_construct.merger import EntityDeduplicator, OntologyMerger
from ontology_construct.models import Chunk, GlobalOntology, LocalOntology
from ontology_construct.output import build_canonical_ontology, format_global_ontology
from ontology_construct.preprocessor import TextChunker, clean_text
from ontology_construct.storage import ProgressTracker, load_json_model, save_json_model


def _schema_identity(profile: str, ontology_name: str) -> tuple[str, str, str]:
    import re

    slug = re.sub(r"[^0-9A-Za-z_]+", "_", ontology_name.strip().lower()).strip("_")[:64]
    schema_id = slug or profile
    return schema_id, profile, slug or profile


@click.group()
@click.version_option()
def cli() -> None:
    """Build ontologies from long text documents."""


@cli.command("chunk")
@click.option(
    "--input",
    "input_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Input TXT, Markdown, or PDF file.",
)
@click.option(
    "--output",
    "output_path",
    required=True,
    type=click.Path(dir_okay=False, path_type=Path),
    help="Output JSONL file for chunks.",
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="YAML config path. Defaults to configs/default.yaml.",
)
@click.option("--chunk-size", type=int, help="Override configured chunk token size.")
@click.option("--chunk-overlap", type=int, help="Override configured chunk token overlap.")
def chunk_command(
    input_path: Path,
    output_path: Path,
    config_path: Path | None,
    chunk_size: int | None,
    chunk_overlap: int | None,
) -> None:
    """Clean and split a long text file into chunk JSONL."""
    config = load_config(config_path)
    if chunk_size is not None:
        config.chunk.size = chunk_size
    if chunk_overlap is not None:
        config.chunk.overlap = chunk_overlap

    chunks = _prepare_chunks(input_path, config)

    _write_chunks_jsonl(chunks, output_path)

    click.echo(f"Wrote {len(chunks)} chunks to {output_path}")


@cli.command("extract")
@click.option(
    "--input",
    "input_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Input TXT, Markdown, or PDF file.",
)
@click.option(
    "--output-dir",
    required=True,
    type=click.Path(file_okay=False, path_type=Path),
    help="Run output directory.",
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="YAML config path. Defaults to configs/default.yaml.",
)
@click.option(
    "--backend",
    type=click.Choice(
        [
            "mock",
            "deepseek",
            "kimi",
            "qwen",
            "qwen-plus",
            "qwen3-8b",
            "jiutian",
            "jiutian-35b",
            "qwen-32b",
        ]
    ),
    help="LLM backend. Defaults to config llm.backend.",
)
@click.option(
    "--profile",
    default="default",
    show_default=True,
    help="Ontology profile name configured under ontology_profiles.",
)
@click.option("--chunk-size", type=int, help="Override configured chunk token size.")
@click.option("--chunk-overlap", type=int, help="Override configured chunk token overlap.")
@click.option(
    "--llm-timeout",
    type=click.IntRange(min=1),
    help="Override LLM request timeout seconds.",
)
@click.option(
    "--llm-retries",
    type=click.IntRange(min=0),
    help="Override LLM max retry count.",
)
@click.option(
    "--llm-concurrency",
    type=click.IntRange(min=1),
    help="Override LLM request concurrency.",
)
def extract_command(
    input_path: Path,
    output_dir: Path,
    config_path: Path | None,
    backend: str,
    profile: str,
    chunk_size: int | None,
    chunk_overlap: int | None,
    llm_timeout: int | None,
    llm_retries: int | None,
    llm_concurrency: int | None,
) -> None:
    """Run local ontology extraction for each chunk."""
    config = load_config(config_path)
    if chunk_size is not None:
        config.chunk.size = chunk_size
    if chunk_overlap is not None:
        config.chunk.overlap = chunk_overlap
    _apply_llm_overrides(config, llm_timeout, llm_retries, llm_concurrency)

    chunks = _prepare_chunks(input_path, config)

    try:
        profile_config = config.get_profile(profile)
        success_count, failure_count, skipped_count = _run_extract(
            chunks=chunks,
            output_dir=output_dir,
            config=config,
            backend=backend,
            prompt_path=profile_config.prompt,
        )
    except (RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"Extracted {success_count} chunks into {output_dir / 'local'}; "
        f"skipped {skipped_count}; failed {failure_count}"
    )


@cli.command("status")
@click.option(
    "--run-dir",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Run output directory.",
)
def status_command(run_dir: Path) -> None:
    """Show chunk processing status for a run directory."""
    db_path = run_dir / "progress.sqlite"
    if not db_path.exists():
        raise click.ClickException(f"Progress database not found: {db_path}")

    tracker = ProgressTracker(db_path)
    try:
        counts = tracker.counts()
    finally:
        tracker.close()

    click.echo(
        f"pending={counts['pending']} done={counts['done']} failed={counts['failed']}"
    )


@cli.command("merge")
@click.option(
    "--local-dir",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Directory containing local ontology JSON files.",
)
@click.option(
    "--output",
    "output_path",
    required=True,
    type=click.Path(dir_okay=False, path_type=Path),
    help="Output global ontology JSON file.",
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="YAML config path. Defaults to configs/default.yaml.",
)
@click.option(
    "--use-embedding/--no-use-embedding",
    default=False,
    show_default=True,
    help="Use configured embedding model for fuzzy entity/concept deduplication.",
)
@click.option(
    "--profile",
    default="default",
    show_default=True,
    help="Ontology profile name used for formatted output metadata.",
)
@click.option(
    "--formatted-output",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Optional formatted ontology JSON output path.",
)
@click.option(
    "--schema-output",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Optional canonical schema (type layer) JSON output path.",
)
@click.option(
    "--graph-output",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Optional canonical graph (instance layer) JSON output path.",
)
@click.option(
    "--instance-count",
    default=0,
    show_default=True,
    type=int,
    help="Generate this many example instances in formatted output.",
)
@click.option(
    "--max-extracted-instances",
    type=click.IntRange(min=0),
    help="Keep at most this many real extracted instances in merged outputs.",
)
@click.option(
    "--embedding-threshold",
    type=float,
    help="Override configured embedding similarity threshold.",
)
def merge_command(
    local_dir: Path,
    output_path: Path,
    config_path: Path | None,
    use_embedding: bool,
    profile: str,
    formatted_output: Path | None,
    schema_output: Path | None,
    graph_output: Path | None,
    instance_count: int,
    max_extracted_instances: int | None,
    embedding_threshold: float | None,
) -> None:
    """Merge local ontology JSON files into one global ontology."""
    config = load_config(config_path)
    if embedding_threshold is not None:
        config.merge.similarity_threshold = embedding_threshold

    local_files = _json_model_files(local_dir)
    if not local_files:
        raise click.ClickException(f"No local ontology JSON files found in {local_dir}")

    try:
        profile_config = config.get_profile(profile)
        global_ontology = _merge_local_ontologies(
            local_files=local_files,
            config=config,
            use_embedding=use_embedding,
        )
        global_ontology = _limit_extracted_instances(
            global_ontology,
            max_extracted_instances,
        )
    except (RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc

    save_json_model(global_ontology, output_path)
    if formatted_output is not None:
        save_json_model(
            format_global_ontology(
                global_ontology,
                ontology_name=profile_config.ontology_name,
                ontology_desc=profile_config.ontology_desc,
                profile=profile,
                generated_instance_count=instance_count,
            ),
            formatted_output,
        )
    if schema_output is not None or graph_output is not None:
        schema_id, scenario, sub_scenario = _schema_identity(
            profile, profile_config.ontology_name
        )
        export = build_canonical_ontology(
            global_ontology,
            schema_id=schema_id,
            scenario=scenario,
            sub_scenario=sub_scenario,
        )
        if schema_output is not None:
            save_json_model(export.schema_def, schema_output)
        if graph_output is not None:
            save_json_model(export.graph, graph_output)
    click.echo(
        f"Merged {len(local_files)} local ontologies into "
        f"{len(global_ontology.entities)} entities and {len(global_ontology.triples)} triples"
    )


@cli.command("build")
@click.option(
    "--input",
    "input_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Input TXT, Markdown, or PDF file.",
)
@click.option(
    "--output-dir",
    required=True,
    type=click.Path(file_okay=False, path_type=Path),
    help="Run output directory.",
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="YAML config path. Defaults to configs/default.yaml.",
)
@click.option(
    "--backend",
    type=click.Choice(
        [
            "mock",
            "deepseek",
            "kimi",
            "qwen",
            "qwen-plus",
            "qwen3-8b",
            "jiutian",
            "jiutian-35b",
            "qwen-32b",
        ]
    ),
    help="LLM backend. Defaults to config llm.backend.",
)
@click.option(
    "--profile",
    default="default",
    show_default=True,
    help="Ontology profile name configured under ontology_profiles.",
)
@click.option("--chunk-size", type=int, help="Override configured chunk token size.")
@click.option("--chunk-overlap", type=int, help="Override configured chunk token overlap.")
@click.option(
    "--use-embedding/--no-use-embedding",
    default=False,
    show_default=True,
    help="Use configured embedding model for fuzzy entity deduplication.",
)
@click.option(
    "--embedding-threshold",
    type=float,
    help="Override configured embedding similarity threshold.",
)
@click.option(
    "--instance-count",
    default=0,
    show_default=True,
    type=int,
    help="Generate this many example instances in formatted_ontology.json.",
)
@click.option(
    "--max-extracted-instances",
    type=click.IntRange(min=0),
    help="Keep at most this many real extracted instances in merged outputs.",
)
@click.option(
    "--llm-timeout",
    type=click.IntRange(min=1),
    help="Override LLM request timeout seconds.",
)
@click.option(
    "--llm-retries",
    type=click.IntRange(min=0),
    help="Override LLM max retry count.",
)
@click.option(
    "--llm-concurrency",
    type=click.IntRange(min=1),
    help="Override LLM request concurrency.",
)
@click.option(
    "--construction-mode",
    type=click.Choice(["standard", "schema-first"]),
    default="standard",
    show_default=True,
    help="Ontology construction strategy.",
)
@click.option(
    "--max-concepts",
    type=click.IntRange(min=1),
    help="Schema-first: maximum final concept count.",
)
@click.option(
    "--min-concept-evidence",
    type=click.IntRange(min=0),
    help="Schema-first: minimum evidence count for keeping low-connectivity concepts.",
)
@click.option(
    "--abstraction-level",
    type=click.Choice(["high", "medium", "low"]),
    help="Schema-first: preferred concept abstraction level.",
)
@click.option(
    "--drop-instances/--keep-instances",
    default=False,
    show_default=True,
    help="Schema-first: drop instance-layer data from final output.",
)
def build_command(
    input_path: Path,
    output_dir: Path,
    config_path: Path | None,
    backend: str | None,
    profile: str,
    chunk_size: int | None,
    chunk_overlap: int | None,
    use_embedding: bool,
    embedding_threshold: float | None,
    instance_count: int,
    max_extracted_instances: int | None,
    llm_timeout: int | None,
    llm_retries: int | None,
    llm_concurrency: int | None,
    construction_mode: str,
    max_concepts: int | None,
    min_concept_evidence: int | None,
    abstraction_level: str | None,
    drop_instances: bool,
) -> None:
    """Run extract, merge, and JSON export in one command."""
    config = load_config(config_path)
    if chunk_size is not None:
        config.chunk.size = chunk_size
    if chunk_overlap is not None:
        config.chunk.overlap = chunk_overlap
    if embedding_threshold is not None:
        config.merge.similarity_threshold = embedding_threshold
    if max_concepts is not None:
        config.concept.max_concepts = max_concepts
    if min_concept_evidence is not None:
        config.concept.min_evidence = min_concept_evidence
    if abstraction_level is not None:
        config.concept.abstraction_level = abstraction_level
    _apply_llm_overrides(config, llm_timeout, llm_retries, llm_concurrency)
    if construction_mode == "schema-first" and config.instance_builder.enabled:
        config.concept.extract_real_instances = False
        config.concept.extract_instance_relations = False

    chunks = _prepare_chunks(input_path, config)
    try:
        profile_config = config.get_profile(profile)
        _apply_profile_overrides(config, profile_config)
        supplemental = _compose_supplemental(profile_config, None)
        if construction_mode == "schema-first":
            global_ontology, success_count, failure_count, skipped_count = (
                _run_schema_first_build(
                    chunks=chunks,
                    output_dir=output_dir,
                    config=config,
                    backend=backend,
                    supplemental_prompt=supplemental,
                    drop_instances=drop_instances,
                    use_embedding=use_embedding,
                )
            )
            global_ontology, _instance_report = _apply_instance_builder(
                global_ontology=global_ontology,
                input_paths=[input_path],
                config=config,
                backend=backend,
                output_dir=output_dir,
            )
            global_ontology, _postprocess_stats = _apply_instance_postprocess(
                global_ontology=global_ontology,
                config=config,
                backend=backend,
                output_dir=output_dir,
            )
            _save_build_outputs(
                global_ontology=global_ontology,
                output_dir=output_dir,
                profile=profile,
                profile_config=profile_config,
                instance_count=instance_count,
            )
            click.echo(
                f"Schema-first build complete: extracted={success_count} "
                f"skipped={skipped_count} failed={failure_count} "
                f"concepts={len(global_ontology.entities)} "
                f"relations={len(global_ontology.triples)} "
                f"output={output_dir / 'ontology.json'} "
                f"formatted_output={output_dir / 'formatted_ontology.json'} "
                f"schema_output={output_dir / 'schema.json'} "
                f"graph_output={output_dir / 'graph.json'}"
            )
            return
        success_count, failure_count, skipped_count = _run_extract(
            chunks=chunks,
            output_dir=output_dir,
            config=config,
            backend=backend,
            prompt_path=profile_config.prompt,
            supplemental_prompt=supplemental,
        )
        local_files = _json_model_files(output_dir / "local")
        if not local_files and failure_count:
            raise ValueError(
                _missing_local_ontologies_message(output_dir, failure_count)
            )
        global_ontology = _merge_local_ontologies(
            local_files=local_files,
            config=config,
            use_embedding=use_embedding,
        )
        global_ontology = _limit_extracted_instances(
            global_ontology,
            max_extracted_instances,
        )
        global_ontology, _postprocess_stats = _apply_instance_postprocess(
            global_ontology=global_ontology,
            config=config,
            backend=backend,
            output_dir=output_dir,
        )
    except (RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc

    global_path = output_dir / "global.json"
    final_path = output_dir / "ontology.json"
    formatted_path = output_dir / "formatted_ontology.json"
    schema_path = output_dir / "schema.json"
    graph_path = output_dir / "graph.json"
    save_json_model(global_ontology, global_path)
    save_json_model(global_ontology, final_path)
    save_json_model(
        format_global_ontology(
            global_ontology,
            ontology_name=profile_config.ontology_name,
            ontology_desc=profile_config.ontology_desc,
            profile=profile,
            generated_instance_count=instance_count,
        ),
        formatted_path,
    )
    schema_id, scenario, sub_scenario = _schema_identity(
        profile, profile_config.ontology_name
    )
    export = build_canonical_ontology(
        global_ontology,
        schema_id=schema_id,
        scenario=scenario,
        sub_scenario=sub_scenario,
    )
    save_json_model(export.schema_def, schema_path)
    save_json_model(export.graph, graph_path)
    click.echo(
        f"Build complete: extracted={success_count} skipped={skipped_count} "
        f"failed={failure_count} entities={len(global_ontology.entities)} "
        f"triples={len(global_ontology.triples)} output={final_path} "
        f"formatted_output={formatted_path} schema_output={schema_path} "
        f"graph_output={graph_path}"
    )


def _save_build_outputs(
    *,
    global_ontology: GlobalOntology,
    output_dir: Path,
    profile: str,
    profile_config,
    instance_count: int,
) -> None:
    global_path = output_dir / "global.json"
    final_path = output_dir / "ontology.json"
    formatted_path = output_dir / "formatted_ontology.json"
    schema_path = output_dir / "schema.json"
    graph_path = output_dir / "graph.json"
    save_json_model(global_ontology, global_path)
    save_json_model(global_ontology, final_path)
    save_json_model(
        format_global_ontology(
            global_ontology,
            ontology_name=profile_config.ontology_name,
            ontology_desc=profile_config.ontology_desc,
            profile=profile,
            generated_instance_count=instance_count,
        ),
        formatted_path,
    )
    schema_id, scenario, sub_scenario = _schema_identity(
        profile, profile_config.ontology_name
    )
    export = build_canonical_ontology(
        global_ontology,
        schema_id=schema_id,
        scenario=scenario,
        sub_scenario=sub_scenario,
    )
    save_json_model(export.schema_def, schema_path)
    save_json_model(export.graph, graph_path)


def _apply_instance_postprocess(
    *,
    global_ontology: GlobalOntology,
    config: AppConfig,
    backend: str | None,
    output_dir: Path | None = None,
    stage_callback: Callable[[str], None] | None = None,
) -> tuple[GlobalOntology, InstancePostprocessStats | None]:
    if not needs_instance_connectivity_repair(
        global_ontology,
        config.instance_postprocess,
    ):
        return global_ontology, None
    if stage_callback is not None:
        stage_callback("实例连通性兜底")
    llm_client = create_llm_client(config.llm, backend)

    async def run() -> tuple[GlobalOntology, InstancePostprocessStats]:
        try:
            return await repair_instance_connectivity(
                global_ontology,
                llm_client,
                config.instance_postprocess,
            )
        finally:
            await aclose_llm_client(llm_client)

    repaired, stats = asyncio.run(run())
    if output_dir is not None:
        (output_dir / "instance_postprocess.json").write_text(
            json.dumps(stats.as_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return repaired, stats


def _apply_instance_builder(
    *,
    global_ontology: GlobalOntology,
    input_paths: list[Path],
    config: AppConfig,
    backend: str | None,
    output_dir: Path | None = None,
    supplemental_prompt: str | None = None,
    stage_callback: Callable[[str], None] | None = None,
) -> tuple[GlobalOntology, InstanceBuildReport | None]:
    if not config.instance_builder.enabled:
        return global_ontology, None
    if stage_callback is not None:
        stage_callback("独立实例抽取")
    needs_llm = (
        config.instance_builder.llm_enabled_for_unresolved
        and any(
            path.suffix.lower() not in {".log", ".json", ".jsonl"}
            for path in input_paths
        )
    )
    llm_client = create_llm_client(config.llm, backend) if needs_llm else None

    async def run() -> tuple[GlobalOntology, InstanceBuildReport]:
        try:
            return await InstanceBuilder(
                config,
                llm_client=llm_client,
                supplemental_prompt=supplemental_prompt,
            ).build(global_ontology, input_paths, output_dir)
        finally:
            if llm_client is not None:
                await aclose_llm_client(llm_client)

    return asyncio.run(run())


def _apply_action_builder(
    *,
    global_ontology: GlobalOntology,
    config: AppConfig,
    profile_config: OntologyProfileConfig | None = None,
    output_dir: Path | None = None,
    stage_callback: Callable[[str], None] | None = None,
) -> tuple[GlobalOntology, ActionBuildStats | None]:
    if not config.action_builder.enabled:
        return global_ontology, None
    builder_config = config.action_builder
    extra_keywords = list(profile_config.action_vocab) if profile_config else []
    if extra_keywords:
        builder_config = builder_config.model_copy(
            update={
                "event_type_keywords": list(
                    dict.fromkeys([*builder_config.event_type_keywords, *extra_keywords])
                )
            }
        )
    if stage_callback is not None:
        stage_callback("动作派生")
    updated, stats = build_actions_from_instances(global_ontology, builder_config)
    if output_dir is not None:
        (output_dir / "action_build.json").write_text(
            json.dumps(stats.as_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return updated, stats


@cli.command("export")
@click.option(
    "--ontology",
    "ontology_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Global ontology JSON file.",
)
@click.option(
    "--format",
    "output_format",
    required=True,
    type=click.Choice(["json"]),
    help="Export format.",
)
@click.option(
    "--output",
    "output_path",
    required=True,
    type=click.Path(dir_okay=False, path_type=Path),
    help="Export output path.",
)
def export_command(
    ontology_path: Path,
    output_format: str,
    output_path: Path,
) -> None:
    """Export a global ontology as standard JSON."""
    ontology = load_json_model(GlobalOntology, ontology_path)
    if output_format != "json":
        raise click.ClickException(f"Unsupported export format: {output_format}")
    save_json_model(ontology, output_path)
    click.echo(f"Exported {output_format} to {output_path}")


def _write_chunks_jsonl(chunks: list[Chunk], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(json.dumps(chunk.model_dump(), ensure_ascii=False))
            f.write("\n")


def _json_model_files(directory: Path) -> list[Path]:
    return sorted(
        path
        for path in directory.glob("*.json")
        if path.is_file() and not path.name.startswith("._")
    )


def _prepare_chunks(input_path: Path, config: AppConfig) -> list[Chunk]:
    raw_text = read_text(input_path)
    cleaned = clean_text(raw_text)
    return TextChunker(config.chunk).split(cleaned)


def _prepare_chunks_for_inputs(input_paths: list[Path], config: AppConfig) -> list[Chunk]:
    all_chunks: list[Chunk] = []
    for file_index, input_path in enumerate(input_paths, start=1):
        for chunk in _prepare_chunks(input_path, config):
            chunk.metadata.update(
                {
                    "source_file": input_path.name,
                    "source_file_index": file_index,
                    "source_chunk_id": chunk.id,
                }
            )
            chunk.id = f"file_{file_index:03d}_{chunk.id}"
            all_chunks.append(chunk)
    return all_chunks


def _compose_supplemental(
    profile_config: OntologyProfileConfig,
    supplemental_prompt: str | None,
) -> str:
    """Prepend the profile's domain guidance (if any) to the user's
    supplemental prompt so both the standard and schema-first prompts carry the
    scenario modeling conventions. Domain text comes first so that, if the
    combined string is later length-capped, the user's note is trimmed before
    the domain guidance."""
    user_text = (supplemental_prompt or "").strip()
    domain_text = profile_domain_text(profile_config)
    if not domain_text:
        return user_text
    if not user_text:
        return f"【领域建模背景知识】\n{domain_text}"
    return (
        f"【领域建模背景知识】\n{domain_text}\n\n"
        f"【本次任务补充说明】\n{user_text}"
    )


def _apply_profile_overrides(
    config: AppConfig,
    profile_config: OntologyProfileConfig,
) -> None:
    """Apply profile-scoped config overrides (currently the schema-first
    relation vocabulary). Standard mode ignores relation_vocab, so this is a
    no-op there."""
    if profile_config.relation_vocab:
        config.concept.relation_vocab = list(profile_config.relation_vocab)


def _apply_llm_overrides(
    config: AppConfig,
    timeout_seconds: int | None,
    max_retries: int | None,
    concurrency: int | None,
) -> None:
    if timeout_seconds is not None:
        config.llm.timeout_seconds = timeout_seconds
    if max_retries is not None:
        config.llm.max_retries = max_retries
    if concurrency is not None:
        config.llm.concurrency = concurrency


def _run_extract(
    chunks: list[Chunk],
    output_dir: Path,
    config: AppConfig,
    backend: str | None,
    prompt_path: str | None = None,
    supplemental_prompt: str | None = None,
) -> tuple[int, int, int]:
    local_dir = output_dir / "local"
    local_dir.mkdir(parents=True, exist_ok=True)
    _write_chunks_jsonl(chunks, output_dir / "chunks.jsonl")
    extractor = LocalExtractor(
        create_llm_client(config.llm, backend),
        prompt_path,
        supplemental_prompt,
    )
    tracker = ProgressTracker(output_dir / "progress.sqlite")
    tracker.register_chunks(chunks)

    async def run() -> tuple[int, int, int]:
        success_count = 0
        failure_count = 0
        skipped_count = 0
        running_count = 0
        semaphore = asyncio.Semaphore(config.llm.concurrency)

        def status_text() -> str:
            return (
                f"running={running_count} done={success_count} "
                f"skipped={skipped_count} failed={failure_count}"
            )

        async def extract_one(chunk: Chunk) -> tuple[str, str]:
            nonlocal running_count
            if tracker.is_done(chunk.id):
                return "skipped", chunk.id
            try:
                async with semaphore:
                    running_count += 1
                    progress.update(task_id, status=status_text())
                    try:
                        local_ontology = await extractor.extract(chunk)
                    finally:
                        running_count -= 1
                        progress.update(task_id, status=status_text())
                save_json_model(local_ontology, local_dir / f"{chunk.id}.json")
                tracker.mark_done(chunk.id, local_ontology)
                return "success", chunk.id
            except Exception as exc:  # noqa: BLE001 - keep chunk failures isolated.
                tracker.mark_failed(chunk.id, _format_chunk_error(exc))
                return "failed", chunk.id

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TextColumn("{task.fields[status]}"),
            TimeElapsedColumn(),
        ) as progress:
            task_id = progress.add_task(
                "Extracting chunks",
                total=len(chunks),
                status=status_text(),
            )
            tasks = [asyncio.create_task(extract_one(chunk)) for chunk in chunks]
            for completed in asyncio.as_completed(tasks):
                status, _chunk_id = await completed
                if status == "success":
                    success_count += 1
                elif status == "skipped":
                    skipped_count += 1
                elif status == "failed":
                    failure_count += 1
                progress.update(
                    task_id,
                    advance=1,
                    status=status_text(),
                )
        return success_count, failure_count, skipped_count

    async def run_with_cleanup() -> tuple[int, int, int]:
        try:
            return await run()
        finally:
            await aclose_llm_client(extractor.llm_client)

    try:
        return asyncio.run(run_with_cleanup())
    finally:
        tracker.close()


def _run_schema_first_build(
    *,
    chunks: list[Chunk],
    output_dir: Path,
    config: AppConfig,
    backend: str | None,
    supplemental_prompt: str | None,
    drop_instances: bool,
    use_embedding: bool = False,
    stage_callback: Callable[[str], None] | None = None,
) -> tuple[GlobalOntology, int, int, int]:
    if stage_callback is not None:
        stage_callback("候选概念抽取")
    candidates, success_count, failure_count, skipped_count = (
        _run_concept_candidate_extract(
            chunks=chunks,
            output_dir=output_dir,
            config=config,
            backend=backend,
            supplemental_prompt=supplemental_prompt,
        )
    )
    if not candidates and failure_count:
        raise ValueError(
            "No concept candidate JSON files found. "
            f"Candidate extraction produced no successful chunk results ({failure_count} failed)."
        )
    embedding_client = None
    if use_embedding or config.concept.use_embedding_clustering:
        config.concept.use_embedding_clustering = True
        embedding_client = create_embedding_client(config.merge)
    generalizer = ConceptGeneralizer(
        create_llm_client(config.llm, backend),
        config.concept,
        supplemental_prompt,
        stage_callback=stage_callback,
        max_concurrency=config.llm.concurrency,
        chunk_texts={chunk.id: chunk.text for chunk in chunks},
        embedding_client=embedding_client,
    )

    async def _generalize() -> object:
        try:
            return await generalizer.generalize_with_artifacts(candidates)
        finally:
            await aclose_llm_client(generalizer.llm_client)

    artifacts = asyncio.run(_generalize())
    if stage_callback is not None:
        stage_callback("写出构建结果")
    global_ontology = artifacts.ontology
    if drop_instances:
        global_ontology = global_ontology.model_copy(
            update={"instances": [], "action_invocations": []}
        )
    save_json_model(global_ontology, output_dir / "concept_global.json")
    save_json_model(
        ConceptClusterReport(clusters=artifacts.clusters),
        output_dir / "concept_clusters.json",
    )
    save_json_model(artifacts.selection, output_dir / "concept_selected.json")
    save_json_model(artifacts.relation_report, output_dir / "concept_relations.json")
    save_json_model(artifacts.instance_report, output_dir / "concept_instances.json")
    save_json_model(artifacts.quality, output_dir / "ontology_quality.json")
    return global_ontology, success_count, failure_count, skipped_count


def _run_concept_candidate_extract(
    *,
    chunks: list[Chunk],
    output_dir: Path,
    config: AppConfig,
    backend: str | None,
    supplemental_prompt: str | None,
) -> tuple[list[ChunkConceptCandidates], int, int, int]:
    candidate_dir = output_dir / "concept_candidates"
    candidate_dir.mkdir(parents=True, exist_ok=True)
    _write_chunks_jsonl(chunks, output_dir / "chunks.jsonl")
    extractor = ConceptCandidateExtractor(
        create_llm_client(config.llm, backend),
        config.concept,
        supplemental_prompt,
    )
    tracker = ProgressTracker(output_dir / "progress.sqlite")
    tracker.register_chunks(chunks)

    async def run() -> tuple[list[ChunkConceptCandidates], int, int, int]:
        success_count = 0
        failure_count = 0
        skipped_count = 0
        running_count = 0
        results: list[ChunkConceptCandidates] = []
        semaphore = asyncio.Semaphore(config.llm.concurrency)

        def status_text() -> str:
            return (
                f"running={running_count} done={success_count} "
                f"skipped={skipped_count} failed={failure_count}"
            )

        async def extract_one(chunk: Chunk) -> tuple[str, ChunkConceptCandidates | None]:
            nonlocal running_count
            candidate_path = candidate_dir / f"{chunk.id}.json"
            if tracker.is_done(chunk.id) and candidate_path.exists():
                return (
                    "skipped",
                    load_json_model(ChunkConceptCandidates, candidate_path),
                )
            try:
                async with semaphore:
                    running_count += 1
                    progress.update(task_id, status=status_text())
                    try:
                        candidates = await extractor.extract(chunk)
                    finally:
                        running_count -= 1
                        progress.update(task_id, status=status_text())
                save_json_model(candidates, candidate_path)
                tracker.mark_done(chunk.id, LocalOntology(chunk_id=chunk.id))
                return "success", candidates
            except Exception as exc:  # noqa: BLE001 - keep chunk failures isolated.
                tracker.mark_failed(chunk.id, _format_chunk_error(exc))
                return "failed", None

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TextColumn("{task.fields[status]}"),
            TimeElapsedColumn(),
        ) as progress:
            task_id = progress.add_task(
                "Extracting concept candidates",
                total=len(chunks),
                status=status_text(),
            )
            tasks = [asyncio.create_task(extract_one(chunk)) for chunk in chunks]
            for completed in asyncio.as_completed(tasks):
                status, candidates = await completed
                if status == "success":
                    success_count += 1
                elif status == "skipped":
                    skipped_count += 1
                elif status == "failed":
                    failure_count += 1
                if candidates is not None:
                    results.append(candidates)
                progress.update(
                    task_id,
                    advance=1,
                    status=status_text(),
                )
        results.sort(key=lambda item: item.chunk_id)
        return results, success_count, failure_count, skipped_count

    async def run_with_cleanup() -> tuple[list[ChunkConceptCandidates], int, int, int]:
        try:
            return await run()
        finally:
            await aclose_llm_client(extractor.llm_client)

    try:
        return asyncio.run(run_with_cleanup())
    finally:
        tracker.close()


def _merge_local_ontologies(
    local_files: list[Path],
    config: AppConfig,
    use_embedding: bool,
) -> GlobalOntology:
    if not local_files:
        raise ValueError("No local ontology JSON files found")
    local_ontologies = [
        load_json_model(LocalOntology, local_file) for local_file in local_files
    ]
    global_ontology = OntologyMerger().merge(local_ontologies)
    if not use_embedding:
        return global_ontology

    embedding_client = create_embedding_client(config.merge)
    return asyncio.run(
        EntityDeduplicator(
            embedding_client,
            config.merge.similarity_threshold,
        ).deduplicate(global_ontology)
    )


def _format_chunk_error(exc: Exception) -> str:
    detail = str(exc) or repr(exc)
    return f"{type(exc).__name__}: {detail}"


def _missing_local_ontologies_message(
    output_dir: Path,
    failure_count: int,
    limit: int = 3,
) -> str:
    if failure_count <= 0:
        return "No local ontology JSON files found"

    message = (
        "No local ontology JSON files found. "
        f"Extraction produced no successful chunk results ({failure_count} failed)."
    )
    db_path = output_dir / "progress.sqlite"
    if not db_path.exists():
        return message

    tracker = ProgressTracker(db_path)
    try:
        errors = tracker.failed_errors(limit=limit)
    finally:
        tracker.close()
    if not errors:
        return message

    examples = "; ".join(
        f"{item['chunk_id']}: {item['error_message']}" for item in errors
    )
    return f"{message} First errors: {examples}"


def _limit_extracted_instances(
    ontology: GlobalOntology,
    max_extracted_instances: int | None,
) -> GlobalOntology:
    if max_extracted_instances is None or len(ontology.instances) <= max_extracted_instances:
        return ontology

    kept_instances = ontology.instances[:max_extracted_instances]
    kept_ids = {instance.id for instance in kept_instances}
    pruned_instances = [
        instance.model_copy(
            update={
                "link_values": [
                    link
                    for link in instance.link_values
                    if link.target_instance_id in kept_ids
                ],
            }
        )
        for instance in kept_instances
    ]
    pruned_invocations = []
    for invocation in ontology.action_invocations:
        input_ids = [
            instance_id
            for instance_id in invocation.input_instance_ids
            if instance_id in kept_ids
        ]
        output_ids = [
            instance_id
            for instance_id in invocation.output_instance_ids
            if instance_id in kept_ids
        ]
        if not input_ids and not output_ids:
            continue
        pruned_invocations.append(
            invocation.model_copy(
                update={
                    "input_instance_ids": input_ids,
                    "output_instance_ids": output_ids,
                }
            )
        )

    return ontology.model_copy(
        update={
            "instances": pruned_instances,
            "action_invocations": pruned_invocations,
        }
    )
