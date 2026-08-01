"""
career_info_node — Kariyer ve Bölüm Araştırma Pipeline'ı

QueryPlanner ile dinamik sorgu üretimi → paralel web araması → URL fetch → LLM sentezi.
Bölüm tespit edilemezse yalnızca açıklama mesajı döner (arama veya LLM çağrılmaz).
"""
import asyncio as _asyncio_for_decorators
import os as _os_for_career
import time as _time_for_career
from typing import Optional
from langchain_core.messages import AIMessage, SystemMessage, HumanMessage

from state import AgentState
from models import llm_responder
from prompts.career_info import _CAREER_INFO_SYSTEM_PROMPT
from utils.validators import get_msg_content, strip_react_thoughts
from utils.decorators import validate_node_input, validate_node_output, with_error_recovery
from utils.extractors import _extract_program_from_text, _extract_program_llm_fallback
from utils.query_planner import query_planner
from utils.context_assembly import (
    build_grouped_context,
    build_sources_block,
    build_documents_block,
)
from utils.link_fetcher import fetch_url_content
from utils.research_topics import (
    CAREER_TOPICS,
    TOPIC_BY_KEY,
    build_career_topic_queries,
    score_url_for_topic,
)
from nodes.fast_lookup import _ddg_quick

SOCIAL_MEDIA_DOMAINS = [
    "instagram.com",
    "facebook.com",
    "twitter.com",
    "tiktok.com",
    "pinterest.com",
]

# Career için fetch'e değmez domainler
_CAREER_SKIP_FETCH_DOMAINS = [
    "instagram.com", "facebook.com", "twitter.com", "tiktok.com", "pinterest.com",
    "youtube.com", "yandex.com", "google.com",
]

# Career için öncelikli fetch domainleri (maaş, iş ilanı, YÖK, kariyer platformları)
_CAREER_PRIORITY_FETCH_DOMAINS = [
    "kariyer.net", "linkedin.com", "yok.gov.tr", "istatistik.yok.gov.tr",
    "glassdoor.com", "secretcv.com", "yenibiris.com", "eksikatalogu.com",
    "eksisozluk.com", "itü.edu.tr", "odtu.edu.tr", "boun.edu.tr",
    "meslekbilgisi.com", "meslekyonetimi.com",
]


# ── Tarama derinliği (env ile ayarlanabilir) ────────────────────────────────
# Sayfalar paralel çekildiği için sayı artırmak süreyi doğrusal artırmaz.
_CAREER_MAX_FETCH = int(_os_for_career.getenv("CAREER_MAX_FETCH_URLS", "8"))
_CAREER_MAX_FETCH_SPECIFIC = int(_os_for_career.getenv("CAREER_MAX_FETCH_URLS_SPECIFIC", "10"))
_CAREER_CHARS_PER_PAGE = int(_os_for_career.getenv("CAREER_MAX_CHARS_PER_PAGE", "2500"))


# ── Paralel sentez ──────────────────────────────────────────────────────────
# Ölçüm: genel kariyer cevabının süresi neredeyse tamamen ÜRETİLEN KARAKTER
# sayısına bağlı (~1.7-1.9 ms/karakter). Arama 4.6 sn, sayfa çekimi 1.2 sn,
# geri kalan ~19 sn LLM üretimi. Tek seferde 11-14k karakter yazmak yerine
# cevabı iki parçaya bölüp EŞZAMANLI ürettiğimizde süre en uzun parça kadar
# oluyor — başlıklardan hiçbiri kaybolmadan ~2x hızlanma.
_PARALLEL_SYNTHESIS = _os_for_career.getenv("CAREER_PARALLEL_SYNTHESIS", "true").lower() == "true"

# Bölüm grupları — birlikte anlamlı olan başlıklar aynı çağrıda kalır.
_SECTION_GROUPS = (
    (
        "GİRİŞ + MÜFREDAT + KARİYER",
        "1) Kısa bir selamlama/giriş paragrafı (2-3 cümle).\n"
        "2) ### 📚 Müfredat & Ders İçerikleri\n"
        "3) ### 💼 Kariyer Yolları & Maaş Beklentileri",
    ),
    (
        "İSTİHDAM + DEĞERLENDİRME",
        "1) ### 📊 İstihdam Oranları & Piyasa Talebi\n"
        "2) ### ⚖️ Güçlü Yönler & Dikkat Edilmesi Gerekenler\n"
        "3) ### 💡 Kimler İçin Uygun?\n"
        "NOT: Selamlama/giriş YAZMA, doğrudan ilk başlıkla başla.",
    ),
)


async def _parallel_synthesis(dept: str, user_text: str, web_context: str) -> str:
    """Cevabı bölüm gruplarına ayırıp EŞZAMANLI üret, sonra birleştir.

    Her çağrı aynı web context'ini görür ama sadece kendi başlıklarını yazar.
    Herhangi bir parça boş dönerse diğerleriyle devam edilir; hepsi boşsa
    çağıran taraf tek-çağrılı yola düşer (exception fırlatılır).
    """
    async def _one(idx: int, baslik: str, talimat: str) -> tuple[int, str]:
        human = (
            f"Bölüm: {dept}\n"
            f"Kullanıcı sorusu: {user_text}\n\n"
            f"SADECE aşağıdaki başlıkları yaz — başka başlık AÇMA, "
            f"'Kaynaklar' bölümü YAZMA:\n{talimat}\n\n"
            f"Aşağıdaki web arama sonuçlarını kullan:\n\n{web_context}"
        )
        try:
            resp = await llm_responder.ainvoke([
                SystemMessage(content=_CAREER_INFO_SYSTEM_PROMPT),
                HumanMessage(content=human),
            ])
            return idx, get_msg_content(resp) or ""
        except Exception as e:
            print(f"[CAREER_INFO] ⚠️ Paralel parça {idx} hatası: {e}")
            return idx, ""

    t0 = _time_for_career.time()
    results = await _asyncio_for_decorators.gather(
        *[_one(i, b, t) for i, (b, t) in enumerate(_SECTION_GROUPS)],
        return_exceptions=True,
    )
    parts: list[tuple[int, str]] = [r for r in results if isinstance(r, tuple) and r[1].strip()]
    parts.sort(key=lambda x: x[0])
    if not parts:
        raise RuntimeError("paralel sentez tüm parçalarda boş döndü")

    print(
        f"[CAREER_INFO] ⚡ Paralel sentez: {len(parts)}/{len(_SECTION_GROUPS)} parça, "
        f"{_time_for_career.time() - t0:.1f} sn"
    )
    return "\n\n".join(p[1].strip() for p in parts)


# Dar konu sinyalleri — bunlardan biri geçiyorsa kullanıcı TEK bir başlık soruyor
_NARROW_CAREER_KEYWORDS = (
    "maaş", "maas", "kazanç", "kazanc", "gelir", "ücret", "ucret",
    "staj", "müfredat", "mufredat", "ders", "yüksek lisans", "yuksek lisans",
    "akademik kariyer", "kpss", "atama", "sektör", "sektor", "işsizlik", "issizlik",
    "istihdam oran", "yurt dışı", "yurt disi", "sınav", "sinav",
)


def _looks_specific(user_text: str, llm_flag: bool) -> bool:
    """Soru gerçekten tek bir alt başlığa mı odaklı?

    QueryPlanner'ın LLM sınıflandırması güvenilmez çıkıyor ("Psikoloji nasıl bir
    bölüm, mezunu ne iş yapar?" spesifik işaretlendi). Dar konu anahtar kelimesi
    yoksa soruyu GENEL kabul ediyoruz — böylece konu bazlı derin tarama çalışır.
    """
    t = (user_text or "").lower()
    if any(k in t for k in _NARROW_CAREER_KEYWORDS):
        return True
    # Açıkça genel kalıplar
    if any(p in t for p in ("nasıl bir bölüm", "nasil bir bolum", "ne iş yapar",
                            "ne is yapar", "hakkında bilgi", "hakkinda bilgi",
                            "okunur mu", "tavsiye eder")):
        return False
    return llm_flag


async def _search_career_by_topic(bolum: str) -> dict[str, list]:
    """Her kariyer konusu için AYRI arama (paralel).

    Genel bölüm sorularında ("Psikoloji nasıl bir bölüm?") tek sorgu havuzu
    kullanınca maaş/istihdam/mezun deneyimi gibi başlıklar boş kalıyordu.
    Konu başına arama, her başlığa kaynak düşmesini garantiler.
    """
    topic_queries = build_career_topic_queries(bolum)
    if not topic_queries:
        return {}

    async def _one(key: str, qs: list[str]):
        try:
            res = await _asyncio_for_decorators.wait_for(
                _asyncio_for_decorators.gather(
                    *[_ddg_quick(q, max_results=6) for q in qs],
                    return_exceptions=True,
                ),
                timeout=14.0,
            )
            flat = []
            for r in res:
                if r and not isinstance(r, Exception):
                    flat.extend(r)
            return key, flat
        except Exception as e:
            print(f"[CAREER_INFO] ⚠️ '{key}' araması başarısız: {e}")
            return key, []

    pairs = await _asyncio_for_decorators.gather(
        *[_one(k, qs) for k, qs in topic_queries], return_exceptions=True
    )
    out: dict[str, list] = {}
    for p in pairs:
        if not isinstance(p, Exception):
            out[p[0]] = p[1]
    return out


def _pick_career_urls_by_topic(topic_results: dict[str, list], max_total: int) -> list[tuple[str, str]]:
    """Kariyer konularına kota dağıt — bir konu diğerlerini ezmesin."""
    seen: set[str] = set()
    picked: list[tuple[str, str]] = []
    for topic in CAREER_TOPICS:
        scored = []
        for item in (topic_results.get(topic.key) or []):
            url = (item or {}).get("url", "")
            if not url or url in seen:
                continue
            if any(d in url.lower() for d in _CAREER_SKIP_FETCH_DOMAINS):
                continue
            scored.append((score_url_for_topic(url, topic.key), url))
        scored.sort(key=lambda x: -x[0])
        for _, url in scored[:topic.quota]:
            if len(picked) >= max_total:
                return picked
            seen.add(url)
            picked.append((topic.key, url))
    return picked


def _pick_career_urls_focused(buckets: list[list], max_urls: int) -> list[tuple[str, str]]:
    """Spesifik kariyer sorusunda tüm bütçeyi sorulan konuya harca."""
    seen: set[str] = set()
    scored: list[tuple[int, str]] = []
    for bucket in buckets:
        for item in (bucket or []):
            url = (item or {}).get("url", "")
            if not url or url in seen:
                continue
            u = url.lower()
            if any(d in u for d in _CAREER_SKIP_FETCH_DOMAINS):
                continue
            seen.add(url)
            s = 0
            if any(d in u for d in _CAREER_PRIORITY_FETCH_DOMAINS):
                s += 8
            if u.endswith(".pdf"):
                s += 2
            scored.append((s, url))
    scored.sort(key=lambda x: -x[0])
    return [("odak", u) for _, u in scored[:max_urls]]


def _pick_career_urls_to_fetch(buckets: list[list], max_urls: int = 3) -> list[str]:
    """Career DDG sonuçlarından fetch edilecek URL'leri seç (yedek yol)."""
    seen: set[str] = set()
    priority: list[str] = []
    fallback: list[str] = []

    for bucket in buckets:
        for item in (bucket or []):
            url = item.get("url", "")
            if not url or url in seen:
                continue
            url_lower = url.lower()
            if any(d in url_lower for d in _CAREER_SKIP_FETCH_DOMAINS):
                continue
            seen.add(url)
            if any(d in url_lower for d in _CAREER_PRIORITY_FETCH_DOMAINS):
                priority.append(url)
            else:
                fallback.append(url)

    return (priority + fallback)[:max_urls]


def _extract_dept_from_text(text: str) -> Optional[str]:
    """Mesajdan bölüm/alan adı çıkar.

    _extract_program_from_text üzerine ince sarmalayıcı; aynı alias tablosunu
    kullanır, dolayısıyla üniversite kariyer sorgularında da tutarlıdır.

    Türkçe büyük İ (U+0130) harfinin Python .lower() ile ``i\u0307`` şeklinde
    iki karaktere dönüşmesi sorununu gidermek için metni önce ASCII-uyumlu
    küçük harfe normalize eder, ardından tekrar dener.
    """
    # Birinci deneme — normal yol
    result = _extract_program_from_text(text)
    if result:
        return result
    # İkinci deneme — Türkçe büyük harfleri ASCII eşdeğerlerine map'le
    import unicodedata as _ud
    normalized = _ud.normalize("NFC", text)
    # U+0130 İ → i, Ş → s, Ğ → g, Ü → u, Ö → o, Ç → c, ı → i, ş → s, ğ → g, ü → u, ö → o, ç → c
    _tr_table = str.maketrans(
        "\u0130\u015e\u011e\u00dc\u00d6\u00c7\u0131\u015f\u011f\u00fc\u00f6\u00e7",
        "isguo" "c" "isguo" "c",
    )
    normalized = normalized.translate(_tr_table)
    return _extract_program_from_text(normalized)


@validate_node_input
@validate_node_output
@with_error_recovery
async def career_info_node(state: AgentState) -> dict:
    """
    Kariyer bilgi ve müfredat araştırma node'u.

    Yollar:
    - dept yok: açıklama mesajı döner — QueryPlanner, arama ve LLM çağrılmaz.
    - dept mevcut: QueryPlanner sınıflandırır ve sorguları üretir;
      is_specific=True → odaklı yanıt (Yol C), False → tam şablon (Yol D).

    1. Bölüm/alan adını tespit eder (regex veya NER context)
    2. QueryPlanner ile sınıflandırma + dinamik sorgu üretimi (LLM, fallback'li)
    3. Toplanan snippet'leri LLM'e verir
    4. LLM kariyer odaklı yapılandırılmış markdown yanıt üretir
    """
    messages = state.get("messages", [])
    user_text = get_msg_content(messages[-1]) if messages else ""
    ner_ctx = dict(state.get("ner_context") or {})

    # Bölüm adını bul — regex önce, yoksa NER context, son çare LLM
    dept = _extract_dept_from_text(user_text) or ner_ctx.get("program") or ner_ctx.get("dept") or ""

    if not dept:
        # LLM fallback — alias tablosunda olmayan bölümler için
        dept = await _extract_program_llm_fallback(user_text) or ""

    # dept boş → QueryPlanner çağrılmaz, açıklama mesajı döner (Requirement 6.6, 6.7)
    if not dept:
        clarification = (
            "Hangi bölüm veya alanı araştırmak istediğini belirtir misin? "
            "(Örn. 'Bilgisayar Mühendisliği', 'Psikoloji', 'Hukuk')"
        )
        return {"messages": [AIMessage(content=clarification)]}

    # Sınıflandırma + sorgu üretimi — QueryPlanner (LLM tabanlı, Redis cache'li)
    try:
        plan = await query_planner.classify_and_generate_queries(
            user_text=user_text,
            entity_name=dept,
            mode="career",
        )
    except Exception as e:
        print(f"[CAREER_INFO] 💥 QueryPlanner hatası: {e}")
        plan = None

    if plan is None or not plan.queries:
        return {
            "messages": [
                AIMessage(
                    content=(
                        f"## 🧭 {dept}\n\n"
                        "Şu an web araması yapamıyorum, lütfen birazdan tekrar dene."
                    )
                )
            ]
        }

    # LLM'in is_specific kararını metin sinyalleriyle doğrula (bkz. _looks_specific)
    is_career_specific = _looks_specific(user_text, plan.is_specific)
    if is_career_specific != plan.is_specific:
        print(f"[CAREER_INFO] 🔧 Sınıflandırma düzeltildi: LLM={plan.is_specific} → {is_career_specific}")
    queries = plan.queries
    print(
        f"[CAREER_INFO] 🧭 Bölüm araştırılıyor: {dept} "
        f"(spesifik={is_career_specific}, {len(queries)} sorgu, source={plan.source})"
    )

    # Yol C (spesifik) / Yol D (genel) sinyali — QueryPlan.is_specific'e göre
    if is_career_specific:
        _human_content_signal = "Soru tipi: SPESİFİK — sadece sorulan konuyu cevapla, tam şablon doldurma"
    else:
        _human_content_signal = "Soru tipi: GENEL — tam kariyer analizi yap"

    # Planner sorguları + (genel soruda) konu bazlı derin tarama — AYNI ANDA.
    # Spesifik soruda konu taraması çalışmaz: tüm bütçe sorulan konuya gider.
    raw_buckets: list[list] = []
    career_topic_results: dict[str, list] = {}
    try:
        plan_task = _asyncio_for_decorators.gather(
            *[_ddg_quick(q, max_results=8) for q in queries],
            return_exceptions=True,
        )
        jobs = [plan_task]
        if not is_career_specific:
            jobs.append(_search_career_by_topic(dept))

        done = await _asyncio_for_decorators.wait_for(
            _asyncio_for_decorators.gather(*jobs, return_exceptions=True),
            timeout=30.0,
        )
        gathered = done[0] if done and not isinstance(done[0], Exception) else []
        if len(done) > 1 and isinstance(done[1], dict):
            career_topic_results = done[1]

        for r in gathered:
            raw_buckets.append(r[:6] if r and not isinstance(r, Exception) else [])
    except Exception as e:
        print(f"[CAREER_INFO] 💥 Arama hatası: {e}")
        return {
            "messages": [
                AIMessage(
                    content=(
                        f"## 🧭 {dept}\n\n"
                        "Şu an web araması yapamıyorum, lütfen birazdan tekrar dene."
                    )
                )
            ]
        }

    if not any(raw_buckets):
        return {
            "messages": [
                AIMessage(
                    content=(
                        f"## 🧭 {dept}\n\n"
                        "Bu bölüm hakkında web'de yeterli kaynak bulamadım."
                    )
                )
            ]
        }

    labels = [f"Arama: {q[:60]}" for q in queries]

    assembly = build_grouped_context(
        raw_buckets,
        labels,
        heading=f"## 🧭 {dept}\n",
        exclude_url_substrings=SOCIAL_MEDIA_DOMAINS,
    )

    if not assembly.context_blocks:
        return {
            "messages": [
                AIMessage(
                    content=(
                        f"## 🧭 {dept}\n\n"
                        "Web'den yeterli kaynak çıkmadı."
                    )
                )
            ]
        }

    web_context = "\n\n".join(assembly.context_blocks)
    fallback_parts: list[str] = assembly.fallback_parts.copy()

    # ── DERİN TARAMA: sayfalar paralel çekilir ──────────────────────────────
    # Eskiden 3 sayfaydı. Paralel çekim sayesinde sayıyı artırmak süreyi
    # doğrusal artırmıyor; genel soruda konulara, spesifik soruda tek konuya.
    if is_career_specific:
        picked = _pick_career_urls_focused(raw_buckets, _CAREER_MAX_FETCH_SPECIFIC)
        print(f"[CAREER_INFO] 🎯 Spesifik soru — odaklı derin tarama ({len(picked)} sayfa)")
    else:
        picked = _pick_career_urls_by_topic(career_topic_results, _CAREER_MAX_FETCH)
        if not picked:
            picked = _pick_career_urls_focused(raw_buckets, _CAREER_MAX_FETCH)

    fetched_sections: list[str] = []
    fetched_docs: list[tuple[str, str]] = []
    if picked:
        print(f"[CAREER_INFO] 🔗 {len(picked)} sayfa paralel çekiliyor: "
              + ", ".join(f"{k}→{u[:44]}" for k, u in picked))
        try:
            fetch_results = await _asyncio_for_decorators.wait_for(
                _asyncio_for_decorators.gather(
                    *[fetch_url_content(u, timeout_seconds=10) for _, u in picked],
                    return_exceptions=True,
                ),
                timeout=25.0,
            )
            for (topic_key, _u), r in zip(picked, fetch_results):
                if isinstance(r, Exception):
                    continue
                if r.success and r.content and len(r.content.strip()) > 200:
                    label = TOPIC_BY_KEY[topic_key].label if topic_key in TOPIC_BY_KEY else "Kaynak"
                    snippet = r.content.strip()[:_CAREER_CHARS_PER_PAGE]
                    fetched_sections.append(f"[{label} — {r.url}]\n{snippet}")
                    # Konu etiketli belge listesi için sakla (odak = spesifik
                    # soru; orada zaten tek konu var, Kaynaklar bloğu yeterli)
                    if topic_key != "odak":
                        fetched_docs.append((label, r.url))
                    print(f"[CAREER_INFO] ✅ {topic_key}: {r.url} ({len(r.content)} kr)")
        except Exception as e:
            print(f"[CAREER_INFO] ⚠️ URL fetch hatası (devam ediliyor): {e}")
    # ────────────────────────────────────────────────────────────────────────

    if fetched_sections:
        web_context += "\n\n### 📄 Tam Sayfa İçerikleri\n\n" + "\n\n".join(fetched_sections)
        print(f"[CAREER_INFO] 📄 {len(fetched_sections)} tam sayfa içeriği context'e eklendi")

    print(
        f"[CAREER_INFO] 📄 {len(assembly.context_blocks)} kategori toplandı, "
        "LLM synthesis başlıyor..."
    )

    human_content = (
        f"{_human_content_signal}\n\n"
        f"Bölüm: {dept}\n"
        f"Kullanıcı sorusu: {user_text}\n\n"
        f"Aşağıdaki web arama sonuçlarını kullanarak {'odaklı yanıt' if is_career_specific else 'detaylı kariyer analizi'} yaz:\n\n"
        f"{web_context}"
    )

    try:
        if _PARALLEL_SYNTHESIS and not is_career_specific:
            answer = await _parallel_synthesis(dept, user_text, web_context)
        else:
            response = await llm_responder.ainvoke(
                [
                    SystemMessage(content=_CAREER_INFO_SYSTEM_PROMPT),
                    HumanMessage(content=human_content),
                ],
            )
            answer = get_msg_content(response)
        answer = strip_react_thoughts(answer)
        if answer and len(answer.strip()) > 100:
            print(f"[CAREER_INFO] ✅ LLM synthesis tamamlandı ({len(answer)} karakter)")
            # Konu etiketli belge listesi — öğrenci ders planı / maaş raporu
            # gibi asıl kaynağa doğrudan tıklayabilsin (PDF'ler işaretli).
            docs = build_documents_block(fetched_docs)
            if docs:
                answer = answer.rstrip() + "\n\n" + docs
                print(f"[CAREER_INFO] 📎 {len(fetched_docs)} belge bağlantısı eklendi")
            # Kaynakları LLM'e bırakma — gerçek URL'lerden deterministik ekle.
            # LLM zaten markdown link koyduysa (](http) tekrar ekleme.
            if "](http" not in answer:
                sources = build_sources_block(
                    raw_buckets,
                    exclude_url_substrings=SOCIAL_MEDIA_DOMAINS,
                    priority_urls=[u for _, u in picked],
                )
                if sources:
                    answer = answer.rstrip() + "\n\n" + sources
            return {"messages": [AIMessage(content=answer)], "is_career_specific": is_career_specific}
        print("[CAREER_INFO] ⚠️ LLM çok kısa yanıt verdi, fallback'e geçiliyor")
    except Exception as e:
        print(f"[CAREER_INFO] ⚠️ LLM hatası, snippet fallback kullanılıyor: {e}")

    fallback_parts.append("### 💡 Tavsiye")
    fallback_parts.append(
        f"Yukarıdaki kaynakları inceleyerek **{dept}** bölümü hakkında "
        "daha detaylı bilgi edinebilirsin. Spesifik bir kariyer veya "
        "müfredat konusu için sormak istersen yazabilirsin."
    )
    return {"messages": [AIMessage(content="\n".join(fallback_parts))]}
