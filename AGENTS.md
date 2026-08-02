# Trip Agent — Cursor Agent 指引

本项目是一个旅行规划 Agent（攻略 → 可执行行程）。

## 项目边界

- V0.1：单城市（大阪）、3-5 天、粘贴攻略输入
- LLM 只做抽取；排程/预算/校验用确定性代码
- 外部 connector（redbook、地图）放 `src/connectors/`，失败可降级

## 修改代码时

1. 先读 `LOOP.md` 了解 loop 策略
2. 按阶段读对应 Skill：`.cursor/skills/trip-*`
3. 改 schema 后跑 `npm test`
4. 不要在不相关阶段加载 `guides/raw.md`

## 关键目录

| 目录 | 职责 |
|------|------|
| `src/agent/` | 编排、loop、orchestrator |
| `src/tools/` | 确定性工具 |
| `src/verifier/` | 校验与 repair |
| `src/connectors/` | 外部 API（P4） |
| `schemas/` | 数据契约 |
| `context/` | 运行时状态（gitignore） |

## 当前阶段

**P0 完成**：API mock + schema + 数据文件  
**下一步 P1**：真实 `build_itinerary` + verifier 基础规则
