import { describe, it, expect } from "vitest";
import { recognizeIntent } from "../src/agent/intent/recognizer.js";
import type { IntentType, ModeBias } from "../src/agent/intent/types.js";

describe("recognizeIntent", () => {
  describe("规则正向命中", () => {
    const cases: Array<{ id: string; text: string; intent: IntentType; modeBias: ModeBias }> = [
      { id: "new_plan", text: "帮我规划一个 5 天的大阪行程", intent: "new_plan", modeBias: "planner" },
      { id: "new_plan", text: "安排 3 天的京都游", intent: "new_plan", modeBias: "planner" },
      { id: "new_plan", text: "帮我做个 7 日游计划", intent: "new_plan", modeBias: "planner" },
      { id: "revise_day", text: "把第二天的景点换成道顿堀", intent: "revise_day", modeBias: "reflection" },
      { id: "revise_day", text: "修改第 3 天的日程", intent: "revise_day", modeBias: "reflection" },
      { id: "revise_day", text: "去掉 poi_x 这个景点", intent: "revise_day", modeBias: "reflection" },
      { id: "verify", text: "帮我检查一下这个行程有没有问题", intent: "verify", modeBias: "reflection" },
      { id: "verify", text: "这样安排会超预算吗", intent: "verify", modeBias: "reflection" },
      { id: "verify", text: "verify my itinerary", intent: "verify", modeBias: "reflection" },
      { id: "search_poi", text: "大阪有什么好玩的景点", intent: "search_poi", modeBias: "react" },
      { id: "search_poi", text: "推荐一些景点", intent: "search_poi", modeBias: "react" },
      { id: "search_poi", text: "去哪玩比较好", intent: "search_poi", modeBias: "react" },
      { id: "search_hotel", text: "大阪住哪比较方便", intent: "search_hotel", modeBias: "react" },
      { id: "search_hotel", text: "帮我找家酒店", intent: "search_hotel", modeBias: "react" },
      { id: "search_hotel", text: "附近有什么民宿", intent: "search_hotel", modeBias: "react" },
      { id: "search_flight", text: "帮我查一下机票", intent: "search_flight", modeBias: "react" },
      { id: "search_flight", text: "北京飞大阪的航班", intent: "search_flight", modeBias: "react" },
      { id: "search_train", text: "东京到大阪的新干线", intent: "search_train", modeBias: "react" },
      { id: "search_train", text: "帮我查 12306 的火车票", intent: "search_train", modeBias: "react" },
      { id: "search_train", text: "jr pass 要怎么买", intent: "search_train", modeBias: "react" },
    ];

    for (const c of cases) {
      it(`[${c.id}] "${c.text}" → ${c.intent} + ${c.modeBias}`, () => {
        const r = recognizeIntent(c.text);
        expect(r.intent).toBe(c.intent);
        expect(r.modeBias).toBe(c.modeBias);
        expect(r.matchedRuleId).toBe(c.id);
        expect(r.confidence).toBe(1);
      });
    }
  });

  describe("规则反向用例（不命中该规则）", () => {
    it("纯问候不命中任何规则 → general_chat", () => {
      const r = recognizeIntent("你好，今天天气怎么样");
      expect(r.intent).toBe("general_chat");
      expect(r.modeBias).toBe("react");
      expect(r.confidence).toBe(0);
      expect(r.matchedRuleId).toBeUndefined();
    });

    it("纯英文 search hotel → search_hotel + react（验证 i flag）", () => {
      const r = recognizeIntent("search hotel in osaka");
      expect(r.intent).toBe("search_hotel");
      expect(r.modeBias).toBe("react");
      expect(r.confidence).toBe(1);
    });

    it("纯英文 verify → verify + reflection", () => {
      const r = recognizeIntent("please verify my plan");
      expect(r.intent).toBe("verify");
      expect(r.modeBias).toBe("reflection");
      expect(r.confidence).toBe(1);
    });
  });

  describe("兜底场景", () => {
    it("空字符串 → general_chat + react", () => {
      const r = recognizeIntent("");
      expect(r.intent).toBe("general_chat");
      expect(r.modeBias).toBe("react");
      expect(r.confidence).toBe(0);
      expect(r.matchedRuleId).toBeUndefined();
    });

    it("纯空白 → general_chat + react", () => {
      const r = recognizeIntent("   \t\n  ");
      expect(r.intent).toBe("general_chat");
      expect(r.modeBias).toBe("react");
      expect(r.confidence).toBe(0);
    });

    it("无关文本 → general_chat + react", () => {
      const r = recognizeIntent("今天吃什么好呢");
      expect(r.intent).toBe("general_chat");
      expect(r.modeBias).toBe("react");
      expect(r.confidence).toBe(0);
    });
  });

  describe("规则优先级", () => {
    it("同时命中 new_plan 和 search_poi → 返回声明顺序靠前的 new_plan", () => {
      // "规划" 命中 new_plan，"景点" 命中 search_poi，new_plan 在前
      const r = recognizeIntent("帮我规划几个好玩的景点");
      expect(r.intent).toBe("new_plan");
      expect(r.modeBias).toBe("planner");
      expect(r.matchedRuleId).toBe("new_plan");
    });

    it("同时命中 verify 和 revise_day → 返回声明顺序靠前的 revise_day", () => {
      // "修改" 命中 revise_day，"有没有问题" 命中 verify，revise_day 在前
      const r = recognizeIntent("修改第 1 天，看看有没有问题");
      expect(r.intent).toBe("revise_day");
      expect(r.matchedRuleId).toBe("revise_day");
    });
  });

  describe("纯函数性质", () => {
    it("同一输入多次调用结果一致", () => {
      const text = "帮我规划 5 天行程";
      const r1 = recognizeIntent(text);
      const r2 = recognizeIntent(text);
      expect(r1).toEqual(r2);
    });

    it("不修改输入字符串", () => {
      const text = "帮我规划 5 天行程";
      const textCopy = text;
      recognizeIntent(text);
      expect(text).toBe(textCopy);
    });
  });
});
