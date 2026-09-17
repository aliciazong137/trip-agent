"""
API 路由契约测试 — test_routes.py

使用 FastAPI TestClient（同步）和 httpx.AsyncClient（异步），
Mock LLM / session_store 避免真实网络依赖。

覆盖：
  GET  /health
  POST /api/trip/plan-nl  → needs_clarification / ok / failed
  GET  /api/trip/session/{id}/map
  POST /api/trip/session/{id}/revise-nl → ok / failed
  非法 session_id → 404 / failed
"""
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

# ---------- 被测 App ----------
from app.main import app
from app.models.schemas import (
    NlTripPlanResponse,
    ReviseNlResponse,
    TripMeta,
)

client = TestClient(app, raise_server_exceptions=False)

# session ID 符合格式 sess_ + 12位 hex
SESS_A = "sess_aabbcc001122"
SESS_B = "sess_bbccdd002233"
SESS_MISSING = "sess_000000000000"   # 通过格式校验，但 load_session 返回 None


def _fake_session(city: str = "南京", days: int = 2, version: int = 1) -> dict:
    """构造一个最小可用的 session dict（与 load_session 返回结构一致）"""
    return {
        "trip_meta": {"city": city, "days": days},
        "itinerary": {
            "session_id": SESS_A,
            "city": city,
            "version": version,
            "days": [
                {
                    "day": 1,
                    "time_blocks": [
                        {"poi_id": "poi_001", "start_time": "09:00", "end_time": "11:00",
                         "reason": "必去", "locked": False},
                        {"poi_id": "poi_002", "start_time": "13:00", "end_time": "15:00",
                         "reason": "推荐", "locked": False},
                    ],
                },
                {
                    "day": 2,
                    "time_blocks": [
                        {"poi_id": "poi_003", "start_time": "09:00", "end_time": "11:00",
                         "reason": "必去", "locked": False},
                    ],
                },
            ],
        },
        "poi_list": {
            "city": city,
            "pois": [
                {"id": "poi_001", "name": "中山陵", "category": "attraction",
                 "area": "城东", "priority": "must"},
                {"id": "poi_002", "name": "夫子庙", "category": "attraction",
                 "area": "城南", "priority": "must"},
                {"id": "poi_003", "name": "玄武湖", "category": "attraction",
                 "area": "城北", "priority": "nice"},
            ],
        },
    }


def _fake_map_data(city: str = "南京") -> dict:
    return {
        "city": city,
        "days": [
            {
                "day": 1,
                "city": city,
                "transportation": "公共交通",
                "route_ready": True,
                "unmapped_points": [],
                "points": [
                    {"order": 1, "poi_id": "poi_001", "name": "中山陵",
                     "location": {"longitude": 118.84, "latitude": 32.07},
                     "start_time": None, "end_time": None},
                    {"order": 2, "poi_id": "poi_002", "name": "夫子庙",
                     "location": {"longitude": 118.79, "latitude": 32.02},
                     "start_time": None, "end_time": None},
                ],
            }
        ],
    }


# ─── 1. 健康检查 ──────────────────────────────────────────────────────────────

class TestHealth:
    def test_health_returns_200(self):
        r = client.get("/health")
        assert r.status_code == 200

    def test_health_body_has_status_ok(self):
        r = client.get("/health")
        body = r.json()
        assert body["status"] == "ok"

    def test_health_body_has_llm_model(self):
        r = client.get("/health")
        body = r.json()
        assert "llm_model" in body


class TestTripContext:
    def test_context_question_reads_existing_session_without_planning(self):
        with patch("app.api.routes.session_store.load_session", new=AsyncMock(return_value=_fake_session())):
            response = client.post(f"/api/trip/session/{SESS_A}/context", json={"query": "你还记得上面的行程吗？"})
        assert response.status_code == 200
        assert "南京2日行程" in response.json()["message"]
        assert "Day1：中山陵 → 夫子庙" in response.json()["message"]


# ─── 2. POST /api/trip/plan-nl ────────────────────────────────────────────────

class TestPlanNl:
    def test_missing_query_returns_422(self):
        r = client.post("/api/trip/plan-nl", json={})
        assert r.status_code == 422

    def test_single_char_query_accepted(self):
        """单字 query（如中文「好」）应被接受，不再 422（min_length=1）"""
        mock_resp = NlTripPlanResponse(
            status="needs_clarification",
            session_id=SESS_A,
            clarification_question="你好呀，想去哪里玩呢？",
            missing_fields=["city"],
        )
        with patch(
            "app.api.routes._get_orchestrator",
            return_value=type("Orch", (), {
                "plan_from_nl": AsyncMock(return_value=mock_resp)
            }),
        ):
            r = client.post("/api/trip/plan-nl", json={"query": "好"})
            assert r.status_code == 200
            assert r.json()["status"] == "needs_clarification"

    def test_vague_query_returns_needs_clarification(self):
        """模糊 query → orchestrator 应返回 needs_clarification（不调真实 LLM）"""
        mock_resp = NlTripPlanResponse(
            status="needs_clarification",
            session_id=SESS_A,
            clarification_question="请问您想去哪个城市？",
            missing_fields=["city"],
        )

        with patch(
            "app.api.routes._get_orchestrator",
            return_value=type("Orch", (), {
                "plan_from_nl": AsyncMock(return_value=mock_resp)
            })(),
        ):
            r = client.post(
                "/api/trip/plan-nl",
                json={"query": "我想出去玩", "user_id": "test"},
            )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "needs_clarification"
        assert body["session_id"] == SESS_A
        assert "clarification_question" in body

    def test_complete_query_returns_ok(self):
        """完整 query → orchestrator 返回 ok + session_id"""
        meta = TripMeta(city="南京", days=2)
        mock_resp = NlTripPlanResponse(
            status="ok",
            session_id=SESS_B,
            trip_meta=meta,
        )

        with patch(
            "app.api.routes._get_orchestrator",
            return_value=type("Orch", (), {
                "plan_from_nl": AsyncMock(return_value=mock_resp)
            })(),
        ):
            r = client.post(
                "/api/trip/plan-nl",
                json={"query": "我想去南京玩2天，公共交通", "user_id": "test"},
            )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["session_id"] == SESS_B

    def test_planner_exception_returns_failed(self):
        """Planner 抛出异常 → 路由降级为 failed，不返回 500"""
        with patch(
            "app.api.routes._get_orchestrator",
            return_value=type("Orch", (), {
                "plan_from_nl": AsyncMock(side_effect=RuntimeError("LLM 超时"))
            })(),
        ):
            r = client.post(
                "/api/trip/plan-nl",
                json={"query": "我想去南京玩2天", "user_id": "test"},
            )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "failed"
        assert body["error_code"] == "INTERNAL_ERROR"


# ─── 3. GET /api/trip/session/{id}/map ───────────────────────────────────────

class TestSessionMap:
    def test_valid_session_returns_map_data(self):
        from app.models.schemas import MapData, DayRouteMap
        fake_map = MapData(**_fake_map_data())

        with (
            patch("app.api.routes.session_store.load_session",
                  new=AsyncMock(return_value=_fake_session())),
            patch("app.services.map_service.build_map_data", return_value=fake_map),
        ):
            r = client.get(f"/api/trip/session/{SESS_A}/map")
        assert r.status_code == 200
        body = r.json()
        assert "days" in body

    def test_missing_session_returns_404(self):
        with patch("app.api.routes.session_store.load_session",
                   new=AsyncMock(return_value=None)):
            r = client.get(f"/api/trip/session/{SESS_MISSING}/map")
        assert r.status_code == 404


# ─── 4. POST /api/trip/session/{id}/revise-nl ────────────────────────────────

class TestReviseNl:
    def test_missing_query_returns_422(self):
        r = client.post(f"/api/trip/session/{SESS_A}/revise-nl", json={})
        assert r.status_code == 422

    def test_valid_revise_returns_ok(self):
        """修订成功 → status ok，itinerary_version 递增"""
        fake = _fake_session(version=1)

        with (
            patch("app.agents.trip_orchestrator.amap_service.geocode", new=AsyncMock(return_value={"longitude": 118.8, "latitude": 32.0})),
            patch("app.agents.trip_orchestrator.session_store.save_poi_list", new=AsyncMock()),
            patch("app.agents.trip_orchestrator.session_store.load_session",
                  new=AsyncMock(return_value=fake)),
            patch("app.agents.trip_orchestrator.session_store.save_itinerary",
                  new=AsyncMock()),
            patch(
                "app.agents.trip_orchestrator.TripPlanOrchestrator._call_llm_for_revise",
                new=AsyncMock(
                    return_value=(
                        "已为你减少下午步行，并加入附近咖啡馆。\n"
                        "```json\n[{\"name\": \"先锋书店\", \"reason\": \"用户希望放松\"}, "
                        "{\"name\": \"老门东\", \"reason\": \"步行友好\"}]\n```"
                    )
                ),
            ),
        ):
            r = client.post(
                f"/api/trip/session/{SESS_A}/revise-nl",
                json={"query": "下午少走路，加一个咖啡馆", "day": 1, "user_id": "test"},
            )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["session_id"] == SESS_A
        assert isinstance(body["message"], str) and len(body["message"]) > 0
        # 修订后版本号应递增
        assert body["itinerary_version"] == 2

    def test_session_not_found_returns_failed(self):
        with patch("app.agents.trip_orchestrator.session_store.load_session",
                   new=AsyncMock(return_value=None)):
            r = client.post(
                f"/api/trip/session/{SESS_MISSING}/revise-nl",
                json={"query": "调整一下行程", "user_id": "test"},
            )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "failed"
        assert body["error_code"] == "SESSION_NOT_FOUND"

    def test_llm_error_returns_failed(self):
        """LLM 调用失败 → 稳定 failed 响应，不返回 500"""
        fake = _fake_session()
        with (
            patch("app.agents.trip_orchestrator.session_store.load_session",
                  new=AsyncMock(return_value=fake)),
            patch(
                "app.agents.trip_orchestrator.TripPlanOrchestrator._call_llm_for_revise",
                new=AsyncMock(side_effect=RuntimeError("LLM API 超时")),
            ),
        ):
            r = client.post(
                f"/api/trip/session/{SESS_A}/revise-nl",
                json={"query": "加一个博物馆", "user_id": "test"},
            )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "failed"
        assert body["error_code"] == "LLM_ERROR"

    def test_revise_without_structured_output_returns_ok_no_map_update(self):
        """LLM 仅回复自然语言（无 JSON 块） → status ok，版本不变"""
        fake = _fake_session(version=3)
        with (
            patch("app.agents.trip_orchestrator.session_store.load_session",
                  new=AsyncMock(return_value=fake)),
            patch(
                "app.agents.trip_orchestrator.TripPlanOrchestrator._call_llm_for_revise",
                new=AsyncMock(return_value="您的行程已经很紧凑了，建议保持现状。"),
            ),
        ):
            r = client.post(
                f"/api/trip/session/{SESS_A}/revise-nl",
                json={"query": "还有没有什么可以改的？", "user_id": "test"},
            )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        # 没有结构化修改 → 版本不变
        assert body["itinerary_version"] == 3
