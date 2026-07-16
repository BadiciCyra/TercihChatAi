"""
Unit tests for ai/utils/query_planner.py

Tests cover:
  - QueryPlan dataclass fields (Task 1)
  - _fallback method (Task 2.1)
  - _validate_queries method (Task 2.3)
  - _cache_key method (Task 3.1)
  - _build_prompt method (Task 3.3)
  - classify_and_generate_queries (Task 4.1) — happy path, fallback paths
"""

import asyncio
import json
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Minimal stubs needed before importing query_planner
# ---------------------------------------------------------------------------

# Stub 'models' module so the singleton import at module bottom doesn't fail
_models_mod = types.ModuleType("models")
_models_mod.llm_responder = MagicMock()
sys.modules.setdefault("models", _models_mod)

# Stub 'utils.redis_cache' module
_redis_cache_mod = types.ModuleType("utils.redis_cache")
_redis_cache_mod._ents_cache = None
sys.modules.setdefault("utils.redis_cache", _redis_cache_mod)
sys.modules.setdefault("utils", types.ModuleType("utils"))

# Now import the classes under test
from utils.query_planner import QueryPlan, QueryPlanner  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


def _make_planner(llm_response=None, llm_exception=None, cache=None):
    """Create a QueryPlanner with a mocked LLM and optional mock cache.

    The implementation calls self._llm_responder.ainvoke(prompt), so we must
    configure mock_llm.ainvoke rather than mock_llm itself.
    """
    mock_llm = MagicMock()
    if llm_exception:
        mock_llm.ainvoke = AsyncMock(side_effect=llm_exception)
    elif llm_response is not None:
        response_obj = MagicMock()
        response_obj.content = llm_response
        mock_llm.ainvoke = AsyncMock(return_value=response_obj)
    else:
        mock_llm.ainvoke = AsyncMock(side_effect=RuntimeError("no llm response configured"))
    return QueryPlanner(llm_responder=mock_llm, cache=cache)


# ===========================================================================
# Task 1 — QueryPlan dataclass
# ===========================================================================

class TestQueryPlanDataclass:
    def test_fields_exist(self):
        plan = QueryPlan(is_specific=True, queries=["sorgu1", "sorgu2", "sorgu3"], source="llm")
        assert plan.is_specific is True
        assert plan.queries == ["sorgu1", "sorgu2", "sorgu3"]
        assert plan.source == "llm"

    def test_fallback_source(self):
        plan = QueryPlan(is_specific=False, queries=["q1", "q2", "q3"], source="fallback")
        assert plan.source == "fallback"
        assert plan.is_specific is False

    def test_is_specific_false(self):
        plan = QueryPlan(is_specific=False, queries=["a", "b", "c"], source="llm")
        assert plan.is_specific is False


# ===========================================================================
# Task 2.1 — _fallback
# ===========================================================================

class TestFallback:
    def _planner(self):
        return QueryPlanner(llm_responder=MagicMock(), cache=None)

    def test_uni_mode_returns_4_queries(self):
        plan = self._planner()._fallback("Ankara Üniversitesi", "uni", "test")
        assert plan.source == "fallback"
        assert plan.is_specific is False
        assert len(plan.queries) == 4

    def test_uni_mode_queries_contain_entity(self):
        entity = "Boğaziçi Üniversitesi"
        plan = self._planner()._fallback(entity, "uni", "test")
        for q in plan.queries:
            assert entity in q, f"entity_name missing in query: {q!r}"

    def test_career_mode_returns_exactly_3_queries(self):
        plan = self._planner()._fallback("Bilgisayar Mühendisliği", "career", "test")
        assert plan.source == "fallback"
        assert plan.is_specific is False
        assert len(plan.queries) == 3

    def test_career_mode_queries_contain_entity(self):
        entity = "Elektrik Elektronik Mühendisliği"
        plan = self._planner()._fallback(entity, "career", "test")
        for q in plan.queries:
            assert entity in q, f"entity_name missing in query: {q!r}"

    def test_unknown_mode_returns_single_generic_query(self):
        entity = "Test Varlığı"
        plan = self._planner()._fallback(entity, "unknown", "test")
        assert plan.source == "fallback"
        assert plan.is_specific is False
        assert len(plan.queries) == 1
        assert entity in plan.queries[0]

    def test_fallback_logs_message(self, capsys):
        self._planner()._fallback("X", "uni", "bazı hata")
        captured = capsys.readouterr()
        assert "[QUERY_PLANNER]" in captured.out
        assert "bazı hata" in captured.out


# ===========================================================================
# Task 2.3 — _validate_queries
# ===========================================================================

class TestValidateQueries:
    def _planner(self):
        return QueryPlanner(llm_responder=MagicMock(), cache=None)

    # -- Type / length checks --

    def test_non_list_returns_none(self):
        assert self._planner()._validate_queries("not a list", "X") is None

    def test_less_than_3_items_returns_none(self):
        assert self._planner()._validate_queries(["q1", "q2"], "X") is None

    def test_more_than_5_items_returns_none(self):
        queries = [f"sorgu {i}" for i in range(6)]
        assert self._planner()._validate_queries(queries, "X") is None

    def test_exactly_3_items_passes(self):
        entity = "Test"
        result = self._planner()._validate_queries([f"{entity} a", f"{entity} b", f"{entity} c"], entity)
        assert result is not None
        assert len(result) == 3

    def test_exactly_5_items_passes(self):
        entity = "Test"
        queries = [f"{entity} sorgu {i}" for i in range(5)]
        result = self._planner()._validate_queries(queries, entity)
        assert result is not None
        assert len(result) == 5

    # -- Empty string filtering --

    def test_empty_strings_filtered_out(self):
        entity = "Uni"
        queries = [f"{entity} a", "", f"{entity} b", "   ", f"{entity} c"]
        result = self._planner()._validate_queries(queries, entity)
        # After filtering we have 3 non-empty strings
        assert result is not None
        assert all(q.strip() for q in result)

    def test_too_many_empty_strings_cause_none_after_dedup(self):
        # 5 items but after filtering empty strings only 2 remain → None
        queries = ["q1", "q2", "", "", ""]
        # This will fail step 1 (needs entity) — let's use valid form first
        # Actually step 1 checks raw list length first (5 items), so passes step 1
        # After filtering: ["q1", "q2"] → len 2, but dedup check is len < 3 → None
        entity = "E"
        result = self._planner()._validate_queries(queries, entity)
        # After filtering: ["E q1", "E q2"] → dedup → 2 → None
        assert result is None

    # -- entity_name prepending --

    def test_missing_entity_name_prepended(self):
        entity = "İTÜ"
        queries = ["bilgisayar bölümü bilgi", "genel bilgi alma", "kampüs hakkında"]
        result = self._planner()._validate_queries(queries, entity)
        assert result is not None
        for q in result:
            assert entity.lower() in q.lower(), f"entity_name missing in: {q!r}"

    def test_present_entity_name_not_double_prepended(self):
        entity = "ODTÜ"
        queries = [f"{entity} sorgu a", f"{entity} sorgu b", f"{entity} sorgu c"]
        result = self._planner()._validate_queries(queries, entity)
        assert result is not None
        # Should not double prepend
        for q in result:
            assert not q.startswith(f"{entity} {entity}"), f"Double prepend in: {q!r}"

    def test_case_insensitive_entity_check(self):
        entity = "ODTÜ"
        queries = ["odtü sorgu a", "Odtü sorgu b", "ODTÜ sorgu c"]
        result = self._planner()._validate_queries(queries, entity)
        assert result is not None
        assert len(result) == 3

    # -- Truncation --

    def test_queries_truncated_to_500_chars(self):
        entity = "Uni"
        long_query = entity + " " + ("x" * 600)
        queries = [long_query, f"{entity} sorgu b", f"{entity} sorgu c"]
        result = self._planner()._validate_queries(queries, entity)
        assert result is not None
        for q in result:
            assert len(q) <= 500

    # -- Deduplication --

    def test_case_insensitive_dedup(self):
        entity = "Test"
        queries = [f"{entity} SORGU A", f"{entity} sorgu a", f"{entity} sorgu b", f"{entity} sorgu c"]
        result = self._planner()._validate_queries(queries, entity)
        # After dedup: 3 unique queries
        assert result is not None
        lower_results = [q.lower() for q in result]
        assert len(lower_results) == len(set(lower_results))

    def test_dedup_resulting_in_less_than_3_returns_none(self):
        entity = "Test"
        # 3 items but 2 are duplicates → after dedup only 2 unique
        queries = [f"{entity} a", f"{entity} a", f"{entity} b"]
        result = self._planner()._validate_queries(queries, entity)
        # After dedup: 2 items → None
        assert result is None


# ===========================================================================
# Task 3.1 — _cache_key
# ===========================================================================

class TestCacheKey:
    def _planner(self):
        return QueryPlanner(llm_responder=MagicMock(), cache=None)

    def test_format(self):
        key = self._planner()._cache_key("soru", "Uni", "uni")
        assert key.startswith("ai:query_plan:uni:")
        assert len(key) == len("ai:query_plan:uni:") + 16

    def test_deterministic(self):
        p = self._planner()
        k1 = p._cache_key("merhaba", "İTÜ", "uni")
        k2 = p._cache_key("merhaba", "İTÜ", "uni")
        assert k1 == k2

    def test_different_user_text_different_key(self):
        p = self._planner()
        k1 = p._cache_key("soru A", "İTÜ", "uni")
        k2 = p._cache_key("soru B", "İTÜ", "uni")
        assert k1 != k2

    def test_different_entity_different_key(self):
        p = self._planner()
        k1 = p._cache_key("soru", "İTÜ", "uni")
        k2 = p._cache_key("soru", "ODTÜ", "uni")
        assert k1 != k2

    def test_different_mode_different_key(self):
        p = self._planner()
        k1 = p._cache_key("soru", "İTÜ", "uni")
        k2 = p._cache_key("soru", "İTÜ", "career")
        assert k1 != k2

    def test_career_mode_prefix(self):
        key = self._planner()._cache_key("soru", "Hukuk", "career")
        assert key.startswith("ai:query_plan:career:")


# ===========================================================================
# Task 3.3 — _build_prompt
# ===========================================================================

class TestBuildPrompt:
    def _planner(self):
        return QueryPlanner(llm_responder=MagicMock(), cache=None)

    def test_contains_user_text(self):
        prompt = self._planner()._build_prompt("test sorusu", "TestUni", "uni")
        assert "test sorusu" in prompt

    def test_contains_entity_name(self):
        prompt = self._planner()._build_prompt("soru", "İstanbul Üniversitesi", "uni")
        assert "İstanbul Üniversitesi" in prompt

    def test_uni_mode_context_hints(self):
        prompt = self._planner()._build_prompt("soru", "Uni", "uni")
        assert "burs" in prompt.lower() or "kampüs" in prompt.lower()

    def test_career_mode_context_hints(self):
        prompt = self._planner()._build_prompt("soru", "Hukuk", "career")
        assert "müfredat" in prompt.lower() or "kariyer" in prompt.lower()

    def test_json_schema_in_prompt(self):
        prompt = self._planner()._build_prompt("soru", "Uni", "uni")
        assert "is_specific" in prompt
        assert "queries" in prompt

    def test_turkish_language_instruction(self):
        prompt = self._planner()._build_prompt("soru", "Uni", "uni")
        assert "Türkçe" in prompt or "türkçe" in prompt.lower()


# ===========================================================================
# Task 4.1 — classify_and_generate_queries
# ===========================================================================

class TestClassifyAndGenerateQueries:

    # -- Happy path: valid LLM JSON response --

    def test_happy_path_llm_source(self):
        llm_json = json.dumps({
            "is_specific": True,
            "queries": [
                "İTÜ bilgisayar bölümü staj",
                "İTÜ yazılım geliştirme kariyer",
                "İTÜ bilgisayar mühendisliği mezun maaş",
            ]
        })
        planner = _make_planner(llm_response=llm_json)
        plan = _run(planner.classify_and_generate_queries("İTÜ staj sorusu", "İTÜ", "uni"))
        assert isinstance(plan, QueryPlan)
        assert plan.source == "llm"
        assert plan.is_specific is True
        assert len(plan.queries) == 3

    def test_happy_path_career_mode(self):
        llm_json = json.dumps({
            "is_specific": False,
            "queries": [
                "Hukuk müfredat bilgi",
                "Hukuk mezun kariyer",
                "Hukuk istihdam YÖK",
            ]
        })
        planner = _make_planner(llm_response=llm_json)
        plan = _run(planner.classify_and_generate_queries("hukuk hakkında", "Hukuk", "career"))
        assert plan.source == "llm"
        assert plan.is_specific is False

    # -- Invalid mode → fallback without LLM call --

    def test_invalid_mode_returns_fallback(self):
        mock_llm = AsyncMock()
        planner = QueryPlanner(llm_responder=mock_llm, cache=None)
        plan = _run(planner.classify_and_generate_queries("soru", "Uni", "invalid_mode"))
        assert plan.source == "fallback"
        mock_llm.ainvoke.assert_not_called()

    def test_invalid_mode_generic_query_returned(self):
        planner = _make_planner()
        plan = _run(planner.classify_and_generate_queries("soru", "TestEntity", "xyz"))
        assert "TestEntity" in plan.queries[0]

    # -- LLM exception → fallback --

    def test_llm_exception_returns_fallback(self):
        planner = _make_planner(llm_exception=RuntimeError("ağ hatası"))
        plan = _run(planner.classify_and_generate_queries("soru", "Uni", "uni"))
        assert plan.source == "fallback"
        assert isinstance(plan, QueryPlan)

    def test_llm_timeout_exception_returns_fallback(self):
        planner = _make_planner(llm_exception=TimeoutError("timeout"))
        plan = _run(planner.classify_and_generate_queries("soru", "Uni", "career"))
        assert plan.source == "fallback"

    # -- Invalid JSON from LLM → fallback --

    def test_invalid_json_returns_fallback(self):
        planner = _make_planner(llm_response="bu geçerli bir json değil")
        plan = _run(planner.classify_and_generate_queries("soru", "Uni", "uni"))
        assert plan.source == "fallback"

    def test_missing_queries_field_returns_fallback(self):
        llm_json = json.dumps({"is_specific": True})
        planner = _make_planner(llm_response=llm_json)
        plan = _run(planner.classify_and_generate_queries("soru", "Uni", "uni"))
        assert plan.source == "fallback"

    def test_missing_is_specific_field_returns_fallback(self):
        llm_json = json.dumps({"queries": ["a", "b", "c"]})
        planner = _make_planner(llm_response=llm_json)
        plan = _run(planner.classify_and_generate_queries("soru", "Uni", "uni"))
        assert plan.source == "fallback"

    # -- Validation failures → fallback --

    def test_too_few_queries_returns_fallback(self):
        llm_json = json.dumps({"is_specific": True, "queries": ["q1", "q2"]})
        planner = _make_planner(llm_response=llm_json)
        plan = _run(planner.classify_and_generate_queries("soru", "Uni", "uni"))
        assert plan.source == "fallback"

    def test_too_many_queries_returns_fallback(self):
        queries = [f"sorgu {i}" for i in range(6)]
        llm_json = json.dumps({"is_specific": True, "queries": queries})
        planner = _make_planner(llm_response=llm_json)
        plan = _run(planner.classify_and_generate_queries("soru", "Uni", "uni"))
        assert plan.source == "fallback"

    # -- Result invariants (queries list) --

    def test_result_always_has_queries(self):
        """Every returned QueryPlan must have at least 1 non-empty query."""
        planner = _make_planner(llm_exception=RuntimeError("err"))
        plan = _run(planner.classify_and_generate_queries("soru", "Uni", "uni"))
        assert len(plan.queries) >= 1
        for q in plan.queries:
            assert q.strip(), f"Empty query found: {q!r}"

    def test_result_queries_max_5(self):
        llm_json = json.dumps({
            "is_specific": False,
            "queries": ["Uni a", "Uni b", "Uni c", "Uni d", "Uni e"],
        })
        planner = _make_planner(llm_response=llm_json)
        plan = _run(planner.classify_and_generate_queries("soru", "Uni", "uni"))
        assert len(plan.queries) <= 5

    # -- Never raises --

    def test_never_raises_on_valid_inputs(self):
        planner = _make_planner(llm_exception=Exception("catastrophic"))
        try:
            plan = _run(planner.classify_and_generate_queries("soru", "Uni", "uni"))
        except Exception as exc:
            pytest.fail(f"classify_and_generate_queries raised unexpectedly: {exc}")
        assert isinstance(plan, QueryPlan)


# ===========================================================================
# Cache integration tests
# ===========================================================================

class TestCacheIntegration:

    def _make_mock_cache(self, get_value=None, get_exception=None):
        mock_cache = MagicMock()
        if get_exception:
            mock_cache.get.side_effect = get_exception
        else:
            mock_cache.get.return_value = get_value
        mock_cache.setex = MagicMock()
        return mock_cache

    def test_cache_hit_returns_plan_without_llm(self):
        cached_data = json.dumps({
            "is_specific": True,
            "queries": ["Uni q1", "Uni q2", "Uni q3"],
            "source": "llm",
        }).encode()
        mock_cache = self._make_mock_cache(get_value=cached_data)
        mock_llm = AsyncMock()
        planner = QueryPlanner(llm_responder=mock_llm, cache=mock_cache)
        plan = _run(planner.classify_and_generate_queries("soru", "Uni", "uni"))
        assert plan.source == "llm"
        assert plan.queries == ["Uni q1", "Uni q2", "Uni q3"]
        mock_llm.ainvoke.assert_not_called()

    def test_cache_miss_calls_llm(self):
        mock_cache = self._make_mock_cache(get_value=None)
        llm_json = json.dumps({
            "is_specific": False,
            "queries": ["Uni a", "Uni b", "Uni c"],
        })
        response_obj = MagicMock()
        response_obj.content = llm_json
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=response_obj)
        planner = QueryPlanner(llm_responder=mock_llm, cache=mock_cache)
        plan = _run(planner.classify_and_generate_queries("soru", "Uni", "uni"))
        assert plan.source == "llm"
        mock_llm.ainvoke.assert_called_once()

    def test_successful_result_written_to_cache(self):
        mock_cache = self._make_mock_cache(get_value=None)
        llm_json = json.dumps({
            "is_specific": True,
            "queries": ["Uni q1", "Uni q2", "Uni q3"],
        })
        response_obj = MagicMock()
        response_obj.content = llm_json
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=response_obj)
        planner = QueryPlanner(llm_responder=mock_llm, cache=mock_cache)
        _run(planner.classify_and_generate_queries("soru", "Uni", "uni"))
        mock_cache.setex.assert_called_once()
        args = mock_cache.setex.call_args
        assert args[0][1] == 3600  # TTL

    def test_corrupt_cache_falls_through_to_llm(self):
        mock_cache = self._make_mock_cache(get_value=b"bu gecersiz json{{")
        llm_json = json.dumps({
            "is_specific": False,
            "queries": ["Uni a", "Uni b", "Uni c"],
        })
        response_obj = MagicMock()
        response_obj.content = llm_json
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=response_obj)
        planner = QueryPlanner(llm_responder=mock_llm, cache=mock_cache)
        plan = _run(planner.classify_and_generate_queries("soru", "Uni", "uni"))
        assert plan.source == "llm"
        mock_llm.ainvoke.assert_called_once()

    def test_cache_exception_falls_through_silently(self):
        mock_cache = self._make_mock_cache(get_exception=ConnectionError("redis down"))
        llm_json = json.dumps({
            "is_specific": True,
            "queries": ["Uni a", "Uni b", "Uni c"],
        })
        response_obj = MagicMock()
        response_obj.content = llm_json
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=response_obj)
        planner = QueryPlanner(llm_responder=mock_llm, cache=mock_cache)
        plan = _run(planner.classify_and_generate_queries("soru", "Uni", "uni"))
        # Should not raise; should proceed to LLM
        assert isinstance(plan, QueryPlan)

    def test_cache_write_exception_does_not_affect_result(self):
        mock_cache = self._make_mock_cache(get_value=None)
        mock_cache.setex.side_effect = ConnectionError("redis write failed")
        llm_json = json.dumps({
            "is_specific": True,
            "queries": ["Uni a", "Uni b", "Uni c"],
        })
        response_obj = MagicMock()
        response_obj.content = llm_json
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=response_obj)
        planner = QueryPlanner(llm_responder=mock_llm, cache=mock_cache)
        plan = _run(planner.classify_and_generate_queries("soru", "Uni", "uni"))
        assert plan.source == "llm"
        assert plan.queries == ["Uni a", "Uni b", "Uni c"]

    def test_none_cache_skips_cache_entirely(self):
        """When cache=None, no cache interaction happens — LLM is called directly."""
        llm_json = json.dumps({
            "is_specific": False,
            "queries": ["Uni a", "Uni b", "Uni c"],
        })
        response_obj = MagicMock()
        response_obj.content = llm_json
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=response_obj)
        planner = QueryPlanner(llm_responder=mock_llm, cache=None)
        plan = _run(planner.classify_and_generate_queries("soru", "Uni", "uni"))
        assert plan.source == "llm"
        mock_llm.ainvoke.assert_called_once()
