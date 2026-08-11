"""
EpisodicMemory 复刻（HelloAgents 情景记忆）

特性：
  - SQLite 权威存储（store.save_memory）
  - ChromaDB 向量索引（store.upsert_vector/query_vectors）
  - 检索时按 user_id 隔离
  - 重排公式对齐 HelloAgents 文档：
    score = (vector_score * 0.8 + recency_score * 0.2) * (0.8 + importance * 0.4)
"""
from datetime import datetime, timezone
from typing import List, Optional

from app.memory.base import MemoryItem, MemorySearchResult
from app.memory import store
from app.rag import embedding


class EpisodicMemory:
    def add(self, item: MemoryItem) -> str:
        item.memory_type = "episodic"
        store.save_memory(item)
        try:
            vec = embedding.encode_one(item.content)
            if vec:
                store.upsert_vector(item, vec)
        except Exception:
            # 向量写入失败不影响权威 SQLite 存储
            pass
        return item.id

    def search(self, user_id: str, query: str, limit: int = 5, min_importance: float = 0.0) -> List[MemorySearchResult]:
        try:
            qvec = embedding.encode_one(query)
        except Exception:
            qvec = []
        results: List[MemorySearchResult] = []
        seen = set()
        if qvec:
            raw = store.query_vectors("episodic", user_id, qvec, limit=max(limit * 3, 10))
            for r in raw:
                item = store.get_memory(r["id"])
                if not item or item.metadata.get("forgotten") or item.importance < min_importance:
                    continue
                final = self._final_score(r.get("vector_score", 0.0), item)
                results.append(MemorySearchResult(
                    item=item,
                    score=final,
                    vector_score=r.get("vector_score", 0.0),
                    recency_score=self._recency_score(item.timestamp),
                    importance_score=0.8 + item.importance * 0.4,
                    source="episodic",
                ))
                seen.add(item.id)
        # SQLite fallback：如果向量没命中，用关键词兜底
        if len(results) < limit:
            for item in store.list_memories(user_id, "episodic", limit=100):
                if item.id in seen or item.importance < min_importance:
                    continue
                ks = self._keyword_score(query.lower(), item.content.lower())
                if ks <= 0:
                    continue
                final = self._final_score(ks, item)
                results.append(MemorySearchResult(
                    item=item,
                    score=final,
                    vector_score=ks,
                    recency_score=self._recency_score(item.timestamp),
                    importance_score=0.8 + item.importance * 0.4,
                    source="episodic_sqlite",
                ))
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:limit]

    def update(self, memory_id: str, content: Optional[str] = None, importance: Optional[float] = None) -> bool:
        ok = store.update_memory(memory_id, content=content, importance=importance)
        if ok and content is not None:
            item = store.get_memory(memory_id)
            if item:
                try:
                    vec = embedding.encode_one(item.content)
                    store.upsert_vector(item, vec)
                except Exception:
                    pass
        return ok

    def remove(self, memory_id: str) -> bool:
        return store.mark_forgotten(memory_id)

    def forget(self, user_id: str, strategy: str = "importance_based", threshold: float = 0.1, max_age_days: int = 30) -> int:
        forgotten = 0
        for item in store.list_memories(user_id, "episodic", limit=10000):
            if strategy == "importance_based" and item.importance < threshold:
                if store.mark_forgotten(item.id):
                    forgotten += 1
            elif strategy == "time_based" and self._age_days(item.timestamp) > max_age_days:
                if store.mark_forgotten(item.id):
                    forgotten += 1
        return forgotten

    def _final_score(self, vector_score: float, item: MemoryItem) -> float:
        recency = self._recency_score(item.timestamp)
        importance = 0.8 + item.importance * 0.4
        return (vector_score * 0.8 + recency * 0.2) * importance

    def _recency_score(self, iso: str) -> float:
        days = self._age_days(iso)
        # 30 天内从 1 衰减到 0.2
        return max(0.2, 1.0 - min(days / 30.0, 1.0) * 0.8)

    def _age_days(self, iso: str) -> float:
        try:
            ts = datetime.fromisoformat(iso)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - ts).total_seconds() / 86400
        except Exception:
            return 0.0

    def _keyword_score(self, query: str, content: str) -> float:
        if not query or not content:
            return 0.0
        if query in content:
            return min(1.0, len(query) / max(len(content), 1) * 2)
        q_chars = set(query)
        c_chars = set(content)
        return len(q_chars & c_chars) / max(len(q_chars | c_chars), 1)
