import type { TFunction } from "@project_neko/components";

type SupportedLang = "zh-CN" | "en";

function normalizeLang(raw?: string | null): SupportedLang {
  if (!raw) return "zh-CN";
  if (raw === "en") return "en";
  if (raw === "zh-CN") return "zh-CN";
  const langCode = raw.split("-")[0];
  if (langCode === "en") return "en";
  if (langCode === "zh") return "zh-CN";
  return "zh-CN";
}

function getByPath(obj: unknown, keyPath: string): unknown {
  if (!obj || typeof obj !== "object") return undefined;
  const parts = keyPath.split(".").filter(Boolean);
  let cur: any = obj;
  for (const p of parts) {
    if (!cur || typeof cur !== "object") return undefined;
    cur = cur[p];
  }
  return cur;
}

function interpolate(template: string, params?: Record<string, unknown>) {
  if (!params) return template;
  return template.replace(/\{\{\s*([a-zA-Z0-9_]+)\s*\}\}/g, (_m, key) => {
    const v = (params as any)[key];
    if (v === null || v === undefined) return "";
    return String(v);
  });
}

async function fetchLocaleJson(staticBaseUrl: string, lang: SupportedLang): Promise<unknown> {
  const base = staticBaseUrl.replace(/\/+$/, "");
  const url = `${base}/static/locales/${lang}.json`;
  // 语言包是静态资源；跨域时不应携带 cookies，否则会触发 CORS 的 credentials 约束
  const res = await fetch(url, { credentials: "omit" });
  if (!res.ok) {
    throw new Error(`Failed to load locale: ${lang}, status=${res.status}`);
  }
  return await res.json();
}

function createT(resources: unknown): TFunction {
  return (key: string, params?: Record<string, unknown>) => {
    try {
      const v = getByPath(resources, key);
      if (typeof v === "string") return interpolate(v, params);
      return key;
    } catch (_e) {
      return key;
    }
  };
}

export async function initWebappI18n(staticBaseUrl: string): Promise<{
  t: TFunction;
  language: SupportedLang;
  source: "window.t" | "static-locales" | "fallback";
}> {
  // 如果旧页面/宿主已经提供了 window.t，则直接复用（保持一致）
  try {
    const w: any = typeof window !== "undefined" ? (window as any) : undefined;
    if (typeof w?.t === "function") {
      const lang = normalizeLang(w?.i18n?.language || localStorage.getItem("i18nextLng") || navigator.language);
      return { t: w.t as TFunction, language: lang, source: "window.t" };
    }
  } catch (_e) {
    // ignore
  }

  const lang = normalizeLang(
    (typeof localStorage !== "undefined" ? localStorage.getItem("i18nextLng") : null) ||
      (typeof navigator !== "undefined" ? navigator.language : null)
  );

  try {
    const resources = await fetchLocaleJson(staticBaseUrl, lang);
    return { t: createT(resources), language: lang, source: "static-locales" };
  } catch (e) {
    // 回退到中文
    try {
      const resources = await fetchLocaleJson(staticBaseUrl, "zh-CN");
      return { t: createT(resources), language: "zh-CN", source: "static-locales" };
    } catch (_e2) {
      console.warn("[webapp] i18n 加载失败，使用 fallback t()：", e);
      return { t: (k: string) => k, language: lang, source: "fallback" };
    }
  }
}


