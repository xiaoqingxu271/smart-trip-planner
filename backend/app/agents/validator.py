"""规划结果校验器：确定性规则检查 + 真实替代候选搜索。

一期检查项：
- 真实性：景点能否在高德核实（同名 POI）——核实不到仅提醒（弱）；
  核实到但坐标与实际位置偏差 > 1.5km 疑似编造（强，触发修复）
- 闭馆日：景点在高德 POI 详情中的 opentime（如"周一闭馆"）与出发日星期几冲突
- 动线距离：同日相邻景点驾车距离 > 15km（maps_distance）
- 行程密度：单日景点 > 3

设计要点：
- 全部是确定性规则，不消耗 LLM；每类结果带 Redis 缓存（POI 信息 7 天），
  同城重复规划零成本；
- 坐标偏差用 haversine 纯计算，不消耗高德配额；
- 搜索替代候选（maps_around_search / maps_text_search）供自动修复与
  用户反馈重规划共用，坐标来自真实 POI，杜绝编造。
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta
from math import asin, cos, radians, sin, sqrt
from typing import Any

from ..models.schemas import TripPlan, TripRequest
from ..services.amap_pacer import pace
from ..storage.cache import Cache

_OPENTIME_TTL = 7 * 24 * 3600
_FAR_DISTANCE_METERS = 15000
# 直线距离预筛阈值：驾车距离恒 ≥ 直线距离，因此直线 >15km 必然超标（免 API）；
# 直线 <8km 时驾车通常 ≤1.4×直线 ≈ 11km，必然达标（免 API）。只对中间地带调 API。
_STRAIGHT_SKIP_METERS = 8000
_MAX_PER_DAY = 3
_COORD_MISMATCH_METERS = 1500  # 景点坐标与高德实际位置的最大可信偏差

# 周一~周日（匹配 opentime 里的"周一闭馆/星期一闭馆"）
_WD_CHAR = ["一", "二", "三", "四", "五", "六", "日"]


def _issue(kind: str, day: int, name: str, text: str, location: dict | None) -> dict[str, Any]:
    return {"kind": kind, "day": day, "name": name, "text": text, "location": location, "strong": True}


def _coord_distance_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """两坐标球面距离（米），haversine 近似——纯计算，不消耗高德配额。"""
    lon1, lat1, lon2, lat2 = map(radians, (lon1, lat1, lon2, lat2))
    a = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371000 * asin(sqrt(min(1.0, a)))


def _pick_poi(pois: list[dict], name: str) -> dict | None:
    """优先取名称完全一致的 POI，否则取名称互相包含的第一条。"""
    for p in pois:
        if str(p.get("name", "")).strip() == name:
            return p
    for p in pois:
        pn = str(p.get("name", ""))
        if pn and (pn in name or name in pn):
            return p
    return None


def _get_poi_info(client, cache: Cache, name: str, city: str) -> dict | None:
    """核实景点用的 POI 信息：{"name","longitude","latitude","opentime"}；未找到返回 None。

    text_search 决定真实性与坐标基准 → search_detail 补 open_time。
    负缓存"-"表示确认查无此 POI；缓存 key 为 poi:info2:（旧 poi:ot2: 只存
    开放时间文本，无法支撑坐标比对，7 天内自然过期）。
    """

    def fetch():
        try:
            pace()
            r = client.call_tool("maps_text_search", {"keywords": name, "city": city})
            pois = json.loads(r).get("pois") or []
            poi = _pick_poi(pois, name)
            if not poi:
                return None
            lng, _, lat = str(poi.get("location", "")).partition(",")
            info: dict[str, Any] = {
                "name": str(poi.get("name", "")),
                "longitude": float(lng) if lng else None,
                "latitude": float(lat) if lat else None,
                "opentime": None,
            }
            pid = poi.get("id")
            if pid:
                pace()
                d = client.call_tool("maps_search_detail", {"id": pid})
                data = json.loads(d)
                plist = data.get("pois") or []
                detail = plist[0] if isinstance(plist, list) and plist else data
                # 高德详情字段不统一：v5 是 open_time/opentime2，老版本为 opentime
                texts = [str(detail.get(k)) for k in ("open_time", "opentime2", "opentime") if detail.get(k)]
                info["opentime"] = "；".join(texts) if texts else None
            return info
        except Exception:  # noqa: BLE001
            return None

    key = f"poi:info2:{hashlib.md5(f'{city}::{name}'.encode()).hexdigest()}"
    v = cache.get(key)
    if v is not None:
        return None if v == "-" else json.loads(v)
    val = fetch()
    cache.set(key, val if val else "-", _OPENTIME_TTL)
    return val


def _is_closed_on(opentime: str, wd: str) -> bool:
    """判断开放时间文案是否表达"周{wd}关闭"。

    兼容：周一闭馆 / 逢周一闭馆 / 周一全天关闭 / 星期一不开放 等；
    排除"周一至周五开放"这类区间表述。
    """
    pattern = rf"(周|星期){wd}(?!至)[^，。；;]{{0,6}}?(闭馆|闭园|全天关闭|关闭|不开放|停止开放)"
    return bool(re.search(pattern, opentime))


def _closed_match(opentime: str, wd: str) -> re.Match | None:
    pattern = rf"(周|星期){wd}(?!至)[^，。；;]{{0,6}}?(闭馆|闭园|全天关闭|关闭|不开放|停止开放)"
    return re.search(pattern, opentime)


def _is_weak_closure(opentime: str, match: re.Match) -> bool:
    """弱闭馆信号：匹配片段前方有"除…外"限定（如"除团城外，其他逢周一关闭"），
    指的是园区内部分院落而非景点本身，只提醒不强制替换。"""
    window = opentime[max(0, match.start() - 14) : match.start()]
    return "除" in window


def _get_distance(client, cache: Cache, loc1: dict, loc2: dict) -> int | None:
    """两点驾车距离（米）。两点坐标不变则距离恒定，结果缓存 7 天；
    查询失败（如限流抖动）不缓存，下次仍会重试。"""
    key = (
        "route:dist:"
        + hashlib.md5(
            f"{loc1['longitude']},{loc1['latitude']}->{loc2['longitude']},{loc2['latitude']}".encode()
        ).hexdigest()
    )
    cached = cache.get(key)
    if cached:
        try:
            return int(cached)
        except ValueError:
            pass

    try:
        pace()
        r = client.call_tool(
            "maps_distance",
            {
                "origins": f"{loc1['longitude']},{loc1['latitude']}",
                "destination": f"{loc2['longitude']},{loc2['latitude']}",
                "type": "1",
            },
        )
        results = json.loads(r).get("results") or []
        if results and results[0].get("distance"):
            dist = int(results[0]["distance"])
            cache.set(key, str(dist), _OPENTIME_TTL)
            return dist
    except Exception:  # noqa: BLE001
        pass
    return None


def validate_plan(plan: TripPlan, request: TripRequest, client, cache: Cache) -> list[dict[str, Any]]:
    """确定性校验，返回问题清单（每项含 kind/day/name/text/location/strong）。

    strong=True 的问题会触发自动修复；strong=False 仅作为提醒展示。
    """
    issues: list[dict[str, Any]] = []
    try:
        start = datetime.strptime(request.start_date, "%Y-%m-%d").date() if request.start_date else date.today()
    except ValueError:
        start = date.today()

    for day in plan.daily_plans:
        # 每一天按自己的日期判断星期（第 2 天是周二就不能用出发日的周一去查）
        if day.date:
            try:
                day_date = datetime.strptime(day.date, "%Y-%m-%d").date()
            except ValueError:
                day_date = start + timedelta(days=day.day - 1)
        else:
            day_date = start + timedelta(days=day.day - 1)
        wd = _WD_CHAR[day_date.weekday()]

        if len(day.attractions) > _MAX_PER_DAY:
            issues.append(
                _issue(
                    "density", day.day, "",
                    f"第{day.day}天安排了 {len(day.attractions)} 个景点，过于紧凑，建议精简到 {_MAX_PER_DAY} 个以内或拆分到相邻天",
                    None,
                )
            )
        prev: dict | None = None
        for attr in day.attractions:
            info = _get_poi_info(client, cache, attr.name, plan.destination)
            if info is None:
                issues.append(
                    _issue(
                        "unverified", day.day, attr.name,
                        f"第{day.day}天「{attr.name}」未能在高德地图中核实（名称可能不规范或不存在），建议确认",
                        attr.location.model_dump(),
                    )
                    | {"strong": False}
                )
            else:
                # 坐标基准比对：LLM 被要求使用真实坐标，大幅偏离即疑似编造
                if info["longitude"] is not None and info["latitude"] is not None:
                    dist = _coord_distance_m(
                        attr.location.longitude, attr.location.latitude,
                        info["longitude"], info["latitude"],
                    )
                    if dist > _COORD_MISMATCH_METERS:
                        issues.append(
                            _issue(
                                "fake_coord", day.day, attr.name,
                                f"第{day.day}天「{attr.name}」坐标与高德实际位置偏差约 {dist / 1000:.1f} 公里，疑似编造",
                                {"longitude": info["longitude"], "latitude": info["latitude"]},
                            )
                        )
                ot = info["opentime"]
                if ot:
                    m = _closed_match(ot, wd)
                    if m:
                        weak = _is_weak_closure(ot, m)
                        qualifier = "（仅部分院落/区域关闭，建议现场确认）" if weak else ""
                        issues.append(
                            _issue(
                                "closed_day", day.day, attr.name,
                                f"第{day.day}天「{attr.name}」周{wd}{'可能' if weak else ''}不开放{qualifier}，开放时间：{ot[:60]}",
                                attr.location.model_dump(),
                            )
                            | {"strong": not weak}
                        )
            if prev is not None:
                prev_loc = prev["location"]
                straight = _coord_distance_m(
                    prev_loc["longitude"], prev_loc["latitude"],
                    attr.location.longitude, attr.location.latitude,
                )
                if straight > _FAR_DISTANCE_METERS:
                    # 直线已超 15km，驾车只会更远，免调 API 直接判罚
                    issues.append(
                        _issue(
                            "far", day.day, attr.name,
                            f"第{day.day}天「{prev['name']}」到「{attr.name}」直线已约 {straight / 1000:.0f} 公里，动线不合理",
                            attr.location.model_dump(),
                        )
                    )
                elif straight >= _STRAIGHT_SKIP_METERS:
                    dist = _get_distance(client, cache, prev_loc, attr.location.model_dump())
                    if dist and dist > _FAR_DISTANCE_METERS:
                        issues.append(
                            _issue(
                                "far", day.day, attr.name,
                                f"第{day.day}天「{prev['name']}」到「{attr.name}」驾车约 {dist / 1000:.0f} 公里，动线不合理",
                                attr.location.model_dump(),
                            )
                        )
                # 直线 <8km：驾车通常 ≤1.4×直线，必然达标，不调 API
            prev = {"name": attr.name, "location": attr.location.model_dump()}
    return issues


def search_candidates(
    client,
    exclude_name: str,
    location: dict | None,
    keywords: str,
    city: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """搜索真实替代候选：有坐标走周边搜（3km），否则城市级关键词搜。"""
    try:
        pace()
        if location:
            r = client.call_tool(
                "maps_around_search",
                {
                    "keywords": keywords,
                    "location": f"{location['longitude']},{location['latitude']}",
                    "radius": 3000,
                },
            )
        else:
            r = client.call_tool("maps_text_search", {"keywords": keywords, "city": city})
        pois = json.loads(r).get("pois") or []
    except Exception:  # noqa: BLE001
        return []

    out: list[dict[str, Any]] = []
    for p in pois:
        nm = str(p.get("name", "")).strip()
        if not nm or nm == exclude_name or exclude_name in nm or nm in exclude_name:
            continue
        lng, _, lat = str(p.get("location", "")).partition(",")
        try:
            lng_f, lat_f = float(lng), float(lat)
        except ValueError:
            continue
        out.append(
            {
                "name": nm,
                "address": str(p.get("address", "")),
                "longitude": lng_f,
                "latitude": lat_f,
            }
        )
        if len(out) >= limit:
            break
    return out
