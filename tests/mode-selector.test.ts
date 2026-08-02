import { describe, it, expect } from "vitest";
import { selectModeBias } from "../src/agent/router/mode-selector.js";
import type { IntentType, ModeBias } from "../src/agent/intent/types.js";

describe("selectModeBias", () => {
  const cases: Array<{ intent: IntentType; modeBias: ModeBias }> = [
    { intent: "new_plan", modeBias: "planner" },
    { intent: "revise_day", modeBias: "reflection" },
    { intent: "verify", modeBias: "reflection" },
    { intent: "search_poi", modeBias: "react" },
    { intent: "search_hotel", modeBias: "react" },
    { intent: "search_flight", modeBias: "react" },
    { intent: "search_train", modeBias: "react" },
    { intent: "general_chat", modeBias: "react" },
  ];

  for (const c of cases) {
    it(`${c.intent} → ${c.modeBias}`, () => {
      expect(selectModeBias(c.intent)).toBe(c.modeBias);
    });
  }

  it("覆盖全部 IntentType", () => {
    // 确保测试覆盖了所有 IntentType 联合成员
    const allIntents: IntentType[] = [
      "new_plan",
      "revise_day",
      "verify",
      "search_poi",
      "search_hotel",
      "search_flight",
      "search_train",
      "general_chat",
    ];
    for (const intent of allIntents) {
      const mode = selectModeBias(intent);
      expect(["react", "planner", "reflection"]).toContain(mode);
    }
  });

  it("纯映射无副作用（多次调用一致）", () => {
    const m1 = selectModeBias("new_plan");
    const m2 = selectModeBias("new_plan");
    expect(m1).toBe(m2);
  });
});
