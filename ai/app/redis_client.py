"""
app/redis_client.py — Paylaşılan Redis bağlantısı.

Cache (answer_cache), rate limiting ve API key yönetimi (auth) ile
admin endpoint'leri (admin) bu tek client'ı `redis_client.cache` üzerinden
call-time'da referanslar. Böylece testler `redis_client.cache`'i tek noktadan
mock'layabilir.
"""
import os

import redis as _redis_sync

_REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
_CACHE_TTL_SECONDS = int(os.getenv("AI_CACHE_TTL", "21600"))  # 6 saat default

try:
    cache = _redis_sync.from_url(_REDIS_URL, decode_responses=True, socket_timeout=2)
    cache.ping()
    print(f"[CACHE] ✅ Redis cache aktif (TTL={_CACHE_TTL_SECONDS}sn)")
except Exception as _e:
    print(f"[CACHE] ⚠️ Redis bağlanamadı, cache devre dışı: {_e}")
    cache = None
