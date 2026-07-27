import operator
from typing import Annotated, Any, List, Optional, Union
from pydantic import BaseModel, Field, field_validator, model_validator

# State ve validation şemaları
class NERContext(BaseModel):
    """NER çıktısı için validation şeması."""
    action: Optional[str] = Field(default="RAG_SEARCH", description="Aksiyon tipi")
    search_type: Optional[str] = Field(default=None, description="Arama tipi")
    web_query: Optional[str] = Field(default=None, description="Web sorgusu")
    rank: Optional[str] = Field(default=None, description="Sıralama")
    city: Optional[str] = Field(default=None, description="Şehir")
    uni: Optional[str] = Field(default=None, description="Üniversite")
    program: Optional[str] = Field(default=None, description="Bölüm")
    uni_type: Optional[str] = Field(default=None, description="Üniversite türü")
    score_type: Optional[str] = Field(default=None, description="Puan türü")
    fee_type: Optional[str] = Field(default=None, description="Ücret türü")
    intent: Optional[str] = Field(default=None, description="Niyet")
    reason: Optional[str] = Field(default=None, description="Gerekçe")
    
    @field_validator('rank')
    @classmethod
    def validate_rank(cls, v):
        """Sıralama değerini normalize et."""
        if v is None:
            return None
        # "150k" → "150000", "50 bin" → "50000"
        v = str(v).lower().strip()
        v = v.replace("k", "000").replace(" bin", "000").replace("bin", "000")
        v = ''.join(filter(str.isdigit, v))
        return v if v else None
    
    @field_validator('score_type')
    @classmethod
    def validate_score_type(cls, v):
        """Puan türünü normalize et."""
        if v is None:
            return None
        v = str(v).upper().strip()
        valid_types = {"SAY", "EA", "SOZ", "DIL", "TYT"}
        # Alias mapping
        aliases = {
            "SAYISAL": "SAY", "SÖZEL": "SOZ", "SOZEL": "SOZ",
            "EŞİT AĞIRLIK": "EA", "ESIT AGIRLIK": "EA", "TM": "EA",
            "YABANCI DİL": "DIL", "YABANCI DIL": "DIL"
        }
        v = aliases.get(v, v)
        return v if v in valid_types else None
    
    @field_validator('uni_type')
    @classmethod
    def validate_uni_type(cls, v):
        """Üniversite türünü normalize et."""
        if v is None:
            return None
        v = str(v).capitalize().strip()
        valid_types = {"Devlet", "Vakıf", "Kktc", "KKTC"}
        if v.upper() == "KKTC":
            return "KKTC"
        return v if v in valid_types else None


class ToolCallValidation(BaseModel):
    """Tool çağrısı validation şeması."""
    name: str = Field(..., description="Tool adı")
    args: dict = Field(default_factory=dict, description="Tool argümanları")
    id: Optional[str] = Field(default=None, description="Tool call ID")
    
    @field_validator('name')
    @classmethod
    def validate_tool_name(cls, v):
        """Tool adının geçerli olduğunu kontrol et."""
        valid_tools = {"yok_atlas_search", "web_search", "fetch_link_content"}
        if v not in valid_tools:
            raise ValueError(f"Geçersiz tool adı: {v}. Geçerli toollar: {valid_tools}")
        return v
    
    @model_validator(mode='after')
    def validate_tool_args(self):
        """Tool argümanlarının uygunluğunu kontrol et."""
        if self.name == "yok_atlas_search":
            # En az bir parametre olmalı
            required_any = ["rank", "city", "uni", "program"]
            if not any(self.args.get(k) for k in required_any):
                print(f"[VALIDATOR] ⚠️ yok_atlas_search için parametreler yetersiz: {self.args}")
        elif self.name == "web_search":
            # query zorunlu
            if not self.args.get("query"):
                raise ValueError("web_search için 'query' parametresi zorunlu")
        return self


class AgentStateValidator(BaseModel):
    """Agent state validation şeması."""
    messages: List[Any] = Field(default_factory=list, description="Mesaj geçmişi")
    ner_context: dict = Field(default_factory=dict, description="NER bağlamı")
    iteration_count: int = Field(default=0, ge=0, le=10, description="İterasyon sayacı")
    
    @field_validator('iteration_count')
    @classmethod
    def validate_iteration(cls, v):
        """Sonsuz döngü koruması."""
        if v > 10:
            raise ValueError(f"Maksimum iterasyon aşıldı: {v}")
        return v
    
    @field_validator('messages')
    @classmethod
    def validate_messages(cls, v):
        """Mesaj listesinin boş olmadığını kontrol et."""
        if not v:
            print("[VALIDATOR] ⚠️ Mesaj listesi boş")
        return v


# TypedDict (LangGraph uyumluluğu için)
class AgentState(dict):
    """Agent state structure for LangGraph."""
    pass

# standard TypedDict usage:
from typing import TypedDict
class AgentState(TypedDict):
    messages: Annotated[List[Any], operator.add]
    ner_context: dict
    iteration_count: int
    # Deep Search alanları
    query_plan: Optional[dict]  # QueryPlan
    thinking_steps: List[dict]  # ThinkingStep listesi
    sub_question_results: dict  # Alt soru sonuçları
    current_answer: Optional[str]  # Güncel cevap
    evaluation: Optional[dict]  # SelfEvaluation
    search_depth: int  # Arama derinliği (0-3)
    fetched_urls: Optional[List[str]]  # URLs already fetched this session (dedup)
    # YENİ — mod seçimi (Requirements: 6.1, 6.2, 6.3)
    mode: Optional[str]
    # IMMUTABLE / SALT-OKUNUR: Bu alan yalnızca initialization sırasında
    # (gate.py → app_graph.ainvoke inputs dict) bir kez set edilir.
    # Pipeline içindeki hiçbir node bu alanı DEĞİŞTİRMEMELİDİR.
    # Geçerli değerler: "wizard" | "research" | "career" | "guidance" | None
    # None → NER otomatik yönlendirme (geriye dönük uyumluluk)
    # YENİ — sorgu spesifiklik bayrakları (Requirements: 1.1, 1.3, 1.5)
    is_specific: Optional[bool]          # Set by uni_info_node
    is_career_specific: Optional[bool]   # Set by career_info_node
    # Oturum kimliği (= LangGraph thread_id). gate.py tarafından bir kez set
    # edilir; node'lar SADECE OKUR. "Son arama" hafızasının kullanıcıya özel
    # tutulması için gerekli — paylaşılan kova oturumlar arası sızıntı yapıyordu.
    session_id: Optional[str]
