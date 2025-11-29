import { createRequestClient } from "./createClient";
import { WebTokenStorage } from "./src/request-client/tokenStorage";

export const request = createRequestClient({
    baseURL: "/api",
    storage: new WebTokenStorage(),
    refreshApi: async (refreshToken: string) => {
        const res = await fetch("/api/auth/refresh", {
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
export { WebTokenStorage } from "./src/request-client/tokenStorage";
export type { RequestClientConfig, TokenStorage, TokenRefreshFn, TokenRefreshResult } from "./src/request-client/types";
