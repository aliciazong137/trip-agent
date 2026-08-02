"""
确定性排程内核 - 移植自 src/tools/build-itinerary.ts

保留算法：
  - 排序：must > nice > optional，同优先级按 area 聚类，再按 confidence 降序
  - 天分配：先放 must，按 pace 控制每日上限（relaxed 360 / normal 480 / packed 600）
  - 时间：从 10:00 起，按 POI 时长 + 区域移动时间累加
  - 成本：累加 POI cost；{unknown:true} 计 0 并产出 warning
  - 全天项目（duration >= 360 或 constraints 含"全天项目"）单独占一天

移植注意：
  - 用 dict 表达 Poi/TripMeta/PoiList，不强依赖 Pydantic（H4 工具层做转换）
  - 保持与 TS 版完全一致的输出，便于测试对齐
"""
import json
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

PACE_DAILY_LIMIT_MINUTES: Dict[str, int] = {
    "relaxed": 360,
    "normal": 480,
    "packed": 600,
}

FULL_DAY_THRESHOLD_MINUTES = 360
FULL_DAY_CONSTRAINT = "全天项目"

# data/travel-time/{city}-area-matrix.json
TRAVEL_TIME_ROOT = Path(__file__).resolve().parent.parent.parent / "data" / "travel-time"

_matrix_cache: Dict[str, Optional[dict]] = {}


async def load_area_matrix(city: str) -> Optional[dict]:
    """加载区域移动时间矩阵"""
    cache_key = city.lower()
    if cache_key in _matrix_cache:
        return _matrix_cache[cache_key]
    matrix_path = TRAVEL_TIME_ROOT / f"{cache_key}-area-matrix.json"
    if not matrix_path.exists():
        _matrix_cache[cache_key] = None
        return None
    try:
        matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
        _matrix_cache[cache_key] = matrix
        return matrix
    except Exception:
        _matrix_cache[cache_key] = None
        return None


def get_travel_time_minutes(matrix: Optional[dict], from_area: Optional[str], to_area: Optional[str]) -> int:
    """从矩阵取两个区域间的移动时间"""
    if not matrix or not from_area or not to_area:
        return 0
    matrix_minutes = matrix.get("matrix_minutes", {})
    row = matrix_minutes.get(from_area)
    if not row:
        return 0
    value = row.get(to_area)
    return value if isinstance(value, (int, float)) else 0


def is_full_day_poi(poi: dict) -> bool:
    """判断是否全天项目"""
    duration = poi.get("estimated_duration_minutes") or 0
    if duration >= FULL_DAY_THRESHOLD_MINUTES:
        return True
    constraints = poi.get("constraints") or []
    if any(FULL_DAY_CONSTRAINT in c for c in constraints):
        return True
    return False


def poi_cost(poi: dict) -> Tuple[int, bool]:
    """返回 (cost, unknown)"""
    raw = poi.get("estimated_cost")
    if raw is None:
        return 0, False
    if isinstance(raw, (int, float)):
        return int(raw), False
    # dict {unknown: true}
    return 0, True


def poi_duration_minutes(poi: dict) -> int:
    return poi.get("estimated_duration_minutes") or 0


def _priority_weight(priority: str) -> int:
    return {"must": 0, "nice": 1, "optional": 2}.get(priority, 2)


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
    def __init__(self, matrix: Optional[dict]):
        self.matrix = matrix
        self.current_minutes = 10 * 60  # 从 10:00 开始
        self.current_area: Optional[str] = None
        self.cost_accumulator = 0
        self.warnings: List[str] = []
        self.blocks: List[dict] = []
        self.areas: set = set()
        self.placed_ids: set = set()

    def push_block(self, poi: dict) -> None:
        travel = get_travel_time_minutes(self.matrix, self.current_area, poi.get("area"))
        if self.current_area and travel > 0:
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
        self.current_area = poi.get("area")


def _new_day_context(matrix: Optional[dict]) -> _PlanDayContext:
    return _PlanDayContext(matrix)


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


async def build_itinerary_from_data(session_id: str, trip_meta: dict, poi_list: dict) -> dict:
    """
    真实排程内核（移植自 build-itinerary.ts:229）

    Args:
        session_id: 会话ID
        trip_meta: {city, days, pace, must_visit, budget, ...}
        poi_list: {city, pois: [...]}

    Returns:
        {itinerary: {...}, warnings: [...]}
    """
    warnings: List[str] = []
    city = poi_list.get("city") or trip_meta.get("city", "")
    matrix = await load_area_matrix(city)
    if not matrix:
        warnings.append(f"未找到 {city} 的区域移动时间矩阵，跨区域移动按 0 分钟估算")

    daily_limit = PACE_DAILY_LIMIT_MINUTES.get(trip_meta.get("pace", "normal"), 480)

    sorted_pois = sort_pois_for_planning(poi_list.get("pois", []))
    full_day_pois = [p for p in sorted_pois if is_full_day_poi(p)]
    regular_pois = [p for p in sorted_pois if not is_full_day_poi(p)]

    days: List[dict] = []
    remaining_must = [p for p in regular_pois if p.get("priority") == "must"]

    # 全天项目各自单独成天
    for poi in full_day_pois:
        if len(days) >= trip_meta.get("days", 0):
            warnings.append(f"全天项目 {poi.get('id')} 因天数上限未排入")
            break
        ctx = _new_day_context(matrix)
        ctx.push_block(poi)
        warnings.extend(ctx.warnings)
        days.append(_finalize_day(len(days) + 1, ctx))

    # 普通项目按区域聚类逐日放置
    day_index = len(days)
    while day_index < trip_meta.get("days", 0):
        ctx = _new_day_context(matrix)
        guard = 0
        while guard < 200:
            guard += 1
            candidate = _pick_next_poi(remaining_must, regular_pois, ctx.current_area, ctx.placed_ids)
            if not candidate:
                break
            travel = get_travel_time_minutes(matrix, ctx.current_area, candidate.get("area"))
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
        days.append(_finalize_day(day_index + 1, ctx))
        day_index += 1

    # 仍有 must 未排：警告
    for m in remaining_must:
        warnings.append(f"必去项 {m.get('id')} 因天数或 pace 上限未排入")

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
