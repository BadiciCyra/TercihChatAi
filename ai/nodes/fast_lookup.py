import os
import re
import asyncio as _asyncio_for_decorators
from typing import Optional
from langchain_core.messages import AIMessage, HumanMessage

from state import AgentState
from models import llm_responder
from utils.validators import get_msg_content, strip_react_thoughts
from utils.decorators import validate_node_input, validate_node_output, with_error_recovery
from utils.redis_cache import _save_last_entities, _load_last_entities
from utils.context_assembly import build_link_supplement
from utils.link_fetcher import fetch_url_content, FetchResult
from utils.extractors import (
    _extract_ogretim_turu_from_text,
    _extract_rank_from_text,
    _extract_program_from_text,
    _extract_city_from_text,
    _extract_score_type_from_text,
    _extract_fee_from_text,
    _extract_uni_type_from_text,
    _scan_history_for_entities,
    _is_followup_question
)


def is_low_quality(snippet: str) -> bool:
    """Returns True if the snippet is shorter than 80 characters."""
    return len(snippet) < 80


async def _fetch_low_quality_supplements(
    links: list[dict],
    max_fetch: int = 2,
    timeout: float = 8.0,
) -> list[dict]:
    """Fetch full content for links whose snippet is < 80 chars.

    Identifies low-quality links (snippet < 80 chars), fetches at most
    max_fetch of them, and replaces their snippet with the first 1500 chars
    of fetched content on success. Skips on TimeoutError or any exception.
    Returns the (possibly modified) links list.
    """
    low_quality_indices = [
        i for i, link in enumerate(links)
        if is_low_quality(link.get("snippet", ""))
    ][:max_fetch]

    for i in low_quality_indices:
        link = links[i]
        url = link.get("url", "")
        try:
            result: FetchResult = await _asyncio_for_decorators.wait_for(
                fetch_url_content(url),
                timeout=timeout,
            )
            if result.success:
                links[i] = {**link, "snippet": result.content[:1500]}
        except (_asyncio_for_decorators.TimeoutError, Exception):
            continue  # skip this link, keep original snippet

    return links


async def _fast_yok_atlas_lookup(ner_ctx: dict) -> Optional[str]:
    """YÖK Atlas verisini retriever servisinden HTTP ile çeker."""
    _RETRIEVER_URL = os.getenv("RETRIEVER_URL", "http://ai-retriever:8000")
    payload = {}
    for src, dst in [("rank","rank"),("program","program"),("uni","uni"),("city","city"),
                     ("uni_type","uni_type"),("score_type","score_type"),("fee_type","fee_type"),
                     ("ogretim_turu","ogretim_turu")]:
        v = ner_ctx.get(src)
        if v:
            payload[dst] = v
    if not payload:
        return None
    print(f"[FAST_LOOKUP] 🔎 Retriever HTTP payload: {payload}")
    try:
        import aiohttp as _aiohttp
        async with _aiohttp.ClientSession() as session:
            async with session.post(
                f"{_RETRIEVER_URL}/retrieve/yok_atlas",
                json=payload,
                timeout=_aiohttp.ClientTimeout(total=60),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("markdown_table") or None
                print(f"[FAST_LOOKUP] ⚠️ HTTP {resp.status}")
                return None
    except Exception as e:
        print(f"[FAST_LOOKUP] 💥 retriever hatası: {e}")
        return None


async def _ddg_quick(query: str, max_results: int = 8) -> list:
    """Hızlı web araması — retriever servisine HTTP ile devreder."""
    _RETRIEVER_URL = os.getenv("RETRIEVER_URL", "http://ai-retriever:8000")
    try:
        import aiohttp as _aiohttp
        payload = {"query": query, "max_results": max_results, "rerank": False}
        async with _aiohttp.ClientSession() as session:
            async with session.post(
                f"{_RETRIEVER_URL}/retrieve/web_search",
                json=payload,
                timeout=_aiohttp.ClientTimeout(total=10),  # 15 → 10sn
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("results", [])
                print(f"[DDG_QUICK] ⚠️ HTTP {resp.status}")
                return []
    except _asyncio_for_decorators.TimeoutError:
        print(f"[DDG_QUICK] ⏰ Timeout: {query[:40]}")
        return []
    except Exception as e:
        print(f"[DDG_QUICK] 💥 {query[:40]}: {e}")
        return []


def _web_supplement_cache_key(program: str, uni: str, city: str) -> str:
    """Redis cache key'i üret — program+uni+city kombinasyonuna göre."""
    parts = [p.lower().strip() for p in [program, uni, city] if p]
    combined = "|".join(parts)
    return f"ai:web_sup:{combined}"


def _load_web_supplement_cache(key: str) -> Optional[str]:
    """Redis'ten web supplement cache'i oku."""
    from utils.redis_cache import _ents_cache
    if not _ents_cache:
        return None
    try:
        val = _ents_cache.get(key)
        if val:
            print(f"[FAST_WEB] 💾 Cache HIT: {key}")
        return val
    except Exception as e:
        print(f"[FAST_WEB] ⚠️ Cache okuma hatası: {e}")
        return None


def _save_web_supplement_cache(key: str, content: str, ttl_seconds: int = 21600) -> None:
    """Web supplement sonucunu Redis'e kaydet (TTL: 6 saat)."""
    from utils.redis_cache import _ents_cache
    if not _ents_cache or not content:
        return
    try:
        _ents_cache.setex(key, ttl_seconds, content)
        print(f"[FAST_WEB] 💾 Cache WRITE: {key} (TTL={ttl_seconds}s)")
    except Exception as e:
        print(f"[FAST_WEB] ⚠️ Cache yazma hatası: {e}")


async def _ddg_multi_query(queries: list[str], max_results: int = 8) -> list:
    """3 sorguyu tek HTTP isteğinde retriever'a gönder (queries array kullanır)."""
    _RETRIEVER_URL = os.getenv("RETRIEVER_URL", "http://ai-retriever:8000")
    try:
        import aiohttp as _aiohttp
        # queries array'ini kullan — retriever bunları paralel çalıştırır
        payload = {
            "query": queries[0],   # primary key (rerank için)
            "queries": queries,    # tüm sorgular tek istekte
            "max_results": max_results,
            "rerank": False,
        }
        async with _aiohttp.ClientSession() as session:
            async with session.post(
                f"{_RETRIEVER_URL}/retrieve/web_search",
                json=payload,
                timeout=_aiohttp.ClientTimeout(total=10),  # outer 12sn'nin altında
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("results", [])
                print(f"[DDG_MULTI] ⚠️ HTTP {resp.status}")
                return []
    except _asyncio_for_decorators.TimeoutError:
        print(f"[DDG_MULTI] ⏰ Timeout (queries: {[q[:30] for q in queries]})")
        return []
    except Exception as e:
        print(f"[DDG_MULTI] 💥 {e}")
        return []


async def _fast_web_supplement(ner_ctx: dict) -> Optional[str]:
    """Fast path için web'den ek bilgi: forum yorumları, hoca tavsiyeleri, linkler.

    Stabilite iyileştirmeleri:
    - Redis cache (TTL 6 saat): aynı program/uni kombinasyonu için DDG'yi tekrar çağırmaz
    - Tek HTTP isteği: 3 ayrı _ddg_quick yerine retriever'ın queries array'ini kullanır
    """
    program = ner_ctx.get("program") or ""
    uni = ner_ctx.get("uni") or ""
    city = ner_ctx.get("city") or ""

    if not (program or uni):
        print("[FAST_WEB] ⚠️ program/uni yok, atlanıyor")
        return None

    # 1. Cache kontrolü — aynı kombinasyon için DDG'yi tekrar çağırma
    cache_key = _web_supplement_cache_key(program, uni, city)
    cached = _load_web_supplement_cache(cache_key)
    if cached:
        return cached

    target_parts = [p for p in [program, uni, city] if p]
    target = " ".join(target_parts).strip()

    queries = [
        f"{target} öğrenci yorumları ekşi sözlük",
        f"{target} tavsiye hoca kalite forum",
        f"{target} hakkında deneyim üniversite",
    ]

    print(f"[FAST_WEB] 🔍 Web supplement (tek istek, {len(queries)} sorgu): {target}")

    # 2. 3 sorguyu tek HTTP isteğiyle gönder
    try:
        raw_results = await _ddg_multi_query(queries, max_results=8)
    except Exception as e:
        print(f"[FAST_WEB] 💥 Multi-query hatası: {e}")
        raw_results = []

    all_links = []
    seen_urls = set()
    _BLOCKED = {"instagram.com", "facebook.com", "twitter.com", "tiktok.com", "pinterest.com"}

    for item in raw_results:
        url = item.get("url", "")
        if not url or url in seen_urls:
            continue
        if any(d in url.lower() for d in _BLOCKED):
            continue
        seen_urls.add(url)
        all_links.append({
            "title": item.get("title", "Başlıksız"),
            "snippet": (item.get("snippet") or "")[:220],
            "url": url,
        })
        if len(all_links) >= 10:
            break

    print(f"[FAST_WEB] ✅ {len(all_links)} link toplandı")

    if not all_links:
        return None

    result = build_link_supplement(all_links)

    # 3. Başarılı sonucu cache'e yaz
    if result:
        _save_web_supplement_cache(cache_key, result)

    return result


@validate_node_input
@validate_node_output
@with_error_recovery
async def fast_lookup_node(state: AgentState, config: dict | None = None) -> dict:
    """Async hızlı yol: tek YÖK Atlas çağrısı + minimal LLM formatla. ~5-10sn."""
    # "Son arama" hafızası OTURUMA ÖZEL olmalı. Eskiden burada sabit
    # "default_session" kullanılıyordu; bu tüm kullanıcıları tek kovaya
    # topluyor ve takip sorularında başka kullanıcının bölümü/sıralaması
    # dönebiliyordu.
    # Kimlik state üzerinden gelir (gate.py inputs["session_id"]); LangGraph
    # config'i decorator sarmalayıcıları yüzünden node'a ulaşmıyor, o yüzden
    # config yalnızca yedek yol olarak kontrol edilir.
    session_key = str(state.get("session_id") or "")
    if not session_key:
        try:
            session_key = ((config or {}).get("configurable") or {}).get("thread_id") or ""
        except Exception:
            session_key = ""

    ner_ctx = dict(state.get("ner_context") or {})
    messages = state.get("messages", [])
    user_text = get_msg_content(messages[-1]) if messages else ""

    _OPEN_ENDED_PATTERNS = (
        'herhangi', 'herhangi bir', 'bir bölüm', 'hangi bölüm', 'ne gelebilir',
        'neler gelir', 'ne gelir', 'hangi bölümler', 'bölüm öner', 'öneri',
        'hangi üniversiteler', 'nereler gelir', 'ne yazabilirim',
        'yazabilir miyim', 'yazabiliyor muyum',
    )
    _is_open_ended = any(p in user_text.lower() for p in _OPEN_ENDED_PATTERNS)
    if _is_open_ended:
        print(f"[FAST_LOOKUP] 🔓 Open-ended sorgu tespit edildi — program regex bypass edildi")
        if not ner_ctx.get("rank"):
            v = _extract_rank_from_text(user_text)
            if v:
                ner_ctx["rank"] = v
                print(f"[FAST_LOOKUP] 🔧 Rank regex: {v}")
        if not ner_ctx.get("city"):
            v = _extract_city_from_text(user_text)
            if v:
                ner_ctx["city"] = v
                print(f"[FAST_LOOKUP] 🔧 City regex: {v}")
        if not ner_ctx.get("score_type"):
            v = _extract_score_type_from_text(user_text)
            if v:
                ner_ctx["score_type"] = v
                print(f"[FAST_LOOKUP] 🔧 Score type regex: {v}")
    else:
        if not ner_ctx.get("rank"):
            v = _extract_rank_from_text(user_text)
            if v:
                ner_ctx["rank"] = v
                print(f"[FAST_LOOKUP] 🔧 Rank regex: {v}")
        if not ner_ctx.get("program") and not ner_ctx.get("uni"):
            v = _extract_program_from_text(user_text)
            if v:
                ner_ctx["program"] = v
                print(f"[FAST_LOOKUP] 🔧 Program regex: {v}")
        elif ner_ctx.get("program"):
            # NER program doldurduysa bile kullanıcı metninden daha spesifik eşleşme varsa override et.
            # Örn: NER "Elektrik-Elektronik Mühendisliği" döndürdü ama kullanıcı
            # "havacılık elektrik elektroniği" yazdı → regex daha doğru eşleşir.
            regex_program = _extract_program_from_text(user_text)
            if regex_program and regex_program != ner_ctx["program"]:
                # Regex sonucu kullanıcı metnine daha yakın mı? Substring kontrolü.
                rp_lower = regex_program.lower()
                ut_lower = user_text.lower()
                ner_p_lower = ner_ctx["program"].lower()
                # Kullanıcı metni regex sonucunu, NER sonucundan daha fazla içeriyorsa override
                regex_words = set(w for w in rp_lower.split() if len(w) > 3)
                ner_words   = set(w for w in ner_p_lower.split() if len(w) > 3)
                regex_match_score = sum(1 for w in regex_words if w in ut_lower)
                ner_match_score   = sum(1 for w in ner_words   if w in ut_lower)
                if regex_match_score > ner_match_score:
                    print(f"[FAST_LOOKUP] 🔄 Program override: '{ner_ctx['program']}' → '{regex_program}' (regex daha iyi eşleşti)")
                    ner_ctx["program"] = regex_program
        if not ner_ctx.get("city"):
            v = _extract_city_from_text(user_text)
            if v:
                ner_ctx["city"] = v
                print(f"[FAST_LOOKUP] 🔧 City regex: {v}")
        if not ner_ctx.get("score_type"):
            v = _extract_score_type_from_text(user_text)
            if v:
                ner_ctx["score_type"] = v
                print(f"[FAST_LOOKUP] 🔧 Score type regex: {v}")
        if not ner_ctx.get("fee_type"):
            v = _extract_fee_from_text(user_text)
            if v:
                ner_ctx["fee_type"] = v
                print(f"[FAST_LOOKUP] 🔧 Fee regex: {v}")
        if not ner_ctx.get("uni_type"):
            v = _extract_uni_type_from_text(user_text)
            if v:
                ner_ctx["uni_type"] = v
                print(f"[FAST_LOOKUP] 🔧 Uni type regex: {v}")

    # Açık/Uzaktan öğretim isteği — NER'den bağımsız, her zaman metinden bakılır.
    # Bu programlar YÖK Atlas'ta ayrı öğrenim türü (id 203/182) ve sıralamaları
    # 1.6 milyona kadar çıkabiliyor; örgün havuzda aranınca sonuç çıkmıyordu.
    if not ner_ctx.get("ogretim_turu"):
        v = _extract_ogretim_turu_from_text(user_text)
        if v:
            ner_ctx["ogretim_turu"] = v
            print(f"[FAST_LOOKUP] 🎓 Öğretim türü regex: {v}")

    has_any_entity = any(ner_ctx.get(k) for k in ("rank", "program", "uni", "city", "ogretim_turu"))
    is_followup = _is_followup_question(user_text)
    print(f"[FAST_LOOKUP] 🔍 has_any_entity={has_any_entity}, is_followup={is_followup}, messages_len={len(messages)}")

    if is_followup:
        redis_ents = _load_last_entities(session_key)
        state_ents = _scan_history_for_entities(messages)
        merged = {**state_ents, **redis_ents}
        if merged:
            print(f"[FAST_LOOKUP] 🎯 Takip sorusu — geçmiş bağlam ÖNCELİKLİ kullanılıyor: {merged}")
            for k, v in merged.items():
                if v:
                    cur = ner_ctx.get(k)
                    if not cur:
                        ner_ctx[k] = v
                    elif k == "program" and isinstance(cur, str) and isinstance(v, str):
                        cur_lower = cur.lower().strip()
                        v_lower = v.lower().strip()
                        if cur_lower in v_lower and len(v) > len(cur) + 3:
                            print(f"[FAST_LOOKUP] 🔄 Program değiştirildi: '{cur}' → '{v}' (eski daha spesifik)")
                            ner_ctx[k] = v
    elif not has_any_entity:
        history_ents = _scan_history_for_entities(messages)
        if history_ents:
            print(f"[FAST_LOOKUP] 📜 State history kullanılıyor: {history_ents}")
            for k, v in history_ents.items():
                if v and not ner_ctx.get(k):
                    ner_ctx[k] = v

        still_missing = not any(ner_ctx.get(k) for k in ("rank", "program", "uni", "city"))
        if still_missing:
            redis_ents = _load_last_entities(session_key)
            if redis_ents:
                print(f"[FAST_LOOKUP] 🗄️ Redis last_ents kullanılıyor: {redis_ents}")
                for k, v in redis_ents.items():
                    if v and not ner_ctx.get(k):
                        ner_ctx[k] = v

    has_any_entity = any(ner_ctx.get(k) for k in ("rank", "program", "uni", "city"))
    if not has_any_entity:
        prior_summary = ""
        for msg in reversed(messages[:-1]):
            if isinstance(msg, AIMessage):
                content = get_msg_content(msg)
                if content and len(content) > 100:
                    lines = [l for l in content.splitlines() if l.strip()]
                    prior_summary = " ".join(lines[:3])[:400]
                    break

        if is_followup and prior_summary:
            helpful = (
                f"## 🤔 Takip Sorusu\n\n"
                f"'{user_text}' diye sordun ama hangi bölüm/sıralama hakkında olduğunu net anlamadım. "
                f"Önceki cevabımı bir kez daha yorumlamamı istersen, sorunu biraz daha açar mısın?\n\n"
                f"### 💡 Örnek\n"
                f"- *\"Bu listede başka şehirler de var mı?\"*\n"
                f"- *\"Daha düşük sıralamayla giren var mı?\"*\n"
                f"- *\"Sadece vakıf üniversitelerini göster\"*\n\n"
                f"Spesifik bir parametre verirsen (şehir, puan, bölüm, ücret) çok daha net cevaplayabilirim."
            )
        else:
            helpful = (
                f"## 👋 Selam!\n\n"
                f"Sana yardımcı olabilmem için lütfen şu bilgilerden en az birini ver:\n\n"
                f"- **🎓 Bölüm**: Hangi bölümü merak ediyorsun? (örn: *Bilgisayar Mühendisliği*, *Tıp*, *Hukuk*)\n"
                f"- **📊 Sıralama**: Sınav sıralamasın kaç? (örn: *50k*, *120 bin*, *250000*)\n"
                f"- **🏙️ Şehir**: Hangi şehirde okumak istersin? (örn: *İstanbul*, *Ankara*)\n"
                f"- **🏫 Üniversite**: Belirli bir üniversite mi merak ediyorsun? (örn: *İTÜ*, *Boğaziçi*)\n\n"
                f"### 💡 Örnek Sorular\n"
                f"- *\"50k say ile İstanbul'da bilgisayar mühendisliği nereler gelir?\"*\n"
                f"- *\"Koç Üniversitesi nasıl?\"*\n"
                f"- *\"%50 burslu Hukuk veren vakıf üniversiteler\"*"
            )
        return {"messages": [AIMessage(content=helpful)]}

    tool_result = await _fast_yok_atlas_lookup(ner_ctx)

    if isinstance(tool_result, str) and "bulunamadı" in tool_result.lower() and ner_ctx.get("program"):
        print("[FAST_LOOKUP] 🔁 İlk arama boş, rank'siz tekrar deniyorum...")
        ner_ctx_no_rank = {k: v for k, v in ner_ctx.items() if k != "rank"}
        tool_result = await _fast_yok_atlas_lookup(ner_ctx_no_rank)

    if not tool_result or (isinstance(tool_result, str) and len(tool_result.strip()) < 30):
        msg = (
            f"Bu sıralama/bölüm için YÖK Atlas'ta sonuç bulamadım. "
            f"Farklı bir bölüm veya sıralama dilimi denemek ister misin?"
        )
        return {"messages": [AIMessage(content=msg)]}

    _save_last_entities(session_key, ner_ctx)

    program_label = ner_ctx.get("program") or "Sonuçlar"
    rank_label = ner_ctx.get("rank")
    intro_bits = []
    if rank_label:
        intro_bits.append(f"~{int(rank_label):,}".replace(",", ".") + " sıralama dilimi")
    if ner_ctx.get("score_type"):
        intro_bits.append(ner_ctx["score_type"])
    intro = " · ".join(intro_bits) if intro_bits else "Aşağıdaki seçenekler"

    USE_LLM_FOR_FORMATTING = os.getenv("FAST_LOOKUP_USE_LLM", "false").lower() == "true"

    if not USE_LLM_FOR_FORMATTING:
        print(f"[FAST_LOOKUP] 📤 Final ner_ctx (score_type={ner_ctx.get('score_type')}): {ner_ctx}")
        print(f"[FAST_LOOKUP] ⚡ YÖK Atlas + web_supplement paralel çalışıyor...")

        async def _web_sup_safe():
            try:
                return await _asyncio_for_decorators.wait_for(
                    _fast_web_supplement(ner_ctx),
                    timeout=12.0,  # 20 → 12sn: iç timeout'lar (10sn HTTP) ile hiyerarşik
                )
            except _asyncio_for_decorators.TimeoutError:
                print(f"[FAST_LOOKUP] ⏰ Web supplement timeout (12s)")
                return None
            except Exception as e:
                import traceback as _tb
                print(f"[FAST_LOOKUP] ⚠️ Web supplement EXCEPTION: {e}")
                _tb.print_exc()
                return None

        web_supplement = await _web_sup_safe()
        print(f"[FAST_LOOKUP] Web supplement result: {'VAR (' + str(len(web_supplement)) + ' bytes)' if web_supplement else 'YOK (None)'}")

        direct_answer = (
            f"## 🎯 {program_label}\n\n"
            f"{intro} için seçenekler:\n\n"
            f"{tool_result}\n\n"
            f"### 💡 Tavsiye\n"
            f"Tablodaki sonuçlar güncel YÖK Atlas verilerine göre derlenmiştir (yıl için tablodaki 'Yıl' sütununa bakınız). "
            f"'🎯 Hedef' etiketli programlar senin sıralamana en yakındır; "
            f"'✅ Garanti' olanlar daha rahat tutar; '⛰️ Çok Zorla' olanlar ise hedeften iyi gerektirir.\n\n"
        )

        if web_supplement:
            direct_answer += web_supplement + "\n\n"
        else:
            direct_answer += (
                "### 🔍 Ek Bilgi\n"
                "Web yorum/forum aramaları şu an dönmedi. Tekrar dene veya spesifik bir üniversite/bölüm "
                "için yorum sor (örn: 'İTÜ bilgisayar yorumları').\n\n"
            )

        direct_answer += (
            f"### 📚 Kaynak\n"
            f"YÖK Atlas — https://yokatlas.yok.gov.tr"
        )
        return {"messages": [AIMessage(content=direct_answer)]}

    fast_prompt = f"""
Aşağıdaki YÖK Atlas tablosunu kullanıcıya AYNEN sun. Sadece şu yapıyı kullan:

## 🎯 {program_label}
{intro} için seçenekler:

[TABLO BURAYA — değiştirme, eksiltme]

### 💡 Kısa Yorum
(2-3 cümle: tablodaki en dikkat çekici noktalar)

YASAKLAR:
- ASLA "DÜŞÜN/PLAN/HAREKET ET/GÖZLEMLE" yazma.
- ASLA başka bölüm önerme (kullanıcı sadece {program_label} istedi).
- ASLA tablo dışı veri ekleme.
"""
    try:
        resp = llm_responder.invoke([HumanMessage(content=fast_prompt)])
        cleaned = strip_react_thoughts(get_msg_content(resp))
        return {"messages": [AIMessage(content=cleaned)]}
    except Exception as e:
        print(f"[FAST_LOOKUP] LLM hata, ham tablo dönülüyor: {e}")
        return {"messages": [AIMessage(content=f"## 🎯 {program_label}\n\n{tool_result}")]}
