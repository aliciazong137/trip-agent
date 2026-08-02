import { describe, it, expect, beforeAll } from "vitest";
import { preloadSchemas, validateAgainstSchema } from "../src/verifier/schema-validator.js";

describe("schemas", () => {
  beforeAll(async () => {
    await preloadSchemas();
  });

  it("loads all core schemas", async () => {
    const names = [
      "trip-meta.schema.json",
      "session.schema.json",
      "poi-list.schema.json",
      "itinerary.schema.json",
      "verifier-error.schema.json",
    ];
    for (const name of names) {
      const result = await validateAgainstSchema(name, {});
      expect(result.valid).toBe(false);
    }
  });

  it("validates trip-meta sample", async () => {
    const sample = {
      city: "osaka",
      days: 4,
      startDate: "2026-09-01",
      budget: { currency: "JPY", amount: 80000 },
      pace: "normal",
      mustVisit: ["USJ"],
      avoid: [],
    };
    const result = await validateAgainstSchema("trip-meta.schema.json", sample);
    expect(result.valid).toBe(true);
  });

  it("validates poi-list sample", async () => {
    const sample = {
      city: "osaka",
      pois: [
        {
          id: "poi_dotonbori",
          name: "道顿堀",
          category: "attraction",
          area: "Namba",
          source_refs: [{ type: "guide", ref: "line-1", snippet: "推荐道顿堀夜景" }],
          confidence: 0.95,
          priority: "nice",
          estimated_duration_minutes: 120,
          estimated_cost: { unknown: true },
        },
      ],
    };
    const result = await validateAgainstSchema("poi-list.schema.json", sample);
    expect(result.valid).toBe(true);
  });

  it("validates itinerary mock", async () => {
    const sample = {
      sessionId: "sess_test",
      city: "osaka",
      version: 1,
      days: [
        {
          day: 1,
          area_cluster: ["Namba"],
          time_blocks: [
            {
              poi_id: "poi_dotonbori",
              start_time: "10:00",
              end_time: "12:00",
              reason: "经典地标",
            },
          ],
          estimated_total_cost: 0,
          estimated_total_minutes: 120,
        },
      ],
    };
    const result = await validateAgainstSchema("itinerary.schema.json", sample);
    expect(result.valid).toBe(true);
  });
});
