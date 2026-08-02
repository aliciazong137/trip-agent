/**
 * GLM-5.2 内置 web_search 搜索结果字段 dump（P4 预研）。
 *
 * 用法：npx tsx scripts/dump-glm-web-search.ts
 *
 * 目的：看 GLM-5.2 用 tools.web_search 搜索时，搜索结果放在响应的哪个字段。
 */
import { LLMConnector, type ChatMessage, type ToolDefinition } from "../src/connectors/llm.js";

if (typeof process.loadEnvFile === "function") {
  process.loadEnvFile();
}

const connector = new LLMConnector();

async function main(): Promise<void> {
  if (!connector.isConfigured()) {
    console.error("LLM_API_KEY not configured. Check .env");
    process.exit(1);
  }

  console.log("===== GLM-5.2 内置 web_search 搜索结果 dump =====\n");

  // GLM web_search 工具定义（按官方 schema）
  const webSearchTool: ToolDefinition = {
    type: "function" as const,
    // 用 function 类型包装 web_search 不对；GLM 的 web_search 是独立 tool 类型
    // 这里用 hack：直接把 web_search 作为 tool 传，看 GLM 怎么返回
    function: {
      name: "web_search",
      description: "GLM built-in web search",
      parameters: {
        type: "object",
        properties: {
          query: { type: "string" },
        },
      },
    },
  };
  void webSearchTool;

  // 正确做法：直接在请求 body 里加 tools 数组，含 web_search 类型项
  // 我们的 connector 现在不支持传原始 tool 类型，这里先手动 fetch 看结构
  const cfg = connector.getConfig();
  const body = {
    model: cfg.model,
    messages: [
      { role: "user", content: "帮我查大阪 USJ 环球影城 2026 年门票价格和开放时间" },
    ] as ChatMessage[],
    tools: [
      {
        type: "web_search",
        web_search: {
          enable: true,
          search_engine: "search_std",
          search_query: "大阪 USJ 环球影城 2026 门票价格",
          search_intent: "false",
          count: 5,
          content_size: "high",
          search_result: true,
          require_search: true,
          search_recency_filter: "noLimit",
        },
      },
    ],
    thinking: { type: "enabled" },
    reasoning_effort: cfg.reasoningEffort,
    max_tokens: cfg.maxTokens,
    temperature: cfg.temperature,
  };

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  try {
    const resp = await fetch(`${cfg.apiBase.replace(/\/$/, "")}/chat/completions`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${cfg.apiKey}`,
      },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    clearTimeout(timer);

    const text = await resp.text();
    console.log("HTTP status:", resp.status);
    const data = JSON.parse(text) as Record<string, unknown>;
    console.log("=== 完整原始响应 ===");
    console.log(JSON.stringify(data, null, 2));
  } catch (e) {
    clearTimeout(timer);
    console.error("fetch error:", e);
  }
}

main().catch((e) => {
  console.error("unhandled:", e);
  process.exit(1);
});
