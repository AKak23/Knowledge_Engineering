from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Protocol

import httpx

from ontology_construct.config import LLMConfig, load_project_env

QWEN_DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
QWEN_DEFAULT_MODEL = "qwen-plus"
QWEN3_8B_DEFAULT_MODEL = "qwen3-8b"
QWEN_32B_DEFAULT_MODEL = "qwen3-32b"
QWEN_API_KEY_ENVS = ("DASHSCOPE_API_KEY", "QWEN_API_KEY", "QWEN_EMBEDDING_API_KEY")

# 九天 (China Mobile Jiutian) gateway — OpenAI-compatible chat completions.
JIUTIAN_DEFAULT_BASE_URL = (
    "http://jiutian.hq.cmcc/kunlun/ingress/api/shh-rsrcpl/"
    "c27a3de6eeb449b08c20997afa226f82/ai-58d8ea2c671440bba72e58deb2bfac80/"
    "service-e25438aa6b884487908c688bee555c4f/v1"
)
JIUTIAN_DEFAULT_MODEL = "jiutian-35b"
# jiutian-35b via completions API — flat "prompt" field instead of "messages".
JIUTIAN_35B_DEFAULT_BASE_URL = (
    "https://hl.jiutian.10086.cn/kunlun/ingress/api/hl-4a9c15/"
    "4095a4f5cc6c45f393e517067fb8f6a9/ai-6c609d9c84604c0ca5ed7200b22904cc/"
    "service-6a4c8d2928204c789e445221948f0fb5/v1"
)
JIUTIAN_35B_DEFAULT_MODEL = "JT-IC-LLM-L1-35B-128K-V0.1.0"
JIUTIAN_API_KEY_ENVS = ("JIUTIAN_API_KEY",)
JIUTIAN_35B_API_KEY_ENVS = ("JIUTIAN_35B_API_KEY", "JIUTIAN_API_KEY")

# qwen-32b via Jiutian gateway — same pattern as the Jiutian backends above.
# Set QWEN_32B_BASE_URL to route qwen-32b chat requests through this gateway
# instead of DashScope. When set, the Jiutian API key is used and the
# enable_thinking flag is omitted (the gateway does not recognise it).
QWEN_32B_JIUTIAN_BASE_URL = (
    "http://jiutian.hq.cmcc/kunlun/ingress/api/shh-rsrcpl/"
    "c27a3de6eeb449b08c20997afa226f82/ai-58d8ea2c671440bba72e58deb2bfac80/"
    "service-e25438aa6b884487908c688bee555c4f/v1"
)
QWEN_32B_JIUTIAN_MODEL = "/model"

# DeepSeek — OpenAI-compatible chat completions.
DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_DEFAULT_MODEL = "deepseek-v4-flash"
DEEPSEEK_API_KEY_ENVS = (
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_API_KEY_2",
    "DEEPSEEK_API_KEY_3",
    "DEEPSEEK_API_KEY_4",
    "DEEPSEEK_API_KEY_5",
)

# Kimi / Moonshot — OpenAI-compatible chat completions.
KIMI_DEFAULT_BASE_URL = "https://api.moonshot.cn/v1"
KIMI_DEFAULT_MODEL = "kimi-k3"
KIMI_API_KEY_ENVS = ("MOONSHOT_API_KEY", "KIMI_API_KEY")

EXTRACTION_SYSTEM_PROMPT = "你是一个本体构建专家，只输出合法 JSON。"
QA_SYSTEM_PROMPT = (
    "你是一位本体推理专家。请基于给定的本体定义和图实例回答问题。"
    "本体定义说明世界中有哪些对象类型、对象之间的关系以及合法操作。"
    "图实例是一次具体业务场景的快照。"
    "同时利用两者：用本体定义确认规则，用图实例查找事实。\n\n"
    "必须只输出 JSON 格式的答案，不要输出其他内容。"
    "JSON 顶层包含 think、highlight、answer 三个字段。"
    "think 是字符串数组，列出推理步骤。"
    "highlight 包含 nodes（字符串数组）和 edges（二维字符串数组，每个元素是 source 和 target）。"
    "answer 包含 summary（字符串）、path（字符串数组）、edges（二维数组，每项含 source、target、关系名）、"
    "groups（对象数组，每项含 label 和 nodes）、raw（字符串，可选）。"
    "只能使用用户消息中提供的节点 id 和边端点。"
    "如果上下文不足以回答，在 summary 中说明缺少哪些信息。"
)
QA_DIRECT_SYSTEM_PROMPT = (
    "你是一位本体推理专家。请基于给定的本体定义和图实例回答问题。"
    "本体定义说明了世界上有哪些类型的对象、对象之间有哪些关系、以及哪些操作是合法的。"
    "图实例是一次具体业务场景的快照。"
    "推理时必须同时利用两者：用本体定义确认规则，用图实例查找事实。"
    "推理过程用 <think></think> 包裹，最终答案用 <answer></answer> 包裹。"
)


class LLMClient(Protocol):
    async def complete(self, prompt: str, *, system_prompt: str | None = None) -> str:
        """Return a raw LLM text completion."""


class MockLLMClient:
    def __init__(self, response: str | None = None):
        self.response = response

    async def complete(self, prompt: str, *, system_prompt: str | None = None) -> str:
        if self.response is not None:
            return self.response
        return json.dumps({"entities": [], "triples": []}, ensure_ascii=False)


class OpenAICompatibleChatClient:
    def __init__(
        self,
        config: LLMConfig,
        api_key: str,
        transport: httpx.AsyncBaseTransport | httpx.BaseTransport | None = None,
    ):
        self.config = config
        self.api_key = api_key
        self.transport = transport
        # A single AsyncClient is reused across all requests so the connection
        # pool keeps HTTP/TLS connections alive instead of re-handshaking per
        # call. Each client instance is used within one asyncio.run loop, so a
        # lazily created client stays bound to the right event loop.
        self._client: httpx.AsyncClient | None = None

    def _http_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.config.timeout_seconds,
                transport=self.transport,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def complete(self, prompt: str, *, system_prompt: str | None = None) -> str:
        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                if system_prompt is None:
                    return await self._complete_once(prompt)
                return await self._complete_once(prompt, system_prompt=system_prompt)
            except httpx.HTTPStatusError as exc:
                # Content moderation (406) and other client errors (4xx) are not
                # transient — retrying the same request will never succeed.
                if 400 <= exc.response.status_code < 500:
                    raise RuntimeError(
                        f"LLM request failed: {_format_llm_error(exc, self.config)}"
                    ) from exc
                last_error = exc
                if attempt >= self.config.max_retries:
                    break
                await asyncio.sleep(min(2**attempt, 8))
            except (httpx.HTTPError, KeyError, IndexError, OSError) as exc:
                last_error = exc
                if attempt >= self.config.max_retries:
                    break
                await asyncio.sleep(min(2**attempt, 8))
        raise RuntimeError(
            f"LLM request failed: {_format_llm_error(last_error, self.config)}"
        ) from last_error

    async def _complete_once(self, prompt: str, *, system_prompt: str | None = None) -> str:
        payload = {
            "model": self.config.model,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt or EXTRACTION_SYSTEM_PROMPT,
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        if self.config.enable_thinking is not None:
            # DashScope/Qwen hybrid-thinking models accept this top-level flag in
            # OpenAI-compatible mode; disabling it skips chain-of-thought output.
            payload["enable_thinking"] = self.config.enable_thinking
        if self.config.extra_body:
            payload.update(self.config.extra_body)
        client = self._http_client()
        response = await client.post(
            f"{self.config.base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]


class FailoverLLMClient:
    """Try equivalent provider clients in order and retain the last good key."""

    def __init__(
        self,
        clients: list[OpenAICompatibleChatClient],
        *,
        provider_name: str,
    ):
        if not clients:
            raise ValueError("FailoverLLMClient requires at least one client")
        self.clients = clients
        self.provider_name = provider_name
        self.config = clients[0].config
        self._active_index = 0

    async def complete(self, prompt: str, *, system_prompt: str | None = None) -> str:
        last_error: RuntimeError | None = None
        start_index = self._active_index
        for offset in range(len(self.clients)):
            index = (start_index + offset) % len(self.clients)
            client = self.clients[index]
            try:
                result = await client.complete(prompt, system_prompt=system_prompt)
            except RuntimeError as exc:
                last_error = exc
                if not _is_failover_error(exc):
                    raise
                self._active_index = (index + 1) % len(self.clients)
                continue
            self._active_index = index
            return result
        raise RuntimeError(
            f"All configured {self.provider_name} API keys failed. "
            f"Last error: {last_error}"
        ) from last_error

    async def aclose(self) -> None:
        for client in self.clients:
            await client.aclose()


class CompletionsClient:
    """Client for OpenAI-style /v1/completions endpoint (non-chat).

    Uses a flat "prompt" field instead of "messages". The system prompt is
    prepended to the user prompt since the completions API has no system role.
    """

    def __init__(
        self,
        config: LLMConfig,
        api_key: str,
        transport: httpx.AsyncBaseTransport | httpx.BaseTransport | None = None,
    ):
        self.config = config
        self.api_key = api_key
        self.transport = transport
        self._client: httpx.AsyncClient | None = None

    def _http_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.config.timeout_seconds,
                transport=self.transport,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def complete(self, prompt: str, *, system_prompt: str | None = None) -> str:
        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                return await self._complete_once(prompt, system_prompt=system_prompt)
            except httpx.HTTPStatusError as exc:
                if 400 <= exc.response.status_code < 500:
                    raise RuntimeError(
                        f"LLM request failed: {_format_llm_error(exc, self.config)}"
                    ) from exc
                last_error = exc
                if attempt >= self.config.max_retries:
                    break
                await asyncio.sleep(min(2 ** attempt, 8))
            except (httpx.HTTPError, KeyError, IndexError, OSError) as exc:
                last_error = exc
                if attempt >= self.config.max_retries:
                    break
                await asyncio.sleep(min(2 ** attempt, 8))
        raise RuntimeError(
            f"LLM request failed: {_format_llm_error(last_error, self.config)}"
        ) from last_error

    async def _complete_once(self, prompt: str, *, system_prompt: str | None = None) -> str:
        sys = system_prompt or EXTRACTION_SYSTEM_PROMPT
        # Prompt template is configurable so different Jiutian models can use
        # their own chat format. {sys} and {prompt} are replaced at runtime.
        template = os.environ.get(
            "COMPLETIONS_PROMPT_TEMPLATE",
            "### System:\n{sys}\n\n### User:\n{prompt}\n\n### Assistant:\n",
        )
        full_prompt = template.format(sys=sys, prompt=prompt)
        payload: dict[str, Any] = {
            "model": self.config.model,
            "prompt": full_prompt,
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
            "top_p": 0.85,
            "top_k": 40,
            "repetition_penalty": 1.08,
            "stream": False,
        }
        client = self._http_client()
        response = await client.post(
            f"{self.config.base_url.rstrip('/')}/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["text"]


def create_llm_client(config: LLMConfig, backend: str | None = None) -> LLMClient:
    selected_backend = backend or config.backend
    if selected_backend == "mock":
        return MockLLMClient()
    if selected_backend == "deepseek":
        return _create_deepseek_client(config)
    if selected_backend == "kimi":
        return _create_kimi_client(config)
    if selected_backend in {"qwen", "qwen-plus", "qwen3-8b"}:
        load_project_env()
        api_key_env, api_key = _first_env_value(QWEN_API_KEY_ENVS)
        if not api_key:
            available = ", ".join(QWEN_API_KEY_ENVS)
            raise RuntimeError(
                f"Missing Qwen API key environment variable. Tried: {available}"
            )
        model_env = {
            "qwen-plus": "QWEN_PLUS_MODEL",
            "qwen3-8b": "QWEN3_8B_MODEL",
        }.get(selected_backend, "QWEN_MODEL")
        default_model = {
            "qwen-plus": QWEN_DEFAULT_MODEL,
            "qwen3-8b": QWEN3_8B_DEFAULT_MODEL,
        }.get(selected_backend, QWEN_DEFAULT_MODEL)
        qwen_config = config.model_copy(
            update={
                "backend": selected_backend,
                "base_url": os.environ.get("QWEN_BASE_URL", QWEN_DEFAULT_BASE_URL),
                "model": os.environ.get(model_env, default_model),
                "api_key_env": api_key_env,
                # Default Qwen to non-thinking mode for fast structured
                # extraction unless the config explicitly opts back in.
                "enable_thinking": (
                    config.enable_thinking
                    if config.enable_thinking is not None
                    else False
                ),
            }
        )
        return OpenAICompatibleChatClient(qwen_config, api_key)
    if selected_backend == "qwen-32b":
        load_project_env()
        # Default: DashScope (same as other Qwen backends).
        # Set QWEN_32B_BASE_URL to the Jiutian gateway URL to route through
        # Jiutian instead. Embedding calls are NOT affected.
        qwen32b_base_url = os.environ.get("QWEN_32B_BASE_URL", QWEN_DEFAULT_BASE_URL)
        using_jiutian_gateway = "jiutian" in qwen32b_base_url
        if using_jiutian_gateway:
            api_key_env, api_key = _first_env_value(JIUTIAN_API_KEY_ENVS)
            if not api_key:
                available = ", ".join(JIUTIAN_API_KEY_ENVS)
                raise RuntimeError(
                    f"Missing Jiutian API key for qwen-32b. Tried: {available}"
                )
            qwen32b_config = config.model_copy(
                update={
                    "backend": "qwen-32b",
                    "base_url": qwen32b_base_url,
                    "model": os.environ.get("QWEN_32B_MODEL", QWEN_32B_JIUTIAN_MODEL),
                    "api_key_env": api_key_env,
                    "enable_thinking": False,
                }
            )
            return OpenAICompatibleChatClient(qwen32b_config, api_key)
        else:
            # Original DashScope mode.
            api_key_env, api_key = _first_env_value(QWEN_API_KEY_ENVS)
            if not api_key:
                available = ", ".join(QWEN_API_KEY_ENVS)
                raise RuntimeError(
                    f"Missing Qwen API key for qwen-32b. Tried: {available}"
                )
            qwen32b_config = config.model_copy(
                update={
                    "backend": "qwen-32b",
                    "base_url": qwen32b_base_url,
                    "model": os.environ.get("QWEN_32B_MODEL", QWEN_32B_DEFAULT_MODEL),
                    "api_key_env": api_key_env,
                    "enable_thinking": (
                        config.enable_thinking
                        if config.enable_thinking is not None
                        else False
                    ),
                }
            )
            return OpenAICompatibleChatClient(qwen32b_config, api_key)
    if selected_backend == "jiutian":
        load_project_env()
        api_key_env, api_key = _first_env_value(JIUTIAN_API_KEY_ENVS)
        if not api_key:
            available = ", ".join(JIUTIAN_API_KEY_ENVS)
            raise RuntimeError(
                f"Missing Jiutian API key environment variable. Tried: {available}"
            )
        jiutian_config = config.model_copy(
            update={
                "backend": "jiutian",
                "base_url": os.environ.get("JIUTIAN_BASE_URL", JIUTIAN_DEFAULT_BASE_URL),
                "model": os.environ.get("JIUTIAN_MODEL", JIUTIAN_DEFAULT_MODEL),
                # The fine-tuned QA model (jiutian-lom) answers deterministically:
                # temperature>0 makes it waver between putting enumerations in
                # summary vs groups, and long fan-out answers need extra headroom.
                # Only this backend is pinned — qwen/jiutian-35b keep global cfg.
                "temperature": float(os.environ.get("JIUTIAN_TEMPERATURE", "0.1")),
                "max_tokens": int(
                    os.environ.get("JIUTIAN_MAX_TOKENS", str(max(config.max_tokens, 8000)))
                ),
                "api_key_env": api_key_env,
                "enable_thinking": None,
            }
        )
        return OpenAICompatibleChatClient(jiutian_config, api_key)
    if selected_backend == "jiutian-35b":
        load_project_env()
        api_key_env, api_key = _first_env_value(JIUTIAN_35B_API_KEY_ENVS)
        if not api_key:
            available = ", ".join(JIUTIAN_API_KEY_ENVS)
            raise RuntimeError(
                f"Missing Jiutian API key environment variable. Tried: {available}"
            )
        jiutian_config = config.model_copy(
            update={
                "backend": "jiutian-35b",
                "base_url": os.environ.get(
                    "JIUTIAN_35B_BASE_URL", JIUTIAN_35B_DEFAULT_BASE_URL
                ),
                "model": os.environ.get("JIUTIAN_35B_MODEL", JIUTIAN_35B_DEFAULT_MODEL),
                "api_key_env": api_key_env,
            }
        )
        return CompletionsClient(jiutian_config, api_key)
    raise ValueError(f"Unsupported LLM backend: {selected_backend}")


def _create_deepseek_client(config: LLMConfig) -> LLMClient:
    load_project_env()
    api_keys = _all_env_values(DEEPSEEK_API_KEY_ENVS)
    if not api_keys:
        available = ", ".join(DEEPSEEK_API_KEY_ENVS)
        raise RuntimeError(
            f"Missing DeepSeek API key environment variable. Tried: {available}"
        )
    clients = [
        OpenAICompatibleChatClient(
            config.model_copy(
                update={
                    "backend": "deepseek",
                    "base_url": os.environ.get(
                        "DEEPSEEK_BASE_URL", DEEPSEEK_DEFAULT_BASE_URL
                    ),
                    "model": os.environ.get("DEEPSEEK_MODEL", DEEPSEEK_DEFAULT_MODEL),
                    "api_key_env": api_key_env,
                    # Disable thinking for fast structured extraction.
                    "enable_thinking": False,
                }
            ),
            api_key,
        )
        for api_key_env, api_key in api_keys
    ]
    if len(clients) == 1:
        return clients[0]
    return FailoverLLMClient(clients, provider_name="DeepSeek")


def _create_kimi_client(config: LLMConfig) -> OpenAICompatibleChatClient:
    load_project_env()
    api_key_env, api_key = _first_env_value(KIMI_API_KEY_ENVS)
    if not api_key:
        available = ", ".join(KIMI_API_KEY_ENVS)
        raise RuntimeError(
            f"Missing Kimi API key environment variable. Tried: {available}"
        )
    extra_body = dict(config.extra_body or {})
    reasoning_effort = os.environ.get("KIMI_REASONING_EFFORT", "").strip()
    if reasoning_effort:
        extra_body["reasoning_effort"] = reasoning_effort
    kimi_config = config.model_copy(
        update={
            "backend": "kimi",
            "base_url": os.environ.get("KIMI_BASE_URL", KIMI_DEFAULT_BASE_URL),
            "model": os.environ.get("KIMI_MODEL", KIMI_DEFAULT_MODEL),
            "api_key_env": api_key_env,
            "enable_thinking": None,
            "extra_body": extra_body or None,
        }
    )
    return OpenAICompatibleChatClient(kimi_config, api_key)


async def aclose_llm_client(client: LLMClient) -> None:
    """Best-effort close of an LLM client's connection pool, if it has one.

    Must be awaited inside the same event loop that issued the requests so the
    underlying httpx.AsyncClient is torn down cleanly.
    """
    aclose = getattr(client, "aclose", None)
    if aclose is not None:
        try:
            await aclose()
        except RuntimeError as exc:
            if "Event loop is closed" not in str(exc):
                raise


def _first_env_value(names: tuple[str, ...]) -> tuple[str, str | None]:
    for name in names:
        value = os.environ.get(name)
        if value:
            return name, value
    return names[0], None


def _all_env_values(names: tuple[str, ...]) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name in names:
        value = (os.environ.get(name) or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        values.append((name, value))
    return values


def _is_failover_error(error: RuntimeError) -> bool:
    cause = error.__cause__
    if isinstance(cause, httpx.HTTPStatusError):
        status = cause.response.status_code
        return status in {401, 402, 403, 408, 409, 429} or status >= 500
    return isinstance(
        cause,
        (httpx.HTTPError, KeyError, IndexError, OSError),
    )


def _format_llm_error(error: Exception | None, config: LLMConfig) -> str:
    if error is None:
        return "unknown error"
    if isinstance(error, httpx.TimeoutException):
        return f"{type(error).__name__}: timed out after {config.timeout_seconds}s"
    if isinstance(error, httpx.HTTPStatusError):
        return (
            f"{type(error).__name__}: HTTP {error.response.status_code} "
            f"{error.response.text[:500]}"
        )
    if isinstance(error, OSError):
        return (
            f"{type(error).__name__}: {error}. "
            "Check SSL_CERT_FILE, REQUESTS_CA_BUNDLE, proxy settings, "
            "and the Python environment's certifi installation."
        )
    return f"{type(error).__name__}: {error}"
