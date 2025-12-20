/**
 * HTTP 请求封装
 */
import axios from 'axios'
import type { AxiosInstance, InternalAxiosRequestConfig, AxiosResponse, AxiosError } from 'axios'
import { ElMessage } from 'element-plus'
import { API_BASE_URL, API_TIMEOUT } from './constants'

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
    // 可以在这里添加 token 等认证信息
    // const token = localStorage.getItem('token')
    // if (token && config.headers) {
    //   config.headers.Authorization = `Bearer ${token}`
    // }
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
    // 直接返回 response.data，简化调用方的处理
    // 如果返回的状态码不是 200，则视为错误
    if (response.status !== 200) {
      const res = response.data as any
      ElMessage.error(res.message || '请求失败')
      return Promise.reject(new Error(res.message || '请求失败'))
    }

    return response.data
  },
  (error: AxiosError) => {
    console.error('Response error:', error)

    let message = '请求失败'
    
    if (error.response) {
      // 服务器返回了错误状态码
      const status = error.response.status
      const data = error.response.data as any

      switch (status) {
        case 400:
          message = data.detail || '请求参数错误'
          break
        case 401:
          message = '未授权，请重新登录'
          // 可以在这里处理登录跳转
          break
        case 403:
          message = '拒绝访问'
          break
        case 404:
          message = data.detail || '请求的资源不存在'
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

    ElMessage.error(message)
    return Promise.reject(error)
  }
)

export default service

