"""价格区间化服务（批次 C）。

把 Planner「编数」的酒店价格替换为真实「起价参考」：
- 酒店：调用可选的第三方聚合（StayAPI 携程广告最低价）取 advertised_from_price
  作为 price_from，并回填 price_source；失败/超时/未配置 → price_from=None（前端显示「起价未知」）。
- 门票 / 餐饮：无免费稳定源，不在此处取价（见 docs/next-iteration-2.md §1.3）。

设计要点：
- `stayapi_key` 未配置时直接跳过，不影响主流程（第三方源可能 503，仅作起价参考、绝不阻塞）；
- `get` 可注入，便于用 Fake 客户端做单测；
- 结果只写入 `Hotel.price_from / price_source`，不覆盖 Planner 的 `price_per_night` 估算，
  也不改动 `hotel_total`（预算口径仍在 `_finalize_plan` 统一说明）。
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

import httpx

from ..config import Settings
from ..models.schemas import TripPlan

logger = logging.getLogger(__name__)

SOURCE_CTRIP = "ctrip_advertised"  # 前端据此显示「来源：携程广告价」

HttpGet = Callable[..., Any]

# 价格字段的候选键：第三方的返回结构不稳定，按此顺序递归取第一个可解析为数字的值。
_PRICE_KEYS = ("advertised_from_price", "lowest_price", "price", "amount")


def _first_number(value: Any) -> Optional[float]:
    """从可能是嵌套 dict/list 的结构里取第一个可解析为数字的价格。"""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", ""))
        except ValueError:
            return None
    if isinstance(value, dict):
        for k in _PRICE_KEYS:
            if k in value:
                found = _first_number(value[k])
                if found is not None:
                    return found
        for v in value.values():
            found = _first_number(v)
            if found is not None:
                return found
    if isinstance(value, (list, tuple)):
        for v in value:
            found = _first_number(v)
            if found is not None:
                return found
    return None


def fetch_hotel_price_from(
    name: str,
    city: str,
    key: str,
    base_url: str,
    get: HttpGet = httpx.get,
) -> tuple[Optional[float], str]:
    """查询酒店起价，返回 (price_from, price_source)。

    key 为空、网络异常、结构不含价格 → 返回 (None, "")，调用方降级为「起价未知」。
    """
    if not key:
        return None, ""
    url = base_url.rstrip("/") + "/v1/ctrip/hotel/rooms"
    try:
        resp = get(
            url,
            params={"hotel_name": name, "city": city, "token": key},
            headers={"Content-Type": "application/json"},
            timeout=8.0,
        )
        data = resp.json()
    except Exception:  # noqa: BLE001  第三方源不稳定，静默降级
        logger.warning("酒店起价查询失败（%s / %s）", city, name, exc_info=True)
        return None, ""
    price = _first_number(data)
    if price is None or price <= 0:
        return None, ""
    return price, SOURCE_CTRIP


def enrich_hotel_prices(
    plan: TripPlan,
    settings: Settings,
    get: HttpGet = httpx.get,
) -> int:
    """为行程内酒店填充真实起价（按酒店名去重），返回成功填充的酒店数。"""
    if not settings.stayapi_key:
        return 0
    enriched = 0
    seen: set[str] = set()
    for day in plan.daily_plans:
        hotel = day.hotel
        if not hotel or hotel.name in seen:
            continue
        seen.add(hotel.name)
        price, source = fetch_hotel_price_from(
            hotel.name, plan.destination, settings.stayapi_key, settings.stayapi_base_url, get
        )
        if price is not None:
            hotel.price_from = price
            hotel.price_source = source
            enriched += 1
    return enriched