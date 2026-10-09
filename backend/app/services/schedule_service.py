"""确定性钟点调度器：为每天景点填 start_time / end_time。

设计要点（批次 1.2）：
- 纯函数，不碰高德、不碰 LLM，便于单测；钟点由这里确定性填写，Planner 不编给人造钟点；
- 点与点间用真实路线 legs 的 duration_s 排间隙，无路线时回退 30 分钟；
- 开园文案解析失败则不卡闭园，只排钟点；超过最晚入园/闭园仅写 warning，不改点位
  （改点位交给 Validator 的定向修复）。
"""
from __future__ import annotations

import re
from datetime import time
from typing import Any

from ..models.schemas import DayPlan, TripPlan, TripRequest
from ..utils.duration import parse_duration_minutes

# 各抵达时段对应的当日开始钟点（分钟数，自 00:00）
_SLOT_START_Minutes = {"morning": 9 * 60, "afternoon": 13 * 60 + 30, "evening": None}
_DEFAULT_START = 9 * 60
_FALLBACK_GAP_MINUTES = 30

_TIME_RANGE_RE = re.compile(r"(\d{1,2})[:：](\d{1,2})\s*[-—~～至到]\s*(\d{1,2})[:：](\d{1,2})")
_LAST_ENTRY_RE = re.compile(r"(\d{1,2})[:：](\d{1,2})\s*[^，。；;\n\d:：]*?(停止入园|停止售票|停止入场|停止检票)")


def parse_open_time(text: str) -> tuple[time | None, time | None]:
    """"9:00-16:30" / "08:30-17:00（16:00停止入园）" → (开园, 最晚入园/闭园)。

    规则：停止入园/售票/入场时间优先作为"最晚可入园"；否则取所有时间区间中
    最晚的结束时间作为闭园。解析失败返回 (None, None)。
    """
    if not text:
        return None, None
    open_t: time | None = None
    close_t: time | None = None
    for m in _TIME_RANGE_RE.finditer(text):
        o = _to_time(int(m.group(1)), int(m.group(2)))
        c = _to_time(int(m.group(3)), int(m.group(4)))
        if o and open_t is None:
            open_t = o
        if c and (close_t is None or c > close_t):
            close_t = c
    last_entry: time | None = None
    m = _LAST_ENTRY_RE.search(text)
    if m:
        last_entry = _to_time(int(m.group(1)), int(m.group(2)))
    return open_t, (last_entry or close_t)


def _to_time(hour: int, minute: int) -> time | None:
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return time(hour, minute)
    return None


def _fmt(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def slot_start_minutes(slot: str) -> int | None:
    """抵达/离开时段 → 当日开始钟点（分钟）；evening 表示不排景点。"""
    if not slot:
        return _DEFAULT_START
    return _SLOT_START_Minutes.get(slot, _DEFAULT_START)


def schedule_attractions(
    attractions: list[Any],
    leg_durations: list[int | None] | None,
    start_minutes: int,
    open_times: dict[str, str] | None = None,
    day_no: int = 1,
) -> list[str]:
    """为一组景点按顺序填写 start_time/end_time，返回 warnings（闭园提醒）。

    leg_durations[i] 为第 i 个景点到第 i+1 个景点之间的车程（秒），
    缺省或为 None 时按 30 分钟步行/通勤回退。attractions 上就地写 start_time/end_time。
    """
    warnings: list[str] = []
    cursor = start_minutes
    for i, attr in enumerate(attractions):
        dur = parse_duration_minutes(attr.duration)
        end = cursor + dur
        attr.start_time = _fmt(cursor)
        attr.end_time = _fmt(end)

        ot = (open_times or {}).get(attr.name)
        if ot:
            _, close_t = parse_open_time(ot)
            if close_t is not None and end > close_t.hour * 60 + close_t.minute:
                warnings.append(
                    f"第{day_no}天「{attr.name}」安排到 {attr.end_time}，可能晚于闭园/最晚入园时间"
                    f"（{ot[:60]}），建议调整顺序或提前出发"
                )

        if i < len(attractions) - 1:
            gap_sec = None
            if leg_durations and i < len(leg_durations):
                gap_sec = leg_durations[i]
            gap_min = _FALLBACK_GAP_MINUTES if not gap_sec else max(1, round(gap_sec / 60))
            cursor = end + gap_min
    return warnings


def schedule_plan(
    plan: TripPlan,
    request: TripRequest,
    routes_by_day: dict[int, Any] | None = None,
    open_times: dict[str, str] | None = None,
) -> list[str]:
    """对整个行程逐天排钟点（就地在 attractions 上写 start_time/end_time）。

    routes_by_day: {day: {"legs": [{"duration": 秒}, ...]}}，来自 compute_day_routes。
    """
    warnings: list[str] = []
    if not plan.daily_plans:
        return warnings
    last_day = max(d.day for d in plan.daily_plans)
    for day in plan.daily_plans:
        if not day.attractions:
            continue
        if day.day == 1:
            start = slot_start_minutes(request.arrival_slot)
        else:
            start = slot_start_minutes("morning")
        if start is None:
            # 首日傍晚抵达：不排景点钟点（密度由 Validator 把关）
            continue
        legs: list[int | None] | None = None
        route = (routes_by_day or {}).get(day.day)
        if route and route.get("legs"):
            legs = [leg.get("duration") for leg in route["legs"]]
        warnings += schedule_attractions(day.attractions, legs, start, open_times, day.day)
    return warnings