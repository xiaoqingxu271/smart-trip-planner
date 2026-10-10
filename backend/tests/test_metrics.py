"""进程内指标观测（批次 E）：计数器与 Prometheus 文本输出。"""
from collections import deque

from app.services import metrics


def test_snapshot_defaults(monkeypatch):
    # 隔离进程内全局状态，避免污染其它用例
    monkeypatch.setattr(metrics, "_llm_calls", 0)
    monkeypatch.setattr(metrics, "_llm_latencies", deque(maxlen=10))
    monkeypatch.setattr(metrics, "_plan_success", 0)
    monkeypatch.setattr(metrics, "_plan_failure", 0)

    s = metrics.snapshot()
    assert s["llm_calls"] == 0
    assert s["llm_latency_p95_ms"] == 0.0
    assert s["plan_failure_rate"] == 0.0


def test_record_plan_failure_rate(monkeypatch):
    monkeypatch.setattr(metrics, "_llm_calls", 0)
    monkeypatch.setattr(metrics, "_llm_latencies", deque(maxlen=10))
    monkeypatch.setattr(metrics, "_plan_success", 3)
    monkeypatch.setattr(metrics, "_plan_failure", 1)

    s = metrics.snapshot()
    assert s["plan_success"] == 3
    assert s["plan_failure"] == 1
    assert abs(s["plan_failure_rate"] - 0.25) < 1e-9


def test_llm_latency_p95(monkeypatch):
    monkeypatch.setattr(metrics, "_llm_calls", 0)
    lat = deque(maxlen=100)
    lat.extend([0.1, 0.2, 0.3, 0.4, 0.5])
    monkeypatch.setattr(metrics, "_llm_latencies", lat)
    monkeypatch.setattr(metrics, "_plan_success", 0)
    monkeypatch.setattr(metrics, "_plan_failure", 0)

    s = metrics.snapshot()
    assert s["llm_latency_p95_ms"] == 500.0


def test_prometheus_text_contains_keys(monkeypatch):
    monkeypatch.setattr(metrics, "_llm_calls", 1)
    monkeypatch.setattr(metrics, "_llm_latencies", deque(maxlen=10))
    monkeypatch.setattr(metrics, "_plan_success", 1)
    monkeypatch.setattr(metrics, "_plan_failure", 0)

    text = metrics.prometheus_text()
    assert "smart_trip_planner_plan_success_total 1" in text
    assert "smart_trip_planner_plan_failure_rate 0.0" in text


def test_record_llm_usage_and_cache_hit(monkeypatch):
    # 隔离新计数器的进程内全局状态（批次 G）
    monkeypatch.setattr(metrics, "_llm_prompt_tokens", 0)
    monkeypatch.setattr(metrics, "_llm_completion_tokens", 0)
    monkeypatch.setattr(metrics, "_cache_hits", 0)

    metrics.record_llm_usage(120, 30)
    metrics.record_llm_usage(80, 20)
    metrics.record_cache_hit()

    s = metrics.snapshot()
    assert s["llm_prompt_tokens"] == 200
    assert s["llm_completion_tokens"] == 50
    assert s["llm_total_tokens"] == 250
    assert s["cache_hits"] == 1
    # Prometheus 输出应包含新指标
    assert "smart_trip_planner_llm_prompt_tokens_total 200" in metrics.prometheus_text()
    assert "smart_trip_planner_cache_hits_total 1" in metrics.prometheus_text()


def test_record_llm_usage_clamps_negative(monkeypatch):
    monkeypatch.setattr(metrics, "_llm_prompt_tokens", 0)
    monkeypatch.setattr(metrics, "_llm_completion_tokens", 0)

    metrics.record_llm_usage(-5, -3)
    s = metrics.snapshot()
    assert s["llm_prompt_tokens"] == 0
    assert s["llm_completion_tokens"] == 0