from __future__ import annotations

from ontology_construct.concept.models import ChunkConceptCandidates
from ontology_construct.concept.parser import parse_concept_candidates
from ontology_construct.concept.prompts import render_concept_candidate_prompt
from ontology_construct.config import ConceptConfig
from ontology_construct.llm.client import LLMClient
from ontology_construct.models import Chunk


class ConceptCandidateExtractor:
    def __init__(
        self,
        llm_client: LLMClient,
        config: ConceptConfig,
        supplemental_prompt: str | None = None,
    ):
        self.llm_client = llm_client
        self.config = config
        self.supplemental_prompt = supplemental_prompt

    async def extract(self, chunk: Chunk) -> ChunkConceptCandidates:
        prompt = render_concept_candidate_prompt(
            chunk,
            self.config,
            self.supplemental_prompt,
        )
        raw_response = await self.llm_client.complete(prompt)
        return parse_concept_candidates(
            raw_response,
            chunk.id,
            max_candidates=self.config.max_candidates_per_chunk,
            relation_vocab=self.config.relation_vocab,
        )
