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
import re
from typing import Optional

from app.agents.intent_recognizer import TripIntentRecognizer
from app.agents.trip_planner import TripPlannerAgent, new_session_id
from app.core.field_validator import (
    validate_trip_meta,
    can_plan,
    build_clarification_question,
)
from app.models.schemas import NlTripPlanResponse, ReviseNlResponse, TripPlan, UserContext, RetrievedSource
from app.services import session_store, amap_service
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
        on_stage=None,
    ) -> NlTripPlanResponse:
        """
        从自然语言 query 生成规划

        Args:
            query: 用户自然语言 query
            session_id: 可选会话 ID（澄清续接时携带）
            on_stage: 可选进度回调（透传给 planner，用于 SSE 流式推送）

        Returns:
            NlTripPlanResponse
        """
        def _emit(agent: str, label: str, status: str, **data):
            if on_stage:
                try:
                    on_stage({"agent": agent, "label": label, "status": status, **data})
                except Exception:
                    pass

        # 1. Memory 检索与意图识别并行：
        #    memory_context 仅作偏好提示（不补全事实），个性化由 2.1 的 post-hoc fill 兜底，
        #    因此无需串行等待，首字延迟直降 memory 检索耗时（首次含 embedding 模型加载 5-7s）。
        _emit("intent", "小渡正在理解需求", "start")

        async def _memory_task() -> UserContext:
            base = UserContext(user_id=user_id or "default_user")
            if not settings.memory_enabled:
                return base
            try:
                if not self._has_any_memory(base.user_id):
                    return base
                return await asyncio.to_thread(self._build_user_context, base.user_id, query)
            except Exception:
                return base

        memory_task = asyncio.create_task(_memory_task())

        # 2. session 续接：合并历史 query（快速，不阻塞意图识别）
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

        # 2. 意图识别（只从 query 抽取；不等 memory）
        intent = await self.intent_recognizer.recognize(merged_query, "")
        _emit("intent", "需求理解", "done")

        # 等 memory 结果（通常意图识别 2-3s 期间已完成）
        user_context = await memory_task
        recalled_profile = user_context.profile

        # 2.1 memory 个性化：preferences 缺失时从历史偏好补默认
        # 仅对 preferences/transportation 这类稳定偏好字段放宽"memory 不补全"约束；
        # city/days/budget 等本次事实仍严格来自用户原话。
        if intent.trip_meta and not intent.trip_meta.get("preferences"):
            recalled = self._profile_preferences(recalled_profile) or self._recall_preferences(user_context.user_id)
            if recalled:
                intent.trip_meta["preferences"] = recalled
                intent.assumptions = [a for a in intent.assumptions if "preferences" not in a.lower() and "偏好" not in a]
                intent.assumptions.append(f"未明说偏好，根据历史记忆设为 {recalled}")

        # 当前 query 没明确交通时才使用用户历史默认交通。
        transportation_clarify = False
        if intent.trip_meta and not intent.trip_meta.get("transportation"):
            recalled_transport = recalled_profile.get("transportation") or self._recall_transportation(user_context.user_id)
            if recalled_transport:
                intent.trip_meta["transportation"] = recalled_transport
                # 移除 intent 里"未提供交通方式"的冗余 assumption，再加来源说明
                intent.assumptions = [a for a in intent.assumptions if "交通方式" not in a]
                intent.assumptions.append(f"未明说交通方式，根据历史记忆设为 {recalled_transport}")
            else:
                transportation_clarify = True  # 无历史，首次问一次

        # 3. 非旅行规划请求 → 友好分类处理，不直接报错
        if intent.intent in {"conversation", "unsupported"}:
            return await self._handle_unsupported_intent(merged_query, session_id, intent.assumptions)

        # 3b. 天气查询 → 直接调高德查天气
        if intent.intent == "weather_query":
            return await self._handle_weather_query(merged_query, intent.trip_meta, session_id)

        # LLM 偶尔会为未提及的可选 travelers 生成非法占位值。
        # 只有用户明确说了人数，才把该字段交给确定性校验；否则不让可选噪声阻断规划。
        if intent.trip_meta and "travelers" in intent.trip_meta and not self._query_mentions_travelers(merged_query):
            intent.trip_meta.pop("travelers", None)

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
                    user_id=user_id or "default_user",
                )

            # 性能优化：意图识别已在同一次调用生成小渡语气追问，
            # 缺失字段与 LLM 自报一致且无非法值时直接复用，省一次 LLM 往返
            question = None
            llm_missing = set(intent.missing_fields or [])
            if (
                intent.clarification_question
                and not invalid
                and llm_missing
                and set(missing) == llm_missing
            ):
                question = intent.clarification_question.strip()
            if not question:
                question = await self._build_clarification_question_llm(
                    query=merged_query,
                    missing=missing,
                    invalid=invalid,
                    partial_trip_meta=intent.trip_meta or {},
                )
            await save_pending_clarification(
                session_id=session_id,
                original_query=merged_query,  # 累积完整上下文，避免多轮补充时丢失偏好/城市/天数
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
                sources=user_context.sources,
                user_context=user_context,
            )

        # 5.1 transportation 软澄清：字段齐全但首次未提供交通方式
        elif transportation_clarify:
            if not session_id:
                session_id = new_session_id()
                await session_store.create_session(
                    session_id,
                    intent.trip_meta or {"city": "", "days": 1},
                    "",
                    user_id=user_id or "default_user",
                )
            question = "您本次出行选择什么交通方式？（如：自驾 / 公共交通 / 步行 / 高铁）"
            await save_pending_clarification(
                session_id=session_id,
                original_query=merged_query,
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
                sources=user_context.sources,
                user_context=user_context,
            )

        # 6. 字段齐全 → 调 Planner
        # 第一期不传 guide_text / knowledge_context，第三期 RAG 注入时再加
        try:
            result = await self.planner.plan_trip(trip_meta.model_dump(), user_context=user_context.model_dump(), on_stage=on_stage)
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
        actual_session_id = result.get("session_id", session_id)
        trip_plan = None
        if trip_plan_data:
            try:
                trip_plan = TripPlan.model_validate(trip_plan_data)
            except Exception as e:
                # 最后防线：LLM 输出再出新的非法字段时，降级为无 trip_plan
                # （地图/行程数据走 /session/{id}/map 独立接口，不受影响），不再让整个请求失败
                logger.warning(
                    "TripPlan 校验失败，降级为无行程概览（地图数据不受影响）: %s", str(e)[:200]
                )

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
                sources=user_context.sources,
                user_context=user_context,
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

        plan_sources = [RetrievedSource.model_validate(s) for s in result.get("sources", [])]
        all_sources = user_context.sources + plan_sources

        return NlTripPlanResponse(
            status="ok",
            session_id=actual_session_id,
            trip_meta=trip_meta,
            trip_plan=trip_plan,
            assumptions=intent.assumptions,
            sources=all_sources,
            user_context=user_context,
            warnings=result.get("warnings", []),
        )

    @staticmethod
    def _query_mentions_travelers(query: str) -> bool:
        markers = ("几人", "成人", "儿童", "小孩", "孩子", "同行", "一家", "夫妻", "情侣", "带娃", "带孩子")
        return any(marker in (query or "") for marker in markers)

    def _build_user_context(self, user_id: str, query: str) -> UserContext:
        """按 user_id 检索 Memory，构建有限且可解释的用户上下文。"""
        try:
            results = self.memory.search(user_id, query or "用户偏好旅行历史", limit=8)
        except Exception as exc:
            logger.warning("build user context failed: %s", exc)
            return UserContext(user_id=user_id)
        profile = {}
        memories = []
        sources = []
        for result in results:
            item = result.item
            meta = item.metadata or {}
            content = item.content[:600]
            memories.append({"id": item.id, "type": item.memory_type, "content": content, "metadata": meta})
            profile.update(meta.get("profile") or {})
            # 兼容第九阶段之前已写入的自然语言 episodic/semantic Memory。
            if "北京出发" in content or "从北京" in content:
                profile.setdefault("home_city", "北京")
            if "J人" in content or "计划性" in content:
                profile.setdefault("planning_style", "J")
            if "公共交通" in content:
                profile.setdefault("transportation", "公共交通")
            if meta.get("event_type") == "profile" and meta.get("profile_key"):
                profile.setdefault(meta["profile_key"], meta.get("profile_value"))
            if meta.get("event_type") == "transportation" and meta.get("transportation"):
                profile.setdefault("transportation", meta["transportation"])
            if meta.get("event_type") == "preference" and meta.get("preference"):
                profile.setdefault("preferences", []).append(meta["preference"])
            sources.append(RetrievedSource(
                type="user_memory", id=item.id, title=meta.get("event_type", item.memory_type),
                content=content, score=round(result.score, 3),
                reason="用于本次规划的历史偏好或用户事实",
            ))
        if isinstance(profile.get("preferences"), list):
            profile["preferences"] = list(dict.fromkeys(profile["preferences"]))[:8]
        return UserContext(user_id=user_id, profile=profile, memories=memories[:8], sources=sources[:8])

    @staticmethod
    def _profile_preferences(profile: dict) -> Optional[str]:
        prefs = profile.get("preferences") if isinstance(profile, dict) else None
        if isinstance(prefs, list) and prefs:
            return ", ".join(str(p) for p in prefs[:5])
        return None

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
                "profile": compression.profile,
            })
            # working + episodic + semantic（完整复刻链路）
            self.memory.add_memory(memory_text, user_id=user_id, memory_type="working", importance=compression.importance, metadata=metadata)
            eid = self.memory.add_memory(memory_text, user_id=user_id, memory_type="episodic", importance=compression.importance, metadata=metadata)
            for pref in compression.preferences:
                self.memory.add_memory(f"用户偏好：{pref}", user_id=user_id, memory_type="semantic", importance=min(1.0, compression.importance), metadata={"event_type": "preference", "preference": pref, "source_memory_id": eid})
            for key, value in compression.profile.items():
                if value:
                    self.memory.add_memory(f"用户画像：{key}={value}", user_id=user_id, memory_type="semantic", importance=min(1.0, compression.importance), metadata={"event_type": "profile", "profile_key": key, "profile_value": value, "source_memory_id": eid})
            # 存交通方式（如有，供下次 memory 个性化复用）
            transport = getattr(trip_meta, "transportation", None)
            if transport:
                self.memory.add_memory(f"用户偏好交通方式：{transport}", user_id=user_id, memory_type="semantic", importance=min(1.0, compression.importance), metadata={"event_type": "transportation", "transportation": transport, "source_memory_id": eid})
        except Exception as e:
            logger.warning("Memory 后台记录失败（不影响规划）: %s", e)

    # ─── 行程自然语言修订 ────────────────────────────────────────────────────────

    async def revise_from_nl(
        self,
        session_id: str,
        query: str,
        day: Optional[int] = None,
        user_id: str = "default_user",
    ) -> ReviseNlResponse:
        """
        用自然语言修订已有行程。

        流程：
          1. 加载现有 Session（trip_meta + poi_list + itinerary）
          2. 用 LLM 理解修订意图，生成可操作的 itinerary 变更
          3. 应用变更，持久化更新后的 itinerary
          4. 返回 AI 消息 + 新版本号

        Args:
            session_id: 已规划的会话 ID
            query:      用户自然语言修订 query
            day:        目标天（None = 全程修订）
            user_id:    用于 Memory

        Returns:
            ReviseNlResponse
        """
        try:
            assert_valid_session_id(session_id)
        except Exception:
            return ReviseNlResponse(
                status="failed",
                session_id=session_id,
                message="会话 ID 无效，请先重新生成行程。",
                error_code="INVALID_SESSION_ID",
            )

        loaded = await session_store.load_session(session_id)
        if not loaded:
            return ReviseNlResponse(
                status="failed",
                session_id=session_id,
                message="找不到对应行程，请先生成一个行程再调整。",
                error_code="SESSION_NOT_FOUND",
            )

        trip_meta_raw = loaded.get("trip_meta") or {}
        itinerary_raw = loaded.get("itinerary") or {}
        poi_list_raw = loaded.get("poi_list") or {}

        city = trip_meta_raw.get("city", "") if isinstance(trip_meta_raw, dict) else getattr(trip_meta_raw, "city", "")
        current_version = itinerary_raw.get("version", 1) if isinstance(itinerary_raw, dict) else getattr(itinerary_raw, "version", 1)
        day_label = f"第 {day} 天" if day else "全程"
        if day is not None and not any(d.get("day") == day for d in itinerary_raw.get("days", [])):
            return ReviseNlResponse(status="failed", session_id=session_id,
                                    message="找不到要调整的那一天。", error_code="INVALID_DAY")

        # 构建给 LLM 的行程摘要 prompt
        itinerary_summary = self._build_itinerary_summary(itinerary_raw, poi_list_raw, day)

        revise_prompt = (
            f"你是旅行助手「小渡」，正在帮用户调整{city}的{day_label}行程。\n\n"
            f"当前行程摘要：\n{itinerary_summary}\n\n"
            f"用户修订要求：{query}\n\n"
            f"请完成以下工作：\n"
            f"1. 简要描述你做了哪些调整（1-2 句话，中文，亲切口吻）\n"
            f"2. 输出调整后的「地点列表」，格式为 JSON 数组，每项包含 name(景点名) 和 reason(调整原因)：\n"
            f"```json\n[{{\"name\": \"景点名\", \"reason\": \"原因\"}}]\n```\n"
            "地点列表必须是调整范围内的完整列表，保留用户未要求改变的地点。\n"
            "用户明确说想去、改成、换成某地点时，必须落实到地点列表，使用用户指定的名称。\n"
            "不得以附近、同一园区、包含在某博物馆内为由拒绝调整或保留原来的笼统名称。\n"
            "例如原地点是孔庙和国子监博物馆，用户说想去国子监，应将该站改为国子监，保留其他站点，不重复添加同一站。\n"
            f"若用户要求不明确，仅输出自然语言回复并不输出 JSON。\n"
            f"若需确认高影响操作（如删除必去景点），说明原因并要求用户确认，不要直接修改。"
        )

        try:
            llm_result = await self._call_llm_for_revise(revise_prompt)
        except Exception as e:
            logger.exception("LLM 调用修订 prompt 失败")
            return ReviseNlResponse(
                status="failed",
                session_id=session_id,
                message=f"调整失败，AI 暂时无法响应：{str(e)[:100]}",
                error_code="LLM_ERROR",
            )

        # 解析 LLM 输出
        ai_message, new_poi_names = self._parse_revise_output(llm_result)
        if not new_poi_names and re.search(r"想去|要去|改成|换成|替换|加上|加入|添加", query):
            # 明确修改请求不能因模型只给解释而伪装成功；给模型一次结构化纠正机会。
            try:
                llm_result = await self._call_llm_for_revise(
                    revise_prompt + "\n上次未返回地点列表。请落实用户明确要求并返回完整 JSON 地点列表；不能用已包含替代修改。"
                )
                ai_message, new_poi_names = self._parse_revise_output(llm_result)
            except Exception:
                logger.exception("行程修订结构化重试失败")
            if not new_poi_names:
                return ReviseNlResponse(status="failed", session_id=session_id,
                                        message="这次未能生成可执行的行程调整，原行程尚未修改，请重试。",
                                        error_code="REVISE_NO_CHANGES", itinerary_version=current_version)

        # 若 LLM 识别出了调整意图，更新 itinerary；否则只返回 AI 消息
        if new_poi_names:
            try:
                import copy
                import uuid
                updated_pois = copy.deepcopy(poi_list_raw)
                pois = updated_pois.setdefault("pois", [])
                for item in new_poi_names:
                    name = item["name"].strip()
                    item["name"] = name
                    if any(p.get("name") == name for p in pois):
                        continue
                    location = await amap_service.geocode(name, city)
                    if not location:
                        return ReviseNlResponse(status="failed", session_id=session_id,
                                                message=f"暂时无法定位{name}，原行程尚未修改，请补充具体地点或稍后重试。",
                                                error_code="POI_LOCATION_UNAVAILABLE", itinerary_version=current_version)
                    pois.append({"id": f"poi_{uuid.uuid4().hex[:8]}", "name": name,
                                 "location": location, "category": "attraction", "priority": "must"})
                new_version = current_version + 1
                updated_itinerary = self._apply_revise(
                    itinerary_raw=itinerary_raw,
                    poi_list_raw=updated_pois,
                    new_poi_names=new_poi_names,
                    day=day,
                    session_id=session_id,
                    new_version=new_version,
                )
                # 先保存地点再保存引用它们的行程，地图读取不会出现悬空 POI。
                await session_store.save_poi_list(session_id, updated_pois)
                await session_store.save_itinerary(session_id, updated_itinerary)
                logger.info("行程修订完成: session=%s day=%s version=%s names=%s", session_id, day, new_version, [p["name"] for p in new_poi_names])
                return ReviseNlResponse(
                    status="ok",
                    session_id=session_id,
                    message=f"已更新{day_label}行程：" + " → ".join(p["name"] for p in new_poi_names) + "。",
                    itinerary_version=new_version,
                )
            except Exception as e:
                logger.exception("行程应用修订失败")
                return ReviseNlResponse(
                    status="failed",
                    session_id=session_id,
                    message="行程数据更新失败，请稍后再试。",
                    error_code="REVISE_APPLY_ERROR",
                    error_message=str(e)[:200],
                )
        else:
            # LLM 仅回复自然语言，无结构化调整（可能是需要确认或需澄清）
            return ReviseNlResponse(
                status="ok",
                session_id=session_id,
                message=ai_message,
                itinerary_version=current_version,
            )

    def _build_itinerary_summary(self, itinerary_raw: dict, poi_list_raw: dict, day: Optional[int]) -> str:
        """构建简洁的行程摘要供 LLM 修订参考"""
        try:
            days_data = itinerary_raw.get("days", []) if isinstance(itinerary_raw, dict) else []
            if day:
                days_data = [d for d in days_data if d.get("day") == day]

            poi_map: dict = {}
            if isinstance(poi_list_raw, dict):
                for p in poi_list_raw.get("pois", []):
                    if isinstance(p, dict):
                        poi_map[p.get("id", "")] = p.get("name", p.get("id", ""))

            lines = []
            for d in days_data[:7]:  # 最多 7 天
                day_num = d.get("day", "?")
                blocks = d.get("time_blocks", [])
                poi_names = []
                for b in blocks:
                    pid = b.get("poi_id", "") if isinstance(b, dict) else ""
                    poi_names.append(poi_map.get(pid, pid))
                lines.append(f"第 {day_num} 天：{'→'.join(poi_names) or '（无安排）'}")

            return "\n".join(lines) or "（行程数据为空）"
        except Exception:
            return "（无法解析当前行程）"

    async def _call_llm_for_revise(self, prompt: str) -> str:
        """调用 LLM 执行修订/问候 prompt，返回原始文本（复用 hello_agents 框架）"""
        from hello_agents import HelloAgentsLLM, SimpleAgent
        llm = HelloAgentsLLM()
        agent = SimpleAgent(
            name="ReviseAgent",
            llm=llm,
            system_prompt="你是旅行助手「小渡」，回答简洁自然。",
            enable_tool_calling=False,
        )
        response = await asyncio.to_thread(agent.run, prompt)
        return response or ""

    @staticmethod
    def _parse_revise_output(llm_text: str) -> tuple[str, list[dict]]:
        """
        解析 LLM 修订输出。
        返回 (ai_message, new_poi_list)
        new_poi_list = [{"name": "...", "reason": "..."}]（未找到 JSON 则为空列表）
        """
        import json as _json
        # 提取 JSON 代码块
        json_blocks = re.findall(r"```(?:json)?\s*(\[.*?\])\s*```", llm_text, re.DOTALL)
        poi_names: list[dict] = []
        ai_text = llm_text
        if json_blocks:
            try:
                poi_names = _json.loads(json_blocks[0])
                if not isinstance(poi_names, list) or not all(
                    isinstance(p, dict) and isinstance(p.get("name"), str) and p["name"].strip()
                    for p in poi_names
                ):
                    poi_names = []
                # 去掉 JSON 部分，只保留自然语言
                ai_text = re.sub(r"```(?:json)?\s*\[.*?\]\s*```", "", llm_text, flags=re.DOTALL).strip()
            except Exception:
                poi_names = []
        return ai_text.strip() or "好的，我已按你的要求调整了行程。", poi_names

    @staticmethod
    def _apply_revise(
        itinerary_raw: dict,
        poi_list_raw: dict,
        new_poi_names: list[dict],
        day: Optional[int],
        session_id: str,
        new_version: int,
    ) -> dict:
        """
        将 LLM 给出的新地点列表应用到 itinerary。

        策略：
        - 若指定了 day，只替换该天的 time_blocks。
        - 地点必须已写入 poi_list，禁止生成没有名称和坐标的悬空 ID。
        - 保留现有 itinerary 的其他字段（session_id, city, version 等）。
        """
        import copy

        # 构建 name → poi_id 映射
        name_to_id: dict = {}
        if isinstance(poi_list_raw, dict):
            for p in poi_list_raw.get("pois", []):
                if isinstance(p, dict):
                    name_to_id[p.get("name", "")] = p.get("id", p.get("name", ""))

        updated = copy.deepcopy(itinerary_raw) if isinstance(itinerary_raw, dict) else {}
        updated["version"] = new_version
        updated["session_id"] = session_id

        days_data = updated.get("days", [])

        # 构建新 time_blocks
        start_hours = [9, 10, 11, 13, 14, 15, 16]
        end_hours = [10, 11, 12, 14, 15, 16, 17]

        def build_blocks(poi_list: list[dict]) -> list[dict]:
            blocks = []
            for i, p in enumerate(poi_list):
                name = p.get("name", f"地点{i+1}")
                pid = name_to_id.get(name)
                if not pid:
                    raise ValueError(f"地点尚未解析：{name}")
                sh = start_hours[i % len(start_hours)]
                eh = end_hours[i % len(end_hours)]
                blocks.append({
                    "poi_id": pid,
                    "start_time": f"{sh:02d}:00",
                    "end_time": f"{eh:02d}:00",
                    "reason": p.get("reason", "用户调整"),
                    "locked": False,
                })
            return blocks

        if day:
            for d in days_data:
                if isinstance(d, dict) and d.get("day") == day:
                    d["time_blocks"] = build_blocks(new_poi_names)
                    break
        else:
            # 全程修订：按天均分地点
            total_days = len(days_data) or 1
            per_day, remainder = divmod(len(new_poi_names), total_days)
            offset = 0
            for idx, d in enumerate(days_data):
                if isinstance(d, dict):
                    count = per_day + (idx < remainder)
                    chunk = new_poi_names[offset:offset + count]
                    offset += count
                    d["time_blocks"] = build_blocks(chunk)

        updated["days"] = days_data
        return updated

    # ─── 非旅行意图友好处理 ──────────────────────────────────────────────────────

    # 常见问候/闲聊词（正则快速判断，无需 LLM，减少延迟）
    _GREETING_RE = re.compile(
        r"^[\s，。！？!?]*"
        r"(你好|您好|hi|hello|嗨|哈喽|hey|在吗|在不在|在不|你是谁|你叫什么|介绍一下自己|"
        r"谢谢|感谢|谢了|好的谢谢|帮帮我|能帮我吗|怎么用|怎么使用|有什么功能|你能做什么|"
        r"早上好|下午好|晚上好|早安|晚安|么么哒|哈哈|嗯嗯|ok|好的|知道了|明白了|收到)"
        r"[\s，。！？!?]*$",
        re.IGNORECASE,
    )

    async def _build_clarification_question_llm(
        self,
        query: str,
        missing: list,
        invalid: list,
        partial_trip_meta: dict,
    ) -> str:
        """
        用 LLM 以小渡语气生成有上下文感的追问，替代机械模板。
        失败时 fallback 到静态模板（不影响主流程）。
        """
        city = partial_trip_meta.get("city", "")
        days = partial_trip_meta.get("days")
        need_city = "city" in missing or "city" in invalid
        need_days = "days" in missing

        if need_city and need_days:
            ask_hint = "请问你想去哪个城市？打算玩几天？"
        elif need_city:
            ask_hint = "请问你想去哪个城市？"
        elif need_days:
            ask_hint = f"去{'「' + city + '」' if city else '那里'}打算玩几天？"
        else:
            fields = ", ".join(missing + invalid)
            ask_hint = f"还需要补充：{fields}"

        prompt = (
            f"你是旅行助手「小渡」，性格亲切活泼、有些俏皮。\n"
            f"用户说：「{query}」\n\n"
            f"你已了解的信息：城市={city or '未知'}, 天数={days or '未知'}。\n"
            f"你需要向用户追问：{ask_hint}\n\n"
            f"要求：\n"
            f"- 用第一人称「我」开头或者直接聊天，不要用「为了帮您生成...」这类模板句式\n"
            f"- 先对用户的输入表示一点热情呼应（如知道城市就表示期待），再自然地问缺的信息\n"
            f"- 口语化，30-60字，不要用列表/破折号\n"
            f"- 只输出回复文字，不要解释"
        )
        try:
            reply = await self._call_llm_for_revise(prompt)
            reply = reply.strip().strip('"').strip("'")
            if len(reply) >= 10:
                return reply
        except Exception as e:
            logger.warning("_build_clarification_question_llm 失败，回退模板: %s", e)

        # fallback 到静态模板
        return build_clarification_question(missing, invalid)

    async def _handle_unsupported_intent(
        self,
        query: str,
        session_id: Optional[str],
        assumptions: list,
    ) -> NlTripPlanResponse:
        """
        对 unsupported 意图做友好分类处理：
        - 问候/闲聊 → LLM 生成个性化引导语，返回 needs_clarification
        - 其他非旅行请求 → 温和提示可以帮规划旅行，返回 needs_clarification（不用 failed）
        """
        is_greeting = bool(self._GREETING_RE.match(query.strip()))

        try:
            if is_greeting:
                prompt = (
                    f"你是旅行助手「小渡」，性格亲切活泼。用户对你说：「{query}」\n\n"
                    "请用一句话（15-40字）热情回应，然后自然地引导用户告诉你想去哪里旅行。\n"
                    "语气：轻松、自然、像朋友聊天，不要太正式。\n"
                    "不要输出任何额外内容，只输出这一句话。"
                )
            else:
                prompt = (
                    f"你是旅行助手「小渡」，性格亲切活泼。用户说：「{query}」\n\n"
                    "这不是旅行规划请求，请用一句话（15-50字）温和说明你主要负责旅行规划，"
                    "并自然地引导用户说出想去的目的地或旅行需求。\n"
                    "语气自然，不要生硬，不要重复用户原话。只输出这一句话。"
                )
            reply = await self._call_llm_for_revise(prompt)
            reply = reply.strip().strip('"').strip("'") or "你好呀～我是小渡，专门帮你规划旅行路线。有想去的地方吗？告诉我城市和天数～"
        except Exception as e:
            logger.warning("_handle_unsupported_intent LLM 调用失败: %s", e)
            reply = "你好呀～我是小渡，专门帮你规划旅行路线的！有想去的城市吗？告诉我，我来帮你安排行程。"

        return NlTripPlanResponse(
            status="needs_clarification",
            session_id=session_id,
            clarification_question=reply,
            assumptions=assumptions,
        )

    async def _handle_weather_query(
        self,
        query: str,
        trip_meta_dict: Optional[dict],
        session_id: Optional[str],
    ) -> NlTripPlanResponse:
        """
        天气查询处理：调高德 MCP maps_weather 工具直接返回结果。

        若 city 未知 → 以澄清形式问用户想查哪个城市。
        """
        city = (trip_meta_dict or {}).get("city", "")

        if not city:
            return NlTripPlanResponse(
                status="needs_clarification",
                session_id=session_id,
                clarification_question="你想查哪个城市的天气呢？",
                missing_fields=["city"],
            )

        try:
            from app.tools.persistent_mcp import PersistentMCPTool
            weather_tool = PersistentMCPTool(
                name="amap_weather",
                description="高德天气查询",
                server_command=["uvx", "amap-mcp-server"],
                env={"AMAP_MAPS_API_KEY": settings.amap_api_key},
                auto_expand=False,
            )
            raw_result = await asyncio.to_thread(
                lambda: (
                    weather_tool.open_shared() or True,
                    weather_tool.run({"tool_name": "maps_weather", "arguments": {"city": city}}),
                    weather_tool.close_shared(),
                )[1]
            )
            # raw_result 可能是 (str, list) tuple 或直接是 str
            weather_text = raw_result[0] if isinstance(raw_result, tuple) else str(raw_result)
            weather_text = weather_text.strip() if weather_text else ""

            if weather_text:
                # 用 LLM 把原始结果整理成自然语言
                summary_prompt = (
                    f"以下是高德地图返回的{city}天气原始数据：\n{weather_text[:1000]}\n\n"
                    "请用 2-3 句自然语言（中文，口语化）向用户说明当前天气和简单建议，"
                    "结尾可以询问用户是否要顺便规划一次出行。不超过80字。"
                )
                reply = await self._call_llm_for_revise(summary_prompt)
                reply = reply.strip() or f"{city}的天气已经查到了，出行前记得关注最新预报哦！"
            else:
                reply = f"暂时没能获取到{city}的天气数据，建议直接在高德地图或天气 App 查询。要不要顺便规划一下{city}的旅行路线？"

        except Exception as e:
            logger.warning("天气查询失败: %s", e)
            reply = f"天气查询暂时遇到了点问题，你可以去高德地图查一下{city}的天气。有需要帮你规划{city}旅行路线吗？"

        return NlTripPlanResponse(
            status="needs_clarification",
            session_id=session_id,
            clarification_question=reply,
        )
