"""
FastAPI 主入口

启动：uvicorn app.main:app --reload
"""
import logging
import logging.handlers
import pathlib
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router, health_router, rag_router, memory_router, discover_router, auth_router
from app.config import settings

# ─── 日志：同时输出到控制台和文件 ────────────────────────────────
# 文件按大小轮转（5MB × 3 份），完整堆栈（logger.exception）都能事后追溯
LOG_DIR = pathlib.Path(__file__).resolve().parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

def _setup_logging() -> None:
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if root.handlers:  # uvicorn 已配置过控制台输出，避免重复
        for h in root.handlers:
            h.setFormatter(fmt)
    # --reload 重载会重新 import，避免重复挂载同一个文件
    if any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers):
        return
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_DIR / "app.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

_setup_logging()
logger = logging.getLogger(__name__)


def _prewarm() -> None:
    """后台预热：首次请求不再付冷启动代价（embedding 模型首次加载约 5-7s）"""
    try:
        if settings.memory_enabled:
            import threading
            from app.rag import embedding
            threading.Thread(
                target=embedding.get_embedder, name="prewarm-embedding", daemon=True,
            ).start()
    except Exception as exc:  # 预热失败不影响启动
        logger.warning("prewarm failed: %s", exc)


@asynccontextmanager
async def lifespan(_: FastAPI):
    from app.services.trending_notes import get_trending_notes, refresh_trending_notes, run_daily_trending_refresh
    stop_event = asyncio.Event()
    task = asyncio.create_task(run_daily_trending_refresh(stop_event), name="daily-trending-notes")
    trending_cache = get_trending_notes()
    if not trending_cache["notes"] or trending_cache.get("schema_version") != 2:
        async def initial_refresh():
            try:
                await refresh_trending_notes()
            except Exception as exc:
                logger.warning("热门旅游笔记首次刷新失败：%s", exc)
        asyncio.create_task(initial_refresh(), name="initial-trending-notes")
    yield
    stop_event.set()
    task.cancel()


app = FastAPI(
    title="Trip Agent API",
    description="基于 HelloAgents 框架的旅行规划 Agent",
    version="0.1.0",
    debug=settings.debug,
    lifespan=lifespan,
)

# CORS（H6 前端 Vue3 跑在 5173 端口）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 路由
app.include_router(health_router, tags=["health"])
app.include_router(router)
app.include_router(rag_router)
app.include_router(memory_router)
app.include_router(discover_router)
app.include_router(auth_router)

# 启动预热（后台线程，不阻塞 uvicorn 启动）
_prewarm()


@app.get("/")
async def root() -> dict:
    return {
        "name": "Trip Agent API",
        "version": "0.1.0",
        "docs": "/docs",
        "llm": settings.llm_model_id,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.debug,
    )
