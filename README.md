# Trip Agent

旅行规划 Agent — P0 脚手架。

## 技术栈

- TypeScript + Node.js 22
- Hono（API）
- Ajv（Schema 校验）
- Vitest（测试）

## 快速开始

```bash
cd ~/Desktop/trip_agent
npm install
npm run build
npm test
npm run dev
```

服务默认：`http://localhost:3000`

## API

### `POST /plan`

```bash
curl -s http://localhost:3000/plan \
  -H 'Content-Type: application/json' \
  -d '{
    "guideText": "第一天道顿堀，第二天环球影城",
    "tripMeta": {
      "city": "osaka",
      "days": 2,
      "startDate": "2026-09-01",
      "budget": { "currency": "JPY", "amount": 80000 },
      "pace": "normal",
      "mustVisit": ["USJ"],
      "avoid": []
    }
  }'
```

### `POST /revise-day`

```bash
curl -s http://localhost:3000/revise-day \
  -H 'Content-Type: application/json' \
  -d '{
    "sessionId": "sess_xxx",
    "day": 2,
    "instruction": "Day2 想轻松一点",
    "lockedItemIds": []
  }'
```

### `GET /session/:id`

```bash
curl -s http://localhost:3000/session/sess_xxx
```

## P0 状态

- [x] 目录骨架
- [x] 5 个 JSON Schema
- [x] 大阪区域矩阵 + POI 白名单样本
- [x] 3 个最小 API（mock 数据）
- [ ] B 阶段攻略解析（P2）
- [ ] 真实排程工具（P1）
- [ ] Verifier 完整规则（P1-P3）

详细设计见上级目录 `tools_aigc/AGENT_PLAN.md` 或复制到本项目的 `docs/ARCHITECTURE.md`。
