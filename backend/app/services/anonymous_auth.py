"""无需注册的匿名用户身份：签名 Cookie 防止客户端伪造 user_id。"""
import base64
import hashlib
import hmac
import json
import time
import uuid
from typing import Optional

COOKIE_NAME = "trip_agent_identity"
MAX_AGE_SECONDS = 60 * 60 * 24 * 365


def _secret() -> bytes:
    # 本地 MVP 可直接运行；部署环境必须通过环境变量替换为随机强密钥。
    from app.config import settings
    return settings.anonymous_auth_secret.encode("utf-8")


def create_identity() -> tuple[str, str]:
    user_id = f"usr_{uuid.uuid4().hex}"
    payload = {"uid": user_id, "exp": int(time.time()) + MAX_AGE_SECONDS}
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).rstrip(b"=")
    signature = hmac.new(_secret(), encoded, hashlib.sha256).hexdigest().encode("ascii")
    return user_id, (encoded + b"." + signature).decode("ascii")


def verify_identity(token: Optional[str]) -> Optional[str]:
    if not token or "." not in token:
        return None
    encoded, supplied = token.encode("utf-8").rsplit(b".", 1)
    expected = hmac.new(_secret(), encoded, hashlib.sha256).hexdigest().encode("ascii")
    if not hmac.compare_digest(supplied, expected):
        return None
    try:
        raw = base64.urlsafe_b64decode(encoded + b"=" * (-len(encoded) % 4))
        payload = json.loads(raw)
        if int(payload.get("exp", 0)) < time.time() or not str(payload.get("uid", "")).startswith("usr_"):
            return None
        return str(payload["uid"])
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
