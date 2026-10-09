"""UserAuthMiddleware 的公开路径清单校验。

保证地图初始化 /utils/* 等前置接口在 user 模式下也能被未登录请求命中，
防止后续重构把它们漏进鉴权保护面。
"""
import pytest
from fastapi.testclient import TestClient

import app.api.main as main_mod


@pytest.fixture(autouse=True)
def _enable_user_auth():
    """强制启用多用户模式（默认已是 user，显式确保用例稳定）。"""
    main_mod.settings.auth_mode = "user"
    yield
    main_mod.settings.auth_mode = "none"


def test_public_paths_accessible_without_token():
    """中间件的 PUBLIC_PATHS 集合里的路径无 Token 也能访问。"""
    client = TestClient(main_mod.app)
    for path in ("/api/health", "/api/config",
                 "/api/utils/geocode", "/api/utils/poi",
                 "/api/utils/image",
                 "/api/auth/register", "/api/auth/login"):
        r = client.get(path, params={"dummy": "1"}) if path.startswith("/api/utils") else client.get(path)
        assert r.status_code != 401, f"{path} 应公开，却被鉴权拦截"


def test_protected_paths_require_token():
    """非公开路径：无 Token → 401。"""
    client = TestClient(main_mod.app)
    # /api/trip/history（GET）: 无 token
    assert client.get("/api/trip/history").status_code == 401
    # /api/trip/plan（POST）: 无 token
    assert client.post("/api/trip/plan", json={"destination": "北京", "days": 1}).status_code == 401


def test_config_returns_amap_keys():
    """/api/config 必须返回 AMAP_JS_KEY 供地图初始化（即便 demo_mode=False）。"""
    client = TestClient(main_mod.app)
    r = client.get("/api/config")
    assert r.status_code == 200
    body = r.json()
    assert "amap_js_key" in body, "/api/config 必须暴露 amap_js_key"
