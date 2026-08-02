import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { z } from "zod";
import { rm, readFile } from "node:fs/promises";
import path from "node:path";
import {
  invokeTool,
  isValidSessionId,
  DEFAULT_OBSERVATION_LIMIT,
  type RegisteredTool,
  type ToolRuntime,
  type ToolEvent,
  type ToolResult,
} from "../src/tools/registry.js";
import {
  registerTool,
  getTool,
  listTools,
  listToolsForLLM,
  clearRegistry,
} from "../src/tools/registry-impl.js";

// ─── 测试辅助 ───────────────────────────────────────────────────────────────

const VALID_SESSION = "sess_a1b2c3d4e5f6";
const TEST_RUN_ID = "run_test_001";

function makeRuntime(
  overrides: Partial<ToolRuntime> = {}
): ToolRuntime & { events: ToolEvent[] } {
  const events: ToolEvent[] = [];
  const base: ToolRuntime & { events: ToolEvent[] } = {
    sessionId: VALID_SESSION,
    runId: TEST_RUN_ID,
    modeBias: "react",
    step: 1,
    signal: new AbortController().signal,
    appendEvent: (e) => events.push(e),
    loadStore: async () => ({
      tripMeta: null,
      poiList: null,
      itinerary: null,
      session: null,
    }),
    events,
  };
  // 覆盖时保留 events 数组引用（除非显式覆盖 appendEvent）
  const merged = { ...base, ...overrides } as ToolRuntime & { events: ToolEvent[] };
  if (overrides.appendEvent) {
    // 显式覆盖了 appendEvent，events 数组仍是 base.events（但不会被新 appendEvent 写入）
    merged.events = events;
  }
  return merged;
}

function makeTool<T extends z.ZodType = z.ZodType>(
  overrides: Partial<RegisteredTool<T>> = {}
): RegisteredTool<T> {
  const baseSchema = z.object({ x: z.number() }) as unknown as T;
  return {
    name: "test_tool",
    description: "test tool",
    schema: baseSchema,
    execute: async (args) => ({
      ok: true,
      observation: `x=${(args as { x: number }).x}`,
      data: args,
    }),
    ...overrides,
  } as RegisteredTool<T>;
}

const runLogPath = () =>
  path.resolve(process.cwd(), "context", VALID_SESSION, "run.log");

async function cleanupRunLog(): Promise<void> {
  try {
    await rm(path.resolve(process.cwd(), "context", VALID_SESSION), {
      recursive: true,
      force: true,
    });
  } catch {
    /* ignore */
  }
}

// ─── 测试用例 ───────────────────────────────────────────────────────────────

describe("isValidSessionId", () => {
  it("合法 sessionId 通过", () => {
    expect(isValidSessionId("sess_a1b2c3d4e5f6")).toBe(true);
    expect(isValidSessionId("sess_0123456789ab")).toBe(true);
  });

  it("非法 sessionId 拒绝", () => {
    expect(isValidSessionId("sess_short")).toBe(false);
    expect(isValidSessionId("invalid_prefix")).toBe(false);
    expect(isValidSessionId("../../etc/passwd")).toBe(false);
    expect(isValidSessionId("")).toBe(false);
  });
});

describe("invokeTool", () => {
  beforeEach(cleanupRunLog);
  afterEach(cleanupRunLog);

  it("合法参数 → ok=true，返回 observation", async () => {
    const tool = makeTool();
    const rt = makeRuntime();
    const r = await invokeTool(tool, { x: 42 }, rt);
    expect(r.ok).toBe(true);
    expect(r.observation).toBe("x=42");
    expect(r.data).toEqual({ x: 42 });
  });

  it("发 tool_start 和 tool_end 事件，含 runId/step", async () => {
    const tool = makeTool();
    const rt = makeRuntime({ step: 3 });
    await invokeTool(tool, { x: 1 }, rt);
    expect(rt.events).toHaveLength(2);
    expect(rt.events[0]).toMatchObject({
      type: "tool_start",
      tool: "test_tool",
      runId: TEST_RUN_ID,
      step: 3,
    });
    expect(rt.events[1]).toMatchObject({
      type: "tool_end",
      tool: "test_tool",
      status: "ok",
      runId: TEST_RUN_ID,
      step: 3,
    });
  });

  it("zod 校验失败 → bad_args，不执行 execute", async () => {
    const execute = vi.fn();
    const tool = makeTool({ execute });
    const rt = makeRuntime();
    const r = await invokeTool(tool, { x: "not a number" }, rt);
    expect(r.ok).toBe(false);
    expect(r.error?.errorCode).toBe("bad_args");
    expect(r.error?.details?.issues).toBeDefined();
    expect(execute).not.toHaveBeenCalled();
    // 失败时 tool_end 事件 status=bad_args
    expect(rt.events).toHaveLength(1);
    expect(rt.events[0]).toMatchObject({ type: "tool_end", status: "bad_args" });
  });

  it("execute 抛异常 → tool_error，不抛到上层", async () => {
    const tool = makeTool({
      execute: async () => {
        throw new Error("boom");
      },
    });
    const r = await invokeTool(tool, { x: 1 }, makeRuntime());
    expect(r.ok).toBe(false);
    expect(r.error?.errorCode).toBe("tool_error");
    expect(r.error?.message).toBe("boom");
  });

  it("execute 超时 → tool_timeout", async () => {
    const tool = makeTool({
      timeoutMs: 50,
      execute: async () => {
        await new Promise((r) => setTimeout(r, 500));
        return { ok: true, observation: "should not reach" };
      },
    });
    const r = await invokeTool(tool, { x: 1 }, makeRuntime());
    expect(r.ok).toBe(false);
    expect(r.error?.errorCode).toBe("tool_timeout");
  });

  // codeflicker-fix: EDGE-Issue-002/rn7exbrwg43z6bqtjt20
  it("超时后 runtime.signal.aborted === true（协作式取消信号）", async () => {
    let receivedSignal: AbortSignal | undefined;
    const tool = makeTool({
      timeoutMs: 30,
      execute: async (_args, runtime) => {
        receivedSignal = runtime.signal;
        await new Promise((r) => setTimeout(r, 300));
        return { ok: true, observation: "should not reach" };
      },
    });
    const r = await invokeTool(tool, { x: 1 }, makeRuntime());
    expect(r.ok).toBe(false);
    expect(r.error?.errorCode).toBe("tool_timeout");
    // 工具应该收到 signal，且超时后 signal 已被 abort
    expect(receivedSignal).toBeDefined();
    expect(receivedSignal!.aborted).toBe(true);
  });

  it("mutating tool 超时后检查 signal，不写状态", async () => {
    let writeCalled = false;
    const tool = makeTool({
      timeoutMs: 30,
      mutatesState: true,
      execute: async (_args, runtime) => {
        // 模拟慢工具
        await new Promise((r) => setTimeout(r, 100));
        // 写入前检查 signal —— 超时后这里应该 abort
        if (runtime.signal.aborted) {
          return {
            ok: false,
            observation: "",
            error: { errorCode: "tool_timeout", message: "aborted before write" },
          };
        }
        writeCalled = true;
        return { ok: true, observation: "written" };
      },
    });
    const r = await invokeTool(tool, { x: 1 }, makeRuntime());
    expect(r.ok).toBe(false);
    expect(r.error?.errorCode).toBe("tool_timeout");
    expect(writeCalled).toBe(false);
  });

  it("observation 超长被截断到 observationLimit", async () => {
    const long = "a".repeat(5000);
    const tool = makeTool({
      observationLimit: 100,
      execute: async () => ({ ok: true, observation: long }),
    });
    const r = await invokeTool(tool, { x: 1 }, makeRuntime());
    expect(r.observation.length).toBe(100);
    expect(r.observation).toContain("(truncated)");
  });

  it("observation 未超 limit 时不截断", async () => {
    const tool = makeTool({
      observationLimit: 100,
      execute: async () => ({ ok: true, observation: "short" }),
    });
    const r = await invokeTool(tool, { x: 1 }, makeRuntime());
    expect(r.observation).toBe("short");
  });

  it("needsConfirm=true → 发 need_confirm 事件，不执行 execute", async () => {
    const execute = vi.fn();
    const tool = makeTool({ needsConfirm: true, execute });
    const rt = makeRuntime();
    const r = await invokeTool(tool, { x: 1 }, rt);
    expect(r.ok).toBe(false);
    expect(r.error?.errorCode).toBe("need_confirm");
    expect(r.error?.details?.confirmId).toMatch(/^cfm_/);
    expect(execute).not.toHaveBeenCalled();
    expect(rt.events).toHaveLength(1);
    expect(rt.events[0]).toMatchObject({ type: "need_confirm", tool: "test_tool" });
  });

  it("needsConfirm 函数返回 true → 同 boolean=true", async () => {
    const execute = vi.fn();
    const tool = makeTool({
      needsConfirm: (args) => (args as { x: number }).x > 10,
      execute,
    });
    const r = await invokeTool(tool, { x: 99 }, makeRuntime());
    expect(r.ok).toBe(false);
    expect(r.error?.errorCode).toBe("need_confirm");
    expect(execute).not.toHaveBeenCalled();
  });

  it("needsConfirm 函数返回 false → 正常执行", async () => {
    const execute = vi.fn(async () => ({ ok: true, observation: "ok" }));
    const tool = makeTool({
      needsConfirm: (args) => (args as { x: number }).x > 10,
      execute,
    });
    const r = await invokeTool(tool, { x: 5 }, makeRuntime());
    expect(r.ok).toBe(true);
    expect(execute).toHaveBeenCalled();
  });

  it("needsConfirm 函数是 async → 等待 Promise", async () => {
    const execute = vi.fn(async () => ({ ok: true, observation: "ok" }));
    const tool = makeTool({
      needsConfirm: async () => true,
      execute,
    });
    const r = await invokeTool(tool, { x: 1 }, makeRuntime());
    expect(r.ok).toBe(false);
    expect(r.error?.errorCode).toBe("need_confirm");
    expect(execute).not.toHaveBeenCalled();
  });

  it("sessionId 非法 → bad_args，不执行 execute", async () => {
    const execute = vi.fn();
    const tool = makeTool({ execute });
    const rt = makeRuntime({ sessionId: "invalid" });
    const r = await invokeTool(tool, { x: 1 }, rt);
    expect(r.ok).toBe(false);
    expect(r.error?.errorCode).toBe("bad_args");
    expect(execute).not.toHaveBeenCalled();
  });

  it("写日志到 context/{sessionId}/run.log", async () => {
    const tool = makeTool();
    await invokeTool(tool, { x: 1 }, makeRuntime());
    const logText = await readFile(runLogPath(), "utf8");
    const lines = logText.trim().split("\n");
    expect(lines.length).toBeGreaterThanOrEqual(1);
    const entry = JSON.parse(lines[lines.length - 1]);
    expect(entry.sessionId).toBe(VALID_SESSION);
    expect(entry.runId).toBe(TEST_RUN_ID);
    expect(entry.tool).toBe("test_tool");
    expect(entry.status).toBe("ok");
  });
});

describe("registry-impl", () => {
  beforeEach(() => clearRegistry());

  it("registerTool + getTool", () => {
    const tool = makeTool({ name: "foo" });
    registerTool(tool);
    expect(getTool("foo")).toBe(tool);
    expect(getTool("bar")).toBeUndefined();
  });

  it("重复注册同名工具 → 抛异常", () => {
    const tool = makeTool({ name: "dup" });
    registerTool(tool);
    expect(() => registerTool(makeTool({ name: "dup" }))).toThrow(
      /already registered/
    );
  });

  it("listTools 返回所有已注册工具", () => {
    registerTool(makeTool({ name: "a" }));
    registerTool(makeTool({ name: "b" }));
    const all = listTools();
    expect(all).toHaveLength(2);
    expect(all.map((t) => t.name).sort()).toEqual(["a", "b"]);
  });

  it("listToolsForLLM 不暴露 execute", () => {
    registerTool(makeTool({ name: "a" }));
    const forLLM = listToolsForLLM();
    expect(forLLM).toHaveLength(1);
    expect(forLLM[0]).toEqual({
      name: "a",
      description: "test tool",
      schema: expect.anything(),
    });
    expect(forLLM[0]).not.toHaveProperty("execute");
  });
});
