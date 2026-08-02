/**
 * P2-1 Intent Recognizer 类型定义
 *
 * 设计要点：
 * - modeBias 只作为 prompt/tool 偏置，不旁路 graph、不裁剪工具（项目 plan §2.1）。
 * - intent 用于 graph 层选择 prompt 片段与工具排序。
 * - 规则不确定时返回 general_chat + react（项目 plan §2.2）。
 * - 永久不做 LLM 慢车道分类，避免额外一次 LLM 调用。
 */

/**
 * 三种 mode 偏置，对应三种 prompt 片段：
 * - react: 优先信息检索工具，适合 POI/景点/餐厅/机票/酒店/火车/开放问答
 * - planner: 优先补齐 trip-meta、调用写状态工具与 build_itinerary
 * - reflection: 优先 verify_itinerary，把 errors/warnings 翻译成中文解释
 */
export type ModeBias = "react" | "planner" | "reflection";

/**
 * 规则能识别的意图类别。
 * `general_chat` 是兜底，规则全部未命中时使用。
 */
export type IntentType =
  | "new_plan"
  | "revise_day"
  | "verify"
  | "search_poi"
  | "search_hotel"
  | "search_flight"
  | "search_train"
  | "general_chat";

/**
 * 单条意图规则。
 * 规则表按声明顺序匹配，第一条命中即返回。
 */
export interface IntentRule {
  /** 规则唯一 id，用于日志与测试 */
  id: string;
  /** 正则模式，命中即触发该规则 */
  pattern: RegExp;
  /** 命中时产出的意图 */
  intent: IntentType;
  /** 命中时产出的 mode 偏置 */
  modeBias: ModeBias;
  /** 规则描述，便于维护 */
  description?: string;
}

/**
 * 识别器输出。
 * 规则命中 confidence=1；兜底 confidence=0。
 */
export interface RecognizerResult {
  intent: IntentType;
  modeBias: ModeBias;
  /** 命中的规则 id；兜底返回 undefined */
  matchedRuleId?: string;
  /** 0-1，规则命中为 1，兜底为 0 */
  confidence: number;
}
