# gate.py (Final B2B Gateway - Deep Search ReAct Architecture)
#
# Bu modül uygulamanın giriş noktasıdır (uvicorn gate:app). Sorumlulukları
# alt modüllere bölünmüştür:
#   - app/redis_client.py : paylaşılan Redis client
#   - app/answer_cache.py : cevap önbelleği (cache_get / cache_set)
#   - app/auth.py         : API key doğrulama + rate limiting
#   - app/admin.py        : Redis key yönetim endpoint'leri (APIRouter)
# Geriye dönük uyumluluk için bu alt modüllerin isimleri aşağıda re-export edilir.
import time
import logging
import json
from dataclasses import dataclass
from fastapi import FastAPI, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from typing import Literal, Optional
from pydantic import BaseModel, field_validator


@dataclass
class ThreadResolution:
    """Encapsulates the result of resolving a session_id to a LangGraph thread_id.

    thread_id             — the id to pass to the LangGraph checkpointer.
    is_anonymous          — True when a fresh anon thread was generated (no history).
    effective_session_id  — the value to echo back to the frontend as session_id.
    """
    thread_id: str
    is_anonymous: bool
    effective_session_id: str  # what to echo back to frontend


_MAX_SESSION_ID_LENGTH = 256


def resolve_thread_id(session_id: "str | None") -> ThreadResolution:
    """Resolve a frontend-supplied session_id to a LangGraph thread_id.

    Resolution rules (Requirements 1.1, 1.3, 1.4, 1.5, 1.6):
      - Valid session_id (non-empty, not "default_session", ≤256 chars):
          thread_id = session_id  (history preserved)
      - None, "", "default_session", or >256 chars:
          thread_id = f"anon:{uuid4().hex}"  (no history, unique per request)

    Returns a ThreadResolution with:
      - thread_id             : the id to use in the LangGraph config
      - is_anonymous          : True when a fresh anon thread was generated
      - effective_session_id  : what to echo back to the frontend
    """
    from uuid import uuid4

    is_invalid = (
        session_id is None
        or session_id == ""
        or session_id == "default_session"
        or len(session_id) > _MAX_SESSION_ID_LENGTH
    )

    if is_invalid:
        anon_thread_id = f"anon:{uuid4().hex}"
        return ThreadResolution(
            thread_id=anon_thread_id,
            is_anonymous=True,
            effective_session_id=anon_thread_id,
        )
    else:
        return ThreadResolution(
            thread_id=session_id,
            is_anonymous=False,
            effective_session_id=session_id,
        )


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

# ── Alt modüller ────────────────────────────────────────────────────────────
from app import redis_client
from app.admin import router as admin_router
# Geriye dönük uyumluluk re-export'ları (testler ve dış importlar app.gate.X bekler)
from app.answer_cache import (  # noqa: F401
    _normalize_for_cache,
    _CACHE_NS,
    _CACHE_TTL_SECONDS,
    _BAD_ANSWER_MARKERS,
    _is_bad_answer,
    _TOPIC_KEYWORDS,
    _is_relevant_to_query,
    _MODE_PREFIX,
    _cache_key,
    cache_get,
    cache_set,
)
from app.auth import (  # noqa: F401
    API_KEY_NAME,
    api_key_header,
    _PLAN_LIMITS,
    _RATE_WINDOW,
    _local_rate,
    _check_rate_local,
    _check_rate_redis,
    check_rate_limit,
    _load_client_from_redis,
    _load_client_from_env,
    _DEV_FALLBACK_KEYS,
    _ALLOW_DEV_FALLBACK,
    _ALLOW_ANONYMOUS,
    get_current_dershane,
)

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

# --- 3. ADMIN ENDPOINTLERİ (Redis key yönetimi) ---
# Endpoint tanımları app/admin.py içinde; buradan uygulamaya bağlanır.
app.include_router(admin_router)

# --- 4. İSTEK MODELİ ---
ChatMode = Literal["wizard", "research", "career", "guidance"]

# Test UI endpoint'i — tarayıcıdan direkt erişim için
@app.get("/test_ui.html", include_in_schema=False)
async def serve_test_ui():
    import os as _os
    from fastapi.responses import HTMLResponse
    html_path = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), "test_ui.html")
    if not _os.path.exists(html_path):
        # Fallback: /app/test_ui.html
        html_path = "/app/test_ui.html"
    try:
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "test_ui.html not found"}, status_code=404)

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
    from fastapi import HTTPException, status
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
    # thread_id: session_id geçerliyse (boş, None veya "default_session" değilse)
    # doğrudan thread_id olarak kullanılır → aynı oturumdaki tüm mesajlar aynı
    # LangGraph thread'inde (checkpointer memory'de) tutulur (Req 1.1, 1.2).
    # Geçersiz session_id durumunda her istek için benzersiz anon thread üretilir
    # (Req 1.3, 1.5, 1.6). Rate-limit ve token sayaçları hâlâ req.session_id
    # (orijinal değer) üzerinden tutulur (Req 1.7).
    thread_resolution = resolve_thread_id(req.session_id)
    config = {
        "configurable": {"thread_id": thread_resolution.thread_id},
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
            "session_id": thread_resolution.effective_session_id,
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
