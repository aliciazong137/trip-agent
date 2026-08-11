"""
WorkingMemory 复刻（HelloAgents 工作记忆）

特性：
  - 纯内存，不持久化
  - TTL 过期清理
  - 容量限制
  - TF-IDF + keyword 混合检索
  - importance + time decay 重排
"""
from datetime import datetime, timezone
from typing import List, Optional

from app.config import settings
from app.memory.base import MemoryItem, MemorySearchResult


class WorkingMemory:
    def __init__(self, capacity: Optional[int] = None, ttl_minutes: Optional[int] = None):
        self.capacity = capacity or settings.memory_working_capacity
        self.ttl_minutes = ttl_minutes or settings.memory_working_ttl_minutes
        self.memories: List[MemoryItem] = []

    def add(self, item: MemoryItem) -> str:
        item.memory_type = "working"
        if item.ttl_minutes is None:
            item.ttl_minutes = self.ttl_minutes
        self._expire_old()
        self.memories.append(item)
        self._enforce_capacity()
        return item.id

    def search(self, user_id: str, query: str, limit: int = 5, min_importance: float = 0.0) -> List[MemorySearchResult]:
        self._expire_old()
        active = [
            m for m in self.memories
            if m.user_id == user_id and not m.metadata.get("forgotten") and m.importance >= min_importance
        ]
        if not active:
            return []

        vector_scores = self._tfidf_scores(query, [m.content for m in active])
        query_lower = query.lower()
        results: List[MemorySearchResult] = []
        for i, m in enumerate(active):
            keyword_score = self._keyword_score(query_lower, m.content.lower())
            vector_score = vector_scores[i] if i < len(vector_scores) else 0.0
            relevance = vector_score * 0.7 + keyword_score * 0.3 if vector_score > 0 else keyword_score
            recency = self._recency_score(m.timestamp)
            importance = 0.8 + m.importance * 0.4
            score = relevance * recency * importance
            if score > 0:
                results.append(MemorySearchResult(
                    item=m,
                    score=score,
                    vector_score=vector_score,
                    recency_score=recency,
                    importance_score=importance,
                    source="working",
                ))
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]

    def update(self, memory_id: str, content: Optional[str] = None, importance: Optional[float] = None) -> bool:
        for m in self.memories:
            if m.id == memory_id:
                if content is not None:
                    m.content = content
                if importance is not None:
                    m.importance = importance
                return True
        return False

    def remove(self, memory_id: str) -> bool:
        before = len(self.memories)
        self.memories = [m for m in self.memories if m.id != memory_id]
        return len(self.memories) < before

    def forget(self, strategy: str = "importance_based", threshold: float = 0.1, max_age_days: int = 30) -> int:
        before = len(self.memories)
        if strategy == "importance_based":
            self.memories = [m for m in self.memories if m.importance >= threshold]
        elif strategy == "time_based":
            cutoff_minutes = max_age_days * 24 * 60
            self.memories = [m for m in self.memories if self._age_minutes(m.timestamp) <= cutoff_minutes]
        elif strategy == "capacity_based":
            self._enforce_capacity()
        return before - len(self.memories)

    def clear(self, user_id: Optional[str] = None) -> int:
        before = len(self.memories)
        if user_id:
            self.memories = [m for m in self.memories if m.user_id != user_id]
        else:
            self.memories.clear()
        return before - len(self.memories)

    def stats(self) -> dict:
        return {"count": len(self.memories), "capacity": self.capacity, "ttl_minutes": self.ttl_minutes}

    def _expire_old(self) -> None:
        self.memories = [m for m in self.memories if not self._is_expired(m)]

    def _is_expired(self, item: MemoryItem) -> bool:
        ttl = item.ttl_minutes or self.ttl_minutes
        return self._age_minutes(item.timestamp) > ttl

    def _age_minutes(self, iso: str) -> float:
        try:
            ts = datetime.fromisoformat(iso)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - ts).total_seconds() / 60
        except Exception:
            return 0.0

    def _recency_score(self, iso: str) -> float:
        age = self._age_minutes(iso)
        # 2 小时内接近 1，随时间衰减
        return max(0.1, 1.0 - min(age / max(self.ttl_minutes, 1), 1.0) * 0.9)

    def _enforce_capacity(self) -> None:
        if len(self.memories) <= self.capacity:
            return
        # 按 importance 低 + 年龄老优先淘汰
        self.memories.sort(key=lambda m: (m.importance, -self._age_minutes(m.timestamp)))
        self.memories = self.memories[-self.capacity:]

    def _keyword_score(self, query: str, content: str) -> float:
        if not query or not content:
            return 0.0
        if query in content:
            return min(1.0, len(query) / max(len(content), 1) * 2)
        q_words = set(query.split())
        c_words = set(content.split())
        if not q_words or not c_words:
            # 中文无空格时做字符交集兜底
            q_chars = set(query)
            c_chars = set(content)
            return len(q_chars & c_chars) / max(len(q_chars | c_chars), 1)
        return len(q_words & c_words) / max(len(q_words | c_words), 1)

    def _tfidf_scores(self, query: str, docs: List[str]) -> List[float]:
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.metrics.pairwise import cosine_similarity
            corpus = [query] + docs
            mat = TfidfVectorizer(token_pattern=r"(?u)\b\w+\b").fit_transform(corpus)
            sims = cosine_similarity(mat[0:1], mat[1:]).flatten()
            return [float(x) for x in sims]
        except Exception:
            return [0.0 for _ in docs]
