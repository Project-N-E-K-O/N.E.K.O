/**
 * neko-plugin-cli 相关 API
 */
import { get, post } from './index'
import { API_BASE_URL } from '@/utils/constants'

export type PluginCliConflictStrategy = 'rename' | 'fail'
export type PluginCliPackMode = 'selected' | 'single' | 'bundle' | 'all'

export interface PluginCliPackRequest {
  mode: PluginCliPackMode
  plugin?: string
  plugins?: string[]
  out?: string
  target_dir?: string
  keep_staging?: boolean
  bundle_id?: string
  package_name?: string
  package_description?: string
  version?: string
}

export interface PluginCliPluginItem {
  plugin: string
  error: string
}

export interface PluginCliPackResult {
  plugin_id: string
  package_type: string
  plugin_ids: string[]
  package_name?: string
  version?: string
  package_path: string
  staging_dir?: string | null
  profile_files: string[]
  packaged_files: string[]
  payload_hash: string
  package_size_bytes: number
  packaged_file_count: number
  profile_file_count: number
}

export interface PluginCliPackResponse {
  packed: PluginCliPackResult[]
  packed_count: number
  failed: PluginCliPluginItem[]
  failed_count: number
  ok: boolean
}

export interface PluginCliPackageRef {
  package: string
}

export interface PluginCliInspectedPlugin {
  plugin_id: string
  archive_path: string
  has_plugin_toml: boolean
}

export interface PluginCliInspectResponse {
  package_path: string
  package_type: string
  package_id: string
  schema_version: string
  package_name: string
  package_description: string
  version: string
  metadata_found: boolean
  payload_hash: string
  payload_hash_verified: boolean | null
  plugins: PluginCliInspectedPlugin[]
  profile_names: string[]
  plugin_count: number
  profile_count: number
}

export interface PluginCliVerifyResponse extends PluginCliInspectResponse {
  ok: boolean
}

export interface PluginCliUnpackRequest {
  package: string
  plugins_root?: string
  profiles_root?: string
  on_conflict?: PluginCliConflictStrategy
}

export interface PluginCliUnpackedPlugin {
  source_folder: string
  target_plugin_id: string
  target_dir: string
  renamed: boolean
}

export interface PluginCliUnpackResponse {
  package_path: string
  package_type: string
  package_id: string
  plugins_root: string
  profiles_root?: string | null
  unpacked_plugins: PluginCliUnpackedPlugin[]
  profile_dir?: string | null
  metadata_found: boolean
  payload_hash: string
  payload_hash_verified: boolean | null
  conflict_strategy: PluginCliConflictStrategy
  unpacked_plugin_count: number
}

export interface PluginCliAnalyzeRequest {
  plugins: string[]
  current_sdk_version?: string
}

export interface PluginCliSharedDependency {
  name: string
  plugin_ids: string[]
  requirement_texts: Record<string, string>
  plugin_count: number
}

export interface PluginCliBundleSdkAnalysis {
  kind: string
  plugin_specifiers: Record<string, string>
  has_overlap: boolean
  matching_versions: string[]
  current_sdk_version: string
  current_sdk_supported_by_all: boolean | null
}

export interface PluginCliAnalyzeResponse {
  plugin_ids: string[]
  shared_dependencies: PluginCliSharedDependency[]
  common_dependencies: PluginCliSharedDependency[]
  sdk_supported_analysis?: PluginCliBundleSdkAnalysis | null
  sdk_recommended_analysis?: PluginCliBundleSdkAnalysis | null
  plugin_count: number
}

export interface PluginCliLocalPluginsResponse {
  plugins: string[]
  count: number
}

export interface PluginCliLocalPackageItem {
  name: string
  path: string
  suffix: string
  size_bytes: number
  modified_at: string
}

export interface PluginCliLocalPackagesResponse {
  packages: PluginCliLocalPackageItem[]
  count: number
  target_dir: string
}

/**
 * 列出当前本地可打包插件
 */
export function getPluginCliPlugins(): Promise<PluginCliLocalPluginsResponse> {
  return get('/plugin-cli/plugins')
}

/**
 * 列出当前 target 目录中的本地包
 */
export function getPluginCliPackages(): Promise<PluginCliLocalPackagesResponse> {
  return get('/plugin-cli/packages')
}

/**
 * 打包一个或多个插件
 */
export function packPluginCli(payload: PluginCliPackRequest): Promise<PluginCliPackResponse> {
  return post('/plugin-cli/pack', payload)
}

/**
 * 检查包内容
 */
export function inspectPluginPackage(payload: PluginCliPackageRef): Promise<PluginCliInspectResponse> {
  return post('/plugin-cli/inspect', payload)
}

/**
 * 校验包的 payload hash
 */
export function verifyPluginPackage(payload: PluginCliPackageRef): Promise<PluginCliVerifyResponse> {
  return post('/plugin-cli/verify', payload)
}

/**
 * 解压插件包或整合包
 */
export function unpackPluginPackage(payload: PluginCliUnpackRequest): Promise<PluginCliUnpackResponse> {
  return post('/plugin-cli/unpack', payload)
}

/**
 * 分析多个插件的整合包兼容性
 */
export function analyzePluginBundle(payload: PluginCliAnalyzeRequest): Promise<PluginCliAnalyzeResponse> {
  return post('/plugin-cli/analyze', payload)
}

// ── Upload & Download ─────────────────────────────────────────────────

export interface PluginCliUploadResult {
  name: string
  path: string
  size_bytes: number
  modified_at: string
}

export interface PluginCliUploadAndUnpackResult {
  upload: PluginCliUploadResult
  unpack: PluginCliUnpackResponse
}

/**
 * 上传插件包文件到服务器
 */
export function uploadPluginPackage(file: File): Promise<PluginCliUploadResult> {
  const formData = new FormData()
  formData.append('file', file)
  return post('/plugin-cli/upload', formData, {
    timeout: 120_000,
  })
}

/**
 * 上传插件包并立即解包安装
 */
export function uploadAndUnpackPlugin(
  file: File,
  options?: { onConflict?: PluginCliConflictStrategy },
): Promise<PluginCliUploadAndUnpackResult> {
  const formData = new FormData()
  formData.append('file', file)
  const params = new URLSearchParams()
  if (options?.onConflict) {
    params.set('on_conflict', options.onConflict)
  }
  const query = params.toString()
  const url = `/plugin-cli/upload-and-unpack${query ? `?${query}` : ''}`
  return post(url, formData, {
    timeout: 120_000,
  })
}

/**
 * 构建插件包下载 URL（用于浏览器直接下载）
 */
export function getPluginPackageDownloadUrl(packagePath: string): string {
  const params = new URLSearchParams({ package: packagePath })
  return `${API_BASE_URL}/plugin-cli/download?${params.toString()}`
}

/**
 * 触发浏览器下载插件包
 */
export function downloadPluginPackage(packagePath: string): void {
  const url = getPluginPackageDownloadUrl(packagePath)
  // Extract just the filename for the browser download hint
  const filename = packagePath.split('/').pop() || packagePath.split('\\').pop() || packagePath
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.style.display = 'none'
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
}
