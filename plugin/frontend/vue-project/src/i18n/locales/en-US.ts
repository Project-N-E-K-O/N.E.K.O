/**
 * English language pack
 */
export default {
  common: {
    loading: 'Loading...',
    refresh: 'Refresh',
    search: 'Search',
    filter: 'Filter',
    reset: 'Reset',
    confirm: 'Confirm',
    cancel: 'Cancel',
    save: 'Save',
    delete: 'Delete',
    edit: 'Edit',
    add: 'Add',
    back: 'Back',
    submit: 'Submit',
    close: 'Close',
    success: 'Success',
    error: 'Error',
    warning: 'Warning',
    info: 'Info',
    noData: 'No Data',
    unknown: 'Unknown',
    nA: 'N/A'
  },
  nav: {
    dashboard: 'Dashboard',
    plugins: 'Plugins',
    metrics: 'Metrics',
    logs: 'Logs',
    serverLogs: 'Server Logs'
  },
  dashboard: {
    title: 'Dashboard',
    pluginOverview: 'Plugin Overview',
    totalPlugins: 'Total Plugins',
    running: 'Running',
    stopped: 'Stopped',
    crashed: 'Crashed',
    globalMetrics: 'Global Performance Monitoring',
    totalCpuUsage: 'Total CPU Usage',
    totalMemoryUsage: 'Total Memory Usage',
    totalThreads: 'Total Threads',
    activePlugins: 'Active Plugins',
    serverInfo: 'Server Info',
    sdkVersion: 'SDK Version',
    updateTime: 'Update Time',
    noMetricsData: 'No Performance Data',
    failedToLoadServerInfo: 'Failed to load server info'
  },
  plugins: {
    title: 'Plugins',
    name: 'Plugin Name',
    id: 'Plugin ID',
    version: 'Version',
    description: 'Description',
    status: 'Status',
    sdkVersion: 'SDK Version',
    actions: 'Actions',
    start: 'Start',
    stop: 'Stop',
    reload: 'Reload',
    viewDetails: 'View Details',
    noPlugins: 'No Plugins',
    pluginNotFound: 'Plugin not found',
    pluginDetail: 'Plugin Detail',
    basicInfo: 'Basic Info',
    entries: 'Entry Points',
    performance: 'Performance',
    logs: 'Logs',
    entryPoint: 'Entry Point',
    entryName: 'Name',
    entryId: 'ID',
    entryDescription: 'Description',
    trigger: 'Trigger',
    noEntries: 'No Entry Points'
  },
  metrics: {
    title: 'Metrics',
    pluginMetrics: 'Plugin Performance Metrics',
    cpuUsage: 'CPU Usage',
    memoryUsage: 'Memory Usage',
    threads: 'Threads',
    pid: 'Process ID',
    noMetrics: 'No Performance Data',
    refreshInterval: 'Refresh Interval',
    seconds: 'seconds'
  },
  logs: {
    title: 'Logs',
    pluginLogs: 'Plugin Logs',
    serverLogs: 'Server Logs',
    level: 'Level',
    time: 'Time',
    source: 'Source',
    file: 'File',
    message: 'Message',
    allLevels: 'All Levels',
    noLogs: 'No Logs',
    autoScroll: 'Auto Scroll',
    scrollToBottom: 'Scroll to Bottom',
    logFiles: 'Log Files',
    selectFile: 'Select File'
  },
  status: {
    running: 'Running',
    stopped: 'Stopped',
    crashed: 'Crashed',
    loading: 'Loading'
  },
  logLevel: {
    DEBUG: 'Debug',
    INFO: 'Info',
    WARNING: 'Warning',
    ERROR: 'Error',
    CRITICAL: 'Critical',
    UNKNOWN: 'Unknown'
  },
  messages: {
    fetchFailed: 'Failed to fetch data',
    operationSuccess: 'Operation successful',
    operationFailed: 'Operation failed',
    confirmDelete: 'Confirm delete?',
    confirmStop: 'Confirm stop plugin?',
    confirmStart: 'Confirm start plugin?',
    confirmReload: 'Confirm reload plugin?',
    pluginStarted: 'Plugin started successfully',
    pluginStopped: 'Plugin stopped',
    pluginReloaded: 'Plugin reloaded successfully',
    startFailed: 'Failed to start',
    stopFailed: 'Failed to stop',
    reloadFailed: 'Failed to reload'
  },
  welcome: {
    about: {
      title: 'About N.E.K.O.',
      description: 'N.E.K.O. (Networked Emotional Knowing Organism) is a "living" AI companion metaverse, built together by you and me. It is an open-source driven, charity-oriented UGC platform dedicated to building an AI-native metaverse closely connected to the real world.'
    },
    pluginManagement: {
      title: 'Plugin Management',
      description: 'Access the plugin list through the left navigation bar. You can view, start, stop, and reload plugins. Each plugin has independent performance monitoring and log viewing features to help you better manage and debug the plugin system.'
    },
    mcpServer: {
      title: 'MCP Server',
      description: 'N.E.K.O. supports Model Context Protocol (MCP) servers, allowing plugins to interact with other AI systems and services through standardized protocols. You can view and manage MCP connections in the plugin details page.'
    },
    documentation: {
      title: 'Documentation & Resources',
      description: 'Check out the project documentation for more information:',
      links: '<a href="https://github.com/wehos/N.E.K.O" target="_blank" rel="noopener">GitHub Repository</a>, <a href="https://store.steampowered.com/app/4099310/__NEKO/" target="_blank" rel="noopener">Steam Store Page</a>, and <a href="https://discord.gg/5kgHfepNJr" target="_blank" rel="noopener">Discord Community</a>.',
      readme: 'README.md file:'
    },
    community: {
      title: 'Community & Support',
      description: 'Join our community to connect with other developers and users:',
      links: '<a href="https://discord.gg/5kgHfepNJr" target="_blank" rel="noopener">Discord Server</a>, <a href="https://qm.qq.com/q/hN82yFONJQ" target="_blank" rel="noopener">QQ Group</a>, and <a href="https://github.com/wehos/N.E.K.O/issues" target="_blank" rel="noopener">GitHub Issues</a>.'
    }
  }
}

