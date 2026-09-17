import type { GuideRoutesResponse, MapData, NlTripPlanResponse, ReviseNlResponse, RouteOption, TrendingNotesResponse } from "./types";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

/** 所有业务请求都携带 HttpOnly 匿名身份 Cookie，前端不保存也不传递 user_id。 */
function apiFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  return fetch(input, { ...init, credentials: "include" });
}

export type AnonymousIdentity = { user_id: string; anonymous: true; is_developer?: boolean };

let anonymousIdentityPromise: Promise<AnonymousIdentity> | null = null;

/** 首次打开应用时创建匿名身份；后续请求由浏览器自动附带 Cookie。 */
export async function ensureAnonymousIdentity(): Promise<AnonymousIdentity> {
  if (!anonymousIdentityPromise) {
    anonymousIdentityPromise = (async () => {
      const response = await apiFetch(`${API_BASE}/api/auth/anonymous`, { method: "POST" });
      if (!response.ok) throw new Error(await parseErrorMessage(response, "匿名身份初始化失败"));
      return response.json() as Promise<AnonymousIdentity>;
    })();
  }
  try {
    return await anonymousIdentityPromise;
  } catch (error) {
    anonymousIdentityPromise = null;
    throw error;
  }
}

export async function fetchTrendingNotes(signal?: AbortSignal, personalized = false): Promise<TrendingNotesResponse> {
  const response = await apiFetch(`${API_BASE}/api/discover/trending${personalized ? "?personalized=true" : ""}`, { signal });
  if (!response.ok) throw new Error(await parseErrorMessage(response, "热门推荐加载失败"));
  return response.json() as Promise<TrendingNotesResponse>;
}

/** 统一解析 HTTP 错误（支持 FastAPI detail 字段） */
async function parseErrorMessage(response: Response, fallback: string): Promise<string> {
  try {
    const data = await response.json();
    if (typeof data?.detail === "string") return data.detail;
    if (typeof data?.error_message === "string") return data.error_message;
  } catch {
    // ignore parse error
  }
  return `${fallback}（${response.status}）`;
}

export async function fetchMapData(
  sessionId: string,
  signal?: AbortSignal,
): Promise<MapData> {
  const response = await apiFetch(
    `${API_BASE}/api/trip/session/${encodeURIComponent(sessionId)}/map`,
    { signal },
  );
  if (!response.ok) {
    const msg = await parseErrorMessage(response, "路线数据加载失败");
    throw new Error(msg);
  }
  return response.json() as Promise<MapData>;
}

export async function planTripFromText(
  query: string,
  sessionId?: string | null,
  signal?: AbortSignal,
): Promise<NlTripPlanResponse> {
  const response = await apiFetch(`${API_BASE}/api/trip/plan-nl`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      query,
      session_id: sessionId || undefined,
    }),
    signal,
  });
  if (!response.ok) {
    const msg = await parseErrorMessage(response, "规划请求失败");
    throw new Error(msg);
  }
  return response.json() as Promise<NlTripPlanResponse>;
}

/** 大师工作流阶段事件 */
export type PlanStageEvent = {
  type: "stage";
  agent: string;
  label: string;
  status: "start" | "done";
  weather?: WeatherBrief[];
};

export type WeatherBrief = {
  city: string;
  date: string;
  day_weather: string;
  night_weather: string;
  day_temp: string;
  night_temp: string;
};

/**
 * 流式规划：边规划边回调阶段事件（大师工作流），
 * 最后 resolve 完整 NlTripPlanResponse。
 */
export async function planTripFromTextStream(
  query: string,
  sessionId?: string | null,
  onStage?: (event: PlanStageEvent) => void,
  signal?: AbortSignal,
): Promise<NlTripPlanResponse> {
  const response = await apiFetch(`${API_BASE}/api/trip/plan-nl/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      query,
      session_id: sessionId || undefined,
    }),
    signal,
  });
  if (!response.ok || !response.body) {
    const msg = await parseErrorMessage(response, "规划请求失败");
    throw new Error(msg);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result: NlTripPlanResponse | null = null;

  // 逐行解析 SSE（data: {...}）
  outer: while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || ""; // 末行可能不完整，留到下一轮
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed.startsWith("data: ")) continue;
      try {
        const data = JSON.parse(trimmed.slice(6));
        if (data.type === "stage") {
          onStage?.(data as PlanStageEvent);
        } else if (data.type === "result") {
          result = data.data as NlTripPlanResponse;
          break outer;
        }
      } catch {
        // 忽略单行解析失败
      }
    }
  }
  if (!result) throw new Error("规划结果流意外中断");
  return result;
}

export type GuideRouteStreamHandlers = {
  onStage?: (event: PlanStageEvent) => void;
  onGuideDelta?: (content: string) => void;
};

/** 热门卡片入口携带已选笔记及其结构化旅行事实，避免再被当作普通聊天解析。 */
export type TrendingInspiration = {
  id: string;
  title: string;
  summary: string;
  tags: string[];
  tripMeta: Record<string, unknown>;
};

export async function generateGuideRoutesStream(
  query: string,
  sessionId?: string | null,
  conversationContext?: string,
  handlers: GuideRouteStreamHandlers = {},
  signal?: AbortSignal,
  guideStyle: "full" | "inspiration" = "full",
  inspiration?: TrendingInspiration,
): Promise<GuideRoutesResponse> {
  const response = await apiFetch(`${API_BASE}/api/trip/guide-routes/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      query,
      session_id: sessionId || undefined,
      conversation_context: conversationContext || undefined,
      guide_style: guideStyle,
      inspiration_note: inspiration ? { id: inspiration.id, title: inspiration.title, summary: inspiration.summary, tags: inspiration.tags } : undefined,
      inspiration_trip_meta: inspiration?.tripMeta,
    }),
    signal,
  });
  if (!response.ok || !response.body) {
    const msg = await parseErrorMessage(response, "攻略生成失败");
    throw new Error(msg);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result: GuideRoutesResponse | null = null;

  outer: while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed.startsWith("data: ")) continue;
      try {
        const data = JSON.parse(trimmed.slice(6));
        if (data.type === "stage") {
          handlers.onStage?.(data as PlanStageEvent);
        } else if (data.type === "guide_delta") {
          handlers.onGuideDelta?.(data.content || "");
        } else if (data.type === "guide_done") {
          result = data.data as GuideRoutesResponse;
          break outer;
        }
      } catch {
        // 忽略单行解析失败
      }
    }
  }
  if (!result) throw new Error("攻略结果流意外中断");
  return result;
}

export async function planFromRouteStream(
  query: string,
  selectedRoute: RouteOption,
  sessionId?: string | null,
  tripMeta?: Record<string, unknown> | null,
  onStage?: (event: PlanStageEvent) => void,
  signal?: AbortSignal,
): Promise<NlTripPlanResponse> {
  const response = await apiFetch(`${API_BASE}/api/trip/plan-from-route/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      query,
      selected_route: selectedRoute,
      session_id: sessionId || undefined,
      trip_meta: tripMeta || undefined,
    }),
    signal,
  });
  if (!response.ok || !response.body) {
    const msg = await parseErrorMessage(response, "路线规划失败");
    throw new Error(msg);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result: NlTripPlanResponse | null = null;

  outer: while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed.startsWith("data: ")) continue;
      try {
        const data = JSON.parse(trimmed.slice(6));
        if (data.type === "stage") {
          onStage?.(data as PlanStageEvent);
        } else if (data.type === "result") {
          result = data.data as NlTripPlanResponse;
          break outer;
        }
      } catch {
        // 忽略单行解析失败
      }
    }
  }
  if (!result) throw new Error("路线规划结果流意外中断");
  return result;
}

export async function reviseTripFromText(
  sessionId: string,
  query: string,
  day?: number | null,
  signal?: AbortSignal,
): Promise<ReviseNlResponse> {
  const response = await apiFetch(
    `${API_BASE}/api/trip/session/${encodeURIComponent(sessionId)}/revise-nl`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query,
        day: day ?? undefined,
      }),
      signal,
    },
  );
  if (!response.ok) {
    const msg = await parseErrorMessage(response, "行程调整失败");
    throw new Error(msg);
  }
  return response.json() as Promise<ReviseNlResponse>;
}
