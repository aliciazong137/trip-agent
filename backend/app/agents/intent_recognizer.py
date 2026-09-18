"""
TripIntentRecognizer - 自然语言意图识别 Agent（第一期）

核心原则（review 反馈）：
  - 只从用户 query 抽取明确出现的字段，不猜测、不推断、不补全
  - query 没说的可选字段保持空，仅必填 city/days 放入 missing_fields
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


def enforce_intent_contract(
    result: IntentResult,
    has_active_session: bool,
    has_conversation_context: bool = False,
) -> IntentResult:
    """校正模型输出的上下文约束，不根据关键词重新判断用户意图。

    模型仍负责开放式理解。这里仅阻止不可能的状态进入工作流：没有当前
    Session 时不能“修改当前行程”；自然对话必须有可展示的回复。
    """
    if result.intent in {"current_trip_question", "current_trip_modify", "current_trip_replan"} and not has_active_session:
        logger.warning("意图模型返回 %s 但当前不存在 Session，降级为自然对话", result.intent)
        return IntentResult(
            intent="conversation",
            chat_reply="我在，想聊什么都可以。",
        )
    if result.intent == "conversation_context_question" and not has_conversation_context:
        logger.warning("意图模型返回 conversation_context_question 但没有聊天摘要，降级为自然对话")
        return IntentResult(intent="conversation", chat_reply="我在，想聊什么都可以。")
    if result.intent == "conversation" and not (result.chat_reply or "").strip():
        return result.model_copy(update={"chat_reply": "我在，想聊什么都可以。"})
    if result.intent == "unsupported" and not (result.chat_reply or "").strip():
        return result.model_copy(update={"chat_reply": "这件事我未必最擅长，不过我会尽量帮你一起想想。"})
    return result


INTENT_RECOGNIZER_PROMPT = """你是旅行需求解析助手。从用户 query 中抽取结构化 trip_meta，用于后续行程规划。

**严格规则（最重要）：**
1. **只抽取用户 query 中明确出现的字段**，不要猜测、不要推断、不要用常识补全
2. query 没说的可选字段保持空，不要填默认值；missing_fields 只记录缺失的 city/days
3. **intent 判断规则**：
   - query 提到任何与"旅行/出游/去某地/玩/几天"相关的词 → intent="trip_planning"
   - 即使 city 或 days 缺失，只要 query 像旅行规划 → intent="trip_planning"（把缺的字段放入 missing_fields）
   - 只有完全与旅行无关（如"写邮件"、"翻译这段话"、"做数学题"）才 intent="unsupported"
   - 问候、寒暄、能力咨询、没有明确旅行对象的开放式表达（如"你好"、"我想问点别的"、"随便聊聊"、"你能做什么"）→ intent="conversation"。它们不是旅行规划，不能追问城市/天数。
   - 对当前话题的取消、放弃或收尾（如"不想去了"、"算了"、"先不用了"、"取消吧"）→ intent="conversation"；自然确认即可，绝对不能展示旅行信息卡或重新开始规划。
   - 完全无关的请求（如写邮件、翻译、做数学题）→ intent="unsupported"，不能进入旅行规划。
   - **天气查询（查天气、今天/明天/本周天气）→ intent="weather_query"**（不是 unsupported！）
   - 若下方提供了“当前已生成行程”，用户在问这份行程的内容、总结或解释（如“还记得上面的行程吗”“第一天去哪”）→ intent="current_trip_question"。
   - 若下方提供了“当前聊天摘要”，用户在问上面的对话、攻略或路线方案（如“你还记得上面的内容吗”“刚才那三个方案是什么”）→ intent="conversation_context_question"。这不是新规划，也不是泛泛闲聊。
   - 若下方提供了“当前已生成行程”，用户要新增、替换、删除、调整该行程中的地点或顺序（如“把目的地改成国子监”“第一天加故宫”）→ intent="current_trip_modify"。
   - **优先识别重新攻略**：用户评价已生成的路线/行程不满意、都不喜欢、想换一版、重做攻略、重新推荐时 → intent="current_trip_replan"，不是 current_trip_question，也不是 current_trip_modify。
   - 若下方提供“正在等待重新攻略偏好”，用户补充主题、节奏、必去或避开内容（如“喜欢历史人文”“想轻松一点”“不要胡同”“必须去故宫”）→ intent="current_trip_replan"，并把本句明确出现的偏好放进 trip_meta。此时不能再追问“是替换还是加入”；系统会沿用已有城市、天数等事实重新生成路线方案。
   - current_trip_modify 只用于明确、可执行的原子修改，如“删掉北海公园”“第一天加故宫”“把目的地换成国子监”。
   - 没有当前已生成行程时，不能输出 current_trip_question 或 current_trip_modify。
   - 不要因为信息不全就把 trip_planning 判成 unsupported

**抽取规则（按这些规则解析用户原话）：**
- "三人行"/"三个人" → travelers.adults=3
- "带两个孩子" → travelers.children=2，不猜测成人数；"一大一小" → adults=1, children=1
- "亲子游/带娃/家庭游" 只表示偏好，不代表明确人数；没有具体人数时 travelers 留空，禁止追问几个大人、几个小朋友或儿童年龄
- "两天一夜"/"玩两天"/"2天" → days=2
- "预算5000" → budget.amount=5000, scope="unknown"
- "预算5000/人" → budget.amount=5000, scope="per_person"
- "预算5000左右" → budget.amount=5000, scope="unknown", approximate=true
- "学生党"/"穷游" 等模糊描述 → 不要推算预算，budget 留空，不放 missing_fields
- "去北京"/"想去南京" → city=对应城市
- preferences（偏好）从用户原话或上下文推断，常见类别：
  - "历史文化/博物馆/古迹/文物/遗址" → preferences="历史文化"
  - "自然/山水/风景/公园/湖泊/登山" → preferences="自然风光"
  - "美食/小吃/探店/吃货/夜市" → preferences="美食探店"
  - "亲子/带娃/儿童/家庭/一大一小" → preferences="亲子"
  - "购物/逛街/商场/免税/血拼" → preferences="购物"
  - "夜生活/酒吧/夜景" → preferences="夜生活"
  - "文艺/艺术/展览/拍照/打卡/网红" → preferences="文艺打卡"
  - 多个偏好用逗号拼接，如 preferences="历史文化,美食探店"
  - 上下文暗示也要推断（"带娃"→亲子、"文艺青年"→文艺打卡、"特种兵"除影响 pace 外也可记偏好）
- "必去故宫" → must_visit=["故宫"]
- transportation（交通方式）：用户明确提到则填入 trip_meta.transportation（不是放 assumptions）：
  - "自驾/开车/开车去" → transportation="自驾"
  - "公共交通/地铁/公交/坐地铁" → transportation="公共交通"
  - "步行/走路/走路去" → transportation="步行"
  - "高铁/火车/动车" → transportation="高铁"
  - 未提及 → 不填（trip_meta.transportation 留空，不要放 assumptions）
- pace（节奏）你**必须**映射到 relaxed/normal/packed 三者之一，禁止输出其他值：
  - 快节奏（特种兵/极限/紧凑/赶/打卡多/急/j人 等）→ packed
  - 慢节奏（轻松/休闲/慢/度假/p人 等）→ relaxed
  - 中性/未提及/拿不准 → normal（放 assumptions 说明，如"未明确节奏，默认 normal"）
  - 俚语、打错字、网络梗也必须归一化到这三者之一，不要原样输出或自创新词
- preferences 默认空（用户没说也放 assumptions）

**关于 assumptions：**
assumptions 记录"用户没明说但用了默认值的字段"，格式如 "未提供节奏，使用 normal"。
assumptions 只记默认值，**不记推断值**（如"学生党"不能推断成 budget=1000）。

**关于 missing_fields：**
missing_fields 只记录必填字段 city、days 的缺失。
travelers、preferences、budget、transportation 等都是可选字段，未提供也不能阻断规划，不放入 missing_fields。

**关于 clarification_question（重要，省一次 LLM 调用）：**
若 missing_fields 含必填字段（city/days），你必须在同一响应中直接生成澄清追问；两者齐全时 clarification_question=null，直接进入规划：
- 人设：小渡，一位亲切活泼、有点俏皮的旅行助手
- 先对用户输入热情呼应（如知道城市就表达期待），再自然地问缺的信息
- 口语化，30-60 字，不要用列表/破折号/模板句式（如「为了帮您生成…」禁止）
- 只追问 missing_fields 里缺的必填字段，不要问别的

**关于 chat_reply（自然对话）：**
- intent="conversation" 或 intent="unsupported" 时，必须生成 chat_reply，1-3 句自然中文。
- 对"你好/hello/嗨"等问候，直接友好地打招呼；不要介绍能力边界、不要追问旅行信息，也不要以问题结尾或邀请用户说明需求。回复只限一条自然招呼，例如“你好呀，很高兴见到你。”
- 对"你能做什么"等能力咨询，简洁介绍旅行规划、路线比较、行程回顾和调整。
- 对非旅行请求，先自然回应，再诚实说明旅行是主要能力；不要使用固定模板，也不要强行把话题带去做攻略。
- intent="trip_planning" 或 intent="weather_query" 时 chat_reply=null。
- intent="current_trip_question" 时 trip_meta=null，必须用 chat_reply 根据当前行程自然回答用户问题；不能固定复读行程摘要。
- intent="conversation_context_question" 时 trip_meta=null，必须用 chat_reply 根据当前聊天摘要自然回答；不能固定复读聊天摘要。
- intent="current_trip_modify" 时 trip_meta=null、chat_reply=null。
- intent="current_trip_replan" 时可从本句抽取部分 trip_meta；若本句尚未给出主题、节奏、必去或避开信息，chat_reply 用一句话一次性询问这些方向。

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
  "assumptions": ["未提供出发日期 start_date"],
  "clarification_question": null,
  "chat_reply": null
}}
```
（missing_fields 非空时 clarification_question 填小渡语气的追问；否则为 null）

**字段说明：**
- trip_meta: 按 schemas.py 的 TripMeta 结构。用户没说的字段用 null 或空数组，**不要编造**
- transportation: 用户明确提到交通方式时必须填入 trip_meta.transportation（不是 assumptions）；未提及则留空
- missing_fields: 必填字段缺失的列表，如 ["city", "days"]
- invalid_fields: 用户说了但值不合法的字段，如 days=100
- assumptions: 用户没说但用了默认值的字段说明

**特殊情况：**
- intent="unsupported" 时，trip_meta 设为 null，missing_fields 为空
- query 完全不像旅行规划时，intent="unsupported"
- **intent="weather_query" 时，trip_meta 中只填 city（若提到），其余为空**
- **intent="conversation" 时，trip_meta 设为 null，chat_reply 填自然回复**
- **intent="current_trip_question" / "current_trip_modify" / "current_trip_replan" 只会在提供当前行程时使用**
- **intent="conversation_context_question" 只会在提供当前聊天摘要时使用，trip_meta 设为 null**
- **信息不全但像旅行规划 → intent="trip_planning"，缺的字段放 missing_fields**
  - 正例："想去玩几天" → intent="trip_planning", missing_fields=["city", "days"]
  - 正例："去北京" → intent="trip_planning", missing_fields=["days"]
  - 正例："查上海天气" → intent="weather_query", trip_meta.city="上海"
  - 正例："明天北京天气怎么样" → intent="weather_query", trip_meta.city="北京"
  - 反例："写邮件" → intent="unsupported"
  - 反例："帮我翻译这段话" → intent="unsupported"
  - 正例："我想问点别的" → intent="conversation"，用 chat_reply 自然邀请用户继续说明
  - 正例："你好" → intent="conversation", chat_reply="你好呀，很高兴见到你。"

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

    def recognize_sync(self, query: str, memory_context: str = "") -> IntentResult:
        """同步识别（用于 run_in_threadpool 包装）"""
        last_error: Optional[str] = None
        user_input = query
        if memory_context:
            user_input = (
                f"用户本次 query：{query}\n\n"
                f"以下是上下文。若其中标注为当前已生成行程，只可用于判断用户是否在回顾或修改该行程；"
                f"禁止用于补全 city/days/travelers/budget 等新规划事实：\n"
                f"{memory_context}"
            )
        for attempt in range(self.max_retries + 1):
            try:
                # 第六阶段：意图识别是结构化 JSON 抽取（与 attraction 同类），关思考提速。
                # 实测关思考零退化（完整/缺字段/非旅行三类 query 全对），15-20s → 2.4s。
                from app.config import settings
                run_kwargs = (
                    {"extra_body": {"thinking": {"type": "disabled"}}}
                    if settings.llm_thinking_disabled else {}
                )
                response = self.agent.run(user_input, **run_kwargs)
                data = _extract_json_from_response(response)
                if data is None:
                    last_error = f"LLM 响应中未找到 JSON: {response[:200]}"
                    logger.warning("IntentRecognizer attempt %d: %s", attempt + 1, last_error)
                    continue

                # LLM 负责意图理解；这里只校验结构和当前 Session 的客观约束。
                result = IntentResult.model_validate(data)
                result = enforce_intent_contract(
                    result,
                    has_active_session="当前已生成行程" in memory_context,
                    has_conversation_context="当前聊天摘要" in memory_context,
                )
                logger.info(
                    "intent 识别完成: intent=%s active_session=%s has_meta=%s",
                    result.intent, "当前已生成行程" in memory_context, bool(result.trip_meta),
                )
                return result
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

    async def recognize(self, query: str, memory_context: str = "") -> IntentResult:
        """异步识别：把同步调用丢到线程池"""
        import asyncio
        return await asyncio.to_thread(self.recognize_sync, query, memory_context)
