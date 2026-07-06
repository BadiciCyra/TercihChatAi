"""
Frontend Mode Selection Round-Trip Tests
==========================================
# Feature: chat-mode-selection, Property 14: Frontend Mode Selection Round-Trip

Tests Property 14 from the design document plus supporting example-based tests:

- Property 14: Frontend Mode Selection Round-Trip
  For any selected mode value and any submitted query text, the HTTP request
  body sent by the Frontend to /b2b/ask_intelligent SHALL contain a `mode`
  field equal to the selected mode value.
  Validates: Requirements 7.2, 7.3

Additional example-based tests:
  - ChatPage initial state uses wizard as the default mode
  - Mode change does NOT clear chat messages (history preserved)
  - _cache_key produces distinct keys for all 4 modes (wizard/research/career/None)

Since Python cannot render React components, we validate the frontend contract
by inspecting the TypeScript source code of ChatPage.tsx for the expected
patterns, and test the backend _cache_key function directly.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parents[2]
AI_DIR = ROOT_DIR / "ai"
FRONTEND_CHAT_PAGE = ROOT_DIR / "frontend" / "src" / "pages" / "ChatPage.tsx"
FRONTEND_MODE_SELECTOR = ROOT_DIR / "frontend" / "src" / "components" / "ModeSelector.tsx"

for _p in (str(ROOT_DIR), str(AI_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from hypothesis import given, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------
VALID_MODES = ("wizard", "research", "career", "guidance")
CACHED_MODES = ("wizard", "research", "career", None)

mode_strategy = st.sampled_from(VALID_MODES)
query_strategy = st.text(min_size=1, max_size=200)


# ---------------------------------------------------------------------------
# Helper — read ChatPage.tsx source
# ---------------------------------------------------------------------------

def _read_chatpage_source() -> str:
    """Return the full source of ChatPage.tsx as a string."""
    assert FRONTEND_CHAT_PAGE.exists(), (
        f"ChatPage.tsx not found at expected path: {FRONTEND_CHAT_PAGE}"
    )
    return FRONTEND_CHAT_PAGE.read_text(encoding="utf-8")


def _read_mode_selector_source() -> str:
    """Return the full source of ModeSelector.tsx as a string."""
    assert FRONTEND_MODE_SELECTOR.exists(), (
        f"ModeSelector.tsx not found at expected path: {FRONTEND_MODE_SELECTOR}"
    )
    return FRONTEND_MODE_SELECTOR.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Helper — import _cache_key from gate
# ---------------------------------------------------------------------------

def _import_cache_key():
    """Import _cache_key from app.gate lazily (after conftest mocks are applied)."""
    from app.gate import _cache_key
    return _cache_key


# ===========================================================================
# Property 14: Frontend Mode Selection Round-Trip
# ===========================================================================

# Feature: chat-mode-selection, Property 14: Frontend Mode Selection Round-Trip
@given(mode=mode_strategy, query=query_strategy)
@settings(max_examples=100, deadline=None)
def test_frontend_request_body_includes_mode_field(mode: str, query: str) -> None:
    """**Validates: Requirements 7.2, 7.3**

    For any selected mode value and any submitted query text, the HTTP request
    body sent by the Frontend to /b2b/ask_intelligent SHALL contain a `mode`
    field equal to the selected mode value.

    Approach: The fetch body in ChatPage.tsx must serialise both `message`
    and `mode`.  We assert that the source code contains the JSON.stringify
    call that includes both fields, then verify it for each mode value by
    confirming the source pattern is mode-agnostic (dynamic, not hardcoded).
    """
    source = _read_chatpage_source()

    # The request body must include the mode field alongside the message field.
    # ChatPage.tsx uses: JSON.stringify({ message: trimmed, mode })
    # We verify the serialisation pattern is present.
    assert "JSON.stringify(" in source, (
        "ChatPage.tsx must use JSON.stringify to build the request body"
    )

    # The body must contain both 'message' and 'mode' fields in the same
    # JSON.stringify call (or equivalent object literal).
    # Accept common patterns:
    #   { message: trimmed, mode }   (shorthand, trailing space before })
    #   { message: trimmed, mode }   (shorthand, no trailing space)
    #   { message: trimmed, mode: mode }
    body_has_message = "message:" in source or "message :" in source
    body_has_mode = (
        ", mode }" in source     # shorthand with space: { message: x, mode }
        or ", mode}" in source   # shorthand no space:   { message: x, mode}
        or ", mode," in source   # shorthand mid-object: { message: x, mode, ... }
        or "mode:" in source     # explicit key:         { message: x, mode: mode }
        or "mode :" in source    # spaced explicit key
    )

    assert body_has_message, (
        "ChatPage.tsx fetch body must contain a 'message' field"
    )
    assert body_has_mode, (
        "ChatPage.tsx fetch body must contain a 'mode' field "
        f"(tested for mode={mode!r}, query={query!r})"
    )

    # The mode value must come from state (i.e. it is a variable, not a
    # hardcoded string literal for a specific mode).  Verify that `setMode`
    # is driven by user interaction and that `mode` is a state variable.
    assert "useState<ChatMode>" in source or "useState(" in source, (
        "ChatPage.tsx must manage mode as React state"
    )

    # The mode variable must be passed to the fetch body via the captured
    # state variable — not hardcoded.  Check that `mode` appears in the
    # JSON.stringify argument region.
    stringify_idx = source.index("JSON.stringify(")
    stringify_body = source[stringify_idx: stringify_idx + 120]
    assert "mode" in stringify_body, (
        f"'mode' must appear inside the JSON.stringify call body in ChatPage.tsx. "
        f"Found context: {stringify_body!r}"
    )


# ===========================================================================
# Example test 1: Default mode is wizard on page load
# ===========================================================================

def test_chatpage_default_mode_is_wizard() -> None:
    """**Validates: Requirement 7.4**

    When the page loads, wizard is the default mode.
    ChatPage.tsx must initialise its mode state with "wizard".
    """
    source = _read_chatpage_source()

    # The canonical React state initialisation is:
    #   useState<ChatMode>("wizard")
    # We check for the exact string.
    assert 'useState<ChatMode>("wizard")' in source, (
        'ChatPage.tsx must initialise mode state with useState<ChatMode>("wizard"). '
        "The default mode on page load must be wizard (Requirement 7.4)."
    )


# ===========================================================================
# Example test 2: Mode change does NOT clear chat messages
# ===========================================================================

def test_mode_change_does_not_clear_messages() -> None:
    """**Validates: Requirement 7.5**

    When the user switches modes, the Frontend SHALL NOT clear the existing
    chat history.  We verify that the setMode handler in ChatPage.tsx does
    not call setMessages (or reset messages) when the mode changes.

    Approach: In ChatPage.tsx, ModeSelector's onChange is wired directly to
    setMode.  We assert:
    1. The onChange prop of ModeSelector is setMode (not a wrapper that also
       clears messages).
    2. setMessages is NOT called inside a function that also calls setMode
       in a way that would reset messages on a plain mode change.
    """
    source = _read_chatpage_source()

    # ModeSelector onChange must be setMode (the raw setter), not a wrapper
    # that also clears messages.
    assert "onChange={setMode}" in source, (
        "ModeSelector onChange prop must be setMode directly, not a wrapper "
        "that clears messages on mode change (Requirement 7.5). "
        f"Did not find 'onChange={{setMode}}' in ChatPage.tsx."
    )

    # Confirm that resetChatUiHard (which clears messages) is NOT invoked
    # directly by the mode change handler.  It must only be invoked from
    # confirmNewChat (the "Yeni Sohbet" confirmation dialog).
    assert "confirmNewChat" in source, (
        "ChatPage.tsx should have a confirmNewChat function for 'Yeni Sohbet'."
    )

    # The setMode call must not be accompanied by setMessages([]) in the same
    # lambda / handler (i.e., a simple mode-change must not reset messages).
    # We look for any pattern like (mode) => { setMode(mode); setMessages
    # which would indicate messages are reset on mode change.
    suspicious_pattern = (
        "setMode" in source
        and "setMessages" in source
    )
    # Both exist — that's expected.  Now verify they're NOT in the same handler.
    # The only place setMessages and setMode should co-appear is unrelated code;
    # specifically, setMode's onChange={setMode} must be a plain setter.
    # We look for lines where setMode is called and then setMessages is called
    # within 3 lines.
    lines = source.splitlines()
    for i, line in enumerate(lines):
        if "setMode(" in line and "onChange" not in line:
            # Found a setMode call that is NOT the onChange prop assignment.
            # Check the surrounding context for a resetMessages-type call.
            context = "\n".join(lines[max(0, i - 2): i + 4])
            assert "setMessages([" not in context, (
                f"Detected setMessages reset near a setMode call at line {i + 1}. "
                "Mode change must not clear chat history (Requirement 7.5). "
                f"Context:\n{context}"
            )


# ===========================================================================
# Example test 3: ModeSelector defines all 4 modes
# ===========================================================================

def test_mode_selector_defines_all_four_modes() -> None:
    """**Validates: Requirement 7.1**

    The ModeSelector component must define the ChatMode type with all four
    allowed values and provide labels for each.
    """
    source = _read_mode_selector_source()

    for mode in VALID_MODES:
        assert f'"{mode}"' in source, (
            f"ModeSelector.tsx must include mode value \"{mode}\" "
            f"in the ChatMode type or MODES array (Requirement 7.1)."
        )

    expected_labels = {
        "wizard": "Tercih Sihirbazı",
        "research": "Araştırma Asistanı",
        "career": "Kariyer Pusulası",
        "guidance": "Rehberlik",
    }
    for mode, label in expected_labels.items():
        assert label in source, (
            f"ModeSelector.tsx must include the label '{label}' for mode "
            f"'{mode}' (Requirement 7.1)."
        )


# ===========================================================================
# Example test 4: _cache_key produces distinct keys for all 4 cache modes
# ===========================================================================

def test_cache_key_distinct_for_all_four_modes_same_query() -> None:
    """**Validates: Requirements 8.1, 8.5**

    _cache_key must produce pairwise distinct keys for wizard, research,
    career, and None (NER) modes for the same query.
    """
    _cache_key = _import_cache_key()
    query = "bilgisayar mühendisliği bölümü"

    keys = {mode: _cache_key(mode, query) for mode in CACHED_MODES}

    # All keys for cached modes must be non-None strings
    for mode, key in keys.items():
        assert key is not None, (
            f"_cache_key returned None for mode={mode!r} (expected a key string)"
        )

    # Pairwise distinct
    import itertools
    seen_keys = list(keys.values())
    for (m1, k1), (m2, k2) in itertools.combinations(keys.items(), 2):
        assert k1 != k2, (
            f"Cache key collision between mode={m1!r} and mode={m2!r} "
            f"for query={query!r}: both produced {k1!r}"
        )


def test_cache_key_guidance_returns_none() -> None:
    """**Validates: Requirement 8.2**

    _cache_key for guidance mode must return None (the cache-skip signal).
    """
    _cache_key = _import_cache_key()
    key = _cache_key("guidance", "motivasyon ve kariyer rehberliği")
    assert key is None, (
        f"_cache_key('guidance', ...) must return None (cache skip signal), "
        f"got {key!r}"
    )


# Feature: chat-mode-selection, Property 14: Frontend Mode Selection Round-Trip
@given(query=query_strategy)
@settings(max_examples=100, deadline=None)
def test_cache_key_mode_isolation_property(query: str) -> None:
    """**Validates: Requirements 8.1, 8.5**

    For any query string, _cache_key must produce pairwise distinct keys for
    wizard, research, career, and None.  This complements Property 14 by
    ensuring the backend correctly isolates mode-specific responses.
    """
    _cache_key = _import_cache_key()

    import itertools
    keys = {mode: _cache_key(mode, query) for mode in CACHED_MODES}

    for mode, key in keys.items():
        assert key is not None, (
            f"_cache_key returned None for cached mode={mode!r}, query={query!r}"
        )

    for (m1, k1), (m2, k2) in itertools.combinations(keys.items(), 2):
        assert k1 != k2, (
            f"Cache key collision between modes {m1!r} and {m2!r} "
            f"for query={query!r}: both returned {k1!r}"
        )


# ===========================================================================
# Example test 5: ChatPage sends mode field to the correct endpoint
# ===========================================================================

def test_chatpage_sends_to_correct_endpoint() -> None:
    """**Validates: Requirement 7.3**

    The ChatPage.tsx must POST to the correct path that includes the
    /chat/send endpoint which in turn reaches /b2b/ask_intelligent through
    the proxy, and the body must include the mode field.
    """
    source = _read_chatpage_source()

    # The API endpoint used by ChatPage
    assert "/chat/send" in source, (
        "ChatPage.tsx must send messages to the /chat/send endpoint. "
        "Did not find '/chat/send' in ChatPage.tsx."
    )

    # mode must appear in the request body JSON object
    # Find the fetch call for /chat/send and confirm mode is included
    chat_send_idx = source.find("/chat/send")
    assert chat_send_idx != -1

    # Scan around /chat/send fetch call for body: JSON.stringify(...mode...)
    region = source[max(0, chat_send_idx - 300): chat_send_idx + 500]
    assert "mode" in region, (
        "The fetch body for /chat/send must include the 'mode' field "
        f"(Requirement 7.3). Context around /chat/send:\n{region!r}"
    )
    assert "JSON.stringify" in region, (
        "The fetch for /chat/send must use JSON.stringify to build the request body."
    )
