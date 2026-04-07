/**
 * neko-plugin-cli 相关 API
 */
import { get, post } from './index'

export type PluginCliConflictStrategy = 'rename' | 'fail'

export interface PluginCliPackRequest {
  plugin?: string
  pack_all?: boolean
  out?: string
  target_dir?: string
  keep_staging?: boolean
}

export interface PluginCliPluginItem {
  plugin: string
  error: string
}

export interface PluginCliPackResult {
  plugin_id: string
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

/**
 * 列出当前本地可打包插件
 */
export function getPluginCliPlugins(): Promise<PluginCliLocalPluginsResponse> {
  return get('/plugin-cli/plugins')
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
