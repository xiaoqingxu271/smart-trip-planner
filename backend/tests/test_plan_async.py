"""异步规划任务：提交/轮询/降级/失败路径。"""
import time

import pytest
from fastapi.testclient import TestClient

import app.api.main as main_mod
from app.storage.cache import _get_client

POLL_TIMEOUT = 30


@pytest.fixture(autouse=True)
def _no_auth(monkeypatch):
    """本文件聚焦异步流水线本身：显式关闭多用户鉴权（默认值已是 user 模式）。"""
    monkeypatch.setattr(main_mod.settings, "auth_mode", "none")


@pytest.fixture(autouse=True)
def _fresh_heavy_bucket():
    """heavy 桶 5/300s：清掉跨运行残留的限流键，并确保存储已初始化（404 用例状态确定）。"""
    main_mod._get_store()
    try:
        c = _get_client(main_mod.settings)
        keys = list(c.scan_iter("rl:heavy:*"))
        if keys:
            c.delete(*keys)
    except Exception:
        pass  # Redis 不可用时由 fail-open 兜底
    yield


def poll_until_done(client: TestClient, job_id: str) -> dict:
    deadline = time.time() + POLL_TIMEOUT
    state = None
    while time.time() < deadline:
        r = client.get(f"/api/trip/jobs/{job_id}")
        assert r.status_code == 200
        state = r.json()
        if state["status"] in ("succeeded", "failed"):
            return state
        time.sleep(0.3)
    raise AssertionError(f"任务超时未完成: {state}")


def test_async_demo_job_succeeds(monkeypatch):
    monkeypatch.setattr(main_mod.settings, "demo_mode", True)
    client = TestClient(main_mod.app)

    r = client.post("/api/trip/plan/async", json={"destination": "北京", "days": 2})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "job" and len(body["job_id"]) == 24

    state = poll_until_done(client, body["job_id"])
    assert state["status"] == "succeeded", state
    assert state["plan"]["destination"] == "北京"
    assert state["stage"] == "done"


def test_async_demo_failure_marks_job_failed(monkeypatch):
    monkeypatch.setattr(main_mod.settings, "demo_mode", True)

    def boom(*a, **k):
        raise RuntimeError("demo boom")

    monkeypatch.setattr(main_mod, "build_demo_plan", boom)
    client = TestClient(main_mod.app)

    r = client.post("/api/trip/plan/async", json={"destination": "北京", "days": 2})
    state = poll_until_done(client, r.json()["job_id"])
    assert state["status"] == "failed"
    assert "error" in state


def test_job_404(monkeypatch):
    monkeypatch.setattr(main_mod.settings, "demo_mode", True)
    client = TestClient(main_mod.app)
    assert client.get("/api/trip/jobs/0123456789abcdef01234567").status_code == 404
    assert client.get("/api/trip/jobs/not-a-valid-id").status_code == 404


def test_sync_fallback_when_cache_none(monkeypatch):
    """Redis 不可用时降级为同步执行：mode=sync 且直接携带结果。"""
    main_mod._get_store()  # 先完成存储初始化，防止后续 _get_store 重建 _cache 覆盖补丁
    monkeypatch.setattr(main_mod.settings, "demo_mode", True)
    monkeypatch.setattr(main_mod, "_cache", None)
    client = TestClient(main_mod.app)
    r = client.post("/api/trip/plan/async", json={"destination": "上海", "days": 2})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "sync"
    assert body["plan"]["destination"] == "北京"  # demo 数据固定为北京 3 日


def test_sync_endpoint_demo_still_works(monkeypatch):
    """兼容层：同步 /api/trip/plan 在 demo 模式下行为不变。"""
    monkeypatch.setattr(main_mod.settings, "demo_mode", True)
    client = TestClient(main_mod.app)
    r = client.post("/api/trip/plan", json={"destination": "广州", "days": 3})
    assert r.status_code == 200
    assert r.json()["destination"] == "北京"  # demo 数据固定为北京 3 日
