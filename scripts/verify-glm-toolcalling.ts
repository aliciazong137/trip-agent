/**
 * GLM-5.2 验证脚本（P3-1 提前验证）。
 *
 * 目的：在接 LangGraph 之前，确认：
 *   1. API key 能通
 *   2. thinking 模式能正常对话
 *   3. thinking + tool calling 是否兼容（关键风险点）
 *
 * 用法：npx tsx scripts/verify-glm-toolcalling.ts
 */
import { LLMConnector } from "../src/connectors/llm.js";

// Node 22+ 内置 .env 加载
if (typeof process.loadEnvFile === "function") {
  process.loadEnvFile();
}

const connector = new LLMConnector();

function header(title: string): void {
  console.log(`\n===== ${title} =====`);
}

async function main(): Promise<void> {
  if (!connector.isConfigured()) {
    console.error("LLM_API_KEY not configured. Check .env");
    process.exit(1);
  }
  const cfg = connector.getConfig();
  console.log("config:", { model: cfg.model, base: cfg.apiBase, thinking: cfg.thinkingEnabled, effort: cfg.reasoningEffort, maxTokens: cfg.maxTokens });

  // 1. 普通对话（thinking 开）
  header("Test 1: thinking + 纯对话");
  const t1 = await connector.chat([
    { role: "system", content: "你是旅行规划助手，用中文回答。" },
    { role: "user", content: "用一句话说大阪最值得去的景点。" },
  ]);
  printResult(t1);

  // 2. tool calling（thinking 开）
  header("Test 2: thinking + tool calling");
  const tools = [
    {
      type: "function" as const,
      function: {
        name: "search_pois",
        description: "按关键词检索城市 POI（景点/餐厅）。",
        parameters: {
          type: "object",
          properties: {
            city: { type: "string", description: "城市名" },
            keyword: { type: "string", description: "关键词" },
          },
          required: ["city"],
        },
      },
    },
  ];
  const t2 = await connector.chatWithTools(
    [
      { role: "system", content: "你是旅行规划助手，需要调用工具检索 POI。" },
      { role: "user", content: "帮我查大阪的 USJ" },
    ],
    tools
  );
  printResult(t2);

  // 3. tool calling（thinking 关）—— 对照
  header("Test 3: thinking 关 + tool calling（对照）");
  const t3 = await connector.chatWithTools(
    [
      { role: "system", content: "你是旅行规划助手，需要调用工具检索 POI。" },
      { role: "user", content: "帮我查大阪的 USJ" },
    ],
    tools,
    { thinkingEnabled: false }
  );
  printResult(t3);

  // 结论
  header("结论");
  const t2ToolCalls = t2.ok ? t2.result.tool_calls.length : -1;
  const t3ToolCalls = t3.ok ? t3.result.tool_calls.length : -1;
  console.log(`thinking+tools tool_calls: ${t2ToolCalls}`);
  console.log(`no-thinking+tools tool_calls: ${t3ToolCalls}`);
  if (t2ToolCalls > 0) {
    console.log("✅ GLM-5.2 thinking + tool calling 兼容，ReAct 可直接用 thinking 模式");
  } else if (t3ToolCalls > 0) {
    console.log("⚠️  thinking 模式下 tool calling 失败，但关 thinking 后正常；ReAct 节点需关闭 thinking");
  } else {
    console.log("❌ 两种模式 tool calling 都失败，需检查 key 或参数");
  }
}

function printResult(r: Awaited<ReturnType<LLMConnector["chat"]>>): void {
  if (!r.ok) {
    console.log("error:", r.error);
    return;
  }
  console.log("content:", r.result.content.slice(0, 400));
  console.log("reasoning_content:", r.result.reasoning_content.slice(0, 400));
  console.log("tool_calls:", JSON.stringify(r.result.tool_calls, null, 2));
  console.log("finish_reason:", r.result.finish_reason);
  console.log("web_search:", r.result.web_search.length);
  console.log("usage:", JSON.stringify(r.result.usage));
  console.log("content_filtered:", r.result.content_filtered);
}

main().catch((e) => {
  console.error("unhandled:", e);
  process.exit(1);
});
