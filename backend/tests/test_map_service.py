import pytest

from app.services.map_service import build_map_data


def poi(poi_id: str, name: str, location=None):
    return {"id": poi_id, "name": name, "location": location}


def test_build_map_data_keeps_time_block_order_and_transportation():
    result = build_map_data(
        {"city": "南京", "transportation": "公共交通"},
        {"city": "南京", "pois": [
            poi("p2", "夫子庙", {"longitude": 118.789, "latitude": 32.023}),
            poi("p1", "中山陵", {"longitude": 118.856, "latitude": 32.058}),
        ]},
        {"city": "南京", "days": [{"day": 1, "time_blocks": [
            {"poi_id": "p1", "start_time": "09:00", "end_time": "12:00"},
            {"poi_id": "p2", "start_time": "17:00", "end_time": "20:00"},
        ]}]},
    )

    assert [point.poi_id for point in result.days[0].points] == ["p1", "p2"]
    assert result.days[0].transportation == "公共交通"
    assert result.days[0].route_ready is True


def test_build_map_data_preserves_unmapped_points_without_failing_route():
    result = build_map_data(
        {"city": "北京"},
        {"city": "北京", "pois": [poi("p1", "故宫", None)]},
        {"city": "北京", "days": [{"day": 1, "time_blocks": [
            {"poi_id": "p1", "start_time": "10:00", "end_time": "12:00"},
        ]}]},
    )

    assert result.days[0].points == []
    assert result.days[0].unmapped_points == ["故宫"]
    assert result.days[0].route_ready is False


def test_build_map_data_ignores_invalid_coordinates_and_keeps_empty_days():
    result = build_map_data(
        {"city": "深圳"},
        {"city": "深圳", "pois": [
            poi("bad", "错误坐标", {"longitude": 300, "latitude": 22}),
        ]},
        {"city": "深圳", "days": [
            {"day": 1, "time_blocks": []},
            {"day": 2, "time_blocks": [{"poi_id": "bad"}]},
        ]},
    )

    assert len(result.days) == 2
    assert result.days[0].points == []
    assert result.days[1].unmapped_points == ["错误坐标"]
