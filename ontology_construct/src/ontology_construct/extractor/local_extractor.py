from __future__ import annotations

from ontology_construct.extractor.parser import parse_local_ontology
from ontology_construct.extractor.prompts import render_extract_prompt
from ontology_construct.llm.client import LLMClient
from ontology_construct.models import Chunk, LocalOntology


class LocalExtractor:
    def __init__(
        self,
        llm_client: LLMClient,
        prompt_path: str | None = None,
        supplemental_prompt: str | None = None,
    ):
        self.llm_client = llm_client
        self.prompt_path = prompt_path
        self.supplemental_prompt = supplemental_prompt

    async def extract(self, chunk: Chunk) -> LocalOntology:
        prompt = render_extract_prompt(
            chunk,
            self.prompt_path,
            self.supplemental_prompt,
        )
        raw_response = await self.llm_client.complete(prompt)
        return parse_local_ontology(raw_response, chunk.id)
