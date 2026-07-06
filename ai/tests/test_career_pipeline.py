"""
Property-based tests for Career Pipeline
==========================================
# Feature: chat-mode-selection

Tests Properties 16, 17, and 18 from the design document:

- Property 16: Career Minimum Search Queries
  For any query in career mode that contains an identifiable department name,
  career_info_node SHALL invoke _ddg_quick at least 3 times with distinct
  query strings covering curriculum/course content, career opportunities and
  salary expectations, and graduate employment statistics.
  Validates: Requirements 9.2

- Property 17: Career Social Media Exclusion
  For any list of raw career search results that includes URLs from social media
  domains, the assembled context passed to the LLM SHALL contain none of those
  social media URLs.
  Validates: Requirements 9.6

- Property 18: Career Missing Department — Clarification Response
  For any query string in career mode that contains no identifiable department
  or major name, career_info_node SHALL return a clarification message asking
  the user to specify the department of interest, without invoking web search
  or LLM synthesis.
  Validates: Requirements 9.4
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

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
# Known department names to use in Property 16 strategy
# ---------------------------------------------------------------------------
KNOWN_DEPARTMENTS = [
    "Bilgisayar Mühendisliği",
    "Psikoloji",
    "Hukuk",
    "Tıp",
    "Makine Mühendisliği",
    "İşletme",
    "Elektrik-Elektronik Mühendisliği",
    "Mimarlık",
    "Yazılım Mühendisliği",
    "Endüstri Mühendisliği",
    "İktisat",
    "İnşaat Mühendisliği",
    "Kimya Mühendisliği",
    "Matematik",
    "Fizik",
    "Biyoloji",
    "Eczacılık",
    "Veteriner",
    "Sosyoloji",
    "Felsefe",
]

# Social media domains as defined in career_info.py SOCIAL_MEDIA_DOMAINS
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

# Sampled known department name for Property 16
dept_strategy = st.sampled_from(KNOWN_DEPARTMENTS)

# Text queries that do NOT contain any known department keyword
# (used in Property 18 — no identifiable department)
_DEPT_KEYWORDS = [
    # From single_aliases in _extract_program_from_text
    "bilgisayar", "psikoloji", "hukuk", "tıp", "tip", "makine", "işletme",
    "isletme", "elektrik", "mimar", "yazılım", "yazlim", "yazlm", "endüstri",
    "endustri", "iktisat", "ekonomi", "inşaat", "insaat", "kimya", "matematik",
    "fizik", "biyoloji", "eczacı", "eczaci", "veteriner", "sosyoloji", "felsefe",
    "havacılık", "havacilik", "uçak", "ucak", "diş", "dis",
    "ybs", "pdr", "tarih", "coğrafya", "cografya", "edebiyat", "elektron",
    # From compound_aliases in _extract_program_from_text
    "pilot", "mekatronik", "genetik", "biyokimya", "antropoloji",
    "fizyoterapi", "hemşirelik", "hemsirelik", "ebelik", "odyoloji", "ergoterapi",
    "sağlık", "saglik", "ziraat", "orman", "gıda", "gida", "çevre", "cevre",
    "tekstil", "metalurji", "maden", "petrol", "jeoloji", "jeofizik",
    "harita", "gemi", "peyzaj", "şehir", "sehir", "iç mimarlık",
    "uluslararası", "uluslararasi", "siyaset", "iletişim", "iletisim",
    "grafik", "siber", "veri", "yapay", "beslenme", "biyomedikal",
    "moleküler", "molekuler", "öğretmenlik", "ogretmenlik", "kamu",
    "pilotaj", "uzay",
]


def _normalize_tr(text: str) -> str:
    """Türkçe büyük harfleri ASCII eşdeğerlerine çevirerek lowercase uygular.

    _extract_dept_from_text'in ikinci-deneme normalizasyonuyla aynı dönüşümü
    yaparak filtrenin extractor ile tutarlı olmasını sağlar.
    U+0130 İ → i, Ş/ş → s, Ğ/ğ → g, Ü/ü → u, Ö/ö → o, Ç/ç → c, ı → i
    """
    import unicodedata
    t = unicodedata.normalize("NFC", text)
    _tr_table = str.maketrans(
        "\u0130\u015e\u011e\u00dc\u00d6\u00c7\u0131\u015f\u011f\u00fc\u00f6\u00e7",
        "isguocisguoc",
    )
    return t.translate(_tr_table).lower()


def _has_no_dept_keyword(text: str) -> bool:
    """Return True when text contains no recognizable department keyword.

    Applies two checks: standard Turkish lowercase (for Ü, Ö, Ş, etc.) and
    ASCII-mapped lowercase (for U+0130 İ → i), matching how the career_info
    extractor handles input.
    """
    # Standard check: regular Python lowercase (handles Ü→ü, Ö→ö, etc.)
    t_standard = text.lower()
    if any(kw in t_standard for kw in _DEPT_KEYWORDS):
        return False

    # Secondary check: ASCII-ify Turkish chars (catches İ → i via transliteration)
    t_ascii = _normalize_tr(text)
    # Build ASCII versions of keywords for comparison
    _DEPT_KEYWORDS_ASCII = [
        kw.translate(str.maketrans(
            "\u0131\u015f\u011f\u00fc\u00f6\u00e7\u0130\u015e\u011e\u00dc\u00d6\u00c7",
            "isguocisguoc",
        ))
        for kw in _DEPT_KEYWORDS
    ]
    if any(kw in t_ascii for kw in _DEPT_KEYWORDS_ASCII):
        return False

    return True


no_dept_query_strategy = st.text(
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd", "Zs", "Po"),
        blacklist_characters="\\",
    ),
    min_size=1,
    max_size=150,
).filter(_has_no_dept_keyword)


# ---------------------------------------------------------------------------
# Helper: build a minimal AgentState for career_info_node
# ---------------------------------------------------------------------------

def _make_career_state(user_text: str, dept_in_ner: str | None = None) -> dict:
    """Return a minimal AgentState-like dict for career_info_node tests."""
    human_msg = MagicMock()
    human_msg.content = user_text
    ner_ctx: dict[str, Any] = {}
    if dept_in_ner:
        ner_ctx["program"] = dept_in_ner
    return {
        "messages": [human_msg],
        "mode": "career",
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

def test_career_info_node_clarification_when_no_dept() -> None:
    """career_info_node returns clarification when no department detected."""
    state = _make_career_state("Selam, sana bir şey sormak istiyorum.")

    with patch("nodes.career_info._ddg_quick") as mock_ddg, \
         patch("nodes.career_info.llm_responder") as mock_llm:

        mock_ddg.return_value = []
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="cevap"))

        import nodes.career_info as career_module
        result = asyncio.run(
            career_module.career_info_node(state)
        )

    # Must not call web search or LLM
    mock_ddg.assert_not_called()
    mock_llm.ainvoke.assert_not_called()

    messages = result.get("messages", [])
    assert len(messages) >= 1
    content = getattr(messages[0], "content", "")
    # Should ask user to specify department
    assert "bölüm" in content.lower() or "alan" in content.lower(), (
        f"Expected clarification message, got: {content!r}"
    )


def test_career_info_node_makes_3_searches_for_known_dept() -> None:
    """career_info_node calls _ddg_quick exactly 3 times for a known department."""
    state = _make_career_state("Bilgisayar Mühendisliği hakkında bilgi ver")

    fake_results = [
        [_make_search_result("https://example.com/1", snippet="Müfredat bilgisi " * 10)],
        [_make_search_result("https://example.com/2", snippet="Kariyer bilgisi " * 10)],
        [_make_search_result("https://example.com/3", snippet="İstihdam bilgisi " * 10)],
    ]

    ddg_call_count = 0
    ddg_queries: list[str] = []

    async def mock_ddg(query: str, max_results: int = 8) -> list:
        nonlocal ddg_call_count
        ddg_call_count += 1
        ddg_queries.append(query)
        return fake_results[ddg_call_count - 1] if ddg_call_count <= 3 else []

    with patch("nodes.career_info._ddg_quick", side_effect=mock_ddg), \
         patch("nodes.career_info.llm_responder") as mock_llm:

        fake_response = MagicMock()
        fake_response.content = "Bilgisayar Mühendisliği kariyer analizi " * 20
        mock_llm.ainvoke = AsyncMock(return_value=fake_response)

        import nodes.career_info as career_module
        asyncio.run(
            career_module.career_info_node(state)
        )

    assert ddg_call_count == 3, f"Expected 3 _ddg_quick calls, got {ddg_call_count}"
    assert len(set(ddg_queries)) == 3, (
        f"Expected 3 distinct query strings, got: {ddg_queries}"
    )


def test_career_info_node_excludes_social_media_urls() -> None:
    """career_info_node filters social media URLs from context passed to LLM."""
    state = _make_career_state("Psikoloji bölümü kariyer")

    captured_human_content: list[str] = []

    async def mock_ddg(query: str, max_results: int = 8) -> list:
        return [
            _make_search_result("https://instagram.com/psikolog", snippet="Instagram profili " * 10),
            _make_search_result("https://psikoloji.edu.tr/kariyer", snippet="Kariyer bilgisi Psikoloji " * 10),
            _make_search_result("https://facebook.com/psi-group", snippet="Facebook grubu " * 10),
        ]

    async def mock_llm_invoke(messages: list) -> MagicMock:
        for msg in messages:
            if hasattr(msg, "content") and "psikoloji" in msg.content.lower():
                captured_human_content.append(msg.content)
        resp = MagicMock()
        resp.content = "Psikoloji kariyer analizi " * 20
        return resp

    with patch("nodes.career_info._ddg_quick", side_effect=mock_ddg), \
         patch("nodes.career_info.llm_responder") as mock_llm:

        mock_llm.ainvoke = AsyncMock(side_effect=mock_llm_invoke)

        import nodes.career_info as career_module
        asyncio.run(
            career_module.career_info_node(state)
        )

    # Check that none of the social media URLs appear in the LLM context
    for content in captured_human_content:
        for domain in SOCIAL_MEDIA_DOMAINS:
            assert domain not in content, (
                f"Social media domain {domain!r} found in LLM context: {content[:200]}"
            )


def test_career_info_node_graceful_error_when_all_searches_fail() -> None:
    """career_info_node returns graceful error message when all web searches fail."""
    state = _make_career_state("Hukuk bölümü")

    async def failing_ddg(query: str, max_results: int = 8) -> list:
        raise Exception("Network error")

    with patch("nodes.career_info._ddg_quick", side_effect=failing_ddg), \
         patch("nodes.career_info.llm_responder") as mock_llm:

        mock_llm.ainvoke = AsyncMock()

        import nodes.career_info as career_module
        result = asyncio.run(
            career_module.career_info_node(state)
        )

    # LLM must not be called when searches fail
    mock_llm.ainvoke.assert_not_called()

    messages = result.get("messages", [])
    assert len(messages) >= 1
    content = getattr(messages[0], "content", "")
    assert content, "Graceful error message must be non-empty"


# ===========================================================================
# Property 18: Career Missing Department — Clarification Response
# ===========================================================================

# Feature: chat-mode-selection, Property 18: Career Missing Department — Clarification Response
@given(query=no_dept_query_strategy)
@settings(max_examples=100, deadline=None)
def test_career_info_node_clarification_for_no_dept_queries(query: str) -> None:
    """**Validates: Requirements 9.4**

    For any query string in career mode that contains no identifiable
    department or major name, career_info_node SHALL return a clarification
    message asking the user to specify the department of interest, without
    invoking web search (_ddg_quick) or LLM synthesis (llm_responder).
    """
    state = _make_career_state(query)

    with patch("nodes.career_info._ddg_quick") as mock_ddg, \
         patch("nodes.career_info.llm_responder") as mock_llm:

        mock_ddg.return_value = []
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="cevap"))

        import nodes.career_info as career_module
        result = asyncio.run(
            career_module.career_info_node(state)
        )

    # Web search and LLM MUST NOT be called
    mock_ddg.assert_not_called()
    mock_llm.ainvoke.assert_not_called()

    # Must return a message asking to clarify the department
    messages = result.get("messages", [])
    assert len(messages) >= 1, (
        f"career_info_node must return at least one message for query={query!r}"
    )
    content = getattr(messages[0], "content", "")
    assert content, (
        f"Clarification message must be non-empty for query={query!r}"
    )
    # Clarification must mention bölüm/alan
    assert "bölüm" in content.lower() or "alan" in content.lower(), (
        f"Clarification message for query={query!r} does not mention 'bölüm' or 'alan'. "
        f"Got: {content!r}"
    )


# ===========================================================================
# Property 16: Career Minimum Search Queries
# ===========================================================================

# Feature: chat-mode-selection, Property 16: Career Minimum Search Queries
@given(dept=dept_strategy)
@settings(max_examples=100, deadline=None)
def test_career_info_node_minimum_3_distinct_search_queries(dept: str) -> None:
    """**Validates: Requirements 9.2**

    For any query in career mode that contains an identifiable department
    name, career_info_node SHALL invoke _ddg_quick at least 3 times with
    distinct query strings covering:
    - curriculum/course content
    - career opportunities and salary expectations
    - graduate employment statistics
    """
    # Compose query that contains the department name so it gets detected
    user_text = f"{dept} bölümü hakkında bilgi ver"
    state = _make_career_state(user_text)

    ddg_queries: list[str] = []

    async def spy_ddg(query: str, max_results: int = 8) -> list:
        ddg_queries.append(query)
        return [_make_search_result(f"https://example.com/{len(ddg_queries)}", snippet="içerik " * 20)]

    with patch("nodes.career_info._ddg_quick", side_effect=spy_ddg), \
         patch("nodes.career_info.llm_responder") as mock_llm:

        fake_response = MagicMock()
        fake_response.content = f"{dept} kariyer analizi açıklaması " * 20
        mock_llm.ainvoke = AsyncMock(return_value=fake_response)

        import nodes.career_info as career_module
        asyncio.run(
            career_module.career_info_node(state)
        )

    # At least 3 calls
    assert len(ddg_queries) >= 3, (
        f"Expected at least 3 _ddg_quick calls for dept={dept!r}, "
        f"got {len(ddg_queries)}: {ddg_queries}"
    )

    # All query strings must be distinct
    assert len(set(ddg_queries)) == len(ddg_queries), (
        f"Expected all distinct query strings, got duplicates: {ddg_queries}"
    )

    # Verify the 3 required topics are covered:
    queries_lower = [q.lower() for q in ddg_queries]

    # Topic 1: curriculum / course content
    has_curriculum = any(
        any(kw in q for kw in ("müfredat", "ders içerik", "ders", "müf"))
        for q in queries_lower
    )
    assert has_curriculum, (
        f"No curriculum-related query found in: {ddg_queries}"
    )

    # Topic 2: career / salary / job ads
    has_career = any(
        any(kw in q for kw in ("kariyer", "maaş", "iş ilan", "mezun"))
        for q in queries_lower
    )
    assert has_career, (
        f"No career/salary-related query found in: {ddg_queries}"
    )

    # Topic 3: employment statistics
    has_employment = any(
        any(kw in q for kw in ("istihdam", "yök", "istatistik", "oran"))
        for q in queries_lower
    )
    assert has_employment, (
        f"No employment-statistics-related query found in: {ddg_queries}"
    )


# ===========================================================================
# Property 17: Career Social Media Exclusion
# ===========================================================================

# Strategy: generate search result lists that include at least one social media URL
_social_url_strategy = st.sampled_from([
    "https://www.instagram.com/bilgisayarmuh",
    "https://facebook.com/bilgisayar-bolumu",
    "https://twitter.com/compsci_dept",
    "https://www.tiktok.com/@yazilim",
    "https://tr.pinterest.com/muh-bolumleri",
    "https://instagram.com/psikologlar",
    "https://www.facebook.com/hukuk-mezunlari",
])

_normal_url_strategy = st.sampled_from([
    "https://www.yok.gov.tr/egitim/programlar",
    "https://kariyer.net/isilan/bilgisayar-muhendisi",
    "https://www.cs.hacettepe.edu.tr/mufredat",
    "https://eksikatalogu.com/bolum/psikoloji",
    "https://istatistik.yok.gov.tr/",
    "https://universite.rehberi.com/bolumler",
    "https://iku.edu.tr/hukuk-fakultesi",
])


@given(
    social_urls=st.lists(_social_url_strategy, min_size=1, max_size=5),
    normal_urls=st.lists(_normal_url_strategy, min_size=1, max_size=5),
)
@settings(max_examples=100, deadline=None)
def test_career_social_media_exclusion(
    social_urls: list[str],
    normal_urls: list[str],
) -> None:
    """**Validates: Requirements 9.6**

    For any list of raw career search results that includes URLs from social
    media domains (instagram.com, facebook.com, twitter.com, tiktok.com,
    pinterest.com), the assembled context passed to the LLM SHALL contain
    none of those social media URLs.

    This test operates at the build_grouped_context level — the same place
    where filtering happens — to verify the exclusion invariant directly,
    mirroring the research pipeline test pattern.
    """
    from utils.context_assembly import build_grouped_context

    # Build mixed result buckets: each bucket has both social and normal results
    mixed_results = [
        [
            _make_search_result(url, snippet="Sosyal medya içeriği " * 10)
            for url in social_urls
        ]
        + [
            _make_search_result(url, snippet="Akademik içerik kariyer " * 10)
            for url in normal_urls
        ]
    ]
    # Pad to 3 buckets as career_info_node uses 3 labels
    while len(mixed_results) < 3:
        mixed_results.append(mixed_results[0])

    labels = ["Müfredat & Dersler", "Kariyer & Maaş", "İstihdam İstatistikleri"]

    result = build_grouped_context(
        mixed_results,
        labels,
        heading="## 🧭 Test Bölümü\n",
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
