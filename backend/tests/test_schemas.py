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
