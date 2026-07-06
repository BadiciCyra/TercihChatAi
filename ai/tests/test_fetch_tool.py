"""
Tests for fetch_link_content tool — Tasks 4.2 and 4.3.

Task 4.2: Unit tests for empty/None URL handling
Task 4.3: Property test for fetch context format (Property 12)
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# ---------------------------------------------------------------------------
# Path setup — ensure ai/ and project root are importable
# ---------------------------------------------------------------------------
_TESTS_DIR = Path(__file__).resolve().parent
_AI_DIR = _TESTS_DIR.parent
_ROOT_DIR = _AI_DIR.parent
for _p in (str(_ROOT_DIR), str(_AI_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ---------------------------------------------------------------------------
# Patch langchain_core.tools with a real pass-through @tool decorator
# BEFORE importing nodes.tools so the decorator works at import time.
# ---------------------------------------------------------------------------
_lc_tools = sys.modules.get("langchain_core.tools")
if _lc_tools is None:
    _lc_tools = types.ModuleType("langchain_core.tools")
    sys.modules["langchain_core.tools"] = _lc_tools

# Provide a @tool decorator that simply returns the underlying coroutine function,
# exposing it via .ainvoke (mirrors LangChain's interface) so tests can call it.
if not hasattr(_lc_tools, "tool"):
    def _tool_decorator(func=None, **kwargs):
        """Minimal @tool shim: wraps an async function so it can be ainvoked."""
        def _wrap(fn):
            async def _ainvoke(input_dict):
                return await fn(**input_dict)
            fn.ainvoke = _ainvoke
            fn.invoke = lambda d: asyncio.get_event_loop().run_until_complete(fn(**d))
            return fn
        if func is not None:
            return _wrap(func)
        return _wrap
    _lc_tools.tool = _tool_decorator

# Now it is safe to import nodes.tools
from utils.link_fetcher import FetchResult  # noqa: E402
import importlib  # noqa: E402

# Import nodes.tools fresh (re-use cached if already loaded)
_nodes_tools = sys.modules.get("nodes.tools")
if _nodes_tools is None:
    import nodes.tools as _nodes_tools  # type: ignore
    sys.modules["nodes.tools"] = _nodes_tools

fetch_link_content = _nodes_tools.fetch_link_content


# ===========================================================================
# Task 4.2 — Unit tests: empty / None URL handling
# ===========================================================================

class TestEmptyAndNoneUrlHandling:
    """fetch_link_content must return a Turkish error string for empty/None URLs."""

    def test_empty_url_returns_error_string(self):
        """Passing url='' must return 'Geçersiz URL: boş değer' without raising."""
        result = asyncio.run(fetch_link_content.ainvoke({"url": ""}))
        assert result == "Geçersiz URL: boş değer", (
            f"Expected 'Geçersiz URL: boş değer' but got: {result!r}"
        )

    def test_none_url_returns_error_string(self):
        """Passing url=None must return 'Geçersiz URL: boş değer' without raising."""
        result = asyncio.run(fetch_link_content.ainvoke({"url": None}))
        assert result == "Geçersiz URL: boş değer", (
            f"Expected 'Geçersiz URL: boş değer' but got: {result!r}"
        )


# ===========================================================================
# Task 4.3 — Property 12: Fetch context format
# Feature: web-search-link-fetcher, Property 12: Fetch context format
# Validates: Requirements 4.6
# ===========================================================================

from hypothesis import given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402


@given(
    url=st.text(min_size=5),
    content=st.text(min_size=100, max_size=3000),
)
@settings(max_examples=100)
def test_fetch_context_format(url: str, content: str):
    """
    # Feature: web-search-link-fetcher, Property 12: Fetch context format

    For any successful FetchResult with `url` and `content`, the assembled
    context string returned by fetch_link_content SHALL match the pattern
    `[Kaynak: {url}]\\n{content}`.

    **Validates: Requirements 4.6**
    """
    mock_result = FetchResult(
        url=url,
        success=True,
        content=content,
        error=None,
        fetch_duration_ms=5,
    )

    with patch("tools.fetch_url_content", new=AsyncMock(return_value=mock_result)):
        result = asyncio.run(fetch_link_content.ainvoke({"url": url}))

    expected = f"[Kaynak: {url}]\n{content}"
    assert result == expected, (
        f"Format mismatch.\n"
        f"  url={url!r}, content_len={len(content)}\n"
        f"  expected={expected[:120]!r}\n"
        f"  got     ={result[:120]!r}"
    )
