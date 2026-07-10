/** 使用 Xiao8 i18n 系统（window.t / window.safeT），英文作为最终 fallback */
export function i18n(key: string, fallback: string, vars?: Record<string, string>): string {
  const w = window as unknown as Record<string, unknown>;
  // When interpolation vars are supplied, hand them to i18next (so the real
  // window.t resolves {{var}} itself) AND re-apply them locally: the crash-proof
  // window.t stub returns the raw template untouched, and i18next would strip an
  // un-passed {{var}} to empty, so local re-application is the belt-and-braces
  // that makes the result correct under both backends.
  const arg: unknown = vars ? { ...vars, defaultValue: fallback } : fallback;
  const apply = (s: string): string => {
    if (!vars) return s;
    let out = s;
    for (const [name, value] of Object.entries(vars)) {
      out = out.split(`{{${name}}}`).join(value).split(`{${name}}`).join(value);
    }
    return out;
  };
  if (typeof w.safeT === 'function') {
    const v = (w.safeT as (k: string, f: unknown) => unknown)(key, arg);
    // Guard against a safeT that echoes the key back on a missing translation
    // (mirrors the window.t branch) so we fall through to the fallback instead.
    if (typeof v === 'string' && v !== key) return apply(v);
  }
  if (typeof w.t === 'function') {
    try {
      const v = (w.t as (k: string, f: unknown) => unknown)(key, arg);
      if (typeof v === 'string' && v && v !== key) return apply(v);
    } catch {}
  }
  return apply(fallback);
}
