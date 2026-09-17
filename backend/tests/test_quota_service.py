from app.config import settings
from app.services import quota_service


def test_daily_quota_limits_normal_user_and_bypasses_developer(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "quota_storage_path", str(tmp_path / "quota.json"))
    monkeypatch.setattr(settings, "quota_full_guides_per_day", 1)
    monkeypatch.setattr(settings, "admin_user_ids", {"usr_developer"})

    first = quota_service.consume("usr_normal", "guide_full")
    second = quota_service.consume("usr_normal", "guide_full")
    developer = quota_service.consume("usr_developer", "guide_full")

    assert first.allowed and first.remaining == 0
    assert not second.allowed and second.limit == 1
    assert developer.allowed and developer.is_developer
