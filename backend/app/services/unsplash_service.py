"""Unsplash 图片服务：为景点补充配图。

按书中设计，这里不封装成 Tool——它只是数据增强步骤，
无需智能决策，直接在 API 路由层为每个景点补充 image_url 即可。
"""
from __future__ import annotations

import httpx

from ..config import Settings


class UnsplashService:
    BASE_URL = "https://api.unsplash.com"

    def __init__(self, settings: Settings):
        self.access_key = settings.unsplash_access_key

    @property
    def enabled(self) -> bool:
        return bool(self.access_key)

    def search_photo_url(self, query: str, orientation: str = "landscape") -> str | None:
        """搜索并返回第一张图片的 regular 尺寸 URL；未配置或失败返回 None。"""
        if not self.enabled:
            return None
        try:
            resp = httpx.get(
                f"{self.BASE_URL}/search/photos",
                params={"query": query, "per_page": 1, "orientation": orientation},
                headers={"Authorization": f"Client-ID {self.access_key}"},
                timeout=10.0,
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
            if results:
                return results[0].get("urls", {}).get("regular")
        except (httpx.HTTPError, ValueError):
            return None
        return None
