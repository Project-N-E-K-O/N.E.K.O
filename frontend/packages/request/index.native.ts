import { createRequestClient } from "./createClient";
import { NativeTokenStorage } from "./src/request-client/tokenStorage";
import type { TokenRefreshFn } from "./src/request-client/types";

/**
 * React Native 环境的请求客户端实例
 * 注意：请在应用侧传入 baseURL / refreshApi，而非依赖 Web 配置。
 */
export function createNativeRequestClient(options: { baseURL: string; refreshApi: TokenRefreshFn }) {
  return createRequestClient({
    baseURL: options.baseURL,
    storage: new NativeTokenStorage(),
    refreshApi: options.refreshApi
  });
}

// 导出类型和工具
export { createRequestClient } from "./createClient";
export { NativeTokenStorage } from "./src/request-client/tokenStorage";
export type { RequestClientConfig, TokenStorage, TokenRefreshFn, TokenRefreshResult } from "./src/request-client/types";

