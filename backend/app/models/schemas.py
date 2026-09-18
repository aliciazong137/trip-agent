"""
Pydantic 数据模型 - 融合当前 TS domain.ts + Datawhale 第十三章 schemas

层次结构（自底向上）：
  Location (经纬度) → Poi (景点) → DayPlan (单日) → Itinerary (行程)
  TripMeta (用户输入元信息)
  TripPlan (顶层，第十三章，含天气/预算/建议)
  SessionState (会话状态)
  ReviseDayOperation (结构化修改)
  API 请求/响应模型
"""
from typing import Any, Dict, Optional, List, Union, Literal
from pydantic import BaseModel, Field, field_validator


# ─── 基础模型 ──────────────────────────────────────────────────────────────────

class Location(BaseModel):
    """位置信息（经纬度坐标）"""
    longitude: float = Field(..., description="经度", ge=-180, le=180)
    latitude: float = Field(..., description="纬度", ge=-90, le=90)


class Budget(BaseModel):
    """预算信息"""
    total_attractions: int = Field(default=0, description="景点门票总费用")
    total_hotels: int = Field(default=0, description="酒店总费用")
    total_meals: int = Field(default=0, description="餐饮总费用")
    total_transportation: int = Field(default=0, description="交通总费用")
    total: int = Field(default=0, description="总费用")

    @field_validator("total_attractions", "total_hotels", "total_meals", "total_transportation", "total", mode="before")
    @classmethod
    def _null_to_zero(cls, v):
        """LLM 对缺失数值字段常输出 null，统一转 0"""
        return 0 if v is None else v


class WeatherInfo(BaseModel):
    """天气信息（第十三章，含温度解析验证器）"""
    date: str = Field(..., description="日期")
    day_weather: str = Field(..., description="白天天气")
    night_weather: str = Field(..., description="夜间天气")
    day_temp: int = Field(..., description="白天温度（摄氏度）")
    night_temp: int = Field(..., description="夜间温度（摄氏度）")
    wind_direction: str = Field(..., description="风向")
    wind_power: str = Field(..., description="风力")

    @field_validator("date", "day_weather", "night_weather", "wind_direction", "wind_power", mode="before")
    @classmethod
    def _null_to_empty(cls, v):
        """LLM 对缺失字段常输出 null，统一转空串"""
        return "" if v is None else v

    @field_validator("day_temp", "night_temp", mode="before")
    @classmethod
    def parse_temperature(cls, v):
        """解析温度字符串："16°C" -> 16"""
        if v is None:
            return 0
        if isinstance(v, str):
            v = v.replace("°C", "").replace("℃", "").replace("°", "").strip()
            try:
                return int(v)
            except ValueError:
                return 0
        return v


# ─── POI / 景点 ─────────────────────────────────────────────────────────────────

PoiCategory = Literal["attraction", "restaurant", "shopping", "experience", "transport"]
PoiPriority = Literal["must", "nice", "optional"]


class UnknownCost(BaseModel):
    unknown: Literal[True] = True


# EstimatedCost = number | {unknown: true}，用 Union 表达
EstimatedCost = Union[int, UnknownCost]


class Poi(BaseModel):
    """POI 景点信息（融合当前 Poi + 第十三章 Attraction）"""
    id: str = Field(..., description="POI ID，格式 poi_xxx")
    name: str = Field(..., description="景点名称")
    aliases: Optional[List[str]] = Field(default=[], description="别名列表")
    category: PoiCategory = Field(..., description="类别")
    area: str = Field(..., description="所在区域")
    location: Optional[Location] = Field(default=None, description="经纬度")
    source_refs: List[dict] = Field(default=[], description="来源引用")
    confidence: float = Field(default=0.8, description="置信度", ge=0, le=1)
    priority: PoiPriority = Field(..., description="优先级 must/nice/optional")
    estimated_duration_minutes: Optional[int] = Field(default=None, description="预估游览时长（分钟）")
    estimated_cost: Optional[EstimatedCost] = Field(default=None, description="预估花费")
    opening_hours: Optional[str] = Field(default=None, description="营业时间")
    constraints: Optional[List[str]] = Field(default=[], description="约束条件")
    # 第十三章扩展
    address: Optional[str] = Field(default=None, description="地址")
    rating: Optional[float] = Field(default=None, description="评分", ge=0, le=5)
    image_url: Optional[str] = Field(default=None, description="图片URL")
    ticket_price: int = Field(default=0, description="门票价格（元）", ge=0)
    description: Optional[str] = Field(default=None, description="描述")


class PoiList(BaseModel):
    """POI 列表"""
    city: str = Field(..., description="城市")
    pois: List[Poi] = Field(default=[], description="POI 列表")


# ─── 餐饮 / 酒店 ────────────────────────────────────────────────────────────────

class Meal(BaseModel):
    """餐饮信息（第十三章）"""
    type: str = Field(..., description="餐饮类型 breakfast/lunch/dinner/snack")
    name: str = Field(..., description="餐饮名称")
    address: Optional[str] = Field(default=None)
    location: Optional[Location] = Field(default=None)
    description: Optional[str] = Field(default=None)
    estimated_cost: int = Field(default=0, description="预估费用")

    @field_validator("type", "name", mode="before")
    @classmethod
    def _null_to_empty(cls, v):
        """LLM 对缺失字段常输出 null，统一转空串"""
        return "" if v is None else v

    @field_validator("estimated_cost", mode="before")
    @classmethod
    def _null_to_zero(cls, v):
        return 0 if v is None else v


class Hotel(BaseModel):
    """酒店信息（第十三章）"""
    name: str = Field(..., description="酒店名称")
    address: str = Field(default="")
    location: Optional[Location] = Field(default=None)
    price_range: str = Field(default="")
    rating: str = Field(default="")
    distance: str = Field(default="")
    type: str = Field(default="")
    estimated_cost: int = Field(default=0, description="预估费用（元/晚）")

    @field_validator("name", "address", "price_range", "rating", "distance", "type", mode="before")
    @classmethod
    def _null_to_empty(cls, v):
        """LLM 对缺失字段常输出 null，统一转空串（rating=null 等）"""
        return "" if v is None else v

    @field_validator("estimated_cost", mode="before")
    @classmethod
    def _null_to_zero(cls, v):
        return 0 if v is None else v


# ─── 行程 ──────────────────────────────────────────────────────────────────────

TimeHHMM = str  # HH:MM 格式，如 "10:00"


class TimeBlock(BaseModel):
    """时间块（当前 TS 定义）"""
    poi_id: str = Field(..., description="POI ID")
    start_time: TimeHHMM = Field(..., description="开始时间 HH:MM")
    end_time: TimeHHMM = Field(..., description="结束时间 HH:MM")
    reason: str = Field(..., description="安排理由")
    locked: bool = Field(default=False, description="是否锁定")


class DayPlan(BaseModel):
    """单日行程（融合当前 DayPlan + 第十三章 DayPlan）"""
    day: int = Field(..., description="第几天（从1开始）")
    date: Optional[str] = Field(default=None, description="日期")
    area_cluster: List[str] = Field(default=[], description="区域聚类")
    time_blocks: List[TimeBlock] = Field(default=[], description="时间块列表")
    estimated_total_cost: int = Field(default=0, description="当日总花费")
    estimated_total_minutes: int = Field(default=0, description="当日总时长（分钟）")
    # 第十三章扩展
    description: Optional[str] = Field(default=None, description="当日行程描述")
    transportation: Optional[str] = Field(default=None, description="交通方式")
    accommodation: Optional[str] = Field(default=None, description="住宿安排")
    hotel: Optional[Hotel] = Field(default=None)
    meals: List[Meal] = Field(default=[], description="餐饮安排")


class Itinerary(BaseModel):
    """完整行程"""
    session_id: str = Field(..., description="会话ID")
    city: str = Field(..., description="城市")
    days: List[DayPlan] = Field(default=[], description="每日行程")
    version: int = Field(default=1, description="版本号")


class MapPoint(BaseModel):
    """地图路线中的一个已定位 POI。"""
    order: int = Field(..., ge=1, description="当日访问顺序")
    poi_id: str = Field(..., description="POI ID")
    name: str = Field(..., description="POI 名称")
    location: Optional[Location] = Field(default=None, description="经纬度")
    start_time: Optional[str] = Field(default=None, description="开始时间")
    end_time: Optional[str] = Field(default=None, description="结束时间")


class DayRouteMap(BaseModel):
    """单日路线地图数据。"""
    day: int = Field(..., ge=1, description="第几天")
    city: str = Field(default="", description="城市")
    transportation: Optional[str] = Field(default=None, description="交通方式")
    points: List[MapPoint] = Field(default=[], description="按行程顺序排列的地图点")
    route_ready: bool = Field(default=False, description="是否至少有两个可规划路线的点")
    unmapped_points: List[str] = Field(default=[], description="缺少合法坐标的 POI 名称")


class MapData(BaseModel):
    """可由前端路线消息直接消费的地图数据。"""
    city: str = Field(default="", description="城市")
    days: List[DayRouteMap] = Field(default=[], description="按天分组的路线")


# ─── 用户输入元信息 ─────────────────────────────────────────────────────────────

Pace = Literal["relaxed", "normal", "packed"]


class TripMeta(BaseModel):
    """行程元信息（融合当前 TripMeta + 第十三章 TripPlanRequest）"""
    city: str = Field(..., description="目的地城市")
    days: int = Field(..., description="天数", gt=0)
    start_date: Optional[str] = Field(default=None, description="开始日期")
    budget: Optional[dict] = Field(default=None, description="预算 {currency, amount}")
    pace: Pace = Field(default="normal", description="节奏 relaxed/normal/packed")
    must_visit: List[str] = Field(default=[], description="必去列表")
    avoid: List[str] = Field(default=[], description="避开列表")
    travelers: Optional[dict] = Field(default=None, description="出行人 {adults, kids}")
    # 第十三章扩展
    preferences: Optional[str] = Field(default=None, description="偏好（如历史文化）")
    transportation: Optional[str] = Field(default=None, description="交通方式")
    accommodation: Optional[str] = Field(default=None, description="住宿类型")


# ─── 顶层旅行计划 ───────────────────────────────────────────────────────────────

class TripPlan(BaseModel):
    """完整旅行计划（第十三章顶层模型）"""
    city: str = Field(..., description="目的地城市")
    start_date: str = Field(..., description="开始日期")
    end_date: str = Field(..., description="结束日期")
    days: List[DayPlan] = Field(default=[], description="每日行程")
    weather_info: List[WeatherInfo] = Field(default=[], description="天气信息")
    overall_suggestions: str = Field(default="", description="总体建议")
    budget: Optional[Budget] = Field(default=None, description="预算信息")
    # 关联
    session_id: Optional[str] = Field(default=None, description="会话ID")

    @field_validator("city", "start_date", "end_date", "overall_suggestions", mode="before")
    @classmethod
    def _null_to_empty(cls, v):
        """LLM 对缺失字段常输出 null（start_date=null 等），统一转空串，避免整个规划失败"""
        return "" if v is None else v


# ─── 会话状态 ───────────────────────────────────────────────────────────────────

SessionPhase = Literal[
    "idle", "intake", "parsing", "parsed",
    "planning", "planned", "revising", "escalate"
]

ConfirmationDecision = Literal["confirm", "replace", "remove"]


class SessionConfirmation(BaseModel):
    """确认请求"""
    id: str
    error_code: str
    decision: Optional[ConfirmationDecision] = None
    at: Optional[str] = None


class SessionState(BaseModel):
    """会话状态（当前 TS 定义）"""
    session_id: str = Field(..., description="会话ID")
    phase: SessionPhase = Field(default="idle")
    intent: str = Field(default="", description="意图")
    completed: List[str] = Field(default=[])
    pending_questions: List[str] = Field(default=[])
    confirmations: List[SessionConfirmation] = Field(default=[])
    updated_at: str = Field(default="")


# ─── 结构化修改 ─────────────────────────────────────────────────────────────────

ReviseDayOpType = Literal["remove", "move", "add"]


class ReviseDayOperation(BaseModel):
    """修改单日行程的操作（当前 TS 定义）"""
    type: ReviseDayOpType
    poi_id: str
    to_day: Optional[int] = None  # for move
    position: Optional[int] = None  # for add


class ReviseDayRequest(BaseModel):
    """修改单日请求"""
    session_id: str
    day: int
    operations: List[ReviseDayOperation]


# ─── API 请求/响应 ──────────────────────────────────────────────────────────────

class TripPlanRequest(BaseModel):
    """生成旅行计划请求"""
    trip_meta: TripMeta
    guide_text: Optional[str] = Field(default=None, description="攻略文本（可选）")


class TripPlanResponse(BaseModel):
    """生成旅行计划响应"""
    session_id: str
    status: Literal["ok", "needs_confirmation", "failed"]
    trip_plan: Optional[TripPlan] = None
    warnings: List[str] = Field(default=[])
    error: Optional[dict] = Field(default=None)


class SessionResponse(BaseModel):
    """获取会话响应"""
    session_id: str
    trip_meta: Optional[TripMeta] = None
    poi_list: Optional[PoiList] = None
    itinerary: Optional[Itinerary] = None
    session_state: Optional[SessionState] = None
    updated_at: str


# ─── 第一期：自然语言入口 Schema ────────────────────────────────────────────────

class NlTripPlanRequest(BaseModel):
    """自然语言规划请求"""
    query: str = Field(..., min_length=1, max_length=2000, description="用户自然语言 query")
    session_id: Optional[str] = Field(default=None, description="会话 ID（澄清续接时携带）")
    user_id: Optional[str] = Field(default="default_user", description="用户 ID（Memory 隔离用，未登录时用 default_user）")
    conversation_context: Optional[str] = Field(default=None, max_length=4000, description="当前可见对话摘要，仅用于回顾本轮聊天")
    guide_style: Literal["full", "inspiration"] = Field(default="full", description="攻略展示样式：直接对话完整攻略 / 热门笔记简短选线")
    # 热门推荐卡片的结构化上下文。该入口已经明确表示“按这篇笔记做路线”，
    # 不再把笔记正文伪装成用户对话后交给意图识别器猜测。
    inspiration_note: Optional[Dict[str, Any]] = Field(default=None, description="用户选中的热门旅行笔记")
    inspiration_trip_meta: Optional[Dict[str, Any]] = Field(default=None, description="由热门笔记标签提取的目的地与默认天数")


class IntentResult(BaseModel):
    """意图识别结构化输出（LLM 抽取结果）

    注意：trip_meta 这里允许部分填充（缺 city/days 是常态），
    所以不能用 TripMeta（必填 city 和 days）。用 dict 接收，
    由 field_validator 做确定性校验。
    """
    intent: Literal["trip_planning", "weather_query", "conversation", "unsupported", "current_trip_question", "current_trip_modify", "current_trip_replan", "conversation_context_question"] = Field(default="trip_planning")
    trip_meta: Optional[dict] = None
    missing_fields: List[str] = Field(default=[])
    invalid_fields: List[str] = Field(default=[])
    assumptions: List[str] = Field(default=[])
    # 性能优化：意图识别同一次调用直接产出小渡语气的追问，
    # orchestrator 在缺失字段一致时直接使用，省一次 LLM 往返
    clarification_question: Optional[str] = Field(default=None)
    # 问候、能力咨询或非旅行闲聊由意图模型同轮生成自然回复，避免固定兜底话术。
    chat_reply: Optional[str] = Field(default=None)


class RetrievedSource(BaseModel):
    """统一可解释来源：用户 Memory、目的地 RAG 或小红书笔记。"""
    type: Literal["user_memory", "destination_rag", "xhs_notes"]
    id: str
    title: str = ""
    content: str = ""
    score: float = 0.0
    reason: str = ""
    source: Optional[str] = None
    city: Optional[str] = None
    category: Optional[str] = None


class RouteOption(BaseModel):
    """攻略选线候选。"""
    id: str
    title: str
    route_text: str = ""
    suitable_for: str = ""
    poi_hints: List[str] = Field(default_factory=list)
    food_hints: List[str] = Field(default_factory=list)
    tips: List[str] = Field(default_factory=list)


class GuideRoutesResponse(BaseModel):
    """攻略选线响应（非流式兜底/guide_done data）。"""
    status: Literal["ok", "failed"]
    session_id: Optional[str] = None
    guide_markdown: str = ""
    route_options: List[RouteOption] = Field(default_factory=list)
    sources: List[RetrievedSource] = Field(default_factory=list)
    # 统一对话入口的下一步：选线、自然对话、回顾或修改现有行程。
    action: Literal["route_options", "conversation", "current_trip_question", "current_trip_modify", "weather_query", "clarification"] = "route_options"
    assistant_message: Optional[str] = None
    trip_meta: Optional[Dict[str, Any]] = None
    itinerary_updated: bool = False
    error_code: Optional[str] = None
    error_message: Optional[str] = None


class PlanFromRouteRequest(BaseModel):
    """用户确认攻略路线后的落地规划请求。"""
    query: str = Field(..., min_length=1, max_length=2000, description="原始用户自然语言 query")
    selected_route: RouteOption
    session_id: Optional[str] = Field(default=None, description="会话 ID")
    user_id: Optional[str] = Field(default="default_user", description="用户 ID")
    # 阶段 A 已确认的事实，阶段 B 直接复用，避免再做一次 LLM 意图识别。
    trip_meta: Optional[Dict[str, Any]] = None


class TripContextRequest(BaseModel):
    """针对当前已生成行程的追问，不触发新一轮规划。"""
    query: str = Field(..., min_length=1, max_length=2000)
    user_id: Optional[str] = Field(default="default_user", description="用户 ID")


class TripContextResponse(BaseModel):
    session_id: str
    message: str


class UserContext(BaseModel):
    """按 user_id 聚合的用户画像与历史上下文。"""
    user_id: str
    profile: Dict[str, Any] = Field(default_factory=dict)
    memories: List[Dict[str, Any]] = Field(default_factory=list)
    sources: List[RetrievedSource] = Field(default_factory=list)


class NlTripPlanResponse(BaseModel):
    """自然语言规划响应"""
    status: Literal["ok", "needs_clarification", "failed"]
    session_id: Optional[str] = None
    trip_meta: Optional[TripMeta] = None
    trip_plan: Optional[TripPlan] = None
    clarification_question: Optional[str] = None
    missing_fields: List[str] = Field(default=[])
    invalid_fields: List[str] = Field(default=[])
    assumptions: List[str] = Field(default=[])
    sources: List[RetrievedSource] = Field(default=[])
    user_context: Optional[UserContext] = None
    warnings: List[str] = Field(default=[])
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    hotel_candidates: List[Dict[str, Any]] = Field(default_factory=list, description="高德搜索到的酒店候选（5 个，含名称/评分/星级/区域）")


# ─── 行程自然语言修订 Schema ─────────────────────────────────────────────────

class ReviseNlRequest(BaseModel):
    """自然语言行程修订请求"""
    query: str = Field(..., min_length=1, max_length=2000, description="用户自然语言修订 query")
    day: Optional[int] = Field(default=None, ge=1, description="目标天（None 表示通用修订）")
    user_id: Optional[str] = Field(default="default_user", description="用户 ID")


class ReviseNlResponse(BaseModel):
    """自然语言行程修订响应"""
    status: Literal["ok", "needs_clarification", "needs_confirmation", "failed"]
    session_id: str
    message: str = Field(default="", description="AI 助手回复给用户的消息")
    itinerary_version: int = Field(default=1, description="修订后行程版本号")
    clarification_question: Optional[str] = Field(default=None, description="需要澄清时的问题")
    warnings: List[str] = Field(default=[])
    error_code: Optional[str] = None
    error_message: Optional[str] = None
