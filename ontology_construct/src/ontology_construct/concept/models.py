from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class ConceptCandidate(BaseModel):
    name: str = Field(min_length=1)
    definition: str = ""
    abstraction_level: str = "medium"
    aliases: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    should_be_class: bool = True

    @field_validator("aliases", "evidence")
    @classmethod
    def dedupe_strings(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(str(value).strip() for value in values if value))


class RelationCandidate(BaseModel):
    source: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    target: str = Field(min_length=1)
    evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0, le=1)

    @field_validator("evidence")
    @classmethod
    def dedupe_evidence(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(str(value).strip() for value in values if value))


class ConceptInstanceCandidate(BaseModel):
    concept: str = Field(min_length=1)
    name: str = Field(min_length=1)
    attributes: dict[str, Any] = Field(default_factory=dict)
    links: list[RelationCandidate] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0, le=1)

    @field_validator("evidence")
    @classmethod
    def dedupe_evidence(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(str(value).strip() for value in values if value))


class ChunkConceptCandidates(BaseModel):
    chunk_id: str
    concepts: list[ConceptCandidate] = Field(default_factory=list)
    relations: list[RelationCandidate] = Field(default_factory=list)
    instances: list[ConceptInstanceCandidate] = Field(default_factory=list)
    raw_response: str | None = None


class ConceptCluster(BaseModel):
    cluster_id: str
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    merged_names: list[str] = Field(default_factory=list)
    definitions: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    source_chunks: list[str] = Field(default_factory=list)
    abstraction_level: str = "medium"
    relation_degree: int = 0
    score: float = 0.0
    keep_reasons: list[str] = Field(default_factory=list)
    reject_reasons: list[str] = Field(default_factory=list)


class ConceptSelectionReport(BaseModel):
    selected: list[ConceptCluster] = Field(default_factory=list)
    rejected: list[ConceptCluster] = Field(default_factory=list)


class ConceptClusterReport(BaseModel):
    clusters: list[ConceptCluster] = Field(default_factory=list)


class ConceptRelationReport(BaseModel):
    relations: list[RelationCandidate] = Field(default_factory=list)
    mode: str = "candidate_fallback"
    error: str = ""


class ConceptInstanceReport(BaseModel):
    instances: list[ConceptInstanceCandidate] = Field(default_factory=list)
    mode: str = "disabled"
    error: str = ""
    relation_mode: str = "disabled"
    relation_error: str = ""
    relation_count: int = 0
    relation_fallback_count: int = 0
    instance_count: int = 0
    linked_instance_count: int = 0
    isolated_instance_count: int = 0
    isolated_instance_names: list[str] = Field(default_factory=list)
    relation_pair_candidate_count: int = 0
    relation_pair_selected_count: int = 0
    relation_pair_truncated_count: int = 0
    relation_pair_max: int = 0
    relation_pair_min_per_instance: int = 0
    relation_pair_instances_without_candidates: list[str] = Field(default_factory=list)


class ConceptQualityReport(BaseModel):
    candidate_concept_count: int = 0
    candidate_relation_count: int = 0
    cluster_count: int = 0
    semantic_cluster_merge_count: int = 0
    selected_concept_count: int = 0
    rejected_concept_count: int = 0
    final_relation_count: int = 0
    isolated_concept_count: int = 0
    average_relation_degree: float = 0.0
    instance_count: int = 0
    instance_link_count: int = 0
    isolated_instance_count: int = 0
    isolated_instance_ratio: float = 0.0
    relation_predicate_distribution: dict[str, int] = Field(default_factory=dict)
    rejection_reason_counts: dict[str, int] = Field(default_factory=dict)
    max_concepts: int = 0
    min_evidence: int = 0
    selection_score_threshold: float = 0.0
    dynamic_score_threshold: float = 0.0


class ConceptBuildArtifacts(BaseModel):
    ontology: Any
    clusters: list[ConceptCluster] = Field(default_factory=list)
    selection: ConceptSelectionReport = Field(default_factory=ConceptSelectionReport)
    relation_report: ConceptRelationReport = Field(default_factory=ConceptRelationReport)
    instance_report: ConceptInstanceReport = Field(default_factory=ConceptInstanceReport)
    quality: ConceptQualityReport = Field(default_factory=ConceptQualityReport)
