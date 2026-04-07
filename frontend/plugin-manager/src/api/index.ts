/**
 * API 客户端配置
 */
import request from '@/utils/request'
import type { AxiosRequestConfig } from 'axios'

/**
 * 通用 GET 请求
 */
export function get<T = any>(url: string, config?: AxiosRequestConfig): Promise<T> {
  return request.get<T, T>(url, config)
}

/**
 * 通用 POST 请求
 */
export function post<T = any>(url: string, data?: any, config?: AxiosRequestConfig): Promise<T> {
  return request.post<T, T>(url, data, config)
}

/**
 * 通用 PUT 请求
 */
export function put<T = any>(url: string, data?: any, config?: AxiosRequestConfig): Promise<T> {
  return request.put<T, T>(url, data, config)
}

/**
 * 通用 DELETE 请求
 */
export function del<T = any>(url: string, config?: AxiosRequestConfig): Promise<T> {
  return request.delete<T, T>(url, config)
}

