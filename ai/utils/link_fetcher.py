"""
Link Fetcher Utility
====================
Fetches full page content from URLs found in web search results.
Designed for graceful degradation: always returns a FetchResult, never raises.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import aiohttp
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

BLOCKED_DOMAINS: frozenset[str] = frozenset(
    {
        "instagram.com",
        "facebook.com",
        "twitter.com",
        "tiktok.com",
        "youtube.com",
        "pinterest.com",
    }
)

DEFAULT_TIMEOUT: int = 10          # seconds
DEFAULT_MAX_CHARS: int = 3000      # maximum characters in FetchResult.content
MIN_CONTENT_CHARS: int = 100       # minimum usable content length
USER_AGENT: str = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
)


# ---------------------------------------------------------------------------
# FetchResult dataclass
# ---------------------------------------------------------------------------


@dataclass
class FetchResult:
    """Result of a single URL fetch attempt.

    Attributes:
        url:               The URL that was fetched.
        success:           True iff usable content was retrieved.
        content:           Parsed plain text, max 3000 chars; empty string on failure.
        error:             Error code / message; None on success.
        fetch_duration_ms: Wall-clock duration in milliseconds (always set).
    """

    url: str
    success: bool
    content: str
    error: Optional[str]
    fetch_duration_ms: int


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_blocked_domain(url: str) -> bool:
    """Return True if the URL's hostname matches a blocked domain."""
    try:
        hostname = urlparse(url).hostname or ""
    except Exception:
        return False
    # Match both bare domain and subdomains (e.g. www.instagram.com)
    for blocked in BLOCKED_DOMAINS:
        if hostname == blocked or hostname.endswith("." + blocked):
            return True
    return False


def _parse_html(html: str) -> str:
    """Strip disallowed tags and extract plain text from raw HTML.

    Removes <script>, <style>, and <nav> elements before extraction.
    Returns text truncated to DEFAULT_MAX_CHARS.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["script", "style", "nav"]):
        tag.decompose()
    text = soup.get_text(separator=" ")
    # Collapse excessive whitespace
    text = " ".join(text.split())
    return text[:DEFAULT_MAX_CHARS]


# ---------------------------------------------------------------------------
# Main fetch function
# ---------------------------------------------------------------------------


async def fetch_url_content(
    url: str,
    timeout_seconds: int = DEFAULT_TIMEOUT,
) -> FetchResult:
    """Fetch and extract plain text content from *url*.

    Contract:
    - Never raises; always returns a FetchResult.
    - Checks the domain blocklist before making any HTTP request.
    - Strips <script>, <style>, <nav> tags before text extraction.
    - Truncates content to DEFAULT_MAX_CHARS (3000) characters.
    - Returns success=False when extracted text is shorter than MIN_CONTENT_CHARS.
    - Records fetch_duration_ms in every code path.

    Args:
        url:             The target URL.
        timeout_seconds: Maximum seconds to wait for the HTTP response.

    Returns:
        FetchResult with appropriate fields populated.
    """
    logger.info("[LINK_FETCHER] 🌐 Fetching: %s", url)
    start_ms = time.monotonic() * 1000

    # --- Domain blocklist check (no HTTP call) ---
    if _is_blocked_domain(url):
        duration = int(time.monotonic() * 1000 - start_ms)
        logger.warning("[LINK_FETCHER] ❌ %s — blocked_domain", url)
        return FetchResult(
            url=url,
            success=False,
            content="",
            error="blocked_domain",
            fetch_duration_ms=duration,
        )

    try:
        headers = {"User-Agent": USER_AGENT}
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)

        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
            response = await asyncio.wait_for(
                session.get(url),
                timeout=timeout_seconds,
            )
            async with response:
                status = response.status

                # --- Non-200 status ---
                if status != 200:
                    duration = int(time.monotonic() * 1000 - start_ms)
                    error_code = f"http_{status}"
                    logger.warning("[LINK_FETCHER] ❌ %s — %s", url, error_code)
                    return FetchResult(
                        url=url,
                        success=False,
                        content="",
                        error=error_code,
                        fetch_duration_ms=duration,
                    )

                # --- Read body and parse HTML ---
                html = await response.text(errors="replace")

    except asyncio.TimeoutError:
        duration = int(time.monotonic() * 1000 - start_ms)
        logger.warning("[LINK_FETCHER] ❌ %s — timeout", url)
        return FetchResult(
            url=url,
            success=False,
            content="",
            error="timeout",
            fetch_duration_ms=duration,
        )
    except Exception as exc:  # noqa: BLE001
        duration = int(time.monotonic() * 1000 - start_ms)
        error_msg = str(exc)[:120]
        logger.exception("[LINK_FETCHER] ❌ %s — %s", url, error_msg)
        return FetchResult(
            url=url,
            success=False,
            content="",
            error=error_msg,
            fetch_duration_ms=duration,
        )

    # --- HTML parsing pipeline ---
    try:
        content = _parse_html(html)
    except Exception as exc:  # noqa: BLE001
        duration = int(time.monotonic() * 1000 - start_ms)
        error_msg = str(exc)[:120]
        logger.exception("[LINK_FETCHER] ❌ %s — parse error: %s", url, error_msg)
        return FetchResult(
            url=url,
            success=False,
            content="",
            error=error_msg,
            fetch_duration_ms=duration,
        )

    duration = int(time.monotonic() * 1000 - start_ms)

    # --- Content too short check ---
    if len(content) < MIN_CONTENT_CHARS:
        logger.warning("[LINK_FETCHER] ❌ %s — content_too_short (%d chars)", url, len(content))
        return FetchResult(
            url=url,
            success=False,
            content="",
            error="content_too_short",
            fetch_duration_ms=duration,
        )

    logger.info(
        "[LINK_FETCHER] ✅ %s — %d chars, %dms",
        url,
        len(content),
        duration,
    )
    return FetchResult(
        url=url,
        success=True,
        content=content,
        error=None,
        fetch_duration_ms=duration,
    )
