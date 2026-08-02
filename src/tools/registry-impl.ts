/**
 * P2-2 工具注册表实例
 *
 * 设计要点：
 * - 全局单例 Map<name, RegisteredTool>，进程内共享。
 * - 重复注册抛异常，避免命名冲突。
 * - listToolsForLLM 只返回 name/description/schema，不暴露 execute 等内部字段。
 * - P2-2 只搭骨架，不注册任何具体工具（P2-3 才注册）。
 *
 * 注意：zod v4 的 z.ZodType 是泛型类型，这里用 unknown 收敛，派生 JSON schema 由
 * LangChain.js 的 DynamicTool 内置 zod → JSON schema 转换完成（P3-3 接 graph 时用）。
 */
import type { RegisteredTool } from "./registry.js";

const REGISTRY = new Map<string, RegisteredTool>();

/** 注册一个工具，重复注册抛异常 */
export function registerTool(tool: RegisteredTool): void {
  if (REGISTRY.has(tool.name)) {
    throw new Error(`Tool already registered: ${tool.name}`);
  }
  REGISTRY.set(tool.name, tool);
}

/** 按名字取工具，不存在返回 undefined */
export function getTool(name: string): RegisteredTool | undefined {
  return REGISTRY.get(name);
}

/** 列出所有已注册工具（含 execute 等内部字段，供 invokeTool 用） */
export function listTools(): RegisteredTool[] {
  return Array.from(REGISTRY.values());
}

/** 给 LLM tools 数组用，只暴露 name/description/schema，不暴露 execute */
export function listToolsForLLM(): Array<{
  name: string;
  description: string;
  schema: unknown;
}> {
  return listTools().map((t) => ({
    name: t.name,
    description: t.description,
    schema: t.schema,
  }));
}

/** 清空注册表（仅供测试用） */
export function clearRegistry(): void {
  REGISTRY.clear();
}
