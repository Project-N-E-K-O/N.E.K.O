/**
 * 中文语言包
 */
export default {
  common: {
    loading: '加载中...',
    refresh: '刷新',
    search: '搜索',
    filter: '筛选',
    reset: '重置',
    confirm: '确认',
    cancel: '取消',
    save: '保存',
    delete: '删除',
    edit: '编辑',
    add: '添加',
    back: '返回',
    submit: '提交',
    close: '关闭',
    success: '成功',
    error: '错误',
    warning: '警告',
    info: '信息',
    noData: '暂无数据',
    unknown: '未知',
    nA: 'N/A'
  },
  nav: {
    dashboard: '仪表盘',
    plugins: '插件列表',
    metrics: '性能指标',
    logs: '日志',
    serverLogs: '服务器日志'
  },
  dashboard: {
    title: '仪表盘',
    pluginOverview: '插件概览',
    totalPlugins: '总插件数',
    running: '运行中',
    stopped: '已停止',
    crashed: '已崩溃',
    globalMetrics: '全局性能监控',
    totalCpuUsage: '总CPU使用率',
    totalMemoryUsage: '总内存使用',
    totalThreads: '总线程数',
    activePlugins: '活跃插件数',
    serverInfo: '服务器信息',
    sdkVersion: 'SDK 版本',
    updateTime: '更新时间',
    noMetricsData: '暂无性能数据',
    failedToLoadServerInfo: '无法加载服务器信息'
  },
  plugins: {
    title: '插件列表',
    name: '插件名称',
    id: '插件ID',
    version: '版本',
    description: '描述',
    status: '状态',
    sdkVersion: 'SDK版本',
    actions: '操作',
    start: '启动',
    stop: '停止',
    reload: '重载',
    viewDetails: '查看详情',
    noPlugins: '暂无插件',
    pluginNotFound: '插件不存在',
    pluginDetail: '插件详情',
    basicInfo: '基本信息',
    entries: '入口点',
    performance: '性能指标',
    logs: '日志',
    entryPoint: '入口点',
    entryName: '名称',
    entryId: 'ID',
    entryDescription: '描述',
    trigger: '触发',
    noEntries: '暂无入口点'
  },
  metrics: {
    title: '性能指标',
    pluginMetrics: '插件性能指标',
    cpuUsage: 'CPU使用率',
    memoryUsage: '内存使用',
    threads: '线程数',
    pid: '进程ID',
    noMetrics: '暂无性能数据',
    refreshInterval: '刷新间隔',
    seconds: '秒'
  },
  logs: {
    title: '日志',
    pluginLogs: '插件日志',
    serverLogs: '服务器日志',
    level: '级别',
    time: '时间',
    source: '来源',
    file: '文件',
    message: '消息',
    allLevels: '全部级别',
    noLogs: '暂无日志',
    autoScroll: '自动滚动',
    scrollToBottom: '滚动到底部',
    logFiles: '日志文件',
    selectFile: '选择文件'
  },
  status: {
    running: '运行中',
    stopped: '已停止',
    crashed: '已崩溃',
    loading: '加载中'
  },
  logLevel: {
    DEBUG: '调试',
    INFO: '信息',
    WARNING: '警告',
    ERROR: '错误',
    CRITICAL: '严重',
    UNKNOWN: '未知'
  },
  messages: {
    fetchFailed: '获取数据失败',
    operationSuccess: '操作成功',
    operationFailed: '操作失败',
    confirmDelete: '确认删除？',
    confirmStop: '确认停止插件？',
    confirmStart: '确认启动插件？',
    confirmReload: '确认重载插件？',
    pluginStarted: '插件启动成功',
    pluginStopped: '插件已停止',
    pluginReloaded: '插件重载成功',
    startFailed: '启动失败',
    stopFailed: '停止失败',
    reloadFailed: '重载失败'
  },
  welcome: {
    about: {
      title: '关于 N.E.K.O.',
      description: 'N.E.K.O. (Networked Emotional Knowing Organism) 是一个"活"的AI伙伴元宇宙，由你我共同构建。这是一个以开源为驱动、以公益为导向的UGC平台，致力于构建一个与现实世界紧密相连的AI原生元宇宙。'
    },
    pluginManagement: {
      title: '插件管理',
      description: '通过左侧导航栏访问插件列表，您可以查看、启动、停止和重载插件。每个插件都有独立的性能监控和日志查看功能，帮助您更好地管理和调试插件系统。'
    },
    mcpServer: {
      title: 'MCP 服务器',
      description: 'N.E.K.O. 支持 Model Context Protocol (MCP) 服务器，允许插件通过标准化的协议与其他AI系统和服务进行交互。您可以在插件详情页面查看和管理MCP连接。'
    },
    documentation: {
      title: '文档与资源',
      description: '查看项目文档了解更多信息：',
      links: '<a href="https://github.com/wehos/N.E.K.O" target="_blank" rel="noopener">GitHub 仓库</a>、<a href="https://store.steampowered.com/app/4099310/__NEKO/" target="_blank" rel="noopener">Steam 商店页面</a>、<a href="https://discord.gg/5kgHfepNJr" target="_blank" rel="noopener">Discord 社区</a>。',
      readme: 'README.md 文件：'
    },
    community: {
      title: '社区与支持',
      description: '加入我们的社区，与其他开发者和用户交流：',
      links: '<a href="https://discord.gg/5kgHfepNJr" target="_blank" rel="noopener">Discord 服务器</a>、<a href="https://qm.qq.com/q/hN82yFONJQ" target="_blank" rel="noopener">QQ 群</a>、<a href="https://github.com/wehos/N.E.K.O/issues" target="_blank" rel="noopener">GitHub Issues</a>。'
    }
  }
}

