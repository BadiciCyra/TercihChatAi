# gate.py (Final B2B Gateway - Deep Search ReAct Architecture)
import time
import logging
import json
import hashlib
import os
import re
from fastapi import FastAPI, Depends, Security, HTTPException, status, Request
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from typing import Literal, Optional
from pydantic import BaseModel, field_validator

# Bizim oluşturduğumuz modüller
from app.graph import app_graph, select_entry_point  # Modüler LangGraph — graph.py
from app.callbacks import B2BTokenTracker # Token Sayacı
from app.monitoring import (
    REGISTRY,
    record_cache_hit,
    record_cache_miss,
    record_cache_set,
    record_error,
    record_request,
)

# --- CACHE: Redis ile aynı/benzer soruları cache'le (LLM çağrısı yapmadan döner) ---
import redis as _redis_sync
_REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
_CACHE_TTL_SECONDS = int(os.getenv("AI_CACHE_TTL", "21600"))  # 6 saat default
try:
    _cache = _redis_sync.from_url(_REDIS_URL, decode_responses=True, socket_timeout=2)
    _cache.ping()
    print(f"[CACHE] ✅ Redis cache aktif (TTL={_CACHE_TTL_SECONDS}sn)")
except Exception as _e:
    print(f"[CACHE] ⚠️ Redis bağlanamadı, cache devre dışı: {_e}")
    _cache = None


def _normalize_for_cache(text: str) -> str:
    """Benzer soruları aynı cache key'e eşle: küçült, fazla boşluk kaldır,
    yaygın typo'ları normalize et.

    NOT: Kelimeler SIRALAMA olmadan korunur — Türkçe'de kelime sırası anlam
    değiştirir ("yazılım geliştirme nasıl" ≠ "nasıl yazılım geliştirme bölümü").
    Sadece küçük harf + Türkçe ASCII dönüşümü + noktalama temizliği yapılır.
    """
    if not text:
        return ""
    t = text.lower().strip()
    # Türkçe karakterleri ASCII'ye çevir (typo toleransı)
    tr_map = str.maketrans("çğıöşüâî", "cgiosuai")
    t = t.translate(tr_map)
    # Sadece alfanumerik + boşluk kalsın
    t = re.sub(r'[^a-z0-9\s]', ' ', t)
    # Çoklu boşlukları teke indir — kelime sırasını KORU (sıralama yok)
    return ' '.join(t.split())


# Cache namespace versiyon: değiştirilirse tüm eski cache geçersiz olur (auto-invalidation)
# v4: score_type suffix toleransı + web supplement zorunlu
# v5: kelime sıralama normalizasyonu kaldırıldı, casual cevaplar cache'lenmiyor
# v6: uni_info_node spesifik sorgu düzeltmesi, planner max 2 alt soru
_CACHE_NS = os.getenv("AI_CACHE_NS", "v6")

# Cache'lenmemesi gereken "kötü cevap" göstergeleri (bunlar cache'e yazılmaz)
_BAD_ANSWER_MARKERS = (
    "bulunamadı",
    "kriterlere uygun",
    "teknik bir sorun",
    "an error occurred",
    "hata oluştu",
    "tekrar dene",
    "tekrar sorar mısınız",
    "yeterli kaynak",          # "web'den yeterli kaynak çıkmadı" fallback mesajları
    "yeterli bilgi",           # "yeterli bilgi toplayamadım" fallback mesajları
    "web araması yapamıyorum", # servis hatası fallback'leri
    # Casual/sohbet cevapları — bunlar kullanıcıya özel, cache'e girmemeli
    "kanka ",
    "kanka,",
    "üzülme",
    "hayal kırıklığı",
    "yalnız değilsin",
    "birlikte bakalım",
    "beraber bakalım",
)


def _is_bad_answer(answer: str) -> bool:
    if not answer:
        return True
    a = answer.lower()
    return any(m in a for m in _BAD_ANSWER_MARKERS)


# Soruda geçen spesifik konu kelimeleri — cevabın başlığında yoksa cache geçersiz say
_TOPIC_KEYWORDS = {
    "kulüp": ["kulüp", "topluluk", "sosyal"],
    "yurt": ["yurt", "barınak", "konaklama"],
    "staj": ["staj", "pratik", "internship"],
    "burs": ["burs", "indirim", "ücretsiz"],
    "ücret": ["ücret", "fiyat", "tl", "para"],
    "puan": ["puan", "taban", "sıralama"],
    "yorum": ["yorum", "deneyim", "öğrenci", "memnun"],
    "kariyer": ["kariyer", "iş", "mezun", "istihdam"],
    "müfredat": ["müfredat", "ders", "program", "eğitim"],
}


def _is_relevant_to_query(query: str, answer: str) -> bool:
    """Cache'ten gelen cevabın soruyla alakalı olup olmadığını kontrol et.

    Soruda spesifik bir konu kelimesi varsa (kulüp, yurt, staj vb.),
    cevabın ilk 800 karakterinde (başlık/özet kısmında) o konuya dair
    bir kelime geçmiyorsa alakasız say → cache miss.
    """
    q = query.lower()
    a_head = answer.lower()[:800]  # Sadece başlık/özet kısmına bak

    for topic, indicators in _TOPIC_KEYWORDS.items():
        if topic in q:
            if not any(ind in a_head for ind in indicators):
                print(f"[CACHE] ⚠️ Alakasız cache: soruda '{topic}' var ama cevap başında yok")
                return False
    return True


_MODE_PREFIX: dict = {
    "wizard":   "wiz",
    "research": "res",
    "career":   "car",
    None:       "ner",
    # guidance: cache kullanılmaz — _cache_key None döner
}


def _cache_key(mode: Optional[str], query: str) -> Optional[str]:
    """Mode-prefixed cache key üret.

    guidance modunda None döner (cache skip sinyali):
    cache_get / cache_set çağrıları None sonucunda işlemi atlamalıdır.

    Diğer modlar için format:
        "ai:answer:{_CACHE_NS}:{prefix}:{md5(normalized_query)}"
    Örnekler:
        wizard   → "ai:answer:v6:wiz:<md5>"
        research → "ai:answer:v6:res:<md5>"
        career   → "ai:answer:v6:car:<md5>"
        None/NER → "ai:answer:v6:ner:<md5>"
        guidance → None  (cache skip)
    """
    if mode == "guidance":
        return None
    prefix = _MODE_PREFIX.get(mode, "ner")
    norm = _normalize_for_cache(query)
    return f"ai:answer:{_CACHE_NS}:{prefix}:{hashlib.md5(norm.encode()).hexdigest()}"


def cache_get(query: str, mode: Optional[str] = None) -> "str | None":
    if not _cache:
        return None
    key = _cache_key(mode, query)
    if key is None:
        # guidance modu veya başka cache-skip sinyali — okuma atla
        return None
    try:
        val = _cache.get(key)
        if val and _is_bad_answer(val):
            # Eski "bulunamadı" tarzı cache'i sil, taze cevap üretsin
            _cache.delete(key)
            print(f"[CACHE] 🗑️ Eski kötü cevap silindi: '{query[:50]}'")
            record_cache_miss()
            return None
        if val and not _is_relevant_to_query(query, val):
            # Cevap soruyla alakasız — sil ve yeniden hesaplat
            _cache.delete(key)
            print(f"[CACHE] 🗑️ Alakasız cache silindi: '{query[:50]}'")
            record_cache_miss()
            return None
        if val:
            print(f"[CACHE] ⚡ HIT: '{query[:50]}' → cache'ten dönülüyor")
            record_cache_hit()
        return val
    except Exception as e:
        print(f"[CACHE] get hatası: {e}")
        record_error(type(e).__name__, source="cache_get")
        return None


def cache_set(query: str, answer: str, mode: Optional[str] = None) -> None:
    if not _cache or not answer:
        return
    key = _cache_key(mode, query)
    if key is None:
        # guidance modu veya başka cache-skip sinyali — yazma atla
        return
    if _is_bad_answer(answer):
        print(f"[CACHE] ⛔ Kötü cevap (bulunamadı/hata) cache'lenmedi: '{query[:50]}'")
        return
    # Çok kısa yanıtları cache'leme — fallback/hata mesajları genellikle kısadır
    if len(answer) < 200:
        print(f"[CACHE] ⛔ Çok kısa yanıt ({len(answer)} byte) cache'lenmedi: '{query[:50]}'")
        return
    try:
        _cache.setex(key, _CACHE_TTL_SECONDS, answer)
        print(f"[CACHE] 💾 SET: '{query[:50]}' ({len(answer)} byte, {_CACHE_TTL_SECONDS}sn)")
        record_cache_set()
    except Exception as e:
        print(f"[CACHE] set hatası: {e}")
        record_error(type(e).__name__, source="cache_set")

# --- 1. AYARLAR VE LOGLAMA ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("GATEWAY")

app = FastAPI(title="Rehber Asistan B2B API")

# --- 2. MIDDLEWARE (Hız Ölçer & CORS) ---
class TimingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        response = None
        status_code = 500
        try:
            response = await call_next(request)
            status_code = getattr(response, "status_code", 200)
            return response
        finally:
            process_time = time.time() - start_time
            if response is not None:
                response.headers["X-Process-Time"] = str(process_time)
            record_request(
                endpoint=request.url.path,
                status=str(status_code),
                duration_seconds=process_time,
                cached=bool(getattr(request.state, "cache_hit", False)),
            )
            if status_code >= 500:
                record_error(f"http_{status_code}", source=request.url.path)

app.add_middleware(TimingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 3. GÜVENLİK (API KEY) + RATE LIMITING ---
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


# --- 🔧 YÖNETİM ENDPOINTLERİ (Redis key yönetimi) ---
# Bu endpointler internal kullanım içindir. Production'da ayrı bir admin key ile koruyun.

_ADMIN_KEY = os.getenv("ADMIN_API_KEY", "")

@app.post("/admin/keys", include_in_schema=False)
async def add_api_key(
    key: str, name: str, plan: str = "free",
    admin: str = Security(APIKeyHeader(name="X-Admin-Key", auto_error=False))
):
    """Yeni bir API key ekle veya güncelle (Redis'e yazar)."""
    if _ADMIN_KEY and admin != _ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Admin key geçersiz")
    if not _cache:
        raise HTTPException(status_code=503, detail="Redis bağlantısı yok")
    _cache.hset(f"ai:apikey:{key}", mapping={"name": name, "plan": plan})
    logger.info(f"[ADMIN] ✅ Key eklendi/güncellendi: {key} → {name} ({plan})")
    return {"ok": True, "key": key, "name": name, "plan": plan}


@app.delete("/admin/keys/{key}", include_in_schema=False)
async def delete_api_key(
    key: str,
    admin: str = Security(APIKeyHeader(name="X-Admin-Key", auto_error=False))
):
    """Bir API key'i sil."""
    if _ADMIN_KEY and admin != _ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Admin key geçersiz")
    if not _cache:
        raise HTTPException(status_code=503, detail="Redis bağlantısı yok")
    deleted = _cache.delete(f"ai:apikey:{key}")
    return {"ok": bool(deleted), "key": key}

@app.get("/admin/keys", include_in_schema=False)
async def list_api_keys(
    admin: str = Security(APIKeyHeader(name="X-Admin-Key", auto_error=False))
):
    """Kayıtlı tüm API key'leri listele."""
    if _ADMIN_KEY and admin != _ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Admin key geçersiz")
    if not _cache:
        raise HTTPException(status_code=503, detail="Redis bağlantısı yok")
    keys = _cache.keys("ai:apikey:*")
    result = []
    for k in keys:
        data = _cache.hgetall(k)
        result.append({"key": k.replace("ai:apikey:", ""), **data})
    return {"keys": result, "count": len(result)}

# --- 4. İSTEK MODELİ ---
ChatMode = Literal["wizard", "research", "career", "guidance"]

class AskRequest(BaseModel):
    query: str
    session_id: str = "default_session"
    mode: Optional[ChatMode] = None  # None → NER otomatik yönlendirme

    @field_validator("mode", mode="before")
    @classmethod
    def validate_mode(cls, v):
        if v is None:
            return None
        if v not in ("wizard", "research", "career", "guidance"):
            raise ValueError(
                f"Geçersiz mod: '{v}'. "
                "Kabul edilen değerler: 'wizard', 'research', 'career', 'guidance'."
            )
        return v

# --- 5. ANA ENDPOINT ---
@app.post("/b2b/ask_intelligent")
async def ask_intelligent_system(
    request: Request,
    client_info: dict = Depends(get_current_dershane)
):
    # İstek gövdesini esnekçe işle (string, dict, nested)
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid JSON body"
        )

    # 1) Bazı istemciler { "input": {...} } şeklinde sarıyor
    if isinstance(payload, dict) and "input" in payload:
        payload = payload["input"]

    # 2) Bazen input bir STRING geliyor: "{ query: '...', session_id: 'default_session' }"
    if isinstance(payload, str):
        s = payload.strip()

        # Tek tırnak -> çift tırnak (JSON'a yaklaştır)
        s = s.replace("'", "\"")

        # Key'ler quote'suz gelebilir: { query: "x", session_id: "y" }
        # En yaygın iki key için hızlı normalize
        # Not: "query" / "session_id" dışında farklı alan beklemiyoruz.
        s = s.replace("{ query", "{\"query\"")
        s = s.replace(", query", ", \"query\"")
        s = s.replace("{ session_id", "{\"session_id\"")
        s = s.replace(", session_id", ", \"session_id\"")
        s = s.replace(": default_session", ": \"default_session\"")

        try:
            payload = json.loads(s)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Input should be a valid dictionary or object to extract fields from"
            )

    # 3) Son kontrol: payload dict olmalı
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Input should be a valid dictionary or object to extract fields from"
        )

    # 4) AskRequest'e parse et (senin mevcut şeman)
    try:
        req = AskRequest(**payload)
    except Exception:
        record_error("validation_error", source="ask_intelligent")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Input should be a valid dictionary or object to extract fields from"
        )

   
    # request.json() bir kez okundu (yukarıda payload olarak).
    # req zaten yukarıda AskRequest(**payload) ile parse edildi.
    # Eski "robust body parsing" bloğu kaldırıldı — çift okuma sorunu yarattığından
    # body her zaman boş geliyordu.

    school_name = client_info["name"]
    logger.info(f"📨 [İSTEK] Kurum: {school_name} | Soru: {req.query}")
    request.state.cache_hit = False

    # ⚡ CACHE CHECK: Aynı/benzer soru cache'te varsa LLM çağrısı yapma
    # _cache_key(mode, query) None dönerse (guidance) cache tamamen atlanır
    cached_answer = cache_get(req.query, mode=req.mode)
    if cached_answer:
        request.state.cache_hit = True
        return {
            "answer": cached_answer,
            "school": school_name,
            "cached": True,
            "usage": {"total_tokens": 0},
        }
    record_cache_miss()

    # A. Token Takipçisini Başlat (Callback)
    tracker = B2BTokenTracker(school_name=school_name, session_id=req.session_id)

    # B. LangGraph Config Hazırla
    # thread_id: Her istek kendi izole thread'inde çalışır.
    # Checkpointer'daki geçmiş konuşmalar farklı sorulara sızmasın diye
    # her istek için benzersiz bir thread_id üretiyoruz.
    # Kullanıcının session_id'si rate-limit ve token takibi için kullanılıyor,
    # ama LangGraph state'i izole tutmak için request-scoped id gerekli.
    import uuid as _uuid
    request_thread_id = f"{req.session_id}:{_uuid.uuid4().hex}"
    config = {
        "configurable": {"thread_id": request_thread_id},
        "callbacks": [tracker]
    }

    try:
        # C. DEEP SEARCH REACT AGENT'I ÇALIŞTIR
        # Akış: NER → Planner → Agent ⟺ Tools → Evaluator → END

        entry_node = select_entry_point(req.mode)
        logger.info(f"[PIPELINE_ROUTER] mode={req.mode!r} → entry={entry_node!r}")

        inputs = {
            "messages": [("user", req.query)],
            "mode": req.mode,            # mod seçimi — AgentState'e taşınır
            "ner_context": {},           # NER node tarafından doldurulacak
            "iteration_count": 0,        # Sonsuz döngü koruması için
            # Deep Search alanları
            "query_plan": None,          # QueryPlanner tarafından doldurulacak
            "thinking_steps": [],        # Düşünce adımları
            "sub_question_results": {},  # Alt soru sonuçları
            "current_answer": None,      # Güncel cevap
            "evaluation": None,          # SelfEvaluation sonucu
            "search_depth": 0            # Arama derinliği
        }

        # Await ile sonucun gelmesini bekle
        result = await app_graph.ainvoke(inputs, config=config)

        # D. CEVABI AL — son mesajdan başlayarak geriye doğru anlamlı içerik ara
        final_answer = ""
        messages_list = result.get("messages", [])

        # Önce current_answer state'ini kontrol et (evaluator buraya yazıyor)
        if result.get("current_answer"):
            final_answer = result["current_answer"]
        else:
            from langchain_core.messages import ToolMessage as _ToolMessage, HumanMessage as _HumanMessage
            # Geriye doğru tara: boş, tool_call, ToolMessage ve HumanMessage'ları atla
            for msg in reversed(messages_list):
                # Tuple mesajları (user input) atla — kullanıcının sorusu, AI cevabı değil
                if isinstance(msg, tuple):
                    continue

                # HumanMessage'ları atla
                if isinstance(msg, _HumanMessage):
                    continue

                # ToolMessage'ları atla (tool çıktısı, AI cevabı değil)
                if isinstance(msg, _ToolMessage):
                    continue

                content = ""
                if hasattr(msg, "content"):
                    content = msg.content or ""

                # Tool call'ı olan ama içeriği boş AIMessage'ları atla
                if hasattr(msg, "tool_calls") and msg.tool_calls and not content:
                    continue

                if content and content.strip():
                    final_answer = content
                    break

        if not final_answer:
            logger.warning(f"⚠️ Tüm mesajlar tarandı ama anlamlı içerik bulunamadı. Toplam mesaj: {len(messages_list)}")
            final_answer = "Şu an bu soruya yanıt üretemiyorum, lütfen tekrar dene."

        # Normalize agent output for frontend: frontend expects a plain
        # markdown string (or JSON with `answer`/`message` as string).
        # Some agent flows return structured JSON like:
        # {"answer": [{"type":"text","text":"..."}, ...]}
        # Convert that shape into a single markdown string so the
        # frontend's `parseAiText` can handle it.
        def _extract_text(item) -> str:
            if item is None:
                return ""
            if isinstance(item, str):
                return item
            if isinstance(item, list):
                parts = [_extract_text(i) for i in item]
                parts = [p for p in parts if p]
                return "\n\n".join(parts)
            if isinstance(item, dict):
                # If dict has 'answer' as primary
                if "answer" in item:
                    return _extract_text(item["answer"])
                # common text keys
                for k in ("text", "content", "message"):
                    v = item.get(k)
                    if isinstance(v, (str, list, dict)):
                        return _extract_text(v)
                # fallback: try to extract from any nested values
                for v in item.values():
                    t = _extract_text(v)
                    if t:
                        return t
                return ""
            # last resort
            try:
                return str(item)
            except Exception:
                return ""

        def _normalize_answer(raw) -> str:
            if raw is None:
                return ""
            # If it's already a structured object
            if isinstance(raw, (dict, list)):
                out = _extract_text(raw)
                return out or json.dumps(raw, ensure_ascii=False)

            # If it's a string, maybe it's JSON encoded
            if isinstance(raw, str):
                s = raw.strip()
                if not s:
                    return ""
                try:
                    obj = json.loads(s)
                    return _normalize_answer(obj)
                except Exception:
                    return raw
            return str(raw)

        final_answer = _normalize_answer(final_answer)

        # 💾 CACHE SET: Bir daha aynı soru gelirse LLM çağırma.
        # cache_set zaten "bulunamadı/hata" içeren cevapları reddediyor.
        # _cache_key(mode, query) None dönerse (guidance) cache yazımı atlanır.
        if final_answer and len(final_answer) > 300:
            cache_set(req.query, final_answer, mode=req.mode)

        return {
            "answer": final_answer,
            "school": school_name,
            "cached": False,
            "usage": {
                "total_tokens": tracker.total_tokens
            }
        }

    except Exception as e:
        import traceback as _tb
        full_trace = _tb.format_exc()
        logger.error(f"💥 Sistem Hatası: {str(e)}\n{full_trace}")
        record_error(type(e).__name__, source="ask_intelligent")
        return {
            "answer": "Şu an teknik bir sorun yaşıyorum, lütfen tekrar dene.",
            "error": str(e),
            "error_type": type(e).__name__,
        }


@app.get("/metrics", include_in_schema=False)
async def metrics_endpoint():
    return {
        "metrics": REGISTRY.snapshot(),
        "text": REGISTRY.render_text(),
    }
