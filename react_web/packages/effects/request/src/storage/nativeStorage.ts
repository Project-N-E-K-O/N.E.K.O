/// <reference path="./async-storage.d.ts" />
import type { Storage } from './types';

// 使用动态导入，避免在 Web 环境中立即加载 React Native 依赖
let AsyncStorageInstance: any = null;
let asyncStoragePromise: Promise<any> | null = null;

async function getAsyncStorage() {
    if (AsyncStorageInstance) {
        return AsyncStorageInstance;
    }
    if (!asyncStoragePromise) {
        asyncStoragePromise = import('@react-native-async-storage/async-storage').then(
            (module) => module.default
        ).catch(() => {
            // 在 Web 环境中，如果导入失败，返回 null
            // 这不应该发生，因为 nativeStorage 只应该在 React Native 环境中使用
            throw new Error('@react-native-async-storage/async-storage is not available. This module should only be used in React Native environment.');
        });
    }
    AsyncStorageInstance = await asyncStoragePromise;
    return AsyncStorageInstance;
}

const nativeStorage: Storage = {
    async getItem(key: string): Promise<string | null> {
        const AsyncStorage = await getAsyncStorage();
        return AsyncStorage.getItem(key);
    },
    async setItem(key: string, value: string): Promise<void> {
        const AsyncStorage = await getAsyncStorage();
        return AsyncStorage.setItem(key, value);
    },
    async removeItem(key: string): Promise<void> {
        const AsyncStorage = await getAsyncStorage();
        return AsyncStorage.removeItem(key);
    },
};

export default nativeStorage;
