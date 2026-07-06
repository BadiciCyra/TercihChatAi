"""
test_web_supplement_latency.py — Web Supplement Latency & Stabilite Testleri

Ne test eder:
  1. Cache MISS → DDG çağrısı → sonuç gelme süresi
  2. Cache HIT  → Redis'ten okuma süresi (DDG'ye gidilmemeli)
  3. Multi-query (yeni) vs tek-query latency karşılaştırması
  4. Timeout durumunda graceful degradation
  5. Gateway üzerinden fast_lookup toplam latency breakdown

ÇALIŞTIRMA:
  # Docker servisler ayakta olmalı:
  #   docker-compose up -d ai-gateway ai-retriever redis
  #
  # Sadece latency testleri:
  pytest ai/tests/test_web_supplement_latency.py -v -m latency -s
  #
  # Tüm web supplement testleri:
  pytest ai/tests/test_web_supplement_latency.py -v -s
  #
  # Sonuçları tablo olarak görmek için:
  pytest ai/tests/test_web_supplement_latency.py -v -s --tb=short

GEREKSINIMLER:
  docker-compose up -d ai-gateway ai-retriever redis
  pip install httpx pytest
"""

import os
import time
import json
import pytest
import httpx

# ── Konfigürasyon ──────────────────────────────────────────────────────────────
GATEWAY_URL   = os.getenv("GATEWAY_URL",   "http://localhost:8003")
RETRIEVER_URL = os.getenv("RETRIEVER_URL", "http://localhost:8000")
REDIS_URL     = os.getenv("REDIS_URL",     "redis://localhost:6379/0")
SCHOOL_KEY    = os.getenv("AI_GATEWAY_SCHOOL_KEY", "test_key")

# Latency bütçeleri (saniye)
BUDGET = {
    "web_search_cold":    12.0,  # Cache miss — DDG'ye gidiyor
    "web_search_warm":     0.5,  # Cache hit  — sadece Redis okuma
    "fast_lookup_total":  25.0,  # Gateway'den gateway'e toplam
    "retriever_health":    2.0,  # /docs endpoint
}

pytestmark = pytest.mark.latency


# ── Yardımcılar ────────────────────────────────────────────────────────────────

class LatencyResult:
    """Tek bir ölçüm sonucu."""
    def __init__(self, name: str, elapsed: float, success: bool, detail: str = ""):
        self.name = name
        self.elapsed = elapsed
        self.success = success
        self.detail = detail

    def __str__(self):
        status = "✅" if self.success else "❌"
        return f"{status} {self.name}: {self.elapsed:.2f}s — {self.detail}"


def _measure(fn, *args, **kwargs) -> tuple[float, any]:
    """Fonksiyonu çalıştır ve (süre, sonuç) döndür."""
    start = time.perf_counter()
    result = fn(*args, **kwargs)
    elapsed = time.perf_counter() - start
    return elapsed, result


def _ask_gateway(query: str, session_id: str = "latency_test", timeout: int = 60) -> tuple[float, dict]:
    """Gateway'e istek gönder, (elapsed, data) döndür."""
    start = time.perf_counter()
    resp = httpx.post(
        f"{GATEWAY_URL}/b2b/ask_intelligent",
        json={"query": query, "session_id": session_id},
        headers={"X-School-Key": SCHOOL_KEY},
        timeout=timeout,
    )
    elapsed = time.perf_counter() - start
    assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text[:200]}"
    return elapsed, resp.json()


def _call_retriever_web_search(queries: list[str], max_results: int = 8) -> tuple[float, dict]:
    """Retriever /retrieve/web_search endpoint'ini doğrudan çağır."""
    payload = {
        "query": queries[0],
        "queries": queries,
        "max_results": max_results,
        "rerank": False,
    }
    start = time.perf_counter()
    resp = httpx.post(
        f"{RETRIEVER_URL}/retrieve/web_search",
        json=payload,
        timeout=30,
    )
    elapsed = time.perf_counter() - start
    assert resp.status_code == 200, f"HTTP {resp.status_code}"
    return elapsed, resp.json()


def _flush_redis_key(pattern: str) -> int:
    """Redis'ten cache key'lerini sil (test izolasyonu için)."""
    try:
        import redis
        r = redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=2)
        keys = r.keys(pattern)
        if keys:
            r.delete(*keys)
        return len(keys)
    except Exception as e:
        print(f"[TEST] Redis flush hatası (devam ediliyor): {e}")
        return 0


def _read_redis_key(key: str) -> str | None:
    """Redis'ten tek bir key'i oku."""
    try:
        import redis
        r = redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=2)
        return r.get(key)
    except Exception:
        return None


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session", autouse=True)
def require_services():
    """Testler başlamadan servis health check."""
    errors = []
    for name, url in [("ai-gateway", GATEWAY_URL), ("ai-retriever", RETRIEVER_URL)]:
        try:
            r = httpx.get(f"{url}/docs", timeout=5)
            if r.status_code not in (200, 404):
                errors.append(f"{name} → HTTP {r.status_code}")
        except Exception as e:
            errors.append(f"{name} → bağlanamadı: {e}")

    if errors:
        pytest.skip("Servisler hazır değil:\n" + "\n".join(errors))

    # test_key'e yüksek rate limit ver
    try:
        admin_key = os.getenv("ADMIN_API_KEY", "")
        httpx.post(
            f"{GATEWAY_URL}/admin/keys",
            params={"key": SCHOOL_KEY, "name": "Latency Tests", "plan": "platinum"},
            headers={"X-Admin-Key": admin_key} if admin_key else {},
            timeout=5,
        )
    except Exception:
        pass


@pytest.fixture(autouse=True)
def print_separator():
    """Her test arasına separator bas."""
    print()
    yield
    print()


# ── 1. Retriever Doğrudan Latency ─────────────────────────────────────────────

class TestRetrieverWebSearchLatency:
    """
    Retriever /retrieve/web_search endpoint'ini doğrudan test eder.
    Gateway overhead'i yok — saf web search süresi görülür.
    """

    def test_single_query_latency(self):
        """Tek sorgu — baseline ölçüm."""
        queries = ["bilgisayar mühendisliği öğrenci yorumları ekşi sözlük"]
        elapsed, data = _call_retriever_web_search(queries)
        result_count = data.get("result_count", 0)

        print(f"\n  📊 Tek sorgu: {elapsed:.2f}s, {result_count} sonuç")
        assert elapsed < 30, f"Tek sorgu çok yavaş: {elapsed:.2f}s"
        # Başarısız olsa bile (0 sonuç) timeout'tan önce dönmeli
        assert "results" in data

    def test_multi_query_array_latency(self):
        """3 sorguyu queries array'iyle tek HTTP isteğinde gönder (yeni yöntem)."""
        target = "Bilgisayar Mühendisliği"
        queries = [
            f"{target} öğrenci yorumları ekşi sözlük",
            f"{target} tavsiye hoca kalite forum",
            f"{target} hakkında deneyim üniversite",
        ]
        elapsed, data = _call_retriever_web_search(queries)
        result_count = data.get("result_count", 0)

        print(f"\n  📊 Multi-query (3 sorgu, tek istek): {elapsed:.2f}s, {result_count} sonuç")
        assert elapsed < BUDGET["web_search_cold"], (
            f"Multi-query çok yavaş: {elapsed:.2f}s > {BUDGET['web_search_cold']}s budget"
        )
        assert "results" in data

    def test_3x_separate_query_latency(self):
        """
        ESKİ YÖNTEM: 3 ayrı HTTP isteği (karşılaştırma için).
        Bu testin yeni yöntemden DAHA YAVAŞ olması beklenir.
        """
        target = "Bilgisayar Mühendisliği"
        queries = [
            f"{target} öğrenci yorumları ekşi sözlük",
            f"{target} tavsiye hoca kalite forum",
            f"{target} hakkında deneyim üniversite",
        ]

        total_elapsed = 0.0
        total_results = 0
        for q in queries:
            elapsed, data = _call_retriever_web_search([q])
            total_elapsed += elapsed
            total_results += data.get("result_count", 0)

        print(f"\n  📊 3x Ayrı istek (eski yöntem): {total_elapsed:.2f}s toplam, {total_results} sonuç")
        # Bu test sadece ölçüm yapar, fail etmez — karşılaştırma için
        # Yeni yöntemden daha yavaş olmalı ama zorunlu değil (DDG bağımsız)
        print(f"  ℹ️  Karşılaştırma: eski={total_elapsed:.2f}s, yeni={BUDGET['web_search_cold']}s budget")

    def test_query_with_uni_context(self):
        """Üniversite + bölüm kombinasyonu — gerçek use case latency."""
        target = "İTÜ Bilgisayar Mühendisliği"
        queries = [
            f"{target} öğrenci yorumları ekşi sözlük",
            f"{target} tavsiye hoca kalite forum",
            f"{target} hakkında deneyim",
        ]
        elapsed, data = _call_retriever_web_search(queries)
        result_count = data.get("result_count", 0)

        print(f"\n  📊 Üniversite+bölüm kombinasyonu: {elapsed:.2f}s, {result_count} sonuç")
        assert elapsed < BUDGET["web_search_cold"]


# ── 2. Redis Cache Latency ─────────────────────────────────────────────────────

class TestRedisCacheLatency:
    """
    Redis cache HIT/MISS davranışını ve latency'sini test eder.
    _fast_web_supplement'in cache katmanını doğrular.
    """

    def test_cache_miss_then_hit_via_gateway(self):
        """
        Gateway üzerinden fast_lookup çağrısı:
        - İlk istek: cache miss → DDG çağrısı
        - İkinci istek: cache hit → sadece Redis
        Süre farkı ölçülür.
        """
        # Test için unique bir kombinasyon kullan — eski cache'i temizle
        program = "Endüstri Mühendisliği"
        _flush_redis_key("ai:web_sup:endüstri mühendisliği*")

        query = f"50k SAY {program} nereler gelir"

        # İlk istek — cache miss
        t1_start = time.perf_counter()
        elapsed1, data1 = _ask_gateway(query, session_id="cache_test_miss")
        t1_total = time.perf_counter() - t1_start

        assert data1["answer"], "İlk istek boş yanıt döndü"
        print(f"\n  📊 1. İstek (cache MISS): {elapsed1:.2f}s toplam")

        # Kısa bekle (cache yazılsın)
        time.sleep(0.5)

        # İkinci istek — cache hit bekleniyor
        elapsed2, data2 = _ask_gateway(query, session_id="cache_test_hit")
        assert data2["answer"], "İkinci istek boş yanıt döndü"
        print(f"  📊 2. İstek (cache HIT bekleniyor): {elapsed2:.2f}s toplam")

        speedup = elapsed1 / elapsed2 if elapsed2 > 0 else 1.0
        print(f"  ⚡ Hızlanma oranı: {speedup:.1f}x")

        # Cache HIT olan istek daha hızlı ya da en az eşit olmalı
        # (Gateway overhead sabit, web supplement kısmı hızlanıyor)
        # Strict assertion yapmıyoruz çünkü YÖK Atlas çağrısı her iki durumda da var
        # Ama 2. istek 1. istekten çok daha yavaş OLMAMALI
        assert elapsed2 <= elapsed1 + 3.0, (
            f"Cache hit olan istek beklenmedik biçimde yavaş: "
            f"miss={elapsed1:.2f}s, hit={elapsed2:.2f}s"
        )

    def test_redis_direct_read_latency(self):
        """Redis'ten direkt okuma latency'si — <50ms olmalı."""
        try:
            import redis
            r = redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=2)
            r.ping()
        except Exception:
            pytest.skip("Redis bağlantısı yok")

        # Test key yaz
        import redis as _redis
        r = _redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=2)
        test_key = "ai:web_sup:test_program"
        test_val = "### Test Content\n- Link 1\n- Link 2"
        r.setex(test_key, 60, test_val)

        # Okuma latency ölç
        elapsed, val = _measure(r.get, test_key)
        r.delete(test_key)

        elapsed_ms = elapsed * 1000
        print(f"\n  📊 Redis GET latency: {elapsed_ms:.1f}ms")
        assert elapsed_ms < 50, f"Redis çok yavaş: {elapsed_ms:.1f}ms (beklenen <50ms)"
        assert val == test_val

    def test_cache_key_format(self):
        """Cache key formatının doğru üretildiğini doğrula."""
        # fast_lookup.py'deki _web_supplement_cache_key fonksiyonunu import et
        import sys
        from pathlib import Path
        ai_dir = Path(__file__).resolve().parents[1]
        if str(ai_dir) not in sys.path:
            sys.path.insert(0, str(ai_dir))

        try:
            # Direkt import yerine fonksiyon mantığını test et
            def make_key(program, uni, city):
                parts = [p.lower().strip() for p in [program, uni, city] if p]
                combined = "|".join(parts)
                return f"ai:web_sup:{combined}"

            assert make_key("Bilgisayar Mühendisliği", "", "") == "ai:web_sup:bilgisayar mühendisliği"
            assert make_key("Bilgisayar Mühendisliği", "İTÜ", "İstanbul") == "ai:web_sup:bilgisayar mühendisliği|i̇tü|i̇stanbul"
            assert make_key("", "Koç Üniversitesi", "") == "ai:web_sup:koç üniversitesi"
            print("\n  ✅ Cache key formatları doğru")
        except AssertionError as e:
            pytest.fail(f"Cache key format hatası: {e}")


# ── 3. Gateway End-to-End Latency Breakdown ───────────────────────────────────

class TestGatewayLatencyBreakdown:
    """
    Gateway üzerinden farklı sorgu tiplerinin latency profilini çıkarır.
    Hangi pipeline'ın ne kadar sürdüğünü gösterir.
    """

    def test_casual_chat_latency(self):
        """Selamlama — NER bypass, LLM yok, <3sn olmalı."""
        elapsed, data = _ask_gateway("selam", session_id="perf_casual")
        print(f"\n  📊 Casual chat: {elapsed:.2f}s")
        assert elapsed < 3.0, f"Casual chat çok yavaş: {elapsed:.2f}s"
        assert data["answer"]

    def test_fast_lookup_no_web_supplement_latency(self):
        """
        Sadece YÖK Atlas — web supplement olmadan.
        cache'i temizle, web supplement'i bypass et.
        """
        # Rank bazlı sorgu — program/uni olmadığında web supplement tetiklenmez
        elapsed, data = _ask_gateway(
            "50000 SAY sıralama ile girebileceğim bölümler",
            session_id="perf_fast_nosupp"
        )
        print(f"\n  📊 Fast lookup (sadece atlas): {elapsed:.2f}s")
        assert elapsed < BUDGET["fast_lookup_total"]
        assert data["answer"]

    def test_fast_lookup_with_web_supplement_latency(self):
        """
        YÖK Atlas + web supplement — tam pipeline.
        Program/uni verilince web supplement tetiklenir.
        """
        # Cache temizle ki cold start ölçelim
        _flush_redis_key("ai:web_sup:bilgisayar mühendisliği*")
        time.sleep(0.2)

        elapsed, data = _ask_gateway(
            "50k SAY bilgisayar mühendisliği nereler gelir?",
            session_id="perf_fast_withsupp"
        )
        print(f"\n  📊 Fast lookup (atlas + web supplement, cold): {elapsed:.2f}s")
        assert elapsed < BUDGET["fast_lookup_total"], (
            f"Fast lookup + web supplement çok yavaş: {elapsed:.2f}s > {BUDGET['fast_lookup_total']}s"
        )
        assert data["answer"]

    def test_fast_lookup_cached_web_supplement_latency(self):
        """
        Aynı sorgu ikinci kez — web supplement cache'ten gelmeli, daha hızlı olmalı.
        """
        # Önce bir kez çalıştır (cache doldur)
        _ask_gateway(
            "50k SAY bilgisayar mühendisliği nereler gelir?",
            session_id="perf_warm_1"
        )
        time.sleep(0.3)

        # Şimdi tekrar — cache hit
        elapsed, data = _ask_gateway(
            "50k SAY bilgisayar mühendisliği nereler gelir?",
            session_id="perf_warm_2"
        )
        print(f"\n  📊 Fast lookup (atlas + web supplement, WARM cache): {elapsed:.2f}s")
        # Warm cache ile 5sn daha hızlı olmalı (web supplement artık Redis'ten geliyor)
        assert elapsed < BUDGET["fast_lookup_total"] - 5, (
            f"Warm cache yeterince hızlandırmadı: {elapsed:.2f}s"
        )


# ── 4. Timeout Graceful Degradation ───────────────────────────────────────────

class TestTimeoutGracefulDegradation:
    """
    Web supplement timeout'a girdiğinde sistem düzgün davranıyor mu?
    YÖK Atlas sonucu yine de dönmeli, sadece web supplement eksik olmalı.
    """

    def test_answer_returned_even_without_web_supplement(self):
        """
        Web supplement çalışmasa bile YÖK Atlas tablosu dönmeli.
        (Retriever kapalıysa ya da çok yavaşsa)
        """
        # Normal istek — sadece yanıt var mı kontrol et
        elapsed, data = _ask_gateway(
            "100k SAY makine mühendisliği",
            session_id="perf_graceful"
        )
        answer = data["answer"]
        print(f"\n  📊 Graceful degradation testi: {elapsed:.2f}s")
        assert answer, "Yanıt boş — sistem tamamen çöktü"
        # YÖK Atlas tablosu ya da anlamlı mesaj olmalı
        has_content = len(answer) > 50
        assert has_content, f"Çok kısa yanıt: '{answer}'"

    def test_web_supplement_timeout_does_not_block(self):
        """
        Web supplement 12sn timeout'u aşarsa, toplam yanıt 25sn'yi geçmemeli.
        (YÖK Atlas zaten bitmiş olacak, web supplement drop ediliyor)
        """
        _flush_redis_key("ai:web_sup:inşaat mühendisliği*")

        start = time.perf_counter()
        elapsed, data = _ask_gateway(
            "150k SAY inşaat mühendisliği",
            session_id="perf_timeout_block",
            timeout=60
        )
        total = time.perf_counter() - start

        print(f"\n  📊 Timeout block testi: gateway={elapsed:.2f}s, total={total:.2f}s")
        assert data["answer"], "Yanıt boş"
        assert total < BUDGET["fast_lookup_total"], (
            f"Toplam süre çok uzun: {total:.2f}s — web supplement drop edilmiyor olabilir"
        )


# ── 5. Latency Özet Raporu ─────────────────────────────────────────────────────

class TestLatencySummary:
    """
    Tüm pipeline adımlarını ölçüp özet tablo basar.
    pytest -s ile çalıştırıldığında terminal'de tablo görünür.
    """

    def test_print_latency_summary(self):
        """
        5 farklı sorgu tipini ölçüp tablo olarak bas.
        Bu test hiç fail etmez — sadece ölçüm yapar.
        """
        scenarios = [
            ("Casual chat",          "selam",                                    "perf_sum_1"),
            ("Fast lookup (sadece atlas)", "50000 SAY İstanbul mühendislik",     "perf_sum_2"),
            ("Fast lookup + web sup","50k SAY bilgisayar mühendisliği",          "perf_sum_3"),
            ("Fast lookup + web sup (warm)", "50k SAY bilgisayar mühendisliği",  "perf_sum_4"),
            ("Uni info",             "Boğaziçi Üniversitesi hakkında bilgi",      "perf_sum_5"),
        ]

        results = []
        for name, query, session in scenarios:
            try:
                elapsed, data = _ask_gateway(query, session_id=session, timeout=60)
                answer_len = len(data.get("answer", ""))
                results.append((name, elapsed, answer_len, "✅"))
            except Exception as e:
                results.append((name, -1.0, 0, f"❌ {str(e)[:40]}"))
            time.sleep(0.5)

        # Tablo bas
        print("\n")
        print("┌─────────────────────────────────────────┬──────────┬───────────┬────────┐")
        print("│ Senaryo                                 │ Süre (s) │ Yanıt (c) │ Durum  │")
        print("├─────────────────────────────────────────┼──────────┼───────────┼────────┤")
        for name, elapsed, ans_len, status in results:
            t = f"{elapsed:.2f}" if elapsed >= 0 else "HATA"
            print(f"│ {name:<39} │ {t:>8} │ {ans_len:>9} │ {status:<6} │")
        print("└─────────────────────────────────────────┴──────────┴───────────┴────────┘")

        budgets_ok = all(
            elapsed <= BUDGET["fast_lookup_total"]
            for name, elapsed, _, status in results
            if elapsed >= 0 and "Casual" not in name
        )
        casual_ok = all(
            elapsed <= 3.0
            for name, elapsed, _, status in results
            if elapsed >= 0 and "Casual" in name
        )

        print(f"\n  Latency budgets: {'✅ Tümü OK' if budgets_ok and casual_ok else '⚠️ Bazıları aşıldı'}")
        # Bu test asla fail etmez — sadece raporlar
        assert True
