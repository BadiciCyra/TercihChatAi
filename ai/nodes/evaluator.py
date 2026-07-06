import re
from typing import Literal
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage

from state import AgentState
from models import llm_evaluator
from schemas import SelfEvaluation
from prompts.evaluator import EVALUATION_PROMPT
from utils.validators import get_msg_content
from utils.decorators import with_error_recovery


@with_error_recovery
def self_evaluation_node(state: AgentState) -> dict:
    """
    Deep Search için cevap değerlendirme.
    Cevabın yeterliliğini kontrol eder, eksikse ek arama önerir.
    """
    messages = state.get("messages", [])
    query_plan = state.get("query_plan", {}) or {}
    thinking_steps = state.get("thinking_steps", [])
    search_depth = state.get("search_depth", 0)
    
    iteration_count = state.get("iteration_count", 0)
    
    # Maksimum iterasyon kontrolü (agent döngüsünden gelen)
    if iteration_count >= 3:
        print("[EVAL] ⚠️ Agent iterasyon limitine ulaştı, değerlendirme atlanıyor (accept).")
        return {"search_depth": search_depth}
    
    # Maksimum derinlik kontrolü
    if search_depth >= 1:
        print("[EVAL] ⚠️ Maksimum arama derinliğine ulaşıldı, mevcut cevap kabul ediliyor.")
        return {"search_depth": search_depth}
    
    # Son AI mesajını al (cevap)
    last_ai_message = None
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            last_ai_message = msg
            break
    
    if not last_ai_message or not last_ai_message.content:
        print("[EVAL] ⚠️ Değerlendirilecek cevap bulunamadı")
        return {"search_depth": search_depth}
    
    current_answer = last_ai_message.content
    original_query = query_plan.get("original_query", "")
    query_type = query_plan.get("query_type", "specific")
    
    # Sadece genel sorularda derinlemesine değerlendirme yap
    has_tool_results = any(
        hasattr(m, 'type') and getattr(m, 'type', '') == 'tool'
        for m in state.get('messages', [])
    )
    if query_type == "specific" and not has_tool_results:
        print("[EVAL] ℹ️ Spesifik sorgu + tool sonucu yok, değerlendirme atlanıyor")
        return {"search_depth": search_depth}
    
    eval_human_content = (
        f"Orijinal Sorgu: {original_query}\n"
        f"Sorgu Tipi: {query_type}\n\n"
        f"Mevcut Cevap:\n{current_answer[:2000]}\n\nBu cevabı değerlendir."
    )
    
    try:
        structured_llm = llm_evaluator.with_structured_output(SelfEvaluation)
        eval_messages = [
            SystemMessage(content=EVALUATION_PROMPT),
            HumanMessage(content=eval_human_content),
        ]
        evaluation: SelfEvaluation = structured_llm.invoke(eval_messages)
        
        print(f"\n[EVAL] 🔍 Cevap Değerlendirmesi:")
        print(f"[EVAL] 📊 Tamlık Puanı: {evaluation.completeness_score}/10")
        print(f"[EVAL] 🏗️ Yapısal Kalite: {evaluation.structure_quality}")
        print(f"[EVAL] 🎯 Karar: {evaluation.final_decision}")
        print(f"[EVAL] 💭 Gerekçe: {evaluation.reasoning[:200]}...")
        
        if evaluation.missing_aspects:
            print(f"[EVAL] ⚠️ Eksik Yönler: {', '.join(evaluation.missing_aspects)}")

        eval_thinking = {
            "step_number": len(thinking_steps) + 1,
            "thought_type": "evaluate",
            "content": evaluation.reasoning,
            "observation": f"Tamlık: {evaluation.completeness_score}/10, Karar: {evaluation.final_decision}"
        }

        final_decision = evaluation.final_decision

        def _extract_text(item) -> str:
            if item is None:
                return ""
            if isinstance(item, str):
                return item
            if isinstance(item, list):
                parts = [_extract_text(i) for i in item]
                parts = [p for p in parts if p]
                return "\n\n".join(parts)
            if isinstance(item, dict):
                for k in ("text", "content", "answer", "message"):
                    if k in item:
                        return _extract_text(item[k])
                for v in item.values():
                    t = _extract_text(v)
                    if t:
                        return t
                return ""
            try:
                return str(item)
            except Exception:
                return ""

        scraped_urls = []
        url_re = re.compile(r"https?://[\w\-./?&=%#]+")

        for m in messages:
            if isinstance(m, ToolMessage):
                txt = get_msg_content(m)
                for u in url_re.findall(txt):
                    if u not in scraped_urls and not any(skip in u for skip in ['google.com/search', 'yahoo.com/search', 'yandex.', 'wikipedia.org/w/api', 'grokipedia.com', 'duckduckgo.com']):
                        scraped_urls.append(u)

        main_content = _extract_text(current_answer)
        
        if scraped_urls:
            sentences = re.split(r'(?<=[.!?])\s+', main_content)
            cited_sentences = []
            src_idx = 0
            for i, sent in enumerate(sentences):
                cited_sentences.append(sent)
                if (i + 1) % 3 == 0 and src_idx < len(scraped_urls):
                    source_num = src_idx + 1
                    cited_sentences[-1] = cited_sentences[-1].rstrip() + f" [{source_num}]"
                    src_idx += 1
            main_content = ' '.join(cited_sentences)

        md_parts = [main_content]

        if scraped_urls:
            all_sources_md = "\n".join(f"[{i+1}] {url}" for i, url in enumerate(scraped_urls[:15]))
            md_parts.append(f"### 📚 Kaynaklar\n{all_sources_md}")
        
        if evaluation.missing_aspects:
            print(f"[EVAL-DEBUG] Eksik yönler (kullanıcıya gösterilmiyor): {evaluation.missing_aspects}")

        final_markdown = "\n\n".join(md_parts).strip()

        if final_decision in ("accept", "revise"):
            final_msg = AIMessage(content=final_markdown or main_content)
            return {
                "messages": [final_msg],
                "evaluation": evaluation.model_dump(),
                "thinking_steps": thinking_steps + [eval_thinking],
                "search_depth": search_depth + 1
            }

        return {
            "evaluation": evaluation.model_dump(),
            "thinking_steps": thinking_steps + [eval_thinking],
            "search_depth": search_depth + 1
        }
        
    except Exception as e:
        print(f"[EVAL] 💥 Değerlendirme hatası: {e}")
        return {"search_depth": search_depth + 1}


def should_search_more(state: AgentState) -> Literal["agent", "end"]:
    """
    Değerlendirme sonucuna göre daha fazla arama gerekip gerekmediğini kontrol eder.
    """
    evaluation = state.get("evaluation", {})
    search_depth = state.get("search_depth", 0)
    iteration_count = state.get("iteration_count", 0)
    
    if iteration_count >= 3 or search_depth >= 1:
        return "end"
    
    if not evaluation:
        return "end"
    
    final_decision = evaluation.get("final_decision", "accept")
    
    if final_decision == "search_more":
        additional_queries = evaluation.get("additional_queries", [])
        print(f"[EVAL] 🔄 Ek arama gerekiyor: {additional_queries}")
        return "agent"
    
    return "end"
