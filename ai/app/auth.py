"""
app/auth.py — API key doğrulama ve rate limiting.

API key kaynak önceliği: Redis → .env → dev fallback → anonim.
Rate limiting: Redis sliding window (atomic INCR + EXPIRE), Redis yoksa
in-process sayaç fallback'i. Redis client `redis_client.cache` üzerinden
call-time'da okunur.
"""
import logging
import os
import time

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from app import redis_client

logger = logging.getLogger("GATEWAY")

# --- GÜVENLİK (API KEY) ---
API_KEY_NAME = "X-School-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

# ── Plan tanımları: dakika başına istek limiti ──────────────────────────────
_PLAN_LIMITS: dict[str, int] = {
    "free":     int(os.getenv("RATE_LIMIT_FREE",     "5")),    # 5 req/dak
    "gold":     int(os.getenv("RATE_LIMIT_GOLD",     "30")),   # 30 req/dak
    "platinum": int(os.getenv("RATE_LIMIT_PLATINUM", "120")),  # 120 req/dak
    "guest":    int(os.getenv("RATE_LIMIT_GUEST",    "2")),    # 2 req/dak (anonim)
}
_RATE_WINDOW = 60  # saniye cinsinden pencere

# ── Fallback: Redis yoksa in-process basit sayaç ───────────────────────────
_local_rate: dict[str, list] = {}  # {key: [timestamp, ...]}


def _check_rate_local(api_key: str, limit: int) -> bool:
    """Redis yoksa in-process sliding window rate check. Thread-safe değil ama
    single-process dev ortamında yeterli."""
    now = time.time()
    window_start = now - _RATE_WINDOW
    hits = _local_rate.get(api_key, [])
    hits = [t for t in hits if t > window_start]
    if len(hits) >= limit:
        return False  # limit aşıldı
    hits.append(now)
    _local_rate[api_key] = hits
    return True


def _check_rate_redis(api_key: str, limit: int) -> bool:
    """Redis sliding window rate limiting (atomic INCR + EXPIRE)."""
    _cache = redis_client.cache
    if not _cache:
        return _check_rate_local(api_key, limit)
    try:
        bucket = f"ai:rate:{api_key}:{int(time.time() // _RATE_WINDOW)}"
        pipe = _cache.pipeline()
        pipe.incr(bucket)
        pipe.expire(bucket, _RATE_WINDOW * 2)
        count, _ = pipe.execute()
        return int(count) <= limit
    except Exception as e:
        logger.warning(f"[RATE] Redis rate check hatası: {e}, in-process fallback")
        return _check_rate_local(api_key, limit)


def check_rate_limit(api_key: str, plan: str) -> bool:
    """Planın dakika limitini kontrol et. True = istek kabul, False = limit aşıldı."""
    limit = _PLAN_LIMITS.get(plan, _PLAN_LIMITS["free"])
    return _check_rate_redis(api_key, limit)


# ── API Key doğrulama: Redis → .env fallback → hard-coded fallback ──────────
# Öncelik sırası:
#   1. Redis'te ai:apikey:<key> hash'i varsa → oradan oku
#   2. Env değişkenleri: API_KEY_<KEY>=name:plan formatında
#   3. Hard-coded fallback (sadece geliştirme ortamı için)

def _load_client_from_redis(api_key: str) -> dict | None:
    """Redis'ten müşteri bilgisini al. Hash key: ai:apikey:<key>"""
    _cache = redis_client.cache
    if not _cache:
        return None
    try:
        data = _cache.hgetall(f"ai:apikey:{api_key}")
        if data and data.get("name"):
            return {"name": data["name"], "plan": data.get("plan", "free")}
    except Exception as e:
        logger.warning(f"[AUTH] Redis key lookup hatası: {e}")
    return None


def _load_client_from_env(api_key: str) -> dict | None:
    """Env değişkeninden müşteri yükle.
    Format: API_KEY_MYKEY123=Okul Adı:gold  veya  API_KEY_MYKEY123=Okul Adı
    """
    # Env key'i: API_KEY_ + büyük harf ve alt çizgi
    safe = api_key.upper().replace("-", "_")
    val = os.getenv(f"API_KEY_{safe}", "")
    if not val:
        return None
    parts = val.split(":", 1)
    name = parts[0].strip()
    plan = parts[1].strip() if len(parts) > 1 else "free"
    return {"name": name, "plan": plan}


# Geliştirme ortamı fallback key'leri — production'da bu dict boş bırakılabilir
# ya da tamamen kaldırılabilir.
_DEV_FALLBACK_KEYS: dict[str, dict] = {
    "test_key": {"name": "Test Lisesi", "plan": "free"},
}
# Production'da fallback'i devre dışı bırakmak için env değişkeni:
_ALLOW_DEV_FALLBACK = os.getenv("ALLOW_DEV_FALLBACK", "true").lower() == "true"
# Anonim erişime izin ver/verme:
_ALLOW_ANONYMOUS   = os.getenv("ALLOW_ANONYMOUS",    "true").lower() == "true"


async def get_current_dershane(api_key: str = Security(api_key_header)) -> dict:
    """API key doğrulama + rate limiting.

    Doğrulama sırası:
    1. Redis'te kayıtlı key
    2. Env değişkeni (API_KEY_<KEY>=isim:plan)
    3. Dev fallback dict (ALLOW_DEV_FALLBACK=true ise)
    4. Anonim izin (ALLOW_ANONYMOUS=true ve key yoksa)
    """
    client_info: dict | None = None

    if api_key:
        # 1. Redis
        client_info = _load_client_from_redis(api_key)
        # 2. Env
        if not client_info:
            client_info = _load_client_from_env(api_key)
        # 3. Dev fallback
        if not client_info and _ALLOW_DEV_FALLBACK:
            client_info = _DEV_FALLBACK_KEYS.get(api_key)

    if not client_info:
        if _ALLOW_ANONYMOUS:
            client_info = {"name": "Anonim Kullanıcı", "plan": "guest"}
        else:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="❌ Geçersiz veya eksik API Anahtarı"
            )

    # Rate limiting
    key_for_rate = api_key or "anonymous"
    if not check_rate_limit(key_for_rate, client_info["plan"]):
        limit = _PLAN_LIMITS.get(client_info["plan"], 5)
        logger.warning(f"[RATE] 🚫 Limit aşıldı: {client_info['name']} ({client_info['plan']}) — {limit} req/dak")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"⏱️ Dakika limiti aşıldı ({limit} istek/dak). Lütfen bekleyin.",
            headers={"Retry-After": str(_RATE_WINDOW)},
        )

    return client_info
