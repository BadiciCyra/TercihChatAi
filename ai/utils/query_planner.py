"""
ai/utils/query_planner.py

LLM tabanlı dinamik sınıflandırma ve sorgu üretim bileşeni.
Hem uni_info_node hem de career_info_node tarafından paylaşılır.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal


# ---------------------------------------------------------------------------
# QueryPlan veri yapısı
# ---------------------------------------------------------------------------

@dataclass
class QueryPlan:
    """Sınıflandırma ve sorgu sonuçlarını taşıyan veri yapısı.

    Alanlar:
        is_specific: Kullanıcının sorusunun spesifik (True) veya
                     genel (False) olduğunu belirtir.
        queries:     Web araması için hazır sorgu listesi.
                     1 ≤ len ≤ 5, her eleman non-empty, her eleman ≤ 500 karakter.
        source:      Sonucun LLM'den mi ("llm") yoksa fallback'ten mi
                     ("fallback") geldiğini belirtir.
    """

    is_specific: bool
    queries: list[str]          # 1 ≤ len ≤ 5, her eleman non-empty, ≤ 500 karakter
    source: Literal["llm", "fallback"]


# ---------------------------------------------------------------------------
# QueryPlanner sınıfı
# ---------------------------------------------------------------------------

class QueryPlanner:
    """Kullanıcı metnini ve varlık adını alarak QueryPlan döndüren bileşen.

    Kullanım:
        plan = await query_planner.classify_and_generate_queries(
            user_text="İTÜ'nün bursları hakkında bilgi ver",
            entity_name="İstanbul Teknik Üniversitesi",
            mode="uni",
        )
    """

    def __init__(
        self,
        llm_responder,   # RotatingGeminiLLM / FallbackLLM instance (.ainvoke uyumlu)
        cache=None,      # Redis client veya None (_ents_cache from utils.redis_cache)
    ) -> None:
        self._llm_responder = llm_responder
        self._cache = cache

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def classify_and_generate_queries(
        self,
        user_text: str,
        entity_name: str,
        mode: Literal["uni", "career"],
    ) -> QueryPlan:
        """Kullanıcı metnini sınıflandırır ve web araması sorguları üretir.

        Args:
            user_text:    Kullanıcının ham mesajı.
            entity_name:  Araştırılan üniversite veya bölüm adı.
            mode:         "uni" (üniversite) veya "career" (kariyer/bölüm).

        Returns:
            QueryPlan nesnesi. Asla exception fırlatmaz.
        """
        import json as _json

        # Step 1: Validate mode — invalid modes go straight to fallback, no LLM call
        if mode not in {"uni", "career"}:
            return self._fallback(entity_name, mode, f"geçersiz mod: {mode!r}")

        # Step 2: Try to read from Redis cache
        cache_key = self._cache_key(user_text, entity_name, mode)
        if self._cache is not None:
            try:
                cached = self._cache.get(cache_key)
                if cached:
                    data = _json.loads(cached)
                    if "is_specific" in data and "queries" in data:
                        return QueryPlan(
                            is_specific=data["is_specific"],
                            queries=data["queries"],
                            source=data.get("source", "llm"),
                        )
            except Exception:
                pass  # Silent — corrupt/missing cache, continue to LLM

        # Step 3: Build prompt and call LLM
        prompt = self._build_prompt(user_text, entity_name, mode)
        try:
            response = await self._llm_responder.ainvoke(prompt)
            raw = response.content if hasattr(response, "content") else str(response)
        except Exception as exc:
            return self._fallback(entity_name, mode, str(exc))

        # Step 4: Parse LLM response
        parsed = self._parse_llm_response(raw)
        if parsed is None:
            return self._fallback(entity_name, mode, "parse error")

        # Step 5: Validate and normalize queries
        raw_queries = parsed.get("queries", [])
        validated = self._validate_queries(raw_queries, entity_name)
        if validated is None:
            return self._fallback(entity_name, mode, "validation error")

        # Step 6: Build the successful QueryPlan
        plan = QueryPlan(
            is_specific=bool(parsed.get("is_specific", False)),
            queries=validated,
            source="llm",
        )

        # Step 7: Write to Redis cache (silent on failure)
        try:
            if self._cache is not None:
                self._cache.setex(
                    cache_key,
                    3600,
                    _json.dumps({
                        "is_specific": plan.is_specific,
                        "queries": plan.queries,
                        "source": plan.source,
                    }),
                )
        except Exception:
            pass  # Silent — cache write failure doesn't affect the result

        return plan

    # ------------------------------------------------------------------
    # Internal helpers (imzalar — gövdeler sonraki task'larda doldurulur)
    # ------------------------------------------------------------------

    def _cache_key(self, user_text: str, entity_name: str, mode: str) -> str:
        """Deterministik Redis cache anahtarı üretir.

        Format: "ai:query_plan:{mode}:{sha256(user_text+entity_name)[:16]}"
        """
        digest = hashlib.sha256((user_text + entity_name).encode()).hexdigest()[:16]
        return f"ai:query_plan:{mode}:{digest}"

    def _build_prompt(self, user_text: str, entity_name: str, mode: str) -> str:
        """LLM'e gönderilecek Türkçe prompt'u oluşturur.

        Prompt; user_text, entity_name ve mode'a özgü bağlam talimatlarını içerir.
        LLM'den JSON formatında yanıt talep eder:
            {"is_specific": true, "queries": ["sorgu1", "sorgu2", "sorgu3"]}
        """
        if mode == "uni":
            context_hint = (
                "kampüs olanakları, burs ve harç bilgisi, "
                "taban puan ve kontenjan, öğrenci yorumları, akademik kadro"
            )
        else:  # career
            context_hint = (
                "müfredat ve ders içerikleri, "
                "mezun maaş ve kariyer yolları, YÖK istihdam istatistikleri"
            )

        return f"""Sen bir Türkçe web arama sorgusu uzmanısın.

Kullanıcı sorusu: "{user_text}"
Araştırılan varlık: "{entity_name}"
Araştırma bağlamı: {context_hint}

Görevin:
1. Kullanıcı sorusunun SPESİFİK mi (belirli bir konu/durum sorgusu) yoksa GENEL mi (genel bilgi/analiz) olduğunu belirle.
2. Bu soruyu yanıtlamak için kullanılabilecek 3 ile 5 arasında Türkçe web arama sorgusu üret.
3. Her sorgu "{entity_name}" ifadesini içermeli.
4. Sorgular birbirinden farklı olmalı ve farklı bilgi kaynaklarını hedeflemelidir.

SADECE aşağıdaki JSON formatında yanıt ver, başka hiçbir şey yazma:
{{"is_specific": true, "queries": ["sorgu1", "sorgu2", "sorgu3"]}}

Kural: is_specific=true ise kullanıcı belirli bir konuyu soruyor, is_specific=false ise genel bilgi/analiz istiyor.
"""

    def _parse_llm_response(self, raw: str) -> dict | None:
        """Ham LLM yanıtını ayrıştırır.

        Returns:
            Geçerli bir dict (is_specific + queries içeren) veya None.
            JSON parse hatası ya da eksik alan durumunda None döner.
        """
        import json
        import re

        if not raw or not isinstance(raw, str):
            return None

        # Try to extract JSON object from the raw string (LLM may add extra text)
        try:
            data = json.loads(raw.strip())
        except (json.JSONDecodeError, ValueError):
            # Try to find a JSON object inside the response (e.g. wrapped in markdown)
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if not match:
                return None
            try:
                data = json.loads(match.group(0))
            except (json.JSONDecodeError, ValueError):
                return None

        # Must have both required fields
        if "is_specific" not in data or "queries" not in data:
            return None

        return data

    def _validate_queries(self, queries: list, entity_name: str) -> list[str] | None:
        """Sorgu listesini doğrular ve normalize eder.

        Adımlar:
            1. queries bir list değilse veya len < 3 ya da len > 5 ise → None
            2. Boş string'leri filtrele
            3. entity_name içermeyen sorguların başına entity_name+" " ekle
            4. Her sorguyu 500 karakterle kırp
            5. Case-insensitive deduplication
            6. Dedup sonrası len < 3 ise → None

        Returns:
            Normalize edilmiş list[str] veya None (fallback tetikler).
        """
        # Step 1: type and length check (before filtering, so raw count is checked)
        if not isinstance(queries, list) or len(queries) < 3 or len(queries) > 5:
            return None

        # Step 2: filter empty strings
        queries = [q for q in queries if isinstance(q, str) and q.strip()]

        # Step 3: prepend entity_name if missing (case-insensitive)
        result = []
        for query in queries:
            if entity_name.lower() not in query.lower():
                query = entity_name + " " + query
            result.append(query)

        # Step 4: truncate each query to 500 characters
        result = [q[:500] for q in result]

        # Step 5: case-insensitive deduplication (preserve first occurrence)
        seen: set[str] = set()
        deduped: list[str] = []
        for q in result:
            key = q.lower()
            if key not in seen:
                seen.add(key)
                deduped.append(q)

        # Step 6: if fewer than 3 unique queries remain, signal fallback
        if len(deduped) < 3:
            return None

        return deduped

    def _fallback(self, entity_name: str, mode: str, reason: str) -> QueryPlan:
        """LLM/parse/validate hatası durumunda deterministik QueryPlan üretir.

        Log formatı: "[QUERY_PLANNER] ⚠️ Fallback — sebep: {reason}"

        mode="uni":    4 sabit sorgu şablonu, is_specific=False
        mode="career": 3 sabit sorgu şablonu, is_specific=False
        other:         1 genel sorgu, is_specific=False
        """
        print(f"[QUERY_PLANNER] ⚠️ Fallback — sebep: {reason}")

        if mode == "uni":
            queries = [
                f"{entity_name} genel bilgi akademik kadro",
                f"{entity_name} öğrenci yorumları",
                f"{entity_name} burs ücret 2024 2025",
                f"{entity_name} kariyer mezun iş imkânları",
            ]
            return QueryPlan(is_specific=False, queries=queries, source="fallback")

        elif mode == "career":
            queries = [
                f"{entity_name} bölümü müfredat ders içerikleri",
                f"{entity_name} mezunu kariyer maaş iş ilanları",
                f"{entity_name} istihdam oranı YÖK istatistik",
            ]
            return QueryPlan(is_specific=False, queries=queries, source="fallback")

        else:
            return QueryPlan(
                is_specific=False,
                queries=[f"{entity_name} hakkında bilgi"],
                source="fallback",
            )


# ---------------------------------------------------------------------------
# Modül düzeyinde singleton
# ---------------------------------------------------------------------------

from models import llm_responder as _llm_responder          # noqa: E402
from utils.redis_cache import _ents_cache as _redis_cache   # noqa: E402

query_planner = QueryPlanner(llm_responder=_llm_responder, cache=_redis_cache)
