import shutil
import tempfile
import asyncio
from pathlib import Path

import pytest

from app.config import settings
from app.memory import store
from app.memory.base import MemoryItem, MemoryCompressionResult, build_memory_text
from app.memory.working import WorkingMemory
from app.memory.manager import MemoryManager
from app.memory.semantic import extract_preferences_from_text


@pytest.fixture(autouse=True)
def temp_memory(monkeypatch):
    tmp = Path(tempfile.mkdtemp(prefix="memory_test_"))
    monkeypatch.setattr(settings, "memory_storage_path", str(tmp), raising=False)
    store._client = None
    store.reset_memory_store()
    yield tmp
    store._client = None
    shutil.rmtree(tmp, ignore_errors=True)


def test_sqlite_save_load():
    item = MemoryItem(user_id="u1", memory_type="episodic", content="用户南京2天亲子游", importance=0.8)
    store.save_memory(item)
    loaded = store.get_memory(item.id)
    assert loaded is not None
    assert loaded.content == item.content
    assert loaded.user_id == "u1"


def test_working_memory_ttl_and_capacity():
    wm = WorkingMemory(capacity=2, ttl_minutes=120)
    wm.add(MemoryItem(user_id="u", memory_type="working", content="一", importance=0.1))
    wm.add(MemoryItem(user_id="u", memory_type="working", content="二", importance=0.9))
    wm.add(MemoryItem(user_id="u", memory_type="working", content="三", importance=0.8))
    assert len(wm.memories) == 2
    assert all(m.content != "一" for m in wm.memories)


def test_working_memory_search_user_isolation():
    wm = WorkingMemory()
    wm.add(MemoryItem(user_id="a", memory_type="working", content="用户喜欢南京亲子游", importance=0.8))
    wm.add(MemoryItem(user_id="b", memory_type="working", content="用户喜欢北京博物馆", importance=0.8))
    res = wm.search("a", "南京", limit=5)
    assert len(res) >= 1
    assert all(r.item.user_id == "a" for r in res)


def test_manager_episodic_search_user_isolation():
    mm = MemoryManager(enable_working=False, enable_episodic=True, enable_semantic=False, enable_perceptual=False)
    mm.add_memory("用户A规划南京2天亲子游，偏好历史文化", user_id="a", memory_type="episodic", importance=0.9)
    mm.add_memory("用户B规划北京3天博物馆游", user_id="b", memory_type="episodic", importance=0.9)
    res_a = mm.search("a", "南京亲子", limit=3)
    assert any("南京" in r.item.content for r in res_a)
    assert all(r.item.user_id == "a" for r in res_a)
    res_b = mm.search("b", "南京亲子", limit=3)
    assert all(r.item.user_id == "b" for r in res_b)


def test_context_has_guardrail():
    mm = MemoryManager(enable_working=True, enable_episodic=False, enable_semantic=False, enable_perceptual=False)
    mm.add_memory("用户上次南京2天", user_id="u", memory_type="working", importance=0.8)
    ctx = mm.get_context_for_query("u", "南京", limit=3)
    assert "不得用于补全 city/days" in ctx


def test_semantic_extract_preferences():
    prefs = extract_preferences_from_text("用户规划南京亲子游，偏好历史文化，不喜欢赶路，喜欢美食。")
    assert any("亲子" in p for p in prefs)
    assert any("历史文化" in p for p in prefs)


def test_compression_text_builder():
    c = MemoryCompressionResult(
        summary="用户规划南京2天亲子游。",
        preferences=["亲子友好", "历史文化"],
        decisions=["选择中山陵"],
        unknowns=["未提供预算"],
        importance=0.8,
    )
    text = build_memory_text(c)
    assert "亲子友好" in text
    assert "选择中山陵" in text


def test_forget_importance_based():
    mm = MemoryManager(enable_working=False, enable_episodic=True, enable_semantic=False, enable_perceptual=False)
    low = mm.add_memory("低重要性", user_id="u", memory_type="episodic", importance=0.05)
    high = mm.add_memory("高重要性", user_id="u", memory_type="episodic", importance=0.9)
    n = mm.forget("u", strategy="importance_based", threshold=0.1)
    assert n == 1
    assert store.get_memory(low).metadata.get("forgotten") is True
    assert not store.get_memory(high).metadata.get("forgotten")

@pytest.mark.asyncio
async def test_orchestrator_records_memory_after_success(monkeypatch):
    """规划成功后自动写 working/episodic/semantic memory（mock Planner，快速 E2E）"""
    from unittest.mock import AsyncMock, MagicMock
    from app.agents.trip_orchestrator import TripPlanOrchestrator
    from app.models.schemas import IntentResult

    orch = TripPlanOrchestrator()
    orch.intent_recognizer = MagicMock()
    orch.intent_recognizer.recognize = AsyncMock(return_value=IntentResult(
        intent="trip_planning",
        trip_meta={"city": "南京", "days": 2, "pace": "normal", "preferences": "历史文化", "transportation": "公共交通"},
        missing_fields=[],
    ))
    orch._planner = MagicMock()
    orch._planner.plan_trip = AsyncMock(return_value={"session_id": "sess_memtest001", "trip_plan": {}, "warnings": []})
    # 避免真实 LLM 压缩，改用模板压缩
    monkeypatch.setattr(settings, "memory_compress_mode", "template", raising=False)

    resp = await orch.plan_from_nl("我想去南京玩2天，偏好历史文化", user_id="user_a")
    assert resp.status == "ok"
    # 第六阶段：memory 写入改为后台 task，测试需等待完成
    if orch._background_tasks:
        await asyncio.gather(*orch._background_tasks, return_exceptions=True)
    memories = store.list_memories("user_a", limit=100)
    types = {m.memory_type for m in memories}
    assert "episodic" in types
    assert any("南京" in m.content for m in memories)


@pytest.mark.asyncio
async def test_memory_does_not_block_planning(monkeypatch):
    """Memory 失败不阻断规划"""
    from unittest.mock import AsyncMock, MagicMock
    from app.agents.trip_orchestrator import TripPlanOrchestrator
    from app.models.schemas import IntentResult

    orch = TripPlanOrchestrator()
    orch.intent_recognizer = MagicMock()
    orch.intent_recognizer.recognize = AsyncMock(return_value=IntentResult(
        intent="trip_planning",
        trip_meta={"city": "南京", "days": 2, "pace": "normal", "transportation": "公共交通"},
    ))
    orch._planner = MagicMock()
    orch._planner.plan_trip = AsyncMock(return_value={"session_id": "sess_memtest002", "trip_plan": {}, "warnings": []})
    orch.memory.add_memory = MagicMock(side_effect=RuntimeError("memory down"))
    resp = await orch.plan_from_nl("去南京2天", user_id="user_a")
    assert resp.status == "ok"
    # 后台任务里的异常被吞掉，不抛出
    if orch._background_tasks:
        await asyncio.gather(*orch._background_tasks, return_exceptions=True)


def test_template_compression_builds_user_profile():
    from app.memory.compressor import MemoryCompressor
    c = MemoryCompressor().compress_template(
        "我是一个j人，想从北京出发去首尔玩3天",
        {"city": "首尔", "days": 3, "pace": "packed", "transportation": "公共交通"},
        None,
    )
    assert c.profile["planning_style"] == "J"
    assert c.profile["home_city"] == "北京"
    assert c.profile["transportation"] == "公共交通"
    assert "偏好计划性（自称J人）" in c.preferences
    assert "用户画像" in build_memory_text(c)
