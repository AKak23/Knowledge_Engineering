import json

import httpx
import pytest

from ontology_construct.config import LLMConfig
from ontology_construct.llm.client import (
    FailoverLLMClient,
    OpenAICompatibleChatClient,
    create_llm_client,
)


def test_create_deepseek_client_requires_env_key(monkeypatch) -> None:
    monkeypatch.setattr("ontology_construct.llm.client.load_project_env", lambda: None)
    for index in range(1, 6):
        suffix = "" if index == 1 else f"_{index}"
        monkeypatch.delenv(f"DEEPSEEK_API_KEY{suffix}", raising=False)

    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        create_llm_client(
            LLMConfig(),
            "deepseek",
        )


def test_deepseek_client_fails_over_to_backup_key(monkeypatch) -> None:
    import asyncio

    monkeypatch.setattr("ontology_construct.llm.client.load_project_env", lambda: None)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "expired-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY_2", "working-key")
    for index in range(3, 6):
        monkeypatch.delenv(f"DEEPSEEK_API_KEY_{index}", raising=False)

    client = create_llm_client(LLMConfig(max_retries=0), "deepseek")
    assert isinstance(client, FailoverLLMClient)

    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        authorization = request.headers.get("authorization", "")
        attempts.append(authorization)
        if authorization == "Bearer expired-key":
            return httpx.Response(401, json={"error": "invalid key"})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "{}"}}]},
        )

    for provider_client in client.clients:
        provider_client.transport = httpx.MockTransport(handler)

    assert asyncio.run(client.complete("hello")) == "{}"
    assert attempts == ["Bearer expired-key", "Bearer working-key"]
    assert asyncio.run(client.complete("hello again")) == "{}"
    assert attempts[-1] == "Bearer working-key"


def test_deepseek_client_uses_current_thinking_toggle_and_json_mode(monkeypatch) -> None:
    import asyncio

    monkeypatch.setattr("ontology_construct.llm.client.load_project_env", lambda: None)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")
    for index in range(2, 6):
        monkeypatch.delenv(f"DEEPSEEK_API_KEY_{index}", raising=False)

    client = create_llm_client(LLMConfig(max_retries=0), "deepseek")
    assert isinstance(client, OpenAICompatibleChatClient)

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.read()))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "{}"}}]},
        )

    client.transport = httpx.MockTransport(handler)
    assert asyncio.run(client.complete("请输出 JSON")) == "{}"

    assert captured["thinking"] == {"type": "disabled"}
    assert captured["response_format"] == {"type": "json_object"}
    assert "enable_thinking" not in captured


def test_deepseek_direct_qa_does_not_force_json_mode(monkeypatch) -> None:
    import asyncio

    monkeypatch.setattr("ontology_construct.llm.client.load_project_env", lambda: None)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")
    for index in range(2, 6):
        monkeypatch.delenv(f"DEEPSEEK_API_KEY_{index}", raising=False)

    client = create_llm_client(LLMConfig(max_retries=0), "deepseek")
    assert isinstance(client, OpenAICompatibleChatClient)

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.read()))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "<answer>正常</answer>"}}]},
        )

    client.transport = httpx.MockTransport(handler)
    result = asyncio.run(
        client.complete("回答问题", system_prompt="请用 <answer> 标签回答。")
    )

    assert result == "<answer>正常</answer>"
    assert "response_format" not in captured


def test_openai_compatible_client_retries_empty_content(monkeypatch) -> None:
    import asyncio

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("ontology_construct.llm.client.asyncio.sleep", no_sleep)
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "length",
                            "message": {
                                "content": "",
                                "reasoning_content": "内部推理",
                            },
                        }
                    ],
                    "usage": {"completion_tokens": 6000},
                },
            )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "{}"}}]},
        )

    client = OpenAICompatibleChatClient(
        LLMConfig(max_retries=1),
        "test-key",
        transport=httpx.MockTransport(handler),
    )

    assert asyncio.run(client.complete("hello")) == "{}"
    assert attempts == 2


def test_openai_compatible_client_reports_empty_content_metadata() -> None:
    import asyncio

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": "", "reasoning_content": "abc"},
                    }
                ],
                "usage": {"completion_tokens": 6000},
            },
        )

    client = OpenAICompatibleChatClient(
        LLMConfig(max_retries=0),
        "test-key",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(
        RuntimeError,
        match=r"empty message\.content .*finish_reason='length'.*reasoning_chars=3",
    ):
        asyncio.run(client.complete("hello"))


def test_create_kimi_client_uses_openai_compatible_config(monkeypatch) -> None:
    monkeypatch.setattr("ontology_construct.llm.client.load_project_env", lambda: None)
    monkeypatch.setenv("MOONSHOT_API_KEY", "kimi-key")
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    monkeypatch.setenv("KIMI_REASONING_EFFORT", "low")
    monkeypatch.delenv("KIMI_TEMPERATURE", raising=False)

    client = create_llm_client(LLMConfig(), "kimi")

    assert isinstance(client, OpenAICompatibleChatClient)
    assert client.config.backend == "kimi"
    assert client.config.base_url == "https://api.moonshot.cn/v1"
    assert client.config.model == "kimi-k3"
    assert client.config.api_key_env == "MOONSHOT_API_KEY"
    assert client.config.temperature == 1
    assert client.config.enable_thinking is None
    assert client.config.extra_body == {"reasoning_effort": "low"}


def test_create_jiutian_client_uses_lom_gateway(monkeypatch) -> None:
    monkeypatch.setattr("ontology_construct.llm.client.load_project_env", lambda: None)
    monkeypatch.setenv("JIUTIAN_API_KEY", "jiutian-key")
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("JIUTIAN_TEMPERATURE", raising=False)
    monkeypatch.delenv("JIUTIAN_MAX_TOKENS", raising=False)

    client = create_llm_client(LLMConfig(), "jiutian")

    assert isinstance(client, OpenAICompatibleChatClient)
    assert client.config.backend == "jiutian"
    assert "jiutian.hq.cmcc" in client.config.base_url
    assert client.config.model == "jiutian-35b"
    assert client.config.api_key_env == "JIUTIAN_API_KEY"
    assert client.config.temperature == 0.1
    assert client.config.max_tokens == 8000
    assert client.config.enable_thinking is None


def test_create_mock_client_without_env() -> None:
    client = create_llm_client(LLMConfig(), "mock")

    assert client is not None


def test_create_qwen_client_requires_env_key(monkeypatch) -> None:
    monkeypatch.setattr("ontology_construct.llm.client.load_project_env", lambda: None)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    monkeypatch.delenv("QWEN_EMBEDDING_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="DASHSCOPE_API_KEY"):
        create_llm_client(LLMConfig(), "qwen")


def test_create_qwen_client_uses_default_dashscope_config(monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "qwen-key")

    client = create_llm_client(LLMConfig(), "qwen")

    assert isinstance(client, OpenAICompatibleChatClient)
    assert client.config.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert client.config.model == "qwen-plus"
    assert client.config.api_key_env == "DASHSCOPE_API_KEY"
    # Qwen defaults to non-thinking mode for fast structured extraction.
    assert client.config.enable_thinking is False


def test_create_qwen_plus_client_uses_explicit_backend(monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "qwen-key")
    monkeypatch.delenv("QWEN_PLUS_MODEL", raising=False)

    client = create_llm_client(LLMConfig(), "qwen-plus")

    assert isinstance(client, OpenAICompatibleChatClient)
    assert client.config.backend == "qwen-plus"
    assert client.config.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert client.config.model == "qwen-plus"
    assert client.config.api_key_env == "DASHSCOPE_API_KEY"
    assert client.config.enable_thinking is False


def test_create_qwen3_8b_client_uses_dashscope_api(monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "qwen-key")
    monkeypatch.delenv("QWEN3_8B_MODEL", raising=False)

    client = create_llm_client(LLMConfig(), "qwen3-8b")

    assert isinstance(client, OpenAICompatibleChatClient)
    assert client.config.backend == "qwen3-8b"
    assert client.config.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert client.config.model == "qwen3-8b"
    assert client.config.api_key_env == "DASHSCOPE_API_KEY"
    assert client.config.enable_thinking is False


def test_create_qwen3_8b_client_allows_model_override(monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "qwen-key")
    monkeypatch.setenv("QWEN3_8B_MODEL", "qwen3-8b-custom")

    client = create_llm_client(LLMConfig(), "qwen3-8b")

    assert isinstance(client, OpenAICompatibleChatClient)
    assert client.config.model == "qwen3-8b-custom"


def test_qwen_payload_includes_enable_thinking_false(monkeypatch) -> None:
    import asyncio

    monkeypatch.setenv("DASHSCOPE_API_KEY", "qwen-key")
    client = create_llm_client(LLMConfig(max_retries=0), "qwen")

    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = request.read().decode("utf-8")
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "{}"}}]},
        )

    client.transport = httpx.MockTransport(handler)
    asyncio.run(client.complete("hello"))

    assert '"enable_thinking":false' in captured["json"]


async def _complete_with_mock_transport() -> str:
    config = LLMConfig(
        base_url="https://api.deepseek.com",
        model="deepseek-v4-pro",
        max_retries=0,
    )
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["json"] = request.read().decode("utf-8")
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"entities": [], "triples": []}',
                        }
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    client = OpenAICompatibleChatClient(config, "test-key", transport=transport)
    result = await client.complete("hello")
    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["authorization"] == "Bearer test-key"
    assert "deepseek-v4-pro" in captured["json"]
    return result


def test_openai_compatible_chat_client_parses_response() -> None:
    import asyncio

    assert asyncio.run(_complete_with_mock_transport()) == '{"entities": [], "triples": []}'


def test_openai_compatible_chat_client_formats_os_errors(monkeypatch) -> None:
    import asyncio

    client = OpenAICompatibleChatClient(LLMConfig(max_retries=0), "test-key")

    async def raise_os_error(_prompt: str) -> str:
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(client, "_complete_once", raise_os_error)

    with pytest.raises(RuntimeError, match="SSL_CERT_FILE"):
        asyncio.run(client.complete("hello"))
