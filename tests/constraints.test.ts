import { describe, it, expect } from "vitest";
import { checkConstraints } from "../src/verifier/check-constraints.js";
import type { Itinerary, Poi, PoiList, TimeBlock } from "../src/types/domain.js";
import type { TripMeta } from "../src/types/api.js";

const poi = (id: string, over: Partial<Poi> = {}): Poi => ({
  id: id as Poi["id"],
  name: id,
  category: "attraction",
  area: "Namba",
  source_refs: [{ type: "whitelist", ref: "test" }],
  confidence: 0.8,
  priority: "must",
  estimated_duration_minutes: 90,
  estimated_cost: 500,
  ...over,
});

const block = (poiId: string, over: Partial<TimeBlock> = {}): TimeBlock => ({
  poi_id: poiId as Poi["id"],
  start_time: "10:00",
  end_time: "11:30",
  reason: "测试",
  ...over,
});

const poiList = (pois: Poi[]): PoiList => ({ city: "osaka", pois });

const trip = (over: Partial<TripMeta> = {}): TripMeta => ({
  city: "osaka",
  days: 1,
  pace: "normal",
  mustVisit: [],
  avoid: [],
  ...over,
});

const itinerary = (days: Itinerary["days"]): Itinerary => ({
  sessionId: "sess_test",
  city: "osaka",
  version: 1,
  days,
});

describe("checkConstraints", () => {
  it("POI 不在 poi-list 中报 error", () => {
    const it = itinerary([
      {
        day: 1,
        area_cluster: ["Namba"],
        time_blocks: [block("poi_ghost")],
        estimated_total_cost: 0,
        estimated_total_minutes: 90,
      },
    ]);
    const result = checkConstraints(it, poiList([poi("poi_a")]), trip());
    expect(result.errors.some((e) => e.includes("poi_ghost"))).toBe(true);
  });

  it("同日重复 POI 报 error", () => {
    const it = itinerary([
      {
        day: 1,
        area_cluster: ["Namba"],
        time_blocks: [block("poi_a"), block("poi_a")],
        estimated_total_cost: 1000,
        estimated_total_minutes: 180,
      },
    ]);
    const result = checkConstraints(it, poiList([poi("poi_a")]), trip());
    expect(result.errors.some((e) => e.includes("重复"))).toBe(true);
  });

  it("每日超时仅 warning", () => {
    const it = itinerary([
      {
        day: 1,
        area_cluster: ["Namba"],
        time_blocks: [block("poi_a", { end_time: "20:00" })],
        estimated_total_cost: 500,
        estimated_total_minutes: 600,
      },
    ]);
    const result = checkConstraints(it, poiList([poi("poi_a")]), trip());
    expect(result.warnings.some((w) => w.includes("超过"))).toBe(true);
  });

  it("预算超限仅 warning", () => {
    const it = itinerary([
      {
        day: 1,
        area_cluster: ["Namba"],
        time_blocks: [block("poi_a")],
        estimated_total_cost: 50000,
        estimated_total_minutes: 90,
      },
    ]);
    const result = checkConstraints(
      it,
      poiList([poi("poi_a")]),
      trip({ budget: { currency: "JPY", amount: 10000 } })
    );
    expect(result.warnings.some((w) => w.includes("超过预算"))).toBe(true);
  });

  it("时间倒挂报 error", () => {
    const it = itinerary([
      {
        day: 1,
        area_cluster: ["Namba"],
        time_blocks: [block("poi_a", { start_time: "12:00", end_time: "10:00" })],
        estimated_total_cost: 500,
        estimated_total_minutes: 0,
      },
    ]);
    const result = checkConstraints(it, poiList([poi("poi_a")]), trip());
    expect(result.errors.some((e) => e.includes("倒挂"))).toBe(true);
  });

  it("时间重叠报 error", () => {
    const it = itinerary([
      {
        day: 1,
        area_cluster: ["Namba"],
        time_blocks: [
          block("poi_a", { start_time: "10:00", end_time: "12:00" }),
          block("poi_b", { start_time: "11:00", end_time: "13:00" }),
        ],
        estimated_total_cost: 1000,
        estimated_total_minutes: 180,
      },
    ]);
    const result = checkConstraints(
      it,
      poiList([poi("poi_a"), poi("poi_b")]),
      trip()
    );
    expect(result.errors.some((e) => e.includes("重叠"))).toBe(true);
  });

  it("must 未覆盖报 error", () => {
    const it = itinerary([
      {
        day: 1,
        area_cluster: ["Namba"],
        time_blocks: [block("poi_a")],
        estimated_total_cost: 500,
        estimated_total_minutes: 90,
      },
    ]);
    const result = checkConstraints(
      it,
      poiList([poi("poi_a"), poi("poi_must", { priority: "must" })]),
      trip({ mustVisit: ["poi_must"] })
    );
    expect(result.errors.some((e) => e.includes("必去项"))).toBe(true);
  });

  it("通过别名匹配 mustVisit 视为覆盖", () => {
    const it = itinerary([
      {
        day: 1,
        area_cluster: ["Namba"],
        time_blocks: [block("poi_usj")],
        estimated_total_cost: 9000,
        estimated_total_minutes: 480,
      },
    ]);
    const result = checkConstraints(
      it,
      poiList([poi("poi_usj", { aliases: ["USJ"] })]),
      trip({ mustVisit: ["USJ"] })
    );
    expect(result.errors.some((e) => e.includes("必去项"))).toBe(false);
  });
});
