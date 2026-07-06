# graph_agent.py - Modülerleştirilmiş ve Temizlenmiş Giriş Noktası (Entrypoint)
"""
Bu dosya, geriye dönük uyumluluğu korumak amacıyla tasarlanmıştır.
Gerçek iş mantığı, node'lar, prompt'lar ve yapılandırmalar ai/ klasöründeki
bağımsız modüllere (state, models, prompts, utils, nodes, graph) taşınmıştır.
"""

# State modellerini ve veri tiplerini dışa aktar
from core.state import AgentState, NERContext, ToolCallValidation, AgentStateValidator

# Modelleri ve LLM araçlarını dışa aktar
from core.models import (
    RotatingGeminiLLM,
    llm_ner,
    llm_agent,
    llm_planner,
    llm_evaluator,
    llm_responder,
    tools,
    llm_with_tools,
    FAST_MODEL,
    SMART_MODEL,
    GEMINI_KEYS
)

# Yardımcı ve temizleme fonksiyonlarını dışa aktar
from utils.validators import get_msg_content, strip_react_thoughts, format_ner_for_agent
from utils.decorators import validate_node_input, validate_node_output, with_error_recovery
from utils.redis_cache import _save_last_entities, _load_last_entities
from utils.extractors import (
    UNI_MAPPING,
    _extract_rank_from_text,
    _extract_program_from_text,
    _extract_city_from_text,
    _extract_score_type_from_text,
    _extract_fee_from_text,
    _extract_uni_type_from_text,
    _scan_history_for_entities,
    _is_followup_question,
    _extract_uni_from_text
)

# Node'ları dışa aktar
from nodes.ner import ner_node, route_after_ner
from nodes.casual_chat import casual_chat_node
from nodes.fast_lookup import fast_lookup_node
from nodes.uni_info import uni_info_node
from nodes.planner import query_planner_node
from nodes.agent import agent_node, should_continue
from nodes.tools import ValidatedToolNode
from nodes.evaluator import self_evaluation_node, should_search_more

# Derlenmiş LangGraph uygulamasını dışa aktar
from app.graph import app_graph, workflow

print("[GRAPH-ENTRYPOINT] ✅ Tüm modüller başarıyla yüklendi. Geriye dönük uyumluluk sağlandı.")
