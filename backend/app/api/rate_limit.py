"""按 IP 的固定窗口限流中间件。

策略（默认值见 config.Settings，均可经 .env 调整，格式 "次数/窗口秒"）：
- heavy: /api/trip/plan 与 /api/trip/replan 共用 5/300 —— 每次消耗
  4-5 次 LLM 调用 + 几十次高德调用（真金白银）；
- geo:   /api/utils/geocode 30/60；
- img:   /api/utils/image 120/60（带宽 + CDN 配额）；
- default: 其余 /api/* 兜底 120/60；health 与浏览器 OPTIONS 预检不限。

工程要点：
- 阻塞的 Redis 检查放 _io_executor 执行：热路径正常 <1ms，但 Redis 挂起时
  （socket_timeout 1.5s）不能阻塞事件循环；
- Redis 不可用时 fail-open 放行（incr_window 返回 None），与 Cache.acquire_lock
  的降级哲学一致；
- 固定窗口在窗口边界最坏有 2 倍突刺，此量级可接受；需要平滑时再换 ZSET 滑动窗口；
- IP 取 request.client.host：X-Forwarded-For 可被客户端伪造，盲信等于限流被
  一个 header 绕过；仅当部署于可信反向代理之后才应改用（届时改 _client_ip）。
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from ..config import Settings
from ..storage.cache import Cache

logger = logging.getLogger(__name__)

# (方法, 精确路径) -> 桶名。plan 与 replan 共用 heavy 桶：
# replan 本质是另一条触发完整流水线的路，分开限会被绕。
_EXACT_RULES: dict[tuple[str, str], str] = {
    ("POST", "/api/trip/plan"): "heavy",
    ("POST", "/api/trip/plan/stream"): "heavy",
    ("POST", "/api/trip/replan"): "heavy",
    ("POST", "/api/utils/geocode"): "geo",
    ("GET", "/api/utils/image"): "img",
}


@dataclass
class Limit:
    count: int
    window: int


def parse_limit(raw: str, fallback: Limit | None = None) -> Limit:
    """解析 "次数/窗口秒"（如 "5/300"）；非法配置退回保守默认并告警。"""
    fallback = fallback or Limit(120, 60)
    try:
        count_s, window_s = raw.split("/", 1)
        count, window = int(count_s), int(window_s)
        if count < 1 or window < 1:
            raise ValueError(raw)
        return Limit(count, window)
    except ValueError:
        logger.warning("RATE_LIMIT 配置非法: %r，退回默认 %d/%d", raw, fallback.count, fallback.window)
        return fallback


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, settings: Settings, executor):
        super().__init__(app)
        self.settings = settings
        self.executor = executor
        self.cache = Cache(settings)
        self.limits: dict[str, Limit] = {
            "heavy": parse_limit(settings.rate_limit_heavy),
            "geo": parse_limit(settings.rate_limit_geo),
            "img": parse_limit(settings.rate_limit_img),
            "default": parse_limit(settings.rate_limit_default),
        }

    def _bucket_for(self, request: Request) -> str | None:
        if request.method == "OPTIONS" or request.url.path == "/api/health":
            return None
        bucket = _EXACT_RULES.get((request.method, request.url.path))
        if bucket:
            return bucket
        return "default" if request.url.path.startswith("/api/") else None

    async def dispatch(self, request: Request, call_next):
        if not self.settings.rate_limit_enabled:
            return await call_next(request)
        bucket = self._bucket_for(request)
        if bucket is None:
            return await call_next(request)

        limit = self.limits[bucket]
        ip = request.client.host if request.client else "unknown"
        window_no = int(time.time()) // limit.window
        key = f"rl:{bucket}:{ip}:{window_no}"

        result = await asyncio.get_running_loop().run_in_executor(
            self.executor, self.cache.incr_window, key, limit.window
        )
        if result is None:  # Redis 不可用：放行
            return await call_next(request)

        count, ttl = result
        headers = {
            "X-RateLimit-Limit": str(limit.count),
            "X-RateLimit-Remaining": str(max(0, limit.count - count)),
        }
        if count > limit.count:
            retry = ttl if ttl > 0 else limit.window
            headers["Retry-After"] = str(retry)
            return JSONResponse(
                status_code=429,
                content={"detail": f"请求太频繁了，请 {retry} 秒后再试。"},
                headers=headers,
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = headers["X-RateLimit-Limit"]
        response.headers["X-RateLimit-Remaining"] = headers["X-RateLimit-Remaining"]
        return response
