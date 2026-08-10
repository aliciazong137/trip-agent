"""
自动抓取攻略入库（第二期 RAG）

用 GLMWebSearchTool 搜「{city} {N} 日游攻略」等 query，
把 search_result 写成带 frontmatter 的 Markdown 存到 data/guides/{city}/。

调用：
    python backend/scripts/fetch_guides.py 南京
    python backend/scripts/fetch_guides.py 南京 --days 3

已知风险（写入计划）：
    GLM 夸克搜索的 content 是网页摘要，大概率是 SEO 介绍性内容，
    而非真实游记里的"玩法建议/避坑经验"。
    第二期验收时人工判断 RAG 增量价值。
"""
import argparse
import asyncio
import hashlib
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List

# 让脚本能在 backend/ 下直接跑
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.tools.glm_web_search_tool import GLMWebSearchTool
from app.config import settings


logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


# 不同场景的搜索模板
_QUERY_TEMPLATES = [
    "{city} {days} 日游攻略",
    "{city} 必去景点 推荐",
    "{city} 美食 推荐 当地",
    "{city} 避坑 提示 注意事项",
]


def _fetch_one_query(tool: GLMWebSearchTool, query: str, count: int = 5) -> List[dict]:
    """
    调 GLM web search 一次，返回 search_result 列表

    GLMWebSearchTool.run 返回的是整合后的文本，不是 JSON。
    我们这里绕过 Tool 直接调 httpx，拿原始 search_result。
    """
    import httpx
    api_key = settings.llm_api_key
    base_url = settings.llm_base_url or "https://open.bigmodel.cn/api/paas/v4"

    try:
        resp = httpx.post(
            f"{base_url}/web_search",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "search_query": query,
                "search_engine": "search_pro_quark",
                "search_intent": True,
                "count": count,
                "search_recency_filter": "oneMonth",
            },
            timeout=30,
        )
        data = resp.json()
        return data.get("search_result", [])
    except Exception as e:
        logger.warning("  查询失败「%s」: %s", query, e)
        return []


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def fetch_guides_for_city(city: str, days: int = 2, max_files: int = 50) -> dict:
    """
    抓取一个城市的攻略，存到 data/guides/{city}/

    Args:
        city: 城市名
        days: 天数（用于 query 模板）
        max_files: 单次抓取上限

    Returns:
        {"city": city, "queries": N, "files_written": M, "duplicates": K, "errors": E}
    """
    out_dir = settings.rag_guides_dir / city
    out_dir.mkdir(parents=True, exist_ok=True)

    tool = GLMWebSearchTool()
    queries = [t.format(city=city, days=days) for t in _QUERY_TEMPLATES]
    logger.info("=== 抓取 %s 攻略 ===", city)
    logger.info("queries: %s", queries)

    files_written = 0
    duplicates = 0
    errors = 0
    total = 0

    for q in queries:
        results = _fetch_one_query(tool, q, count=5)
        logger.info("「%s」拿到 %d 条结果", q, len(results))
        for r in results:
            total += 1
            if total > max_files:
                logger.info("达到单次上限 %d，停止", max_files)
                break

            content = (r.get("content") or "").strip()
            title = r.get("title", "").strip()
            link = r.get("link", "").strip()
            media = r.get("media", "").strip()
            publish_date = r.get("publish_date", "").strip()

            if not content or len(content) < 50:
                # 太短的内容跳过（搜索摘要常是 100-300 字）
                continue

            chash = _content_hash(content)
            filename = f"{chash}.md"
            out_path = out_dir / filename

            if out_path.exists():
                duplicates += 1
                continue

            # 写成带 frontmatter 的 Markdown
            frontmatter = [
                "---",
                f"city: {city}",
                f"source_url: {link}",
                f"source_title: {title}",
                f"source_media: {media}",
                f"publish_date: {publish_date}",
                f"fetched_at: {datetime.now(timezone.utc).isoformat()}",
                "is_external: true",
                f"query: {q}",
                "---",
                "",
                content,
            ]
            try:
                out_path.write_text("\n".join(frontmatter), encoding="utf-8")
                files_written += 1
                logger.info("  写入 %s (%d 字)", filename, len(content))
            except Exception as e:
                errors += 1
                logger.warning("  写入失败 %s: %s", filename, e)

    summary = {
        "city": city,
        "days": days,
        "queries": len(queries),
        "total_results": total,
        "files_written": files_written,
        "duplicates": duplicates,
        "errors": errors,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }

    # 写抓取报告
    report_path = out_dir / "_fetch_report.json"
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("=== 完成 %s: 写入 %d, 重复 %d, 失败 %d ===", city, files_written, duplicates, errors)
    return summary


def main():
    parser = argparse.ArgumentParser(description="抓取城市攻略入库 RAG")
    parser.add_argument("cities", nargs="+", help="城市名（可多个）")
    parser.add_argument("--days", type=int, default=2, help="行程天数（用于搜索 query）")
    parser.add_argument("--max-files", type=int, default=50, help="单次抓取文件上限")
    args = parser.parse_args()

    summaries = []
    for city in args.cities:
        s = fetch_guides_for_city(city, days=args.days, max_files=args.max_files)
        summaries.append(s)

    print("\n=== 抓取汇总 ===")
    print(json.dumps(summaries, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
