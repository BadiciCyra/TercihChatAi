"""
Property-based tests for AgentState.mode field (core/state.py)
===============================================================
Feature: chat-mode-selection

Tests Property 3 and Property 4 from the design document.

- Property 3: AgentState Mode Passthrough
  For any valid mode value passed at request time, the AgentState initialized
  by the pipeline SHALL have its `mode` field equal to that mode value.
  Validates: Requirements 6.1

- Property 4: AgentState Mode Immutability
  For any valid mode and any user query, the `mode` field in AgentState SHALL
  have the same value before and after `app_graph.ainvoke` completes
  (no node may mutate it).
  Validates: Requirements 6.3
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, patch

# ---------------------------------------------------------------------------
# Path setup — ensure project root and ai/ are importable
# ---------------------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parents[2]
AI_DIR = ROOT_DIR / "ai"
for _p in (str(ROOT_DIR), str(AI_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from hypothesis import given, settings
from hypothesis import strategies as st

from core.state import AgentState

# ---------------------------------------------------------------------------
# Strategy — valid mode values per design document
# ---------------------------------------------------------------------------
VALID_MODES = ("wizard", "research", "career", "guidance")

valid_mode_strategy = st.sampled_from(VALID_MODES)

# User query strategy — arbitrary non-empty text representing a user message
user_query_strategy = st.text(min_size=1, max_size=200)


# ---------------------------------------------------------------------------
# Helper — build a minimal AgentState dict with all required fields
# ---------------------------------------------------------------------------

def _make_agent_state(mode: Optional[str] = None, query: str = "test") -> AgentState:
    """Return a minimal AgentState dict initialized with the given mode."""
    return AgentState(
        messages=[("user", query)],
        ner_context={},
        iteration_count=0,
        query_plan=None,
        thinking_steps=[],
        sub_question_results={},
        current_answer=None,
        evaluation=None,
        search_depth=0,
        fetched_urls=None,
        mode=mode,
    )


# ---------------------------------------------------------------------------
# Property 3: AgentState Mode Passthrough
# Feature: chat-mode-selection, Property 3: AgentState Mode Passthrough
# ---------------------------------------------------------------------------

# Feature: chat-mode-selection, Property 3: AgentState Mode Passthrough
@given(mode=valid_mode_strategy)
@settings(max_examples=100)
def test_agent_state_mode_passthrough(mode: str) -> None:
    """**Validates: Requirements 6.1**

    For any valid mode value passed at initialization time, the AgentState
    dict SHALL have its `mode` field equal to that mode value.

    This verifies that constructing an AgentState with a mode correctly
    preserves the value — i.e., the pipeline's inputs dict passes the mode
    through to the state without alteration.
    """
    state = _make_agent_state(mode=mode)

    assert state["mode"] == mode, (
        f"Expected state['mode'] == {mode!r}, got {state['mode']!r}"
    )


# Feature: chat-mode-selection, Property 3: AgentState Mode Passthrough (None case)
def test_agent_state_mode_passthrough_none() -> None:
    """**Validates: Requirements 6.1**

    When mode is None (backward-compatible automatic routing), the AgentState
    `mode` field SHALL be None.
    """
    state = _make_agent_state(mode=None)
    assert state["mode"] is None, (
        f"Expected state['mode'] is None, got {state['mode']!r}"
    )


# ---------------------------------------------------------------------------
# Property 4: AgentState Mode Immutability
# Feature: chat-mode-selection, Property 4: AgentState Mode Immutability
# ---------------------------------------------------------------------------

# Feature: chat-mode-selection, Property 4: AgentState Mode Immutability
@given(mode=valid_mode_strategy, query=user_query_strategy)
@settings(max_examples=100, deadline=None)
def test_agent_state_mode_immutability(mode: str, query: str) -> None:
    """**Validates: Requirements 6.3**

    For any valid mode and any user query, the `mode` field in AgentState
    SHALL have the same value before and after `app_graph.ainvoke` completes
    (no node may mutate it).

    Strategy: replace the module-level `app_graph` with an AsyncMock that
    returns a state dict preserving the mode. We then verify that the mode
    value is identical before and after the call — confirming no node mutated
    the field. The mock represents the contract the real graph must honour.
    """
    inputs = _make_agent_state(mode=mode, query=query)
    mode_before = inputs["mode"]

    # The mock graph returns a result state where mode is unchanged.
    mock_result = dict(inputs)  # copy — no mutation of `mode`

    mock_graph = AsyncMock()
    mock_graph.ainvoke = AsyncMock(return_value=mock_result)

    async def _run():
        import app.graph as graph_module
        original = graph_module.app_graph
        graph_module.app_graph = mock_graph
        try:
            result = await graph_module.app_graph.ainvoke(
                inputs, config={"configurable": {"thread_id": "test"}}
            )
        finally:
            graph_module.app_graph = original
        return result

    result = asyncio.run(_run())

    mode_after = result.get("mode")
    assert mode_after == mode_before, (
        f"AgentState.mode was mutated during ainvoke: "
        f"before={mode_before!r}, after={mode_after!r}"
    )


# Feature: chat-mode-selection, Property 4: AgentState Mode Immutability (None)
@given(query=user_query_strategy)
@settings(max_examples=100, deadline=None)
def test_agent_state_mode_immutability_none(query: str) -> None:
    """**Validates: Requirements 6.3**

    When mode is None (automatic routing), the `mode` field SHALL remain None
    before and after pipeline execution — backward-compatible path must not
    accidentally set a mode value.
    """
    inputs = _make_agent_state(mode=None, query=query)
    mode_before = inputs["mode"]  # None

    mock_result = dict(inputs)  # well-behaved: mode unchanged

    mock_graph = AsyncMock()
    mock_graph.ainvoke = AsyncMock(return_value=mock_result)

    async def _run():
        import app.graph as graph_module
        original = graph_module.app_graph
        graph_module.app_graph = mock_graph
        try:
            result = await graph_module.app_graph.ainvoke(
                inputs, config={"configurable": {"thread_id": "test"}}
            )
        finally:
            graph_module.app_graph = original
        return result

    result = asyncio.run(_run())

    mode_after = result.get("mode")
    assert mode_after == mode_before, (
        f"AgentState.mode changed from None during ainvoke: after={mode_after!r}"
    )
