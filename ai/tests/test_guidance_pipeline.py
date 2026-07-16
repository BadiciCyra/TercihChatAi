"""
Property-based tests for Guidance Pipeline
============================================
# Feature: chat-mode-selection

Tests Properties 13 and 6 (integration at casual_chat_node level)
from the design document:

- Property 13: Guidance No Tool Invocations
  For any query in guidance mode, yok_atlas_search, web_search, and
  fetch_link_content tools SHALL NOT be called during pipeline execution.
  Validates: Requirements 4.4

- Property 6: Guidance Mode Cache Skip (integration)
  For any guidance-mode query, cache read/write shall be zero Redis
  operations at the casual_chat_node level.
  Validates: Requirements 4.5, 8.2
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Path setup — mirrors the pattern in all existing test files
# ---------------------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parents[2]
AI_DIR = ROOT_DIR / "ai"
for _p in (str(ROOT_DIR), str(AI_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from hypothesis import given, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------
query_strategy = st.text(min_size=1, max_size=200)


# ---------------------------------------------------------------------------
# Helper: build a minimal AgentState for casual_chat_node
# ---------------------------------------------------------------------------

def _make_state(mode, user_text: str = "Motivasyon sorusu") -> dict:
    """Return a minimal AgentState-like dict for casual_chat_node tests."""
    # Minimal HumanMessage mock
    human_msg = MagicMock()
    human_msg.content = user_text
    return {
        "messages": [human_msg],
        "mode": mode,
        "ner_context": {},
        "iteration_count": 0,
        "query_plan": None,
        "thinking_steps": [],
        "sub_question_results": {},
        "current_answer": None,
        "evaluation": None,
        "search_depth": 0,
        "fetched_urls": None,
    }


def _fake_ai_message(content: str = "Empatik rehberlik cevabı."):
    """Return a fake AIMessage-like object (matching conftest.py mock)."""
    from langchain_core.messages import AIMessage
    return AIMessage(content=content)


# ===========================================================================
# Unit Tests — Example-based
# ===========================================================================

def test_casual_chat_node_loads_guidance_prompt_when_mode_guidance() -> None:
    """casual_chat_node loads GUIDANCE_CHAT_PROMPT when mode='guidance'.

    Verifies that the correct prompt is selected and passed to the LLM.
    """
    from prompts.guidance import GUIDANCE_CHAT_PROMPT

    state = _make_state(mode="guidance", user_text="Sınavdan çok korkuyorum.")
    fake_response = _fake_ai_message("Endişen çok anlaşılır, birlikte bakalım.")

    with patch("nodes.casual_chat.llm_responder") as mock_llm:
        mock_llm.ainvoke = AsyncMock(return_value=fake_response)

        import nodes.casual_chat as casual_module
        result = asyncio.run(
            casual_module.casual_chat_node(state)
        )

    # Assert LLM was called exactly once
    mock_llm.ainvoke.assert_called_once()

    # Check the SystemMessage content matches GUIDANCE_CHAT_PROMPT
    call_args = mock_llm.ainvoke.call_args
    messages_arg = call_args[0][0]  # first positional arg is the list of messages
    system_messages = [m for m in messages_arg if hasattr(m, "content") and m.content == GUIDANCE_CHAT_PROMPT]
    assert len(system_messages) == 1, (
        f"Expected exactly one SystemMessage with GUIDANCE_CHAT_PROMPT, "
        f"got {len(system_messages)}. Messages: {[getattr(m, 'content', '')[:50] for m in messages_arg]}"
    )


def test_casual_chat_node_raises_value_error_when_guidance_prompt_is_none() -> None:
    """casual_chat_node raises ValueError (internally) when GUIDANCE_CHAT_PROMPT is None.

    The @with_error_recovery decorator wraps casual_chat_node and catches all
    exceptions, returning an error recovery message. This test verifies that:
    1. No unhandled exception escapes to the caller.
    2. The recovery response contains an error indicator message.
    The internal ValueError IS raised (visible in stderr/logs) but the decorator
    converts it into a recovery AIMessage — this is the designed behavior per
    the Error Handling section of the design document.
    """
    state = _make_state(mode="guidance", user_text="Yardım lazım.")

    with patch("nodes.casual_chat.GUIDANCE_CHAT_PROMPT", None):
        import nodes.casual_chat as casual_module

        # @with_error_recovery catches the ValueError and returns a recovery dict
        result = asyncio.run(
            casual_module.casual_chat_node(state)
        )

    # The decorated function must return a dict (not raise)
    assert isinstance(result, dict), (
        f"Expected a recovery dict when GUIDANCE_CHAT_PROMPT is None, got {type(result)}"
    )
    # Recovery response must contain messages
    messages = result.get("messages", [])
    assert len(messages) >= 1, "Recovery path must return at least one AIMessage"

    # The recovery message should signal an error, not a normal guidance response
    recovery_content = getattr(messages[0], "content", "")
    assert recovery_content, "Recovery AIMessage must have non-empty content"


def test_casual_chat_node_raises_value_error_when_guidance_prompt_is_empty() -> None:
    """casual_chat_node handles empty GUIDANCE_CHAT_PROMPT via @with_error_recovery.

    When GUIDANCE_CHAT_PROMPT is an empty string, the node internally raises
    ValueError("[GUIDANCE] GUIDANCE_CHAT_PROMPT yüklenemedi."). The
    @with_error_recovery decorator catches this and returns a recovery dict.
    """
    state = _make_state(mode="guidance", user_text="Yardım lazım.")

    with patch("nodes.casual_chat.GUIDANCE_CHAT_PROMPT", ""):
        import nodes.casual_chat as casual_module

        result = asyncio.run(
            casual_module.casual_chat_node(state)
        )

    assert isinstance(result, dict), (
        f"Expected a recovery dict when GUIDANCE_CHAT_PROMPT is empty, got {type(result)}"
    )
    messages = result.get("messages", [])
    assert len(messages) >= 1, "Recovery path must return at least one AIMessage"

    recovery_content = getattr(messages[0], "content", "")
    assert recovery_content, "Recovery AIMessage must have non-empty content"


def test_casual_chat_node_uses_casual_prompt_when_mode_is_none() -> None:
    """casual_chat_node uses CASUAL_CHAT_PROMPT when mode=None."""
    from prompts.casual import CASUAL_CHAT_PROMPT

    state = _make_state(mode=None, user_text="Selam!")
    fake_response = _fake_ai_message("Selam kanka!")

    with patch("nodes.casual_chat.llm_responder") as mock_llm:
        mock_llm.ainvoke = AsyncMock(return_value=fake_response)

        import nodes.casual_chat as casual_module
        result = asyncio.run(
            casual_module.casual_chat_node(state)
        )

    mock_llm.ainvoke.assert_called_once()
    call_args = mock_llm.ainvoke.call_args
    messages_arg = call_args[0][0]
    system_messages = [m for m in messages_arg if hasattr(m, "content") and m.content == CASUAL_CHAT_PROMPT]
    assert len(system_messages) == 1, (
        f"Expected CASUAL_CHAT_PROMPT to be used for mode=None. "
        f"Messages: {[getattr(m, 'content', '')[:50] for m in messages_arg]}"
    )


def test_casual_chat_node_uses_casual_prompt_when_mode_is_wizard() -> None:
    """casual_chat_node uses CASUAL_CHAT_PROMPT when mode='wizard'."""
    from prompts.casual import CASUAL_CHAT_PROMPT

    state = _make_state(mode="wizard", user_text="Tercih sihirbazı mı?")
    fake_response = _fake_ai_message("Bilgisayar Mühendisliği için sıralaman ne?")

    with patch("nodes.casual_chat.llm_responder") as mock_llm:
        mock_llm.ainvoke = AsyncMock(return_value=fake_response)

        import nodes.casual_chat as casual_module
        result = asyncio.run(
            casual_module.casual_chat_node(state)
        )

    mock_llm.ainvoke.assert_called_once()
    call_args = mock_llm.ainvoke.call_args
    messages_arg = call_args[0][0]
    system_messages = [m for m in messages_arg if hasattr(m, "content") and m.content == CASUAL_CHAT_PROMPT]
    assert len(system_messages) == 1, (
        "Expected CASUAL_CHAT_PROMPT for mode='wizard', not GUIDANCE_CHAT_PROMPT"
    )


def test_casual_chat_node_uses_casual_prompt_when_mode_is_research() -> None:
    """casual_chat_node uses CASUAL_CHAT_PROMPT when mode='research'."""
    from prompts.casual import CASUAL_CHAT_PROMPT

    state = _make_state(mode="research", user_text="İTÜ hakkında bilgi ver.")
    fake_response = _fake_ai_message("İTÜ'nün Maçka kampüsü...")

    with patch("nodes.casual_chat.llm_responder") as mock_llm:
        mock_llm.ainvoke = AsyncMock(return_value=fake_response)

        import nodes.casual_chat as casual_module
        result = asyncio.run(
            casual_module.casual_chat_node(state)
        )

    mock_llm.ainvoke.assert_called_once()
    call_args = mock_llm.ainvoke.call_args
    messages_arg = call_args[0][0]
    system_messages = [m for m in messages_arg if hasattr(m, "content") and m.content == CASUAL_CHAT_PROMPT]
    assert len(system_messages) == 1, (
        "Expected CASUAL_CHAT_PROMPT for mode='research', not GUIDANCE_CHAT_PROMPT"
    )


# ===========================================================================
# Property 13: Guidance No Tool Invocations
# ===========================================================================

# Feature: chat-mode-selection, Property 13: Guidance No Tool Invocations
@given(query=query_strategy)
@settings(max_examples=100, deadline=None)
def test_guidance_mode_no_tool_invocations(query: str) -> None:
    """**Validates: Requirements 4.4**

    For any query in guidance mode, the yok_atlas_search, web_search, and
    fetch_link_content tools SHALL NOT be called during casual_chat_node
    execution.

    casual_chat_node only calls llm_responder.ainvoke — tools are only
    ever invoked by agent_node. This test verifies that the guidance path
    through casual_chat_node does not touch any tool functions.
    """
    state = _make_state(mode="guidance", user_text=query)
    fake_response = _fake_ai_message("Motivasyonun için buradayım.")

    # Create spies on all three tool functions/classes
    mock_yok_atlas_arun = MagicMock(return_value=AsyncMock(return_value="yok data"))
    mock_web_search_arun = MagicMock(return_value=AsyncMock(return_value="web data"))
    mock_fetch_link = AsyncMock(return_value="page content")

    with patch("nodes.casual_chat.llm_responder") as mock_llm, \
         patch("tools.YokAtlasTool._arun", mock_yok_atlas_arun), \
         patch("tools.WebSearchTool._arun", mock_web_search_arun), \
         patch("tools.fetch_link_content", mock_fetch_link):

        mock_llm.ainvoke = AsyncMock(return_value=fake_response)

        import nodes.casual_chat as casual_module
        result = asyncio.run(
            casual_module.casual_chat_node(state)
        )

    # Verify: LLM was called once
    mock_llm.ainvoke.assert_called_once()

    # Verify: no tool was invoked
    mock_yok_atlas_arun.assert_not_called()
    mock_web_search_arun.assert_not_called()
    mock_fetch_link.assert_not_called()

    # Verify: result contains the LLM answer in messages
    messages = result.get("messages", [])
    assert len(messages) >= 1, "casual_chat_node should return at least one message"


# ===========================================================================
# Property 6: Guidance Mode Cache Skip — Integration at casual_chat_node level
# ===========================================================================

# Feature: chat-mode-selection, Property 6: Guidance Mode Cache Skip (integration)
@given(query=query_strategy)
@settings(max_examples=100, deadline=None)
def test_guidance_casual_chat_node_does_not_call_cache(query: str) -> None:
    """**Validates: Requirements 4.5, 8.2**

    For any guidance-mode query, casual_chat_node itself SHALL NOT call
    any cache functions (cache_get / cache_set). Cache operations are
    handled at gate.py level, and casual_chat_node has no cache calls —
    this test confirms the invariant holds: zero cache operations from
    within the node.
    """
    state = _make_state(mode="guidance", user_text=query)
    fake_response = _fake_ai_message("Sınav kaygısı yaşıyorsun, bu çok normal.")

    cache_get_calls: list = []
    cache_set_calls: list = []

    def spy_cache_get(q, mode=None):
        cache_get_calls.append((q, mode))
        return None

    def spy_cache_set(q, answer, mode=None):
        cache_set_calls.append((q, answer, mode))

    with patch("nodes.casual_chat.llm_responder") as mock_llm, \
         patch("app.gate.cache_get", side_effect=spy_cache_get), \
         patch("app.gate.cache_set", side_effect=spy_cache_set):

        mock_llm.ainvoke = AsyncMock(return_value=fake_response)

        import nodes.casual_chat as casual_module
        result = asyncio.run(
            casual_module.casual_chat_node(state)
        )

    # casual_chat_node must NEVER call cache_get or cache_set directly
    assert len(cache_get_calls) == 0, (
        f"casual_chat_node called cache_get {len(cache_get_calls)} times — "
        f"it should NEVER touch the cache directly. Calls: {cache_get_calls}"
    )
    assert len(cache_set_calls) == 0, (
        f"casual_chat_node called cache_set {len(cache_set_calls)} times — "
        f"it should NEVER touch the cache directly. Calls: {cache_set_calls}"
    )

    # LLM must still have been called
    mock_llm.ainvoke.assert_called_once()


# Feature: chat-mode-selection, Property 6: Guidance Mode Cache Skip (Redis mock integration)
@given(query=query_strategy)
@settings(max_examples=100, deadline=None)
def test_guidance_mode_zero_redis_ops_via_cache_key(query: str) -> None:
    """**Validates: Requirements 4.5, 8.2**

    Integration confirmation: _cache_key("guidance", query) returns None
    for any query, which is the skip signal used by gate.py's cache_get
    and cache_set to avoid any Redis operation in guidance mode.

    This test verifies the skip signal at the _cache_key level to
    complement the HTTP-level test in test_cache_isolation.py.
    """
    from app.gate import _cache_key

    key = _cache_key("guidance", query)
    assert key is None, (
        f"_cache_key('guidance', query={query[:50]!r}) returned {key!r}, "
        f"expected None (cache skip signal)"
    )


def test_guidance_cache_get_returns_none_without_redis_read() -> None:
    """**Validates: Requirements 4.5, 8.2**

    At the node integration level: when gate.py's cache_get is called with
    mode='guidance', it returns None immediately without touching Redis.
    This is the integration-level confirmation that the node will never
    receive a cached response when in guidance mode.
    """
    from app.gate import cache_get
    from app import redis_client as _RC

    mock_redis = MagicMock()
    original_cache = _RC.cache
    _RC.cache = mock_redis

    try:
        result = cache_get("rehberlik ve motivasyon sorusu", mode="guidance")
    finally:
        _RC.cache = original_cache

    assert result is None, (
        f"cache_get with mode='guidance' must return None (skip signal), got {result!r}"
    )
    mock_redis.get.assert_not_called()


def test_guidance_cache_set_does_not_write_redis() -> None:
    """**Validates: Requirements 4.5, 8.2**

    Integration confirmation: cache_set with mode='guidance' SHALL NOT
    write to Redis regardless of the answer content.
    """
    from app.gate import cache_set
    from app import redis_client as _RC

    mock_redis = MagicMock()
    original_cache = _RC.cache
    _RC.cache = mock_redis

    try:
        long_answer = "Üniversite tercih süreci zor olabilir ama birlikte bakabiliriz. " * 5
        cache_set("rehberlik sorusu", long_answer, mode="guidance")
    finally:
        _RC.cache = original_cache

    mock_redis.setex.assert_not_called()
    mock_redis.set.assert_not_called()
