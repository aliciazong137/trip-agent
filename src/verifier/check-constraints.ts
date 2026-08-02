import type {
  ConstraintCheckResult,
  Itinerary,
  Poi,
  PoiList,
  TimeBlock,
  TripMeta,
} from "../types/domain.js";

const PACE_DAILY_LIMIT_MINUTES: Record<TripMeta["pace"], number> = {
  relaxed: 360,
  normal: 480,
  packed: 600,
};

function parseHHMM(value: string): number | null {
  const match = /^(\d{2}):(\d{2})$/.exec(value);
  if (!match) return null;
  const h = Number(match[1]);
  const m = Number(match[2]);
  if (h < 0 || h > 23 || m < 0 || m > 59) return null;
  return h * 60 + m;
}

function blockMinutes(block: TimeBlock): number | null {
  const start = parseHHMM(block.start_time);
  const end = parseHHMM(block.end_time);
  if (start === null || end === null) return null;
  if (end < start) return null;
  return end - start;
}

function isOverlap(a: TimeBlock, b: TimeBlock): boolean {
  const as = parseHHMM(a.start_time);
  const ae = parseHHMM(a.end_time);
  const bs = parseHHMM(b.start_time);
  const be = parseHHMM(b.end_time);
  if (as === null || ae === null || bs === null || be === null) return false;
  return as < be && bs < ae;
}

function findPoi(poiList: PoiList, poiId: string): Poi | undefined {
  return poiList.pois.find((p) => p.id === poiId);
}

/**
 * 行程业务约束检查（schema 之外）。
 *
 * 检查项：
 * - POI 存在性：itinerary 中引用的 poi_id 必须在 poiList 中。
 * - must 覆盖：tripMeta.mustVisit 里的每个 POI 至少出现在行程某天。
 * - POI 重复：同一天内同一 POI 出现多次。
 * - 每日超时：每日 estimated_total_minutes 超过 pace 上限（只 warning）。
 * - 预算超限：行程总成本超过 tripMeta.budget.amount（只 warning）。
 * - 时间倒挂/重叠：同一天内 start >= end，或时间块互相重叠。
 */
export function checkConstraints(
  itinerary: Itinerary,
  poiList: PoiList,
  tripMeta: TripMeta
): ConstraintCheckResult {
  const errors: string[] = [];
  const warnings: string[] = [];

  const dailyLimit = PACE_DAILY_LIMIT_MINUTES[tripMeta.pace];

  // 收集行程中出现的 POI id 和别名
  const placedIds = new Set<string>();
  const placedAliases = new Set<string>();
  for (const day of itinerary.days) {
    for (const block of day.time_blocks) {
      placedIds.add(block.poi_id);
      const poi = findPoi(poiList, block.poi_id);
      if (poi?.aliases) {
        for (const alias of poi.aliases) placedAliases.add(alias);
      }
    }
  }

  for (const day of itinerary.days) {
    const seenInDay = new Set<string>();
    for (const block of day.time_blocks) {
      // 存在性
      const poi = findPoi(poiList, block.poi_id);
      if (!poi) {
        errors.push(`POI ${block.poi_id} 不在 poi-list 中（Day ${day.day}）`);
      }

      // 同日重复
      if (seenInDay.has(block.poi_id)) {
        errors.push(`POI ${block.poi_id} 在 Day ${day.day} 重复出现`);
      }
      seenInDay.add(block.poi_id);

      // 时间倒挂
      const minutes = blockMinutes(block);
      if (minutes === null) {
        errors.push(
          `Day ${day.day} 时间块 ${block.poi_id} 时间非法或倒挂（${block.start_time}-${block.end_time}）`
        );
      }
    }

    // 同日重叠
    for (let i = 0; i < day.time_blocks.length; i++) {
      for (let j = i + 1; j < day.time_blocks.length; j++) {
        if (isOverlap(day.time_blocks[i], day.time_blocks[j])) {
          errors.push(
            `Day ${day.day} 时间块 ${day.time_blocks[i].poi_id} 与 ${day.time_blocks[j].poi_id} 重叠`
          );
        }
      }
    }

    // 每日超时（仅 warning）
    if (day.estimated_total_minutes > dailyLimit) {
      warnings.push(
        `Day ${day.day} 总时长 ${day.estimated_total_minutes} 分钟超过 ${tripMeta.pace} 节奏上限 ${dailyLimit} 分钟`
      );
    }
  }

  // must 覆盖
  for (const mustId of tripMeta.mustVisit) {
    if (!placedIds.has(mustId) && !placedAliases.has(mustId)) {
      errors.push(`必去项 ${mustId} 未在行程中出现`);
    }
  }

  // 预算超限（仅 warning）
  if (tripMeta.budget) {
    const totalCost = itinerary.days.reduce(
      (sum, d) => sum + d.estimated_total_cost,
      0
    );
    if (totalCost > tripMeta.budget.amount) {
      warnings.push(
        `行程总成本 ${totalCost} 超过预算 ${tripMeta.budget.amount}（${tripMeta.budget.currency}）`
      );
    }
  }

  return { errors, warnings };
}
