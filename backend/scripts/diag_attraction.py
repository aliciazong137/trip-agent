#!/usr/bin/env python3
"""诊断：复现 attraction agent 南京调用，抓完整回复看为什么没吐 JSON"""
import asyncio
import sys
sys.path.insert(0, ".")

from app.agents.trip_planner import TripPlannerAgent, _parse_llm_json

async def main():
    planner = TripPlannerAgent()
    city = "南京"
    days = 2
    preferences = "历史文化"
    must_visit = ["中山陵", "夫子庙"]

    min_pois = max(days * 2, 4)
    max_pois = min(days * 3, 8)
    max_tool_calls = len(must_visit) + 1 + max_pois + 2

    from app.agents.prompts import ATTRACTION_AGENT_PROMPT
    planner.attraction_agent.system_prompt = ATTRACTION_AGENT_PROMPT.format(
        days=days, min_pois=min_pois, max_pois=max_pois, max_tool_calls=max_tool_calls,
    )

    must_visit_hint = f"用户必去景点：{must_visit}" if must_visit else "用户未指定必去景点"
    attraction_query = (
        f"请搜索 {city} 的景点。{must_visit_hint}，偏好：{preferences}。"
        f"至少 {min_pois} 个、最多 {max_pois} 个 POI。\n"
        f"[TOOL_CALL:amap_maps_text_search:keywords={preferences},city={city}]"
    )

    print("=" * 60)
    print("QUERY:", attraction_query)
    print("=" * 60)

    # 统计 LLM 调用轮次（第三阶段：验证打包引导后轮次是否下降）
    llm_calls = {"n": 0}
    orig_invoke = planner.llm.invoke
    def counting_invoke(*args, **kwargs):
        llm_calls["n"] += 1
        return orig_invoke(*args, **kwargs)
    planner.llm.invoke = counting_invoke

    import time as _t
    t0 = _t.time()
    resp = planner.attraction_agent.run(attraction_query, max_tool_iterations=max_tool_calls)
    elapsed = _t.time() - t0
    print(f"LLM 调用轮次: {llm_calls['n']}  总耗时: {elapsed:.1f}s")
    print("RESPONSE LENGTH:", len(resp or ""))
    print("=" * 60)
    print("RESPONSE FULL:")
    print(resp)
    print("=" * 60)
    pois = planner._extract_pois_from_response(resp, city)
    print("EXTRACTED POIS COUNT:", len(pois))
    if pois:
        print("IDS:", [p.get("id") for p in pois])

asyncio.run(main())
