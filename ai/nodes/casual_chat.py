from langchain_core.messages import AIMessage, SystemMessage, HumanMessage, ToolMessage

from state import AgentState
from models import llm_responder
from prompts.casual import CASUAL_CHAT_PROMPT
from prompts.guidance import GUIDANCE_CHAT_PROMPT
from utils.validators import get_msg_content
from utils.decorators import validate_node_input, validate_node_output, with_error_recovery


@validate_node_input
@validate_node_output
@with_error_recovery
async def casual_chat_node(state: AgentState) -> dict:
    """Sohbet/selamlama yanıtları — LLM ile dinamik üretim.

    Guidance modunda empatik rehberlik promptu kullanılır.
    Diğer modlarda (casual, wizard, None vb.) standart sohbet promptu kullanılır.
    """
    mode = state.get("mode")

    if mode == "guidance":
        prompt = GUIDANCE_CHAT_PROMPT
        if not prompt:
            raise ValueError("[GUIDANCE] GUIDANCE_CHAT_PROMPT yüklenemedi.")
        mode_label = "GUIDANCE"
    else:
        prompt = CASUAL_CHAT_PROMPT
        mode_label = "CASUAL"

    messages = state.get("messages", [])

    # Session hafızası: checkpointer aynı thread'in tüm geçmişini state'e
    # koyuyor — sadece son mesajı değil, son 10 turu LLM'e ver ki
    # aynı sohbet içinde bağlam (isim, önceki sorular vb.) hatırlansın.
    llm_messages: list = [SystemMessage(content=prompt)]
    for m in messages[-10:]:
        if isinstance(m, ToolMessage):
            continue
        content = get_msg_content(m)
        if not content or not str(content).strip():
            continue
        if isinstance(m, AIMessage):
            llm_messages.append(AIMessage(content=content))
        else:
            llm_messages.append(HumanMessage(content=content))
    if len(llm_messages) == 1:
        llm_messages.append(HumanMessage(content="Selam"))

    response = await llm_responder.ainvoke(llm_messages)
    answer = get_msg_content(response)

    print(f"[{mode_label}] 🤖 LLM yanıtı üretildi: '{answer[:60]}...'")

    return {
        "messages": [AIMessage(content=answer)],
        "iteration_count": 0
    }
