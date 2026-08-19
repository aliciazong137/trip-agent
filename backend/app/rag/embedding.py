"""
Embedding 模型封装（第二期 RAG）

本地 BAAI/bge-small-zh-v1.5 单例：
  - 约 100MB，首次下载，CPU 推理够快
  - 中文检索效果好
  - normalize_embeddings=True 便于后续用 cosine 距离

单例设计：
  SentenceTransformer 加载模型耗时 ~5s（首次下载+加载），
  避免每次检索都重新加载。模块级单例 + 懒加载。
"""
import logging
from typing import List, Optional

from app.config import settings


logger = logging.getLogger(__name__)

# 模块级单例
_embedder = None
_embedder_model_name: Optional[str] = None


def get_embedder():
    """
    获取 SentenceTransformer 单例（懒加载）

    第一次调用会触发模型下载（约 100MB）+ 加载到内存，
    后续调用直接返回已加载的实例。
    """
    global _embedder, _embedder_model_name

    model_name = settings.rag_embedding_model
    if _embedder is not None and _embedder_model_name == model_name:
        return _embedder

    # 路径优先：若 .env 配置了 RAG_EMBEDDING_MODEL_PATH，用本地路径（离线场景）
    model_path_or_name = settings.rag_embedding_model_path or model_name

    logger.info("加载 embedding 模型: %s（首次约 5s）", model_path_or_name)
    from sentence_transformers import SentenceTransformer
    _embedder = SentenceTransformer(
        model_path_or_name,
        device=settings.rag_device,
        local_files_only=True,  # 离线模式：用本地缓存，避免联网经代理 502
    )
    _embedder_model_name = model_name
    logger.info("Embedding 模型已加载: %s", model_name)
    return _embedder


def encode(texts: List[str]) -> List[List[float]]:
    """
    文本转向量

    Args:
        texts: 文本列表

    Returns:
        向量列表，每个向量是 List[float]
    """
    if not texts:
        return []
    embedder = get_embedder()
    # normalize_embeddings=True：让向量单位化，cosine 距离 = 内积
    vectors = embedder.encode(
        texts,
        normalize_embeddings=True,
        batch_size=settings.rag_batch_size,
        convert_to_numpy=True,
    )
    return vectors.tolist()


def encode_one(text: str) -> List[float]:
    """单文本转向量（query 用）"""
    return encode([text])[0] if text else []


def reset_embedder() -> None:
    """测试用：重置单例（切换模型时调）"""
    global _embedder, _embedder_model_name
    _embedder = None
    _embedder_model_name = None
