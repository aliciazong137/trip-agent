import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";

// 高德 JS API 2.0 服务类插件（路线规划等）必须配套安全密钥，否则请求被静默拦截、路线画不出来
const securityCode = import.meta.env.VITE_AMAP_SECURITY_CODE;
if (securityCode) {
  (window as unknown as { _AMapSecurityConfig?: { securityJsCode: string } })._AMapSecurityConfig = {
    securityJsCode: securityCode,
  };
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode><App /></React.StrictMode>,
);
