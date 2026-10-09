"""钟点调度器纯函数单测（不打高德、不碰 LLM）。"""
from app.models.schemas import Attraction, DayPlan, Location, TripPlan, TripRequest
from app.services.schedule_service import (
    parse_open_time,
    schedule_attractions,
    schedule_plan,
    slot_start_minutes,
)


def _attr(name, duration):
    return Attraction(name=name, location=Location(longitude=116.0, latitude=39.9), duration=duration)


def test_parse_open_time_basic():
    o, c = parse_open_time("9:00-16:30")
    assert (o.hour, o.minute) == (9, 0)
    assert (c.hour, c.minute) == (16, 30)


def test_parse_open_time_last_entry_wins():
    o, c = parse_open_time("08:30-17:00（16:00停止入园）")
    assert (o.hour, o.minute) == (8, 30)
    assert (c.hour, c.minute) == (16, 0)


def test_parse_open_time_unparseable():
    assert parse_open_time("") == (None, None)
    assert parse_open_time("全天开放") == (None, None)


def test_slot_start_morning_afternoon_evening():
    assert slot_start_minutes("morning") == 9 * 60
    assert slot_start_minutes("afternoon") == 13 * 60 + 30
    assert slot_start_minutes("evening") is None
    assert slot_start_minutes("") == 9 * 60  # 不填时段默认早上


def test_schedule_attractions_fills_times_with_legs():
    attrs = [_attr("甲", "2小时"), _attr("乙", "1小时")]
    warnings = schedule_attractions(attrs, [1200], 9 * 60, {}, 1)
    assert attrs[0].start_time == "09:00"
    assert attrs[0].end_time == "11:00"
    # 1200 秒车程 = 20 分钟间隙
    assert attrs[1].start_time == "11:20"
    assert attrs[1].end_time == "12:20"
    assert warnings == []


def test_schedule_attractions_fallback_gap_when_no_legs():
    attrs = [_attr("甲", "1小时"), _attr("乙", "1小时")]
    schedule_attractions(attrs, None, 9 * 60, {}, 1)
    # 无路线时回退 30 分钟间隔
    assert attrs[0].start_time == "09:00"
    assert attrs[1].start_time == "10:30"


def test_schedule_attractions_warns_late_end():
    attrs = [_attr("甲", "8小时")]
    warnings = schedule_attractions(attrs, None, 9 * 60, {"甲": "9:00-16:30"}, 1)
    assert attrs[0].end_time == "17:00"
    assert any("闭园" in w or "最晚入园" in w for w in warnings)


def test_schedule_plan_afternoon_arrival():
    req = TripRequest(destination="北京", start_date="2026-03-02", days=1, arrival_slot="afternoon")
    day = DayPlan(day=1, date="2026-03-02", attractions=[_attr("甲", "1小时")])
    plan = TripPlan(destination="北京", days=1, daily_plans=[day])
    schedule_plan(plan, req, {}, {})
    assert day.attractions[0].start_time == "13:30"


def test_schedule_plan_evening_arrival_skips():
    req = TripRequest(destination="北京", start_date="2026-03-02", days=1, arrival_slot="evening")
    day = DayPlan(day=1, date="2026-03-02", attractions=[_attr("甲", "1小时")])
    plan = TripPlan(destination="北京", days=1, daily_plans=[day])
    schedule_plan(plan, req, {}, {})
    assert day.attractions[0].start_time == ""