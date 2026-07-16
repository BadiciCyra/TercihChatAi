"""
Property-based tests for Research Pipeline
===========================================
# Feature: chat-mode-selection (llm-dynamic-query-generator entegrasyonuna uyarlandı)

Tests Properties 10, 11, and 12 from the design document:

- Property 10: Research Minimum Search Queries
  For any query in research mode that contains an identifiable university
  name, uni_info_node SHALL run at least 3 distinct web search queries.
  (Sorgu üretimi artık QueryPlanner'dadır; node QueryPlan.queries'i
  _ddg_multi_query'ye tek çağrıda iletir.)
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

from utils.query_planner import QueryPlan

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
_UNI_KEYWORDS = [
    "üniversite", "universite", "itu", "odtu", "metu", "bogazici", "bilkent",
    "koc", "koç", "sabanci", "sabancı", "hacettepe", "ankara", "istanbul",
    "ege", "marmara", "gazi", "yıldız", "yildiz", "dokuz", "eylul",
    "izmir", "karadeniz", "gebze", "erciyes", "selcuk", "cukurova", "çukurova",
    # UNI_MAPPING abbreviations (lowercased) from utils/extractors.py
    "i̇tü", "itü", "estü", "odtü", "kü", "sü", "bü", "özü", "yü", "beü",
    "aü", "mef", "i̇ü", "iü", "mü", "bkü", "başkent", "baskent",
    "haliç", "halic", "üsküdar", "uskudar", "acıbadem", "acibadem",
    "piri reis", "medipol", "özyeğin", "ozyegin", "yeditepe",
    "bahçeşehir", "bahcesehir",
]


def _has_no_uni_keyword(text: str) -> bool:
    """Return True when text contains no recognizable university keyword."""
    t_standard = text.lower()
    if any(kw in t_standard for kw in _UNI_KEYWORDS):
        return False

    import unicodedata
    t_norm = unicodedata.normalize("NFC", text)
    _tr_table = str.maketrans(
        "İŞĞÜÖÇışğüöç",
        "isguocisguoc",
    )
    t_ascii = t_norm.translate(_tr_table).lower()
    _UNI_KEYWORDS_ASCII = [
        kw.translate(str.maketrans("üöçşğı", "uocsgi"))
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
# Helpers
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


def _make_plan(uni: str) -> QueryPlan:
    """Deterministic 3-query general plan for a university."""
    return QueryPlan(
        is_specific=False,
        queries=[
            f"{uni} genel bilgi akademik kadro",
            f"{uni} öğrenci yorumları",
            f"{uni} burs ücret 2024 2025",
        ],
        source="llm",
    )


async def _invoke_uni_info(
    state: dict,
    plan: QueryPlan | None,
    ddg_results: list | None = None,
    ddg_exception: Exception | None = None,
    llm_content: str = "Detaylı üniversite analizi. " * 20,
):
    """uni_info_node'u dış bağımlılıklar mock'lanmış şekilde çalıştırır.

    Returns:
        (result, planner_mock, ddg_mock, llm_ainvoke_mock, captured_human_contents)
    """
    captured: list[str] = []

    fake_response = MagicMock()
    fake_response.content = llm_content

    async def fake_llm_ainvoke(messages, **kwargs):
        for msg in messages:
            if type(msg).__name__ == "HumanMessage" and hasattr(msg, "content"):
                captured.append(msg.content)
        return fake_response

    llm_ainvoke_mock = AsyncMock(side_effect=fake_llm_ainvoke)

    if ddg_exception is not None:
        ddg_mock = AsyncMock(side_effect=ddg_exception)
    else:
        ddg_mock = AsyncMock(return_value=ddg_results if ddg_results is not None else [])

    planner_mock = AsyncMock(return_value=plan)

    with (
        patch("nodes.uni_info._ddg_multi_query", new=ddg_mock),
        patch("nodes.uni_info.llm_responder") as mock_llm,
        patch("nodes.uni_info.query_planner") as mock_planner,
        patch("nodes.uni_info._extract_uni_llm_fallback", new=AsyncMock(return_value=None)),
        patch(
            "nodes.uni_info.fetch_url_content",
            new=AsyncMock(return_value=MagicMock(success=False, content="", url="")),
        ),
        patch("nodes.uni_info._load_uni_info_cache", return_value=None),
        patch("nodes.uni_info._save_uni_info_cache", return_value=None),
    ):
        mock_llm.ainvoke = llm_ainvoke_mock
        mock_planner.classify_and_generate_queries = planner_mock
        import nodes.uni_info as uni_module
        result = await uni_module.uni_info_node(state)

    return result, planner_mock, ddg_mock, llm_ainvoke_mock, captured


# ===========================================================================
# Unit Tests — Example-based
# ===========================================================================

def test_uni_info_node_clarification_when_no_uni() -> None:
    """uni_info_node returns clarification when no university is detected."""
    state = _make_research_state("Selam, sana bir şey sormak istiyorum.")

    result, planner_mock, ddg_mock, llm_mock, _ = asyncio.run(
        _invoke_uni_info(state, plan=None)
    )

    # Must not call planner, web search or LLM
    planner_mock.assert_not_awaited()
    ddg_mock.assert_not_awaited()
    llm_mock.assert_not_awaited()

    messages = result.get("messages", [])
    assert len(messages) >= 1
    content = getattr(messages[0], "content", "")
    assert "üniversite" in content.lower(), (
        f"Expected clarification message mentioning 'üniversite', got: {content!r}"
    )


def test_uni_info_node_makes_searches_for_known_uni() -> None:
    """uni_info_node runs all QueryPlan queries (>=3) for a known university."""
    uni = "Boğaziçi Üniversitesi"
    state = _make_research_state(f"{uni} hakkında bilgi ver")

    ddg_results = [
        _make_search_result(f"https://example.com/{i}", snippet="içerik " * 20)
        for i in range(9)
    ]

    _, _, ddg_mock, _, _ = asyncio.run(
        _invoke_uni_info(state, plan=_make_plan(uni), ddg_results=ddg_results)
    )

    ddg_mock.assert_awaited_once()
    searched_queries = ddg_mock.await_args.args[0]
    assert len(searched_queries) >= 3, (
        f"Expected at least 3 search queries, got {len(searched_queries)}"
    )


def test_uni_info_node_excludes_social_media_urls() -> None:
    """uni_info_node filters social media URLs from context passed to LLM."""
    uni = "Hacettepe Üniversitesi"
    state = _make_research_state(f"{uni} hakkında bilgi ver")

    ddg_results = [
        _make_search_result("https://instagram.com/hacettepe_uni", snippet="Instagram profili " * 10),
        _make_search_result("https://hacettepe.edu.tr/hakkimizda", snippet="Resmi üniversite sayfası " * 10),
        _make_search_result("https://facebook.com/hacettepe-group", snippet="Facebook grubu " * 10),
    ] * 3

    _, _, _, _, captured = asyncio.run(
        _invoke_uni_info(state, plan=_make_plan(uni), ddg_results=ddg_results)
    )

    assert captured, "LLM'e HumanMessage gönderilmedi"
    for content in captured:
        for domain in SOCIAL_MEDIA_DOMAINS:
            assert domain not in content, (
                f"Social media domain {domain!r} found in LLM context: {content[:200]}"
            )


def test_uni_info_node_graceful_error_when_all_searches_fail() -> None:
    """uni_info_node returns graceful error message when web search fails."""
    uni = "Ankara Üniversitesi"
    state = _make_research_state(f"{uni} hakkında bilgi ver")

    result, _, _, llm_mock, _ = asyncio.run(
        _invoke_uni_info(state, plan=_make_plan(uni), ddg_exception=Exception("Network error"))
    )

    # LLM must not be called when searches fail
    llm_mock.assert_not_awaited()

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
    for the university name, without invoking web search or LLM synthesis.
    """
    state = _make_research_state(query)

    result, planner_mock, ddg_mock, llm_mock, _ = asyncio.run(
        _invoke_uni_info(state, plan=None)
    )

    # Planner, web search and LLM MUST NOT be called
    planner_mock.assert_not_awaited()
    ddg_mock.assert_not_awaited()
    llm_mock.assert_not_awaited()

    messages = result.get("messages", [])
    assert len(messages) >= 1, (
        f"uni_info_node must return at least one message for query={query!r}"
    )
    content = getattr(messages[0], "content", "")
    assert content, (
        f"Clarification message must be non-empty for query={query!r}"
    )
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
    name, uni_info_node SHALL run at least 3 distinct web search queries
    (QueryPlan.queries, tek _ddg_multi_query çağrısında).
    """
    user_text = f"{uni} hakkında bilgi ver"
    state = _make_research_state(user_text, uni_in_ner=uni)

    ddg_results = [
        _make_search_result(f"https://example.com/{i}", snippet="üniversite içeriği bilgi " * 20)
        for i in range(9)
    ]

    _, planner_mock, ddg_mock, _, _ = asyncio.run(
        _invoke_uni_info(state, plan=_make_plan(uni), ddg_results=ddg_results)
    )

    planner_mock.assert_awaited_once()
    ddg_mock.assert_awaited_once()
    searched_queries = ddg_mock.await_args.args[0]

    # At least 3 queries
    assert len(searched_queries) >= 3, (
        f"Expected at least 3 search queries for uni={uni!r}, "
        f"got {len(searched_queries)}: {searched_queries}"
    )

    # All query strings must be distinct
    assert len(set(searched_queries)) == len(searched_queries), (
        f"Expected all distinct query strings, got duplicates: {searched_queries}"
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

    mixed_results = [single_bucket] * 4

    labels = ["Konu Araştırması", "Öğrenci Yorumları", "Genel & Akademik", "Ücret & Burs"]

    result = build_grouped_context(
        mixed_results,
        labels,
        heading="## 🏫 Test Üniversitesi\n",
        exclude_url_substrings=SOCIAL_MEDIA_DOMAINS,
    )

    full_context = "\n\n".join(result.context_blocks)
    for social_url in social_urls:
        assert social_url not in full_context, (
            f"Social media URL {social_url!r} found in assembled context. "
            f"Context excerpt: {full_context[:300]}"
        )

    for url in result.seen_urls:
        url_lower = url.lower()
        for domain in SOCIAL_MEDIA_DOMAINS:
            assert domain not in url_lower, (
                f"Social media domain {domain!r} found in seen_urls: {url!r}"
            )
