"""
app/admin.py — Redis API key yönetim endpoint'leri (internal).

Bu endpoint'ler yönetim amaçlıdır ve OpenAPI şemasına dahil edilmez.
Production'da ADMIN_API_KEY env değişkeni ile korunmalıdır.
"""
import logging
import os

from fastapi import APIRouter, HTTPException, Security
from fastapi.security import APIKeyHeader

from app import redis_client

logger = logging.getLogger("GATEWAY")

router = APIRouter()

_ADMIN_KEY = os.getenv("ADMIN_API_KEY", "")
_admin_key_header = APIKeyHeader(name="X-Admin-Key", auto_error=False)


def _require_admin(admin: str) -> None:
    """Admin key doğrula; geçersizse 403 fırlat."""
    if _ADMIN_KEY and admin != _ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Admin key geçersiz")


@router.post("/admin/keys", include_in_schema=False)
async def add_api_key(
    key: str, name: str, plan: str = "free",
    admin: str = Security(_admin_key_header),
):
    """Yeni bir API key ekle veya güncelle (Redis'e yazar)."""
    _require_admin(admin)
    _cache = redis_client.cache
    if not _cache:
        raise HTTPException(status_code=503, detail="Redis bağlantısı yok")
    _cache.hset(f"ai:apikey:{key}", mapping={"name": name, "plan": plan})
    logger.info(f"[ADMIN] ✅ Key eklendi/güncellendi: {key} → {name} ({plan})")
    return {"ok": True, "key": key, "name": name, "plan": plan}


@router.delete("/admin/keys/{key}", include_in_schema=False)
async def delete_api_key(
    key: str,
    admin: str = Security(_admin_key_header),
):
    """Bir API key'i sil."""
    _require_admin(admin)
    _cache = redis_client.cache
    if not _cache:
        raise HTTPException(status_code=503, detail="Redis bağlantısı yok")
    deleted = _cache.delete(f"ai:apikey:{key}")
    return {"ok": bool(deleted), "key": key}


@router.get("/admin/keys", include_in_schema=False)
async def list_api_keys(
    admin: str = Security(_admin_key_header),
):
    """Kayıtlı tüm API key'leri listele."""
    _require_admin(admin)
    _cache = redis_client.cache
    if not _cache:
        raise HTTPException(status_code=503, detail="Redis bağlantısı yok")
    keys = _cache.keys("ai:apikey:*")
    result = []
    for k in keys:
        data = _cache.hgetall(k)
        result.append({"key": k.replace("ai:apikey:", ""), **data})
    return {"keys": result, "count": len(result)}
