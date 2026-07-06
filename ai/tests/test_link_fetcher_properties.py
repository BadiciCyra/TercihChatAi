"""
Property-based tests for ai/utils/link_fetcher.py
==================================================
Uses Hypothesis to verify correctness properties across many generated inputs.
Each test is tagged with its corresponding property from the design document.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Path setup — ensure ai/ is importable
# ---------------------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parents[2]
AI_DIR = ROOT_DIR / "ai"
for _p in (str(ROOT_DIR), str(AI_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from hypothesis import given, settings
from hypothesis import strategies as st

from utils.link_fetcher import fetch_url_content


# ---------------------------------------------------------------------------
# Helper — build a mock aiohttp.ClientSession that returns HTTP 200 with
# a given HTML body string.
# ---------------------------------------------------------------------------

def _make_mock_session(html_body: str, status: int = 200):
    """Return a mock aiohttp.ClientSession context-manager for a fixed response."""
    mock_response = AsyncMock()
    mock_response.status = status
    mock_response.text = AsyncMock(return_value=html_body)
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_get = AsyncMock(return_value=mock_response)

    mock_session = MagicMock()
    mock_session.get = mock_get
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    return mock_session


# ---------------------------------------------------------------------------
# Property 6: Short content invalidation
# Feature: web-search-link-fetcher, Property 6: Short content invalidation
# ---------------------------------------------------------------------------

# Feature: web-search-link-fetcher, Property 6: Short content invalidation
@given(short_text=st.text(max_size=99))
@settings(max_examples=100)
def test_short_content_fails(short_text: str) -> None:
    """**Validates: Requirements 6.5**

    For any HTTP 200 response whose parsed plain text contains fewer than
    100 characters, fetch_url_content SHALL return FetchResult(success=False)
    with error="content_too_short".

    Note: _parse_html collapses whitespace, so pure-whitespace text produces
    0 chars — both 0 and < 100 are covered by the content_too_short path.
    """
    html = f"<html><body>{short_text}</body></html>"
    mock_session = _make_mock_session(html, status=200)

    async def _run():
        with patch("aiohttp.ClientSession", return_value=mock_session):
            return await fetch_url_content("https://example.com")

    result = asyncio.run(_run())

    assert result.success is False
    assert result.error == "content_too_short"


# ---------------------------------------------------------------------------
# Property 1: HTML parser strips disallowed tags
# Feature: web-search-link-fetcher, Property 1: HTML parser strips disallowed tags
# ---------------------------------------------------------------------------

import uuid  # noqa: E402

from utils.link_fetcher import _parse_html  # noqa: E402


# Feature: web-search-link-fetcher, Property 1: HTML parser strips disallowed tags
@given(
    body=st.text(),
    scripts=st.lists(st.text(max_size=50), max_size=5),
    styles=st.lists(st.text(max_size=50), max_size=5),
)
@settings(max_examples=100)
def test_html_parser_strips_disallowed_tags(body: str, scripts: list, styles: list) -> None:
    """**Validates: Requirements 1.3**

    For any HTML string containing <script>, <style>, or <nav> elements with
    arbitrary content, the text extracted by _parse_html SHALL contain none of
    those tag names as substrings, and unique sentinel-tagged content placed
    exclusively inside those elements SHALL not appear in the output.
    """
    # Use unique sentinels so we can unambiguously detect leakage.
    # Each script/style entry gets a UUID prefix that cannot appear in body.
    script_sentinels = [f"SCRIPT_SENTINEL_{uuid.uuid4().hex}" for _ in scripts]
    style_sentinels = [f"STYLE_SENTINEL_{uuid.uuid4().hex}" for _ in styles]
    nav_sentinel = f"NAV_SENTINEL_{uuid.uuid4().hex}"

    html = (
        f"<html><body>{body}"
        + "".join(
            f"<script>{sentinel}{s}</script>"
            for sentinel, s in zip(script_sentinels, scripts)
        )
        + "".join(
            f"<style>{sentinel}{s}</style>"
            for sentinel, s in zip(style_sentinels, styles)
        )
        + f"<nav>{nav_sentinel}</nav>"
        + "</body></html>"
    )

    result = _parse_html(html)

    # Tag wrappers must not appear in output
    assert "<script>" not in result
    assert "<style>" not in result
    assert "<nav>" not in result

    # Unique sentinel content placed exclusively inside stripped tags must not leak
    for sentinel in script_sentinels:
        assert sentinel not in result, (
            f"Script sentinel {sentinel!r} leaked into parsed output"
        )
    for sentinel in style_sentinels:
        assert sentinel not in result, (
            f"Style sentinel {sentinel!r} leaked into parsed output"
        )
    assert nav_sentinel not in result, (
        f"Nav sentinel {nav_sentinel!r} leaked into parsed output"
    )


# ---------------------------------------------------------------------------
# Property 2: Content length invariant
# Feature: web-search-link-fetcher, Property 2: Content length invariant
# ---------------------------------------------------------------------------

# Feature: web-search-link-fetcher, Property 2: Content length invariant
@given(html=st.text(min_size=1, max_size=20000))
@settings(max_examples=100)
def test_content_max_3000_chars(html: str) -> None:
    """**Validates: Requirements 1.4**

    For any HTTP response body, the content produced by _parse_html SHALL have
    length at most 3000 characters.
    """
    result = _parse_html(html)

    assert len(result) <= 3000, (
        f"Parsed content length {len(result)} exceeds 3000-char cap"
    )


# ---------------------------------------------------------------------------
# Property 3: Non-200 status error format
# Feature: web-search-link-fetcher, Property 3: Non-200 status error format
# ---------------------------------------------------------------------------

# Feature: web-search-link-fetcher, Property 3: Non-200 status error format
@given(status=st.integers(min_value=100, max_value=599).filter(lambda s: s != 200))
@settings(max_examples=100)
def test_non_200_error_format(status: int) -> None:
    """**Validates: Requirements 1.6**

    For any HTTP status code that is not 200, fetch_url_content SHALL return
    FetchResult(success=False, error=f"http_{status}").
    """
    # Build a mock session whose response carries the given non-200 status.
    # No body reading should occur for non-200 responses.
    mock_response = AsyncMock()
    mock_response.status = status
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_get = AsyncMock(return_value=mock_response)

    mock_session = MagicMock()
    mock_session.get = mock_get
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    async def _run():
        with patch("aiohttp.ClientSession", return_value=mock_session):
            return await fetch_url_content("https://example.com/page")

    result = asyncio.run(_run())

    assert result.success is False, (
        f"Expected success=False for HTTP {status}, got success={result.success}"
    )
    assert result.error == f"http_{status}", (
        f"Expected error='http_{status}', got error={result.error!r}"
    )


# ---------------------------------------------------------------------------
# Property 4: Exception containment
# Feature: web-search-link-fetcher, Property 4: Exception containment
# ---------------------------------------------------------------------------

# Feature: web-search-link-fetcher, Property 4: Exception containment
@given(exc_type=st.sampled_from([ConnectionError, OSError, ValueError, RuntimeError]))
@settings(max_examples=100)
def test_exception_containment(exc_type: type) -> None:
    """**Validates: Requirements 1.7, 6.1**

    For any exception type raised during an HTTP fetch, fetch_url_content SHALL
    catch it and return FetchResult(success=False) without propagating the
    exception to the caller.
    """
    # Make the session itself raise when used as a context manager (__aenter__),
    # simulating a connection-level failure before any response is received.
    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(side_effect=exc_type("simulated error"))
    mock_session.__aexit__ = AsyncMock(return_value=False)

    async def _run():
        with patch("aiohttp.ClientSession", return_value=mock_session):
            return await fetch_url_content("https://example.com/page")

    # Must not raise — any unhandled exception would propagate out of asyncio.run()
    result = asyncio.run(_run())

    assert result.success is False, (
        f"Expected success=False when {exc_type.__name__} is raised, "
        f"got success={result.success}"
    )


# ---------------------------------------------------------------------------
# Property 5: Blocked domain rejection
# Feature: web-search-link-fetcher, Property 5: Blocked domain rejection
# ---------------------------------------------------------------------------

# Feature: web-search-link-fetcher, Property 5: Blocked domain rejection
@given(
    domain=st.sampled_from([
        "instagram.com", "facebook.com", "twitter.com",
        "tiktok.com", "youtube.com", "pinterest.com",
    ]),
    path=st.text(
        alphabet=st.characters(whitelist_categories=("L", "N")),
        min_size=0,
        max_size=50,
    ),
)
@settings(max_examples=100)
def test_blocked_domain_rejection(domain: str, path: str) -> None:
    """**Validates: Requirements 1.2**

    For any URL whose hostname belongs to the blocked-domain list,
    fetch_url_content SHALL return FetchResult(success=False,
    error="blocked_domain") without making any HTTP request.
    fetch_duration_ms SHALL be a non-negative integer in every code path.
    """
    url = f"https://{domain}/{path}"

    # No mocking needed — the blocklist check happens before any HTTP call.
    result = asyncio.run(fetch_url_content(url))

    assert result.success is False, (
        f"Expected success=False for blocked domain {domain!r}, "
        f"got success={result.success}"
    )
    assert result.error == "blocked_domain", (
        f"Expected error='blocked_domain' for {domain!r}, got error={result.error!r}"
    )
    assert result.fetch_duration_ms >= 0, (
        f"Expected fetch_duration_ms >= 0, got {result.fetch_duration_ms}"
    )


# ---------------------------------------------------------------------------
# Property 16: Timing field completeness
# Feature: web-search-link-fetcher, Property 16: Timing field completeness
# ---------------------------------------------------------------------------

# Feature: web-search-link-fetcher, Property 16: Timing field completeness
@given(url=st.text(min_size=1))
@settings(max_examples=100)
def test_timing_field_completeness(url: str) -> None:
    """**Validates: Requirements 1.8**

    For any input URL, even when a connection-level exception is raised,
    fetch_url_content SHALL always populate fetch_duration_ms with a
    non-negative integer value.
    """
    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(side_effect=ConnectionError("simulated"))
    mock_session.__aexit__ = AsyncMock(return_value=False)

    async def _run():
        with patch("aiohttp.ClientSession", return_value=mock_session):
            return await fetch_url_content(url)

    result = asyncio.run(_run())

    assert result.fetch_duration_ms >= 0, (
        f"Expected fetch_duration_ms >= 0, got {result.fetch_duration_ms}"
    )
