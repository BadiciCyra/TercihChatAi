# test_gate_auth.py — gate.py auth ve rate limit mantığı için unit testler
# Docker gerektirmez — sadece fonksiyon logiğini test eder
import pytest
import time


def test_load_client_from_env_parses_name_and_plan(monkeypatch):
    monkeypatch.setenv("API_KEY_TESTSCHOOL123", "Test Okulu:gold")
    from ai.app.gate import _load_client_from_env
    result = _load_client_from_env("testschool123")
    assert result is not None
    assert result["name"] == "Test Okulu"
    assert result["plan"] == "gold"


def test_load_client_from_env_default_plan(monkeypatch):
    monkeypatch.setenv("API_KEY_SIMPLEKEY", "Basit Okul")
    from ai.app.gate import _load_client_from_env
    result = _load_client_from_env("simplekey")
    assert result["plan"] == "free"


def test_load_client_from_env_missing_key(monkeypatch):
    from ai.app.gate import _load_client_from_env
    result = _load_client_from_env("nonexistent_key_xyz")
    assert result is None


def test_check_rate_local_allows_within_limit():
    # Rate limiti test et — in-process fallback
    from ai.app.gate import _check_rate_local, _local_rate
    _local_rate.clear()
    key = f"test_key_{time.time()}"
    # limit=3, 3 istek geçmeli
    assert _check_rate_local(key, 3) is True
    assert _check_rate_local(key, 3) is True
    assert _check_rate_local(key, 3) is True
    # 4. istek engellenмeli
    assert _check_rate_local(key, 3) is False


def test_normalize_for_cache_is_deterministic():
    from ai.app.gate import _normalize_for_cache
    # Aynı sorgu iki kez çağrıldığında aynı sonucu vermeli
    q = "50k ile İstanbul bilgisayar mühendisliği"
    assert _normalize_for_cache(q) == _normalize_for_cache(q)

def test_normalize_for_cache_lowercases():
    from ai.app.gate import _normalize_for_cache
    # Aynı sorgu — sadece noktalama farkı — aynı sonucu vermeli
    q1 = "50k bilgisayar mühendisliği istanbulda"
    q2 = "50k bilgisayar mühendisliği istanbulda!!!"
    # Özel karakterler çıkarıldıktan sonra eşit olmalı
    assert _normalize_for_cache(q1) == _normalize_for_cache(q2)


def test_normalize_for_cache_removes_special_chars():
    from ai.app.gate import _normalize_for_cache
    result = _normalize_for_cache("50k ile hangi bölüm?!")
    assert "?" not in result
    assert "!" not in result
