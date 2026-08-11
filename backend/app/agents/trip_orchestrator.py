"""
TripPlanOrchestrator - 自然语言规划协调器（第一期）

职责：
  1. 意图识别：用 TripIntentRecognizer 从 query 抽取 trip_meta
  2. 字段校验：用 validate_trip_meta 确定性规则校验
  3. 澄清闭环：必填缺失 → 生成澄清问题 + 存 pending_clarification
  4. 规划调用：字段齐全 → 调 TripPlannerAgent.plan_trip

与 RAG/Memory 的关系（review 第1点）：
  第一期不接 RAG/Memory。用户事实严格来自用户 query，不由知识库补全。
  第二期 RAG 只提供旅行知识给 Planner，不参与意图识别。
"""
import asyncio
import logging
from typing import Optional

from app.agents.intent_recognizer import TripIntentRecognizer
from app.agents.trip_planner import TripPlannerAgent, new_session_id
from app.core.field_validator import (
    validate_trip_meta,
    can_plan,
    build_clarification_question,
)
from app.models.schemas import NlTripPlanResponse, TripPlan
from app.services import session_store
from app.services.clarification_store import (
    save_pending_clarification,
    load_pending_clarification,
    clear_pending_clarification,
    merge_query_for_clarification,
)
from app.utils.ids import assert_valid_session_id
from app.config import settings
from app.memory.manager import MemoryManager
from app.memory.compressor import MemoryCompressor
from app.memory.base import build_memory_text


logger = logging.getLogger(__name__)


class TripPlanOrchestrator:
    """
    自然语言规划协调器

    用法：
        orch = TripPlanOrchestrator()
        resp = await orch.plan_from_nl("我想去南京玩2天", session_id=None)
    """

    def __init__(self):
        self.intent_recognizer = TripIntentRecognizer()
        # Planner 单例化由 routes.py 的 _get_planner 管理，这里每次 new 可能重复初始化 MCP
        # 改为延迟注入：由调用方传 planner 实例，或用类级单例
        self._planner: Optional[TripPlannerAgent] = None
        self.memory = MemoryManager()
        self.memory_compressor = MemoryCompressor()

    @property
    def planner(self) -> TripPlannerAgent:
        """延迟创建 Planner（共享 MCP 实例，避免重复初始化）"""
        if self._planner is None:
            self._planner = TripPlannerAgent()
        return self._planner

    async def plan_from_nl(
        self,
        query: str,
        session_id: Optional[str] = None,
        user_id: str = "default_user",
    ) -> NlTripPlanResponse:
        """
        从自然语言 query 生成规划

        Args:
            query: 用户自然语言 query
            session_id: 可选会话 ID（澄清续接时携带）

        Returns:
            NlTripPlanResponse
        """
        # 1. Memory 检索（第四期新增）
        # 仅作为偏好参考，不能用于补全 city/days/travelers/budget 等本次事实。
        memory_context = ""
        if settings.memory_enabled:
            try:
                memory_context = await asyncio.to_thread(
                    self.memory.get_context_for_query, user_id or "default_user", query, 3
                )
            except Exception:
                memory_context = ""

        # 2. session 续接：合并历史 query
        merged_query = query
        prev_clarification = None
        if session_id:
            try:
                assert_valid_session_id(session_id)
                prev_clarification = await load_pending_clarification(session_id)
            except Exception:
                # session_id 格式不合法或 session 不存在，按新会话处理
                session_id = None
                prev_clarification = None

            if prev_clarification:
                merged_query = merge_query_for_clarification(
                    prev_clarification.get("original_query", ""), query
                )
                logger.info(
                    "Session %s 续接澄清：原 query + 新 query 合并为 %s",
                    session_id, merged_query[:100],
                )

        # 2. 意图识别（只从 query 抽取；memory_context 只作偏好提示，不补全事实）
        intent = await self.intent_recognizer.recognize(merged_query, memory_context)

        # 3. 非旅行规划请求
        if intent.intent == "unsupported":
            return NlTripPlanResponse(
                status="failed",
                session_id=session_id,
                error_code="UNSUPPORTED_INTENT",
                error_message="这不是一个旅行规划请求，请尝试描述您的旅行需求（如「我想去北京玩2天」）。",
                assumptions=intent.assumptions,
            )

        # 4. 确定性字段校验
        trip_meta, missing, invalid = validate_trip_meta(
            intent.trip_meta if intent.trip_meta else {}
        )

        # 5. 字段缺失或非法 → 澄清
        if not can_plan(missing, invalid):
            # 创建 session（若未传）
            if not session_id:
                session_id = new_session_id()
                # 用 partial trip_meta 创建 session（含已有字段，便于后续查询）
                await session_store.create_session(
                    session_id,
                    intent.trip_meta or {"city": "", "days": 1},
                    "",
                )

            question = build_clarification_question(missing, invalid)
            await save_pending_clarification(
                session_id=session_id,
                original_query=query,   # 存原始（未合并）query，便于续接
                missing_fields=missing,
                invalid_fields=invalid,
                partial_trip_meta=intent.trip_meta or {},
            )

            return NlTripPlanResponse(
                status="needs_clarification",
                session_id=session_id,
                trip_meta=None,  # 字段不全时不返回 TripMeta，前端看 missing_fields
                clarification_question=question,
                missing_fields=missing,
                invalid_fields=invalid,
                assumptions=intent.assumptions,
            )

        # 6. 字段齐全 → 调 Planner
        # 第一期不传 guide_text / knowledge_context，第三期 RAG 注入时再加
        try:
            result = await self.planner.plan_trip(trip_meta.model_dump())
        except Exception as e:
            logger.exception("Planner 调用失败")
            return NlTripPlanResponse(
                status="failed",
                session_id=session_id,
                error_code="PLANNER_ERROR",
                error_message=f"规划引擎调用失败：{type(e).__name__}: {str(e)[:200]}",
                trip_meta=trip_meta,
                assumptions=intent.assumptions,
            )

        # 澄清已满足，清除待澄清状态
        if session_id and prev_clarification:
            await clear_pending_clarification(session_id)

        trip_plan_data = result.get("trip_plan", {})
        trip_plan = TripPlan.model_validate(trip_plan_data) if trip_plan_data else None
        actual_session_id = result.get("session_id", session_id)

        # 7. 规划成功后自动记录 Memory（不阻断主流程）
        if settings.memory_enabled:
            try:
                compression = await self.memory_compressor.compress_trip(
                    user_query=merged_query,
                    trip_meta=trip_meta.model_dump(),
                    trip_plan=trip_plan_data,
                    warnings=result.get("warnings", []),
                )
                memory_text = build_memory_text(compression)
                metadata = compression.model_dump()
                metadata.update({
                    "session_id": actual_session_id,
                    "city": trip_meta.city,
                    "days": trip_meta.days,
                    "event_type": "trip_planned",
                })
                # working + episodic + semantic（完整复刻链路）
                self.memory.add_memory(memory_text, user_id=user_id or "default_user", memory_type="working", importance=compression.importance, metadata=metadata)
                eid = self.memory.add_memory(memory_text, user_id=user_id or "default_user", memory_type="episodic", importance=compression.importance, metadata=metadata)
                for pref in compression.preferences:
                    self.memory.add_memory(f"用户偏好：{pref}", user_id=user_id or "default_user", memory_type="semantic", importance=min(1.0, compression.importance), metadata={"event_type": "preference", "preference": pref, "source_memory_id": eid})
            except Exception as e:
                logger.warning("Memory 记录失败，不阻断规划: %s", e)

        return NlTripPlanResponse(
            status="ok",
            session_id=actual_session_id,
            trip_meta=trip_meta,
            trip_plan=trip_plan,
            assumptions=intent.assumptions,
            warnings=result.get("warnings", []),
        )
