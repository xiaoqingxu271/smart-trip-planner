"""磁盘垃圾治理：启动时清理过期的图片代理缓存与 Agent trace/session 文件。

这些目录会随使用无限增长（图片缓存按 URL 落盘、trace 每次规划一个文件），
无淘汰机制时最终会填满磁盘。按 mtime 清理超过 30 天的旧文件。
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_MAX_AGE_DAYS = 30
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_TARGETS = (
    _BACKEND_ROOT / ".image_cache",
    _BACKEND_ROOT / "memory" / "traces",
    _BACKEND_ROOT / "memory" / "sessions",
)


def cleanup_stale_files(max_age_days: int = _MAX_AGE_DAYS) -> None:
    """删除各目录中超过 max_age_days 的旧文件；目录不存在或文件不可删时静默跳过。"""
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    for target in _TARGETS:
        if not target.is_dir():
            continue
        try:
            entries = list(target.iterdir())
        except OSError:
            continue
        for f in entries:
            try:
                if f.is_file() and f.stat().st_mtime < cutoff:
                    f.unlink()
                    removed += 1
            except OSError:
                continue
    if removed:
        logger.info("已清理 %d 个过期文件（>%d 天）", removed, max_age_days)
