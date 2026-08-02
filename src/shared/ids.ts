/**
 * 统一的 ID 合法性校验，供 store.ts / registry.ts / API route 共用。
 *
 * - sessionId：项目固定格式 sess_ + 12 位 hex（orchestrator.newSessionId 生成），
 *   严格限制字符集和长度，杜绝路径穿越。
 * - resultId：搜索缓存文件名，1-64 字符的字母数字下划线连字符。
 */
export const SESSION_ID_RE = /^sess_[a-f0-9]{12}$/;
export const RESULT_ID_RE = /^[a-zA-Z0-9_-]{1,64}$/;

/** sessionId 类型守卫 */
export function isValidSessionId(value: unknown): value is string {
  return typeof value === "string" && SESSION_ID_RE.test(value);
}

/** sessionId 断言，非法时抛错；供 store 公开方法入口显式校验 */
export function assertValidSessionId(value: unknown): asserts value is string {
  if (!isValidSessionId(value)) {
    throw new Error(`invalid sessionId: ${String(value)}`);
  }
}

/** resultId 类型守卫 */
export function isValidResultId(value: unknown): value is string {
  return typeof value === "string" && RESULT_ID_RE.test(value);
}
