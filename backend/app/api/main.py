"""FastAPI 应用入口。

启动（在 backend 目录下）::

    uvicorn app.api.main:app --reload

交互式文档: http://localhost:8000/docs
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import quote, urlparse

logger = logging.getLogger(__name__)

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..agents.mcp_tool import MCPError
from ..agents.trip_planner import PlannerError, get_planner
from ..agents.validator import _get_poi_info
from ..config import get_settings
from ..models.schemas import (
    Budget,
    Location,
    ReplanBody,
    TripPlan,
    TripRequest,
    TripSummary,
)
from ..services.demo_data import build_demo_plan
from ..services.cleanup import cleanup_stale_files
from ..services.ical_service import plan_to_ics
from ..services.image_service import ImageService
from ..services.route_service import compute_day_routes
from ..services.schedule_service import schedule_plan
from ..storage.cache import Cache
from ..storage.db import StorageUnavailable, ensure_database
from ..storage.trip_store import TripStore
from .access import AccessCodeMiddleware, UserAuthMiddleware
from .auth import router as auth_router
from .rate_limit import RateLimitMiddleware

settings = get_settings()

# 应用日志：stdout + 文件轮转（logs/app.log，单文件 5MB × 3 份）
_LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
_file_handler = RotatingFileHandler(_LOG_DIR / "app.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
_file_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT, handlers=[logging.StreamHandler(), _file_handler])

app = FastAPI(
    title="智能旅行助手 API",
    description="基于 HelloAgents 多智能体框架 + 高德地图 MCP 的行程规划服务",
    version="1.0.0",
    # 开启访问码鉴权时关闭公开文档，避免对外暴露 API 结构
    docs_url=None if settings.app_password else "/docs",
    redoc_url=None if settings.app_password else "/redoc",
    openapi_url=None if settings.app_password else "/openapi.json",
)

# Agent 流水线全程串行且耗时（10-60s），放到独立线程池里跑，避免阻塞事件循环；
# 与 IO 线程池分离——图片下载/DB 操作被刷时不会饿死规划流水线
_pipeline_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="plan")
_io_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="io")

# 规划并发闸门：流水线只有 2 个 worker，与其让第 3 个请求无限排队
# （前端 axios 120s 就超时断开，后端白排队），不如快速失败
_plan_gate = threading.Semaphore(2)

# 中间件注册顺序 = 后注册者在外层：鉴权（最内，401 也消耗限流配额，
# 抑制暴力枚举）→ 限流 → CORS（最外，429/401 响应都要补上跨域头，浏览器才能读到）
# 访问码与多用户互斥：AUTH_MODE=user 时登录体系取代访问码
if settings.app_password and not settings.user_auth_enabled:
    app.add_middleware(AccessCodeMiddleware, password=settings.app_password)
app.add_middleware(UserAuthMiddleware, settings=settings, cache=Cache(settings))
app.add_middleware(RateLimitMiddleware, settings=settings, executor=_io_executor)

app.include_router(auth_router)


def _uid(http: Request) -> int | None:
    """当前登录用户（AUTH_MODE=user 时由中间件解析；其余模式恒为 None）。"""
    return getattr(http.state, "user_id", None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 图片增强服务（含 POI 照片缓存），首个真实请求时惰性创建
_image_service: ImageService | None = None
_image_service_lock = threading.Lock()

# 图片增强服务（含 POI 照片缓存），首个真实请求时惰性创建
_image_service: ImageService | None = None

# 行程持久化（MySQL）与缓存（Redis），启动时初始化
_trip_store: TripStore | None = None
_cache: Cache | None = None
_store_lock = threading.Lock()


def _get_store() -> TripStore | None:
    global _trip_store, _cache
    # 双重检查：并发首调时确保只有一个 TripStore 实例、只跑一遍建库 DDL
    if _trip_store is None:
        with _store_lock:
            if _trip_store is None:
                try:
                    ensure_database(settings)
                    _trip_store = TripStore(settings)
                    _cache = Cache(settings)
                except Exception:  # noqa: BLE001
                    logger.warning("存储初始化失败（历史/作品集功能降级）", exc_info=True)
                    return None
    return _trip_store

# 演示模式下也接受用户传入的日期，保证体验一致


@app.get("/api/health")
async def health():
    from ..storage.cache import check as redis_check
    from ..storage.db import check as mysql_check

    def _checks() -> tuple[bool, bool]:
        return mysql_check(settings), redis_check(settings)

    demo = settings.demo_mode or not settings.ready_for_agents
    # 探测含网络 I/O（Redis 超时 1.5s、MySQL 建连），放线程池避免阻塞事件循环
    mysql_ok, redis_ok = await asyncio.get_running_loop().run_in_executor(_io_executor, _checks)
    return {
        "status": "ok",
        "demo_mode": demo or not settings.ready_for_agents,
        "auth_required": bool(settings.app_password),
        "auth_mode": settings.auth_mode,
        "mysql": mysql_ok,
        "redis": redis_ok,
        "message": "" if settings.ready_for_agents or settings.demo_mode
        else "未配置 LLM_API_KEY / AMAP_API_KEY，将自动进入演示模式；请在 backend/.env 中配置。",
    }


class AppConfig(BaseModel):
    amap_js_key: str
    amap_js_secret: str
    demo_mode: bool


@app.get("/api/config", response_model=AppConfig)
async def get_app_config():
    """前端地图初始化所需的公开配置（JS API Key 本身就是浏览器端公开的）。"""
    demo = settings.demo_mode or not settings.ready_for_agents
    return AppConfig(
        amap_js_key=settings.amap_js_key,
        amap_js_secret=settings.amap_js_secret,
        demo_mode=demo,
    )


class GeocodeRequest(BaseModel):
    address: str
    city: str = ""


class GeocodeResponse(BaseModel):
    longitude: float
    latitude: float
    formatted_address: str


# ---------- 图片代理 ----------

# 高德图片 CDN 偶发防盗链/混合内容问题，经同源代理转发并落盘缓存，
# 前端 <img> 与 html2canvas 导出都更稳定。
# 注意：高德 photos 里除 autonavi/amap 自有图床外，还常见口碑商户图（alicdn.com）
_IMAGE_CACHE_DIR = Path(__file__).resolve().parents[2] / ".image_cache"
_IMAGE_HOST_SUFFIXES = ("autonavi.com", "amap.com", "alicdn.com")
_MAX_IMAGE_REDIRECTS = 3

_IMAGE_MAGICS = (  # (magic bytes, content-type)
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"RIFF", "image/webp"),
)


def _sniff_image(data: bytes) -> str | None:
    for magic, ctype in _IMAGE_MAGICS:
        if data.startswith(magic):
            return ctype
    return None


def _is_allowed_image_host(url: str) -> bool:
    if not url.lower().startswith(("https://", "http://")):
        return False
    host = urlparse(url).hostname or ""
    return any(host == s or host.endswith("." + s) for s in _IMAGE_HOST_SUFFIXES)


@app.get("/api/utils/image")
async def proxy_image(u: str):
    """代理高德图片：白名单域名落盘缓存后转发；未知图床直接 400。

    不做 302 直连降级——那等于开放重定向，本域会被当钓鱼跳板用。
    """
    if not _is_allowed_image_host(u):
        raise HTTPException(status_code=400, detail="仅支持高德系图床（autonavi/amap/alicdn）的图片地址")

    cache_key = hashlib.md5(u.encode()).hexdigest()
    cache_file = _IMAGE_CACHE_DIR / f"{cache_key}.bin"
    type_file = _IMAGE_CACHE_DIR / f"{cache_key}.type"
    if cache_file.exists() and type_file.exists():
        return FileResponse(
            cache_file,
            media_type=type_file.read_text(encoding="ascii"),
            headers={"Cache-Control": "public, max-age=604800"},
        )

    def _download() -> tuple[bytes, str]:
        url = u
        # 手动跟随重定向：每一跳都重新校验白名单，防止白名单域 302 到内网/任意地址
        for _ in range(_MAX_IMAGE_REDIRECTS + 1):
            try:
                resp = httpx.get(
                    url,
                    headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.amap.com/"},
                    timeout=15.0,
                    follow_redirects=False,
                )
            except httpx.HTTPError:
                raise HTTPException(status_code=502, detail="图片源请求失败")
            if resp.status_code in (301, 302, 303, 307, 308):
                loc = resp.headers.get("location")
                if not loc:
                    raise HTTPException(status_code=502, detail="图片源重定向缺少地址")
                url = str(httpx.URL(url).join(loc))
                if not _is_allowed_image_host(url):
                    logger.warning("图片源重定向到非白名单域名，已拦截: %s -> %s", u, url)
                    raise HTTPException(status_code=400, detail="图片源重定向到非白名单域名")
                continue
            if resp.status_code != 200:
                raise HTTPException(status_code=404, detail="图片不存在")
            # 高德部分 CDN 把 content-type 标成 octet-stream，用魔数识别真实类型
            ctype = _sniff_image(resp.content)
            if ctype is None:
                raise HTTPException(status_code=404, detail="响应不是有效图片")
            return resp.content, ctype
        raise HTTPException(status_code=502, detail="图片源重定向次数过多")

    loop = asyncio.get_running_loop()
    content, ctype = await loop.run_in_executor(_io_executor, _download)

    try:
        _IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_bytes(content)
        type_file.write_text(ctype, encoding="ascii")
    except OSError:
        pass  # 缓存写入失败不影响返回

    return Response(
        content=content,
        media_type=ctype,
        headers={"Cache-Control": "public, max-age=604800"},
    )


@app.post("/api/utils/geocode", response_model=GeocodeResponse)
async def geocode(req: GeocodeRequest):
    """地理编码：供前端"添加景点"时把地址转成坐标。"""
    if settings.demo_mode or not settings.ready_for_agents:
        raise HTTPException(status_code=503, detail="演示模式下不支持地理编码，请配置密钥后使用真实模式。")
    planner = get_planner(settings)

    def _run() -> GeocodeResponse:
        try:
            text = planner.mcp_tool.client.call_tool(
                "maps_geo", {"address": req.address, "city": req.city}
            )
        except MCPError as e:
            raise PlannerError(str(e)) from e
        import json as _json

        try:
            data = _json.loads(text)
            geocodes = data.get("geocodes", [])
            if not geocodes:
                raise PlannerError("未查询到该地址的坐标，请更换描述后重试。")
            lng, lat = geocodes[0]["location"].split(",")
            return GeocodeResponse(
                longitude=float(lng),
                latitude=float(lat),
                formatted_address=geocodes[0].get("formatted_address", req.address),
            )
        except (ValueError, KeyError, TypeError) as e:
            raise PlannerError(f"地理编码结果解析失败: {e}") from e

    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(_io_executor, _run)
    except PlannerError as e:
        raise HTTPException(status_code=400, detail=str(e))


class PoiResolveResponse(BaseModel):
    name: str
    longitude: float
    latitude: float
    address: str = ""


@app.get("/api/utils/poi", response_model=PoiResolveResponse)
async def resolve_poi(keyword: str, city: str = ""):
    """POI 名称 → 坐标（导航兜底：餐厅/酒店无坐标时前端点击实时解析）。

    与地理编码相比，POI 搜索对餐厅/酒店等门店名命中率更高；
    名称常带括号后缀（如"全聚德（前门店）"），主名命中率更高。
    """
    if settings.demo_mode or not settings.ready_for_agents:
        raise HTTPException(status_code=503, detail="演示模式下不支持 POI 解析，请配置密钥后使用真实模式。")
    if not keyword.strip():
        raise HTTPException(status_code=400, detail="keyword 不能为空")
    planner = get_planner(settings)

    def _run() -> PoiResolveResponse:
        primary = keyword.split("（")[0].split("(")[0].strip() or keyword
        params: dict = {"keywords": primary}
        if city:
            params["city"] = city
            params["citylimit"] = "true"
        try:
            text = planner.mcp_tool.client.call_tool("maps_text_search", params)
        except MCPError as e:
            raise PlannerError(str(e)) from e
        try:
            data = json.loads(text)
            pois = data.get("pois") or []
            if not pois:
                raise PlannerError("未查询到该地点，可在高德地图中手动搜索。")
            poi = pois[0]
            lng, lat = str(poi["location"]).split(",")
            return PoiResolveResponse(
                name=poi.get("name", keyword),
                longitude=float(lng),
                latitude=float(lat),
                address=poi.get("address", "") or "",
            )
        except (ValueError, KeyError, TypeError) as e:
            raise PlannerError(f"POI 搜索结果解析失败: {e}") from e

    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(_io_executor, _run)
    except PlannerError as e:
        raise HTTPException(status_code=400, detail=str(e))


RESULT_TTL = 24 * 3600  # plan:result:{hash} 结果缓存 24h


def _request_hash(request: TripRequest) -> str:
    """规划请求指纹。start_date 为空时按"今天"归一化——否则空日期的请求
    今天和明天哈希相同，会命中过期天气的缓存。"""
    src = request.model_dump_json()
    if not request.start_date:
        src = request.model_copy(update={"start_date": date.today().isoformat()}).model_dump_json()
    return hashlib.md5(src.encode()).hexdigest()


class PlanJobError(Exception):
    """规划编排中前端可见的失败（status + 可读消息）。"""

    def __init__(self, status: int, detail: str):
        self.status = status
        self.detail = detail
        super().__init__(detail)


_DEMO_STAGES = [
    ("started", "演示模式开始", 0.2),
    ("attractions", "景点搜索完成，共 12 个候选（演示）", 0.4),
    ("weather", "目的地天气预报已获取（演示）", 0.4),
    ("hotel", "酒店候选已就绪（演示）", 0.4),
    ("planning", "正在整合候选信息，规划每日行程…", 0.5),
    ("planned", "行程初稿已生成", 0.2),
    ("validating", "正在校验行程合理性…", 0.2),
    ("validated", "校验通过", 0.1),
    ("images", "正在补充实景配图", 0.2),
]


def _finalize_plan(request: TripRequest, plan: TripPlan) -> None:
    """确定性后处理（批次 1.2/1.3/1.4）：就地改写 plan。

    1. 真实路线 → 市内交通费回写（taxi/drive 才按打车估价；walk/transit 计 0）；
    2. 门票合计 = Σ ticket_price（确定性）；
    3. 一订到底时住宿合计 = 单价 × (天数-1)；
    4. 钟点调度器填写每处景点 start_time/end_time，闭园/最晚入园超时写入 warnings；
    5. budget_note 按交通方式选择口径说明。
    """
    try:
        routes = compute_day_routes(settings.amap_api_key, _cache, plan)
    except Exception:  # noqa: BLE001
        logger.warning("路线计算失败，交通费与钟点采用回退口径", exc_info=True)
        routes = {"routes": []}
    route_by_day = {r["day"]: r for r in routes.get("routes", [])}
    taxi_sum = round(sum(r.get("taxi_cost") or 0 for r in route_by_day.values()), 2)

    if plan.budget is None:
        plan.budget = Budget()
    plan.budget.attraction_total = round(
        sum(a.ticket_price for d in plan.daily_plans for a in d.attractions), 2
    )

    # 酒店合计：全程同一酒店时按 单价 × 晚数 重写（晚数 = 天数-1）
    hotels = [d.hotel for d in plan.daily_plans if d.hotel]
    names = {h.name for h in hotels if h}
    if len(names) == 1:
        nights = max(1, plan.days - 1)
        plan.budget.hotel_total = round(hotels[0].price_per_night * nights, 2)

    # 市内交通费按方式回写
    if request.transit_mode in ("taxi", "drive"):
        plan.budget.transport_total = taxi_sum
        plan.budget_note = "门票/住宿/餐饮为模型估算，交通为高德打车估价"
    elif request.transit_mode == "walk":
        plan.budget.transport_total = 0
        plan.budget_note = "门票/住宿/餐饮为模型估算，交通按步行计（未计打车）"
    else:  # transit
        plan.budget.transport_total = 0
        plan.budget_note = "门票/住宿/餐饮为模型估算，交通为公交/地铁（费用未计入）"

    items_sum = (
        plan.budget.attraction_total + plan.budget.hotel_total
        + plan.budget.meal_total + plan.budget.transport_total
    )
    if items_sum > 0:
        plan.budget.grand_total = round(items_sum, 2)

    # 开园时间富化（复用校验器缓存，零额外高德开销）＋ 钟点调度
    open_times: dict[str, str] = {}
    try:
        planner = get_planner(settings)
        for d in plan.daily_plans:
            for a in d.attractions:
                info = _get_poi_info(planner.mcp_tool.client, planner._cache, a.name, plan.destination)
                if info and info.get("opentime"):
                    open_times[a.name] = info["opentime"]
                    a.open_time_text = info["opentime"]
    except Exception:  # noqa: BLE001
        logger.warning("开园时间核验失败，仅按钟点排程", exc_info=True)
    sched_warnings = schedule_plan(plan, request, route_by_day, open_times)
    for w in sched_warnings:
        if w not in plan.warnings:
            plan.warnings.append(w)


def _plan_execute(request: TripRequest, progress=None, user_id: int | None = None) -> TripPlan:
    """规划执行主体：结果缓存 → 流水线 → 图片增强 → 入库 → 结果写缓存。

    并发闸门与规划锁由调用方持有；同步阻塞，必须在 _pipeline_executor 中运行。
    progress(stage, message, extra) 透传各阶段真实进度。
    """
    def notify(stage, message, extra=None):
        if progress:
            try:
                progress(stage, message, extra)
            except Exception:  # noqa: BLE001
                logger.warning("进度回调异常（忽略）", exc_info=True)

    request_hash = _request_hash(request)
    result_key = f"plan:result:{request_hash}"
    store = _get_store()

    # 结果缓存命中：直接返回上次成功结果（已含图片增强与 trip_id），零成本
    if store and _cache:
        cached = _cache.get(result_key)
        if cached:
            try:
                logger.info("结果缓存命中: %s", result_key)
                notify("started", "结果缓存命中，直接复用上次规划")
                return TripPlan.model_validate(json.loads(cached))
            except Exception:  # noqa: BLE001
                logger.warning("结果缓存解析失败，重新规划", exc_info=True)

    plan = get_planner(settings).plan(request, progress=notify)

    notify("images", "正在为景点/餐厅/酒店补充实景配图")
    try:
        _enrich_images(plan)
    except Exception:  # noqa: BLE001
        logger.warning("图片增强失败（跳过）", exc_info=True)

    # 确定性后处理：真实路线 → 市内交通费/钟点回写；门票合计求和（批次 1.2/1.3/1.4）
    try:
        _finalize_plan(request, plan)
    except Exception:  # noqa: BLE001
        logger.warning("确定性后处理失败（保留估算口径）", exc_info=True)

    # 自动入库（失败不影响返回，历史功能降级）
    if store:
        try:
            plan.trip_id = store.save(request, plan, user_id)
        except StorageUnavailable:
            logger.warning("MySQL 不可用，本次行程未入历史库")
        # 结果入缓存（已含增强图与 trip_id；写失败不影响返回）
        _cache.set(result_key, plan.model_dump_json(), RESULT_TTL)
    return plan


def _plan_full(request: TripRequest, progress=None, user_id: int | None = None) -> TripPlan:
    """同步完整规划：demo 分支 + 结果缓存 + 并发闸门 + 规划锁 + 执行。"""
    demo = settings.demo_mode or not settings.ready_for_agents
    if demo:
        if progress:
            for stage, message, delay in _DEMO_STAGES:
                progress(stage, message)
                time.sleep(delay)
        return build_demo_plan(_resolve_dates(request))

    lock_key = f"plan:lock:{_request_hash(request)}"
    store = _get_store()

    # 并发闸门：拿不到许可快速失败；拿到则无论结果如何都在 finally 释放
    if not _plan_gate.acquire(blocking=False):
        raise PlanJobError(429, "系统正在规划其他行程，请稍后再试。")
    lock_token: str | None = None
    try:
        if store and _cache:
            allowed, lock_token = _cache.acquire_lock(lock_key, 300)
            if not allowed:
                raise PlanJobError(409, "相同的规划请求正在处理中，请稍候再试。")
        return _plan_execute(request, progress, user_id)
    except PlanJobError:
        raise
    except PlannerError as e:
        # PlannerError 是面向用户的可读消息（前端一直按此展示）；异常链进服务端日志
        logger.warning("规划流水线失败: %s", e, exc_info=e.__cause__)
        raise PlanJobError(500, str(e)) from e
    except Exception:  # noqa: BLE001
        # 未预期异常不外泄内部细节，详情看服务端日志
        logger.exception("行程规划未预期失败")
        raise PlanJobError(500, "行程规划失败，请稍后重试（详情见服务端日志）") from None
    finally:
        _plan_gate.release()
        if store:
            _cache.release_lock(lock_key, lock_token)


@app.post("/api/trip/plan", response_model=TripPlan)
async def create_trip_plan(request: TripRequest, http: Request):
    """同步生成完整行程计划（连接挂住直到完成；前端建议用 /async 版本）。

    - 未配置密钥或 DEMO_MODE=true 时返回演示数据；
    - 相同请求 24h 内有成功结果时直接复用（结果缓存，含图片增强）；
    - 相同请求正在规划中时 409；系统繁忙时 429。
    """
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(_pipeline_executor, _plan_full, request, _uid(http))
    except PlanJobError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)


def _job_key(job_id: str) -> str:
    return f"plan:job:{job_id}"


def _write_job(job_id: str, state: dict) -> None:
    """任务状态写 Redis（TTL 与结果缓存一致，24h 自动过期）。"""
    if _cache:
        _cache.set(_job_key(job_id), json.dumps(state, ensure_ascii=False), RESULT_TTL)


@app.post("/api/trip/plan/async")
async def submit_plan_job(request: TripRequest, http: Request):
    """异步规划：立即返回 job_id，进度与结果经 GET /api/trip/jobs/{job_id} 轮询。

    - 客户端断开不影响任务执行；结果 24h 内可取（Redis）；
    - 限流、闸门、规划锁冲突在提交时同步以 HTTP 状态码返回（429/409）；
    - Redis 不可用时降级为同步执行：响应 {"mode": "sync", "plan": {...}}。
    """
    demo = settings.demo_mode or not settings.ready_for_agents
    loop = asyncio.get_running_loop()
    store = await loop.run_in_executor(_io_executor, _get_store)

    # Redis 不可用：任务队列无从谈起，降级为同步执行（保持旧体验）
    if _cache is None:
        try:
            plan = await loop.run_in_executor(_pipeline_executor, _plan_full, request)
            return {"mode": "sync", "plan": plan}
        except PlanJobError as e:
            raise HTTPException(status_code=e.status, detail=e.detail)

    # 闸门与规划锁在提交时同步获取：冲突立即反馈；由后台任务持有至执行结束
    lock_token: str | None = None
    if not demo:
        if not _plan_gate.acquire(blocking=False):
            raise HTTPException(status_code=429, detail="系统正在规划其他行程，请稍后再试。")
        if store and _cache:
            allowed, lock_token = await loop.run_in_executor(
                _io_executor, _cache.acquire_lock, f"plan:lock:{_request_hash(request)}", 300
            )
            if not allowed:
                _plan_gate.release()
                raise HTTPException(status_code=409, detail="相同的规划请求正在处理中，请稍候再试。")

    user_id = _uid(http)
    job_id = secrets.token_hex(12)
    _write_job(job_id, {"job_id": job_id, "status": "pending", "message": "已受理，等待调度", "user_id": user_id})

    def progress(stage: str, message: str, extra: dict | None = None) -> None:
        _write_job(job_id, {"job_id": job_id, "status": "running", "stage": stage, "message": message, **(extra or {})})

    def run() -> None:
        try:
            if demo:
                for stage, message, delay in _DEMO_STAGES:
                    progress(stage, message)
                    time.sleep(delay)
                plan = build_demo_plan(_resolve_dates(request))
            else:
                plan = _plan_execute(request, progress, user_id)
            _write_job(job_id, {"job_id": job_id, "status": "succeeded", "stage": "done",
                                "message": "规划完成", "plan": plan.model_dump(), "user_id": user_id})
        except PlanJobError as e:
            _write_job(job_id, {"job_id": job_id, "status": "failed", "error": e.detail, "user_id": user_id})
        except Exception:  # noqa: BLE001
            logger.exception("异步规划未预期失败")
            _write_job(job_id, {"job_id": job_id, "status": "failed",
                                "error": "行程规划失败，请稍后重试（详情见服务端日志）"})
        finally:
            if not demo:
                _plan_gate.release()
                if lock_token:
                    _cache.release_lock(f"plan:lock:{_request_hash(request)}", lock_token)

    loop.run_in_executor(_pipeline_executor, run)
    return {"mode": "job", "job_id": job_id}


_JOB_ID_RE = re.compile(r"^[0-9a-f]{24}$")


@app.get("/api/trip/jobs/{job_id}")
async def get_plan_job(job_id: str, http: Request):
    """查询异步规划任务状态。

    pending/running（含 stage/message/count）→ succeeded（含完整 plan）/ failed（含 error）。
    绑定了用户的任务仅本人可读。
    """
    if _cache is None:
        raise HTTPException(status_code=503, detail="任务状态存储（Redis）不可用")
    if not _JOB_ID_RE.fullmatch(job_id):
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    raw = await asyncio.get_running_loop().run_in_executor(_io_executor, _cache.get, _job_key(job_id))
    if not raw:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    state = json.loads(raw)
    owner = state.get("user_id")
    if owner is not None and owner != _uid(http):
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return state


# ---------- 历史与作品集 ----------

def _get_store_or_503() -> TripStore:
    store = _get_store()
    if store is None:
        raise HTTPException(status_code=503, detail="存储不可用，历史/作品集功能暂不可用。")
    return store


@app.get("/api/trip/history", response_model=list[TripSummary])
async def list_history(http: Request, filter: str = "recent", limit: int = 12):
    """行程卡片列表。filter: recent(我的历史) | starred(我的收藏) | seed(示例作品)"""
    user_id = _uid(http)
    if filter not in ("recent", "starred", "seed"):
        raise HTTPException(status_code=400, detail="filter 仅支持 recent/starred/seed")
    limit = max(1, min(limit, 50))

    def _load():
        return _get_store_or_503().list_summaries(filter, limit, user_id)

    try:
        # 同步 MySQL 查询（含首次存储初始化的 DDL），放 IO 线程池避免阻塞事件循环
        return await asyncio.get_running_loop().run_in_executor(_io_executor, _load)
    except StorageUnavailable:
        raise HTTPException(status_code=503, detail="MySQL 不可用，历史功能暂不可用。")


@app.get("/api/trip/history/{trip_id}")
async def get_history_detail(http: Request, trip_id: int):
    """按 ID 取完整行程（含收藏状态），供结果页 /result?id= 加载。"""
    user_id = _uid(http)

    def _load():
        return _get_store_or_503().get_detail(trip_id, user_id)

    try:
        detail = await asyncio.get_running_loop().run_in_executor(_io_executor, _load)
    except StorageUnavailable:
        raise HTTPException(status_code=503, detail="MySQL 不可用，历史功能暂不可用。")
    if detail is None:
        raise HTTPException(status_code=404, detail="行程不存在或已删除。")
    return detail


@app.post("/api/trip/replan", response_model=TripPlan)
async def replan_trip(body: ReplanBody, http: Request):
    """带约束重规划：用户对行程中的安排反馈问题（约满/没房/排队久/不想去），
    系统搜索真实替代候选后让 PlannerAgent 做最小改动，产出新版本（parent_id 指向原行程）。"""
    user_id = _uid(http)
    loop = asyncio.get_running_loop()

    def _load_original():
        return _get_store_or_503().get_detail(body.trip_id, user_id)

    lock_key = f"plan:lock:replan:{hashlib.md5(body.model_dump_json().encode()).hexdigest()}"

    # 并发闸门：与 /plan 同一组 2 个 worker，同样快速失败
    acquired = _plan_gate.acquire(blocking=False)
    if not acquired:
        raise HTTPException(status_code=429, detail="系统正在规划其他行程，请稍后再试。")

    lock_token: str | None = None
    try:
        detail = await loop.run_in_executor(_io_executor, _load_original)
        if detail is None:
            raise HTTPException(status_code=404, detail="原行程不存在或已删除。")

        original_request = TripRequest(**detail["request"])
        original_plan = TripPlan(**detail["plan"])

        if _cache:
            allowed, lock_token = await loop.run_in_executor(_io_executor, _cache.acquire_lock, lock_key, 600)
            if not allowed:
                raise HTTPException(status_code=409, detail="相同的重规划请求正在处理中，请稍候再试。")

        new_plan = await loop.run_in_executor(
            _pipeline_executor,
            get_planner(settings).replan,
            original_request,
            original_plan,
            body.feedbacks,
        )
    except HTTPException:
        raise
    except StorageUnavailable:
        raise HTTPException(status_code=503, detail="MySQL 不可用，重规划功能暂不可用。")
    except PlannerError as e:
        logger.warning("重规划流水线失败: %s", e, exc_info=e.__cause__)
        raise HTTPException(status_code=500, detail=str(e))
    except Exception:  # noqa: BLE001
        logger.exception("重规划未预期失败")
        raise HTTPException(status_code=500, detail="重规划失败，请稍后重试（详情见服务端日志）")
    finally:
        _plan_gate.release()
        if _cache:
            await loop.run_in_executor(_io_executor, _cache.release_lock, lock_key, lock_token)

    await loop.run_in_executor(_io_executor, _enrich_images, new_plan)
    new_plan.parent_id = body.trip_id

    # 重规划同样重新计算路线并回写交通费/钟点（批次 1.2/1.3）
    try:
        _finalize_plan(original_request, new_plan)
    except Exception:  # noqa: BLE001
        logger.warning("重规划确定性后处理失败", exc_info=True)

    def _save():
        return _get_store_or_503().save(original_request, new_plan, user_id)

    try:
        new_plan.trip_id = await loop.run_in_executor(_io_executor, _save)
    except StorageUnavailable:
        logger.warning("MySQL 不可用，重规划结果未入历史库")
    return new_plan


class SwapBody(BaseModel):
    day: int
    original: str  # 要换下的主行程景点名
    backup: str    # 要换上的 Plan B 备选名


@app.post("/api/trip/history/{trip_id}/swap-backup", response_model=TripPlan)
async def swap_backup(http: Request, trip_id: int, body: SwapBody):
    """一键启用 Plan B：把某天的主景点与备选景点对调并入库。"""
    user_id = _uid(http)
    loop = asyncio.get_running_loop()

    def _load():
        return _get_store_or_503().get_detail(trip_id, user_id)

    try:
        detail = await loop.run_in_executor(_io_executor, _load)
    except StorageUnavailable:
        raise HTTPException(status_code=503, detail="MySQL 不可用，暂无法启用备选。")
    if detail is None:
        raise HTTPException(status_code=404, detail="行程不存在或已删除。")

    plan = TripPlan(**detail["plan"])
    day = next((d for d in plan.daily_plans if d.day == body.day), None)
    if day is None:
        raise HTTPException(status_code=404, detail=f"行程中没有第 {body.day} 天。")
    ai = next((i for i, a in enumerate(day.attractions) if a.name == body.original), None)
    bi = next((i for i, b in enumerate(day.backup_attractions) if b.name == body.backup), None)
    if ai is None or bi is None:
        raise HTTPException(status_code=400, detail="未找到对应的主景点或备选景点（行程可能已变化，请刷新页面）。")

    # 对调：备选进入主行程原位置，原景点进入备选位（可换回）
    day.attractions[ai], day.backup_attractions[bi] = day.backup_attractions[bi], day.attractions[ai]

    def _save():
        return _get_store_or_503().update_plan(trip_id, plan, user_id)

    try:
        stored = await loop.run_in_executor(_io_executor, _save)
    except StorageUnavailable:
        raise HTTPException(status_code=503, detail="MySQL 不可用，暂无法启用备选。")
    if stored is None:
        raise HTTPException(status_code=404, detail="行程不存在或已删除。")
    return plan


class StarBody(BaseModel):
    starred: bool


@app.get("/api/trip/history/{trip_id}/routes")
async def get_trip_routes(http: Request, trip_id: int):
    """每日真实驾车路线：按天串接景点的导航折线、里程、车程与打车费。

    按需计算并缓存（Redis 7 天）；demo 行程或高德不可用时返回空 routes，
    前端静默降级为不画线。
    """
    user_id = _uid(http)
    loop = asyncio.get_running_loop()

    def _load():
        return _get_store_or_503().get_detail(trip_id, user_id)

    try:
        detail = await loop.run_in_executor(_io_executor, _load)
    except StorageUnavailable:
        raise HTTPException(status_code=503, detail="MySQL 不可用，暂无法计算路线。")
    if detail is None:
        raise HTTPException(status_code=404, detail="行程不存在或已删除。")

    try:
        plan = TripPlan(**detail["plan"])
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=422, detail="行程数据无法解析，无法计算路线。")

    def _compute():
        return compute_day_routes(settings.amap_api_key, _cache, plan)

    return await loop.run_in_executor(_io_executor, _compute)


@app.get("/api/trip/history/{trip_id}/ical")
async def export_trip_ical(http: Request, trip_id: int):
    """行程导出 iCal (.ics)：景点与三餐生成日历事件，可直接导入手机/电脑日历。"""
    user_id = _uid(http)
    loop = asyncio.get_running_loop()

    def _load():
        return _get_store_or_503().get_detail(trip_id, user_id)

    try:
        detail = await loop.run_in_executor(_io_executor, _load)
    except StorageUnavailable:
        raise HTTPException(status_code=503, detail="MySQL 不可用，暂无法导出日历。")
    if detail is None:
        raise HTTPException(status_code=404, detail="行程不存在或已删除。")

    try:
        plan = TripPlan(**detail["plan"])
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=422, detail="行程数据无法解析，无法导出日历。")

    ics = plan_to_ics(plan)
    filename = quote(f"{plan.destination}行程.ics")
    return Response(
        content=ics,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@app.put("/api/trip/history/{trip_id}", response_model=TripPlan)
async def update_history(http: Request, trip_id: int, plan: TripPlan):
    """编辑保存：把结果页编辑后的行程写回数据库。"""
    user_id = _uid(http)

    def _save():
        return _get_store_or_503().update_plan(trip_id, plan, user_id)

    try:
        updated = await asyncio.get_running_loop().run_in_executor(_io_executor, _save)
    except StorageUnavailable:
        raise HTTPException(status_code=503, detail="MySQL 不可用，保存功能暂不可用。")
    if updated is None:
        raise HTTPException(status_code=404, detail="行程不存在或已删除。")
    return updated


@app.post("/api/trip/history/{trip_id}/star")
async def star_trip(http: Request, trip_id: int, body: StarBody):
    user_id = _uid(http)

    def _save():
        return _get_store_or_503().set_star(trip_id, body.starred, user_id)

    try:
        ok = await asyncio.get_running_loop().run_in_executor(_io_executor, _save)
    except StorageUnavailable:
        raise HTTPException(status_code=503, detail="MySQL 不可用，收藏功能暂不可用。")
    if not ok:
        raise HTTPException(status_code=404, detail="行程不存在或仅本人行程可收藏。")
    return {"id": trip_id, "starred": body.starred}


@app.delete("/api/trip/history/{trip_id}")
async def delete_history(http: Request, trip_id: int):
    user_id = _uid(http)

    def _save():
        return _get_store_or_503().delete(trip_id, user_id)

    try:
        ok = await asyncio.get_running_loop().run_in_executor(_io_executor, _save)
    except StorageUnavailable:
        raise HTTPException(status_code=503, detail="MySQL 不可用，删除功能暂不可用。")
    if not ok:
        raise HTTPException(status_code=404, detail="行程不存在或不可删除（示例行程不可删）。")
    return {"id": trip_id, "deleted": True}


def _resolve_dates(request: TripRequest) -> list[str]:
    try:
        start = (
            datetime.strptime(request.start_date, "%Y-%m-%d").date()
            if request.start_date
            else date.today()
        )
    except ValueError:
        start = date.today()
    return [(start + timedelta(days=i)).isoformat() for i in range(request.days)]


def _enrich_images(plan: TripPlan) -> None:
    """为景点/餐厅/酒店补充配图（高德实景图为主，Unsplash 氛围图兜底）。

    同步阻塞且耗时（几十个外部 HTTP 请求），只允许在 _io_executor 中调用。
    """
    global _image_service
    if _image_service is None:
        with _image_service_lock:
            if _image_service is None:
                _image_service = ImageService(settings)
    _image_service.enrich(plan)


# 启动时后台清理过期的图片缓存与 Agent trace（不阻塞启动）
threading.Thread(target=cleanup_stale_files, name="cleanup", daemon=True).start()


if __name__ == "__main__":
    import uvicorn

    # 默认仅本机可达；需要局域网/外网访问时改回 "0.0.0.0" 并务必配置 APP_PASSWORD
    uvicorn.run(app, host="127.0.0.1", port=8000)
