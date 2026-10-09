"""游览时长解析：iCal 与服务端调度器共用的纯函数。"""
from __future__ import annotations

import re
from datetime import timedelta


def parse_duration(text: str) -> timedelta:
    """"2小时"/"1.5小时"/"90分钟" → timedelta；解析失败默认 2 小时。"""
    m = re.search(r"(\d+(?:\.\d+)?)\s*小时", text or "")
    if m:
        return timedelta(hours=float(m.group(1)))
    m = re.search(r"(\d+)\s*分钟", text or "")
    if m:
        return timedelta(minutes=int(m.group(1)))
    return timedelta(hours=2)


def parse_duration_minutes(text: str) -> int:
    """游览时长 → 分钟（整数，向上取整到 5 分钟便于排钟点）。"""
    total = int(parse_duration(text).total_seconds() // 60)
    return total