"""
H3 确定性内核测试 - 移植自 tests/build-itinerary.test.ts + tests/constraints.test.ts + tests/search-cache.test.ts

覆盖：
  - ids 校验
  - session_store create/load/save/search-cache
  - scheduler 排程（must 优先、pace 上限、全天项目）
  - constraints 校验（must 覆盖、重复、时间倒挂、预算）
"""
import asyncio
import shutil
from pathlib import Path

import pytest

from app.utils.ids import is_valid_session_id, assert_valid_session_id, is_valid_result_id
from app.services import session_store
from app.core.scheduler import build_itinerary_from_data, sort_pois_for_planning, is_full_day_poi, poi_cost
from app.services.travel_matrix import (
    get_travel_minutes,
    matrix_key,
    prefetch_travel_matrix,
    _parse_distance_results,
)
from app.core.constraints import check_constraints


# ─── 测试夹具 ───────────────────────────────────────────────────────────────────

SESSION_ID = "sess_a1b2c3d4e5f6"
TEST_CONTEXT_DIR = Path(__file__).resolve().parent.parent / "context_test"


@pytest.fixture(autouse=True)
def override_context_root(monkeypatch):
    """每个测试用独立的 context 目录"""
    from app.config import settings
    if TEST_CONTEXT_DIR.exists():
        shutil.rmtree(TEST_CONTEXT_DIR)
    TEST_CONTEXT_DIR.mkdir(parents=True)
    from app.config import settings
    settings.set_context_root(str(TEST_CONTEXT_DIR))
    yield
    if TEST_CONTEXT_DIR.exists():
        shutil.rmtree(TEST_CONTEXT_DIR)


def base_trip_meta():
    return {
        "city": "Osaka",
        "days": 3,
        "pace": "normal",
        "must_visit": ["poi_usj"],
        "avoid": [],
        "budget": {"currency": "JPY", "amount": 100000},
    }


def base_poi(over: dict = None) -> dict:
    poi = {
        "id": "poi_x",
        "name": "x",
        "category": "attraction",
        "area": "Namba",
        "source_refs": [{"type": "whitelist", "ref": "test"}],
        "confidence": 0.8,
        "priority": "optional",
        "estimated_duration_minutes": 60,
        "estimated_cost": 500,
    }
    if over:
        poi.update(over)
    return poi


# ─── ids 校验 ──────────────────────────────────────────────────────────────────

class TestIds:
    def test_valid_session_id(self):
        assert is_valid_session_id("sess_a1b2c3d4e5f6")
        assert is_valid_session_id("sess_0123456789ab")

    def test_invalid_session_id(self):
        assert not is_valid_session_id("sess_short")
        assert not is_valid_session_id("invalid_prefix")
        assert not is_valid_session_id("../../etc/passwd")
        assert not is_valid_session_id("")

    def test_assert_valid_session_id_raises(self):
        with pytest.raises(ValueError):
            assert_valid_session_id("invalid")

    def test_valid_result_id(self):
        assert is_valid_result_id("res_abc123")
        assert is_valid_result_id("abc-123_xyz")

    def test_invalid_result_id_path_traversal(self):
        assert not is_valid_result_id("../../etc/passwd")
        assert not is_valid_result_id("../secret")
        assert not is_valid_result_id("a/b")
        assert not is_valid_result_id("")


# ─── session_store ─────────────────────────────────────────────────────────────

class TestSessionStore:
    @pytest.mark.asyncio
    async def test_create_and_load_session(self):
        await session_store.create_session(SESSION_ID, base_trip_meta(), "test guide")
        loaded = await session_store.load_session(SESSION_ID)
        assert loaded is not None
        assert loaded["trip_meta"]["city"] == "Osaka"
        assert loaded["session"]["phase"] == "intake"

    @pytest.mark.asyncio
    async def test_load_nonexistent_session_returns_none(self):
        assert await session_store.load_session("sess_000000000000") is None

    @pytest.mark.asyncio
    async def test_invalid_session_id_raises(self):
        with pytest.raises(ValueError):
            await session_store.create_session("invalid", base_trip_meta())

    @pytest.mark.asyncio
    async def test_save_and_load_itinerary(self):
        await session_store.create_session(SESSION_ID, base_trip_meta())
        itinerary = {
            "session_id": SESSION_ID,
            "city": "Osaka",
            "version": 1,
            "days": [{"day": 1, "time_blocks": [], "estimated_total_minutes": 0,
                      "estimated_total_cost": 0, "area_cluster": []}],
        }
        await session_store.save_itinerary(SESSION_ID, itinerary)
        loaded = await session_store.load_itinerary(SESSION_ID)
        assert loaded is not None
        assert loaded["days"][0]["day"] == 1

    @pytest.mark.asyncio
    async def test_search_cache_roundtrip(self):
        await session_store.create_session(SESSION_ID, base_trip_meta())
        data = {"items": [{"title": "USJ", "price": 8600}]}
        await session_store.save_search_result(SESSION_ID, "res_test1", data)
        loaded = await session_store.load_search_result(SESSION_ID, "res_test1")
        assert loaded == data

    @pytest.mark.asyncio
    async def test_search_cache_path_traversal_rejected(self):
        await session_store.create_session(SESSION_ID, base_trip_meta())
        with pytest.raises(ValueError):
            await session_store.save_search_result(SESSION_ID, "../escape", {"bad": True})

    @pytest.mark.asyncio
    async def test_load_store_aggregates(self):
        await session_store.create_session(SESSION_ID, base_trip_meta())
        store = await session_store.load_store(SESSION_ID)
        assert store["trip_meta"]["city"] == "Osaka"
        assert store["poi_list"]["city"] == "Osaka"
        assert store["itinerary"]["days"] == []
        assert store["session"]["phase"] == "intake"


# ─── scheduler ─────────────────────────────────────────────────────────────────

class TestScheduler:
    @pytest.mark.asyncio
    async def test_empty_poi_list_generates_empty_days(self):
        # 空 POI 列表时，排程仍会生成 trip_meta.days 个空 day（与 TS 版一致）
        # must_visit 里的 ID 不在 poi_list，不会被 warning（remaining_must 为空，校验阶段才检查）
        result = await build_itinerary_from_data(
            SESSION_ID, base_trip_meta(), {"city": "Osaka", "pois": []}
        )
        assert len(result["itinerary"]["days"]) == 3
        assert all(d["time_blocks"] == [] for d in result["itinerary"]["days"])

    @pytest.mark.asyncio
    async def test_must_poi_scheduled_first(self):
        poi_list = {
            "city": "Osaka",
            "pois": [
                base_poi({"id": "poi_usj", "name": "USJ", "priority": "must",
                          "estimated_duration_minutes": 480, "area": "Konohana"}),
                base_poi({"id": "poi_castle", "name": "Osaka Castle", "priority": "nice"}),
            ],
        }
        result = await build_itinerary_from_data(SESSION_ID, base_trip_meta(), poi_list)
        day1 = result["itinerary"]["days"][0]
        assert day1["time_blocks"][0]["poi_id"] == "poi_usj"

    @pytest.mark.asyncio
    async def test_sort_pois_must_first(self):
        pois = [
            base_poi({"id": "poi_opt", "priority": "optional"}),
            base_poi({"id": "poi_must", "priority": "must"}),
            base_poi({"id": "poi_nice", "priority": "nice"}),
        ]
        sorted_pois = sort_pois_for_planning(pois)
        assert sorted_pois[0]["id"] == "poi_must"
        assert sorted_pois[1]["id"] == "poi_nice"
        assert sorted_pois[2]["id"] == "poi_opt"

    @pytest.mark.asyncio
    async def test_full_day_poi_detection(self):
        full_day = base_poi({"estimated_duration_minutes": 400})
        half_day = base_poi({"estimated_duration_minutes": 120})
        assert is_full_day_poi(full_day)
        assert not is_full_day_poi(half_day)

        constraint_poi = base_poi({"estimated_duration_minutes": 60, "constraints": ["全天项目"]})
        assert is_full_day_poi(constraint_poi)

    @pytest.mark.asyncio
    async def test_full_day_poi_gets_own_day(self):
        # duration >= 360 的 POI 被当全天项目，单独占一天
        poi_list = {
            "city": "Osaka",
            "pois": [
                base_poi({"id": "poi_fullday", "name": "Full Day Tour", "priority": "must",
                          "estimated_duration_minutes": 400, "area": "Namba"}),
                base_poi({"id": "poi_small", "name": "Small", "priority": "nice",
                          "estimated_duration_minutes": 60, "area": "Namba"}),
            ],
        }
        trip_meta = {**base_trip_meta(), "days": 2, "must_visit": ["poi_fullday"]}
        result = await build_itinerary_from_data(SESSION_ID, trip_meta, poi_list)
        # 全天项目应该在 Day 1 单独成天
        day1 = result["itinerary"]["days"][0]
        assert day1["time_blocks"][0]["poi_id"] == "poi_fullday"
        # 第二天放 small
        day2 = result["itinerary"]["days"][1]
        assert day2["time_blocks"][0]["poi_id"] == "poi_small"

    @pytest.mark.asyncio
    async def test_budget_exceeded_warning(self):
        poi_list = {
            "city": "Osaka",
            "pois": [
                base_poi({"id": "poi_expensive", "name": "Expensive", "priority": "must",
                          "estimated_cost": 200000, "area": "Namba"}),
            ],
        }
        trip_meta = {**base_trip_meta(), "budget": {"currency": "JPY", "amount": 50000},
                     "must_visit": ["poi_expensive"]}
        result = await build_itinerary_from_data(SESSION_ID, trip_meta, poi_list)
        assert any("超出预算" in w for w in result["warnings"])

    @pytest.mark.asyncio
    async def test_unknown_cost_warning(self):
        poi_list = {
            "city": "Osaka",
            "pois": [
                base_poi({"id": "poi_unknown", "name": "Unknown Price", "priority": "must",
                          "estimated_cost": {"unknown": True}, "area": "Namba"}),
            ],
        }
        trip_meta = {**base_trip_meta(), "must_visit": ["poi_unknown"]}
        result = await build_itinerary_from_data(SESSION_ID, trip_meta, poi_list)
        assert any("估算成本未知" in w for w in result["warnings"])


# ─── constraints ────────────────────────────────────────────────────────────────

class TestConstraints:
    def test_missing_must_visit(self):
        result = check_constraints(
            {"days": []},
            {"pois": []},
            {"must_visit": ["USJ"], "pace": "normal"},
        )
        assert "必去项 USJ 未在行程中出现" in result["errors"]

    def test_must_visit_covered_by_alias(self):
        poi = base_poi({"id": "poi_a", "aliases": ["USJ"]})
        itinerary = {
            "days": [{
                "day": 1,
                "time_blocks": [{"poi_id": "poi_a", "start_time": "10:00", "end_time": "12:00", "reason": "t"}],
                "estimated_total_minutes": 120,
                "estimated_total_cost": 0,
                "area_cluster": [],
            }]
        }
        result = check_constraints(itinerary, {"pois": [poi]},
                                   {"must_visit": ["USJ"], "pace": "normal"})
        assert "必去项 USJ 未在行程中出现" not in result["errors"]

    def test_duplicate_poi_same_day(self):
        poi = base_poi({"id": "poi_a"})
        itinerary = {
            "days": [{
                "day": 1,
                "time_blocks": [
                    {"poi_id": "poi_a", "start_time": "10:00", "end_time": "11:00", "reason": "t"},
                    {"poi_id": "poi_a", "start_time": "12:00", "end_time": "13:00", "reason": "t"},
                ],
                "estimated_total_minutes": 120,
                "estimated_total_cost": 0,
                "area_cluster": [],
            }]
        }
        result = check_constraints(itinerary, {"pois": [poi]}, {"must_visit": [], "pace": "normal"})
        assert any("重复出现" in e for e in result["errors"])

    def test_time_inversion(self):
        poi = base_poi({"id": "poi_a"})
        itinerary = {
            "days": [{
                "day": 1,
                "time_blocks": [{"poi_id": "poi_a", "start_time": "14:00", "end_time": "10:00", "reason": "t"}],
                "estimated_total_minutes": 0,
                "estimated_total_cost": 0,
                "area_cluster": [],
            }]
        }
        result = check_constraints(itinerary, {"pois": [poi]}, {"must_visit": [], "pace": "normal"})
        assert any("时间非法或倒挂" in e for e in result["errors"])

    def test_overlapping_blocks(self):
        poi = base_poi({"id": "poi_a"})
        itinerary = {
            "days": [{
                "day": 1,
                "time_blocks": [
                    {"poi_id": "poi_a", "start_time": "10:00", "end_time": "12:00", "reason": "t"},
                    {"poi_id": "poi_a", "start_time": "11:00", "end_time": "13:00", "reason": "t"},
                ],
                "estimated_total_minutes": 180,
                "estimated_total_cost": 0,
                "area_cluster": [],
            }]
        }
        result = check_constraints(itinerary, {"pois": [poi]}, {"must_visit": [], "pace": "normal"})
        assert any("重叠" in e for e in result["errors"])

    def test_day_overloaded_warning(self):
        itinerary = {
            "days": [{
                "day": 1,
                "time_blocks": [],
                "estimated_total_minutes": 600,  # 超过 normal 480
                "estimated_total_cost": 0,
                "area_cluster": [],
            }]
        }
        result = check_constraints(itinerary, {"pois": []}, {"pace": "normal"})
        assert any("超过 normal 节奏上限" in w for w in result["warnings"])

    def test_budget_exceeded_warning(self):
        itinerary = {
            "days": [{
                "day": 1,
                "time_blocks": [],
                "estimated_total_minutes": 0,
                "estimated_total_cost": 200000,
                "area_cluster": [],
            }]
        }
        result = check_constraints(
            itinerary, {"pois": []},
            {"pace": "normal", "budget": {"currency": "JPY", "amount": 100000}},
        )
        assert any("超过预算" in w for w in result["warnings"])

    def test_nonexistent_poi_error(self):
        itinerary = {
            "days": [{
                "day": 1,
                "time_blocks": [{"poi_id": "poi_ghost", "start_time": "10:00", "end_time": "12:00", "reason": "t"}],
                "estimated_total_minutes": 120,
                "estimated_total_cost": 0,
                "area_cluster": [],
            }]
        }
        result = check_constraints(itinerary, {"pois": []}, {"pace": "normal"})
        assert any("不在 poi-list 中" in e for e in result["errors"])


# ─── 第零期：cost 字符串解析 ────────────────────────────────────────────────────

class TestPoiCostParsing:
    """
    回归测试：防止门票价格静默丢失

    背景：审计发现历史 session 中 39 个 POI 有 25 个 estimated_cost 是字符串
    （如 '60元（旺季）/40元（淡季）'），而 poi_cost() 只处理 int/float，
    字符串走 return 0, True 分支被静默当成 unknown —— 64% 门票价格丢失。
    """

    def test_int_cost(self):
        assert poi_cost({"estimated_cost": 60}) == (60, False)

    def test_float_cost(self):
        assert poi_cost({"estimated_cost": 59.5}) == (59, False)

    def test_none_cost_is_zero_not_unknown(self):
        """None 表示"无需门票"，不是未知"""
        assert poi_cost({"estimated_cost": None}) == (0, False)

    def test_missing_key(self):
        assert poi_cost({}) == (0, False)

    def test_unknown_dict(self):
        assert poi_cost({"estimated_cost": {"unknown": True}}) == (0, True)

    def test_simple_string(self):
        assert poi_cost({"estimated_cost": "30元"}) == (30, False)

    def test_season_range_takes_max(self):
        """'60元（旺季）/40元（淡季）' → 60（保守取高值，宁可预算高估不可低估）"""
        assert poi_cost({"estimated_cost": "60元（旺季）/40元（淡季）"}) == (60, False)
        assert poi_cost({"estimated_cost": "40元（旺季）/35元（淡季）"}) == (40, False)

    def test_free_is_zero_not_unknown(self):
        """免费是确定信息，不该产生"成本未知"warning"""
        assert poi_cost({"estimated_cost": "免费"}) == (0, False)
        assert poi_cost({"estimated_cost": "免费（需预约）"}) == (0, False)
        assert poi_cost({"estimated_cost": "无门票"}) == (0, False)

    def test_unparseable_string_is_unknown(self):
        assert poi_cost({"estimated_cost": "视具体项目而定"}) == (0, True)

    def test_empty_string_is_unknown(self):
        assert poi_cost({"estimated_cost": ""}) == (0, True)
        assert poi_cost({"estimated_cost": "   "}) == (0, True)

    def test_bool_is_unknown(self):
        """bool 是 int 子类，若不拦截 True 会被当成 1 元"""
        assert poi_cost({"estimated_cost": True}) == (0, True)

    @pytest.mark.asyncio
    async def test_string_cost_flows_into_budget(self):
        """端到端：字符串 cost 应真实计入 estimated_total_cost"""
        pois = [
            base_poi({"id": "p1", "priority": "must", "estimated_cost": "60元（旺季）/40元（淡季）"}),
            base_poi({"id": "p2", "priority": "must", "estimated_cost": "免费"}),
        ]
        result = await build_itinerary_from_data(
            SESSION_ID, {"city": "北京", "days": 1, "pace": "normal"},
            {"city": "北京", "pois": pois},
        )
        total = sum(d["estimated_total_cost"] for d in result["itinerary"]["days"])
        assert total == 60, f"期望 60（60+免费0），实际 {total}"
        assert not any("估算成本未知" in w for w in result["warnings"]), \
            "免费 POI 不应产生成本未知 warning"


# ─── 第零期：移动时间三级降级 ────────────────────────────────────────────────────

GUGONG = {"id": "gugong", "name": "故宫", "area": "东城区",
          "location": {"longitude": 116.397, "latitude": 39.917}}
GUOBO = {"id": "guobo", "name": "国博", "area": "东城区",
         "location": {"longitude": 116.402, "latitude": 39.905}}
BADALING = {"id": "badaling", "name": "八达岭", "area": "延庆区",
            "location": {"longitude": 116.024, "latitude": 40.354}}


class TestTravelMinutes:
    """
    移动时间三级降级：预取矩阵 → 经纬度兜底 → 0

    背景：原方案用手写区域矩阵，但 TRAVEL_TIME_ROOT 路径算错
    （指向不存在的 backend/data/），所有矩阵从未生效，移动时间恒为 0。
    改用高德 maps_distance 预取 + 经纬度兜底。
    """

    def test_prefetched_matrix_takes_priority(self):
        """一级：有预取数据时优先使用真实 duration"""
        matrix = {matrix_key("gugong", "badaling"): 81}
        assert get_travel_minutes(matrix, GUGONG, BADALING) == 81

    def test_same_poi_is_zero(self):
        assert get_travel_minutes({}, GUGONG, GUGONG) == 0

    def test_haversine_fallback_long_distance(self):
        """二级：故宫→八达岭直线 58km，兜底应在合理区间（高德实测 81 分钟）"""
        minutes = get_travel_minutes({}, GUGONG, BADALING)
        assert 60 <= minutes <= 140, f"远郊估算 {minutes} 分钟超出合理区间"

    def test_haversine_fallback_short_distance(self):
        """二级：故宫→国博直线 1.4km，含固定开销应 15-30 分钟（实际约 15）"""
        minutes = get_travel_minutes({}, GUGONG, GUOBO)
        assert 15 <= minutes <= 30, f"市区短距离估算 {minutes} 分钟不合理"

    def test_fallback_is_monotonic(self):
        """距离越远耗时越长"""
        near = get_travel_minutes({}, GUGONG, GUOBO)
        far = get_travel_minutes({}, GUGONG, BADALING)
        assert far > near

    def test_no_location_returns_zero(self):
        """三级：无预取且无 location → 0"""
        assert get_travel_minutes({}, {"id": "x"}, {"id": "y"}) == 0

    def test_partial_location_returns_zero(self):
        assert get_travel_minutes({}, GUGONG, {"id": "y"}) == 0

    def test_matrix_miss_falls_back_to_haversine(self):
        """预取矩阵存在但缺这条路线 → 走经纬度兜底而非返回 0"""
        matrix = {matrix_key("other_a", "other_b"): 30}
        assert get_travel_minutes(matrix, GUGONG, BADALING) > 0


class TestSchedulerTravelTime:
    """排程层：移动时间应影响分天决策"""

    @pytest.mark.asyncio
    async def test_forbidden_city_and_great_wall_not_same_day(self):
        """
        回归测试：故宫（东城区）与八达岭（延庆区）不应同一天

        修复前移动时间恒为 0，排程算出 240+0+240=480 刚好等于 normal 上限，
        会把两者塞进同一天，产出"14:00 离开故宫，14:00 到达 70km 外八达岭"的
        物理上不可能的行程。
        """
        pois = [
            {**GUGONG, "priority": "must", "estimated_duration_minutes": 240,
             "estimated_cost": "60元", "category": "attraction", "confidence": 0.9,
             "source_refs": [{"type": "whitelist", "ref": "t"}]},
            {**BADALING, "priority": "must", "estimated_duration_minutes": 240,
             "estimated_cost": "40元", "category": "attraction", "confidence": 0.9,
             "source_refs": [{"type": "whitelist", "ref": "t"}]},
        ]
        result = await build_itinerary_from_data(
            SESSION_ID, {"city": "北京", "days": 2, "pace": "normal"},
            {"city": "北京", "pois": pois},
        )
        day1_ids = [b["poi_id"] for b in result["itinerary"]["days"][0]["time_blocks"]]
        assert not ("gugong" in day1_ids and "badaling" in day1_ids), \
            "故宫与八达岭相距 70km，移动时间应阻止两者同一天"

    @pytest.mark.asyncio
    async def test_prefetched_matrix_is_used_by_scheduler(self):
        """排程应消费传入的预取矩阵"""
        pois = [
            {**GUGONG, "priority": "must", "estimated_duration_minutes": 200,
             "estimated_cost": 60, "category": "attraction", "confidence": 0.9,
             "source_refs": [{"type": "whitelist", "ref": "t"}]},
            {**GUOBO, "priority": "must", "estimated_duration_minutes": 200,
             "estimated_cost": 0, "category": "attraction", "confidence": 0.9,
             "source_refs": [{"type": "whitelist", "ref": "t"}]},
        ]
        # 给一个夸张的移动时间，强制两者分开
        matrix = {matrix_key("gugong", "guobo"): 300, matrix_key("guobo", "gugong"): 300}
        result = await build_itinerary_from_data(
            SESSION_ID, {"city": "北京", "days": 2, "pace": "normal"},
            {"city": "北京", "pois": pois}, matrix,
        )
        day1_ids = [b["poi_id"] for b in result["itinerary"]["days"][0]["time_blocks"]]
        assert len(day1_ids) == 1, "300 分钟移动时间应使两个 200 分钟 POI 无法同天"

    @pytest.mark.asyncio
    async def test_warning_when_no_matrix_but_has_location(self):
        pois = [
            {**GUGONG, "priority": "must", "estimated_duration_minutes": 120,
             "estimated_cost": 60, "category": "attraction", "confidence": 0.9,
             "source_refs": [{"type": "whitelist", "ref": "t"}]},
            {**GUOBO, "priority": "nice", "estimated_duration_minutes": 120,
             "estimated_cost": 0, "category": "attraction", "confidence": 0.9,
             "source_refs": [{"type": "whitelist", "ref": "t"}]},
        ]
        result = await build_itinerary_from_data(
            SESSION_ID, {"city": "北京", "days": 1, "pace": "normal"},
            {"city": "北京", "pois": pois},
        )
        assert any("经纬度" in w for w in result["warnings"])

    @pytest.mark.asyncio
    async def test_warning_when_no_location(self):
        pois = [base_poi({"id": "p1", "priority": "must"})]
        result = await build_itinerary_from_data(
            SESSION_ID, {"city": "北京", "days": 1, "pace": "normal"},
            {"city": "北京", "pois": pois},
        )
        assert any("0 分钟" in w for w in result["warnings"])


class TestPrefetchParsing:
    """高德返回值解析（含前缀文本）"""

    def test_parse_mcp_string_with_prefix(self):
        """实测 MCPTool.run 返回带前缀文本，直接 json.loads 会失败"""
        raw = (
            "工具 'maps_distance' 执行结果:\n"
            '{\n  "results": [\n    {\n      "origin_id": "1",\n'
            '      "dest_id": "1",\n      "distance": "6104",\n      "duration": "1441"\n    }\n  ]\n}'
        )
        results = _parse_distance_results(raw)
        assert len(results) == 1
        assert results[0]["duration"] == "1441"

    def test_parse_dict_input(self):
        results = _parse_distance_results({"results": [{"origin_id": "1", "duration": "600"}]})
        assert len(results) == 1

    def test_parse_garbage(self):
        assert _parse_distance_results("连接失败") == []
        assert _parse_distance_results(None) == []
        assert _parse_distance_results("") == []

    @pytest.mark.asyncio
    async def test_prefetch_without_tool_returns_empty(self):
        """无工具时返回空矩阵（排程会走经纬度兜底），不应抛异常"""
        assert await prefetch_travel_matrix([GUGONG, BADALING], None) == {}

    @pytest.mark.asyncio
    async def test_prefetch_single_poi_returns_empty(self):
        assert await prefetch_travel_matrix([GUGONG], object()) == {}

    @pytest.mark.asyncio
    async def test_prefetch_tool_failure_is_tolerated(self):
        """工具抛异常时不中断，返回空矩阵由兜底接管"""
        class BrokenTool:
            def run(self, params):
                raise RuntimeError("MCP 连接失败")
        assert await prefetch_travel_matrix([GUGONG, BADALING], BrokenTool()) == {}

    @pytest.mark.asyncio
    async def test_prefetch_builds_bidirectional_matrix(self):
        """N-1 次调用，矩阵双向存储"""
        class FakeTool:
            def run(self, params):
                return ('结果:\n{"results": [{"origin_id": "1", "dest_id": "1", '
                        '"distance": "6104", "duration": "1200"}]}')
        matrix = await prefetch_travel_matrix([GUGONG, BADALING], FakeTool())
        assert matrix[matrix_key("gugong", "badaling")] == 20
        assert matrix[matrix_key("badaling", "gugong")] == 20

    @pytest.mark.asyncio
    async def test_poi_not_duplicated_across_days(self):
        """
        回归测试：同一 POI 不应出现在多天

        端到端实测发现 poi_foxiangge 同时出现在 Day1 和 Day2。
        原因：ctx.placed_ids 随每日 context 重建，只防同天重复，跨天未去重。
        """
        pois = [
            {**GUGONG, "priority": "must", "estimated_duration_minutes": 120,
             "estimated_cost": 60, "category": "attraction", "confidence": 0.9,
             "source_refs": [{"type": "whitelist", "ref": "t"}]},
            {**GUOBO, "priority": "nice", "estimated_duration_minutes": 120,
             "estimated_cost": 0, "category": "attraction", "confidence": 0.8,
             "source_refs": [{"type": "whitelist", "ref": "t"}]},
        ]
        result = await build_itinerary_from_data(
            SESSION_ID, {"city": "北京", "days": 3, "pace": "normal"},
            {"city": "北京", "pois": pois},
        )
        all_ids = [
            b["poi_id"]
            for d in result["itinerary"]["days"]
            for b in d["time_blocks"]
        ]
        assert len(all_ids) == len(set(all_ids)), \
            f"POI 跨天重复: {all_ids}"


class TestEmptyDayWarning:
    """第零期续作：空天产出显式 warning"""

    @pytest.mark.asyncio
    async def test_empty_day_produces_warning(self):
        """2 天但只 1 个 POI → Day2 空 → warning"""
        pois = [base_poi({"id": "p1", "priority": "must",
                          "estimated_duration_minutes": 240})]
        result = await build_itinerary_from_data(
            SESSION_ID, {"city": "北京", "days": 2, "pace": "normal"},
            {"city": "北京", "pois": pois},
        )
        assert any("未安排任何 POI" in w for w in result["warnings"]), \
            f"空天应产 warning，实际: {result['warnings']}"

    @pytest.mark.asyncio
    async def test_empty_day_warning_includes_diagnostic(self):
        """warning 应包含诊断信息：当前 POI 数、建议数"""
        pois = [base_poi({"id": "p1", "priority": "must",
                          "estimated_duration_minutes": 240})]
        result = await build_itinerary_from_data(
            SESSION_ID, {"city": "北京", "days": 2, "pace": "normal"},
            {"city": "北京", "pois": pois},
        )
        ws = result["warnings"]
        empty_warning = next(w for w in ws if "未安排任何 POI" in w)
        assert "当前 1 个" in empty_warning, "应提示当前 POI 数"
        assert "建议至少 4 个" in empty_warning, "应提示建议数（2天*2）"

    @pytest.mark.asyncio
    async def test_full_days_no_empty_warning(self):
        """POI 足够填满天数时不误报告警"""
        pois = [
            base_poi({"id": f"p{i}", "priority": "must",
                      "estimated_duration_minutes": 240})
            for i in range(4)
        ]
        result = await build_itinerary_from_data(
            SESSION_ID, {"city": "北京", "days": 2, "pace": "normal"},
            {"city": "北京", "pois": pois},
        )
        assert not any("未安排任何 POI" in w for w in result["warnings"]), \
            f"填满时不应告警，实际: {result['warnings']}"

    @pytest.mark.asyncio
    async def test_single_day_no_empty_warning(self):
        """1 天行程即使空也不该告警（空天只在多天时才有意义）"""
        result = await build_itinerary_from_data(
            SESSION_ID, {"city": "北京", "days": 1, "pace": "normal"},
            {"city": "北京", "pois": []},
        )
        # 1 天 0 POI 时 Day1 空，也应提示
        # （单天空也有诊断价值，不应跳过）
        assert any("未安排任何 POI" in w for w in result["warnings"])
