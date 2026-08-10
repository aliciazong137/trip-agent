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


settings = Settings()
