"""
移动时间矩阵 - 用高德 maps_distance 预取 POI 两两耗时（第零期新增）

替代原手写区域矩阵方案。原方案问题：
  - 区级粗粒度（东城区内统一 10 分钟，但故宫→国博 15 分钟、天坛→国博 30 分钟）
  - 每个城市需手写 N² 数据，覆盖不足
  - `TRAVEL_TIME_ROOT` 路径算错，所有矩阵从未生效（移动时间恒为 0）

新方案三级降级：
  1. 预取：研究阶段用 maps_distance 拿真实路网 duration，存 session
  2. 兜底：POI 经纬度算 Haversine 直线距离 × 路网系数 ÷ 平均时速
  3. 最后：0 分钟（无 location 时，调用方产出 warning）

架构约束：
  预取在研究阶段（有网络 I/O），排程阶段只读矩阵不发请求，
  保证 build_itinerary_from_data 仍是可测试的确定性纯逻辑。
"""
import asyncio
import json
import math
import re
from typing import Any, Dict, List, Optional

# Haversine 兜底参数
#
# 分段速度模型：单一平均时速在远郊和市区都失真。
#   实测偏差（25 km/h 单速模型）：
#     故宫→八达岭（直线 54km）估 195 分钟，实际高德约 90-120 分钟 → 高估 60-100%
#       原因：远郊走高速，实际时速 60-80 km/h
#     故宫→国博（直线 1.5km）估 5 分钟，实际约 15 分钟 → 低估
#       原因：忽略了步行、等车、换乘的固定开销
#
# 故改为：固定开销 + 按距离分段的时速
ROAD_FACTOR = 1.4              # 路网系数：实际路径 / 直线距离
FIXED_OVERHEAD_MINUTES = 10    # 固定开销：步行到站、等车、换乘
EARTH_RADIUS_KM = 6371.0

# (距离上限 km, 平均时速 km/h)
_SPEED_TIERS = (
    (3.0, 12.0),     # 3km 内：步行/短驳为主
    (10.0, 20.0),    # 市区中距离：地铁+步行
    (30.0, 30.0),    # 市区远距离/近郊：地铁快线、城市快速路
    (float("inf"), 55.0),   # 远郊：高速/城际铁路
)

# 同一 POI 之间的移动时间（同点无需移动）
SAME_POI_MINUTES = 0


def _speed_for_distance(road_km: float) -> float:
    """按路程距离取对应时速（分段模型）"""
    for limit, speed in _SPEED_TIERS:
        if road_km <= limit:
            return speed
    return _SPEED_TIERS[-1][1]


def matrix_key(poi_a_id: str, poi_b_id: str) -> str:
    """矩阵键：poi_a::poi_b（有向，双向都存以便 O(1) 查询）"""
    return f"{poi_a_id}::{poi_b_id}"


def _get_location(poi: dict) -> Optional[tuple]:
    """提取 (longitude, latitude)，缺失返回 None"""
    loc = poi.get("location")
    if not isinstance(loc, dict):
        return None
    lng = loc.get("longitude")
    lat = loc.get("latitude")
    if lng is None or lat is None:
        return None
    try:
        return float(lng), float(lat)
    except (TypeError, ValueError):
        return None


def haversine_km(loc_a: tuple, loc_b: tuple) -> float:
    """两个 (lng, lat) 之间的球面直线距离（公里）"""
    lng1, lat1 = loc_a
    lng2, lat2 = loc_b
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lng2 - lng1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def haversine_minutes(
    poi_a: dict,
    poi_b: dict,
    road_factor: float = ROAD_FACTOR,
) -> Optional[int]:
    """
    经纬度兜底估算移动时间（分钟）

    模型：固定开销 + 路程 / 分段时速
      路程 = 直线距离 × 路网系数
      时速按路程分段（3km内 12km/h ... 30km外 55km/h）

    Returns:
        分钟数；任一 POI 缺 location 时返回 None（由调用方决定降级策略）
    """
    loc_a = _get_location(poi_a)
    loc_b = _get_location(poi_b)
    if not loc_a or not loc_b:
        return None
    straight_km = haversine_km(loc_a, loc_b)
    road_km = straight_km * road_factor
    speed = _speed_for_distance(road_km)
    minutes = FIXED_OVERHEAD_MINUTES + road_km / speed * 60
    return int(round(minutes))


def get_travel_minutes(travel_matrix: Optional[dict], poi_a: dict, poi_b: dict) -> int:
    """
    查询两个 POI 间移动时间（分钟），三级降级

    Args:
        travel_matrix: 预取矩阵 {"poi_a::poi_b": minutes}，可为 None
        poi_a: 起点 POI（需含 id，兜底时需含 location）
        poi_b: 终点 POI

    Returns:
        分钟数。无预取数据且无 location 时返回 0
    """
    id_a = poi_a.get("id")
    id_b = poi_b.get("id")

    if id_a and id_b and id_a == id_b:
        return SAME_POI_MINUTES

    # 1. 预取矩阵
    if travel_matrix and id_a and id_b:
        val = travel_matrix.get(matrix_key(id_a, id_b))
        if isinstance(val, (int, float)):
            return int(val)

    # 2. 经纬度兜底
    fallback = haversine_minutes(poi_a, poi_b)
    if fallback is not None:
        return fallback

    # 3. 无数据
    return 0


def _parse_distance_results(raw: Any) -> List[dict]:
    """
    从 maps_distance 返回值提取 results

    HelloAgents 的 MCPTool.run 返回带前缀的字符串，实测格式：
        工具 'maps_distance' 执行结果:
        {
          "results": [{"origin_id": "1", "dest_id": "1",
                       "distance": "6104", "duration": "1441"}]
        }
    直接 json.loads 会失败，需先截取 JSON 主体。
    同时兼容工具直接返回 dict 的情况。
    """
    if raw is None:
        return []

    data = raw
    if isinstance(raw, str):
        # 截取第一个 { 到最后一个 } 之间的 JSON 主体，跳过前缀说明文本
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return []
        try:
            data = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return []

    if not isinstance(data, dict):
        return []
    results = data.get("results")
    return results if isinstance(results, list) else []


async def prefetch_travel_matrix(
    pois: List[dict],
    amap_tool: Any = None,
    max_pois: int = 12,
) -> Dict[str, int]:
    """
    用高德 maps_distance 预取 POI 两两移动时间

    调用次数为 N-1（不是 N²）：每次把「之前所有点」作为 origins，
    当前点作为 destination，一次拿回多个 duration。

    Args:
        pois: POI 列表，需含 id 和 location
        amap_tool: 高德 MCPTool 实例；None 时跳过预取（退化为经纬度兜底）
        max_pois: POI 数上限，超出部分只用经纬度兜底（控制调用次数与耗时）

    Returns:
        {"poi_a::poi_b": minutes} 双向都存。预取失败时返回已获得的部分
    """
    matrix: Dict[str, int] = {}

    # 只对有 location 的 POI 预取
    valid = [p for p in pois if p.get("id") and _get_location(p)]
    if len(valid) < 2 or amap_tool is None:
        return matrix

    valid = valid[:max_pois]

    for i in range(1, len(valid)):
        dest = valid[i]
        origins_pois = valid[:i]
        dest_loc = _get_location(dest)
        if not dest_loc:
            continue

        origins_str = "|".join(
            f"{_get_location(p)[0]},{_get_location(p)[1]}" for p in origins_pois
        )
        dest_str = f"{dest_loc[0]},{dest_loc[1]}"

        try:
            # MCPTool.run({"tool_name": ..., "arguments": {...}})，同步方法丢到线程池
            raw = await asyncio.to_thread(
                amap_tool.run,
                {
                    "tool_name": "maps_distance",
                    "arguments": {
                        "origins": origins_str,
                        "destination": dest_str,
                        "type": "1",   # 1=驾车
                    },
                },
            )
        except Exception:
            # 单次调用失败不中断整体预取，缺失项由经纬度兜底
            continue

        results = _parse_distance_results(raw)
        if not results:
            continue

        # 高德 origin_id 从 1 开始，对应 origins_pois 的下标 origin_id-1
        for item in results:
            try:
                origin_idx = int(item.get("origin_id", 0)) - 1
                duration_sec = float(item.get("duration", 0))
            except (TypeError, ValueError):
                continue
            if not (0 <= origin_idx < len(origins_pois)):
                continue
            if duration_sec <= 0:
                continue
            minutes = int(round(duration_sec / 60))
            src_id = origins_pois[origin_idx].get("id")
            dst_id = dest.get("id")
            if src_id and dst_id:
                matrix[matrix_key(src_id, dst_id)] = minutes
                matrix[matrix_key(dst_id, src_id)] = minutes  # 双向

    return matrix
