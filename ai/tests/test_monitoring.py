from monitoring import MetricsRegistry


def test_metrics_registry_records_requests_and_tokens():
    registry = MetricsRegistry()

    registry.inc("requests_total:ask_intelligent:200")
    registry.observe("request_latency_seconds:ask_intelligent", 0.25)
    registry.inc("cache_hit_total:answer")
    registry.inc("cache_miss_total:answer")
    registry.inc("llm_total_tokens_total:Demo School", 42)

    snapshot = registry.snapshot()

    assert snapshot["counters"]["requests_total:ask_intelligent:200"] == 1
    assert snapshot["counters"]["cache_hit_total:answer"] == 1
    assert snapshot["counters"]["cache_miss_total:answer"] == 1
    assert snapshot["counters"]["llm_total_tokens_total:Demo School"] == 42
    assert snapshot["latencies"]["request_latency_seconds:ask_intelligent"] == [0.25]

    text = registry.render_text()
    assert 'ai_metrics_total{name="requests_total:ask_intelligent:200"} 1' in text
    assert 'ai_metrics_latency_seconds_count{name="request_latency_seconds:ask_intelligent"} 1' in text
