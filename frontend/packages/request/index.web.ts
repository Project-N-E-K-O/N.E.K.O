import { createRequestClient } from "./createClient";
import { WebTokenStorage } from "./src/request-client/tokenStorage.web";

const REFRESH_API_TIMEOUT_MS = 10_000;

interface RefreshTokenSuccessResponse {
  access_token: string;
  refresh_token: string;
  expires_in?: number;
  token_type?: string;
}

interface RefreshTokenErrorResponse {
  message?: string;
  error?: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isRefreshTokenSuccessResponse(value: unknown): value is RefreshTokenSuccessResponse {
  if (!isRecord(value)) return false;
  if (typeof value.access_token !== "string") return false;
  if (typeof value.refresh_token !== "string") return false;
  if (value.expires_in !== undefined && typeof value.expires_in !== "number") return false;
  if (value.token_type !== undefined && typeof value.token_type !== "string") return false;
  return true;
}

export const request = createRequestClient({
  baseURL: "/api",
  storage: new WebTokenStorage(),
  refreshApi: async (refreshToken: string) => {
    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), REFRESH_API_TIMEOUT_MS);

      let response: Response;
      try {
        response = await fetch("/api/auth/refresh", {
          method: "POST",
          body: JSON.stringify({ refreshToken }),
          headers: { "Content-Type": "application/json" },
          signal: controller.signal,
        });
      } catch (error) {
        const isAbort =
          error instanceof Error &&
          (error.name === "AbortError" || String(error.message).includes("aborted"));
        if (isAbort) {
          const timeoutError = new Error(
            `Refresh token request timed out after ${REFRESH_API_TIMEOUT_MS}ms`
          );
          (timeoutError as any).code = "ETIMEDOUT";
          throw timeoutError;
        }
        throw error;
      } finally {
        clearTimeout(timeoutId);
      }

      let data: unknown;
      try {
        data = (await response.json()) as unknown;
      } catch (parseError) {
        throw new Error(`Failed to parse refresh token response: ${String(parseError)}`);
      }

      if (!response.ok) {
        const errData = (isRecord(data) ? (data as RefreshTokenErrorResponse) : undefined);
        const message =
          (errData && (errData.message || errData.error)) ||
          `Refresh token request failed with status ${response.status} ${response.statusText}`;
        const error = new Error(message);
        (error as any).status = response.status;
        (error as any).data = data;
        throw error;
      }

      if (!isRefreshTokenSuccessResponse(data)) {
        throw new Error("Invalid refresh token response: missing or invalid access_token/refresh_token");
      }

      return {
        accessToken: data.access_token,
        refreshToken: data.refresh_token,
      };
    } catch (error) {
      // 让上层统一错误处理逻辑接管
      throw error;
    }
  }
});

// 导出类型和工具
export { createRequestClient } from "./createClient";
export { WebTokenStorage } from "./src/request-client/tokenStorage.web";
export type { RequestClientConfig, TokenStorage, TokenRefreshFn, TokenRefreshResult } from "./src/request-client/types";

