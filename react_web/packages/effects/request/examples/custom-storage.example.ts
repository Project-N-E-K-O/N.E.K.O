/**
 * 自定义 Token 存储示例
 */

import { createRequestClient } from '../index';
import type { TokenStorage } from '../src/request-client/types';

// 实现自定义 Token 存储
class CustomTokenStorage implements TokenStorage {
    private tokens: {
        accessToken?: string;
        refreshToken?: string;
    } = {};

    async getAccessToken(): Promise<string | null> {
        // 从你的存储中获取，例如：IndexedDB、SecureStore 等
        return this.tokens.accessToken || null;
    }

    async setAccessToken(token: string): Promise<void> {
        this.tokens.accessToken = token;
        // 保存到你的存储中
    }

    async getRefreshToken(): Promise<string | null> {
        return this.tokens.refreshToken || null;
    }

    async setRefreshToken(token: string): Promise<void> {
        this.tokens.refreshToken = token;
        // 保存到你的存储中
    }

    async clearTokens(): Promise<void> {
        this.tokens = {};
        // 从你的存储中清除
    }
}

// 使用自定义存储
export const request = createRequestClient({
    baseURL: '/api',
    storage: new CustomTokenStorage(),
    refreshApi: async (refreshToken: string) => {
        // 你的刷新逻辑
        const res = await fetch('/api/auth/refresh', {
            method: 'POST',
            body: JSON.stringify({ refreshToken }),
            headers: { 'Content-Type': 'application/json' },
        }).then(r => r.json());

        return {
            accessToken: res.access_token,
            refreshToken: res.refresh_token,
        };
    },
});

