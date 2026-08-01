import asyncio as _asyncio_for_decorators
import os as _os
import time as _time
from langchain_core.messages import AIMessage, SystemMessage, HumanMessage

from state import AgentState
from models import llm_responder
from prompts.uni_info import _UNI_INFO_SYSTEM_PROMPT
from utils.validators import get_msg_content, strip_react_thoughts
from utils.decorators import validate_node_input, validate_node_output, with_error_recovery
from utils.extractors import _extract_uni_from_text, _extract_uni_llm_fallback
from utils.query_planner import query_planner
from utils.context_assembly import (
    build_grouped_context,
    build_sources_block,
    build_documents_block,
)
from utils.link_fetcher import fetch_url_content
from utils.research_topics import (
    TOPICS,
    TOPIC_BY_KEY,
    build_topic_queries,
    score_url_for_topic,
    uni_tokens,
)
from nodes.fast_lookup import _ddg_multi_query

# ── Araştırma derinliği ayarları ────────────────────────────────────────────
# Sayfalar PARALEL çekildiği için sayfa sayısını artırmak süreyi doğrusal
# artırmaz; darboğaz en yavaş sayfadır. Env ile ayarlanabilir.
_MAX_FETCH_URLS = int(_os.getenv("RESEARCH_MAX_FETCH_URLS", "8"))
# Spesifik sorularda konu taraması yapılmadığı için bütçe tek konuya ayrılır;
# aynı süreye daha fazla sayfa sığar.
_MAX_FETCH_URLS_SPECIFIC = int(_os.getenv("RESEARCH_MAX_FETCH_URLS_SPECIFIC", "10"))
_MAX_CHARS_PER_PAGE = int(_os.getenv("RESEARCH_MAX_CHARS_PER_PAGE", "2000"))
_FETCH_TOTAL_TIMEOUT = float(_os.getenv("RESEARCH_FETCH_TIMEOUT", "20"))

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


# ── Paralel sentez ──────────────────────────────────────────────────────────
# Ölçüm (kariyer node'unda): süre neredeyse tamamen üretilen karakter sayısına
# bağlı. Cevabı iki gruba bölüp EŞZAMANLI üretmek 24.5 sn → 13.6 sn yaptı,
# hiçbir başlık kaybolmadan. Araştırma cevabı da aynı yapıda olduğu için
# aynı yöntem buraya taşındı.
_PARALLEL_SYNTHESIS = _os.getenv("RESEARCH_PARALLEL_SYNTHESIS", "true").lower() == "true"

_UNI_SECTION_GROUPS = (
    (
        "GİRİŞ + AKADEMİK",
        "1) Kısa selamlama/giriş paragrafı (2-3 cümle).\n"
        "2) ### 📌 Genel Bakış\n"
        "3) ### 🎓 Akademik Yapı & Eğitim Kalitesi\n"
        "4) ### 👨‍🏫 Akademik Kadro  (context'te veri varsa)\n"
        "5) ### 📖 Ders Programı & Müfredat  (context'te veri varsa)\n"
        "6) ### 🌍 Erasmus & Değişim  (context'te veri varsa)",
    ),
    (
        "MALİYET + DENEYİM + DEĞERLENDİRME",
        "1) ### 💰 Ücretler & Burs Olanakları\n"
        "2) ### 💬 Öğrenciler Ne Diyor?\n"
        "3) ### ⚖️ Artılar & Eksiler\n"
        "4) ### 💡 Kime Göre İyi, Kime Göre Değil?\n"
        "NOT: Selamlama/giriş YAZMA, doğrudan ilk başlıkla başla.",
    ),
)


async def _parallel_synthesis_uni(uni: str, user_text: str, web_context: str) -> str:
    """Araştırma cevabını iki gruba bölüp eşzamanlı üret, sonra birleştir."""
    async def _one(idx: int, talimat: str) -> tuple[int, str]:
        human = (
            f"Üniversite: {uni}\n"
            f"Kullanıcı sorusu: {user_text}\n\n"
            f"SADECE aşağıdaki başlıkları yaz — başka başlık AÇMA, "
            f"'Kaynaklar' bölümü YAZMA:\n{talimat}\n\n"
            f"Aşağıdaki web arama sonuçlarını kullan:\n\n{web_context}"
        )
        try:
            resp = await llm_responder.ainvoke([
                SystemMessage(content=_UNI_INFO_SYSTEM_PROMPT),
                HumanMessage(content=human),
            ])
            return idx, get_msg_content(resp) or ""
        except Exception as e:
            print(f"[UNI_INFO] ⚠️ Paralel parça {idx} hatası: {e}")
            return idx, ""

    t0 = _time.time()
    results = await _asyncio_for_decorators.gather(
        *[_one(i, t) for i, (_b, t) in enumerate(_UNI_SECTION_GROUPS)],
        return_exceptions=True,
    )
    parts = [r for r in results if isinstance(r, tuple) and r[1].strip()]
    parts.sort(key=lambda x: x[0])
    if not parts:
        raise RuntimeError("paralel sentez tüm parçalarda boş döndü")
    print(f"[UNI_INFO] ⚡ Paralel sentez: {len(parts)}/{len(_UNI_SECTION_GROUPS)} parça, "
          f"{_time.time() - t0:.1f} sn")
    return "\n\n".join(p[1].strip() for p in parts)


# Dar konu sinyalleri — biri geçiyorsa kullanıcı TEK bir başlık soruyor
_NARROW_UNI_KEYWORDS = (
    "yurt", "burs", "ücret", "ucret", "harç", "harc", "kulüp", "kulup",
    "staj", "ulaşım", "ulasim", "yemekhane", "kütüphane", "kutuphane",
    "kayıt", "kayit", "hazırlık", "hazirlik", "değişim", "degisim", "erasmus",
    "taban puan", "kontenjan", "yatay geçiş", "yatay gecis",
)


def _looks_specific_uni(user_text: str, llm_flag: bool) -> bool:
    """QueryPlanner'ın is_specific kararını metin sinyalleriyle doğrula.

    LLM sınıflandırması güvenilmez: "İTÜ nasıl bir üniversite?" spesifik
    işaretlenmişti ve konu bazlı derin tarama hiç çalışmıyordu.
    """
    t = (user_text or "").lower()
    if any(k in t for k in _NARROW_UNI_KEYWORDS):
        return True
    if any(p in t for p in ("nasıl bir üniversite", "nasil bir universite",
                            "nasıl bir okul", "nasil bir okul",
                            "hakkında bilgi", "hakkinda bilgi",
                            "okunur mu", "tavsiye eder", "nasıl?", "nasil?")):
        return False
    return llm_flag


async def _search_by_topic(uni: str, bolum: str = "") -> dict[str, list]:
    """Her araştırma konusu için AYRI web araması yap (paralel).

    Eskiden tek bir sorgu havuzu vardı ve seçilen 2 sayfa genelde "genel
    tanıtım" sayfalarıydı; ders programı/Erasmus/memnuniyet hiç taranmıyordu.
    Konu başına ayrı arama, her başlığa kaynak düşmesini garantiler.

    Dönüş: {topic_key: [sonuç, ...]}
    """
    topic_queries = build_topic_queries(uni, bolum)

    async def _one(topic_key: str, queries: list[str]):
        try:
            res = await _asyncio_for_decorators.wait_for(
                _ddg_multi_query(queries, max_results=6),
                timeout=12.0,
            )
            return topic_key, (res or [])
        except Exception as e:
            print(f"[UNI_INFO] ⚠️ '{topic_key}' araması başarısız: {e}")
            return topic_key, []

    pairs = await _asyncio_for_decorators.gather(
        *[_one(k, qs) for k, qs in topic_queries],
        return_exceptions=True,
    )
    out: dict[str, list] = {}
    for p in pairs:
        if isinstance(p, Exception):
            continue
        k, res = p
        out[k] = res
    return out


def _pick_urls_by_topic(topic_results: dict[str, list], max_total: int = 8,
                        uni_adi: str = "") -> list[tuple[str, str]]:
    """Konu bazlı URL seçimi — her konuya kendi kotası kadar sayfa ayır.

    Dönüş: [(topic_key, url), ...] — en fazla max_total adet.
    Tek havuzdan seçim yerine bu yöntem, popüler konunun diğerlerini
    ezmesini engeller.
    """
    seen: set[str] = set()
    picked: list[tuple[str, str]] = []
    toks = uni_tokens(uni_adi)

    for topic in TOPICS:
        results = topic_results.get(topic.key) or []
        scored = []
        for item in results:
            url = (item or {}).get("url", "")
            if not url or url in seen:
                continue
            if any(d in url.lower() for d in _SKIP_FETCH_DOMAINS):
                continue
            s = score_url_for_topic(url, topic.key, toks,
                                    title=(item or {}).get("title", ""))
            if s < 0:
                continue  # başka üniversitenin resmi sayfası — alma
            scored.append((s, url))
        scored.sort(key=lambda x: -x[0])
        for _, url in scored[:topic.quota]:
            if len(picked) >= max_total:
                return picked
            seen.add(url)
            picked.append((topic.key, url))

    return picked


def _pick_urls_focused(buckets: list[list], max_urls: int, uni_adi: str = "") -> list[tuple[str, str]]:
    """SPESİFİK sorular için derin seçim.

    Spesifik soruda ("yurt imkanları nasıl?", "staj olanakları?") QueryPlanner
    zaten o konuya odaklı sorgular üretiyor. Sabit konu listesini taramak yerine
    planner'ın KENDİ sonuçlarından çok sayıda sayfa çekmek gerekir — böylece
    tüm tarama bütçesi sorulan konuya harcanır.

    Dönüş: [("odak", url), ...]
    """
    toks = uni_tokens(uni_adi)
    seen: set[str] = set()
    scored: list[tuple[int, str]] = []

    for bucket in buckets:
        for item in (bucket or []):
            url = (item or {}).get("url", "")
            if not url or url in seen:
                continue
            u = url.lower()
            if any(d in u for d in _SKIP_FETCH_DOMAINS):
                continue
            seen.add(url)

            s = 0
            # Doğru üniversitenin resmi sayfası en değerlisi
            if toks and any(t in u for t in toks):
                s += 12
            elif ".edu.tr" in u:
                s -= 15          # başka üniversitenin resmi sayfası
            if ".edu.tr" in u:
                s += 6
            if any(d in u for d in _PRIORITY_FETCH_DOMAINS):
                s += 5
            if u.endswith(".pdf"):
                s += 3
            scored.append((s, url))

    scored.sort(key=lambda x: -x[0])
    return [("odak", u) for s, u in scored[:max_urls] if s > -10]


def _pick_urls_to_fetch(buckets: list[list], max_urls: int = 2) -> list[str]:
    """DDG sonuçlarından fetch edilecek URL'leri seç (genel havuz — yedek yol).

    Önce öncelikli domainlerden sırala, sonra genel sonuçlardan tamamla.
    Sosyal medya ve faydasız domainleri atla. max_urls kadar döndür.
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
    1. QueryPlanner ile sınıflandırma + dinamik sorgu üretimi (LLM, fallback'li)
    2. Sorguları paralel web aramasında kullanır
    3. Toplanan snippet'leri LLM'e verir
    4. LLM gerçek bir analiz/yorum üretir — sadece link listesi değil
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

    # Sınıflandırma + sorgu üretimi — QueryPlanner (LLM tabanlı, Redis cache'li)
    try:
        plan = await query_planner.classify_and_generate_queries(
            user_text=user_text,
            entity_name=uni,
            mode="uni",
        )
    except Exception as e:
        print(f"[UNI_INFO] 💥 QueryPlanner hatası: {e}")
        plan = None

    if plan is None or not plan.queries:
        return {"messages": [AIMessage(content="Bu üniversite için şu an arama yapamıyorum, lütfen tekrar dene.")]}

    is_specific = _looks_specific_uni(user_text, plan.is_specific)
    if is_specific != plan.is_specific:
        print(f"[UNI_INFO] 🔧 Sınıflandırma düzeltildi: LLM={plan.is_specific} → {is_specific}")
    queries = plan.queries
    print(f"[UNI_INFO] 🧠 QueryPlan: source={plan.source}, is_specific={is_specific}, {len(queries)} sorgu")

    # Genel soru ise cache kontrolü yap (spesifik sorular cache'lenmez — kullanıcıya özel)
    if not is_specific:
        cache_key = _uni_info_cache_key(uni)
        cached = _load_uni_info_cache(cache_key)
        if cached:
            return {"messages": [AIMessage(content=cached)]}
    else:
        cache_key = None

    # ── ARAMALAR: genel havuz + konu bazlı derin tarama, AYNI ANDA ──────────
    # Bu ikisi eskiden sırayla çalışıyordu (12s + 16s'e kadar). Birbirlerine
    # bağlı olmadıkları için paralel çalıştırıyoruz; toplam süre ikisinden
    # yavaş olanı kadar.
    bolum_adi = (ner_ctx.get("program") or "").strip()
    raw_buckets: list[list] = []
    fetched_sections: list[str] = []
    fetched_docs: list[tuple[str, str]] = []

    # SPESİFİK soruda sabit konu listesi (ders programı, Erasmus...) alakasız
    # kalıyor; tüm bütçe planner'ın odaklı sorgularına harcanmalı. Bu yüzden
    # konu taraması yalnızca GENEL sorularda çalışır — spesifik sorularda hem
    # isabet artar hem 6 ekstra arama yapılmadığı için süre düşer.
    genel_task = _asyncio_for_decorators.create_task(
        _ddg_multi_query(queries, max_results=(10 if is_specific else 8))
    )
    tasks = [genel_task]
    konu_task = None
    if not is_specific:
        konu_task = _asyncio_for_decorators.create_task(
            _search_by_topic(uni, bolum_adi)
        )
        tasks.append(konu_task)

    try:
        gathered = await _asyncio_for_decorators.wait_for(
            _asyncio_for_decorators.gather(*tasks, return_exceptions=True),
            timeout=18.0,
        )
    except Exception as e:
        print(f"[UNI_INFO] ⚠️ Arama zaman aşımı: {e}")
        gathered = []

    all_results = gathered[0] if len(gathered) > 0 else []
    topic_results = gathered[1] if len(gathered) > 1 else {}

    if isinstance(all_results, Exception):
        print(f"[UNI_INFO] 💥 Genel arama hatası: {all_results}")
        all_results = []
    if isinstance(topic_results, Exception):
        print(f"[UNI_INFO] ⚠️ Konu bazlı arama hatası: {topic_results}")
        topic_results = {}

    # Retriever tüm sonuçları flat döndürüyor; sorgu sayısı kadar bucket'a böl
    if all_results:
        n_buckets = max(1, len(queries))
        chunk = max(1, len(all_results) // n_buckets)
        raw_buckets = [
            all_results[i * chunk:(i + 1) * chunk][:6]
            for i in range(n_buckets)
        ]

    if not any(raw_buckets) and not topic_results:
        return {"messages": [AIMessage(content=f"## 🏫 {uni}\n\nBu üniversite hakkında web'de yeterli kaynak bulamadım.")]}

    if is_specific:
        # Tüm tarama bütçesi sorulan konuya — planner'ın kendi sonuçlarından
        topic_urls = _pick_urls_focused(raw_buckets, _MAX_FETCH_URLS_SPECIFIC, uni)
        print(f"[UNI_INFO] 🎯 Spesifik soru — odaklı derin tarama ({len(topic_urls)} sayfa)")
    else:
        topic_urls = _pick_urls_by_topic(topic_results, max_total=_MAX_FETCH_URLS, uni_adi=uni)

    # Hiçbir şey seçilemediyse eski genel havuza düş
    if not topic_urls:
        topic_urls = [("genel", u) for u in _pick_urls_to_fetch(raw_buckets, max_urls=3)]

    if topic_urls:
        print(f"[UNI_INFO] 🔗 {len(topic_urls)} sayfa paralel çekiliyor: "
              + ", ".join(f"{k}→{u[:48]}" for k, u in topic_urls))
        try:
            fetch_results = await _asyncio_for_decorators.wait_for(
                _asyncio_for_decorators.gather(
                    *[fetch_url_content(u, timeout_seconds=8) for _, u in topic_urls],
                    return_exceptions=True,
                ),
                timeout=_FETCH_TOTAL_TIMEOUT,
            )
            for (topic_key, _url), r in zip(topic_urls, fetch_results):
                if isinstance(r, Exception):
                    continue
                if r.success and r.content and len(r.content.strip()) > 200:
                    label = TOPIC_BY_KEY[topic_key].label if topic_key in TOPIC_BY_KEY else "Ek Kaynak"
                    snippet = r.content.strip()[:_MAX_CHARS_PER_PAGE]
                    fetched_sections.append(f"[{label} — {r.url}]\n{snippet}")
                    # Öğrenciye konu etiketli belge listesi için sakla
                    if topic_key != "odak":
                        fetched_docs.append((label, r.url))
                    print(f"[UNI_INFO] ✅ {topic_key}: {r.url} ({len(r.content)} kr)")
        except Exception as e:
            print(f"[UNI_INFO] ⚠️ URL fetch hatası (devam ediliyor): {e}")

    if fetched_sections:
        print(f"[UNI_INFO] 📚 {len(fetched_sections)} konu için tam içerik toplandı")
    # ────────────────────────────────────────────────────────────────────────

    labels = [f"Arama: {q[:60]}" for q in queries]

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
        if _PARALLEL_SYNTHESIS and not is_specific:
            answer = await _parallel_synthesis_uni(uni, user_text, web_context)
        else:
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
            # Konu etiketli belge listesi — "ders programı" okuyan öğrenci
            # ilgili ders planına (çoğu zaman PDF) doğrudan tıklayabilsin.
            docs = build_documents_block(fetched_docs)
            if docs:
                answer = answer.rstrip() + "\n\n" + docs
                print(f"[UNI_INFO] 📎 {len(fetched_docs)} belge bağlantısı eklendi")
            # Kaynakları LLM'e bırakma — gerçek URL'lerden deterministik ekle.
            if "](http" not in answer:
                sources = build_sources_block(
                    raw_buckets,
                    exclude_url_substrings=_SKIP_FETCH_DOMAINS,
                    priority_urls=[u for _, u in topic_urls],
                )
                if sources:
                    answer = answer.rstrip() + "\n\n" + sources
            # Genel sorgular için cache'e yaz (kaynaklar dahil)
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
