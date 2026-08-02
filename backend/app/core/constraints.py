"""
业务约束校验 - 移植自 src/verifier/check-constraints.ts

检查项：
  - POI 存在性：itinerary 中引用的 poi_id 必须在 poiList 中
  - must 覆盖：tripMeta.must_visit 里的每个 POI 至少出现在行程某天（含别名）
  - POI 重复：同一天内同一 POI 出现多次
  - 每日超时：每日 estimated_total_minutes 超过 pace 上限（warning）
  - 预算超限：行程总成本超过 tripMeta.budget.amount（warning）
  - 时间倒挂/重叠：同一天内 start >= end，或时间块互相重叠
"""
import re
from typing import Dict, List, Tuple, Optional

PACE_DAILY_LIMIT_MINUTES = {
    "relaxed": 360,
    "normal": 480,
    "packed": 600,
}

_HHMM_RE = re.compile(r"^(\d{2}):(\d{2})$")


def _parse_hhmm(value: str) -> Optional[int]:
    """HH:MM 转分钟数，非法返回 None"""
    m = _HHMM_RE.match(value)
    if not m:
        return None
    h = int(m.group(1))
    minute = int(m.group(2))
    if not (0 <= h <= 23 and 0 <= minute <= 59):
        return None
    return h * 60 + minute


def _block_minutes(block: dict) -> Optional[int]:
    """时间块的时长（分钟），非法/倒挂返回 None"""
    start = _parse_hhmm(block.get("start_time", ""))
    end = _parse_hhmm(block.get("end_time", ""))
    if start is None or end is None:
        return None
    if end < start:
        return None
    return end - start


def _is_overlap(a: dict, b: dict) -> bool:
    """两个时间块是否重叠"""
    as_ = _parse_hhmm(a.get("start_time", ""))
    ae = _parse_hhmm(a.get("end_time", ""))
    bs = _parse_hhmm(b.get("start_time", ""))
    be = _parse_hhmm(b.get("end_time", ""))
    if None in (as_, ae, bs, be):
        return False
    return as_ < be and bs < ae


def _find_poi(poi_list: dict, poi_id: str) -> Optional[dict]:
    """从 poi_list 找 POI"""
    for p in poi_list.get("pois", []):
        if p.get("id") == poi_id:
            return p
    return None


def check_constraints(itinerary: dict, poi_list: dict, trip_meta: dict) -> Dict[str, List[str]]:
    """
    行程业务约束检查（schema 之外）

    Args:
        itinerary: {days: [{day, time_blocks: [...], ...}]}
        poi_list: {city, pois: [...]}
        trip_meta: {pace, must_visit, budget, ...}

    Returns:
        {"errors": [...], "warnings": [...]}
    """
    errors: List[str] = []
    warnings: List[str] = []

    daily_limit = PACE_DAILY_LIMIT_MINUTES.get(trip_meta.get("pace", "normal"), 480)

    # 收集行程中出现的 POI id 和别名
    placed_ids: set = set()
    placed_aliases: set = set()
    for day in itinerary.get("days", []):
        for block in day.get("time_blocks", []):
            placed_ids.add(block.get("poi_id"))
            poi = _find_poi(poi_list, block.get("poi_id"))
            if poi and poi.get("aliases"):
                for alias in poi["aliases"]:
                    placed_aliases.add(alias)

    for day in itinerary.get("days", []):
        seen_in_day: set = set()
        for block in day.get("time_blocks", []):
            poi = _find_poi(poi_list, block.get("poi_id"))

            # 存在性
            if not poi:
                errors.append(f"POI {block.get('poi_id')} 不在 poi-list 中（Day {day.get('day')}）")

            # 同日重复
            if block.get("poi_id") in seen_in_day:
                errors.append(f"POI {block.get('poi_id')} 在 Day {day.get('day')} 重复出现")
            seen_in_day.add(block.get("poi_id"))

            # 时间倒挂
            minutes = _block_minutes(block)
            if minutes is None:
                errors.append(
                    f"Day {day.get('day')} 时间块 {block.get('poi_id')} 时间非法或倒挂"
                    f"（{block.get('start_time')}-{block.get('end_time')}）"
                )

        # 同日重叠
        blocks = day.get("time_blocks", [])
        for i in range(len(blocks)):
            for j in range(i + 1, len(blocks)):
                if _is_overlap(blocks[i], blocks[j]):
                    errors.append(
                        f"Day {day.get('day')} 时间块 {blocks[i].get('poi_id')} "
                        f"与 {blocks[j].get('poi_id')} 重叠"
                    )

        # 每日超时（warning）
        if day.get("estimated_total_minutes", 0) > daily_limit:
            warnings.append(
                f"Day {day.get('day')} 总时长 {day.get('estimated_total_minutes')} 分钟"
                f"超过 {trip_meta.get('pace')} 节奏上限 {daily_limit} 分钟"
            )

    # must 覆盖
    for must_id in trip_meta.get("must_visit", []):
        if must_id not in placed_ids and must_id not in placed_aliases:
            errors.append(f"必去项 {must_id} 未在行程中出现")

    # 预算超限（warning）
    budget = trip_meta.get("budget")
    if budget:
        total_cost = sum(d.get("estimated_total_cost", 0) for d in itinerary.get("days", []))
        if total_cost > budget.get("amount", 0):
            warnings.append(
                f"行程总成本 {total_cost} 超过预算 {budget.get('amount')}（{budget.get('currency')}）"
            )

    return {"errors": errors, "warnings": warnings}
