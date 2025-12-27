/**
 * HTTP 请求封装
 */
import axios from 'axios'
import type { AxiosInstance, InternalAxiosRequestConfig, AxiosResponse, AxiosError } from 'axios'
import { ElMessage } from 'element-plus'
import { API_BASE_URL, API_TIMEOUT } from './constants'
import { useAuthStore } from '@/stores/auth'

// 处理认证失败的辅助函数
async function handleAuthError() {
  try {
    const authStore = useAuthStore()
    authStore.clearAuthCode()
  } catch (err) {
    console.debug('Auth store not available:', err)
  }
  // 动态导入 router 避免循环依赖
  import('@/router').then(({ default: router }) => {
    router.push('/login')
  })
  // 不显示错误消息，因为会自动跳转
  ElMessage.closeAll()
}

// 创建 axios 实例
const service: AxiosInstance = axios.create({
  baseURL: API_BASE_URL,
  timeout: API_TIMEOUT,
  headers: {
    'Content-Type': 'application/json'
  }
})

// 请求拦截器
service.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    // 添加认证头
    try {
      const authStore = useAuthStore()
      const authHeader = authStore.getAuthHeader()
      if (authHeader && config.headers) {
        config.headers.Authorization = authHeader
      }
    } catch (err) {
      // Store 可能还未初始化，忽略错误
      // 在开发环境下可能会看到这个错误，但不影响功能
    }
    return config
  },
  (error: AxiosError) => {
    console.error('Request error:', error)
    return Promise.reject(error)
  }
)

// 响应拦截器
service.interceptors.response.use(
  (response: AxiosResponse) => {
    // Axios 默认只会把 2xx 响应放到这里，直接返回 data 即可
    return response.data
  },
  async (error: AxiosError) => {
    // 对于 404 错误，不输出错误日志（这是正常的，某些资源可能不存在）
    // 对于 401/403 错误，也不输出错误日志（会自动跳转登录页）
    const status = error.response?.status
    if (status !== 404 && status !== 401 && status !== 403) {
      console.error('Response error:', error)
    }

    let message = '请求失败'
    
    if (error.response) {
      // 服务器返回了错误状态码
      const data = error.response.data as any

      switch (status) {
        case 400:
          message = data.detail || '请求参数错误'
          break
        case 401:
          message = '未授权，请重新登录'
          await handleAuthError()
          break
        case 403:
          message = data.detail || '拒绝访问：验证码错误或已过期'
          await handleAuthError()
          break
        case 404:
          message = data.detail || '请求的资源不存在'
          // 404 错误不显示通用错误消息，让调用方自己处理
          ElMessage.closeAll()
          break
        case 500:
          message = data.detail || '服务器内部错误'
          break
        case 503:
          message = data.detail || '服务不可用'
          break
        default:
          message = data.detail || `请求失败 (${status})`
      }
    } else if (error.request) {
      // 请求已发出，但没有收到响应
      message = '网络错误，请检查网络连接'
    } else {
      // 其他错误
      message = error.message || '请求失败'
    }

    // 对于 401/403/404，不显示错误消息（已处理或由调用方处理）
    if (error.response && [401, 403, 404].includes(error.response.status)) {
      return Promise.reject(error)
    }
    
    ElMessage.error(message)
    return Promise.reject(error)
  }
)

export default service

