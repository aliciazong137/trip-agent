/**
 * GLM-5.2 web_search 返回结构验证（最小入参）。
 *
 * 用法：npx tsx scripts/dump-glm-web-search-v2.ts
 *
 * 目的：按官方最简入参格式调 web_search，确认 search_result:true 时
 *       响应顶层是否返回 web_search 数组。
 */
import { LLMConnector, type ChatMessage } from "../src/connectors/llm.js";

if (typeof process.loadEnvFile === "function") {
  process.loadEnvFile();
}

const connector = new LLMConnector();

async function call(label: string, webSearchParams: Record<string, unknown>, userMessage: string): Promise<void> {
  console.log(`\n===== ${label} =====`);
  const cfg = connector.getConfig();
  const body = {
    model: cfg.model,
    messages: [
      { role: "user", content: userMessage },
    ] as ChatMessage[],
    tools: [
      {
        type: "web_search",
        web_search: webSearchParams,
      },
    ],
    max_tokens: 2048,
    temperature: 0.7,
  };

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 60000);
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
    console.log("顶层字段:", Object.keys(data));
    console.log("是否有 web_search 数组:", Array.isArray(data.web_search));
    if (Array.isArray(data.web_search)) {
      console.log("web_search 条数:", (data.web_search as unknown[]).length);
      console.log("web_search 第一条:", JSON.stringify((data.web_search as unknown[])[0], null, 2));
    }
    const choice = (data.choices as Array<Record<string, unknown>>)?.[0];
    const msg = choice?.message as Record<string, unknown> | undefined;
    console.log("content 前 200 字:", (msg?.content as string)?.slice(0, 200));
    console.log("finish_reason:", choice?.finish_reason);
    console.log("usage:", JSON.stringify(data.usage));
  } catch (e) {
    clearTimeout(timer);
    console.error("fetch error:", e);
  }
}

async function main(): Promise<void> {
  if (!connector.isConfigured()) {
    console.error("LLM_API_KEY not configured");
    process.exit(1);
  }

  // 测试 1：最简入参，search_result:true
  await call(
    "Test 1: 最简入参 search_result=true",
    {
      enable: true,
      search_engine: "search_pro",
      search_result: true,
      count: 5,
      content_size: "high",
    },
    "大阪 USJ 环球影城门票价格"
  );

  // 测试 2：加 search_query 强制触发
  await call(
    "Test 2: 加 search_query 强制触发",
    {
      enable: true,
      search_engine: "search_pro",
      search_result: true,
      search_query: "大阪 USJ 环球影城 门票价格",
      count: 5,
      content_size: "high",
    },
    "大阪 USJ 环球影城门票价格"
  );

  // 测试 3：关 search_result 对照
  await call(
    "Test 3: search_result=false 对照",
    {
      enable: true,
      search_engine: "search_pro",
      search_result: false,
      count: 5,
      content_size: "high",
    },
    "大阪 USJ 环球影城门票价格"
  );
}

main().catch((e) => {
  console.error("unhandled:", e);
  process.exit(1);
});
