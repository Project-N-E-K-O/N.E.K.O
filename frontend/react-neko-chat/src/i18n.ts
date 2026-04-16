/** 使用 Xiao8 i18n 系统（window.t / window.safeT），英文作为最终 fallback */
export function i18n(key: string, fallback: string): string {
  const w = window as unknown as Record<string, unknown>;
  if (typeof w.safeT === 'function') return (w.safeT as (k: string, f: string) => string)(key, fallback);
  if (typeof w.t === 'function') {
    try {
      const v = (w.t as (k: string, f: string) => string)(key, fallback);
      if (v && v !== key) return v;
    } catch {}
  }
  return fallback;
}
