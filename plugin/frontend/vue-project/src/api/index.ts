/**
 * API 客户端配置
 */
import request from '@/utils/request'
import type { AxiosRequestConfig } from 'axios'

/**
 * Perform a GET request to the specified URL.
 *
 * @returns The response body parsed as `T`.
 */
export function get<T = any>(url: string, config?: AxiosRequestConfig): Promise<T> {
  return request.get<T, T>(url, config)
}

/**
 * Send a POST request to the specified URL and return the response data.
 *
 * @param url - Request URL
 * @param data - Request payload to be sent in the POST body
 * @param config - Optional Axios request configuration
 * @returns The response data of type `T`
 */
export function post<T = any>(url: string, data?: any, config?: AxiosRequestConfig): Promise<T> {
  return request.post<T, T>(url, data, config)
}

/**
 * Sends an HTTP PUT request to the specified URL.
 *
 * @param url - The request URL
 * @param data - The request payload to send as the PUT body
 * @param config - Optional Axios request configuration
 * @returns The response data typed as `T`
 */
export function put<T = any>(url: string, data?: any, config?: AxiosRequestConfig): Promise<T> {
  return request.put<T, T>(url, data, config)
}

/**
 * Sends an HTTP DELETE request to the specified URL and returns the response data.
 *
 * @param url - The endpoint URL to send the DELETE request to.
 * @param config - Optional Axios request configuration.
 * @returns The response data typed as `T`.
 */
export function del<T = any>(url: string, config?: AxiosRequestConfig): Promise<T> {
  return request.delete<T, T>(url, config)
}
