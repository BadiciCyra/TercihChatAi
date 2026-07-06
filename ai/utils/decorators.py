import asyncio as _asyncio_for_decorators
import traceback as _tb_for_decorators
from functools import wraps
from langchain_core.messages import AIMessage
from pydantic import ValidationError

from state import AgentState, AgentStateValidator, ToolCallValidation, NERContext

def _validate_input_state(func_name: str, state: AgentState) -> None:
    """Ortak input validation mantığı (async/sync ortak)."""
    try:
        AgentStateValidator(**state)
        print(f"[VALIDATOR] ✅ {func_name} input validation başarılı")
    except ValidationError as e:
        print(f"[VALIDATOR] ❌ {func_name} input validation hatası: {e}")
        if "iteration_count" not in state:
            state["iteration_count"] = 0
        if "ner_context" not in state:
            state["ner_context"] = {}
        if "messages" not in state:
            state["messages"] = []


def _validate_output_result(func_name: str, result: dict) -> dict:
    """Ortak output validation mantığı."""
    try:
        if isinstance(result, dict) and result.get("messages"):
            last_msg = result["messages"][-1]
            if hasattr(last_msg, 'tool_calls') and last_msg.tool_calls:
                for tc in last_msg.tool_calls:
                    try:
                        ToolCallValidation(**tc)
                        print(f"[VALIDATOR] ✅ Tool call validation: {tc.get('name')}")
                    except ValidationError as e:
                        print(f"[VALIDATOR] ⚠️ Tool call validation: {e}")
        if isinstance(result, dict) and result.get("ner_context"):
            try:
                validated_ner = NERContext(**result["ner_context"])
                result["ner_context"] = validated_ner.model_dump()
                print(f"[VALIDATOR] ✅ NER context validation başarılı")
            except ValidationError as e:
                print(f"[VALIDATOR] ⚠️ NER validation: {e}")
        print(f"[VALIDATOR] ✅ {func_name} output validation başarılı")
    except Exception as e:
        print(f"[VALIDATOR] ⚠️ {func_name} output validation: {e}")
    return result


def validate_node_input(func):
    """Node girişini doğrulayan decorator — async/sync dual."""
    if _asyncio_for_decorators.iscoroutinefunction(func):
        @wraps(func)
        async def async_wrapper(state: AgentState) -> dict:
            _validate_input_state(func.__name__, state)
            return await func(state)
        return async_wrapper

    @wraps(func)
    def sync_wrapper(state: AgentState) -> dict:
        _validate_input_state(func.__name__, state)
        return func(state)
    return sync_wrapper


def validate_node_output(func):
    """Node çıkışını doğrulayan decorator — async/sync dual."""
    if _asyncio_for_decorators.iscoroutinefunction(func):
        @wraps(func)
        async def async_wrapper(state: AgentState) -> dict:
            result = await func(state)
            return _validate_output_result(func.__name__, result)
        return async_wrapper

    @wraps(func)
    def sync_wrapper(state: AgentState) -> dict:
        result = func(state)
        return _validate_output_result(func.__name__, result)
    return sync_wrapper


def with_error_recovery(func):
    """Hata durumunda recovery sağlayan decorator — async/sync dual."""
    if _asyncio_for_decorators.iscoroutinefunction(func):
        @wraps(func)
        async def async_wrapper(state: AgentState) -> dict:
            try:
                return await func(state)
            except Exception as e:
                print(f"[RECOVERY] 💥 {func.__name__} hatası: {e}")
                _tb_for_decorators.print_exc()
                return {
                    "messages": [AIMessage(content="Bir işlem hatası oluştu. Lütfen sorunuzu tekrar sorar mısınız?")],
                    "iteration_count": state.get("iteration_count", 0) + 1
                }
        return async_wrapper

    @wraps(func)
    def sync_wrapper(state: AgentState) -> dict:
        try:
            return func(state)
        except Exception as e:
            print(f"[RECOVERY] 💥 {func.__name__} hatası: {e}")
            _tb_for_decorators.print_exc()
            return {
                "messages": [AIMessage(content="Bir işlem hatası oluştu. Lütfen sorunuzu tekrar sorar mısınız?")],
                "iteration_count": state.get("iteration_count", 0) + 1
            }
    return sync_wrapper
