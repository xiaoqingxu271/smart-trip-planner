"""流式规划端点（demo 模式）：验证 SSE 事件序列与最终结果。

限流/锁冲突的"流开始前 HTTP 错误"行为由 test_rate_limit.py 的确定性用例覆盖。
"""
import json

from fastapi.testclient import TestClient

import app.api.main as main_mod


def test_stream_demo_mode(monkeypatch):
    monkeypatch.setattr(main_mod.settings, "demo_mode", True)
    client = TestClient(main_mod.app)

    events: list[str] = []
    stages: list[str] = []
    final_plan: dict | None = None

    with client.stream("POST", "/api/trip/plan/stream", json={"destination": "北京", "days": 2}) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        current_event = "message"
        for line in r.iter_lines():
            if line.startswith("event: "):
                current_event = line[7:].strip()
                events.append(current_event)
            elif line.startswith("data: "):
                payload = json.loads(line[6:])
                if current_event == "progress":
                    stages.append(payload["stage"])
                elif current_event == "done":
                    final_plan = payload["plan"]

    assert events, "应至少收到一个 SSE 事件"
    assert events[0] == "progress" and events[-1] == "done"
    # 演示分支的阶段顺序与后端 pipeline 阶段一一对应
    assert stages == [
        "started", "attractions", "weather", "hotel",
        "planning", "planned", "validating", "validated", "images",
    ]
    assert final_plan is not None
    assert final_plan["destination"] == "北京"
    # demo 数据是固定的北京 3 日行程，不随请求天数变化
    assert len(final_plan["daily_plans"]) == 3
