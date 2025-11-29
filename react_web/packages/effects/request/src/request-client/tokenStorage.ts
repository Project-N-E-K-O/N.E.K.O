import type { TokenStorage } from './types';
import webStorage from '../storage/webStorage';
import type { Storage } from '../storage/types';

const TOKEN_KEYS = {
    ACCESS_TOKEN: 'access_token',
    REFRESH_TOKEN: 'refresh_token',
} as const;

/**
 * Web 环境 Token 存储实现
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
            this.storage.removeItem(TOKEN_KEYS.REFRESH_TOKEN),
        ]);
    }
}

/**
 * React Native 环境 Token 存储实现
 */
export class NativeTokenStorage implements TokenStorage {
    private storagePromise: Promise<Storage> | null = null;
    
    private async getStorage(): Promise<Storage> {
        if (!this.storagePromise) {
            this.storagePromise = import('../storage/nativeStorage').then(m => m.default);
        }
        return this.storagePromise;
    }

    async getAccessToken(): Promise<string | null> {
        const storage = await this.getStorage();
        return await storage.getItem(TOKEN_KEYS.ACCESS_TOKEN);
    }

    async setAccessToken(token: string): Promise<void> {
        const storage = await this.getStorage();
        await storage.setItem(TOKEN_KEYS.ACCESS_TOKEN, token);
    }

    async getRefreshToken(): Promise<string | null> {
        const storage = await this.getStorage();
        return await storage.getItem(TOKEN_KEYS.REFRESH_TOKEN);
    }

    async setRefreshToken(token: string): Promise<void> {
        const storage = await this.getStorage();
        await storage.setItem(TOKEN_KEYS.REFRESH_TOKEN, token);
    }

    async clearTokens(): Promise<void> {
        const storage = await this.getStorage();
        await Promise.all([
            storage.removeItem(TOKEN_KEYS.ACCESS_TOKEN),
            storage.removeItem(TOKEN_KEYS.REFRESH_TOKEN),
        ]);
    }
}
