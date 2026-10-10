"""进程级高德调用节拍器（令牌桶，支持并发调用方）。

Agent 经 MCP 的搜索、校验器的 POI/距离查询、图片服务的直连 REST、路线计算共享
同一个高德 Key 的 QPS 配额，必须全局统一限速：每次调用前先 pace()。平均速率
不突破 QPS，但拿不到令牌的调用方在锁外短睡，多个线程可并发等待、互不阻塞，
配合图片/路线/天气的线程并发优化在限速内并行消耗配额。

interval 缺省时按 config.Settings.amap_qps 派生（interval = 1/qps），对应
个人认证 3 QPS、企业认证 30 QPS，消除「多个模块各维护一个 0.35/0.4 常量」的隐患。
此外维护进程内调用计数，供 /api/health 观测当日高德用量是否逼近配额。
"""
from __future__ import annotations

import threading
import time

from ..config import get_settings

_lock = threading.Lock()
_calls = 0          # 进程内累计高德调用次数（当天用量观测）
_tokens = 0.0       # 桶内可用令牌（个）
_last = 0.0         # monotonic 时间戳；0 表示尚未开始补充令牌的哨兵
_BURST = 3.0        # 桶容量（个），允许 LLM 长间隙期间累积的短时突发


def _current_qps() -> float:
    try:
        return float(get_settings().amap_qps)
    except Exception:  # noqa: BLE001
        return 3.0


def pace(interval: float | None = None) -> None:
    """消耗一个令牌；不足则在锁外短睡重试，直到桶里补出令牌。

    与旧版「锁内 sleep 串行排队」不同，本版是令牌桶：拿不到令牌的线程在锁外等待，
    多个并发调用方（图片/路线/天气的线程）互不阻塞，可在限速内并行消耗配额；
    平均速率仍不超过 QPS。首次调用立即放行，避免启动时无谓等待。
    """
    global _calls, _tokens, _last
    if interval is None:
        qps = _current_qps()
        interval = 1.0 / qps if qps > 0 else 0.35
    while True:
        with _lock:
            now = time.monotonic()
            if _last == 0.0:
                _last = now
                _tokens = 1.0
            _tokens += (now - _last) / interval
            _last = now
            if _tokens > _BURST:
                _tokens = _BURST
            if _tokens >= 1.0:
                _tokens -= 1.0
                _calls += 1
                return
            need = (1.0 - _tokens) * interval
        time.sleep(min(need, 0.02))


def call_count() -> int:
    """进程内累计高德调用次数（含 MCP 搜索/校验/图片/路线）。"""
    return _calls