"""匿名用户 Cookie：不注册也能隔离行程和 Memory 的身份边界。"""
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.api.routes import _authenticated_user_id
from app.main import app
from app.services.anonymous_auth import COOKIE_NAME, create_identity, verify_identity


def test_signed_identity_round_trip_and_tampering_is_rejected():
    user_id, token = create_identity()
    assert verify_identity(token) == user_id
    assert verify_identity(token[:-1] + ("0" if token[-1] != "0" else "1")) is None


def test_anonymous_endpoint_reuses_cookie_identity():
    with TestClient(app) as client:
        first = client.post("/api/auth/anonymous")
        assert first.status_code == 200
        first_user_id = first.json()["user_id"]
        assert first_user_id.startswith("usr_")
        assert COOKIE_NAME in first.headers.get("set-cookie", "")

        second = client.post("/api/auth/anonymous")
        assert second.status_code == 200
        assert second.json()["user_id"] == first_user_id


def test_session_of_another_anonymous_user_is_hidden():
    owner_id, owner_token = create_identity()
    intruder_id, intruder_token = create_identity()
    loaded = {
        "session": {"session_id": "sess_aabbcc001122", "user_id": owner_id},
        "trip_meta": {"city": "南京", "days": 2},
        "poi_list": {"pois": []},
        "itinerary": {"days": []},
    }
    with TestClient(app, raise_server_exceptions=False) as client:
        client.cookies.set(COOKIE_NAME, intruder_token)
        with patch("app.api.routes.session_store.load_session", new=AsyncMock(return_value=loaded)):
            response = client.get("/api/trip/session/sess_aabbcc001122")
    assert intruder_id != owner_id
    assert response.status_code == 404
