import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { rm } from "node:fs/promises";
import path from "node:path";
import {
  createSession,
  saveSearchResult,
  loadSearchResult,
  isValidResultId,
} from "../src/context/store.js";

const SESSION_ID = "sess_a1b2c3d4e5f7";
const CONTEXT_DIR = path.resolve(process.cwd(), "context", SESSION_ID);

const baseTripMeta = {
  city: "Osaka",
  days: 2,
  pace: "normal" as const,
  mustVisit: [],
  avoid: [],
};

describe("search-cache", () => {
  beforeEach(async () => {
    await rm(CONTEXT_DIR, { recursive: true, force: true });
    await createSession(SESSION_ID, baseTripMeta, "test");
  });
  afterEach(async () => {
    await rm(CONTEXT_DIR, { recursive: true, force: true });
  });

  describe("isValidResultId", () => {
    it("合法 resultId 通过", () => {
      expect(isValidResultId("res_abc123")).toBe(true);
      expect(isValidResultId("abc-123_xyz")).toBe(true);
      expect(isValidResultId("r1")).toBe(true);
    });

    it("拒绝路径穿越", () => {
      expect(isValidResultId("../../etc/passwd")).toBe(false);
      expect(isValidResultId("../secret")).toBe(false);
      expect(isValidResultId("a/b")).toBe(false);
    });

    it("拒绝空和超长", () => {
      expect(isValidResultId("")).toBe(false);
      expect(isValidResultId("a".repeat(65))).toBe(false);
    });
  });

  describe("saveSearchResult + loadSearchResult", () => {
    it("写入后能读回", async () => {
      const data = { items: [{ title: "USJ", price: 8600 }] };
      await saveSearchResult(SESSION_ID, "res_test1", data);
      const loaded = await loadSearchResult(SESSION_ID, "res_test1");
      expect(loaded).toEqual(data);
    });

    it("不存在的 resultId 返回 null", async () => {
      const loaded = await loadSearchResult(SESSION_ID, "res_nonexistent");
      expect(loaded).toBeNull();
    });

    it("非法 resultId 写入抛异常", async () => {
      await expect(
        saveSearchResult(SESSION_ID, "../escape", { bad: true })
      ).rejects.toThrow(/invalid resultId/);
    });

    it("非法 resultId 读取返回 null（不抛异常）", async () => {
      const loaded = await loadSearchResult(SESSION_ID, "../../etc/passwd");
      expect(loaded).toBeNull();
    });

    it("覆盖写相同 resultId", async () => {
      await saveSearchResult(SESSION_ID, "res_dup", { v: 1 });
      await saveSearchResult(SESSION_ID, "res_dup", { v: 2 });
      const loaded = await loadSearchResult(SESSION_ID, "res_dup");
      expect(loaded).toEqual({ v: 2 });
    });
  });
});
