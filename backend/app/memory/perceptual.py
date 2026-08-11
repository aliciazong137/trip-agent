"""
PerceptualMemory 接口复刻（metadata-only）

HelloAgents 原版支持 text/image/audio/video + CLIP/CLAP 等多模态 embedding。
本项目暂不引入多模态依赖，只复刻接口和 metadata 存储：
  - modality: text/image/audio/video/file
  - file_path: 文件路径
  - content: 文件描述/用户说明

检索：MVP 用 SQLite keyword fallback；未来可接图片/音频 embedding。
"""
from typing import List

from app.memory.base import MemoryItem, MemorySearchResult
from app.memory import store


class PerceptualMemory:
    def add(self, item: MemoryItem) -> str:
        item.memory_type = "perceptual"
        store.save_memory(item)
        return item.id

    def search(self, user_id: str, query: str, limit: int = 5, min_importance: float = 0.0) -> List[MemorySearchResult]:
        out = []
        q = set(query.lower())
        for item in store.list_memories(user_id, "perceptual", limit=100):
            if item.importance < min_importance:
                continue
            c = set(item.content.lower())
            score = len(q & c) / max(len(q | c), 1) if q and c else 0.0
            if score > 0:
                out.append(MemorySearchResult(item=item, score=score, vector_score=score, source="perceptual"))
        out.sort(key=lambda x: x.score, reverse=True)
        return out[:limit]

    def forget(self, user_id: str, strategy: str = "importance_based", threshold: float = 0.1, max_age_days: int = 30) -> int:
        forgotten = 0
        for item in store.list_memories(user_id, "perceptual", limit=10000):
            if strategy == "importance_based" and item.importance < threshold:
                if store.mark_forgotten(item.id):
                    forgotten += 1
        return forgotten
