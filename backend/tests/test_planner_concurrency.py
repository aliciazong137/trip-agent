"""共享 Planner 的并发隔离回归测试。"""
import asyncio

import pytest

from app.agents.trip_planner import TripPlannerAgent


@pytest.mark.asyncio
async def test_shared_planner_serializes_mutable_agent_state():
    """两个请求同时到达时，底层共享 Agent 不允许重叠执行。"""
    planner = object.__new__(TripPlannerAgent)
    active = 0
    peak = 0
    calls: list[str] = []

    async def fake_plan(trip_meta, guide_text="", user_context=None, on_stage=None):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        calls.append(trip_meta["city"])
        await asyncio.sleep(0.02)
        active -= 1
        return {"status": "ok", "session_id": f"sess_{trip_meta['city']}"}

    planner._plan_trip = fake_plan
    first, second = await asyncio.gather(
        planner.plan_trip({"city": "南京"}),
        planner.plan_trip({"city": "北京"}),
    )

    assert peak == 1
    assert calls == ["南京", "北京"]
    assert first["session_id"] == "sess_南京"
    assert second["session_id"] == "sess_北京"
