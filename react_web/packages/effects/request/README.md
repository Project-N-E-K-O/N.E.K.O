# @xiao8/request

真正可直接用于 **React Web + React Native** 的统一请求库，提供 `@vben/request` 等价功能。

## ✨ 特性

- ✅ **Axios 基础** - 基于 Axios，提供强大的 HTTP 客户端能力
- ✅ **统一请求实例** - 一次配置，全项目使用
- ✅ **自动 Token 刷新** - 401 时自动刷新 access token，无需手动处理
- ✅ **请求队列** - 防止并发刷新 token，确保请求顺序执行
- ✅ **Web/RN 通用存储抽象** - 自动适配 localStorage (Web) 和 AsyncStorage (RN)
- ✅ **TypeScript 支持** - 完整的类型定义
- ✅ **灵活配置** - 支持自定义拦截器、错误处理等

## 📦 安装

```bash
# 安装核心依赖
npm install axios axios-auth-refresh

# Web 环境（可选，通常已安装）
# localStorage 是浏览器原生 API

# React Native 环境
npm install @react-native-async-storage/async-storage
```

## 🚀 快速开始

### 基础使用（3 步）

```typescript
// 1. 导入 request
import { request } from '~/api/request';

// 2. 发起请求
const users = await request.get('/api/users');
const newUser = await request.post('/api/users', { name: 'John' });

// 3. 处理错误
try {
  const data = await request.get('/api/users');
} catch (error) {
  console.error('Request failed:', error);
}
```

### Web 环境配置

```typescript
// app/api/request.ts
import { createRequestClient, WebTokenStorage } from '@xiao8/request';

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
    }
});
```

### React Native 环境配置

```typescript
// index.native.ts
import { createRequestClient, NativeTokenStorage } from '@xiao8/request';

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
    }
});
```

## 📖 在 react_web 项目中使用

### 配置

1. **TypeScript 路径映射**（已在 `tsconfig.json` 中配置）：
```json
{
  "compilerOptions": {
    "paths": {
      "@xiao8/request": ["./packages/effects/request/index.web"]
    }
  }
}
```

2. **使用配置好的实例**：
```typescript
import { request } from '~/api/request';
```

### 使用示例

```typescript
// GET 请求
const users = await request.get('/api/users');

// POST 请求
const newUser = await request.post('/api/users', {
  name: 'John',
  email: 'john@example.com',
});

// PUT 请求
const updated = await request.put('/api/users/1', { name: 'Jane' });

// DELETE 请求
await request.delete('/api/users/1');

// 带查询参数
const data = await request.get('/api/users', {
  params: { status: 'active', page: 1 },
});
```

### 在 React 组件中使用

```typescript
import { useEffect, useState } from 'react';
import { request } from '~/api/request';

function UserList() {
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const fetchUsers = async () => {
      setLoading(true);
      try {
        const data = await request.get('/api/users');
        setUsers(data);
      } catch (error) {
        console.error('Failed to fetch users:', error);
      } finally {
        setLoading(false);
      }
    };
    fetchUsers();
  }, []);

  return (
    <div>
      {loading ? <p>Loading...</p> : (
        <ul>
          {users.map(user => (
            <li key={user.id}>{user.name}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
```

## 🔄 从 fetchWithBaseUrl 迁移

### 迁移步骤

**之前：**
```typescript
// 使用 fetchWithBaseUrl（已废弃）
const response = await fetchWithBaseUrl('/api/users');
const data = await response.json();
```

**之后：**
```typescript
import { request } from '~/api/request';

const data = await request.get('/api/users');
```

### 完整迁移对照

| 操作 | 之前 | 之后 |
|------|------|------|
| GET | `fetchWithBaseUrl(buildApiUrl('/api/users')).then(r => r.json())` | `request.get('/api/users')` |
| POST | `fetchWithBaseUrl(buildApiUrl('/api/users'), { method: 'POST', body: JSON.stringify(data) }).then(r => r.json())` | `request.post('/api/users', data)` |
| PUT | `fetchWithBaseUrl(buildApiUrl('/api/users/1'), { method: 'PUT', body: JSON.stringify(data) }).then(r => r.json())` | `request.put('/api/users/1', data)` |
| DELETE | `fetchWithBaseUrl(buildApiUrl('/api/users/1'), { method: 'DELETE' })` | `request.delete('/api/users/1')` |

### 迁移优势

迁移后自动获得：
- ✅ 自动 Token 管理 - 自动添加 Authorization header
- ✅ Token 自动刷新 - 401 时自动刷新 token
- ✅ 请求队列 - 防止并发刷新 token
- ✅ 统一错误处理 - 可配置的错误处理
- ✅ 类型安全 - 完整的 TypeScript 支持
- ✅ 自动 baseURL - 使用项目的 `VITE_API_BASE_URL` 环境变量
- ✅ JSON 自动处理 - 不需要手动调用 `.json()`

## 📖 API 文档

### `createRequestClient(options)`

创建请求客户端实例。

#### 配置选项

```typescript
interface RequestClientConfig {
    /** 基础 URL */
    baseURL: string;
    
    /** Token 存储实现 */
    storage: TokenStorage;
    
    /** Token 刷新函数 */
    refreshApi: (refreshToken: string) => Promise<{
        accessToken: string;
        refreshToken: string;
    }>;
    
    /** 请求超时时间（毫秒），默认 15000 */
    timeout?: number;
    
    /** 自定义请求拦截器 */
    requestInterceptor?: (config: InternalAxiosRequestConfig) => InternalAxiosRequestConfig | Promise<InternalAxiosRequestConfig>;
    
    /** 自定义响应拦截器 */
    responseInterceptor?: {
        onFulfilled?: (response: AxiosResponse) => any;
        onRejected?: (error: AxiosError) => any;
    };
    
    /** 是否在响应中自动返回 data，默认 true */
    returnDataOnly?: boolean;
    
    /** 自定义错误处理 */
    errorHandler?: (error: AxiosError) => void | Promise<void>;
}
```

### `TokenStorage` 接口

```typescript
interface TokenStorage {
    getAccessToken(): Promise<string | null>;
    setAccessToken(token: string): Promise<void>;
    getRefreshToken(): Promise<string | null>;
    setRefreshToken(token: string): Promise<void>;
    clearTokens(): Promise<void>;
}
```

### 内置实现

- **`WebTokenStorage`** - Web 环境，使用 localStorage
- **`NativeTokenStorage`** - React Native 环境，使用 AsyncStorage

## 🔧 高级用法

### 自定义 Token 存储

```typescript
import { TokenStorage } from '@xiao8/request';

class CustomTokenStorage implements TokenStorage {
    async getAccessToken() {
        return await yourStorage.get('access_token');
    }
    
    async setAccessToken(token: string) {
        await yourStorage.set('access_token', token);
    }
    
    // ... 实现其他方法
}

const request = createRequestClient({
    baseURL: '/api',
    storage: new CustomTokenStorage(),
    refreshApi: async (refreshToken) => { /* ... */ }
});
```

### 自定义拦截器

```typescript
const request = createRequestClient({
    baseURL: '/api',
    storage: new WebTokenStorage(),
    refreshApi: async (refreshToken) => { /* ... */ },
    
    // 自定义请求拦截器
    requestInterceptor: async (config) => {
        config.headers['X-Custom-Header'] = 'value';
        return config;
    },
    
    // 自定义响应拦截器
    responseInterceptor: {
        onFulfilled: (response) => {
            if (response.data.code === 0) {
                return response.data.data;
            }
            throw new Error(response.data.message);
        },
        onRejected: (error) => {
            console.error('Request failed:', error);
            return Promise.reject(error);
        }
    },
    
    // 自定义错误处理
    errorHandler: async (error) => {
        if (error.response?.status === 403) {
            window.location.href = '/login';
        }
    }
});
```

### Token 管理

```typescript
import { WebTokenStorage } from '@xiao8/request';

const storage = new WebTokenStorage();

// 登录后设置 token
await storage.setAccessToken('your-access-token');
await storage.setRefreshToken('your-refresh-token');

// 登出时清空 token
await storage.clearTokens();
```

## 🔄 工作原理

### Token 刷新流程

1. 请求发送时，自动在 header 中添加 `Authorization: Bearer {accessToken}`
2. 如果收到 401 响应，触发 token 刷新流程
3. 刷新期间，新的请求会被加入队列等待
4. 刷新成功后，使用新 token 重试失败的请求，并处理队列中的请求
5. 如果刷新失败，清空 token 并拒绝所有请求

### 请求队列

请求队列确保在 token 刷新期间：
- 不会并发执行多个刷新请求
- 等待中的请求会在刷新完成后自动重试
- 所有请求都能获得最新的 token

## 🎯 与 @vben/request 的对比

| 特性 | @vben/request | @xiao8/request |
|------|--------------|----------------|
| React Web | ✅ | ✅ |
| React Native | ❌ | ✅ |
| Axios 基础 | ✅ | ✅ |
| Token 自动刷新 | ✅ | ✅ |
| 请求队列 | ✅ | ✅ |
| 存储抽象 | ❌ | ✅ |
| TypeScript | ✅ | ✅ |

## 📝 类型定义

所有类型都已导出，可以直接使用：

```typescript
import type {
    RequestClientConfig,
    TokenStorage,
    TokenRefreshFn,
    TokenRefreshResult,
    QueuedRequest,
} from '@xiao8/request';
```

## ⚠️ 注意事项

1. **依赖安装**: 确保安装了 `axios` 和 `axios-auth-refresh`
2. **RN 环境**: React Native 需要安装 `@react-native-async-storage/async-storage`
3. **Token 格式**: 默认使用 `Bearer` token，可在拦截器中修改
4. **刷新 API**: 确保刷新 API 返回 `{ accessToken, refreshToken }` 格式
5. **并发请求**: 请求队列确保不会并发刷新 token
6. **baseURL**: 自动使用项目的 `VITE_API_BASE_URL` 环境变量
7. **JSON 处理**: 自动处理 JSON，不需要手动调用 `.json()`

## 📁 项目结构

```
request/
├── src/
│   ├── request-client/
│   │   ├── types.ts          # 类型定义
│   │   ├── tokenStorage.ts   # Token 存储实现
│   │   └── requestQueue.ts   # 请求队列管理器
│   └── storage/
│       ├── types.ts           # 存储接口定义
│       ├── webStorage.ts     # Web 存储实现
│       ├── nativeStorage.ts  # RN 存储实现
│       └── index.ts          # 自动选择存储
├── examples/                  # 使用示例
├── createClient.ts            # 核心创建函数
├── index.ts                   # 统一导出
├── index.web.ts              # Web 环境入口
├── index.native.ts           # RN 环境入口
└── README.md                  # 本文档
```

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📄 许可证

MIT
