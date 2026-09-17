"""每日热门旅游笔记缓存。失败时保留上一份可用结果。"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)
_QUERIES = ["近期热门旅行攻略", "周末旅行热门", "citywalk 旅行热门"]
_DESTINATIONS = ("北京", "上海", "南京", "杭州", "成都", "重庆", "长沙", "西安", "青岛", "厦门", "大理", "丽江", "三亚", "泉州", "苏州", "呼伦贝尔", "千岛湖", "香格里拉", "黄山", "新疆", "日本", "京都")


def _note_tags(title: str, content: str) -> list[str]:
    """不额外调用模型，用可解释规则提炼目的地和主题标签。"""
    text = f"{title} {content}"
    tags = [city for city in _DESTINATIONS if city in text][:1]
    topics = (
        ("亲子", ("亲子", "带娃", "孩子", "家庭")),
        ("酒店度假", ("酒店", "staycation", "民宿")),
        ("城市漫游", ("citywalk", "胡同", "街区", "古城")),
        ("自然风光", ("草原", "雪山", "海边", "山", "湖", "日落")),
        ("旅行攻略", ("攻略", "路线", "避坑", "一日游")),
        ("美食", ("美食", "小吃", "餐厅", "咖啡")),
    )
    for label, keywords in topics:
        if any(keyword.lower() in text.lower() for keyword in keywords):
            tags.append(label)
            break
    return (tags or ["旅行灵感"])[:2]


def _cache_path() -> Path:
    return Path(settings.context_dir) / "trending-notes.json"


async def refresh_trending_notes() -> dict[str, Any]:
    from app.tools.xhs_note_search_tool import XhsNoteSearchTool

    tool = XhsNoteSearchTool()
    gathered: list[dict[str, Any]] = []
    for query in _QUERIES:
        result = await tool._fetch(query, 5)
        if result.get("success"):
            gathered.extend(result.get("notes") or [])

    seen: set[str] = set()
    notes: list[dict[str, Any]] = []
    for note in gathered:
        note_id = str(note.get("note_id") or note.get("title") or "")
        if not note_id or note_id in seen or not note.get("note_url"):
            continue
        seen.add(note_id)
        content = str(note.get("desc") or "").strip()
        title = str(note.get("title") or "旅行灵感")
        notes.append({
            "id": note_id,
            "title": title,
            "summary": content,
            "tags": _note_tags(title, content),
            "cover_url": str(note.get("cover_url") or ""),
            "note_url": str(note["note_url"]),
        })
        if len(notes) == 5:
            break
    if not notes:
        raise RuntimeError("小红书未返回可展示的热门旅游笔记")

    payload = {"schema_version": 2, "updated_at": datetime.now().isoformat(timespec="seconds"), "notes": notes}
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("热门旅游笔记已刷新：%s 条", len(notes))
    return payload


def get_trending_notes() -> dict[str, Any]:
    try:
        payload = json.loads(_cache_path().read_text(encoding="utf-8"))
        for note in payload.get("notes", []):
            if not note.get("tags"):
                note["tags"] = _note_tags(str(note.get("title") or ""), str(note.get("summary") or ""))
        return payload
    except (FileNotFoundError, json.JSONDecodeError):
        return {"schema_version": 2, "updated_at": None, "notes": []}


async def run_daily_trending_refresh(stop_event: asyncio.Event) -> None:
    """在服务进程内每天本地时间 18:00 刷新，重启后继续计算下一次执行时间。"""
    while not stop_event.is_set():
        now = datetime.now()
        target = now.replace(hour=18, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=(target - now).total_seconds())
            continue
        except asyncio.TimeoutError:
            pass
        try:
            await refresh_trending_notes()
        except Exception as exc:
            logger.warning("18:00 热门旅游笔记刷新失败，保留旧缓存：%s", exc)
