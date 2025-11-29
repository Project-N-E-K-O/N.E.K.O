/**
 * React Native 环境使用示例
 */

import { createRequestClient, NativeTokenStorage } from '../index.native';

// 创建请求客户端
export const request = createRequestClient({
    baseURL: 'https://api.yourserver.com',
    storage: new NativeTokenStorage(),
    refreshApi: async (refreshToken: string) => {
        const res = await fetch('https://api.yourserver.com/auth/refresh', {
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
});

// 使用示例
async function example() {
    try {
        // GET 请求
        const users = await request.get('/users');
        console.log('Users:', users);

        // POST 请求
        const newUser = await request.post('/users', {
            name: 'John Doe',
            email: 'john@example.com',
        });

        // 带自定义 header 的请求
        const data = await request.get('/protected', {
            headers: {
                'X-Custom-Header': 'value',
            },
        });
    } catch (error) {
        console.error('Request failed:', error);
    }
}

