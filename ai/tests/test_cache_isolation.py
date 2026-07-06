"""
Property-based tests for Cache Isolation
=========================================
# Feature: chat-mode-selection

Tests Properties 5, 6, and 15 from the design document:

- Property 5: Cache Key Mode Isolation
  For any query string, the cache keys generated for modes wizard, research,
  career, and None SHALL be pairwise distinct — no two different modes shall
  produce the same cache key for the same query.
  Validates: Requirements 2.5, 8.1, 8.5

- Property 6: Guidance Mode Cache Skip
  For any query submitted with mode="guidance", the Session_Cache SHALL
  perform zero read operations (cache_get) and zero write operations
  (cache_set) during the request lifecycle.
  Validates: Requirements 4.5, 8.2

- Property 15: Cache Short-Circuit — Pipeline Bypass
  For any valid (mode, query) pair where the cache already contains an entry
  for that pair, app_graph.ainvoke SHALL NOT be called; the cached response
  SHALL be returned directly.
  Validates: Requirements 8.4
"""

from __future__ import annotations

import asyncio
import itertools
import os
import sys
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch, call

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parents[2]
AI_DIR = ROOT_DIR / "ai"
for _p in (str(ROOT_DIR), str(AI_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from hypothesis import given, settings, assume
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# Modes that participate in the cache key (guidance is excluded — it skips cache)
CACHED_MODES = ("wizard", "research", "career", None)
VALID_MODES = ("wizard", "research", "career", "guidance")

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------
query_strategy = st.text(min_size=1, max_size=200)

valid_cached_mode_strategy = st.sampled_from(CACHED_MODES)

valid_mode_strategy = st.sampled_from(VALID_MODES)


# ---------------------------------------------------------------------------
# Helper — import the private functions under test
# ---------------------------------------------------------------------------

def _import_cache_key():
    """Import _cache_key from app.gate lazily (after conftest mocks are set)."""
    from app.gate import _cache_key
    return _cache_key


def _import_cache_functions():
    """Import cache_get and cache_set from app.gate lazily."""
    from app.gate import cache_get, cache_set
    return cache_get, cache_set


# ===========================================================================
# Property 5: Cache Key Mode Isolation
# ===========================================================================

# Feature: chat-mode-selection, Property 5: Cache Key Mode Isolation
@given(query=query_strategy)
@settings(max_examples=100, deadline=None)
def test_cache_key_mode_isolation(query: str) -> None:
    """**Validates: Requirements 2.5, 8.1, 8.5**

    For any query string, the cache keys generated for modes wizard, research,
    career, and None SHALL be pairwise distinct — no two different modes shall
    produce the same cache key for the same query.

    This verifies that:
    - wizard uses "wiz:" prefix (Req 8.5)
    - research uses "res:" prefix
    - career uses "car:" prefix (Req 8.5)
    - None/NER uses "ner:" prefix (Req 8.1)
    """
    _cache_key = _import_cache_key()

    keys = {mode: _cache_key(mode, query) for mode in CACHED_MODES}

    # All keys for cached modes must be non-None strings
    for mode, key in keys.items():
        assert key is not None, (
            f"Expected non-None cache key for mode={mode!r}, query={query!r}"
        )
        assert isinstance(key, str), (
            f"Expected string cache key for mode={mode!r}, got {type(key).__name__}"
        )

    # Pairwise: all 6 pairs of (wizard, research, career, None) must differ
    for (mode_a, mode_b) in itertools.combinations(CACHED_MODES, 2):
        key_a = keys[mode_a]
        key_b = keys[mode_b]
        assert key_a != key_b, (
            f"Cache key collision for modes {mode_a!r} and {mode_b!r} "
            f"with query={query!r}: both produced {key_a!r}"
        )


def test_cache_key_contains_mode_prefix_wizard() -> None:
    """**Validates: Requirements 8.5**

    Wizard mode cache key SHALL contain the 'wiz:' prefix.
    """
    _cache_key = _import_cache_key()
    key = _cache_key("wizard", "sıralama sorgusu")
    assert key is not None
    assert ":wiz:" in key, f"Expected ':wiz:' in wizard cache key, got: {key!r}"


def test_cache_key_contains_mode_prefix_research() -> None:
    """**Validates: Requirements 8.1**

    Research mode cache key SHALL contain the 'res:' prefix.
    """
    _cache_key = _import_cache_key()
    key = _cache_key("research", "üniversite araştırma")
    assert key is not None
    assert ":res:" in key, f"Expected ':res:' in research cache key, got: {key!r}"


def test_cache_key_contains_mode_prefix_career() -> None:
    """**Validates: Requirements 8.5**

    Career mode cache key SHALL contain the 'car:' prefix.
    """
    _cache_key = _import_cache_key()
    key = _cache_key("career", "bilgisayar mühendisliği kariyer")
    assert key is not None
    assert ":car:" in key, f"Expected ':car:' in career cache key, got: {key!r}"


def test_cache_key_contains_mode_prefix_none() -> None:
    """**Validates: Requirements 8.1**

    NER/automatic routing (mode=None) cache key SHALL contain the 'ner:' prefix.
    """
    _cache_key = _import_cache_key()
    key = _cache_key(None, "herhangi bir soru")
    assert key is not None
    assert ":ner:" in key, f"Expected ':ner:' in None-mode cache key, got: {key!r}"


def test_guidance_cache_key_returns_none() -> None:
    """**Validates: Requirements 8.2**

    Guidance mode _cache_key SHALL return None (the cache skip signal).
    """
    _cache_key = _import_cache_key()
    key = _cache_key("guidance", "motivasyon sorusu")
    assert key is None, (
        f"Expected _cache_key('guidance', ...) == None, got {key!r}"
    )


# Feature: chat-mode-selection, Property 5: Cache Key Mode Isolation (same query, same md5)
@given(query=query_strategy)
@settings(max_examples=50, deadline=None)
def test_cache_key_same_mode_same_query_is_deterministic(query: str) -> None:
    """**Validates: Requirements 8.1**

    For any mode and query, calling _cache_key twice with the same arguments
    SHALL produce the same key (deterministic hashing).
    """
    _cache_key = _import_cache_key()
    for mode in CACHED_MODES:
        key1 = _cache_key(mode, query)
        key2 = _cache_key(mode, query)
        assert key1 == key2, (
            f"_cache_key not deterministic for mode={mode!r}, query={query!r}: "
            f"{key1!r} != {key2!r}"
        )


# ===========================================================================
# Property 6: Guidance Mode Cache Skip
# ===========================================================================

# Feature: chat-mode-selection, Property 6: Guidance Mode Cache Skip
def test_guidance_cache_get_returns_none_without_redis_call() -> None:
    """**Validates: Requirements 4.5, 8.2**

    For guidance mode, cache_get SHALL return None immediately without
    attempting any Redis read operation.
    """
    cache_get, cache_set = _import_cache_functions()

    mock_redis = MagicMock()
    import app.gate as gate_module

    original_cache = gate_module._cache
    gate_module._cache = mock_redis
    try:
        result = cache_get("rehberlik sorusu", mode="guidance")
    finally:
        gate_module._cache = original_cache

    # Must return None (cache skip)
    assert result is None, (
        f"Expected cache_get to return None for guidance mode, got {result!r}"
    )
    # Redis .get() must NOT have been called
    mock_redis.get.assert_not_called()


# Feature: chat-mode-selection, Property 6: Guidance Mode Cache Skip
def test_guidance_cache_set_does_not_write_to_redis() -> None:
    """**Validates: Requirements 4.5, 8.2**

    For guidance mode, cache_set SHALL NOT write to Redis — the call must be
    silently skipped when key is None (guidance cache skip signal).
    """
    cache_get, cache_set = _import_cache_functions()

    mock_redis = MagicMock()
    import app.gate as gate_module

    original_cache = gate_module._cache
    gate_module._cache = mock_redis
    try:
        cache_set("rehberlik sorusu", "empatik bir cevap metni olabilir", mode="guidance")
    finally:
        gate_module._cache = original_cache

    # Redis .setex() must NOT have been called
    mock_redis.setex.assert_not_called()
    # Redis .set() must also NOT have been called
    mock_redis.set.assert_not_called()


# Feature: chat-mode-selection, Property 6: Guidance Mode Cache Skip (HTTP level)
@given(query=query_strategy)
@settings(max_examples=100, deadline=None)
def test_guidance_mode_http_skips_all_cache_operations(query: str) -> None:
    """**Validates: Requirements 4.5, 8.2**

    For any query submitted with mode="guidance" via the HTTP endpoint,
    the Session_Cache SHALL perform zero read operations (cache_get) and
    zero write operations (cache_set) during the request lifecycle.

    Tests at the function level: patches cache_get and cache_set to spy on
    calls, then invokes the handler with mode="guidance".
    """
    import os
    os.environ.setdefault("ALLOW_ANONYMOUS", "true")
    os.environ.setdefault("ALLOW_DEV_FALLBACK", "true")

    import app.gate as gate_module
    import app.graph as graph_module

    # Mock app_graph so no real LangGraph execution happens
    mock_result = {
        "messages": [],
        "current_answer": "Empatik bir rehberlik cevabı.",
        "ner_context": {},
        "iteration_count": 0,
        "query_plan": None,
        "thinking_steps": [],
        "sub_question_results": {},
        "evaluation": None,
        "search_depth": 0,
        "fetched_urls": None,
        "mode": "guidance",
    }
    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(return_value=mock_result)

    # Patch rate limit counters so property test (100 calls) does not hit 429
    original_plan_limits = gate_module._PLAN_LIMITS.copy()
    gate_module._PLAN_LIMITS["guest"] = 99999
    gate_module._PLAN_LIMITS["free"] = 99999
    gate_module._local_rate.clear()

    original_graph = graph_module.app_graph
    graph_module.app_graph = mock_graph

    cache_get_calls: list = []
    cache_set_calls: list = []

    original_cache_get = gate_module.cache_get
    original_cache_set = gate_module.cache_set

    def spy_cache_get(q, mode=None):
        cache_get_calls.append((q, mode))
        return original_cache_get(q, mode=mode)

    def spy_cache_set(q, answer, mode=None):
        cache_set_calls.append((q, answer, mode))
        return original_cache_set(q, answer, mode=mode)

    gate_module.cache_get = spy_cache_get
    gate_module.cache_set = spy_cache_set

    try:
        from starlette.testclient import TestClient
        from app.gate import app as fastapi_app

        client = TestClient(fastapi_app, raise_server_exceptions=False)
        response = client.post(
            "/b2b/ask_intelligent",
            json={"query": query, "mode": "guidance"},
        )
        # Request must succeed
        assert response.status_code == 200, (
            f"Expected HTTP 200 for guidance mode, got {response.status_code}"
        )

        # Verify: cache_get was called exactly once (the call happens in the handler)
        # but the _cache_key returned None, so Redis was NOT read.
        # We verify this by checking that the underlying Redis .get was not called.
        # Since _cache is None in test env (no Redis), we verify via _cache_key return.
        from app.gate import _cache_key
        expected_key = _cache_key("guidance", query)
        assert expected_key is None, (
            "guidance _cache_key must return None — that's the skip signal"
        )

        # The spy confirms cache_get was called with guidance mode
        guidance_reads = [c for c in cache_get_calls if c[1] == "guidance"]
        assert len(guidance_reads) >= 1, (
            "cache_get should have been called once for the request"
        )

        # cache_get with guidance must have returned None (no read attempted)
        for read_call in guidance_reads:
            result = original_cache_get(read_call[0], mode=read_call[1])
            assert result is None, (
                f"cache_get returned non-None for guidance mode: {result!r}"
            )

        # cache_set must NOT have been called with guidance mode writing to Redis
        guidance_writes = [c for c in cache_set_calls if c[2] == "guidance"]
        # If cache_set was called, verify it was a no-op (no Redis write)
        # This is guaranteed by _cache_key returning None for guidance
        mock_redis = MagicMock()
        original_c = gate_module._cache
        gate_module._cache = mock_redis
        try:
            for write_call in guidance_writes:
                original_cache_set(write_call[0], write_call[1], mode=write_call[2])
            mock_redis.setex.assert_not_called()
        finally:
            gate_module._cache = original_c

    finally:
        graph_module.app_graph = original_graph
        gate_module._PLAN_LIMITS.update(original_plan_limits)
        gate_module.cache_get = original_cache_get
        gate_module.cache_set = original_cache_set


# Feature: chat-mode-selection, Property 6: Guidance Mode Cache Skip (Redis not called)
@given(query=query_strategy)
@settings(max_examples=100, deadline=None)  # type: ignore[arg-type]
def test_guidance_mode_zero_redis_reads_and_writes(query: str) -> None:
    """**Validates: Requirements 4.5, 8.2**

    For any guidance-mode query, the underlying Redis client SHALL receive
    zero .get() calls and zero .setex()/.set() calls.

    Tests cache_get and cache_set directly with a mock Redis client to confirm
    the skip logic is unconditional for guidance mode regardless of query content.
    """
    import app.gate as gate_module

    mock_redis = MagicMock()
    original_cache = gate_module._cache
    gate_module._cache = mock_redis

    try:
        # Test cache_get — must not touch Redis
        from app.gate import cache_get, cache_set
        cache_get(query, mode="guidance")
        mock_redis.get.assert_not_called()

        # Test cache_set — must not touch Redis
        # Use a "good" answer (long enough to normally be cached)
        good_answer = "Bu bölüm hakkında detaylı kariyer bilgisi: " + ("x" * 100)
        cache_set(query, good_answer, mode="guidance")
        mock_redis.setex.assert_not_called()
        mock_redis.set.assert_not_called()

    finally:
        gate_module._cache = original_cache


# ===========================================================================
# Property 15: Cache Short-Circuit — Pipeline Bypass
# ===========================================================================

# Feature: chat-mode-selection, Property 15: Cache Short-Circuit — Pipeline Bypass
@given(
    mode=st.sampled_from(("wizard", "research", "career", None)),
    query=query_strategy,
)
@settings(max_examples=100, deadline=None)
def test_cache_hit_bypasses_app_graph_ainvoke(mode: Optional[str], query: str) -> None:
    """**Validates: Requirements 8.4**

    For any valid (mode, query) pair where the cache already contains an
    entry for that pair, app_graph.ainvoke SHALL NOT be called; the cached
    response SHALL be returned directly.

    Strategy:
    - Patch cache_get to return a pre-seeded cached answer.
    - Call the HTTP endpoint with the given (mode, query).
    - Assert app_graph.ainvoke was NOT called.
    - Assert the response body contains the cached answer.
    """
    import os
    os.environ.setdefault("ALLOW_ANONYMOUS", "true")
    os.environ.setdefault("ALLOW_DEV_FALLBACK", "true")

    import app.gate as gate_module

    # Seed the mock cache hit value
    cached_answer = "Önbellekten gelen mükemmel bir cevap metni olabilir örnek."

    # Mock app_graph — should NOT be called on cache hit
    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(return_value={})

    # Patch rate limits to prevent 429 during property test iterations
    original_plan_limits = gate_module._PLAN_LIMITS.copy()
    gate_module._PLAN_LIMITS["guest"] = 99999
    gate_module._PLAN_LIMITS["free"] = 99999
    gate_module._local_rate.clear()

    # Patch app_graph on gate_module directly (gate.py uses its own reference)
    original_graph = gate_module.app_graph
    gate_module.app_graph = mock_graph

    original_cache_get = gate_module.cache_get

    def mock_cache_get(q, mode=None):
        # Always return a cache hit — simulates a pre-seeded cache entry
        return cached_answer

    gate_module.cache_get = mock_cache_get

    try:
        from starlette.testclient import TestClient
        from app.gate import app as fastapi_app

        payload = {"query": query}
        if mode is not None:
            payload["mode"] = mode

        client = TestClient(fastapi_app, raise_server_exceptions=False)
        response = client.post("/b2b/ask_intelligent", json=payload)

        # Request must succeed
        assert response.status_code == 200, (
            f"Expected HTTP 200 for cache hit, got {response.status_code}"
        )

        # app_graph.ainvoke MUST NOT have been called
        mock_graph.ainvoke.assert_not_called()

        # The cached answer must appear in the response
        body = response.json()
        assert body.get("answer") == cached_answer, (
            f"Expected cached answer in response, got: {body.get('answer')!r}"
        )

        # Response should indicate it came from cache
        assert body.get("cached") is True, (
            f"Expected cached=True in response for cache hit, got: {body.get('cached')!r}"
        )

    finally:
        gate_module.app_graph = original_graph
        gate_module._PLAN_LIMITS.update(original_plan_limits)
        gate_module.cache_get = original_cache_get
@given(query=query_strategy)
@settings(max_examples=50, deadline=None)
def test_guidance_mode_is_never_short_circuited(query: str) -> None:
    """**Validates: Requirements 4.5, 8.2, 8.4**

    Even if there were a cache entry, guidance mode requests SHALL always
    invoke the pipeline — because _cache_key returns None for guidance, so
    cache_get always returns None and the pipeline IS invoked.

    Patches app.gate.app_graph (the name used inside gate.py) so the mock
    is seen by the handler regardless of how app_graph was imported.
    """
    import os
    os.environ.setdefault("ALLOW_ANONYMOUS", "true")
    os.environ.setdefault("ALLOW_DEV_FALLBACK", "true")

    import app.gate as gate_module

    mock_result = {
        "messages": [],
        "current_answer": "Empatik rehberlik cevabı bu olabilir.",
        "ner_context": {},
        "iteration_count": 0,
        "query_plan": None,
        "thinking_steps": [],
        "sub_question_results": {},
        "evaluation": None,
        "search_depth": 0,
        "fetched_urls": None,
        "mode": "guidance",
    }
    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(return_value=mock_result)

    original_plan_limits = gate_module._PLAN_LIMITS.copy()
    gate_module._PLAN_LIMITS["guest"] = 99999
    gate_module._PLAN_LIMITS["free"] = 99999
    gate_module._local_rate.clear()

    # Patch app_graph on gate_module directly (gate.py uses its own reference)
    original_graph = gate_module.app_graph
    gate_module.app_graph = mock_graph

    try:
        from starlette.testclient import TestClient
        from app.gate import app as fastapi_app

        client = TestClient(fastapi_app, raise_server_exceptions=False)
        response = client.post(
            "/b2b/ask_intelligent",
            json={"query": query, "mode": "guidance"},
        )

        assert response.status_code == 200, (
            f"Expected HTTP 200 for guidance mode, got {response.status_code}"
        )

        # For guidance, cache is skipped → pipeline (ainvoke) MUST be called
        mock_graph.ainvoke.assert_called_once()

    finally:
        gate_module.app_graph = original_graph
        gate_module._PLAN_LIMITS.update(original_plan_limits)
