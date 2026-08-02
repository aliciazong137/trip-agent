/**
 * GLM web_search 独立工具 API 测试。
 *
 * 端点：POST https://open.bigmodel.cn/api/paas/v4/web_search
 *
 * 用法：npx tsx scripts/dump-glm-web-search-api.ts
 *
 * 目的：直接调独立 web_search API，看返回的结构化搜索结果。
 */
import { LLMConnector } from "../src/connectors/llm.js";

if (typeof process.loadEnvFile === "function") {
  process.loadEnvFile();
}

const connector = new LLMConnector();

async function main(): Promise<void> {
  if (!connector.isConfigured()) {
    console.error("LLM_API_KEY not configured");
    process.exit(1);
  }
  const cfg = connector.getConfig();
  const url = `${cfg.apiBase.replace(/\/$/, "")}/web_search`;

  const body = {
    search_query: "大阪 USJ 环球影城 门票价格",
    search_engine: "search_pro_quark",
    search_intent: true,
    count: 10,
    search_recency_filter: "oneMonth",
  };

  console.log("===== GLM 独立 web_search API (search_pro_quark + search_intent=true + oneMonth) =====");
  console.log("URL:", url);
  console.log("请求 body:", JSON.stringify(body, null, 2));

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  try {
    const resp = await fetch(url, {
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
    let data: Record<string, unknown>;
    try {
      data = JSON.parse(text) as Record<string, unknown>;
    } catch {
      console.log("非 JSON 响应:", text.slice(0, 500));
      return;
    }
    console.log("=== 完整响应 ===");
    console.log(JSON.stringify(data, null, 2));
    console.log("=== 顶层字段 ===");
    console.log(Object.keys(data));
    if (Array.isArray(data.search_result)) {
      console.log("\n=== search_result 条数 ===");
      console.log((data.search_result as unknown[]).length);
      console.log("\n=== 第一条 ===");
      console.log(JSON.stringify((data.search_result as unknown[])[0], null, 2));
    }
  } catch (e) {
    clearTimeout(timer);
    console.error("fetch error:", e instanceof Error ? e.message : e);
  }
}

main().catch((e) => {
  console.error("unhandled:", e);
  process.exit(1);
});
