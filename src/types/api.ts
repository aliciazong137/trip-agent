export type ApiStatus = "ok" | "needs_confirmation" | "failed";

export type Pace = "relaxed" | "normal" | "packed";

export interface TripMeta {
  city: string;
  days: number;
  startDate?: string;
  budget?: { currency: string; amount: number };
  pace: Pace;
  mustVisit: string[];
  avoid: string[];
  travelers?: { adults?: number; kids?: number };
}

export interface PlanRequest {
  guideText: string;
  tripMeta: TripMeta;
}

export interface ReviseDayRequest {
  sessionId: string;
  day: number;
  instruction: string;
  lockedItemIds?: string[];
}

export interface ApiError {
  errorCode: string;
  message: string;
  details?: Record<string, unknown>;
}

export interface ConfirmationRequest {
  id: string;
  errorCode: string;
  message: string;
  poiId?: string;
  day?: number;
  actions: Array<"confirm" | "replace" | "remove">;
}

export interface PlanResponse {
  sessionId: string;
  status: ApiStatus;
  itinerary: Record<string, unknown>;
  warnings: string[];
  confirmationRequests: ConfirmationRequest[];
  error?: ApiError;
}

export interface ReviseDayResponse {
  sessionId: string;
  status: ApiStatus;
  itinerary: Record<string, unknown>;
  changedDays: number[];
  warnings: string[];
  confirmationRequests: ConfirmationRequest[];
  error?: ApiError;
}

export interface SessionResponse {
  sessionId: string;
  tripMeta: TripMeta | Record<string, never>;
  poiList: unknown[];
  itinerary: Record<string, unknown>;
  verifierStatus: Record<string, unknown>;
  updatedAt: string;
}
