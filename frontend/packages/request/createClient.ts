import axios from "axios";
import type { AxiosInstance, AxiosRequestConfig, AxiosResponse, AxiosError, InternalAxiosRequestConfig } from "axios";
import createAuthRefreshInterceptor from "axios-auth-refresh";
import type { RequestClientConfig, TokenStorage, TokenRefreshFn } from "./src/request-client/types";
import { RequestQueue } from "./src/request-client/requestQueue";

/**
 * 安全序列化日志内容，避免循环引用导致异常
 */
const safeStringify = (value: unknown): string => {
  try {
    return JSON.stringify(value);
  } catch {
    try {
      return String(value);
    } catch {
      return "[Unserializable]";
    }
  }
};

// 由 Vite 构建时注入（见 packages/request/vite.config.ts）。
// 在 React Native/Metro 环境中这些常量通常不存在，应保持可选。
declare const __NEKO_VITE_MODE__: string | undefined;
declare const __NEKO_VITE_NODE_ENV__: string | undefined;

/**
 * 检查是否启用请求日志
 * 优先读取 config 覆盖 / 全局标记，其次根据构建模式判断
 * 无法读取环境变量时默认关闭
 */
const isRequestLogEnabled = (logEnabledOverride?: boolean): boolean => {
  if (typeof logEnabledOverride === "boolean") return logEnabledOverride;

  const globalFlag = (globalThis as any)?.NEKO_REQUEST_LOG_ENABLED;
  if (typeof globalFlag === "boolean") return globalFlag;

  // 注意：不要使用 import.meta（Metro/Hermes 可能无法解析）。
  // Web(Vite) 侧通过全局常量注入保持“开发默认开日志”的体验；
  // RN 侧可通过 __DEV__ 或 process.env.NODE_ENV 判断。
  const mode =
    (typeof __NEKO_VITE_MODE__ === "string" && __NEKO_VITE_MODE__) ||
    (typeof __NEKO_VITE_NODE_ENV__ === "string" && __NEKO_VITE_NODE_ENV__) ||
    // React Native 常见全局：__DEV__
    (typeof (globalThis as any).__DEV__ === "boolean" ? ((globalThis as any).__DEV__ ? "development" : "production") : "") ||
    // Node/Jest/某些打包环境
    (typeof process !== "undefined" && (process as any)?.env?.NODE_ENV ? String((process as any).env.NODE_ENV) : "") ||
    "";

  return mode === "development";
};

/**
 * Create an Axios HTTP client preconfigured with token attachment, automatic refresh, queuing, and optional logging.
 *
 * The client:
 * - attaches the latest access token from `storage` to every request,
 * - queues new requests while a refresh is in progress to avoid duplicate refresh calls,
 * - refreshes tokens on 401 via `refreshApi`, then retries queued requests,
 * - supports custom request/response interceptors and a unified `errorHandler`,
 * - returns `response.data` by default when `returnDataOnly` is true,
 * - allows request/response logging controlled by `logEnabled`, a global flag, or env (development only).
 *
 * @param options - Client configuration including `baseURL`, `storage`, `refreshApi`, optional interceptors,
 *                  `timeout`, `returnDataOnly`, `errorHandler`, and `logEnabled` overrides.
 * @returns An AxiosInstance with Bearer token injection, refresh queue handling, optional interceptors,
 *          safe logging, and sanitized error payloads.
 */
export function createRequestClient(options: RequestClientConfig): AxiosInstance {
  const {
    baseURL,
    storage,
    refreshApi,
    timeout = 15000,
    requestInterceptor,
    responseInterceptor,
    returnDataOnly = true,
    errorHandler,
    logEnabled
  } = options;

  const REQUEST_LOG_ENABLED = isRequestLogEnabled(logEnabled);

  // 创建 Axios 实例
  const instance = axios.create({
    baseURL,
    timeout,
    headers: {
      "Content-Type": "application/json"
    }
  });

  // 创建请求队列管理器
  const requestQueue = new RequestQueue();

  /**
   * Request 拦截器：自动添加 access_token
   */
  instance.interceptors.request.use(
    async (config: InternalAxiosRequestConfig) => {
      // 记录请求日志（仅在启用时）
      if (REQUEST_LOG_ENABLED) {
        const method = config.method?.toUpperCase() || 'GET';
        const url = config.url || '';
        const fullUrl = config.baseURL ? `${config.baseURL}${url}` : url;
        
        const logInfo: Record<string, unknown> = {};
        if (config.params) {
          const paramsStr = safeStringify(config.params);
          logInfo.params = paramsStr.length > 200 ? paramsStr.substring(0, 200) + '...' : paramsStr;
        }
        if (config.data) {
          const dataStr = typeof config.data === 'string' ? config.data : safeStringify(config.data);
          logInfo.data = dataStr.length > 200 ? dataStr.substring(0, 200) + '...' : dataStr;
        }
        
        console.log(`[Request] ${method} ${fullUrl}`, Object.keys(logInfo).length > 0 ? logInfo : '');
      }

      // 如果正在刷新 token，将请求加入队列
      if (requestQueue.getIsRefreshing()) {
        return new Promise<InternalAxiosRequestConfig>((resolve, reject) => {
          requestQueue.enqueue({
            resolve: async (cfg) => {
              // 添加最新 access token
              const token = await storage.getAccessToken();
              if (token && cfg.headers) {
                cfg.headers.Authorization = `Bearer ${token}`;
              }

              // 执行自定义请求拦截器
              if (requestInterceptor) {
                resolve(await requestInterceptor(cfg));
              } else {
                resolve(cfg);
              }
            },
            reject,
            config
          });
        });
      }

      // 添加 access token
      const token = await storage.getAccessToken();
      if (token && config.headers) {
        config.headers.Authorization = `Bearer ${token}`;
      }

      // 执行自定义请求拦截器
      if (requestInterceptor) {
        return await requestInterceptor(config);
      }

      return config;
    },
    (error: AxiosError) => {
      if (REQUEST_LOG_ENABLED) {
        console.error('[Request] 请求拦截器错误:', error);
      }
      return Promise.reject(error);
    }
  );

  /**
   * Token 刷新拦截器：401 时自动刷新 token
   */
  createAuthRefreshInterceptor(
    instance,
    async (failedRequest: AxiosError<any>) => {
      // 记录进入时是否已经在刷新，避免覆盖进行中的刷新 Promise
      const wasRefreshing = requestQueue.getIsRefreshing();
      const refreshPromise = requestQueue.startRefresh();

      // 已在刷新：只需等待既有刷新完成，然后使用新 token 重试
      if (wasRefreshing) {
        try {
          await refreshPromise;
          const newToken = await storage.getAccessToken();
          if (newToken && failedRequest.config?.headers) {
            failedRequest.config.headers.Authorization = `Bearer ${newToken}`;
          }
          return Promise.resolve();
        } catch (error) {
          return Promise.reject(error);
        }
      }

      try {
        const refreshToken = await storage.getRefreshToken();
        if (!refreshToken) {
          throw new Error("No refresh token available");
        }

        // 调用刷新 API
        const newTokens = await refreshApi(refreshToken);

        // 保存新 token
        await storage.setAccessToken(newTokens.accessToken);
        await storage.setRefreshToken(newTokens.refreshToken);

        // 更新失败请求的 header
        if (failedRequest.config?.headers) {
          failedRequest.config.headers.Authorization = `Bearer ${newTokens.accessToken}`;
        }

        // 完成刷新，处理队列中的请求
        await requestQueue.finishRefresh();

        return Promise.resolve();
      } catch (error) {
        // 刷新失败，清空 token 并处理队列
        await storage.clearTokens();
        await requestQueue.finishRefreshWithError(error);
        return Promise.reject(error);
      }
    },
    {
      statusCodes: [401], // 只在 401 时触发刷新
      // axios-auth-refresh v3 推荐使用 pauseInstanceWhileRefreshing：
      // - axios-auth-refresh 负责暂停/排队当前实例内的失败请求（如并发 401 时避免重复刷新）
      // - RequestQueue 负责请求拦截器阶段的新请求排队（isRefreshing=true 时挂起新请求）
      // 两层机制分工清晰，避免旧的 skipWhileRefreshing 失效导致行为不确定
      pauseInstanceWhileRefreshing: true
    }
  );

  /**
   * Response 拦截器：统一响应格式和错误处理
   */
  instance.interceptors.response.use(
    (response: AxiosResponse) => {
      // 记录响应日志（仅在启用时）
      if (REQUEST_LOG_ENABLED) {
        const method = response.config.method?.toUpperCase() || 'GET';
        const url = response.config.url || '';
        const fullUrl = response.config.baseURL ? `${response.config.baseURL}${url}` : url;
        const status = response.status;
        
        let responseDataStr = '';
        if (response.data !== undefined && response.data !== null) {
          const rawDataStr = typeof response.data === 'string' ? response.data : safeStringify(response.data);
          responseDataStr = rawDataStr.length > 200 ? rawDataStr.substring(0, 200) + '...' : rawDataStr;
        }
        
        console.log(`[Request] ${method} ${fullUrl} 响应 ${status}`, responseDataStr || '');
      }

      // 执行自定义成功拦截器
      if (responseInterceptor?.onFulfilled) {
        return responseInterceptor.onFulfilled(response);
      }

      // 默认返回 data
      return returnDataOnly ? response.data : response;
    },
    async (error: AxiosError) => {
      // 记录错误日志（仅在启用时）
      if (REQUEST_LOG_ENABLED) {
        const method = error.config?.method?.toUpperCase() || 'GET';
        const url = error.config?.url || '';
        const fullUrl = error.config?.baseURL ? `${error.config.baseURL}${url}` : url;
        const status = error.response?.status;
        
        const errorInfo: Record<string, unknown> = {
          status: status || 'N/A',
          message: error.message || 'Unknown error'
        };
        
        if (error.response?.data) {
          const errorDataStr =
            typeof error.response.data === 'string'
              ? error.response.data
              : safeStringify(error.response.data);
          const truncated = errorDataStr.length > 200 ? errorDataStr.substring(0, 200) + '...' : errorDataStr;
          errorInfo.data = truncated;
        }
        
        console.error(`[Request] ${method} ${fullUrl} 失败:`, errorInfo);
      }

      // 执行自定义错误拦截器
      if (responseInterceptor?.onRejected) {
        return responseInterceptor.onRejected(error);
      }

      // 执行自定义错误处理
      if (errorHandler) {
        await errorHandler(error);
      }

      // 统一错误格式，并对 config 进行脱敏（避免泄露 token/请求体等）
      const sanitizedConfig = (() => {
        if (!error.config) return undefined;
        const {
          url,
          method,
          baseURL,
          timeout,
          responseType,
          withCredentials,
          paramsSerializer
        } = error.config;
        // 仅保留非敏感字段，显式省略 headers/auth/params/data 等
        return {
          url,
          method,
          baseURL,
          timeout,
          responseType,
          withCredentials,
          // paramsSerializer 可能影响调试，但不包含敏感值
          paramsSerializer
        };
      })();

      const errorResponse = {
        message: error.message || "Request failed",
        status: error.response?.status,
        data: error.response?.data,
        config: sanitizedConfig
      };

      return Promise.reject(errorResponse);
    }
  );

  return instance;
}

