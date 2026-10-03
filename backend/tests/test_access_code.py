"""访问码鉴权中间件（进程内 TestClient，不起真实服务）。"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.access import AccessCodeMiddleware


def make_client(password: str) -> TestClient:
    app = FastAPI()
    app.add_middleware(AccessCodeMiddleware, password=password)

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/things")
    def things():
        return {"ok": True}

    return TestClient(app)


def test_disabled_when_password_empty():
    c = make_client("")
    assert c.get("/api/health").status_code == 200
    assert c.get("/api/things").status_code == 200


def test_enabled_requires_code():
    c = make_client("pw123")
    assert c.get("/api/health").status_code == 200, "health 必须免鉴权供前端探测"
    assert c.get("/api/things").status_code == 401
    assert c.get("/api/things", headers={"X-Access-Code": "wrong"}).status_code == 401
    assert c.get("/api/things", headers={"X-Access-Code": "pw123"}).status_code == 200
    assert c.get("/api/things", params={"code": "pw123"}).status_code == 200, "<img> 场景走查询参数"


def test_non_api_paths_exempt():
    c = make_client("pw123")
    assert c.get("/not-api").status_code in (404,), "非 /api 路径不归鉴权管"
