/**
 * GLM-5.2 web_search 数组返回条件系统测试。
 *
 * 用法：npx tsx scripts/dump-glm-web-search-v3.ts
 *
 * 目的：试多种参数组合，找出哪种能让 GLM-5.2 返回顶层 web_search 数组。
 */
import { LLMConnector, type ChatMessage } from "../src/connectors/llm.js";

if (typeof process.loadEnvFile === "function") {
  process.loadEnvFile();
}

const connector = new LLMConnector();

interface TestCase {
  label: string;
  webSearch: Record<string, unknown>;
  userMessage: string;
  thinkingOff?: boolean;
}

const cases: TestCase[] = [
  {
    label: "1. 最简 search_std + search_result:true",
    webSearch: {
      enable: true,
      search_engine: "search_std",
      search_result: true,
      count: 5,
    },
    userMessage: "大阪 USJ 环球影城门票价格",
  },
  {
    label: "2. search_pro + search_result:true",
    webSearch: {
      enable: true,
      search_engine: "search_pro",
      search_result: true,
      count: 5,
    },
    userMessage: "大阪 USJ 环球影城门票价格",
  },
  {
    label: "3. search_pro + search_query 强制 + search_result:true",
    webSearch: {
      enable: true,
      search_engine: "search_pro",
      search_result: true,
      search_query: "大阪 USJ 环球影城 门票价格",
      count: 5,
    },
    userMessage: "大阪 USJ 环球影城门票价格",
  },
  {
    label: "4. search_intent:false + result_sequence:before + search_result:true",
    webSearch: {
      enable: true,
      search_engine: "search_pro",
      search_result: true,
      search_intent: "false",
      result_sequence: "before",
      count: 5,
    },
    userMessage: "大阪 USJ 环球影城门票价格",
  },
  {
    label: "5. content_size:medium + require_search:true",
    webSearch: {
      enable: true,
      search_engine: "search_pro",
      search_result: true,
      content_size: "medium",
      require_search: true,
      count: 5,
    },
    userMessage: "大阪 USJ 环球影城门票价格",
  },
  {
    label: "6. 关 thinking（不传 thinking 字段）+ search_result:true",
    webSearch: {
      enable: true,
      search_engine: "search_pro",
      search_result: true,
      count: 5,
    },
    userMessage: "大阪 USJ 环球影城门票价格",
    thinkingOff: true,
  },
  {
    label: "7. search_pro_sogou + search_result:true",
    webSearch: {
      enable: true,
      search_engine: "search_pro_sogou",
      search_result: true,
      count: 10,
    },
    userMessage: "大阪 USJ 环球影城门票价格",
  },
  {
    label: "8. 全字段 + search_prompt 自定义",
    webSearch: {
      enable: true,
      search_engine: "search_pro",
      search_result: true,
      search_query: "USJ 大阪 门票",
      search_intent: "false",
      count: 5,
      content_size: "high",
      result_sequence: "before",
      require_search: true,
      search_recency_filter: "noLimit",
      search_prompt: "请整理搜索结果为结构化数据",
    },
    userMessage: "大阪 USJ 环球影城门票价格",
    thinkingOff: true,
  },
];

async function runCase(c: TestCase): Promise<void> {
  console.log(`\n===== ${c.label} =====`);
  const cfg = connector.getConfig();
  const body: Record<string, unknown> = {
    model: cfg.model,
    messages: [{ role: "user", content: c.userMessage }] as ChatMessage[],
    tools: [{ type: "web_search", web_search: c.webSearch }],
    max_tokens: 2048,
    temperature: 0.7,
  };
  if (!c.thinkingOff) {
    body.thinking = { type: "enabled" };
    body.reasoning_effort = cfg.reasoningEffort;
  }

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
    let data: Record<string, unknown>;
    try {
      data = JSON.parse(text) as Record<string, unknown>;
    } catch {
      console.log("HTTP", resp.status, "非 JSON 响应:", text.slice(0, 200));
      return;
    }
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
    console.log("content 前 100 字:", (msg?.content as string)?.slice(0, 100));
    console.log("finish_reason:", choice?.finish_reason);
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
  console.log("model:", connector.getConfig().model);
  // 只跑 case 8：全字段 + search_prompt
  await runCase(cases[7]);
}

main().catch((e) => {
  console.error("unhandled:", e);
  process.exit(1);
});
