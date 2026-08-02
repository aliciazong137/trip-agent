/**
 * P2-2 统一 Tool Registry 核心类型与 invokeTool
 *
 * 设计要点（项目 plan §2.5、§2.8、§2.9、§2.10、§2.11、P2-2）：
 *
 * - 所有工具通过 ToolRuntime 注入的 sessionId 操作 store；LLM 参数只传 id 和少量标量。
 * - 工具调用参数必须过 zod schema 校验，非法参数返回 bad_args。
 * - 工具调用有单次超时（默认 5s），超时返回 tool_timeout。
 * - needsConfirm 支持 boolean 或函数形式（args + runtime → boolean | Promise<boolean>）。
 * - observation 默认上限 2000 字符，超出截断。
 * - 每次工具调用写日志：sessionId/runId/modeBias/step/tool/args_summary/duration/status。
 * - sessionId 严格校验 ^sess_[a-f0-9]{12}$，防路径穿越。
 * - 不抛异常到上层，所有错误返回结构化 ToolError。
 */
import { z } from "zod";
import type { ModeBias } from "../agent/intent/types.js";
import type { TripMeta } from "../types/api.js";
import type { Itinerary, PoiList, SessionState } from "../types/domain.js";
import { writeToolLog, type ToolLogEntry } from "./logging.js";
// codeflicker-fix: SEC-Issue-001/q8vpipdbhelt02wp6nps
// sessionId 校验下沉到 shared/ids.ts，registry 与 store 共用同一规则
export { isValidSessionId, SESSION_ID_RE } from "../shared/ids.js";
import { isValidSessionId } from "../shared/ids.js";

// ─── 常量 ──────────────────────────────────────────────────────────────────

export const DEFAULT_TOOL_TIMEOUT_MS = 5000;
export const DEFAULT_OBSERVATION_LIMIT = 2000;
export const ARGS_SUMMARY_LIMIT = 200;

// ─── ToolRuntime 与 SessionStore ────────────────────────────────────────────

/** 聚合 store.ts 的 load* 函数返回值，供工具一次性拿到 session 级业务文件 */
export interface SessionStore {
  tripMeta: TripMeta | null;
  poiList: PoiList | null;
  itinerary: Itinerary | null;
  session: SessionState | null;
}

/** 工具执行时注入的运行时上下文，由 graph 层（P3-3）创建 */
export interface ToolRuntime {
  sessionId: string;
  runId: string;
  modeBias: ModeBias;
  step: number;
  /** 超时/取消信号；invokeTool 超时时调用 controller.abort()，工具应在关键节点检查 */
  signal: AbortSignal;
  /** 追加 SSE 事件（tool_start/tool_end/need_confirm），由 graph 层注入真实实现 */
  appendEvent: (event: ToolEvent) => void;
  /** 加载 session 级业务文件（trip-meta/poi-list/itinerary/session） */
  loadStore: () => Promise<SessionStore>;
}

// ─── ToolEvent（SSE 事件，P3 接 graph 时用） ────────────────────────────────

export type ToolEvent =
  | {
      type: "tool_start";
      tool: string;
      args_summary: string;
      runId: string;
      step: number;
    }
  | {
      type: "tool_end";
      tool: string;
      duration_ms: number;
      status: "ok" | "bad_args" | "tool_timeout" | "tool_error" | "need_confirm";
      runId: string;
      step: number;
    }
  | {
      type: "need_confirm";
      confirmId: string;
      tool: string;
      args_summary: string;
      runId: string;
      step: number;
    };

// ─── ToolResult 与 ToolError ────────────────────────────────────────────────

export interface ToolError {
  errorCode: "bad_args" | "tool_timeout" | "tool_error" | "need_confirm";
  message: string;
  details?: Record<string, unknown>;
}

export interface ToolResult {
  ok: boolean;
  /** 给 LLM 看的摘要，会被截断到 observationLimit */
  observation: string;
  /** 完整结果，可写 search-cache（P2-4）或供 graph 层使用 */
  data?: unknown;
  /** 非致命警告，仍算 ok */
  warning?: string;
  error?: ToolError;
}

// ─── RegisteredTool ───────────────────────────────────────────────────────────

export interface RegisteredTool<TSchema extends z.ZodType = z.ZodType> {
  name: string;
  description: string;
  /** zod schema，用于参数校验；LLM 工具定义也用它派生 JSON schema */
  schema: TSchema;
  /** 工具执行函数，拿到 zod 校验后的 args 和 runtime */
  execute: (
    args: z.infer<TSchema>,
    runtime: ToolRuntime
  ) => Promise<ToolResult>;
  /** 单次执行超时，默认 5000ms */
  timeoutMs?: number;
  /** 是否需要用户确认；boolean 或函数（args + runtime → boolean | Promise<boolean>） */
  needsConfirm?:
    | boolean
    | ((args: unknown, runtime: ToolRuntime) => boolean | Promise<boolean>);
  /** 是否修改状态（用于日志和事件标注） */
  mutatesState?: boolean;
  /** observation 字符数上限，默认 2000 */
  observationLimit?: number;
}

// ─── invokeTool 核心函数 ──────────────────────────────────────────────────────

/** 把 args 序列化成短摘要，用于日志和 SSE 事件 */
function summarizeArgs(args: unknown): string {
  let text: string;
  try {
    text = JSON.stringify(args);
  } catch {
    text = String(args);
  }
  if (text.length > ARGS_SUMMARY_LIMIT) {
    return text.slice(0, ARGS_SUMMARY_LIMIT) + "...";
  }
  return text;
}

/** 把 observation 截断到 limit；"... (truncated)" 占 15 字符 */
function truncateObservation(obs: string, limit: number): string {
  const suffix = "... (truncated)";
  if (obs.length <= limit) return obs;
  const sliceLen = Math.max(0, limit - suffix.length);
  return obs.slice(0, sliceLen) + suffix;
}

/** 生成 confirmId（P3-6 的 /confirm 接口用） */
function generateConfirmId(): string {
  return `cfm_${Math.random().toString(36).slice(2, 14)}`;
}

/** 安全发送 SSE 事件；事件回调抛错不影响工具主流程 */
function safeAppendEvent(runtime: ToolRuntime, event: ToolEvent): void {
  try {
    runtime.appendEvent(event);
  } catch {
    // SSE 事件失败不影响工具主流程
  }
}

/** 安全写日志；日志 IO 失败不影响工具结果 */
async function safeWriteToolLog(entry: ToolLogEntry): Promise<void> {
  try {
    await writeToolLog(entry);
  } catch {
    // 日志失败不影响工具结果
  }
}

/**
 * 执行一个工具，内置 zod 校验、确认策略、超时、try/catch、observation 截断、日志、事件。
 *
 * 所有错误都返回结构化 ToolResult.ok=false，不抛异常到上层。
 */
export async function invokeTool(
  tool: RegisteredTool,
  rawArgs: unknown,
  runtime: ToolRuntime
): Promise<ToolResult> {
  const startedAt = Date.now();
  const argsSummary = summarizeArgs(rawArgs);
  const timeoutMs = tool.timeoutMs ?? DEFAULT_TOOL_TIMEOUT_MS;
  const observationLimit = tool.observationLimit ?? DEFAULT_OBSERVATION_LIMIT;

  // 1. sessionId 校验
  if (!isValidSessionId(runtime.sessionId)) {
    const status: ToolLogEntry["status"] = "bad_args";
    await emitAndLog(
      tool,
      runtime,
      argsSummary,
      startedAt,
      status,
      { ok: false, observation: "", error: { errorCode: "bad_args", message: "invalid sessionId" } }
    );
    return {
      ok: false,
      observation: "",
      error: {
        errorCode: "bad_args",
        message: "invalid sessionId",
        details: { sessionId: runtime.sessionId },
      },
    };
  }

  // 2. zod 参数校验
  const parseResult = tool.schema.safeParse(rawArgs);
  if (!parseResult.success) {
    const details: Record<string, unknown> = {
      issues: parseResult.error.issues,
    };
    const status: ToolLogEntry["status"] = "bad_args";
    await emitAndLog(
      tool,
      runtime,
      argsSummary,
      startedAt,
      status,
      {
        ok: false,
        observation: "",
        error: {
          errorCode: "bad_args",
          message: "zod schema validation failed",
          details,
        },
      }
    );
    return {
      ok: false,
      observation: "",
      error: {
        errorCode: "bad_args",
        message: "zod schema validation failed",
        details,
      },
    };
  }

  const args = parseResult.data;

  // 3. 确认策略判断
  const needsConfirm = await evalNeedsConfirm(tool, args, runtime);
  if (needsConfirm) {
    const confirmId = generateConfirmId();
    safeAppendEvent(runtime, {
      type: "need_confirm",
      confirmId,
      tool: tool.name,
      args_summary: argsSummary,
      runId: runtime.runId,
      step: runtime.step,
    });
    const status: ToolLogEntry["status"] = "need_confirm";
    await emitAndLog(
      tool,
      runtime,
      argsSummary,
      startedAt,
      status,
      {
        ok: false,
        observation: "",
        error: {
          errorCode: "need_confirm",
          message: "tool requires user confirmation",
          details: { confirmId, tool: tool.name },
        },
      },
      "need_confirm"
    );
    return {
      ok: false,
      observation: "",
      error: {
        errorCode: "need_confirm",
        message: "tool requires user confirmation",
        details: { confirmId, tool: tool.name },
      },
    };
  }

  // 4. 发 tool_start 事件
  safeAppendEvent(runtime, {
    type: "tool_start",
    tool: tool.name,
    args_summary: argsSummary,
    runId: runtime.runId,
    step: runtime.step,
  });

  // 5. 超时执行 + try/catch
  // codeflicker-fix: EDGE-Issue-002/b5waq8qovxqy67s4npzn
  // 为本次调用创建 AbortController，超时时先 abort 再 reject，让工具能在关键节点检查 signal。
  const controller = new AbortController();
  const runtimeWithSignal: ToolRuntime = { ...runtime, signal: controller.signal };

  let result: ToolResult;
  let status: ToolLogEntry["status"] = "ok";
  try {
    // 传 task 函数（而非已执行的 Promise），保证 execute 在计时开始后才启动
    result = await runWithTimeout(
      () => tool.execute(args, runtimeWithSignal),
      timeoutMs,
      controller
    );
    if (!result.ok) {
      status = result.error?.errorCode === "tool_timeout" ? "tool_timeout" : "tool_error";
    }
  } catch (err) {
    const isTimeout =
      err instanceof Error && (err as { code?: string }).code === "tool_timeout";
    status = isTimeout ? "tool_timeout" : "tool_error";
    result = {
      ok: false,
      observation: "",
      error: {
        errorCode: isTimeout ? "tool_timeout" : "tool_error",
        message: err instanceof Error ? err.message : String(err),
        details: isTimeout
          ? { timeoutMs }
          : { stack: err instanceof Error ? err.stack : undefined },
      },
    };
  }

  // 6. observation 截断
  result = {
    ...result,
    observation: truncateObservation(result.observation, observationLimit),
  };

  // 7. 发 tool_end + 写日志
  safeAppendEvent(runtime, {
    type: "tool_end",
    tool: tool.name,
    duration_ms: Date.now() - startedAt,
    status,
    runId: runtime.runId,
    step: runtime.step,
  });

  await safeWriteToolLog({
    sessionId: runtime.sessionId,
    runId: runtime.runId,
    modeBias: runtime.modeBias,
    step: runtime.step,
    tool: tool.name,
    args_summary: argsSummary,
    duration_ms: Date.now() - startedAt,
    status,
    timestamp: new Date().toISOString(),
  });

  return result;
}

/** 评估 needsConfirm（boolean 或函数） */
async function evalNeedsConfirm(
  tool: RegisteredTool,
  args: unknown,
  runtime: ToolRuntime
): Promise<boolean> {
  if (tool.needsConfirm === undefined) return false;
  if (typeof tool.needsConfirm === "boolean") return tool.needsConfirm;
  return await tool.needsConfirm(args, runtime);
}

/**
 * 用 Promise.race 实现超时；接受 task 函数而非已执行的 Promise，
 * 保证 execute 在计时开始后才启动；超时时先 controller.abort() 再 reject。
 * settled 守卫防止 timer 与 promise 同时触发时重复 settle。
 */
function runWithTimeout<T>(
  task: () => Promise<T>,
  timeoutMs: number,
  controller: AbortController
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    let settled = false;

    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      controller.abort();
      reject(
        Object.assign(new Error(`tool_timeout after ${timeoutMs}ms`), {
          code: "tool_timeout",
        })
      );
    }, timeoutMs);

    let promise: Promise<T>;
    try {
      promise = task();
    } catch (error) {
      clearTimeout(timer);
      if (settled) return;
      settled = true;
      reject(error);
      return;
    }

    promise.then(
      (value) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        resolve(value);
      },
      (error) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        reject(error);
      }
    );
  });
}

/**
 * 统一处理事件发送 + 日志写入（用于早期返回场景：sessionId 非法、zod 失败、need_confirm）。
 * 早期返回时 tool_start/tool_end 事件的语义：
 * - tool_start 不发（execute 没真正开始）
 * - tool_end 发一条，status 反映失败原因
 */
async function emitAndLog(
  tool: RegisteredTool,
  runtime: ToolRuntime,
  argsSummary: string,
  startedAt: number,
  status: ToolLogEntry["status"],
  _result: ToolResult,
  eventType: "tool_end" | "need_confirm" = "tool_end"
): Promise<void> {
  if (eventType === "need_confirm") {
    // need_confirm 事件已在调用方发了，这里只写日志
  } else {
    safeAppendEvent(runtime, {
      type: "tool_end",
      tool: tool.name,
      duration_ms: Date.now() - startedAt,
      status,
      runId: runtime.runId,
      step: runtime.step,
    });
  }
  await safeWriteToolLog({
    sessionId: runtime.sessionId,
    runId: runtime.runId,
    modeBias: runtime.modeBias,
    step: runtime.step,
    tool: tool.name,
    args_summary: argsSummary,
    duration_ms: Date.now() - startedAt,
    status,
    timestamp: new Date().toISOString(),
  });
}
