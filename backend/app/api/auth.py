"""注册 / 登录 / 登出 / 用户信息（AUTH_MODE=user 时由 UserAuthMiddleware 保护）。

注册与登录是公开路径（登录前无法有 Token），暴力破解由限流 auth 桶（10/分/IP）抑制。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..config import get_settings
from ..services import user_service
from ..storage.cache import Cache

settings = get_settings()
router = APIRouter(prefix="/api/auth", tags=["auth"])


class Credentials(BaseModel):
    username: str = Field(..., min_length=1, max_length=32, description="用户名")
    password: str = Field(..., min_length=1, max_length=64, description="密码")


def _cache() -> Cache:
    return Cache(settings)


def _token_from(request: Request) -> str:
    auth = request.headers.get("authorization") or ""
    return auth[7:] if auth.lower().startswith("bearer ") else ""


def _fail_count(cache: Cache, username: str) -> int:
    """某账号当前的连续登录失败计数（Redis 不可用时返回 0，fail-open 由 IP 限流兜底）。"""
    raw = cache.get(f"auth:fail:{username}")
    try:
        return int(raw or 0)
    except ValueError:
        return 0


@router.post("/register")
async def register(body: Credentials):
    try:
        user_id = user_service.create_user(settings, body.username, body.password)
    except user_service.AuthError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)
    token = user_service.issue_token(_cache(), user_id)
    return {"token": token, "user_id": user_id, "username": body.username}


@router.post("/login")
async def login(body: Credentials):
    cache = _cache()
    fail_key = f"auth:fail:{body.username}"
    # 账号级锁定：连续失败达上限即拒绝（与 IP 限流互补，防分布式字典爆破）
    if _fail_count(cache, body.username) >= settings.login_max_attempts:
        minutes = max(1, settings.login_lockout_seconds // 60)
        raise HTTPException(status_code=429, detail=f"登录失败次数过多，请 {minutes} 分钟后再试。")
    user_id = user_service.verify_login(settings, body.username, body.password)
    if user_id is None:
        cache.incr_window(fail_key, settings.login_lockout_seconds)
        raise HTTPException(status_code=401, detail="用户名或密码不正确")
    cache.delete(fail_key)
    token = user_service.issue_token(cache, user_id)
    return {"token": token, "user_id": user_id, "username": body.username}


@router.post("/logout")
async def logout(request: Request):
    token = _token_from(request)
    if token:
        user_service.revoke_token(_cache(), token)
    return {"ok": True}


@router.get("/me")
async def me(request: Request):
    # auth 路由自含 token 解析：不依赖中间件，任何模式下持有效 token 均可用
    token = _token_from(request)
    user_id = user_service.resolve_token(_cache(), token) if token else None
    if not user_id:
        raise HTTPException(status_code=401, detail="请先登录")
    username = user_service.get_username(settings, int(user_id))
    if username is None:
        raise HTTPException(status_code=401, detail="用户不存在或已失效")
    return {"user_id": user_id, "username": username}


@router.delete("/account")
async def delete_account(request: Request):
    """注销（批次 E）：删除本人全部行程与账号数据，并撤销当前会话。

    数据删除不可恢复；历史分享链接随行程删除一并失效。
    """
    token = _token_from(request)
    user_id = user_service.resolve_token(_cache(), token) if token else None
    if not user_id:
        raise HTTPException(status_code=401, detail="请先登录")
    try:
        user_service.delete_user(settings, int(user_id))
    except user_service.AuthError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)
    user_service.revoke_token(_cache(), token)
    return {"ok": True}
