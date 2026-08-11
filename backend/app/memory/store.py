"""
Memory 存储层（SQLite + ChromaDB）

复刻 HelloAgents 的存储分层：
  - SQLite：权威结构化存储（memory.db）
  - ChromaDB：向量索引（不同 memory_type 使用不同 collection）

collection：
  - memory_working
  - memory_episodic
  - memory_semantic
  - memory_perceptual
"""
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

import chromadb

from app.config import settings
from app.memory.base import MemoryItem, MemoryType


logger = logging.getLogger(__name__)

_COLLECTION_PREFIX = "memory_"
_client: Optional[chromadb.PersistentClient] = None


def get_memory_db_path() -> Path:
    return settings.memory_db_path


def get_connection() -> sqlite3.Connection:
    """获取 SQLite 连接，并确保表存在"""
    db_path = get_memory_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """初始化 SQLite 表"""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            memory_type TEXT NOT NULL,
            content TEXT NOT NULL,
            importance REAL NOT NULL,
            timestamp TEXT NOT NULL,
            ttl_minutes INTEGER,
            modality TEXT DEFAULT 'text',
            file_path TEXT,
            session_id TEXT,
            city TEXT,
            days INTEGER,
            event_type TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            forgotten INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_user_type ON memories(user_id, memory_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_timestamp ON memories(timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_city ON memories(city)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS semantic_relations (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            subject TEXT NOT NULL,
            relation TEXT NOT NULL,
            object TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 0.5,
            source_memory_id TEXT,
            timestamp TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rel_user ON semantic_relations(user_id)")
    conn.commit()


def save_memory(item: MemoryItem) -> str:
    """保存/更新 MemoryItem 到 SQLite"""
    meta = item.metadata or {}
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO memories (
                id, user_id, memory_type, content, importance, timestamp,
                ttl_minutes, modality, file_path, session_id, city, days, event_type,
                metadata_json, forgotten
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.id,
                item.user_id,
                item.memory_type,
                item.content,
                item.importance,
                item.timestamp,
                item.ttl_minutes,
                item.modality,
                item.file_path,
                meta.get("session_id"),
                meta.get("city"),
                meta.get("days"),
                meta.get("event_type"),
                json.dumps(meta, ensure_ascii=False),
                1 if meta.get("forgotten") else 0,
            ),
        )
        conn.commit()
    return item.id


def row_to_memory(row: sqlite3.Row) -> MemoryItem:
    meta = json.loads(row["metadata_json"] or "{}")
    if row["forgotten"]:
        meta["forgotten"] = True
    return MemoryItem(
        id=row["id"],
        user_id=row["user_id"],
        memory_type=row["memory_type"],
        content=row["content"],
        importance=row["importance"],
        timestamp=row["timestamp"],
        metadata=meta,
        ttl_minutes=row["ttl_minutes"],
        modality=row["modality"] or "text",
        file_path=row["file_path"],
    )


def get_memory(memory_id: str) -> Optional[MemoryItem]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
        return row_to_memory(row) if row else None


def list_memories(user_id: str, memory_type: Optional[str] = None, limit: int = 100) -> List[MemoryItem]:
    sql = "SELECT * FROM memories WHERE user_id=? AND forgotten=0"
    params: List[Any] = [user_id]
    if memory_type:
        sql += " AND memory_type=?"
        params.append(memory_type)
    sql += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [row_to_memory(r) for r in rows]


def update_memory(memory_id: str, content: Optional[str] = None, importance: Optional[float] = None,
                  metadata: Optional[Dict[str, Any]] = None) -> bool:
    item = get_memory(memory_id)
    if not item:
        return False
    if content is not None:
        item.content = content
    if importance is not None:
        item.importance = importance
    if metadata is not None:
        item.metadata.update(metadata)
    save_memory(item)
    return True


def mark_forgotten(memory_id: str) -> bool:
    with get_connection() as conn:
        cur = conn.execute("UPDATE memories SET forgotten=1 WHERE id=?", (memory_id,))
        conn.commit()
        return cur.rowcount > 0


def delete_user_memories(user_id: str) -> int:
    """删除用户所有 memory（SQLite + Chroma 各 collection）"""
    with get_connection() as conn:
        rows = conn.execute("SELECT id, memory_type FROM memories WHERE user_id=?", (user_id,)).fetchall()
        conn.execute("DELETE FROM memories WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM semantic_relations WHERE user_id=?", (user_id,))
        conn.commit()
    # 删除向量
    for r in rows:
        try:
            get_collection(r["memory_type"]).delete(ids=[r["id"]])
        except Exception:
            pass
    return len(rows)


def get_chroma_client() -> chromadb.PersistentClient:
    global _client
    if _client is None:
        path = str(settings.memory_dir / "chroma")
        logger.info("初始化 Memory ChromaDB: %s", path)
        _client = chromadb.PersistentClient(path=path)
    return _client


def collection_name(memory_type: str) -> str:
    return f"{_COLLECTION_PREFIX}{memory_type}"


def get_collection(memory_type: str):
    client = get_chroma_client()
    return client.get_or_create_collection(
        name=collection_name(memory_type),
        metadata={"hnsw:space": "cosine"},
    )


def upsert_vector(item: MemoryItem, embedding: List[float]) -> None:
    coll = get_collection(item.memory_type)
    meta = {
        "user_id": item.user_id,
        "memory_type": item.memory_type,
        "importance": item.importance,
        "timestamp": item.timestamp,
        "session_id": item.metadata.get("session_id", ""),
        "city": item.metadata.get("city", ""),
        "event_type": item.metadata.get("event_type", ""),
    }
    coll.upsert(ids=[item.id], documents=[item.content], embeddings=[embedding], metadatas=[meta])


def query_vectors(memory_type: str, user_id: str, embedding: List[float], limit: int = 5) -> List[Dict[str, Any]]:
    coll = get_collection(memory_type)
    try:
        result = coll.query(
            query_embeddings=[embedding],
            n_results=limit,
            where={"user_id": user_id},
            include=["documents", "metadatas", "distances"],
        )
    except Exception as e:
        logger.warning("Memory vector query failed: %s", e)
        return []
    ids = result.get("ids", [[]])[0]
    docs = result.get("documents", [[]])[0]
    metas = result.get("metadatas", [[]])[0]
    dists = result.get("distances", [[]])[0]
    out = []
    for i, mid in enumerate(ids):
        dist = dists[i] if i < len(dists) else 1.0
        out.append({
            "id": mid,
            "content": docs[i] if i < len(docs) else "",
            "metadata": metas[i] if i < len(metas) else {},
            "vector_score": max(0.0, 1.0 - dist),
        })
    return out


def reset_memory_store() -> None:
    """测试用：清空 SQLite + Chroma collections"""
    db = get_memory_db_path()
    if db.exists():
        db.unlink()
    global _client
    _client = None
    chroma_dir = settings.memory_dir / "chroma"
    if chroma_dir.exists():
        import shutil
        shutil.rmtree(chroma_dir, ignore_errors=True)
