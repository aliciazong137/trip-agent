"""
澄清闭环存储（第一期，review 第2点）

设计：
  - 单独文件 context/{session_id}/pending-clarification.json
  - 记录：原始 query、缺失字段、生成时间
  - session_id 续接：用户回答澄清问题后，合并 original_query + new_query 重跑 IntentRecognizer

为什么单独文件而非混入 session.json：
  - session.json 是确定性排程的产物文件，语义是"规划过程中的状态"
  - 澄清是入口层逻辑，与排程解耦
  - 单独文件便于查询、调试、清理
"""
import asyncio
import json
from datetime import datetime, timezone
from typing import Optional

import aiofiles

from app.services.session_store import _session_dir, assert_valid_session_id


async def save_pending_clarification(
    session_id: str,
    original_query: str,
    missing_fields: list,
    invalid_fields: Optional[list] = None,
    partial_trip_meta: Optional[dict] = None,
) -> None:
    """
    存储待澄清状态

    Args:
        session_id: 会话 ID
        original_query: 用户原始 query（用于续接时合并）
        missing_fields: 缺失字段列表
        invalid_fields: 非法字段列表
        partial_trip_meta: 已抽取的部分 trip_meta（可用于前端展示已填项）
    """
    assert_valid_session_id(session_id)
    payload = {
        "session_id": session_id,
        "original_query": original_query,
        "missing_fields": missing_fields,
        "invalid_fields": invalid_fields or [],
        "partial_trip_meta": partial_trip_meta or {},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    path = _session_dir(session_id) / "pending-clarification.json"
    async with aiofiles.open(path, "w", encoding="utf-8") as f:
        await f.write(json.dumps(payload, ensure_ascii=False, indent=2))


async def load_pending_clarification(session_id: str) -> Optional[dict]:
    """
    读取待澄清状态；不存在返回 None

    Returns:
        {session_id, original_query, missing_fields, invalid_fields, partial_trip_meta, created_at}
    """
    assert_valid_session_id(session_id)
    path = _session_dir(session_id) / "pending-clarification.json"
    try:
        async with aiofiles.open(path, encoding="utf-8") as f:
            return json.loads(await f.read())
    except FileNotFoundError:
        return None


async def clear_pending_clarification(session_id: str) -> None:
    """澄清已满足后清除待澄清状态"""
    import aiofiles.os
    assert_valid_session_id(session_id)
    path = _session_dir(session_id) / "pending-clarification.json"
    try:
        await aiofiles.os.remove(path)
    except FileNotFoundError:
        pass


def merge_query_for_clarification(original_query: str, new_query: str) -> str:
    """
    合并原始 query 和澄清补充 query

    设计：
      - 原始 query 提供主要上下文
      - 补充 query 通常是"北京，两天"这种短回答
      - 合并后让 IntentRecognizer 重新抽取，避免 LLM 自己拼接 trip_meta

    示例：
      original: "我想去玩几天"
      new:      "去南京，2天"
      merged:   "我想去玩几天。补充信息：去南京，2天"
    """
    if not original_query:
        return new_query
    if not new_query:
        return original_query
    return f"{original_query}。补充信息：{new_query}"
