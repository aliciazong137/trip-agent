export type Location = { longitude: number; latitude: number };

export type MapPoint = {
  order: number;
  poi_id: string;
  name: string;
  location: Location | null;
  start_time?: string | null;
  end_time?: string | null;
};

export type DayRouteMap = {
  day: number;
  city: string;
  transportation?: string | null;
  points: MapPoint[];
  route_ready: boolean;
  unmapped_points: string[];
};

export type MapData = { city: string; days: DayRouteMap[] };

export type Transportation = "公共交通" | "驾车" | "步行" | "骑行";

export type HotelCandidate = {
  name?: string;
  rating?: string;
  type?: string;
  area?: string;
};

export type NlTripPlanResponse = {
  status: "ok" | "needs_clarification" | "failed";
  session_id?: string | null;
  clarification_question?: string | null;
  missing_fields?: string[];
  invalid_fields?: string[];
  assumptions?: string[];
  warnings?: string[];
  error_code?: string | null;
  error_message?: string | null;
  trip_meta?: {
    city: string;
    days: number;
    preferences?: string | null;
    transportation?: string | null;
  } | null;
  // 完整契约同步（对应后端 NlTripPlanResponse 的全量字段）
  trip_plan?: TripPlanData | null;
  sources?: unknown[];
  user_context?: unknown | null;
  hotel_candidates?: HotelCandidate[];
};

/** 行程确认卡用的 TripPlan 子集（对应后端 TripPlan/DayPlan/WeatherInfo） */
export type TripPlanData = {
  city?: string | null;
  days?: TripPlanDay[];
  weather_info?: TripWeatherInfo[];
  overall_suggestions?: string | null;
};

export type TripPlanDay = {
  day: number;
  date?: string | null;
  description?: string | null;
  estimated_total_cost?: number;
  hotel?: {
    name?: string;
    area?: string;
    price_range?: string;
    rating?: string;
    estimated_cost?: number;
    type?: string;
    amap_url?: string;
  } | null;
  time_blocks?: { poi_id: string; start_time?: string | null; end_time?: string | null }[];
};

export type TripWeatherInfo = {
  date?: string | null;
  day_weather?: string | null;
  night_weather?: string | null;
  day_temp?: number | string | null;
  night_temp?: number | string | null;
};

export type RouteOption = {
  id: string;
  title: string;
  route_text: string;
  suitable_for?: string;
  poi_hints?: string[];
  food_hints?: string[];
  tips?: string[];
};

export type GuideRoutesResponse = {
  status: "ok" | "failed";
  session_id?: string | null;
  guide_markdown?: string;
  route_options?: RouteOption[];
  sources?: unknown[];
  action?: "route_options" | "conversation" | "current_trip_question" | "current_trip_modify" | "weather_query" | "clarification";
  assistant_message?: string | null;
  trip_meta?: Record<string, unknown> | null;
  itinerary_updated?: boolean;
  error_code?: string | null;
  error_message?: string | null;
};

export type TrendingNote = { id: string; title: string; summary: string; tags: string[]; cover_url: string; note_url: string };
export type TrendingNotesResponse = { updated_at: string | null; notes: TrendingNote[] };
export type TripContextResponse = { session_id: string; message: string };

export type ReviseNlResponse = {
  status: "ok" | "needs_clarification" | "needs_confirmation" | "failed";
  session_id: string;
  message: string;
  itinerary_version: number;
  clarification_question?: string | null;
  warnings?: string[];
  error_code?: string | null;
  error_message?: string | null;
};

// 单条指引步骤（公共交通分段 or 驾车/步行/骑行转向）
export type RouteStep = {
  kind: "walk" | "transit" | "drive" | "ride";
  instruction: string;
  road?: string;
  orientation?: string;
  action?: string;
  lineName?: string;
  boardingStop?: string;
  alightingStop?: string;
  stopCount?: number;
  distanceMeters?: number;
  durationSeconds?: number;
};

// 单个路段（相邻两个景点之间）
export type RouteLeg = {
  index: number;
  from: string;
  to: string;
  mode: Transportation;
  distanceMeters?: number;
  durationSeconds?: number;
  tollsYuan?: number;
  steps: RouteStep[];
  path: [number, number][];
};

export type RouteLegDetail = {
  from: string;
  to: string;
  mode: Transportation;
  lineName?: string;
  boardingStop?: string;
  alightingStop?: string;
  stopCount?: number;
  walkingDistanceMeters?: number;
  transfers?: number;
};

export type RouteSummary = {
  status: "idle" | "loading" | "complete" | "partial" | "unavailable" | "error";
  distanceKm?: number;
  durationMinutes?: number;
  tollsYuan?: number;
  legsCompleted: number;
  legsTotal: number;
  details?: RouteLegDetail[];
  legs?: RouteLeg[];
  navFrom?: [number, number];
  navTo?: [number, number];
  fromName?: string;
  toName?: string;
  detail?: string;
};
