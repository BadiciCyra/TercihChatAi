from langchain_core.messages import HumanMessage

from state import AgentState
from models import llm_planner
from schemas import QueryPlan
from utils.validators import get_msg_content, format_ner_for_agent
from utils.decorators import with_error_recovery


@with_error_recovery
def query_planner_node(state: AgentState) -> dict:
    """
    Deep Search için sorgu planlayıcı.
    Genel soruları alt sorulara böler ve arama stratejisi belirler.
    """
    messages = state.get("messages", [])
    ner_context = state.get("ner_context", {})
    
    # Kullanıcı sorusunu al
    user_query = ""
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage) or (isinstance(msg, tuple) and msg[0] == "user"):
            user_query = get_msg_content(msg)
            break
    
    # NER bağlamını ekle
    ner_summary = format_ner_for_agent(ner_context)
    
    human_content = (
        f"Kullanıcı Sorusu: {user_query}\n\n"
        f"NER Bağlamı (JSON):\n{ner_summary}\n\n"
        "Bu sorguyu analiz et ve bir QueryPlan oluştur."
    )
    
    try:
        structured_llm = llm_planner.with_structured_output(QueryPlan)
        # Sadece structured LLM'i invoke ediyoruz çünkü ChatPromptTemplate { } işaretlerinde patlar
        result = structured_llm.invoke(human_content)
        plan_dict = result.dict() if hasattr(result, 'dict') else result
        print(f"[PLANNER] 🎯 Plan oluşturuldu: {len(plan_dict.get('sub_questions', []))} alt soru.")
        return {
            "query_plan": plan_dict,
            "iteration_count": 0
        }
    except Exception as e:
        print(f"[PLANNER] 💥 Planlama hatası: {e}")
        # Boş plan fallback'i
        return {
            "query_plan": {
                "thinking": "Hata oluştu, tekli aramaya geçiliyor.",
                "query_type": "specific",
                "sub_questions": [
                    {"id": 1, "question": user_query, "search_strategy": "web_general", "depends_on": []}
                ]
            },
            "iteration_count": 0
        }
