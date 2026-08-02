"""
GLM Web Search Tool - 封装 GLM 的 search_pro_quark（夸克搜索）为 HelloAgents Tool

用途：高德 MCP 的 maps_text_search / maps_search_detail 不返回门票价格（cost 字段实测为空），
      用 GLM 夸克搜索补"景点名 门票价格"，从实时网页提取真实门票。

API: POST {LLM_BASE_URL}/web_search
body: {search_query, search_engine: "search_pro_quark", search_intent: True, count, search_recency_filter}
返回: {search_result: [{content, title, link, media, publish_date, refer}]}
"""
import json
import os
from typing import List, Dict, Any

import httpx
from hello_agents.tools import Tool, ToolParameter

from app.config import settings


class GLMWebSearchTool(Tool):
    """GLM 夸克搜索工具（继承 HelloAgents Tool，返回 str）"""

    def __init__(self):
        super().__init__(
            name="glm_web_search",
            description="GLM 夸克搜索（search_pro_quark），返回实时网页内容。用于查询景点门票价格、最新信息等高德 MCP 拿不到的数据。",
        )

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="search_query",
                type="string",
                description="搜索查询词，如 '故宫博物院 门票价格'",
                required=True,
            ),
            ToolParameter(
                name="count",
                type="integer",
                description="返回结果条数，默认 5",
                required=False,
            ),
        ]

    def run(self, parameters: Dict[str, Any]) -> str:
        query = parameters.get("search_query", "")
        if not query:
            return "错误：search_query 不能为空"

        count = int(parameters.get("count", 5))
        api_key = settings.llm_api_key
        base_url = settings.llm_base_url or "https://open.bigmodel.cn/api/paas/v4"

        if not api_key:
            return "错误：LLM_API_KEY 未配置"

        try:
            resp = httpx.post(
                f"{base_url}/web_search",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "search_query": query,
                    "search_engine": "search_pro_quark",
                    "search_intent": True,
                    "count": count,
                    "search_recency_filter": "oneMonth",
                },
                timeout=30,
            )
            data = resp.json()
            results = data.get("search_result", [])
            if not results:
                return f"未找到搜索结果（query={query}）"

            # 整理成紧凑文本，给 LLM 提取价格
            parts = []
            for i, r in enumerate(results[:count], 1):
                parts.append(
                    f"[{i}] {r.get('title', '')}\n"
                    f"来源: {r.get('media', '')} ({r.get('publish_date', '')})\n"
                    f"内容: {r.get('content', '')[:500]}"
                )
            return "\n\n".join(parts)
        except httpx.HTTPError as e:
            return f"错误：GLM web search 请求失败: {e}"
        except Exception as e:
            return f"错误：GLM web search 异常: {e}"
