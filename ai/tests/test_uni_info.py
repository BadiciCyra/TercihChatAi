# Feature: llm-dynamic-query-generator — uni_info_node QueryPlanner entegrasyonu
"""
Integration tests for uni_info_node + QueryPlanner.

Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5, 5.7

Eski keyword tabanlı testler (_UNI_TOPIC_KEYWORDS, _build_uni_queries) kaldırıldı;
bu mantık artık QueryPlanner tarafından üstlenildiği için burada node'un
QueryPlanner ile doğru konuştuğu doğrulanır.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage, AIMessage

from utils.query_planner import QueryPlan
from nodes.uni_info import uni_info_node


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

_FAKE_DDG_RESULTS = [
    {"title": f"Sonuç {i}", "url": f"https://example.com/{i}", "body": f"İçerik {i}"}
    for i in range(12)
]

_GENERAL_PLAN = QueryPlan(
    is_specific=False,
    queries=[
        "Ankara Üniversitesi genel bilgi",
        "Ankara Üniversitesi öğrenci yorumları",
        "Ankara Üniversitesi burs imkânları",
    ],
    source="llm",
)

_SPECIFIC_PLAN = QueryPlan(
    is_specific=True,
    queries=[
        "Ankara Üniversitesi yurt imkânları",
        "Ankara Üniversitesi yurt ücretleri 2025",
        "Ankara Üniversitesi yurt öğrenci yorumları",
    ],
    source="llm",
)


def _make_state(uni: str, user_text: str) -> dict:
    return {
        "messages": [HumanMessage(content=user_text)],
        "ner_context": {"uni": uni, "program": None, "web_query": ""},
        "iteration_count": 0,
    }


async def _run_node(state: dict, plan, cached_uni_info=None, planner_side_effect=None):
    """uni_info_node'u tüm dış bağımlılıklar mock'lanmış şekilde çalıştırır.

    Returns:
        (result, planner_mock, captured_human_contents, ddg_mock)
    """
    captured: list[str] = []

    fake_llm_response = MagicMock()
    fake_llm_response.content = "Detaylı üniversite analizi. " * 20

    async def fake_llm_ainvoke(messages, **kwargs):
        for msg in messages:
            if type(msg).__name__ == "HumanMessage" and hasattr(msg, "content"):
                captured.append(msg.content)
        return fake_llm_response

    planner_mock = AsyncMock(return_value=plan)
    if planner_side_effect is not None:
        planner_mock.side_effect = planner_side_effect

    ddg_mock = AsyncMock(return_value=_FAKE_DDG_RESULTS)

    with (
        patch("nodes.uni_info._ddg_multi_query", new=ddg_mock),
        patch("nodes.uni_info.llm_responder") as mock_llm,
        patch("nodes.uni_info.query_planner") as mock_planner,
        patch("nodes.uni_info._extract_uni_llm_fallback", new=AsyncMock(return_value=None)),
        patch(
            "nodes.uni_info.fetch_url_content",
            new=AsyncMock(return_value=MagicMock(success=False, content="", url="")),
        ),
        patch("nodes.uni_info._load_uni_info_cache", return_value=cached_uni_info),
        patch("nodes.uni_info._save_uni_info_cache", return_value=None),
    ):
        mock_llm.ainvoke = fake_llm_ainvoke
        mock_planner.classify_and_generate_queries = planner_mock
        result = await uni_info_node(state)

    return result, planner_mock, captured, ddg_mock


# ---------------------------------------------------------------------------
# Requirement 5.1 — QueryPlanner doğru argümanlarla çağrılır
# ---------------------------------------------------------------------------

def test_planner_called_with_correct_args():
    """uni_info_node, classify_and_generate_queries'i user_text + uni + mode='uni' ile çağırmalı."""
    uni = "Ankara Üniversitesi"
    user_text = "Ankara Üniversitesi hakkında genel bilgi ver"
    state = _make_state(uni, user_text)

    _, planner_mock, _, _ = asyncio.run(_run_node(state, _GENERAL_PLAN))

    planner_mock.assert_awaited_once_with(
        user_text=user_text,
        entity_name=uni,
        mode="uni",
    )


# ---------------------------------------------------------------------------
# Requirement 5.2 — plan.queries web araması için kullanılır
# ---------------------------------------------------------------------------

def test_plan_queries_passed_to_web_search():
    """Web aramasına giden sorgu listesi birebir QueryPlan.queries olmalı."""
    state = _make_state("Ankara Üniversitesi", "Ankara Üniversitesi nasıl bir okul")

    _, _, _, ddg_mock = asyncio.run(_run_node(state, _GENERAL_PLAN))

    ddg_mock.assert_awaited_once()
    called_queries = ddg_mock.await_args.args[0]
    assert called_queries == _GENERAL_PLAN.queries


# ---------------------------------------------------------------------------
# Requirement 5.5 — human_content sinyali is_specific'e göre belirlenir
# ---------------------------------------------------------------------------

def test_specific_plan_puts_spesifik_signal_in_human_content():
    """is_specific=True → human_content 'SPESİFİK' sinyalini içermeli."""
    state = _make_state("Ankara Üniversitesi", "Ankara Üniversitesi yurt imkânları nasıl")

    _, _, captured, _ = asyncio.run(_run_node(state, _SPECIFIC_PLAN))

    assert captured, "LLM'e HumanMessage gönderilmedi"
    assert "SPESİFİK" in captured[0]
    assert "Soru tipi: SPESİFİK — sadece sorulan konuyu cevapla, genel üniversite analizi yapma" in captured[0]


def test_general_plan_puts_genel_signal_in_human_content():
    """is_specific=False → human_content 'GENEL' sinyalini içermeli, 'SPESİFİK' içermemeli."""
    state = _make_state("Ankara Üniversitesi", "Ankara Üniversitesi hakkında bilgi ver")

    _, _, captured, _ = asyncio.run(_run_node(state, _GENERAL_PLAN))

    assert captured, "LLM'e HumanMessage gönderilmedi"
    assert "Soru tipi: GENEL — tam üniversite analizi yap" in captured[0]
    assert "SPESİFİK" not in captured[0]


# ---------------------------------------------------------------------------
# Requirement 5.4 — is_specific=False → üniversite Redis cache kontrolü sürer
# ---------------------------------------------------------------------------

def test_general_plan_returns_cached_uni_info_without_search():
    """is_specific=False ve cache doluysa cache'teki yanıt dönmeli; arama/LLM çağrılmamalı."""
    state = _make_state("Ankara Üniversitesi", "Ankara Üniversitesi hakkında bilgi")

    result, _, captured, ddg_mock = asyncio.run(
        _run_node(state, _GENERAL_PLAN, cached_uni_info="Önbellekteki analiz")
    )

    assert result["messages"][0].content == "Önbellekteki analiz"
    ddg_mock.assert_not_awaited()
    assert not captured  # LLM synthesis çağrılmadı


def test_specific_plan_skips_uni_cache():
    """is_specific=True → üniversite cache'i hiç okunmamalı, arama yapılmalı."""
    state = _make_state("Ankara Üniversitesi", "Ankara Üniversitesi yurt imkânları")

    result, _, _, ddg_mock = asyncio.run(
        _run_node(state, _SPECIFIC_PLAN, cached_uni_info="Önbellekteki analiz")
    )

    # Cache dolu olmasına rağmen spesifik soruda kullanılmaz
    assert result["messages"][0].content != "Önbellekteki analiz"
    ddg_mock.assert_awaited_once()


# ---------------------------------------------------------------------------
# Requirement 5.7 — planner hatası / boş sorgu listesi → hata mesajı
# ---------------------------------------------------------------------------

_UNI_ERROR_MESSAGE = "Bu üniversite için şu an arama yapamıyorum, lütfen tekrar dene."


def test_empty_queries_returns_error_message():
    """plan.queries boşsa node hata mesajı dönmeli, arama yapmamalı."""
    empty_plan = QueryPlan(is_specific=False, queries=[], source="fallback")
    state = _make_state("Ankara Üniversitesi", "Ankara Üniversitesi hakkında bilgi")

    result, _, _, ddg_mock = asyncio.run(_run_node(state, empty_plan))

    assert isinstance(result["messages"][0], AIMessage)
    assert result["messages"][0].content == _UNI_ERROR_MESSAGE
    ddg_mock.assert_not_awaited()


def test_planner_exception_returns_error_message():
    """classify_and_generate_queries exception fırlatırsa (teorik) hata mesajı dönmeli."""
    state = _make_state("Ankara Üniversitesi", "Ankara Üniversitesi hakkında bilgi")

    result, _, _, ddg_mock = asyncio.run(
        _run_node(state, None, planner_side_effect=RuntimeError("LLM down"))
    )

    assert result["messages"][0].content == _UNI_ERROR_MESSAGE
    ddg_mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# Üniversite adı bulunamazsa planner hiç çağrılmaz
# ---------------------------------------------------------------------------

def test_no_uni_detected_skips_planner():
    """Üniversite adı tespit edilemezse QueryPlanner çağrılmadan açıklama mesajı döner."""
    state = {
        "messages": [HumanMessage(content="bana bilgi ver")],
        "ner_context": {},
        "iteration_count": 0,
    }

    with patch("nodes.uni_info._extract_uni_from_text", return_value=None):
        result, planner_mock, _, _ = asyncio.run(_run_node(state, _GENERAL_PLAN))

    planner_mock.assert_not_awaited()
    assert "Hangi üniversite" in result["messages"][0].content
