import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { rm } from "node:fs/promises";
import path from "node:path";
import { invokeTool, type ToolRuntime, type ToolEvent } from "../src/tools/registry.js";
import {
  createSession,
  saveTripMeta,
  savePoiList,
  saveItinerary,
  saveSearchResult,
  loadStore,
  loadTripMeta,
} from "../src/context/store.js";
import { registerAllTools, clearRegistry } from "../src/tools/register-all.js";
import { getTool, listTools } from "../src/tools/registry-impl.js";
import type { TripMeta } from "../src/types/api.js";
import type { Itinerary, Poi, PoiList } from "../src/types/domain.js";

// ─── 测试辅助 ───────────────────────────────────────────────────────────────

const SESSION_ID = "sess_b1b2c3d4e5f6";
const CONTEXT_DIR = path.resolve(process.cwd(), "context", SESSION_ID);

const baseTripMeta: TripMeta = {
  city: "Osaka",
  days: 3,
  pace: "normal",
  mustVisit: ["USJ"],
  avoid: [],
};

function makeRuntime(step = 1): ToolRuntime & { events: ToolEvent[] } {
  const events: ToolEvent[] = [];
  return {
    sessionId: SESSION_ID,
    runId: "run_p2_3_test",
    modeBias: "planner",
    step,
    signal: new AbortController().signal,
    appendEvent: (e) => events.push(e),
    loadStore: async () => loadStore(SESSION_ID),
    events,
  } as ToolRuntime & { events: ToolEvent[] };
}

const basePoi = (over: Partial<Poi>): Poi => ({
  id: "poi_x",
  name: "x",
  category: "attraction",
  area: "Namba",
  source_refs: [{ type: "whitelist", ref: "test" }],
  confidence: 0.8,
  priority: "optional",
  estimated_duration_minutes: 60,
  estimated_cost: 500,
  ...over,
});

async function setupSession(): Promise<void> {
  await createSession(SESSION_ID, baseTripMeta, "test guide");
}

async function cleanupSession(): Promise<void> {
  await rm(CONTEXT_DIR, { recursive: true, force: true });
}

// ─── 测试用例 ───────────────────────────────────────────────────────────────

describe("P2-3 状态与对话控制工具", () => {
  beforeEach(async () => {
    await cleanupSession();
    await setupSession();
    clearRegistry();
    registerAllTools();
  });
  afterEach(async () => {
    await cleanupSession();
    clearRegistry();
  });

  it("registerAllTools 注册 11 个工具", () => {
    expect(listTools().length).toBe(11);
  });

  describe("get_itinerary", () => {
    it("空 itinerary 提示先 build", async () => {
      const tool = getTool("get_itinerary")!;
      const r = await invokeTool(tool, {}, makeRuntime());
      expect(r.ok).toBe(true);
      expect(r.observation).toContain("还没有行程");
    });

    it("有 itinerary 返回天数和 POI 摘要", async () => {
      const itinerary: Itinerary = {
        sessionId: SESSION_ID,
        city: "Osaka",
        version: 1,
        days: [
          {
            day: 1,
            time_blocks: [{ poi_id: "poi_a", start_time: "09:00", end_time: "11:00", reason: "test", locked: false }],
            estimated_total_minutes: 120,
            estimated_total_cost: 500,
            area_cluster: ["Namba"],
          },
        ],
      };
      await saveItinerary(SESSION_ID, itinerary);
      const tool = getTool("get_itinerary")!;
      const r = await invokeTool(tool, {}, makeRuntime());
      expect(r.ok).toBe(true);
      expect(r.observation).toContain("共 1 天");
      expect(r.observation).toContain("poi_a");
    });
  });

  describe("get_poi_list", () => {
    it("空列表", async () => {
      const r = await invokeTool(getTool("get_poi_list")!, {}, makeRuntime());
      expect(r.ok).toBe(true);
      expect(r.observation).toContain("为空");
    });

    it("有 POI 返回 priority 分布", async () => {
      const poiList: PoiList = {
        city: "Osaka",
        pois: [
          basePoi({ id: "poi_a", name: "A", priority: "must" }),
          basePoi({ id: "poi_b", name: "B", priority: "nice" }),
          basePoi({ id: "poi_c", name: "C", priority: "optional" }),
        ],
      };
      await savePoiList(SESSION_ID, poiList);
      const r = await invokeTool(getTool("get_poi_list")!, {}, makeRuntime());
      expect(r.ok).toBe(true);
      expect(r.observation).toContain("must=1");
      expect(r.observation).toContain("nice=1");
    });
  });

  describe("set_trip_meta", () => {
    it("部分更新（只传 days，保留其他字段）", async () => {
      const r = await invokeTool(
        getTool("set_trip_meta")!,
        { days: 5 },
        makeRuntime()
      );
      expect(r.ok).toBe(true);
      expect(r.observation).toContain("5 天");
      // 验证其他字段保留
      const updated = await loadTripMeta(SESSION_ID);
      expect(updated?.days).toBe(5);
      expect(updated?.city).toBe("Osaka"); // 保留
      expect(updated?.pace).toBe("normal"); // 保留
    });

    it("needsConfirm 未设置 → 不触发确认", async () => {
      const rt = makeRuntime();
      await invokeTool(getTool("set_trip_meta")!, { days: 7 }, rt);
      expect(rt.events.some((e) => e.type === "need_confirm")).toBe(false);
    });
  });

  describe("add_pois_to_list", () => {
    it("带 resultId 从 search-cache 加载并去重", async () => {
      const pois: Poi[] = [
        basePoi({ id: "poi_a", name: "A" }),
        basePoi({ id: "poi_b", name: "B" }),
      ];
      await saveSearchResult(SESSION_ID, "res_abc123", { pois });
      const r = await invokeTool(
        getTool("add_pois_to_list")!,
        { resultId: "res_abc123", poiIds: ["poi_a", "poi_b"] },
        makeRuntime()
      );
      expect(r.ok).toBe(true);
      expect(r.observation).toContain("已添加 2 个");
    });
  });

  describe("remove_pois_from_list", () => {
    it("≤3 个不触发确认", async () => {
      const poiList: PoiList = {
        city: "Osaka",
        pois: [
          basePoi({ id: "poi_a", name: "A" }),
          basePoi({ id: "poi_b", name: "B" }),
        ],
      };
      await savePoiList(SESSION_ID, poiList);
      const rt = makeRuntime();
      const r = await invokeTool(
        getTool("remove_pois_from_list")!,
        { poiIds: ["poi_a"] },
        rt
      );
      expect(r.ok).toBe(true);
      expect(rt.events.some((e) => e.type === "need_confirm")).toBe(false);
    });

    it(">3 个触发确认，不执行", async () => {
      const poiList: PoiList = {
        city: "Osaka",
        pois: [
          basePoi({ id: "poi_a", name: "A" }),
          basePoi({ id: "poi_b", name: "B" }),
          basePoi({ id: "poi_c", name: "C" }),
          basePoi({ id: "poi_d", name: "D" }),
        ],
      };
      await savePoiList(SESSION_ID, poiList);
      const rt = makeRuntime();
      const r = await invokeTool(
        getTool("remove_pois_from_list")!,
        { poiIds: ["poi_a", "poi_b", "poi_c", "poi_d"] },
        rt
      );
      expect(r.ok).toBe(false);
      expect(r.error?.errorCode).toBe("need_confirm");
      expect(rt.events.some((e) => e.type === "need_confirm")).toBe(true);
    });
  });

  describe("build_itinerary", () => {
    it("首次（空 itinerary）不确认", async () => {
      const rt = makeRuntime();
      // 先准备 poi-list
      await savePoiList(SESSION_ID, {
        city: "Osaka",
        pois: [basePoi({ id: "poi_a", name: "A", priority: "must" })],
      });
      const r = await invokeTool(getTool("build_itinerary")!, {}, rt);
      expect(r.ok).toBe(true);
      expect(rt.events.some((e) => e.type === "need_confirm")).toBe(false);
    });

    it("已有 itinerary（days>0）触发确认", async () => {
      // 先建一个有内容 itinerary
      await saveItinerary(SESSION_ID, {
        sessionId: SESSION_ID,
        city: "Osaka",
        version: 1,
        days: [
          {
            day: 1,
            time_blocks: [],
            estimated_total_minutes: 0,
            estimated_total_cost: 0,
            area_cluster: [],
          },
        ],
      });
      const rt = makeRuntime();
      const r = await invokeTool(getTool("build_itinerary")!, {}, rt);
      expect(r.ok).toBe(false);
      expect(r.error?.errorCode).toBe("need_confirm");
    });
  });

  describe("verify_itinerary", () => {
    it("空 itinerary 且有 mustVisit 时返回校验失败", async () => {
      const r = await invokeTool(getTool("verify_itinerary")!, {}, makeRuntime());
      expect(r.ok).toBe(false);
      expect(r.observation).toContain("errors");
      expect(r.data).toMatchObject({
        errors: expect.arrayContaining([expect.stringContaining("USJ")]),
      });
    });
  });

  describe("revise_day", () => {
    it("恒定 needsConfirm=true", async () => {
      const rt = makeRuntime();
      const r = await invokeTool(
        getTool("revise_day")!,
        { day: 1, operations: [{ type: "remove", poiId: "poi_a" }] },
        rt
      );
      expect(r.ok).toBe(false);
      expect(r.error?.errorCode).toBe("need_confirm");
    });
  });

  describe("get_search_detail", () => {
    it("不存在的 resultId 返回错误", async () => {
      const r = await invokeTool(
        getTool("get_search_detail")!,
        { resultId: "res_nonexistent" },
        makeRuntime()
      );
      expect(r.ok).toBe(false);
      expect(r.error?.errorCode).toBe("tool_error");
    });

    it("存在的 resultId 返回完整数据", async () => {
      await saveSearchResult(SESSION_ID, "res_xyz", { items: [{ title: "test" }] });
      const r = await invokeTool(
        getTool("get_search_detail")!,
        { resultId: "res_xyz" },
        makeRuntime()
      );
      expect(r.ok).toBe(true);
      expect(r.data).toEqual({ items: [{ title: "test" }] });
    });
  });

  describe("ask_user", () => {
    it("返回 question 作为 observation", async () => {
      const r = await invokeTool(
        getTool("ask_user")!,
        { question: "你想去几天？" },
        makeRuntime()
      );
      expect(r.ok).toBe(true);
      expect(r.observation).toBe("你想去几天？");
    });
  });

  describe("finish", () => {
    it("返回 message 作为 observation", async () => {
      const r = await invokeTool(
        getTool("finish")!,
        { message: "行程已规划完成" },
        makeRuntime()
      );
      expect(r.ok).toBe(true);
      expect(r.observation).toBe("行程已规划完成");
    });
  });
});
