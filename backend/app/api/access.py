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

from ..config import Settings
from ..services.user_service import resolve_token
from ..storage.cache import Cache


class AccessCodeMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, password: str):
        super().__init__(app)
        self._password = password

    async def dispatch(self, request: Request, call_next):
        if not self._password:
            return await call_next(request)
        path = request.url.path
        if path == "/api/health" or path.startswith("/api/trip/share/") or not path.startswith("/api/"):
            return await call_next(request)
        given = request.headers.get("x-access-code") or request.query_params.get("code") or ""
        if secrets.compare_digest(given.encode(), self._password.encode()):
            return await call_next(request)
        return JSONResponse(
            status_code=401,
            content={"detail": "需要访问密码（X-Access-Code 请求头或 ?code= 查询参数）"},
        )


class UserAuthMiddleware(BaseHTTPMiddleware):
    """多用户模式（AUTH_MODE=user）：除公开路径外一律要求 Bearer Token。

    Token 支持 Authorization: Bearer 头与 ?token= 查询参数（<img>/下载场景无法带头）。
    解析出的 user_id 挂到 request.state.user_id 供端点做数据隔离；
    认证必须 fail-closed——Redis 不可用时 Token 无法核验，一律 401。
    """

    PUBLIC_PATHS = {
        "/api/health",
        "/api/config",  # 地图初始化公开配置，登录前必须能访问
        "/api/utils/image",  # 图片代理（前端 img 标签，走 ?token= 查询参数）
        "/api/utils/geocode",  # 地理编码（行程规划前地址搜索）
        "/api/utils/poi",  # POI 解析（行程规划前地址补全）
        "/api/auth/register",
        "/api/auth/login",
    }

    def __init__(self, app, settings: Settings, cache: Cache):
        super().__init__(app)
        self._settings = settings
        self._cache = cache

    async def dispatch(self, request: Request, call_next):
        if not self._settings.user_auth_enabled:
            request.state.user_id = None
            return await call_next(request)

        path = request.url.path
        if path in self.PUBLIC_PATHS or path.startswith("/api/trip/share/") or not path.startswith("/api/"):
            request.state.user_id = None
            return await call_next(request)

        auth = request.headers.get("authorization") or ""
        token = auth[7:] if auth.lower().startswith("bearer ") else (request.query_params.get("token") or "")
        user_id = resolve_token(self._cache, token) if token else None
        if user_id is None:
            return JSONResponse(status_code=401, content={"detail": "请先登录"})
        request.state.user_id = user_id
        return await call_next(request)
