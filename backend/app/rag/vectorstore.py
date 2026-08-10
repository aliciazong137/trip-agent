"""
ChromaDB 向量库封装（第二期 RAG）

设计：
  - 持久化目录：项目根/data/chroma（见 config.settings.rag_chroma_dir）
  - 单一 collection "guides"，cosine 距离
  - 文档 id 用 "{content_hash}:{chunk_index}"，同内容变化时删旧写新
  - metadata 含 city/source/source_url/is_external 等，支持按 city 过滤

为什么用 cosine：
  - bge-small-zh 已 normalize，cosine = 内积，性能高
  - 文本检索场景 cosine 最常用
"""
import logging
from typing import Any, Dict, List, Optional

import chromadb

from app.config import settings


logger = logging.getLogger(__name__)

COLLECTION_NAME = "guides"

# 模块级单例（ChromaDB 客户端）
_client: Optional[chromadb.PersistentClient] = None


def get_client() -> chromadb.PersistentClient:
    """获取 ChromaDB 客户端单例"""
    global _client
    if _client is None:
        path = str(settings.rag_chroma_dir)
        logger.info("初始化 ChromaDB 客户端: %s", path)
        _client = chromadb.PersistentClient(path=path)
    return _client


def get_collection():
    """
    获取 guides collection（cosine 距离）

    ChromaDB 默认用 L2 距离，需用 metadata={"hnsw:space": "cosine"} 指定。
    collection 一旦创建，距离度量不可改，重建需先 delete_collection。
    """
    client = get_client()
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


def reset_collection() -> None:
    """删除并重建 collection（测试用 + 内容全量更新时用）"""
    client = get_client()
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass  # 不存在时忽略
    get_collection()


def upsert_chunks(
    ids: List[str],
    embeddings: List[List[float]],
    contents: List[str],
    metadatas: List[Dict[str, Any]],
) -> int:
    """
    写入（或更新）chunks

    Args:
        ids: 文档 id 列表（"{content_hash}:{chunk_index}"）
        embeddings: 向量列表
        contents: 原文
        metadatas: metadata 列表

    Returns:
        写入条数
    """
    if not ids:
        return 0
    coll = get_collection()
    coll.upsert(
        ids=ids,
        embeddings=embeddings,
        documents=contents,
        metadatas=metadatas,
    )
    return len(ids)


def query(
    embedding: List[float],
    top_k: int = 3,
    city: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    检索 top_k 最相似的 chunks

    Args:
        embedding: query 向量（已 normalize）
        top_k: 返回数量
        city: 可选，按城市过滤（None 表示不过滤）

    Returns:
        List[{"id", "content", "score", "metadata"}]
        score 是 1 - distance（cosine 距离 0 表示完全相似，1 表示完全不相似）
    """
    coll = get_collection()
    where = {"city": city} if city else None
    result = coll.query(
        query_embeddings=[embedding],
        n_results=top_k,
        where=where,
        include=["documents", "metadatas", "distances"],
    )

    # ChromaDB 返回结构：{ids: [[...]], documents: [[...]], metadatas: [[...]], distances: [[...]]}
    ids = result.get("ids", [[]])[0]
    documents = result.get("documents", [[]])[0]
    metadatas = result.get("metadatas", [[]])[0]
    distances = result.get("distances", [[]])[0]

    out = []
    for i, doc in enumerate(documents):
        distance = distances[i] if i < len(distances) else 1.0
        # cosine 距离转相似度：score = 1 - distance（bge 已 normalize，距离 0=最相似）
        score = max(0.0, 1.0 - distance)
        out.append({
            "id": ids[i] if i < len(ids) else "",
            "content": doc,
            "score": score,
            "metadata": metadatas[i] if i < len(metadatas) else {},
        })
    return out


def delete_by_content_hash(content_hash: str) -> int:
    """
    按 content_hash 删除 chunks（文档内容变化时删旧）

    Args:
        content_hash: 内容哈希

    Returns:
        删除条数
    """
    coll = get_collection()
    try:
        coll.delete(where={"content_hash": content_hash})
        return 1
    except Exception as e:
        logger.warning("delete_by_content_hash %s 失败: %s", content_hash, e)
        return 0


def count() -> int:
    """当前 collection 中的 chunk 总数"""
    coll = get_collection()
    try:
        return coll.count()
    except Exception:
        return 0


def list_all() -> List[Dict[str, Any]]:
    """列出所有 chunks（调试用，慎用，可能很大）"""
    coll = get_collection()
    result = coll.get(include=["documents", "metadatas"])
    ids = result.get("ids", [])
    docs = result.get("documents", [])
    metas = result.get("metadatas", [])
    return [
        {"id": ids[i], "content": docs[i], "metadata": metas[i]}
        for i in range(len(ids))
    ]
