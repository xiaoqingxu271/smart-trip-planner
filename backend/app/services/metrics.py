"""进程内指标观测（批次 E）：线程安全的轻量计数器，供 /api/health 与 /metrics 暴露。

只用标准库（threading + deque），不引入 Prometheus 客户端依赖。多进程部署时
各 worker 独立计数（对本应用足够了）；如需跨进程聚合，再换真正的监控采集器。
"""
from __future__ import annotations

import threading
from collections import deque

_lock = threading.Lock()
_llm_latencies: deque[float] = deque(maxlen=1000)  # 最近 1000 次 LLM 步骤耗时（秒）
_llm_calls: int = 0
_plan_success: int = 0
_plan_failure: int = 0


def record_llm_call(latency_seconds: float) -> None:
    global _llm_calls
    with _lock:
        _llm_calls += 1
        _llm_latencies.append(max(0.0, latency_seconds))


def record_plan(success: bool) -> None:
    global _plan_success, _plan_failure
    with _lock:
        if success:
            _plan_success += 1
        else:
            _plan_failure += 1


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = int(round((pct / 100.0) * (len(ordered) - 1)))
    return ordered[idx]


def snapshot() -> dict:
    """返回 health 用的结构化指标（含 LLM 时延与规划失败率）。"""
    with _lock:
        lat = list(_llm_latencies)
        calls = _llm_calls
        success = _plan_success
        failure = _plan_failure
    total = success + failure
    return {
        "llm_calls": calls,
        "llm_latency_avg_ms": round(sum(lat) / len(lat) * 1000, 1) if lat else 0.0,
        "llm_latency_p95_ms": round(_percentile(lat, 95.0) * 1000, 1),
        "plan_success": success,
        "plan_failure": failure,
        "plan_failure_rate": round(failure / total, 4) if total else 0.0,
    }


def prometheus_text() -> str:
    """Prometheus 风格文本（可选 /metrics 端点）。"""
    s = snapshot()
    aid = "smart_trip_planner"
    lines = [
        f"# HELP {aid}_llm_calls_total 累计 LLM 步骤调用次数",
        f"# TYPE {aid}_llm_calls_total counter",
        f"{aid}_llm_calls_total {s['llm_calls']}",
        f"# HELP {aid}_llm_latency_p95_ms LLM 步骤耗时 P95（毫秒）",
        f"# TYPE {aid}_llm_latency_p95_ms gauge",
        f"{aid}_llm_latency_p95_ms {s['llm_latency_p95_ms']}",
        f"# HELP {aid}_plan_success_total 规划成功次数",
        f"# TYPE {aid}_plan_success_total counter",
        f"{aid}_plan_success_total {s['plan_success']}",
        f"# HELP {aid}_plan_failure_total 规划失败次数",
        f"# TYPE {aid}_plan_failure_total counter",
        f"{aid}_plan_failure_total {s['plan_failure']}",
        f"# HELP {aid}_plan_failure_rate 规划失败率（0~1）",
        f"# TYPE {aid}_plan_failure_rate gauge",
        f"{aid}_plan_failure_rate {s['plan_failure_rate']}",
    ]
    return "\n".join(lines) + "\n"