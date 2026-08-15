"""
应用配置 - 从 .env 读取
"""
import os
from dotenv import load_dotenv
from pathlib import Path

# backend/.env
BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_DIR / ".env")


class Settings:
    # LLM（HelloAgents 框架）
    llm_model_id: str = os.getenv("LLM_MODEL_ID", "glm-5.2")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
    # glm-5.2 默认 thinking.type=enabled，每轮调用先跑思考链（实测 14s vs 关思考 5.5s）。
    # 研究 Agent 的工具调用是结构化任务，思考收益低。试验证实关思考无行为退化
    # （轮次/JSON/POI 不变，attraction 67s → 21s）。默认开，env 可回退。
    llm_thinking_disabled: bool = os.getenv("LLM_THINKING_DISABLED", "true").lower() == "true"

    # 高德地图
    amap_api_key: str = os.getenv("AMAP_API_KEY", "")

    # Unsplash
    unsplash_access_key: str = os.getenv("UNSPLASH_ACCESS_KEY", "")

    # 小红书 MCP（H9 预留）
    xiaohongshu_mcp_url: str = os.getenv("XIAOHONGSHU_MCP_URL", "http://localhost:18060/mcp")

    # 应用
    app_host: str = os.getenv("APP_HOST", "0.0.0.0")
    app_port: int = int(os.getenv("APP_PORT", "8000"))
    debug: bool = os.getenv("DEBUG", "true").lower() == "true"

    # context 目录（session 文件存储）
    context_root: str = os.getenv("CONTEXT_ROOT", "context")

    # RAG 检索配置（第二期）
    rag_embedding_model: str = os.getenv("RAG_EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
    rag_embedding_model_path: str = os.getenv("RAG_EMBEDDING_MODEL_PATH", "")
    rag_device: str = os.getenv("RAG_DEVICE", "cpu")
    rag_batch_size: int = int(os.getenv("RAG_BATCH_SIZE", "32"))
    rag_chroma_path: str = os.getenv("RAG_CHROMA_PATH", "data/chroma")
    rag_guides_path: str = os.getenv("RAG_GUIDES_PATH", "data/guides")
    rag_chunk_size: int = int(os.getenv("RAG_CHUNK_SIZE", "500"))
    rag_chunk_overlap: int = int(os.getenv("RAG_CHUNK_OVERLAP", "50"))
    rag_top_k: int = int(os.getenv("RAG_TOP_K", "3"))
    rag_min_score: float = float(os.getenv("RAG_MIN_SCORE", "0.35"))
    rag_debug_response: bool = os.getenv("RAG_DEBUG_RESPONSE", "false").lower() == "true"

    @property
    def context_dir(self) -> Path:
        """context 绝对路径"""
        return self._resolve_context_dir()

    def _resolve_context_dir(self) -> Path:
        p = Path(self.context_root)
        if not p.is_absolute():
            p = BACKEND_DIR / p
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def rag_chroma_dir(self) -> Path:
        """ChromaDB 持久化目录（绝对路径）"""
        p = Path(self.rag_chroma_path)
        if not p.is_absolute():
            p = BACKEND_DIR.parent / p   # 项目根/data/chroma
        return p

    @property
    def rag_guides_dir(self) -> Path:
        """攻略文件目录（绝对路径）"""
        p = Path(self.rag_guides_path)
        if not p.is_absolute():
            p = BACKEND_DIR.parent / p   # 项目根/data/guides
        return p

    def set_context_root(self, path) -> None:
        """测试用：覆盖 context_root"""
        self.context_root = str(path)

    def set_rag_chroma_path(self, path) -> None:
        """测试用：覆盖 chroma 路径"""
        self.rag_chroma_path = str(path)

    # Memory 配置（第四期，复刻 HelloAgents Memory 架构）
    memory_enabled: bool = os.getenv("MEMORY_ENABLED", "true").lower() == "true"
    memory_storage_path: str = os.getenv("MEMORY_STORAGE_PATH", "data/memory")
    memory_working_capacity: int = int(os.getenv("MEMORY_WORKING_CAPACITY", "10"))
    memory_working_ttl_minutes: int = int(os.getenv("MEMORY_WORKING_TTL_MINUTES", "120"))
    memory_importance_threshold: float = float(os.getenv("MEMORY_IMPORTANCE_THRESHOLD", "0.1"))
    memory_compress_mode: str = os.getenv("MEMORY_COMPRESS_MODE", "llm")  # llm | template
    memory_compress_timeout: int = int(os.getenv("MEMORY_COMPRESS_TIMEOUT", "15"))
    memory_compress_fallback: str = os.getenv("MEMORY_COMPRESS_FALLBACK", "template")

    @property
    def memory_dir(self) -> Path:
        """Memory 存储目录（绝对路径）"""
        p = Path(self.memory_storage_path)
        if not p.is_absolute():
            p = BACKEND_DIR.parent / p
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def memory_db_path(self) -> Path:
        """Memory SQLite 路径"""
        return self.memory_dir / "memory.db"

    def set_memory_storage_path(self, path) -> None:
        """测试用：覆盖 memory 存储目录"""
        self.memory_storage_path = str(path)


settings = Settings()
