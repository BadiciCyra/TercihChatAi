# Feature: llm-dynamic-query-generator — career_info_node QueryPlanner entegrasyonu
"""
Integration tests for career_info_node + QueryPlanner.

Validates: Requirements 6.1, 6.2, 6.3, 6.4, 6.6, 6.7

Eski sinyal listesi testleri (_detect_career_specificity, _COMPANY_SIGNALS vb.)
kaldırıldı; sınıflandırma artık QueryPlanner tarafından yapıldığı için burada
node'un QueryPlanner ile doğru konuştuğu doğrulanır.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage, AIMessage

from utils.query_planner import QueryPlan
from nodes.career_info import career_info_node
from prompts.career_info import _CAREER_INFO_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

_FAKE_DDG_RESULTS = [
    {"title": f"Sonuç {i}", "url": f"https://example.com/{i}", "body": f"İçerik {i}"}
    for i in range(6)
]

_GENERAL_PLAN = QueryPlan(
    is_specific=False,
    queries=[
        "Bilgisayar Mühendisliği bölümü müfredat ders içerikleri",
        "Bilgisayar Mühendisliği mezunu kariyer maaş iş ilanları",
        "Bilgisayar Mühendisliği istihdam oranı YÖK istatistik",
    ],
    source="llm",
)

_SPECIFIC_PLAN = QueryPlan(
    is_specific=True,
    queries=[
        "Bilgisayar Mühendisliği Google işe alım süreci",
        "Bilgisayar Mühendisliği mezunu Google maaş",
        "Bilgisayar Mühendisliği yurt dışı kariyer deneyimleri",
    ],
    source="llm",
)


def _make_state(user_text: str, dept: str | None = "Bilgisayar Mühendisliği") -> dict:
    return {
        "messages": [HumanMessage(content=user_text)],
        "ner_context": {"program": dept} if dept else {},
        "iteration_count": 0,
    }


async def _run_node(state: dict, plan, dept_extractable=True, planner_side_effect=None):
    """career_info_node'u tüm dış bağımlılıklar mock'lanmış şekilde çalıştırır.

    Returns:
        (result, planner_mock, captured_human_contents, ddg_mock, llm_called)
    """
    captured: list[str] = []

    fake_llm_response = MagicMock()
    fake_llm_response.content = "Detaylı kariyer analizi. " * 20

    llm_called = []

    async def fake_llm_ainvoke(messages, **kwargs):
        llm_called.append(True)
        for msg in messages:
            if type(msg).__name__ == "HumanMessage" and hasattr(msg, "content"):
                captured.append(msg.content)
        return fake_llm_response

    planner_mock = AsyncMock(return_value=plan)
    if planner_side_effect is not None:
        planner_mock.side_effect = planner_side_effect

    ddg_mock = AsyncMock(return_value=_FAKE_DDG_RESULTS)

    from contextlib import ExitStack

    with ExitStack() as stack:
        stack.enter_context(patch("nodes.career_info._ddg_quick", new=ddg_mock))
        mock_llm = stack.enter_context(patch("nodes.career_info.llm_responder"))
        mock_planner = stack.enter_context(patch("nodes.career_info.query_planner"))
        stack.enter_context(
            patch("nodes.career_info._extract_program_llm_fallback", new=AsyncMock(return_value=None))
        )
        stack.enter_context(
            patch(
                "nodes.career_info.fetch_url_content",
                new=AsyncMock(return_value=MagicMock(success=False, content="", url="")),
            )
        )
        if not dept_extractable:
            stack.enter_context(
                patch("nodes.career_info._extract_dept_from_text", return_value=None)
            )

        mock_llm.ainvoke = fake_llm_ainvoke
        mock_planner.classify_and_generate_queries = planner_mock
        result = await career_info_node(state)

    return result, planner_mock, captured, ddg_mock, llm_called


# ---------------------------------------------------------------------------
# Requirement 6.1 — QueryPlanner doğru argümanlarla çağrılır
# ---------------------------------------------------------------------------

def test_planner_called_with_correct_args():
    """career_info_node, classify_and_generate_queries'i user_text + dept + mode='career' ile çağırmalı."""
    user_text = "Bilgisayar Mühendisliği mezunu ne kadar kazanır"
    state = _make_state(user_text)

    _, planner_mock, _, _, _ = asyncio.run(_run_node(state, _GENERAL_PLAN))

    planner_mock.assert_awaited_once()
    kwargs = planner_mock.await_args.kwargs
    assert kwargs["user_text"] == user_text
    assert kwargs["entity_name"] == "Bilgisayar Mühendisliği"
    assert kwargs["mode"] == "career"


# ---------------------------------------------------------------------------
# Requirement 6.2 — plan.queries web araması için kullanılır
# ---------------------------------------------------------------------------

def test_plan_queries_passed_to_web_search():
    """Web aramasına giden sorgular birebir QueryPlan.queries olmalı."""
    state = _make_state("Bilgisayar Mühendisliği nasıl bir bölüm")

    _, _, _, ddg_mock, _ = asyncio.run(_run_node(state, _GENERAL_PLAN))

    searched = [call.args[0] for call in ddg_mock.await_args_list]
    assert searched == _GENERAL_PLAN.queries


# ---------------------------------------------------------------------------
# Requirement 6.3 / 6.4 — is_specific rota ve human_content sinyalini belirler
# ---------------------------------------------------------------------------

def test_specific_plan_puts_spesifik_signal_in_human_content():
    """is_specific=True → Yol C: human_content 'SPESİFİK' sinyalini içermeli."""
    state = _make_state("Bilgisayar Mühendisliği mezunu Google'da çalışabilir mi")

    result, _, captured, _, _ = asyncio.run(_run_node(state, _SPECIFIC_PLAN))

    assert captured, "LLM'e HumanMessage gönderilmedi"
    assert "Soru tipi: SPESİFİK — sadece sorulan konuyu cevapla, tam şablon doldurma" in captured[0]
    assert result.get("is_career_specific") is True


def test_general_plan_puts_genel_signal_in_human_content():
    """is_specific=False → Yol D: human_content 'GENEL' sinyalini içermeli."""
    state = _make_state("Bilgisayar Mühendisliği nedir")

    result, _, captured, _, _ = asyncio.run(_run_node(state, _GENERAL_PLAN))

    assert captured, "LLM'e HumanMessage gönderilmedi"
    assert "Soru tipi: GENEL — tam kariyer analizi yap" in captured[0]
    assert "SPESİFİK" not in captured[0]


# ---------------------------------------------------------------------------
# Requirement 6.6 / 6.7 — dept boşken planner çağrılmaz, açıklama mesajı döner
# ---------------------------------------------------------------------------

def test_empty_dept_skips_planner_and_returns_clarification():
    """dept tespit edilemezse QueryPlanner, arama ve LLM hiç çağrılmamalı."""
    state = _make_state("tavsiye eder misin", dept=None)

    result, planner_mock, _, ddg_mock, llm_called = asyncio.run(
        _run_node(state, _GENERAL_PLAN, dept_extractable=False)
    )

    planner_mock.assert_not_awaited()
    ddg_mock.assert_not_awaited()
    assert not llm_called

    out = result["messages"]
    assert len(out) == 1
    assert isinstance(out[0], AIMessage)
    assert "Hangi bölüm" in out[0].content


# ---------------------------------------------------------------------------
# Boş sorgu listesi / planner hatası → hata mesajı
# ---------------------------------------------------------------------------

def test_empty_queries_returns_error_message():
    """plan.queries boşsa arama yapılmadan hata mesajı dönmeli."""
    empty_plan = QueryPlan(is_specific=False, queries=[], source="fallback")
    state = _make_state("Bilgisayar Mühendisliği nedir")

    result, _, _, ddg_mock, _ = asyncio.run(_run_node(state, empty_plan))

    ddg_mock.assert_not_awaited()
    assert "Şu an web araması yapamıyorum" in result["messages"][0].content


def test_planner_exception_returns_error_message():
    """classify_and_generate_queries exception fırlatırsa (teorik) hata mesajı dönmeli."""
    state = _make_state("Bilgisayar Mühendisliği nedir")

    result, _, _, ddg_mock, _ = asyncio.run(
        _run_node(state, None, planner_side_effect=RuntimeError("LLM down"))
    )

    ddg_mock.assert_not_awaited()
    assert "Şu an web araması yapamıyorum" in result["messages"][0].content


# ===========================================================================
# Prompt template format selection tests (önceki feature'dan korunmuştur)
# ===========================================================================

def test_prompt_contains_specific_signal():
    """
    _CAREER_INFO_SYSTEM_PROMPT must contain the 'Soru tipi: SPESİFİK' signal
    so the LLM knows when to use the short/focused format.
    """
    assert "Soru tipi: SPESİFİK" in _CAREER_INFO_SYSTEM_PROMPT


def test_prompt_contains_general_signal():
    """
    _CAREER_INFO_SYSTEM_PROMPT must contain the 'Soru tipi: GENEL' signal
    so the LLM knows when to use the full template format.
    """
    assert "Soru tipi: GENEL" in _CAREER_INFO_SYSTEM_PROMPT


def test_prompt_specific_section_instructs_not_to_fill_template():
    """
    The specific-question section must explicitly instruct the LLM NOT to fill
    the full template (e.g., containing 'DOLDURMA' or an equivalent instruction).
    """
    assert "DOLDURMA" in _CAREER_INFO_SYSTEM_PROMPT


def test_prompt_general_section_references_full_template_sections():
    """
    The general-question section must reference the full template sections
    (Müfredat, Kariyer Yolları, İstihdam) so the LLM knows to use them.
    """
    for keyword in ("Müfredat", "Kariyer Yolları", "İstihdam"):
        assert keyword in _CAREER_INFO_SYSTEM_PROMPT, (
            f"Expected full-template section keyword '{keyword}' in "
            f"_CAREER_INFO_SYSTEM_PROMPT but it was not found."
        )


def test_format_selection_section_appears_before_template_sections():
    """
    The FORMAT SEÇİMİ section must appear before the detailed template sections
    so the LLM encounters format instructions first.
    """
    format_pos = _CAREER_INFO_SYSTEM_PROMPT.find("FORMAT SEÇİMİ")
    template_pos = _CAREER_INFO_SYSTEM_PROMPT.find("EN ÖNEMLİ KURAL")
    assert format_pos != -1, "FORMAT SEÇİMİ section not found in prompt."
    assert template_pos != -1, "EN ÖNEMLİ KURAL section not found in prompt."
    assert format_pos < template_pos
