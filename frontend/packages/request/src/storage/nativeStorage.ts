/// <reference path="./async-storage.d.ts" />
import type { Storage } from "./types";

// 使用动态导入，避免在 Web 环境中立即加载 React Native 依赖
let AsyncStorageInstance: any = null;
let asyncStoragePromise: Promise<any> | null = null;

/**
 * Lazily load and cache the React Native AsyncStorage module.
 *
 * @returns 已加载的 AsyncStorage 实例
 * @throws 导入失败时（例如在非 RN 环境）抛出明确错误
 */
async function getAsyncStorage() {
  if (AsyncStorageInstance) {
    return AsyncStorageInstance;
  }
  if (!asyncStoragePromise) {
    asyncStoragePromise = import("@react-native-async-storage/async-storage")
      .then((module) => module.default)
      .catch(() => {
        // 在 Web 环境中，如果导入失败，返回错误提示
        throw new Error(
          "@react-native-async-storage/async-storage is not available. This module should only be used in React Native environment."
        );
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
  }
};

export default nativeStorage;

// 仅供测试覆盖使用，重置内部缓存
export function __resetNativeStorageInternal() {
  AsyncStorageInstance = null;
  asyncStoragePromise = null;
}

