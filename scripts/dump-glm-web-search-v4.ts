/**
 * GLM-5.2 web_search 最简调用，看 search_result 不传时的行为。
 */
import { LLMConnector, type ChatMessage } from "../src/connectors/llm.js";

if (typeof process.loadEnvFile === "function") {
  process.loadEnvFile();
}

const connector = new LLMConnector();

async function call(label: string, webSearchParams: Record<string, unknown>): Promise<void> {
  console.log(`\n===== ${label} =====`);
  console.log("请求 web_search 参数:", JSON.stringify(webSearchParams));
  const cfg = connector.getConfig();
  const body = {
    model: cfg.model,
    messages: [{ role: "user", content: "大阪 USJ 环球影城门票价格" }] as ChatMessage[],
    tools: [{ type: "web_search", web_search: webSearchParams }],
    max_tokens: 8192,
    temperature: 0.7,
  };

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 120000);
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
    const data = JSON.parse(text) as Record<string, unknown>;
    console.log("HTTP:", resp.status);
    console.log("顶层字段:", Object.keys(data));
    const hasWebSearch = Array.isArray(data.web_search);
    console.log("★ 是否返回 web_search 数组:", hasWebSearch);
    if (hasWebSearch) {
      const arr = data.web_search as unknown[];
      console.log("  web_search 条数:", arr.length);
      console.log("  第一条:", JSON.stringify(arr[0], null, 2));
    }
    const choice = (data.choices as Array<Record<string, unknown>>)?.[0];
    const msg = choice?.message as Record<string, unknown> | undefined;
    console.log("content 前 200 字:", (msg?.content as string)?.slice(0, 200));
    console.log("finish_reason:", choice?.finish_reason);
    console.log("usage:", JSON.stringify(data.usage));
  } catch (e) {
    clearTimeout(timer);
    console.error("fetch error:", e instanceof Error ? e.message : e);
  }
}

async function main(): Promise<void> {
  if (!connector.isConfigured()) {
    console.error("LLM_API_KEY not configured");
    process.exit(1);
  }

  // A: 最简，只 enable + search_engine，不传 search_result
  await call("A. 最简 enable+search_engine，不传 search_result", {
    enable: true,
    search_engine: "search_std",
  });

  // B: 只加 search_result: true
  await call("B. + search_result:true", {
    enable: true,
    search_engine: "search_std",
    search_result: true,
  });

  // C: 加 count + content_size
  await call("C. + count:5 + content_size:high + search_result:true", {
    enable: true,
    search_engine: "search_std",
    search_result: true,
    count: 5,
    content_size: "high",
  });
}

main().catch((e) => {
  console.error("unhandled:", e);
  process.exit(1);
});
