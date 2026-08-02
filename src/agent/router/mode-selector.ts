/**
 * P2-1 Mode Selector
 *
 * 纯映射函数，把 IntentType 映射到默认 ModeBias。
 * 用于 graph 层在只有 intent 时回推 modeBias。
 *
 * 规则表 rules.ts 里已带 modeBias，所以 recognizer 命中时直接用规则的 modeBias。
 * 这个函数用于 graph 层从 checkpoint state 只拿到 intent 字段时回推 modeBias 的场景。
 *
 * 映射规则（项目 plan §0）：
 * - react 偏置：信息检索类
 * - planner 偏置：新建行程
 * - reflection 偏置：检查/修改行程
 */
import type { IntentType, ModeBias } from "../intent/types.js";

const INTENT_TO_MODE: Record<IntentType, ModeBias> = {
  new_plan: "planner",
  revise_day: "reflection",
  verify: "reflection",
  search_poi: "react",
  search_hotel: "react",
  search_flight: "react",
  search_train: "react",
  general_chat: "react",
};

/**
 * 把 IntentType 映射到默认 ModeBias。
 * 纯映射，无副作用，无 I/O。
 */
export function selectModeBias(intent: IntentType): ModeBias {
  return INTENT_TO_MODE[intent];
}
