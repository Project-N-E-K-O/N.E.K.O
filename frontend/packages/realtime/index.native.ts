import { createRealtimeClient } from "./src/client";
import type { RealtimeClientOptions } from "./src/types";

/**
 * React Native 入口：
 * - RN 环境一般无法使用 location 推导 ws 地址，因此建议显式传入 url 或 buildUrl/base。
 * - 默认直接复用 core createRealtimeClient（无 DOM 依赖）。
 */
export function createNativeRealtimeClient(options: RealtimeClientOptions) {
  return createRealtimeClient(options);
}

export { createRealtimeClient } from "./src/client";
export type { RealtimeClientOptions } from "./src/types";


