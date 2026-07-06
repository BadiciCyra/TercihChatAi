from typing import Optional, Literal, List
from pydantic import BaseModel, Field

# ═══════════════════════════════════════════════════════════════════════════════
# 1. NER / INTENT SCHEMA
# ═══════════════════════════════════════════════════════════════════════════════

class RouterOutput(BaseModel):
    """
    NER (Named Entity Recognition) ve Intent Detection için çıktı şeması.
    
    ReAct yapısında bu schema, agent'e bağlam sağlamak için kullanılır:
    - Kullanıcı sorgusundan varlıkları (üniversite, bölüm, şehir vb.) çıkarır
    - Kullanıcının niyetini (intent) belirler
    - Agent bu bilgileri kullanarak hangi tool'u çağıracağına karar verir
    
    NOT: Asıl karar mekanizması artık Agent'te, bu schema sadece NER/bağlam için kullanılır.
    """
    
    # 1. NİYET BELİRLEME (Agent'e ipucu verir)
    action: Literal["RAG_SEARCH", "DIRECT_CHAT"] = Field(
        ..., 
        description="RAG_SEARCH: Veri/bilgi gerektiren soru. DIRECT_CHAT: Selamlama veya basit sohbet."
    )
    
    # 2. ÖNERILAN ARAMA TÜRÜ (Agent bunu ipucu olarak kullanabilir)
    search_type: Optional[Literal["TABLO", "WEB_ARASTIRMA"]] = Field(
        None,
        description="TABLO: Net sayısal veri (puan, sıralama, kontenjan). WEB_ARASTIRMA: Yorum, deneyim, subjektif bilgi."
    )
    
    # 3. Web Araması İçin Önerilen Sorgu
    web_query: Optional[str] = Field(
        None, 
        description="Web araması için önerilen sorgu. Örn: 'İTÜ Bilgisayar Mühendisliği öğrenci yorumları'"
    )
    
    # ═══════════════════════════════════════════════════════════════════════
    # ÇIKARILAN VARLIKLAR (NER - Named Entity Recognition)
    # ═══════════════════════════════════════════════════════════════════════
    
    rank: Optional[str] = Field(
        None, 
        description="Başarı sıralaması (string). '120k' → '120000', '50 bin' → '50000' olarak dönüştür."
    )
    city: Optional[str] = Field(
        None, 
        description="Şehir adı. Örn: 'İstanbul', 'Ankara', 'İzmir'"
    )
    uni: Optional[str] = Field(
        None, 
        description="Üniversite adı/kısaltması. Kısaltmaları aç: İTÜ→İstanbul Teknik Üniversitesi"
    )
    program: Optional[str] = Field(
        None, 
        description=(
            "Bölüm/program adı. Kullanıcının söylediği bölüm adını AYNEN koru, asla başka bir bölümle karıştırma. "
            "Örnekler: Bilgisayar→Bilgisayar Mühendisliği, YBS→Yönetim Bilişim Sistemleri. "
            "UYARI: 'Bilişim Sistemleri ve Teknolojileri' ile 'Yönetim Bilişim Sistemleri (YBS)' FARKLI bölümlerdir. "
            "'Bilişim sistemleri ve teknolojileri' → 'Bilişim Sistemleri ve Teknolojileri' olarak çıkar, YBS YAPMA."
        )
    )
    uni_type: Optional[Literal["Devlet", "Vakıf", "KKTC"]] = Field(
        None, 
        description="Üniversite türü filtresi."
    )
    score_type: Optional[Literal["SAY", "EA", "SOZ", "DIL"]] = Field(
        None, 
        description="Puan türü. SAY=Sayısal, EA=Eşit Ağırlık, SOZ=Sözel, DIL=Dil"
    )
    fee_type: Optional[Literal["Burslu", "%50 İndirimli", "Ücretli"]] = Field(
        None, 
        description="Ücret/burs durumu filtresi."
    )
    
    # ═══════════════════════════════════════════════════════════════════════
    # META BİLGİLER (Loglama ve Debug için)
    # ═══════════════════════════════════════════════════════════════════════
    
    intent: Optional[str] = Field(
        None, 
        description="Niyet etiketi. Örnekler: 'siralama_sorgusu', 'bolum_bilgisi', 'universite_yorumlari', 'karsilastirma'"
    )
    reason: Optional[str] = Field(
        None, 
        description="NER ve intent belirleme gerekçesi. Debug için kullanılır."
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 2. DEEP SEARCH / QUERY PLANNING SCHEMAS (Jina AI Style)
# ═══════════════════════════════════════════════════════════════════════════════

class SubQuestion(BaseModel):
    """Bir alt soru ve arama stratejisi."""
    question: str = Field(..., description="Alt soru metni")
    search_type: Literal["yok_atlas", "web_general", "web_academic", "web_reviews", "web_specific"] = Field(
        ..., 
        description="""Bu alt soru için hangi arama yapılmalı:
        - yok_atlas: Taban puanı, sıralama, kontenjan (NET VERİ)
        - web_general: Genel tanıtım, akademik yapı, kampüs olanakları
        - web_academic: RESMİ VERİLER - Güncel ücretler (TL), burs oranları, iletişim, akademisyen sayıları
        - web_reviews: Öğrenci yorumları ve deneyimleri
        - web_specific: Spesifik konular (kulüp, yurt, staj)
        """
    )
    priority: int = Field(default=1, ge=1, le=5, description="Öncelik (1=en yüksek)")
    keywords: List[str] = Field(default_factory=list, description="Arama için anahtar kelimeler")


class QueryPlan(BaseModel):
    """
    Deep Search için sorgu planlama şeması.
    Genel soruları alt sorulara böler ve arama stratejisi belirler.
    """
    original_query: str = Field(..., description="Orijinal kullanıcı sorusu")
    query_type: Literal["specific", "general", "comparison", "ambiguous"] = Field(
        ...,
        description="""Sorgu tipi:
        - specific: Spesifik bir bilgi isteniyor (taban puanı, kontenjan vb.)
        - general: Genel tanıtım/bilgi isteniyor (X üniversitesi nasıl?)
        - comparison: Karşılaştırma isteniyor (A vs B)
        - ambiguous: Belirsiz, netleştirme gerekiyor
        """
    )
    needs_clarification: bool = Field(
        default=False, 
        description="Kullanıcıdan netleştirme gerekiyor mu?"
    )
    clarification_question: Optional[str] = Field(
        None, 
        description="Netleştirme gerekiyorsa sorulacak soru"
    )
    sub_questions: List[SubQuestion] = Field(
        default_factory=list,
        max_length=2,
        description="Alt sorular listesi. MAKSIMUM 2 alt soru üret — agent en fazla 3 iterasyonda çalışır, her iterasyon 1 tool çağırır."
    )
    thinking: str = Field(
        ..., 
        description="Düşünce süreci açıklaması (neden bu alt soruları oluşturdun?)"
    )


class ThinkingStep(BaseModel):
    """Bir düşünce adımı (ReAct Thought)."""
    step_number: int = Field(..., description="Adım numarası")
    thought_type: Literal["analyze", "plan", "execute", "observe", "evaluate", "conclude"] = Field(
        ...,
        description="""Düşünce tipi:
        - analyze: Soruyu analiz et
        - plan: Strateji belirle
        - execute: Aksiyon al (tool çağır)
        - observe: Sonuçları gözlemle
        - evaluate: Yeterliliği değerlendir
        - conclude: Sonuç çıkar
        """
    )
    content: str = Field(..., description="Düşünce içeriği")
    action_taken: Optional[str] = Field(None, description="Alınan aksiyon (varsa)")
    observation: Optional[str] = Field(None, description="Gözlem (varsa)")


class SelfEvaluation(BaseModel):
    """Cevap değerlendirme şeması (Self-Critique)."""
    is_complete: bool = Field(..., description="Cevap tam ve yeterli mi?")
    completeness_score: int = Field(..., ge=1, le=10, description="Tamlık puanı (1-10)")
    
    missing_aspects: List[str] = Field(
        default_factory=list, 
        description="Eksik kalan yönler"
    )
    needs_more_search: bool = Field(
        default=False, 
        description="Daha fazla arama gerekiyor mu?"
    )
    additional_queries: List[str] = Field(
        default_factory=list, 
        description="Yapılması gereken ek aramalar"
    )
    
    structure_quality: Literal["poor", "fair", "good", "excellent"] = Field(
        ..., 
        description="Cevabın yapısal kalitesi"
    )
    improvement_suggestions: List[str] = Field(
        default_factory=list, 
        description="İyileştirme önerileri"
    )
    
    final_decision: Literal["accept", "revise", "search_more"] = Field(
        ...,
        description="""Karar:
        - accept: Cevabı kabul et
        - revise: Cevabı düzenle
        - search_more: Daha fazla ara
        """
    )
    reasoning: str = Field(..., description="Değerlendirme gerekçesi")


class DeepSearchState(BaseModel):
    """Deep Search için genişletilmiş state."""
    query_plan: Optional[QueryPlan] = Field(None, description="Sorgu planı")
    thinking_steps: List[ThinkingStep] = Field(default_factory=list, description="Düşünce adımları")
    sub_question_results: dict = Field(default_factory=dict, description="Alt soru sonuçları")
    current_evaluation: Optional[SelfEvaluation] = Field(None, description="Güncel değerlendirme")
    search_iteration: int = Field(default=0, description="Arama iterasyonu")
