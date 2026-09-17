import AMapLoader from "@amap/amap-jsapi-loader";
import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import type { DayRouteMap, MapPoint, RouteLeg, RouteStep, RouteSummary, Transportation } from "../types";

type RouteMapProps = {
  day: DayRouteMap;
  transportation: Transportation;
  onSelectPoint: (point: MapPoint) => void;
  onSummaryChange: (summary: RouteSummary) => void;
};

export type RouteMapHandle = { startNavigation: () => void; clearNavigation: () => void };

type AMapMap = { add: (items: unknown[]) => void; remove: (items: unknown[]) => void; setFitView: (items?: unknown[]) => void; destroy: () => void };
type AMapMarker = { on: (event: string, handler: (e?: unknown) => void) => void };
type AMapInstance = { Map: new (element: HTMLDivElement, options: Record<string, unknown>) => AMapMap; Marker: new (options: Record<string, unknown>) => AMapMarker; [plugin: string]: unknown };
type RoutePlugin = { search: (...args: unknown[]) => void; clear?: () => void };
type RoutePluginConstructor = new (options: Record<string, unknown>) => RoutePlugin;

const pluginByMode: Record<Transportation, { loaderName: string; constructorName: string }> = {
  公共交通: { loaderName: "AMap.Transfer", constructorName: "Transfer" },
  驾车: { loaderName: "AMap.Driving", constructorName: "Driving" },
  步行: { loaderName: "AMap.Walking", constructorName: "Walking" },
  骑行: { loaderName: "AMap.Riding", constructorName: "Riding" },
};

function pointOf(point: MapPoint): [number, number] | null { return point.location ? [point.location.longitude, point.location.latitude] : null; }
function numberValue(value: unknown): number | undefined { const number = Number(value); return Number.isFinite(number) && number > 0 ? number : undefined; }
function textValue(value: unknown): string | undefined { return typeof value === "string" && value.trim() ? value.trim() : undefined; }

// 把高德坐标数组统一成 [lng, lat][]
function toPath(points: unknown): [number, number][] {
  if (!Array.isArray(points)) return [];
  return points.flatMap((item) => {
    if (Array.isArray(item) && item.length >= 2) return [[Number(item[0]), Number(item[1])]] as [number, number][];
    const p = (item || {}) as { lng?: unknown; lat?: unknown };
    if (typeof p.lng === "number" && typeof p.lat === "number") return [[p.lng, p.lat]] as [number, number][];
    return [];
  });
}

// 驾车/步行/骑行：从 routes[0] 解析距离、耗时、path、分步指引
function parseSimpleLeg(result: unknown, mode: Transportation, index: number, from: string, to: string): RouteLeg | null {
  const source = (result || {}) as Record<string, unknown>;
  const route = ((source.routes as unknown[])?.[0] || (source.plans as unknown[])?.[0] || source) as Record<string, unknown>;
  const distanceMeters = numberValue(route.distance);
  const durationSeconds = numberValue(route.time || route.duration);
  const tollsYuan = numberValue(route.tolls);
  // 骑行结果在 rides[]，驾车/步行在 steps[]
  const rawSteps = Array.isArray(route.rides) ? route.rides : (Array.isArray(route.steps) ? route.steps : []);
  const stepKind: RouteStep["kind"] = mode === "驾车" ? "drive" : mode === "骑行" ? "ride" : "walk";
  const path: [number, number][] = [];
  const steps: RouteStep[] = [];
  rawSteps.forEach((raw) => {
    const step = (raw || {}) as Record<string, unknown>;
    path.push(...toPath(step.path));
    const instruction = textValue(step.instruction);
    if (instruction) steps.push({
      kind: stepKind,
      instruction,
      road: textValue(step.road),
      orientation: textValue(step.orientation),
      action: textValue(step.action) || textValue(step.assist_action),
      distanceMeters: numberValue(step.distance),
      durationSeconds: numberValue(step.time),
    });
  });
  if (!path.length) path.push(...toPath(route.path));
  return { index, from, to, mode, distanceMeters, durationSeconds, tollsYuan, steps, path };
}

// 公共交通：从 plans[0].segments 解析步行接驳 + 乘车段
function parseTransitLeg(result: unknown, index: number, from: string, to: string): RouteLeg & { costYuan?: number; walkingMeters?: number } | null {
  const source = (result || {}) as Record<string, unknown>;
  const plan = ((source.plans as unknown[])?.[0] || source) as Record<string, unknown>;
  const distanceMeters = numberValue(plan.distance);
  const durationSeconds = numberValue(plan.time || plan.duration);
  const costYuan = numberValue(plan.cost);
  const walkingMeters = numberValue(plan.walking_distance);
  const segments = Array.isArray(plan.segments) ? plan.segments : [];
  const steps: RouteStep[] = [];
  const path: [number, number][] = [];
  segments.forEach((raw) => {
    const seg = (raw || {}) as Record<string, unknown>;
    const mode = textValue(seg.transit_mode);
    const transit = (seg.transit || {}) as Record<string, unknown>;
    if (mode === "WALK") {
      const distance = numberValue(seg.distance);
      path.push(...toPath(transit.path));
      if (distance) steps.push({ kind: "walk", instruction: `步行约 ${Math.round(distance)} 米`, distanceMeters: distance });
      return;
    }
    const lines = Array.isArray(transit.lines) ? transit.lines : [];
    const firstLine = (lines[0] || {}) as Record<string, unknown>;
    const onStation = (transit.on_station || {}) as Record<string, unknown>;
    const offStation = (transit.off_station || {}) as Record<string, unknown>;
    const lineName = textValue(firstLine.name);
    path.push(...toPath(transit.path));
    if (lineName) {
      const viaNum = numberValue(transit.via_num);
      steps.push({ kind: "transit", instruction: lineName, lineName, boardingStop: textValue(onStation.name), alightingStop: textValue(offStation.name), stopCount: viaNum ? viaNum + 1 : undefined });
    }
  });
  return { index, from, to, mode: "公共交通", distanceMeters, durationSeconds, steps, path, costYuan, walkingMeters };
}

export const RouteMap = forwardRef<RouteMapHandle, RouteMapProps>(function RouteMap({ day, transportation, onSelectPoint, onSummaryChange }, ref) {
  const mapElement = useRef<HTMLDivElement>(null);
  const panelElement = useRef<HTMLDivElement>(null);
  const mapRef = useRef<AMapMap | null>(null);
  const amapRef = useRef<AMapInstance | null>(null);
  const overlaysRef = useRef<unknown[]>([]);
  const routeRefs = useRef<RoutePlugin[]>([]);
  const staticLineRef = useRef<unknown | null>(null);
  const navPluginRef = useRef<RoutePlugin | null>(null);
  const [status, setStatus] = useState("准备规划路线");
  const [navActive, setNavActive] = useState(false);

  // 保存当前的起终点/途经点坐标，供导航使用
  const navPointsRef = useRef<[number, number][]>([]);
  const modeRef = useRef<Transportation>(transportation);
  modeRef.current = transportation;

  // 地图内官方导航：用对应插件带 map + panel + showTraffic + autoFitView（严格按官方 references/api/routing.md）
  useImperativeHandle(ref, () => {
    const clearNavigation = () => {
      const map = mapRef.current;
      if (navPluginRef.current) { navPluginRef.current.clear?.(); navPluginRef.current = null; }
      if (panelElement.current) panelElement.current.innerHTML = "";
      // 恢复静态预览蓝线与 marker
      if (map) {
        if (overlaysRef.current.length) map.add(overlaysRef.current);
        if (staticLineRef.current) map.add([staticLineRef.current]);
        map.setFitView(overlaysRef.current);
      }
      setNavActive(false);
    };
    const startNavigation = () => {
      const map = mapRef.current;
      const AMap = amapRef.current;
      const points = navPointsRef.current;
      if (!map || !AMap || points.length < 2) return;
      const mode = modeRef.current;
      if (mode === "公共交通") return; // 仅驾车/步行/骑行支持地图内导航
      const plugin = pluginByMode[mode];
      const Ctor = AMap[plugin.constructorName] as RoutePluginConstructor | undefined;
      if (!Ctor) return;
      // 清理旧导航与静态预览元素，让官方插件独占地图（避免叠加）
      if (navPluginRef.current) { navPluginRef.current.clear?.(); navPluginRef.current = null; }
      if (staticLineRef.current) { map.remove([staticLineRef.current]); }
      if (overlaysRef.current.length) map.remove(overlaysRef.current);
      if (panelElement.current) panelElement.current.innerHTML = "";

      const LngLatCtor = AMap.LngLat as new (lng: number, lat: number) => unknown;
      const toLngLat = (p: [number, number]) => new LngLatCtor(p[0], p[1]);
      // 官方参数：map 自动绘制导航路线，panel 渲染分步导航面板，autoFitView 自动视野
      const options: Record<string, unknown> = { map, panel: panelElement.current || undefined, autoFitView: true, hideMarkers: false };
      if (mode === "驾车") { options.showTraffic = true; options.policy = 0; }
      const navPlugin = new Ctor(options);
      navPluginRef.current = navPlugin;
      const origin = toLngLat(points[0]);
      const destination = toLngLat(points[points.length - 1]);
      // 中间景点作为途经点（driving 支持 waypoints，最多 16 个）
      const waypoints = points.slice(1, -1).map(toLngLat);
      setNavActive(true);
      if (mode === "驾车" && waypoints.length) {
        navPlugin.search(origin, destination, { waypoints }, () => {});
      } else {
        navPlugin.search(origin, destination, () => {});
      }
    };
    return { startNavigation, clearNavigation };
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function render() {
      if (!mapElement.current) return;
      const key = import.meta.env.VITE_AMAP_JS_KEY;
      const locatedPoints = day.points.map(pointOf).filter((point): point is [number, number] => Boolean(point));
      const totalLegs = Math.max(0, locatedPoints.length - 1);
      navPointsRef.current = locatedPoints;
      if (!key) { setStatus("地图服务暂不可用"); onSummaryChange({ status: "error", legsCompleted: 0, legsTotal: totalLegs, detail: "地图服务暂未配置" }); return; }
      if (locatedPoints.length < 2) { setStatus("暂无足够地点规划路线"); onSummaryChange({ status: "unavailable", legsCompleted: 0, legsTotal: totalLegs }); return; }
      setStatus(`${transportation}路线规划中`);
      onSummaryChange({ status: "loading", legsCompleted: 0, legsTotal: totalLegs });
      try {
        const plugin = pluginByMode[transportation];
        const AMap = await AMapLoader.load({ key, version: "2.0", plugins: ["AMap.Scale", "AMap.ToolBar", plugin.loaderName] }) as unknown as AMapInstance;
        if (cancelled || !mapElement.current) return;
        amapRef.current = AMap;
        const LngLatCtor = AMap.LngLat as new (lng: number, lat: number) => unknown;
        const toLngLat = (p: [number, number]) => new LngLatCtor(p[0], p[1]);
        const map = mapRef.current || new AMap.Map(mapElement.current, { zoom: 12, center: locatedPoints[0], viewMode: "2D" });
        mapRef.current = map;
        // 切换时清理上一轮导航与覆盖物
        if (navPluginRef.current) { navPluginRef.current.clear?.(); navPluginRef.current = null; }
        if (panelElement.current) panelElement.current.innerHTML = "";
        setNavActive(false);
        if (overlaysRef.current.length) map.remove(overlaysRef.current);
        if (staticLineRef.current) { map.remove([staticLineRef.current]); staticLineRef.current = null; }
        routeRefs.current.forEach((route) => route.clear?.());
        routeRefs.current = [];
        overlaysRef.current = day.points.flatMap((point) => { const position = pointOf(point); if (!position) return []; const marker = new AMap.Marker({ position, title: `${point.order}. ${point.name}`, label: { content: String(point.order), direction: "center" } }); marker.on("click", () => onSelectPoint(point)); return [marker]; });
        if (overlaysRef.current.length) map.add(overlaysRef.current);
        map.setFitView(overlaysRef.current);
        const RouteConstructor = AMap[plugin.constructorName] as RoutePluginConstructor | undefined;
        if (!RouteConstructor) throw new Error("路线服务暂不可用");
        let completed = 0;
        let failed = 0;
        let distance = 0;
        let duration = 0;
        let tolls = 0;
        const legs: RouteLeg[] = [];
        const names = day.points.filter((point) => point.location).map((point) => point.name);
        const finish = (routeStatus: string, result: unknown, index: number) => {
          if (cancelled) return;
          const from = names[index] || "起点";
          const to = names[index + 1] || "终点";
          if (routeStatus === "complete") {
            completed += 1;
            const leg = transportation === "公共交通" ? parseTransitLeg(result, index, from, to) : parseSimpleLeg(result, transportation, index, from, to);
            if (leg) {
              legs.push(leg);
              distance += leg.distanceMeters || 0;
              duration += leg.durationSeconds || 0;
              tolls += leg.tollsYuan || 0;
            }
          } else failed += 1;
          if (completed + failed < totalLegs) return;
          if (completed === 0) { setStatus("暂时没有找到可用路线"); onSummaryChange({ status: "error", legsCompleted: 0, legsTotal: totalLegs, detail: "高德路线服务没有返回可用方案" }); return; }
          legs.sort((a, b) => a.index - b.index);
          const partial = failed > 0;
          setStatus(partial ? "部分路段暂时无法规划，已保留可用路线" : `${transportation}路线已规划`);
          // 静态预览蓝线（拼接所有段 path），导航开始时会被移除换成官方导航路线
          if (staticLineRef.current) { map.remove([staticLineRef.current]); staticLineRef.current = null; }
          const fullPath = legs.flatMap((leg) => leg.path).map((p) => toLngLat(p));
          const PolylineCtor = AMap.Polyline as (new (options: Record<string, unknown>) => unknown) | undefined;
          if (PolylineCtor && fullPath.length > 1) {
            const line = new PolylineCtor({ map, path: fullPath, strokeColor: "#28F", strokeWeight: 6, strokeOpacity: 0.9, showDir: true, lineJoin: "round" });
            staticLineRef.current = line;
            map.setFitView([line]);
          }
          onSummaryChange({
            status: partial ? "partial" : "complete",
            distanceKm: distance ? distance / 1000 : undefined,
            durationMinutes: duration ? Math.round(duration / 60) : undefined,
            tollsYuan: tolls || undefined,
            legsCompleted: completed,
            legsTotal: totalLegs,
            legs,
            navFrom: locatedPoints[0],
            navTo: locatedPoints[locatedPoints.length - 1],
            fromName: names[0],
            toName: names[names.length - 1],
          });
        };
        for (let index = 0; index < locatedPoints.length - 1; index += 1) {
          // 规划插件不传 map：只用它返回的数据画干净的预览蓝线；地图内导航时才用带 map 的插件
          const options: Record<string, unknown> = {};
          if (transportation === "公共交通") { options.city = day.city; const policyEnum = AMap.TransferPolicy as Record<string, unknown> | undefined; options.policy = policyEnum?.LEAST_TIME ?? 0; }
          if (transportation === "驾车") options.policy = 0;
          const route = new RouteConstructor(options);
          routeRefs.current.push(route);
          const legIndex = index;
          route.search(toLngLat(locatedPoints[index]), toLngLat(locatedPoints[index + 1]), (routeStatus: string, result: unknown) => finish(routeStatus, result, legIndex));
        }
      } catch (error) {
        if (cancelled) return;
        setStatus("路线规划失败，请稍后重试");
        onSummaryChange({ status: "error", legsCompleted: 0, legsTotal: totalLegs, detail: error instanceof Error ? error.message : "路线服务异常" });
      }
    }
    void render();
    return () => { cancelled = true; routeRefs.current.forEach((route) => route.clear?.()); routeRefs.current = []; };
  }, [day, transportation, onSelectPoint, onSummaryChange]);

  useEffect(() => () => { routeRefs.current.forEach((route) => route.clear?.()); mapRef.current?.destroy(); }, []);
  return <div className="map-shell">
    <div ref={mapElement} className="map-canvas" aria-label={`${day.city}第${day.day}天路线地图`} />
    <div className="map-status"><span className="status-dot" />{status}</div>
    {!import.meta.env.VITE_AMAP_JS_KEY && <div className="map-fallback">地图服务暂未配置，请稍后再试。</div>}
    <div ref={panelElement} className={`amap-nav-panel ${navActive ? "open" : ""}`} />
  </div>;
});
