/**
 * 中文语言包
 */
import { yuiGuideLocales } from './yuiGuide'

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
    nA: 'N/A',
    darkMode: '深色模式',
    lightMode: '浅色模式',
    logoutConfirmTitle: '提示',
    disconnected: '服务器已断开连接',
    languageAuto: '自动'
  },
  nav: {
    dashboard: '仪表盘',
    plugins: '插件管理',
    metrics: '性能指标',
    logs: '日志',
    runs: '运行记录',
    serverLogs: '服务器日志',
    adapters: '适配器',
    adapterUI: '适配器界面',
    packageManager: '包管理'
  },
  auth: {
    unauthorized: '未授权访问',
    forbidden: '拒绝访问'
  },
  plugin: {
    addProfile: {
      prompt: '请输入新的配置方案名称',
      title: '新增配置方案',
      inputError: '名称不能为空，且不能只包含空白字符'
    },
    removeProfile: {
      confirm: '确定要删除配置方案 "{name}" 吗？',
      title: '删除配置方案'
    }
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
    failedToLoadServerInfo: '无法加载服务器信息',
    startTutorial: '教程引导',
    tutorialHint: '第一次使用插件管理器？点这里让我带你快速认识一下。'
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
    reloadAll: '重载全部',
    reloadAllConfirm: '确认要重载所有 {count} 个运行中的插件吗？',
    reloadAllSuccess: '已成功重载 {count} 个插件',
    reloadAllPartial: '重载完成：{success} 个成功，{fail} 个失败',
    viewDetails: '查看详情',
    noPlugins: '暂无插件',
    adapterNotFound: '适配器不存在',
    pluginNotFound: '插件不存在',
    pluginDetail: '插件详情',
    basicInfo: '基本信息',
    entries: '入口点',
    performance: '性能指标',
    config: '配置',
    logs: '日志',
    entryPoint: '入口点',
    entryName: '名称',
    entryId: 'ID',
    entryDescription: '描述',
    trigger: '触发',
    triggerSuccess: '触发成功',
    triggerFailed: '触发失败',
    noEntries: '暂无入口点',
    showMetrics: '显示性能指标',
    hideMetrics: '隐藏性能指标',
    filterPlaceholder: '筛选插件（支持正则、拼音与 is:/type:/has: 规则）',
    filterRules: '规则',
    filterRulesTitle: '筛选规则',
    filterRulesHint: '点击下方规则可直接插入到查询框，支持与普通文本组合使用。',
    filterWhitelist: '白名单',
    filterBlacklist: '黑名单',
    invalidRegex: '正则表达式无效',
    hoverToShowFilter: '悬停以显示筛选',
    configPath: '配置文件',
    lastModified: '最后修改',
    configEditorPlaceholder: '请输入 TOML 格式的配置内容',
    configInvalidToml: 'TOML 格式无效，请检查后再保存',
    configLoadFailed: '加载插件配置失败',
    configSaveFailed: '保存插件配置失败',
    configReloadTitle: '需要重载',
    configReloadPrompt: '配置已更新，是否立即重载插件以使其生效？',
    configApplyTitle: '应用配置',
    configHotUpdatePrompt: '配置已保存。是否立即应用到运行中的插件？（热更新不需要重启插件）',
    hotUpdate: '热更新',
    reloadPlugin: '重启插件',
    hotUpdateSuccess: '配置已热更新成功',
    hotUpdatePartial: '配置已保存，但插件未运行，需要启动后生效',
    hotUpdateFailed: '热更新失败',
    formMode: '表单',
    sourceMode: '源码',
    formModeHint: '该模式基于后端解析的配置对象渲染表单。复杂 TOML 语法（如注释、格式化）请使用源码模式。',
    addField: '新增字段',
    addItem: '新增项',
    fieldName: '字段名',
    fieldNameRequired: '字段名不能为空',
    invalidFieldKey: '字段名不合法',
    fieldType: '字段类型',
    duplicateFieldKey: '字段名已存在，请换一个',
    profiles: '配置方案',
    active: '当前',
    diffPreview: '差异预览',
    unsavedChangesWarning: '你有未保存的更改，切换插件将丢失这些更改。是否继续？',
    enabled: '已启用',
    disabled: '已禁用',
    autoStart: '自动启动',
    manualStart: '手动启动',
    fetchFailed: '获取插件列表失败',
    extension: '扩展',
    pluginType: '类型',
    pluginTypeNormal: '插件',
    hostPlugin: '宿主插件',
    boundExtensions: '绑定扩展',
    pluginsSection: '插件',
    adaptersSection: '适配器',
    extensionsSection: '扩展',
    typePlugin: '插件',
    typeAdapter: '适配器',
    typeExtension: '扩展',
    openPackageManager: '包管理',
    closePackageManager: '收起包管理',
    packageManagerOpened: '包管理已展开',
    packageManagerSyncHint: '当前筛选和多选结果会直接同步到右侧包管理面板。',
    multiSelect: '多选',
    exitMultiSelect: '退出多选',
    selectedCount: '已选 {count} 项',
    selectAllVisible: '全选当前',
    invertVisibleSelection: '反选当前',
    clearSelection: '清空选择',
    batchStartConfirm: '确认批量启动 {count} 个插件？',
    batchStopConfirm: '确认批量停止 {count} 个运行中的插件？',
    batchReloadConfirm: '确认批量重载 {count} 个运行中的插件？',
    batchDeleteConfirm: '确认批量删除 {count} 个插件？此操作不可逆。',
    batchStartSuccess: '已成功启动 {count} 个插件',
    batchStopSuccess: '已成功停止 {count} 个插件',
    batchReloadSuccess: '已成功重载 {count} 个插件',
    batchDeleteSuccess: '已成功删除 {count} 个插件',
    batchPartial: '操作完成：{success} 个成功，{fail} 个失败',
    batchNoStartable: '选中的插件中没有可启动的',
    batchNoStoppable: '选中的插件中没有运行中的',
    batchNoReloadable: '选中的插件中没有运行中的',
    import: '导入',
    importing: '导入中…',
    importSuccess: '已导入 {name}，解包了 {count} 个插件',
    importFailed: '导入失败',
    export: '导出',
    exportSuccess: '已导出 {count} 个包',
    exportFailed: '导出失败',
    exportPackFailed: '打包失败，无法导出',
    filterRuleGroups: {
      state: '状态',
      type: '类型',
      meta: '元数据'
    },
    filterRuleLabels: {
      running: '运行中',
      stopped: '已停止',
      disabled: '已禁用',
      selected: '当前已选',
      manual: '手动启动',
      auto: '自动启动',
      plugin: '插件',
      adapter: '适配器',
      extension: '扩展',
      ui: '带界面',
      entries: '有入口点',
      host: '有宿主',
      name: '按名称',
      id: '按 ID',
      hostTarget: '按宿主',
      version: '按版本',
      entry: '按入口点',
      author: '按作者'
    },
    contextSections: {
      navigation: '浏览',
      runtime: '运行',
      plugin: '扩展功能'
    },
    pack: '打包插件',
    delete: '删除插件',
    disableExtension: '禁用扩展',
    enableExtension: '启用扩展',
    dangerDialog: {
      title: '危险操作确认',
      warningTitle: '不可逆操作',
      deleteMessage: '删除插件“{pluginName}”后，其目录会被移除，当前列表也会立即刷新。',
      hint: '为避免误触，请按住下方按钮完成确认。',
      holdIdle: '按住以确认删除',
      holdActive: '继续按住，正在确认…',
      loading: '正在删除插件…'
    },
    ui: {
      open: '打开界面',
      panel: '面板',
      guide: '教程',
      loading: '加载插件界面中...',
      loadError: '加载插件界面失败',
      noUI: '该插件没有自定义界面',
      hostedTsxPending: 'Hosted TSX 渲染即将支持',
      markdownPending: 'Markdown 教程渲染即将支持',
      autoPending: '自动生成面板即将支持',
      surfaceUnavailable: 'Surface 暂不可用',
      surfaceEntryMissing: '该 Surface 声明的入口文件不存在，请检查 plugin.toml 中的 entry 路径。',
      surfaceWarnings: '插件 UI 声明存在需要处理的问题',
      controlError: '插件界面控件错误',
      hostedRuntimePending: '前端容器已经识别到该 Surface。TSX/Markdown/Auto 渲染器会在后续阶段接入。'
    }
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
    seconds: '秒',
    cpu: 'CPU使用率',
    memory: '内存使用',
    memoryPercent: '内存占比',
    pendingRequests: '待处理请求',
    totalExecutions: '总执行次数',
    noData: '暂无数据'
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
    selectFile: '选择文件',
    search: '搜索日志...',
    lines: '行数',
    totalLogs: '共 {count} 条',
    loadError: '无法加载日志：{error}',
    emptyFile: '日志文件为空或不存在',
    noMatches: '没有匹配的日志',
    logFile: '日志文件',
    totalLines: '总行数',
    returnedLines: '返回行数',
    connected: '已连接',
    disconnected: '未连接',
    connectionFailed: '日志流连接失败'
  },
  runs: {
    title: '运行记录',
    detail: '运行详情',
    wsDisconnected: '实时连接未建立，请检查服务器状态',
    noRuns: '暂无运行记录',
    selectRun: '请选择一条运行记录',
    runId: 'Run ID',
    status: '状态',
    pluginId: '插件ID',
    entryId: '入口',
    updatedAt: '更新时间',
    createdAt: '创建时间',
    stage: '阶段',
    message: '消息',
    progress: '进度',
    error: '错误',
    export: '导出',
    exportType: '类型',
    exportContent: '内容',
    noExport: '暂无导出内容',
    cancel: '取消运行',
    cancelConfirmTitle: '确认取消运行？',
    cancelConfirmMessage: 'Run ID: {runId}',
    cancelSuccess: '已发送取消请求'
  },
  status: {
    running: '运行中',
    stopped: '已停止',
    crashed: '已崩溃',
    loadFailed: '加载失败',
    loading: '加载中',
    disabled: '已禁用',
    injected: '已注入',
    pending: '等待宿主'
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
    pluginPacked: '插件已打包：{packageName}',
    pluginDeleted: '插件已删除',
    startFailed: '启动失败',
    stopFailed: '停止失败',
    reloadFailed: '重载失败',
    packFailed: '打包插件失败',
    deleteFailed: '删除插件失败',
    pluginDisabled: '插件已禁用，请先启用',
    pluginLoadFailed: '插件加载失败，当前不可启动',
    confirmDisableExt: '确认禁用此扩展？宿主插件中的扩展功能将被卸载。',
    extensionDisabled: '扩展已禁用',
    extensionEnabled: '扩展已启用',
    disableExtFailed: '禁用扩展失败',
    enableExtFailed: '启用扩展失败',
    requestFailed: '请求失败',
    requestFailedWithStatus: '请求失败 ({status})',
    badRequest: '请求参数错误',
    resourceNotFound: '请求的资源不存在',
    internalServerError: '服务器内部错误',
    serviceUnavailable: '服务不可用',
    networkError: '网络错误，请检查网络连接'
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
      links: [
        { text: 'GitHub 仓库', url: 'https://github.com/Project-N-E-K-O/N.E.K.O' },
        { text: 'Steam 商店页面', url: 'https://store.steampowered.com/app/4099310/__NEKO/' },
        { text: 'Discord 社区', url: 'https://discord.gg/5kgHfepNJr' }
      ],
      linkSeparator: '、',
      linkLastSeparator: '',
      readme: 'README.md 文件：',
      openFailed: '无法在编辑器中打开 README.md 文件',
      openTimeout: '请求超时，无法打开 README.md 文件',
      openError: '打开 README.md 文件时发生错误'
    },
    community: {
      title: '社区与支持',
      description: '加入我们的社区，与其他开发者和用户交流：',
      links: [
        { text: 'Discord 服务器', url: 'https://discord.gg/5kgHfepNJr' },
        { text: 'QQ 群', url: 'https://qm.qq.com/q/hN82yFONJQ' },
        { text: 'GitHub Issues', url: 'https://github.com/Project-N-E-K-O/N.E.K.O/issues' }
      ],
      linkSeparator: '、',
      linkLastSeparator: ''
    }
  },
  app: {
    titleSuffix: 'N.E.K.O 插件管理'
  },
  tutorial: {
    yuiGuide: yuiGuideLocales['zh-CN']
  },
  yuiTutorial: {
    title: '喵～欢迎来到插件管理面板！',
    welcome: '这里就是管理所有插件的地方啦！你可以查看、启动、配置各种插件，让我变得更厉害哦～',
    hint: '随便看看吧，看完了点下面的按钮告诉我～',
    complete: '看完了喵～',
    dismiss: '先不看',
    keyboardSkipHint: '按 Enter 或空格进入下一步，每步开始后 0.5 秒生效。',
    steps: {
      start: {
        title: '从这里开始',
        body: '点这个按钮就可以随时重新播放插件管理器的教程，不会自动打扰你喵。'
      },
      stats: {
        title: '插件总览',
        body: '这里会显示插件总数、运行中、已停止和崩溃数量，让你一眼看出当前状态。'
      },
      metrics: {
        title: '性能监控',
        body: '这里展示插件服务整体的 CPU、内存、线程和活跃插件情况，排查问题时很有用。'
      },
      server: {
        title: '服务器信息',
        body: '这里可以看到 SDK 版本、插件数量和更新时间，用来确认当前插件服务是否正常。'
      },
      plugins: {
        title: '插件列表入口',
        body: '要启动、停止、配置插件，或者查看单个插件日志，就从左侧的插件管理进入。'
      },
      pluginWorkbench: {
        title: '插件管理工作台',
        body: '这里集中展示插件、适配器和扩展，是日常管理插件的主要页面。'
      },
      pluginFilters: {
        title: '筛选和搜索',
        body: '可以按名称、状态、类型或高级规则筛选插件，插件很多时会特别好用。'
      },
      pluginLayout: {
        title: '视图布局',
        body: '这里可以切换列表、单排、双排和紧凑布局，按你的屏幕空间调整显示方式。'
      },
      pluginContextMenu: {
        title: '右键操作',
        body: '对插件右键可以快速打开详情、配置、日志，也能执行启停、重载等常用操作。'
      },
      packageManager: {
        title: '包管理侧栏',
        body: '包管理会复用当前筛选和选择结果，用来打包、检查、校验或解包插件包。'
      },
      packageOperations: {
        title: '包管理操作区',
        body: '这里可以选择打包模式、检查包、解包或分析整合包；危险操作不会在教程中自动执行。'
      },
      pluginDetail: {
        title: '插件详情页',
        body: '进入详情页后可以查看插件元信息、入口点、性能、配置和日志。'
      },
      pluginDetailActions: {
        title: '详情页操作',
        body: '右上角保留了针对当前插件的快捷操作，适合在确认详情后再启动、停止或重载。'
      },
      runs: {
        title: '运行记录',
        body: '运行记录会展示插件入口任务的执行历史和实时状态。'
      },
      runsList: {
        title: '运行列表',
        body: '左侧列表用于选择某次运行，刷新按钮可以重新同步最新记录。'
      },
      runsDetail: {
        title: '运行详情',
        body: '右侧会显示阶段、进度、错误和导出物；取消按钮只对可取消任务出现。'
      },
      logs: {
        title: '服务器日志',
        body: '服务器日志可以帮助你查看插件服务本身的输出和错误。'
      },
      logToolbar: {
        title: '日志筛选工具',
        body: '这里可以按级别、关键词和行数筛选日志，也可以控制是否自动滚动。'
      },
      logList: {
        title: '日志列表',
        body: '日志列表按时间展示来源、级别和消息，是排查插件问题的第一站。'
      }
    }
  }
}
