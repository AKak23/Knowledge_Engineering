from __future__ import annotations

import os
from typing import Protocol

import httpx

from ontology_construct.config import MergeConfig, load_project_env

EMBEDDING_BATCH_SIZE = 10
QWEN_EMBEDDING_API_KEY_ENVS = (
    "QWEN_EMBEDDING_API_KEY",
    "DASHSCOPE_API_KEY",
    "QWEN_API_KEY",
)


class EmbeddingClient(Protocol):
    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per input text."""


class OpenAICompatibleEmbeddingClient:
    def __init__(
        self,
        config: MergeConfig,
        api_key: str,
        transport: httpx.AsyncBaseTransport | httpx.BaseTransport | None = None,
    ):
        self.config = config
        self.api_key = api_key
        self.transport = transport

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), EMBEDDING_BATCH_SIZE):
            batch = texts[start : start + EMBEDDING_BATCH_SIZE]
            vectors.extend(await self._embed_batch(batch))
        return vectors

    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient(
            timeout=60,
            transport=self.transport,
        ) as client:
            response = await client.post(
                f"{self.config.embedding_base_url.rstrip('/')}/embeddings",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.config.embedding_model,
                    "input": texts,
                },
            )
            response.raise_for_status()
            data = response.json()
            items = sorted(data["data"], key=lambda item: item.get("index", 0))
            return [item["embedding"] for item in items]


def create_embedding_client(config: MergeConfig) -> EmbeddingClient:
    if config.embedding_backend != "qwen":
        raise ValueError(f"Unsupported embedding backend: {config.embedding_backend}")
    load_project_env()
    api_key_envs = [config.embedding_api_key_env]
    if config.embedding_api_key_env == "QWEN_EMBEDDING_API_KEY":
        api_key_envs = list(dict.fromkeys(QWEN_EMBEDDING_API_KEY_ENVS))
    api_key = next(
        (os.environ.get(name) for name in api_key_envs if os.environ.get(name)),
        None,
    )
    if not api_key:
        raise RuntimeError(
            "Missing embedding API key environment variable. "
            f"Tried: {', '.join(api_key_envs)}. "
            "Set QWEN_EMBEDDING_API_KEY in ontology_construct/.env."
        )
    runtime_config = config.model_copy(
        update={
            "embedding_base_url": os.environ.get(
                "QWEN_EMBEDDING_BASE_URL",
                config.embedding_base_url,
            ),
            "embedding_model": os.environ.get(
                "QWEN_EMBEDDING_MODEL",
                config.embedding_model,
            ),
        }
    )
    return OpenAICompatibleEmbeddingClient(runtime_config, api_key)
