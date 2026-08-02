/**
 * P2-3 注册全部 P2 状态/对话控制工具到 registry。
 *
 * 应用启动时调一次；测试用 clearRegistry 后再调以重置状态。
 */
import { registerTool } from "./registry-impl.js";
import { ALL_TOOL_DEFS } from "./state-tools.js";

export function registerAllTools(): void {
  for (const tool of ALL_TOOL_DEFS) {
    registerTool(tool);
  }
}

export { clearRegistry } from "./registry-impl.js";
