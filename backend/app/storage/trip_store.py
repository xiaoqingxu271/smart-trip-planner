"""行程数据访问：MySQL 为事实来源，Redis 只做读缓存与排行副本。

写路径：先写 MySQL，再删/改 Redis 对应键（不做双写，避免不一致）。
并发安全由 db.connection() 的连接级锁保证（单连接串行），上层无需加锁。
列表页摘要在写入时从 plan 提取到独立列，读取不再拉全量 plan_json。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from ..config import Settings
from ..models.schemas import TripPlan, TripRequest
from .cache import Cache
from .db import StorageUnavailable, connection

logger = logging.getLogger(__name__)

DETAIL_TTL = 24 * 3600  # trip:detail:{id} 缓存 24h
STARS_KEY = "gallery:stars"


def derive_summary(plan: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    """从 plan JSON 提取列表页摘要（封面图 / 主题 / 总览），写入时算一次。"""
    cover_url = None
    themes: list[str] = []
    for day in plan.get("daily_plans", []):
        if cover_url is None:
            cover_url = next(
                (a.get("image_url") for a in day.get("attractions", []) if a.get("image_url")), None
            )
        if day.get("theme"):
            themes.append(day["theme"])
    return (
        cover_url,
        "|".join(themes[:4]) or None,
        (plan.get("summary") or "")[:80] or None,
    )


class TripStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.cache = Cache(settings)
        self._backfill_summaries()

    def _backfill_summaries(self) -> None:
        """一次性回填摘要列为 NULL 的旧行（摘要列上线前写入的行程）。

        失败只影响列表摘要显示，不阻断存储功能。
        """
        try:
            with connection(self.settings) as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT id, plan_json FROM trips"
                    " WHERE cover_url IS NULL OR themes IS NULL OR summary IS NULL LIMIT 500"
                )
                rows = cur.fetchall()
                for row in rows:
                    try:
                        cover, themes, summary = derive_summary(json.loads(row["plan_json"]))
                    except (ValueError, TypeError):
                        continue
                    cur.execute(
                        "UPDATE trips SET cover_url = %s, themes = %s, summary = %s WHERE id = %s",
                        (cover, themes, summary, row["id"]),
                    )
                if rows:
                    logger.info("已为 %d 条旧行程回填列表摘要", len(rows))
        except Exception:  # noqa: BLE001
            logger.warning("摘要回填失败（列表卡片可能缺封面/主题）", exc_info=True)

    # ---------- 写 ----------

    def save(self, request: TripRequest, plan: TripPlan) -> int:
        """写入新行程，返回自增 id。"""
        cover, themes, summary = derive_summary(plan.model_dump())
        with connection(self.settings) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO trips (request_json, plan_json, destination, days, grand_total,"
                " cover_url, themes, summary, starred, is_seed, parent_id)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 0, 0, %s)",
                (
                    request.model_dump_json(),
                    plan.model_dump_json(),
                    plan.destination,
                    plan.days,
                    plan.budget.grand_total if plan.budget else None,
                    cover,
                    themes,
                    summary,
                    plan.parent_id,
                ),
            )
            return int(cur.lastrowid)

    def set_star(self, trip_id: int, starred: bool) -> None:
        with connection(self.settings) as conn, conn.cursor() as cur:
            cur.execute("UPDATE trips SET starred = %s WHERE id = %s", (1 if starred else 0, trip_id))
        if starred:
            self.cache.zadd(STARS_KEY, str(trip_id))
        else:
            self.cache.zrem(STARS_KEY, str(trip_id))
        self.cache.delete(f"trip:detail:{trip_id}")

    def update_plan(self, trip_id: int, plan: TripPlan) -> TripPlan | None:
        """编辑保存：更新 plan_json、预算总计与摘要列，并失效详情缓存。"""
        cover, themes, summary = derive_summary(plan.model_dump())
        with connection(self.settings) as conn, conn.cursor() as cur:
            cur.execute("SELECT id FROM trips WHERE id = %s", (trip_id,))
            if not cur.fetchone():
                return None
            cur.execute(
                "UPDATE trips SET plan_json = %s, grand_total = %s,"
                " cover_url = %s, themes = %s, summary = %s WHERE id = %s",
                (
                    plan.model_dump_json(),
                    plan.budget.grand_total if plan.budget else None,
                    cover,
                    themes,
                    summary,
                    trip_id,
                ),
            )
        self.cache.delete(f"trip:detail:{trip_id}")
        return plan

    def delete(self, trip_id: int) -> None:
        with connection(self.settings) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM trips WHERE id = %s AND is_seed = 0", (trip_id,))
        self.cache.zrem(STARS_KEY, str(trip_id))
        self.cache.delete(f"trip:detail:{trip_id}")

    # ---------- 读 ----------

    def get_detail(self, trip_id: int) -> dict[str, Any] | None:
        """取完整行程；先查 Redis，未命中查 MySQL 回填。"""
        cached = self.cache.get(f"trip:detail:{trip_id}")
        if cached:
            return json.loads(cached)

        with connection(self.settings) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, request_json, plan_json, starred, is_seed, created_at"
                " FROM trips WHERE id = %s",
                (trip_id,),
            )
            row = cur.fetchone()
        if not row:
            return None
        detail = {
            "id": row["id"],
            "starred": bool(row["starred"]),
            "is_seed": bool(row["is_seed"]),
            "created_at": row["created_at"].isoformat(timespec="minutes"),
            "request": json.loads(row["request_json"]),
            "plan": json.loads(row["plan_json"]),
        }
        self.cache.set(f"trip:detail:{trip_id}", json.dumps(detail, ensure_ascii=False), DETAIL_TTL)
        return detail

    def list_summaries(self, filter: str, limit: int = 12) -> list[dict[str, Any]]:
        """卡片摘要列表。filter: recent | starred | seed（只查摘要列，不拉 plan_json）"""
        order = {
            "recent": "created_at DESC",
            "starred": "created_at DESC",
            "seed": "created_at DESC",
        }[filter]
        where = {"recent": "is_seed = 0", "starred": "starred = 1", "seed": "is_seed = 1"}[filter]
        sql = (
            "SELECT id, destination, days, grand_total, starred, is_seed, created_at,"
            " cover_url, themes, summary"
            f" FROM trips WHERE {where} ORDER BY {order} LIMIT %s"
        )
        with connection(self.settings) as conn, conn.cursor() as cur:
            cur.execute(sql, (limit,))
            rows = cur.fetchall()

        # 收藏页按 Redis ZSET 的收藏先后倒序（新的在前），库不可用时保持 SQL 顺序
        if filter == "starred":
            zids = self.cache.zrevrange(STARS_KEY, 0, limit - 1)
            if zids:
                by_id = {str(r["id"]): r for r in rows}
                rows = [by_id[z] for z in zids if z in by_id]

        return [self._row_to_summary(r) for r in rows]

    # ---------- 内部 ----------

    @staticmethod
    def _row_to_summary(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "destination": row["destination"],
            "days": row["days"],
            "grand_total": float(row["grand_total"]) if row["grand_total"] is not None else None,
            "starred": bool(row["starred"]),
            "is_seed": bool(row["is_seed"]),
            "created_at": row["created_at"].isoformat(timespec="minutes"),
            "cover_url": row["cover_url"],
            "themes": row["themes"].split("|") if row["themes"] else [],
            "summary": row["summary"] or "",
        }
