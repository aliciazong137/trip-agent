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

from app.agents.prompts import (
    ATTRACTION_AGENT_PROMPT,
    WEATHER_AGENT_PROMPT,
    HOTEL_AGENT_PROMPT,
    PLANNER_AGENT_PROMPT,
)
from app.tools.persistent_mcp import PersistentMCPTool
from app.tools.state_tools import BuildItineraryTool, CheckConstraintsTool, ReviseDayTool
from app.services import session_store
from app.config import settings

import logging

logger = logging.getLogger(__name__)


def new_session_id() -> str:
    """生成合法 sessionId：sess_ + 12位hex"""
    return f"sess_{uuid.uuid4().hex[:12]}"


def _create_amap_mcp_tool() -> PersistentMCPTool:
    """创建高德地图 MCP 工具（共享实例，16 个工具自动展开）

    第二阶段：改用 PersistentMCPTool 子类，研究阶段可复用一次进程连接，
    避免每次工具调用新建 uvx 子进程（5-8 秒/次固定开销）。
    接口与 MCPTool 完全兼容，无共享连接时回落父类短连接。
    """
    tool = PersistentMCPTool(
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


def _tier_from_budget(budget) -> str:
    """预算金额 → 酒店档位（accommodation 未指定时的默认推断）"""
    if not isinstance(budget, dict):
        return ""
    amount = budget.get("amount")
    try:
        amount = float(amount) if amount is not None else None
    except (TypeError, ValueError):
        return ""
    if amount is None or amount <= 0:
        return ""
    if amount <= 1500:
        return "经济型"
    if amount <= 4000:
        return "舒适型"
    if amount <= 10000:
        return "品质型"
    return "豪华型"


def _star_to_text(star) -> str:
    """星级字段转可读文本："3" → "3星级"；空返回空（不显示）"""
    if isinstance(star, list):
        star = star[0] if star else None
    if star is None:
        return ""
    s = str(star).strip()
    if not s:
        return ""
    if s.isdigit():
        return f"{s}星级"
    return s  # 已是文本（如"四星"、"舒适型"）


def _itinerary_center(pois: list) -> Optional[tuple]:
    """行程重心：所有有坐标的 POI 的平均经纬度（供酒店周边搜索）"""
    lngs, lats = [], []
    for p in pois or []:
        loc = (p or {}).get("location") or {}
        if isinstance(loc, dict) and loc.get("longitude") and loc.get("latitude"):
            try:
                lngs.append(float(loc["longitude"]))
                lats.append(float(loc["latitude"]))
            except (TypeError, ValueError):
                continue
    if not lngs:
        return None
    return round(sum(lngs) / len(lngs), 6), round(sum(lats) / len(lats), 6)


def _parse_weather_brief(raw) -> list:
    """从高德 maps_weather 原始返回提取简报列表（供流式进度卡提前展示）

    宽容解析：截取 JSON 主体，兼容 dayweather/day_weather 两种键名。
    返回 [{"city", "date", "day_weather", "night_weather", "day_temp", "night_temp"}]；
    解析失败返回 []（前端不展示，不影响主流程）。
    """
    if not raw:
        return []
    text = raw if isinstance(raw, str) else str(raw)
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    brief = []
    for item in data[:4]:
        if not isinstance(item, dict):
            continue
        brief.append({
            "city": str(item.get("city", "") or ""),
            "date": str(item.get("forecast_date", "") or ""),
            "day_weather": str(item.get("dayweather") or item.get("day_weather") or ""),
            "night_weather": str(item.get("nightweather") or item.get("night_weather") or ""),
            "day_temp": str(item.get("daytemp") or item.get("day_temp") or ""),
            "night_temp": str(item.get("nighttemp") or item.get("night_temp") or ""),
        })
    return brief


class TripPlannerAgent:
    """
    旅行规划主协调器

    H5a：3 个研究 Agent 共享高德 MCP，用 [TOOL_CALL:...] 精确调工具
    """

    def __init__(self, max_steps: int = 20):
        self.llm = HelloAgentsLLM()
        # Agent prompt 与 MCP 长连接均是共享可变状态。连接可复用，但一次只能由
        # 一个规划事务使用，避免并发请求串入另一条路线的城市/景点数量。
        self._planning_lock = asyncio.Lock()

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
            # 兜底 prompt（含占位符的模板在 plan_trip 时按 trip_meta 实参 format 注入）
            system_prompt=ATTRACTION_AGENT_PROMPT.format(
                days=3, min_pois=4, max_pois=8, max_tool_calls=11
            ),
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

    async def plan_trip(self, trip_meta: dict, guide_text: str = "", user_context: Optional[dict] = None, on_stage=None) -> dict:
        """串行进入共享 Planner；排队只影响规划任务，不影响聊天或行程回顾。"""
        import time
        lock = getattr(self, "_planning_lock", None)
        # 支持现有单元测试以 object.__new__ 构造最小 Planner。
        if lock is None:
            lock = asyncio.Lock()
            self._planning_lock = lock
        queued_at = time.monotonic()
        if lock.locked() and on_stage:
            on_stage({"agent": "queue", "label": "前一份行程正在收尾，马上开始", "status": "start"})
        async with lock:
            waited = time.monotonic() - queued_at
            if waited >= 0.05:
                logger.info("[perf] planner queue wait=%.2fs city=%s", waited, trip_meta.get("city", ""))
                if on_stage:
                    on_stage({"agent": "queue", "label": "开始为你规划", "status": "done"})
            return await self._plan_trip(trip_meta, guide_text, user_context, on_stage)

    async def _plan_trip(self, trip_meta: dict, guide_text: str = "", user_context: Optional[dict] = None, on_stage=None) -> dict:
        """生成旅行计划

        Args:
            on_stage: 可选回调 on_stage(event: dict)，用于流式推送规划阶段进度。
                      event = {"agent": ..., "label": ..., "status": "start"/"done", ...}
                      回调可能在 worker 线程触发，实现方需自行保证线程安全。
        """
        import time
        t = {"start": time.time()}

        def _emit(agent: str, label: str, status: str, **data):
            if on_stage:
                try:
                    on_stage({"agent": agent, "label": label, "status": status, **data})
                except Exception:
                    pass

        def _mark(k): t[k] = time.time()
        def _log():
            import logging
            log = logging.getLogger("perf")
            segs = []
            prev = t["start"]
            for k in ["create_session","research","schedule","knowledge","hotel","planner","parse"]:
                if k in t:
                    segs.append(f"{k}={t[k]-prev:.1f}s")
                    prev = t[k]
            log.warning("[perf] plan_trip total=%.1fs | %s", time.time()-t["start"], " ".join(segs))

        # 1. 创建 session
        session_id = new_session_id()
        session_user_id = user_context.get("user_id") if isinstance(user_context, dict) else None
        await session_store.create_session(session_id, trip_meta, guide_text, user_id=session_user_id)
        _mark("create_session")

        city = trip_meta.get("city", "")
        days = trip_meta.get("days") or 3  # 兜底：None / 0 均视为 3 天
        preferences = trip_meta.get("preferences", "景点")
        accommodation = trip_meta.get("accommodation", "经济型")
        must_visit = trip_meta.get("must_visit", [])

        # 2. 串行调 3 个研究 Agent（第十三章原版做法，MCP server 单进程串行处理）
        # POI 数量按天数动态调整（第零期续作：原硬编码"最多 3 个"导致 2 天行程 POI 不足）
        min_pois = max(days * 2, 4)        # 至少 4 个；2 天至少 4 个候选
        max_pois = min(days * 3, 8)        # 最多 8 个，控制 MCP 调用耗时
        # 工具调用上限同时作为 SimpleAgent 的最大迭代轮数。
        # 无 must_visit 时模型可能仍对候选调用 detail，至少保留 6 轮：
        # 搜索 → 详情 → 门票 → 最终 JSON，并给模型一次重试余量。
        max_tool_calls = max(6, len(must_visit) * 2 + 1 + 2)

        # 景点 Agent 的 prompt 按本次 trip_meta 动态注入数量参数（第零期续作新增）
        self.attraction_agent.system_prompt = ATTRACTION_AGENT_PROMPT.format(
            days=days,
            min_pois=min_pois,
            max_pois=max_pois,
            max_tool_calls=max_tool_calls,
        )

        must_visit_hint = f"用户必去景点：{must_visit}" if must_visit else "用户未指定必去景点"
        attraction_query = (
            f"请搜索 {city} 的景点。{must_visit_hint}，偏好：{preferences}。"
            f"至少 {min_pois} 个、最多 {max_pois} 个 POI。\n"
            f"[TOOL_CALL:amap_maps_text_search:keywords={preferences},city={city}]"
        )
        # 2. 景点+天气并行（第八阶段 A+B：天气改直调 MCP，与景点 Agent 并行）
        #    整个研究阶段包在一个线程的一个 loop 里，MCP 进程只启动一次。
        _emit("attraction", "行程规划师", "start")
        _emit("weather", "天气大师", "start")
        attraction_response, weather_response = await asyncio.to_thread(
            self._research_phase_sync,
            attraction_query, city, max_tool_calls, _emit,
        )
        _mark("research")

        # 3. 确定性排程（Python 直接调，不经过 LLM）
        # 从景点研究结果提取 POI（LLM 返回的 JSON 数组）
        pois = self._extract_pois_from_response(attraction_response, city)

        # 第零期三作：POI 提取为空时，用 must_visit + 高德 maps_text_search 兜底
        fallback_used = False
        if not pois and must_visit:
            pois = await self._fallback_pois_from_must_visit(city, must_visit)
            if pois:
                fallback_used = True
                schedule_warnings = [
                    f"景点 Agent 提取 POI 失败，已用 must_visit 兜底生成 {len(pois)} 个基础 POI"
                ]
            else:
                schedule_warnings = ["未能从景点研究结果提取 POI，must_visit 兜底也失败"]
        elif not pois:
            schedule_warnings = ["未能从景点研究结果提取 POI，且无 must_visit 可兜底"]

        if pois:
            await session_store.save_poi_list(session_id, {"city": city, "pois": pois})

            # 3.1 预取 POI 两两移动时间（第零期新增，网络 I/O 只在此阶段）
            #     调用次数 N-1，失败或缺 location 时由排程内部经纬度兜底
            from app.services.travel_matrix import prefetch_travel_matrix
            travel_matrix = await prefetch_travel_matrix(pois, self.amap_tool)
            if travel_matrix:
                await session_store.save_travel_matrix(session_id, travel_matrix)

            # 3.2 确定性排程（读矩阵，不发网络请求）
            from app.core.scheduler import build_itinerary_from_data
            schedule_result = await build_itinerary_from_data(
                session_id, trip_meta, {"city": city, "pois": pois}, travel_matrix
            )
            await session_store.save_itinerary(session_id, schedule_result["itinerary"])
            await session_store.update_session_phase(session_id, "planned")
            itinerary = schedule_result["itinerary"]
            if not fallback_used:
                schedule_warnings = schedule_result["warnings"]
            else:
                # fallback 模式：叠加排程 warnings
                schedule_warnings = schedule_warnings + schedule_result["warnings"]
        else:
            itinerary = {"session_id": session_id, "city": city, "version": 1, "days": []}
        _mark("schedule")

        # 3.5 攻略知识检索
        #     RAG 暂时关闭（代码保留，后续对话沉淀改造时再激活）
        #     阶段 B（用户已确认路线）可通过 _skip_xhs 跳过，避免二次触发小红书。
        _emit("knowledge", "攻略学者", "start")
        # RAG 暂时关闭：knowledge_context, knowledge_sources = await self._retrieve_knowledge(city, trip_meta)
        knowledge_context, knowledge_sources = "", []
        if not trip_meta.get("_skip_xhs"):
            xhs_context, xhs_sources = await self._retrieve_xhs_notes(city, trip_meta)
            # 小红书作为唯一知识源
            if xhs_context:
                knowledge_context = "**小红书图文笔记（实时检索，本地人视角）:**\n" + xhs_context
                knowledge_sources = xhs_sources
        _emit("knowledge", "攻略学者", "done")
        _mark("knowledge")

        # 3.6 酒店搜索（排程后，Python 直调 MCP：text_search + search_detail 拿真实数据）
        #     第零期四作：替代 HotelAgent LLM 循环。text_search 只有名称/地址，
        #     rating/business_area/lowest_price 在 search_detail 里（LLM 以前只能编造）。
        #     第零期五作：优先 maps_around_search 按行程重心搜周边（真正“行程附近”），
        #     失败/空再降级全市 text_search。
        _emit("hotel", "酒店管家", "start")
        hotel_center = _itinerary_center(pois)
        hotels = await self._hotel_search_direct(city, hotel_center)
        _emit("hotel", "酒店管家", "done", hotels=hotels[:5])
        _mark("hotel")

        # 4. PlannerAgent（SimpleAgent，一次 LLM 调用）整合所有结果
        _emit("planner", "行程总设计师", "start")
        planner_query = self._build_planner_query(
            trip_meta, attraction_response, weather_response, hotels, session_id, itinerary, knowledge_context,
            user_context=user_context or {},
        )
        # PlannerAgent 纯 JSON 整合（无工具调用），同样关思考提速
        planner_kwargs = (
            {"extra_body": {"thinking": {"type": "disabled"}}}
            if settings.llm_thinking_disabled else {}
        )
        planner_response = await asyncio.to_thread(self.planner_agent.run, planner_query, **planner_kwargs)
        _emit("planner", "行程总设计师", "done")
        _mark("planner")

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

        # 确定性天气兜底（与酒店注入同思路）：总设计师漏填 weather_info 时，
        # 用 research 阶段高德原始天气填充——天气提示不该依赖 LLM 是否记得填
        if not trip_plan.get("weather_info"):
            brief = _parse_weather_brief(weather_response)
            if brief:
                trip_plan["weather_info"] = brief
                logger.info("weather_info 为空，已用高德原始天气确定性填充 %d 天", len(brief))

        # 确定性 days 优先结构（time_blocks/area_cluster/cost/minutes），
        # 但 merge LLM 填的软字段（hotel/accommodation/description/meals/transportation）
        if itinerary.get("days"):
            llm_days = trip_plan.get("days", []) or []
            det_days = itinerary["days"]
            for det_d in det_days:
                day_num = det_d.get("day")
                llm_d = next((d for d in llm_days if d.get("day") == day_num), {})
                for soft in ("hotel", "accommodation", "description", "transportation", "meals"):
                    if llm_d.get(soft):
                        det_d[soft] = llm_d[soft]
            trip_plan["days"] = det_days
            # 确定性酒店注入：LLM 只允许从真实候选里选（防漏选/编造），
            # 不在候选内或不填 → business_area/area_cluster 双向匹配确定性替换
            if hotels:
                valid_names = {h.get("name", "") for h in hotels if isinstance(h, dict)}
                for d in trip_plan["days"]:
                    llm_hotel = d.get("hotel")
                    if not (isinstance(llm_hotel, dict) and llm_hotel.get("name") in valid_names):
                        chosen = self._pick_hotel_for_day(hotels, d.get("area_cluster", []))
                        if chosen:
                            d["hotel"] = dict(chosen)

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

        # 第零期三作：status 反映确定性排程是否真正产出行程
        # - "ok"：itinerary.days 非空，行程已生成
        # - "poi_empty"：POI 提取失败（含 fallback 失败），行程为空
        plan_status = "ok" if itinerary.get("days") else "poi_empty"
        _mark("parse")
        _log()

        return {
            "session_id": session_id,
            "status": plan_status,
            "trip_plan": trip_plan,
            "warnings": schedule_warnings,
            "sources": knowledge_sources,
            "hotel_candidates": hotels[:5],
            "research": {
                "attractions": (attraction_response or "")[:800],
                "weather": (weather_response or "")[:800],
                "hotels": json.dumps(hotels, ensure_ascii=False)[:800],
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

    def _research_phase_sync(self, attraction_query: str, weather_city: str,
                             max_tool_calls: int, _emit=None):
        """景点 Agent 与天气直调 MCP 真并行。"""
        from concurrent.futures import ThreadPoolExecutor

        def _fire(agent, label, status, **data):
            if _emit:
                try:
                    _emit(agent, label, status, **data)
                except Exception:
                    pass

        think_kwargs = (
            {"extra_body": {"thinking": {"type": "disabled"}}}
            if settings.llm_thinking_disabled else {}
        )

        def run_attraction():
            self.amap_tool.open_shared()
            try:
                result = self.attraction_agent.run(
                    attraction_query,
                    max_tool_iterations=max_tool_calls,
                    **think_kwargs,
                )
                _fire("attraction", "景点规划师", "done")
                return result
            finally:
                self.amap_tool.close_shared()

        def run_weather():
            # 天气只有一次确定性 MCP 调用，不再经过 WeatherAgent/LLM。
            # 使用独立工具实例，避免和景点长连接跨线程共享 event loop。
            weather_tool = _create_amap_mcp_tool()
            try:
                weather_tool.open_shared()
                raw = weather_tool.run({
                    "tool_name": "maps_weather",
                    "arguments": {"city": weather_city},
                })
                _fire("weather", "天气大师", "done", weather=_parse_weather_brief(raw))
                return raw
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning("direct weather lookup failed: %s", exc)
                _fire("weather", "天气大师", "done", weather=[])
                return "", []
            finally:
                weather_tool.close_shared()

        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="research") as pool:
            attraction_future = pool.submit(run_attraction)
            weather_future = pool.submit(run_weather)
            return attraction_future.result(), weather_future.result()

    async def _hotel_search_phase(self, city: str, trip_meta: dict, itinerary: dict) -> str:
        """排程后搜酒店，结合行程主区域（area_cluster）搜附近酒店"""
        areas = self._extract_main_areas(itinerary)
        area_hint = areas[0] if areas else city
        accommodation = trip_meta.get("accommodation") or _tier_from_budget(trip_meta.get("budget")) or "经济型"
        hotel_query = (
            f"请搜索 {city} {area_hint} 附近的 {accommodation} 酒店（结合行程区域推荐）\n"
            f"[TOOL_CALL:amap_maps_text_search:keywords=酒店,city={city}]"
        )
        think_kwargs = (
            {"extra_body": {"thinking": {"type": "disabled"}}}
            if settings.llm_thinking_disabled else {}
        )
        def _run():
            self.amap_tool.open_shared()
            try:
                return self.hotel_agent.run(hotel_query, max_tool_iterations=5, **think_kwargs)
            finally:
                self.amap_tool.close_shared()
        try:
            return await asyncio.to_thread(_run)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("hotel search phase failed: %s", e)
            return ""

    async def _hotel_search_direct(self, city: str, center: Optional[tuple] = None) -> list:
        """Python 直调高德查酒店（第零期四作：替代 HotelAgent LLM 循环）

        数据链（全部真实，LLM 不参与）：
          1. 优先 maps_around_search(酒店, 行程重心, 5km) → 周边 20 家候选
             （text_search 是全市随机结果，区域匹配无意义）
          2. 每个候选调 maps_search_detail(id) → rating/business_area/
             lowest_price/star/location（搜索接口不含这些字段）

        返回结构化酒店列表（字段缺省为空，不编造）；任一步失败返回部分结果。
        """
        def _parse_price(val):
            """lowest_price 可能是 [] / "" / "350" / 350 → (int 价格, 展示文本)"""
            if isinstance(val, list):
                val = val[0] if val else None
            if val is None or val == "":
                return 0, ""
            try:
                num = int(float(val))
                return num, f"{num}元"
            except (TypeError, ValueError):
                return 0, str(val)

        def _parse_loc(val):
            if isinstance(val, str) and "," in val:
                parts = val.split(",")
                if len(parts) == 2:
                    try:
                        return {"longitude": float(parts[0]), "latitude": float(parts[1])}
                    except (TypeError, ValueError):
                        pass
            return None

        def _search_candidates():
            """周边搜索优先，失败/空降级全市搜索"""
            if center:
                try:
                    raw = self.amap_tool.run({
                        "tool_name": "maps_around_search",
                        "arguments": {
                            "keywords": "酒店",
                            "location": f"{center[0]},{center[1]}",
                            "radius": "5000",
                        },
                    })
                    text = raw if isinstance(raw, str) else str(raw)
                    s, e = text.find("{"), text.rfind("}")
                    if s != -1:
                        data = json.loads(text[s:e + 1])
                        pois = data.get("pois") if isinstance(data, dict) else None
                        if isinstance(pois, list) and pois:
                            return pois
                except Exception as exc:
                    import logging
                    logging.getLogger(__name__).warning(
                        "酒店周边搜索失败，降级全市搜索: %s", exc)
            raw = self.amap_tool.run({
                "tool_name": "maps_text_search",
                "arguments": {"keywords": "酒店", "city": city},
            })
            text = raw if isinstance(raw, str) else str(raw)
            s, e = text.find("{"), text.rfind("}")
            if s == -1:
                return []
            data = json.loads(text[s:e + 1])
            pois = data.get("pois") if isinstance(data, dict) else None
            return pois if isinstance(pois, list) else []

        def _sync():
            try:
                candidates = _search_candidates()
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning("酒店候选搜索失败: %s", exc)
                return []

            hotels = []
            for poi in candidates[:6]:
                if not isinstance(poi, dict) or not poi.get("name"):
                    continue
                h = {
                    "name": str(poi.get("name", "")),
                    "address": str(poi.get("address", "") or ""),
                    "area": "",           # business_area 由详情补
                    "rating": "",
                    "price_range": "",
                    "estimated_cost": 0,
                    "type": "",
                    "location": None,
                    "poi_id": str(poi.get("id", "") or ""),
                    "amap_url": f"https://www.amap.com/search?query={poi.get('name', '')}",
                }
                # 详情：拿真实 rating / business_area / lowest_price / star / location
                try:
                    raw = self.amap_tool.run({
                        "tool_name": "maps_search_detail",
                        "arguments": {"id": poi.get("id", "")},
                    })
                    text = raw if isinstance(raw, str) else str(raw)
                    s, e = text.find("{"), text.rfind("}")
                    detail = {}
                    if s != -1:
                        try:
                            d = json.loads(text[s:e + 1])
                            if isinstance(d, dict):
                                # 详情可能直接返回 poi，也可能包在 pois 里
                                if isinstance(d.get("pois"), list) and d["pois"]:
                                    detail = d["pois"][0] if isinstance(d["pois"][0], dict) else {}
                                else:
                                    detail = d
                        except json.JSONDecodeError:
                            pass
                    if detail:
                        h["area"] = str(detail.get("business_area", "") or "") or h["address"][:20]
                        h["rating"] = str(detail.get("rating", "") or "")
                        cost, price_text = _parse_price(detail.get("lowest_price"))
                        h["estimated_cost"] = cost
                        h["price_range"] = price_text
                        star = detail.get("star")
                        h["type"] = _star_to_text(star)
                        h["location"] = _parse_loc(detail.get("location"))
                except Exception as exc:
                    import logging
                    logging.getLogger(__name__).warning(
                        "酒店详情查询失败（%s），仅用搜索字段: %s", poi.get("name", ""), exc)
                hotels.append(h)
            return hotels

        def _run():
            self.amap_tool.open_shared()
            try:
                return _sync()
            finally:
                self.amap_tool.close_shared()

        try:
            return await asyncio.to_thread(_run)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("hotel direct search failed: %s", e)
            return []

    def _extract_main_areas(self, itinerary: dict) -> list:
        """从行程 area_cluster 提取主区域（去重保序，前3个）"""
        areas = []
        for d in (itinerary or {}).get("days", []):
            areas.extend(d.get("area_cluster", []))
        seen = set()
        uniq = [a for a in areas if not (a in seen or seen.add(a))]
        return uniq[:3]

    def _extract_hotels_from_response(self, response: str) -> list:
        """从酒店 Agent 响应里提取酒店列表（JSON 数组）

        已被 _hotel_search_direct 替代，保留兼容；解析失败不再静默，打 warning 可观测。
        """
        import json as _json
        import re as _re
        import logging
        if not response:
            return []
        m = _re.search(r"```(?:json)?\s*(\[.*?\])\s*```", response, _re.DOTALL)
        if m:
            try:
                arr = _json.loads(m.group(1))
                if isinstance(arr, list):
                    return arr
            except _json.JSONDecodeError:
                pass
        start = response.find("[")
        end = response.rfind("]")
        if start != -1 and end != -1 and end > start:
            try:
                arr = _json.loads(response[start:end+1])
                if isinstance(arr, list):
                    return arr
            except _json.JSONDecodeError:
                pass
        logging.getLogger(__name__).warning(
            "酒店响应解析失败（%d 字符），返回空列表", len(response))
        return []

    def _pick_hotel_for_day(self, hotels: list, areas: list) -> Optional[dict]:
        """按当天 area_cluster 匹配酒店，无匹配取第一个

        双向包含匹配：行程区域（POI address 片段）与酒店 business_area
        是不同粒度（如「景山前街4号」vs「王府井」），单向匹配会漏，双向更准。
        """
        if not hotels:
            return None
        for area in areas:
            if not area:
                continue
            for h in hotels:
                if not isinstance(h, dict):
                    continue
                for key in ("area", "business_area", "address", "name"):
                    hay = str(h.get(key) or "")
                    if hay and (area in hay or hay in area):
                        return h
        return hotels[0] if hotels else None

    async def _fallback_pois_from_must_visit(self, city: str, must_visit: list) -> list:
        """
        第零期三作：景点 Agent 提取失败时，用 must_visit 直接调高德 maps_text_search 兜底

        策略：
          - 对每个 must_visit 项调 maps_text_search，取第 1 个结果
          - 用搜到的 id/name/location 构造最小 POI
          - estimated_duration_minutes 默认 240（4 小时），estimated_cost=0（未知不阻塞排程）
          - priority="must"
          - 调用 maps_search_detail 补 location（text_search 可能不返回坐标）

        失败的 must_visit 项跳过，不影响其他项。
        全部失败时返回 []，由调用方决定 status。
        """
        if not must_visit or not city:
            return []

        pois = []
        seen_ids = set()
        for spot in must_visit:
            if not isinstance(spot, str) or not spot.strip():
                continue
            try:
                raw = await asyncio.to_thread(
                    self.amap_tool.run,
                    {
                        "tool_name": "maps_text_search",
                        "arguments": {"keywords": spot.strip(), "city": city},
                    },
                )
            except Exception:
                continue

            poi = self._build_fallback_poi(raw, city, spot.strip())
            if poi and poi["id"] and poi["id"] not in seen_ids:
                seen_ids.add(poi["id"])
                pois.append(poi)

        return pois

    def _build_fallback_poi(self, raw: str, city: str, spot: str) -> Optional[dict]:
        """从 maps_text_search 返回值构造最小 POI dict"""
        import json as _json
        import re as _re

        # 解析高德返回（和 _parse_distance_results 同款解析逻辑）
        data = raw
        if isinstance(raw, str):
            start = raw.find("{")
            end = raw.rfind("}")
            if start == -1 or end == -1 or end <= start:
                return None
            try:
                data = _json.loads(raw[start : end + 1])
            except _json.JSONDecodeError:
                return None

        if not isinstance(data, dict):
            return None

        # 高德 maps_text_search 返回 {"pois": [{"id","name","location","address",...}]}
        pois_list = data.get("pois") or data.get("results") or []
        if not isinstance(pois_list, list) or not pois_list:
            return None

        first = pois_list[0]
        if not isinstance(first, dict):
            return None

        poi_id = first.get("id") or first.get("poi_id")
        name = first.get("name") or spot
        # location 格式 "经度,纬度"
        loc_raw = first.get("location") or first.get("longitude") or ""
        location = {}
        if isinstance(loc_raw, str) and "," in loc_raw:
            parts = loc_raw.split(",")
            if len(parts) == 2:
                try:
                    location = {"longitude": float(parts[0]), "latitude": float(parts[1])}
                except (ValueError, TypeError):
                    pass
        elif isinstance(first.get("location"), dict):
            loc = first["location"]
            if loc.get("longitude") and loc.get("latitude"):
                location = {"longitude": float(loc["longitude"]), "latitude": float(loc["latitude"])}

        if not poi_id:
            return None

        # 兜底 POI：最小可用字段，duration 默认 240 分钟，cost 0（未知，不阻塞排程）
        return {
            "id": str(poi_id),
            "name": name,
            "category": "attraction",
            "area": (first.get("address") or "")[:50] or city,
            "location": location,
            "priority": "must",
            "estimated_duration_minutes": 240,
            "estimated_cost": 0,
            "opening_hours": first.get("opentime2") or "",
            "rating": float(first.get("rating") or 0) or 0,
            "level": "",
            "description": f"必去景点（兜底）：{spot}",
        }

    async def _retrieve_knowledge(self, city: str, trip_meta: dict) -> tuple[str, list[dict]]:
        """
        第三期：RAG 检索攻略知识，用 city + preferences 个性化检索

        返回拼接的攻略片段文本；知识库为空或检索失败返回空串（不阻断主流程）

        短期优化（2026-09-13）：条件触发，避免"白费时间"
          - 库里没有该城市 → 跳过（retriever 已预检 count_by_city）
          - 用户只说基础需求（"想去北京"，无 preferences/must_visit）→ 跳过
            （LLM 内置知识够用，RAG 此时只增噪声不增值）
          - 用户问了细节（preferences 非空 或 must_visit 非空）→ 检索
        """
        try:
            from app.rag import retriever
            preferences = trip_meta.get("preferences", "") or ""
            must_visit = trip_meta.get("must_visit", []) or []
            # 条件触发：基础规划不查 RAG（LLM 内置知识够用，避免噪声+延迟）
            if not preferences and not must_visit:
                logger.info("RAG 跳过：用户未提供 preferences/must_visit，用 LLM 内置知识")
                return "", []
            rag_query = f"{city} {preferences}".strip()
            chunks = await retriever.search(rag_query, top_k=3, city=city)
            if not chunks:
                return "", []
            parts = []
            sources = []
            for c in chunks:
                title = c.title or c.source or "攻略"
                parts.append(f"【{title}】\n{c.content[:400]}")
                sources.append({"type": "destination_rag", "id": c.chunk_id, "title": title, "content": c.content[:400], "score": round(c.score, 3), "reason": "用于补充目的地攻略知识", "source": c.source, "city": c.city})
            return "\n\n".join(parts), sources
        except Exception as e:
            logger.warning("RAG retrieve failed: %s", e)
            return "", []

    def _parse_guide_routes(self, text: str) -> tuple[str, list[dict]]:
        """从攻略 Markdown 中解析路线候选，供前端选择卡使用。
        放宽匹配：支持 **加粗**、带变体选择符的 emoji、---  分隔线。"""
        options: list[dict] = []
        # 匹配 🏛/🚶/🏔 开头的段落（支持 emoji 后可能有 ** 加粗、可能带变体选择符）
        section_pattern = re.compile(r"(?P<emoji>[🏛🏛️🚶🚶️🏔🏔️])\s*\**\s*(?P<title>[^\n*]+?)\**\s*\n(?P<body>.*?)(?=\n[🏛🏛️🚶🚶️🏔🏔️🍜🍜️💡💡️]|\Z)", re.S)
        for idx, match in enumerate(section_pattern.finditer(text), 1):
            raw_title = match.group("title").strip().rstrip("*").strip()
            body = match.group("body").strip()
            # 路线：支持 **路线**：xxx 或 路线：xxx
            route_match = re.search(r"\**\s*路线\s*\**\s*[:：]\s*(.+)", body)
            suitable_match = re.search(r"\**\s*适合人群\s*\**\s*[:：]\s*(.+)", body)
            route_text = route_match.group(1).strip().rstrip("*").strip() if route_match else ""
            # 去掉路线文本里的 ** 等 Markdown 符号
            route_text = re.sub(r"\*+", "", route_text).strip()
            poi_hints = [p.strip() for p in re.split(r"[→➡>、,，]", route_text) if p.strip() and p.strip() != "→"]
            options.append({
                "id": f"route_{idx}",
                "title": raw_title,
                "route_text": route_text,
                "suitable_for": (suitable_match.group(1).strip().rstrip("*").strip() if suitable_match else ""),
                "poi_hints": poi_hints[:8],
                "food_hints": [],
                "tips": [],
            })
        return text, options

    async def generate_guide_routes(
        self,
        trip_meta: dict,
        guide_style: str = "full",
        inspiration_note: Optional[dict] = None,
        on_stage=None,
        on_delta=None,
    ) -> dict:
        """阶段 A：基于攻略素材生成路线候选；只区分展示文案的详细度。
        前提：trip_meta 已经过确定性校验，city/days 齐全，可选偏好和预算按已知信息使用。
        """
        import time

        started_at = time.monotonic()
        city = trip_meta.get("city") or ""
        if not city:
            return {"status": "failed", "guide_markdown": "", "route_options": [], "sources": [], "error_message": "缺少城市"}

        def _emit(agent: str, label: str, status: str, **data):
            if on_stage:
                try:
                    on_stage({"agent": agent, "label": label, "status": status, **data})
                except Exception:
                    pass

        _emit("intent", "小渡理解需求", "start")
        _emit("intent", "小渡理解需求", "done")
        _emit("knowledge", "攻略学者", "start")
        knowledge_context, sources = await self._retrieve_xhs_notes(city, trip_meta, inspiration_note=inspiration_note)
        knowledge_seconds = time.monotonic() - started_at
        _emit("knowledge", "攻略学者", "done")
        if not knowledge_context:
            return {"status": "failed", "guide_markdown": "", "route_options": [], "sources": sources, "error_message": "未检索到可用攻略素材"}

        _emit("guide", "路线策划师", "start")
        guide_prompt = self._build_guide_routes_prompt(trip_meta, knowledge_context, guide_style)
        guide_text = await self._call_guide_llm_stream(guide_prompt, on_delta=on_delta)
        llm_seconds = time.monotonic() - started_at - knowledge_seconds
        guide_markdown, route_options = self._parse_guide_routes(guide_text)
        _emit("guide", "路线策划师", "done")
        logger.info(
            "[perf] guide_routes city=%s total=%.1fs xhs=%.1fs llm=%.1fs chars=%d options=%d",
            city, time.monotonic() - started_at, knowledge_seconds, llm_seconds,
            len(guide_text), len(route_options),
        )
        return {
            "status": "ok",
            "guide_markdown": guide_markdown,
            "route_options": route_options,
            "sources": sources,
        }

    def _build_guide_routes_prompt(self, trip_meta: dict, knowledge_context: str, guide_style: str = "full") -> str:
        """只控制攻略展示层：完整对话攻略或热门灵感的快速选线。"""
        city = trip_meta.get("city", "")
        days = trip_meta.get("days", 1)
        preferences = trip_meta.get("preferences", "") or ""
        if guide_style == "inspiration":
            return f"""你是小渡旅行路线策划师。请基于下面按类型分组的小红书攻略素材，输出一段可直接展示给用户的 Markdown 攻略，并提供清晰的路线候选。

用户需求：{city}{days}日游，偏好：{preferences or '未指定'}。

素材：
{knowledge_context}

输出要求：
用户此刻只是在比较路线方向，不需要完整攻略。结合真实经验，为 {city}{days}日游给出三个取向明确、互不重复的候选路线。

🏛 经典古迹线
路线：A → B → C
适合人群：...
玩法说明：用一句话概括体验重点与行程节奏。
避坑提醒：只写一个最重要的提醒。

🚶 胡同人文线
路线：A → B → C
适合人群：...
玩法说明：用一句话概括体验重点与行程节奏。
避坑提醒：只写一个最重要的提醒。

🏔 小众补充线
路线：A → B → C
适合人群：...
玩法说明：用一句话概括体验重点与行程节奏。
避坑提醒：只写一个最重要的提醒。

规则：
- 每条路线总字数不超过 90 个汉字；不要添加开场白、总结、吃什么或实用提醒。
- 如素材包含「用户选中的热门笔记」，它是本次路线的首要依据；可用补充素材校验和丰富细节，但不要偏离这篇笔记的地点与主题。
- 不要出现「笔记1/笔记2/小红书」等引用痕迹。
- 不要声称参考了固定数量的全网内容。
- 只输出攻略 Markdown，不要输出 JSON。
- 不要使用任何 Markdown 符号（**、#、*、`、--- 等），只用纯文本 + emoji + → 箭头。
- 每条路线必须严格按下面格式输出，emoji 后直接写标题（不加 **），标签后直接写冒号（不加 **）。
- 必须输出全部 3 条路线（🏛🚶🏔），不要只输出 1 条或 2 条。
- 不要输出 --- 分隔线。"""

        return f"""你是小渡旅行攻略编辑。请基于下面按类型分组的真实旅行素材，为用户写一份可直接阅读的完整攻略，同时保留三条可选择的路线方向。

用户需求：{city}{days}日游，偏好：{preferences or '未指定'}。

素材：
{knowledge_context}

输出结构：
开头先用小渡自然、温暖的口吻回应用户 1-2 句：可以轻轻表达对目的地的期待，并结合已知的天数或偏好给一个积极判断，例如「北京很适合慢慢逛，四天能把中轴线和胡同的节奏都留出来」。不要使用夸张、模板化的赞美，不要虚构季节、人数或没有提供的事实。
接着用 2-3 句给出与已知季节、天数和用户偏好相关的开场判断；不要声称参考了固定数量的经验。

🏛 经典古迹线
路线：A → B → C
适合人群：...
玩法说明：按上午/中午/下午说明合理的游玩节奏、关键看点和顺路关系。
避坑提醒：预约、门票、入口、排队或闭馆中只写最重要的 1-2 条。

🚶 胡同人文线
路线：A → B → C
适合人群：...
玩法说明：说明顺路街区、适合停留的咖啡/文创/公园，以及步行节奏。
避坑提醒：说明游客主街、步行强度或时间取舍。

🏔 小众补充线
路线：A → B → C
适合人群：...
玩法说明：突出安静街区、博物馆、书店或与偏好匹配的替代体验。
避坑提醒：说明闭馆、交通距离或可舍弃项。

🍜 吃什么
按上述路线附近的区域整理 3-5 条餐饮建议。素材有明确店名时才写店名；否则写本地品类与适合安排的区域，不要编造餐厅。

💡 实用提醒
写 3-4 条真正有帮助的提醒，只选与本次行程有关的天气/穿搭、预约、交通、步行强度或节假日信息。

规则：
- 必须综合至少 2 篇不同素材的信息，不能被单篇内容主导。
- 单点素材只能贡献预约、避坑或入口细节，不能占满整体输出。
- 优先突出与「{preferences or '用户偏好'}」相关的内容。
- 不要出现「笔记1/笔记2/小红书」等引用痕迹，也不要写固定数量的“真实经验”。
- 只输出纯文本攻略，不要输出 JSON、Markdown 代码块或 --- 分隔线。
- 必须输出全部 3 条路线（🏛🚶🏔）；🍜 吃什么和 💡 实用提醒不可省略。"""

    async def _call_guide_llm_stream(self, prompt: str, on_delta=None) -> str:
        """调用 GLM/OpenAI-compatible Chat Completions；支持 stream，失败降级为 SimpleAgent 一次性。"""
        import httpx
        from app.config import settings
        messages = [
            {"role": "system", "content": "你是专业旅行攻略编辑，擅长把真实游记素材整理成可选择路线。"},
            {"role": "user", "content": prompt},
        ]
        if settings.llm_api_key:
            try:
                full = ""
                payload = {
                    "model": settings.llm_model_id,
                    "messages": messages,
                    "stream": True,
                }
                if settings.llm_thinking_disabled:
                    payload["extra_body"] = {"thinking": {"type": "disabled"}}
                url = settings.llm_base_url.rstrip("/") + "/chat/completions"
                async with httpx.AsyncClient(timeout=90) as client:
                    async with client.stream("POST", url, headers={"Authorization": f"Bearer {settings.llm_api_key}", "Content-Type": "application/json"}, json=payload) as resp:
                        resp.raise_for_status()
                        async for line in resp.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if not data or data == "[DONE]":
                                continue
                            try:
                                obj = json.loads(data)
                                delta = obj.get("choices", [{}])[0].get("delta", {}).get("content") or ""
                            except Exception:
                                delta = ""
                            if delta:
                                full += delta
                                if on_delta:
                                    on_delta(delta)
                if full.strip():
                    return full.strip()
            except Exception as e:
                logger.warning("guide stream LLM failed, fallback: %s", e)
        agent = SimpleAgent(name="GuideRoutesAgent", llm=self.llm, system_prompt="你是专业旅行攻略编辑。")
        text = await asyncio.to_thread(agent.run, prompt)
        if on_delta and text:
            on_delta(text)
        return text or ""

    def _classify_xhs_note(self, title: str, desc: str) -> str:
        """小红书笔记轻量分桶：先规则分类，避免额外 LLM 调用。"""
        text = f"{title} {desc}".lower()
        food_keywords = ["美食", "小吃", "吃什么", "烤鸭", "炸酱面", "卤煮", "炒肝", "咖啡", "甜品", "门钉肉饼", "包子"]
        route_keywords = ["多条路线", "路线合集", "经典路线", "小众路线", "怎么玩", "一日游路线", "路线推荐", "几条路线", "玩法", "封神citywalk"]
        culture_keywords = ["胡同", "citywalk", "白塔寺", "鼓楼", "什刹海", "烟袋斜街", "书店", "老北京", "烟火气", "博物馆", "古迹", "中轴线", "前门", "杨梅竹斜街"]
        spot_keywords = ["预约", "门票", "安检", "闭馆", "保姆级", "入馆", "放票", "展览", "讲解", "禁带"]
        if any(k.lower() in text for k in food_keywords):
            return "food"
        if any(k.lower() in text for k in route_keywords):
            return "route_collection"
        # 有明显路线箭头/路线结构时，优先视作路线集合或人文路线
        arrow_count = text.count("→") + text.count("➡") + text.count("-")
        if arrow_count >= 4 and any(k.lower() in text for k in ["路线", "citywalk", "一日游", "出发"]):
            return "route_collection"
        if any(k.lower() in text for k in culture_keywords):
            return "culture_route"
        if any(k.lower() in text for k in spot_keywords):
            return "spot_tip"
        return "general"

    def _select_xhs_notes(self, candidates: list[dict], limit: int = 5) -> list[dict]:
        """按分桶选择最终进入 LLM 的笔记，避免单点攻略占据全部上下文。"""
        if not candidates:
            return []
        buckets: dict[str, list[dict]] = {}
        for item in candidates:
            buckets.setdefault(item.get("category", "general"), []).append(item)
        selected: list[dict] = []
        used: set[str] = set()

        def take(category: str, n: int):
            for item in buckets.get(category, [])[:n]:
                note_id = item.get("note_id") or item.get("title", "")
                if note_id in used:
                    continue
                selected.append(item)
                used.add(note_id)
                if len([x for x in selected if x.get("category") == category]) >= n:
                    break

        take("route_collection", 2)
        take("culture_route", 1)
        take("food", 1)
        take("spot_tip", 1)
        for item in candidates:
            if len(selected) >= limit:
                break
            note_id = item.get("note_id") or item.get("title", "")
            if note_id not in used:
                selected.append(item)
                used.add(note_id)
        return selected[:limit]

    async def _retrieve_xhs_notes(self, city: str, trip_meta: dict, inspiration_note: Optional[dict] = None) -> tuple[str, list[dict]]:
        """
        小红书图文笔记检索（Spider_XHS）：多 query 搜路线集合/偏好路线/美食补充，分桶后给 LLM 总结攻略。
        设计：
          - 失败静默返回空（Cookie 过期/签名失败/网络异常 → 规划照常进行）
          - 只取图文（note_type=2），按点赞排序（sort_type_choice=2），半年内（note_time=3）
          - 多 query + note_id 去重 + 规则分桶，避免单点攻略占满上下文
        """
        primary_content = ""
        primary_sources: list[dict] = []
        if inspiration_note:
            title = str(inspiration_note.get("title") or "用户选中的旅行灵感").strip()
            summary = str(inspiration_note.get("summary") or "").strip()
            if title or summary:
                primary_content = f"【用户选中的热门笔记（本次路线优先参考）】\n- {title}\n{summary}".strip()
                primary_sources.append({
                    "type": "xhs_notes",
                    "id": str(inspiration_note.get("id") or f"selected_{abs(hash(title + summary))}"),
                    "title": title,
                    "content": summary,
                    "reason": "用户主动选中的热门笔记，作为本次路线的首要灵感来源",
                    "source": "xiaohongshu",
                    "city": city,
                    "category": "selected_inspiration",
                })
        try:
            from app.tools.xhs_note_search_tool import XhsNoteSearchTool
            preferences = trip_meta.get("preferences", "") or ""
            days = trip_meta.get("days") or ""
            day_part = f"{days}日游" if days else "旅游"
            base = f"{city}{day_part}"
            # 方案选择阶段只需要足够的路线素材。过去三轮串行检索会启动三次独立爬虫，
            # 再为每条结果请求正文，明显拉长首个方案出现的时间；改为一次定向检索。
            queries = [" ".join([p for p in [base, preferences, "攻略 路线 城市漫游 美食"] if p]).strip()]
            tool = XhsNoteSearchTool()
            seen: set[str] = set()
            candidates: list[dict] = []
            for query in queries:
                result = await tool._fetch(query, 4)
                if not result.get("success"):
                    logger.warning("XHS 搜索失败: query=%s msg=%s", query, result.get("msg", ""))
                    continue
                for n in result.get("notes", []) or []:
                    title = n.get("title", "")
                    desc = n.get("desc", "")
                    note_id = n.get("note_id") or f"xhs_{abs(hash(title + desc))}"
                    if note_id in seen or not (title or desc):
                        continue
                    seen.add(note_id)
                    item = dict(n)
                    item["note_id"] = note_id
                    item["category"] = self._classify_xhs_note(title, desc)
                    candidates.append(item)
            notes = self._select_xhs_notes(candidates)
            if not notes:
                return primary_content, primary_sources

            grouped: dict[str, list[dict]] = {}
            for n in notes:
                grouped.setdefault(n.get("category", "general"), []).append(n)
            category_titles = {
                "route_collection": "路线集合",
                "culture_route": "人文/Citywalk",
                "food": "美食补充",
                "spot_tip": "预约/避坑",
                "general": "其他参考",
            }
            sections = []
            for cat in ("route_collection", "culture_route", "food", "spot_tip", "general"):
                bucket = grouped.get(cat) or []
                if not bucket:
                    continue
                lines = [f"【{category_titles.get(cat, cat)}】"]
                for i, n in enumerate(bucket, 1):
                    title = n.get("title", "")
                    desc = n.get("desc", "")
                    lines.append(f"- {title}\n{desc}")
                sections.append("\n".join(lines))
            content = "\n\n".join([part for part in [primary_content, "\n\n".join(sections)] if part])

            sources = []
            for i, n in enumerate(notes, 1):
                title = n.get("title", "") or f"小红书笔记{i}"
                note_id = n.get("note_id") or f"xhs_{city}_{i}"
                desc = n.get("desc", "")
                category = n.get("category", "general")
                sources.append({
                    "type": "xhs_notes",
                    "id": note_id,
                    "title": title,
                    "content": desc,
                    "reason": f"小红书{category_titles.get(category, category)}素材，补充路线/美食/避坑",
                    "source": "xiaohongshu",
                    "city": city,
                    "category": category,
                })
            return content, primary_sources + sources
        except Exception as e:
            logger.warning("XHS retrieve failed: %s", e)
            # 热门卡片已有一份成功抓取并缓存的笔记内容；即使补充检索失败，也能生成路线。
            return primary_content, primary_sources

    def _build_planner_query(
        self,
        trip_meta: dict,
        attraction_response: str,
        weather_response: str,
        hotels: Optional[list],
        session_id: str,
        itinerary: dict = None,
        knowledge_context: str = "",
        user_context: dict = None,
    ) -> str:
        # 第八阶段 D：只给 Planner 传结构化摘要，避免完整 MCP 响应撑大上下文。
        # attraction_response 仅用于兼容调用签名，不再注入原始文本。
        del attraction_response
        weather_short = (weather_response or "")[:500]
        knowledge_section = (
            "**小红书攻略素材（已按类型分组，半年内高赞图文；仅作攻略素材，不可照抄）:**\n"
            f"{knowledge_context}\n\n"
            "请把上述素材整理成一段可直接展示给用户的 Markdown 风格攻略总结，写入 overall_suggestions。\n"
            "overall_suggestions 不要写成普通 3 条建议，而要使用下面结构：\n\n"
            f"结合小红书上的真实经验，{trip_meta.get('city', '')}{trip_meta.get('days', 3)}日游可以按兴趣分成几类，按你的偏好选一条就够了。\n\n"
            "🏛 经典古迹线\n"
            "路线：用「A → B → C」形式写出。\n"
            "适合人群：第一次来、想看皇家建筑/历史文化/中轴线等。\n"
            "玩法说明：写出上午/中午/下午大致节奏，包含 1-2 个关键看点。\n"
            "避坑提醒：预约、门票、入口、排队、闭馆等，只保留最重要的。\n\n"
            "🚶 胡同人文线\n"
            "路线：用「A → B → C」形式写出。\n"
            "适合人群：喜欢胡同、烟火气、citywalk、拍照、慢节奏。\n"
            "玩法说明：写出顺路街区、可停留的小店/文创/咖啡/公园。\n"
            "避坑提醒：避开游客主街、不要贪多、注意步行距离。\n\n"
            "🏔 小众补充线\n"
            "路线：如果素材中有小众路线则写小众路线；如果没有，就基于确定性行程和素材给一个轻量替代方案。\n"
            "适合人群：不想太挤、喜欢博物馆/书店/安静街区。\n"
            "玩法说明：突出人少、历史底蕴、出片点。\n"
            "避坑提醒：闭馆、预约、交通距离、时间不够时可放弃。\n\n"
            "🍜 吃什么\n"
            "按区域整理，不要泛泛写当地美食；如果来源没有明确餐厅，可以写品类，不要编造不存在的店。\n\n"
            "💡 实用提醒\n"
            "写天气/穿搭、步行强度/交通、预约/闭馆、一日游取舍原则。\n\n"
            "融合规则：\n"
            "- 必须综合至少 2 篇不同笔记的信息，不能被单篇笔记主导；\n"
            "- 单点攻略只能贡献预约/避坑/入口细节，不能占满整体输出；\n"
            f"- 如果用户偏好是「{trip_meta.get('preferences', '')}」，优先突出古迹、博物馆、胡同、中轴线等相关内容；\n"
            "- 小红书内容只能作为补充，不要覆盖确定性行程；\n"
            "- 不要出现「笔记1/笔记2/小红书」等引用痕迹；\n"
            "- 不要声称参考了固定数量的全网内容，除非输入明确给出数量。"
            if knowledge_context else ""
        )

        # 酒店摘要：真实数据（_hotel_search_direct 从 maps_search_detail 取），
        # rating/estimated_cost 缺省为空，不编造。
        hotel_summary = []
        for h in (hotels or [])[:6]:
            if not isinstance(h, dict):
                continue
            hotel_summary.append({
                "name": h.get("name", ""),
                "area": h.get("area", ""),
                "rating": h.get("rating", ""),
                "price_range": h.get("price_range", ""),
                "estimated_cost": h.get("estimated_cost", 0),
                "type": h.get("type", ""),
            })
        hotel_section = (
            f"**酒店摘要（高德真实数据，rating/price 可能为空，为空时不要编造）:**\n{json.dumps(hotel_summary, ensure_ascii=False)}"
            if hotel_summary else ""
        )

        user_context = user_context or {}
        profile = user_context.get("profile") or {}
        memories = user_context.get("memories") or []
        user_context_section = (
            "**用户历史偏好参考（仅用于个性化，不覆盖本次明确需求）:**\n"
            + json.dumps(profile, ensure_ascii=False)
            + ("\n历史记忆摘要：\n" + "\n".join(m.get("content", "")[:300] for m in memories[:4]) if memories else "")
            if profile or memories else ""
        )

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

{knowledge_section}

{user_context_section}

{hotel_section}

**天气信息（来自高德 MCP）:**
{weather_short}

**确定性行程（已排好，不要改 days，门票已确定性计算）:**
{itinerary_summary}

**确定性门票总和:** {total_attractions_cost} 元（budget.total_attractions 必须用这个数，不要自己估算）

**用户需求:** 节奏={trip_meta.get('pace', 'normal')}, 预算={trip_meta.get('budget', '未指定')}, 住宿={trip_meta.get('accommodation', '经济型')}, 天数={trip_meta.get('days', 3)}

返回严格 JSON，不要解释：
{{"city":"{trip_meta.get('city', '')}","start_date":"","end_date":"","days":[],"weather_info":[{{"date":"","day_weather":"","night_weather":"","day_temp":0,"night_temp":0,"wind_direction":"","wind_power":""}}],"overall_suggestions":"基于天气和行程给3条建议","budget":{{"total_attractions":{total_attractions_cost},"total_hotels":0,"total_meals":0,"total_transportation":0,"total":0}}}}

要求：weather_info 取天气信息的前 {trip_meta.get('days', 3)} 天；budget.total_attractions 必须等于 {total_attractions_cost}（确定性门票总和）；budget.total_hotels = 每晚房价×(天数-1)；budget.total_meals = 每天餐饮×天数；budget.total = 四项之和；days 留空。"""
