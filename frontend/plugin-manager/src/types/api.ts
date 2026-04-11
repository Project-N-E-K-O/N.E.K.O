/**
 * API 相关类型定义
 */

// 通用响应结构
export interface ApiResponse<T = any> {
  success?: boolean
  data?: T
  message?: string
  error?: string
  time?: string
}

// 插件元数据
export interface PluginAuthor {
  name?: string
  email?: string
}

export interface PluginDependency {
  id?: string
  entry?: string
  custom_event?: string
  providers?: string[]
  recommended?: string
  supported?: string
  untested?: string
  conflicts?: string[] | boolean
}

export type PluginType = 'plugin' | 'extension' | 'script' | 'adapter'

export interface PluginMeta {
  id: string
  name: string
  type?: PluginType
  description: string
  version: string
  sdk_version?: string
  sdk_recommended?: string
  sdk_supported?: string
  sdk_untested?: string
  sdk_conflicts?: string[]
  entries?: PluginEntry[]
  runtime_enabled?: boolean
  runtime_auto_start?: boolean
  author?: PluginAuthor
  dependencies?: PluginDependency[]
  input_schema?: JSONSchema
  host_plugin_id?: string
  status?: string
}

// JSON Schema（简化版），用于描述插件入口参数
export interface JSONSchemaProperty {
  type?: string
  description?: string
  default?: any
}

export interface JSONSchema {
  type?: string
  properties?: Record<string, JSONSchemaProperty>
  required?: string[]
}

// 插件入口点
export interface PluginEntry {
  id: string
  name: string
  description: string
  input_schema?: JSONSchema
  return_message?: string
}

// 插件状态
export interface PluginStatusData {
  plugin_id: string
  status: {
    status?: string
    [key: string]: any
  }
  updated_at?: string
  source?: string
}

// 插件健康检查
export interface PluginHealth {
  alive: boolean
  exitcode?: number | null
  pid?: number | null
  status: 'running' | 'stopped' | 'crashed'
  communication?: {
    pending_requests?: number
    consumer_running?: boolean
  }
}

// 性能指标
export interface PluginMetrics {
  plugin_id: string
  timestamp: string
  pid?: number | null
  cpu_percent: number
  memory_mb: number
  memory_percent: number
  num_threads: number
  total_executions?: number
  successful_executions?: number
  failed_executions?: number
  avg_execution_time?: number
  pending_requests?: number
  queue_size?: number
}

// 插件消息
export interface PluginMessage {
  plugin_id: string
  source: string
  description: string
  priority: number
  message_type: 'text' | 'url' | 'binary' | 'binary_url'
  content?: string
  binary_data?: string
  binary_url?: string
  metadata?: Record<string, any>
  timestamp: string
  message_id: string
}

// 日志条目
export interface LogEntry {
  timestamp: string
  level: string
  file: string
  line: number
  message: string
}

// 日志文件信息
export interface LogFile {
  filename: string
  size: number
  modified: number
}

// 插件配置
export interface PluginConfig {
  plugin_id: string
  config: Record<string, any>
  last_modified: string
  config_path?: string
}

// 服务器信息
export interface ServerInfo {
  sdk_version: string
  plugins_count: number
  time: string
}

// 全局性能指标
export interface GlobalMetrics {
  total_cpu_percent: number
  total_memory_mb: number
  total_memory_percent: number
  total_threads: number
  active_plugins: number
}

// 性能指标响应
export interface MetricsResponse {
  metrics: PluginMetrics[]
  count: number
  global?: GlobalMetrics
  time: string
}

// 单个插件性能指标
export interface PluginMetricsResult {
  plugin_id: string
  metrics: PluginMetrics | null
  time: string
  message?: string
  plugin_running?: boolean
  process_alive?: boolean
}

// 插件性能指标历史
export interface PluginMetricsHistoryResult {
  plugin_id: string
  history: PluginMetrics[]
  count: number
  time: string
}
