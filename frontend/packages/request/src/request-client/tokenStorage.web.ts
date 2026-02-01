import type { TokenStorage } from "./types";
import webStorage from "../storage/webStorage";

const TOKEN_KEYS = {
  ACCESS_TOKEN: "access_token",
  REFRESH_TOKEN: "refresh_token"
} as const;

/**
 * Web 环境 Token 存储实现（纯 web 版本）
 *
 * 重要：此文件不得引入任何 React Native 相关依赖，
 * 否则 Expo Web/Metro 会在打包阶段尝试解析 native 依赖并失败。
 */
export class WebTokenStorage implements TokenStorage {
  private storage = webStorage;

  async getAccessToken(): Promise<string | null> {
    return await this.storage.getItem(TOKEN_KEYS.ACCESS_TOKEN);
  }

  async setAccessToken(token: string): Promise<void> {
    await this.storage.setItem(TOKEN_KEYS.ACCESS_TOKEN, token);
  }

  async getRefreshToken(): Promise<string | null> {
    return await this.storage.getItem(TOKEN_KEYS.REFRESH_TOKEN);
  }

  async setRefreshToken(token: string): Promise<void> {
    await this.storage.setItem(TOKEN_KEYS.REFRESH_TOKEN, token);
  }

  async clearTokens(): Promise<void> {
    await Promise.all([
      this.storage.removeItem(TOKEN_KEYS.ACCESS_TOKEN),
      this.storage.removeItem(TOKEN_KEYS.REFRESH_TOKEN)
    ]);
  }
}


