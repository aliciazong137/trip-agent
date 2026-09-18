"""
API 路由 - 旅行规划

H4 阶段：接入真实 TripPlannerAgent
"""
import asyncio
import logging
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import Response as RawResponse

from app.models.schemas import (
    TripPlanRequest,
    TripPlanResponse,
    TripPlan,
    DayPlan,
    SessionResponse,
    NlTripPlanRequest,
    NlTripPlanResponse,
    ReviseNlRequest,
    ReviseNlResponse,
    MapData,
    GuideRoutesResponse,
    PlanFromRouteRequest,
    TripContextRequest,
    TripContextResponse,
)
from app.config import settings
from app.agents.trip_planner import TripPlannerAgent
from app.agents.trip_orchestrator import TripPlanOrchestrator
from app.services import session_store
from app.core.field_validator import validate_trip_meta, build_clarification_question

router = APIRouter(prefix="/api/trip", tags=["trip"])
discover_router = APIRouter(prefix="/api/discover", tags=["discover"])
auth_router = APIRouter(prefix="/api/auth", tags=["auth"])
logger = logging.getLogger(__name__)


def _authenticated_user_id(http_request: Request, fallback: str = "default_user") -> str:
    """Cookie 身份优先；绝不相信浏览器 body 中声称的 user_id。"""
    from app.services.anonymous_auth import COOKIE_NAME, verify_identity
    return verify_identity(http_request.cookies.get(COOKIE_NAME)) or fallback


@auth_router.post("/anonymous")
async def ensure_anonymous_identity(http_request: Request, response: Response) -> dict:
    from app.services.anonymous_auth import COOKIE_NAME, MAX_AGE_SECONDS, create_identity
    user_id = _authenticated_user_id(http_request, "")
    if not user_id:
        user_id, token = create_identity()
        response.set_cookie(COOKIE_NAME, token, max_age=MAX_AGE_SECONDS, httponly=True, samesite="lax", secure=False)
    from app.services.quota_service import is_developer
    return {"user_id": user_id, "anonymous": True, "is_developer": is_developer(user_id)}


def _non_planning_reply() -> str:
    return "当然可以。我的主要能力是规划旅行、比较路线、回顾或调整已生成的行程。你可以直接告诉我想问的事；如果要做旅行攻略，告诉我目的地和天数就行。"


def _planning_error_response(exc: Exception) -> tuple[str, str]:
    """把可预期的外部故障转成稳定 API 契约，保留日志中的完整堆栈。"""
    text = str(exc).lower()
    if isinstance(exc, TimeoutError) or "timeout" in text or "timed out" in text:
        return "UPSTREAM_TIMEOUT", "旅行数据服务响应较慢，请稍后重试。"
    if any(marker in text for marker in ("connection", "connect", "mcp", "network", "httpx")):
        return "UPSTREAM_UNAVAILABLE", "旅行数据服务暂时不可用，请稍后重试。"
    return "INTERNAL_ERROR", "服务处理这次请求时出了点问题，请稍后重试。"


def _trip_context_message(loaded: dict) -> str:
    """把已落盘 Session 压缩成可展示、可供意图识别使用的当前行程摘要。"""
    trip_meta = loaded.get("trip_meta") or {}
    itinerary = loaded.get("itinerary") or {}
    poi_list = loaded.get("poi_list") or {}
    poi_names = {
        str(p.get("id") or p.get("poi_id") or ""): p.get("name", "")
        for p in poi_list.get("pois", []) if isinstance(p, dict)
    }
    day_lines: list[str] = []
    for day in itinerary.get("days", [])[:5]:
        blocks = day.get("time_blocks", []) if isinstance(day, dict) else []
        names = [poi_names.get(str(block.get("poi_id")), "") for block in blocks if isinstance(block, dict)]
        names = [name for name in names if name]
        if names:
            day_lines.append(f"Day{day.get('day', len(day_lines) + 1)}：{' → '.join(names)}")
    city = trip_meta.get("city") or itinerary.get("city") or "本次"
    days = trip_meta.get("days") or len(day_lines)
    if day_lines:
        return f"当前是{city}{days}日行程：\n" + "\n".join(day_lines)
    return f"当前{city}行程已创建，但还没有可回顾的景点安排。"


def _session_owned_by(loaded: dict, user_id: str) -> bool:
    """新 Session 记录所属用户；历史 Session 没有 owner 时保持向后兼容。"""
    owner = (loaded.get("session") or {}).get("user_id")
    return not owner or owner == user_id


def _conversation_context_message(context: str) -> str:
    """当前 UI 可见对话的短回顾；不把它写入长期 Memory。"""
    lines = [line.strip() for line in (context or "").splitlines() if line.strip()]
    if not lines:
        return "我还没有足够的上文可以回顾。"
    return "记得。刚才我们聊到：\n" + "\n".join(lines[-6:])


def _replan_has_direction(meta: dict) -> bool:
    """用户给出这些方向后即可重新生成候选路线，无需再追问已有事实。"""
    return any(bool(meta.get(key)) for key in ("preferences", "must_visit", "avoid", "pace", "transportation", "budget"))


def _merge_replan_meta(existing: dict, update: dict) -> dict:
    """保留已确认旅行事实，只让用户本轮明确表达覆盖对应字段。"""
    merged = dict(existing or {})
    for key, value in (update or {}).items():
        if value not in (None, "", [], {}):
            merged[key] = value
    return merged


@discover_router.get("/trending")
async def get_trending_travel_notes(http_request: Request, personalized: bool = False) -> dict:
    """读取每日 18:00 缓存的小红书热门旅游笔记。"""
    from app.services.trending_notes import get_trending_notes
    payload = _trending_payload_with_local_covers(get_trending_notes())
    if not personalized:
        return payload
    cities = await session_store.list_recent_cities(_authenticated_user_id(http_request))
    if not cities:
        return payload
    notes = list(payload.get("notes") or [])
    position = {city: index for index, city in enumerate(cities)}
    notes.sort(key=lambda note: min((position.get(tag, len(cities)) for tag in note.get("tags", [])), default=len(cities)))
    return {**payload, "notes": notes}


def _trending_payload_with_local_covers(payload: dict) -> dict:
    """浏览器只加载本站图片地址，避免 CDN 外链被防盗链或 HTTPS 策略拦截。"""
    notes = []
    for source in payload.get("notes") or []:
        note = dict(source)
        if note.get("cover_url") and note.get("id"):
            note["cover_url"] = f"/api/discover/trending/{note['id']}/cover"
        notes.append(note)
    return {**payload, "notes": notes}


@discover_router.get("/trending/{note_id}/cover")
async def get_trending_note_cover(note_id: str) -> RawResponse:
    """代理已缓存热门笔记的封面；不接受任意 URL，避免形成开放代理。"""
    from app.services.trending_notes import get_trending_notes

    note = next(
        (item for item in get_trending_notes().get("notes", []) if str(item.get("id")) == note_id),
        None,
    )
    source_url = str((note or {}).get("cover_url") or "")
    parsed = urlparse(source_url)
    if not source_url or parsed.scheme not in {"http", "https"} or not (parsed.hostname or "").endswith("xhscdn.com"):
        raise HTTPException(status_code=404, detail="封面不存在")

    try:
        import httpx

        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            upstream = await client.get(source_url, headers={"User-Agent": "Mozilla/5.0"})
        content_type = upstream.headers.get("content-type", "")
        if upstream.status_code != 200 or not content_type.startswith("image/") or len(upstream.content) > 8 * 1024 * 1024:
            raise HTTPException(status_code=502, detail="封面暂时不可用")
        return RawResponse(
            content=upstream.content,
            media_type=content_type.split(";", 1)[0],
            headers={"Cache-Control": "public, max-age=21600"},
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("热门笔记封面代理失败 note=%s: %s", note_id, exc)
        raise HTTPException(status_code=502, detail="封面暂时不可用") from exc

# 单例 TripPlannerAgent（LLM 连接复用）
_planner: TripPlannerAgent | None = None
# 单例 TripPlanOrchestrator
_orchestrator: TripPlanOrchestrator | None = None


def _get_planner() -> TripPlannerAgent:
    global _planner
    if _planner is None:
        _planner = TripPlannerAgent(max_steps=20)
    return _planner


def _get_orchestrator() -> TripPlanOrchestrator:
    """单例 Orchestrator，复用 Planner 的 MCP 连接"""
    global _orchestrator
    if _orchestrator is None:
        orch = TripPlanOrchestrator()
        # 复用 _get_planner 创建的实例（共享 MCP 连接）
        orch._planner = _get_planner()
        _orchestrator = orch
    return _orchestrator


@router.post("/plan", response_model=TripPlanResponse)
async def create_trip_plan(request: TripPlanRequest, http_request: Request) -> TripPlanResponse:
    """生成旅行计划（接入真实 Agent）"""
    trip_meta = request.trip_meta.model_dump()
    guide_text = request.guide_text or ""

    user_id = _authenticated_user_id(http_request)
    from app.services.quota_service import consume, exceeded_message
    quota = consume(user_id, "plan")
    if not quota.allowed:
        return TripPlanResponse(
            session_id="quota_exceeded", status="failed", warnings=[],
            error={"code": "QUOTA_EXCEEDED", "message": exceeded_message("plan")},
        )
    try:
        result = await _get_planner().plan_trip(trip_meta, guide_text, user_context={"user_id": user_id})
    except Exception as e:
        # Agent 调用失败时返回 mock，保证 API 可用
        return TripPlanResponse(
            session_id="error",
            status="failed",
            warnings=[],
            error={"code": "AGENT_ERROR", "message": str(e)[:500]},
        )

    trip_plan_data = result.get("trip_plan", {})
    trip_plan = TripPlan(**trip_plan_data) if trip_plan_data else None

    return TripPlanResponse(
        session_id=result["session_id"],
        status=result.get("status", "ok"),
        trip_plan=trip_plan,
        warnings=result.get("warnings", []),
    )


@router.get("/session/{session_id}", response_model=SessionResponse)
async def get_session(session_id: str, http_request: Request) -> SessionResponse:
    """获取会话状态"""
    loaded = await session_store.load_session(session_id)
    if not loaded:
        raise HTTPException(status_code=404, detail="Session not found")
    if not _session_owned_by(loaded, _authenticated_user_id(http_request)):
        raise HTTPException(status_code=404, detail="Session not found")

    from datetime import datetime, timezone
    updated_at = await session_store.touch_session(session_id)

    return SessionResponse(
        session_id=session_id,
        trip_meta=loaded.get("trip_meta"),
        poi_list=loaded.get("poi_list"),
        itinerary=loaded.get("itinerary"),
        session_state=loaded.get("session"),
        updated_at=updated_at,
    )


@router.get("/session/{session_id}/map", response_model=MapData)
async def get_session_map(session_id: str, http_request: Request) -> MapData:
    """读取已有 session，生成对话路线可视化所需的稳定地图数据。"""
    loaded = await session_store.load_session(session_id)
    if not loaded:
        raise HTTPException(status_code=404, detail="Session not found")
    if not _session_owned_by(loaded, _authenticated_user_id(http_request)):
        raise HTTPException(status_code=404, detail="Session not found")

    from app.services.map_service import build_map_data

    return build_map_data(
        loaded.get("trip_meta"),
        loaded.get("poi_list"),
        loaded.get("itinerary"),
    )


@router.post("/session/{session_id}/context", response_model=TripContextResponse)
async def answer_trip_context(session_id: str, request: TripContextRequest, http_request: Request) -> TripContextResponse:
    """基于当前 Session 回答行程回顾类追问，绝不重新触发需求理解或攻略生成。"""
    loaded = await session_store.load_session(session_id)
    if not loaded:
        raise HTTPException(status_code=404, detail="Session not found")
    if not _session_owned_by(loaded, _authenticated_user_id(http_request)):
        raise HTTPException(status_code=404, detail="Session not found")

    message = "当然记得。" + _trip_context_message(loaded)
    logger.info("行程上下文问答: session=%s query=%r", session_id, request.query[:80])
    return TripContextResponse(session_id=session_id, message=message)


@router.post("/plan-nl", response_model=NlTripPlanResponse)
async def create_trip_plan_from_nl(request: NlTripPlanRequest, http_request: Request) -> NlTripPlanResponse:
    """
    自然语言规划入口（第一期）

    流程：
        query → IntentRecognizer 抽取 trip_meta
             → field_validator 校验必填字段
             → 缺字段 → needs_clarification + 存 pending
             → 齐全 → TripPlannerAgent 生成行程

    澄清续接：
        前端在 needs_clarification 响应里拿到 session_id，
        用户补充后用同一 session_id + 新 query 再次调用，后端合并原 query。
    """
    try:
        result = await _get_orchestrator().plan_from_nl(request.query, request.session_id, _authenticated_user_id(http_request))
        logger.info(
            "plan-nl 完成: query=%r session=%s → status=%s missing=%s err=%s",
            request.query[:80], request.session_id, result.status,
            result.missing_fields, result.error_code,
        )
        return result
    except Exception as e:
        logger.exception("plan-nl 未预期异常: query=%r session=%s", request.query[:80], request.session_id)
        # 未预期异常：返回 failed 而非 500，让前端可处理
        return NlTripPlanResponse(
            status="failed",
            session_id=request.session_id,
            error_code="INTERNAL_ERROR",
            error_message=f"内部错误：{type(e).__name__}: {str(e)[:200]}",
        )


@router.post("/guide-routes/stream")
async def create_guide_routes_stream(request: NlTripPlanRequest, http_request: Request):
    """阶段 A：攻略选线流式接口。只生成攻略 Markdown + route_options，不跑高德/酒店。"""
    import json as _json
    from fastapi.responses import StreamingResponse

    q: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    user_id = _authenticated_user_id(http_request)

    def on_stage(event: dict) -> None:
        loop.call_soon_threadsafe(q.put_nowait, {"type": "stage", **event})

    def on_delta(delta: str) -> None:
        loop.call_soon_threadsafe(q.put_nowait, {"type": "guide_delta", "content": delta})

    async def run_guide():
        import time
        started_at = time.monotonic()
        try:
            # 单一对话入口：把已存在的行程摘要交给意图模型，让模型区分回顾、修改和新规划。
            orch = _get_orchestrator()
            loaded_session = None
            session_context = ""
            if request.session_id:
                loaded_session = await session_store.load_session(request.session_id)
                if loaded_session and not _session_owned_by(loaded_session, user_id):
                    logger.warning("忽略非所属用户的 session: session=%s", request.session_id)
                    loaded_session = None
                if loaded_session:
                    session_context = "当前已生成行程（仅用于判断是否在问或修改它，不可补全新规划事实）：\n" + _trip_context_message(loaded_session)
                    replan_draft = (loaded_session.get("session") or {}).get("replan_draft")
                    if replan_draft:
                        session_context += "\n正在等待重新攻略偏好：用户已否定上一版方案；下一句若提供主题、节奏、必去或避开内容，应直接重新生成路线方案。"
            conversation_context = (request.conversation_context or "").strip()
            context_parts = [part for part in [session_context, ("当前聊天摘要（仅用于回顾这轮对话，不可补全新规划事实）：\n" + conversation_context) if conversation_context else ""] if part]
            # 热门推荐是一个明确的产品动作：用户点了“查看路线方案”，不应再把
            # 笔记摘要当普通聊天文本解析，并因此反问天数。
            inspiration_meta = dict(request.inspiration_trip_meta or {})
            has_inspiration = request.guide_style == "inspiration" and bool(request.inspiration_note)
            intent = None if has_inspiration else await orch.intent_recognizer.recognize(request.query, "\n\n".join(context_parts))
            if has_inspiration:
                trip_meta_dict = inspiration_meta
            else:
                assert intent is not None
                trip_meta_dict = dict(intent.trip_meta or {})
            intent_seconds = time.monotonic() - started_at
            if intent and intent.intent == "conversation_context_question" and conversation_context:
                await q.put({"type": "guide_done", "data": GuideRoutesResponse(
                    status="ok", session_id=request.session_id, action="conversation",
                    assistant_message=intent.chat_reply or _conversation_context_message(conversation_context),
                ).model_dump(mode="json")})
                return
            if intent and intent.intent == "current_trip_question" and loaded_session:
                await q.put({"type": "guide_done", "data": GuideRoutesResponse(
                    status="ok", session_id=request.session_id, action="current_trip_question",
                    assistant_message=intent.chat_reply or ("当然记得。" + _trip_context_message(loaded_session)),
                ).model_dump(mode="json")})
                return
            if intent and intent.intent == "current_trip_replan" and loaded_session:
                replan_update = dict(intent.trip_meta or {})
                if not _replan_has_direction(replan_update):
                    await session_store.save_replan_draft(request.session_id, {"status": "awaiting_preferences"})
                    await q.put({"type": "guide_done", "data": GuideRoutesResponse(
                        status="ok", session_id=request.session_id, action="conversation",
                        assistant_message=intent.chat_reply or (
                            "明白，这版没有贴合你的期待。你一次告诉我更喜欢的主题、节奏，"
                            "以及必去或不想去的地方，我会保留已确认的出行信息重新给你出 3 条路线。"
                        ),
                    ).model_dump(mode="json")})
                    return
                trip_meta_dict = _merge_replan_meta(loaded_session.get("trip_meta") or {}, replan_update)
                await session_store.save_replan_draft(request.session_id, None)
                logger.info("重新攻略: session=%s city=%s days=%s", request.session_id, trip_meta_dict.get("city"), trip_meta_dict.get("days"))
            if intent and intent.intent == "current_trip_modify" and loaded_session:
                revision = await orch.revise_from_nl(
                    request.session_id, request.query, user_id=user_id
                )
                ok = revision.status == "ok"
                await q.put({"type": "guide_done", "data": GuideRoutesResponse(
                    status="ok" if ok else "failed", session_id=request.session_id,
                    action="current_trip_modify", itinerary_updated=ok,
                    assistant_message=revision.message if ok else None,
                    error_code=None if ok else revision.error_code or "REVISION_FAILED",
                    error_message=None if ok else revision.error_message or revision.message,
                ).model_dump(mode="json")})
                return
            if intent and intent.intent in {"conversation", "unsupported"}:
                await q.put({"type": "guide_done", "data": GuideRoutesResponse(
                    status="ok", session_id=request.session_id, action="conversation",
                    assistant_message=intent.chat_reply or _non_planning_reply(),
                ).model_dump(mode="json")})
                return
            if intent and intent.intent == "weather_query":
                await q.put({"type": "guide_done", "data": GuideRoutesResponse(
                    status="ok", session_id=request.session_id, action="weather_query",
                    assistant_message="我可以帮你查旅行相关天气。告诉我想查的城市和日期，或直接开始规划一次行程。",
                ).model_dump(mode="json")})
                return
            # 模型只负责抽取；是否追问由确定性规则决定，不能把亲子偏好变成人数必填。
            _, missing, invalid = validate_trip_meta(trip_meta_dict)
            if "travelers" in invalid:
                trip_meta_dict.pop("travelers", None)
                invalid.remove("travelers")
            logger.info(
                "guide-routes 校验: session=%s llm_missing=%s missing=%s invalid=%s",
                request.session_id, intent.missing_fields if intent else [], missing, invalid,
            )
            if missing or invalid:
                await q.put({"type": "guide_done", "data": GuideRoutesResponse(
                    status="failed",
                    session_id=request.session_id,
                    action="clarification",
                    error_code="NEEDS_CLARIFICATION",
                    error_message=build_clarification_question(missing, invalid, trip_meta_dict),
                ).model_dump(mode="json")})
                return
            capability = "guide_inspiration" if request.guide_style == "inspiration" else "guide_full"
            from app.services.quota_service import consume, exceeded_message
            quota = consume(user_id, capability)
            if not quota.allowed:
                await q.put({"type": "guide_done", "data": GuideRoutesResponse(
                    status="failed", session_id=request.session_id, error_code="QUOTA_EXCEEDED",
                    error_message=exceeded_message(capability),
                ).model_dump(mode="json")})
                return
            await q.put({"type": "guide_start"})
            result = await _get_planner().generate_guide_routes(
                trip_meta_dict,
                guide_style=request.guide_style,
                inspiration_note=request.inspiration_note if has_inspiration else None,
                on_stage=on_stage,
                on_delta=on_delta,
            )
            result["session_id"] = request.session_id
            result["action"] = "route_options"
            result["trip_meta"] = trip_meta_dict
            logger.info(
                "[perf] guide-routes request total=%.1fs intent=%.1fs status=%s",
                time.monotonic() - started_at, intent_seconds, result.get("status"),
            )
            await q.put({"type": "guide_done", "data": GuideRoutesResponse(**result).model_dump(mode="json")})
        except Exception as e:
            logger.exception("guide-routes/stream 未预期异常: query=%r", request.query[:80])
            error_code, error_message = _planning_error_response(e)
            await q.put({"type": "guide_done", "data": GuideRoutesResponse(status="failed", session_id=request.session_id, error_code=error_code, error_message=error_message).model_dump(mode="json")})

    guide_task = asyncio.create_task(run_guide())

    async def event_gen():
        try:
            while True:
                item = await q.get()
                yield f"data: {_json.dumps(item, ensure_ascii=False, default=str)}\n\n"
                if item["type"] == "guide_done":
                    break
        finally:
            if not guide_task.done():
                guide_task.cancel()

    return StreamingResponse(event_gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
    })


@router.post("/plan-from-route/stream")
async def create_trip_plan_from_route_stream(request: PlanFromRouteRequest, http_request: Request):
    """阶段 B：用户确认路线后，基于 route poi_hints 落地成真实行程。"""
    import json as _json
    from fastapi.responses import StreamingResponse
    from app.models.schemas import NlTripPlanResponse, RetrievedSource, TripMeta

    q: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    user_id = _authenticated_user_id(http_request)

    def on_stage(event: dict) -> None:
        loop.call_soon_threadsafe(q.put_nowait, {"type": "stage", **event})

    async def run_plan():
        try:
            orch = _get_orchestrator()
            # 阶段 A 已做过字段抽取时，直接复用其规范化事实；仅为兼容旧客户端才回退识别。
            assumptions = []
            if request.trip_meta:
                meta = dict(request.trip_meta)
            else:
                intent = await orch.intent_recognizer.recognize(request.query)
                meta = dict(intent.trip_meta or {})
                assumptions = intent.assumptions
            _, missing, invalid = validate_trip_meta(meta)
            if missing or invalid:
                await q.put({"type": "result", "data": NlTripPlanResponse(status="failed", session_id=request.session_id, error_code="MISSING_CITY", error_message="还需要目的地城市").model_dump(mode="json")})
                return
            meta.setdefault("transportation", "公共交通")
            meta["preferences"] = " ".join([p for p in [meta.get("preferences") or "", request.selected_route.title, request.selected_route.suitable_for] if p]).strip()
            poi_hints = request.selected_route.poi_hints or []
            if poi_hints:
                meta["must_visit"] = poi_hints
            meta["_skip_xhs"] = True
            from app.services.quota_service import consume, exceeded_message
            quota = consume(user_id, "plan")
            if not quota.allowed:
                await q.put({"type": "result", "data": NlTripPlanResponse(
                    status="failed", session_id=request.session_id, error_code="QUOTA_EXCEEDED",
                    error_message=exceeded_message("plan"),
                ).model_dump(mode="json")})
                return
            on_stage({"agent": "route_confirm", "label": "路线已确认", "status": "done"})
            result = await _get_planner().plan_trip(
                meta,
                guide_text=request.selected_route.route_text,
                user_context={"user_id": user_id},
                on_stage=on_stage,
            )
            trip_plan_data = result.get("trip_plan", {})
            trip_plan = None
            if trip_plan_data:
                try:
                    from app.models.schemas import TripPlan
                    trip_plan = TripPlan.model_validate(trip_plan_data)
                except Exception as e:
                    logger.warning("plan-from-route TripPlan 校验失败: %s", str(e)[:200])
            # 两阶段流程此前绕过了 Orchestrator.plan_from_nl，导致最终选线后的完整行程
            # 没有写进 Memory。异步写入，不增加当前响应等待时间。
            if settings.memory_enabled and result.get("status") == "ok" and result.get("session_id"):
                _get_orchestrator()._schedule_memory_recording(
                    merged_query=request.query,
                    trip_meta=TripMeta.model_validate(meta),
                    trip_plan_data=trip_plan_data,
                    warnings=result.get("warnings", []),
                    user_id=user_id,
                    session_id=result["session_id"],
                )
            sources = [RetrievedSource.model_validate(s) for s in result.get("sources", [])]
            # 酒店候选：只保留名称/评分/星级/区域，不要价格和链接
            hotel_candidates = [
                {
                    "name": h.get("name", ""),
                    "rating": h.get("rating", ""),
                    "type": h.get("type", ""),
                    "area": h.get("area", ""),
                }
                for h in (result.get("hotel_candidates") or [])
                if h.get("name")
            ][:5]
            resp = NlTripPlanResponse(
                status="ok" if result.get("status") == "ok" else "failed",
                session_id=result.get("session_id"),
                trip_meta=TripMeta.model_validate(meta),
                trip_plan=trip_plan,
                assumptions=assumptions,
                sources=sources,
                warnings=result.get("warnings", []),
                error_code=None if result.get("status") == "ok" else "POI_EXTRACTION_EMPTY",
                error_message=None if result.get("status") == "ok" else "基于该路线搜索景点失败，请换一条路线或补充具体景点。",
                hotel_candidates=hotel_candidates,
            )
            await q.put({"type": "result", "data": resp.model_dump(mode="json")})
        except Exception as e:
            logger.exception("plan-from-route/stream 未预期异常: route=%r", request.selected_route.title)
            error_code, error_message = _planning_error_response(e)
            await q.put({"type": "result", "data": NlTripPlanResponse(status="failed", session_id=request.session_id, error_code=error_code, error_message=error_message).model_dump(mode="json")})

    plan_task = asyncio.create_task(run_plan())

    async def event_gen():
        try:
            while True:
                item = await q.get()
                yield f"data: {_json.dumps(item, ensure_ascii=False, default=str)}\n\n"
                if item["type"] == "result":
                    break
        finally:
            if not plan_task.done():
                plan_task.cancel()

    return StreamingResponse(event_gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
    })


@router.post("/plan-nl/stream")
async def create_trip_plan_from_nl_stream(request: NlTripPlanRequest, http_request: Request):
    """
    自然语言规划入口（SSE 流式版）

    事件格式（text/event-stream，每行 data: {...}）：
        {"type": "stage", "agent": "weather", "label": "天气大师", "status": "start"/"done", "weather": [...]}
        {"type": "result", "data": <NlTripPlanResponse JSON>}

    前端边规划边渲染大师工作流；天气大师完成时即可提前展示天气提醒。
    """
    import json as _json
    from fastapi.responses import StreamingResponse

    q: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    user_id = _authenticated_user_id(http_request)

    def on_stage(event: dict) -> None:
        # 回调可能在 planner 的 worker 线程触发，需线程安全投递
        loop.call_soon_threadsafe(q.put_nowait, {"type": "stage", **event})

    async def run_plan():
        try:
            result = await _get_orchestrator().plan_from_nl(
                request.query, request.session_id,
                user_id, on_stage=on_stage,
            )
            await q.put({"type": "result", "data": result.model_dump(mode="json")})
        except Exception as e:
            logger.exception("plan-nl/stream 未预期异常: query=%r session=%s", request.query[:80], request.session_id)
            await q.put({"type": "result", "data": NlTripPlanResponse(
                status="failed",
                session_id=request.session_id,
                error_code="INTERNAL_ERROR",
                error_message=f"内部错误：{type(e).__name__}: {str(e)[:200]}",
            )})

    plan_task = asyncio.create_task(run_plan())

    async def event_gen():
        try:
            while True:
                item = await q.get()
                yield f"data: {_json.dumps(item, ensure_ascii=False, default=str)}\n\n"
                if item["type"] == "result":
                    break
        finally:
            if not plan_task.done():
                plan_task.cancel()

    return StreamingResponse(event_gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
    })


@router.post("/session/{session_id}/revise-nl", response_model=ReviseNlResponse)
async def revise_trip_from_nl(session_id: str, request: ReviseNlRequest, http_request: Request) -> ReviseNlResponse:
    """
    自然语言行程修订接口

    流程：
        加载现有 Session → LLM 理解修订意图 → 应用变更 → 持久化 → 返回 AI 消息

    用法：
        POST /api/trip/session/{session_id}/revise-nl
        Body: {"query": "下午少走路，加一个咖啡馆", "day": 1}

    返回：
        ReviseNlResponse（status, message, itinerary_version）
        成功后客户端再调用 GET /api/trip/session/{session_id}/map 刷新地图。
    """
    try:
        loaded = await session_store.load_session(session_id)
        user_id = _authenticated_user_id(http_request)
        if not loaded or not _session_owned_by(loaded, user_id):
            return ReviseNlResponse(
                status="failed", session_id=session_id, message="未找到可调整的行程。",
                error_code="SESSION_NOT_FOUND", error_message="Session not found",
            )
        from app.services.quota_service import consume, exceeded_message
        quota = consume(user_id, "revise")
        if not quota.allowed:
            message = exceeded_message("revise")
            return ReviseNlResponse(
                status="failed", session_id=session_id, message=message,
                error_code="QUOTA_EXCEEDED", error_message=message,
            )
        return await _get_orchestrator().revise_from_nl(
            session_id=session_id,
            query=request.query,
            day=request.day,
            user_id=user_id,
        )
    except Exception as e:
        return ReviseNlResponse(
            status="failed",
            session_id=session_id,
            message=f"内部错误：{type(e).__name__}",
            error_code="INTERNAL_ERROR",
            error_message=str(e)[:200],
        )


# ─── RAG 调试接口（第二期） ────────────────────────────────────────────────────

rag_router = APIRouter(prefix="/api/rag", tags=["rag"])


@rag_router.get("/search")
async def rag_search(q: str, top_k: int = 3, city: Optional[str] = None) -> dict:
    """
    RAG 检索调试接口

    用法：
        GET /api/rag/search?q=南京+中山陵&top_k=5&city=南京

    响应：
        若 RAG_DEBUG_RESPONSE=true：返回完整 content
        否则只返回 sources 摘要（title + score），不含正文（避免暴露外部内容）
    """
    from app.rag import retriever

    chunks = await retriever.search(q, top_k=top_k, city=city)

    if settings.rag_debug_response:
        return {
            "query": q,
            "city": city,
            "count": len(chunks),
            "chunks": [c.model_dump() for c in chunks],
        }

    return {
        "query": q,
        "city": city,
        "count": len(chunks),
        "sources": [
            {"title": c.title or c.source, "score": round(c.score, 3),
             "city": c.city, "is_external": c.is_external}
            for c in chunks
        ],
    }


@rag_router.get("/stats")
async def rag_stats() -> dict:
    """RAG 知识库统计"""
    from app.rag import vectorstore
    return {
        "total_chunks": vectorstore.count(),
        "chroma_path": str(settings.rag_chroma_dir),
        "guides_path": str(settings.rag_guides_dir),
    }


# ─── Memory 调试接口（第四期） ───────────────────────────────────────────────

memory_router = APIRouter(prefix="/api/memory", tags=["memory"])


def _get_memory_manager():
    return _get_orchestrator().memory


@memory_router.get("/{user_id}/search")
async def memory_search(user_id: str, q: str, limit: int = 5) -> dict:
    results = await asyncio.to_thread(_get_memory_manager().search, user_id, q, limit)
    return {
        "user_id": user_id,
        "query": q,
        "count": len(results),
        "memories": [
            {"id": r.item.id, "type": r.item.memory_type, "score": round(r.score, 3), "content": r.item.content, "metadata": r.item.metadata}
            for r in results
        ],
    }


@memory_router.get("/{user_id}/summary")
async def memory_summary(user_id: str, limit: int = 10) -> dict:
    return {"user_id": user_id, "summary": _get_memory_manager().summary(user_id, limit)}


@memory_router.get("/{user_id}/stats")
async def memory_stats(user_id: str) -> dict:
    return _get_memory_manager().stats(user_id)


@memory_router.post("/{user_id}/consolidate")
async def memory_consolidate(user_id: str, from_type: str = "episodic", to_type: str = "semantic") -> dict:
    n = await asyncio.to_thread(_get_memory_manager().consolidate, user_id, from_type, to_type)
    return {"user_id": user_id, "consolidated": n}


@memory_router.delete("/{user_id}")
async def memory_clear(user_id: str) -> dict:
    n = await asyncio.to_thread(_get_memory_manager().clear_all, user_id)
    return {"user_id": user_id, "cleared": n}


health_router = APIRouter()


@health_router.get("/health")
async def health() -> dict:
    """健康检查"""
    return {
        "status": "ok",
        "llm_model": settings.llm_model_id,
        "context_dir": str(settings.context_dir),
    }
