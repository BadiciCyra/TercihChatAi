import sys
import types
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
AI_DIR = ROOT_DIR / "ai"
for path in (str(ROOT_DIR), str(AI_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

# --- Minimal mock for langchain_google_genai ---
lcg = types.ModuleType("langchain_google_genai")
class ChatGoogleGenerativeAI:
    def __init__(self, *args, **kwargs):
        pass
    def with_structured_output(self, model):
        # Return a dummy structured LLM that simply echoes/creates a RouterOutput-like dict
        class DummyStructured:
            def invoke(self, payload):
                inp = payload.get('input', '')
                # simple heuristic: if 'atlas' in input, normalize uni
                if 'atlas' in inp.lower():
                    return {"action": "RAG_SEARCH", "search_type": "TABLO", "uni": "İstanbul Atlas Üniversitesi", "program": None, "rank": None, "intent": "siralama_sorgusu", "reason": "mocked ner"}
                return {"action": "DIRECT_CHAT"}
        return DummyStructured()

lcg.ChatGoogleGenerativeAI = ChatGoogleGenerativeAI
sys.modules['langchain_google_genai'] = lcg

def _install_optional_llm_stub(module_name: str, class_name: str):
    mod = types.ModuleType(module_name)
    class _DummyChat:
        def __init__(self, *args, **kwargs):
            pass
        def invoke(self, *args, **kwargs):
            raise RuntimeError(f"{module_name} stub invoke")
        async def ainvoke(self, *args, **kwargs):
            raise RuntimeError(f"{module_name} stub ainvoke")
        def with_structured_output(self, *args, **kwargs):
            return self
        def bind_tools(self, *args, **kwargs):
            return self
    setattr(mod, class_name, _DummyChat)
    sys.modules[module_name] = mod

_install_optional_llm_stub("langchain_openai", "ChatOpenAI")
_install_optional_llm_stub("langchain_anthropic", "ChatAnthropic")
_install_optional_llm_stub("langchain_groq", "ChatGroq")
_install_optional_llm_stub("langchain_mistralai", "ChatMistralAI")

# --- Minimal mocks for langchain_core.messages and prompts ---
lc = types.ModuleType("langchain_core")
msgs = types.ModuleType("langchain_core.messages")
class BaseMessage: pass
class SystemMessage:
    def __init__(self, content): self.content = content
class AIMessage:
    def __init__(self, content, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []
class HumanMessage:
    def __init__(self, content): self.content = content
class ToolMessage:
    def __init__(self, content): self.content = content

msgs.BaseMessage = BaseMessage
msgs.SystemMessage = SystemMessage
msgs.AIMessage = AIMessage
msgs.HumanMessage = HumanMessage
msgs.ToolMessage = ToolMessage

prompts = types.ModuleType("langchain_core.prompts")
class ChatPromptTemplate:
    @staticmethod
    def from_messages(msgs):
        class DummyPrompt:
            def __or__(self, structured):
                class Chain:
                    def __init__(self, structured): self.structured = structured
                    def invoke(self, payload): return self.structured.invoke(payload)
                return Chain(structured)
        return DummyPrompt()

prompts.ChatPromptTemplate = ChatPromptTemplate

sys.modules['langchain_core'] = lc
sys.modules['langchain_core.messages'] = msgs
sys.modules['langchain_core.prompts'] = prompts
# Minimal langchain_core.tools mock
import asyncio as _asyncio
tools_mod = types.ModuleType('langchain_core.tools')
class BaseTool:
    pass
tools_mod.BaseTool = BaseTool
def tool(func=None, *args, **kwargs):
    """Minimal stub for @tool decorator.

    Attaches .ainvoke and .invoke to the decorated async function so that
    tests can call fetch_link_content.ainvoke({"url": ...}) without the
    real LangChain StructuredTool wrapper.
    """
    def _wrap(fn):
        async def _ainvoke(input_dict):
            return await fn(**input_dict)
        def _invoke(input_dict):
            return _asyncio.get_event_loop().run_until_complete(fn(**input_dict))
        fn.ainvoke = _ainvoke
        fn.invoke = _invoke
        return fn
    if func is not None:
        return _wrap(func)
    return _wrap
tools_mod.tool = tool
sys.modules['langchain_core.tools'] = tools_mod

# Minimal langchain_core.callbacks mock
callbacks_mod = types.ModuleType('langchain_core.callbacks')
class BaseCallbackHandler:
    def on_llm_start(self, *a, **kw): pass
    def on_llm_end(self, *a, **kw): pass
    def on_tool_start(self, *a, **kw): pass
    def on_chain_error(self, *a, **kw): pass
callbacks_mod.BaseCallbackHandler = BaseCallbackHandler
sys.modules['langchain_core.callbacks'] = callbacks_mod

# langchain_core.outputs mock
outputs_mod = types.ModuleType('langchain_core.outputs')
class LLMResult:
    def __init__(self, generations=None, llm_output=None):
        self.generations = generations or []
        self.llm_output = llm_output or {}
outputs_mod.LLMResult = LLMResult
sys.modules['langchain_core.outputs'] = outputs_mod

# --- Minimal mocks for langgraph pieces used at import time ---
lg = types.ModuleType('langgraph')
class StateGraph:
    def __init__(self, *args, **kwargs): pass
    def add_node(self, *args, **kwargs): pass
    def set_entry_point(self, *args, **kwargs): pass
    def add_edge(self, *args, **kwargs): pass
    def add_conditional_edges(self, *args, **kwargs): pass
    def compile(self, *args, **kwargs): return None

prebuilt = types.ModuleType('langgraph.prebuilt')
class ToolNode:
    def __init__(self, tools): pass
prebuilt.ToolNode = ToolNode

checkpoint = types.ModuleType('langgraph.checkpoint')
checkpoint.memory = types.ModuleType('langgraph.checkpoint.memory')
class MemorySaver:
    def __init__(self): pass
checkpoint.memory.MemorySaver = MemorySaver

lg.StateGraph = StateGraph
lg.prebuilt = prebuilt
lg.checkpoint = checkpoint

sys.modules['langgraph'] = lg
sys.modules['langgraph.prebuilt'] = prebuilt
sys.modules['langgraph.checkpoint'] = checkpoint
sys.modules['langgraph.checkpoint.memory'] = checkpoint.memory

# Provide langgraph.graph submodule with StateGraph and END constant
lg_graph = types.ModuleType('langgraph.graph')
lg_graph.StateGraph = StateGraph
END = object()
lg_graph.END = END
sys.modules['langgraph.graph'] = lg_graph

# Map top-level 'schemas' to the package module 'ai.schemas' so imports in graph_agent work
try:
    import importlib
    schemas_mod = importlib.import_module('ai.schemas')
    sys.modules['schemas'] = schemas_mod
except Exception:
    pass

# Map top-level 'tools' to 'ai.tools' so imports in graph_agent work
try:
    import importlib
    tools_mod = importlib.import_module('ai.tools')
    sys.modules['tools'] = tools_mod
except Exception:
    pass

# Minimal 'retrieve' mock to satisfy imports in ai.tools during tests
retrieve_mod = types.ModuleType('retrieve')
async def _search_yok_atlas(entities):
    return {}
async def _search_ddg(query, is_review_search=False):
    return []
async def _fetch_content(req):
    return {'data': []}
class FetchContentRequest:
    def __init__(self, urls=None, synthesize=False, synthesize_model=None):
        self.urls = urls or []
        self.synthesize = synthesize
        self.synthesize_model = synthesize_model

retrieve_mod.search_yok_atlas = _search_yok_atlas
retrieve_mod.search_ddg = _search_ddg
retrieve_mod.fetch_content = _fetch_content
retrieve_mod.FetchContentRequest = FetchContentRequest
sys.modules['retrieve'] = retrieve_mod
