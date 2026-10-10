"""图片增强服务：为行程中的景点/餐厅/酒店补充配图。

三层策略：
1. 高德 POI 搜索自带的实景照片（与 POI 一一对应，主方案，免费额度内零成本）
2. Unsplash 氛围图（可选，需配置 UNSPLASH_ACCESS_KEY）
3. 两者都拿不到时由前端显示渐变占位图（保底）

图片增强是数据增强步骤而非智能决策，按书中设计不封装为 Tool，
由 API 层在 PlannerAgent 输出后直接调用。使用与 MCP 相同的
「Web 服务」Key 直连高德 place/text REST 接口。

注意：高德个人 Key 有 QPS 限制（搜索类约 3 次/秒），实测并发请求
会触发 CUQPS_HAS_EXCEEDED_THE_LIMIT 限流，因此真实 HTTP 请求前统一过
amap_pacer.pace() 全局节拍（与校验器/MCP 调用共享同一计拍，谁调谁排队）；
进程内缓存可减少同行程内的重复请求。
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any

import httpx

from ..config import Settings
from ..models.schemas import Location, TripPlan
from ..storage.cache import Cache
from .amap_pacer import pace
from .unsplash_service import UnsplashService

_POI_CACHE_TTL = 7 * 24 * 3600  # POI 图片地址缓存 7 天


def _https(url: str) -> str:
    """高德部分图片链接是 http，页面上会因混合内容被浏览器拦截，统一升级 https。"""
    return "https://" + url[len("http://"):] if url.startswith("http://") else url


def _first_photo(poi: dict) -> str | None:
    """从 POI 的 photos 字段取第一张图；兼容 dict/list 两种返回结构。"""
    photos = poi.get("photos") or []
    if isinstance(photos, dict):
        photos = [photos] if photos.get("url") else list(photos.values())
    for ph in photos:
        if isinstance(ph, dict):
            url = ph.get("url")
            if url:
                return _https(url)
    return None


def _parse_location(raw: Any) -> Location | None:
    """"116.487585,39.991754" → Location。"""
    try:
        lng, lat = str(raw).split(",")
        return Location(longitude=float(lng), latitude=float(lat))
    except (ValueError, TypeError):
        return None


def _decode_cached_poi(raw: str) -> dict | None:
    """解码 Redis 缓存值，兼容历史格式：新版 JSON 记录 / 纯图片 URL / "-"（无结果）。"""
    if raw == "-":
        return None
    try:
        rec = json.loads(raw)
        if isinstance(rec, dict) and ("photo" in rec or "location" in rec):
            return rec
    except ValueError:
        pass
    return {"photo": raw, "location": None}  # 旧格式：只缓存了图片地址


class ImageService:
    AMAP_PLACE_TEXT = "https://restapi.amap.com/v3/place/text"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.unsplash = UnsplashService(settings)
        # 两级缓存：Redis（跨重启共享）+ 进程内 dict（Redis 不可用时的兜底）
        self._redis = Cache(settings)
        self._cache: dict[str, dict | None] = {}
        self._lock = threading.Lock()

    # ---------- 对外入口 ----------

    def enrich(self, plan: TripPlan) -> None:
        """为行程中所有景点/餐厅/酒店填充 image_url（全局节拍限速 + 缓存）。"""
        targets: list[tuple[Any, str]] = []
        for day in plan.daily_plans:
            for a in day.attractions:
                targets.append((a, a.name))
            for m in day.meals:
                targets.append((m, m.restaurant))
            if day.hotel:
                targets.append((day.hotel, day.hotel.name))
        for obj, name in targets:
            try:
                self._fill(obj, name, plan.destination)
            except Exception:  # noqa: BLE001  单张图失败不影响整体
                pass

    # ---------- 内部实现 ----------

    def _fill(self, obj: Any, name: str, city: str) -> None:
        info = self._amap_poi(name, city)
        url = info.get("photo") if info else None
        if url is None:
            url = self.unsplash.search_photo_url(f"{city} travel")
        if url:
            obj.image_url = url
        # 顺手富化坐标：餐厅等 Planner 不产出坐标的对象由此获得真实 POI 位置（前端导航用）
        if info and info.get("location") and getattr(obj, "location", None) is None:
            obj.location = _parse_location(info["location"])

    def search_photo(self, name: str, city: str) -> str | None:
        """按名称搜索单个 POI 的配图地址（手动添加景点等单点场景）。

        高德实景图优先，Unsplash 氛围图兜底；复用与 enrich 相同的两级缓存与全局限速。
        """
        if not name:
            return None
        info = self._amap_poi(name, city)
        url = info.get("photo") if info else None
        if url is None:
            url = self.unsplash.search_photo_url(name) or (
                self.unsplash.search_photo_url(f"{city} travel") if city else None
            )
        return url

    def _amap_poi(self, name: str, city: str) -> dict | None:
        """POI 搜索结果 {"photo": 图片地址|None, "location": "lng,lat"|None}；未搜到返回 None。"""
        cache_key = f"{city}::{name}"
        redis_key = f"poi:photo:{hashlib.md5(cache_key.encode()).hexdigest()}"
        with self._lock:
            if cache_key in self._cache:
                return self._cache[cache_key]
        # L1: Redis（后端重启也不丢，节省高德配额）
        cached = self._redis.get(redis_key)
        if cached:
            info = _decode_cached_poi(cached)
        else:
            info = self._fetch_amap_poi(name, city)
            # 未命中也缓存为 "-"，避免反复消耗配额搜索无图 POI
            self._redis.set(redis_key, json.dumps(info, ensure_ascii=False) if info else "-", _POI_CACHE_TTL)
        with self._lock:
            self._cache[cache_key] = info
        return info

    def _fetch_amap_poi(self, name: str, city: str) -> dict | None:
        if not self.settings.amap_api_key:
            return None
        # Planner 给出的名称常带括号后缀（如"知味观（湖滨总店）"），原样搜可能命中差；
        # 先用主名（去括号）搜，再退回全名各试一次。
        primary = name.split("（")[0].split("(")[0].strip() or name
        best_loc: str | None = None
        for keyword in dict.fromkeys([primary, name]):
            poi = self._search_poi(keyword, city, name)
            if not poi:
                continue
            url = _first_photo(poi)
            loc = poi.get("location")
            if url:
                return {"photo": url, "location": loc or best_loc}
            if loc and best_loc is None:
                best_loc = loc
        return {"photo": None, "location": best_loc} if best_loc else None

    def _search_poi(self, keyword: str, city: str, expect: str) -> dict | None:
        """place/text 搜索：优先名称匹配且有图的 POI，其次有图的 POI，再次名称匹配的 POI。"""
        body = None
        for attempt in range(3):
            pace()  # 调用前过全局节拍（间隔由 Settings.amap_qps 派生），最后一次调用后不再空等
            try:
                resp = httpx.get(
                    self.AMAP_PLACE_TEXT,
                    params={
                        "key": self.settings.amap_api_key,
                        "keywords": keyword,
                        "city": city,
                        "citylimit": "true",
                        "offset": 10,
                        "page": 1,
                    },
                    timeout=6.0,
                )
                resp.raise_for_status()
                body = resp.json()
            except (httpx.HTTPError, ValueError):
                return None
            if body.get("status") == "1":
                break
            # QPS/配额限流：指数退避后重试（规划 Agent 刚用过同一个 Key，容易触发）
            if "CUQPS" in str(body.get("info", "")).upper() or "LIMIT" in str(body.get("info", "")).upper():
                time.sleep(1.0 + attempt * 1.5)
                continue
            return None  # 其他业务错误（如 key 无效）不重试
        if not body or body.get("status") != "1":
            return None
        pois = body.get("pois") or []
        if not pois:
            return None
        # 名称匹配判定：搜索结果偶尔会插入相近 POI，名称能对上的优先
        def _matched(poi: dict) -> bool:
            poi_name = str(poi.get("name", ""))
            return bool(poi_name) and (poi_name in expect or expect in poi_name)

        for poi in pois:
            if _matched(poi) and _first_photo(poi):
                return poi
        for poi in pois:
            if _first_photo(poi):
                return poi
        for poi in pois:
            if _matched(poi):
                return poi  # 无图但名称对上：坐标仍可用于导航富化
        return None
