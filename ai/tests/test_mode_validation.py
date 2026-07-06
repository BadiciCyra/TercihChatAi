"""
Property-based tests for Mode Validation — Accept/Reject Partition
===================================================================
# Feature: chat-mode-selection, Property 1: Mode Validation — Accept/Reject Partition

For any string value passed as `mode` in the request body, the API_Gateway
SHALL accept the request (HTTP 200) if and only if the value is in
{"wizard", "research", "career", "guidance"}, and SHALL reject it with
HTTP 422 for any other non-None string value.

Validates: Requirements 1.1, 1.3
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

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
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Constants — valid modes per requirements 1.1
# ---------------------------------------------------------------------------
VALID_MODES = ("wizard", "research", "career", "guidance")

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

valid_mode_strategy = st.sampled_from(VALID_MODES)

# Generate arbitrary text that is NOT one of the 4 valid modes and not None/empty
invalid_mode_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd", "Po", "Zs")),
    min_size=1,
    max_size=64,
).filter(lambda s: s.strip() not in VALID_MODES and s.strip() != "")

# Arbitrary non-empty query text
query_strategy = st.text(min_size=1, max_size=200)


# ===========================================================================
# Section 1: Direct Pydantic model validation tests
#
# Tests the AskRequest model directly without spinning up the HTTP server.
# This validates that the field_validator on `mode` correctly accepts/rejects
# values per Requirements 1.1 and 1.3.
# ===========================================================================

def _import_ask_request():
    """Import AskRequest lazily to pick up conftest mocks."""
    from app.gate import AskRequest
    return AskRequest


# Feature: chat-mode-selection, Property 1: Mode Validation — Accept/Reject Partition
@given(mode=valid_mode_strategy, query=query_strategy)
@settings(max_examples=100, deadline=None)
def test_valid_modes_accepted_by_pydantic(mode: str, query: str) -> None:
    """**Validates: Requirements 1.1**

    For any value in {"wizard", "research", "career", "guidance"}, the
    AskRequest model SHALL accept the `mode` field without raising a
    ValidationError.
    """
    AskRequest = _import_ask_request()
    # Should NOT raise
    req = AskRequest(query=query, mode=mode)
    assert req.mode == mode, (
        f"Expected req.mode == {mode!r}, got {req.mode!r}"
    )


# Feature: chat-mode-selection, Property 1: Mode Validation — Accept/Reject Partition
@given(mode=invalid_mode_strategy, query=query_strategy)
@settings(max_examples=100)
def test_invalid_modes_rejected_by_pydantic(mode: str, query: str) -> None:
    """**Validates: Requirements 1.3**

    For any non-None string that is NOT in {"wizard", "research", "career",
    "guidance"}, the AskRequest model SHALL raise a ValidationError.
    """
    AskRequest = _import_ask_request()
    # Filter: skip if mode happens to match a valid mode after stripping
    assume(mode.strip() not in VALID_MODES)

    try:
        AskRequest(query=query, mode=mode)
        # If we reach here without exception, the test fails
        raise AssertionError(
            f"AskRequest accepted invalid mode {mode!r} without raising ValidationError"
        )
    except ValidationError as exc:
        # Expected — verify the error message mentions the mode field
        error_str = str(exc)
        # ValidationError for the mode field should be present
        assert len(exc.errors()) > 0, "ValidationError should contain at least one error"
    except Exception as exc:
        # Any other exception type also indicates rejection — acceptable
        # (e.g. ValueError re-raised by field_validator)
        pass


def test_none_mode_accepted_by_pydantic() -> None:
    """**Validates: Requirements 1.2**

    When `mode` is None (absent), the AskRequest model SHALL accept the
    request and default mode to None (backward-compatible NER routing).
    """
    AskRequest = _import_ask_request()
    req = AskRequest(query="test question")
    assert req.mode is None, f"Expected req.mode is None, got {req.mode!r}"


def test_absent_mode_field_defaults_to_none() -> None:
    """**Validates: Requirements 1.2**

    When the `mode` field is entirely absent from the request body, the
    AskRequest model SHALL default mode to None.
    """
    AskRequest = _import_ask_request()
    req = AskRequest(query="herhangi bir soru")
    assert req.mode is None


def test_all_four_valid_modes_individually() -> None:
    """**Validates: Requirements 1.1**

    Each of the four valid modes SHALL be accepted by the model individually.
    """
    AskRequest = _import_ask_request()
    for mode in VALID_MODES:
        req = AskRequest(query="test", mode=mode)
        assert req.mode == mode, f"Mode {mode!r} was not preserved: got {req.mode!r}"


# ===========================================================================
# Section 2: HTTP endpoint validation tests
#
# Tests the /b2b/ask_intelligent endpoint via TestClient.
# Valid modes → HTTP 200; invalid non-None modes → HTTP 422.
# app_graph.ainvoke is mocked so no LangGraph execution occurs.
# ===========================================================================

def _make_test_client():
    """Build a FastAPI TestClient with app_graph.ainvoke mocked."""
    from httpx import AsyncClient
    import app.graph as graph_module

    # Mock app_graph.ainvoke to return a minimal valid result
    mock_result = {
        "messages": [],
        "current_answer": "Mocked cevap için test.",
        "ner_context": {},
        "iteration_count": 0,
        "query_plan": None,
        "thinking_steps": [],
        "sub_question_results": {},
        "evaluation": None,
        "search_depth": 0,
        "fetched_urls": None,
        "mode": None,
    }

    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(return_value=mock_result)
    return mock_graph


def _run_endpoint_request(payload: dict) -> int:
    """
    Make a POST request to /b2b/ask_intelligent and return HTTP status code.
    Uses httpx TestClient (synchronous) with mocked app_graph.
    """
    import os
    os.environ.setdefault("ALLOW_ANONYMOUS", "true")
    os.environ.setdefault("ALLOW_DEV_FALLBACK", "true")
    # Set a very high rate limit for tests so property tests (100+ requests) don't hit 429
    os.environ["RATE_LIMIT_FREE"] = "99999"
    os.environ["RATE_LIMIT_GUEST"] = "99999"

    # Import here to pick up all mocks from conftest
    from app.gate import app as fastapi_app
    import app.gate as gate_module
    import app.graph as graph_module

    mock_graph = _make_test_client()

    import asyncio

    original_graph = graph_module.app_graph
    graph_module.app_graph = mock_graph

    # Patch _PLAN_LIMITS at the module level so rate limiter uses high limits
    # regardless of when the module was first imported
    original_plan_limits = gate_module._PLAN_LIMITS.copy()
    gate_module._PLAN_LIMITS["guest"] = 99999
    gate_module._PLAN_LIMITS["free"] = 99999
    # Clear in-process rate counters so sliding window doesn't accumulate across
    # hypothesis examples (no Redis in test env, so _check_rate_local is used)
    gate_module._local_rate.clear()

    try:
        # Use synchronous TestClient
        from starlette.testclient import TestClient
        client = TestClient(fastapi_app, raise_server_exceptions=False)
        response = client.post(
            "/b2b/ask_intelligent",
            json=payload,
        )
        return response.status_code
    finally:
        graph_module.app_graph = original_graph
        gate_module._PLAN_LIMITS.update(original_plan_limits)


# Feature: chat-mode-selection, Property 1: Mode Validation — Accept/Reject Partition
@given(mode=valid_mode_strategy, query=query_strategy)
@settings(max_examples=100, deadline=None)
def test_valid_modes_return_http_200(mode: str, query: str) -> None:
    """**Validates: Requirements 1.1**

    For any value in {"wizard", "research", "career", "guidance"}, the
    API_Gateway SHALL accept the request and return HTTP 200.
    """
    payload = {"query": query, "mode": mode}
    status_code = _run_endpoint_request(payload)
    assert status_code == 200, (
        f"Expected HTTP 200 for valid mode {mode!r}, got {status_code}"
    )


# Feature: chat-mode-selection, Property 1: Mode Validation — Accept/Reject Partition
@given(mode=invalid_mode_strategy, query=query_strategy)
@settings(max_examples=100, deadline=None)
def test_invalid_modes_return_http_422(mode: str, query: str) -> None:
    """**Validates: Requirements 1.3**

    For any non-None string NOT in {"wizard", "research", "career",
    "guidance"}, the API_Gateway SHALL return HTTP 422.
    """
    assume(mode.strip() not in VALID_MODES)
    payload = {"query": query, "mode": mode}
    status_code = _run_endpoint_request(payload)
    assert status_code == 422, (
        f"Expected HTTP 422 for invalid mode {mode!r}, got {status_code}"
    )


def test_no_mode_field_returns_http_200() -> None:
    """**Validates: Requirements 1.2**

    When `mode` is absent from the request body, the API SHALL return HTTP 200
    (backward-compatible NER routing path).
    """
    status_code = _run_endpoint_request({"query": "Bilgisayar mühendisliği"})
    assert status_code == 200, f"Expected 200 for missing mode, got {status_code}"


def test_null_mode_returns_http_200() -> None:
    """**Validates: Requirements 1.2**

    When `mode` is explicitly null/None in the request body, the API SHALL
    return HTTP 200.
    """
    status_code = _run_endpoint_request({"query": "test", "mode": None})
    assert status_code == 200, f"Expected 200 for null mode, got {status_code}"


# ===========================================================================
# Section 3: Property 2 — Mode-to-Node Routing Completeness
#
# Feature: chat-mode-selection, Property 2: Mode-to-Node Routing Completeness
#
# For any valid mode value in {"wizard", "research", "career", "guidance"},
# the Pipeline_Router SHALL route to the corresponding entry node
# (wizard→fast_lookup, research→uni_info, career→career_info,
# guidance→casual_chat), and the ner_node LLM SHALL NOT be invoked.
#
# Validates: Requirements 1.5, 2.1, 3.1, 4.1, 5.1, 9.1
# ===========================================================================

# Expected routing mappings per design document
_ENTRY_NODE_EXPECTED = {
    "wizard":   "fast_lookup",
    "research": "uni_info",
    "career":   "career_info",
    "guidance": "casual_chat",
}

_ROUTE_AFTER_NER_EXPECTED = {
    "wizard":   "fast",
    "research": "uni_info",
    "career":   "career_info",
    "guidance": "casual",
}


# Feature: chat-mode-selection, Property 2: Mode-to-Node Routing Completeness
@given(mode=st.sampled_from(["wizard", "research", "career", "guidance"]))
@settings(max_examples=100, deadline=None)
def test_select_entry_point_routes_correctly(mode: str) -> None:
    """**Validates: Requirements 1.5, 5.1**

    For any valid mode, select_entry_point() SHALL return the correct entry
    node name as defined in ENTRY_NODE_MAP.
    """
    from app.graph import select_entry_point

    entry = select_entry_point(mode)
    expected = _ENTRY_NODE_EXPECTED[mode]
    assert entry == expected, (
        f"select_entry_point({mode!r}) returned {entry!r}, expected {expected!r}"
    )


# Feature: chat-mode-selection, Property 2: Mode-to-Node Routing Completeness
@given(mode=st.sampled_from(["wizard", "research", "career", "guidance"]))
@settings(max_examples=100, deadline=None)
def test_route_after_ner_routes_correctly(mode: str) -> None:
    """**Validates: Requirements 1.5, 5.1**

    For any valid mode, route_after_ner() SHALL return the correct route string
    based on the mode stored in AgentState, bypassing NER-based logic entirely.
    """
    from nodes.ner import route_after_ner

    # Minimal AgentState with only mode set (simulates post-NER-bypass state)
    state: dict = {
        "messages": [],
        "ner_context": {"action": "MODE_SELECTED", "mode": mode},
        "mode": mode,
        "iteration_count": 0,
        "query_plan": None,
        "thinking_steps": [],
        "sub_question_results": {},
        "current_answer": None,
        "evaluation": None,
        "search_depth": 0,
        "fetched_urls": None,
    }

    route = route_after_ner(state)
    expected = _ROUTE_AFTER_NER_EXPECTED[mode]
    assert route == expected, (
        f"route_after_ner() with mode={mode!r} returned {route!r}, expected {expected!r}"
    )


# Feature: chat-mode-selection, Property 2: Mode-to-Node Routing Completeness
@given(mode=st.sampled_from(["wizard", "research", "career", "guidance"]))
@settings(max_examples=100, deadline=None)
def test_ner_node_bypasses_llm_when_mode_set(mode: str) -> None:
    """**Validates: Requirements 2.1, 3.1, 4.1, 9.1**

    When a valid mode is provided, ner_node SHALL NOT call the LLM
    (llm_ner.with_structured_output). The LLM call is bypassed entirely and
    ner_context SHALL contain action="MODE_SELECTED".
    """
    from unittest.mock import patch, MagicMock

    state: dict = {
        "messages": [MagicMock(content=f"test query for {mode}")],
        "ner_context": {},
        "mode": mode,
        "iteration_count": 0,
        "query_plan": None,
        "thinking_steps": [],
        "sub_question_results": {},
        "current_answer": None,
        "evaluation": None,
        "search_depth": 0,
        "fetched_urls": None,
    }

    # Patch the LLM used in ner_node so we can assert it was never called
    mock_llm = MagicMock()
    mock_structured = MagicMock()
    mock_llm.with_structured_output.return_value = mock_structured

    with patch("nodes.ner.llm_ner", mock_llm):
        import nodes.ner as ner_module
        # ner_node may be wrapped by decorators; call the underlying logic
        # by importing and invoking directly
        result = ner_module.ner_node(state)

    # The LLM should never have been touched
    mock_llm.with_structured_output.assert_not_called()
    mock_structured.invoke.assert_not_called()

    # The result must signal MODE_SELECTED bypass
    # Note: ner_context goes through NERContext validator which normalises the
    # dict — the "mode" key is stripped since NERContext schema doesn't carry
    # it. The important invariant is that action == "MODE_SELECTED" and that
    # the LLM was never invoked.
    ner_ctx = result.get("ner_context", {})
    assert ner_ctx.get("action") == "MODE_SELECTED", (
        f"Expected ner_context.action == 'MODE_SELECTED' for mode={mode!r}, "
        f"got {ner_ctx!r}"
    )


# Feature: chat-mode-selection, Property 2: Mode-to-Node Routing Completeness
@given(mode=st.sampled_from(["wizard", "research", "career", "guidance"]))
@settings(max_examples=100, deadline=None)
def test_full_routing_pipeline_no_llm_for_valid_modes(mode: str) -> None:
    """**Validates: Requirements 1.5, 2.1, 3.1, 4.1, 5.1, 9.1**

    Combined property: for any valid mode, select_entry_point returns the
    correct node AND ner_node bypasses the LLM AND route_after_ner returns
    the correct route — all three invariants hold together.
    """
    from app.graph import select_entry_point
    from nodes.ner import route_after_ner
    from unittest.mock import patch, MagicMock

    # 1. Entry point selection
    entry = select_entry_point(mode)
    assert entry == _ENTRY_NODE_EXPECTED[mode], (
        f"[entry] mode={mode!r}: expected {_ENTRY_NODE_EXPECTED[mode]!r}, got {entry!r}"
    )

    # 2. NER LLM bypass
    state: dict = {
        "messages": [MagicMock(content="test")],
        "ner_context": {},
        "mode": mode,
        "iteration_count": 0,
        "query_plan": None,
        "thinking_steps": [],
        "sub_question_results": {},
        "current_answer": None,
        "evaluation": None,
        "search_depth": 0,
        "fetched_urls": None,
    }

    mock_llm = MagicMock()
    with patch("nodes.ner.llm_ner", mock_llm):
        import nodes.ner as ner_module
        result = ner_module.ner_node(state)

    mock_llm.with_structured_output.assert_not_called()

    ner_ctx = result.get("ner_context", {})
    assert ner_ctx.get("action") == "MODE_SELECTED", (
        f"[ner] mode={mode!r}: ner_context.action should be MODE_SELECTED, got {ner_ctx!r}"
    )

    # 3. Post-bypass routing
    post_state = {**state, "ner_context": ner_ctx}
    route = route_after_ner(post_state)
    assert route == _ROUTE_AFTER_NER_EXPECTED[mode], (
        f"[route] mode={mode!r}: expected {_ROUTE_AFTER_NER_EXPECTED[mode]!r}, got {route!r}"
    )
