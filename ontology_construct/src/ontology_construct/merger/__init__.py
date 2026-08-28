from ontology_construct.merger.entity_deduplicator import (
    DuplicatePair,
    EntityDeduplicator,
    cosine_similarity,
    merge_entity_groups,
)
from ontology_construct.merger.normalizer import normalize_name
from ontology_construct.merger.ontology_merger import OntologyMerger

__all__ = [
    "DuplicatePair",
    "EntityDeduplicator",
    "OntologyMerger",
    "cosine_similarity",
    "merge_entity_groups",
    "normalize_name",
]
