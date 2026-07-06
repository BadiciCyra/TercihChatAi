import os
import logging
from typing import Optional
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from core.state import AgentState

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# PIPELINE ROUTER — Mod Bazlı Giriş Noktası Seçici
# ═══════════════════════════════════════════════════════════════════════════════

ENTRY_NODE_MAP = {
    "wizard":   "fast_lookup",
    "research": "uni_info",
    "career":   "career_info",
    "guidance": "casual_chat",
    None:       "ner",          # Otomatik yönlendirme (geriye dönük uyumlu)
}


def select_entry_point(mode: Optional[str]) -> str:
    """Mod değerinden LangGraph node adını döndür."""
    entry = ENTRY_NODE_MAP.get(mode, "ner")
    logger.info(f"[PIPELINE_ROUTER] mode={mode!r} → entry={entry}")
    return entry


from core.models import tools
from nodes.ner import ner_node, route_after_ner
from nodes.casual_chat import casual_chat_node
from nodes.fast_lookup import fast_lookup_node
from nodes.uni_info import uni_info_node
from nodes.career_info import career_info_node
from nodes.planner import query_planner_node
from nodes.agent import agent_node, should_continue
from nodes.tools import ValidatedToolNode
from nodes.evaluator import self_evaluation_node, should_search_more

# ═══════════════════════════════════════════════════════════════════════════════
# GRAPH TANIMI (Deep Search ReAct Döngüsü)
# ═══════════════════════════════════════════════════════════════════════════════

workflow = StateGraph(AgentState)

# Node'ları ekle
workflow.add_node("ner", ner_node)                         # 1. Varlık çıkarma
workflow.add_node("casual_chat", casual_chat_node)         # 1.5 Selamlama (1 LLM)
workflow.add_node("fast_lookup", fast_lookup_node)         # 1.6 Yapısal sorgu hızlı yol (1 tool + 1 LLM, ~5-10sn)
workflow.add_node("uni_info", uni_info_node)               # 1.7 Üniversite genel tanıtım (1 tool + 1 LLM, ~10-15sn)
workflow.add_node("career_info", career_info_node)         # 1.8 Kariyer/bölüm araştırma (3 tool + 1 LLM, ~15-20sn)
workflow.add_node("planner", query_planner_node)           # 2. Deep Search: Planlama
workflow.add_node("agent", agent_node)                     # 3. Deep Search: ReAct Döngüsü (Karar)
workflow.add_node("tools", ValidatedToolNode(tools))       # 4. Deep Search: Araç çalıştırma
workflow.add_node("evaluator", self_evaluation_node)       # 5. Deep Search: Kalite Kontrol (Değerlendirme)

# Akış kenarları (edges)
workflow.set_entry_point("ner")

# NER sonrasında kural tabanlı veya bağlamsal yönlendirme
workflow.add_conditional_edges(
    "ner",
    route_after_ner,
    {
        "casual": "casual_chat",
        "fast": "fast_lookup",
        "uni_info": "uni_info",
        "career_info": "career_info",
        "search": "planner",
    }
)
workflow.add_edge("casual_chat", END)
workflow.add_edge("fast_lookup", END)
workflow.add_edge("uni_info", END)
workflow.add_edge("career_info", END)
workflow.add_edge("planner", "agent")

# Agent'ten sonra koşullu geçiş
workflow.add_conditional_edges(
    "agent",
    should_continue,
    {
        "tools": "tools",
        "end": "evaluator"
    }
)

# Tool'dan sonra TEKRAR agent'e dön (ReAct döngüsü!)
workflow.add_edge("tools", "agent")

# Değerlendirmeden sonra koşullu geçiş
workflow.add_conditional_edges(
    "evaluator",
    should_search_more,
    {
        "agent": "agent",
        "end": END
    }
)

# ═══════════════════════════════════════════════════════════════════════════════
# GRAPH DERLEMESİ
# ═══════════════════════════════════════════════════════════════════════════════

def _build_checkpointer():
    """Redis varsa Redis-backed checkpointer, yoksa in-memory kullan."""
    redis_url = os.getenv("REDIS_URL", "")
    if not redis_url:
        print("[GRAPH] ℹ️ REDIS_URL yok, MemorySaver kullanılıyor (sadece dev/test)")
        return MemorySaver()
    try:
        from langgraph.checkpoint.redis import RedisSaver
        checkpointer = RedisSaver.from_conn_string(redis_url)
        print(f"[GRAPH] ✅ RedisCheckpointer aktif — konuşmalar Redis'e kaydediliyor")
        return checkpointer
    except ImportError:
        print("[GRAPH] ⚠️ langgraph-checkpoint-redis kurulu değil, MemorySaver kullanılıyor")
        print("[GRAPH]    Kurmak için: pip install langgraph-checkpoint-redis")
    except Exception as e:
        print(f"[GRAPH] ⚠️ Redis checkpointer başlatılamadı ({e}), MemorySaver kullanılıyor")
    return MemorySaver()


_checkpointer = _build_checkpointer()
app_graph = workflow.compile(checkpointer=_checkpointer)

print("[GRAPH] ✅ Deep Search ReAct Agent Graph başarıyla derlendi!")
