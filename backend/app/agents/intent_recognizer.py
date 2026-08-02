"""
TripIntentRecognizer - 自然语言意图识别 Agent（第一期）

核心原则（review 反馈）：
  - 只从用户 query 抽取明确出现的字段，不猜测、不推断、不补全
  - query 没说的字段一律放入 missing_fields，不要填默认值
  - 非旅行规划请求 → intent=unsupported
  - 不接收 RAG 上下文（用户事实严格来自用户原话）

技术实现：
  - 基于 HelloAgents SimpleAgent，不挂工具，一次 LLM 调用
  - LLM 输出 JSON，用 Pydantic IntentResult 校验
  - 非法 JSON 重试一次，再降级 failed
"""
import json
import logging
import re
from typing import Optional

from hello_agents import HelloAgentsLLM, SimpleAgent

from app.models.schemas import IntentResult, TripMeta


logger = logging.getLogger(__name__)


INTENT_RECOGNIZER_PROMPT = """你是旅行需求解析助手。从用户 query 中抽取结构化 trip_meta，用于后续行程规划。

**严格规则（最重要）：**
1. **只抽取用户 query 中明确出现的字段**，不要猜测、不要推断、不要用常识补全
2. query 没说的字段一律放入 missing_fields，不要填默认值
3. **intent 判断规则**：
   - query 提到任何与"旅行/出游/去某地/玩/几天"相关的词 → intent="trip_planning"
   - 即使 city 或 days 缺失，只要 query 像旅行规划 → intent="trip_planning"（把缺的字段放入 missing_fields）
   - 只有完全与旅行无关（如"写邮件"、"查天气"、"翻译这段话"）才 intent="unsupported"
   - 不要因为信息不全就把 trip_planning 判成 unsupported

**抽取规则（按这些规则解析用户原话）：**
- "三人行"/"三个人" → travelers.adults=3
- "带两个孩子"/"一大一小" → adults=1, children=1（或按原话分）
- "两天一夜"/"玩两天"/"2天" → days=2
- "预算5000" → budget.amount=5000, scope="unknown"
- "预算5000/人" → budget.amount=5000, scope="per_person"
- "预算5000左右" → budget.amount=5000, scope="unknown", approximate=true
- "学生党"/"穷游" 等模糊描述 → 不要推算预算，放 missing_fields
- "去北京"/"想去南京" → city=对应城市
- "历史文化"/"博物馆"/"美食" → preferences=对应词
- "必去故宫" → must_visit=["故宫"]
- pace（节奏）默认 normal（用户没说也用 normal，放 assumptions 不算 missing）
- preferences 默认空（用户没说也放 assumptions）

**关于 assumptions：**
assumptions 记录"用户没明说但用了默认值的字段"，格式如 "未提供节奏，使用 normal"。
assumptions 只记默认值，**不记推断值**（如"学生党"不能推断成 budget=1000）。

**关于 missing_fields：**
missing_fields 记录必填字段缺失或可选字段用户可能想提供但没说清楚的。
必填：city, days。缺任一必须放入 missing_fields。

**输出 JSON 格式（严格按此结构）：**
```json
{{
  "intent": "trip_planning",
  "trip_meta": {{
    "city": "北京",
    "days": 2,
    "budget": {{"currency": "CNY", "amount": 5000, "scope": "unknown"}},
    "travelers": {{"adults": 3, "children": 0}},
    "pace": "normal",
    "preferences": "历史文化",
    "must_visit": ["故宫"],
    "avoid": []
  }},
  "missing_fields": [],
  "invalid_fields": [],
  "assumptions": ["未提供出发日期 start_date", "未提供交通方式 transportation"]
}}
```

**字段说明：**
- trip_meta: 按 schemas.py 的 TripMeta 结构。用户没说的字段用 null 或空数组，**不要编造**
- missing_fields: 必填字段缺失的列表，如 ["city", "days"]
- invalid_fields: 用户说了但值不合法的字段，如 days=100
- assumptions: 用户没说但用了默认值的字段说明

**特殊情况：**
- intent="unsupported" 时，trip_meta 设为 null，missing_fields 为空
- query 完全不像旅行规划时，intent="unsupported"
- **信息不全但像旅行规划 → intent="trip_planning"，缺的字段放 missing_fields**
  - 正例："想去玩几天" → intent="trip_planning", missing_fields=["city", "days"]
  - 正例："去北京" → intent="trip_planning", missing_fields=["days"]
  - 反例："写邮件" → intent="unsupported"
  - 反例："今天上海天气" → intent="unsupported"

**最后一条消息必须是纯 JSON**（不要 ```json``` 代码块，直接输出 { 开头的 JSON 对象）。
"""


def _extract_json_from_response(text: str) -> Optional[dict]:
    """
    从 LLM 响应中提取 JSON 对象

    LLM 可能返回带前缀文本或 markdown 代码块的 JSON，需稳健提取。
    优先级：```json``` 代码块 → 第一个 { 到最后一个 }。
    """
    if not text:
        return None

    # 1. 代码块
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # 2. 第一个 { 到最后一个 }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass

    return None


class TripIntentRecognizer:
    """
    旅行意图识别器

    用法：
        recognizer = TripIntentRecognizer()
        result: IntentResult = await recognizer.recognize(query)

    设计：
        - 一次 LLM 调用，不挂工具（纯文本抽取任务）
        - 非法 JSON 重试一次（LLM 偶尔会返回带噪声的 JSON）
        - 再失败降级为 IntentResult(intent="unsupported")
    """

    def __init__(self, max_retries: int = 1):
        self.llm = HelloAgentsLLM()
        self.agent = SimpleAgent(
            name="TripIntentRecognizer",
            llm=self.llm,
            system_prompt=INTENT_RECOGNIZER_PROMPT,
            enable_tool_calling=False,
        )
        self.max_retries = max_retries

    def recognize_sync(self, query: str) -> IntentResult:
        """同步识别（用于 run_in_threadpool 包装）"""
        last_error: Optional[str] = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self.agent.run(query)
                data = _extract_json_from_response(response)
                if data is None:
                    last_error = f"LLM 响应中未找到 JSON: {response[:200]}"
                    logger.warning("IntentRecognizer attempt %d: %s", attempt + 1, last_error)
                    continue

                # 兜底：若 LLM 返回的 trip_meta 缺字段，Pydantic 校验会抛错
                return IntentResult.model_validate(data)
            except Exception as e:
                last_error = f"attempt {attempt + 1}: {type(e).__name__}: {str(e)[:200]}"
                logger.warning("IntentRecognizer %s", last_error)
                continue

        # 全部重试失败，降级
        logger.error("IntentRecognizer 全部重试失败: %s", last_error)
        return IntentResult(
            intent="unsupported",
            trip_meta=None,
            missing_fields=[],
            invalid_fields=[],
            assumptions=[],
        )

    async def recognize(self, query: str) -> IntentResult:
        """异步识别：把同步调用丢到线程池"""
        import asyncio
        return await asyncio.to_thread(self.recognize_sync, query)
