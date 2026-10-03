"""限流中间件（确定性假缓存计数，不依赖 Redis，结果可复现）。"""
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.rate_limit import RateLimitMiddleware
from app.config import Settings


class FakeCache:
    """进程内固定窗口计数，返回的 ttl 恒为窗口长度，测试结果确定。"""

    def __init__(self):
        self.counts = {}

    def incr_window(self, key, window):
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key], window


def build_and_inject(heavy="2/60", img="6/60", enabled=True):
    """构建带限流中间件的应用，并在栈构建后注入确定性假缓存。"""
    settings = Settings(
        rate_limit_heavy=heavy,
        rate_limit_img=img,
        rate_limit_enabled=enabled,
    )
    app = FastAPI()
    executor = ThreadPoolExecutor(max_workers=2)

    holder = {}

    class RecordingMiddleware(RateLimitMiddleware):
        def __init__(self, app, **kwargs):
            super().__init__(app, **kwargs)
            holder["mw"] = self

    app.add_middleware(RecordingMiddleware, settings=settings, executor=executor)

    @app.post("/api/trip/plan")
    def plan():
        return {"ok": True}

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/utils/image")
    def image():
        return {"ok": True}

    client = TestClient(app)
    client.get("/api/health")  # 触发中间件栈构建
    holder["mw"].cache = FakeCache()
    return client


def test_heavy_bucket_limits_and_headers():
    client = build_and_inject(heavy="2/60")
    r1 = client.post("/api/trip/plan")
    r2 = client.post("/api/trip/plan")
    r3 = client.post("/api/trip/plan")
    assert r1.status_code == r2.status_code == 200
    assert r1.headers["X-RateLimit-Limit"] == "2"
    assert r3.status_code == 429
    assert int(r3.headers["Retry-After"]) > 0
    assert "太频繁" in r3.json()["detail"]


def test_health_and_options_bypass():
    client = build_and_inject(heavy="1/60")
    assert client.post("/api/trip/plan").status_code == 200
    assert client.post("/api/trip/plan").status_code == 429
    # health 与 OPTIONS 预检不受限流影响（OPTIONS 无对应路由，405 即证明未被限流 429）
    assert client.get("/api/health").status_code == 200
    assert client.options("/api/trip/plan").status_code == 405


def test_img_bucket_independent_from_heavy():
    client = build_and_inject(heavy="1/60", img="2/60")
    assert client.post("/api/trip/plan").status_code == 200
    assert client.post("/api/trip/plan").status_code == 429
    assert client.get("/api/utils/image").status_code == 200
    assert client.get("/api/utils/image").status_code == 200
    assert client.get("/api/utils/image").status_code == 429


def test_disabled_switch():
    client = build_and_inject(heavy="1/60", enabled=False)
    assert client.post("/api/trip/plan").status_code == 200
    assert client.post("/api/trip/plan").status_code == 200, "RATE_LIMIT_ENABLED=false 应整体关闭"
