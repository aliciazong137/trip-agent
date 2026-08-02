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

    def set_context_root(self, path) -> None:
        """测试用：覆盖 context_root"""
        self.context_root = str(path)


settings = Settings()
