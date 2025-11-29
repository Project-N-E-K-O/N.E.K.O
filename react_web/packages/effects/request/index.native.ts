import { createRequestClient } from "./createClient";
import { NativeTokenStorage } from "./src/request-client/tokenStorage";
import type { TokenRefreshFn } from "./src/request-client/types";

/**
 * React Native 环境的请求客户端实例
 */
export const request = createRequestClient({
    baseURL: "https://api.yourserver.com",
    storage: new NativeTokenStorage(),
    refreshApi: async (refreshToken: string) => {
        const res = await fetch("https://api.yourserver.com/auth/refresh", {
            method: "POST",
            body: JSON.stringify({ refreshToken }),
            headers: { "Content-Type": "application/json" },
        }).then(r => r.json());

        return {
            accessToken: res.access_token,
            refreshToken: res.refresh_token,
        };
    }
});

// 导出类型和工具
export { createRequestClient } from "./createClient";
export { NativeTokenStorage } from "./src/request-client/tokenStorage";
export type { RequestClientConfig, TokenStorage, TokenRefreshFn, TokenRefreshResult } from "./src/request-client/types";
