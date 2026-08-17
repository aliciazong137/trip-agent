# Trip Agent

旅行规划 Agent — 自然语言 → 可执行行程。

## 技术栈

- Python 3.13 + FastAPI
- HelloAgents 框架 + LangGraph
- GLM-5.2（智谱，OpenAI 兼容）
- ChromaDB + sentence-transformers（RAG）
- MCP（工具调用）

## 快速开始

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # 填入 LLM_API_KEY / AMAP_API_KEY
uvicorn app.main:app --reload
```

- API：http://localhost:8000（Swagger：/docs）
- 前端 Vue3：localhost:5173（CORS 已放行，前端不在本仓库）

## API

### `POST /api/trip/plan-nl`（自然语言，主入口）

```bash
curl -s http://localhost:8000/api/trip/plan-nl \
  -H 'Content-Type: application/json' \
  -d '{"query": "我想去南京玩2天"}'
```

字段缺失时返回 `needs_clarification` + `session_id`，用户补充后带同一 `session_id` 续接。

### `POST /api/trip/plan`（结构化）

```bash
curl -s http://localhost:8000/api/trip/plan \
  -H 'Content-Type: application/json' \
  -d '{"trip_meta": {"city": "南京", "days": 2, "pace": "normal", "must_visit": [], "avoid": []}}'
```

### 其他

- `GET /api/trip/session/{session_id}`：会话状态
- `GET /api/rag/search?q=南京+中山陵&top_k=5&city=南京`：RAG 检索
- `GET /api/memory/{user_id}/search?q=...`：Memory 检索
- `GET /health`：健康检查

## 测试

```bash
cd backend && pytest
```

## 数据

- `data/guides/`：攻略 markdown（南京、北京），RAG 数据源
- `data/chroma/`：向量库（本地生成，.gitignore 忽略）

详见 `AGENTS.md`（目录职责）与 `LOOP.md`（规划流程）。
