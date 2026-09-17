/// <reference types="vite/client" />

declare module "@amap/amap-jsapi-loader" {
  type LoaderOptions = { key: string; version: string; plugins?: string[] };
  const AMapLoader: { load: (options: LoaderOptions) => Promise<unknown> };
  export default AMapLoader;
}
