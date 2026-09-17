"""
MemoryCompressor：LLM 压缩 + template fallback

用于把一次规划对话压缩成可检索的 MemoryCompressionResult。
默认 MEMORY_COMPRESS_MODE=llm，失败/超时后用 template 兜底。
"""
import asyncio
import json
import logging
import re
from typing import Any, Dict, Optional

from hello_agents import HelloAgentsLLM, SimpleAgent

from app.config import settings
from app.memory.base import MemoryCompressionResult

logger = logging.getLogger(__name__)

PROMPT = """你是旅行记忆压缩器。请把一次旅行规划对话压缩成可检索的用户记忆。

严格规则：
1. 只能基于输入内容总结，不要添加输入中没有的信息
2. 不要把攻略知识当成用户偏好
3. 不要把系统推荐当成用户明确偏好，除非用户接受或明确表达
4. 稳定画像只记录输入中有明确证据的内容：J人/计划型 → planning_style=J；P人/随性 → planning_style=P；“从北京出发/常住北京” → home_city=北京；明确交通/住宿偏好才记录对应字段
5. 本次目的地、天数、日期是旅行事实，放 facts，不要放入 profile；不要把“去过/计划去某城市”当成 home_city
6. unknowns 要记录用户没提供的重要字段
7. 输出严格 JSON，不要解释
8. 如果不确定，宁可放 unknowns，不要猜

输出 JSON schema：
{
  "summary": "1-3句话摘要",
  "facts": {"city": "南京", "days": 2, "travelers": {...}, "budget": null, "pace": "normal"},
  "profile": {"planning_style": "J", "home_city": "北京", "transportation": "公共交通", "accommodation": null},
  "preferences": ["亲子友好", "历史文化"],
  "avoid": [],
  "decisions": ["选择中山陵作为核心景点"],
  "unknowns": ["未提供预算", "未提供住宿偏好"],
  "importance": 0.7
}
"""


def _extract_json(text: str) -> Optional[dict]:
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    raw = m.group(1) if m else text[text.find("{"): text.rfind("}") + 1]
    try:
        return json.loads(raw)
    except Exception:
        return None


class MemoryCompressor:
    def __init__(self):
        self._agent: Optional[SimpleAgent] = None

    @property
    def agent(self) -> SimpleAgent:
        if self._agent is None:
            self._agent = SimpleAgent(
                name="MemoryCompressor",
                llm=HelloAgentsLLM(),
                system_prompt=PROMPT,
                enable_tool_calling=False,
            )
        return self._agent

    async def compress_trip(self, user_query: str, trip_meta: dict, trip_plan: Optional[dict], warnings: Optional[list] = None) -> MemoryCompressionResult:
        if settings.memory_compress_mode == "template":
            return self.compress_template(user_query, trip_meta, trip_plan, warnings)
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._compress_llm_sync, user_query, trip_meta, trip_plan, warnings),
                timeout=settings.memory_compress_timeout,
            )
        except Exception as e:
            logger.warning("LLM memory compression failed, fallback to template: %s", e)
            if settings.memory_compress_fallback == "template":
                return self.compress_template(user_query, trip_meta, trip_plan, warnings)
            raise

    def _compress_llm_sync(self, user_query: str, trip_meta: dict, trip_plan: Optional[dict], warnings: Optional[list]) -> MemoryCompressionResult:
        # 限制 trip_plan 输入大小，只传关键摘要
        plan_summary = self._summarize_plan_for_prompt(trip_plan)
        payload = {
            "user_query": user_query,
            "trip_meta": trip_meta,
            "trip_plan_summary": plan_summary,
            "warnings": warnings or [],
        }
        resp = self.agent.run(json.dumps(payload, ensure_ascii=False, indent=2))
        data = _extract_json(resp)
        if data is None:
            raise ValueError("LLM compressor response has no JSON")
        return MemoryCompressionResult.model_validate(data)

    def compress_template(self, user_query: str, trip_meta: dict, trip_plan: Optional[dict], warnings: Optional[list] = None) -> MemoryCompressionResult:
        city = trip_meta.get("city") or "未知城市"
        days = trip_meta.get("days") or "未知天数"
        prefs = trip_meta.get("preferences")
        travelers = trip_meta.get("travelers") or {}
        must = trip_meta.get("must_visit") or []
        facts = {
            "city": trip_meta.get("city"),
            "days": trip_meta.get("days"),
            "travelers": travelers,
            "budget": trip_meta.get("budget"),
            "pace": trip_meta.get("pace"),
            "transportation": trip_meta.get("transportation"),
        }
        profile = {}
        query_lower = (user_query or "").lower()
        if "j人" in query_lower or "计划型" in query_lower or "高能量" in query_lower:
            profile["planning_style"] = "J"
        if "从北京出发" in user_query or "北京出发" in user_query:
            profile["home_city"] = "北京"
        if trip_meta.get("transportation"):
            profile["transportation"] = trip_meta["transportation"]
        if trip_meta.get("accommodation"):
            profile["accommodation"] = trip_meta["accommodation"]
        preferences = []
        if profile.get("planning_style") == "J":
            preferences.append("偏好计划性（自称J人）")
        if travelers.get("kids") or travelers.get("children"):
            preferences.append("亲子友好")
        decisions = []
        if must:
            decisions.append("用户指定必去：" + "、".join(must))
        summary = f"用户规划了{city}{days}天行程"
        if travelers:
            summary += f"，同行人信息：{travelers}"
        if prefs:
            summary += f"，偏好：{prefs}"
        summary += "。"
        unknowns = []
        if not trip_meta.get("budget"):
            unknowns.append("未提供预算")
        if not trip_meta.get("accommodation"):
            unknowns.append("未提供住宿偏好")
        return MemoryCompressionResult(
            summary=summary,
            facts=facts,
            profile=profile,
            preferences=preferences,
            avoid=trip_meta.get("avoid") or [],
            decisions=decisions,
            unknowns=unknowns,
            importance=0.75,
        )

    def _summarize_plan_for_prompt(self, trip_plan: Optional[dict]) -> dict:
        if not trip_plan:
            return {}
        days_summary = []
        for d in (trip_plan.get("days") or [])[:7]:
            blocks = []
            for b in (d.get("time_blocks") or [])[:8]:
                blocks.append({"poi_id": b.get("poi_id"), "time": f"{b.get('start_time')}-{b.get('end_time')}"})
            days_summary.append({"day": d.get("day"), "blocks": blocks, "cost": d.get("estimated_total_cost")})
        return {"city": trip_plan.get("city"), "days": days_summary, "budget": trip_plan.get("budget")}
