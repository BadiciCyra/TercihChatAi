"""
Property-based preservation tests for ai/nodes/casual_chat.py
=============================================================
Task 2 — Property 2: Preservation
----------------------------------
These tests document the STABLE / PRESERVED behaviour of casual_chat_node:

  • The function always returns a dict with exactly "messages" and
    "iteration_count" keys.
  • result["messages"] is a list of exactly one AIMessage instance.
  • result["iteration_count"] equals 0 on a successful invocation.
  • The three decorators (@validate_node_input, @validate_node_output,
    @with_error_recovery) execute without modification regardless of input.
  • When llm_responder.ainvoke raises an exception, @with_error_recovery
    catches it and returns a fallback AIMessage dict — it does NOT re-raise.

**EXPECTED OUTCOME**: ALL tests PASS on UNFIXED code.
They encode the baseline that must remain unbroken after the fix is applied.

Validates: Requirements 3.2, 3.3, 3.4
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Path setup — ensure both the repo root and ai/ are importable
# ---------------------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parents[2]
AI_DIR = ROOT_DIR / "ai"
for _p in (str(ROOT_DIR), str(AI_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from hypothesis import given, settings
from hypothesis import strategies as st
from langchain_core.messages import AIMessage

# ---------------------------------------------------------------------------
# Import the node under test directly from its source file, bypassing
# nodes/__init__.py which would trigger heavy model/tool imports.
# ---------------------------------------------------------------------------
import importlib.util as _ilu

def _load_casual_chat_module():
    spec = _ilu.spec_from_file_location(
        "nodes.casual_chat",
        AI_DIR / "nodes" / "casual_chat.py",
    )
    mod = _ilu.module_from_spec(spec)  # type: ignore[arg-type]
    # Ensure the module's own package namespace resolves correctly
    import sys as _sys
    _sys.modules.setdefault("nodes.casual_chat", mod)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod

_casual_chat_module = _load_casual_chat_module()
casual_chat_node = _casual_chat_module.casual_chat_node


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state(user_text: str) -> dict:
    """Build a minimal AgentState dict with one HumanMessage-like object."""
    class _FakeHuman:
        def __init__(self, text: str):
            self.content = text
    return {
        "messages": [_FakeHuman(user_text)],
        "iteration_count": 0,
        "ner_context": {},
    }


def _invoke(state: dict):
    """Call casual_chat_node, handling both sync and async (post-fix) variants."""
    import inspect
    result = casual_chat_node(state)
    if inspect.isawaitable(result):
        return asyncio.run(result)  # type: ignore[arg-type]
    return result


# ---------------------------------------------------------------------------
# Property 2a: Return shape is preserved for all non-empty user messages
# Feature: casual-chat-llm-routing, Property 2: Preservation
# ---------------------------------------------------------------------------

@given(st.text(min_size=1, max_size=500))
@settings(max_examples=100)
def test_return_shape_preserved_for_all_inputs(user_text: str) -> None:
    """**Validates: Requirements 3.2, 3.4**

    For any non-empty user message the return dict MUST contain:
      • "messages" key  → a list of exactly one AIMessage instance
      • "iteration_count" key  → integer value 0

    This property verifies that the stable contract of casual_chat_node is
    maintained regardless of what the user sends.

    On the UNFIXED code, llm_responder does not exist as an import in
    casual_chat.py, so no patching of it is required here.  We do patch it
    as a no-op so that the SAME test also passes after the fix is applied
    (when ainvoke is introduced).
    """
    mock_ai_msg = AIMessage(content="MOCK_LLM_RESPONSE_FOR_PRESERVATION")

    state = _make_state(user_text)

    # Patch llm_responder.ainvoke so the test is deterministic after the fix.
    # We use patch.object on the already-loaded module to avoid triggering
    # nodes/__init__.py which has a heavy import chain.
    # On the unfixed code, llm_responder doesn't exist in the module so we
    # skip patching it — the static _pick_casual_response path is taken instead.
    if hasattr(_casual_chat_module, "llm_responder"):
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_ai_msg)
        with patch.object(_casual_chat_module, "llm_responder", mock_llm):
            result = _invoke(state)
    else:
        result = _invoke(state)

    # --- Shape assertions ---
    assert isinstance(result, dict), (
        f"casual_chat_node must return a dict, got {type(result)}"
    )
    assert "messages" in result, (
        f'"messages" key missing from result: {result!r}'
    )
    assert "iteration_count" in result, (
        f'"iteration_count" key missing from result: {result!r}'
    )

    # --- Messages list assertions ---
    msgs = result["messages"]
    assert isinstance(msgs, list), (
        f'result["messages"] must be a list, got {type(msgs)}'
    )
    assert len(msgs) == 1, (
        f'result["messages"] must contain exactly one message, got {len(msgs)}: {msgs!r}'
    )
    assert isinstance(msgs[0], AIMessage), (
        f'result["messages"][0] must be an AIMessage instance, got {type(msgs[0])}'
    )

    # --- iteration_count assertion ---
    assert result["iteration_count"] == 0, (
        f'result["iteration_count"] must be 0 on success, got {result["iteration_count"]}'
    )


# ---------------------------------------------------------------------------
# Property 2b: @with_error_recovery catches exceptions and returns fallback
#              (does NOT re-raise)
# Feature: casual-chat-llm-routing, Property 2: Preservation
# ---------------------------------------------------------------------------

def test_with_error_recovery_returns_fallback_on_exception() -> None:
    """**Validates: Requirements 3.2**

    When the underlying LLM call (or any inner code) raises an exception,
    @with_error_recovery MUST:
      1. NOT re-raise the exception to the caller.
      2. Return a dict with "messages" containing a fallback AIMessage.
      3. Return a dict with "iteration_count" that is an integer (may be > 0).

    On the unfixed code, the node is sync and never calls ainvoke, so we
    trigger the error recovery by patching _pick_casual_response to raise.
    After the fix we patch llm_responder.ainvoke to raise instead.  Both
    scenarios are covered by patching the relevant callable in the module.
    """
    state = _make_state("Bu mesaj hata yolunu test eder")

    # -----------------------------------------------------------------------
    # Path A: Unfixed code — _pick_casual_response raises RuntimeError.
    # Path B: Fixed code    — llm_responder.ainvoke raises RuntimeError.
    # We try path A first; if the attribute doesn't exist (post-fix), we
    # fall through to path B.
    # -----------------------------------------------------------------------
    _module = _casual_chat_module

    if hasattr(_module, "_pick_casual_response"):
        # Unfixed code path
        with patch.object(_module, "_pick_casual_response", side_effect=RuntimeError("simulated error")):
            result = _invoke(state)
    else:
        # Fixed code path
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(side_effect=RuntimeError("simulated error"))
        with patch.object(_module, "llm_responder", mock_llm):
            result = _invoke(state)

    # --- Must not re-raise; we already got here, so that's proven ---

    # --- Fallback shape ---
    assert isinstance(result, dict), (
        f"Fallback result must be a dict, got {type(result)}"
    )
    assert "messages" in result, (
        f'"messages" key missing from fallback result: {result!r}'
    )
    assert "iteration_count" in result, (
        f'"iteration_count" key missing from fallback result: {result!r}'
    )
    msgs = result["messages"]
    assert isinstance(msgs, list) and len(msgs) >= 1, (
        f'Fallback "messages" must be a non-empty list, got {msgs!r}'
    )
    assert isinstance(msgs[0], AIMessage), (
        f'Fallback message must be an AIMessage, got {type(msgs[0])}'
    )
    assert isinstance(msgs[0].content, str) and msgs[0].content.strip(), (
        f'Fallback AIMessage.content must be a non-empty string, got {msgs[0].content!r}'
    )
    assert isinstance(result["iteration_count"], int), (
        f'"iteration_count" must be an int in fallback, got {type(result["iteration_count"])}'
    )


# ---------------------------------------------------------------------------
# Bonus determinism check: AIMessage.content is always a non-empty string
# Feature: casual-chat-llm-routing, Property 2: Preservation
# ---------------------------------------------------------------------------

@given(st.text(min_size=1, max_size=500))
@settings(max_examples=50)
def test_messages_content_is_nonempty_string(user_text: str) -> None:
    """**Validates: Requirements 3.4**

    For any non-empty user message, the returned AIMessage.content must be a
    non-empty string. This guards against accidentally returning an empty reply.
    """
    mock_ai_msg = AIMessage(content="MOCK_LLM_RESPONSE_CONTENT_CHECK")

    state = _make_state(user_text)

    if hasattr(_casual_chat_module, "llm_responder"):
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_ai_msg)
        with patch.object(_casual_chat_module, "llm_responder", mock_llm):
            result = _invoke(state)
    else:
        result = _invoke(state)

    content = result["messages"][0].content
    assert isinstance(content, str), (
        f"AIMessage.content must be a string, got {type(content)}"
    )
    assert content.strip(), (
        f"AIMessage.content must not be empty or whitespace-only, got {content!r}"
    )
