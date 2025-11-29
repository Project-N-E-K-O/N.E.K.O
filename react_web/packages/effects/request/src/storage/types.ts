/**
 * 通用存储接口
 * 用于 Web (localStorage) 和 React Native (AsyncStorage) 的统一抽象
 */
export interface Storage {
    getItem(key: string): Promise<string | null>;
    setItem(key: string, value: string): Promise<void>;
    removeItem(key: string): Promise<void>;
}

