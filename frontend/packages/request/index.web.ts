import { createRequestClient } from "./createClient";
import { WebTokenStorage } from "./src/request-client/tokenStorage";

export const request = createRequestClient({
  baseURL: "/api",
  storage: new WebTokenStorage(),
  refreshApi: async (refreshToken: string) => {
    try {
      const response = await fetch("/api/auth/refresh", {
        method: "POST",
        body: JSON.stringify({ refreshToken }),
        headers: { "Content-Type": "application/json" }
      });

      let data: any;
      try {
        data = await response.json();
      } catch (parseError) {
        throw new Error(`Failed to parse refresh token response: ${String(parseError)}`);
      }

      if (!response.ok) {
        const message =
          (data && (data.message || data.error)) ||
          `Refresh token request failed with status ${response.status} ${response.statusText}`;
        const error = new Error(message);
        (error as any).status = response.status;
        (error as any).data = data;
        throw error;
      }

      const accessToken = data?.access_token;
      const newRefreshToken = data?.refresh_token;

      if (!accessToken || !newRefreshToken) {
        throw new Error("Refresh token response is missing access_token or refresh_token");
      }

      return {
        accessToken,
        refreshToken: newRefreshToken
      };
    } catch (error) {
      // 让上层统一错误处理逻辑接管
      throw error;
    }
  }
});

// 导出类型和工具
export { createRequestClient } from "./createClient";
export { WebTokenStorage } from "./src/request-client/tokenStorage";
export type { RequestClientConfig, TokenStorage, TokenRefreshFn, TokenRefreshResult } from "./src/request-client/types";

