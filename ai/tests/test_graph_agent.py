import os

os.environ["GRAPH_AGENT_TEST_MODE"] = "1"


def test_graph_agent_entrypoint_exports_app_graph():
    import ai.graph_agent as ga

    assert hasattr(ga, "app_graph")
    assert hasattr(ga, "workflow")
    assert hasattr(ga, "llm_with_tools")
    assert hasattr(ga, "AgentState")
