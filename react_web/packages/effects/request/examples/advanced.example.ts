/**
 * 高级配置示例
 */

import { createRequestClient, WebTokenStorage } from '../index.web';
import type { AxiosRequestConfig, AxiosResponse, AxiosError } from 'axios';

export const request = createRequestClient({
    baseURL: '/api',
    storage: new WebTokenStorage(),
    refreshApi: async (refreshToken: string) => {
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
    
    // 自定义请求拦截器
    requestInterceptor: async (config: AxiosRequestConfig) => {
        // 添加时间戳
        if (config.params) {
            config.params._t = Date.now();
        } else {
            config.params = { _t: Date.now() };
        }
        
        // 添加自定义 header
        if (config.headers) {
            config.headers['X-Request-ID'] = generateRequestId();
        }
        
        return config;
    },
    
    // 自定义响应拦截器
    responseInterceptor: {
        onFulfilled: (response: AxiosResponse) => {
            // 统一处理业务逻辑
            if (response.data && response.data.code === 0) {
                return response.data.data;
            }
            // 业务错误
            throw new Error(response.data?.message || 'Request failed');
        },
        onRejected: (error: AxiosError) => {
            // 统一错误处理
            if (error.response) {
                switch (error.response.status) {
                    case 403:
                        console.error('Forbidden');
                        break;
                    case 404:
                        console.error('Not Found');
                        break;
                    case 500:
                        console.error('Server Error');
                        break;
                }
            }
            return Promise.reject(error);
        },
    },
    
    // 自定义错误处理
    errorHandler: async (error: AxiosError) => {
        // 可以在这里进行错误上报
        if (error.response?.status === 403) {
            // 处理 403，例如跳转到登录页
            window.location.href = '/login';
        }
    },
    
    // 返回完整响应对象而不是只返回 data
    returnDataOnly: false,
});

function generateRequestId(): string {
    return `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
}

