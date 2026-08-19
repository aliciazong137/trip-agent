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
        # 第六阶段：后台 memory 写入任务引用（防 GC，响应不等待压缩）
        self._background_tasks: set = set()

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
        # 优化：用户无任何历史记忆时跳过检索，避免无谓加载 embedding（省 457MB/9s）
        memory_context = ""
        if settings.memory_enabled and self._has_any_memory(user_id or "default_user"):
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

        # 2.1 memory 个性化：preferences 缺失时从历史偏好补默认
        # 仅对 preferences/transportation 这类稳定偏好字段放宽"memory 不补全"约束；
        # city/days/budget 等本次事实仍严格来自用户原话。
        if intent.trip_meta and not intent.trip_meta.get("preferences"):
            recalled = self._recall_preferences(user_id or "default_user")
            if recalled:
                intent.trip_meta["preferences"] = recalled
                # 移除 intent 里"未提及偏好/preferences默认空"的冗余 assumption，再加来源说明
                intent.assumptions = [a for a in intent.assumptions if "preferences" not in a.lower() and "偏好" not in a]
                intent.assumptions.append(f"未明说偏好，根据历史记忆设为 {recalled}")

        # 2.2 transportation 个性化 + 软澄清：缺失时查 memory，无历史则首次问一次
        transportation_clarify = False
        if intent.trip_meta and not intent.trip_meta.get("transportation"):
            recalled_transport = self._recall_transportation(user_id or "default_user")
            if recalled_transport:
                intent.trip_meta["transportation"] = recalled_transport
                # 移除 intent 里"未提供交通方式"的冗余 assumption，再加来源说明
                intent.assumptions = [a for a in intent.assumptions if "交通方式" not in a]
                intent.assumptions.append(f"未明说交通方式，根据历史记忆设为 {recalled_transport}")
            else:
                transportation_clarify = True  # 无历史，首次问一次

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

        # 5.1 transportation 软澄清：字段齐全但首次未提供交通方式
        elif transportation_clarify:
            if not session_id:
                session_id = new_session_id()
                await session_store.create_session(
                    session_id,
                    intent.trip_meta or {"city": "", "days": 1},
                    "",
                )
            question = "您本次出行选择什么交通方式？（如：自驾 / 公共交通 / 步行 / 高铁）"
            await save_pending_clarification(
                session_id=session_id,
                original_query=query,
                missing_fields=["transportation"],
                invalid_fields=[],
                partial_trip_meta=intent.trip_meta or {},
            )
            return NlTripPlanResponse(
                status="needs_clarification",
                session_id=session_id,
                trip_meta=None,
                clarification_question=question,
                missing_fields=["transportation"],
                invalid_fields=[],
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

        # 第零期三作：Planner 返回 poi_empty 时降级为 failed
        # 不再静默返回 ok + 空行程
        plan_status = result.get("status", "ok")
        if plan_status == "poi_empty":
            return NlTripPlanResponse(
                status="failed",
                session_id=actual_session_id,
                trip_meta=trip_meta,
                trip_plan=trip_plan,
                error_code="POI_EXTRACTION_EMPTY",
                error_message="景点提取失败，未生成行程。请补充更明确的 must_visit 或 preferences。",
                assumptions=intent.assumptions,
                warnings=result.get("warnings", []),
            )

        # 7. 规划成功后后台异步记录 Memory（第六阶段：不阻塞响应）
        # 压缩是 LLM 调用（约 10-15s），放在后台写，响应立即返回。
        # memory 是增强而非关键路径，进程崩溃丢一条记录可接受。
        if settings.memory_enabled:
            self._schedule_memory_recording(
                merged_query=merged_query,
                trip_meta=trip_meta,
                trip_plan_data=trip_plan_data,
                warnings=result.get("warnings", []),
                user_id=user_id or "default_user",
                session_id=actual_session_id,
            )

        return NlTripPlanResponse(
            status="ok",
            session_id=actual_session_id,
            trip_meta=trip_meta,
            trip_plan=trip_plan,
            assumptions=intent.assumptions,
            warnings=result.get("warnings", []),
        )

    def _has_any_memory(self, user_id: str) -> bool:
        """轻量检查用户是否有任何持久化记忆（不加载 embedding，避免无谓 457MB）"""
        try:
            stats = self.memory.stats(user_id)
            return stats.get("total_persisted", 0) > 0
        except Exception:
            return False

    def _recall_preferences(self, user_id: str) -> Optional[str]:
        """
        memory 个性化：从 semantic memory 检索历史偏好（event_type=preference）

        规划成功时 compressor 会把 preferences 存成 semantic memory（metadata.event_type=preference）。
        这里检索并去重，返回逗号拼接的偏好串；无则 None。
        无任何记忆时直接返回 None，避免触发 embedding 加载（省 457MB）。
        """
        if not self._has_any_memory(user_id):
            return None
        try:
            results = self.memory.search(
                user_id, "用户偏好", limit=5, memory_types=["semantic"]
            )
            prefs: list[str] = []
            for r in results:
                meta = r.item.metadata or {}
                if meta.get("event_type") == "preference" and meta.get("preference"):
                    prefs.append(meta["preference"])
            if not prefs:
                return None
            seen = set()
            uniq = [p for p in prefs if not (p in seen or seen.add(p))]
            return ", ".join(uniq)
        except Exception as e:
            logger.warning("recall preferences failed: %s", e)
            return None

    def _recall_transportation(self, user_id: str) -> Optional[str]:
        """memory 个性化：从 semantic memory 检索历史交通方式（event_type=transportation）"""
        if not self._has_any_memory(user_id):
            return None
        try:
            results = self.memory.search(
                user_id, "交通方式", limit=3, memory_types=["semantic"]
            )
            for r in results:
                meta = r.item.metadata or {}
                if meta.get("event_type") == "transportation" and meta.get("transportation"):
                    return meta["transportation"]
        except Exception as e:
            logger.warning("recall transportation failed: %s", e)
        return None

    def _schedule_memory_recording(self, merged_query: str, trip_meta, trip_plan_data: dict,
                                   warnings: list, user_id: str, session_id: str) -> asyncio.Task:
        """后台调度 memory 记录，返回 task（测试可 await）"""
        task = asyncio.create_task(
            self._record_memory_safe(merged_query, trip_meta, trip_plan_data, warnings, user_id, session_id)
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def _record_memory_safe(self, merged_query: str, trip_meta, trip_plan_data: dict,
                                  warnings: list, user_id: str, session_id: str) -> None:
        """后台 memory 记录，任何异常只记日志不抛出"""
        try:
            compression = await self.memory_compressor.compress_trip(
                user_query=merged_query,
                trip_meta=trip_meta.model_dump(),
                trip_plan=trip_plan_data,
                warnings=warnings,
            )
            memory_text = build_memory_text(compression)
            metadata = compression.model_dump()
            metadata.update({
                "session_id": session_id,
                "city": trip_meta.city,
                "days": trip_meta.days,
                "event_type": "trip_planned",
            })
            # working + episodic + semantic（完整复刻链路）
            self.memory.add_memory(memory_text, user_id=user_id, memory_type="working", importance=compression.importance, metadata=metadata)
            eid = self.memory.add_memory(memory_text, user_id=user_id, memory_type="episodic", importance=compression.importance, metadata=metadata)
            for pref in compression.preferences:
                self.memory.add_memory(f"用户偏好：{pref}", user_id=user_id, memory_type="semantic", importance=min(1.0, compression.importance), metadata={"event_type": "preference", "preference": pref, "source_memory_id": eid})
            # 存交通方式（如有，供下次 memory 个性化复用）
            transport = getattr(trip_meta, "transportation", None)
            if transport:
                self.memory.add_memory(f"用户偏好交通方式：{transport}", user_id=user_id, memory_type="semantic", importance=min(1.0, compression.importance), metadata={"event_type": "transportation", "transportation": transport, "source_memory_id": eid})
        except Exception as e:
            logger.warning("Memory 后台记录失败（不影响规划）: %s", e)
