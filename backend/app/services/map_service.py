"""从已保存行程构建前端地图数据，不调用 LLM 或高德服务。"""
from __future__ import annotations

from typing import Any, Iterable

from app.models.schemas import DayRouteMap, MapData, MapPoint, Location


def _as_location(value: Any) -> Location | None:
    """只接受有限且在地理范围内的经纬度。"""
    if isinstance(value, Location):
        return value
    if not isinstance(value, dict):
        return None
    try:
        location = Location(
            longitude=float(value["longitude"]),
            latitude=float(value["latitude"]),
        )
    except (KeyError, TypeError, ValueError):
        return None
    return location if location.longitude == location.longitude and location.latitude == location.latitude else None


def _iter_pois(poi_list: dict[str, Any] | None) -> Iterable[dict[str, Any]]:
    if not isinstance(poi_list, dict):
        return []
    return (poi for poi in poi_list.get("pois", []) if isinstance(poi, dict))


def build_map_data(
    trip_meta: dict[str, Any] | None,
    poi_list: dict[str, Any] | None,
    itinerary: dict[str, Any] | None,
) -> MapData:
    """将 session 中的 POI 和确定性排程转换为按天的地图 payload。

    顺序严格跟随 `time_blocks`。坐标缺失或非法的 POI 会进入
    `unmapped_points`，其余点仍可用于地图和时间轴展示。
    """
    trip_meta = trip_meta or {}
    poi_list = poi_list or {}
    itinerary = itinerary or {}
    poi_by_id = {
        str(poi.get("id")): poi
        for poi in _iter_pois(poi_list)
        if poi.get("id") is not None
    }
    city = str(itinerary.get("city") or poi_list.get("city") or trip_meta.get("city") or "")
    default_transportation = trip_meta.get("transportation")
    result_days: list[DayRouteMap] = []

    for raw_day in itinerary.get("days", []):
        if not isinstance(raw_day, dict):
            continue
        day_number = int(raw_day.get("day") or len(result_days) + 1)
        points: list[MapPoint] = []
        unmapped: list[str] = []
        for index, block in enumerate(raw_day.get("time_blocks", []), start=1):
            if not isinstance(block, dict):
                continue
            poi_id = str(block.get("poi_id") or "")
            poi = poi_by_id.get(poi_id)
            name = str((poi or {}).get("name") or poi_id or f"景点 {index}")
            location = _as_location((poi or {}).get("location"))
            if location is None:
                unmapped.append(name)
                continue
            points.append(MapPoint(
                order=index,
                poi_id=poi_id,
                name=name,
                location=location,
                start_time=block.get("start_time"),
                end_time=block.get("end_time"),
            ))
        transportation = raw_day.get("transportation") or default_transportation
        result_days.append(DayRouteMap(
            day=day_number,
            city=city,
            transportation=transportation,
            points=points,
            route_ready=len(points) >= 2,
            unmapped_points=unmapped,
        ))

    return MapData(city=city, days=result_days)
