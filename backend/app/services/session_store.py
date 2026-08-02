"""
Session 文件存储 - 移植自 src/context/store.ts

保留设计：
  - sessionId 严格校验（两层防护：assertValidSessionId + resolve 后仍在 CONTEXT_ROOT 内）
  - 原子写（tmp + rename，POSIX 原子）
  - search-cache（resultId 路径穿越防护）
  - asyncio CancelledError 支持（协作式取消，移植自 AbortSignal）

session 目录结构：
  context/{sessionId}/
    session.json          # SessionState
    trip-meta.json        # TripMeta
    poi-list.json         # PoiList
    itinerary.json        # Itinerary
    guides/
      raw.md              # 攻略原文
    search-cache/
      {resultId}.json     # 搜索结果缓存
    run.log               # 工具调用日志（H4 用）
"""
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Optional

import aiofiles

from app.config import settings
from app.utils.ids import assert_valid_session_id, is_valid_result_id


def _context_root() -> Path:
    """context 绝对路径"""
    return settings.context_dir


def _session_dir(session_id: str) -> Path:
    """
    解析 sessionId 到 session 目录，两层防护：
    1. assert_valid_session_id 严格校验字符集/长度（防 ../、绝对路径、空字符串）
    2. resolve 后确认仍在 CONTEXT_ROOT 之内（防御性，未来即使放宽 ID 规则也不越界）
    """
    assert_valid_session_id(session_id)

    root = _context_root().resolve()
    path = (root / session_id).resolve()

    if path != root and not str(path).startswith(str(root) + os.sep):
        raise ValueError("sessionId resolves outside context root")

    return path


def _create_abort_error() -> Exception:
    """统一的取消错误"""
    err = Exception("The operation was aborted")
    err.name = "AbortError"  # type: ignore[attr-defined]
    return err


async def _atomic_write(file_path: Path, content: str, signal: Optional[asyncio.Event] = None) -> None:
    """
    原子写：写到临时文件再 rename（POSIX 原子）。

    signal 支持协作式取消：
      - 写入前检查 signal.is_set()
      - rename 前再次检查
      - finally 清理 tmp
    注意：rename 本身不可取消，若 abort 发生在 rename 开始后无法回滚。
    """
    if signal is not None and signal.is_set():
        raise _create_abort_error()

    file_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = file_path.with_suffix(f".{os.getpid()}.{int(time.time() * 1000)}.tmp")

    try:
        async with aiofiles.open(tmp, "w", encoding="utf-8") as f:
            await f.write(content)

        if signal is not None and signal.is_set():
            raise _create_abort_error()

        tmp.replace(file_path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


# ─── Session 生命周期 ──────────────────────────────────────────────────────────

async def create_session(session_id: str, trip_meta: dict, guide_text: str = "") -> dict:
    """
    创建 session，初始化所有业务文件。

    Args:
        session_id: sess_ + 12位hex
        trip_meta: TripMeta dict
        guide_text: 攻略原文（可选）

    Returns:
        SessionFiles dict
    """
    assert_valid_session_id(session_id)

    _context_root().mkdir(parents=True, exist_ok=True)
    session_dir = _session_dir(session_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "guides").mkdir(parents=True, exist_ok=True)
    (session_dir / "search-cache").mkdir(parents=True, exist_ok=True)

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    session = {
        "session_id": session_id,
        "phase": "intake",
        "intent": "new_plan",
        "completed": [],
        "pending_questions": [],
        "confirmations": [],
        "updated_at": now,
    }
    poi_list = {"city": trip_meta.get("city", ""), "pois": []}
    itinerary = {
        "session_id": session_id,
        "city": trip_meta.get("city", ""),
        "days": [],
        "version": 1,
    }

    await _atomic_write(session_dir / "session.json", json.dumps(session, ensure_ascii=False, indent=2))
    await _atomic_write(session_dir / "trip-meta.json", json.dumps(trip_meta, ensure_ascii=False, indent=2))
    await _atomic_write(session_dir / "poi-list.json", json.dumps(poi_list, ensure_ascii=False, indent=2))
    await _atomic_write(session_dir / "itinerary.json", json.dumps(itinerary, ensure_ascii=False, indent=2))

    if guide_text:
        guides_raw = session_dir / "guides" / "raw.md"
        async with aiofiles.open(guides_raw, "w", encoding="utf-8") as f:
            await f.write(guide_text)

    return {
        "session_id": session_id,
        "trip_meta": trip_meta,
        "session": session,
        "poi_list": poi_list,
        "itinerary": itinerary,
        "guide_text": guide_text,
    }


async def load_session(session_id: str) -> Optional[dict]:
    """加载完整 session（所有业务文件）"""
    assert_valid_session_id(session_id)
    session_dir = _session_dir(session_id)
    try:
        async with aiofiles.open(session_dir / "session.json", encoding="utf-8") as f:
            session = json.loads(await f.read())
        async with aiofiles.open(session_dir / "trip-meta.json", encoding="utf-8") as f:
            trip_meta = json.loads(await f.read())
        async with aiofiles.open(session_dir / "poi-list.json", encoding="utf-8") as f:
            poi_list = json.loads(await f.read())
        async with aiofiles.open(session_dir / "itinerary.json", encoding="utf-8") as f:
            itinerary = json.loads(await f.read())
        return {
            "session_id": session_id,
            "session": session,
            "trip_meta": trip_meta,
            "poi_list": poi_list,
            "itinerary": itinerary,
        }
    except FileNotFoundError:
        return None


# ─── 单文件 save/load ──────────────────────────────────────────────────────────

async def save_itinerary(session_id: str, itinerary: dict, signal: Optional[asyncio.Event] = None) -> None:
    assert_valid_session_id(session_id)
    await _atomic_write(_session_dir(session_id) / "itinerary.json", json.dumps(itinerary, ensure_ascii=False, indent=2), signal)


async def save_trip_meta(session_id: str, trip_meta: dict, signal: Optional[asyncio.Event] = None) -> None:
    assert_valid_session_id(session_id)
    await _atomic_write(_session_dir(session_id) / "trip-meta.json", json.dumps(trip_meta, ensure_ascii=False, indent=2), signal)


async def save_poi_list(session_id: str, poi_list: dict, signal: Optional[asyncio.Event] = None) -> None:
    assert_valid_session_id(session_id)
    await _atomic_write(_session_dir(session_id) / "poi-list.json", json.dumps(poi_list, ensure_ascii=False, indent=2), signal)


async def load_trip_meta(session_id: str) -> Optional[dict]:
    assert_valid_session_id(session_id)
    try:
        async with aiofiles.open(_session_dir(session_id) / "trip-meta.json", encoding="utf-8") as f:
            return json.loads(await f.read())
    except FileNotFoundError:
        return None


async def load_poi_list(session_id: str) -> Optional[dict]:
    assert_valid_session_id(session_id)
    try:
        async with aiofiles.open(_session_dir(session_id) / "poi-list.json", encoding="utf-8") as f:
            return json.loads(await f.read())
    except FileNotFoundError:
        return None


async def load_itinerary(session_id: str) -> Optional[dict]:
    assert_valid_session_id(session_id)
    try:
        async with aiofiles.open(_session_dir(session_id) / "itinerary.json", encoding="utf-8") as f:
            return json.loads(await f.read())
    except FileNotFoundError:
        return None


async def save_travel_matrix(
    session_id: str, travel_matrix: dict, signal: Optional[asyncio.Event] = None
) -> None:
    """
    保存预取的 POI 级移动时间矩阵（第零期新增）

    格式：{"poi_a::poi_b": minutes, ...}（双向都存）
    来源：高德 maps_distance，在研究阶段预取，供确定性排程读取
    """
    assert_valid_session_id(session_id)
    await _atomic_write(
        _session_dir(session_id) / "travel-matrix.json",
        json.dumps(travel_matrix, ensure_ascii=False, indent=2),
        signal,
    )


async def load_travel_matrix(session_id: str) -> Optional[dict]:
    """读取移动时间矩阵；不存在时返回 None（排程会退化为经纬度兜底）"""
    assert_valid_session_id(session_id)
    try:
        async with aiofiles.open(
            _session_dir(session_id) / "travel-matrix.json", encoding="utf-8"
        ) as f:
            return json.loads(await f.read())
    except FileNotFoundError:
        return None


async def load_session_state(session_id: str) -> Optional[dict]:
    assert_valid_session_id(session_id)
    try:
        async with aiofiles.open(_session_dir(session_id) / "session.json", encoding="utf-8") as f:
            return json.loads(await f.read())
    except FileNotFoundError:
        return None


async def update_session_phase(session_id: str, phase: str, signal: Optional[asyncio.Event] = None) -> Optional[dict]:
    """更新 session phase，被 abort 时向上抛出而非吞成 None"""
    assert_valid_session_id(session_id)
    session_dir = _session_dir(session_id)
    try:
        if signal is not None and signal.is_set():
            raise _create_abort_error()
        async with aiofiles.open(session_dir / "session.json", encoding="utf-8") as f:
            session = json.loads(await f.read())
        from datetime import datetime, timezone
        session["phase"] = phase
        session["updated_at"] = datetime.now(timezone.utc).isoformat()
        await _atomic_write(session_dir / "session.json", json.dumps(session, ensure_ascii=False, indent=2), signal)
        return session
    except Exception as e:
        if signal is not None and signal.is_set():
            raise
        return None


async def touch_session(session_id: str) -> str:
    """更新 session.updatedAt"""
    assert_valid_session_id(session_id)
    session_dir = _session_dir(session_id)
    async with aiofiles.open(session_dir / "session.json", encoding="utf-8") as f:
        session = json.loads(await f.read())
    from datetime import datetime, timezone
    updated_at = datetime.now(timezone.utc).isoformat()
    session["updated_at"] = updated_at
    await _atomic_write(session_dir / "session.json", json.dumps(session, ensure_ascii=False, indent=2))
    return updated_at


# ─── Search Cache ─────────────────────────────────────────────────────────────

async def save_search_result(session_id: str, result_id: str, data: Any) -> None:
    """写搜索结果到 search-cache/{resultId}.json"""
    assert_valid_session_id(session_id)
    if not is_valid_result_id(result_id):
        raise ValueError(f"invalid resultId: {result_id}")
    cache_dir = _session_dir(session_id) / "search-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    await _atomic_write(cache_dir / f"{result_id}.json", json.dumps(data, ensure_ascii=False, indent=2))


async def load_search_result(session_id: str, result_id: str) -> Optional[Any]:
    """读搜索结果；不存在或 resultId 非法返回 None"""
    assert_valid_session_id(session_id)
    if not is_valid_result_id(result_id):
        return None
    try:
        async with aiofiles.open(_session_dir(session_id) / "search-cache" / f"{result_id}.json", encoding="utf-8") as f:
            return json.loads(await f.read())
    except FileNotFoundError:
        return None


# ─── 聚合加载 ─────────────────────────────────────────────────────────────────

async def load_store(session_id: str) -> dict:
    """一次性加载 session 级业务文件，任一缺失对应字段为 None"""
    assert_valid_session_id(session_id)
    trip_meta = await load_trip_meta(session_id)
    poi_list = await load_poi_list(session_id)
    itinerary = await load_itinerary(session_id)
    session = await load_session_state(session_id)
    return {
        "trip_meta": trip_meta,
        "poi_list": poi_list,
        "itinerary": itinerary,
        "session": session,
    }
