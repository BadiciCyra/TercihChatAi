import re
from typing import Optional, Literal

from state import AgentState
from models import llm_ner
from schemas import RouterOutput
from utils.extractors import UNI_MAPPING
from utils.validators import get_msg_content, format_ner_for_agent
from utils.decorators import validate_node_output, with_error_recovery

# ── Minimal kural-tabanlı router ──────────────────────────────────────────────
# Sadece iki kesin durum yakalanır:
#   1. Mesajda sıralama sayısı var → direkt fast_lookup (YÖK Atlas tablosu)
#   2. Çok kısa selamlama mesajı   → direkt casual_chat
# Geri kalan her şey NER'e (Gemini'ye) bırakılır.
# Böylece keyword listesi bakımına gerek kalmaz — LLM intent'i kendisi anlar.

_RANK_REGEX = re.compile(
    # Branch 1: "150k", "25 bin", "150.000", "150,000" gibi kısa-biçimli sıralamalar
    r'\b\d{1,3}[\.,]?\d{0,3}\s*(?:k|bin|000)\b'
    # Branch 2: 4-7 haneli sayı yalnızca "sıralama" / "sira" / "sıra" kelimesiyle birlikte gelirse
    r'|\b\d{4,7}\s*(?:sıralama|siralama|sıra|sira)\b'
    # Branch 3: "sıralamam X", "sıram X" gibi ters sıralamalı ifadeler
    r'|(?:sıralama|siralama|sıram?|siram?)\s*\d{4,7}\b',
    re.IGNORECASE
)

_CASUAL_TRIGGERS = frozenset({
    'selam', 'slm', 'merhaba', 'mrb', 'naber', 'ne haber', 'nasılsın',
    'nasilsin', 'iyi misin', 'teşekkür', 'tesekkur', 'sağ ol', 'sagol',
    'tamam', 'ok', 'okey', 'eyvallah', 'görüşürüz', 'gorusuruz',
})


def rule_based_router(text: str) -> Optional[Literal["casual", "fast", "search", "uni_info"]]:
    """Sadece kesin, hızlı kararlar verir.
    Belirsiz veya karmaşık her sorgu → None (NER'e git, LLM karar versin).
    """
    if not text:
        return None
    t = text.lower().strip()
    words = t.split()

    # 1. Sıralama sayısı varsa → fast_lookup (tablo gerekiyor, LLM'e gerek yok)
    if _RANK_REGEX.search(t):
        return "fast"

    # 2. Çok kısa (≤4 kelime) + selamlama kelimesi → casual
    if len(words) <= 4 and any(trigger in t for trigger in _CASUAL_TRIGGERS):
        return "casual"

    # Geri kalan her şey NER'e — LLM intent'i anlasın
    return None


def route_after_ner(state: AgentState) -> Literal["casual", "fast", "search", "uni_info", "career_info"]:
    """NER sonucuna göre yönlendir.
    Mod seçilmişse doğrudan ilgili node'a yönlendir (NER tabanlı mantığı atla).
    rule_based_router sadece kesin kısa-devre durumları yakalar (sıralama sayısı, selamlama).
    Geri kalan her şeyde NER'in (Gemini) kararı esas alınır:
      - search_type=TABLO + rank/program/uni varsa  → fast_lookup
      - search_type=WEB_ARASTIRMA                  → search
      - action=DIRECT_CHAT                         → casual
      - uni entity varsa + WEB                     → uni_info
    """
    # Mod seçilmişse NER tabanlı yönlendirmeyi atla
    mode = state.get("mode")
    if mode == "wizard":
        print(f"[ROUTER] 🎯 mod=wizard → fast_lookup")
        return "fast"
    if mode == "research":
        print(f"[ROUTER] 🔍 mod=research → uni_info")
        return "uni_info"
    if mode == "career":
        print(f"[ROUTER] 🧭 mod=career → career_info")
        return "career_info"
    if mode == "guidance":
        print(f"[ROUTER] 💬 mod=guidance → casual")
        return "casual"

    messages = state.get("messages", [])
    user_text = get_msg_content(messages[-1]) if messages else ""

    # Kural-tabanlı kısa devre (sadece kesin durumlar)
    rule_decision = rule_based_router(user_text)
    if rule_decision:
        print(f"[ROUTER] ⚡ Kısa devre: '{user_text[:50]}' → {rule_decision}")
        return rule_decision

    # NER kararına bak
    ner_ctx = state.get("ner_context", {}) or {}
    action     = (ner_ctx.get("action")      or "RAG_SEARCH").upper()
    search_type = (ner_ctx.get("search_type") or "").upper()

    # Casual / selamlama
    if action == "DIRECT_CHAT":
        print("[ROUTER] 💬 NER→DIRECT_CHAT → casual_chat")
        return "casual"

    has_rank    = bool(ner_ctx.get("rank"))
    has_program = bool(ner_ctx.get("program"))
    has_uni     = bool(ner_ctx.get("uni"))
    needs_web   = search_type in ("WEB_ARASTIRMA", "WEB", "REVIEWS", "WEB_GENERAL")

    # Üniversite sorusu + web araması → uni_info
    if has_uni and needs_web and not has_rank:
        print("[ROUTER] 🏫 NER→uni+web → uni_info")
        return "uni_info"

    # Yapısal sorgu (sıralama / bölüm) + tablo → fast_lookup
    if (has_rank or has_program or has_uni) and not needs_web:
        print("[ROUTER] ⚡ NER→Yapısal → fast_lookup")
        return "fast"

    # Web araması gerekiyor → tam agent pipeline
    print("[ROUTER] 🔍 NER→Web/Karmaşık → search pipeline")
    return "search"


@validate_node_output
@with_error_recovery
def ner_node(state: AgentState) -> dict:
    """Kullanıcı sorgusundan varlıkları (NER) çıkarır."""
    # Mod seçilmişse NER LLM çağrısını tamamen atla
    mode = state.get("mode")
    if mode is not None:
        print(f"[NER] ⚡ mod={mode!r} → NER bypass")
        return {
            "ner_context": {"action": "MODE_SELECTED", "mode": mode},
            "iteration_count": 0,
            "query_plan": None,
            "thinking_steps": [],
            "sub_question_results": {},
            "current_answer": None,
            "evaluation": None,
            "search_depth": 0,
        }

    messages = state.get("messages", [])
    user_query = get_msg_content(messages[-1]) if messages else "Selam"

    rule_decision = rule_based_router(user_query)
    if rule_decision == "casual":
        # Selamlama: LLM çağrısı yapmaya gerek yok
        print(f"[NER] ⚡ LLM bypass: selamlama → casual")
        return {
            "ner_context": {"action": "DIRECT_CHAT"},
            "iteration_count": 0,
            "query_plan": None,
            "thinking_steps": [],
            "sub_question_results": {},
            "current_answer": None,
            "evaluation": None,
            "search_depth": 0,
        }
    if rule_decision == "fast":
        # Sıralama sayısı var: NER çalıştırmak yerine boş context ile fast_lookup'a git
        # fast_lookup_node zaten regex ile entity'leri çıkarır
        print(f"[NER] ⚡ LLM bypass: sıralama sayısı → fast")
        return {
            "ner_context": {"action": "RAG_SEARCH", "search_type": "TABLO"},
            "iteration_count": 0,
            "query_plan": None,
            "thinking_steps": [],
            "sub_question_results": {},
            "current_answer": None,
            "evaluation": None,
            "search_depth": 0,
        }
    # Diğer tüm durumlarda NER LLM'ini çalıştır

    history_context = ""
    if len(messages) > 1:
        history_context = "\n".join([
            f"{m.type if hasattr(m, 'type') else 'user'}: {get_msg_content(m)}"
            for m in messages[-5:-1]
        ])
    
    NER_SYSTEM_PROMPT = """Sen YKS/YÖK Atlas uzmanı bir NER (Varlık Çıkarma) asistanısın.

### GÖREV:
Kullanıcının sorgusundan şu varlıkları çıkar:
- Üniversite adı (kısaltmaları aç: İTÜ→İstanbul Teknik Üniversitesi, ODTÜ→Orta Doğu Teknik Üniversitesi)
- Bölüm adı (kısaltmaları aç: Bilgisayar→Bilgisayar Mühendisliği, YBS→Yönetim Bilişim Sistemleri)
- Şehir
- Sıralama (120k→120000, 50 bin→50000, "25k için"→25000)
- Puan türü (SAY/EA/SOZ/DIL)
- Üniversite türü (Devlet/Vakıf)
- Ücret durumu (Burslu/%50/Ücretli)

### action SEÇİMİ (KRİTİK):
- `DIRECT_CHAT`: Sadece selamlama, hal-hatır sorma, sohbet mesajları için.
- `RAG_SEARCH`: Üniversite/bölüm/sıralama/puan ile ilgili HER soru için.

### search_type SEÇİMİ:
- `TABLO`: Sıralama, taban puanı, kontenjan gibi NET SAYISAL VERİ isteniyorsa.
- `WEB_ARASTIRMA`: Yorum, öneri, kariyer, nasıl okunur, yatay geçiş, süreç gibi bilgi soruları için.

### BÖLÜM ADI KURALI (ÇOK ÖNEMLİ):
Bölüm adını AYNEN yaz, kendi başına başka bir bölüme dönüştürme.
- "Havacılık Elektrik Elektroniği" → program: "Havacılık Elektrik ve Elektroniği" (Elektrik-Elektronik Mühendisliği DEĞİL)
- "Bilgisayar" → "Bilgisayar Mühendisliği" (tekli kısaltma genişletilebilir)
- "Elektrik" tek başına → "Elektrik-Elektronik Mühendisliği"
- "Havacılık elektrik" → "Havacılık Elektrik ve Elektroniği" (havacılık prefix'i korunmalı)
- "Bilişim Sistemleri ve Teknolojileri" → "Bilişim Sistemleri ve Teknolojileri" (YBS DEĞİL — tamamen farklı bölüm)
- "YBS" veya "Yönetim Bilişim" → "Yönetim Bilişim Sistemleri"
- "Bilişim sistemleri ve teknolojileri" ile "Yönetim Bilişim Sistemleri" FARKLI bölümlerdir, karıştırma.

### KURALLAR:
1. Yazım hatalarını agresif şekilde düzelt.
2. Geçmiş konuşmadan varlık taşı.
3. Sıralama tahmini: "25k"→25000, "25 bin"→25000.
4. Belirsizse boş bırak ama bağlamdan tahmin et.
5. action RAG_SEARCH varsayılan.
6. Kullanıcı spesifik bölüm adı vermemişse program=null bırak."""

    from langchain_core.messages import SystemMessage, HumanMessage as _HM
    ner_messages = [
        SystemMessage(content=NER_SYSTEM_PROMPT),
        _HM(content=f"GEÇMİŞ:\n{history_context}\n\nSORGU: {user_query}"),
    ]

    try:
        structured_llm = llm_ner.with_structured_output(RouterOutput)
        result: RouterOutput = structured_llm.invoke(ner_messages)
    except Exception as e:
        print(f"[NER] 💥 Hata: {e}")
        result = RouterOutput(action="RAG_SEARCH", search_type="WEB_ARASTIRMA",
                              intent="fallback", reason=str(e))
    
    if result.uni and result.uni in UNI_MAPPING:
        expanded = UNI_MAPPING[result.uni]
        print(f"[NER] 🏫 Üniversite genişletildi: {result.uni} → {expanded}")
        result.uni = expanded
    
    ner_dict = result.dict()
    ner_json = format_ner_for_agent(ner_dict)
    print(f"\n[NER] 🧠 Çıkarılan Varlıklar (JSON):")
    print(ner_json)
    
    return {
        "ner_context": ner_dict,
        "iteration_count": 0,
        "query_plan": None,
        "thinking_steps": [],
        "sub_question_results": {},
        "current_answer": None,
        "evaluation": None,
        "search_depth": 0
    }
