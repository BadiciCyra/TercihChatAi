# callbacks.py
import logging
from typing import Any, Dict, List, Optional
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from app.monitoring import record_llm_tokens, record_error

# --- LOG AYARLARI ---
# Docker loglarında "B2B_TRAFFIC" etiketiyle göreceğiz.
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("B2B_TRAFFIC")

class B2BTokenTracker(BaseCallbackHandler):
    """
    Bu sınıf, LangChain çalışırken araya girer ve:
    1. Hangi dershanenin işlem yaptığını takip eder.
    2. Google Gemini'nin harcadığı Token miktarını yakalar.
    3. Kullanılan Tool'ları loglar.
    """

    def __init__(self, school_name: str, session_id: str):
        self.school_name = school_name
        self.session_id = session_id
        self.total_tokens = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def on_llm_start(self, serialized: Dict[str, Any], prompts: List[str], **kwargs: Any) -> Any:
        """LLM (Yapay Zeka) düşünmeye başladığında tetiklenir."""
        logger.info(f"🟢 [BAŞLATILDI] Kurum: {self.school_name} | Kullanıcı: {self.session_id}")

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> Any:
        """LLM cevabı bitirdiğinde tetiklenir (Fatura burada kesilir)."""
        # Try multiple possible shapes where providers store usage info.
        try:
            llm_out = None
            if hasattr(response, 'llm_output') and response.llm_output:
                llm_out = response.llm_output
            elif isinstance(response, dict):
                llm_out = response.get('llm_output') or response

            token_usage = {}
            if isinstance(llm_out, dict):
                # Common patterns
                token_usage = llm_out.get('token_usage') or llm_out.get('usage') or {}

                # Some providers (Google) use camelCase keys
                if not token_usage:
                    # try to extract direct fields
                    prompt = llm_out.get('prompt_tokens') or llm_out.get('promptTokens')
                    comp = llm_out.get('completion_tokens') or llm_out.get('completionTokens')
                    total = llm_out.get('total_tokens') or llm_out.get('totalTokens')
                    if any(v is not None for v in (prompt, comp, total)):
                        token_usage = {
                            'prompt_tokens': int(prompt or 0),
                            'completion_tokens': int(comp or 0),
                            'total_tokens': int(total or (int(prompt or 0) + int(comp or 0)))
                        }

            # fallback: maybe response contains 'usage' at top-level
            if not token_usage and isinstance(response, dict):
                u = response.get('usage') or {}
                token_usage = u

            p_tokens = int(token_usage.get('prompt_tokens', token_usage.get('promptTokens', 0) or 0))
            c_tokens = int(token_usage.get('completion_tokens', token_usage.get('completionTokens', 0) or 0))
            t_tokens = int(token_usage.get('total_tokens', token_usage.get('totalTokens', 0) or (p_tokens + c_tokens)))

            # As a last-resort, if everything missing, try to estimate from generations length
            if t_tokens == 0:
                try:
                    gens = getattr(response, 'generations', None) or []
                    # Count characters as crude proxy (not accurate for billing)
                    est = 0
                    for g in gens:
                        txt = ''
                        if isinstance(g, list):
                            for gg in g:
                                txt += getattr(gg, 'text', str(gg))
                        else:
                            txt += getattr(g, 'text', str(g))
                        est += max(0, len(txt) // 4)
                    t_tokens = est
                except Exception:
                    t_tokens = 0

            # Accumulate
            self.prompt_tokens += p_tokens
            self.completion_tokens += c_tokens
            self.total_tokens += t_tokens

            logger.info(
                f"💰 [MALİYET] Kurum: {self.school_name} | "
                f"Girdi: {p_tokens} + Çıktı: {c_tokens} = Toplam: {t_tokens} Token | GrandTotal: {self.total_tokens}"
            )
            record_llm_tokens(
                school_name=self.school_name,
                prompt_tokens=p_tokens,
                completion_tokens=c_tokens,
                total_tokens=t_tokens,
            )
        except Exception as e:
            logger.warning(f"⚠️ Token parse hatası: {e}")
            record_error("token_parse", source="llm_callback")
            
            # --- GELİŞTİRME NOTU ---
            # İleride buraya veritabanı kodu ekleyeceksin:
            # await db.save_usage(school=self.school_name, tokens=t_tokens)
            # -----------------------

    def on_tool_start(self, serialized: Dict[str, Any], input_str: str, **kwargs: Any) -> Any:
        """Bir Tool (YÖK Atlas veya Web Search) çalışmaya başladığında tetiklenir."""
        tool_name = serialized.get("name", "Bilinmeyen-Tool")
        logger.info(f"🔨 [TOOL KULLANIMI] Kurum: {self.school_name} -> {tool_name} çalıştırılıyor...")

    def on_chain_error(self, error: BaseException, **kwargs: Any) -> Any:
        """Sistem bir yerde hata verirse burası yakalar."""
        logger.error(f"🚨 [HATA] Kurum: {self.school_name} | Mesaj: {str(error)}")
        record_error(type(error).__name__, source="llm_chain")
