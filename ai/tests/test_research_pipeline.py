"""
Property-based tests for Research Pipeline
===========================================
# Feature: chat-mode-selection

Tests Properties 10, 11, and 12 from the design document:

- Property 10: Research Minimum Search Queries
  For any query in research mode that contains an identifiable university
  name, uni_info_node SHALL invoke _ddg_quick at least 3 times with distinct
  query strings.
  Validates: Requirements 3.2

- Property 11: Research Social Media Exclusion
  For any list of raw research search results that includes URLs from social
  media domains, the assembled context passed to the LLM SHALL contain none
  of those social media URLs.
  Validates: Requirements 3.6

- Property 12: Research Missing University — Clarification Response
  For any query string in research mode that contains no identifiable
  university name, uni_info_node SHALL return a clarification message asking
  for the university name, without invoking web search or LLM synthesis.
  Validates: Requirements 3.4
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any
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
# Known university names to use in Property 10 strategy
# ---------------------------------------------------------------------------
KNOWN_UNIVERSITIES = [
    "İstanbul Teknik Üniversitesi",
    "Orta Doğu Teknik Üniversitesi",
    "Boğaziçi Üniversitesi",
    "Bilkent Üniversitesi",
    "Koç Üniversitesi",
    "Sabancı Üniversitesi",
    "Hacettepe Üniversitesi",
    "Ankara Üniversitesi",
    "İstanbul Üniversitesi",
    "Ege Üniversitesi",
    "Marmara Üniversitesi",
    "Gazi Üniversitesi",
    "Yıldız Teknik Üniversitesi",
    "Dokuz Eylül Üniversitesi",
    "İzmir Yüksek Teknoloji Enstitüsü",
    "Karadeniz Teknik Üniversitesi",
    "Gebze Teknik Üniversitesi",
    "Erciyes Üniversitesi",
    "Selçuk Üniversitesi",
    "Çukurova Üniversitesi",
]

# Social media domains as defined in uni_info.py
SOCIAL_MEDIA_DOMAINS = [
    "instagram.com",
    "facebook.com",
    "twitter.com",
    "tiktok.com",
    "pinterest.com",
]

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Sampled known university name for Property 10
uni_strategy = st.sampled_from(KNOWN_UNIVERSITIES)

# Text queries that do NOT contain any known university keyword
# (used in Property 12 — no identifiable university)
# Includes all UNI_MAPPING keys (lowercased) from utils/extractors.py to
# ensure Hypothesis-generated strings containing abbreviations like "sü", "kü",
# "bü" etc. are also filtered out.
_UNI_KEYWORDS = [
    "üniversite", "universite", "itu", "odtu", "metu", "bogazici", "bilkent",
    "koc", "koç", "sabanci", "sabancı", "hacettepe", "ankara", "istanbul",
    "ege", "marmara", "gazi", "yıldız", "yildiz", "dokuz", "eylul",
    "izmir", "karadeniz", "gebze", "erciyes", "selcuk", "cukurova", "çukurova",
    # UNI_MAPPING abbreviations (lowercased) from utils/extractors.py
    # These are 2-3 letter abbreviations that the extractor recognises as
    # university names when they appear as standalone words.
    "i̇tü", "itü", "estü", "odtü", "kü", "sü", "bü", "özü", "yü", "beü",
    "aü", "mef", "i̇ü", "iü", "mü", "bkü", "başkent", "baskent",
    "haliç", "halic", "üsküdar", "uskudar", "acıbadem", "acibadem",
    "piri reis", "medipol", "özyeğin", "ozyegin", "yeditepe",
    "bahçeşehir", "bahcesehir",
]


def _has_no_uni_keyword(text: str) -> bool:
    """Return True when text contains no recognizable university keyword.

    Applies two checks to cover both standard Turkish lowercasing and the
    special case of U+0130 İ (capital I with dot), which Python lowercases
    to i+combining-dot rather than plain 'i'.
    """
    # Standard check: regular Python lowercase (handles Ü→ü, Ö→ö, etc.)
    t_standard = text.lower()
    if any(kw in t_standard for kw in _UNI_KEYWORDS):
        return False

    # Secondary check: ASCII-ify Turkish chars (catches İ → i via transliteration)
    import unicodedata
    t_norm = unicodedata.normalize("NFC", text)
    _tr_table = str.maketrans(
        "\u0130\u015e\u011e\u00dc\u00d6\u00c7\u0131\u015f\u011f\u00fc\u00f6\u00e7",
        "isguocisguoc",
    )
    t_ascii = t_norm.translate(_tr_table).lower()
    # Check against ASCII-lowercased versions of the keywords
    _UNI_KEYWORDS_ASCII = [
        kw.translate(str.maketrans(
            "üöçşğı", "uocsgi"
        ))
        for kw in _UNI_KEYWORDS
    ]
    if any(kw in t_ascii for kw in _UNI_KEYWORDS_ASCII):
        return False

    return True


no_uni_query_strategy = st.text(
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd", "Zs", "Po"),
        blacklist_characters="\\",
    ),
    min_size=1,
    max_size=150,
).filter(_has_no_uni_keyword)

# ---------------------------------------------------------------------------
# URL strategies for Property 11
# ---------------------------------------------------------------------------

_social_url_strategy = st.sampled_from([
    "https://www.instagram.com/bolum_sayfasi",
    "https://facebook.com/universiteler",
    "https://twitter.com/uni_haber",
    "https://www.tiktok.com/@universitelife",
    "https://tr.pinterest.com/uni-kampus",
    "https://instagram.com/ogrenci_yorumlari",
    "https://www.facebook.com/universite-rehberi",
])

_normal_url_strategy = st.sampled_from([
    "https://www.yok.gov.tr/universiteler",
    "https://eksisozluk.com/bogazici-universitesi",
    "https://www.itu.edu.tr/hakkimizda",
    "https://unirehberi.com/bogazici",
    "https://universiterehberi.com/burs-oranlari",
    "https://kariyer.net/universite-bilgi",
    "https://ogr.info/hacettepe",
])


# ---------------------------------------------------------------------------
# Helper: build a minimal AgentState for uni_info_node
# ---------------------------------------------------------------------------

def _make_research_state(user_text: str, uni_in_ner: str | None = None) -> dict:
    """Minimal AgentState-like dict for uni_info_node tests."""
    human_msg = MagicMock()
    human_msg.content = user_text
    ner_ctx: dict[str, Any] = {}
    if uni_in_ner:
        ner_ctx["uni"] = uni_in_ner
    return {
        "messages": [human_msg],
        "mode": "research",
        "ner_context": ner_ctx,
        "iteration_count": 0,
        "query_plan": None,
        "thinking_steps": [],
        "sub_question_results": {},
        "current_answer": None,
        "evaluation": None,
        "search_depth": 0,
        "fetched_urls": None,
    }


def _make_search_result(url: str, title: str = "Başlık", snippet: str = "İçerik") -> dict:
    """Create a fake search result dict."""
    return {"url": url, "title": title, "snippet": snippet}


# ===========================================================================
# Unit Tests — Example-based
# ===========================================================================

def test_uni_info_node_clarification_when_no_uni() -> None:
    """uni_info_node returns clarification when no university is detected."""
    state = _make_research_state("Selam, sana bir şey sormak istiyorum.")

    with patch("nodes.uni_info._ddg_quick") as mock_ddg, \
         patch("nodes.uni_info.llm_responder") as mock_llm:

        mock_ddg.return_value = []
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="cevap"))

        import nodes.uni_info as uni_module
        result = asyncio.run(
            uni_module.uni_info_node(state)
        )

    # Must not call web search or LLM
    mock_ddg.assert_not_called()
    mock_llm.ainvoke.assert_not_called()

    messages = result.get("messages", [])
    assert len(messages) >= 1
    content = getattr(messages[0], "content", "")
    # Should ask user to specify university
    assert "üniversite" in content.lower(), (
        f"Expected clarification message mentioning 'üniversite', got: {content!r}"
    )


def test_uni_info_node_makes_searches_for_known_uni() -> None:
    """uni_info_node calls _ddg_quick at least 3 times for a known university."""
    state = _make_research_state("Boğaziçi Üniversitesi hakkında bilgi ver")

    ddg_call_count = 0
    ddg_queries: list[str] = []

    async def mock_ddg(query: str, max_results: int = 8) -> list:
        nonlocal ddg_call_count
        ddg_call_count += 1
        ddg_queries.append(query)
        return [_make_search_result(f"https://example.com/{ddg_call_count}", snippet="içerik " * 20)]

    with patch("nodes.uni_info._ddg_quick", side_effect=mock_ddg), \
         patch("nodes.uni_info.llm_responder") as mock_llm:

        fake_response = MagicMock()
        fake_response.content = "Boğaziçi Üniversitesi hakkında detaylı bilgi " * 20
        mock_llm.ainvoke = AsyncMock(return_value=fake_response)

        import nodes.uni_info as uni_module
        asyncio.run(
            uni_module.uni_info_node(state)
        )

    assert ddg_call_count >= 3, (
        f"Expected at least 3 _ddg_quick calls, got {ddg_call_count}"
    )


def test_uni_info_node_excludes_social_media_urls() -> None:
    """uni_info_node filters social media URLs from context passed to LLM."""
    state = _make_research_state("Hacettepe Üniversitesi hakkında bilgi ver")

    captured_human_content: list[str] = []

    async def mock_ddg(query: str, max_results: int = 8) -> list:
        return [
            _make_search_result("https://instagram.com/hacettepe_uni", snippet="Instagram profili " * 10),
            _make_search_result("https://hacettepe.edu.tr/hakkimizda", snippet="Resmi üniversite sayfası " * 10),
            _make_search_result("https://facebook.com/hacettepe-group", snippet="Facebook grubu " * 10),
        ]

    async def mock_llm_invoke(messages: list) -> MagicMock:
        for msg in messages:
            if hasattr(msg, "content") and "hacettepe" in msg.content.lower():
                captured_human_content.append(msg.content)
        resp = MagicMock()
        resp.content = "Hacettepe Üniversitesi hakkında detaylı analiz " * 20
        return resp

    with patch("nodes.uni_info._ddg_quick", side_effect=mock_ddg), \
         patch("nodes.uni_info.llm_responder") as mock_llm:

        mock_llm.ainvoke = AsyncMock(side_effect=mock_llm_invoke)

        import nodes.uni_info as uni_module
        asyncio.run(
            uni_module.uni_info_node(state)
        )

    # Check that none of the social media URLs appear in the LLM context
    for content in captured_human_content:
        for domain in SOCIAL_MEDIA_DOMAINS:
            assert domain not in content, (
                f"Social media domain {domain!r} found in LLM context: {content[:200]}"
            )


def test_uni_info_node_graceful_error_when_all_searches_fail() -> None:
    """uni_info_node returns graceful error message when all web searches fail."""
    state = _make_research_state("Ankara Üniversitesi hakkında bilgi ver")

    async def failing_ddg(query: str, max_results: int = 8) -> list:
        raise Exception("Network error")

    with patch("nodes.uni_info._ddg_quick", side_effect=failing_ddg), \
         patch("nodes.uni_info.llm_responder") as mock_llm:

        mock_llm.ainvoke = AsyncMock()

        import nodes.uni_info as uni_module
        result = asyncio.run(
            uni_module.uni_info_node(state)
        )

    # LLM must not be called when searches fail
    mock_llm.ainvoke.assert_not_called()

    messages = result.get("messages", [])
    assert len(messages) >= 1
    content = getattr(messages[0], "content", "")
    assert content, "Graceful error message must be non-empty"


# ===========================================================================
# Property 12: Research Missing University — Clarification Response
# ===========================================================================

# Feature: chat-mode-selection, Property 12: Research Missing University — Clarification Response
@given(query=no_uni_query_strategy)
@settings(max_examples=100, deadline=None)
def test_uni_info_node_clarification_for_no_uni_queries(query: str) -> None:
    """**Validates: Requirements 3.4**

    For any query string in research mode that contains no identifiable
    university name, uni_info_node SHALL return a clarification message asking
    for the university name, without invoking web search (_ddg_quick) or LLM
    synthesis (llm_responder).
    """
    state = _make_research_state(query)

    with patch("nodes.uni_info._ddg_quick") as mock_ddg, \
         patch("nodes.uni_info.llm_responder") as mock_llm:

        mock_ddg.return_value = []
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="cevap"))

        import nodes.uni_info as uni_module
        result = asyncio.run(
            uni_module.uni_info_node(state)
        )

    # Web search and LLM MUST NOT be called
    mock_ddg.assert_not_called()
    mock_llm.ainvoke.assert_not_called()

    # Must return a message asking to clarify the university
    messages = result.get("messages", [])
    assert len(messages) >= 1, (
        f"uni_info_node must return at least one message for query={query!r}"
    )
    content = getattr(messages[0], "content", "")
    assert content, (
        f"Clarification message must be non-empty for query={query!r}"
    )
    # Clarification must mention üniversite
    assert "üniversite" in content.lower(), (
        f"Clarification message for query={query!r} does not mention 'üniversite'. "
        f"Got: {content!r}"
    )


# ===========================================================================
# Property 10: Research Minimum Search Queries
# ===========================================================================

# Feature: chat-mode-selection, Property 10: Research Minimum Search Queries
@given(uni=uni_strategy)
@settings(max_examples=100, deadline=None)
def test_uni_info_node_minimum_3_distinct_search_queries(uni: str) -> None:
    """**Validates: Requirements 3.2**

    For any query in research mode that contains an identifiable university
    name, uni_info_node SHALL invoke _ddg_quick at least 3 times with distinct
    query strings.
    """
    # Use uni_in_ner to guarantee detection even if regex doesn't catch it
    user_text = f"{uni} hakkında bilgi ver"
    state = _make_research_state(user_text, uni_in_ner=uni)

    ddg_queries: list[str] = []

    async def spy_ddg(query: str, max_results: int = 8) -> list:
        ddg_queries.append(query)
        return [_make_search_result(
            f"https://example.com/{len(ddg_queries)}",
            snippet="üniversite içeriği bilgi " * 20,
        )]

    with patch("nodes.uni_info._ddg_quick", side_effect=spy_ddg), \
         patch("nodes.uni_info.llm_responder") as mock_llm:

        fake_response = MagicMock()
        fake_response.content = f"{uni} hakkında detaylı üniversite analizi " * 20
        mock_llm.ainvoke = AsyncMock(return_value=fake_response)

        import nodes.uni_info as uni_module
        asyncio.run(
            uni_module.uni_info_node(state)
        )

    # At least 3 calls
    assert len(ddg_queries) >= 3, (
        f"Expected at least 3 _ddg_quick calls for uni={uni!r}, "
        f"got {len(ddg_queries)}: {ddg_queries}"
    )

    # All query strings must be distinct
    assert len(set(ddg_queries)) == len(ddg_queries), (
        f"Expected all distinct query strings, got duplicates: {ddg_queries}"
    )


# ===========================================================================
# Property 11: Research Social Media Exclusion
# ===========================================================================

# Feature: chat-mode-selection, Property 11: Research Social Media Exclusion
@given(
    social_urls=st.lists(_social_url_strategy, min_size=1, max_size=5),
    normal_urls=st.lists(_normal_url_strategy, min_size=1, max_size=5),
)
@settings(max_examples=100, deadline=None)
def test_research_social_media_exclusion(
    social_urls: list[str],
    normal_urls: list[str],
) -> None:
    """**Validates: Requirements 3.6**

    For any list of raw search results that includes URLs from social media
    domains, the assembled context passed to the LLM SHALL contain none of
    those social media URLs.

    This test operates at the build_grouped_context level — the same place
    where filtering happens — to verify the exclusion invariant directly,
    mirroring the career pipeline test pattern.
    """
    from utils.context_assembly import build_grouped_context

    # Build mixed result buckets: each bucket has both social and normal results
    # uni_info uses 4 buckets/labels
    single_bucket = (
        [
            _make_search_result(url, snippet="Sosyal medya içeriği " * 10)
            for url in social_urls
        ]
        + [
            _make_search_result(url, snippet="Akademik üniversite içeriği " * 10)
            for url in normal_urls
        ]
    )

    mixed_results = [single_bucket] * 4  # 4 buckets matching uni_info's 4 labels

    labels = ["Konu Araştırması", "Öğrenci Yorumları", "Genel & Akademik", "Ücret & Burs"]

    result = build_grouped_context(
        mixed_results,
        labels,
        heading="## 🏫 Test Üniversitesi\n",
        exclude_url_substrings=SOCIAL_MEDIA_DOMAINS,
    )

    # The assembled context blocks must contain none of the social media URLs
    full_context = "\n\n".join(result.context_blocks)
    for social_url in social_urls:
        assert social_url not in full_context, (
            f"Social media URL {social_url!r} found in assembled context. "
            f"Context excerpt: {full_context[:300]}"
        )

    # Also verify the seen_urls list does not include social media URLs
    for url in result.seen_urls:
        url_lower = url.lower()
        for domain in SOCIAL_MEDIA_DOMAINS:
            assert domain not in url_lower, (
                f"Social media domain {domain!r} found in seen_urls: {url!r}"
            )
