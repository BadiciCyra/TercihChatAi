import os
import json as _json_ents

try:
    import redis as _redis_sync_ents
    _redis_url_ents = os.getenv("REDIS_URL", "redis://redis:6379/0")
    _ents_cache = _redis_sync_ents.from_url(_redis_url_ents, decode_responses=True, socket_timeout=2)
    _ents_cache.ping()
    print("[ENT_CACHE] ✅ Redis entity cache aktif")
except Exception as _e:
    print(f"[ENT_CACHE] ⚠️ Redis bağlanamadı: {_e}")
    _ents_cache = None


def _save_last_entities(session_id: str, ents: dict) -> None:
    """Başarılı bir aramadan sonra entity'leri Redis'e kaydet (1 saat TTL).

    Kayıt OTURUMA ÖZELDİR. session_id yoksa hiç yazılmaz — paylaşılan bir
    kovaya yazmak, takip sorularında başka kullanıcının bölüm/sıralamasının
    dönmesine yol açıyordu.
    """
    if not _ents_cache or not ents:
        return
    if not session_id:
        return
    try:
        clean = {k: v for k, v in ents.items() if v}
        if not clean:
            return
        key = f"ai:last_ents:{session_id}"
        _ents_cache.setex(key, 3600, _json_ents.dumps(clean, ensure_ascii=False))
        print(f"[ENT_CACHE] 💾 Saved ({session_id}): {clean}")
    except Exception as e:
        print(f"[ENT_CACHE] ⚠️ Save error: {e}")


def _load_last_entities(session_id: str) -> dict:
    """Redis'ten bu OTURUMA ait en son entity'leri al.

    Başka oturuma / paylaşılan kovaya düşme YOK: session_id boşsa boş döner.
    """
    if not _ents_cache or not session_id:
        return {}
    try:
        key = f"ai:last_ents:{session_id}"
        val = _ents_cache.get(key)
        if val:
            ents = _json_ents.loads(val)
            print(f"[ENT_CACHE] 📜 Loaded ({session_id}): {ents}")
            return ents
    except Exception as e:
        print(f"[ENT_CACHE] ⚠️ Load error: {e}")
    return {}
