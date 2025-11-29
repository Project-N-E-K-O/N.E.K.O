/**
 * @xiao8/request 使用示例
 * 
 * 这个文件展示了如何在 react_web 项目中使用 request 包
 */

import { request } from './request';

// ========== 基础使用 ==========

/**
 * GET 请求示例
 */
export async function fetchUsers() {
  try {
    const users = await request.get('/api/users');
    console.log('Users:', users);
    return users;
  } catch (error) {
    console.error('Failed to fetch users:', error);
    throw error;
  }
}

/**
 * POST 请求示例
 */
export async function createUser(userData: { name: string; email: string }) {
  try {
    const newUser = await request.post('/api/users', userData);
    console.log('Created user:', newUser);
    return newUser;
  } catch (error) {
    console.error('Failed to create user:', error);
    throw error;
  }
}

/**
 * PUT 请求示例
 */
export async function updateUser(userId: string, userData: Partial<{ name: string; email: string }>) {
  try {
    const updatedUser = await request.put(`/api/users/${userId}`, userData);
    console.log('Updated user:', updatedUser);
    return updatedUser;
  } catch (error) {
    console.error('Failed to update user:', error);
    throw error;
  }
}

/**
 * DELETE 请求示例
 */
export async function deleteUser(userId: string) {
  try {
    await request.delete(`/api/users/${userId}`);
    console.log('User deleted successfully');
  } catch (error) {
    console.error('Failed to delete user:', error);
    throw error;
  }
}

// ========== 带参数的请求 ==========

/**
 * 带查询参数的 GET 请求
 */
export async function searchUsers(params: { status?: string; page?: number; limit?: number }) {
  try {
    const users = await request.get('/api/users', { params });
    return users;
  } catch (error) {
    console.error('Failed to search users:', error);
    throw error;
  }
}

// ========== 在 React 组件中使用 ==========

/**
 * React Hook 示例
 * 
 * 在组件中使用：
 * ```typescript
 * import { useState, useEffect } from 'react';
 * import { request } from '~/api/request';
 * 
 * function UserList() {
 *   const [users, setUsers] = useState([]);
 *   const [loading, setLoading] = useState(false);
 *   const [error, setError] = useState(null);
 * 
 *   useEffect(() => {
 *     const fetchUsers = async () => {
 *       setLoading(true);
 *       setError(null);
 *       try {
 *         const data = await request.get('/api/users');
 *         setUsers(data);
 *       } catch (err: any) {
 *         setError(err.message);
 *       } finally {
 *         setLoading(false);
 *       }
 *     };
 *     fetchUsers();
 *   }, []);
 * 
 *   return { users, loading, error };
 * }
 * ```
 */

// ========== 替换现有的 fetchWithBaseUrl ==========

/**
 * 使用 request 替换原有的 fetchWithBaseUrl
 * 
 * 原来的代码：
 * ```typescript
 * const response = await fetchWithBaseUrl(apiUrl);
 * const data = await response.json();
 * ```
 * 
 * 新的代码：
 * ```typescript
 * const data = await request.get(apiUrl);
 * ```
 */

/**
 * 示例：获取页面配置（替换原有实现）
 */
export async function loadPageConfig(lanlanName?: string) {
  try {
    const url = lanlanName
      ? `/api/config/page_config?lanlan_name=${encodeURIComponent(lanlanName)}`
      : '/api/config/page_config';
    
    const data = await request.get(url);
    
    if (data.success) {
      return data;
    } else {
      throw new Error(data.message || 'Failed to load page config');
    }
  } catch (error) {
    console.error('Failed to load page config:', error);
    throw error;
  }
}

// ========== Token 管理 ==========

/**
 * 手动设置 Token（登录后）
 */
export async function setAuthTokens(accessToken: string, refreshToken: string) {
  const { WebTokenStorage } = await import('@xiao8/request');
  const storage = new WebTokenStorage();
  await storage.setAccessToken(accessToken);
  await storage.setRefreshToken(refreshToken);
}

/**
 * 清除 Token（登出）
 */
export async function clearAuthTokens() {
  const { WebTokenStorage } = await import('@xiao8/request');
  const storage = new WebTokenStorage();
  await storage.clearTokens();
}

