"""
FastAPI 主入口

启动：uvicorn app.main:app --reload
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router, health_router, rag_router
from app.config import settings

app = FastAPI(
    title="Trip Agent API",
    description="基于 HelloAgents 框架的旅行规划 Agent",
    version="0.1.0",
    debug=settings.debug,
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
