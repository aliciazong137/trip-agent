"""
确定性排程内核 - 移植自 src/tools/build-itinerary.ts

保留算法：
  - 排序：must > nice > optional，同优先级按 area 聚类，再按 confidence 降序
  - 天分配：先放 must，按 pace 控制每日上限（relaxed 360 / normal 480 / packed 600）
  - 时间：从 10:00 起，按 POI 时长 + POI 间真实移动时间累加
  - 成本：累加 POI cost；字符串（如 "60元（旺季）/40元（淡季）"）解析取高值；
         "免费" 记 0 且不告警；无法解析的 {unknown:true} 计 0 并产出 warning
  - 全天项目（duration >= 360 或 constraints 含"全天项目"）单独占一天

移动时间（第零期改造）：
  原方案用手写区域矩阵 data/travel-time/{city}-area-matrix.json，存在两个问题：
    1. 路径算错（指向不存在的 backend/data/），所有矩阵从未生效，移动时间恒为 0
    2. 区级粒度太粗，且每城市需手写 N² 数据
  现改为高德 maps_distance 预取的 POI 级矩阵，见 services/travel_matrix.py。
  排程仍为确定性纯逻辑（只读矩阵，不发网络请求）。

移植注意：
  - 用 dict 表达 Poi/TripMeta/PoiList，不强依赖 Pydantic（H4 工具层做转换）
  - 保持与 TS 版完全一致的输出，便于测试对齐
"""
import json
import re
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

from app.services.travel_matrix import get_travel_minutes

PACE_DAILY_LIMIT_MINUTES: Dict[str, int] = {
    "relaxed": 360,
    "normal": 480,
    "packed": 600,
}

FULL_DAY_THRESHOLD_MINUTES = 360
FULL_DAY_CONSTRAINT = "全天项目"


def is_full_day_poi(poi: dict) -> bool:
    """判断是否全天项目"""
    duration = poi.get("estimated_duration_minutes") or 0
    if duration >= FULL_DAY_THRESHOLD_MINUTES:
        return True
    constraints = poi.get("constraints") or []
    if any(FULL_DAY_CONSTRAINT in c for c in constraints):
        return True
    return False


# 明确表示免费的关键词（免费是确定信息，不是未知）
_FREE_KEYWORDS = ("免费", "无门票", "不收费", "无需门票")


def _parse_cost_string(raw: str) -> Tuple[int, bool]:
    """
    解析中文门票价格字符串，返回 (cost, unknown)

    实测格式（来自高德 MCP + GLM web search 提取结果）：
      '60元（旺季）/40元（淡季）' → (60, False)  取高值，宁可高估不低估
      '30元'                      → (30, False)
      '免费（需预约）'             → (0, False)   免费是确定信息
      '视具体项目而定'             → (0, True)    真正无法解析

    规则：
      1. 含免费关键词 → (0, False)
      2. 提取所有数字取最大值（旺季价，保守估算避免预算低估）
      3. 无法提取数字 → (0, True)
    """
    if not raw or not raw.strip():
        return 0, True

    text = raw.strip()

    # 规则 1：免费（确定信息，非未知）
    if any(kw in text for kw in _FREE_KEYWORDS):
        return 0, False

    # 规则 2：提取数字取最大值
    numbers = re.findall(r"\d+(?:\.\d+)?", text)
    if numbers:
        return int(max(float(n) for n in numbers)), False

    # 规则 3：无法解析
    return 0, True


def poi_cost(poi: dict) -> Tuple[int, bool]:
    """返回 (cost, unknown)"""
    raw = poi.get("estimated_cost")
    if raw is None:
        return 0, False
    if isinstance(raw, bool):
        # bool 是 int 子类，必须在 int 判断前拦截，否则 True → 1 元
        return 0, True
    if isinstance(raw, (int, float)):
        return int(raw), False
    if isinstance(raw, str):
        return _parse_cost_string(raw)
    # dict {unknown: true} 或其他类型
    return 0, True


def poi_duration_minutes(poi: dict) -> int:
    return poi.get("estimated_duration_minutes") or 0


def _priority_weight(priority: str) -> int:
    return {"must": 0, "nice": 1, "optional": 2}.get(priority, 2)


def _is_user_must_visit(poi: dict, must_visit: Optional[List[str]]) -> bool:
    """POI 是否命中用户显式指定的 must_visit（名称/ID 包含匹配）

    must_visit 项是用户输入的短名（"中山陵"），POI name 是高德全称（"中山陵景区"），
    用双向包含匹配。LLM 自标的 must 不在此列——用于排程冲突时优先保用户项（第五阶段）。
    """
    name = str(poi.get("name") or "")
    pid = str(poi.get("id") or "")
    for mv in (must_visit or []):
        if not isinstance(mv, str):
            continue
        mv = mv.strip()
        if not mv:
            continue
        if mv in name or (name and name in mv) or mv in pid:
            return True
    return False


def sort_pois_for_planning(pois: List[dict]) -> List[dict]:
    """排序：must > nice > optional，同优先级按 area，再按 confidence 降序"""
    return sorted(
        pois,
        key=lambda p: (
            _priority_weight(p.get("priority", "optional")),
            p.get("area", ""),
            -(p.get("confidence") or 0),
        ),
    )


def _to_hhmm(total_minutes: int) -> str:
    """分钟数转 HH:MM"""
    safe = max(0, int(total_minutes))
    hours = (safe // 60) % 24
    minutes = safe % 60
    return f"{hours:02d}:{minutes:02d}"


def _reason_for(poi: dict) -> str:
    priority_label = {
        "must": "必去",
        "nice": "推荐",
        "optional": "可选",
    }.get(poi.get("priority", "optional"), "可选")
    return f"{priority_label}：{poi.get('name', '')}（{poi.get('area', '')}）"


class _PlanDayContext:
    """单日排程上下文"""
    def __init__(self, travel_matrix: Optional[dict]):
        self.travel_matrix = travel_matrix
        self.current_minutes = 10 * 60  # 从 10:00 开始
        self.current_poi: Optional[dict] = None   # 第零期：从 current_area 改为 POI（便于取 id/location）
        self.cost_accumulator = 0
        self.warnings: List[str] = []
        self.blocks: List[dict] = []
        self.areas: set = set()
        self.placed_ids: set = set()

    @property
    def current_area(self) -> Optional[str]:
        """兼容属性：_pick_next_poi 仍按区域聚类选点"""
        return self.current_poi.get("area") if self.current_poi else None

    def travel_to(self, poi: dict) -> int:
        """计算从当前位置到目标 POI 的移动时间（不改变状态，供容量预判用）"""
        if self.current_poi is None:
            return 0
        return get_travel_minutes(self.travel_matrix, self.current_poi, poi)

    def push_block(self, poi: dict) -> None:
        travel = self.travel_to(poi)
        if travel > 0:
            self.current_minutes += travel
        duration = poi_duration_minutes(poi)
        start = _to_hhmm(self.current_minutes)
        self.current_minutes += duration
        end = _to_hhmm(self.current_minutes)
        cost, unknown = poi_cost(poi)
        self.cost_accumulator += cost
        if unknown:
            self.warnings.append(f"POI {poi.get('id')} 估算成本未知，按 0 计入预算")
        self.blocks.append({
            "poi_id": poi.get("id"),
            "start_time": start,
            "end_time": end,
            "reason": _reason_for(poi),
            "locked": False,
        })
        self.areas.add(poi.get("area", ""))
        self.placed_ids.add(poi.get("id"))
        self.current_poi = poi


def _new_day_context(travel_matrix: Optional[dict]) -> _PlanDayContext:
    return _PlanDayContext(travel_matrix)


def _finalize_day(day: int, ctx: _PlanDayContext) -> dict:
    """收尾单日"""
    total_minutes = 0
    for b in ctx.blocks:
        sh, sm = b["start_time"].split(":")
        eh, em = b["end_time"].split(":")
        total_minutes += (int(eh) * 60 + int(em)) - (int(sh) * 60 + int(sm))
    return {
        "day": day,
        "area_cluster": list(ctx.areas),
        "time_blocks": ctx.blocks,
        "estimated_total_cost": ctx.cost_accumulator,
        "estimated_total_minutes": total_minutes,
    }


def _pick_next_poi(
    must_queue: List[dict],
    all_pois: List[dict],
    current_area: Optional[str],
    placed_ids: set,
) -> Optional[dict]:
    """选择下一个 POI：同区域 must > 任意 must > 同区域非 must > 任意非 must"""
    # 同区域 must
    for p in must_queue:
        if p.get("area") == current_area and p.get("id") not in placed_ids:
            return p
    # 任意 must
    for p in must_queue:
        if p.get("id") not in placed_ids:
            return p
    # 同区域非 must
    for p in all_pois:
        if p.get("priority") != "must" and p.get("area") == current_area and p.get("id") not in placed_ids:
            return p
    # 任意非 must
    for p in all_pois:
        if p.get("priority") != "must" and p.get("id") not in placed_ids:
            return p
    return None


async def build_itinerary_from_data(
    session_id: str,
    trip_meta: dict,
    poi_list: dict,
    travel_matrix: Optional[dict] = None,
) -> dict:
    """
    真实排程内核（移植自 build-itinerary.ts:229）

    Args:
        session_id: 会话ID
        trip_meta: {city, days, pace, must_visit, budget, ...}
        poi_list: {city, pois: [...]}
        travel_matrix: 预取的 POI 级移动时间 {"a::b": minutes}（第零期新增）
                       为 None 时退化为经纬度兜底估算

    Returns:
        {itinerary: {...}, warnings: [...]}

    注：本函数不发网络请求。移动时间预取在研究阶段完成（trip_planner.py），
        保证排程仍为可测试的确定性纯逻辑。
    """
    warnings: List[str] = []
    city = poi_list.get("city") or trip_meta.get("city", "")
    pois_raw = poi_list.get("pois", [])

    # 移动时间数据源告知（三级降级可见）
    if not travel_matrix:
        with_loc = sum(1 for p in pois_raw if isinstance(p.get("location"), dict))
        if with_loc >= 2:
            warnings.append(
                "未提供预取的移动时间矩阵，已改用经纬度直线距离×路网系数估算（精度低于高德真实路线）"
            )
        else:
            warnings.append(
                "无移动时间矩阵且 POI 缺少经纬度，景点间移动按 0 分钟计算，实际耗时会显著高于估算"
            )

    daily_limit = PACE_DAILY_LIMIT_MINUTES.get(trip_meta.get("pace", "normal"), 480)

    sorted_pois = sort_pois_for_planning(pois_raw)
    full_day_pois = [p for p in sorted_pois if is_full_day_poi(p)]
    regular_pois = [p for p in sorted_pois if not is_full_day_poi(p)]

    days: List[dict] = []
    remaining_must = [p for p in regular_pois if p.get("priority") == "must"]

    # 排程分级（第五阶段）：用户显式 must_visit 命中的 POI 排最前，LLM 自标的 must 靠后。
    # 背景：prompt 旧规则允许 LLM 把偏好景点标 must，4 个 must 超 relaxed 容量时
    # 用户指定的夫子庙被 LLM 推荐的博物院挤掉。稳定排序保持原 area/confidence 次序。
    user_must_visit = trip_meta.get("must_visit") or []
    remaining_must.sort(key=lambda p: 0 if _is_user_must_visit(p, user_must_visit) else 1)

    # 全天项目各自单独成天
    for poi in full_day_pois:
        if len(days) >= trip_meta.get("days", 0):
            warnings.append(f"全天项目 {poi.get('id')} 因天数上限未排入")
            break
        ctx = _new_day_context(travel_matrix)
        ctx.push_block(poi)
        warnings.extend(ctx.warnings)
        days.append(_finalize_day(len(days) + 1, ctx))

    # 普通项目按区域聚类逐日放置
    #
    # placed_globally：跨天去重（第零期修复）
    #   ctx.placed_ids 每天随 context 重建，只能防止同一天重复，
    #   导致同一个 POI 可能出现在多天（实测 Day1/Day2 都排了 poi_foxiangge）。
    placed_globally: set = set()
    for d in days:
        for b in d.get("time_blocks", []):
            placed_globally.add(b.get("poi_id"))

    day_index = len(days)
    while day_index < trip_meta.get("days", 0):
        ctx = _new_day_context(travel_matrix)
        ctx.placed_ids = set(placed_globally)   # 继承已排 POI，避免跨天重复
        guard = 0
        while guard < 200:
            guard += 1
            candidate = _pick_next_poi(remaining_must, regular_pois, ctx.current_area, ctx.placed_ids)
            if not candidate:
                break
            travel = ctx.travel_to(candidate)
            used = ctx.current_minutes - 10 * 60
            if poi_duration_minutes(candidate) + travel > daily_limit - used:
                if candidate.get("priority") == "must" and not ctx.blocks:
                    ctx.push_block(candidate)
                    warnings.append(f"Day {day_index + 1} 必去项 {candidate.get('id')} 超出 pace 上限，已强制排入")
                    continue
                break
            ctx.push_block(candidate)
            remaining_must = [p for p in remaining_must if p.get("id") != candidate.get("id")]
        warnings.extend(ctx.warnings)
        placed_globally |= ctx.placed_ids   # 同步本日已排，供后续天去重
        days.append(_finalize_day(day_index + 1, ctx))
        day_index += 1

    # 仍有 must 未排：警告（第五阶段分级：用户指定必去 vs LLM 自标）
    for m in remaining_must:
        if _is_user_must_visit(m, user_must_visit):
            warnings.append(
                f"用户指定的必去景点 {m.get('name') or m.get('id')} 因天数或 pace 上限未排入，"
                f"建议减少其他景点或放宽节奏"
            )
        else:
            warnings.append(f"必去项 {m.get('id')} 因天数或 pace 上限未排入")

    # 空天告警（第零期续作新增）
    # 2 天行程只排出 1 天内容时，Day2 是空的 [{"day":2,"time_blocks":[]}]，
    # 原实现静默产出空天，用户不知道是 POI 不足还是排程问题。
    empty_days = [d["day"] for d in days if not d.get("time_blocks")]
    if empty_days:
        pois_count = len(pois_raw)
        recommended = trip_meta.get("days", 0) * 2
        warnings.append(
            f"Day {','.join(map(str, empty_days))} 未安排任何 POI。"
            f"原因可能是：候选 POI 数量不足（当前 {pois_count} 个，建议至少 {recommended} 个），"
            f"或所有 must POI 已在第 1 天排完且剩余 POI 耗时超出 pace 上限。"
            f"建议：增加 preferences 细化偏好，或在 must_visit 中补充更多景点。"
        )

    # 预算检查
    total_cost = sum(d["estimated_total_cost"] for d in days)
    budget = trip_meta.get("budget")
    if budget and total_cost > budget.get("amount", 0):
        warnings.append(
            f"行程估算总成本 {total_cost} 超出预算 {budget.get('amount')}（{budget.get('currency')}）"
        )

    itinerary = {
        "session_id": session_id,
        "city": poi_list.get("city") or trip_meta.get("city", ""),
        "version": 1,
        "days": days,
    }

    return {"itinerary": itinerary, "warnings": warnings}
