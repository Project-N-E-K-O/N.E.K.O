/**
 * 插件市场 API — 从 Market 后端获取插件列表和详情
 *
 * Market URL 从本地 /market/status 端点获取（由 NEKO_MARKET_URL 配置）。
 *
 * 注意：
 * - 后端返回扁平结构（author_name / icon_url / download_count / is_featured）；
 *   前端通过 normalizeMarketPlugin 转为组件期望的嵌套结构。
 * - 搜索参数统一用 `q`（后端 /plugins 接收 q，不是 search）。
 */
import axios from 'axios'
import type { AxiosInstance } from 'axios'

let _marketBaseUrl: string | null = null
let _marketClient: AxiosInstance | null = null

/** 前端使用的规范化后的插件结构。 */
export interface MarketPlugin {
  id: number | string
  /** Market 侧稳定 slug；用于与本地 "installed" 插件配对。 */
  slug?: string
  name: string
  description: string
  short_description?: string
  version: string
  author: {
    name: string
    avatar?: string
    github?: string
  }
  github_repo?: string
  download_url?: string
  icon_url?: string
  zone?: string
  tags: string[]
  downloads: number
  likes: number
  rating_average?: number
  created_at: string
  updated_at: string
  is_recommended?: boolean
}

/** 后端返回的原始结构（扁平）。 */
interface MarketPluginRaw {
  id: number | string
  slug?: string
  name: string
  description?: string | null
  short_description?: string | null
  author_id?: number
  author_name: string
  author?: {
    username?: string
    display_name?: string
    avatar_url?: string
  }
  version: string
  icon_url?: string | null
  download_url?: string | null
  repo_url?: string | null
  readme?: string | null
  zone_id?: number | null
  zone_slug?: string | null
  tags?: string[] | null
  download_count?: number
  likes?: number
  rating_average?: number
  rating_count?: number
  status?: string
  is_featured?: number | boolean
  created_at: string
  updated_at: string
  published_at?: string | null
}

interface PaginatedRaw<T> {
  items: T[]
  total: number
  page: number
  page_size: number
  total_pages?: number
  has_next?: boolean
  has_prev?: boolean
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

const ZONE_BY_ID: Record<number, string> = {
  1: 'game',
  2: 'companion',
  3: 'function',
  4: 'entertainment',
  5: 'tool',
}

function githubOwnerFromRepo(repoUrl?: string | null): string {
  if (!repoUrl) return ''
  try {
    const url = new URL(repoUrl)
    if (url.hostname !== 'github.com') return ''
    return url.pathname.split('/').filter(Boolean)[0] ?? ''
  } catch {
    return ''
  }
}

function githubProfile(repoUrl?: string | null): string | undefined {
  const owner = githubOwnerFromRepo(repoUrl)
  return owner ? `https://github.com/${owner}` : undefined
}

/** 将后端扁平结构规范化为组件期望的嵌套结构。 */
export function normalizeMarketPlugin(raw: MarketPluginRaw): MarketPlugin {
  const description = raw.description ?? raw.short_description ?? ''
  const zone = raw.zone_slug || (raw.zone_id ? ZONE_BY_ID[raw.zone_id] : undefined)
  const authorName =
    raw.author_name || raw.author?.display_name || raw.author?.username || ''

  return {
    id: raw.id,
    slug: raw.slug,
    name: raw.name,
    description,
    short_description: raw.short_description ?? undefined,
    version: raw.version,
    author: {
      name: authorName,
      avatar: raw.author?.avatar_url ?? raw.icon_url ?? undefined,
      github: githubProfile(raw.repo_url),
    },
    github_repo: raw.repo_url ?? undefined,
    download_url: raw.download_url ?? raw.repo_url ?? undefined,
    icon_url: raw.icon_url ?? undefined,
    zone,
    tags: raw.tags ?? [],
    downloads: raw.download_count ?? 0,
    likes: raw.likes ?? 0,
    rating_average: raw.rating_average,
    created_at: raw.created_at,
    updated_at: raw.updated_at,
    is_recommended: Boolean(raw.is_featured),
  }
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

/** 获取 Market HTTP 客户端。 */
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

/** 重置缓存（Market URL 变更或需要切换环境时调用）。 */
export function resetMarketClient(): void {
  _marketBaseUrl = null
  _marketClient = null
}

/** 检查 Market 是否可用。 */
export async function isMarketAvailable(): Promise<boolean> {
  const url = await getMarketBaseUrl()
  return !!url
}

export interface FetchMarketPluginsParams {
  page?: number
  page_size?: number
  /** 搜索关键词（映射到后端 `q`）。 */
  search?: string
  /** 分类 slug（映射到后端 `category`）。 */
  category?: string
  author?: string
  /** 排序字段：created_at | download_count | rating_average | name。 */
  sort_by?: string
  /** 排序方向：asc | desc。 */
  sort_order?: string
  /** 只显示推荐插件。 */
  featured_only?: boolean
}

/** 获取 Market 插件列表（自动规范化每一项）。 */
export async function fetchMarketPlugins(
  params?: FetchMarketPluginsParams,
): Promise<MarketPluginListResponse | null> {
  const client = await getClient()
  if (!client) return null

  // 后端 /plugins 接收 q，不是 search
  const { search, ...rest } = params || {}
  const queryParams: Record<string, unknown> = { ...rest }
  if (search) queryParams.q = search

  try {
    const res = await client.get<PaginatedRaw<MarketPluginRaw>>('/plugins', {
      params: queryParams,
    })
    const data = res.data
    return {
      items: data.items.map(normalizeMarketPlugin),
      total: data.total,
      page: data.page,
      page_size: data.page_size,
      pages: data.total_pages ?? Math.ceil(data.total / data.page_size),
    }
  } catch (err) {
    console.warn('[Market] Failed to fetch plugins:', err)
    return null
  }
}

/** 获取单个插件详情。 */
export async function fetchMarketPlugin(
  pluginId: string | number,
): Promise<MarketPlugin | null> {
  const client = await getClient()
  if (!client) return null

  try {
    const res = await client.get<MarketPluginRaw>(`/plugins/${pluginId}`)
    return normalizeMarketPlugin(res.data)
  } catch (err) {
    console.warn('[Market] Failed to fetch plugin:', err)
    return null
  }
}

/** 获取插件版本列表。 */
export async function fetchMarketPluginVersions(
  pluginId: string | number,
): Promise<MarketPluginVersion[] | null> {
  const client = await getClient()
  if (!client) return null

  try {
    const res = await client.get<MarketPluginVersion[]>(`/plugins/${pluginId}/versions`)
    return res.data
  } catch (err) {
    console.warn('[Market] Failed to fetch versions:', err)
    return null
  }
}

/** 获取 Market URL（供外部链接使用）。 */
export async function getMarketUrl(): Promise<string | null> {
  return getMarketBaseUrl()
}
