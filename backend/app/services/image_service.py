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
import threading
import time
from typing import Any

import httpx

from ..config import Settings
from ..models.schemas import TripPlan
from ..storage.cache import Cache
from .amap_pacer import pace
from .unsplash_service import UnsplashService

_REQUEST_INTERVAL = 0.4  # 高德 QPS 限流保护（个人 Key 搜索类约 3 QPS）
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


class ImageService:
    AMAP_PLACE_TEXT = "https://restapi.amap.com/v3/place/text"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.unsplash = UnsplashService(settings)
        # 两级缓存：Redis（跨重启共享）+ 进程内 dict（Redis 不可用时的兜底）
        self._redis = Cache(settings)
        self._cache: dict[str, str | None] = {}
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
        url = self._amap_photo(name, city)
        if url is None:
            url = self.unsplash.search_photo_url(f"{city} travel")
        if url:
            obj.image_url = url

    def _amap_photo(self, name: str, city: str) -> str | None:
        cache_key = f"{city}::{name}"
        redis_key = f"poi:photo:{hashlib.md5(cache_key.encode()).hexdigest()}"
        with self._lock:
            if cache_key in self._cache:
                return self._cache[cache_key]
        # L1: Redis（后端重启也不丢，节省高德配额）
        cached = self._redis.get(redis_key)
        if cached:
            url = None if cached == "-" else cached
            with self._lock:
                self._cache[cache_key] = url
            return url
        url = self._fetch_amap_photo(name, city)
        # 未命中也缓存为 "-"，避免反复消耗配额搜索无图 POI
        self._redis.set(redis_key, url or "-", _POI_CACHE_TTL)
        with self._lock:
            self._cache[cache_key] = url
        return url

    def _fetch_amap_photo(self, name: str, city: str) -> str | None:
        if not self.settings.amap_api_key:
            return None
        # Planner 给出的名称常带括号后缀（如"知味观（湖滨总店）"），原样搜可能命中差；
        # 先用主名（去括号）搜，再退回全名各试一次。
        primary = name.split("（")[0].split("(")[0].strip() or name
        for keyword in dict.fromkeys([primary, name]):
            url = self._search_photo(keyword, city, name)
            if url:
                return url
        return None

    def _search_photo(self, keyword: str, city: str, expect: str) -> str | None:
        body = None
        for attempt in range(3):
            pace(_REQUEST_INTERVAL)  # 调用前过全局节拍，最后一次调用后不再空等
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
        # 优先取名称能对上的 POI（搜索结果偶尔会插入相近 POI）
        for poi in pois:
            poi_name = str(poi.get("name", ""))
            if poi_name and (poi_name in expect or expect in poi_name):
                url = _first_photo(poi)
                if url:
                    return url
        for poi in pois:
            url = _first_photo(poi)
            if url:
                return url
        return None
