/**
 * 插件市场 API — 从 Market 后端获取插件列表和详情
 *
 * Market API URL 从本地 /market/status 端点获取（由 NEKO_MARKET_API_URL 配置）。
 *
 * v2（neko-market-version-sync）：
 * - 后端 schema 切到 latest_version 嵌套对象。version / download_url 不再
 *   直接在顶层；前端从 latest_version 取，缺失（latest_version === null）时
 *   下载按钮置灰。
 * - 删掉 `download_url ?? repo_url` 的 fallback：repo_url 仅作展示链接。
 * - fetchMarketPlugins / fetchMarketPluginVersions 接收 channel /
 *   include_yanked 透传。
 *
 * 注意：
 * - 搜索参数统一用 `q`（后端 /plugins 接收 q，不是 search）。
 */
import axios from 'axios'
import type { AxiosInstance } from 'axios'

let _marketBaseUrl: string | null = null
let _marketWebBaseUrl: string | null = null
let _marketClient: AxiosInstance | null = null

/** Market 上 latest_version 嵌套对象（v2 schema）。 */
export interface LatestVersion {
  version: string
  channel: string
  package_url: string
  package_sha256: string
  payload_hash: string | null
  created_at: string
}

/** 前端使用的规范化后的插件结构。 */
export interface MarketPlugin {
  id: number | string
  /** Market 侧稳定 slug；用于与本地 "installed" 插件配对。 */
  slug?: string
  /** Market 侧的稳定 ID（即 raw.id），用于触发 install / upgrade 接口。 */
  rawId: number | string
  name: string
  description: string
  short_description?: string
  /** 取自 latest_version.version；latest_version === null 时为 ''。 */
  version: string
  author: {
    name: string
    avatar?: string
    github?: string
  }
  github_repo?: string
  /** 取自 latest_version.package_url；latest_version === null 时为 undefined。 */
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
  /** 以下字段供 MarketPanel / MarketPluginCard 判断 upgrade / yank。 */
  latest_channel?: string
  latest_package_sha256?: string
  latest_payload_hash?: string | null
  latest_published_at?: string
  /** latest_version 不存在 → 暂无可用版本，安装按钮禁用。 */
  has_release: boolean
}

/** 后端返回的原始结构（v2 schema：latest_version 嵌套对象）。 */
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
  /** v2: latest_version 嵌套对象，没有发布版本时为 null。 */
  latest_version: LatestVersion | null
  icon_url?: string | null
  /** repo_url 保留但仅用于展示链接，不再作为下载源。 */
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
  /** v2: stable / beta channel 标记。 */
  channel: string
  changelog?: string
  /** v2: 重构后只保留 package_url，前端不再读 download_url。 */
  package_url?: string
  package_sha256?: string
  payload_hash?: string | null
  /** v2: 标识当前版本是否为该 channel 上的 latest。 */
  is_latest: boolean
  /** v2: yank 状态；非 null 即此版本已被作者撤回。 */
  yanked_at: string | null
  yanked_reason: string | null
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

/** 将后端扁平结构规范化为组件期望的嵌套结构（v2）。
 *
 * 关键变更：
 *   - version / download_url 取自 raw.latest_version；缺失时分别为
 *     '' / undefined（不再 fallback 到 repo_url，避免误把 GitHub 仓库主页
 *     当成下载链接）。
 *   - has_release 反映 latest_version 是否存在；UI 据此决定安装按钮可用性。
 *   - latest_* 派生字段方便 MarketPluginCard 计算 upgrade / yank 显示。
 */
export function normalizeMarketPlugin(raw: MarketPluginRaw): MarketPlugin {
  const description = raw.description ?? raw.short_description ?? ''
  const zone = raw.zone_slug || (raw.zone_id ? ZONE_BY_ID[raw.zone_id] : undefined)
  const authorName =
    raw.author_name || raw.author?.display_name || raw.author?.username || ''

  const lv = raw.latest_version
  const version = lv?.version ?? ''
  const downloadUrl = lv?.package_url ?? undefined

  return {
    id: raw.id,
    slug: raw.slug,
    rawId: raw.id,
    name: raw.name,
    description,
    short_description: raw.short_description ?? undefined,
    version,
    author: {
      name: authorName,
      avatar: raw.author?.avatar_url ?? raw.icon_url ?? undefined,
      github: githubProfile(raw.repo_url),
    },
    github_repo: raw.repo_url ?? undefined,
    download_url: downloadUrl,
    icon_url: raw.icon_url ?? undefined,
    zone,
    tags: raw.tags ?? [],
    downloads: raw.download_count ?? 0,
    likes: raw.likes ?? 0,
    rating_average: raw.rating_average,
    created_at: raw.created_at,
    updated_at: raw.updated_at,
    is_recommended: Boolean(raw.is_featured),
    latest_channel: lv?.channel,
    latest_package_sha256: lv?.package_sha256,
    latest_payload_hash: lv?.payload_hash,
    latest_published_at: lv?.created_at,
    has_release: lv !== null && lv !== undefined,
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
      _marketBaseUrl = normalizeBaseUrl(res.data.market_url)
      _marketWebBaseUrl = normalizeBaseUrl(res.data.market_web_url || res.data.market_url)
      return _marketBaseUrl
    }
  } catch {
    // 本地服务不可达或未配置
  }
  return null
}

async function getMarketWebBaseUrl(): Promise<string | null> {
  if (_marketWebBaseUrl !== null) return _marketWebBaseUrl
  await getMarketBaseUrl()
  return _marketWebBaseUrl
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
  _marketWebBaseUrl = null
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
  /** v2: 全局 channel 偏好（stable / beta）；后端按此过滤 latest_version。 */
  channel?: 'stable' | 'beta'
}

/** 获取 Market 插件列表（自动规范化每一项）。 */
export async function fetchMarketPlugins(
  params?: FetchMarketPluginsParams,
): Promise<MarketPluginListResponse | null> {
  const client = await getClient()
  if (!client) return null

  // 后端 /plugins 接收 q，不是 search
  const { search, channel, ...rest } = params || {}
  const queryParams: Record<string, unknown> = { ...rest }
  if (search) queryParams.q = search
  if (channel) queryParams.channel = channel

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

/** 获取插件版本列表。
 *
 * v2: options 参数让调用方按 channel 过滤 / 决定是否包含 yank 历史。
 * yank 检测路径会传 `include_yanked: true`；普通 UI 列表不传或传 false。
 */
export async function fetchMarketPluginVersions(
  pluginId: string | number,
  options?: { channel?: 'stable' | 'beta'; include_yanked?: boolean },
): Promise<MarketPluginVersion[] | null> {
  const client = await getClient()
  if (!client) return null

  const queryParams: Record<string, unknown> = {}
  if (options?.channel) queryParams.channel = options.channel
  if (options?.include_yanked) queryParams.include_yanked = true

  try {
    const res = await client.get<MarketPluginVersion[]>(
      `/plugins/${pluginId}/versions`,
      { params: queryParams },
    )
    return res.data
  } catch (err) {
    console.warn('[Market] Failed to fetch versions:', err)
    return null
  }
}

/** 获取 Market URL（供外部链接使用）。 */
export async function getMarketUrl(): Promise<string | null> {
  return getMarketWebBaseUrl()
}

function normalizeBaseUrl(value: string): string {
  return value.replace(/\/+$/, '')
}
