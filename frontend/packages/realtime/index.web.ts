import { createRealtimeClient } from "./src/client";
import type { RealtimeClientOptions } from "./src/types";
import { buildWebSocketUrlFromBase, defaultWebSocketBaseFromLocation } from "./src/url";

/**
 * Web 端默认 builder：
 * - 优先使用 web-bridge 注入的 window.buildWebSocketUrl
 * - 否则回退到 location 推导的 ws(s) base
 *
 * 注意：这只是一个“便利方法”，业务仍可直接 import createRealtimeClient 并自行传 url/buildUrl。
 */
export function createWebRealtimeClient(options: Omit<RealtimeClientOptions, "buildUrl"> & { buildUrl?: RealtimeClientOptions["buildUrl"] }) {
  const buildUrl =
    options.buildUrl ||
    ((path: string) => {
      const w: any = typeof window !== "undefined" ? (window as any) : undefined;
      if (w && typeof w.buildWebSocketUrl === "function") {
        return w.buildWebSocketUrl(path);
      }
      const base = defaultWebSocketBaseFromLocation();
      if (!base) {
        throw new Error("Cannot infer WebSocket base from location. Please provide options.url or options.buildUrl.");
      }
      return buildWebSocketUrlFromBase(base, path);
    });

  return createRealtimeClient({
    ...options,
    buildUrl,
  });
}

export { createRealtimeClient } from "./src/client";
export type { RealtimeClientOptions } from "./src/types";


