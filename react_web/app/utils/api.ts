/**
 * API 工具函数
 * 用于处理 API 请求的 baseUrl 配置
 */

/**
 * 构建完整的 API URL
 * @param path API 路径（如 "/api/characters"）
 * @returns 完整的 URL
 */
export function buildApiUrl(path: string): string {
  const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL as string) || "http://localhost:48911";
  
  // 如果 path 已经是完整 URL，直接返回
  if (path.startsWith("http://") || path.startsWith("https://")) {
    return path;
  }
  
  // 如果设置了 baseUrl，则拼接
  if (API_BASE_URL) {
    // 确保 baseUrl 不以 / 结尾，path 以 / 开头
    const base = API_BASE_URL.replace(/\/$/, "");
    const cleanPath = path.startsWith("/") ? path : `/${path}`;
    return `${base}${cleanPath}`;
  }
  
  // 否则返回原始路径（相对路径）
  return path;
}

/**
 * 包装的 fetch 函数，自动添加 baseUrl
 * @param input 请求 URL 或 Request 对象
 * @param init 可选的请求配置
 * @returns Promise<Response>
 */
export async function fetchWithBaseUrl(
  input: RequestInfo | URL,
  init?: RequestInit
): Promise<Response> {
  let url: string;
  
  if (typeof input === "string") {
    url = buildApiUrl(input);
  } else if (input instanceof URL) {
    url = buildApiUrl(input.toString());
  } else {
    // Request 对象
    url = buildApiUrl(input.url);
    // 创建新的 Request 对象，保持其他属性
    const newRequest = new Request(url, input);
    return fetch(newRequest, init);
  }

  console.log("fetchWithBaseUrl: ", url);
  
  return fetch(url, init);
}

