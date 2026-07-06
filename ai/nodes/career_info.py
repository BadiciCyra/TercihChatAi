"""
career_info_node — Kariyer ve Bölüm Araştırma Pipeline'ı

3 paralel web araması (müfredat, kariyer/maaş, istihdam istatistikleri) → URL fetch → LLM sentezi.
Bölüm tespit edilemezse yalnızca açıklama mesajı döner (arama veya LLM çağrılmaz).
"""
import asyncio as _asyncio_for_decorators
from typing import Optional
from langchain_core.messages import AIMessage, SystemMessage, HumanMessage

from state import AgentState
from models import llm_responder
from prompts.career_info import _CAREER_INFO_SYSTEM_PROMPT
from utils.validators import get_msg_content, strip_react_thoughts
from utils.decorators import validate_node_input, validate_node_output, with_error_recovery
from utils.extractors import _extract_program_from_text, _extract_program_llm_fallback
from utils.context_assembly import build_grouped_context
from utils.link_fetcher import fetch_url_content
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


def _pick_career_urls_to_fetch(buckets: list[list], max_urls: int = 3) -> list[str]:
    """Career DDG sonuçlarından fetch edilecek URL'leri seç."""
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
    1. Bölüm/alan adını tespit eder (regex veya NER context)
    2. 3 paralel web araması yapar (müfredat, kariyer/maaş, istihdam)
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

    if not dept:
        clarification = (
            "Hangi bölüm veya alanı araştırmak istediğini belirtir misin? "
            "(Örn. 'Bilgisayar Mühendisliği', 'Psikoloji', 'Hukuk')"
        )
        return {"messages": [AIMessage(content=clarification)]}

    print(f"[CAREER_INFO] 🧭 Bölüm araştırılıyor: {dept}")

    queries = [
        f"{dept} bölümü müfredat ders içerikleri",
        f"{dept} mezunu kariyer maaş iş ilanları",
        f"{dept} istihdam oranı YÖK istatistik",
    ]

    raw_buckets: list[list] = []
    try:
        gathered = await _asyncio_for_decorators.wait_for(
            _asyncio_for_decorators.gather(
                *[_ddg_quick(q, max_results=8) for q in queries],
                return_exceptions=True,
            ),
            timeout=30.0,
        )
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

    labels = ["Müfredat & Dersler", "Kariyer & Maaş", "İstihdam İstatistikleri"]

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

    # ── URL FETCH: En değerli 3 linkin tam içeriğini çek ────────────────────
    urls_to_fetch = _pick_career_urls_to_fetch(raw_buckets, max_urls=3)
    fetched_sections: list[str] = []
    if urls_to_fetch:
        print(f"[CAREER_INFO] 🔗 {len(urls_to_fetch)} URL fetch ediliyor: {urls_to_fetch}")
        try:
            fetch_results = await _asyncio_for_decorators.wait_for(
                _asyncio_for_decorators.gather(
                    *[fetch_url_content(u, timeout_seconds=10) for u in urls_to_fetch],
                    return_exceptions=True,
                ),
                timeout=25.0,
            )
            for r in fetch_results:
                if isinstance(r, Exception):
                    continue
                if r.success and r.content and len(r.content.strip()) > 200:
                    snippet = r.content.strip()[:3000]
                    fetched_sections.append(f"[Tam İçerik — {r.url}]\n{snippet}")
                    print(f"[CAREER_INFO] ✅ Fetch OK: {r.url} ({len(r.content)} karakter)")
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
        f"Bölüm: {dept}\n"
        f"Kullanıcı sorusu: {user_text}\n\n"
        f"Aşağıdaki web arama sonuçlarını kullanarak detaylı kariyer analizi yaz:\n\n"
        f"{web_context}"
    )

    try:
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
            return {"messages": [AIMessage(content=answer)]}
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
