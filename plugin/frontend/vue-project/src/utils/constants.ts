/**
 * 常量定义
 */

// API 基础配置
// 开发环境使用代理，生产环境使用完整 URL
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || (
  import.meta.env.DEV ? '' : window.location.origin
)
export const API_TIMEOUT = 30000 // 30秒

// 插件状态
export enum PluginStatus {
  RUNNING = 'running',
  STOPPED = 'stopped',
  CRASHED = 'crashed',
  LOADING = 'loading',
  DISABLED = 'disabled',
  INJECTED = 'injected',
  PENDING = 'pending'
}

// 日志级别
export enum LogLevel {
  DEBUG = 'DEBUG',
  INFO = 'INFO',
  WARNING = 'WARNING',
  ERROR = 'ERROR',
  CRITICAL = 'CRITICAL'
}

// 消息类型
export enum MessageType {
  TEXT = 'text',
  URL = 'url',
  BINARY = 'binary',
  BINARY_URL = 'binary_url'
}

// 状态颜色映射
export const STATUS_COLORS = {
  [PluginStatus.RUNNING]: '#67C23A',
  [PluginStatus.STOPPED]: '#909399',
  [PluginStatus.CRASHED]: '#F56C6C',
  [PluginStatus.LOADING]: '#409EFF',
  [PluginStatus.DISABLED]: '#909399',
  [PluginStatus.INJECTED]: '#67C23A',
  [PluginStatus.PENDING]: '#E6A23C'
} as const

// 状态文本映射
export const STATUS_TEXT_KEYS = {
  [PluginStatus.RUNNING]: 'status.running',
  [PluginStatus.STOPPED]: 'status.stopped',
  [PluginStatus.CRASHED]: 'status.crashed',
  [PluginStatus.LOADING]: 'status.loading',
  [PluginStatus.DISABLED]: 'status.disabled',
  [PluginStatus.INJECTED]: 'status.injected',
  [PluginStatus.PENDING]: 'status.pending'
} as const

// 日志级别颜色映射
export const LOG_LEVEL_COLORS = {
  [LogLevel.DEBUG]: '#909399',
  [LogLevel.INFO]: '#409EFF',
  [LogLevel.WARNING]: '#E6A23C',
  [LogLevel.ERROR]: '#F56C6C',
  [LogLevel.CRITICAL]: '#F56C6C'
} as const

// 分页配置
export const PAGINATION = {
  DEFAULT_PAGE_SIZE: 20,
  PAGE_SIZE_OPTIONS: [10, 20, 50, 100]
} as const

// 性能指标刷新间隔（毫秒）
export const METRICS_REFRESH_INTERVAL = 5000

// 日志刷新间隔（毫秒）
export const LOGS_REFRESH_INTERVAL = 3000

