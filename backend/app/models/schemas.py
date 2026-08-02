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
from typing import Optional, List, Union, Literal
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


class WeatherInfo(BaseModel):
    """天气信息（第十三章，含温度解析验证器）"""
    date: str = Field(..., description="日期")
    day_weather: str = Field(..., description="白天天气")
    night_weather: str = Field(..., description="夜间天气")
    day_temp: int = Field(..., description="白天温度（摄氏度）")
    night_temp: int = Field(..., description="夜间温度（摄氏度）")
    wind_direction: str = Field(..., description="风向")
    wind_power: str = Field(..., description="风力")

    @field_validator("day_temp", "night_temp", mode="before")
    @classmethod
    def parse_temperature(cls, v):
        """解析温度字符串："16°C" -> 16"""
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
