/* c8 ignore file */
/* istanbul ignore file */
import type { AxiosResponse, AxiosError, InternalAxiosRequestConfig } from "axios";

/**
 * Token 存储接口
 */
export interface TokenStorage {
  getAccessToken(): Promise<string | null>;
  setAccessToken(token: string): Promise<void>;
  getRefreshToken(): Promise<string | null>;
  setRefreshToken(token: string): Promise<void>;
  clearTokens(): Promise<void>;
}

/**
 * Token 刷新结果
 */
export interface TokenRefreshResult {
  accessToken: string;
  refreshToken: string;
}

/**
 * Token 刷新函数
 */
export type TokenRefreshFn = (refreshToken: string) => Promise<TokenRefreshResult>;

/**
 * 请求客户端配置
 */
export interface RequestClientConfig {
  /** 基础 URL */
  baseURL: string;
  /** Token 存储实现 */
  storage: TokenStorage;
  /** Token 刷新函数 */
  refreshApi: TokenRefreshFn;
  /** 请求超时时间（毫秒） */
  timeout?: number;
  /** 请求拦截器 */
  requestInterceptor?: (config: InternalAxiosRequestConfig) => InternalAxiosRequestConfig | Promise<InternalAxiosRequestConfig>;
  /** 响应拦截器 */
  responseInterceptor?: {
    onFulfilled?: (response: AxiosResponse) => any;
    onRejected?: (error: AxiosError) => any;
  };
  /** 是否在响应中自动返回 data */
  returnDataOnly?: boolean;
  /** 自定义错误处理 */
  errorHandler?: (error: AxiosError) => void | Promise<void>;
  /** 是否启用请求/响应日志（优先级：config > 全局变量 > env；env 不可读时默认 false） */
  logEnabled?: boolean;
}

/**
 * 请求队列项
 */
export interface QueuedRequest {
  resolve: (value: any) => void;
  reject: (error: any) => void;
  config: InternalAxiosRequestConfig;
}

