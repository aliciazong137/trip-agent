"""
Memory 基础类型（第四期，复刻 HelloAgents Memory 架构）

复刻目标：
  - MemoryItem / MemoryConfig / MemorySearchResult 等基础结构
  - 与 HelloAgents 类似的 memory_type / importance / timestamp / metadata 语义
  - 但底层存储替换为 SQLite + ChromaDB + bge-small-zh
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


MemoryType = Literal["working", "episodic", "semantic", "perceptual"]
MemoryModality = Literal["text", "image", "audio", "video", "file"]


def now_iso() -> str:
    """UTC ISO 时间字符串"""
    return datetime.now(timezone.utc).isoformat()


def new_memory_id(prefix: str = "mem") -> str:
    """生成 memory id"""
    return f"{prefix}_{uuid4().hex[:16]}"


class MemoryItem(BaseModel):
    """单条记忆（对齐 HelloAgents MemoryItem 语义）"""
    id: str = Field(default_factory=lambda: new_memory_id())
    user_id: str = "default_user"
    memory_type: MemoryType = "episodic"
    content: str
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    timestamp: str = Field(default_factory=now_iso)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    # working memory 专用
    ttl_minutes: Optional[int] = None
    # perceptual memory 专用
    modality: MemoryModality = "text"
    file_path: Optional[str] = None


class MemorySearchResult(BaseModel):
    """检索结果，包含评分解释"""
    item: MemoryItem
    score: float = Field(ge=0.0)
    vector_score: float = 0.0
    recency_score: float = 0.0
    importance_score: float = 0.0
    source: str = ""


class MemoryConfig(BaseModel):
    """Memory 配置（轻量版，字段名尽量对齐 HelloAgents）"""
    storage_path: str = "data/memory"
    max_capacity: int = 1000
    importance_threshold: float = 0.1
    decay_factor: float = 0.95
    working_memory_capacity: int = 10
    working_memory_tokens: int = 2000
    working_memory_ttl_minutes: int = 120
    perceptual_memory_modalities: List[str] = Field(default_factory=lambda: ["text", "image", "audio", "video"])


class MemoryCompressionResult(BaseModel):
    """LLM 压缩一次旅行规划后的结构化结果"""
    summary: str
    facts: Dict[str, Any] = Field(default_factory=dict)
    # 稳定用户画像：只有输入有证据时才填写，不把本次临时目的地当长期偏好
    profile: Dict[str, Any] = Field(default_factory=dict)
    preferences: List[str] = Field(default_factory=list)
    avoid: List[str] = Field(default_factory=list)
    decisions: List[str] = Field(default_factory=list)
    unknowns: List[str] = Field(default_factory=list)
    importance: float = Field(default=0.7, ge=0.0, le=1.0)


def build_memory_text(compression: MemoryCompressionResult) -> str:
    """把压缩结果转为适合 embedding 的文本"""
    parts = [compression.summary.strip()]
    if compression.profile:
        parts.append("用户画像：" + "、".join(f"{k}={v}" for k, v in compression.profile.items()))
    if compression.preferences:
        parts.append("偏好：" + "、".join(compression.preferences))
    if compression.avoid:
        parts.append("避开：" + "、".join(compression.avoid))
    if compression.decisions:
        parts.append("关键决策：" + "；".join(compression.decisions))
    if compression.unknowns:
        parts.append("未知信息：" + "、".join(compression.unknowns))
    return "\n".join(p for p in parts if p)
