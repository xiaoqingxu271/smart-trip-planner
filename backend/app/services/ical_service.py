"""行程 → iCal (.ics)：景点与三餐生成 VEVENT，可直接导入手机日历。

时间为近似安排（景点按天序从 09:00 起按游览时长顺延，三餐固定时段），
导入后用户可自行微调。日期缺失的天跳过。
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone

from ..models.schemas import DayPlan, Meal, TripPlan

MEAL_TIMES = {"breakfast": (8, 0), "lunch": (12, 0), "dinner": (18, 0)}
MEAL_LABELS = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐"}
DEFAULT_ATTRACTION_START = 9  # 每天第一个景点 09:00 开始
_GAP_MINUTES = 30  # 景点之间留 30 分钟通勤余量


def _fmt(dt: datetime) -> str:
    """iCal UTC 时间（Z 后缀）。"""
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _esc(text: str) -> str:
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _parse_duration(text: str) -> timedelta:
    """"2小时"/"1.5小时"/"90分钟" → timedelta；解析失败默认 2 小时。"""
    m = re.search(r"(\d+(?:\.\d+)?)\s*小时", text or "")
    if m:
        return timedelta(hours=float(m.group(1)))
    m = re.search(r"(\d+)\s*分钟", text or "")
    if m:
        return timedelta(minutes=int(m.group(1)))
    return timedelta(hours=2)


def _uid(plan: TripPlan, day: DayPlan, kind: str, idx: int) -> str:
    raw = f"{plan.destination}|{day.day}|{kind}|{idx}"
    return hashlib.md5(raw.encode()).hexdigest() + "@smart-trip-planner"


def _event(lines: list[str], uid: str, start: datetime, end: datetime,
           summary: str, location: str, description: str) -> None:
    lines += [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{_fmt(datetime.now(timezone.utc))}",
        f"DTSTART:{_fmt(start)}",
        f"DTEND:{_fmt(end)}",
        f"SUMMARY:{_esc(summary)}",
        f"LOCATION:{_esc(location)}",
        f"DESCRIPTION:{_esc(description)}",
        "END:VEVENT",
    ]


def plan_to_ics(plan: TripPlan) -> str:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//smart-trip-planner//CN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_esc(plan.destination)}行程",
    ]
    for day in plan.daily_plans:
        if not day.date:
            continue
        try:
            d0 = datetime.strptime(day.date, "%Y-%m-%d")
        except ValueError:
            continue

        cursor = d0.replace(hour=DEFAULT_ATTRACTION_START)
        for i, a in enumerate(day.attractions):
            start = cursor
            end = start + _parse_duration(a.duration)
            _event(
                lines,
                _uid(plan, day, "attraction", i),
                start, end,
                f"🗿 {a.name}",
                a.address or plan.destination,
                a.recommended_reason or a.description or "",
            )
            cursor = end + timedelta(minutes=_GAP_MINUTES)

        for i, m in enumerate(day.meals):
            hh, mm = MEAL_TIMES.get(m.type, (12, 0))
            start = d0.replace(hour=hh, minute=mm)
            _event(
                lines,
                _uid(plan, day, f"meal-{m.type}", i),
                start, start + timedelta(hours=1),
                f"🍽 {_meal_summary(m)}",
                plan.destination,
                f"人均约¥{m.cost:g}" if m.cost else "",
            )
    lines.append("END:VCALENDAR")
    # RFC5545 要求 CRLF 行尾
    return "\r\n".join(lines)


def _meal_summary(m: Meal) -> str:
    base = MEAL_LABELS.get(m.type, "餐饮")
    return f"{base} · {m.restaurant}" if m.restaurant else base
