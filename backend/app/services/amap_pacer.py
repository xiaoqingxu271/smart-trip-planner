"""进程级高德调用节拍器。

Agent 经 MCP 的搜索、校验器的 POI/距离查询、图片服务的直连 REST 共享同一个
高德 Key 的 QPS 配额，必须全局统一限速：调用前先 pace()，保证相邻调用的起始
间隔不小于 interval。相比"每次调用后固定 sleep"的写法，最后一次调用后不再
空等（N 次调用省 N 次尾部等待），也不会因两个模块各自计时而挤爆配额。

interval 缺省时按 config.Settings.amap_qps 派生（interval = 1/qps），对应
个人认证 3 QPS、企业认证 30 QPS，消除「多个模块各维护一个 0.35/0.4 常量」的隐患。
此外维护进程内调用计数，供 /api/health 观测当日高德用量是否逼近配额。
"""
from __future__ import annotations

import threading
import time

from ..config import get_settings

_lock = threading.Lock()
_last = 0.0  # monotonic 时间戳
_calls = 0   # 进程内累计高德调用次数（当天用量观测）


def _current_qps() -> float:
    try:
        return float(get_settings().amap_qps)
    except Exception:  # noqa: BLE001
        return 3.0


def pace(interval: float | None = None) -> None:
    """阻塞至距上一次调用满 interval 秒；睡眠在锁内完成，调用方因此全局串行排队。"""
    global _last, _calls
    if interval is None:
        qps = _current_qps()
        interval = 1.0 / qps if qps > 0 else 0.35
    with _lock:
        wait = _last + interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last = time.monotonic()
        _calls += 1


def call_count() -> int:
    """进程内累计高德调用次数（含 MCP 搜索/校验/图片/路线）。"""
    return _calls