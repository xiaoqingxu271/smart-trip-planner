"""Redis 封装：缓存 / 收藏排行 / 规划锁。

所有操作静默降级——Redis 不可用时返回 None/False 并记住状态，
上层功能照常（详情直查 MySQL、图片缓存退回进程内存）。
"""
from __future__ import annotations

import secrets
import threading
import time

import redis as redis_py

from ..config import Settings

_LOCK = threading.Lock()
_CLIENT: redis_py.Redis | None = None
AVAILABLE: bool | None = None  # None=未探测
# 熔断静默期（秒级时间戳）：探测失败后 10s 内直接快速失败，不再碰 socket。
# 否则 Redis 宕机时每个请求都要等 3s 探测超时，4 个 IO worker 会被
# 限流中间件的每请求检查全部占满，把一次缓存故障放大成全站卡死。
_DOWN_UNTIL = 0.0
_DOWN_COOLDOWN = 10.0


def _get_client(settings: Settings) -> redis_py.Redis:
    global _CLIENT, AVAILABLE, _DOWN_UNTIL
    if time.time() < _DOWN_UNTIL:
        raise CacheUnavailable("Redis 静默期，快速失败") from None
    with _LOCK:
        if time.time() < _DOWN_UNTIL:  # 等锁期间可能已被其他线程置位
            raise CacheUnavailable("Redis 静默期，快速失败") from None
        if _CLIENT is None:
            _CLIENT = redis_py.Redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_timeout=1.5,
                socket_connect_timeout=1.5,
                protocol=2,  # 兼容本地旧版 Redis（<6.0 不支持 RESP3 的 HELLO 命令）
            )
        try:
            _CLIENT.ping()
            AVAILABLE = True
            _DOWN_UNTIL = 0.0
        except Exception:  # noqa: BLE001
            AVAILABLE = False
            _DOWN_UNTIL = time.time() + _DOWN_COOLDOWN
            raise CacheUnavailable("Redis 不可用") from None
        return _CLIENT


class CacheUnavailable(Exception):
    pass


def check(settings: Settings) -> bool:
    """主动探测 Redis 可用性（供 /api/health）。"""
    try:
        _get_client(settings)
        return True
    except Exception:  # noqa: BLE001
        return False


class Cache:
    """带降级的 Redis 访问器。"""

    def __init__(self, settings: Settings):
        self.settings = settings

    # ---------- 基础 KV ----------

    def get(self, key: str) -> str | None:
        try:
            return _get_client(self.settings).get(key)
        except Exception:  # noqa: BLE001
            return None

    def set(self, key: str, value: str, ttl: int) -> None:
        try:
            _get_client(self.settings).setex(key, ttl, value)
        except Exception:  # noqa: BLE001
            pass

    def delete(self, *keys: str) -> None:
        try:
            _get_client(self.settings).delete(*keys)
        except Exception:  # noqa: BLE001
            pass

    # ---------- 规划锁（带所有权，防误删他人锁） ----------

    _RELEASE_LOCK_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""

    def acquire_lock(self, key: str, ttl: int) -> tuple[bool, str | None]:
        """返回 (是否放行, owner_token)。

        - (True, token)：加锁成功，结束时必须用 release_lock(key, token) 释放；
        - (False, None)：锁被他人持有，调用方应拒绝；
        - (True, None)：Redis 不可用，宁可持续重试也不误伤，直接放行。
        若无所有权校验，A 的锁超时过期被 B 拿走后，A 的 finally 会把 B 的锁删掉。
        """
        try:
            token = secrets.token_hex(8)
            if _get_client(self.settings).set(key, token, nx=True, ex=ttl):
                return True, token
            return False, None
        except Exception:  # noqa: BLE001
            return True, None

    def release_lock(self, key: str, token: str | None) -> None:
        """仅当 token 与持有者一致时才删锁（Lua 比对原子执行）。"""
        if not token:
            return
        try:
            _get_client(self.settings).eval(self._RELEASE_LOCK_SCRIPT, 1, key, token)
        except Exception:  # noqa: BLE001
            pass

    # ---------- 收藏排行（ZSET） ----------

    def zadd(self, key: str, member: str) -> None:
        try:
            _get_client(self.settings).zadd(key, {member: time.time()})
        except Exception:  # noqa: BLE001
            pass

    def zrem(self, key: str, member: str) -> None:
        try:
            _get_client(self.settings).zrem(key, member)
        except Exception:  # noqa: BLE001
            pass

    def zrevrange(self, key: str, start: int, stop: int) -> list[str]:
        try:
            return _get_client(self.settings).zrevrange(key, start, stop)
        except Exception:  # noqa: BLE001
            return []

    # ---------- 限流（固定窗口计数） ----------

    _RATE_WINDOW_SCRIPT = """
local v = redis.call('INCR', KEYS[1])
if v == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return {v, redis.call('TTL', KEYS[1])}
"""

    def incr_window(self, key: str, window: int) -> tuple[int, int] | None:
        """固定窗口计数（Lua 原子 INCR + 首次 EXPIRE）。

        返回 (窗口内已计数, key 剩余秒数)；Redis 不可用返回 None，调用方放行。
        """
        try:
            count, ttl = _get_client(self.settings).eval(self._RATE_WINDOW_SCRIPT, 1, key, window)
            return int(count), int(ttl)
        except Exception:  # noqa: BLE001
            return None
