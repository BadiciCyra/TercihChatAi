"""
Property-based tests for Wizard Pipeline
==========================================
# Feature: chat-mode-selection

Tests Properties 7, 8, and 9 from the design document:

- Property 7: Wizard Entity Extraction — Rank Formats
  For any string containing a valid Turkish ranking number in the formats
  accepted by the system (150k, 25 bin, 200000, 50.000 and variations),
  _extract_rank_from_text SHALL return a non-None normalized integer string.
  Validates: Requirements 2.2

- Property 8: Wizard Retriever Result Passthrough
  For any non-empty markdown table string returned by a mocked YÖK Atlas
  retriever, the Wizard_Pipeline response message SHALL contain that table
  content.
  Validates: Requirements 2.3

- Property 9: Wizard Missing Entity — Clarification Response
  For any user query string that contains no recognizable rank, program,
  university, or city entity, fast_lookup_node in wizard mode SHALL return
  a response that does NOT contain a markdown table, and SHALL contain a
  request for the user to provide missing information.
  Validates: Requirements 2.4
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Optional
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
# Rank format strategies for Property 7
# ---------------------------------------------------------------------------

# Strategy for "150k" format: integer 1–999 followed by "k"
_k_format_strategy = st.builds(
    lambda n, space: f"{n}{space}k",
    n=st.integers(min_value=1, max_value=999),
    space=st.sampled_from(["", " "]),
)

# Strategy for "25 bin" format: integer 1–999 followed by "bin"
_bin_format_strategy = st.builds(
    lambda n, space: f"{n}{space}bin",
    n=st.integers(min_value=1, max_value=999),
    space=st.sampled_from(["", " "]),
)

# Strategy for "200000" format: plain integer 4–7 digits
_plain_int_strategy = st.integers(min_value=1000, max_value=9999999).map(str)

# Strategy for "50.000" format: Turkish dot-separated thousands
_dot_format_strategy = st.builds(
    lambda n: f"{n}.{0:03d}",
    n=st.integers(min_value=1, max_value=999),
)

# Combined: pick any of the four formats and optionally wrap in surrounding text
_rank_token_strategy = st.one_of(
    _k_format_strategy,
    _bin_format_strategy,
    _plain_int_strategy,
    _dot_format_strategy,
)

_prefix_strategy = st.text(
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Zs"),
        blacklist_characters="0123456789.kK",
    ),
    min_size=0,
    max_size=20,
)

_suffix_strategy = st.text(
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Zs"),
        blacklist_characters="0123456789.kK",
    ),
    min_size=0,
    max_size=20,
)

# Full strategy: surrounding text + rank token + surrounding text
_rank_in_sentence_strategy = st.builds(
    lambda prefix, rank, suffix: f"{prefix} {rank} {suffix}".strip(),
    prefix=_prefix_strategy,
    rank=_rank_token_strategy,
    suffix=_suffix_strategy,
)


# ---------------------------------------------------------------------------
# Keywords that would let fast_lookup_node detect an entity
# (used to EXCLUDE them from Property 9's "no-entity" strategy)
# ---------------------------------------------------------------------------

_ENTITY_KEYWORDS = [
    # Rank patterns — all digits
    "0", "1", "2", "3", "4", "5", "6", "7", "8", "9",
    # k/bin suffixes
    "k", "bin",
    # Common program keywords (must match _extract_program_from_text logic)
    "bilgisayar", "yazılım", "yazlim", "tıp", "tip", "hukuk", "diş", "dis",
    "psikoloji", "mimar", "eczacı", "eczaci", "veteriner", "işletme", "isletme",
    "iktisat", "ekonomi", "havacılık", "havacilik", "uçak", "ucak",
    "elektrik", "elektron", "makine", "inşaat", "insaat", "kimya",
    "biyoloji", "fizik", "matematik", "tarih", "coğrafya", "cografya",
    "edebiyat", "endüstri", "endustri", "yazılım", "yazlim",
    "ybs", "pdr", "siber", "veri", "yapay", "mekatronik",
    # Common university short names / keywords
    "üniversite", "universitesi", "universite", "itu", "odtu", "bilkent",
    "koç", "koc", "sabancı", "sabanci", "bülent", "bahçeşehir",
    "medipol", "başkent", "baskent", "yeditepe", "özyeğin",
    # Common city keywords (from _CITY_VARIANTS keys)
    "istanbul", "ankara", "izmir", "bursa", "antalya", "adana", "konya",
    "gaziantep", "mersin", "kayseri", "eskişehir", "eskisehir",
    "ist", "ank", "izm",
]


def _has_no_entity_keyword(text: str) -> bool:
    """Return True when text contains no recognizable entity keyword."""
    t = text.lower()
    return not any(kw in t for kw in _ENTITY_KEYWORDS)


# Strategy for queries with no recognizable entity (Property 9)
_no_entity_query_strategy = st.text(
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Zs", "Po"),
        blacklist_characters="0123456789\\kKbB",
    ),
    min_size=1,
    max_size=150,
).filter(_has_no_entity_keyword)


# ---------------------------------------------------------------------------
# Helper: build a minimal AgentState for fast_lookup_node
# ---------------------------------------------------------------------------

def _make_wizard_state(user_text: str, ner_context: Optional[dict] = None) -> dict:
    """Return a minimal AgentState-like dict for fast_lookup_node tests."""
    human_msg = MagicMock()
    human_msg.content = user_text
    return {
        "messages": [human_msg],
        "mode": "wizard",
        "ner_context": ner_context or {},
        "iteration_count": 0,
        "query_plan": None,
        "thinking_steps": [],
        "sub_question_results": {},
        "current_answer": None,
        "evaluation": None,
        "search_depth": 0,
        "fetched_urls": None,
    }


# ===========================================================================
# Unit Tests — Example-based (Task 7.1 verification examples)
# ===========================================================================

def test_extract_rank_handles_150k() -> None:
    """_extract_rank_from_text correctly handles '150k' format."""
    from utils.extractors import _extract_rank_from_text
    result = _extract_rank_from_text("150k ile bilgisayar")
    assert result == "150000", f"Expected '150000', got {result!r}"


def test_extract_rank_handles_150_space_k() -> None:
    """_extract_rank_from_text handles '150 k' (space before k)."""
    from utils.extractors import _extract_rank_from_text
    result = _extract_rank_from_text("150 k sıralamasıyla")
    assert result == "150000", f"Expected '150000', got {result!r}"


def test_extract_rank_handles_25_bin() -> None:
    """_extract_rank_from_text correctly handles '25 bin' format."""
    from utils.extractors import _extract_rank_from_text
    result = _extract_rank_from_text("25 bin sıralamamla ne okuyabilirim")
    assert result == "25000", f"Expected '25000', got {result!r}"


def test_extract_rank_handles_25bin_no_space() -> None:
    """_extract_rank_from_text handles '25bin' (no space before bin)."""
    from utils.extractors import _extract_rank_from_text
    result = _extract_rank_from_text("25bin sıralaması")
    assert result == "25000", f"Expected '25000', got {result!r}"


def test_extract_rank_handles_200000() -> None:
    """_extract_rank_from_text correctly handles '200000' (plain integer) format."""
    from utils.extractors import _extract_rank_from_text
    result = _extract_rank_from_text("sıralamam 200000")
    assert result == "200000", f"Expected '200000', got {result!r}"


def test_extract_rank_handles_50_dot_000() -> None:
    """_extract_rank_from_text correctly handles '50.000' Turkish dot-separated format."""
    from utils.extractors import _extract_rank_from_text
    result = _extract_rank_from_text("50.000 sıralamasıyla İstanbul")
    assert result == "50000", f"Expected '50000', got {result!r}"


def test_extract_rank_handles_150_dot_000() -> None:
    """_extract_rank_from_text correctly handles '150.000' Turkish format."""
    from utils.extractors import _extract_rank_from_text
    result = _extract_rank_from_text("sayısal 150.000")
    assert result == "150000", f"Expected '150000', got {result!r}"


def test_extract_rank_returns_none_for_empty_string() -> None:
    """_extract_rank_from_text returns None for empty string."""
    from utils.extractors import _extract_rank_from_text
    result = _extract_rank_from_text("")
    assert result is None, f"Expected None, got {result!r}"


def test_extract_rank_returns_none_for_no_rank() -> None:
    """_extract_rank_from_text returns None when no rank is present."""
    from utils.extractors import _extract_rank_from_text
    result = _extract_rank_from_text("Bilgisayar Mühendisliği hakkında bilgi ver")
    assert result is None, f"Expected None, got {result!r}"


def test_extract_rank_works_on_ner_context_empty(monkeypatch) -> None:
    """fast_lookup_node's regex extraction works when ner_context is empty (mode bypass case).

    In wizard mode bypass, ner_context is {} (empty). The node must still
    extract the rank from user_text via regex.
    """
    from utils.extractors import _extract_rank_from_text

    # Simulates the mode bypass scenario: ner_context is empty
    user_text = "sıralamam 75.000 bilgisayar için ne gelir"
    ner_ctx: dict = {}  # empty — as in mode bypass

    # Replicate the logic in fast_lookup_node: if not ner_ctx.get("rank"), call regex
    if not ner_ctx.get("rank"):
        v = _extract_rank_from_text(user_text)
        if v:
            ner_ctx["rank"] = v

    assert ner_ctx.get("rank") == "75000", (
        f"Expected ner_ctx['rank']='75000' after regex extraction with empty context, "
        f"got {ner_ctx.get('rank')!r}"
    )


# ===========================================================================
# Property 7: Wizard Entity Extraction — Rank Formats
# ===========================================================================

# Feature: chat-mode-selection, Property 7: Wizard Entity Extraction — Rank Formats
@given(text=_rank_in_sentence_strategy)
@settings(max_examples=100, deadline=None)
def test_extract_rank_returns_non_none_for_all_valid_formats(text: str) -> None:
    """**Validates: Requirements 2.2**

    For any string containing a valid Turkish ranking number in the formats
    accepted by the system (150k, 25 bin, 200000, 50.000 and variations),
    _extract_rank_from_text SHALL return a non-None normalized integer string.
    """
    from utils.extractors import _extract_rank_from_text

    result = _extract_rank_from_text(text)

    assert result is not None, (
        f"_extract_rank_from_text returned None for text={text!r}, "
        f"expected a non-None integer string"
    )

    # Must be a string that parses to a positive integer
    assert result.isdigit(), (
        f"_extract_rank_from_text returned {result!r} for text={text!r}, "
        f"expected a digit-only string"
    )
    assert int(result) > 0, (
        f"_extract_rank_from_text returned {result!r} for text={text!r}, "
        f"expected a positive integer string"
    )


# ===========================================================================
# Property 8: Wizard Retriever Result Passthrough
# ===========================================================================

# Strategy: generate non-empty markdown table strings
_markdown_table_strategy = st.builds(
    lambda header_cols, rows: (
        "| " + " | ".join(f"Sütun{i+1}" for i in range(max(1, header_cols))) + " |\n"
        "| " + " | ".join("---" for _ in range(max(1, header_cols))) + " |\n"
        + "".join(
            "| " + " | ".join(f"değer{r+1}_{c+1}" for c in range(max(1, header_cols))) + " |\n"
            for r in range(max(1, len(rows)))
        )
    ),
    header_cols=st.integers(min_value=2, max_value=6),
    rows=st.lists(st.just(None), min_size=1, max_size=5),
)


# Feature: chat-mode-selection, Property 8: Wizard Retriever Result Passthrough
@given(markdown_table=_markdown_table_strategy)
@settings(max_examples=100, deadline=None)
def test_wizard_retriever_passthrough_table_in_response(markdown_table: str) -> None:
    """**Validates: Requirements 2.3**

    For any non-empty markdown table string returned by a mocked YÖK Atlas
    retriever, the Wizard_Pipeline (fast_lookup_node) response message SHALL
    contain that table content.
    """
    assert markdown_table.strip(), "Strategy must produce non-empty table"

    # Provide a state with a known rank so the node reaches the retriever call
    state = _make_wizard_state(
        user_text="50000 sıralamasıyla bilgisayar mühendisliği",
        ner_context={"rank": "50000", "program": "Bilgisayar Mühendisliği"},
    )

    async def mock_yok_atlas_lookup(ner_ctx: dict) -> Optional[str]:
        return markdown_table

    async def mock_web_supplement(ner_ctx: dict) -> Optional[str]:
        return None  # keep response deterministic

    with patch("nodes.fast_lookup._fast_yok_atlas_lookup", side_effect=mock_yok_atlas_lookup), \
         patch("nodes.fast_lookup._fast_web_supplement", side_effect=mock_web_supplement), \
         patch("nodes.fast_lookup._save_last_entities"):

        import nodes.fast_lookup as fast_module
        result = asyncio.run(
            fast_module.fast_lookup_node(state)
        )

    messages = result.get("messages", [])
    assert len(messages) >= 1, (
        f"fast_lookup_node must return at least one message, got empty list"
    )
    content = getattr(messages[0], "content", "")
    assert content, "Response message must have non-empty content"

    # The table content must appear in the response
    # We check for a distinctive portion of the table (first line)
    first_table_line = markdown_table.splitlines()[0].strip()
    assert first_table_line in content, (
        f"Markdown table content not found in response.\n"
        f"Table first line: {first_table_line!r}\n"
        f"Response excerpt: {content[:500]!r}"
    )


# ===========================================================================
# Property 9: Wizard Missing Entity — Clarification Response
# ===========================================================================

# Feature: chat-mode-selection, Property 9: Wizard Missing Entity — Clarification Response
@given(query=_no_entity_query_strategy)
@settings(max_examples=100, deadline=None)
def test_wizard_missing_entity_returns_clarification(query: str) -> None:
    """**Validates: Requirements 2.4**

    For any user query string that contains no recognizable rank, program,
    university, or city entity, fast_lookup_node in wizard mode SHALL:
    1. Return a response that does NOT contain a markdown table (no '|' table rows).
    2. Return a response that DOES contain a request for the user to provide
       missing information (mentions at least one of: bölüm, sıralama,
       şehir, üniversite, bilgi).
    """
    state = _make_wizard_state(user_text=query, ner_context={})

    with patch("nodes.fast_lookup._fast_yok_atlas_lookup") as mock_yok, \
         patch("nodes.fast_lookup._fast_web_supplement") as mock_web, \
         patch("nodes.fast_lookup._load_last_entities", return_value={}), \
         patch("nodes.fast_lookup._scan_history_for_entities", return_value={}), \
         patch("nodes.fast_lookup._save_last_entities"):

        mock_yok.return_value = None
        mock_web.return_value = None

        import nodes.fast_lookup as fast_module
        result = asyncio.run(
            fast_module.fast_lookup_node(state)
        )

    # Retriever must NOT have been called (no entity → short-circuit)
    mock_yok.assert_not_called()

    messages = result.get("messages", [])
    assert len(messages) >= 1, (
        f"fast_lookup_node must return at least one message for query={query!r}"
    )
    content = getattr(messages[0], "content", "")
    assert content, (
        f"Clarification message must be non-empty for query={query!r}"
    )

    # 1. Must NOT contain a markdown table (no pipe-delimited rows like "| col |")
    has_table = bool(
        __import__("re").search(r"^\|.+\|", content, __import__("re").MULTILINE)
    )
    assert not has_table, (
        f"Clarification response for query={query!r} unexpectedly contains a markdown table. "
        f"Response excerpt: {content[:300]!r}"
    )

    # 2. Must ask for missing information — at least one clarification keyword
    content_lower = content.lower()
    clarification_keywords = ["bölüm", "sıralama", "siralama", "şehir", "sehir",
                               "üniversite", "universite", "bilgi", "ver", "lütfen",
                               "lutfen", "hangi", "kaç", "kac"]
    has_clarification = any(kw in content_lower for kw in clarification_keywords)
    assert has_clarification, (
        f"Clarification message for query={query!r} does not ask for missing info. "
        f"Response: {content[:300]!r}"
    )
