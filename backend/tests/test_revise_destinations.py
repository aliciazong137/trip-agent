from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.services.map_service import build_map_data


def test_explicit_destination_retries_and_persists_map_point():
    session = {
        "trip_meta": {"city": "北京", "days": 2},
        "poi_list": {"city": "北京", "pois": [
            {"id": "old", "name": "孔庙和国子监博物馆", "location": {"longitude": 116.41, "latitude": 39.94}},
            {"id": "park", "name": "北海公园", "location": {"longitude": 116.39, "latitude": 39.92}},
        ]},
        "itinerary": {"version": 1, "days": [
            {"day": 1, "time_blocks": [{"poi_id": "old"}, {"poi_id": "park"}]},
            {"day": 2, "time_blocks": [{"poi_id": "park"}]},
        ]},
    }
    with (
        patch("app.agents.trip_orchestrator.session_store.load_session", new=AsyncMock(return_value=session)),
        patch("app.agents.trip_orchestrator.session_store.save_poi_list", new=AsyncMock()) as save_pois,
        patch("app.agents.trip_orchestrator.session_store.save_itinerary", new=AsyncMock()) as save_trip,
        patch("app.agents.trip_orchestrator.amap_service.geocode", new=AsyncMock(return_value={"longitude": 116.414, "latitude": 39.946})),
        patch("app.agents.trip_orchestrator.TripPlanOrchestrator._call_llm_for_revise", new=AsyncMock(side_effect=[
            "国子监已包含在博物馆中，不做额外调整。",
            '已调整。```json\n[{"name":"国子监"},{"name":"北海公园"}]\n```',
        ])) as llm,
    ):
        response = TestClient(app).post("/api/trip/session/sess_aabbcc001122/revise-nl", json={"query": "我想去国子监", "day": 1}).json()
    assert response["status"] == "ok"
    assert response["itinerary_version"] == 2
    assert llm.await_count == 2
    itinerary = save_trip.call_args.args[1]
    assert itinerary["days"][1] == session["itinerary"]["days"][1]
    mapped = build_map_data(session["trip_meta"], save_pois.call_args.args[1], itinerary)
    assert [p.name for p in mapped.days[0].points] == ["国子监", "北海公园"]
    assert mapped.days[0].unmapped_points == []


def test_explicit_request_without_action_does_not_report_success():
    with (
        patch("app.agents.trip_orchestrator.session_store.load_session", new=AsyncMock(return_value={"trip_meta": {"city": "北京"}, "itinerary": {"version": 1}})),
        patch("app.agents.trip_orchestrator.TripPlanOrchestrator._call_llm_for_revise", new=AsyncMock(return_value="已包含，不做调整。")),
        patch("app.agents.trip_orchestrator.session_store.save_itinerary", new=AsyncMock()) as save,
    ):
        response = TestClient(app).post("/api/trip/session/sess_aabbcc001122/revise-nl", json={"query": "我想去国子监"}).json()
    assert response["status"] == "failed"
    assert response["error_code"] == "REVISE_NO_CHANGES"
    save.assert_not_awaited()
