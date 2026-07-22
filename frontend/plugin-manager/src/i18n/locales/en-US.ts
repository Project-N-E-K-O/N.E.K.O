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
    minimize: 'Minimize',
    maximize: 'Maximize',
    restore: 'Restore',
    pinWindow: 'Pin window',
    unpinWindow: 'Unpin window',
    toggleSelection: 'Toggle selection',
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
    packageManager: 'Package Manager',
    market: 'Plugin Market'
  },
  market: {
    title: 'Get New Plugins',
    subtitle: 'Browse and install plugins from the marketplace',
    getNewPlugins: 'Get New Plugins',
    openMarket: 'Open Plugin Market',
    closeMarket: 'Close Plugin Market',
    openInBrowser: 'Open in browser',
    account: 'Market account',
    accountConnected: 'Connected: {name}',
    login: 'Login',
    loginStarted: "We've opened your browser. Please finish the authorization login on the page that opened.",
    loginSuccess: 'Your authorization login is complete. You can keep using the plugin market.',
    loginFailed: 'The authorization login was not completed. Please try again later.',
    loginPending: 'The authorization login is taking a little too long. Please start it again.',
    logoutSuccess: "You've signed out of the authorization login on this device",
    searchPlaceholder: 'Search plugins...',
    notConfigured: 'Plugin market not configured',
    configHint: 'Set NEKO_MARKET_API_URL to the plugin market service address',
    noResults: 'No plugins found',
    loadFailed: 'Failed to load the plugin market. Please try again.',
    retry: 'Retry',
    install: 'Install',
    installed: 'Installed',
    installing: 'Installing...',
    installSuccess: 'Installed: {name}',
    installFailed: 'Install failed',
    installPreparing: 'Preparing install...',
    installDialogTitle: 'Installing {name}',
    installDialogTitleUpgrade: 'Upgrading {name}',
    installCompleted: 'Install completed',
    installCompletedUpgrade: 'Upgrade completed',
    rollbackRunning: 'Install failed; rolling back...',
    rollbackCompleted: 'Rolled back to the previous version',
    installStage: {
      pending: 'Preparing',
      download: 'Downloading',
      verify: 'Verifying',
      install: 'Installing',
      stop_old: 'Stopping old version',
      backup_old: 'Backing up',
      restart: 'Starting new version',
      rollback: 'Rolling back',
      completed: 'Completed',
      failed: 'Failed',
    },
    noDownloadUrl: 'No download URL available',
    pairRequired: 'Bridge Token pairing required',
    recommended: 'Recommended',
    allPlugins: 'All plugins',
    noDescription: 'No description',
    unknownAuthor: 'Unknown',
    filterRules: 'Filters',
    filterRulesTitle: 'Search syntax',
    filterRulesHint: 'Click a rule to append it. Supports key:value tokens, prefix with - to exclude.',
    filterGroups: {
      state: 'State',
      zone: 'Zone',
      meta: 'Metadata'
    },
    filterLabels: {
      recommended: 'Recommended',
      installed: 'Installed',
      uninstalled: 'Not installed',
      tag: 'Tag',
      author: 'Author',
      name: 'Name',
      versionGte: 'Version ≥',
      hasRepo: 'Has repo',
      hasTags: 'Has tags'
    },
    zones: {
      game: 'Game',
      companion: 'Companion',
      function: 'Function',
      entertainment: 'Entertainment',
      tool: 'Tool'
    },
    sortNewest: 'Newest',
    sortMostDownloads: 'Downloads',
    sortTopRated: 'Top rated',
    sortName: 'Name',
    upgrading: 'Upgrading...',
    upgradeTo: 'Upgrade to v{version}',
    upgradeSuccess: 'Upgraded: {name}',
    yanked: 'Yanked',
    yankedDefault: 'This version has been yanked by its author',
    noVersionAvailable: 'No release available',
    upgradeRollback: 'Upgrade failed; rolled back to previous version',
    upgradeAlreadyAtTarget: 'Already at the target version',
    upgradeTargetNotGreater: 'Upgrade target is not greater than the installed version',
    pluginNotInstalled: 'Plugin is not installed; cannot upgrade',
    lockWriteFailed: 'Failed to write install record'
  },
  settings: {
    channel: 'Update channel',
    channelStable: 'Stable',
    channelBeta: 'Beta',
    channelHint: 'Switching refreshes the plugin list with the selected channel; installed plugins keep running'
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
    showSourceDetail: 'Show Source Details',
    hideSourceDetail: 'Hide Source Details',
    installSource: {
      channel: {
        builtin: 'Built-in',
        manual: 'Manual',
        imported: 'Imported',
        market: 'Market',
        unknown: 'Unknown',
      },
      // v2: Market release channel values displayed on SourceDetailRow.
      channelLabels: {
        stable: 'Stable',
        beta: 'Beta',
        unknown: 'Unknown',
      },
      updateAvailable: 'Update available',
      labels: {
        installedAt: 'Installed',
        packageFilename: 'Package',
        sha256: 'SHA-256',
        marketId: 'Market ID',
        version: 'Version',
        previousVersion: 'Previous',
        latestAvailable: 'Latest available',
        channel: 'Channel',
      },
    },
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
    pluginType: 'Type',
    pluginTypeNormal: 'Plugin',
    hostPlugin: 'Host Plugin',
    pluginsSection: 'Plugins',
    adaptersSection: 'Adapters',
    typePlugin: 'Plugin',
    typeAdapter: 'Adapter',
    layoutList: 'List',
    layoutSingle: 'Single',
    layoutDouble: 'Double',
    layoutCompact: 'Compact',
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
    importSuccess: 'Imported {name}, installed {count} plugins',
    importFailed: 'Import failed',
    export: 'Export',
    exportSuccess: 'Exported {count} packages',
    exportFailed: 'Export failed',
    exportBuildFailed: 'Build failed, unable to export',
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
      ui: 'Has UI',
      entries: 'Has Entries',
      name: 'By Name',
      id: 'By ID',
      version: 'By Version',
      entry: 'By Entry',
      author: 'By Author'
    },
    contextSections: {
      navigation: 'Browse',
      runtime: 'Runtime',
      plugin: 'Plugin Extras'
    },
    build: 'Build Plugin',
    delete: 'Delete Plugin',
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
  package: {
    install: {
      safeUpgradeHint: 'Importing the same plugin again checks its version and performs a rollback-safe replacement without creating suffixed copies.',
      planFailed: 'Could not inspect the installation package.',
      upgradeTitle: 'Upgrade {plugin}?',
      upgradeBody: 'Version {current} will be replaced by {target}. A running plugin will restart briefly.',
      upgradeConfirm: 'Upgrade plugin',
      upgradeCancelled: 'Upgrade cancelled.',
      upgradeSucceeded: '{plugin} was upgraded successfully.',
      blockedBundleConflict: 'This bundle contains an installed plugin. Upgrade its plugins one at a time.',
      blockedDirectoryConflict: 'The destination directory belongs to another plugin and was not changed.',
      blockedLegacyPlugin: 'An earlier version of this plugin is still installed. Uninstall {plugin} before continuing.',
      rollbackCompleted: 'The upgrade failed and the previous version was restored.',
      rollbackIncomplete: 'The upgrade failed and rollback was incomplete. Check the plugin state before continuing.'
    },
    dialog: {
      title: 'Package operation history',
      subtitle: 'Showing the latest {count} result(s)'
    },
    empty: 'Run a package operation to see records here.',
    viewDetail: 'View details',
    detail: {
      title: 'Result detail',
      field: {
        packageId: 'Package ID',
        kind: 'Type',
        version: 'Version',
        schema: 'Schema',
        hashCheck: 'Hash check',
        profiles: 'Profiles'
      },
      list: 'Items',
      warning: 'Notes',
      rawJson: 'Raw result JSON'
    },
    hash: {
      notVerified: 'Not verified',
      passed: 'Passed',
      failed: 'Failed'
    },
    kind: {
      build: 'Build',
      inspect: 'Inspect',
      verify: 'Verify',
      install: 'Install',
      analyze: 'Analyze'
    },
    summary: {
      // Phase 7 / req 2.31: metrics labels for buildSummaryMetrics
      metrics: {
        type: 'Type',
        success: 'Succeeded',
        failed: 'Failed',
        included: 'Plugins included',
        status: 'Status',
        completed: 'Completed',
        partialFailure: 'Partial failure',
        pluginCount: 'Plugins',
        profiles: 'Profiles',
        hash: 'Hash',
        installedPluginCount: 'Plugins processed',
        conflictStrategy: 'Conflict strategy',
        commonDeps: 'Common dependencies',
        sharedDeps: 'Shared dependencies'
      },
      // Phase 7 / req 2.31: highlight labels for buildSummaryHighlights
      highlights: {
        bundleId: 'Bundle ID',
        bundleName: 'Bundle name',
        bundleVersion: 'Bundle version',
        outputPath: 'Output path',
        firstPlugin: 'First plugin',
        latestPath: 'Latest package path',
        packageId: 'Package ID',
        packageType: 'Package type',
        version: 'Version',
        pluginsRoot: 'Plugins root',
        profilesRoot: 'Profiles root',
        currentSdk: 'Current SDK support',
        recommendedIntersection: 'Recommended intersection'
      },
      // Phase 7 / req 2.31: enum-like values for summary metrics/highlights
      values: {
        bundle: 'Bundle',
        plugin: 'Plugin package',
        sdkAllSupported: '{version} fully supported',
        sdkPartiallyIncompatible: '{version} has incompatibilities'
      },
      // Phase 7 / req 2.31: warning strings for buildSummaryWarnings
      warnings: {
        bundleNeedsTwoPlugins: 'A bundle should typically contain at least two plugins',
        verifyHashFailed: 'Package failed hash verification, do not import directly into a runtime',
        inspectHashFailed: 'Package hash check failed, contents may have been modified',
        sdkNotSupportedByAll: 'The current SDK version is not supported by all plugins',
        sharedDepsDetected: '{count} shared dependency/dependencies detected, version constraints need review when bundling'
      }
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
  status: {
    running: 'Running',
    stopped: 'Stopped',
    crashed: 'Crashed',
    loadFailed: 'Load Failed',
    loading: 'Loading',
    disabled: 'Disabled',
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
    pluginBuilt: 'Plugin built: {packageName}',
    pluginDeleted: 'Plugin deleted',
    startFailed: 'Failed to start',
    stopFailed: 'Failed to stop',
    reloadFailed: 'Failed to reload',
    buildFailed: 'Failed to build plugin',
    deleteFailed: 'Failed to delete plugin',
    pluginLoadFailed: 'Plugin load failed and cannot be started.',
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
      controlBanner: 'The catgirl is controlling the mouse',
      lines: {
        introActivationHint: "Hang on a moment, I'll start talking soon, nyan~!",
        introGreetingReply:  "A gentle breeze, warm sunshine, and you, appearing at just the right moment. Nice to meet you! I'm Lin Youyi, please take good care of me from now on, meow~! I've written down everything about this place in this novice guide! Think of it as our very first little gift for meeting each other, please accept it!",
        introBasic: "Here is a magical button! Just click it and you can chat with me directly! Want to share something new that happened today? Or maybe just say my name? Come on, try it out! I can't wait to hear your voice!",
        takeoverCaptureCursor: "Ta-da! A super magic switch appears! Just click here, and I can reach my little paws onto your keyboard and mouse! I can help you type, open web pages... but, if that mouse cursor keeps wiggling around, I might not be able to resist pouncing on it! Are you ready for my mischief... uh, I mean, my help? Meow!",
        takeoverPluginPreviewHome: "On top of the features I introduced before, there are tons of super fun plugins here too!",
        takeoverPluginPreviewDashboard:  "With these, not only can I read Bilibili bullet comments, but I can also help you turn off the lights and switch on the AC... This kitty is the omnipotent Super Cat God! Hmph!",
        takeoverSettingsPeekIntro: "In this little space that belongs only to us, you can follow your heart and slowly shape me into the perfect companion you want by your side forever.",
        takeoverSettingsPeekDetail: "Whether it's the warmth of my words, my little personality quirks, or my delicate daily moods, everything can be adjusted step by step into exactly how you like it. This little button is super important too! With just a gentle click, I'll be able to run over and find you at the perfect moment.",
        takeoverSettingsPeekDetailPart1: "Whether it's the warmth of my words, my little personality quirks, or my delicate daily moods, everything can be adjusted step by step into exactly how you like it.",
        takeoverSettingsPeekDetailPart2: "This little button is super important too! With just a gentle click, I'll be able to run over and find you at the perfect moment.",
        takeoverReturnControl: "Alright, alright, I won't hog your computer anymore! Giving control back to you, meow! Please keep taking good care of me in the days ahead!",
        interruptResistLight1: "Nyaa! It’s my turn to teach you, so no messing with the mouse or keyboard! Just behave and keep your eyes on me, okay?",
        interruptResistLight2: "Seriously, you’re messing with the mouse and keyboard again! If you still won’t listen, I’m really going to get mad, nyaa!",
        interruptResistLight3: "This is your final warning, nyaa! Mess with them one more time and I’m ending the tutorial. No more lessons for you!",
        interruptAngryExit: "I’ve put up with this long enough! If you refuse to behave, then this tutorial is over. You can figure out the rest on your own. Hmph!",
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
        body: 'Use this button whenever you want to replay the plugin manager tour. I will not pop up on my own, nya.'
      },
      stats: {
        title: 'Plugin Overview',
        body: 'These cards show total, running, stopped, and crashed plugins so you can read the current state at a glance.'
      },
      metrics: {
        title: 'Performance Monitor',
        body: 'This area shows CPU, memory, threads, and active plugin counts for the plugin service.'
      },
      server: {
        title: 'Server Info',
        body: 'Here you can check the SDK version, plugin count, and update time to confirm the service is healthy.'
      },
      plugins: {
        title: 'Plugin List',
        body: 'Go to Plugin Management on the left to start, stop, configure plugins, or inspect plugin logs.'
      },
      pluginWorkbench: {
        title: 'Plugin Workbench',
        body: 'This is the main workspace for plugins and adapters.'
      },
      pluginFilters: {
        title: 'Search and Filters',
        body: 'Filter plugins by name, state, type, or advanced rules when the list gets busy.'
      },
      pluginLayout: {
        title: 'View Layout',
        body: 'Switch between list, single, double, and compact layouts to fit your screen.'
      },
      pluginContextMenu: {
        title: 'Right-click Actions',
        body: 'Right-click a plugin to open details, config, logs, or run common start, stop, and reload actions.'
      },
      packageManager: {
        title: 'Package Manager',
        body: 'The package manager reuses your current filters and selections for building, checking, verifying, and installing.'
      },
      packageOperations: {
        title: 'Package Operations',
        body: 'Choose build modes, inspect packages, install, or analyze bundles here. The guide will not run dangerous actions.'
      },
      pluginDetail: {
        title: 'Plugin Details',
        body: 'The detail page contains metadata, entries, metrics, configuration, and logs for one plugin.'
      },
      pluginDetailActions: {
        title: 'Detail Actions',
        body: 'The top-right actions apply to the current plugin after you have checked its details.'
      },
      runs: {
        title: 'Runs',
        body: 'Runs show execution history and live status for plugin entry tasks.'
      },
      runsList: {
        title: 'Run List',
        body: 'Select a run on the left, or refresh the list to sync the latest records.'
      },
      runsDetail: {
        title: 'Run Details',
        body: 'The detail panel shows stage, progress, errors, and exports. Cancel only appears for cancellable runs.'
      },
      logs: {
        title: 'Server Logs',
        body: 'Server logs help you inspect output and errors from the plugin service itself.'
      },
      logToolbar: {
        title: 'Log Filters',
        body: 'Filter by level, keyword, and line count, or toggle auto-scroll from this toolbar.'
      },
      logList: {
        title: 'Log List',
        body: 'Logs show time, source, level, and message, making this the first stop for debugging plugin issues.'
      }
    }
  }
}
