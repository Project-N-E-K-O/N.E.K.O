import axios from 'axios';
import type { AxiosInstance, AxiosRequestConfig, AxiosResponse, AxiosError, InternalAxiosRequestConfig } from 'axios';
import createAuthRefreshInterceptor from 'axios-auth-refresh';
import type { RequestClientConfig, TokenStorage, TokenRefreshFn } from './src/request-client/types';
import { RequestQueue } from './src/request-client/requestQueue';

/**
 * 创建统一的请求客户端
 * 支持 Axios、Token 刷新、请求队列、Web/RN 通用存储
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
    } = options;

    // 创建 Axios 实例
    const instance = axios.create({
        baseURL,
        timeout,
        headers: {
            'Content-Type': 'application/json',
        },
    });

    // 创建请求队列管理器
    const requestQueue = new RequestQueue();

    /**
     * Request 拦截器：自动添加 access_token
     */
    instance.interceptors.request.use(
        async (config: InternalAxiosRequestConfig) => {
            // 如果正在刷新 token，将请求加入队列
            if (requestQueue.getIsRefreshing()) {
                return new Promise((resolve, reject) => {
                    requestQueue.enqueue({ resolve, reject, config });
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
        (error: AxiosError) => Promise.reject(error)
    );

    /**
     * Token 刷新拦截器：401 时自动刷新 token
     */
    createAuthRefreshInterceptor(
        instance,
        async (failedRequest: AxiosError<any>) => {
            // 如果已经在刷新，等待刷新完成
            if (requestQueue.getIsRefreshing()) {
                try {
                    await requestQueue.startRefresh();
                    // 刷新完成后，使用新 token 重试请求
                    const newToken = await storage.getAccessToken();
                    if (newToken && failedRequest.config?.headers) {
                        failedRequest.config.headers.Authorization = `Bearer ${newToken}`;
                    }
                    return Promise.resolve();
                } catch (error) {
                    return Promise.reject(error);
                }
            }

            // 开始刷新流程
            await requestQueue.startRefresh();

            try {
                const refreshToken = await storage.getRefreshToken();
                if (!refreshToken) {
                    throw new Error('No refresh token available');
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
            skipWhileRefreshing: false, // 允许在刷新期间处理其他请求
        }
    );

    /**
     * Response 拦截器：统一响应格式和错误处理
     */
    instance.interceptors.response.use(
        (response: AxiosResponse) => {
            // 执行自定义成功拦截器
            if (responseInterceptor?.onFulfilled) {
                return responseInterceptor.onFulfilled(response);
            }

            // 默认返回 data
            return returnDataOnly ? response.data : response;
        },
        async (error: AxiosError) => {
            // 执行自定义错误拦截器
            if (responseInterceptor?.onRejected) {
                return responseInterceptor.onRejected(error);
            }

            // 执行自定义错误处理
            if (errorHandler) {
                await errorHandler(error);
            }

            // 统一错误格式
            const errorResponse = {
                message: error.message || 'Request failed',
                status: error.response?.status,
                data: error.response?.data,
                config: error.config,
            };

            return Promise.reject(errorResponse);
        }
    );

    return instance;
}
