import { Hono } from "hono";
import { handleGetSession, handlePlan, handleReviseDay } from "../agent/orchestrator.js";
import type { PlanRequest, ReviseDayRequest } from "../types/api.js";

export const app = new Hono();

app.get("/health", (c) => c.json({ status: "ok", version: "0.1.0-p0" }));

app.post("/plan", async (c) => {
  const body = (await c.req.json()) as PlanRequest;
  const result = await handlePlan(body);
  const status = result.status === "failed" ? 400 : 200;
  return c.json(result, status);
});

app.post("/revise-day", async (c) => {
  const body = (await c.req.json()) as ReviseDayRequest;
  const result = await handleReviseDay(body);
  const status = result.status === "failed" ? 400 : 200;
  return c.json(result, status);
});

app.get("/session/:id", async (c) => {
  const sessionId = c.req.param("id");
  const result = await handleGetSession(sessionId);
  if (!result) {
    return c.json(
      {
        status: "failed",
        error: {
          errorCode: "schema_error",
          message: "Session not found",
          details: { sessionId },
        },
        warnings: [],
        confirmationRequests: [],
      },
      404
    );
  }
  return c.json(result);
});
