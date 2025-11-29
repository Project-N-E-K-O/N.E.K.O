/**
 * Web 环境使用示例
 */

import { createRequestClient, WebTokenStorage } from '../index.web';

// 创建请求客户端
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
    // 可选配置
    timeout: 10000,
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
        console.log('New user:', newUser);

        // PUT 请求
        const updatedUser = await request.put('/users/1', {
            name: 'Jane Doe',
        });

        // DELETE 请求
        await request.delete('/users/1');

        // 带参数的请求
        const filteredUsers = await request.get('/users', {
            params: { status: 'active' },
        });
    } catch (error) {
        console.error('Request failed:', error);
    }
}

