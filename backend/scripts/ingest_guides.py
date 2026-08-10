"""
入库脚本：把 data/guides/{city}/*.md 切分后写入 ChromaDB

调用：
    python backend/scripts/ingest_guides.py
    python backend/scripts/ingest_guides.py --city 南京
    python backend/scripts/ingest_guides.py --reset   # 清空 collection 后重新入库
"""
import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.rag import ingest, vectorstore


logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="攻略入库 RAG")
    parser.add_argument("--city", help="只入库指定城市（默认全部）")
    parser.add_argument("--reset", action="store_true", help="清空 collection 后重新入库")
    args = parser.parse_args()

    if args.reset:
        before = vectorstore.count()
        logger.info("重置 collection（当前 %d chunks）", before)
        ingest.clear_all()

    base = settings.rag_guides_dir
    if not base.exists():
        logger.error("guides 目录不存在: %s", base)
        logger.error("先运行: python backend/scripts/fetch_guides.py <城市>")
        return

    if args.city:
        target = base / args.city
        if not target.exists():
            logger.error("城市目录不存在: %s", target)
            return
        results = ingest.ingest_directory(target)
    else:
        results = ingest.ingest_directory(base)

    # 汇总
    total_written = sum(r.chunks_written for r in results)
    total_failed = sum(1 for r in results if r.error)
    by_city: dict = {}
    for r in results:
        key = r.city or "?"
        by_city.setdefault(key, {"files": 0, "chunks": 0})
        by_city[key]["files"] += 1
        by_city[key]["chunks"] += r.chunks_written

    print("\n=== 入库汇总 ===")
    print(json.dumps({
        "files": len(results),
        "chunks_written": total_written,
        "failed": total_failed,
        "by_city": by_city,
        "total_in_chroma": vectorstore.count(),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
