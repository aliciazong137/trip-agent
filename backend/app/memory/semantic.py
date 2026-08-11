"""
SemanticMemory 复刻（轻量版）

HelloAgents 原版：Neo4j + Qdrant + spaCy。
本项目复刻：SQLite relation table + ChromaDB + 规则/LLM 抽取。

MVP 能力：
  - add 语义记忆（偏好/avoid/旅行风格等）
  - search 语义记忆
  - extract_preferences_from_text 规则抽取偏好
"""
import re
from typing import List, Optional

from app.memory.base import MemoryItem, MemorySearchResult, new_memory_id, now_iso
from app.memory import store
from app.rag import embedding


class SemanticMemory:
    def add(self, item: MemoryItem) -> str:
        item.memory_type = "semantic"
        store.save_memory(item)
        try:
            vec = embedding.encode_one(item.content)
            if vec:
                store.upsert_vector(item, vec)
        except Exception:
            pass
        return item.id

    def add_preference(self, user_id: str, preference: str, source_memory_id: Optional[str] = None,
                       importance: float = 0.8) -> str:
        item = MemoryItem(
            id=new_memory_id("sem"),
            user_id=user_id,
            memory_type="semantic",
            content=f"用户偏好：{preference}",
            importance=importance,
            timestamp=now_iso(),
            metadata={"event_type": "preference", "preference": preference, "source_memory_id": source_memory_id},
        )
        return self.add(item)

    def search(self, user_id: str, query: str, limit: int = 5, min_importance: float = 0.0) -> List[MemorySearchResult]:
        results: List[MemorySearchResult] = []
        try:
            qvec = embedding.encode_one(query)
        except Exception:
            qvec = []
        if qvec:
            raw = store.query_vectors("semantic", user_id, qvec, limit=max(limit * 3, 10))
            for r in raw:
                item = store.get_memory(r["id"])
                if not item or item.metadata.get("forgotten") or item.importance < min_importance:
                    continue
                score = r.get("vector_score", 0.0) * (0.8 + item.importance * 0.4)
                results.append(MemorySearchResult(
                    item=item, score=score, vector_score=r.get("vector_score", 0.0),
                    importance_score=0.8 + item.importance * 0.4, source="semantic"
                ))
        # fallback
        if len(results) < limit:
            for item in store.list_memories(user_id, "semantic", limit=100):
                if item.importance < min_importance:
                    continue
                ks = self._keyword_score(query.lower(), item.content.lower())
                if ks > 0:
                    results.append(MemorySearchResult(item=item, score=ks, vector_score=ks, source="semantic_sqlite"))
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:limit]

    def consolidate_from_episodic(self, user_id: str, episodes: List[MemoryItem], importance_threshold: float = 0.7) -> int:
        """从 episodic 里抽取稳定偏好写入 semantic"""
        count = 0
        seen = {m.metadata.get("preference") for m in store.list_memories(user_id, "semantic", limit=1000)}
        for ep in episodes:
            if ep.importance < importance_threshold:
                continue
            for pref in extract_preferences_from_text(ep.content):
                if pref and pref not in seen:
                    self.add_preference(user_id, pref, source_memory_id=ep.id, importance=min(1.0, ep.importance))
                    seen.add(pref)
                    count += 1
        return count

    def forget(self, user_id: str, strategy: str = "importance_based", threshold: float = 0.1, max_age_days: int = 30) -> int:
        forgotten = 0
        for item in store.list_memories(user_id, "semantic", limit=10000):
            if strategy == "importance_based" and item.importance < threshold:
                if store.mark_forgotten(item.id):
                    forgotten += 1
        return forgotten

    def _keyword_score(self, query: str, content: str) -> float:
        if query in content:
            return 1.0
        q = set(query)
        c = set(content)
        return len(q & c) / max(len(q | c), 1)


def extract_preferences_from_text(text: str) -> List[str]:
    """轻量规则抽取偏好（后续可替换成 LLM 抽取）"""
    prefs = []
    patterns = [
        r"偏好[:：]?([^。；\n]+)",
        r"喜欢([^。；\n]+)",
        r"不喜欢([^。；\n]+)",
        r"带(?:娃|孩子|小孩)",
        r"亲子(?:游|旅行)?",
        r"历史文化",
        r"美食",
        r"轻松(?:节奏)?",
        r"不赶路",
    ]
    for p in patterns:
        for m in re.finditer(p, text):
            val = m.group(0).strip()
            if val not in prefs:
                prefs.append(val)
    return prefs[:10]
