"""进程级高德调用节拍器。

Agent 经 MCP 的搜索、校验器的 POI/距离查询、图片服务的直连 REST 共享同一个
高德 Key 的 QPS 配额，必须全局统一限速：调用前先 pace()，保证相邻调用的起始
间隔不小于 interval。相比"每次调用后固定 sleep"的写法，最后一次调用后不再
空等（N 次调用省 N 次尾部等待），也不会因两个模块各自计时而挤爆配额。
"""
from __future__ import annotations

import threading
import time

_INTERVAL = 0.35  # 个人 Key 搜索类约 3 QPS
_lock = threading.Lock()
_last = 0.0  # monotonic 时间戳


def pace(interval: float = _INTERVAL) -> None:
    """阻塞至距上一次调用满 interval 秒；睡眠在锁内完成，调用方因此全局串行排队。"""
    global _last
    with _lock:
        wait = _last + interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last = time.monotonic()
