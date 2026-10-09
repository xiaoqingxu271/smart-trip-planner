"""行程数据访问：MySQL 为事实来源，Redis 只做读缓存与排行副本。

写路径：先写 MySQL，再删/改 Redis 对应键（不做双写，避免不一致）。
并发安全由 db.connection() 的连接级锁保证（单连接串行），上层无需加锁。
列表页摘要在写入时从 plan 提取到独立列，读取不再拉全量 plan_json。

多用户隔离（user_id 参数，None = 无鉴权模式不做过滤）：
- 可见：本人行程 / 示例作品（is_seed）/ 无主行程（user_id 为 NULL 的存量数据）；
- 修改/删除/收藏：仅本人行程。他人行程与示例/无主行程一律拒绝（返回 None）。
"""
from __future__ import annotations

import json
import logging
import secrets
from typing import Any

from ..config import Settings
from ..models.schemas import TripPlan, TripRequest
from .cache import Cache
from .db import StorageUnavailable, connection

logger = logging.getLogger(__name__)

DETAIL_TTL = 24 * 3600  # trip:detail:{id} 缓存 24h
STARS_KEY = "gallery:stars"  # 无鉴权模式的全局收藏排行


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

    def _stars_key(self, user_id: int | None) -> str:
        """收藏排行 ZSET：多用户模式按用户隔离，无鉴权模式用全局键。"""
        return f"gallery:stars:{user_id}" if user_id is not None else STARS_KEY

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

    def save(self, request: TripRequest, plan: TripPlan, user_id: int | None = None) -> int:
        """写入新行程，返回自增 id。"""
        cover, themes, summary = derive_summary(plan.model_dump())
        with connection(self.settings) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO trips (request_json, plan_json, destination, days, grand_total,"
                " cover_url, themes, summary, starred, is_seed, user_id, parent_id)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 0, 0, %s, %s)",
                (
                    request.model_dump_json(),
                    plan.model_dump_json(),
                    plan.destination,
                    plan.days,
                    plan.budget.grand_total if plan.budget else None,
                    cover,
                    themes,
                    summary,
                    user_id,
                    plan.parent_id,
                ),
            )
            return int(cur.lastrowid)

    def set_star(self, trip_id: int, starred: bool, user_id: int | None = None) -> bool:
        """收藏/取消收藏；仅限本人行程，返回是否成功。"""
        if user_id is not None and not self._is_owned_by(trip_id, user_id):
            return False
        with connection(self.settings) as conn, conn.cursor() as cur:
            cur.execute("UPDATE trips SET starred = %s WHERE id = %s", (1 if starred else 0, trip_id))
        stars = self._stars_key(user_id)
        if starred:
            self.cache.zadd(stars, str(trip_id))
        else:
            self.cache.zrem(stars, str(trip_id))
        self.cache.delete(f"trip:detail:{trip_id}")
        return True

    def update_plan(self, trip_id: int, plan: TripPlan, user_id: int | None = None) -> TripPlan | None:
        """编辑保存：更新 plan_json、预算总计与摘要列，并失效详情缓存。仅限本人行程。"""
        if user_id is not None and not self._is_owned_by(trip_id, user_id):
            return None
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

    def delete(self, trip_id: int, user_id: int | None = None) -> bool:
        """删除行程；仅限本人行程（示例/无主行程不可删）。"""
        if user_id is not None and not self._is_owned_by(trip_id, user_id):
            return False
        with connection(self.settings) as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM trips WHERE id = %s AND is_seed = 0"
                + (" AND user_id = %s" if user_id is not None else ""),
                (trip_id, user_id) if user_id is not None else (trip_id,),
            )
        self.cache.zrem(self._stars_key(user_id), str(trip_id))
        self.cache.delete(f"trip:detail:{trip_id}")
        return True

    def get_or_create_share_token(self, trip_id: int, user_id: int | None = None) -> str | None:
        """生成/复用只读分享令牌（仅限本人行程；示例/无主行程不生成）。"""
        if user_id is not None and not self._is_owned_by(trip_id, user_id):
            return None
        with connection(self.settings) as conn, conn.cursor() as cur:
            cur.execute("SELECT share_token FROM trips WHERE id = %s", (trip_id,))
            row = cur.fetchone()
            if not row:
                return None
            token = row.get("share_token")
            if token:
                return token
            token = secrets.token_hex(12)
            cur.execute("UPDATE trips SET share_token = %s WHERE id = %s", (token, trip_id))
            return token

    def get_share_detail(self, token: str) -> dict[str, Any] | None:
        """按分享令牌只读取行程（独立于登录态，不做用户可见性过滤）。"""
        with connection(self.settings) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, request_json, plan_json, created_at FROM trips WHERE share_token = %s",
                (token,),
            )
            row = cur.fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "created_at": row["created_at"].isoformat(timespec="minutes"),
            "request": json.loads(row["request_json"]),
            "plan": json.loads(row["plan_json"]),
        }

    def _is_owned_by(self, trip_id: int, user_id: int) -> bool:
        with connection(self.settings) as conn, conn.cursor() as cur:
            cur.execute("SELECT user_id FROM trips WHERE id = %s", (trip_id,))
            row = cur.fetchone()
        return bool(row) and row["user_id"] == user_id

    # ---------- 读 ----------

    def get_detail(self, trip_id: int, user_id: int | None = None) -> dict[str, Any] | None:
        """取完整行程；先查 Redis，未命中查 MySQL 回填。

        可见性（user_id 非空时）：本人 / 示例 / 无主行程；他人行程返回 None。
        缓存条目带归属信息，命中后同样复检，防止他人行程经缓存泄漏。
        """
        cached = self.cache.get(f"trip:detail:{trip_id}")
        if cached:
            detail = json.loads(cached)
            if not self._visible(detail, user_id):
                return None
            return detail

        with connection(self.settings) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, request_json, plan_json, starred, is_seed, user_id, created_at"
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
            "user_id": row["user_id"],
            "created_at": row["created_at"].isoformat(timespec="minutes"),
            "request": json.loads(row["request_json"]),
            "plan": json.loads(row["plan_json"]),
        }
        if not self._visible(detail, user_id):
            return None
        self.cache.set(f"trip:detail:{trip_id}", json.dumps(detail, ensure_ascii=False), DETAIL_TTL)
        return detail

    @staticmethod
    def _visible(detail: dict[str, Any], user_id: int | None) -> bool:
        """可见性复检：本人 / 示例 / 无主行程可见；他人行程不可见。

        user_id 参数为 None（无鉴权模式）时不过滤。
        """
        if user_id is None:
            return True
        return detail.get("user_id") in (user_id, None) or bool(detail.get("is_seed"))

    def list_summaries(self, filter: str, limit: int = 12, user_id: int | None = None) -> list[dict[str, Any]]:
        """卡片摘要列表。filter: recent | starred | seed（只查摘要列，不拉 plan_json）。

        多用户模式：recent/starred 限定本人，seed（示例作品）全员可见。
        """
        order = {
            "recent": "created_at DESC",
            "starred": "created_at DESC",
            "seed": "created_at DESC",
        }[filter]
        if user_id is None:
            where = {"recent": "is_seed = 0", "starred": "starred = 1", "seed": "is_seed = 1"}[filter]
            params: tuple = (limit,)
        else:
            where = {
                "recent": "is_seed = 0 AND user_id = %s",
                "starred": "starred = 1 AND user_id = %s",
                "seed": "is_seed = 1",
            }[filter]
            params = (user_id, limit) if "%s" in where else (limit,)
        sql = (
            "SELECT id, destination, days, grand_total, starred, is_seed, created_at,"
            " cover_url, themes, summary"
            f" FROM trips WHERE {where} ORDER BY {order} LIMIT %s"
        )
        with connection(self.settings) as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

        # 收藏页按 Redis ZSET 的收藏先后倒序（新的在前），库不可用时保持 SQL 顺序
        if filter == "starred":
            zids = self.cache.zrevrange(self._stars_key(user_id), 0, limit - 1)
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
