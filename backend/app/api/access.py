"""访问码鉴权（单管理员密码模式）。

APP_PASSWORD 未配置时完全关闭（本地演示零影响）；配置后所有 /api/*
（除 /api/health 供前端探测）必须携带访问码：
- 请求头 X-Access-Code（axios 统一注入）；
- 或查询参数 ?code=（<img> 标签无法携带自定义头，图片代理 URL 走查询参数）。

对比用 secrets.compare_digest 防时序侧信道。
注册在限流之内层：每个 401 请求同样消耗限流配额（default 桶 120/分），
天然抑制对访问码的暴力枚举。
"""
from __future__ import annotations

import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


class AccessCodeMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, password: str):
        super().__init__(app)
        self._password = password

    async def dispatch(self, request: Request, call_next):
        if not self._password:
            return await call_next(request)
        path = request.url.path
        if path == "/api/health" or not path.startswith("/api/"):
            return await call_next(request)
        given = request.headers.get("x-access-code") or request.query_params.get("code") or ""
        if secrets.compare_digest(given.encode(), self._password.encode()):
            return await call_next(request)
        return JSONResponse(
            status_code=401,
            content={"detail": "需要访问密码（X-Access-Code 请求头或 ?code= 查询参数）"},
        )
