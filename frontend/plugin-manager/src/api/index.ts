/**
 * API 客户端配置
 */
import request from '@/utils/request'
import type { ErrorDisplayRequestConfig } from '@/utils/request'

// 响应拦截器已将 AxiosResponse 解包为 data；AxiosInstance 类型无法表达该转换。
/**
 * 通用 GET 请求
 */
export function get<T = any>(url: string, config?: ErrorDisplayRequestConfig): Promise<T> {
  return request.get<T>(url, config) as unknown as Promise<T>
}

/**
 * 通用 POST 请求
 */
export function post<T = any>(url: string, data?: any, config?: ErrorDisplayRequestConfig): Promise<T> {
  return request.post<T>(url, data, config) as unknown as Promise<T>
}

/**
 * 通用 PUT 请求
 */
export function put<T = any>(url: string, data?: any, config?: ErrorDisplayRequestConfig): Promise<T> {
  return request.put<T>(url, data, config) as unknown as Promise<T>
}

/**
 * 通用 DELETE 请求
 */
export function del<T = any>(url: string, config?: ErrorDisplayRequestConfig): Promise<T> {
  return request.delete<T>(url, config) as unknown as Promise<T>
}
