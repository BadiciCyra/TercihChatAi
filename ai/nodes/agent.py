from typing import Literal
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage
from pydantic import ValidationError

from state import AgentState, ToolCallValidation
from models import llm_agent, llm_with_tools
from utils.validators import get_msg_content, format_ner_for_agent
from utils.decorators import validate_node_input, validate_node_output, with_error_recovery
from prompts.agent import REACT_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Snippet sufficiency & fetch-URL helpers (used by agent_node in task 6.1)
# ---------------------------------------------------------------------------

def _evaluate_snippet_sufficiency(tool_messages: list) -> bool:
    """Return True if the total character count of all snippets >= 500.

    Sums the character count of each ToolMessage's text content.
    Returns False (insufficient) when total < 500, True (sufficient) when >= 500.

    Validates: Requirements 3.2, 3.3
    """
    total_chars = 0
    for msg in tool_messages:
        if isinstance(msg, ToolMessage):
            content = msg.content
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                # Handle list-of-block content (e.g. [{type: "text", text: "..."}])
                for block in content:
                    if isinstance(block, dict):
                        total_chars += len(block.get("text", ""))
                    elif isinstance(block, str):
                        total_chars += len(block)
    return total_chars >= 500


def _select_fetch_urls(search_results: list, fetched_urls: set, max_fetch: int = 3) -> list:
    """Return up to max_fetch URLs from search_results that are not in fetched_urls.

    Args:
        search_results: List of dicts, each containing at least a "url" key.
        fetched_urls:   Set of URLs already fetched in this session.
        max_fetch:      Maximum number of URLs to return (default 3).

    Returns:
        A list of URL strings, len <= max_fetch, none of which are in fetched_urls.

    Validates: Requirements 3.6, 4.2, 4.6
    """
    selected = []
    for result in search_results:
        if len(selected) >= max_fetch:
            break
        url = result.get("url", "")
        if url and url not in fetched_urls:
            selected.append(url)
    return selected


def _format_fetch_context(url: str, content: str) -> str:
    """Format fetched page content with its source URL for LLM context.

    Returns a string in the format ``[Kaynak: {url}]\\n{content}``.

    Validates: Requirements 4.6
    """
    return f"[Kaynak: {url}]\n{content}"


# ---------------------------------------------------------------------------


@validate_node_input
@validate_node_output
@with_error_recovery
async def agent_node(state: AgentState) -> dict:
    """
    Deep Search ReAct Agent - Düşün → Hareket Et → Gözlemle döngüsü.
    Query Plan'daki alt soruları kullanarak kapsamlı arama yapar.
    """
    messages = state.get("messages", [])
    ner_context = state.get("ner_context", {})
    iteration_count = state.get("iteration_count", 0)
    query_plan = state.get("query_plan", {})
    thinking_steps = state.get("thinking_steps", [])
    sub_question_results = state.get("sub_question_results", {})

    # Sonsuz döngü koruması (max 3 iterasyon - hız için sınırlandırıldı)
    if iteration_count >= 3:
        print("[AGENT] ⚠️ Maksimum iterasyon sayısına ulaşıldı, direkt yanıt veriliyor.")

        existing_answer = state.get("current_answer", "")

        # Sadece bu request'in ToolMessage'larını topla.
        # messages listesinde checkpointer'dan gelen eski turların mesajları
        # da olabilir — ama iteration_count=0'dan başladığı için bu request'e
        # ait olanlar listenin sonunda. Hepsini alıyoruz ama kısa tutuyoruz.
        tool_contents = []
        for msg in messages:
            if isinstance(msg, ToolMessage):
                txt = get_msg_content(msg)
                if txt and len(txt.strip()) > 50:
                    tool_contents.append(txt[:1200])

        if tool_contents:
            # Kullanıcının bu turdaki sorusunu al — tuple veya HumanMessage olabilir.
            # reversed() ile geçmiş konuşmalardan değil bu turdakini alalım:
            # iteration_count sıfırdan başladığı için en son kullanıcı mesajı
            # listenin sonlarında. Tuple olanlar bu turn'ün user inputu.
            user_query = ""
            for msg in reversed(messages):
                if isinstance(msg, tuple) and msg[0] == "user":
                    user_query = get_msg_content(msg)
                    break
            if not user_query:
                for msg in reversed(messages):
                    if isinstance(msg, HumanMessage):
                        user_query = get_msg_content(msg)
                        break

            synthesis_prompt = (
                f"Kullanıcı sorusu: {user_query}\n\n"
                f"Aşağıdaki araştırma sonuçlarından yararlanarak kullanıcının sorusunu yanıtla. "
                f"Yapılandırılmış, markdown formatında kapsamlı bir cevap yaz.\n\n"
                + "\n\n---\n\n".join(tool_contents)
            )
            try:
                synthesis_response = await llm_agent.ainvoke([
                    SystemMessage(content=REACT_SYSTEM_PROMPT),
                    HumanMessage(content=synthesis_prompt),
                ])
                fallback_content = synthesis_response.content or existing_answer or "Üzgünüm, bu sorgu için yeterli bilgi toplayamadım."
            except Exception:
                fallback_content = existing_answer or "Üzgünüm, bu sorgu için yeterli bilgi toplayamadım."
        elif existing_answer:
            fallback_content = existing_answer
        else:
            fallback_content = "Üzgünüm, bu sorgu için yeterli bilgi toplayamadım. Lütfen daha spesifik bir soru sorar mısın?"

        return {
            "messages": [AIMessage(content=fallback_content)],
            "iteration_count": iteration_count,
            "current_answer": fallback_content,
        }
    
    ner_summary = format_ner_for_agent(ner_context)
    
    user_query = ""
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage) or (isinstance(msg, tuple) and msg[0] == "user"):
            user_query = get_msg_content(msg)
            break

    # ── Erken synthesis: 2+ ToolMessage ve yeterli veri varsa tekrar arama yapma ──
    tool_messages_so_far = [m for m in messages if isinstance(m, ToolMessage)]
    if len(tool_messages_so_far) >= 2 and _evaluate_snippet_sufficiency(tool_messages_so_far):
        print(f"[AGENT] ✅ Yeterli veri toplandı ({len(tool_messages_so_far)} tool sonucu), synthesis yapılıyor.")
        tool_contents = [
            get_msg_content(m)[:1200]
            for m in tool_messages_so_far
            if get_msg_content(m) and len(get_msg_content(m).strip()) > 50
        ]
        # tuple formatındaki kullanıcı sorusunu bul (bu request'e ait)
        if not user_query:
            for msg in reversed(messages):
                if isinstance(msg, tuple) and msg[0] == "user":
                    user_query = get_msg_content(msg)
                    break
        synthesis_prompt = (
            f"Kullanıcı sorusu: {user_query}\n\n"
            f"Aşağıdaki araştırma sonuçlarından yararlanarak kullanıcının sorusunu yanıtla. "
            f"Yapılandırılmış, markdown formatında kapsamlı bir cevap yaz.\n\n"
            + "\n\n---\n\n".join(tool_contents)
        )
        try:
            synthesis_response = await llm_agent.ainvoke([
                SystemMessage(content=REACT_SYSTEM_PROMPT),
                HumanMessage(content=synthesis_prompt),
            ])
            final_content = synthesis_response.content or "Üzgünüm, bu sorgu için yeterli bilgi toplayamadım."
        except Exception:
            final_content = "Üzgünüm, bu sorgu için yeterli bilgi toplayamadım."
        return {
            "messages": [AIMessage(content=final_content)],
            "iteration_count": iteration_count + 1,
            "current_answer": final_content,
        }
    
    formatted_messages = [SystemMessage(content=REACT_SYSTEM_PROMPT)]
    
    plan_info = ""
    if query_plan:
        query_type = query_plan.get("query_type", "specific")
        sub_questions = query_plan.get("sub_questions", [])
        thinking = query_plan.get("thinking", "")
        
        if sub_questions:
            plan_info = f"""
**Sorgu Tipi:** {query_type}
**Plan Düşüncesi:** {thinking[:300]}...

**Alt Sorular (Sırayla yanıtla):**
"""
            for idx, sq in enumerate(sub_questions, 1):
                status = "✅" if sq.get("question") in sub_question_results else "⏳"
                plan_info += f"{idx}. {status} [{sq.get('search_type')}] {sq.get('question')}\n"
    
    context_msg = f"""
**Kullanıcı Sorgusu:** {user_query}

**Çıkarılan Varlıklar (NER - JSON):**
```json
{ner_summary}
```
{plan_info}

**ARAMA KURALLARI (ZORUNLU):**
- NER'de hem `uni` hem `program` varsa: her web_search sorgusuna ikisini BİRLİKTE yaz.
  Yanlış: `"Yeditepe üniversitesi yorumları"`
  Doğru:  `"Yeditepe üniversitesi yazılım geliştirme bölümü yorumları"`
- Alt soruda `[web_reviews]` yazıyorsa: `query_type="reviews"` kullan (yorum/ekşi sözlük sonuçları için).
- Alt soruda `[web_academic]` yazıyorsa: `query_type="academic"` kullan.
- Alt soruda `[web_general]` yazıyorsa: `query_type="general"` kullan.

**Düşünce Akışı:**
1. Önce sorguyu analiz et
2. Query Plan'daki alt soruları sırayla yanıtla (varsa)
3. Her arama sonucunu değerlendir
4. Kapsamlı ve yapılandırılmış bir cevap oluştur
"""
    formatted_messages.append(HumanMessage(content=context_msg))
    
    msg_idx = 0
    while msg_idx < len(messages):
        msg = messages[msg_idx]
        
        if isinstance(msg, tuple):
            role, content = msg[0], msg[1]
            if role == "user":
                if msg_idx == 0:
                    msg_idx += 1
                    continue
                formatted_messages.append(HumanMessage(content=content))
            else:
                formatted_messages.append(AIMessage(content=content))
        elif isinstance(msg, HumanMessage):
            if msg_idx == 0:
                msg_idx += 1
                continue
            formatted_messages.append(msg)
        elif isinstance(msg, AIMessage):
            if hasattr(msg, 'tool_calls') and msg.tool_calls:
                if msg_idx + 1 < len(messages) and isinstance(messages[msg_idx + 1], ToolMessage):
                    formatted_messages.append(msg)
                else:
                    print(f"[AGENT] ⚠️ Tool call sonrası ToolMessage eksik, content olarak ekleniyor")
                    formatted_messages.append(AIMessage(content=msg.content or "Arama yapılıyor..."))
            else:
                formatted_messages.append(msg)
        elif isinstance(msg, ToolMessage):
            formatted_messages.append(msg)
        
        msg_idx += 1
    
    last_msg_type = type(messages[-1]).__name__ if messages else "None"
    query_type = query_plan.get("query_type", "specific") if query_plan else "specific"
    print(f"\n[AGENT] 🤖 Deep Search ReAct Döngüsü #{iteration_count + 1}")
    print(f"[AGENT] 📝 Son mesaj tipi: {last_msg_type} | Sorgu tipi: {query_type}")
    print(f"[AGENT] 🧠 NER Bağlamı (JSON):")
    print(ner_summary)
    
    try:
        response = await llm_with_tools.ainvoke(formatted_messages)
        
        new_thinking_step = {
            "step_number": len(thinking_steps) + 1,
            "thought_type": "execute",
            "content": f"İterasyon #{iteration_count + 1}",
            "action_taken": None,
            "observation": None
        }
        
        if hasattr(response, 'tool_calls') and response.tool_calls:
            tool_names = [tc.get('name', 'unknown') for tc in response.tool_calls]
            print(f"[AGENT] 🔧 Tool çağrısı yapılıyor: {tool_names}")
            new_thinking_step["action_taken"] = f"Tool çağrısı: {', '.join(tool_names)}"
        else:
            print(f"[AGENT] ✅ Doğrudan yanıt veriliyor")
            new_thinking_step["thought_type"] = "conclude"
            new_thinking_step["action_taken"] = "Doğrudan yanıt"
        
        return {
            "messages": [response],
            "iteration_count": iteration_count + 1,
            "thinking_steps": thinking_steps + [new_thinking_step],
            "current_answer": response.content if not (hasattr(response, 'tool_calls') and response.tool_calls) else None
        }
        
    except Exception as e:
        print(f"[AGENT] 💥 Hata: {e}")
        return {
            "messages": [AIMessage(content=f"Bir hata oluştu: {str(e)}. Lütfen tekrar deneyin.")],
            "iteration_count": iteration_count + 1
        }


def should_continue(state: AgentState) -> Literal["tools", "end"]:
    """
    Agent'in tool çağırıp çağırmadığını kontrol eder.
    """
    messages = state.get("messages", [])
    iteration_count = state.get("iteration_count", 0)
    
    if iteration_count >= 3:
        print("[VALIDATOR] ⚠️ Maksimum iterasyon aşıldı, döngü sonlandırılıyor")
        return "end"
    
    if not messages:
        print("[VALIDATOR] ⚠️ Mesaj listesi boş")
        return "end"
    
    last_message = messages[-1]
    
    if hasattr(last_message, 'tool_calls') and last_message.tool_calls:
        for tc in last_message.tool_calls:
            try:
                ToolCallValidation(**tc)
                print(f"[VALIDATOR] ✅ Tool call geçerli: {tc.get('name')}")
            except ValidationError as e:
                print(f"[VALIDATOR] ⚠️ Tool call uyarısı: {e}")
        return "tools"
    
    return "end"
