"""
确定性 Tool 封装 - 适配 learn_version 分支的 Tool 基类（run 返回 str）

3 个工具：
  - BuildItineraryTool    确定性排程（buildItineraryFromData）
  - CheckConstraintsTool  约束校验（checkConstraints）
  - ReviseDayTool          修改单日（H4 占位，P5 接真实实现）

设计要点：
  - sessionId 从 parameters 注入（由 TripPlannerAgent 创建时传入）
  - 工具内部调 H3 移植的 scheduler/constraints/session_store
  - 返回 str（learn_version API），JSON 字符串给 LLM 读
"""
import asyncio
import json
from typing import List, Dict, Any

from hello_agents.tools import Tool, ToolParameter

from app.core.scheduler import build_itinerary_from_data
from app.core.constraints import check_constraints
from app.services import session_store
from app.utils.ids import assert_valid_session_id


class BuildItineraryTool(Tool):
    """确定性排程：根据 trip-meta + poi-list 生成行程（非 LLM 规划）"""

    def __init__(self):
        super().__init__(
            name="build_itinerary",
            description="根据 trip-meta 和 poi-list 生成行程（确定性排程，非 LLM 规划）。调用后 itinerary 保存到 session 文件。",
        )

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="session_id",
                type="string",
                description="会话ID，格式 sess_xxxxxxxxxxxx",
                required=True,
            ),
        ]

    def run(self, parameters: Dict[str, Any]) -> str:
        """同步入口（框架要求 run 是同步的，内部用 asyncio.run 跑异步逻辑）"""
        session_id = parameters.get("session_id", "")
        try:
            assert_valid_session_id(session_id)
        except ValueError as e:
            return f"错误：invalid session_id: {e}"

        try:
            result = asyncio.run(self._arun(session_id))
            return result
        except Exception as e:
            return f"错误：build_itinerary 失败：{e}"

    async def _arun(self, session_id: str) -> str:
        trip_meta = await session_store.load_trip_meta(session_id)
        if not trip_meta:
            return "错误：trip-meta 缺失，请先调用 set_trip_meta"

        poi_list = await session_store.load_poi_list(session_id)
        if not poi_list:
            return "错误：poi-list 缺失"

        # 第零期：读取预取的移动时间矩阵；缺失时排程内部用经纬度兜底
        travel_matrix = await session_store.load_travel_matrix(session_id)

        result = await build_itinerary_from_data(session_id, trip_meta, poi_list, travel_matrix)
        await session_store.save_itinerary(session_id, result["itinerary"])
        await session_store.update_session_phase(session_id, "planned")

        text = f"已生成 {len(result['itinerary']['days'])} 天行程"
        if result["warnings"]:
            text += f"，{len(result['warnings'])} 条警告：{'; '.join(result['warnings'])}"

        # 返回 JSON 字符串给 LLM
        return json.dumps({
            "status": "success",
            "text": text,
            "itinerary": result["itinerary"],
            "warnings": result["warnings"],
            "days_count": len(result["itinerary"]["days"]),
        }, ensure_ascii=False)


class CheckConstraintsTool(Tool):
    """约束校验：校验当前行程是否符合约束"""

    def __init__(self):
        super().__init__(
            name="verify_itinerary",
            description="校验当前行程是否符合约束（must 覆盖、时间倒挂/重叠、预算、重复等），返回 errors/warnings",
        )

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="session_id",
                type="string",
                description="会话ID",
                required=True,
            ),
        ]

    def run(self, parameters: Dict[str, Any]) -> str:
        session_id = parameters.get("session_id", "")
        try:
            assert_valid_session_id(session_id)
        except ValueError as e:
            return f"错误：invalid session_id: {e}"

        try:
            return asyncio.run(self._arun(session_id))
        except Exception as e:
            return f"错误：verify_itinerary 失败：{e}"

    async def _arun(self, session_id: str) -> str:
        itinerary = await session_store.load_itinerary(session_id)
        poi_list = await session_store.load_poi_list(session_id)
        trip_meta = await session_store.load_trip_meta(session_id)

        if not itinerary or not poi_list or not trip_meta:
            return "错误：itinerary/poi-list/trip-meta 缺失，无法校验"

        result = check_constraints(itinerary, poi_list, trip_meta)

        if not result["errors"] and not result["warnings"]:
            return json.dumps({
                "status": "success",
                "text": "行程校验通过，无 errors 无 warnings",
                "errors": [],
                "warnings": [],
            }, ensure_ascii=False)

        status = "error" if result["errors"] else "partial"
        parts: List[str] = []
        if result["errors"]:
            parts.append(f"errors({len(result['errors'])}): {'; '.join(result['errors'])}")
        if result["warnings"]:
            parts.append(f"warnings({len(result['warnings'])}): {'; '.join(result['warnings'])}")

        return json.dumps({
            "status": status,
            "text": "\n".join(parts),
            "errors": result["errors"],
            "warnings": result["warnings"],
        }, ensure_ascii=False)


class ReviseDayTool(Tool):
    """修改单日行程（H4 占位，P5 接 trip-revise-day 真实实现）"""

    def __init__(self):
        super().__init__(
            name="revise_day",
            description="修改某天的景点安排（结构化 operations：remove/move/add），修改已有行程需用户确认",
        )

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(name="session_id", type="string", description="会话ID", required=True),
            ToolParameter(name="day", type="integer", description="第几天（从1开始）", required=True),
            ToolParameter(
                name="operations",
                type="array",
                description='修改操作列表，每项 {type: "remove"/"move"/"add", poi_id, to_day?, position?}',
                required=True,
            ),
        ]

    def run(self, parameters: Dict[str, Any]) -> str:
        # H4 占位：返回未实现错误，避免 LLM 误以为修改完成
        return f"错误：revise_day 尚未实现（P5 接 trip-revise-day skill），收到参数：day={parameters.get('day')}, operations={parameters.get('operations')}"


# 导出所有确定性 tool
DETERMINISTIC_TOOLS = [BuildItineraryTool, CheckConstraintsTool, ReviseDayTool]
