# Trip Agent — 项目规划

旅行规划 Agent：自然语言 / 攻略 → 可执行行程。

## 1. 目标与边界

### 目标
帮用户少搜、少整理、少手工排路线：把自然语言需求或粘贴的攻略，转成可执行的结构化行程。

### 当前范围
- 多城市（南京、北京等，可扩展）
- 2-5 天行程
- 输入：自然语言 query（主）或结构化 trip_meta + 攻略文本
- 输出：结构化 TripPlan（每日时间块、POI、预算、天气）+ 会话状态
- 支持澄清续接（缺字段时追问）

### 不做
- 机酒预订
- 多城市联游编排
- 全自动无人值守长循环

## 2. 演进历程

项目实际起点早于 git。`AGENT_PLAN.md`（6/30，上级目录 `tools_aigc/`）定下 V0.1 规划并锁定 TypeScript，但实施中并行出现了 Python backend，最终收敛为 Python 单实现。

| 时间 | 里程碑 | 说明 |
|------|--------|------|
| 6/30–8/02 | git 前开发期 | AGENT_PLAN.md 定规划；并行开发 TS 脚手架(src) 与 Python backend(H1-H5a) |
| 8/02 | git 初始化 | 首次快照 9e10403：src(23) + backend(27) 并存入库；连提第零期 bug 修复 + 第一期 NL 入口 |
| 8/02 | 第一期 | 自然语言入口 / 意图识别 / 字段校验 / 澄清闭环 |
| 8/10 | 第二期 | RAG 检索 + GLM 自动抓取攻略入库（南京/北京） |
| 8/11 | 第四期 | 复刻 HelloAgents 四层 Memory（working/episodic/semantic/perceptual） |
| 8/15 | 稳定性 | POI 提取稳定性、景点 Agent max_tool_iterations 根治不吐 JSON |
| 8/16 | 性能优化 | 端到端 119s → 54s（MCP 复用、关 GLM thinking、Memory 异步压缩）；修 must 语义稀释 |
| 8/18 | 收敛 | 移除 TS 脚手架，文档重写为 backend 版本，收敛为 Python 单实现 |

## 3. 当前架构

### 技术栈
Python 3.13 · FastAPI · HelloAgents + LangGraph · GLM-5.2（智谱）· ChromaDB + bge-small-zh · MCP

### 模块（backend/app/）

| 模块 | 职责 |
|------|------|
| `agents/` | TripPlanOrchestrator(协调器) · TripIntentRecognizer · TripPlannerAgent · prompts |
| `api/` | FastAPI 路由（plan-nl / plan / session / rag / memory） |
| `core/` | field_validator(字段校验) · constraints(约束) · scheduler(排程) |
| `models/` | pydantic 数据模型（Poi/DayPlan/Itinerary/TripPlan/TripMeta...） |
| `services/` | session_store · clarification_store · amap_service · travel_matrix |
| `tools/` | persistent_mcp · glm_web_search_tool · state_tools |
| `rag/` | retriever · vectorstore · embedding · ingest · text_splitter |
| `memory/` | manager · compressor · working/episodic/semantic/perceptual |

### 数据流（plan-nl 主入口）

```
query → Memory 检索(参考) → IntentRecognizer(LLM 抽 trip_meta)
     → field_validator(确定性必填校验)
     → [缺字段] 澄清闭环 → needs_clarification(可续接)
     → [齐全] TripPlannerAgent(MCP 工具, max_steps=20) → TripPlan
     → 后台异步 Memory 记录 → ok
```

### 数据
- `data/guides/`：攻略 markdown（南京、北京），RAG 数据源
- `data/chroma/`：向量库（本地，.gitignore）
- `backend/context/{session_id}/`：会话运行时状态

## 4. 阶段现状

- [x] 第零期：核心数据管道、POI 提取稳定性
- [x] 第一期：NL 入口 + 意图识别 + 字段校验 + 澄清闭环
- [x] 第二期：RAG 检索 + 攻略自动入库
- [x] 第四期：四层 Memory（检索参考 + 异步写入）
- [x] 性能优化：端到端 54s
- [x] 工程收敛：移除 TS 脚手架，文档对齐
- [ ] 第三期：RAG 知识注入 Planner（orchestrator 已预留 knowledge_context 接口）
- [ ] revise_day 闭环（结构化修改 + 全局校验）
- [ ] Verifier 完整规则（幻觉 POI / 必去覆盖 / 时间窗 / 人工确认门）
- [ ] 测试补全

## 5. 后续路线

1. **第三期 RAG 注入**：把 RAG 检索的攻略知识作为 knowledge_context 注入 Planner prompt
2. **revise_day 闭环**：结构化 operations（remove/move/add）+ 修改后全局 verifier
3. **Verifier 完整化**：幻觉 POI 校验、必去覆盖、时间窗冲突、人工确认门（confirmationRequests）
4. **测试补全**：fixtures + 回归样例
5. **外部接入**：小红书 MCP（已预留）、地图 geocode/route time

## 6. 成功指标（参考 V0.1 规划）

- 幻觉 POI：0
- 必去点覆盖率：100%
- schema pass rate ≥ 95%
- 重复 POI：0
- 明显过载天数：0
- 端到端响应 ≤ 60s（当前 54s）
- 用户 2 次内得到可接受行程

## 7. 设计原则（继承自 AGENT_PLAN.md）

- 旅行行程本质是受约束的排序与分配问题，不是文本生成
- LLM 做抽取与解释；排程/预算/校验用确定性代码
- 每个 POI 必须可追溯（source_refs）
- 失败可降级，外部 connector 不作为硬依赖
