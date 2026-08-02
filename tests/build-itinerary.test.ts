import { describe, it, expect } from "vitest";
import {
  buildItineraryFromData,
  isFullDayPoi,
  sortPoisForPlanning,
} from "../src/tools/build-itinerary.js";
import type { Poi, PoiList } from "../src/types/domain.js";
import type { TripMeta } from "../src/types/api.js";

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
}) as Poi;

const baseTrip = (over: Partial<TripMeta>): TripMeta => ({
  city: "osaka",
  days: 2,
  pace: "normal",
  mustVisit: [],
  avoid: [],
  ...over,
});

const poiListFrom = (pois: Poi[]): PoiList => ({ city: "osaka", pois });

describe("buildItineraryFromData", () => {
  it("USJ 等全天项目单独占一天", async () => {
    const pois: Poi[] = [
      basePoi({
        id: "poi_usj",
        name: "USJ",
        area: "USJ",
        priority: "must",
        estimated_duration_minutes: 480,
        estimated_cost: 9000,
        constraints: ["需预约", "全天项目"],
      }),
      basePoi({
        id: "poi_dotonbori",
        name: "道顿堀",
        area: "Namba",
        priority: "nice",
        estimated_duration_minutes: 120,
        estimated_cost: 0,
      }),
    ];
    const trip = baseTrip({ days: 2 });
    const { itinerary } = await buildItineraryFromData("sess_test", trip, poiListFrom(pois));

    expect(itinerary.days.length).toBe(2);
    expect(itinerary.days[0].time_blocks[0].poi_id).toBe("poi_usj");
    expect(itinerary.days[0].time_blocks.length).toBe(1);
    expect(itinerary.days[1].time_blocks[0].poi_id).toBe("poi_dotonbori");
  });

  it("must 优先于 nice/optional 排入行程", async () => {
    const pois: Poi[] = [
      basePoi({ id: "poi_optional", name: "opt", priority: "optional", area: "Namba" }),
      basePoi({ id: "poi_must", name: "must", priority: "must", area: "Namba" }),
      basePoi({ id: "poi_nice", name: "nice", priority: "nice", area: "Namba" }),
    ];
    const trip = baseTrip({ days: 1, pace: "packed" });
    const { itinerary } = await buildItineraryFromData("sess_test", trip, poiListFrom(pois));

    const ids = itinerary.days[0].time_blocks.map((b) => b.poi_id);
    expect(ids[0]).toBe("poi_must");
    expect(ids.indexOf("poi_nice")).toBeLessThan(ids.indexOf("poi_optional"));
  });

  it("pace=normal 每日 480 分钟上限不被超过（非全天项目）", async () => {
    const pois: Poi[] = [
      basePoi({ id: "poi_a", name: "a", priority: "must", area: "Namba", estimated_duration_minutes: 200 }),
      basePoi({ id: "poi_b", name: "b", priority: "must", area: "Namba", estimated_duration_minutes: 200 }),
      basePoi({ id: "poi_c", name: "c", priority: "must", area: "Namba", estimated_duration_minutes: 200 }),
    ];
    const trip = baseTrip({ days: 1, pace: "normal" });
    const { itinerary } = await buildItineraryFromData("sess_test", trip, poiListFrom(pois));

    expect(itinerary.days[0].estimated_total_minutes).toBeLessThanOrEqual(480);
  });

  it("预算超限产出 warning", async () => {
    const pois: Poi[] = [
      basePoi({ id: "poi_costly", name: "costly", priority: "must", area: "Namba", estimated_cost: 50000 }),
    ];
    const trip = baseTrip({
      days: 1,
      pace: "packed",
      budget: { currency: "JPY", amount: 10000 },
    });
    const { warnings } = await buildItineraryFromData("sess_test", trip, poiListFrom(pois));
    expect(warnings.some((w) => w.includes("超出预算"))).toBe(true);
  });

  it("unknown 成本计 0 并 warning", async () => {
    const pois: Poi[] = [
      basePoi({
        id: "poi_unknown",
        name: "unknown",
        priority: "must",
        area: "Namba",
        estimated_cost: { unknown: true },
      }),
    ];
    const trip = baseTrip({ days: 1, pace: "packed" });
    const { warnings } = await buildItineraryFromData("sess_test", trip, poiListFrom(pois));
    expect(warnings.some((w) => w.includes("估算成本未知"))).toBe(true);
  });

  it("时间块格式 HH:MM 且 start<end", async () => {
    const pois: Poi[] = [
      basePoi({ id: "poi_a", name: "a", priority: "must", area: "Namba", estimated_duration_minutes: 90 }),
    ];
    const trip = baseTrip({ days: 1, pace: "packed" });
    const { itinerary } = await buildItineraryFromData("sess_test", trip, poiListFrom(pois));
    const b = itinerary.days[0].time_blocks[0];
    expect(b.start_time).toMatch(/^\d{2}:\d{2}$/);
    expect(b.end_time).toMatch(/^\d{2}:\d{2}$/);
    expect(b.end_time > b.start_time).toBe(true);
  });
});

describe("sortPoisForPlanning", () => {
  it("must < nice < optional，同优先级按 area 聚类", () => {
    const sorted = sortPoisForPlanning([
      basePoi({ id: "poi_opt", name: "opt", priority: "optional", area: "Umeda" }),
      basePoi({ id: "poi_must_b", name: "mustb", priority: "must", area: "Umeda" }),
      basePoi({ id: "poi_must_a", name: "musta", priority: "must", area: "Namba" }),
      basePoi({ id: "poi_nice", name: "nice", priority: "nice", area: "Namba" }),
    ]);
    const ids = sorted.map((p) => p.id);
    expect(ids).toEqual(["poi_must_a", "poi_must_b", "poi_nice", "poi_opt"]);
  });
});

describe("isFullDayPoi", () => {
  it("duration >= 360 视为全天", () => {
    expect(isFullDayPoi(basePoi({ estimated_duration_minutes: 360 }))).toBe(true);
    expect(isFullDayPoi(basePoi({ estimated_duration_minutes: 120 }))).toBe(false);
  });
  it("constraints 含『全天项目』视为全天", () => {
    expect(isFullDayPoi(basePoi({ constraints: ["全天项目"] }))).toBe(true);
  });
});
