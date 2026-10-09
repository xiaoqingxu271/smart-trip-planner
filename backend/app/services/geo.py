"""简单地理几何：候选点质心与按天数聚类（给酒店 / 餐饮 around 搜索提供中心点）。

批次 2.2/2.1 用：酒店围绕景点候选质心搜索，餐饮围绕「按天聚类」后的各簇质心搜索。
纯函数、确定性（无随机数），便于单测。
"""
from __future__ import annotations

import math

Coord = tuple[float, float]


def _dist2(a: Coord, b: Coord) -> float:
    """经纬度近似平面距离平方（经度按中点纬度做 cos 加权，抑制高纬横向拉伸）。"""
    dx = (a[0] - b[0]) * math.cos(math.radians((a[1] + b[1]) / 2))
    dy = a[1] - b[1]
    return dx * dx + dy * dy


def centroid(coords: list[Coord]) -> Coord | None:
    """多组 (经度, 纬度) 的平面质心；空列表返回 None。"""
    if not coords:
        return None
    n = len(coords)
    lng = sum(c[0] for c in coords) / n
    lat = sum(c[1] for c in coords) / n
    return round(lng, 6), round(lat, 6)


def cluster_centroids(coords: list[Coord], k: int) -> list[Coord]:
    """把候选点分成 k 个地理簇，返回非空簇的质心（按经度升序）。

    farthest-first 确定性种子 + 至多 10 轮 Lloyd 迭代；无随机数，同输入同输出。
    点数不足 k 时退化为每点一簇（直接返回原坐标）。
    """
    coords = list(coords)
    if not coords or k <= 0:
        return []
    if k >= len(coords):
        return sorted(coords, key=lambda c: (c[0], c[1]))

    # farthest-first 种子：从首个点出发，反复选距已有中心最远的点
    centers: list[Coord] = [coords[0]]
    while len(centers) < k:
        best = max(coords, key=lambda c: min(_dist2(c, s) for s in centers))
        centers.append(best)

    for _ in range(10):
        buckets: list[list[Coord]] = [[] for _ in centers]
        for c in coords:
            j = min(range(len(centers)), key=lambda i: _dist2(c, centers[i]))
            buckets[j].append(c)
        new_centers: list[Coord] = []
        for i, b in enumerate(buckets):
            if b:
                new_centers.append(centroid(b))  # type: ignore[arg-type]
            else:  # 空簇：保持原中心，避免漂移到 (0,0)
                new_centers.append(centers[i])
        if new_centers == centers:
            break
        centers = new_centers

    out = [c for c, b in zip(centers, buckets) if b]
    out.sort(key=lambda c: (c[0], c[1]))
    return out