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
from app.core.scheduler import build_itinerary_from_data, sort_pois_for_planning, is_full_day_poi
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
