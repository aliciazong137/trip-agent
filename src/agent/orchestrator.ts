import { randomUUID } from "node:crypto";
import type {
  PlanRequest,
  PlanResponse,
  ReviseDayRequest,
  ReviseDayResponse,
  SessionResponse,
} from "../types/api.js";
import {
  createSession,
  loadSession,
  saveItinerary,
  touchSession,
} from "../context/store.js";
import { validateAgainstSchema } from "../verifier/schema-validator.js";
import { planItinerary } from "../phases/plan.js";
import type { Itinerary } from "../types/domain.js";

function newSessionId(): string {
  return `sess_${randomUUID().replace(/-/g, "").slice(0, 12)}`;
}

export async function handlePlan(body: PlanRequest): Promise<PlanResponse> {
  const metaCheck = await validateAgainstSchema("trip-meta.schema.json", body.tripMeta);
  if (!metaCheck.valid) {
    return {
      sessionId: "",
      status: "failed",
      itinerary: {},
      warnings: [],
      confirmationRequests: [],
      error: {
        errorCode: "schema_error",
        message: "tripMeta failed schema validation",
        details: { errors: metaCheck.errors },
      },
    };
  }

  const sessionId = newSessionId();
  await createSession(sessionId, body.tripMeta, body.guideText);

  const result = await planItinerary(sessionId, { allowWhitelistFallback: true });

  if (!result.ok || !result.result) {
    return {
      sessionId,
      status: "failed",
      itinerary: {},
      warnings: [
        ...(result.buildWarnings ?? []),
        ...(result.constraintWarnings ?? []),
      ],
      confirmationRequests: [],
      error: {
        errorCode: "schema_error",
        message: "Generated itinerary failed validation",
        details: {
          schemaErrors: result.schemaErrors,
          constraintErrors: result.constraintErrors,
          error: result.error,
        },
      },
    };
  }

  const itinerary = result.result.itinerary;
  const warnings = [
    ...(result.buildWarnings ?? []),
    ...(result.constraintWarnings ?? []),
  ];

  return {
    sessionId,
    status: "ok",
    itinerary: itinerary as unknown as Record<string, unknown>,
    warnings,
    confirmationRequests: [],
  };
}

export async function handleReviseDay(body: ReviseDayRequest): Promise<ReviseDayResponse> {
  const session = await loadSession(body.sessionId);
  if (!session) {
    return {
      sessionId: body.sessionId,
      status: "failed",
      itinerary: {},
      changedDays: [],
      warnings: [],
      confirmationRequests: [],
      error: {
        errorCode: "schema_error",
        message: "Session not found",
        details: { sessionId: body.sessionId },
      },
    };
  }

  const itinerary: Itinerary = {
    ...session.itinerary,
    days: session.itinerary.days,
    version: (session.itinerary.version ?? 1) + 1,
  };

  const dayIndex = itinerary.days.findIndex((d) => d.day === body.day);
  if (dayIndex === -1) {
    return {
      sessionId: body.sessionId,
      status: "failed",
      itinerary: {},
      changedDays: [],
      warnings: [],
      confirmationRequests: [],
      error: {
        errorCode: "schema_error",
        message: `Day ${body.day} not found in itinerary`,
        details: { day: body.day },
      },
    };
  }

  itinerary.days[dayIndex] = {
    ...itinerary.days[dayIndex],
    time_blocks: [
      {
        poi_id: "poi_shinsekai",
        start_time: "11:00",
        end_time: "12:30",
        reason: `P0 mock revise: ${body.instruction}`,
        locked: false,
      },
    ],
    estimated_total_minutes: 90,
    estimated_total_cost: 900,
    area_cluster: ["Shinsekai"],
  };

  const itineraryCheck = await validateAgainstSchema("itinerary.schema.json", itinerary);
  if (!itineraryCheck.valid) {
    return {
      sessionId: body.sessionId,
      status: "failed",
      itinerary: {},
      changedDays: [],
      warnings: [],
      confirmationRequests: [],
      error: {
        errorCode: "schema_error",
        message: "Revised itinerary failed schema validation",
        details: { errors: itineraryCheck.errors },
      },
    };
  }

  await saveItinerary(body.sessionId, itinerary);

  return {
    sessionId: body.sessionId,
    status: "ok",
    itinerary: itinerary as unknown as Record<string, unknown>,
    changedDays: [body.day],
    warnings: ["P0 mock revise — deterministic repair not yet implemented"],
    confirmationRequests: [],
  };
}

export async function handleGetSession(sessionId: string): Promise<SessionResponse | null> {
  const session = await loadSession(sessionId);
  if (!session) return null;

  const updatedAt = await touchSession(sessionId);

  return {
    sessionId,
    tripMeta: session.tripMeta,
    poiList: (session.poiList.pois as unknown[]) ?? [],
    itinerary: session.itinerary as unknown as Record<string, unknown>,
    verifierStatus: { phase: session.session.phase, lastCheck: "P0 not run" },
    updatedAt,
  };
}
