"""校验器回归：真实性 / 闭馆日 / 距离 / 密度（FakeClient，不打真实高德 API）。"""
import json

import app.agents.validator as validator
import pytest
from app.agents.validator import validate_plan
from app.models.schemas import Attraction, DayPlan, Location, TripPlan, TripRequest


class FakeCache:
    def __init__(self):
        self.store = {}

    def get(self, k):
        return self.store.get(k)

    def set(self, k, v, ttl):
        self.store[k] = v


class FakeValidatorClient:
    """只实现 validate_plan 用到的 call_tool。"""

    def __init__(self, pois_by_keyword, details_by_id, distances=None):
        self.pois = pois_by_keyword
        self.details = details_by_id
        self.distances = distances or {}

    def call_tool(self, name, arguments=None):
        if name == "maps_text_search":
            return json.dumps({"pois": self.pois.get(arguments.get("keywords"), [])})
        if name == "maps_search_detail":
            return json.dumps(self.details.get(arguments.get("id"), {}))
        if name == "maps_distance":
            pair = (arguments["origins"], arguments["destination"])
            return json.dumps({"results": self.distances.get(pair, [])})
        if name == "maps_around_search":
            return json.dumps({"pois": []})
        raise AssertionError(f"unexpected tool {name}")


REQ = TripRequest(destination="北京", start_date="2026-03-02", days=1)  # 周一
GUGONG = {"id": "B000A8UIN8", "name": "故宫博物院", "location": "116.397428,39.90923", "address": "景山前街4号"}


def make_plan(attractions):
    day = DayPlan(day=1, date="2026-03-02", attractions=attractions)
    return TripPlan(destination="北京", days=1, daily_plans=[day])


def attr(name, lon, lat):
    return Attraction(name=name, location=Location(longitude=lon, latitude=lat))


@pytest.fixture(autouse=True)
def no_pace(monkeypatch):
    monkeypatch.setattr(validator, "pace", lambda *a, **k: None)


def kinds(issues):
    return {i["kind"]: i for i in issues}


def test_real_poi_exact_coords_passes():
    client = FakeValidatorClient(
        {"故宫博物院": [GUGONG]},
        {"B000A8UIN8": {"pois": [{"open_time": "周二至周日 08:30-17:00"}]}},
    )
    issues = validate_plan(make_plan([attr("故宫博物院", 116.397428, 39.90923)]), REQ, client, FakeCache())
    assert not [i for i in issues if i["kind"] in ("unverified", "fake_coord")]


def test_fabricated_poi_is_weak_warning():
    client = FakeValidatorClient({"梦幻亚特兰蒂斯城堡": []}, {})
    issues = validate_plan(make_plan([attr("梦幻亚特兰蒂斯城堡", 116.397428, 39.90923)]), REQ, client, FakeCache())
    uv = kinds(issues).get("unverified")
    assert uv is not None and uv["strong"] is False


def test_wrong_coords_is_strong_issue_with_real_location():
    client = FakeValidatorClient({"故宫博物院": [GUGONG]}, {"B000A8UIN8": {}})
    issues = validate_plan(make_plan([attr("故宫博物院", 121.4737, 31.2304)]), REQ, client, FakeCache())
    fc = kinds(issues).get("fake_coord")
    assert fc is not None and fc["strong"] is True
    assert fc["location"] == {"longitude": 116.397428, "latitude": 39.90923}


def test_fuzzy_name_match_verifiable():
    client = FakeValidatorClient({"故宫": [GUGONG]}, {"B000A8UIN8": {}})
    issues = validate_plan(make_plan([attr("故宫", 116.397428, 39.90923)]), REQ, client, FakeCache())
    assert "unverified" not in kinds(issues)


def test_closed_day_regression():
    client = FakeValidatorClient(
        {"故宫博物院": [GUGONG]}, {"B000A8UIN8": {"pois": [{"open_time": "周一闭馆"}]}}
    )
    issues = validate_plan(make_plan([attr("故宫博物院", 116.397428, 39.90923)]), REQ, client, FakeCache())
    cd = kinds(issues).get("closed_day")
    assert cd is not None and cd["strong"] is True


def test_negative_cache_prevents_repeat_calls():
    cache = FakeCache()
    client = FakeValidatorClient({"不存在的景点XYZ": []}, {})
    plan = make_plan([attr("不存在的景点XYZ", 116.0, 39.9)])
    validate_plan(plan, REQ, client, cache)
    validate_plan(plan, REQ, client, cache)
    assert any(v == "-" for v in cache.store.values())


def test_distance_api_path_cached_and_far_flagged():
    """直线距离在 [8km, 15km] 中间地带：走 API，结果缓存后二次不再调 API。"""
    # 0.1° 经度 @39.9°N ≈ 8.5km 直线，落在中间地带
    pair = ("116.0,39.9", "116.1,39.9")
    client = FakeValidatorClient(
        {"天坛": [], "颐和园": []},
        {},
        distances={pair: [{"distance": "20000"}]},  # 驾车 20km > 15km
    )
    cache = FakeCache()
    plan = make_plan([attr("天坛", 116.0, 39.9), attr("颐和园", 116.1, 39.9)])
    issues = validate_plan(plan, REQ, client, cache)
    assert "far" in kinds(issues)
    # 第二次命中距离缓存：client 的距离表清空后仍应报 far
    client.distances = {}
    issues2 = validate_plan(plan, REQ, client, cache)
    assert "far" in kinds(issues2)


def test_far_straight_line_flags_without_api():
    """直线 >15km：驾车只会更远，不调 API 直接判罚（distance 表为空仍报 far）。"""
    client = FakeValidatorClient({"天坛": [], "颐和园": []}, {})  # 无任何距离数据
    plan = make_plan([attr("天坛", 116.0, 39.9), attr("颐和园", 116.5, 39.4)])  # 直线约 70km
    issues = validate_plan(plan, REQ, client, FakeCache())
    far = kinds(issues).get("far")
    assert far is not None and "直线" in far["text"]


def test_close_pairs_skip_api():
    """直线 <8km：驾车必然达标，不调 API 也不判罚。"""
    client = FakeValidatorClient({"甲": [], "乙": []}, {})
    plan = make_plan([attr("甲", 116.0, 39.9), attr("乙", 116.05, 39.9)])  # 约 4.3km
    issues = validate_plan(plan, REQ, client, FakeCache())
    assert "far" not in kinds(issues)


def test_density_rule():
    client = FakeValidatorClient({n: [] for n in ("甲", "乙", "丙", "丁")}, {})
    plan = make_plan([attr(n, 116.0 + i * 0.01, 39.9) for i, n in enumerate(("甲", "乙", "丙", "丁"))])
    issues = validate_plan(plan, REQ, client, FakeCache())
    assert "density" in kinds(issues)
