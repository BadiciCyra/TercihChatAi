import re
import json
from typing import Any, Tuple

# İç ReAct düşünce satırlarını tespit eden kalıplar — kullanıcıya gitmeden temizle
_REACT_THOUGHT_PATTERNS = [
    r'^\s*\*?\*?(DÜŞÜN|PLAN|HAREKET ET|GÖZLEMLE|DEĞERLENDİR|SONUÇLANDIR)\s*(\([^)]*\))?\s*:?\*?\*?.*$',
    r'^\s*\*?\*?(Thought|Plan|Action|Observation|Evaluate|Conclude|Reasoning)\s*:?\*?\*?.*$',
]
_REACT_THOUGHT_REGEX = re.compile('|'.join(_REACT_THOUGHT_PATTERNS), re.IGNORECASE | re.MULTILINE)


def get_msg_content(msg) -> str:
    """Mesaj içeriğini güvenli şekilde çeker."""
    if hasattr(msg, 'content'):
        return msg.content
    elif isinstance(msg, tuple) and len(msg) > 1:
        return str(msg[1])
    elif isinstance(msg, dict):
        return msg.get("content", str(msg))
    return str(msg)


def strip_react_thoughts(text: str) -> str:
    """LLM cevabından iç ReAct düşünce satırlarını sök — kullanıcı görmesin.
    Cevap '## ' başlığıyla veya tabloyla başlamalı; ondan önceki düşünceler kaldırılır."""
    if not text:
        return text
    # Önce satır-bazlı temizlik
    cleaned = _REACT_THOUGHT_REGEX.sub('', text)
    # İlk anlamlı içeriğe kadar baştaki gereksiz boşlukları kırp
    cleaned = cleaned.lstrip('\n ').rstrip()
    # Birden fazla peş peşe boş satırı tek satıra indir
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned or text  # Eğer hepsini sildiyse orijinali döndür (güvenlik)


def format_ner_for_agent(ner_dict: dict) -> str:
    """NER sonuçlarını JSON formatında döner - tüm alanlar görünür, boşlar null."""
    # Sadece NER ile ilgili alanları çıkar
    ner_fields = {
        "uni": ner_dict.get("uni"),
        "program": ner_dict.get("program"),
        "city": ner_dict.get("city"),
        "rank": ner_dict.get("rank"),
        "score_type": ner_dict.get("score_type"),
        "uni_type": ner_dict.get("uni_type"),
        "fee_type": ner_dict.get("fee_type"),
        "action": ner_dict.get("action"),
        "search_type": ner_dict.get("search_type"),
        "intent": ner_dict.get("intent"),
        "web_query": ner_dict.get("web_query")
    }
    return json.dumps(ner_fields, ensure_ascii=False, indent=2)


def validate_tool_response(tool_output: Any) -> Tuple[bool, str]:
    """Tool çıktısını doğrular ve geçerlilik durumunu döner."""
    if tool_output is None:
        return False, "Tool çıktısı None"

    # If tool returned a structured error payload, only reject on explicit unambiguous error keys.
    if isinstance(tool_output, dict):
        for errk in ("error", "http_error"):
            val = tool_output.get(errk)
            if val and isinstance(val, str) and len(val.strip()) > 0:
                return False, f"Tool structured error: {val}"

        # Prefer explicit content/formatted fields for subsequent text checks.
        for content_key in ("formatted", "content", "text", "result"):
            if content_key in tool_output and tool_output[content_key]:
                output_str = str(tool_output[content_key])
                break
        else:
            try:
                output_str = json.dumps(tool_output, ensure_ascii=False)
            except Exception:
                output_str = str(tool_output)
    else:
        output_str = str(tool_output)

    if not output_str.strip():
        return False, "Tool çıktısı boş"

    # If output is extremely short it is likely not useful
    if len(output_str.strip()) < 40:
        return False, "Tool çıktısı çok kısa / yetersiz"

    # For long outputs (>800 chars) assume the content is substantive
    if len(output_str) > 800:
        return True, "Tool çıktısı geçerli"

    # For SHORT outputs only: check captcha / access block indicators
    lowered = output_str.lower()
    captcha_indicators = [
        "captcha", "showcaptcha", "are you human", "please enable cookies",
        "access denied", "forbidden", "too many requests", "http 403", "http 429"
    ]
    for ind in captcha_indicators:
        if ind in lowered:
            return False, f"Tool çıktısı erişim engeli veya captcha tespit edildi: {ind}"

    # For SHORT outputs: generic English error phrases
    if re.search(r"\b(error|failed|not found|kriterlere uygun sonuç yok)\b", lowered):
        snippet = (output_str[:400] + "...") if len(output_str) > 400 else output_str
        print(f"[TOOL-VALIDATOR] ⚠️ Reddetme nedeni: genel hata anahtar kelimesi bulundu. Snippet: {snippet}")
        return False, "Tool çıktısı hata içeren anahtar kelime içeriyor"

    # For SHORT outputs: Turkish error patterns with context
    if re.search(r"(?:hata[:\s]|başarısız[:\s]|exception|traceback)", lowered):
        snippet = (output_str[:400] + "...") if len(output_str) > 400 else output_str
        print(f"[TOOL-VALIDATOR] ⚠️ Reddetme nedeni: 'hata' bağlamında hata tespit edildi. Snippet: {snippet}")
        return False, "Tool çıktısı hata içeriyor (bağlam doğrulandı)"

    return True, "Tool çıktısı geçerli"
