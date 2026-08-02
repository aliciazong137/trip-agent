"""
TripPlannerAgent - 主协调器（照搬第十三章用法 + 融合本项目确定性排程）

关键（与第十三章一致）：
  - 共享一个 MCPTool 实例，3 个研究 Agent 用 add_tool 添加
  - query 里直接写 [TOOL_CALL:amap_maps_xxx:参数]，精确控制工具调用
  - 研究 Agent 用 SimpleAgent + enable_tool_calling=True

差异（本项目增强）：
  - PlannerAgent 之外还有确定性排程：build_itinerary / verify_itinerary
  - 行程顺序由确定性排程决定，PlannerAgent 只做 JSON 整合 + 调用确定性工具
"""
import asyncio
import json
import os
import re
import uuid
from typing import Optional

from hello_agents import HelloAgentsLLM, SimpleAgent, ReActAgent, ToolRegistry
from hello_agents.tools import MCPTool

from app.agents.prompts import (
    ATTRACTION_AGENT_PROMPT,
    WEATHER_AGENT_PROMPT,
    HOTEL_AGENT_PROMPT,
    PLANNER_AGENT_PROMPT,
)
from app.tools.state_tools import BuildItineraryTool, CheckConstraintsTool, ReviseDayTool
from app.services import session_store
from app.config import settings


def new_session_id() -> str:
    """生成合法 sessionId：sess_ + 12位hex"""
    return f"sess_{uuid.uuid4().hex[:12]}"


def _create_amap_mcp_tool() -> MCPTool:
    """创建高德地图 MCP 工具（共享实例，16 个工具自动展开）"""
    tool = MCPTool(
        name="amap",
        description="高德地图服务",
        server_command=["uvx", "amap-mcp-server"],
        env={"AMAP_MAPS_API_KEY": settings.amap_api_key},
        auto_expand=True,
    )
    tool.expandable = True
    return tool


def _parse_llm_json(text: str) -> Optional[dict]:
    """从 LLM 响应文本里提取 JSON"""
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    return None


class TripPlannerAgent:
    """
    旅行规划主协调器

    H5a：3 个研究 Agent 共享高德 MCP，用 [TOOL_CALL:...] 精确调工具
    """

    def __init__(self, max_steps: int = 20):
        self.llm = HelloAgentsLLM()

        # 共享一个 MCPTool 实例（只创建一次，避免重复启动 MCP server）
        self.amap_tool = _create_amap_mcp_tool()

        # 3 个研究 Agent：照搬第十三章做法——SimpleAgent + add_tool + enable_tool_calling
        # 景点 Agent：挂 maps_text_search + maps_search_detail + glm_web_search（补门票）
        from app.tools.glm_web_search_tool import GLMWebSearchTool
        attraction_registry = ToolRegistry()
        # 高德 MCP 的 16 个工具里，只给景点 Agent 挂 text_search + search_detail
        amap_tools = self.amap_tool.get_expanded_tools()
        for t in amap_tools:
            if "maps_text_search" in t.name or "maps_search_detail" in t.name:
                attraction_registry.register_tool(t)
        # 再挂 GLM web search（补门票）
        attraction_registry.register_tool(GLMWebSearchTool())
        self.attraction_agent = SimpleAgent(
            name="AttractionSearchAgent",
            llm=self.llm,
            system_prompt=ATTRACTION_AGENT_PROMPT,
            tool_registry=attraction_registry,
            enable_tool_calling=True,
        )

        self.weather_agent = SimpleAgent(
            name="WeatherQueryAgent",
            llm=self.llm,
            system_prompt=WEATHER_AGENT_PROMPT,
            enable_tool_calling=True,
        )
        self.weather_agent.add_tool(self.amap_tool)

        self.hotel_agent = SimpleAgent(
            name="HotelAgent",
            llm=self.llm,
            system_prompt=HOTEL_AGENT_PROMPT,
            enable_tool_calling=True,
        )
        self.hotel_agent.add_tool(self.amap_tool)

        # PlannerAgent：SimpleAgent（一次 LLM 调用，不做 ReAct 循环）
        # 行程顺序由确定性排程决定，PlannerAgent 只做 JSON 整合
        self.planner_agent = SimpleAgent(
            name="PlannerAgent",
            llm=self.llm,
            system_prompt=PLANNER_AGENT_PROMPT,
            enable_tool_calling=False,  # 不挂工具，纯 JSON 整合
        )

    async def plan_trip(self, trip_meta: dict, guide_text: str = "") -> dict:
        """
        生成旅行计划

        编排（修正后）：
          1. create_session
          2. 并行调 3 个研究 Agent（asyncio.gather）—— 景点/天气/酒店独立任务
          3. 确定性排程（Python 直接调，不经过 LLM）：
             - 从景点研究结果提取 POI 存到 poi-list
             - build_itinerary_from_data 生成行程
             - check_constraints 校验
          4. PlannerAgent（SimpleAgent，一次 LLM 调用）整合所有结果 → TripPlan JSON
          5. 从 session 读确定性 itinerary 作为最终 days

        Returns:
            {session_id, trip_plan, warnings, status, research}
        """
        # 1. 创建 session
        session_id = new_session_id()
        await session_store.create_session(session_id, trip_meta, guide_text)

        city = trip_meta.get("city", "")
        days = trip_meta.get("days", 3)
        preferences = trip_meta.get("preferences", "景点")
        accommodation = trip_meta.get("accommodation", "经济型")

        # 2. 串行调 3 个研究 Agent（第十三章原版做法，MCP server 单进程串行处理）
        attraction_query = (
            f"请使用 amap_maps_text_search 工具搜索 {city} 的 {preferences} 相关景点。\n"
            f"[TOOL_CALL:amap_maps_text_search:keywords={preferences},city={city}]"
        )
        attraction_response = await asyncio.to_thread(self.attraction_agent.run, attraction_query)

        weather_query = f"请查询 {city} 的天气信息\n[TOOL_CALL:amap_maps_weather:city={city}]"
        weather_response = await asyncio.to_thread(self.weather_agent.run, weather_query)

        hotel_query = (
            f"请搜索 {city} 的 {accommodation} 酒店\n"
            f"[TOOL_CALL:amap_maps_text_search:keywords=酒店,city={city}]"
        )
        hotel_response = await asyncio.to_thread(self.hotel_agent.run, hotel_query)

        # 3. 确定性排程（Python 直接调，不经过 LLM）
        # 从景点研究结果提取 POI（LLM 返回的 JSON 数组）
        pois = self._extract_pois_from_response(attraction_response, city)
        if pois:
            await session_store.save_poi_list(session_id, {"city": city, "pois": pois})

            # 确定性排程
            from app.core.scheduler import build_itinerary_from_data
            schedule_result = await build_itinerary_from_data(session_id, trip_meta, {"city": city, "pois": pois})
            await session_store.save_itinerary(session_id, schedule_result["itinerary"])
            await session_store.update_session_phase(session_id, "planned")
            itinerary = schedule_result["itinerary"]
            schedule_warnings = schedule_result["warnings"]
        else:
            itinerary = {"session_id": session_id, "city": city, "version": 1, "days": []}
            schedule_warnings = ["未能从景点研究结果提取 POI，跳过确定性排程"]

        # 4. PlannerAgent（SimpleAgent，一次 LLM 调用）整合所有结果
        planner_query = self._build_planner_query(
            trip_meta, attraction_response, weather_response, hotel_response, session_id, itinerary
        )
        planner_response = await asyncio.to_thread(self.planner_agent.run, planner_query)

        # 5. 解析 TripPlan
        trip_plan = _parse_llm_json(planner_response) or {
            "city": city,
            "start_date": trip_meta.get("start_date", ""),
            "end_date": "",
            "days": [],
            "weather_info": [],
            "overall_suggestions": planner_response[:500] if isinstance(planner_response, str) else "",
            "budget": None,
            "session_id": session_id,
        }
        trip_plan["session_id"] = session_id

        # 用确定性排程的 days 覆盖 LLM 生成的 days（确定性优先）
        if itinerary.get("days"):
            trip_plan["days"] = itinerary["days"]

        # 确定性 budget 兜底：total_attractions 用确定性门票总和（不信任 LLM 估算的门票）
        deterministic_attractions_cost = sum(d.get("estimated_total_cost", 0) for d in itinerary.get("days", []))
        if trip_plan.get("budget"):
            trip_plan["budget"]["total_attractions"] = deterministic_attractions_cost
            # 重新计算 total（确保四项之和）
            b = trip_plan["budget"]
            b["total"] = b.get("total_attractions", 0) + b.get("total_hotels", 0) + b.get("total_meals", 0) + b.get("total_transportation", 0)
        else:
            trip_plan["budget"] = {
                "total_attractions": deterministic_attractions_cost,
                "total_hotels": 0,
                "total_meals": 0,
                "total_transportation": 0,
                "total": deterministic_attractions_cost,
            }

        return {
            "session_id": session_id,
            "trip_plan": trip_plan,
            "warnings": schedule_warnings,
            "status": "ok",
            "research": {
                "attractions": (attraction_response or "")[:800],
                "weather": (weather_response or "")[:800],
                "hotels": (hotel_response or "")[:800],
            },
        }

    def _extract_pois_from_response(self, response: str, city: str) -> list:
        """从景点研究 Agent 的响应里提取 POI 列表"""
        import json as _json
        import re as _re

        if not response:
            return []

        # 尝试从响应里找 JSON 数组
        # 先找 ```json ... ``` 代码块
        m = _re.search(r"```(?:json)?\s*(\[.*?\])\s*```", response, _re.DOTALL)
        if m:
            try:
                arr = _json.loads(m.group(1))
                if isinstance(arr, list):
                    return arr
            except _json.JSONDecodeError:
                pass

        # 找第一个 [ 到最后一个 ]
        start = response.find("[")
        end = response.rfind("]")
        if start != -1 and end != -1 and end > start:
            try:
                arr = _json.loads(response[start : end + 1])
                if isinstance(arr, list):
                    return arr
            except _json.JSONDecodeError:
                pass

        return []

    def _build_planner_query(
        self,
        trip_meta: dict,
        attraction_response: str,
        weather_response: str,
        hotel_response: str,
        session_id: str,
        itinerary: dict = None,
    ) -> str:
        # 极简 query：只传天气 + 确定性行程摘要（含每天门票），避免 query 过长导致 GLM 重试
        weather_short = (weather_response or "")[:800]

        itinerary_summary = "无确定性排程结果"
        total_attractions_cost = 0
        if itinerary and itinerary.get("days"):
            days_brief = []
            for d in itinerary["days"]:
                cost = d.get("estimated_total_cost", 0)
                total_attractions_cost += cost
                pois = [b.get("poi_id", "") for b in d.get("time_blocks", [])]
                days_brief.append(f"Day{d.get('day')}: {' → '.join(pois)} ({d.get('estimated_total_minutes', 0)}分钟, 门票{cost}元)")
            itinerary_summary = "\n".join(days_brief)

        return f"""为 {trip_meta.get('city', '')} 的 {trip_meta.get('days', 3)} 日旅行计划填充元信息。

**天气信息（来自高德 MCP）:**
{weather_short}

**确定性行程（已排好，不要改 days，门票已确定性计算）:**
{itinerary_summary}

**确定性门票总和:** {total_attractions_cost} 元（budget.total_attractions 必须用这个数，不要自己估算）

**用户需求:** 节奏={trip_meta.get('pace', 'normal')}, 预算={trip_meta.get('budget', '未指定')}, 住宿={trip_meta.get('accommodation', '经济型')}, 天数={trip_meta.get('days', 3)}

返回严格 JSON，不要解释：
{{"city":"{trip_meta.get('city', '')}","start_date":"","end_date":"","days":[],"weather_info":[{{"date":"","day_weather":"","night_weather":"","day_temp":0,"night_temp":0,"wind_direction":"","wind_power":""}}],"overall_suggestions":"基于天气和行程给3条建议","budget":{{"total_attractions":{total_attractions_cost},"total_hotels":0,"total_meals":0,"total_transportation":0,"total":0}}}}

要求：weather_info 取天气信息的前 {trip_meta.get('days', 3)} 天；budget.total_attractions 必须等于 {total_attractions_cost}（确定性门票总和）；budget.total_hotels = 每晚房价×(天数-1)；budget.total_meals = 每天餐饮×天数；budget.total = 四项之和；days 留空。"""
