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
    nA: 'N/A',
    darkMode: 'Dark Mode',
    lightMode: 'Light Mode',
    logoutConfirmTitle: 'Notice',
    disconnected: 'Server disconnected',
    languageAuto: 'Auto'
  },
  nav: {
    dashboard: 'Dashboard',
    plugins: 'Plugins',
    metrics: 'Metrics',
    logs: 'Logs',
    runs: 'Runs',
    serverLogs: 'Server Logs',
    adapters: 'Adapters',
    adapterUI: 'Adapter UI',
    packageManager: 'Package Manager'
  },
  auth: {
    unauthorized: 'Unauthorized access',
    forbidden: 'Access denied'
  },
  plugin: {
    addProfile: {
      prompt: 'Enter a new profile name',
      title: 'Add Profile',
      inputError: 'Name cannot be empty or whitespace only'
    },
    removeProfile: {
      confirm: 'Are you sure you want to delete profile "{name}"?',
      title: 'Delete Profile'
    }
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
    failedToLoadServerInfo: 'Failed to load server info',
    startTutorial: 'Tutorial Guide',
    tutorialHint: 'New to the plugin manager? Tap here and I will show you around.'
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
    reloadAll: 'Reload All',
    reloadAllConfirm: 'Are you sure you want to reload all {count} running plugins?',
    reloadAllSuccess: 'Successfully reloaded {count} plugins',
    reloadAllPartial: 'Reload completed: {success} succeeded, {fail} failed',
    viewDetails: 'View Details',
    noPlugins: 'No Plugins',
    adapterNotFound: 'Adapter not found',
    pluginNotFound: 'Plugin not found',
    pluginDetail: 'Plugin Detail',
    basicInfo: 'Basic Info',
    entries: 'Entry Points',
    performance: 'Performance',
    config: 'Config',
    logs: 'Logs',
    entryPoint: 'Entry Point',
    entryName: 'Name',
    entryId: 'ID',
    entryDescription: 'Description',
    trigger: 'Trigger',
    triggerSuccess: 'Trigger successful',
    triggerFailed: 'Trigger failed',
    noEntries: 'No Entry Points',
    showMetrics: 'Show Metrics',
    hideMetrics: 'Hide Metrics',
    filterPlaceholder: 'Filter plugins with text, pinyin, and is:/type:/has: rules',
    filterRules: 'Rules',
    filterRulesTitle: 'Filter Rules',
    filterRulesHint: 'Click a rule below to insert it into the query and combine it with normal text.',
    filterWhitelist: 'Whitelist',
    filterBlacklist: 'Blacklist',
    invalidRegex: 'Invalid regular expression',
    hoverToShowFilter: 'Hover to show filter',
    configPath: 'Config File',
    lastModified: 'Last Modified',
    configEditorPlaceholder: 'Please enter plugin config in TOML format',
    configInvalidToml: 'Invalid TOML format. Please fix it before saving.',
    configLoadFailed: 'Failed to load plugin config',
    configSaveFailed: 'Failed to save plugin config',
    configReloadTitle: 'Reload Required',
    configReloadPrompt: 'Config updated. Reload the plugin now to apply changes?',
    configApplyTitle: 'Apply Config',
    configHotUpdatePrompt: 'Config saved. Apply to running plugin now? (Hot update does not require restart)',
    hotUpdate: 'Hot Update',
    reloadPlugin: 'Restart Plugin',
    hotUpdateSuccess: 'Config hot-updated successfully',
    hotUpdatePartial: 'Config saved, but plugin is not running. Will take effect after start.',
    hotUpdateFailed: 'Hot update failed',
    formMode: 'Form',
    sourceMode: 'Source',
    formModeHint: 'This mode renders a form from the server-parsed config object. Use source mode for advanced TOML features (comments/formatting).',
    addField: 'Add Field',
    addItem: 'Add Item',
    fieldName: 'Field Name',
    fieldNameRequired: 'Field name is required',
    invalidFieldKey: 'Invalid field name',
    fieldType: 'Field Type',
    duplicateFieldKey: 'Field name already exists. Please choose another one.',
    profiles: 'Profiles',
    active: 'Active',
    diffPreview: 'Diff Preview',
    unsavedChangesWarning: 'You have unsaved changes. Switching plugins will discard them. Continue?',
    enabled: 'Enabled',
    disabled: 'Disabled',
    autoStart: 'Auto Start',
    manualStart: 'Manual Start',
    fetchFailed: 'Failed to fetch plugins',
    extension: 'Extension',
    pluginType: 'Type',
    pluginTypeNormal: 'Plugin',
    hostPlugin: 'Host Plugin',
    boundExtensions: 'Bound Extensions',
    pluginsSection: 'Plugins',
    adaptersSection: 'Adapters',
    extensionsSection: 'Extensions',
    typePlugin: 'Plugin',
    typeAdapter: 'Adapter',
    typeExtension: 'Extension',
    openPackageManager: 'Package Manager',
    closePackageManager: 'Hide Package Manager',
    packageManagerOpened: 'Package manager open',
    packageManagerSyncHint: 'The current filters and selected plugins are synced directly to the package manager panel.',
    multiSelect: 'Multi-select',
    exitMultiSelect: 'Exit Multi-select',
    selectedCount: '{count} selected',
    selectAllVisible: 'Select Visible',
    invertVisibleSelection: 'Invert Visible',
    clearSelection: 'Clear Selection',
    batchStartConfirm: 'Start {count} selected plugins?',
    batchStopConfirm: 'Stop {count} running plugins?',
    batchReloadConfirm: 'Reload {count} running plugins?',
    batchDeleteConfirm: 'Delete {count} selected plugins? This cannot be undone.',
    batchStartSuccess: 'Successfully started {count} plugins',
    batchStopSuccess: 'Successfully stopped {count} plugins',
    batchReloadSuccess: 'Successfully reloaded {count} plugins',
    batchDeleteSuccess: 'Successfully deleted {count} plugins',
    batchPartial: 'Completed: {success} succeeded, {fail} failed',
    batchNoStartable: 'No startable plugins in selection',
    batchNoStoppable: 'No running plugins in selection',
    batchNoReloadable: 'No running plugins in selection',
    import: 'Import',
    importing: 'Importing…',
    importSuccess: 'Imported {name}, unpacked {count} plugins',
    importFailed: 'Import failed',
    export: 'Export',
    exportSuccess: 'Exported {count} packages',
    exportFailed: 'Export failed',
    exportPackFailed: 'Packaging failed, unable to export',
    filterRuleGroups: {
      state: 'State',
      type: 'Type',
      meta: 'Metadata'
    },
    filterRuleLabels: {
      running: 'Running',
      stopped: 'Stopped',
      disabled: 'Disabled',
      selected: 'Selected',
      manual: 'Manual Start',
      auto: 'Auto Start',
      plugin: 'Plugin',
      adapter: 'Adapter',
      extension: 'Extension',
      ui: 'Has UI',
      entries: 'Has Entries',
      host: 'Has Host',
      name: 'By Name',
      id: 'By ID',
      hostTarget: 'By Host',
      version: 'By Version',
      entry: 'By Entry',
      author: 'By Author'
    },
    contextSections: {
      navigation: 'Browse',
      runtime: 'Runtime',
      plugin: 'Plugin Extras'
    },
    pack: 'Package Plugin',
    delete: 'Delete Plugin',
    disableExtension: 'Disable Extension',
    enableExtension: 'Enable Extension',
    dangerDialog: {
      title: 'Confirm Destructive Action',
      warningTitle: 'This action cannot be undone',
      deleteMessage: 'Deleting "{pluginName}" will remove its plugin directory and refresh the list immediately.',
      hint: 'To avoid accidental clicks, press and hold the button below to continue.',
      holdIdle: 'Press and hold to delete',
      holdActive: 'Keep holding to confirm…',
      loading: 'Deleting plugin...'
    },
    ui: {
      open: 'Open UI',
      title: 'UI',
      panel: 'Panel',
      guide: 'Guide',
      loading: 'Loading plugin UI...',
      loadError: 'Failed to load plugin UI',
      noUI: 'This plugin has no custom UI',
      hostedTsxPending: 'Hosted TSX rendering is coming soon',
      markdownPending: 'Markdown guide rendering is coming soon',
      autoPending: 'Auto-generated panels are coming soon',
      surfaceUnavailable: 'Surface unavailable',
      surfaceEntryMissing: 'The entry file declared by this surface does not exist. Check the entry path in plugin.toml.',
      surfaceWarnings: 'Plugin UI declaration needs attention',
      controlError: 'Plugin UI control error',
      hostedRuntimePending: 'The Vue container recognized this surface. TSX, Markdown, and Auto renderers will be connected in a later phase.'
    }
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
    seconds: 'seconds',
    cpu: 'CPU Usage',
    memory: 'Memory',
    memoryPercent: 'Memory %',
    pendingRequests: 'Pending Requests',
    totalExecutions: 'Total Executions',
    noData: 'No data'
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
    selectFile: 'Select File',
    search: 'Search logs...',
    lines: 'Lines',
    totalLogs: 'Total {count} logs',
    loadError: 'Failed to load logs: {error}',
    emptyFile: 'Log file is empty or does not exist',
    noMatches: 'No matching logs',
    logFile: 'Log File',
    totalLines: 'Total Lines',
    returnedLines: 'Returned Lines',
    connected: 'Connected',
    disconnected: 'Disconnected',
    connectionFailed: 'Log stream connection failed'
  },
  runs: {
    title: 'Runs',
    detail: 'Run Detail',
    wsDisconnected: 'Realtime connection is not established. Please check the server status.',
    noRuns: 'No runs',
    selectRun: 'Select a run to view details',
    runId: 'Run ID',
    status: 'Status',
    pluginId: 'Plugin ID',
    entryId: 'Entry',
    updatedAt: 'Updated At',
    createdAt: 'Created At',
    stage: 'Stage',
    message: 'Message',
    progress: 'Progress',
    error: 'Error',
    export: 'Export',
    exportType: 'Type',
    exportContent: 'Content',
    noExport: 'No export items',
    cancel: 'Cancel Run',
    cancelConfirmTitle: 'Cancel this run?',
    cancelConfirmMessage: 'Run ID: {runId}',
    cancelSuccess: 'Cancel requested'
  },
  packageManager: {
    resultDialog: {
      title: 'Package Results',
      subtitle: 'Keep the latest {count} execution results',
      empty: 'Package operation results will appear here',
      viewDetails: 'View details',
      detailTitle: 'Result Details',
      summaryTitle: 'Details',
      notesTitle: 'Notes',
      rawJsonTitle: 'Raw Result JSON',
      kinds: {
        pack: 'Pack',
        inspect: 'Inspect',
        verify: 'Verify',
        unpack: 'Unpack',
        analyze: 'Analyze',
      },
      inspect: {
        packageId: 'Package ID',
        packageType: 'Type',
        version: 'Version',
        schemaVersion: 'Schema',
        hashCheck: 'Hash Check',
        profiles: 'Profiles',
        packageTypes: {
          bundle: 'Bundle',
          plugin: 'Plugin Package',
        },
        hashStatus: {
          notChecked: 'Not checked',
          passed: 'Passed',
          failed: 'Failed',
        },
      },
      metrics: {
        pack: {
          type: 'Type',
          succeeded: 'Succeeded',
          failed: 'Failed',
          containsPlugins: 'Contains plugins',
          status: 'Status',
          complete: 'Completed',
          partialFailed: 'Partially failed',
        },
        inspect: {
          pluginCount: 'Plugin count',
          profileCount: 'Profiles',
          hash: 'Hash',
        },
        unpack: {
          processedPlugins: 'Processed plugins',
          conflictStrategy: 'Conflict strategy',
          hash: 'Hash',
        },
        analyze: {
          pluginCount: 'Plugin count',
          commonDependencies: 'Common dependencies',
          sharedDependencies: 'Shared dependencies',
        },
      },
      highlights: {
        pack: {
          bundlePluginId: 'Bundle ID',
          bundleName: 'Bundle name',
          bundleVersion: 'Bundle version',
          outputPath: 'Output path',
          firstPlugin: 'First plugin',
          latestPackagePath: 'Latest package path',
        },
        inspect: {
          packageId: 'Package ID',
          packageType: 'Package type',
          version: 'Version',
        },
        unpack: {
          packageId: 'Package ID',
          pluginsRoot: 'Plugins directory',
          profilesRoot: 'Profiles directory',
        },
        analyze: {
          currentSdk: 'Current SDK support',
          supported: 'supported',
          unsupported: 'not fully compatible',
          matchingVersions: 'Recommended combinations',
        },
      },
      list: {
        pluginPrefix: 'plugin:',
        profilePrefix: 'profile:',
        renamedSuffix: '(renamed)',
        arrow: '->',
      },
      warnings: {
        bundleNeedsTwoPlugins: 'A bundle should usually contain at least two plugins',
        verifyFailed: 'The package did not pass hash verification. Do not import it directly into a runtime environment.',
        inspectHashFailed: 'The current package hash check failed and the contents may have been modified.',
        analyzeSdkMismatch: 'The current SDK version is not supported by all plugins together.',
        analyzeSharedDependencies: 'Detected {count} shared dependencies. Check version constraints carefully when bundling.',
      },
    },
  },
  status: {
    running: 'Running',
    stopped: 'Stopped',
    crashed: 'Crashed',
    loadFailed: 'Load Failed',
    loading: 'Loading',
    disabled: 'Disabled',
    injected: 'Injected',
    pending: 'Pending Host'
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
    pluginPacked: 'Plugin packaged: {packageName}',
    pluginDeleted: 'Plugin deleted',
    startFailed: 'Failed to start',
    stopFailed: 'Failed to stop',
    reloadFailed: 'Failed to reload',
    packFailed: 'Failed to package plugin',
    deleteFailed: 'Failed to delete plugin',
    pluginDisabled: 'Plugin is disabled. Please enable it first.',
    pluginLoadFailed: 'Plugin load failed and cannot be started.',
    confirmDisableExt: 'Disable this extension? Its functionality will be unloaded from the host plugin.',
    extensionDisabled: 'Extension disabled',
    extensionEnabled: 'Extension enabled',
    disableExtFailed: 'Failed to disable extension',
    enableExtFailed: 'Failed to enable extension',
    requestFailed: 'Request failed',
    requestFailedWithStatus: 'Request failed ({status})',
    badRequest: 'Invalid request parameters',
    resourceNotFound: 'Requested resource not found',
    internalServerError: 'Internal server error',
    serviceUnavailable: 'Service unavailable',
    networkError: 'Network error. Please check your connection.'
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
      links: [
        { text: 'GitHub Repository', url: 'https://github.com/Project-N-E-K-O/N.E.K.O' },
        { text: 'Steam Store Page', url: 'https://store.steampowered.com/app/4099310/__NEKO/' },
        { text: 'Discord Community', url: 'https://discord.gg/5kgHfepNJr' }
      ],
      linkSeparator: ', ',
      linkLastSeparator: ', and ',
      readme: 'README.md file:',
      openFailed: 'Failed to open README.md in editor',
      openTimeout: 'Request timeout, failed to open README.md file',
      openError: 'Error occurred while opening README.md file'
    },
    community: {
      title: 'Community & Support',
      description: 'Join our community to connect with other developers and users:',
      links: [
        { text: 'Discord Server', url: 'https://discord.gg/5kgHfepNJr' },
        { text: 'QQ Group', url: 'https://qm.qq.com/q/hN82yFONJQ' },
        { text: 'GitHub Issues', url: 'https://github.com/Project-N-E-K-O/N.E.K.O/issues' }
      ],
      linkSeparator: ', ',
      linkLastSeparator: ', and '
    }
  },
  app: {
    titleSuffix: 'N.E.K.O Plugin Manager'
  },
  tutorial: {
    yuiGuide: {
      buttons: {
        skipChat: 'Not now',
        sayHello: 'Hello',
      },
      lines: {
        introActivationHint: 'Click here so I can start talking, nyan~!',
        introGreetingReply: "Gentle breeze, sunshine, and you showing up at just the right time. Nice to meet you, I'm Lin Youyi, please take care of me from now on, meow! I've written everything about this place into the beginner's guide! Consider it the first little gift of our encounter, please check it out!",
        introBasic: "Ooh, look at this shiny little button! Give it a click, and we can start chatting right away! Want to share today's news with me? Or just call my name? Come on and try it, I can't wait to hear your voice! Meow!",
        takeoverCaptureCursor: "Ta-da! The ultimate magic switch appears! Just tap right here, and I can stretch my little paws onto your keyboard and mouse! I'll help you type, help you open webpages... But, if that mouse pointer moves around, I might not be able to resist pouncing on it! Are you ready for my troublemaking... ah no, my help? Meow!",
        takeoverPluginPreviewHome: "It's not over yet! Hey, look, there are soooo many fun plugins here!",
        takeoverPluginPreviewDashboard: 'With them, not only can I read Bilibili danmaku, but I can also help you turn off the lights and turn on the AC... I am the omnipotent super cat god! Hehe!',
        takeoverSettingsPeekIntro: "Of course, if you want me to chat with you more, it's not entirely out of the question. Just prepare more dried fish for me, hehe. Alright, I'll stop teasing you, the settings are all in this gear icon.",
        takeoverSettingsPeekDetail: "Look, here you can put on my new clothes, give me a nice-sounding voice... Change to another catgirl or modify memories? Wait a minute! What are you doing? Don't tell me you want to replace me? Ahhhh no way! Close it, close it quick!",
        takeoverSettingsPeekDetailPart1: 'Look, here you can put on my new clothes, give me a nice-sounding voice... Change to another catgirl or modify memories?',
        takeoverSettingsPeekDetailPart2: "Wait a minute! What are you doing? Don't tell me you want to replace me? Ahhhh no way! Close it, close it quick!",
        takeoverReturnControl: "Alright, alright, I won't hog your computer anymore! Control is returned to you! But you're not allowed to click on weird settings when I'm not looking! I'll be in your care from now on!",
        interruptResistLight1: "Hey! Don't drag me, it's not your turn yet!",
        interruptResistLight3: "Wait a minute! It's not over yet, don't just interrupt me!",
        interruptAngryExit: "Human! You are really impolite! Since you want to operate it yourself so much, then go play with the cold screen by yourself! Hmph!",
        introPractice: "Now, try talking to me and see if we're perfectly in sync, nyan~!",
      },
    }
  },
  yuiTutorial: {
    title: 'Meow~ Welcome to the Plugin Manager!',
    welcome: 'This is where you manage all your plugins, nya~ You can browse, launch, and tweak them to make me even more powerful!',
    hint: 'Take your time and poke around a little, then tap the button below when you\'re done~',
    complete: 'All done, meow~',
    dismiss: 'Maybe later~',
    keyboardSkipHint: 'Press Enter or Space for the next step. This becomes active 0.5 seconds after each step starts.',
    steps: {
      start: {
        title: 'Start Here',
        body: 'Use this button to replay the plugin manager tour at any time. If you switch languages while it is running, the tour follows the new language.'
      },
      stats: {
        title: 'Plugin Overview',
        body: 'These cards summarize total, running, stopped, and crashed plugins so you can judge the plugin service state first.'
      },
      metrics: {
        title: 'Performance Monitor',
        body: 'This area shows CPU, memory, threads, and active plugin counts. Check it first when galgame OCR or Agent work feels slow.'
      },
      server: {
        title: 'Server Info',
        body: 'Here you can check SDK version, plugin count, and update time to confirm the backend plugin service is available.'
      },
      plugins: {
        title: 'Plugin List',
        body: 'Open Plugin Management to start, stop, reload, configure plugins, or open the galgame_plugin UI and guide.'
      },
      pluginWorkbench: {
        title: 'Plugin Workbench',
        body: 'This workspace groups regular plugins, adapters, and extensions. galgame_plugin, Danmaku, MCP, and other plugins live here.'
      },
      pluginFilters: {
        title: 'Search and Filters',
        body: 'Filter by name, state, type, or advanced rules. To find galgame_plugin quickly, search for galgame.'
      },
      pluginLayout: {
        title: 'View Layout',
        body: 'Switch between list, single, double, and compact layouts. Double or compact layouts reduce scrolling when there are many plugins.'
      },
      pluginContextMenu: {
        title: 'Right-click Actions',
        body: 'Right-click a plugin to open details, config, logs, UI, or guide, and to run start, stop, and reload actions.'
      },
      packageManager: {
        title: 'Package Manager',
        body: 'The package manager reuses current filters and multi-selection to create single-plugin packages or bundles, and to handle local packages.'
      },
      packageOperations: {
        title: 'Package Operations',
        body: 'Pack selected, single, or all plugins; build bundles; inspect and verify packages; unpack packages; or analyze bundle dependencies here.'
      },
      pluginDetail: {
        title: 'Plugin Details',
        body: 'The detail page contains plugin UI, guide, basic info, entries, metrics, configuration, and logs. galgame_plugin uses the UI tab as its main panel.'
      },
      pluginDetailActions: {
        title: 'Detail Actions',
        body: 'The top-right actions apply to the current plugin. For galgame_plugin debugging, confirm it is running before opening UI or logs.'
      },
      runs: {
        title: 'Runs',
        body: 'Runs show execution history and live status for plugin entry tasks, such as installing OCR dependencies, explaining lines, or summarizing scenes.'
      },
      runsList: {
        title: 'Run List',
        body: 'Select a task run on the left. After install, analysis, or Agent entries finish, use this list to review results.'
      },
      runsDetail: {
        title: 'Run Details',
        body: 'The detail panel shows stage, progress, errors, and exports. Cancel appears only for cancellable long-running tasks.'
      },
      logs: {
        title: 'Server Logs',
        body: 'Server logs show output from the whole plugin service. galgame_plugin-specific logs are also available from its detail page.'
      },
      logToolbar: {
        title: 'Log Filters',
        body: 'Filter by level, keyword, and line count, or toggle auto-scroll. Use the plugin ID as a keyword when debugging.'
      },
      logList: {
        title: 'Log List',
        body: 'Logs show time, source, level, and message. OCR, Memory Reader, Agent, and package-manager errors can usually be located here first.'
      }
    }
  }
}
