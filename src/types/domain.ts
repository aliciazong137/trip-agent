import type { Pace, TripMeta } from "./api.js";

export type { Pace, TripMeta };

export type SourceRefType = "guide" | "whitelist" | "external";

export interface SourceRef {
  type: SourceRefType;
  ref: string;
  snippet?: string;
}

export type PoiCategory = "attraction" | "restaurant" | "shopping" | "experience" | "transport";

export type PoiPriority = "must" | "nice" | "optional";

export interface UnknownCost {
  unknown: true;
}

export type EstimatedCost = number | UnknownCost;

export interface Poi {
  id: `poi_${string}`;
  name: string;
  aliases?: string[];
  category: PoiCategory;
  area: string;
  source_refs: SourceRef[];
  confidence: number;
  priority: PoiPriority;
  estimated_duration_minutes?: number;
  estimated_cost?: EstimatedCost;
  opening_hours?: string;
  constraints?: string[];
}

export interface PoiList {
  city: string;
  pois: Poi[];
}

export type TimeHHMM = `${number}${number}:${number}${number}`;

export interface TimeBlock {
  poi_id: `poi_${string}`;
  start_time: TimeHHMM;
  end_time: TimeHHMM;
  reason: string;
  locked?: boolean;
}

export interface DayPlan {
  day: number;
  area_cluster: string[];
  time_blocks: TimeBlock[];
  estimated_total_cost: number;
  estimated_total_minutes: number;
}

export interface Itinerary {
  sessionId: string;
  city: string;
  days: DayPlan[];
  version: number;
}

export type SessionPhase =
  | "idle"
  | "intake"
  | "parsing"
  | "parsed"
  | "planning"
  | "planned"
  | "revising"
  | "escalate";

export type ConfirmationDecision = "confirm" | "replace" | "remove";

export interface SessionConfirmation {
  id: string;
  errorCode: string;
  decision: ConfirmationDecision;
  at?: string;
}

export interface SessionState {
  sessionId: string;
  phase: SessionPhase;
  intent: string;
  completed?: string[];
  pendingQuestions?: string[];
  confirmations?: SessionConfirmation[];
  updatedAt: string;
}

export type VerifierErrorCode =
  | "schema_error"
  | "hallucinated_poi"
  | "duplicate_poi"
  | "missing_must_poi"
  | "over_budget"
  | "day_overloaded"
  | "impossible_time_window"
  | "weak_evidence"
  | "unknown_route_time";

export interface VerifierError {
  errorCode: VerifierErrorCode;
  message: string;
  details?: Record<string, unknown>;
  poi_id?: string;
  day?: number;
}

export interface ConstraintCheckResult {
  errors: string[];
  warnings: string[];
}

export interface BuildItineraryResult {
  itinerary: Itinerary;
  warnings: string[];
}

export type ReviseDayOperation =
  | { type: "remove"; poiId: Poi["id"] }
  | { type: "move"; poiId: Poi["id"]; toDay: number }
  | { type: "add"; poiId: Poi["id"]; position?: number };

export interface ReviseDayToolInput {
  day: number;
  operations: ReviseDayOperation[];
}
