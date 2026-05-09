/**
 * 插件市场 API — 从 Market 后端获取插件列表和详情
 *
 * Market URL 从本地 /market/status 端点获取（由 NEKO_MARKET_URL 配置）。
 */
import axios from 'axios'
import type { AxiosInstance } from 'axios'

let _marketBaseUrl: string | null = null
let _marketClient: AxiosInstance | null = null

export interface MarketPlugin {
  id: number | string
  name: string
  description: string
  version: string
  author: {
    name: string
    avatar?: string
    github?: string
  }
  github_repo?: string
  download_url?: string
  zone?: string
  tags: string[]
  downloads: number
  likes: number
  created_at: string
  updated_at: string
  is_recommended?: boolean
}

export interface MarketPluginListResponse {
  items: MarketPlugin[]
  total: number
  page: number
  page_size: number
  pages: number
}

export interface MarketPluginVersion {
  id: number
  plugin_id: number
  version: string
  changelog?: string
  download_url?: string
  package_url?: string
  package_sha256?: string
  payload_hash?: string
  created_at: string
}

/**
 * 获取 Market base URL（从本地 Plugin Server 的 /market/status 获取）
 */
async function getMarketBaseUrl(): Promise<string | null> {
  if (_marketBaseUrl !== null) return _marketBaseUrl

  try {
    const res = await axios.get('/market/status', { timeout: 3000 })
    if (res.data?.market_url) {
      _marketBaseUrl = res.data.market_url
      return _marketBaseUrl
    }
  } catch {
    // 本地服务不可达或未配置
  }
  return null
}

/**
 * 获取 Market HTTP 客户端
 */
async function getClient(): Promise<AxiosInstance | null> {
  if (_marketClient) return _marketClient

  const baseUrl = await getMarketBaseUrl()
  if (!baseUrl) return null

  _marketClient = axios.create({
    baseURL: `${baseUrl}/api/v1`,
    timeout: 10000,
    headers: { 'Content-Type': 'application/json' },
  })

  return _marketClient
}

/**
 * 重置缓存（Market URL 变更时调用）
 */
export function resetMarketClient(): void {
  _marketBaseUrl = null
  _marketClient = null
}

/**
 * 检查 Market 是否可用
 */
export async function isMarketAvailable(): Promise<boolean> {
  const url = await getMarketBaseUrl()
  return !!url
}

/**
 * 获取 Market 插件列表
 */
export async function fetchMarketPlugins(params?: {
  page?: number
  page_size?: number
  search?: string
  zone?: string
  sort_by?: string
}): Promise<MarketPluginListResponse | null> {
  const client = await getClient()
  if (!client) return null

  try {
    const res = await client.get('/plugins', { params })
    return res.data
  } catch (err) {
    console.warn('[Market] Failed to fetch plugins:', err)
    return null
  }
}

/**
 * 获取单个插件详情
 */
export async function fetchMarketPlugin(pluginId: string | number): Promise<MarketPlugin | null> {
  const client = await getClient()
  if (!client) return null

  try {
    const res = await client.get(`/plugins/${pluginId}`)
    return res.data
  } catch (err) {
    console.warn('[Market] Failed to fetch plugin:', err)
    return null
  }
}

/**
 * 获取插件版本列表
 */
export async function fetchMarketPluginVersions(pluginId: string | number): Promise<MarketPluginVersion[] | null> {
  const client = await getClient()
  if (!client) return null

  try {
    const res = await client.get(`/plugins/${pluginId}/versions`)
    return res.data
  } catch (err) {
    console.warn('[Market] Failed to fetch versions:', err)
    return null
  }
}

/**
 * 获取 Market URL（供外部链接使用）
 */
export async function getMarketUrl(): Promise<string | null> {
  return getMarketBaseUrl()
}
