/**
 * P2-2 工具调用日志
 *
 * 设计要点（项目 plan §2.11）：
 * - 每次对话步骤写日志：sessionId / runId / modeBias / step / tool / args_summary / duration / status。
 * - 日志输出到 stdout + context/{sessionId}/run.log（追加模式）。
 * - 单进程下 appendFile 追加是安全的；多进程并发不在 P2-2 范围。
 * - 写日志失败不阻塞主流程，只打 stderr。
 */
import { appendFile, mkdir } from "node:fs/promises";
import path from "node:path";

const CONTEXT_ROOT = path.resolve(process.cwd(), "context");

export interface ToolLogEntry {
  sessionId: string;
  runId: string;
  modeBias: string;
  step: number;
  tool: string;
  args_summary: string;
  duration_ms: number;
  status: "ok" | "bad_args" | "tool_timeout" | "tool_error" | "need_confirm";
  timestamp: string;
}

/**
 * 写一条工具调用日志到 stdout + context/{sessionId}/run.log。
 * 写文件失败只打 stderr，不抛异常。
 */
export async function writeToolLog(entry: ToolLogEntry): Promise<void> {
  const line = JSON.stringify(entry);
  // stdout 便于本地开发观察
  process.stdout.write(`[tool] ${line}\n`);

  try {
    const dir = path.join(CONTEXT_ROOT, entry.sessionId);
    await mkdir(dir, { recursive: true });
    await appendFile(path.join(dir, "run.log"), line + "\n", "utf8");
  } catch (err) {
    // 日志写失败不阻塞主流程
    process.stderr.write(
      `[tool-log] failed to write run.log: ${err instanceof Error ? err.message : String(err)}\n`
    );
  }
}
