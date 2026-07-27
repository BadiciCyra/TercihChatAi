"""
app/user_quota.py — Forum kullanıcısı kimliği (imzalı token) + günlük soru kotası.

Akış:
  1. Forum, giriş yapmış kullanıcı için kısa ömürlü HS256 JWT üretir
     (payload: {"sub": "<kullanici_id>", "exp": <unix_ts>}).
  2. Chat bu token'ı `X-User-Token` header'ında gateway'e yollar.
  3. Burada imza doğrulanır ve kullanıcı başına GÜNLÜK kota uygulanır.

Neden session_id değil: session_id tarayıcıda üretiliyor ve "Yeni sohbet"te
sıfırlanıyor — kullanıcı F5'e basınca kotası yenilenirdi. İmzalı token
istemci tarafından uydurulamaz.

Kota sayacı Redis'te tutulur: ai:quota:<user_id>:<YYYY-MM-DD>
Redis yoksa in-process fallback kullanılır (tek process dev ortamı için).
"""
import logging
import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import HTTPException, Request, status

from app import redis_client

logger = logging.getLogger("GATEWAY")

# ── Ayarlar ────────────────────────────────────────────────────────────────
# Forum ile paylaşılan imza sırrı. Production'da MUTLAKA set edilmeli.
_TOKEN_SECRET = os.getenv("AI_USER_TOKEN_SECRET", "")
_TOKEN_HEADER = "X-User-Token"

# Günlük soru hakkı (kullanıcı başına)
DAILY_LIMIT = int(os.getenv("USER_DAILY_LIMIT", "25"))

# Token zorunlu mu? Forum entegrasyonu hazır olana kadar "false" bırakılır;
# false iken token yoksa istek eskisi gibi geçer (kota uygulanmaz).
_REQUIRE_TOKEN = os.getenv("REQUIRE_USER_TOKEN", "false").lower() == "true"

# Kota günü Türkiye saatine göre döner (gece yarısı TR).
_TR_TZ = timezone(timedelta(hours=3))

# Redis yoksa fallback sayaç: {(user_id, gun): adet}
_local_quota: dict[tuple[str, str], int] = {}


def _today_key() -> str:
    """Türkiye saatine göre YYYY-MM-DD."""
    return datetime.now(_TR_TZ).strftime("%Y-%m-%d")


def _seconds_until_midnight() -> int:
    """TR gece yarısına kalan saniye (sayaç TTL'i için)."""
    now = datetime.now(_TR_TZ)
    tomorrow = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return max(60, int((tomorrow - now).total_seconds()))


def verify_user_token(token: str) -> str | None:
    """İmzalı token'ı doğrula, kullanıcı id'sini döndür. Geçersizse None.

    `algorithms` açıkça HS256'ya sabitlenir — aksi halde "alg: none" saldırısı
    mümkün olurdu.
    """
    if not token or not _TOKEN_SECRET:
        return None
    try:
        payload = jwt.decode(token, _TOKEN_SECRET, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        logger.info("[QUOTA] Token süresi dolmuş")
        return None
    except Exception as e:
        logger.warning(f"[QUOTA] Token doğrulanamadı: {type(e).__name__}")
        return None

    user_id = payload.get("sub")
    if not user_id:
        return None
    return str(user_id)


def _incr_quota(user_id: str) -> int:
    """Kullanıcının bugünkü sayacını 1 artır ve yeni değeri döndür."""
    day = _today_key()
    _cache = redis_client.cache
    if not _cache:
        key = (user_id, day)
        _local_quota[key] = _local_quota.get(key, 0) + 1
        return _local_quota[key]
    try:
        bucket = f"ai:quota:{user_id}:{day}"
        pipe = _cache.pipeline()
        pipe.incr(bucket)
        pipe.expire(bucket, _seconds_until_midnight())
        count, _ = pipe.execute()
        return int(count)
    except Exception as e:
        logger.warning(f"[QUOTA] Redis sayaç hatası: {e}, in-process fallback")
        key = (user_id, day)
        _local_quota[key] = _local_quota.get(key, 0) + 1
        return _local_quota[key]


def get_quota_status(user_id: str) -> dict:
    """Sayacı ARTIRMADAN mevcut kullanımı döndür (bilgi amaçlı)."""
    day = _today_key()
    used = 0
    _cache = redis_client.cache
    if _cache:
        try:
            raw = _cache.get(f"ai:quota:{user_id}:{day}")
            used = int(raw) if raw else 0
        except Exception:
            used = _local_quota.get((user_id, day), 0)
    else:
        used = _local_quota.get((user_id, day), 0)
    return {"used": used, "limit": DAILY_LIMIT, "remaining": max(0, DAILY_LIMIT - used)}


async def enforce_daily_quota(request: Request) -> dict | None:
    """FastAPI dependency: token'ı doğrula ve günlük kotayı uygula.

    Dönüş: kullanıcı bilgisi ({user_id, used, remaining}) veya token yoksa None.
    Kota aşılmışsa 429 fırlatır.
    """
    token = request.headers.get(_TOKEN_HEADER, "")
    user_id = verify_user_token(token) if token else None

    if not user_id:
        if _REQUIRE_TOKEN:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Bu özelliği kullanmak için foruma giriş yapman gerekiyor.",
            )
        # Entegrasyon açılmadan önceki geçiş dönemi: kota uygulanmaz.
        return None

    count = _incr_quota(user_id)
    if count > DAILY_LIMIT:
        logger.warning(f"[QUOTA] 🚫 Günlük limit aşıldı: user={user_id} ({count}/{DAILY_LIMIT})")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Günlük {DAILY_LIMIT} soru hakkını doldurdun. "
                "Yarın tekrar bekleriz!"
            ),
            headers={
                "Retry-After": str(_seconds_until_midnight()),
                "X-Quota-Limit": str(DAILY_LIMIT),
                "X-Quota-Remaining": "0",
            },
        )

    return {
        "user_id": user_id,
        "used": count,
        "remaining": max(0, DAILY_LIMIT - count),
    }
