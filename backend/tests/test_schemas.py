"""请求/输出模型的边界：超长与超量输入必须被拦截。"""
import pytest
from pydantic import ValidationError

from app.models.schemas import Feedback, ReplanBody, TripPlan, TripRequest
from app.services.demo_data import build_demo_plan


def must_fail(request_builder):
    with pytest.raises(ValidationError):
        request_builder()


def test_destination_max_length():
    must_fail(lambda: TripRequest(destination="北" * 51))
    TripRequest(destination="北" * 50)  # 恰好在上限内


def test_notes_max_length():
    must_fail(lambda: TripRequest(destination="北京", notes="x" * 501))
    TripRequest(destination="北京", notes="x" * 500)


def test_preferences_bounds():
    must_fail(lambda: TripRequest(destination="北京", preferences=["a"] * 11))
    must_fail(lambda: TripRequest(destination="北京", preferences=["x" * 31]))
    TripRequest(destination="北京", preferences=["博物馆", "美食"] * 5)


def test_days_range():
    must_fail(lambda: TripRequest(destination="北京", days=0))
    must_fail(lambda: TripRequest(destination="北京", days=8))


def test_budget_minimum_per_day():
    from app.models.schemas import MIN_BUDGET_PER_DAY

    # 未填预算：仍可选，不强制
    TripRequest(destination="北京", days=3)
    # 恰好达标 → 通过
    TripRequest(destination="北京", days=3, budget=3 * MIN_BUDGET_PER_DAY)
    # 低于最低标准 → 拒绝
    must_fail(lambda: TripRequest(destination="北京", days=3, budget=3 * MIN_BUDGET_PER_DAY - 1))
    # 天数多预算少 → 拒绝
    must_fail(lambda: TripRequest(destination="北京", days=7, budget=500))
    # 天数少预算低 → 按天数折算后达标
    TripRequest(destination="北京", days=1, budget=MIN_BUDGET_PER_DAY)


def test_replan_feedback_bounds():
    fb = lambda: Feedback(target="attraction", name="x", reason="y")  # noqa: E731
    must_fail(lambda: ReplanBody(trip_id=1, feedbacks=[fb()] * 11))
    must_fail(lambda: Feedback(target="attraction", name="x" * 101, reason="y"))
    must_fail(lambda: Feedback(target="attraction", name="x", reason="y" * 201))


def test_llm_output_side_bounds():
    """TripPlan 同时校验 LLM 输出，失控长文本应被 schema 拦截。"""
    plan = build_demo_plan(["2026-10-01"] * 3)
    data = plan.model_dump()
    data["daily_plans"][0]["attractions"][0]["name"] = "x" * 101
    with pytest.raises(ValidationError):
        TripPlan.model_validate(data)


def test_demo_plan_passes_new_limits():
    """demo 数据是既有基线，模型收紧后必须仍然通过。"""
    plan = build_demo_plan(["2026-10-01"] * 3)
    TripPlan.model_validate(plan.model_dump())


def test_new_request_fields_have_defaults():
    """批次 1.1/3 新增请求字段：缺省时取默认，旧请求可正常构造。"""
    req = TripRequest(destination="北京")
    assert req.arrival_slot == ""
    assert req.departure_slot == ""
    assert req.pace == "standard"
    assert req.must_see == []
    assert req.avoid == []
    assert req.transit_mode == "taxi"
    assert req.origin == ""


def test_old_request_json_loads_without_new_fields():
    """旧请求 JSON（无新字段）能 model_validate，不报错。"""
    TripRequest.model_validate({"destination": "北京", "start_date": "2026-10-01", "days": 3})


def test_must_see_avoid_bounds():
    must_fail(lambda: TripRequest(destination="北京", must_see=["x"] * 6))
    must_fail(lambda: TripRequest(destination="北京", must_see=["x" * 41]))
    must_fail(lambda: TripRequest(destination="北京", avoid=["x"] * 6))
    TripRequest(destination="北京", must_see=["中山陵"] * 5, avoid=["夫子庙"] * 5)


def test_pace_literal_rejects_invalid():
    must_fail(lambda: TripRequest(destination="北京", pace="slow"))  # 非法 Literal


def test_transit_mode_literal_rejects_invalid():
    must_fail(lambda: TripRequest(destination="北京", transit_mode="bike"))
