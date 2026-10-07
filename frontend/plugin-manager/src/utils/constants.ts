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
// height 用 100%：面板高度由**宿主容器**决定，不再拿视口猜。此前是
// `calc(100vh - Npx)`，只要页头/卡片内边距/工具栏换行一变就算错——实测面板越界
// 77~180px，于是外层 .app-main 多出一条滚动条，和面板自己的内层滚动条并存
//（插件详情页的"双滚动条"）。容器给多高就多高，换行/告警条/横幅出现都自动正确。
//
// min 不做在这里，而是做在**宿主页面的根元素**上（见 PANEL_HOST_MIN_HEIGHT）：
// 面板只负责"填满容器"，下限交给宿主 —— 这样没有任何元素会溢出自己的盒子，
// 于是不需要任何 overflow:visible 打通，也就不存在"内容被裁"这一类风险。
// 宿主忘了给下限时，面板只会变小（仍可靠自身内层滚动使用），不会失控。
export const PANEL_FILL_HEIGHT = '100%'

// max 只做"宿主要了一个比一屏还高的盒子"时的兜底，不参与日常布局：
//   * 下限 520px 是为了**短窗**不算错（max-height 低于容器高度时就会去截它，
//     早先那条 520px 下限的用意就是这个），
//   * 上限跟着视口走（100vh - 220px），不再写死 1200px。写死那一版在
//     `100vh > 1420px` 的窗口里会先把面板截在 1200px：宿主盒 1303/1703px，
//     面板却是 1200px，卡片里空出 103 / 503px（1600 / 2000 高窗口实测）。
//     它和使用它的三个组件共存了很久，但那是"拿视口猜高度"年代的遗留值，
//     与现在"高度由容器决定"的契约相矛盾：宿主盒已经是视口内剩余高度，
//     固定上限只会在高屏上多留空白，永远垫不到什么。
// 正常宿主（.app-main 内容盒 ≈ 100vh - 180px，再减卡片/页头/tab 链的 chrome）
// 都比 100vh - 220px 矮，所以这个 max 在真实页面里不会生效；它只在那类
// "给了超过一屏高度"的宿主上兑底。
export const PANEL_MAX_HEIGHT = 'max(520px, calc(100vh - 220px))'

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
  [PluginStatus.RUNNING]: '#67C23A',
  [PluginStatus.STOPPED]: '#909399',
  [PluginStatus.CRASHED]: '#F56C6C',
  [PluginStatus.LOAD_FAILED]: '#F56C6C',
  [PluginStatus.LOADING]: '#409EFF',
  [PluginStatus.DISABLED]: '#909399',
  [PluginStatus.PENDING]: '#E6A23C'
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
