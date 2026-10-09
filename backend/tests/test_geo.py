"""地理几何纯函数：候选点质心与按天数聚类（确定性，无外部依赖）。"""
from app.services.geo import centroid, cluster_centroids


def test_centroid_empty_returns_none():
    assert centroid([]) is None


def test_centroid_mean():
    assert centroid([(0.0, 0.0), (2.0, 2.0)]) == (1.0, 1.0)


def test_cluster_two_well_separated_groups():
    coords = [(10.0, 10.0), (10.1, 10.0), (20.0, 10.0), (20.1, 10.0)]
    centers = cluster_centroids(coords, 2)
    assert len(centers) == 2
    lngs = [c[0] for c in centers]
    assert any(abs(x - 10.05) < 0.01 for x in lngs)
    assert any(abs(x - 20.05) < 0.01 for x in lngs)


def test_cluster_more_clusters_than_points():
    coords = [(1.0, 1.0), (2.0, 2.0)]
    centers = cluster_centroids(coords, 5)
    assert len(centers) == 2


def test_cluster_deterministic():
    coords = [(1.0, 1.0), (1.5, 1.0), (9.0, 1.0), (9.5, 1.0)]
    a = cluster_centroids(coords, 2)
    b = cluster_centroids(coords, 2)
    assert a == b