"""
第一期单元测试：意图识别链路

测试范围（不依赖真实 LLM）：
  1. field_validator：确定性字段校验
  2. clarification_store：澄清闭环存储
  3. intent_recognizer：_extract_json_from_response 提取逻辑
  4. orchestrator：用 mock 的 IntentRecognizer + mock Planner 测分流逻辑
"""
import asyncio
import json
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.field_validator import (
    validate_trip_meta,
    can_plan,
    build_clarification_question,
)
from app.services.clarification_store import (
    save_pending_clarification,
    load_pending_clarification,
    clear_pending_clarification,
    merge_query_for_clarification,
)
from app.agents.intent_recognizer import _extract_json_from_response, enforce_intent_contract
from app.models.schemas import IntentResult, TripMeta


class TestIntentContract:
    """模型开放判断后的结构化安全边界，不依赖关键词重分类。"""

    def test_current_trip_intent_requires_an_active_session(self):
        result = enforce_intent_contract(IntentResult(intent="current_trip_modify"), has_active_session=False)
        assert result.intent == "conversation"
        assert result.chat_reply

    def test_current_trip_intent_is_preserved_with_an_active_session(self):
        result = enforce_intent_contract(IntentResult(intent="current_trip_question"), has_active_session=True)
        assert result.intent == "current_trip_question"

    def test_conversation_always_has_a_displayable_reply(self):
        result = enforce_intent_contract(IntentResult(intent="conversation"), has_active_session=False)
        assert result.chat_reply

    def test_chat_recall_requires_a_conversation_summary(self):
        result = enforce_intent_contract(
            IntentResult(intent="conversation_context_question"),
            has_active_session=False,
            has_conversation_context=False,
        )
        assert result.intent == "conversation"

    def test_chat_recall_is_preserved_with_a_conversation_summary(self):
        result = enforce_intent_contract(
            IntentResult(intent="conversation_context_question"),
            has_active_session=False,
            has_conversation_context=True,
        )
        assert result.intent == "conversation_context_question"

    def test_planning_result_is_not_reclassified(self):
        result = enforce_intent_contract(
            IntentResult(intent="trip_planning", trip_meta={"city": "南京", "days": 2}),
            has_active_session=False,
        )
        assert result.intent == "trip_planning"
        assert result.trip_meta == {"city": "南京", "days": 2}


# ─── 测试夹具 ───────────────────────────────────────────────────────────────

TEST_CONTEXT_DIR = Path(__file__).resolve().parent.parent / "context_test_intent"


@pytest.fixture(autouse=True)
def override_context_root(monkeypatch):
    """每个测试用独立的 context 目录"""
    from app.config import settings
    if TEST_CONTEXT_DIR.exists():
        shutil.rmtree(TEST_CONTEXT_DIR)
    TEST_CONTEXT_DIR.mkdir(parents=True)
    settings.set_context_root(str(TEST_CONTEXT_DIR))
    yield
    if TEST_CONTEXT_DIR.exists():
        shutil.rmtree(TEST_CONTEXT_DIR)


# ─── field_validator ──────────────────────────────────────────────────────────


class TestValidateTripMeta:
    """确定性字段校验"""

    def test_valid_complete(self):
        """完整 trip_meta → 可规划"""
        raw = {"city": "北京", "days": 2, "pace": "normal", "preferences": "历史文化"}
        tm, missing, invalid = validate_trip_meta(raw)
        assert tm is not None
        assert missing == []
        assert invalid == []
        assert tm.city == "北京"
        assert tm.days == 2

    def test_missing_city(self):
        raw = {"days": 2}
        tm, missing, invalid = validate_trip_meta(raw)
        assert tm is None
        assert "city" in missing
        assert can_plan(missing, invalid) is False

    def test_missing_days(self):
        raw = {"city": "北京"}
        tm, missing, invalid = validate_trip_meta(raw)
        assert tm is None
        assert "days" in missing

    def test_missing_both(self):
        raw = {}
        tm, missing, invalid = validate_trip_meta(raw)
        assert tm is None
        assert "city" in missing
        assert "days" in missing

    def test_empty_dict(self):
        tm, missing, invalid = validate_trip_meta({})
        assert tm is None
        assert "city" in missing
        assert "days" in missing

    def test_none_input(self):
        tm, missing, invalid = validate_trip_meta(None)
        assert tm is None
        assert "city" in missing
        assert "days" in missing

    def test_days_too_large(self):
        """days=100 超出 1-30 区间"""
        raw = {"city": "北京", "days": 100}
        tm, missing, invalid = validate_trip_meta(raw)
        assert tm is None
        assert "days" in invalid

    def test_days_zero(self):
        raw = {"city": "北京", "days": 0}
        tm, missing, invalid = validate_trip_meta(raw)
        assert tm is None
        assert "days" in invalid

    def test_days_string(self):
        """字符串数字 days 应被接受（LLM 偶尔返回 '2'）"""
        raw = {"city": "北京", "days": "2"}
        tm, missing, invalid = validate_trip_meta(raw)
        assert tm is not None
        assert tm.days == 2

    def test_days_bool_intercepted(self):
        """bool 是 int 子类，True 不应被当成 days=1"""
        raw = {"city": "北京", "days": True}
        tm, missing, invalid = validate_trip_meta(raw)
        assert tm is None
        assert "days" in invalid

    def test_city_strips_whitespace(self):
        raw = {"city": "  北京  ", "days": 2}
        tm, _, _ = validate_trip_meta(raw)
        assert tm is not None
        assert tm.city == "北京"

    def test_city_empty_string(self):
        raw = {"city": "   ", "days": 2}
        tm, missing, _ = validate_trip_meta(raw)
        assert tm is None
        assert "city" in missing

    def test_travelers_valid(self):
        raw = {"city": "北京", "days": 2, "travelers": {"adults": 3, "children": 0}}
        tm, _, invalid = validate_trip_meta(raw)
        assert tm is not None
        assert invalid == []

    def test_travelers_negative(self):
        raw = {"city": "北京", "days": 2, "travelers": {"adults": -1, "children": 0}}
        tm, _, invalid = validate_trip_meta(raw)
        assert "travelers" in invalid

    def test_travelers_kids_alias(self):
        """schemas 用 kids，prompt 用 children，应同步"""
        raw = {"city": "北京", "days": 2, "travelers": {"adults": 2, "kids": 1}}
        tm, _, invalid = validate_trip_meta(raw)
        assert tm is not None
        assert tm.travelers.get("children") == 1
        assert tm.travelers.get("kids") == 1

    def test_budget_negative(self):
        raw = {"city": "北京", "days": 2, "budget": {"amount": -100}}
        tm, _, invalid = validate_trip_meta(raw)
        assert "budget" in invalid

    def test_budget_amount_null_is_dropped(self):
        """LLM 输出 {"amount": null} 占位 → 视为未提供预算，不进 invalid 也不崩排程"""
        raw = {"city": "北京", "days": 2, "budget": {"amount": None, "currency": "CNY"}}
        tm, missing, invalid = validate_trip_meta(raw)
        assert "budget" not in invalid
        assert tm is not None
        assert tm.budget is None or (tm.budget or {}).get("amount") is None
        # 排程器不再会遇到 amount=None 崩溃（scheduler.py 378 回归）
        from app.core.scheduler import build_itinerary_from_data
        import asyncio
        pois = [{"id": "p1", "name": "故宫", "area": "东城区", "priority": "nice",
                 "estimated_duration_minutes": 120, "estimated_cost": 60,
                 "location": {"longitude": 116.4, "latitude": 39.9}}]
        trip_meta_dict = {"city": "北京", "days": 1, "budget": {"amount": None}}
        result = asyncio.run(build_itinerary_from_data("sess_x", trip_meta_dict, {"city": "北京", "pois": pois}))
        assert result["itinerary"]["days"]

    def test_pace_invalid_normalizes_alias(self):
        """pace 口语别名归一化：fast → packed，不再判 invalid"""
        raw = {"city": "北京", "days": 2, "pace": "fast"}
        tm, _, invalid = validate_trip_meta(raw)
        assert tm is not None
        assert invalid == []
        assert tm.pace == "packed"

    def test_pace_alias_chinese(self):
        """中文口语别名归一化"""
        cases = {
            "特种兵": "packed", "极限": "packed", "紧凑": "packed",
            "轻松": "relaxed", "休闲": "relaxed", "度假": "relaxed",
            "一般": "normal", "常规": "normal",
        }
        for alias, expected in cases.items():
            raw = {"city": "北京", "days": 2, "pace": alias}
            tm, _, invalid = validate_trip_meta(raw)
            assert tm is not None, f"{alias} 应归一化为 {expected}"
            assert invalid == [], f"{alias} 不应判 invalid"
            assert tm.pace == expected, f"{alias} 应归一化为 {expected}，实际 {tm.pace}"

    def test_pace_unrecognizable_falls_back_normal(self):
        """无法识别的 pace 兜底为 normal，不判 invalid"""
        raw = {"city": "北京", "days": 2, "pace": "闪现"}
        tm, _, invalid = validate_trip_meta(raw)
        assert tm is not None
        assert invalid == []
        assert tm.pace == "normal"

    def test_pace_valid_enum(self):
        for pace in ["relaxed", "normal", "packed"]:
            raw = {"city": "北京", "days": 2, "pace": pace}
            tm, _, invalid = validate_trip_meta(raw)
            assert tm is not None
            assert invalid == []

    def test_must_visit_not_list(self):
        raw = {"city": "北京", "days": 2, "must_visit": "故宫"}
        tm, _, invalid = validate_trip_meta(raw)
        assert "must_visit" in invalid


class TestBuildClarificationQuestion:
    """澄清问题生成"""

    def test_missing_city_and_days(self):
        q = build_clarification_question(["city", "days"], [])
        assert "城市" in q
        assert "几天" in q
        assert "补充以下信息" in q

    def test_missing_only_days(self):
        q = build_clarification_question(["days"], [])
        assert "几天" in q
        assert "城市" not in q

    def test_invalid_days(self):
        q = build_clarification_question([], ["days"])
        assert "1-30" in q

    def test_empty_inputs(self):
        """无缺失无非法时兜底文案"""
        q = build_clarification_question([], [])
        assert "补充" in q


# ─── clarification_store ──────────────────────────────────────────────────────


class TestClarificationStore:
    """澄清闭环存储"""

    @pytest.mark.asyncio
    async def test_save_and_load(self):
        sid = "sess_a1b2c3d4e5f6"
        # 先创建 session 目录（create_session 会建）
        from app.services import session_store
        await session_store.create_session(sid, {"city": "", "days": 1}, "")

        await save_pending_clarification(
            sid, "我想去玩几天", ["city", "days"], [], {"pace": "normal"}
        )
        loaded = await load_pending_clarification(sid)
        assert loaded is not None
        assert loaded["original_query"] == "我想去玩几天"
        assert loaded["missing_fields"] == ["city", "days"]
        assert loaded["partial_trip_meta"]["pace"] == "normal"

    @pytest.mark.asyncio
    async def test_load_nonexistent(self):
        sid = "sess_b2c3d4e5f6a1"
        from app.services import session_store
        await session_store.create_session(sid, {"city": "", "days": 1}, "")
        loaded = await load_pending_clarification(sid)
        assert loaded is None

    @pytest.mark.asyncio
    async def test_clear_after_save(self):
        sid = "sess_c3d4e5f6a1b2"
        from app.services import session_store
        await session_store.create_session(sid, {"city": "", "days": 1}, "")
        await save_pending_clarification(sid, "q", ["city"], [], {})
        await clear_pending_clarification(sid)
        loaded = await load_pending_clarification(sid)
        assert loaded is None

    @pytest.mark.asyncio
    async def test_clear_nonexistent_no_error(self):
        """清除不存在的 pending 不报错"""
        sid = "sess_d4e5f6a1b2c3"
        from app.services import session_store
        await session_store.create_session(sid, {"city": "", "days": 1}, "")
        await clear_pending_clarification(sid)  # 不应抛异常


class TestMergeQuery:
    """query 合并逻辑"""

    def test_both_present(self):
        merged = merge_query_for_clarification("我想去玩几天", "去南京，2天")
        assert "我想去玩几天" in merged
        assert "去南京，2天" in merged
        assert "补充信息" in merged

    def test_empty_original(self):
        merged = merge_query_for_clarification("", "北京")
        assert merged == "北京"

    def test_empty_new(self):
        merged = merge_query_for_clarification("我想去北京", "")
        assert merged == "我想去北京"


# ─── intent_recognizer._extract_json_from_response ────────────────────────────


class TestExtractJson:
    """从 LLM 响应提取 JSON（不依赖真实 LLM）"""

    def test_plain_json(self):
        text = '{"intent": "trip_planning", "trip_meta": {"city": "北京"}}'
        data = _extract_json_from_response(text)
        assert data is not None
        assert data["intent"] == "trip_planning"
        assert data["trip_meta"]["city"] == "北京"

    def test_json_with_prefix(self):
        text = '好的，以下是解析结果：\n{"intent": "trip_planning", "city": "北京"}'
        data = _extract_json_from_response(text)
        assert data is not None
        assert data["city"] == "北京"

    def test_json_code_block(self):
        text = '```json\n{"intent": "unsupported"}\n```'
        data = _extract_json_from_response(text)
        assert data is not None
        assert data["intent"] == "unsupported"

    def test_json_code_block_no_lang(self):
        text = '```\n{"intent": "trip_planning"}\n```'
        data = _extract_json_from_response(text)
        assert data is not None

    def test_no_json(self):
        text = "我无法解析这个请求"
        assert _extract_json_from_response(text) is None

    def test_empty(self):
        assert _extract_json_from_response("") is None
        assert _extract_json_from_response(None) is None

    def test_malformed_json(self):
        text = '{"intent": "trip_planning", "city": '
        assert _extract_json_from_response(text) is None


# ─── Orchestrator（mock IntentRecognizer + Planner） ───────────────────────────


class TestOrchestratorFlow:
    """Orchestrator 分流逻辑（mock 依赖）"""

    @pytest.mark.asyncio
    async def test_needs_clarification_when_missing(self):
        """缺 city 和 days → needs_clarification"""
        from app.agents.trip_orchestrator import TripPlanOrchestrator
        from app.models.schemas import IntentResult

        orch = TripPlanOrchestrator()
        # mock IntentRecognizer 返回缺字段的 IntentResult
        orch.intent_recognizer = MagicMock()
        orch.intent_recognizer.recognize = AsyncMock(return_value=IntentResult(
            intent="trip_planning",
            trip_meta=None,
            missing_fields=["city", "days"],
            assumptions=[],
        ))

        resp = await orch.plan_from_nl("想去玩几天", session_id=None)

        assert resp.status == "needs_clarification"
        assert resp.session_id is not None
        assert "city" in resp.missing_fields
        assert "days" in resp.missing_fields
        assert resp.clarification_question is not None
        assert "城市" in resp.clarification_question
        assert "几天" in resp.clarification_question

    @pytest.mark.asyncio
    async def test_unsupported_intent(self):
        """非旅行规划请求 → 友好澄清，不再返回错误"""
        from app.agents.trip_orchestrator import TripPlanOrchestrator
        from app.models.schemas import IntentResult

        orch = TripPlanOrchestrator()
        orch.intent_recognizer = MagicMock()
        orch.intent_recognizer.recognize = AsyncMock(return_value=IntentResult(
            intent="unsupported",
            trip_meta=None,
            assumptions=[],
        ))
        orch._call_llm_for_revise = AsyncMock(
            return_value="我主要负责旅行规划，想去哪里玩呢？"
        )

        resp = await orch.plan_from_nl("帮我写邮件", session_id=None)
        assert resp.status == "needs_clarification"
        assert resp.error_code is None
        assert "旅行" in resp.clarification_question

    @pytest.mark.asyncio
    async def test_full_flow_calls_planner(self):
        """字段齐全 → 调用 Planner → 返回 ok"""
        from app.agents.trip_orchestrator import TripPlanOrchestrator
        from app.models.schemas import IntentResult, TripMeta

        orch = TripPlanOrchestrator()
        orch.intent_recognizer = MagicMock()
        orch.intent_recognizer.recognize = AsyncMock(return_value=IntentResult(
            intent="trip_planning",
            trip_meta={"city": "南京", "days": 2, "pace": "normal", "transportation": "公共交通"},
            missing_fields=[],
            assumptions=["未提供交通方式"],
        ))
        # mock Planner
        orch._planner = MagicMock()
        orch._planner.plan_trip = AsyncMock(return_value={
            "session_id": "sess_test_orch123",
            "trip_plan": {},
            "warnings": [],
        })

        resp = await orch.plan_from_nl("去南京玩2天", session_id=None)
        assert resp.status == "ok"
        assert resp.trip_meta.city == "南京"
        assert resp.trip_meta.days == 2
        assert resp.assumptions == ["未提供交通方式"]
        orch._planner.plan_trip.assert_called_once()

    @pytest.mark.asyncio
    async def test_clarification_continuation(self):
        """session 续接：第二次调用合并原 query"""
        from app.agents.trip_orchestrator import TripPlanOrchestrator
        from app.models.schemas import IntentResult, TripMeta
        from app.services import session_store
        from app.services.clarification_store import save_pending_clarification

        sid = "sess_e5f6a1b2c3d4"
        await session_store.create_session(sid, {"city": "", "days": 1}, "")
        # 先存 pending
        await save_pending_clarification(sid, "我想去玩几天", ["city", "days"], [], {})

        orch = TripPlanOrchestrator()
        orch.intent_recognizer = MagicMock()
        orch.intent_recognizer.recognize = AsyncMock(return_value=IntentResult(
            intent="trip_planning",
            trip_meta={"city": "南京", "days": 2, "pace": "normal", "transportation": "公共交通"},
            missing_fields=[],
            assumptions=[],
        ))
        orch._planner = MagicMock()
        orch._planner.plan_trip = AsyncMock(return_value={
            "session_id": sid, "trip_plan": {}, "warnings": [],
        })

        resp = await orch.plan_from_nl("去南京，2天", session_id=sid)

        # 应合并：原 query "我想去玩几天" + 补充 "去南京，2天"
        called_query = orch.intent_recognizer.recognize.call_args[0][0]
        assert "我想去玩几天" in called_query
        assert "去南京，2天" in called_query
        assert resp.status == "ok"

    @pytest.mark.asyncio
    async def test_multi_turn_clarification_accumulates_preferences(self):
        """上海 → 美食打卡 → 3天：每轮补充必须累积，偏好不能被覆盖。"""
        from app.agents.trip_orchestrator import TripPlanOrchestrator
        from app.models.schemas import IntentResult

        orch = TripPlanOrchestrator()
        orch.intent_recognizer = MagicMock()
        orch.intent_recognizer.recognize = AsyncMock(side_effect=[
            IntentResult(
                intent="trip_planning",
                trip_meta={"city": "上海", "pace": "normal"},
                missing_fields=["days"],
            ),
            IntentResult(
                intent="trip_planning",
                trip_meta={"city": "上海", "preferences": "美食打卡", "pace": "normal"},
                missing_fields=["days"],
            ),
            IntentResult(
                intent="trip_planning",
                trip_meta={
                    "city": "上海",
                    "days": 3,
                    "preferences": "美食打卡",
                    "pace": "normal",
                    "transportation": "公共交通",
                },
            ),
        ])
        orch._planner = MagicMock()
        orch._planner.plan_trip = AsyncMock(return_value={
            "session_id": "sess_f6a1b2c3d4e5",
            "trip_plan": {},
            "warnings": [],
        })

        first = await orch.plan_from_nl("我想去上海", session_id=None)
        assert first.status == "needs_clarification"
        assert first.session_id is not None

        second = await orch.plan_from_nl("我偏好美食打卡", session_id=first.session_id)
        assert second.status == "needs_clarification"
        assert "days" in second.missing_fields

        pending = await load_pending_clarification(first.session_id)
        assert pending is not None
        assert "我想去上海" in pending["original_query"]
        assert "我偏好美食打卡" in pending["original_query"]

        third = await orch.plan_from_nl("3天", session_id=first.session_id)
        assert third.status == "ok"
        final_query = orch.intent_recognizer.recognize.call_args_list[2].args[0]
        assert "我想去上海" in final_query
        assert "我偏好美食打卡" in final_query
        assert "3天" in final_query
        planned_meta = orch._planner.plan_trip.call_args.args[0]
        assert planned_meta["city"] == "上海"
        assert planned_meta["days"] == 3
        assert planned_meta["preferences"] == "美食打卡"

    @pytest.mark.asyncio
    async def test_invalid_session_id_treated_as_new(self):
        """非法 session_id → 当新会话处理"""
        from app.agents.trip_orchestrator import TripPlanOrchestrator
        from app.models.schemas import IntentResult

        orch = TripPlanOrchestrator()
        orch.intent_recognizer = MagicMock()
        orch.intent_recognizer.recognize = AsyncMock(return_value=IntentResult(
            intent="trip_planning", trip_meta=None, missing_fields=["city", "days"],
        ))

        resp = await orch.plan_from_nl("想去玩", session_id="invalid_id")
        # 不应抛异常，应正常返回 needs_clarification
        assert resp.status == "needs_clarification"
        assert resp.session_id is not None  # 生成新 session_id

    @pytest.mark.asyncio
    async def test_planner_failure_returns_failed(self):
        """Planner 抛异常 → failed + PLANNER_ERROR"""
        from app.agents.trip_orchestrator import TripPlanOrchestrator
        from app.models.schemas import IntentResult, TripMeta

        orch = TripPlanOrchestrator()
        orch.intent_recognizer = MagicMock()
        orch.intent_recognizer.recognize = AsyncMock(return_value=IntentResult(
            intent="trip_planning",
            trip_meta={"city": "北京", "days": 2, "transportation": "公共交通"},
        ))
        orch._planner = MagicMock()
        orch._planner.plan_trip = AsyncMock(side_effect=RuntimeError("MCP 连接失败"))

        resp = await orch.plan_from_nl("去北京2天", session_id=None)
        assert resp.status == "failed"
        assert resp.error_code == "PLANNER_ERROR"
        assert "MCP 连接失败" in resp.error_message


class TestFieldValidatorListToString:
    """LLM 偶尔把 str 字段返回成 list，validator 应规范化"""

    def test_preferences_empty_list_to_none(self):
        raw = {"city": "北京", "days": 2, "preferences": []}
        tm, _, invalid = validate_trip_meta(raw)
        assert tm is not None
        assert tm.preferences is None
        assert invalid == []

    def test_preferences_non_empty_list_to_string(self):
        raw = {"city": "北京", "days": 2, "preferences": ["历史文化", "博物馆"]}
        tm, _, invalid = validate_trip_meta(raw)
        assert tm is not None
        assert tm.preferences == "历史文化, 博物馆"

    def test_transportation_empty_string_to_none(self):
        raw = {"city": "北京", "days": 2, "transportation": "  "}
        tm, _, _ = validate_trip_meta(raw)
        assert tm is not None
        assert tm.transportation is None

    def test_start_date_as_number(self):
        raw = {"city": "北京", "days": 2, "start_date": 20240101}
        tm, _, _ = validate_trip_meta(raw)
        assert tm is not None
        assert tm.start_date == "20240101"


# ─── 第七阶段：transportation 抽取规则修复 ──────────────────────────────────────

class TestPromptTransportationExtraction:
    """intent prompt 必须教 LLM 把交通方式填进 trip_meta.transportation，不是 assumptions"""

    def test_prompt_has_transportation_extraction_rule(self):
        from app.agents.intent_recognizer import INTENT_RECOGNIZER_PROMPT
        assert "transportation（交通方式）" in INTENT_RECOGNIZER_PROMPT
        assert 'transportation="公共交通"' in INTENT_RECOGNIZER_PROMPT
        assert 'transportation="自驾"' in INTENT_RECOGNIZER_PROMPT

    def test_prompt_no_misleading_assumption(self):
        """示例里不应再误导 LLM 把交通方式默认放 assumptions"""
        from app.agents.intent_recognizer import INTENT_RECOGNIZER_PROMPT
        assert "未提供交通方式 transportation" not in INTENT_RECOGNIZER_PROMPT

    def test_prompt_transportation_field_note(self):
        """字段说明里明确 transportation 必须填入 trip_meta 不是 assumptions"""
        from app.agents.intent_recognizer import INTENT_RECOGNIZER_PROMPT
        assert "trip_meta.transportation（不是 assumptions）" in INTENT_RECOGNIZER_PROMPT


class TestOrchestratorTransportationClarificationClosable:
    """软澄清必须能被用户续接关闭（730eea8 引入 bug 的回归测试）"""

    @pytest.mark.asyncio
    async def test_transportation_in_query_skips_soft_clarify(self):
        """query 含 transportation → 不触发软澄清 → 进 planner"""
        from unittest.mock import AsyncMock, MagicMock
        from app.agents.trip_orchestrator import TripPlanOrchestrator
        from app.models.schemas import IntentResult

        orch = TripPlanOrchestrator()
        orch.intent_recognizer = MagicMock()
        orch.intent_recognizer.recognize = AsyncMock(return_value=IntentResult(
            intent="trip_planning",
            trip_meta={"city": "深圳", "days": 3, "pace": "packed",
                       "transportation": "公共交通"},
            missing_fields=[],
        ))
        orch._planner = MagicMock()
        orch._planner.plan_trip = AsyncMock(return_value={
            "session_id": "sess_sz", "status": "ok",
            "trip_plan": {"city": "深圳", "start_date": "", "end_date": "", "days": []},
            "warnings": [],
        })

        resp = await orch.plan_from_nl("我想去深圳玩3天 高能量 公共交通", user_id="u_sz")
        assert resp.status == "ok", f"应进 planner 不触发软澄清，实际 {resp.status}"
        orch._planner.plan_trip.assert_called_once()

    @pytest.mark.asyncio
    async def test_transportation_soft_clarify_then_closable(self):
        """首次无 transportation → 问一次；用户答后 → 进 planner（不再卡住）"""
        from unittest.mock import AsyncMock, MagicMock
        from app.agents.trip_orchestrator import TripPlanOrchestrator
        from app.models.schemas import IntentResult

        # 第一次：无 transportation，无 memory 历史 → 软澄清
        orch = TripPlanOrchestrator()
        orch.intent_recognizer = MagicMock()
        orch.intent_recognizer.recognize = AsyncMock(return_value=IntentResult(
            intent="trip_planning",
            trip_meta={"city": "深圳", "days": 3, "pace": "packed"},
            missing_fields=[],
        ))
        # 无 memory 历史（_has_any_memory 返回 False）
        orch._has_any_memory = MagicMock(return_value=False)

        resp1 = await orch.plan_from_nl("我想去深圳玩3天 高能量", user_id="u_sz2")
        assert resp1.status == "needs_clarification"
        assert "transportation" in resp1.missing_fields
        sid = resp1.session_id
        assert sid is not None

        # 第二次续接：用户答"公共交通"，intent 抽到 transportation → 进 planner
        orch2 = TripPlanOrchestrator()
        orch2.intent_recognizer = MagicMock()
        orch2.intent_recognizer.recognize = AsyncMock(return_value=IntentResult(
            intent="trip_planning",
            trip_meta={"city": "深圳", "days": 3, "pace": "packed",
                       "transportation": "公共交通"},
            missing_fields=[],
        ))
        orch2._has_any_memory = MagicMock(return_value=False)
        orch2._planner = MagicMock()
        orch2._planner.plan_trip = AsyncMock(return_value={
            "session_id": sid, "status": "ok",
            "trip_plan": {"city": "深圳", "start_date": "", "end_date": "", "days": []},
            "warnings": [],
        })

        resp2 = await orch2.plan_from_nl("公共交通", session_id=sid, user_id="u_sz2")
        assert resp2.status == "ok", f"续接答交通方式后应进 planner，实际 {resp2.status}"
        orch2._planner.plan_trip.assert_called_once()


@pytest.mark.asyncio
async def test_user_context_isolated_and_source_traceable():
    from unittest.mock import MagicMock
    from app.agents.trip_orchestrator import TripPlanOrchestrator
    from app.memory.base import MemoryItem, MemorySearchResult

    orch = TripPlanOrchestrator()
    orch.memory.search = MagicMock(return_value=[MemorySearchResult(
        item=MemoryItem(
            user_id="user_a", memory_type="semantic", content="用户画像：home_city=北京",
            metadata={"event_type": "profile", "profile_key": "home_city", "profile_value": "北京"},
        ),
        score=0.9,
    )])
    ctx = orch._build_user_context("user_a", "大阪旅行")
    assert ctx.user_id == "user_a"
    assert ctx.profile["home_city"] == "北京"
    assert len(ctx.sources) == 1
    assert ctx.sources[0].type == "user_memory"
    assert ctx.sources[0].id == ctx.memories[0]["id"]
    orch.memory.search.assert_called_once_with("user_a", "大阪旅行", limit=8)


def test_clarification_with_known_city_has_warm_opening():
    question = build_clarification_question(["days"], [], {"city": "北京"})
    assert question.startswith("北京很值得慢慢逛")
    assert "几天" in question
    assert "为了帮您生成准确" not in question
