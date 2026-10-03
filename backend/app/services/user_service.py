"""用户与会话：注册 / 登录 / Token（多用户体系的服务层）。

- 密码哈希：标准库 PBKDF2-HMAC-SHA256，20 万轮 + 随机 16 字节盐，零第三方依赖；
- 会话 Token：随机 64 hex，存 Redis（auth:token:{token} → user_id，TTL 7 天），
  登出即删；Redis 不可用时 resolve 返回 None → 一律 401（认证必须 fail-closed）。
"""
from __future__ import annotations

import hashlib
import re
import secrets

from ..config import Settings
from ..storage.cache import Cache
from ..storage.db import StorageUnavailable, connection

TOKEN_TTL = 7 * 24 * 3600
_PBKDF2_ROUNDS = 200_000
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,32}$")


class AuthError(Exception):
    """认证相关的前端可见错误（status + 可读消息）。"""

    def __init__(self, status: int, detail: str):
        self.status = status
        self.detail = detail
        super().__init__(detail)


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _PBKDF2_ROUNDS).hex()
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        calc = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(rounds)).hex()
        return secrets.compare_digest(calc, digest)
    except (ValueError, TypeError):
        return False


def validate_credentials(username: str, password: str) -> None:
    if not _USERNAME_RE.fullmatch(username):
        raise AuthError(400, "用户名需 3-32 位，仅限字母/数字/下划线")
    if not (6 <= len(password) <= 64):
        raise AuthError(400, "密码长度需 6-64 位")


def create_user(settings: Settings, username: str, password: str) -> int:
    """创建用户，返回 user_id；用户名重复/格式非法抛 AuthError。"""
    validate_credentials(username, password)
    try:
        with connection(settings) as conn, conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE username = %s", (username,))
            if cur.fetchone():
                raise AuthError(400, "用户名已被占用")
            cur.execute(
                "INSERT INTO users (username, password_hash) VALUES (%s, %s)",
                (username, hash_password(password)),
            )
            return int(cur.lastrowid)
    except StorageUnavailable:
        raise AuthError(503, "用户存储（MySQL）不可用") from None


def verify_login(settings: Settings, username: str, password: str) -> int | None:
    """校验登录，成功返回 user_id，失败返回 None。"""
    try:
        with connection(settings) as conn, conn.cursor() as cur:
            cur.execute("SELECT id, password_hash FROM users WHERE username = %s", (username,))
            row = cur.fetchone()
    except StorageUnavailable:
        raise AuthError(503, "用户存储（MySQL）不可用") from None
    if not row or not verify_password(password, row["password_hash"]):
        return None
    return int(row["id"])


def get_username(settings: Settings, user_id: int) -> str | None:
    try:
        with connection(settings) as conn, conn.cursor() as cur:
            cur.execute("SELECT username FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()
    except StorageUnavailable:
        return None
    return row["username"] if row else None


# ---------- 会话 Token（Redis） ----------

def issue_token(cache: Cache, user_id: int) -> str:
    token = secrets.token_hex(32)
    cache.set(f"auth:token:{token}", str(user_id), TOKEN_TTL)
    return token


def resolve_token(cache: Cache, token: str) -> int | None:
    v = cache.get(f"auth:token:{token}")
    return int(v) if v else None


def revoke_token(cache: Cache, token: str) -> None:
    cache.delete(f"auth:token:{token}")
