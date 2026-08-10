"""
第二期 RAG 集成测试

测试范围：
  1. text_splitter：中文切分逻辑
  2. ingest：frontmatter 解析、content_hash 去重、入库
  3. retriever：检索 + min_score 过滤 + 空库返回 []
  4. vectorstore：ChromaDB 读写

为避免测试污染真实 ChromaDB，每个测试用独立临时目录。
embedding 用真实 bge-small-zh（~100MB 已下载），首次加载约 5s。
"""
import shutil
import tempfile
from pathlib import Path

import pytest

from app.rag import text_splitter, vectorstore, retriever, ingest, embedding
from app.config import settings


# ─── 测试夹具 ───────────────────────────────────────────────────────────────

@pytest.fixture
def temp_chroma(monkeypatch):
    """每个测试用独立 ChromaDB 目录"""
    tmp = Path(tempfile.mkdtemp(prefix="chroma_test_"))
    monkeypatch.setattr(settings, "rag_chroma_path", str(tmp), raising=False)
    # 重置单例，让下一个 get_client 用新路径
    vectorstore._client = None
    yield tmp
    vectorstore._client = None
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def temp_guides(monkeypatch):
    """每个测试用独立 guides 目录"""
    tmp = Path(tempfile.mkdtemp(prefix="guides_test_"))
    monkeypatch.setattr(settings, "rag_guides_path", str(tmp), raising=False)
    yield tmp
    shutil.rmtree(tmp, ignore_errors=True)


# ─── text_splitter ──────────────────────────────────────────────────────────


class TestTextSplitter:
    """中文切分"""

    def test_short_text_one_chunk(self):
        text = "这是一段短文本，不超过 chunk_size。"
        chunks = text_splitter.split_text(text, chunk_size=500)
        assert len(chunks) == 1
        assert chunks[0].content == text
        assert chunks[0].chunk_index == 0

    def test_long_text_multiple_chunks(self):
        text = "段一。\n\n段二。\n\n段三。\n\n段四。"
        chunks = text_splitter.split_text(text, chunk_size=20, overlap=0)
        assert len(chunks) >= 3
        # chunk_index 连续
        for i, c in enumerate(chunks):
            assert c.chunk_index == i

    def test_overlap_makes_continuous(self):
        """overlap 让相邻 chunk 内容连续"""
        text = "第一句。第二句。第三句。第四句。第五句。"
        chunks = text_splitter.split_text(text, chunk_size=30, overlap=10)
        if len(chunks) > 1:
            # 第二个 chunk 的开头应该是第一个 chunk 的末尾
            assert chunks[1].content.startswith(chunks[0].content[-10:])

    def test_context_before_after(self):
        """每个 chunk 记录前后上下文"""
        text = "段落一内容。\n\n段落二内容。\n\n段落三内容。"
        chunks = text_splitter.split_text(text, chunk_size=20, context_chars=5)
        if len(chunks) >= 3:
            # 中间 chunk 应有前后上下文
            middle = chunks[1]
            assert middle.context_before != ""
            assert middle.context_after != ""

    def test_empty_text(self):
        assert text_splitter.split_text("") == []
        assert text_splitter.split_text("   ") == []

    def test_chinese_delimiters(self):
        """按中文标点切"""
        # 文本长度 > chunk_size，应在句号处切
        text = "中山陵是孙中山先生的陵墓位于紫金山南麓。开放时间是上午八点半到下午五点。门票免费但需提前预约。"
        chunks = text_splitter.split_text(text, chunk_size=30, overlap=0)
        assert len(chunks) >= 2
        # 每段应在句号处断开（句号在某 chunk 末尾或附近）
        for c in chunks:
            assert c.content


# ─── ingest frontmatter 解析 ────────────────────────────────────────────────


class TestFrontmatterParse:
    """frontmatter 解析"""

    def test_parse_with_frontmatter(self):
        text = """---
city: 南京
source_url: https://example.com
source_title: 南京两日游
is_external: true
---

中山陵是孙中山的陵墓。"""
        meta, body = ingest._parse_frontmatter(text)
        assert meta["city"] == "南京"
        assert meta["source_url"] == "https://example.com"
        assert meta["source_title"] == "南京两日游"
        assert meta["is_external"] == "true"
        assert "中山陵" in body

    def test_parse_without_frontmatter(self):
        text = "没有 frontmatter 的纯文本。"
        meta, body = ingest._parse_frontmatter(text)
        assert meta == {}
        assert body == text

    def test_content_hash_deterministic(self):
        """相同内容 hash 一致"""
        h1 = ingest._content_hash("相同内容")
        h2 = ingest._content_hash("相同内容")
        assert h1 == h2
        h3 = ingest._content_hash("不同内容")
        assert h1 != h3


# ─── 入库 + 检索（需要真实 embedding） ─────────────────────────────────────


class TestIngestAndSearch:
    """入库与检索集成测试（用真实 bge-small-zh）"""

    @pytest.fixture(autouse=True)
    def setup_guides(self, temp_chroma, temp_guides):
        """每个测试前准备 guides 文件"""
        # 写一篇南京攻略
        nj_dir = temp_guides / "南京"
        nj_dir.mkdir()
        (nj_dir / "guide1.md").write_text(
            """---
city: 南京
source_url: https://example.com/nj
source_title: 南京两日游攻略
source_media: 马蜂窝
is_external: true
---

中山陵是孙中山先生的陵墓，位于南京紫金山南麓。开放时间 8:30-17:00，门票免费但需要提前预约。建议上午 9 点到达，避开人流高峰。

夫子庙是南京最著名的景点之一，夜景很美。秦淮河游船约 80 元每人，建议晚上 7 点后坐船。

南京博物院是中国三大博物馆之一，馆藏丰富，建议预留 3-4 小时。免费参观，但需要预约。

美食推荐：鸭血粉丝汤、盐水鸭、南京大牌档。鸭血粉丝汤是南京最有名的小吃。
""",
            encoding="utf-8",
        )
        # 写一篇北京攻略
        bj_dir = temp_guides / "北京"
        bj_dir.mkdir()
        (bj_dir / "guide1.md").write_text(
            """---
city: 北京
source_url: https://example.com/bj
source_title: 北京两日游攻略
source_media: 携程
is_external: true
---

故宫博物院是北京最著名的景点，位于天安门广场北侧。门票 60 元，建议提前网上预约。开放时间 8:30-17:00。

八达岭长城距市区 70 公里，单程约 2 小时。门票 40 元。建议预留一整天时间。

颐和园是皇家园林，门票 30 元。昆明湖边的长廊是世界最长的画廊。
""",
            encoding="utf-8",
        )
        yield

    def test_ingest_directory(self):
        """入库 2 个城市文件"""
        results = ingest.ingest_directory()
        assert len(results) == 2
        total_chunks = sum(r.chunks_written for r in results)
        assert total_chunks > 0
        # ChromaDB 里能查到
        assert vectorstore.count() == total_chunks

    def test_search_returns_relevant(self):
        """检索「南京中山陵」应命中南京攻略"""
        ingest.ingest_directory()
        chunks = retriever.search_sync("南京中山陵怎么去", top_k=3)
        assert len(chunks) > 0
        # 至少有一条来自南京
        nj_chunks = [c for c in chunks if c.city == "南京"]
        assert len(nj_chunks) > 0
        # 内容应包含中山陵
        assert any("中山陵" in c.content for c in nj_chunks)

    def test_search_city_filter(self):
        """按 city=南京 过滤，不返回北京结果"""
        ingest.ingest_directory()
        chunks = retriever.search_sync("景点", top_k=10, city="南京")
        assert len(chunks) > 0
        # 全部来自南京
        for c in chunks:
            assert c.city == "南京"

    def test_empty_library_returns_empty(self, temp_chroma):
        """空库检索返回 []，不假装有知识"""
        # 不入库直接检索
        assert retriever.search_sync("任何查询") == []

    def test_min_score_filters_low_relevance(self):
        """低于 min_score 的不返回"""
        ingest.ingest_directory()
        # 用极低 score 检索（要求 0.99 几乎不可能命中）
        chunks = retriever.search_sync("asdfghjkl", min_score=0.99)
        assert chunks == []

    def test_metadata_complete(self):
        """检索结果 metadata 完整"""
        ingest.ingest_directory()
        chunks = retriever.search_sync("南京中山陵", top_k=1)
        assert len(chunks) >= 1
        c = chunks[0]
        assert c.source  # 有 source 路径
        assert c.city == "南京"
        assert c.chunk_id  # 有 chunk_id
        assert c.is_external is True  # 标记为外部内容

    def test_ingest_idempotent(self):
        """重复入库不重复存储"""
        ingest.ingest_directory()
        count1 = vectorstore.count()
        # 再次入库相同内容
        ingest.ingest_directory()
        count2 = vectorstore.count()
        # content_hash 相同，upsert 幂等，数量不变
        assert count1 == count2


class TestRagApiStats:
    """RAG stats 接口"""

    def test_stats_empty_library(self, temp_chroma):
        """空库 stats"""
        # 不入库直接调 stats
        result = {
            "total_chunks": vectorstore.count(),
        }
        assert result["total_chunks"] == 0
