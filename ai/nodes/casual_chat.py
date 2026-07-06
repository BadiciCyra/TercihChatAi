from langchain_core.messages import AIMessage, SystemMessage, HumanMessage

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
    user_text = get_msg_content(messages[-1]) if messages else "Selam"

    response = await llm_responder.ainvoke([
        SystemMessage(content=prompt),
        HumanMessage(content=user_text),
    ])
    answer = get_msg_content(response)

    print(f"[{mode_label}] 🤖 LLM yanıtı üretildi: '{answer[:60]}...'")

    return {
        "messages": [AIMessage(content=answer)],
        "iteration_count": 0
    }
