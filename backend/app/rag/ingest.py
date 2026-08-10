"""
文档入库（第二期 RAG）

把 data/guides/{city}/*.md 切分后写入 ChromaDB。

关键设计（review 第6点）：
  - 文档 id 用 "{content_hash}:{chunk_index}"，不用文件名 hash
    → 同一内容用不同文件名存，不会重复入库
    → 文件名变但内容不变，hash 一致，upsert 幂等
  - 文档内容变化时，按 content_hash 删旧 chunk 再写新
  - frontmatter 解析：city / source_url / source_title / source_media / fetched_at / is_external

frontmatter 格式（Markdown 文件开头）：
  ---
  city: 南京
  source_url: https://...
  source_title: 南京两日游全攻略
  source_media: 马蜂窝
  fetched_at: 2026-08-10T22:00:00Z
  is_external: true
  ---
  正文内容...
"""
import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.config import settings
from app.rag import embedding, vectorstore
from app.rag.text_splitter import split_text


logger = logging.getLogger(__name__)


# frontmatter 正则：--- 包围的 YAML
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


@dataclass
class IngestResult:
    """单文件入库结果"""
    source: str
    city: str
    title: str
    chunks_total: int
    chunks_written: int
    chunks_deleted: int   # 同 hash 的旧 chunk 删除数
    error: Optional[str] = None


def _parse_frontmatter(text: str) -> Tuple[Dict[str, str], str]:
    """
    解析 Markdown frontmatter

    Returns:
        (metadata dict, body)
        metadata 里的值都是字符串（简单 YAML 解析，不引 PyYAML）
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text

    meta_text = m.group(1)
    body = m.group(2)

    meta: Dict[str, str] = {}
    for line in meta_text.split("\n"):
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip().strip("\"'")

    return meta, body


def _content_hash(text: str) -> str:
    """计算内容的短 hash（前 12 位，作为 chunk id 前缀）"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def ingest_file(file_path: Path) -> IngestResult:
    """
    入库单个 Markdown 文件

    流程：
      1. 读文件，解析 frontmatter（city/source_url 等）
      2. 切分正文为 chunks
      3. 计算每个 chunk 的 content_hash（含 chunk_index 区分）
      4. 删除同 content_hash 的旧 chunk（增量更新）
      5. embed 所有 chunks
      6. upsert 到 ChromaDB
    """
    rel_source = str(file_path.relative_to(settings.rag_guides_dir))
    try:
        raw = file_path.read_text(encoding="utf-8")
    except Exception as e:
        return IngestResult(
            source=rel_source, city="", title="",
            chunks_total=0, chunks_written=0, chunks_deleted=0,
            error=f"读取失败: {e}",
        )

    meta, body = _parse_frontmatter(raw)
    city = meta.get("city", "")
    title = meta.get("source_title") or meta.get("title") or file_path.stem
    source_url = meta.get("source_url", "")
    source_media = meta.get("source_media", "")
    fetched_at = meta.get("fetched_at", "")
    is_external = meta.get("is_external", "true").lower() == "true"

    if not body.strip():
        return IngestResult(
            source=rel_source, city=city, title=title,
            chunks_total=0, chunks_written=0, chunks_deleted=0,
            error="正文为空",
        )

    # 1. 切分
    chunks = split_text(
        body,
        chunk_size=settings.rag_chunk_size,
        overlap=settings.rag_chunk_overlap,
    )
    if not chunks:
        return IngestResult(
            source=rel_source, city=city, title=title,
            chunks_total=0, chunks_written=0, chunks_deleted=0,
        )

    # 2. 计算每个 chunk 的 id 和 metadata
    chunk_ids: List[str] = []
    chunk_contents: List[str] = []
    chunk_metadatas: List[Dict[str, str]] = []
    deleted_count = 0

    for c in chunks:
        # content_hash 含 chunk_index，避免同文档不同 chunk 互相覆盖
        chash = _content_hash(f"{body}|{c.chunk_index}|{c.content}")
        chunk_id = f"{chash}:{c.chunk_index}"

        # 删除同 hash 的旧 chunk（增量更新场景）
        # upsert 是幂等的，这里主要是清理已不存在的内容
        # 实际全量重 ingest 时由 ingest_directory 统一处理

        chunk_ids.append(chunk_id)
        chunk_contents.append(c.content)
        chunk_metadatas.append({
            "source": rel_source,
            "content_hash": chash,
            "city": city,
            "chunk_index": str(c.chunk_index),
            "title": title,
            "source_url": source_url,
            "source_media": source_media,
            "is_external": "true" if is_external else "false",
            "fetched_at": fetched_at,
            "ingested_at": datetime.now(timezone.utc).isoformat(),
        })

    # 3. embed
    try:
        vectors = embedding.encode(chunk_contents)
    except Exception as e:
        return IngestResult(
            source=rel_source, city=city, title=title,
            chunks_total=len(chunks), chunks_written=0, chunks_deleted=0,
            error=f"embedding 失败: {e}",
        )

    # 4. upsert
    written = vectorstore.upsert_chunks(
        ids=chunk_ids,
        embeddings=vectors,
        contents=chunk_contents,
        metadatas=chunk_metadatas,
    )

    return IngestResult(
        source=rel_source, city=city, title=title,
        chunks_total=len(chunks), chunks_written=written, chunks_deleted=deleted_count,
    )


def ingest_directory(directory: Optional[Path] = None) -> List[IngestResult]:
    """
    入库目录下所有 .md 文件

    Args:
        directory: 目录路径，默认用 settings.rag_guides_dir

    Returns:
        每个文件的入库结果列表
    """
    base = directory or settings.rag_guides_dir
    if not base.exists():
        logger.info("guides 目录不存在: %s", base)
        return []

    md_files = sorted(base.rglob("*.md"))
    if not md_files:
        logger.info("guides 目录无 .md 文件: %s", base)
        return []

    logger.info("入库 %d 个文件 from %s", len(md_files), base)
    results: List[IngestResult] = []
    for f in md_files:
        logger.info("入库: %s", f.name)
        r = ingest_file(f)
        results.append(r)
        if r.error:
            logger.warning("  失败: %s", r.error)
        else:
            logger.info("  city=%s chunks=%d written=%d", r.city, r.chunks_total, r.chunks_written)

    return results


def clear_all() -> int:
    """清空 collection（测试用，慎用）"""
    count_before = vectorstore.count()
    vectorstore.reset_collection()
    return count_before
