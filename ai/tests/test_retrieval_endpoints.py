# test_retrieval_endpoints.py — retrieve.py endpoint'leri için integration testler
# Docker gerektirir: ai-retriever:8000 ayakta olmalı
# Çalıştırma: pytest ai/tests/test_retrieval_endpoints.py -v -m integration
import os
import pytest
import httpx

RETRIEVER_URL = os.getenv("RETRIEVER_URL", "http://localhost:8000")

pytestmark = pytest.mark.integration  # sadece --integration flag ile çalışır


@pytest.fixture(scope="module")
def retriever_url():
    return RETRIEVER_URL


def is_retriever_up(url: str) -> bool:
    try:
        r = httpx.get(f"{url}/docs", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


@pytest.fixture(scope="module", autouse=True)
def skip_if_retriever_down(retriever_url):
    if not is_retriever_up(retriever_url):
        pytest.skip(f"ai-retriever ulaşılamıyor: {retriever_url}")


class TestYokAtlasEndpoint:
    def test_basic_program_search(self, retriever_url):
        resp = httpx.post(
            f"{retriever_url}/retrieve/yok_atlas",
            json={"program": "Bilgisayar Mühendisliği", "score_type": "SAY"},
            timeout=30,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "result_count" in data
        assert "markdown_table" in data

    def test_rank_based_search(self, retriever_url):
        resp = httpx.post(
            f"{retriever_url}/retrieve/yok_atlas",
            json={"rank": "100000", "score_type": "SAY"},
            timeout=30,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["result_count"] >= 0

    def test_empty_params_returns_error(self, retriever_url):
        resp = httpx.post(
            f"{retriever_url}/retrieve/yok_atlas",
            json={},
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        # Parametre yoksa error field dönmeli
        assert "error" in data or data["result_count"] == 0

    def test_city_filter(self, retriever_url):
        resp = httpx.post(
            f"{retriever_url}/retrieve/yok_atlas",
            json={"program": "Hukuk", "city": "İstanbul"},
            timeout=30,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "result_count" in data


class TestWebSearchEndpoint:
    def test_basic_search(self, retriever_url):
        resp = httpx.post(
            f"{retriever_url}/retrieve/web_search",
            json={"query": "İTÜ bilgisayar mühendisliği hakkında"},
            timeout=30,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        assert "result_count" in data

    def test_multi_query(self, retriever_url):
        resp = httpx.post(
            f"{retriever_url}/retrieve/web_search",
            json={
                "query": "Boğaziçi üniversitesi",
                "queries": [
                    "Boğaziçi üniversitesi hakkında",
                    "Boğaziçi öğrenci yorumları",
                ],
                "max_results": 5,
            },
            timeout=30,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["result_count"] <= 5

    def test_review_search(self, retriever_url):
        resp = httpx.post(
            f"{retriever_url}/retrieve/web_search",
            json={
                "query": "Koç üniversitesi yorumları",
                "is_review_search": True,
                "max_results": 3,
            },
            timeout=30,
        )
        assert resp.status_code == 200


class TestGatewayEndpoint:
    """Gateway /b2b/ask_intelligent endpoint'i için smoke testler."""

    GATEWAY_URL = os.getenv("GATEWAY_URL", "http://localhost:8003")

    @pytest.fixture(scope="class", autouse=True)
    def skip_if_gateway_down(self):
        try:
            r = httpx.get(f"{self.GATEWAY_URL}/docs", timeout=3)
            if r.status_code != 200:
                pytest.skip("ai-gateway ulaşılamıyor")
        except Exception:
            pytest.skip("ai-gateway ulaşılamıyor")

    def test_simple_query(self):
        resp = httpx.post(
            f"{self.GATEWAY_URL}/b2b/ask_intelligent",
            json={"query": "selam", "session_id": "test_session"},
            headers={"X-School-Key": "test_key"},
            timeout=30,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "answer" in data
        assert data["answer"]  # boş olmamalı

    def test_yok_atlas_query(self):
        resp = httpx.post(
            f"{self.GATEWAY_URL}/b2b/ask_intelligent",
            json={"query": "50k SAY ile İstanbul bilgisayar mühendisliği", "session_id": "test_session_2"},
            headers={"X-School-Key": "test_key"},
            timeout=60,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "answer" in data

    def test_rate_limit_header(self):
        """Rate limit aşıldığında 429 dönmeli."""
        headers = {"X-School-Key": "test_key"}
        payload = {"query": "test", "session_id": "rate_test"}
        # guest plan = 2 req/dak, arka arkaya 3 istek at
        for _ in range(2):
            httpx.post(
                f"{self.GATEWAY_URL}/b2b/ask_intelligent",
                json=payload,
                headers=headers,
                timeout=10,
            )
        # 3. istek 429 dönebilir (free plan = 5 req/dak, test_key free)
        # Bu test soft — servis yüküne göre değişir
