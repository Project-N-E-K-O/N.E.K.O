/**
 * 插件相关 API
 */
import { del, get, post } from './index'
import type {
  PluginMeta,
  PluginStatusData,
  PluginHealth,
  PluginMessage,
  PluginUiInfo,
  PluginUiContext,
  PluginUiSurface,
  PluginUiWarning,
} from '@/types/api'

/**
 * 获取插件列表
 */
export function getPlugins(): Promise<{ plugins: PluginMeta[]; message: string }> {
  return get('/plugins')
}

/**
 * 刷新插件注册表
 */
export function refreshPluginsRegistry(): Promise<{
  success: boolean
  added: string[]
  updated: string[]
  removed: string[]
  removed_running: string[]
  unchanged: string[]
  failed: Array<{ plugin_id: string; config_path: string; error: string }>
  scanned_count: number
}> {
  return post('/plugins/refresh')
}

/**
 * 获取插件状态
 */
export function getPluginStatus(pluginId?: string): Promise<PluginStatusData | { plugins: Record<string, PluginStatusData> }> {
  const url = pluginId ? `/plugin/status?plugin_id=${encodeURIComponent(pluginId)}` : '/plugin/status'
  return get(url)
}

/**
 * 获取插件健康状态
 */
export function getPluginHealth(pluginId: string): Promise<PluginHealth> {
  const safeId = encodeURIComponent(pluginId)
  return get(`/plugin/${safeId}/health`)
}

/**
 * 启动插件
 */
export function startPlugin(pluginId: string): Promise<{ success: boolean; plugin_id: string; message: string }> {
  const safeId = encodeURIComponent(pluginId)
  return post(`/plugin/${safeId}/start`)
}

/**
 * 停止插件
 */
export function stopPlugin(pluginId: string): Promise<{ success: boolean; plugin_id: string; message: string }> {
  const safeId = encodeURIComponent(pluginId)
  return post(`/plugin/${safeId}/stop`)
}

/**
 * 重载插件
 */
export function reloadPlugin(pluginId: string): Promise<{ success: boolean; plugin_id: string; message: string }> {
  const safeId = encodeURIComponent(pluginId)
  return post(`/plugin/${safeId}/reload`)
}

/**
 * 重载所有插件（批量 API，后端并行处理）
 */
export function reloadAllPlugins(): Promise<{
  success: boolean
  reloaded: string[]
  failed: { plugin_id: string; error: string }[]
  skipped: string[]
  message: string
}> {
  return post('/plugins/reload')
}

/**
 * 删除插件目录并刷新注册表
 */
export function deletePlugin(pluginId: string): Promise<{
  success: boolean
  plugin_id: string
  plugin_dir: string
  deleted_from_disk: boolean
  host_plugin_id?: string
  message: string
}> {
  const safeId = encodeURIComponent(pluginId)
  return del(`/plugin/${safeId}`)
}

/**
 * 获取插件消息
 */
export function getPluginMessages(params?: {
  plugin_id?: string
  max_count?: number
  priority_min?: number
}): Promise<{ messages: PluginMessage[]; count: number; time: string }> {
  return get('/plugin/messages', { params })
}

function normalizeSurface(raw: any, fallbackKind: PluginUiSurface['kind'] = 'panel'): PluginUiSurface | null {
  if (!raw || typeof raw !== 'object') return null
  const id = typeof raw.id === 'string' && raw.id.trim() ? raw.id.trim() : 'main'
  const kind = raw.kind === 'guide' || raw.kind === 'docs' || raw.kind === 'panel' ? raw.kind : fallbackKind
  const mode = raw.mode === 'hosted-tsx' || raw.mode === 'markdown' || raw.mode === 'auto' || raw.mode === 'static'
    ? raw.mode
    : 'static'
  return {
    id,
    kind,
    mode,
    title: typeof raw.title === 'string' ? raw.title : undefined,
    entry: typeof raw.entry === 'string' ? raw.entry : undefined,
    url: typeof raw.url === 'string' ? raw.url : undefined,
    ui_path: typeof raw.ui_path === 'string' ? raw.ui_path : undefined,
    open_in: raw.open_in === 'new_tab' || raw.open_in === 'same_tab' || raw.open_in === 'iframe' ? raw.open_in : undefined,
    context: typeof raw.context === 'string' ? raw.context : undefined,
    permissions: Array.isArray(raw.permissions) ? raw.permissions.filter((item: unknown) => typeof item === 'string') : undefined,
    available: typeof raw.available === 'boolean' ? raw.available : undefined,
  }
}

/**
 * 获取插件 UI surface 列表。优先使用未来统一 /surfaces 接口，
 * 当前后端未实现时回退到现有 /ui-info，把 static UI 归一化为 panel surface。
 */
export async function getPluginUiSurfaces(pluginId: string): Promise<PluginUiSurface[]> {
  const result = await getPluginUiSurfaceInfo(pluginId)
  return result.surfaces
}

export async function getPluginUiSurfaceInfo(pluginId: string): Promise<{
  surfaces: PluginUiSurface[]
  warnings: PluginUiWarning[]
}> {
  const safeId = encodeURIComponent(pluginId)
  try {
    const response = await get<{ surfaces?: any[]; warnings?: any[] } | any[]>(`/plugin/${safeId}/surfaces`)
    const rawSurfaces = Array.isArray(response) ? response : response?.surfaces
    const rawWarnings = Array.isArray(response) ? [] : response?.warnings
    if (Array.isArray(rawSurfaces)) {
      return {
        surfaces: rawSurfaces
        .map((surface) => normalizeSurface(surface))
          .filter((surface): surface is PluginUiSurface => !!surface),
        warnings: Array.isArray(rawWarnings)
          ? rawWarnings
            .filter((warning) => warning && typeof warning === 'object')
            .map((warning) => ({
              path: typeof warning.path === 'string' ? warning.path : 'plugin.ui',
              code: typeof warning.code === 'string' ? warning.code : 'ui_manifest_warning',
              message: typeof warning.message === 'string' ? warning.message : 'UI manifest warning',
            }))
          : [],
      }
    }
  } catch (caught: any) {
    const status = caught?.response?.status
    if (status !== 404 && status !== 405) {
      throw caught
    }
    // Older plugin servers expose only /ui-info; fall through to compatibility mode.
  }

  // LEGACY_STATIC_UI_COMPAT:
  // Existing plugins expose static/index.html through /plugin/{id}/ui-info.
  // Keep this fallback until backend surfaces normalize it as:
  // [[plugin.ui.panel]] mode = "static", entry = "static/index.html".
  try {
    const info = await get<PluginUiInfo>(`/plugin/${safeId}/ui-info`)
    if (!info?.has_ui) {
      return { surfaces: [], warnings: [] }
    }
    return {
      surfaces: [{
        id: 'main',
        kind: 'panel',
        mode: 'static',
        title: undefined,
        entry: 'static/index.html',
        url: info.ui_path || `/plugin/${safeId}/ui/`,
        ui_path: info.ui_path || `/plugin/${safeId}/ui/`,
        open_in: 'iframe',
        available: true,
      }],
      warnings: [],
    }
  } catch (caught: any) {
    const status = caught?.response?.status
    if (status === 404) {
      return { surfaces: [], warnings: [] }
    }
    throw caught
  }
}

export function getPluginHostedSurfaceSource(pluginId: string, params: {
  kind: PluginUiSurface['kind']
  id: string
}): Promise<{
  plugin_id: string
  kind: string
  surface_id: string
  mode: string
  entry: string
  source: string
  source_locale?: string
  translations?: Record<string, Record<string, string>>
  warnings?: PluginUiWarning[]
}> {
  const safeId = encodeURIComponent(pluginId)
  return get(`/plugin/${safeId}/hosted-ui/source`, {
    params: {
      kind: params.kind,
      id: params.id,
    },
  })
}

export function getPluginHostedSurfaceContext(pluginId: string, params: {
  kind: PluginUiSurface['kind']
  id: string
  locale?: string
}): Promise<PluginUiContext> {
  const safeId = encodeURIComponent(pluginId)
  return get(`/plugin/${safeId}/hosted-ui/context`, {
    params: {
      kind: params.kind,
      id: params.id,
      locale: params.locale,
    },
  })
}

export function callPluginHostedSurfaceAction(pluginId: string, actionId: string, args?: Record<string, any>, surface?: {
  kind: PluginUiSurface['kind']
  id: string
}): Promise<{
  plugin_id: string
  action_id: string
  result: any
}> {
  const safeId = encodeURIComponent(pluginId)
  const safeActionId = encodeURIComponent(actionId)
  return post(`/plugin/${safeId}/hosted-ui/action/${safeActionId}`, {
    args: args || {},
    kind: surface?.kind,
    surface_id: surface?.id,
  })
}

/**
 * 禁用 Extension（热切换）
 */
export function disableExtension(extId: string): Promise<{ success: boolean; ext_id: string; host_plugin_id: string; data?: any; message?: string }> {
  const safeId = encodeURIComponent(extId)
  return post(`/plugin/${safeId}/extension/disable`)
}

/**
 * 启用 Extension（热切换）
 */
export function enableExtension(extId: string): Promise<{ success: boolean; ext_id: string; host_plugin_id: string; data?: any; message?: string }> {
  const safeId = encodeURIComponent(extId)
  return post(`/plugin/${safeId}/extension/enable`)
}

/**
 * 获取服务器信息（包括SDK版本）
 */
export function getServerInfo(): Promise<{
  sdk_version: string
  plugins_count: number
  time: string
}> {
  return get('/server/info')
}
