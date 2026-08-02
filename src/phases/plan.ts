import {
  loadPoiList,
  loadTripMeta,
  saveItinerary,
  updateSessionPhase,
} from "../context/store.js";
import { validateAgainstSchema } from "../verifier/schema-validator.js";
import { buildItineraryFromData } from "../tools/build-itinerary.js";
import { checkConstraints } from "../verifier/check-constraints.js";
import type { BuildItineraryResult, Poi, PoiList } from "../types/domain.js";

export interface PlanItineraryOptions {
  /** poi-list 为空时，从 mustVisit + 城市白名单降级生成 POI。 */
  allowWhitelistFallback?: boolean;
  /** 注入白名单 POI，便于测试；默认从 data/pois/{city}.json 读取。 */
  loadWhitelist?: (city: string) => Promise<Poi[] | null>;
}

export interface PlanItineraryResult {
  ok: boolean;
  result?: BuildItineraryResult;
  schemaErrors?: string[];
  constraintErrors?: string[];
  constraintWarnings?: string[];
  buildWarnings?: string[];
  error?: string;
}

/**
 * P1-4 plan phase：
 * 读 trip-meta + poi-list → buildItineraryFromData → schema + constraint 校验 → 写回 itinerary.json → 更新 session.phase。
 *
 * 失败不抛异常，返回结构化结果；phase 写失败只警告不阻断。
 */
export async function planItinerary(
  sessionId: string,
  options: PlanItineraryOptions = {}
): Promise<PlanItineraryResult> {
  const tripMeta = await loadTripMeta(sessionId);
  if (!tripMeta) {
    return { ok: false, error: "trip-meta not found" };
  }

  let poiList = await loadPoiList(sessionId);
  if (!poiList) {
    return { ok: false, error: "poi-list not found" };
  }

  // poi-list 为空时，从 mustVisit + 白名单降级生成
  if (poiList.pois.length === 0 && options.allowWhitelistFallback !== false) {
    const loader = options.loadWhitelist ?? defaultWhitelistLoader;
    const whitelistPois = await loader(tripMeta.city);
    const must = tripMeta.mustVisit;
    const mustAliases = new Set(must.map((m) => m.toLowerCase()));
    const matched: Poi[] = [];
    if (whitelistPois) {
      for (const poi of whitelistPois) {
        const hitsAlias =
          poi.aliases?.some((a) => mustAliases.has(a.toLowerCase())) ?? false;
        const hitsId = mustAliases.has(poi.id.toLowerCase());
        const hitsName = mustAliases.has(poi.name.toLowerCase());
        if (hitsAlias || hitsId || hitsName) {
          matched.push({ ...poi, priority: "must", source_refs: poi.source_refs ?? [
            { type: "whitelist", ref: `data/pois/${tripMeta.city}.json` },
          ] });
        }
      }
    }
    poiList = { city: poiList.city, pois: matched };
  }

  const build = await buildItineraryFromData(sessionId, tripMeta, poiList);

  const schemaCheck = await validateAgainstSchema(
    "itinerary.schema.json",
    build.itinerary
  );
  if (!schemaCheck.valid) {
    return {
      ok: false,
      schemaErrors: schemaCheck.errors,
      buildWarnings: build.warnings,
    };
  }

  const constraints = checkConstraints(build.itinerary, poiList, tripMeta);
  if (constraints.errors.length > 0) {
    return {
      ok: false,
      constraintErrors: constraints.errors,
      constraintWarnings: constraints.warnings,
      buildWarnings: build.warnings,
      result: build,
    };
  }

  await saveItinerary(sessionId, build.itinerary);
  await updateSessionPhase(sessionId, "planned");

  return {
    ok: true,
    result: build,
    constraintWarnings: constraints.warnings,
    buildWarnings: build.warnings,
  };
}

async function defaultWhitelistLoader(city: string): Promise<Poi[] | null> {
  try {
    const { readFile } = await import("node:fs/promises");
    const path = await import("node:path");
    const raw = await readFile(
      path.resolve(process.cwd(), "data", "pois", `${city.toLowerCase()}.json`),
      "utf8"
    );
    const list = JSON.parse(raw) as PoiList;
    return list.pois;
  } catch {
    return null;
  }
}
