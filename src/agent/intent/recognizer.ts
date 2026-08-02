/**
 * P2-1 规则快车道意图识别器
 *
 * 设计要点（项目 plan §2.2）：
 * - 永久不做 LLM 慢车道分类，避免额外一次 LLM 调用。
 * - 规则能识别就给出 intent/modeBias。
 * - 规则不能识别就返回 general_chat + react。
 * - 纯函数，无 I/O，无 LLM 调用。
 */
import { INTENT_RULES } from "./rules.js";
import type { RecognizerResult } from "./types.js";

/**
 * 对用户输入做基础规范化：
 * - trim 去掉首尾空白
 * - 不转小写：中文不受影响，英文关键词已在规则正则里用 `i` flag 处理
 */
function normalize(text: string): string {
  return text.trim();
}

/**
 * 识别用户消息的意图与 mode 偏置。
 *
 * @param text 用户原始消息
 * @returns 识别结果；规则命中 confidence=1，兜底 confidence=0
 */
export function recognizeIntent(text: string): RecognizerResult {
  const normalized = normalize(text);

  // 空字符串或纯空白 → 兜底
  if (normalized.length === 0) {
    return {
      intent: "general_chat",
      modeBias: "react",
      confidence: 0,
    };
  }

  // 按声明顺序匹配，第一条命中即返回
  for (const rule of INTENT_RULES) {
    if (rule.pattern.test(normalized)) {
      return {
        intent: rule.intent,
        modeBias: rule.modeBias,
        matchedRuleId: rule.id,
        confidence: 1,
      };
    }
  }

  // 全部未命中 → general_chat + react
  return {
    intent: "general_chat",
    modeBias: "react",
    confidence: 0,
  };
}
