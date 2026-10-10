"""每日真实驾车路线：串接当天景点，返回导航折线、里程与打车费。

注意：高德官方 MCP 服务器的 maps_direction_driving 会裁掉 polyline 与
taxi_cost（只回步骤文字），因此这里与 image_service 一样**直连高德 REST**
（restapi.amap.com/v3/direction/driving，Web 服务 Key 仅存在后端），
并复用全局节拍器与 7 天缓存。cache 允许为 None（Redis 不可用时只算不缓存）。
"""
from __future__ import annotations

import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx

from ..models.schemas import TripPlan
from .amap_pacer import pace

logger = logging.getLogger(__name__)

_ROUTE_TTL = 7 * 24 * 3600
_AMAP_DRIVING = "https://restapi.amap.com/v3/direction/driving"


def _to_int(v: Any) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _to_float(v: Any) -> float | None:
    try:
        return round(float(v), 1)
    except (TypeError, ValueError):
        return None


def _cache_get(cache, key: str) -> str | None:
    return cache.get(key) if cache else None


def _cache_set(cache, key: str, value: str, ttl: int) -> None:
    if cache:
        cache.set(key, value, ttl)


def _drive_route(amap_key: str, cache, orig: dict, dest: dict) -> dict[str, Any] | None:
    """两点驾车路线：{"polyline","distance"(米),"duration"(秒),"taxi_cost"(元)}。"""
    key = (
        "route:drive:"
        + hashlib.md5(f"{orig['longitude']},{orig['latitude']}->{dest['longitude']},{dest['latitude']}".encode()).hexdigest()
    )
    cached = _cache_get(cache, key)
    if cached:
        try:
            return json.loads(cached)
        except ValueError:
            pass

    try:
        pace()
        resp = httpx.get(
            _AMAP_DRIVING,
            params={
                "key": amap_key,
                "origin": f"{orig['longitude']},{orig['latitude']}",
                "destination": f"{dest['longitude']},{dest['latitude']}",
                "extensions": "all",  # 含 taxi_cost；polyline 两种模式都返回
                "strategy": "32",  # 默认策略，兼顾拒载/限行
            },
            timeout=8.0,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "1":
            logger.warning("驾车路线业务失败: %s", data.get("info"))
            return None
        route = data.get("route") or {}
        paths = route.get("paths") or []
        if not paths:
            return None
        path = paths[0]
        points: list[list[float]] = []
        for step in path.get("steps") or []:
            for pair in str(step.get("polyline") or "").split(";"):
                if not pair:
                    continue
                lng_s, _, lat_s = pair.partition(",")
                try:
                    points.append([float(lng_s), float(lat_s)])
                except ValueError:
                    continue
        if not points:
            return None
        out = {
            "polyline": points,
            "distance": _to_int(path.get("distance")),
            "duration": _to_int(path.get("duration")),
            # v3 接口 taxi_cost 在 route 层而非 path 层
            "taxi_cost": _to_float(route.get("taxi_cost")),
        }
        _cache_set(cache, key, json.dumps(out), _ROUTE_TTL)
        return out
    except Exception:  # noqa: BLE001
        logger.warning("驾车路线查询失败", exc_info=True)
        return None


def compute_day_routes(amap_key: str, cache, plan: TripPlan) -> dict[str, Any]:
    """按天串接景点坐标，返回前端绘图所需的逐段折线与汇总信息。

    各天相互独立，并发计算以重叠高德 driving 往返（受全局限速约束，不突破 QPS）。
    """
    if not amap_key:
        return {"routes": []}

    def _day_routes(day) -> dict[str, Any] | None:
        pts = [(a.name, a.location.model_dump()) for a in day.attractions]
        if len(pts) < 2:
            return None
        legs: list[dict[str, Any]] = []
        for (name1, loc1), (name2, loc2) in zip(pts, pts[1:]):
            seg = _drive_route(amap_key, cache, loc1, loc2)
            if seg:
                legs.append({"from": name1, "to": name2, **seg})
        if not legs:
            return None
        return {
            "day": day.day,
            "theme": day.theme,
            "distance_m": sum(l["distance"] or 0 for l in legs),
            "duration_s": sum(l["duration"] or 0 for l in legs),
            "taxi_cost": round(sum(l["taxi_cost"] or 0 for l in legs), 1),
            "legs": legs,
        }

    with ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(_day_routes, plan.daily_plans))
    return {"routes": [r for r in results if r]}
