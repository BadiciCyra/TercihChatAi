"""
Bug Condition Exploration Test — casual-chat-llm-routing
=========================================================
**Property 1: Bug Condition** - Static Response Instead of LLM Call

CRITICAL: This test MUST FAIL on unfixed code — failure confirms the bug exists.
DO NOT fix the code or modify this test to make it pass.

The test encodes the EXPECTED (correct) behavior:
  - llm_responder.ainvoke is called exactly once per invocation
  - _pick_casual_response is NOT called
  - The returned AIMessage.content equals the sentinel ("MOCK_LLM_RESPONSE")

On the UNFIXED code these assertions will fail because:
  - casual_chat_node never calls ainvoke
  - casual_chat_node always calls _pick_casual_response
  - The returned content is a string from _CASUAL_RESPONSES, not the sentinel

**Validates: Requirements 1.1, 1.2, 1.3**
"""

from __future__ import annotations

import asyncio
import importlib
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Path setup — ensure ai/ and project root are on sys.path
# ---------------------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parents[2]
AI_DIR = ROOT_DIR / "ai"
for _p in (str(ROOT_DIR), str(AI_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ---------------------------------------------------------------------------
# Stub heavy transitive deps BEFORE importing the node under test.
# casual_chat.py (unfixed) imports:
#   - langchain_core.messages   (already stubbed by conftest.py)
#   - state
#   - utils.validators
#   - utils.decorators
#   - prompts.casual
# It does NOT import models/llm_responder (that's the bug — the fixed version will).
# We stub 'models' so that if/when the fix adds it, no real network calls happen.
# ---------------------------------------------------------------------------

# Ensure 'models' module is available as a lightweight stub
if "models" not in sys.modules:
    _models_stub = types.ModuleType("models")
    _mock_llm = MagicMock()
    _mock_llm.ainvoke = AsyncMock(return_value=None)
    _models_stub.llm_responder = _mock_llm
    sys.modules["models"] = _models_stub

# Prevent nodes/__init__.py from being executed (it imports all other nodes which
# pull in the full models/tools chain). Import the module file directly.
if "nodes" not in sys.modules:
    # Register a package stub so sub-module import works cleanly
    _nodes_pkg = types.ModuleType("nodes")
    _nodes_pkg.__path__ = [str(AI_DIR / "nodes")]
    _nodes_pkg.__package__ = "nodes"
    sys.modules["nodes"] = _nodes_pkg

# ---------------------------------------------------------------------------
# Now import the module under test directly
# ---------------------------------------------------------------------------
import importlib.util as _ilu
_cc_spec = _ilu.spec_from_file_location(
    "nodes.casual_chat",
    str(AI_DIR / "nodes" / "casual_chat.py"),
)
_casual_chat_module = _ilu.module_from_spec(_cc_spec)
sys.modules["nodes.casual_chat"] = _casual_chat_module
_cc_spec.loader.exec_module(_casual_chat_module)

casual_chat_node = _casual_chat_module.casual_chat_node

# ---------------------------------------------------------------------------
# Hypothesis imports
# ---------------------------------------------------------------------------
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# langchain_core.messages — use the versions already in sys.modules (conftest stubs)
# ---------------------------------------------------------------------------
from langchain_core.messages import AIMessage, HumanMessage
from prompts.casual import _CASUAL_RESPONSES

# All static responses as a flat set for fast membership checks
_ALL_STATIC_RESPONSES: frozenset[str] = frozenset(
    resp
    for responses in _CASUAL_RESPONSES.values()
    for resp in responses
)

SENTINEL = "MOCK_LLM_RESPONSE"


def _build_state(user_text: str) -> dict:
    """Build a minimal AgentState dict with a single HumanMessage."""
    return {
        "messages": [HumanMessage(content=user_text)],
        "iteration_count": 0,
        "ner_context": {},
    }


def _run_node(state: dict) -> dict:
    """
    Run casual_chat_node safely — handles both sync (unfixed) and async (fixed) cases.
    """
    import inspect
    if inspect.iscoroutinefunction(casual_chat_node):
        return asyncio.run(casual_chat_node(state))
    return casual_chat_node(state)


# ---------------------------------------------------------------------------
# Property 1: Bug Condition — any non-empty user message must invoke the LLM
# ---------------------------------------------------------------------------

@given(user_text=st.text(min_size=1, max_size=500))
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
def test_casual_chat_calls_llm_not_static_pool(user_text: str) -> None:
    """**Validates: Requirements 1.1, 1.2, 1.3**

    For any non-empty user message:
      1. llm_responder.ainvoke MUST be called exactly once.
      2. _pick_casual_response MUST NOT be called.
      3. The returned AIMessage.content MUST equal the sentinel value ("MOCK_LLM_RESPONSE"),
         not any string from _CASUAL_RESPONSES.

    EXPECTED OUTCOME on unfixed code: FAIL
      - ainvoke is never called (call_count == 0)
      - _pick_casual_response is always called (call_count >= 1)
      - returned content is a static template from _CASUAL_RESPONSES, not SENTINEL
    """
    sentinel_ai_msg = AIMessage(content=SENTINEL)
    mock_ainvoke = AsyncMock(return_value=sentinel_ai_msg)

    # Patch llm_responder on the module (it doesn't exist on unfixed code, create=True handles that)
    mock_llm = MagicMock()
    mock_llm.ainvoke = mock_ainvoke

    # Patch _pick_casual_response on the module to track calls
    original_pick = getattr(_casual_chat_module, "_pick_casual_response", None)
    call_tracker = MagicMock(wraps=original_pick) if original_pick is not None else MagicMock(return_value="TRACKED_STATIC")

    with (
        patch.object(_casual_chat_module, "llm_responder", mock_llm, create=True),
        patch.object(_casual_chat_module, "_pick_casual_response", call_tracker, create=True),
    ):
        state = _build_state(user_text)
        result = _run_node(state)

    # --- Assertion 1: llm_responder.ainvoke called exactly once ---
    assert mock_ainvoke.call_count == 1, (
        f"[BUG DETECTED] llm_responder.ainvoke was NOT called.\n"
        f"  call_count={mock_ainvoke.call_count} (expected 1)\n"
        f"  user_text={user_text!r}\n"
        f"  Bug: casual_chat_node does NOT call ainvoke — it takes the static path."
    )

    # --- Assertion 2: _pick_casual_response was NOT called ---
    assert call_tracker.call_count == 0, (
        f"[BUG DETECTED] _pick_casual_response WAS called.\n"
        f"  call_count={call_tracker.call_count} (expected 0)\n"
        f"  user_text={user_text!r}\n"
        f"  Bug: casual_chat_node uses the static response pool instead of LLM."
    )

    # --- Assertion 3: returned content equals the sentinel, not a static string ---
    returned_messages = result.get("messages", [])
    assert len(returned_messages) >= 1, (
        f"Expected at least one message in result, got {returned_messages!r}"
    )
    returned_content = returned_messages[0].content
    assert returned_content == SENTINEL, (
        f"[BUG DETECTED] Returned content is NOT the LLM sentinel.\n"
        f"  returned={returned_content!r}\n"
        f"  expected={SENTINEL!r}\n"
        f"  user_text={user_text!r}\n"
        f"  Bug: static template returned instead of LLM-generated response."
    )
    assert returned_content not in _ALL_STATIC_RESPONSES, (
        f"[BUG DETECTED] Returned content is from _CASUAL_RESPONSES static pool.\n"
        f"  returned={returned_content!r}\n"
        f"  user_text={user_text!r}\n"
        f"  Bug: static path taken instead of LLM call."
    )
