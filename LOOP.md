# Loop 策略（plan-nl 协调流程）

## Goal

从自然语言 query 生成可执行的 `TripPlan`，写入 `backend/context/{session_id}/`。

## Cycle

```
query → Memory 检索(参考) → IntentRecognizer(LLM 抽 trip_meta)
     → field_validator(确定性必填校验)
     → [缺字段] 澄清闭环: 存 pending + 追问 → needs_clarification(可续接)
     → [齐全]   TripPlannerAgent(MCP 工具, max_steps=20) → TripPlan
     → 后台异步 Memory 记录(压缩, 不阻塞响应) → ok
```

## 关键组件

- `TripPlanOrchestrator`（`agents/trip_orchestrator.py`）：协调器，单例
- `TripIntentRecognizer`（`agents/intent_recognizer.py`）：LLM 抽 trip_meta
- `field_validator`（`core/field_validator.py`）：确定性必填校验 + 澄清问题生成
- `clarification_store`（`services/clarification_store.py`）：澄清状态持久化、query 合并续接
- `TripPlannerAgent`（`agents/trip_planner.py`）：MCP 工具调用循环
- `MemoryManager`（`memory/manager.py`）：四层记忆，规划成功后后台异步写入

## Stop 条件

- 字段齐全 + Planner 成功 → `status: ok` + `trip_plan`
- 字段缺失/非法 → `status: needs_clarification` + `missing_fields`
- 非旅行意图 → `status: failed` + `UNSUPPORTED_INTENT`
- Planner 抛异常 → `status: failed` + `PLANNER_ERROR`
- POI 提取为空 → `status: failed` + `POI_EXTRACTION_EMPTY`

## Memory 策略

- 检索：规划前查 Memory 作偏好参考，**不补全** city/days 等本次事实
- 写入：规划成功后后台异步压缩记录（working + episodic + semantic），不阻塞响应
- 隔离：按 `user_id`（未登录用 `default_user`）

## Budget

- 单次规划：1 次意图识别 LLM + Planner 内多步工具调用（max_steps=20）
- 端到端基线约 54s（关 GLM thinking）
