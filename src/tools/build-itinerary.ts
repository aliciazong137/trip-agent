import { readFile } from "node:fs/promises";
import path from "node:path";

import type { TripMeta } from "../types/api.js";
import type {
  BuildItineraryResult,
  DayPlan,
  Itinerary,
  Poi,
  PoiList,
  TimeBlock,
} from "../types/domain.js";

/** 保留 P0 mock，供 orchestrator 在 P1-5 接入真实内核前继续兼容。 */
export function buildMockItinerary(
  sessionId: string,
  tripMeta: TripMeta
): Record<string, unknown> {
  return {
    sessionId,
    city: tripMeta.city,
    version: 1,
    days: Array.from({ length: tripMeta.days }, (_, i) => ({
      day: i + 1,
      area_cluster: i === 0 ? ["Namba"] : ["Umeda"],
      time_blocks: [
        {
          poi_id: "poi_dotonbori",
          start_time: "10:00",
          end_time: "12:00",
          reason: "P0 mock: 经典地标打卡",
          locked: false,
        },
        {
          poi_id: "poi_kuromon",
          start_time: "12:30",
          end_time: "14:00",
          reason: "P0 mock: 午餐体验",
          locked: false,
        },
      ],
      estimated_total_cost: 3000,
      estimated_total_minutes: 210,
    })),
  };
}

const PACE_DAILY_LIMIT_MINUTES: Record<TripMeta["pace"], number> = {
  relaxed: 360,
  normal: 480,
  packed: 600,
};

const FULL_DAY_THRESHOLD_MINUTES = 360;
const FULL_DAY_CONSTRAINT = "全天项目";

const TRAVEL_TIME_ROOT = path.resolve(process.cwd(), "data", "travel-time");

interface AreaMatrix {
  city: string;
  areas: string[];
  matrix_minutes: Record<string, Record<string, number>>;
}

const matrixCache = new Map<string, AreaMatrix>();

async function loadAreaMatrix(city: string): Promise<AreaMatrix | null> {
  const cacheKey = city.toLowerCase();
  if (matrixCache.has(cacheKey)) {
    return matrixCache.get(cacheKey)!;
  }
  try {
    const raw = await readFile(path.join(TRAVEL_TIME_ROOT, `${cacheKey}-area-matrix.json`), "utf8");
    const matrix = JSON.parse(raw) as AreaMatrix;
    matrixCache.set(cacheKey, matrix);
    return matrix;
  } catch {
    return null;
  }
}

export function getTravelTimeMinutes(
  matrix: AreaMatrix | null,
  fromArea: string | undefined,
  toArea: string | undefined
): number {
  if (!matrix || !fromArea || !toArea) return 0;
  const row = matrix.matrix_minutes[fromArea];
  if (!row) return 0;
  const value = row[toArea];
  return typeof value === "number" ? value : 0;
}

export function isFullDayPoi(poi: Poi): boolean {
  if ((poi.estimated_duration_minutes ?? 0) >= FULL_DAY_THRESHOLD_MINUTES) {
    return true;
  }
  if (poi.constraints?.some((c) => c.includes(FULL_DAY_CONSTRAINT))) {
    return true;
  }
  return false;
}

export function poiCost(poi: Poi): { cost: number; unknown: boolean } {
  const raw = poi.estimated_cost;
  if (raw === undefined) return { cost: 0, unknown: false };
  if (typeof raw === "number") return { cost: raw, unknown: false };
  return { cost: 0, unknown: true };
}

export function poiDurationMinutes(poi: Poi): number {
  return poi.estimated_duration_minutes ?? 0;
}

function priorityWeight(priority: Poi["priority"]): number {
  switch (priority) {
    case "must":
      return 0;
    case "nice":
      return 1;
    case "optional":
      return 2;
  }
}

export function sortPoisForPlanning(pois: Poi[]): Poi[] {
  return [...pois].sort((a, b) => {
    const byPriority = priorityWeight(a.priority) - priorityWeight(b.priority);
    if (byPriority !== 0) return byPriority;
    if (a.area !== b.area) {
      return a.area.localeCompare(b.area);
    }
    return (b.confidence ?? 0) - (a.confidence ?? 0);
  });
}

function toHHMM(totalMinutes: number): TimeBlock["start_time"] {
  const safe = Math.max(0, Math.floor(totalMinutes));
  const hours = Math.floor(safe / 60) % 24;
  const minutes = safe % 60;
  const hh = String(hours).padStart(2, "0");
  const mm = String(minutes).padStart(2, "0");
  return `${hh}:${mm}` as TimeBlock["start_time"];
}

function reasonFor(poi: Poi): string {
  const priorityLabel = {
    must: "必去",
    nice: "推荐",
    optional: "可选",
  }[poi.priority];
  return `${priorityLabel}：${poi.name}（${poi.area}）`;
}

interface PlanDayContext {
  matrix: AreaMatrix | null;
  currentMinutes: number;
  currentArea: string | undefined;
  costAccumulator: number;
  warnings: string[];
  blocks: TimeBlock[];
  areas: Set<string>;
  placedIds: Set<string>;
}

function pushBlock(ctx: PlanDayContext, poi: Poi): void {
  const travel = getTravelTimeMinutes(ctx.matrix, ctx.currentArea, poi.area);
  if (ctx.currentArea && travel > 0) {
    ctx.currentMinutes += travel;
  }
  const duration = poiDurationMinutes(poi);
  const start = toHHMM(ctx.currentMinutes);
  ctx.currentMinutes += duration;
  const end = toHHMM(ctx.currentMinutes);
  const { cost, unknown } = poiCost(poi);
  ctx.costAccumulator += cost;
  if (unknown) {
    ctx.warnings.push(`POI ${poi.id} 估算成本未知，按 0 计入预算`);
  }
  ctx.blocks.push({
    poi_id: poi.id,
    start_time: start,
    end_time: end,
    reason: reasonFor(poi),
    locked: false,
  });
  ctx.areas.add(poi.area);
  ctx.placedIds.add(poi.id);
  ctx.currentArea = poi.area;
}

function newDayContext(matrix: AreaMatrix | null): PlanDayContext {
  return {
    matrix,
    currentMinutes: 10 * 60,
    currentArea: undefined,
    costAccumulator: 0,
    warnings: [],
    blocks: [],
    areas: new Set<string>(),
    placedIds: new Set<string>(),
  };
}

function finalizeDay(day: number, ctx: PlanDayContext): DayPlan {
  return {
    day,
    area_cluster: Array.from(ctx.areas),
    time_blocks: ctx.blocks,
    estimated_total_cost: ctx.costAccumulator,
    estimated_total_minutes: ctx.blocks.reduce((sum, b) => {
      const [sh, sm] = b.start_time.split(":").map(Number);
      const [eh, em] = b.end_time.split(":").map(Number);
      return sum + (eh * 60 + em - (sh * 60 + sm));
    }, 0),
  };
}

/**
 * 真实排程内核。
 *
 * - 输入：sessionId + tripMeta + poiList，不依赖 LLM。
 * - 排序：must > nice > optional，同优先级按 area 聚类。
 * - 天分配：先放 must，按 pace 控制每日上限（relaxed 360 / normal 480 / packed 600 分钟）。
 * - 时间：从 10:00 起，按 POI 时长 + 区域移动时间累加。
 * - 成本：累加 POI cost；{unknown:true} 计 0 并产出 warning。
 * - 全天项目（duration >= 360 或 constraints 含"全天项目"）单独占一天。
 */
export async function buildItineraryFromData(
  sessionId: string,
  tripMeta: TripMeta,
  poiList: PoiList
): Promise<BuildItineraryResult> {
  const warnings: string[] = [];
  const matrix = await loadAreaMatrix(poiList.city ?? tripMeta.city);
  if (!matrix) {
    warnings.push(`未找到 ${poiList.city ?? tripMeta.city} 的区域移动时间矩阵，跨区域移动按 0 分钟估算`);
  }
  const dailyLimit = PACE_DAILY_LIMIT_MINUTES[tripMeta.pace];

  const sorted = sortPoisForPlanning(poiList.pois);
  const fullDayPois = sorted.filter(isFullDayPoi);
  const regularPois = sorted.filter((p) => !isFullDayPoi(p));

  const days: DayPlan[] = [];
  let remainingMust = regularPois.filter((p) => p.priority === "must");

  // 全天项目各自单独成天
  for (const poi of fullDayPois) {
    if (days.length >= tripMeta.days) {
      warnings.push(`全天项目 ${poi.id} 因天数上限未排入`);
      break;
    }
    const ctx = newDayContext(matrix);
    pushBlock(ctx, poi);
    warnings.push(...ctx.warnings);
    days.push(finalizeDay(days.length + 1, ctx));
  }

  // 普通项目按区域聚类逐日放置
  for (let dayIndex = days.length; dayIndex < tripMeta.days; dayIndex++) {
    const ctx = newDayContext(matrix);
    let guard = 0;
    while (guard++ < 200) {
      const candidate = pickNextPoi(remainingMust, regularPois, ctx.currentArea, ctx.placedIds);
      if (!candidate) break;
      const travel = getTravelTimeMinutes(matrix, ctx.currentArea, candidate.area);
      const used = ctx.currentMinutes - 10 * 60;
      if (
        poiDurationMinutes(candidate) + travel >
        dailyLimit - used
      ) {
        if (candidate.priority === "must" && ctx.blocks.length === 0) {
          pushBlock(ctx, candidate);
          warnings.push(`Day ${dayIndex + 1} 必去项 ${candidate.id} 超出 pace 上限，已强制排入`);
          continue;
        }
        break;
      }
      pushBlock(ctx, candidate);
      remainingMust = remainingMust.filter((p) => p.id !== candidate.id);
    }
    warnings.push(...ctx.warnings);
    days.push(finalizeDay(dayIndex + 1, ctx));
  }

  // 仍有 must 未排：最后警告
  for (const m of remainingMust) {
    warnings.push(`必去项 ${m.id} 因天数或 pace 上限未排入`);
  }

  // 计算总成本并检查预算
  const totalCost = days.reduce((sum, d) => sum + d.estimated_total_cost, 0);
  if (tripMeta.budget && totalCost > tripMeta.budget.amount) {
    warnings.push(
      `行程估算总成本 ${totalCost} 超出预算 ${tripMeta.budget.amount}（${tripMeta.budget.currency}）`
    );
  }

  const itinerary: Itinerary = {
    sessionId,
    city: poiList.city ?? tripMeta.city,
    version: 1,
    days,
  };

  return { itinerary, warnings };
}

function pickNextPoi(
  mustQueue: Poi[],
  allPois: Poi[],
  currentArea: string | undefined,
  placedIds: Set<string>
): Poi | undefined {
  const sameAreaMust = mustQueue.find((p) => p.area === currentArea && !placedIds.has(p.id));
  if (sameAreaMust) return sameAreaMust;
  if (mustQueue.length > 0) {
    const next = mustQueue.find((p) => !placedIds.has(p.id));
    if (next) return next;
  }
  const sameAreaAny = allPois.find(
    (p) => p.priority !== "must" && p.area === currentArea && !placedIds.has(p.id)
  );
  if (sameAreaAny) return sameAreaAny;
  return allPois.find((p) => p.priority !== "must" && !placedIds.has(p.id));
}

/**
 * 小入参工具壳（P1-2 仅定义形状，runtime 注入在 P2-2 之后接上）。
 *
 * 设计意图：LLM 只传 { opts? }，sessionId 由 ToolRuntime 注入，
 * 内部从 store 读取 trip-meta.json 和 poi-list.json。
 */
export interface BuildItineraryToolInput {
  opts?: {
    ignorePoiList?: boolean;
  };
}

export async function buildItineraryTool(
  input: BuildItineraryToolInput,
  runtime: { sessionId: string; loadTripMeta: () => Promise<TripMeta>; loadPoiList: () => Promise<PoiList> }
): Promise<BuildItineraryResult> {
  const tripMeta = await runtime.loadTripMeta();
  const poiList = await runtime.loadPoiList();
  void input;
  return buildItineraryFromData(runtime.sessionId, tripMeta, poiList);
}
