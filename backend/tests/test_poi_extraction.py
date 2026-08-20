"""
第零期三作：POI 提取稳定性与状态降级测试

覆盖：
  - _build_fallback_poi 解析高德 maps_text_search 返回值
  - _fallback_pois_from_must_visit 用 mock amap_tool 兜底生成 POI
  - Orchestrator 收到 status=poi_empty 时降级为 failed + POI_EXTRACTION_EMPTY
  - Orchestrator 收到 status=ok 时正常返回 ok
"""
import json
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

TEST_CONTEXT_DIR = Path(__file__).resolve().parent.parent / "context_test"


@pytest.fixture(autouse=True)
def override_context_root():
    """每个测试用独立的 context 目录"""
    from app.config import settings
    if TEST_CONTEXT_DIR.exists():
        shutil.rmtree(TEST_CONTEXT_DIR)
    TEST_CONTEXT_DIR.mkdir(parents=True)
    settings.set_context_root(str(TEST_CONTEXT_DIR))
    yield
    if TEST_CONTEXT_DIR.exists():
        shutil.rmtree(TEST_CONTEXT_DIR)


def _make_planner():
    """绕过 __init__（会启动 MCP server），构造最小 TripPlannerAgent 用于测 fallback 方法"""
    from app.agents.trip_planner import TripPlannerAgent
    planner = object.__new__(TripPlannerAgent)
    planner.amap_tool = MagicMock()
    return planner


# ─── _build_fallback_poi 解析逻辑 ─────────────────────────────────────────────

class TestBuildFallbackPoi:
    """_build_fallback_poi 从高德 maps_text_search 返回值构造 POI"""

    def test_parses_pois_field(self):
        """高德返回 {"pois": [...]}，取第 1 个构造 POI"""
        planner = _make_planner()
        raw = json.dumps({
            "pois": [{
                "id": "B000A8UIN8",
                "name": "故宫博物院",
                "location": "116.397,39.917",
                "address": "北京市东城区景山前街4号",
                "rating": "4.9",
                "opentime2": "08:30-17:00",
            }]
        })
        poi = planner._build_fallback_poi(raw, "北京", "故宫")
        assert poi is not None
        assert poi["id"] == "B000A8UIN8"
        assert poi["name"] == "故宫博物院"
        assert poi["location"] == {"longitude": 116.397, "latitude": 39.917}
        assert poi["priority"] == "must"
        assert poi["estimated_duration_minutes"] == 240
        assert poi["estimated_cost"] == 0
        assert "故宫" in poi["description"]

    def test_parses_results_field(self):
        """兼容 results 字段格式"""
        planner = _make_planner()
        raw = json.dumps({
            "results": [{"id": "poi_001", "name": "中山陵", "location": "118.84,32.05"}]
        })
        poi = planner._build_fallback_poi(raw, "南京", "中山陵")
        assert poi is not None
        assert poi["id"] == "poi_001"
        assert poi["name"] == "中山陵"

    def test_returns_none_on_empty_pois(self):
        """空 pois 列表 → None"""
        planner = _make_planner()
        raw = json.dumps({"pois": []})
        assert planner._build_fallback_poi(raw, "北京", "故宫") is None

    def test_returns_none_on_invalid_json(self):
        """非法 JSON → None"""
        planner = _make_planner()
        assert planner._build_fallback_poi("not json", "北京", "故宫") is None

    def test_returns_none_on_missing_id(self):
        """缺少 id 字段 → None（排程需要 id）"""
        planner = _make_planner()
        raw = json.dumps({"pois": [{"name": "故宫", "location": "116.397,39.917"}]})
        assert planner._build_fallback_poi(raw, "北京", "故宫") is None

    def test_handles_amap_prefixed_response(self):
        """高德 MCP 返回带前缀文本，解析仍能提取 JSON"""
        planner = _make_planner()
        raw = (
            "工具 'maps_text_search' 执行结果:\n"
            '{"pois": [{"id": "B001", "name": "夫子庙", "location": "118.79,32.02"}]}'
        )
        poi = planner._build_fallback_poi(raw, "南京", "夫子庙")
        assert poi is not None
        assert poi["id"] == "B001"
        assert poi["name"] == "夫子庙"


# ─── _fallback_pois_from_must_visit ────────────────────────────────────────────

class TestFallbackPoisFromMustVisit:
    """must_visit 兜底：mock amap_tool.run 生成基础 POI"""

    @pytest.mark.asyncio
    async def test_fallback_generates_pois_for_each_must_visit(self):
        """2 个 must_visit + 每次 amap 返回 1 个 POI → 生成 2 个 POI"""
        planner = _make_planner()
        planner.amap_tool.run = MagicMock(side_effect=[
            json.dumps({"pois": [{"id": "B001", "name": "中山陵", "location": "118.84,32.05"}]}),
            json.dumps({"pois": [{"id": "B002", "name": "夫子庙", "location": "118.79,32.02"}]}),
        ])
        pois = await planner._fallback_pois_from_must_visit("南京", ["中山陵", "夫子庙"])
        assert len(pois) == 2
        assert pois[0]["id"] == "B001"
        assert pois[1]["id"] == "B002"
        assert all(p["priority"] == "must" for p in pois)

    @pytest.mark.asyncio
    async def test_fallback_deduplicates_by_id(self):
        """两次搜索返回同一 id → 去重，只保留 1 个"""
        planner = _make_planner()
        planner.amap_tool.run = MagicMock(side_effect=[
            json.dumps({"pois": [{"id": "B001", "name": "中山陵", "location": "118.84,32.05"}]}),
            json.dumps({"pois": [{"id": "B001", "name": "中山陵", "location": "118.84,32.05"}]}),
        ])
        pois = await planner._fallback_pois_from_must_visit("南京", ["中山陵", "钟山"])
        assert len(pois) == 1

    @pytest.mark.asyncio
    async def test_fallback_skips_failed_searches(self):
        """某次 amap 调用抛异常 → 跳过该项，其他项仍生成"""
        planner = _make_planner()
        planner.amap_tool.run = MagicMock(side_effect=[
            RuntimeError("amap timeout"),
            json.dumps({"pois": [{"id": "B002", "name": "夫子庙", "location": "118.79,32.02"}]}),
        ])
        pois = await planner._fallback_pois_from_must_visit("南京", ["不存在的景点", "夫子庙"])
        assert len(pois) == 1
        assert pois[0]["id"] == "B002"

    @pytest.mark.asyncio
    async def test_fallback_returns_empty_when_all_fail(self):
        """所有 amap 调用失败 → 返回空列表"""
        planner = _make_planner()
        planner.amap_tool.run = MagicMock(side_effect=RuntimeError("amap down"))
        pois = await planner._fallback_pois_from_must_visit("南京", ["中山陵", "夫子庙"])
        assert pois == []

    @pytest.mark.asyncio
    async def test_fallback_returns_empty_on_no_must_visit(self):
        """must_visit 为空 → 返回空列表"""
        planner = _make_planner()
        planner.amap_tool.run = MagicMock()
        pois = await planner._fallback_pois_from_must_visit("南京", [])
        assert pois == []
        planner.amap_tool.run.assert_not_called()


# ─── Orchestrator 状态降级 ─────────────────────────────────────────────────────

class TestOrchestratorPoiEmptyDegradation:
    """Orchestrator 收到 poi_empty 时降级为 failed"""

    @pytest.mark.asyncio
    async def test_poi_empty_returns_failed_with_error_code(self):
        """Planner 返回 status=poi_empty → Orchestrator 返回 failed + POI_EXTRACTION_EMPTY"""
        from app.agents.trip_orchestrator import TripPlanOrchestrator
        from app.models.schemas import IntentResult

        orch = TripPlanOrchestrator()
        orch.intent_recognizer = MagicMock()
        orch.intent_recognizer.recognize = AsyncMock(return_value=IntentResult(
            intent="trip_planning",
            trip_meta={"city": "南京", "days": 2, "pace": "normal", "transportation": "公共交通"},
            missing_fields=[],
        ))
        orch._planner = MagicMock()
        orch._planner.plan_trip = AsyncMock(return_value={
            "session_id": "sess_poi_empty_001",
            "status": "poi_empty",
            "trip_plan": {},
            "warnings": ["未能从景点研究结果提取 POI，且无 must_visit 可兜底"],
        })

        resp = await orch.plan_from_nl("去南京玩2天", user_id="u_test")
        assert resp.status == "failed"
        assert resp.error_code == "POI_EXTRACTION_EMPTY"
        assert "景点提取失败" in (resp.error_message or "")
        assert resp.trip_meta is not None
        assert resp.trip_meta.city == "南京"

    @pytest.mark.asyncio
    async def test_ok_status_returns_ok(self):
        """Planner 返回 status=ok + 非空 days → Orchestrator 正常返回 ok"""
        from app.agents.trip_orchestrator import TripPlanOrchestrator
        from app.models.schemas import IntentResult

        orch = TripPlanOrchestrator()
        orch.intent_recognizer = MagicMock()
        orch.intent_recognizer.recognize = AsyncMock(return_value=IntentResult(
            intent="trip_planning",
            trip_meta={"city": "南京", "days": 2, "pace": "normal", "transportation": "公共交通"},
            missing_fields=[],
        ))
        orch._planner = MagicMock()
        orch._planner.plan_trip = AsyncMock(return_value={
            "session_id": "sess_ok_001",
            "status": "ok",
            "trip_plan": {"city": "南京", "start_date": "", "end_date": "", "days": []},
            "warnings": [],
        })

        resp = await orch.plan_from_nl("去南京玩2天", user_id="u_test")
        assert resp.status == "ok"
        assert resp.trip_plan is not None

    @pytest.mark.asyncio
    async def test_missing_status_defaults_to_ok(self):
        """Planner 返回无 status 字段（兼容旧调用方）→ 默认 ok"""
        from app.agents.trip_orchestrator import TripPlanOrchestrator
        from app.models.schemas import IntentResult

        orch = TripPlanOrchestrator()
        orch.intent_recognizer = MagicMock()
        orch.intent_recognizer.recognize = AsyncMock(return_value=IntentResult(
            intent="trip_planning",
            trip_meta={"city": "南京", "days": 2, "pace": "normal", "transportation": "公共交通"},
            missing_fields=[],
        ))
        orch._planner = MagicMock()
        orch._planner.plan_trip = AsyncMock(return_value={
            "session_id": "sess_no_status",
            "trip_plan": {"city": "南京", "start_date": "", "end_date": "", "days": []},
            "warnings": [],
        })

        resp = await orch.plan_from_nl("去南京玩2天", user_id="u_test")
        assert resp.status == "ok"


# ─── max_tool_iterations 传递（根因修复验证） ─────────────────────────────────

class TestAttractionMaxToolIterations:
    """plan_trip 调 attraction_agent.run 时必须传 max_tool_iterations

    根因：SimpleAgent.run 默认 max_tool_iterations=3，景点工作流需 8-10 次工具调用，
    3 轮耗尽后裸调 LLM 输出不稳定，导致 POI 间歇性提取失败。
    """

    @pytest.mark.asyncio
    async def test_plan_trip_passes_max_tool_iterations(self):
        """attraction_agent.run 收到的 kwargs 含 max_tool_iterations=max_tool_calls"""
        from app.agents.trip_planner import TripPlannerAgent

        planner = object.__new__(TripPlannerAgent)

        # mock 4 个 agent 的 run（同步 MagicMock，asyncio.to_thread 会在线程里执行）
        planner.attraction_agent = MagicMock()
        planner.attraction_agent.run = MagicMock(return_value="")  # 空回复 → 触发 fallback
        planner.weather_agent = MagicMock()
        planner.weather_agent.run = MagicMock(return_value="")
        planner.hotel_agent = MagicMock()
        planner.hotel_agent.run = MagicMock(return_value="")
        planner.planner_agent = MagicMock()
        planner.planner_agent.run = MagicMock(return_value="")

        # amap_tool：text_search 返回 POI（给 fallback 用），maps_distance 返回空
        def fake_amap_run(params):
            tool = (params or {}).get("tool_name", "")
            if tool == "maps_text_search":
                return json.dumps({"pois": [{
                    "id": "B001", "name": "中山陵景区", "location": "118.854,32.054",
                }]})
            return json.dumps({"results": []})

        planner.amap_tool = MagicMock()
        planner.amap_tool.run = MagicMock(side_effect=fake_amap_run)

        trip_meta = {"city": "南京", "days": 2, "must_visit": ["中山陵"], "pace": "normal"}
        result = await planner.plan_trip(trip_meta)

        # max_tool_calls = len(must_visit)=1 + 1 + max_pois=min(2*3,8)=6 + 2 = 10
        call = planner.attraction_agent.run.call_args
        assert call is not None, "attraction_agent.run 未被调用"
        kwargs = call.kwargs
        assert kwargs.get("max_tool_iterations") == 10, (
            f"期望 max_tool_iterations=10，实际 {kwargs.get('max_tool_iterations')}"
        )
        # fallback 兜底生成了 POI → 不应是 poi_empty
        assert result["status"] == "ok"


# ─── 第三阶段：prompt 打包调用引导 ─────────────────────────────────────────────

class TestAttractionPromptPacking:
    """prompt 必须引导 LLM 同轮打包无依赖调用（减少 LLM 轮次）"""

    def test_prompt_has_packing_rule(self):
        from app.agents.prompts import ATTRACTION_AGENT_PROMPT
        s = ATTRACTION_AGENT_PROMPT.format(days=2, min_pois=4, max_pois=6, max_tool_calls=11)
        assert "打包调用" in s
        assert "禁止一轮只发一个调用" in s

    def test_prompt_example_shows_batched_calls(self):
        """示例里同一轮连续输出多个 TOOL_CALL（无串行暗示）"""
        from app.agents.prompts import ATTRACTION_AGENT_PROMPT
        s = ATTRACTION_AGENT_PROMPT.format(days=2, min_pois=4, max_pois=6, max_tool_calls=11)
        assert "[TOOL_CALL:amap_maps_text_search:keywords=故宫,city=北京]\n[TOOL_CALL:amap_maps_text_search:keywords=八达岭长城,city=北京]" in s
        # 串行暗示文案已删
        assert "搜完故宫后继续搜八达岭" not in s

    def test_prompt_workflow_is_round_based(self):
        """工作流按轮组织，明确 4 轮结构"""
        from app.agents.prompts import ATTRACTION_AGENT_PROMPT
        s = ATTRACTION_AGENT_PROMPT.format(days=2, min_pois=4, max_pois=6, max_tool_calls=11)
        assert "第 1 轮" in s and "第 2 轮" in s and "第 3 轮" in s and "第 4 轮" in s

    def test_prompt_format_placeholders_intact(self):
        """format 注入后无残留占位符"""
        from app.agents.prompts import ATTRACTION_AGENT_PROMPT
        s = ATTRACTION_AGENT_PROMPT.format(days=3, min_pois=6, max_pois=8, max_tool_calls=13)
        for ph in ("{days}", "{min_pois}", "{max_pois}", "{max_tool_calls}"):
            assert ph not in s
