# Loop 策略（P0）

## Goal

生成通过 schema 校验的 `itinerary.json`，并写入 `context/{sessionId}/`。

## Cycle（目标态）

```
intake → parse (B) → verify poi-list → plan (A) → verify itinerary → summarize
```

P0 仅实现：`intake → mock plan → schema verify`

## Stop 条件

- `itinerary.schema.json` 校验通过 → `status: ok`
- 需要人工确认 → `status: needs_confirmation`（P3）
- schema 失败 → `status: failed` + `error.errorCode`
- 同类 repair 失败 2 次 → escalate（P3）

## Budget

- P0：无 LLM 调用
- P1 目标：完整规划 ≤ 2 次 LLM

## Memory

所有状态写入 `context/{sessionId}/`：
- `session.json`
- `trip-meta.json`
- `poi-list.json`
- `itinerary.json`
- `guides/raw.md`

## Escalate

- Session 不存在
- Schema 持续失败
- `weak_evidence` + must POI（P3 人工确认门）
