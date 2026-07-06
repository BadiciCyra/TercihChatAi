from langgraph.prebuilt import ToolNode
from langchain_core.messages import ToolMessage, AIMessage
from langchain_core.tools import tool

from state import AgentState
from utils.validators import validate_tool_response
from utils.link_fetcher import fetch_url_content
from tools import fetch_link_content


class ValidatedToolNode(ToolNode):
    """
    Tool çıktılarını validate eden özel ToolNode.
    Tool hatası durumunda agent'e bilgi verir.
    """
    
    async def ainvoke(self, state: AgentState, config=None):
        """Async tool çağrısı - validation ile."""
        try:
            result = await super().ainvoke(state, config)
            
            # Tool çıktısını validate et
            if "messages" in result and result["messages"]:
                for msg in result["messages"]:
                    if isinstance(msg, ToolMessage):
                        is_valid, validation_msg = validate_tool_response(msg.content)
                        if not is_valid:
                            print(f"[TOOL-VALIDATOR] ⚠️ {validation_msg}")
                        else:
                            print(f"[TOOL-VALIDATOR] ✅ Tool çıktısı geçerli")
            
            return result
        except Exception as e:
            print(f"[TOOL-VALIDATOR] 💥 Tool hatası: {e}")
            return {
                "messages": [ToolMessage(
                    content=f"Tool hatası: {str(e)}. Lütfen farklı parametreler deneyin.",
                    tool_call_id="error_recovery"
                )]
            }
