"""
API 路由 - 旅行规划

H4 阶段：接入真实 TripPlannerAgent
"""
import asyncio
from typing import Optional
from fastapi import APIRouter, HTTPException

from app.models.schemas import (
    TripPlanRequest,
    TripPlanResponse,
    TripPlan,
    DayPlan,
    SessionResponse,
    NlTripPlanRequest,
    NlTripPlanResponse,
)
from app.config import settings
from app.agents.trip_planner import TripPlannerAgent
from app.agents.trip_orchestrator import TripPlanOrchestrator
from app.services import session_store

router = APIRouter(prefix="/api/trip", tags=["trip"])

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
async def create_trip_plan(request: TripPlanRequest) -> TripPlanResponse:
    """生成旅行计划（接入真实 Agent）"""
    trip_meta = request.trip_meta.model_dump()
    guide_text = request.guide_text or ""

    try:
        result = await _get_planner().plan_trip(trip_meta, guide_text)
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
async def get_session(session_id: str) -> SessionResponse:
    """获取会话状态"""
    loaded = await session_store.load_session(session_id)
    if not loaded:
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


@router.post("/plan-nl", response_model=NlTripPlanResponse)
async def create_trip_plan_from_nl(request: NlTripPlanRequest) -> NlTripPlanResponse:
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
        return await _get_orchestrator().plan_from_nl(request.query, request.session_id)
    except Exception as e:
        # 未预期异常：返回 failed 而非 500，让前端可处理
        return NlTripPlanResponse(
            status="failed",
            session_id=request.session_id,
            error_code="INTERNAL_ERROR",
            error_message=f"内部错误：{type(e).__name__}: {str(e)[:200]}",
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


health_router = APIRouter()


@health_router.get("/health")
async def health() -> dict:
    """健康检查"""
    return {
        "status": "ok",
        "llm_model": settings.llm_model_id,
        "context_dir": str(settings.context_dir),
    }
