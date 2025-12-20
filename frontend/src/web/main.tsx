import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { I18nProvider } from "@project_neko/components";
import type { TFunction } from "@project_neko/components";
import { initWebappI18n } from "./i18n";

const trimTrailingSlash = (url?: string) => (url ? url.replace(/\/+$/, "") : "");
const API_BASE = trimTrailingSlash(
  (import.meta as any).env?.VITE_API_BASE_URL ||
    (typeof window !== "undefined" ? (window as any).API_BASE_URL : "") ||
    "http://localhost:48911"
);
const STATIC_BASE = trimTrailingSlash(
  (import.meta as any).env?.VITE_STATIC_SERVER_URL ||
    (typeof window !== "undefined" ? (window as any).STATIC_SERVER_URL : "") ||
    API_BASE
);

const rootEl = document.getElementById("root");

if (!rootEl) {
  throw new Error("Root element #root 未找到，检查模板挂载点。");
}

ReactDOM.createRoot(rootEl).render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>
);

function Root() {
  const [t, setT] = React.useState<TFunction>(() => (key: string) => key);
  const [language, setLanguage] = React.useState<"zh-CN" | "en">("zh-CN");

  React.useEffect(() => {
    let cancelled = false;
    initWebappI18n(STATIC_BASE).then(({ t, language, source }) => {
      if (cancelled) return;
      console.log("[webapp] i18n 已就绪:", { language, source, staticBaseUrl: STATIC_BASE });
      setLanguage(language);
      setT(() => t);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleChangeLanguage = React.useCallback(async (lng: "zh-CN" | "en") => {
    try {
      const w: any = typeof window !== "undefined" ? (window as any) : undefined;
      if (typeof w?.changeLanguage === "function") {
        await w.changeLanguage(lng);
      } else if (typeof localStorage !== "undefined") {
        localStorage.setItem("i18nextLng", lng);
      }
    } catch (e) {
      console.warn("[webapp] 切换语言失败，将继续尝试加载语言包：", e);
      try {
        localStorage.setItem("i18nextLng", lng);
      } catch (_e2) {
        // ignore
      }
    }

    const { t, language, source } = await initWebappI18n(STATIC_BASE);
    console.log("[webapp] i18n 已切换:", { language, source });
    setLanguage(language);
    setT(() => t);

    // 在非 i18next 宿主场景下，也广播一次，方便其它逻辑监听
    try {
      window.dispatchEvent(new CustomEvent("localechange"));
    } catch (_e) {
      // ignore
    }
  }, []);

  return (
    <I18nProvider t={t}>
      <App language={language} onChangeLanguage={handleChangeLanguage} />
    </I18nProvider>
  );
}

