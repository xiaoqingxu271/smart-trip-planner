"""多用户体系：密码哈希 / 注册登录流 / 数据隔离与可见性。"""
import uuid

import pytest
from fastapi.testclient import TestClient

import app.api.main as main_mod
from app.services.user_service import hash_password, verify_password
from app.storage.cache import _get_client

client = TestClient(main_mod.app)


@pytest.fixture(autouse=True)
def _flush_auth_rate_limit():
    """auth 桶限流 10/分/IP：每个用例前清掉认证限流键，避免用例间互相挤占配额。"""
    try:
        c = _get_client(main_mod.settings)
        keys = list(c.scan_iter("rl:auth:*"))
        if keys:
            c.delete(*keys)
    except Exception:
        pass  # Redis 不可用时由 fail-open 兜底
    yield


def _uniq(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _register(username: str, password: str = "secret66"):
    r = client.post("/api/auth/register", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


# ---------- 密码哈希 ----------

def test_password_hash_roundtrip():
    stored = hash_password("s3cret-密码")
    assert stored.startswith("pbkdf2_sha256$200000$")
    assert verify_password("s3cret-密码", stored)
    assert not verify_password("wrong", stored)


def test_password_hash_malformed_stored():
    assert not verify_password("x", "garbage")


# ---------- 注册 / 登录 / 会话 ----------

def test_register_login_me_logout_flow():
    username = _uniq("user")
    reg = _register(username)
    token = reg["token"]
    assert reg["username"] == username
    auth = {"Authorization": f"Bearer {token}"}

    # me
    r = client.get("/api/auth/me", headers=auth)
    assert r.status_code == 200 and r.json()["username"] == username

    # 重复登录（密码正确）
    r = client.post("/api/auth/login", json={"username": username, "password": "secret66"})
    assert r.status_code == 200 and r.json()["token"]

    # 登出后 token 失效
    assert client.post("/api/auth/logout", headers=auth).status_code == 200
    assert client.get("/api/auth/me", headers=auth).status_code == 401


def test_register_validation_and_conflicts():
    username = _uniq("user")
    _register(username)
    # 用户名重复
    r = client.post("/api/auth/register", json={"username": username, "password": "secret66"})
    assert r.status_code == 400
    # 非法用户名
    r = client.post("/api/auth/register", json={"username": "ab", "password": "secret66"})
    assert r.status_code == 400
    r = client.post("/api/auth/register", json={"username": "含中文", "password": "secret66"})
    assert r.status_code == 400
    # 密码过短
    r = client.post("/api/auth/register", json={"username": _uniq("user"), "password": "12345"})
    assert r.status_code == 400


def test_login_wrong_password():
    username = _uniq("user")
    _register(username, "secret66")
    r = client.post("/api/auth/login", json={"username": username, "password": "wrong-pass"})
    assert r.status_code == 401


# ---------- 多用户模式下的隔离与可见性 ----------

def test_user_mode_isolation(monkeypatch):
    monkeypatch.setattr(main_mod.settings, "auth_mode", "user")
    store = main_mod._get_store()
    assert store is not None

    user_a = _register(_uniq("alice"))
    user_b = _register(_uniq("bob"))
    auth_a = {"Authorization": f"Bearer {user_a['token']}"}
    auth_b = {"Authorization": f"Bearer {user_b['token']}"}
    uid_a, uid_b = user_a["user_id"], user_b["user_id"]

    # 各自建一条行程（直接走存储层，不触发 LLM）
    from app.services.demo_data import build_demo_plan
    from app.models.schemas import TripRequest

    req = TripRequest(destination="北京", days=2)
    plan = build_demo_plan(["2026-10-01", "2026-10-02"])
    trip_a = store.save(req, plan, uid_a)
    trip_b = store.save(req, plan, uid_b)

    # 未登录 → 401（历史与详情）
    assert client.get("/api/trip/history").status_code == 401
    assert client.get(f"/api/trip/history/{trip_a}").status_code == 401

    # A 只看到自己的 recent
    r = client.get("/api/trip/history", params={"filter": "recent"}, headers=auth_a)
    ids_a = [t["id"] for t in r.json()]
    assert trip_a in ids_a and trip_b not in ids_a

    # 详情：本人可见，他人 404；health 始终公开
    assert client.get(f"/api/trip/history/{trip_a}", headers=auth_a).status_code == 200
    assert client.get(f"/api/trip/history/{trip_a}", headers=auth_b).status_code == 404
    assert client.get("/api/health").status_code == 200

    # 写操作：他人一律 404
    assert client.put(f"/api/trip/history/{trip_b}", json=plan.model_dump(), headers=auth_a).status_code == 404
    assert client.post(f"/api/trip/history/{trip_b}/star", json={"starred": True}, headers=auth_a).status_code == 404
    assert client.delete(f"/api/trip/history/{trip_b}", headers=auth_a).status_code == 404

    # 本人操作正常
    assert client.post(f"/api/trip/history/{trip_a}/star", json={"starred": True}, headers=auth_a).status_code == 200
    r = client.get("/api/trip/history", params={"filter": "starred"}, headers=auth_a)
    assert trip_a in [t["id"] for t in r.json()]

    # 示例/无主行程全员可读、不可改删
    legacy_id = store.save(req, plan, None)
    assert client.get(f"/api/trip/history/{legacy_id}", headers=auth_b).status_code == 200
    assert client.delete(f"/api/trip/history/{legacy_id}", headers=auth_b).status_code == 404

    # 清理测试数据
    store.delete(trip_a, uid_a)
    store.delete(trip_b, uid_b)
    main_mod._cache.delete(f"trip:detail:{legacy_id}")


def test_none_mode_store_ignores_user_param(monkeypatch):
    """无鉴权模式（user_id=None）：行为与从前一致。"""
    store = main_mod._get_store()
    assert store is not None
    from app.services.demo_data import build_demo_plan
    from app.models.schemas import TripRequest

    req = TripRequest(destination="北京", days=2)
    plan = build_demo_plan(["2026-10-01", "2026-10-02"])
    tid = store.save(req, plan)
    assert store.get_detail(tid) is not None
    store.delete(tid)
    assert store.get_detail(tid) is None
