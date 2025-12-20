import React, { createContext, useContext } from "react";

/**
 * Minimal i18n contract for the components package.
 *
 * Why:
 * - React App can use any React i18n solution and inject `t` via Provider.
 * - Legacy HTML/JS (UMD) can provide `window.t`.
 * - Components must work without Provider (no hard dependency).
 */
export type TFunction = (key: string, params?: Record<string, unknown>) => string;

const I18nContext = createContext<TFunction | null>(null);

export interface I18nProviderProps {
  t: TFunction;
  children: React.ReactNode;
}

export function I18nProvider({ t, children }: I18nProviderProps) {
  return <I18nContext.Provider value={t}>{children}</I18nContext.Provider>;
}

function getWindowT(): TFunction | null {
  try {
    const w: any = typeof window !== "undefined" ? (window as any) : undefined;
    const t = w?.t;
    return typeof t === "function" ? (t as TFunction) : null;
  } catch (_e) {
    return null;
  }
}

/**
 * Get translation function with fallback order:
 * Provider injected `t` -> window.t -> identity (returns key)
 */
export function useT(): TFunction {
  const ctxT = useContext(I18nContext);
  if (ctxT) return ctxT;
  const wt = getWindowT();
  if (wt) return wt;
  return (key: string) => key;
}

/**
 * Helper for "fallback default text" when the translation key is missing.
 *
 * Convention: if `t(key)` returns exactly `key`, treat it as missing.
 */
export function tOrDefault(
  t: TFunction,
  key: string,
  fallback: string,
  params?: Record<string, unknown>
): string {
  try {
    const v = t(key, params);
    if (!v || v === key) return fallback;
    return v;
  } catch (_e) {
    return fallback;
  }
}


