/**
 * neko-plugin-cli 相关 API
 */
import { get, post } from './index'
import { API_BASE_URL } from '@/utils/constants'

export type PluginCliConflictStrategy = 'fail'
export type PluginCliBuildMode = 'selected' | 'single' | 'bundle' | 'all'
export type PluginCliInstallAction = 'install' | 'upgrade' | 'blocked'

export interface PluginCliPluginRef {
  root_id: 'builtin' | 'user'
  directory_name: string
  plugin_id?: string
  label?: string
}

export interface PluginCliBuildRequest {
  mode: PluginCliBuildMode
  plugin?: string
  plugins?: string[]
  plugin_ref?: PluginCliPluginRef
  plugin_refs?: PluginCliPluginRef[]
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

export interface PluginCliBuildResult {
  plugin_id: string
  package_type: string
  plugin_ids: string[]
  package_name?: string
  version?: string
  package_path: string
  staging_dir?: string | null
  profile_files: string[]
  staged_files: string[]
  payload_hash: string
  package_size_bytes: number
  staged_file_count: number
  profile_file_count: number
}

export interface PluginCliBuildResponse {
  built: PluginCliBuildResult[]
  built_count: number
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

export interface PluginCliInstallRequest {
  package: string
  plugins_root?: string
  profiles_root?: string
  on_conflict?: PluginCliConflictStrategy
  confirm_upgrade?: boolean
  confirmation_token?: string
}

export interface PluginCliInstallPlanRequest {
  package: string
  plugins_root?: string
  profiles_root?: string
}

export interface PluginCliInstallPlanResponse {
  action: PluginCliInstallAction
  package_type: 'plugin' | 'bundle'
  plugin_id: string
  directory_name: string
  current_version: string
  target_version: string
  confirmation_token: string
  reason: string
  legacy_plugin_ids: string[]
}

export interface PluginCliInstalledPlugin {
  source_folder: string
  target_plugin_id: string
  target_dir: string
  renamed: boolean
}

export interface PluginCliInstallResponse {
  package_path: string
  package_type: string
  package_id: string
  plugins_root: string
  profiles_root?: string | null
  installed_plugins: PluginCliInstalledPlugin[]
  profile_dir?: string | null
  metadata_found: boolean
  payload_hash: string
  payload_hash_verified: boolean | null
  conflict_strategy: PluginCliConflictStrategy
  installed_plugin_count: number
  operation: 'install' | 'upgrade'
  restarted: boolean
  rollback_status: 'not_needed' | 'completed' | 'incomplete'
}

export interface PluginCliAnalyzeRequest {
  plugins?: string[]
  plugin_refs?: PluginCliPluginRef[]
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
  plugin_refs?: PluginCliPluginRef[]
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
 * 列出当前本地可构建插件
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
 * 构建一个或多个插件
 */
export function buildPluginCli(payload: PluginCliBuildRequest): Promise<PluginCliBuildResponse> {
  return post('/plugin-cli/build', payload)
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
 * 安装插件包或整合包
 */
export function installPluginPackage(payload: PluginCliInstallRequest): Promise<PluginCliInstallResponse> {
  return post('/plugin-cli/install', payload)
}

/**
 * 检查本地包将执行首次安装、原位升级，还是因冲突被阻止
 */
export function planPluginInstall(payload: PluginCliInstallPlanRequest): Promise<PluginCliInstallPlanResponse> {
  return post('/plugin-cli/install-plan', payload)
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

export interface PluginCliUploadAndInstallResult {
  upload: PluginCliUploadResult
  install: PluginCliInstallResponse
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
 * 上传插件包并立即安装
 */
export function uploadAndInstallPlugin(
  file: File,
  options?: { onConflict?: PluginCliConflictStrategy },
): Promise<PluginCliUploadAndInstallResult> {
  const formData = new FormData()
  formData.append('file', file)
  const params = new URLSearchParams()
  if (options?.onConflict) {
    params.set('on_conflict', options.onConflict)
  }
  const query = params.toString()
  const url = `/plugin-cli/upload-and-install${query ? `?${query}` : ''}`
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
