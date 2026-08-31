import json

import httpx
import pytest

from ontology_construct.config import MergeConfig
from ontology_construct.embedding.client import (
    OpenAICompatibleEmbeddingClient,
    create_embedding_client,
)


def test_create_qwen_embedding_client_requires_env_key(monkeypatch) -> None:
    monkeypatch.delenv("MISSING_QWEN_EMBEDDING_KEY_FOR_TEST", raising=False)

    with pytest.raises(RuntimeError, match="MISSING_QWEN_EMBEDDING_KEY_FOR_TEST"):
        create_embedding_client(
            MergeConfig(embedding_api_key_env="MISSING_QWEN_EMBEDDING_KEY_FOR_TEST")
        )


def test_create_qwen_embedding_client_uses_runtime_model_override(monkeypatch) -> None:
    monkeypatch.setattr(
        "ontology_construct.embedding.client.load_project_env",
        lambda: None,
    )
    monkeypatch.setenv("QWEN_EMBEDDING_API_KEY", "embedding-key")
    monkeypatch.setenv("QWEN_EMBEDDING_MODEL", "qwen3.7-text-embedding")
    monkeypatch.setenv(
        "QWEN_EMBEDDING_BASE_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    )

    client = create_embedding_client(MergeConfig(embedding_model="legacy-model"))

    assert isinstance(client, OpenAICompatibleEmbeddingClient)
    assert client.config.embedding_model == "qwen3.7-text-embedding"
    assert client.config.embedding_base_url.endswith("/compatible-mode/v1")


async def _embed_with_mock_transport() -> list[list[float]]:
    config = MergeConfig(
        embedding_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        embedding_model="text-embedding-v4",
    )
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["json"] = request.read().decode("utf-8")
        return httpx.Response(
            200,
            json={
                "data": [
                    {"embedding": [0.1, 0.2]},
                    {"embedding": [0.3, 0.4]},
                ]
            },
        )

    client = OpenAICompatibleEmbeddingClient(
        config,
        "embedding-key",
        transport=httpx.MockTransport(handler),
    )
    result = await client.embed_texts(["苹果公司", "Apple Inc."])
    assert (
        captured["url"]
        == "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
    )
    assert captured["authorization"] == "Bearer embedding-key"
    assert "text-embedding-v4" in captured["json"]
    return result


def test_openai_compatible_embedding_client_parses_vectors() -> None:
    import asyncio

    assert asyncio.run(_embed_with_mock_transport()) == [[0.1, 0.2], [0.3, 0.4]]


async def _embed_large_input_with_mock_transport() -> list[list[float]]:
    config = MergeConfig(embedding_model="text-embedding-v4")
    batch_sizes = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.read().decode("utf-8"))
        batch_sizes.append(len(payload["input"]))
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": index, "embedding": [float(index)]}
                    for index, _ in enumerate(payload["input"])
                ]
            },
        )

    client = OpenAICompatibleEmbeddingClient(
        config,
        "embedding-key",
        transport=httpx.MockTransport(handler),
    )
    result = await client.embed_texts([f"实体{index}" for index in range(11)])
    assert batch_sizes == [10, 1]
    return result


def test_openai_compatible_embedding_client_batches_large_inputs() -> None:
    import asyncio

    assert asyncio.run(_embed_large_input_with_mock_transport()) == [
        *[[float(index)] for index in range(10)],
        [0.0],
    ]
