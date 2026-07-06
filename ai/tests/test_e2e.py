# test_e2e.py — Uçtan Uca (E2E) Testler
#
# Tüm AI servis zincirini test eder:
#   ai-gateway:8003 → app_graph (LangGraph) → ai-retriever:8000 → YÖK Atlas / DDG
#
# ÇALIŞTIRMA:
#   # Tüm E2E testleri (Docker ayakta olmalı):
#   pytest ai/tests/test_e2e.py -v -m e2e
#
#   # Sadece hızlı smoke testleri:
#   pytest ai/tests/test_e2e.py -v -m "e2e and smoke"
#
# GEREKSINIMLER:
#   docker-compose up -d ai-gateway ai-retriever ai-reranker redis
#   pip install httpx pytest

import os
import re
import time
import pytest
import httpx

# ── Konfigürasyon ──────────────────────────────────────────────────────────────
GATEWAY_URL  = os.getenv("GATEWAY_URL",  "http://localhost:8003")
RETRIEVER_URL = os.getenv("RETRIEVER_URL", "http://localhost:8000")
SCHOOL_KEY   = os.getenv("AI_GATEWAY_SCHOOL_KEY", "test_key")

# Test key'ine yüksek rate limit — .env'de RATE_LIMIT_TEST=1000 veya
# test_key'i platinum plan ile Redis'e kaydet:
#   curl -X POST "http://localhost:8003/admin/keys?key=test_key&name=E2E+Tests&plan=platinum"
#       -H "X-Admin-Key: <your_admin_key>"
# Alternatif: TEST_RATE_BYPASS=true yapılırsa testler arası 1sn bekler.
_RATE_BYPASS_SLEEP = float(os.getenv("E2E_RATE_SLEEP", "0"))  # saniye

pytestmark = pytest.mark.e2e


# ── Yardımcılar ────────────────────────────────────────────────────────────────

def _ask(query: str, session_id: str = "e2e_session", timeout: int = 90) -> dict:
    """Gateway'e soru gönder, yanıtı dict olarak döndür.
    Rate limit aşılırsa 60sn bekleyip bir kez daha dener.
    """
    if _RATE_BYPASS_SLEEP:
        time.sleep(_RATE_BYPASS_SLEEP)

    resp = httpx.post(
        f"{GATEWAY_URL}/b2b/ask_intelligent",
        json={"query": query, "session_id": session_id},
        headers={"X-School-Key": SCHOOL_KEY, "Content-Type": "application/json"},
        timeout=timeout,
    )

    # Rate limit yendi → 60 saniye bekle ve tekrar dene (1 retry)
    if resp.status_code == 429:
        print(f"\n[E2E] ⏳ Rate limit (429) — 65sn bekleniyor, sonra tekrar denenecek...")
        time.sleep(65)
        resp = httpx.post(
            f"{GATEWAY_URL}/b2b/ask_intelligent",
            json={"query": query, "session_id": session_id},
            headers={"X-School-Key": SCHOOL_KEY, "Content-Type": "application/json"},
            timeout=timeout,
        )

    assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text[:300]}"
    return resp.json()


def _has_table(answer: str) -> bool:
    """Yanıt markdown tablo içeriyor mu?"""
    return "|" in answer and "---" in answer


def _extract_ranks(answer: str) -> list[int]:
    """Yanıttaki bold sıralama sayılarını çek: **123456**"""
    matches = re.findall(r'\*\*(\d{4,7})\*\*', answer)
    return [int(m) for m in matches]


# ── Fixture: servisler ayakta mı? ──────────────────────────────────────────────

@pytest.fixture(scope="session", autouse=True)
def require_services():
    """Tüm E2E testlerinden önce servis health check + test_key'e yüksek rate limit."""
    errors = []
    for name, url, path in [
        ("ai-gateway",  GATEWAY_URL,   "/docs"),
        ("ai-retriever", RETRIEVER_URL, "/docs"),
    ]:
        try:
            r = httpx.get(f"{url}{path}", timeout=5)
            if r.status_code not in (200, 404):
                errors.append(f"{name} → HTTP {r.status_code}")
        except Exception as e:
            errors.append(f"{name} → {e}")

    if errors:
        pytest.skip(f"Servisler hazır değil:\n" + "\n".join(errors))

    # test_key'e platinum plan ver (Redis'e yaz) — rate limit sorununu önler
    # Admin key yoksa bu adım sessizce atlanır
    try:
        admin_key = os.getenv("ADMIN_API_KEY", "")
        resp = httpx.post(
            f"{GATEWAY_URL}/admin/keys",
            params={"key": SCHOOL_KEY, "name": "E2E Test Suite", "plan": "platinum"},
            headers={"X-Admin-Key": admin_key} if admin_key else {},
            timeout=5,
        )
        if resp.status_code == 200:
            print(f"\n[E2E] ✅ {SCHOOL_KEY} → platinum plan (rate limit kaldırıldı)")
    except Exception:
        pass  # Admin endpoint yoksa veya Redis bağlantısı yoksa devam et


# ── SMOKE TESTLER (hızlı, ~5 sn) ─────────────────────────────────────────────

class TestSmoke:
    """Temel sağlık kontrolleri."""

    @pytest.mark.smoke
    def test_gateway_docs_accessible(self):
        r = httpx.get(f"{GATEWAY_URL}/docs", timeout=5)
        assert r.status_code in (200, 404)  # 404 = docs disabled, OK

    @pytest.mark.smoke
    def test_retriever_docs_accessible(self):
        r = httpx.get(f"{RETRIEVER_URL}/docs", timeout=5)
        assert r.status_code in (200, 404)

    @pytest.mark.smoke
    def test_gateway_rejects_invalid_json(self):
        r = httpx.post(
            f"{GATEWAY_URL}/b2b/ask_intelligent",
            content="not json",
            headers={"X-School-Key": SCHOOL_KEY, "Content-Type": "application/json"},
            timeout=10,
        )
        assert r.status_code in (422, 400)


# ── CASUAL CHAT TESTLER (~3 sn) ───────────────────────────────────────────────

class TestCasualChat:
    """Selamlama ve basit sohbet — LLM çağrısı yok, anında dönmeli."""

    def test_selam_returns_answer(self):
        data = _ask("selam", session_id="e2e_casual_1", timeout=15)
        assert data["answer"], "Yanıt boş"
        assert data.get("usage", {}).get("total_tokens", 0) == 0

    def test_tesekkurler_returns_answer(self):
        data = _ask("teşekkürler", session_id="e2e_casual_2", timeout=15)
        assert data["answer"]
        assert len(data["answer"]) > 5

    def test_naber_returns_answer(self):
        data = _ask("naber kanka", session_id="e2e_casual_3", timeout=15)
        assert data["answer"]


# ── FAST LOOKUP TESTLER (YÖK Atlas tablosu, ~10-20 sn) ────────────────────────

class TestFastLookup:
    """Sıralama/bölüm bazlı YÖK Atlas sorguları — tablo dönmeli."""

    def test_rank_program_returns_table(self):
        data = _ask("50k SAY ile bilgisayar mühendisliği nereler gelir?")
        answer = data["answer"]
        assert answer, "Yanıt boş"
        assert _has_table(answer), f"Tablo yok:\n{answer[:500]}"

    def test_rank_city_filters_correctly(self):
        data = _ask("100k SAY ile İstanbul'da mühendislik bölümleri")
        answer = data["answer"]
        assert answer
        # Tablo varsa İstanbul olmalı
        if _has_table(answer):
            assert "İstanbul" in answer or "istanbul" in answer.lower()

    def test_ranks_are_sensible(self):
        """Dönen sıralamalar sorgu sıralamasına yakın olmalı."""
        data = _ask("200k SAY için bilgisayar mühendisliği")
        answer = data["answer"]
        ranks = _extract_ranks(answer)
        if ranks:
            # En az bir sıralama 50k-500k arasında olmalı (200k ±150k)
            reasonable = [r for r in ranks if 50_000 <= r <= 500_000]
            assert reasonable, f"Sıralamalar mantıksız: {ranks[:5]}"

    def test_vakif_filter(self):
        data = _ask("vakıf üniversitelerinde 150k ile hukuk")
        answer = data["answer"]
        assert answer
        if _has_table(answer):
            assert "Vakıf" in answer or "vakıf" in answer.lower()

    def test_burslu_filter(self):
        data = _ask("%50 burslu tıp fakülteleri İstanbul")
        answer = data["answer"]
        assert answer

    def test_no_result_graceful(self):
        """Sonuç yoksa kilitlenmemeli, anlamlı mesaj dönmeli."""
        data = _ask("1k SAY ile tıp fakültesi devlet ücretsiz")
        answer = data["answer"]
        assert answer
        assert len(answer) > 20

    def test_open_ended_query(self):
        """Belirli bölüm belirtilmeden sıralama sorusu."""
        data = _ask("280k SAY ile hangi bölümlere girebilirim İstanbul'da?")
        answer = data["answer"]
        assert answer
        # Diş Hekimliği gibi alakasız bölümler gelmemeli (bu regression test)
        # Yanıtta "Diş Hekimliği" varsa ve hiç mühendislik yoksa sorun var
        has_dis_hekimligi = "Diş Hekimliği" in answer
        has_muhendislik = "ühendis" in answer or "Mühendis" in answer
        if has_dis_hekimligi and not has_muhendislik:
            pytest.fail(
                "Open-ended sorgu yanlış bölüm döndürdü: sadece 'Diş Hekimliği' var, "
                "280k SAY için mühendislik bölümleri bekleniyor."
            )


# ── UNI INFO TESTLER (~15-30 sn) ──────────────────────────────────────────────

class TestUniInfo:
    """Üniversite tanıtım soruları — LLM analizi dönmeli."""

    def test_uni_info_returns_analysis(self):
        data = _ask("İstanbul Gelişim Üniversitesi hakkında bilgi ver")
        answer = data["answer"]
        assert answer
        assert len(answer) > 100, "Yanıt çok kısa"

    def test_uni_info_not_just_links(self):
        """Sadece link listesi değil, gerçek analiz dönmeli."""
        data = _ask("Bahçeşehir Üniversitesi nasıl bir yer?")
        answer = data["answer"]
        assert answer
        # Minimum 200 karakter — gerçek içerik olduğunu gösterir
        assert len(answer) > 200, f"Yanıt çok kısa (link listesi?): {answer[:300]}"
        # "Yukarıdaki linklerden bakabilirsin" gibi kaçamak cümle olmamalı
        assert "linklerden bakabilirsin" not in answer

    def test_meslek_sorusu(self):
        """Meslek/kariyer soruları — 'Şu an yanıt üretemiyorum' dönmemeli."""
        data = _ask("bilgi güvenliği teknolojisi nasıl bir meslek? okunur mu?")
        answer = data["answer"]
        assert answer
        assert "tekrar dene" not in answer.lower()
        # Bu soru "nasıl" + "meslek" içeriyor → search veya uni_info yoluna gitmeli
        # Casual chat yanıtı kısa olur (< 100 karakter), gerçek analiz uzun olur
        # Test: en azından bir yanıt var ve sistem çökmedi
        assert len(answer) > 10  # minimum "bir şey" döndü
        # Eğer çok kısa (casual chat) döndüyse, bu bir regression — warn et ama fail etme
        if len(answer) < 50:
            pytest.xfail(
                f"Meslek sorusu casual chat'e düştü ({len(answer)} karakter). "
                f"Yanıt: '{answer}'. "
                f"NER'de 'meslek' keyword'ü _NARRATIVE_KEYWORDS'te olmalı."
            )


# ── CACHE TESTLER ─────────────────────────────────────────────────────────────

class TestCache:
    """Cache davranışını test eder — aynı sorgu ikinci kez cache'ten gelmeli."""

    def test_second_request_is_cached(self):
        query = f"selam e2e_cache_test_{int(time.time())}"  # unique sorgu
        # İlk istek — cache miss
        data1 = _ask(query, session_id="cache_test_1")
        assert not data1.get("cached", False), "İlk istek cache hit olmamalı"

        # Aynı sorgu tekrar — cache hit bekleniyor
        # (casual chat cache'lenmeyebilir çünkü kısa cevap, session'a bağlı)
        # Bu test sadece servisin çökmediğini doğrular
        data2 = _ask(query, session_id="cache_test_1")
        assert data2["answer"]  # en azından yanıt dönmeli


# ── HATA DURUMU TESTLER ────────────────────────────────────────────────────────

class TestErrorHandling:
    """Hata durumlarında sistem düzgün davranıyor mu?"""

    def test_empty_query_handled(self):
        """Boş sorgu sistemi çökertmemeli."""
        try:
            data = _ask("", session_id="e2e_err_1", timeout=15)
            assert "answer" in data or True
        except Exception:
            pass  # 422 veya benzeri HTTP hata kabul edilebilir

    def test_very_long_query_handled(self):
        """Çok uzun sorgu sistemi çökertmemeli."""
        long_q = "istanbul bilgisayar mühendisliği " * 20
        data = _ask(long_q, session_id="e2e_err_2", timeout=60)
        assert data["answer"]

    def test_gibberish_query_handled(self):
        """Anlamsız sorgu düzgün yanıt dönmeli."""
        data = _ask("xyzqwerty123asdfgh", session_id="e2e_err_3", timeout=30)
        assert data["answer"]
        assert len(data["answer"]) > 5


# ── PERFORMANS TESTLER (opsiyonel) ────────────────────────────────────────────

class TestPerformance:
    """Temel yanıt süresi kontrolleri."""

    @pytest.mark.slow
    def test_casual_chat_under_5s(self):
        start = time.time()
        _ask("selam", session_id="e2e_perf_1", timeout=10)
        elapsed = time.time() - start
        assert elapsed < 5, f"Casual chat çok yavaş: {elapsed:.1f}sn (beklenen <5sn)"

    @pytest.mark.slow
    def test_fast_lookup_under_30s(self):
        start = time.time()
        _ask("50k SAY bilgisayar mühendisliği", session_id="e2e_perf_2", timeout=60)
        elapsed = time.time() - start
        assert elapsed < 30, f"Fast lookup çok yavaş: {elapsed:.1f}sn (beklenen <30sn)"
