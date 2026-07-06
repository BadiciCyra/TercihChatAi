import os
import asyncio as _asyncio_for_decorators
from dataclasses import dataclass
from typing import Any, Optional

from tools import YokAtlasTool, WebSearchTool, fetch_link_content


def _collect_gemini_keys() -> list[str]:
    """GEMINI_API_KEY + GEMINI_API_KEY_2..N env'lerinden tüm key'leri topla."""
    keys = []
    primary = os.getenv("GEMINI_API_KEY")
    if primary:
        keys.append(primary)
    for i in range(2, 11):
        key = os.getenv(f"GEMINI_API_KEY_{i}")
        if key:
            keys.append(key)
    return keys


def _is_retryable_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(token in message for token in (
        "429",
        "resource_exhausted",
        "quota",
        "503",
        "504",
        "unavailable",
        "deadline_exceeded",
        "deadline exceeded",
        "rate limit",
        "temporarily unavailable",
    ))


GEMINI_KEYS = _collect_gemini_keys()
FAST_MODEL = os.getenv("GEMINI_FAST_MODEL", "gemini-2.5-flash-lite")
SMART_MODEL = os.getenv("GEMINI_SMART_MODEL", "gemini-2.5-flash-lite")

if not GEMINI_KEYS:
    print("[LLM] ⚠️ GEMINI_API_KEY tanımsız! Gemini provider fallback zinciri zayıf kalabilir.")
else:
    print(f"[LLM] 🔑 {len(GEMINI_KEYS)} adet Gemini API key bulundu (rotasyon aktif)")


class RotatingGeminiLLM:
    """Birden fazla Gemini key arasında dönen LLM wrapper."""

    provider_name = "gemini"

    def __init__(self, model: str, temperature: float = 0.3, timeout: int = 30, disable_thinking: bool = False):
        self.model = model
        self.temperature = temperature
        self.timeout = timeout
        self._clients = self._build_clients(model, temperature, timeout, disable_thinking=disable_thinking)
        self._cursor = 0
        self._max_server_retries = int(os.getenv("GEMINI_MAX_RETRIES", "3"))

    @staticmethod
    def _build_clients(model: str, temperature: float, timeout: int, disable_thinking: bool = False):
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
        except Exception as exc:
            print(f"[LLM] ⚠️ ChatGoogleGenerativeAI import edilemedi: {exc}")
            return []

        extra_kwargs = {}
        if disable_thinking:
            extra_kwargs["thinking_config"] = {"thinking_budget": 0}

        clients = [
            ChatGoogleGenerativeAI(
                model=model,
                google_api_key=key,
                temperature=temperature,
                max_retries=1,
                timeout=timeout,
                **extra_kwargs,
            )
            for key in GEMINI_KEYS
        ]

        if not clients:
            clients = [
                ChatGoogleGenerativeAI(
                    model=model,
                    temperature=temperature,
                    max_retries=1,
                    timeout=timeout,
                    **extra_kwargs,
                )
            ]
        return clients

    def _select_client(self, attempt: int):
        if not self._clients:
            raise RuntimeError("Gemini client oluşturulamadı")
        idx = (self._cursor + attempt) % len(self._clients)
        return idx, self._clients[idx]

    def invoke(self, messages, **kwargs):
        last_exc = None
        n = len(self._clients)
        if not n:
            raise RuntimeError("Gemini client oluşturulamadı")

        server_retries = 0
        attempt = 0
        while attempt < n + self._max_server_retries:
            idx, client = self._select_client(attempt)
            try:
                result = client.invoke(messages, **kwargs)
                self._cursor = (idx + 1) % n
                return result
            except Exception as exc:
                last_exc = exc
                if _is_retryable_error(exc) and server_retries < self._max_server_retries:
                    wait = 2 ** server_retries
                    print(f"[LLM] ⏳ Gemini hatası ({str(exc)[:60]}), {wait}sn beklenip tekrar denenecek ({server_retries+1}/{self._max_server_retries})")
                    import time as _time
                    _time.sleep(wait)
                    server_retries += 1
                    continue
                attempt += 1
                print(f"[LLM] 🔄 Gemini key/provider fallback: {str(exc)[:120]}")
        raise last_exc if last_exc else RuntimeError("Hiç Gemini key tanımlı değil")

    async def ainvoke(self, messages, **kwargs):
        last_exc = None
        n = len(self._clients)
        if not n:
            raise RuntimeError("Gemini client oluşturulamadı")

        server_retries = 0
        attempt = 0
        while attempt < n + self._max_server_retries:
            idx, client = self._select_client(attempt)
            try:
                result = await client.ainvoke(messages, **kwargs)
                self._cursor = (idx + 1) % n
                return result
            except Exception as exc:
                last_exc = exc
                if _is_retryable_error(exc) and server_retries < self._max_server_retries:
                    wait = 2 ** server_retries
                    print(f"[LLM] ⏳ Gemini hatası (async) ({str(exc)[:60]}), {wait}sn beklenip tekrar denenecek ({server_retries+1}/{self._max_server_retries})")
                    await _asyncio_for_decorators.sleep(wait)
                    server_retries += 1
                    continue
                attempt += 1
                print(f"[LLM] 🔄 Gemini async fallback: {str(exc)[:120]}")
        raise last_exc if last_exc else RuntimeError("Hiç Gemini key tanımlı değil")

    def with_structured_output(self, *args, **kwargs):
        if not self._clients:
            raise RuntimeError("Gemini client oluşturulamadı")
        return self._clients[self._cursor].with_structured_output(*args, **kwargs)

    def bind_tools(self, *args, **kwargs):
        if not self._clients:
            raise RuntimeError("Gemini client oluşturulamadı")
        return self._clients[self._cursor].bind_tools(*args, **kwargs)


@dataclass
class _ProviderEntry:
    name: str
    client: Any


class FallbackLLM:
    """Birden fazla provider arasında fallback yapan ince sarıcı."""

    def __init__(self, providers: list[_ProviderEntry], structured_schema: Any = None, bound_tools: Any = None):
        self._providers = providers
        self._structured_schema = structured_schema
        self._bound_tools = bound_tools

    def _clone(self, *, structured_schema: Any = None, bound_tools: Any = None):
        return FallbackLLM(
            self._providers,
            structured_schema=self._structured_schema if structured_schema is None else structured_schema,
            bound_tools=self._bound_tools if bound_tools is None else bound_tools,
        )

    def _prepare_client(self, entry: _ProviderEntry):
        client = entry.client
        if self._structured_schema is not None and hasattr(client, "with_structured_output"):
            client = client.with_structured_output(self._structured_schema)
        if self._bound_tools is not None and hasattr(client, "bind_tools"):
            client = client.bind_tools(self._bound_tools)
        return client

    def _iter_clients(self):
        if not self._providers:
            raise RuntimeError("LLM provider bulunamadı")
        order = os.getenv("LLM_FALLBACK_ORDER", "gemini,openai,anthropic,groq,mistral").lower().split(",")
        ordered = []
        seen = set()
        for name in order:
            name = name.strip()
            for entry in self._providers:
                if entry.name == name and entry.name not in seen:
                    ordered.append(entry)
                    seen.add(entry.name)
        for entry in self._providers:
            if entry.name not in seen:
                ordered.append(entry)
                seen.add(entry.name)
        return ordered

    def invoke(self, messages, **kwargs):
        last_exc = None
        for entry in self._iter_clients():
            try:
                client = self._prepare_client(entry)
                print(f"[LLM] 🧭 Provider deneniyor: {entry.name}")
                return client.invoke(messages, **kwargs)
            except Exception as exc:
                last_exc = exc
                print(f"[LLM] ⚠️ Provider fallback ({entry.name}): {str(exc)[:140]}")
                continue
        raise last_exc if last_exc else RuntimeError("Hiçbir LLM provider çalışmadı")

    async def ainvoke(self, messages, **kwargs):
        last_exc = None
        for entry in self._iter_clients():
            try:
                client = self._prepare_client(entry)
                print(f"[LLM] 🧭 Provider deneniyor (async): {entry.name}")
                return await client.ainvoke(messages, **kwargs)
            except Exception as exc:
                last_exc = exc
                print(f"[LLM] ⚠️ Provider fallback (async/{entry.name}): {str(exc)[:140]}")
                continue
        raise last_exc if last_exc else RuntimeError("Hiçbir LLM provider çalışmadı")

    def with_structured_output(self, *args, **kwargs):
        return self._clone(structured_schema=args[0] if args else kwargs.get("schema"))

    def bind_tools(self, tools, **kwargs):
        return self._clone(bound_tools=tools)


def _build_provider_chain(model: str, temperature: float, timeout: int, disable_thinking: bool = False):
    providers: list[_ProviderEntry] = []

    # 1) Gemini (ana provider)
    try:
        gemini_client = RotatingGeminiLLM(model=model, temperature=temperature, timeout=timeout, disable_thinking=disable_thinking)
        providers.append(_ProviderEntry(name="gemini", client=gemini_client))
    except Exception as exc:
        print(f"[LLM] ⚠️ Gemini provider oluşturulamadı: {exc}")

    # 2) OpenAI
    if os.getenv("OPENAI_API_KEY"):
        try:
            from langchain_openai import ChatOpenAI
            openai_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
            providers.append(_ProviderEntry(name="openai", client=ChatOpenAI(model=openai_model, temperature=temperature, timeout=timeout)))
            print(f"[LLM] ✅ OpenAI fallback aktif: {openai_model}")
        except Exception as exc:
            print(f"[LLM] ⚠️ OpenAI provider kullanılamadı: {exc}")

    # 3) Anthropic
    if os.getenv("ANTHROPIC_API_KEY"):
        try:
            from langchain_anthropic import ChatAnthropic
            anthropic_model = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest")
            providers.append(_ProviderEntry(name="anthropic", client=ChatAnthropic(model=anthropic_model, temperature=temperature, timeout=timeout)))
            print(f"[LLM] ✅ Anthropic fallback aktif: {anthropic_model}")
        except Exception as exc:
            print(f"[LLM] ⚠️ Anthropic provider kullanılamadı: {exc}")

    # 4) Groq
    if os.getenv("GROQ_API_KEY"):
        try:
            from langchain_groq import ChatGroq
            groq_model = os.getenv("GROQ_MODEL", "llama-3.1-70b-versatile")
            providers.append(_ProviderEntry(name="groq", client=ChatGroq(model=groq_model, temperature=temperature, timeout=timeout)))
            print(f"[LLM] ✅ Groq fallback aktif: {groq_model}")
        except Exception as exc:
            print(f"[LLM] ⚠️ Groq provider kullanılamadı: {exc}")

    # 5) Mistral
    if os.getenv("MISTRAL_API_KEY"):
        try:
            from langchain_mistralai import ChatMistralAI
            mistral_model = os.getenv("MISTRAL_MODEL", "mistral-large-latest")
            providers.append(_ProviderEntry(name="mistral", client=ChatMistralAI(model=mistral_model, temperature=temperature, timeout=timeout)))
            print(f"[LLM] ✅ Mistral fallback aktif: {mistral_model}")
        except Exception as exc:
            print(f"[LLM] ⚠️ Mistral provider kullanılamadı: {exc}")

    if not providers:
        raise RuntimeError("Hiçbir LLM provider başlatılamadı")

    order_names = ", ".join(entry.name for entry in providers)
    print(f"[LLM] 🌐 Provider zinciri: {order_names}")
    return providers


# Tüm LLM'ler artık provider fallback zinciri üzerinde
llm_ner = FallbackLLM(_build_provider_chain(FAST_MODEL, 0, 30))
llm_agent = FallbackLLM(_build_provider_chain(SMART_MODEL, 0.3, 30, disable_thinking=True))
llm_planner = FallbackLLM(_build_provider_chain(FAST_MODEL, 0.2, 30))
llm_evaluator = FallbackLLM(_build_provider_chain(FAST_MODEL, 0, 30))
llm_responder = FallbackLLM(_build_provider_chain(SMART_MODEL, 0.3, 30, disable_thinking=True))

# Tool listesi
tools = [YokAtlasTool(), WebSearchTool(), fetch_link_content]

# Agent'e tool binding — thinking kapalı provider zincirinden direkt bind
llm_with_tools = FallbackLLM(
    _build_provider_chain(SMART_MODEL, 0.3, 30, disable_thinking=True),
    bound_tools=tools,
)


__all__ = [
    "os",
    "_asyncio_for_decorators",
    "dataclass",
    "Any",
    "Optional",
    "_collect_gemini_keys",
    "_asyncio_for_decorators",
    "_ProviderEntry",
    "RotatingGeminiLLM",
    "FallbackLLM",
    "_build_provider_chain",
    "llm_ner",
    "llm_agent",
    "llm_planner",
    "llm_evaluator",
    "llm_responder",
    "tools",
    "llm_with_tools",
    "FAST_MODEL",
    "SMART_MODEL",
    "GEMINI_KEYS",
    "YokAtlasTool",
    "WebSearchTool",
    "fetch_link_content",
]
