"""MySQL 连接管理：模块级单连接 + 连接级互斥锁 + 断线重连。

本应用的写入频率极低（每次规划一条），单连接足够；但 pymysql 连接
**非线程安全**——ping 与任何查询都必须在同一临界区内完成（I/O 已全部
移入线程池，多线程并发使用连接会产生协议错乱），因此 connection()
把「取连接 → ping → 执行查询 → 归还」整体串行化，与上层实例个数无关。
连接失败时抛 StorageUnavailable，上层据此优雅降级。
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pymysql

from ..config import Settings

_LOCK = threading.Lock()
_CONN: pymysql.connections.Connection | None = None
AVAILABLE: bool | None = None  # None=未探测
# 熔断静默期（秒级时间戳）：建连/探测失败后 10s 内直接快速失败。
# MySQL 不可达时 connect_timeout=5s，若无熔断，持续的 503 请求会把
# IO 线程池整个楔死，把一次存储故障放大成全站卡死。
_DOWN_UNTIL = 0.0
_DOWN_COOLDOWN = 10.0


def _connect(settings: Settings) -> pymysql.connections.Connection:
    return pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        database=settings.mysql_database,
        charset="utf8mb4",
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor,
        # 连接/读写超时：MySQL 挂起时最多阻塞 30s 而不是无限卡死调用方
        connect_timeout=5,
        read_timeout=30,
        write_timeout=30,
    )


def ensure_database(settings: Settings) -> None:
    """建库建表（幂等），应用首次使用存储前调用。"""
    conn = pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        charset="utf8mb4",
        connect_timeout=5,
    )
    try:
        sql = "\n".join(
            line
            for line in (Path(__file__).resolve().parents[2] / "sql" / "init.sql")
            .read_text(encoding="utf-8")
            .splitlines()
            if not line.strip().startswith("--")
        )
        with conn.cursor() as cur:
            for stmt in (s.strip() for s in sql.split(";")):
                if stmt:
                    cur.execute(stmt)
            # 轻量迁移：已有表补缺失列（CREATE TABLE IF NOT EXISTS 不会更新存量表）
            cur.execute(
                "SELECT COUNT(*) FROM information_schema.columns"
                " WHERE table_schema = %s AND table_name = 'trips' AND column_name = 'parent_id'",
                (settings.mysql_database,),
            )
            if cur.fetchone()[0] == 0:
                cur.execute("ALTER TABLE trips ADD COLUMN parent_id BIGINT NULL, ADD INDEX idx_parent (parent_id)")
            # 列表页摘要列：避免为封面/主题/总览拉全量 plan_json
            cur.execute(
                "SELECT COUNT(*) FROM information_schema.columns"
                " WHERE table_schema = %s AND table_name = 'trips' AND column_name = 'cover_url'",
                (settings.mysql_database,),
            )
            if cur.fetchone()[0] == 0:
                cur.execute(
                    "ALTER TABLE trips ADD COLUMN cover_url VARCHAR(500) NULL,"
                    " ADD COLUMN themes VARCHAR(200) NULL,"
                    " ADD COLUMN summary VARCHAR(200) NULL"
                )
        conn.commit()
    finally:
        conn.close()


@contextmanager
def connection(settings: Settings) -> Iterator[pymysql.connections.Connection]:
    """共享单连接的完整使用周期（含 ping），全程持有 _LOCK 串行。

    - 建连/ping 失败 → StorageUnavailable（上层降级为 503），并进入 10s 熔断静默期；
    - 查询级异常沿原样向上抛，但连接状态已不可信，置空待下次重建。
    """
    global _CONN, AVAILABLE, _DOWN_UNTIL
    if time.time() < _DOWN_UNTIL:
        raise StorageUnavailable("MySQL 静默期，快速失败") from None
    with _LOCK:
        if time.time() < _DOWN_UNTIL:  # 等锁期间可能已被其他线程置位
            raise StorageUnavailable("MySQL 静默期，快速失败") from None
        try:
            if _CONN is None:
                _CONN = _connect(settings)
            else:
                _CONN.ping(reconnect=True)
            AVAILABLE = True
            _DOWN_UNTIL = 0.0
        except Exception:  # noqa: BLE001
            AVAILABLE = False
            _CONN = None
            _DOWN_UNTIL = time.time() + _DOWN_COOLDOWN
            raise StorageUnavailable("MySQL 不可用") from None
        try:
            yield _CONN
        except Exception:  # noqa: BLE001
            _CONN = None
            raise


def check(settings: Settings) -> bool:
    """主动探测 MySQL 可用性（供 /api/health）。"""
    try:
        with connection(settings):
            pass
        return True
    except Exception:  # noqa: BLE001
        return False


class StorageUnavailable(Exception):
    pass
