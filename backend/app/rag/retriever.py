"""
RAG 高层检索接口（第二期）

提供 search() 函数：query → 嵌入 → 向量库检索 → 过滤 min_score → 返回 RetrievedChunk

设计原则（review 反馈）：
  - 知识库为空时返回 []，不假装有知识
  - 低于 min_score 的不返回（避免噪声）
  - 同步操作（encode、query）用 run_in_threadpool 包装，不阻塞 FastAPI 事件循环
"""
import asyncio
import logging
from typing import List, Optional

from pydantic import BaseModel

from app.config import settings
from app.rag import embedding, vectorstore


logger = logging.getLogger(__name__)


class RetrievedChunk(BaseModel):
    """检索到的文本块"""
    content: str
    score: float
    source: str
    city: Optional[str] = None
    chunk_id: str
    title: Optional[str] = None
    is_external: bool = False


def search_sync(
    query: str,
    top_k: Optional[int] = None,
    min_score: Optional[float] = None,
    city: Optional[str] = None,
) -> List[RetrievedChunk]:
    """
    同步检索

    Args:
        query: 用户查询文本
        top_k: 返回数量，默认用 settings.rag_top_k
        min_score: 最低相似度阈值，默认用 settings.rag_min_score
        city: 可选，按城市过滤

    Returns:
        List[RetrievedChunk]，按 score 降序，低于 min_score 的已过滤
        知识库为空返回 []
    """
    if not query or not query.strip():
        return []

    top_k = top_k or settings.rag_top_k
    min_score = min_score if min_score is not None else settings.rag_min_score

    # 知识库空时直接返回 []，不触发 embedding 加载
    if vectorstore.count() == 0:
        logger.info("RAG 知识库为空，跳过检索")
        return []

    # 1. query 转向量
    qvec = embedding.encode_one(query)
    if not qvec:
        return []

    # 2. 向量库检索
    raw = vectorstore.query(qvec, top_k=top_k, city=city)

    # 3. 过滤 min_score + 转 RetrievedChunk
    out: List[RetrievedChunk] = []
    for r in raw:
        if r["score"] < min_score:
            continue
        meta = r.get("metadata", {}) or {}
        out.append(RetrievedChunk(
            content=r.get("content", ""),
            score=r["score"],
            source=meta.get("source", ""),
            city=meta.get("city"),
            chunk_id=r.get("id", ""),
            title=meta.get("title"),
            is_external=bool(meta.get("is_external", False)),
        ))
    return out


async def search(
    query: str,
    top_k: Optional[int] = None,
    min_score: Optional[float] = None,
    city: Optional[str] = None,
) -> List[RetrievedChunk]:
    """
    异步检索：把同步操作丢到线程池，不阻塞事件循环

    sentence-transformers encode 和 ChromaDB query 都是同步阻塞操作，
    在 FastAPI async 路由里直接调用会卡住事件循环。
    """
    return await asyncio.to_thread(search_sync, query, top_k, min_score, city)
