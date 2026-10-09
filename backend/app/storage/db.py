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

# 编号化迁移（批次 E）：每条约一个「列名探测键 + ALTER 语句」。
# 应用一次后记录到 schema_migrations；列级探测仍保留——MySQL 8 无
# ADD COLUMN IF NOT EXISTS，且老部署可能已由旧版 ALTER 加过列而未登记迁移，
# 需要能自愈把缺失列补上、把已存在列跳过。
_MIGRATIONS: list[tuple[str, tuple[tuple[str, str], ...]]] = [
    ("001", (("parent_id", "ALTER TABLE trips ADD COLUMN parent_id BIGINT NULL, ADD INDEX idx_parent (parent_id)"),)),
    ("002", (("cover_url", "ALTER TABLE trips ADD COLUMN cover_url VARCHAR(500) NULL,"
             " ADD COLUMN themes VARCHAR(200) NULL, ADD COLUMN summary VARCHAR(200) NULL"),)),
    ("003", (("user_id", "ALTER TABLE trips ADD COLUMN user_id BIGINT NULL, ADD INDEX idx_user (user_id)"),)),
    ("004", (("share_token", "ALTER TABLE trips ADD COLUMN share_token VARCHAR(32) NULL,"
             " ADD UNIQUE INDEX idx_share (share_token)"),)),
    ("005", (("share_created_at", "ALTER TABLE trips ADD COLUMN share_created_at DATETIME NULL"),)),
]


def _apply_migrations(cur, settings: Settings) -> None:
    """按版本号递增应用未登记的列迁移，并记录到 schema_migrations。"""
    cur.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version VARCHAR(16) PRIMARY KEY,"
        " applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
    )
    cur.execute("SELECT version FROM schema_migrations")
    applied = {row[0] for row in cur.fetchall()}
    for version, cols in _MIGRATIONS:
        if version in applied:
            continue
        for col, alter_sql in cols:
            cur.execute(
                "SELECT COUNT(*) FROM information_schema.columns"
                " WHERE table_schema = %s AND table_name = 'trips' AND column_name = %s",
                (settings.mysql_database, col),
            )
            if cur.fetchone()[0] == 0:
                cur.execute(alter_sql)
        cur.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))


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
            # 编号化列迁移：已有表补缺失列（CREATE TABLE IF NOT EXISTS 不更新存量表）
            _apply_migrations(cur, settings)
        conn.commit()
    finally:
        conn.close()


_ENSURE_LOCK = threading.Lock()
_ENSURED = False


def ensure_database_once(settings: Settings) -> None:
    """进程内幂等版：保证库表已创建。

    注册/登录等不经过 _get_store 的路径（如首次注册时 users 表还不存在）
    必须先调用本函数；其余路径沿用 ensure_database。
    """
    global _ENSURED
    if _ENSURED:
        return
    with _ENSURE_LOCK:
        if _ENSURED:
            return
        ensure_database(settings)
        _ENSURED = True


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
