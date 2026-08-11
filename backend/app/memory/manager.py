"""
MemoryManager 复刻（HelloAgents MemoryManager）

统一调度 working / episodic / semantic / perceptual。
"""
from typing import Any, Dict, List, Optional

from app.config import settings
from app.memory.base import MemoryItem, MemorySearchResult, new_memory_id, now_iso
from app.memory.working import WorkingMemory
from app.memory.episodic import EpisodicMemory
from app.memory.semantic import SemanticMemory
from app.memory.perceptual import PerceptualMemory
from app.memory import store


class MemoryManager:
    def __init__(self, enable_working=True, enable_episodic=True, enable_semantic=True, enable_perceptual=True):
        self.memory_types: Dict[str, Any] = {}
        if enable_working:
            self.memory_types["working"] = WorkingMemory()
        if enable_episodic:
            self.memory_types["episodic"] = EpisodicMemory()
        if enable_semantic:
            self.memory_types["semantic"] = SemanticMemory()
        if enable_perceptual:
            self.memory_types["perceptual"] = PerceptualMemory()

    def add_memory(self, content: str, user_id: str = "default_user", memory_type: str = "working",
                   importance: float = 0.5, metadata: Optional[Dict[str, Any]] = None,
                   modality: str = "text", file_path: Optional[str] = None) -> str:
        item = MemoryItem(
            id=new_memory_id(memory_type[:3]),
            user_id=user_id,
            memory_type=memory_type,
            content=content,
            importance=importance,
            timestamp=now_iso(),
            metadata=metadata or {},
            modality=modality,
            file_path=file_path,
        )
        if memory_type not in self.memory_types:
            raise ValueError(f"不支持的 memory_type: {memory_type}")
        return self.memory_types[memory_type].add(item)

    def search(self, user_id: str, query: str, limit: int = 5,
               memory_types: Optional[List[str]] = None, min_importance: float = 0.0) -> List[MemorySearchResult]:
        types = memory_types or list(self.memory_types.keys())
        results: List[MemorySearchResult] = []
        per_type_limit = max(1, limit // max(len(types), 1) + 1)
        for t in types:
            mem = self.memory_types.get(t)
            if not mem:
                continue
            try:
                results.extend(mem.search(user_id, query, limit=per_type_limit, min_importance=min_importance))
            except Exception:
                continue
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]

    def get_context_for_query(self, user_id: str, query: str, limit: int = 3) -> str:
        results = self.search(user_id, query, limit=limit, min_importance=settings.memory_importance_threshold)
        if not results:
            return ""
        lines = ["【用户历史记忆，仅供偏好参考，不得用于补全 city/days/travelers/budget 等本次事实】"]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. ({r.item.memory_type}, score={r.score:.2f}) {r.item.content}")
        return "\n".join(lines)

    def update_memory(self, memory_id: str, content: Optional[str] = None, importance: Optional[float] = None) -> bool:
        item = store.get_memory(memory_id)
        if not item:
            # working memory 可能不在 SQLite，逐个查
            for mem in self.memory_types.values():
                if hasattr(mem, "update") and mem.update(memory_id, content, importance):
                    return True
            return False
        mem = self.memory_types.get(item.memory_type)
        return bool(mem and hasattr(mem, "update") and mem.update(memory_id, content, importance))

    def remove_memory(self, memory_id: str) -> bool:
        item = store.get_memory(memory_id)
        if item:
            mem = self.memory_types.get(item.memory_type)
            return bool(mem and hasattr(mem, "remove") and mem.remove(memory_id))
        for mem in self.memory_types.values():
            if hasattr(mem, "remove") and mem.remove(memory_id):
                return True
        return False

    def consolidate(self, user_id: str, from_type: str = "episodic", to_type: str = "semantic",
                    importance_threshold: float = 0.7) -> int:
        if from_type == "episodic" and to_type == "semantic" and "semantic" in self.memory_types:
            episodes = [m for m in store.list_memories(user_id, "episodic", limit=1000) if m.importance >= importance_threshold]
            return self.memory_types["semantic"].consolidate_from_episodic(user_id, episodes, importance_threshold)
        if from_type == "working" and to_type == "episodic" and "working" in self.memory_types:
            count = 0
            for r in self.memory_types["working"].search(user_id, "", limit=1000, min_importance=importance_threshold):
                self.add_memory(r.item.content, user_id=user_id, memory_type="episodic", importance=r.item.importance, metadata=r.item.metadata)
                count += 1
            return count
        return 0

    def forget(self, user_id: str, strategy: str = "importance_based", threshold: float = 0.1, max_age_days: int = 30) -> int:
        total = 0
        for mem in self.memory_types.values():
            if hasattr(mem, "forget"):
                try:
                    total += mem.forget(user_id, strategy, threshold, max_age_days)
                except TypeError:
                    total += mem.forget(strategy, threshold, max_age_days)
        return total

    def summary(self, user_id: str, limit: int = 10) -> str:
        memories = store.list_memories(user_id, limit=limit)
        if not memories:
            return "暂无记忆"
        return "\n".join(f"- [{m.memory_type}] {m.content}" for m in memories)

    def stats(self, user_id: str) -> dict:
        memories = store.list_memories(user_id, limit=10000)
        by_type: Dict[str, int] = {}
        for m in memories:
            by_type[m.memory_type] = by_type.get(m.memory_type, 0) + 1
        if "working" in self.memory_types:
            by_type["working_in_memory"] = self.memory_types["working"].stats()["count"]
        return {"user_id": user_id, "total_persisted": len(memories), "by_type": by_type}

    def clear_all(self, user_id: str) -> int:
        n = store.delete_user_memories(user_id)
        if "working" in self.memory_types:
            n += self.memory_types["working"].clear(user_id)
        return n
