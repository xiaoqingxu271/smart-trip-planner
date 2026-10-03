"""存储层：规划锁所有权、Redis 计数、磁盘清理（需要真实 Redis 的用例自动跳过）。"""
import os
import time

import pytest

from app.config import Settings
from app.services.cleanup import cleanup_stale_files
from app.storage import cache as cache_mod
from app.storage.cache import Cache

settings = Settings()
redis_ok = cache_mod.check(settings)
requires_redis = pytest.mark.skipif(not redis_ok, reason="Redis 不可用")


@requires_redis
def test_lock_owner_token_semantics():
    c = Cache(settings)
    client = cache_mod._get_client(settings)
    key = "test:lock:owner"
    client.delete(key)

    try:
        ok, token = c.acquire_lock(key, 30)
        assert ok and token

        ok2, token2 = c.acquire_lock(key, 30)
        assert not ok2 and token2 is None, "锁被持有时二次获取应失败"

        c.release_lock(key, "wrong-token")
        ok3, _ = c.acquire_lock(key, 30)
        assert not ok3, "错误 token 不得删除他人锁"

        c.release_lock(key, token)
        ok4, token4 = c.acquire_lock(key, 30)
        assert ok4 and token4, "正确 token 释放后应可重新获取"
        c.release_lock(key, token4)
    finally:
        client.delete(key)


@requires_redis
def test_release_without_token_is_noop():
    c = Cache(settings)
    c.release_lock("test:lock:none", None)  # 不应抛异常


@requires_redis
def test_incr_window_counts():
    c = Cache(settings)
    client = cache_mod._get_client(settings)
    client.delete("test:rl:unit")
    try:
        r1 = c.incr_window("test:rl:unit", 30)
        r2 = c.incr_window("test:rl:unit", 30)
        assert r1 == (1, 30)
        assert r2[0] == 2 and 1 <= r2[1] <= 30
    finally:
        client.delete("test:rl:unit")


def test_cleanup_removes_only_stale_files(tmp_path, monkeypatch):
    target = tmp_path / "cache"
    target.mkdir()
    old = target / "old.bin"
    new = target / "new.bin"
    old.write_bytes(b"x")
    new.write_bytes(b"y")
    two_months_ago = time.time() - 60 * 86400
    os.utime(old, (two_months_ago, two_months_ago))

    import app.services.cleanup as cleanup_mod

    monkeypatch.setattr(cleanup_mod, "_TARGETS", (target,))
    cleanup_stale_files(max_age_days=30)

    assert not old.exists(), "超过 30 天的文件应被清理"
    assert new.exists(), "新文件应保留"
