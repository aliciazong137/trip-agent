"""
API 路由 - 旅行规划

H4 阶段：接入真实 TripPlannerAgent
"""
import asyncio
from fastapi import APIRouter, HTTPException

from app.models.schemas import (
    TripPlanRequest,
    TripPlanResponse,
    TripPlan,
    DayPlan,
    SessionResponse,
)
from app.config import settings
from app.agents.trip_planner import TripPlannerAgent
from app.services import session_store

router = APIRouter(prefix="/api/trip", tags=["trip"])

# 单例 TripPlannerAgent（LLM 连接复用）
_planner: TripPlannerAgent | None = None


def _get_planner() -> TripPlannerAgent:
    global _planner
    if _planner is None:
        _planner = TripPlannerAgent(max_steps=20)
    return _planner


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


health_router = APIRouter()


@health_router.get("/health")
async def health() -> dict:
    """健康检查"""
    return {
        "status": "ok",
        "llm_model": settings.llm_model_id,
        "context_dir": str(settings.context_dir),
    }
