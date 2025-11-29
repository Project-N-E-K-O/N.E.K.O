import webStorage from './webStorage';
import type { Storage } from './types';

// 检测是否为 React Native 环境
const isNative = (() => {
    try {
        // 尝试访问 react-native 的 Platform，如果不存在会抛出错误
        const { Platform } = require('react-native');
        return typeof Platform !== 'undefined';
    } catch {
        return false;
    }
})();

// 动态导入 nativeStorage，避免在 Web 环境中加载 React Native 依赖
let nativeStorageModule: { default: Storage } | null = null;
let nativeStoragePromise: Promise<{ default: Storage }> | null = null;

async function getNativeStorage(): Promise<Storage> {
    if (nativeStorageModule) {
        return nativeStorageModule.default;
    }
    if (!nativeStoragePromise) {
        nativeStoragePromise = import('./nativeStorage');
    }
    const module = await nativeStoragePromise;
    nativeStorageModule = module;
    return module.default;
}

// 创建存储实例
const storage: Storage = {
    async getItem(key: string): Promise<string | null> {
        if (isNative) {
            const storageImpl = await getNativeStorage();
            return storageImpl.getItem(key);
        }
        return webStorage.getItem(key);
    },
    async setItem(key: string, value: string): Promise<void> {
        if (isNative) {
            const storageImpl = await getNativeStorage();
            return storageImpl.setItem(key, value);
        }
        return webStorage.setItem(key, value);
    },
    async removeItem(key: string): Promise<void> {
        if (isNative) {
            const storageImpl = await getNativeStorage();
            return storageImpl.removeItem(key);
        }
        return webStorage.removeItem(key);
    },
};

export default storage;
export type { Storage };
