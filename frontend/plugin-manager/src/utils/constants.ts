/**
 * 常量定义
 */

// API 基础配置
// 开发环境使用代理，生产环境使用完整 URL
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || (
  import.meta.env.DEV ? '' : window.location.origin
)
export const API_TIMEOUT = 30000 // 30秒
// 插件后端的启动超时为 30 秒；前端留出传输和错误序列化余量，
// 使后端的明确启动错误能先于 Axios 超时返回。
export const PLUGIN_LIFECYCLE_TIMEOUT = 45000
// 批量重载按依赖顺序等待每个插件完成；插件数量不定，不能用单插件期限截断整个操作。
export const PLUGIN_RELOAD_ALL_TIMEOUT = 0

// ── 面板尺寸契约（hosted surface / 插件 UI frame / 日志面板共用）──────────────
//
// 默认 height:100%，高度来自确定高度的宿主；调用方仍可显式传入自定义 height。
// 不再叠加视口 max-height：页头移除、工具栏换行或连接横幅都会改变剩余空间，
// `100vh - 常量` 既可能越界，也可能把正确的宿主截短、留下大块空白。
// 最小可用高度由宿主页面设置（PANEL_HOST_MIN_HEIGHT）；短窗口下页面外层滚动，
// 面板本身仅管理 iframe/日志/占位内容的内层滚动。
export const PANEL_FILL_HEIGHT = '100%'

/**
 * 撑满型面板的宿主页面根元素所需的最小高度。
 *
 * 取 440px = 面板约 300px + 卡片/标签链的 chrome 约 140px：
 * 既保证面板不会小到不可用（日志面板自身工具栏就要 ~140px），
 * 又让常见窗口（720p 可用高度 588px）不必出现"页面滚动条与面板内滚动条并存"。
 * 窗口不够高时，整张卡片保持这个高度、由 `.app-main` 滚动（与改动前一致，但幅度更小），
 * 而不是把这个下限压在面板上 —— 压在面板上就会面板溢出卡片、被 .el-card 默认的
 * `overflow:hidden` 裁掉底部（这条已由 src/e2e/plugin-detail-layout.e2e.ts 钉住）。
 */
export const PANEL_HOST_MIN_HEIGHT = '440px'

// 插件状态
export enum PluginStatus {
  RUNNING = 'running',
  STOPPED = 'stopped',
  CRASHED = 'crashed',
  LOAD_FAILED = 'load_failed',
  LOADING = 'loading',
  DISABLED = 'disabled',
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
  [PluginStatus.RUNNING]: 'var(--el-color-success)',
  [PluginStatus.STOPPED]: 'var(--el-color-info)',
  [PluginStatus.CRASHED]: 'var(--el-color-danger)',
  [PluginStatus.LOAD_FAILED]: 'var(--el-color-danger)',
  [PluginStatus.LOADING]: 'var(--el-color-primary)',
  [PluginStatus.DISABLED]: 'var(--el-color-info)',
  [PluginStatus.PENDING]: 'var(--el-color-warning)'
} as const

// 状态文本映射
export const STATUS_TEXT_KEYS = {
  [PluginStatus.RUNNING]: 'status.running',
  [PluginStatus.STOPPED]: 'status.stopped',
  [PluginStatus.CRASHED]: 'status.crashed',
  [PluginStatus.LOAD_FAILED]: 'status.loadFailed',
  [PluginStatus.LOADING]: 'status.loading',
  [PluginStatus.DISABLED]: 'status.disabled',
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
