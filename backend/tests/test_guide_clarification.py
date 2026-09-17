"""攻略入口不应把模型的可选人数追问当成规划门槛。"""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.schemas import IntentResult
from app.api.routes import _planning_error_response


@pytest.mark.parametrize("travelers", [None, {"adults": None, "children": None}])
def test_family_preference_does_not_require_counts(travelers):
    intent = IntentResult(
        trip_meta={"city": "南京", "days": 2, "preferences": "人文历史、亲子游",
                   "transportation": "自驾", "budget": {"amount": 3000},
                   "travelers": travelers},
        missing_fields=["travelers"],
        clarification_question="方便说下几个大人几个小朋友吗？",
    )
    planner = SimpleNamespace(generate_guide_routes=AsyncMock(return_value={"status": "ok"}))
    result = _request(intent, planner)
    assert result["status"] == "ok"
    planner.generate_guide_routes.assert_awaited_once()
    assert planner.generate_guide_routes.call_args.args[0]["preferences"] == "人文历史、亲子游"


@pytest.mark.parametrize("meta, expected", [({"city": "南京"}, "几天"), ({"city": "南京", "days": 0}, "1-30")])
def test_required_fields_are_checked_even_when_llm_reports_complete(meta, expected):
    intent = IntentResult(trip_meta=meta, missing_fields=[], clarification_question="几个小朋友？")
    planner = SimpleNamespace(generate_guide_routes=AsyncMock())
    result = _request(intent, planner)
    assert result["error_code"] == "NEEDS_CLARIFICATION"
    assert expected in result["error_message"]
    assert "小朋友" not in result["error_message"]
    planner.generate_guide_routes.assert_not_awaited()


def test_conversation_does_not_enter_trip_planning_workflow():
    planner = SimpleNamespace(generate_guide_routes=AsyncMock())
    result = _request(IntentResult(intent="conversation", chat_reply="你好呀，很高兴见到你。"), planner, query="你好")
    assert result["status"] == "ok"
    assert result["action"] == "conversation"
    assert result["assistant_message"] == "你好呀，很高兴见到你。"
    planner.generate_guide_routes.assert_not_awaited()


def test_cancel_message_does_not_enter_trip_planning_workflow():
    planner = SimpleNamespace(generate_guide_routes=AsyncMock())
    result = _request(IntentResult(intent="conversation", chat_reply="没关系，随时改变主意都可以。"), planner, query="我不想去了")
    assert result["status"] == "ok"
    assert result["action"] == "conversation"
    assert "没关系" in result["assistant_message"]
    planner.generate_guide_routes.assert_not_awaited()


def test_current_trip_question_uses_session_without_generating_new_routes():
    intent = IntentResult(intent="current_trip_question")
    planner = SimpleNamespace(generate_guide_routes=AsyncMock())
    orch = SimpleNamespace(intent_recognizer=SimpleNamespace(recognize=AsyncMock(return_value=intent)))
    loaded = {
        "trip_meta": {"city": "南京", "days": 2},
        "poi_list": {"pois": [{"id": "p1", "name": "夫子庙"}]},
        "itinerary": {"days": [{"day": 1, "time_blocks": [{"poi_id": "p1"}]}]},
    }
    with patch("app.api.routes._get_orchestrator", return_value=orch), patch("app.api.routes._get_planner", return_value=planner), patch("app.api.routes.session_store.load_session", new=AsyncMock(return_value=loaded)):
        response = TestClient(app).post("/api/trip/guide-routes/stream", json={"query": "还记得上面的行程吗？", "session_id": "session_123"})
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    result = next(event["data"] for event in events if event["type"] == "guide_done")
    assert result["status"] == "ok"
    assert result["action"] == "current_trip_question"
    assert "夫子庙" in result["assistant_message"]
    planner.generate_guide_routes.assert_not_awaited()


def test_other_users_session_is_not_used_as_current_trip_context():
    intent = IntentResult(intent="current_trip_question")
    planner = SimpleNamespace(generate_guide_routes=AsyncMock())
    orch = SimpleNamespace(intent_recognizer=SimpleNamespace(recognize=AsyncMock(return_value=intent)))
    loaded = {
        "session": {"user_id": "another_user"},
        "trip_meta": {"city": "南京", "days": 2},
        "poi_list": {"pois": [{"id": "p1", "name": "夫子庙"}]},
        "itinerary": {"days": [{"day": 1, "time_blocks": [{"poi_id": "p1"}]}]},
    }
    with patch("app.api.routes._get_orchestrator", return_value=orch), patch("app.api.routes._get_planner", return_value=planner), patch("app.api.routes.session_store.load_session", new=AsyncMock(return_value=loaded)):
        response = TestClient(app).post("/api/trip/guide-routes/stream", json={
            "query": "还记得上面的行程吗？", "session_id": "session_123", "user_id": "lumina_user",
        })
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    result = next(event["data"] for event in events if event["type"] == "guide_done")
    assert result["action"] == "clarification"
    assert "夫子庙" not in (result["assistant_message"] or result["error_message"] or "")
    planner.generate_guide_routes.assert_not_awaited()


def test_conversation_recall_uses_visible_chat_context():
    planner = SimpleNamespace(generate_guide_routes=AsyncMock())
    orch = SimpleNamespace(intent_recognizer=SimpleNamespace(recognize=AsyncMock(return_value=IntentResult(intent="conversation_context_question"))))
    with patch("app.api.routes._get_orchestrator", return_value=orch), patch("app.api.routes._get_planner", return_value=planner):
        response = TestClient(app).post("/api/trip/guide-routes/stream", json={
            "query": "你还记得上面的内容吗？",
            "conversation_context": "用户：帮我规划南京两日游\n小渡：我整理了三条路线方案",
        })
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    result = next(event["data"] for event in events if event["type"] == "guide_done")
    assert result["status"] == "ok"
    assert result["action"] == "conversation"
    assert "南京两日游" in result["assistant_message"]
    planner.generate_guide_routes.assert_not_awaited()


@pytest.mark.parametrize(
    ("exc", "expected_code"),
    [
        (TimeoutError("timed out"), "UPSTREAM_TIMEOUT"),
        (ConnectionError("connection refused"), "UPSTREAM_UNAVAILABLE"),
        (ValueError("bad response"), "INTERNAL_ERROR"),
    ],
)
def test_external_failures_have_stable_user_facing_codes(exc, expected_code):
    code, message = _planning_error_response(exc)
    assert code == expected_code
    assert message


def _request(intent, planner, query="帮我规划南京两日游，2天，偏好人文历史、亲子游，自驾出行，预算3000元左右"):
    orch = SimpleNamespace(intent_recognizer=SimpleNamespace(recognize=AsyncMock(return_value=intent)))
    with patch("app.api.routes._get_orchestrator", return_value=orch), patch("app.api.routes._get_planner", return_value=planner):
        response = TestClient(app).post("/api/trip/guide-routes/stream", json={
            "query": query,
        })
    assert response.status_code == 200
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    return next(event["data"] for event in events if event["type"] == "guide_done")
