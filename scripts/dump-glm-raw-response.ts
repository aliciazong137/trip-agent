/**
 * GLM-5.2 完整原始响应 dump（P3-1 调试）。
 *
 * 用法：npx tsx scripts/dump-glm-raw-response.ts
 *
 * 目的：打印 GLM-5.2 第一次调用返回的完整 JSON 响应，看原始结构。
 */
import { LLMConnector } from "../src/connectors/llm.js";

if (typeof process.loadEnvFile === "function") {
  process.loadEnvFile();
}

const connector = new LLMConnector();

async function main(): Promise<void> {
  if (!connector.isConfigured()) {
    console.error("LLM_API_KEY not configured. Check .env");
    process.exit(1);
  }

  console.log("===== GLM-5.2 thinking + tool calling 完整原始响应 =====\n");

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

  const resp = await connector.chatWithTools(
    [
      { role: "system", content: "你是旅行规划助手，需要调用工具检索 POI。" },
      { role: "user", content: "帮我查大阪的 USJ" },
    ],
    tools
  );

  if (!resp.ok) {
    console.log("error:", JSON.stringify(resp.error, null, 2));
    return;
  }

  console.log("=== 完整原始 raw 响应 ===");
  console.log(JSON.stringify(resp.result.raw, null, 2));

  console.log("\n=== 解析后的字段 ===");
  console.log("content:", JSON.stringify(resp.result.content));
  console.log("reasoning_content:", JSON.stringify(resp.result.reasoning_content));
  console.log("tool_calls:", JSON.stringify(resp.result.tool_calls, null, 2));
  console.log("finish_reason:", resp.result.finish_reason);
  console.log("web_search:", resp.result.web_search.length, "条");
  console.log("usage:", resp.result.usage);
  console.log("content_filtered:", resp.result.content_filtered);
}

main().catch((e) => {
  console.error("unhandled:", e);
  process.exit(1);
});
