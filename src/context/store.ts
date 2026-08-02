import { readFile, writeFile, mkdir, rename, unlink } from "node:fs/promises";
import path from "node:path";
import type { TripMeta } from "../types/api.js";
import type { Itinerary, PoiList, SessionState } from "../types/domain.js";
import type { SessionStore } from "../tools/registry.js";
import {
  assertValidSessionId,
  isValidResultId as isValidResultIdShared,
} from "../shared/ids.js";

const CONTEXT_ROOT = path.resolve(process.cwd(), "context");

export interface SessionFiles {
  sessionId: string;
  tripMeta: TripMeta;
  session: Record<string, unknown>;
  poiList: PoiList;
  itinerary: Itinerary;
  guideText?: string;
}

/** 写入选项；signal 用于协作式取消（如工具超时后拒绝提交） */
// codeflicker-fix: EDGE-Issue-002/rn7exbrwg43z6bqtjt20
export interface WriteOptions {
  signal?: AbortSignal;
}

/** 统一的 abort 错误，name=AbortError，便于上层识别 */
function createAbortError(): Error {
  const error = new Error("The operation was aborted");
  error.name = "AbortError";
  return error;
}

/**
 * 解析 sessionId 到 session 目录，带两层保护：
 * 1. assertValidSessionId 严格校验字符集/长度（防 ../、绝对路径、空字符串）
 * 2. resolve 后确认仍在 CONTEXT_ROOT 之内（防御性，未来即使放宽 ID 规则也不越界）
 * // codeflicker-fix: SEC-Issue-001/q8vpipdbhelt02wp6nps
 */
function sessionDir(sessionId: string): string {
  assertValidSessionId(sessionId);

  const root = path.resolve(CONTEXT_ROOT);
  const dir = path.resolve(root, sessionId);

  if (dir !== root && !dir.startsWith(`${root}${path.sep}`)) {
    throw new Error("sessionId resolves outside context root");
  }

  return dir;
}

export async function ensureContextRoot(): Promise<void> {
  await mkdir(CONTEXT_ROOT, { recursive: true });
}

/**
 * 原子写：写到临时文件再 rename（POSIX 原子）。
 * 避免并发写时读到半截 JSON。
 *
 * signal 支持协作式取消：
 * - 写入前检查 signal.aborted
 * - writeFile 传 signal，可被中断
 * - rename 前再次检查，避免超时后提交
 * - finally 清理 tmp（无论成功/失败/取消）
 * 注意：rename 本身不可取消，若 abort 发生在 rename 开始后无法回滚。
 */
async function atomicWrite(
  filePath: string,
  content: string,
  signal?: AbortSignal
): Promise<void> {
  if (signal?.aborted) {
    throw createAbortError();
  }

  const tmp = `${filePath}.${process.pid}.${Date.now()}.tmp`;

  try {
    await writeFile(tmp, content, { encoding: "utf8", signal });

    if (signal?.aborted) {
      throw createAbortError();
    }

    await rename(tmp, filePath);
  } finally {
    await unlink(tmp).catch(() => {
      /* tmp 已被 rename 消费或从未创建，忽略 */
    });
  }
}

export async function createSession(
  sessionId: string,
  tripMeta: TripMeta,
  guideText: string
): Promise<SessionFiles> {
  assertValidSessionId(sessionId);

  await ensureContextRoot();
  const dir = sessionDir(sessionId);
  await mkdir(dir, { recursive: true });
  await mkdir(path.join(dir, "guides"), { recursive: true });
  await mkdir(path.join(dir, "search-cache"), { recursive: true });

  const now = new Date().toISOString();
  const session = {
    sessionId,
    phase: "intake",
    intent: "new_plan",
    completed: [],
    pendingQuestions: [],
    confirmations: [],
    updatedAt: now,
  };

  const poiList: PoiList = { city: tripMeta.city, pois: [] };
  const itinerary: Itinerary = {
    sessionId,
    city: tripMeta.city,
    days: [],
    version: 1,
  };

  await atomicWrite(path.join(dir, "session.json"), JSON.stringify(session, null, 2));
  await atomicWrite(path.join(dir, "trip-meta.json"), JSON.stringify(tripMeta, null, 2));
  await atomicWrite(path.join(dir, "poi-list.json"), JSON.stringify(poiList, null, 2));
  await atomicWrite(path.join(dir, "itinerary.json"), JSON.stringify(itinerary, null, 2));
  await writeFile(path.join(dir, "guides", "raw.md"), guideText);

  return { sessionId, tripMeta, session, poiList, itinerary, guideText };
}

export async function loadSession(sessionId: string): Promise<SessionFiles | null> {
  assertValidSessionId(sessionId);

  const dir = sessionDir(sessionId);
  try {
    const [sessionRaw, tripMetaRaw, poiListRaw, itineraryRaw] = await Promise.all([
      readFile(path.join(dir, "session.json"), "utf8"),
      readFile(path.join(dir, "trip-meta.json"), "utf8"),
      readFile(path.join(dir, "poi-list.json"), "utf8"),
      readFile(path.join(dir, "itinerary.json"), "utf8"),
    ]);
    return {
      sessionId,
      tripMeta: JSON.parse(tripMetaRaw) as TripMeta,
      session: JSON.parse(sessionRaw) as Record<string, unknown>,
      poiList: JSON.parse(poiListRaw) as PoiList,
      itinerary: JSON.parse(itineraryRaw) as Itinerary,
    };
  } catch {
    return null;
  }
}

export async function saveItinerary(
  sessionId: string,
  itinerary: Itinerary,
  options?: WriteOptions
): Promise<void> {
  assertValidSessionId(sessionId);

  await atomicWrite(
    path.join(sessionDir(sessionId), "itinerary.json"),
    JSON.stringify(itinerary, null, 2),
    options?.signal
  );
}

export async function saveTripMeta(
  sessionId: string,
  tripMeta: TripMeta,
  options?: WriteOptions
): Promise<void> {
  assertValidSessionId(sessionId);

  await atomicWrite(
    path.join(sessionDir(sessionId), "trip-meta.json"),
    JSON.stringify(tripMeta, null, 2),
    options?.signal
  );
}

export async function savePoiList(
  sessionId: string,
  poiList: PoiList,
  options?: WriteOptions
): Promise<void> {
  assertValidSessionId(sessionId);

  await atomicWrite(
    path.join(sessionDir(sessionId), "poi-list.json"),
    JSON.stringify(poiList, null, 2),
    options?.signal
  );
}

export async function loadTripMeta(sessionId: string): Promise<TripMeta | null> {
  assertValidSessionId(sessionId);

  try {
    const raw = await readFile(path.join(sessionDir(sessionId), "trip-meta.json"), "utf8");
    return JSON.parse(raw) as TripMeta;
  } catch {
    return null;
  }
}

export async function loadPoiList(sessionId: string): Promise<PoiList | null> {
  assertValidSessionId(sessionId);

  try {
    const raw = await readFile(path.join(sessionDir(sessionId), "poi-list.json"), "utf8");
    return JSON.parse(raw) as PoiList;
  } catch {
    return null;
  }
}

export async function loadItineraryRecord(sessionId: string): Promise<Itinerary | null> {
  assertValidSessionId(sessionId);

  try {
    const raw = await readFile(path.join(sessionDir(sessionId), "itinerary.json"), "utf8");
    return JSON.parse(raw) as Itinerary;
  } catch {
    return null;
  }
}

export async function loadSessionState(sessionId: string): Promise<SessionState | null> {
  assertValidSessionId(sessionId);

  try {
    const raw = await readFile(path.join(sessionDir(sessionId), "session.json"), "utf8");
    return JSON.parse(raw) as SessionState;
  } catch {
    return null;
  }
}

export async function updateSessionPhase(
  sessionId: string,
  phase: SessionState["phase"],
  options?: WriteOptions
): Promise<SessionState | null> {
  assertValidSessionId(sessionId);

  const dir = sessionDir(sessionId);
  try {
    if (options?.signal?.aborted) {
      throw createAbortError();
    }

    const raw = await readFile(path.join(dir, "session.json"), "utf8");
    const session = JSON.parse(raw) as SessionState;
    session.phase = phase;
    session.updatedAt = new Date().toISOString();
    await atomicWrite(
      path.join(dir, "session.json"),
      JSON.stringify(session, null, 2),
      options?.signal
    );
    return session;
  } catch (error) {
    // 若是被取消，向上抛出而非吞成 null，让工具能识别为超时
    if (options?.signal?.aborted) {
      throw error;
    }
    return null;
  }
}

export async function touchSession(sessionId: string): Promise<string> {
  assertValidSessionId(sessionId);

  const dir = sessionDir(sessionId);
  const sessionRaw = await readFile(path.join(dir, "session.json"), "utf8");
  const session = JSON.parse(sessionRaw) as Record<string, unknown>;
  const updatedAt = new Date().toISOString();
  session.updatedAt = updatedAt;
  await atomicWrite(path.join(dir, "session.json"), JSON.stringify(session, null, 2));
  return updatedAt;
}

// ─── Search Cache（P2-4） ────────────────────────────────────────────────────

/** resultId 合法性校验，防路径穿越；委托给 shared/ids.ts */
export function isValidResultId(resultId: string): boolean {
  return isValidResultIdShared(resultId);
}

/** 写搜索结果到 search-cache/{resultId}.json */
export async function saveSearchResult(
  sessionId: string,
  resultId: string,
  data: unknown
): Promise<void> {
  assertValidSessionId(sessionId);

  if (!isValidResultId(resultId)) {
    throw new Error(`invalid resultId: ${resultId}`);
  }
  const dir = path.join(sessionDir(sessionId), "search-cache");
  await mkdir(dir, { recursive: true });
  await atomicWrite(path.join(dir, `${resultId}.json`), JSON.stringify(data, null, 2));
}

/** 读搜索结果；不存在或 resultId 非法返回 null */
export async function loadSearchResult(
  sessionId: string,
  resultId: string
): Promise<unknown | null> {
  assertValidSessionId(sessionId);

  if (!isValidResultId(resultId)) {
    return null;
  }
  try {
    const raw = await readFile(
      path.join(sessionDir(sessionId), "search-cache", `${resultId}.json`),
      "utf8"
    );
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

// ─── 聚合加载（供 ToolRuntime.loadStore 用） ──────────────────────────────────

/** 一次性加载 session 级业务文件，任一缺失对应字段为 null */
export async function loadStore(sessionId: string): Promise<SessionStore> {
  assertValidSessionId(sessionId);

  const [tripMeta, poiList, itinerary, session] = await Promise.all([
    loadTripMeta(sessionId),
    loadPoiList(sessionId),
    loadItineraryRecord(sessionId),
    loadSessionState(sessionId),
  ]);
  return { tripMeta, poiList, itinerary, session };
}
