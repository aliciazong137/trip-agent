"""全局测试隔离：试用额度不能跨测试用例累计。"""
import pytest

from app.config import settings


@pytest.fixture(autouse=True)
def isolate_anonymous_quota(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "quota_storage_path", str(tmp_path / "anonymous_quotas.json"))
    monkeypatch.setattr(settings, "admin_user_ids", set())
