const trimTrailingSlash = (url: string): string => url.replace(/\/+$/, "");

const ensureLeadingSlash = (path: string): string => (path.startsWith("/") ? path : `/${path}`);

const toWebSocketUrl = (url: string): string =>
  url.replace(/^http:\/\//i, "ws://").replace(/^https:\/\//i, "wss://");

/**
 * 在仅知道 base + path 的场景下，把它拼成 ws/wss URL。
 *
 * 说明：
 * - base 可以是 http/https/ws/wss（会统一转为 ws/wss）
 * - path 会自动补齐前导 /
 */
export function buildWebSocketUrlFromBase(base: string, path: string): string {
  const cleanBase = trimTrailingSlash(base);
  const cleanPath = ensureLeadingSlash(path);
  return toWebSocketUrl(`${cleanBase}${cleanPath}`);
}

/**
 * 在浏览器环境中，从 location 推导 ws/wss base。
 * RN/非浏览器环境会返回空字符串（调用方需自行提供 url/base）。
 */
export function defaultWebSocketBaseFromLocation(): string {
  try {
    const loc: any = (globalThis as any).location;
    if (!loc || typeof loc.protocol !== "string" || typeof loc.host !== "string") return "";
    const protocol = String(loc.protocol).toLowerCase() === "https:" ? "wss:" : "ws:";
    return `${protocol}//${loc.host}`;
  } catch (_e) {
    return "";
  }
}


