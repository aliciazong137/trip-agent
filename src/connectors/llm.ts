/**
 * LLM connector（OpenAI 兼容，默认 GLM-5.2）。
 *
 * 设计要点：
 * - 手写 fetch，不依赖 openai SDK，便于透传 GLM 特有参数（thinking / reasoning_effort / 大 max_tokens）。
 * - 超时 8s，重试 2 次（指数退避），失败降级返回结构化 ConnectorError，不抛异常到上层。
 * - key 未配置时返回 connector_error，不崩。
 * - 同时支持普通 chat 与 tool calling；streaming 留到 P3-3 接 streamEvents 时再加。
 */

export type ChatRole = "system" | "user" | "assistant" | "tool";

export interface ChatMessage {
  role: ChatRole;
  content: string;
  tool_calls?: ToolCall[];
  tool_call_id?: string;
  name?: string;
}

export interface ToolCall {
  id: string;
  type: "function";
  function: { name: string; arguments: string };
}

export interface ToolDefinition {
  type: "function";
  function: {
    name: string;
    description: string;
    parameters: Record<string, unknown>;
  };
}

export interface LLMResult {
  content: string;
  reasoning_content: string;
  tool_calls: ToolCall[];
  finish_reason: string;
  web_search: WebSearchSource[];
  usage: LLMUsage | null;
  content_filtered: boolean;
  raw: Record<string, unknown>;
}

export interface WebSearchSource {
  title: string;
  link: string;
  media?: string;
  publish_date?: string;
  content?: string;
  refer?: string;
  icon?: string;
}

export interface LLMUsage {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}

export interface ConnectorError {
  errorCode: "connector_error";
  message: string;
  retryable: boolean;
  details?: Record<string, unknown>;
}

export type LLMResponse =
  | { ok: true; result: LLMResult }
  | { ok: false; error: ConnectorError };

export interface LLMConnectorConfig {
  apiBase: string;
  apiKey: string;
  model: string;
  thinkingEnabled: boolean;
  reasoningEffort: string;
  maxTokens: number;
  temperature: number;
  timeoutMs: number;
  maxRetries: number;
  toolStream: boolean;
}

function envBool(value: string | undefined, fallback: boolean): boolean {
  if (value === undefined || value === "") return fallback;
  return value.toLowerCase() === "true" || value === "1" || value === "yes";
}

function envInt(value: string | undefined, fallback: number): number {
  if (value === undefined || value === "") return fallback;
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function envFloat(value: string | undefined, fallback: number): number {
  if (value === undefined || value === "") return fallback;
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

export function loadLLMConfig(overrides: Partial<LLMConnectorConfig> = {}): LLMConnectorConfig {
  return {
    apiBase: overrides.apiBase ?? process.env.LLM_API_BASE ?? "https://open.bigmodel.cn/api/paas/v4",
    apiKey: overrides.apiKey ?? process.env.LLM_API_KEY ?? "",
    model: overrides.model ?? process.env.LLM_MODEL ?? "glm-5.2",
    thinkingEnabled: envBool(process.env.LLM_THINKING_ENABLED, true),
    reasoningEffort: process.env.LLM_REASONING_EFFORT ?? overrides.reasoningEffort ?? "medium",
    maxTokens: envInt(process.env.LLM_MAX_TOKENS, overrides.maxTokens ?? 65536),
    temperature: envFloat(process.env.LLM_TEMPERATURE, overrides.temperature ?? 1.0),
    timeoutMs: overrides.timeoutMs ?? 8000,
    maxRetries: overrides.maxRetries ?? 2,
    toolStream: envBool(process.env.LLM_TOOL_STREAM, overrides.toolStream ?? false),
  };
}

function isKeyConfigured(config: LLMConnectorConfig): boolean {
  return config.apiKey.length > 0 && !config.apiKey.includes("your-");
}

function buildBody(
  config: LLMConnectorConfig,
  messages: ChatMessage[],
  tools?: ToolDefinition[],
  options?: { temperature?: number; maxTokens?: number; thinkingEnabled?: boolean; toolStream?: boolean }
): Record<string, unknown> {
  const body: Record<string, unknown> = {
    model: config.model,
    messages,
    max_tokens: options?.maxTokens ?? config.maxTokens,
    temperature: options?.temperature ?? config.temperature,
  };
  const thinkingOn = options?.thinkingEnabled ?? config.thinkingEnabled;
  if (thinkingOn) {
    body.thinking = { type: "enabled" };
    body.reasoning_effort = config.reasoningEffort;
  }
  if (tools && tools.length > 0) {
    body.tools = tools;
    const stream = options?.toolStream ?? config.toolStream;
    if (stream) {
      body.tool_stream = true;
    }
  }
  return body;
}

async function callWithRetry(
  config: LLMConnectorConfig,
  body: Record<string, unknown>
): Promise<LLMResponse> {
  if (!isKeyConfigured(config)) {
    return {
      ok: false,
      error: {
        errorCode: "connector_error",
        message: "LLM_API_KEY not configured",
        retryable: false,
      },
    };
  }

  const url = `${config.apiBase.replace(/\/$/, "")}/chat/completions`;
  let lastError: ConnectorError | null = null;

  for (let attempt = 0; attempt <= config.maxRetries; attempt++) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), config.timeoutMs);
    try {
      const resp = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${config.apiKey}`,
        },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      clearTimeout(timer);

      if (!resp.ok) {
        const text = await resp.text().catch(() => "");
        const retryable = resp.status >= 500 || resp.status === 429;
        lastError = {
          errorCode: "connector_error",
          message: `LLM HTTP ${resp.status}: ${text.slice(0, 500)}`,
          retryable,
          details: { status: resp.status, body: text.slice(0, 1000) },
        };
        if (!retryable) break;
        await backoff(attempt);
        continue;
      }

      const data = (await resp.json()) as Record<string, unknown>;
      const choice = (data.choices as Array<Record<string, unknown>> | undefined)?.[0];
      if (!choice) {
        return {
          ok: false,
          error: {
            errorCode: "connector_error",
            message: "LLM response missing choices",
            retryable: false,
            details: { raw: data },
          },
        };
      }
      const message = choice.message as Record<string, unknown> | undefined;
      const content = (message?.content as string) ?? "";
      const reasoning_content = (message?.reasoning_content as string) ?? "";
      const tool_calls = (message?.tool_calls as ToolCall[]) ?? [];
      const finish_reason = (choice.finish_reason as string) ?? "stop";
      const web_search = (data.web_search as WebSearchSource[]) ?? [];
      const usageRaw = data.usage as Record<string, unknown> | undefined;
      const usage: LLMUsage | null = usageRaw
        ? {
            prompt_tokens: Number(usageRaw.prompt_tokens ?? 0),
            completion_tokens: Number(usageRaw.completion_tokens ?? 0),
            total_tokens: Number(usageRaw.total_tokens ?? 0),
          }
        : null;
      const content_filter = (data.content_filter as unknown[]) ?? [];
      const content_filtered = content_filter.length > 0;

      // 内容被拦截时，不让拦截内容流到上层
      if (content_filtered) {
        return {
          ok: false,
          error: {
            errorCode: "connector_error",
            message: "LLM response content filtered",
            retryable: false,
            details: { content_filter },
          },
        };
      }

      return {
        ok: true,
        result: {
          content,
          reasoning_content,
          tool_calls,
          finish_reason,
          web_search,
          usage,
          content_filtered,
          raw: data,
        },
      };
    } catch (err) {
      clearTimeout(timer);
      const isAbort = err instanceof Error && err.name === "AbortError";
      lastError = {
        errorCode: "connector_error",
        message: isAbort ? `LLM timeout after ${config.timeoutMs}ms` : `LLM fetch error: ${(err as Error).message}`,
        retryable: true,
      };
      if (attempt < config.maxRetries) {
        await backoff(attempt);
      }
    }
  }

  return { ok: false, error: lastError ?? { errorCode: "connector_error", message: "unknown", retryable: false } };
}

function backoff(attempt: number): Promise<void> {
  const ms = Math.min(1000 * 2 ** attempt, 4000);
  return new Promise((r) => setTimeout(r, ms));
}

export class LLMConnector {
  private config: LLMConnectorConfig;

  constructor(config: Partial<LLMConnectorConfig> = {}) {
    this.config = loadLLMConfig(config);
  }

  getConfig(): LLMConnectorConfig {
    return { ...this.config };
  }

  isConfigured(): boolean {
    return isKeyConfigured(this.config);
  }

  async chat(
    messages: ChatMessage[],
    options?: { temperature?: number; maxTokens?: number; thinkingEnabled?: boolean; toolStream?: boolean }
  ): Promise<LLMResponse> {
    const body = buildBody(this.config, messages, undefined, options);
    return callWithRetry(this.config, body);
  }

  async chatWithTools(
    messages: ChatMessage[],
    tools: ToolDefinition[],
    options?: { temperature?: number; maxTokens?: number; thinkingEnabled?: boolean; toolStream?: boolean }
  ): Promise<LLMResponse> {
    const body = buildBody(this.config, messages, tools, options);
    return callWithRetry(this.config, body);
  }
}
