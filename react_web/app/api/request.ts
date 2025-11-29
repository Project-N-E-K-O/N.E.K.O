/**
 * 请求客户端配置
 * 使用 @xiao8/request 包提供的统一请求库
 */

import { createRequestClient, WebTokenStorage } from '@xiao8/request';

/**
 * 获取 API 基础 URL
 */
function getApiBaseUrl(): string {
  const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL as string) || "http://localhost:48911";
  return API_BASE_URL;
}

/**
 * 创建并导出请求客户端实例
 */
export const request = createRequestClient({
  baseURL: getApiBaseUrl(),
  storage: new WebTokenStorage(),
  refreshApi: async (refreshToken: string) => {
    const baseURL = getApiBaseUrl();
    const refreshUrl = `${baseURL.replace(/\/$/, "")}/api/auth/refresh`;
    const res = await fetch(refreshUrl, {
      method: 'POST',
      body: JSON.stringify({ refreshToken }),
      headers: { 'Content-Type': 'application/json' },
    }).then(r => r.json());

    return {
      accessToken: res.access_token,
      refreshToken: res.refresh_token,
    };
  },
  // 可选配置
  timeout: 15000,
  returnDataOnly: true,
  // 自定义错误处理
  errorHandler: async (error) => {
    // 可以在这里进行错误上报或统一处理
    if (error.response?.status === 403) {
      // 处理 403 错误，例如跳转到登录页
      console.warn('Access forbidden, redirecting to login...');
    }
  },
});

// 导出类型
export type { RequestClientConfig, TokenStorage, TokenRefreshFn } from '@xiao8/request';

/**
 * 获取静态资源服务器 URL
 */
function getStaticServerUrl(): string {
  const STATIC_SERVER_URL = (import.meta.env.VITE_STATIC_SERVER_URL as string) || getApiBaseUrl();
  return STATIC_SERVER_URL;
}

/**
 * 构建完整的 API URL
 * @param path - API 路径（如 /api/users）
 * @returns 完整的 URL
 */
export function buildApiUrl(path: string): string {
  // 如果已经是完整 URL，直接返回
  if (path.startsWith('http://') || path.startsWith('https://')) {
    return path;
  }
  
  const baseURL = getApiBaseUrl();
  const base = baseURL.replace(/\/$/, '');
  const cleanPath = path.startsWith('/') ? path : `/${path}`;
  return `${base}${cleanPath}`;
}

/**
 * 构建完整的静态资源 URL
 * @param path - 静态资源路径（如 /static/icon.png）
 * @returns 完整的 URL
 */
export function buildStaticUrl(path: string): string {
  // 如果已经是完整 URL，直接返回
  if (path.startsWith('http://') || path.startsWith('https://')) {
    return path;
  }
  
  const baseURL = getStaticServerUrl();
  const base = baseURL.replace(/\/$/, '');
  const cleanPath = path.startsWith('/') ? path : `/${path}`;
  return `${base}${cleanPath}`;
}

/**
 * 构建 WebSocket URL
 * @param path - WebSocket 路径（如 /ws/chat）
 * @returns WebSocket URL
 */
export function buildWebSocketUrl(path: string): string {
  const httpUrl = buildApiUrl(path);
  // 将 http:// 替换为 ws://，https:// 替换为 wss://
  return httpUrl.replace(/^http:\/\//, 'ws://').replace(/^https:\/\//, 'wss://');
}

/**
 * 将 request 实例和相关工具函数暴露到全局，供外部 JS 文件使用
 * 在 main.tsx 中调用此函数来初始化全局 API
 */
export function exposeRequestToGlobal(): void {
  if (typeof window !== 'undefined') {
    const win = window as any;
    
    // 暴露 request 实例
    win.request = request;
    
    // 暴露工具函数
    win.buildApiUrl = buildApiUrl;
    win.buildStaticUrl = buildStaticUrl;
    win.buildWebSocketUrl = buildWebSocketUrl;
    
    // 暴露配置（向后兼容）
    win.API_BASE_URL = getApiBaseUrl();
    win.STATIC_SERVER_URL = getStaticServerUrl();
    
    console.log('[Request] request 实例和工具函数已暴露到全局');
  }
}

