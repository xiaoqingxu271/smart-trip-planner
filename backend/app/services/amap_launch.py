"""高德唤端链接（批次 D）。

用高德官方 URI API（https://lbs.amap.com/api/uri-api）生成确定的唤端链接，
零网络调用、零配额消耗：
- 单点导航：uri.amap.com/navigation?to=lng,lat,name —— PC 网页版 / 手机唤起高德 APP；
- 一键看地图：uri.amap.com/marker?position=lng,lat —— 定位到目的地/首站。

注意：文档中「生成专属地图（多 POI 导入高德 APP）」来自**官方** MCP Server
（lbs.amap.com/api/mcp-server/summary），需另行接入官方 MCP 客户端；本项目当前
mcp_server_command 是社区搜索包，不含该唤端能力，故本模块只提供 URI API 兜底：
`amap_map_url` 为「打开高德地图看目的地」，`nav_url` 为「导航到指定点」。
"""
from __future__ import annotations

from urllib.parse import quote

from ..config import Settings
from ..models.schemas import TripPlan

# 高德 URI API 的 src 参数：应用标识（唤起高德时透传的调用方名）
_SRC = "smart-trip-planner"


def build_nav_url(name: str, longitude: float, latitude: float) -> str:
    """导航到指定点（GCJ-02 坐标，无需转换）。"""
    to = f"{longitude},{latitude},{quote(name)}"
    return f"https://uri.amap.com/navigation?to={to}&mode=car&src={_SRC}&coordinate=gaode"


def build_marker_url(name: str, longitude: float, latitude: float) -> str:
    """在指定点打标记并打开高德地图。"""
    return (
        f"https://uri.amap.com/marker?position={longitude},{latitude}"
        f"&name={quote(name)}&src={_SRC}&coordinate=gaode"
    )


def apply_launch_links(plan: TripPlan, _settings: Settings) -> None:
    """回填 nav_url（每个景点）与 amap_map_url（整份行程）。纯确定性、就地改写。"""
    first: tuple[str, float, float] | None = None
    for day in plan.daily_plans:
        for attr in day.attractions:
            if not attr.location:
                continue
            if attr.nav_url == "":
                attr.nav_url = build_nav_url(attr.name, attr.location.longitude, attr.location.latitude)
            if first is None:
                first = (attr.name, attr.location.longitude, attr.location.latitude)
    if first is not None and plan.amap_map_url == "":
        plan.amap_map_url = build_marker_url(*first)