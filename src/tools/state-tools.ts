/**
 * P2-3 状态与对话控制工具
 *
 * 11 个 RegisteredTool 定义：
 * - 读状态：get_itinerary / get_poi_list
 * - 写状态：set_trip_meta / add_pois_to_list / remove_pois_from_list
 * - 规划：build_itinerary / verify_itinerary / revise_day
 * - 搜索详情：get_search_detail
 * - 对话控制：ask_user / finish
 */
import { z } from "zod";
import type { RegisteredTool, ToolResult } from "./registry.js";
import {
  loadItineraryRecord,
  loadPoiList,
  loadTripMeta,
  loadSearchResult,
  saveItinerary,
  savePoiList,
  saveTripMeta,
  updateSessionPhase,
} from "../context/store.js";
import { buildItineraryFromData } from "./build-itinerary.js";
import { checkConstraints } from "../verifier/check-constraints.js";
import type { Poi, PoiList } from "../types/domain.js";
import type { TripMeta } from "../types/api.js";

function summarizePoi(poi: Poi): string {
  return `${poi.name}(${poi.id}, ${poi.priority}, ${poi.area})`;
}

function poiBuckets(pois: Poi[]): { must: number; nice: number; optional: number } {
  let must = 0;
  let nice = 0;
  let optional = 0;
  for (const p of pois) {
    if (p.priority === "must") must++;
    else if (p.priority === "nice") nice++;
    else optional++;
  }
  return { must, nice, optional };
}

function mergeTripMeta(base: TripMeta, patch: Partial<TripMeta>): TripMeta {
  return {
    ...base,
    ...patch,
    budget: patch.budget ?? base.budget,
    travelers: patch.travelers ?? base.travelers,
  };
}

/**
 * 检查 signal 是否已取消；返回结构化 ToolResult 以便工具早退。
 * // codeflicker-fix: EDGE-Issue-002/rn7exbrwg43z6bqtjt20
 */
function abortedResult(runtime: { signal: AbortSignal }): ToolResult {
  return {
    ok: false,
    observation: "",
    error: {
      errorCode: "tool_timeout",
      message: "tool execution was aborted",
      details: { aborted: runtime.signal.aborted },
    },
  };
}

const emptySchema = z.object({}).strict();

export const getItineraryToolDef: RegisteredTool<typeof emptySchema> = {
  name: "get_itinerary",
  description: "读取当前 session 的行程安排，返回天数和每天的 POI 摘要",
  schema: emptySchema,
  execute: async (_args, runtime) => {
    const itinerary = await loadItineraryRecord(runtime.sessionId);
    if (!itinerary) {
      return { ok: false, observation: "itinerary 不存在", error: { errorCode: "tool_error", message: "itinerary not found" } };
    }
    if (itinerary.days.length === 0) {
      return { ok: true, observation: "当前还没有行程，请先调用 build_itinerary 生成" };
    }
    const lines = itinerary.days.map((d) => {
      const pois = d.time_blocks.map((b) => b.poi_id).join(", ");
      return `Day ${d.day}: ${pois} (${d.estimated_total_minutes}min)`;
    });
    return { ok: true, observation: `共 ${itinerary.days.length} 天\n${lines.join("\n")}`, data: itinerary };
  },
};

export const getPoiListToolDef: RegisteredTool<typeof emptySchema> = {
  name: "get_poi_list",
  description: "读取当前 session 的 POI 列表，返回总数和 priority 分布",
  schema: emptySchema,
  execute: async (_args, runtime) => {
    const poiList = await loadPoiList(runtime.sessionId);
    if (!poiList) {
      return { ok: false, observation: "poi-list 不存在", error: { errorCode: "tool_error", message: "poi-list not found" } };
    }
    if (poiList.pois.length === 0) {
      return { ok: true, observation: "POI 列表为空" };
    }
    const buckets = poiBuckets(poiList.pois);
    const summary = poiList.pois.slice(0, 5).map(summarizePoi).join("; ");
    return {
      ok: true,
      observation: `共 ${poiList.pois.length} 个 POI（must=${buckets.must}, nice=${buckets.nice}, optional=${buckets.optional}）。前 5 个：${summary}`,
      data: poiList,
    };
  },
};

const setTripMetaSchema = z.object({
  days: z.number().int().positive().optional(),
  budget: z.object({ currency: z.string(), amount: z.number().nonnegative() }).optional(),
  pace: z.enum(["relaxed", "normal", "packed"]).optional(),
  mustVisit: z.array(z.string()).optional(),
  avoid: z.array(z.string()).optional(),
  travelers: z.object({ adults: z.number().int().optional(), kids: z.number().int().optional() }).optional(),
}).strict();

export const setTripMetaToolDef: RegisteredTool<typeof setTripMetaSchema> = {
  name: "set_trip_meta",
  description: "更新行程元信息（days/budget/pace/mustVisit/avoid/travelers），部分更新",
  schema: setTripMetaSchema,
  mutatesState: true,
  execute: async (args, runtime) => {
    const base = await loadTripMeta(runtime.sessionId);
    if (!base) {
      return { ok: false, observation: "trip-meta 不存在", error: { errorCode: "tool_error", message: "trip-meta not found" } };
    }
    if (runtime.signal.aborted) return abortedResult(runtime);
    const merged = mergeTripMeta(base, args);
    if (runtime.signal.aborted) return abortedResult(runtime);
    await saveTripMeta(runtime.sessionId, merged, { signal: runtime.signal });
    return {
      ok: true,
      observation: `trip-meta 已更新：${args.days ?? base.days} 天，pace=${args.pace ?? base.pace}，mustVisit=${(args.mustVisit ?? base.mustVisit).length} 个`,
      data: merged,
    };
  },
};

const addPoisSchema = z.object({
  resultId: z.string().optional(),
  poiIds: z.array(z.string()).min(1),
}).strict();

export const addPoisToListToolDef: RegisteredTool<typeof addPoisSchema> = {
  name: "add_pois_to_list",
  description: "把 POI 加到 poi-list（去重）；poiIds 可来自 search_pois 的 resultId",
  schema: addPoisSchema,
  mutatesState: true,
  execute: async (args, runtime) => {
    const poiList = await loadPoiList(runtime.sessionId);
    if (!poiList) {
      return { ok: false, observation: "poi-list 不存在", error: { errorCode: "tool_error", message: "poi-list not found" } };
    }
    if (runtime.signal.aborted) return abortedResult(runtime);
    let fromCache: Poi[] = [];
    if (args.resultId) {
      const cached = await loadSearchResult(runtime.sessionId, args.resultId);
      if (cached && typeof cached === "object" && Array.isArray((cached as { pois?: unknown }).pois)) {
        fromCache = (cached as { pois: Poi[] }).pois.filter((p) => args.poiIds.includes(p.id));
      }
    }
    if (runtime.signal.aborted) return abortedResult(runtime);
    const existingIds = new Set(poiList.pois.map((p) => p.id));
    const toAdd = fromCache.filter((p) => !existingIds.has(p.id));
    const updated: PoiList = { ...poiList, pois: [...poiList.pois, ...toAdd] };
    await savePoiList(runtime.sessionId, updated, { signal: runtime.signal });
    return { ok: true, observation: `已添加 ${toAdd.length} 个 POI（去重后），当前共 ${updated.pois.length} 个`, data: { added: toAdd.length, total: updated.pois.length } };
  },
};

const removePoisSchema = z.object({
  poiIds: z.array(z.string()).min(1),
}).strict();

export const removePoisFromListToolDef: RegisteredTool<typeof removePoisSchema> = {
  name: "remove_pois_from_list",
  description: "从 poi-list 移除指定 POI；批量删除（>3 个）需要用户确认",
  schema: removePoisSchema,
  needsConfirm: (args): boolean => {
    if (args && typeof args === "object" && "poiIds" in args) {
      const ids = (args as { poiIds: unknown }).poiIds;
      return Array.isArray(ids) && ids.length > 3;
    }
    return false;
  },
  mutatesState: true,
  execute: async (args, runtime) => {
    const poiList = await loadPoiList(runtime.sessionId);
    if (!poiList) {
      return { ok: false, observation: "poi-list 不存在", error: { errorCode: "tool_error", message: "poi-list not found" } };
    }
    if (runtime.signal.aborted) return abortedResult(runtime);
    const idsToRemove = new Set(args.poiIds);
    const before = poiList.pois.length;
    const updated: PoiList = { ...poiList, pois: poiList.pois.filter((p) => !idsToRemove.has(p.id)) };
    if (runtime.signal.aborted) return abortedResult(runtime);
    const removed = before - updated.pois.length;
    await savePoiList(runtime.sessionId, updated, { signal: runtime.signal });
    return { ok: true, observation: `已移除 ${removed} 个 POI，当前共 ${updated.pois.length} 个`, data: { removed, total: updated.pois.length } };
  },
};

const buildItinerarySchema = z.object({
  opts: z.object({ ignorePoiList: z.boolean().optional() }).optional(),
}).strict();

export const buildItineraryToolDef: RegisteredTool<typeof buildItinerarySchema> = {
  name: "build_itinerary",
  description: "根据 trip-meta 和 poi-list 生成行程；首次不确认，覆盖已有行程时需确认",
  schema: buildItinerarySchema,
  needsConfirm: async (_args, runtime) => {
    const itinerary = await loadItineraryRecord(runtime.sessionId);
    return itinerary !== null && itinerary.days.length > 0;
  },
  mutatesState: true,
  execute: async (_args, runtime) => {
    if (runtime.signal.aborted) return abortedResult(runtime);
    const tripMeta = await loadTripMeta(runtime.sessionId);
    if (!tripMeta) return { ok: false, observation: "trip-meta 缺失，请先 set_trip_meta", error: { errorCode: "tool_error", message: "missing trip-meta" } };
    const poiList = await loadPoiList(runtime.sessionId);
    if (!poiList) return { ok: false, observation: "poi-list 缺失", error: { errorCode: "tool_error", message: "missing poi-list" } };
    if (runtime.signal.aborted) return abortedResult(runtime);
    const result = await buildItineraryFromData(runtime.sessionId, tripMeta, poiList);
    if (runtime.signal.aborted) return abortedResult(runtime);
    await saveItinerary(runtime.sessionId, result.itinerary, { signal: runtime.signal });
    if (runtime.signal.aborted) return abortedResult(runtime);
    const updatedSession = await updateSessionPhase(runtime.sessionId, "planned", { signal: runtime.signal });
    if (runtime.signal.aborted) return abortedResult(runtime);
    if (!updatedSession) {
      return {
        ok: false,
        observation: "行程已保存，但 session 状态更新失败",
        error: { errorCode: "tool_error", message: "failed to update session phase" },
      };
    }
    return {
      ok: true,
      observation: `已生成 ${result.itinerary.days.length} 天行程${result.warnings.length > 0 ? `，${result.warnings.length} 条警告：${result.warnings.join("; ")}` : ""}`,
      data: result,
      warning: result.warnings.length > 0 ? result.warnings.join("; ") : undefined,
    };
  },
};

export const verifyItineraryToolDef: RegisteredTool<typeof emptySchema> = {
  name: "verify_itinerary",
  description: "校验当前行程是否符合约束（must 覆盖、时间、预算、重复等），返回 errors/warnings",
  schema: emptySchema,
  execute: async (_args, runtime) => {
    const [itinerary, poiList, tripMeta] = await Promise.all([
      loadItineraryRecord(runtime.sessionId),
      loadPoiList(runtime.sessionId),
      loadTripMeta(runtime.sessionId),
    ]);
    if (!itinerary || !poiList || !tripMeta) {
      return { ok: false, observation: "itinerary/poi-list/trip-meta 缺失", error: { errorCode: "tool_error", message: "missing state for verification" } };
    }
    const result = checkConstraints(itinerary, poiList, tripMeta);
    if (result.errors.length === 0 && result.warnings.length === 0) return { ok: true, observation: "行程校验通过，无 errors 无 warnings", data: result };
    const parts: string[] = [];
    if (result.errors.length > 0) parts.push(`errors(${result.errors.length}): ${result.errors.join("; ")}`);
    if (result.warnings.length > 0) parts.push(`warnings(${result.warnings.length}): ${result.warnings.join("; ")}`);
    return { ok: result.errors.length === 0, observation: parts.join("\n"), data: result, warning: result.warnings.length > 0 ? result.warnings.join("; ") : undefined };
  },
};

const reviseDayOperationSchema = z.discriminatedUnion("type", [
  z.object({ type: z.literal("remove"), poiId: z.string() }),
  z.object({ type: z.literal("move"), poiId: z.string(), toDay: z.number().int().positive() }),
  z.object({ type: z.literal("add"), poiId: z.string(), position: z.number().int().optional() }),
]);

const reviseDaySchema = z.object({
  day: z.number().int().positive(),
  operations: z.array(reviseDayOperationSchema).min(1),
}).strict();

export const reviseDayToolDef: RegisteredTool<typeof reviseDaySchema> = {
  name: "revise_day",
  description: "修改某天的景点安排（结构化 operations：remove/move/add），修改已有行程需确认",
  schema: reviseDaySchema,
  needsConfirm: true,
  mutatesState: true,
  execute: async (args, runtime) => {
    if (runtime.signal.aborted) return abortedResult(runtime);
    const opSummary = args.operations.map((o) => `${o.type}(${o.poiId})`).join(", ");
    return {
      ok: false,
      observation: `revise_day(day=${args.day}, ops=[${opSummary}]) 尚未实现`,
      error: { errorCode: "tool_error", message: "revise_day is not implemented" },
    };
  },
};

const getSearchDetailSchema = z.object({ resultId: z.string() }).strict();

export const getSearchDetailToolDef: RegisteredTool<typeof getSearchDetailSchema> = {
  name: "get_search_detail",
  description: "按 resultId 读取搜索缓存中的完整结果",
  schema: getSearchDetailSchema,
  execute: async (args, runtime) => {
    const data = await loadSearchResult(runtime.sessionId, args.resultId);
    if (data === null) return { ok: false, observation: `resultId=${args.resultId} 不存在`, error: { errorCode: "tool_error", message: "search result not found", details: { resultId: args.resultId } } };
    return { ok: true, observation: `搜索结果详情（resultId=${args.resultId}）`, data };
  },
};

const askUserSchema = z.object({ question: z.string(), options: z.array(z.string()).optional() }).strict();

export const askUserToolDef: RegisteredTool<typeof askUserSchema> = {
  name: "ask_user",
  description: "参数不足时向用户追问；提供 question 和可选 options",
  schema: askUserSchema,
  execute: async (args) => ({ ok: true, observation: args.question, data: { question: args.question, options: args.options } }),
};

const finishSchema = z.object({ message: z.string() }).strict();

export const finishToolDef: RegisteredTool<typeof finishSchema> = {
  name: "finish",
  description: "答复完成，结束当前对话",
  schema: finishSchema,
  execute: async (args) => ({ ok: true, observation: args.message, data: { message: args.message } }),
};

export const ALL_TOOL_DEFS: RegisteredTool[] = [
  getItineraryToolDef,
  getPoiListToolDef,
  setTripMetaToolDef,
  addPoisToListToolDef,
  removePoisFromListToolDef,
  buildItineraryToolDef,
  verifyItineraryToolDef,
  reviseDayToolDef,
  getSearchDetailToolDef,
  askUserToolDef,
  finishToolDef,
];
