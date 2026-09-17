# Trip Agent — Agent 指引

旅行规划 Agent：自然语言 / 攻略 → 可执行行程。

## 唯一实现

`backend/`（Python）。原 `src/` TypeScript 脚手架已于 2026-08-18 删除，不再维护。

## 项目边界

- 多城市（南京、北京等）、2-5 天
- 自然语言入口为主：意图识别抽 trip_meta → 确定性字段校验 → 澄清闭环 → Planner 生成
- LLM 做意图抽取与规划编排；字段校验、排程、约束用确定性代码
- 外部能力（高德、GLM web_search、小红书 MCP）在 `backend/app/tools` 与 `backend/app/services`，失败可降级
- 路线地图可视化前端在 `frontend/`，以对话路线消息为主交互形态

## 修改代码时

1. 先读 `LOOP.md` 了解 plan-nl 协调流程
2. 数据模型在 `backend/app/models/schemas.py`（pydantic），改后跑 `cd backend && pytest`
3. LLM 与外部服务配置见 `backend/app/config.py`，密钥在 `backend/.env`（勿提交）

## 关键目录

| 目录 | 职责 |
|------|------|
| `backend/app/agents/` | 编排协调器、意图识别、TripPlanner、prompts |
| `backend/app/api/` | FastAPI 路由 |
| `backend/app/core/` | 字段校验、约束校验、排程 |
| `backend/app/models/` | pydantic 数据模型 |
| `backend/app/services/` | session_store、地图数据、澄清、高德、travel_matrix |
| `backend/app/tools/` | MCP 连接、GLM web_search、状态工具 |
| `backend/app/rag/` | 攻略检索（ChromaDB + bge-small-zh） |
| `backend/app/memory/` | 四层记忆 working/episodic/semantic/perceptual |
| `data/guides/` | 攻略原文（RAG 数据源） |
| `data/chroma/` | 向量库（本地，.gitignore） |

## 入口与状态

- `POST /api/trip/plan-nl`：自然语言规划（主入口，带澄清续接）
- `POST /api/trip/plan`：结构化规划
- `GET /api/trip/session/:id`：会话状态
- `GET /api/trip/session/:id/map`：对话路线消息使用的每日地图数据
- `GET /api/rag/search` / `/api/rag/stats`：RAG 调试
- `GET /api/memory/:user_id/*`：Memory 调试

## 地图路线消息

- 后端：`GET /api/trip/session/{session_id}/map` 从已有 session 构建稳定 `map_data`，不重新调用 LLM。
- 前端：`frontend/` 提供本地可运行的对话路线消息演示，支持日期切换、marker/时间轴联动和发送路线详情。
- 配置：`cp frontend/.env.example frontend/.env`，填写高德 Web JS API Key；服务端密钥不得放入前端。


- 第一~四期成型：NL 规划 / 澄清闭环 / RAG / Memory
- 性能基线：端到端约 54s（关 GLM thinking + MCP 复用 + Memory 异步压缩）
- 待办：测试补全、文档细化
