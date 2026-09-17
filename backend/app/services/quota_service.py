"""匿名试用额度：服务端持久化计数，客户端无法篡改。"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import settings

_LOCK = threading.Lock()
_TIMEZONE = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class QuotaDecision:
    allowed: bool
    is_developer: bool
    remaining: int | None = None
    limit: int | None = None


def _storage_path() -> Path:
    path = Path(settings.quota_storage_path)
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[3] / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def is_developer(user_id: str) -> bool:
    return bool(user_id) and user_id in settings.admin_user_ids


def _limits() -> dict[str, int]:
    return {
        "guide_full": settings.quota_full_guides_per_day,
        "guide_inspiration": settings.quota_inspiration_guides_per_day,
        "plan": settings.quota_plans_per_day,
        "revise": settings.quota_revisions_per_day,
    }


def consume(user_id: str, capability: str) -> QuotaDecision:
    """为一次会产生外部成本的请求预扣额度；开发者白名单永不限额。"""
    if is_developer(user_id):
        return QuotaDecision(allowed=True, is_developer=True)
    limit = _limits().get(capability)
    if limit is None:
        raise ValueError(f"unknown quota capability: {capability}")
    if limit <= 0:
        return QuotaDecision(allowed=False, is_developer=False, remaining=0, limit=limit)

    day = datetime.now(_TIMEZONE).date().isoformat()
    path = _storage_path()
    with _LOCK:
        try:
            records = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except (OSError, json.JSONDecodeError):
            records = {}
        key = f"{day}:{user_id}:{capability}"
        used = int(records.get(key, 0))
        if used >= limit:
            return QuotaDecision(allowed=False, is_developer=False, remaining=0, limit=limit)
        records[key] = used + 1
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(records, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        temp.replace(path)
        return QuotaDecision(allowed=True, is_developer=False, remaining=limit - used - 1, limit=limit)


def exceeded_message(capability: str) -> str:
    labels = {
        "guide_full": "完整攻略",
        "guide_inspiration": "热门路线方案",
        "plan": "完整行程生成",
        "revise": "行程调整",
    }
    return f"今天的{labels.get(capability, '试用')}次数已用完，明天再来继续安排吧。"
