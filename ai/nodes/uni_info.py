import asyncio as _asyncio_for_decorators
import os as _os
from langchain_core.messages import AIMessage, SystemMessage, HumanMessage

from state import AgentState
from models import llm_responder
from prompts.uni_info import _UNI_INFO_SYSTEM_PROMPT
from utils.validators import get_msg_content, strip_react_thoughts
from utils.decorators import validate_node_input, validate_node_output, with_error_recovery
from utils.extractors import _extract_uni_from_text, _extract_uni_llm_fallback
from utils.context_assembly import build_grouped_context
from utils.link_fetcher import fetch_url_content
from nodes.fast_lookup import _ddg_multi_query

# Sosyal medya + faydasız domainler (fetch yapmaya değmez)
_SKIP_FETCH_DOMAINS = [
    "instagram.com", "facebook.com", "twitter.com", "tiktok.com", "pinterest.com",
    "youtube.com", "yandex.com", "google.com", "wikipedia.org",  # wiki snippet yeterli
]

# Fetch için öncelikli domainler (öğrenci yorumu / ücret / resmi bilgi açısından değerli)
_PRIORITY_FETCH_DOMAINS = [
    "eksisozluk.com", "sikayetvar.com", "unirehberi.com", "unipedia.co",
    "studyleo.com", "eleman.net", "ogr.info", "universiterehberi.com",
    "yok.gov.tr", "kariyer.net", "burs.com.tr", "bursbul.com",
    "ogrenci.net", "kampüs.com", "kampus.com",
]

def _pick_urls_to_fetch(buckets: list[list], max_urls: int = 2) -> list[str]:
    """DDG sonuçlarından fetch edilecek URL'leri seç.

    Önce öncelikli domainlerden sırala, sonra genel sonuçlardan tamamla.
    Sosyal medya ve faydasız domainleri atla. max_urls kadar döndür.
    max_urls 3→2: her fetch ~5-8s alıyor, 2 fetch = ~8s, 3 fetch = ~15s
    """
    seen: set[str] = set()
    priority: list[str] = []
    fallback: list[str] = []

    for bucket in buckets:
        for item in (bucket or []):
            url = item.get("url", "")
            if not url or url in seen:
                continue
            url_lower = url.lower()
            if any(d in url_lower for d in _SKIP_FETCH_DOMAINS):
                continue
            seen.add(url)
            if any(d in url_lower for d in _PRIORITY_FETCH_DOMAINS):
                priority.append(url)
            else:
                fallback.append(url)

    selected = (priority + fallback)[:max_urls]
    return selected


def _uni_info_cache_key(uni: str) -> str:
    """Redis cache key'i üret — üniversite adına göre."""
    return f"ai:uni_info:{uni.lower().strip()}"


def _load_uni_info_cache(key: str):
    """Redis'ten uni_info cache'i oku."""
    from utils.redis_cache import _ents_cache
    if not _ents_cache:
        return None
    try:
        val = _ents_cache.get(key)
        if val:
            print(f"[UNI_INFO] 💾 Cache HIT: {key}")
        return val
    except Exception as e:
        print(f"[UNI_INFO] ⚠️ Cache okuma hatası: {e}")
        return None


def _save_uni_info_cache(key: str, content: str, ttl_seconds: int = 43200) -> None:
    """Uni info sonucunu Redis'e kaydet (TTL: 12 saat)."""
    from utils.redis_cache import _ents_cache
    if not _ents_cache or not content:
        return
    try:
        _ents_cache.setex(key, ttl_seconds, content)
        print(f"[UNI_INFO] 💾 Cache WRITE: {key} (TTL={ttl_seconds}s)")
    except Exception as e:
        print(f"[UNI_INFO] ⚠️ Cache yazma hatası: {e}")


@validate_node_input
@validate_node_output
@with_error_recovery
async def uni_info_node(state: AgentState) -> dict:
    """
    Üniversite bilgi & yorum node'u.
    1. 4 paralel web araması yapar (genel, ücret, yorum, forum)
    2. Toplanan snippet'leri LLM'e verir
    3. LLM gerçek bir analiz/yorum üretir — sadece link listesi değil
    """
    messages = state.get("messages", [])
    user_text = get_msg_content(messages[-1]) if messages else ""
    ner_ctx = dict(state.get("ner_context") or {})

    # Üniversite adını bul — NER veya regex, son çare LLM
    uni = ner_ctx.get("uni") or _extract_uni_from_text(user_text) or ""
    if not uni:
        # LLM fallback — kısaltma tablosunda olmayan üniversiteler için
        uni = await _extract_uni_llm_fallback(user_text) or ""
    if not uni:
        return {"messages": [AIMessage(content="Hangi üniversite hakkında bilgi vermemi istiyorsun? Adını net şekilde yazar mısın?")]}

    print(f"[UNI_INFO] 🏫 Üniversite araştırılıyor: {uni}")

    # Genel soru ise cache kontrolü yap (spesifik sorular cache'lenmez — kullanıcıya özel)
    ner_web_query = ner_ctx.get("web_query", "")
    is_specific = ner_web_query and any(
        kw in user_text.lower()
        for kw in ['kulüp', 'yurt', 'staj', 'burs', 'ücret', 'kampüs', 'yemek', 'ulaşım', 'spor', 'müfredat', 'hoca']
    )

    if not is_specific:
        cache_key = _uni_info_cache_key(uni)
        cached = _load_uni_info_cache(cache_key)
        if cached:
            return {"messages": [AIMessage(content=cached)]}
    else:
        cache_key = None

    # Birinci sorgu: kullanıcının spesifik sorusu — NER'in ürettiği web_query öncelikli
    specific_query = ner_web_query or f"{uni} {user_text}"

    queries = [
        specific_query,                                                              # Spesifik soru
        f"{uni} öğrenci yorumları deneyimleri ekşi sözlük şikayetvar",              # Yorumlar
        f"{uni} genel bilgi akademik kadro kampüs eğitim kalitesi",                 # Genel
        f"{uni} burs olanakları KYK YÖK burs başarı bursu 2024 2025 öğrenim ücreti",  # Burs & Ücret
    ]

    raw_buckets: list[list] = []
    try:
        # 4 sorguyu tek HTTP isteğiyle retriever'a gönder (fast_lookup'taki _ddg_multi_query gibi)
        all_results = await _asyncio_for_decorators.wait_for(
            _ddg_multi_query(queries, max_results=8),
            timeout=12.0,
        )
        # Retriever tüm sonuçları flat döndürüyor; 4 bucket'a böl (her biri max 6)
        chunk = max(1, len(all_results) // 4)
        raw_buckets = [
            all_results[i * chunk:(i + 1) * chunk][:6]
            for i in range(4)
        ]
    except Exception as e:
        print(f"[UNI_INFO] 💥 Arama hatası: {e}")
        return {"messages": [AIMessage(content=f"## 🏫 {uni}\n\nŞu an web araması yapamıyorum, lütfen birazdan tekrar dene.")]}

    if not any(raw_buckets):
        return {"messages": [AIMessage(content=f"## 🏫 {uni}\n\nBu üniversite hakkında web'de yeterli kaynak bulamadım.")]}

    # ── URL FETCH: En değerli 2 linkin tam içeriğini çek ────────────────────
    urls_to_fetch = _pick_urls_to_fetch(raw_buckets, max_urls=2)
    fetched_sections: list[str] = []
    if urls_to_fetch:
        print(f"[UNI_INFO] 🔗 {len(urls_to_fetch)} URL fetch ediliyor: {urls_to_fetch}")
        try:
            fetch_results = await _asyncio_for_decorators.wait_for(
                _asyncio_for_decorators.gather(
                    *[fetch_url_content(u, timeout_seconds=5) for u in urls_to_fetch],
                    return_exceptions=True,
                ),
                timeout=12.0,  # 25s → 12s: 2 URL × 5s + overhead
            )
            for r in fetch_results:
                if isinstance(r, Exception):
                    continue
                if r.success and r.content and len(r.content.strip()) > 200:
                    # 3000 → 1500 karakter: LLM context boyutunu yarıya indir
                    snippet = r.content.strip()[:1500]
                    fetched_sections.append(f"[Tam İçerik — {r.url}]\n{snippet}")
                    print(f"[UNI_INFO] ✅ Fetch OK: {r.url} ({len(r.content)} karakter)")
        except Exception as e:
            print(f"[UNI_INFO] ⚠️ URL fetch hatası (devam ediliyor): {e}")
    # ────────────────────────────────────────────────────────────────────────

    labels = ["Konu Araştırması", "Öğrenci Yorumları", "Genel & Akademik", "Ücret & Burs"]

    assembly = build_grouped_context(
        raw_buckets,
        labels,
        heading=f"## 🏫 {uni}\n",
        max_entries_per_group=2,  # 3 → 2: LLM context boyutunu kısalt
        exclude_url_substrings=["instagram.com", "facebook.com", "twitter.com", "tiktok.com", "pinterest.com"],
    )

    if not assembly.context_blocks:
        return {"messages": [AIMessage(content=f"## 🏫 {uni}\n\nWeb'den yeterli kaynak çıkmadı.")]}

    web_context = "\n\n".join(assembly.context_blocks)

    # Fetch edilen tam içerikleri context'e ekle
    if fetched_sections:
        web_context += "\n\n### 📄 Tam Sayfa İçerikleri\n\n" + "\n\n".join(fetched_sections)
        print(f"[UNI_INFO] 📄 {len(fetched_sections)} tam sayfa içeriği context'e eklendi")
    fallback_parts: list[str] = assembly.fallback_parts.copy()
    print(f"[UNI_INFO] 📄 {len(assembly.context_blocks)} kategori toplandı, LLM synthesis başlıyor...")

    human_content = (
        f"Üniversite: {uni}\n"
        f"Kullanıcı sorusu: {user_text}\n"
        f"Soru tipi: {'SPESİFİK — sadece sorulan konuyu cevapla, genel üniversite analizi yapma' if is_specific else 'GENEL — tam üniversite analizi yap'}\n\n"
        f"Aşağıdaki web arama sonuçlarından EN ÖNEMLİ, EN GÜNCEL ve EN ALAKALI bilgileri seç. "
        f"Tekrar eden, çelişen veya alakasız kısımları atla. Seçtiğin bilgileri sentezleyerek analiz yaz:\n\n"
        f"{web_context}"
    )

    try:
        response = await llm_responder.ainvoke(
            [
                SystemMessage(content=_UNI_INFO_SYSTEM_PROMPT),
                HumanMessage(content=human_content),
            ],
        )
        answer = get_msg_content(response)
        answer = strip_react_thoughts(answer)
        if answer and len(answer.strip()) > 100:
            print(f"[UNI_INFO] ✅ LLM synthesis tamamlandı ({len(answer)} karakter)")
            # Genel sorgular için cache'e yaz
            if cache_key:
                _save_uni_info_cache(cache_key, answer)
            return {"messages": [AIMessage(content=answer)]}
        print(f"[UNI_INFO] ⚠️ LLM çok kısa yanıt verdi, fallback'e geçiliyor")
    except Exception as e:
        print(f"[UNI_INFO] ⚠️ LLM hatası, snippet fallback kullanılıyor: {e}")

    fallback_parts.append("### 💡 Tavsiye")
    fallback_parts.append(
        f"Yukarıdaki kaynakları inceleyerek **{uni}** hakkında "
        f"daha detaylı bilgi edinebilirsin. Spesifik bir bölüm veya "
        f"taban puanı için sormak istersen yazabilirsin."
    )
    return {"messages": [AIMessage(content="\n".join(fallback_parts))]}
