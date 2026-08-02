"""
高德地图 REST API 服务 - 直接调 REST，避免 MCP 启动开销和工具循环

H5a 阶段：3 个研究 Agent 用 GLM 知识生成结构化数据，再用本服务补充真实坐标/天气
"""
import httpx
from typing import Optional, List, Dict, Any

from app.config import settings


AMAP_REST_BASE = "https://restapi.amap.com/v3"


async def geocode(address: str, city: str = "") -> Optional[Dict[str, float]]:
    """
    地址转经纬度（maps_geo 等价）

    Returns: {longitude, latitude} 或 None
    """
    if not settings.amap_api_key:
        return None
    params = {"key": settings.amap_api_key, "address": address}
    if city:
        params["city"] = city
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{AMAP_REST_BASE}/geocode/geo", params=params)
        data = resp.json()
    if data.get("status") != "1" or not data.get("geocodes"):
        return None
    location = data["geocodes"][0].get("location", "")
    if not location:
        return None
    lng, lat = location.split(",")
    return {"longitude": float(lng), "latitude": float(lat)}


async def text_search(keywords: str, city: str = "", types: str = "") -> List[Dict[str, Any]]:
    """
    POI 搜索（maps_text_search 等价）

    Returns: POI 列表 [{id, name, address, location, typecode, ...}]
    """
    if not settings.amap_api_key:
        return []
    params = {
        "key": settings.amap_api_key,
        "keywords": keywords,
        "offset": 10,
        "page": 1,
        "extensions": "all",
    }
    if city:
        params["city"] = city
    if types:
        params["types"] = types
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{AMAP_REST_BASE}/place/text", params=params)
        data = resp.json()
    if data.get("status") != "1":
        return []
    return data.get("pois", [])


async def get_weather(city: str) -> List[Dict[str, Any]]:
    """
    查询城市天气预报（maps_weather 等价）

    Returns: 天气 casts 列表 [{date, day_weather, night_weather, day_temp, night_temp, ...}]
    """
    if not settings.amap_api_key:
        return []
    # 先用城市名查 adcode
    async with httpx.AsyncClient(timeout=10) as client:
        geo_resp = await client.get(
            f"{AMAP_REST_BASE}/geocode/geo",
            params={"key": settings.amap_api_key, "address": city},
        )
        geo_data = geo_resp.json()
    if geo_data.get("status") != "1" or not geo_data.get("geocodes"):
        return []
    adcode = geo_data["geocodes"][0].get("adcode", "")
    if not adcode:
        return []

    # 用 adcode 查天气
    async with httpx.AsyncClient(timeout=10) as client:
        weather_resp = await client.get(
            f"{AMAP_REST_BASE}/weather/weatherInfo",
            params={"key": settings.amap_api_key, "city": adcode, "extensions": "all"},
        )
        weather_data = weather_resp.json()
    if weather_data.get("status") != "1" or not weather_data.get("forecasts"):
        return []

    casts = weather_data["forecasts"][0].get("casts", [])
    # 转成统一格式
    result = []
    for c in casts:
        result.append({
            "date": c.get("date", ""),
            "day_weather": c.get("dayweather", ""),
            "night_weather": c.get("nightweather", ""),
            "day_temp": int(c.get("daytemp", 0) or 0),
            "night_temp": int(c.get("nighttemp", 0) or 0),
            "wind_direction": c.get("daywind", ""),
            "wind_power": c.get("daypower", ""),
        })
    return result
