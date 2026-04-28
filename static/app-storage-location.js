(function () {
    if (window.appStorageLocation) return;

    var STORAGE_APP_FOLDER_NAME = 'N.E.K.O';
    var STORAGE_RESTART_MESSAGE_TYPE = 'storage_location_restart_initiated';
    var STORAGE_RESTART_CHANNEL = 'neko_storage_location_channel';
    var STORAGE_RESTART_PAGE_ID = window.__nekoStorageLocationPageId || (
        'storage-location-' + Date.now() + '-' + Math.random().toString(36).slice(2)
    );
    window.__nekoStorageLocationPageId = STORAGE_RESTART_PAGE_ID;
    var autoStart = !(
        document.currentScript
        && document.currentScript.getAttribute('data-storage-location-auto-start') === 'false'
    );

    var STORAGE_I18N_EN = {
        badge: 'Storage Location',
        bootstrapError: 'Failed to load storage initialization information. Please try again.',
        blockingGeneric: 'The selected storage location cannot be used right now. Please choose another location or try again later.',
        blockingInsufficientSpace: 'The target volume does not have enough free space for a safe migration.',
        blockingTargetNotWritable: 'The target path is not writable, so migration cannot start.',
        chooseOther: 'Choose another location',
        cleanupRetainedRoot: 'Clean up old data directory',
        cleanupRetainedRootConfirm: 'This will delete the retained old data directory and will not affect the new active directory. Continue?',
        cleanupRetainedRootDone: 'The old data directory has been cleaned up. Only the new runtime directory remains.',
        cleanupRetainedRootFailed: 'Failed to clean up the old data directory. Please try again later.',
        completionMessage: 'The new runtime directory is active. The old data directory is still retained for manual cleanup.',
        completionTitle: 'Storage migration completed',
        confirmReconnect: 'Confirm shutdown and reconnect path',
        confirmRestart: 'Confirm shutdown and migrate',
        confirmExistingTargetContent: 'The target folder already contains N.E.K.O runtime data. If you continue, migration will replace same-name runtime data folders in the target. Other files in the target folder will be kept. Continue?',
        currentPath: 'Current path',
        customPathPlaceholder: 'Choose a parent folder; N.E.K.O will use its N.E.K.O subfolder',
        customPreviewNotice: 'Backend confirmed that switching to this location requires closing the current instance, migrating data, and restarting automatically.',
        dialogLabel: 'Storage location selection',
        errorBadge: 'Load failed',
        errorTitle: 'Storage startup information is temporarily unavailable',
        estimatedPayload: 'Estimated data size',
        legacyChoiceEmpty: 'No reusable legacy data folder was found. You can choose a folder or enter a path manually.',
        loadingFetchBootstrapSubtitle: 'Preparing the storage location screen.',
        loadingSubtitle: 'The main UI will continue after the storage state is confirmed.',
        loadingTitle: 'Checking storage layout',
        loadingWaitSubtitle: 'The main UI will continue after the storage state is confirmed.',
        maintenanceClosingStatus: 'Waiting for the current instance to finish shutting down...',
        maintenanceNote: 'A brief disconnect is expected. The page will reconnect automatically after the service recovers.',
        maintenanceOfflineStatus: 'The connection is temporarily interrupted. Waiting for the service to recover. Please keep this page open.',
        maintenanceReconnectStatus: 'Keep this page open. The home page will recover automatically.',
        maintenanceReconnectSubtitle: 'The service is back. Reconnecting the app...',
        maintenanceTargetStatus: 'Target path recorded. Waiting for shutdown and recovery:',
        maintenanceTitle: 'Optimizing storage layout...',
        maintenanceWaitingStatus: 'This page will stay here and retry automatically until the service recovers.',
        maintenanceWaitingSubtitle: 'The current instance is about to close. Data will be migrated after shutdown and the service will restart automatically.',
        migrationPending: 'An unfinished migration plan was detected. The home page will remain blocked until the service becomes usable again.',
        noWarnings: 'No additional risk notes detected.',
        otherPanelNote: 'You can reuse an existing data folder or choose a new folder for future runs.',
        otherPanelTitle: 'Another location',
        openActiveRoot: 'Open active directory',
        openDirectoryFailed: 'Failed to open the directory.',
        openDirectoryUnavailable: 'Opening directories is unavailable in this environment.',
        openRetainedRoot: 'Open old directory',
        pathOverview: 'Path overview',
        permissionBlocked: 'Not writable',
        permissionCheck: 'Write access',
        permissionOk: 'Writable',
        pickFolder: 'Choose folder',
        pickFolderUnavailable: 'The system folder picker is unavailable. Please enter the path manually.',
        pickFolderFailed: 'Failed to open the folder picker. Please enter the path manually.',
        previewBoundary: 'The root will not hot-switch in this session, and the stable root will not be changed early. After confirmation, the backend will perform shutdown, any required migration, auto-restart, and final layout recovery in order.',
        previewOther: 'Use this location',
        previewStepClose: '1. The current instance will close first.',
        previewStepMigrate: '2. After shutdown, the target layout will be restored; data will be migrated only when needed.',
        previewStepRestart: '3. The app will restart automatically after that.',
        previewStepRetain: '4. Old data is kept by default and will not be deleted automatically.',
        previewTitle: 'This choice requires a shutdown and migration',
        progressCommitting: 'Applying the new storage location',
        progressCompleted: 'Migration completed. Recovering the service',
        progressCopying: 'Migrating runtime data',
        progressFailed: 'Migration did not complete. Waiting for recovery handling',
        progressPending: 'Target path recorded. Preparing to shut down the current instance',
        progressPreflight: 'Checking the target location and preparing migration',
        progressRebindPreflight: 'Preparing to reconnect the original storage location',
        progressRebinding: 'Shutting down the current instance and reconnecting the original path',
        progressRecovered: 'The service is back. Reconnecting the page',
        progressRetaining: 'Keeping the old data directory for later manual cleanup',
        progressStepCommit: 'Verify and apply',
        progressStepRecover: 'Recover service',
        progressStepShutdown: 'Shut down current instance',
        progressStepTransfer: 'Process storage directory',
        progressVerifying: 'Verifying migration results',
        progressWaitingShutdown: 'Waiting for the current instance to shut down safely',
        rebindPreviewNotice: 'Backend confirmed that the original path is reachable again. The current instance will close and restart on that path. No runtime data will be copied this time.',
        recommendedPath: 'Recommended path',
        recommendedPreviewNotice: 'Backend confirmed that switching to the recommended location requires closing the current instance, migrating data, and restarting automatically.',
        recoveryRequired: 'A recoverable storage state was detected. Confirm the storage location for this launch before continuing.',
        recoverySourceUnavailable: 'The original data path is unavailable. Reconnect it or explicitly switch to the recommended default path.',
        restartRequestFailed: 'Failed to start shutdown and migration preparation. Please try again later.',
        restartRequestUnexpected: 'The shutdown and migration preparation API returned an unrecognized result.',
        restartNotRequired: 'The target path is already the current path. Shutdown is not required.',
        restartScheduleFailed: 'Failed to schedule the controlled shutdown. Please try again later.',
        restartUnavailable: 'This instance cannot perform a controlled shutdown right now. Please try again later.',
        retainedRoot: 'Retained directory',
        retainedSourceCleanupFailed: 'Failed to clean up the old data directory. Please try again later.',
        retainedSourceMismatch: 'The requested cleanup path does not match the retained directory. Please refresh and try again.',
        retainedSourceNotFound: 'There is no retained old data directory to clean up.',
        selectPathRequired: 'Please provide a target path first.',
        selectedRootInsideState: 'This location is inside N.E.K.O runtime state and cannot be used as the storage root.',
        selectedRootUnavailable: 'The selected storage path is still unavailable. Restore that path before trying again.',
        selectionSubmitFailed: 'Failed to submit the storage location choice. Please try again later.',
        selectionSubmitUnexpected: 'The storage location selection API returned an unrecognized result.',
        selectionSubtitle: 'The app is open. Confirm the storage location on this page before continuing.',
        selectionTitle: 'Choose the storage location for this launch',
        sourceLabel: 'Source path',
        statusUnexpected: 'The storage maintenance status API returned an unrecognized result.',
        storageBootstrapBlocking: 'Storage still needs recovery or migration, so this session cannot continue yet.',
        targetNotEmpty: 'The target already contains runtime data. Confirm the target before migration.',
        systemStatusUnavailable: 'The local service status could not be confirmed. Please try again.',
        systemStatusUnexpected: 'The storage startup status API returned an unrecognized result.',
        targetFreeSpace: 'Free space on target volume',
        targetLabel: 'Target path',
        unknownBytes: 'Not estimated yet',
        useCurrent: 'Keep current path',
        useLegacyPath: 'Use this legacy data path',
        useRecommended: 'Use recommended location',
        warningExternalVolume: 'The target path is on an external or mounted volume. Stability depends on whether the volume stays available.',
        warningNetworkShare: 'The target path is on a network share. Connection instability may affect migration reliability.',
        warningSummary: 'Risk notes',
        warningSymlink: 'The target path goes through a symlink or equivalent redirect. Please confirm the actual destination.',
        warningSyncFolder: 'The target path is inside a sync folder. Sync software may interfere during migration.',
        warningTargetHasExistingContent: 'The target already contains runtime data. A second confirmation is required before migration starts.'
    };

    var STORAGE_I18N_ZH_CN = {
        badge: '存储位置',
        bootstrapError: '无法读取存储位置初始化信息，请重试。',
        blockingGeneric: '当前无法使用所选存储位置，请换一个位置或稍后重试。',
        blockingInsufficientSpace: '目标卷剩余空间不足，无法安全执行关闭后的迁移。',
        blockingTargetNotWritable: '目标路径当前不可写，无法开始关闭后的迁移流程。',
        chooseOther: '选择其他位置',
        cleanupRetainedRoot: '清理旧数据目录',
        cleanupRetainedRootConfirm: '这会删除当前保留的旧数据目录，且不会影响当前已经生效的新目录。要继续吗？',
        cleanupRetainedRootDone: '旧数据目录已清理，当前仅保留新的运行目录。',
        cleanupRetainedRootFailed: '清理旧数据目录失败，请稍后重试。',
        completionMessage: '新的运行目录已经生效，旧数据目录目前仍保留，你可以稍后再决定是否清理。',
        completionTitle: '存储迁移已完成',
        confirmReconnect: '确认关闭并重连路径',
        confirmRestart: '确认关闭并迁移',
        confirmExistingTargetContent: '目标文件夹已经包含 N.E.K.O 运行时数据。继续后，迁移会覆盖目标中的同名运行时数据目录，目标目录里的其他文件会保留。确认继续吗？',
        currentPath: '当前路径',
        customPathPlaceholder: '选择一个父目录，应用会使用其中的 N.E.K.O 子文件夹',
        customPreviewNotice: '后端已确认：如果后续改用这个位置，需要先关闭当前实例，再迁移数据并自动重启。',
        dialogLabel: '存储位置选择',
        errorBadge: '读取失败',
        errorTitle: '暂时无法读取存储位置引导信息',
        estimatedPayload: '预计迁移体量',
        legacyChoiceEmpty: '未检测到可直接复用的旧数据目录，可直接选择文件夹或手动输入路径。',
        loadingFetchBootstrapSubtitle: '正在准备存储位置选择页面。',
        loadingSubtitle: '主业务界面会在存储状态确认完成后再继续加载。',
        loadingTitle: '正在确认存储布局状态',
        loadingWaitSubtitle: '主业务界面会在存储状态确认完成后再继续加载。',
        maintenanceClosingStatus: '正在等待当前实例完成关闭...',
        maintenanceNote: '连接短暂中断属于正常现象。页面会在服务恢复后自动重新连接。',
        maintenanceOfflineStatus: '连接已暂时中断，正在等待服务恢复。请不要关闭当前页面。',
        maintenanceReconnectStatus: '请保持当前页面打开，主页会自动恢复。',
        maintenanceReconnectSubtitle: '检测到服务已经恢复，正在重新连接应用。',
        maintenanceTargetStatus: '目标路径已记录，正在等待服务关闭并恢复：',
        maintenanceTitle: '正在优化存储布局...',
        maintenanceWaitingStatus: '服务尚未恢复前，页面会继续停留在这里并自动重试连接。',
        maintenanceWaitingSubtitle: '当前实例即将关闭，数据会在关闭后迁移并自动重启。',
        migrationPending: '检测到尚未完成的迁移计划。当前主页会继续保持阻断，直到服务恢复到可继续状态。',
        noWarnings: '当前未检测到需要额外提示的风险项。',
        otherPanelNote: '你可以直接复用旧数据目录，也可以选择一个新的文件夹作为后续运行位置。',
        otherPanelTitle: '其他位置',
        openActiveRoot: '打开当前目录',
        openDirectoryFailed: '打开目录失败。',
        openDirectoryUnavailable: '当前环境不支持直接打开目录。',
        openRetainedRoot: '打开旧目录',
        pathOverview: '路径总览',
        permissionBlocked: '当前不可写',
        permissionCheck: '目标路径写入权限',
        permissionOk: '当前可写',
        pickFolder: '选择文件夹',
        pickFolderUnavailable: '当前系统目录选择器不可用，请手动输入路径。',
        pickFolderFailed: '打开文件夹选择器失败，请手动输入路径。',
        previewBoundary: '当前不会在本会话里热切根，也不会提前把稳定根改成新路径。确认后会由后端按设计顺序完成关闭、必要迁移、自动重启与最终布局恢复。',
        previewOther: '提交该位置',
        previewStepClose: '1. 当前实例会先关闭。',
        previewStepMigrate: '2. 关闭后会恢复目标存储布局；必要时再迁移数据。',
        previewStepRestart: '3. 迁移完成后会自动重启。',
        previewStepRetain: '4. 旧数据默认不会自动删除。',
        previewTitle: '该选择需要后续关闭并迁移',
        progressCommitting: '正在提交新的存储位置',
        progressCompleted: '迁移已完成，正在恢复服务',
        progressCopying: '正在迁移运行时数据',
        progressFailed: '迁移未能完成，正在等待恢复处理',
        progressPending: '目标路径已记录，正在准备关闭当前实例',
        progressPreflight: '正在检查目标位置并准备迁移',
        progressRebindPreflight: '正在准备重连原始存储位置',
        progressRebinding: '正在关闭当前实例并重连原始路径',
        progressRecovered: '服务已恢复，正在重新连接页面',
        progressRetaining: '正在保留旧数据目录以便后续手动清理',
        progressStepCommit: '校验并生效',
        progressStepRecover: '恢复服务',
        progressStepShutdown: '关闭当前实例',
        progressStepTransfer: '处理存储目录',
        progressVerifying: '正在校验迁移结果',
        progressWaitingShutdown: '正在等待当前实例安全关闭',
        rebindPreviewNotice: '后端已确认：原路径已经可以重新连接。后续会关闭当前实例并自动重启到该路径，本次不会复制运行时数据。',
        recommendedPath: '推荐路径',
        recommendedPreviewNotice: '后端已确认：如果后续改用推荐位置，需要先关闭当前实例，再迁移数据并自动重启。',
        recoveryRequired: '检测到需要恢复的存储状态，请先重新确认本次使用的存储位置。',
        recoverySourceUnavailable: '原始数据路径当前不可用。请先重连原路径，或显式切回推荐默认路径继续当前会话。',
        restartRequestFailed: '启动关闭与迁移准备失败，请稍后重试。',
        restartRequestUnexpected: '关闭与迁移准备接口返回了未识别的结果。',
        restartNotRequired: '目标路径与当前路径一致，不需要关闭当前实例。',
        restartScheduleFailed: '受控关闭启动失败，请稍后重试。',
        restartUnavailable: '当前实例暂时无法执行受控关闭，请稍后重试。',
        retainedRoot: '当前保留目录',
        retainedSourceCleanupFailed: '清理旧数据保留目录失败，请稍后重试。',
        retainedSourceMismatch: '请求的清理路径与当前保留目录不一致，请刷新后重试。',
        retainedSourceNotFound: '当前没有可清理的旧数据保留目录。',
        selectPathRequired: '请先提供目标路径。',
        selectedRootInsideState: '该位置位于 N.E.K.O 运行时状态目录内，不能作为存储根目录。',
        selectedRootUnavailable: '原始数据路径当前仍不可用，请先恢复该路径后再重试。',
        selectionSubmitFailed: '提交存储位置选择失败，请稍后重试。',
        selectionSubmitUnexpected: '存储位置选择接口返回了未识别的结果。',
        selectionSubtitle: '应用已经正常打开。接下来请先在当前页面内确认存储位置，再继续使用。',
        selectionTitle: '请选择本次运行使用的存储位置',
        sourceLabel: '原始路径',
        statusUnexpected: '存储维护状态接口返回了未识别的结果。',
        storageBootstrapBlocking: '当前存储状态仍需恢复或迁移，暂时不能继续当前会话。',
        targetNotEmpty: '目标路径已经包含运行时数据，请确认目标目录后再继续迁移。',
        systemStatusUnavailable: '暂时无法确认本地服务状态，请重试。',
        systemStatusUnexpected: '存储启动状态接口返回了未识别的结果。',
        targetFreeSpace: '目标卷剩余空间',
        targetLabel: '目标路径',
        unknownBytes: '暂未估算',
        useCurrent: '保持当前路径',
        useLegacyPath: '使用该旧数据路径',
        useRecommended: '使用推荐位置',
        warningExternalVolume: '目标路径位于外置卷或挂载卷，稳定性取决于卷是否持续可用。',
        warningNetworkShare: '目标路径位于网络共享目录，连接波动可能影响迁移稳定性。',
        warningSummary: '额外风险提示',
        warningSymlink: '目标路径命中了符号链接或等价重定向目录，请确认真实落点正确。',
        warningSyncFolder: '目标路径位于同步盘目录，迁移期间可能受到同步程序干扰。',
        warningTargetHasExistingContent: '目标路径已经包含运行时数据，开始迁移前需要二次确认。'
    };

    function cloneTranslations(base, overrides) {
        var cloned = {};
        Object.keys(base).forEach(function (key) {
            cloned[key] = base[key];
        });
        Object.keys(overrides || {}).forEach(function (key) {
            cloned[key] = overrides[key];
        });
        return cloned;
    }

    var STORAGE_I18N_ZH_TW = cloneTranslations(STORAGE_I18N_ZH_CN, {
        badge: '存儲位置',
        chooseOther: '選擇其他位置',
        cleanupRetainedRoot: '清理舊資料目錄',
        cleanupRetainedRootConfirm: '這會刪除目前保留的舊資料目錄，且不會影響目前已生效的新目錄。要繼續嗎？',
        cleanupRetainedRootDone: '舊資料目錄已清理，目前僅保留新的執行目錄。',
        cleanupRetainedRootFailed: '清理舊資料目錄失敗，請稍後重試。',
        completionMessage: '新的執行目錄已生效，舊資料目錄目前仍保留，你可以稍後再決定是否清理。',
        completionTitle: '存儲遷移已完成',
        confirmReconnect: '確認關閉並重連路徑',
        confirmRestart: '確認關閉並遷移',
        confirmExistingTargetContent: '目標資料夾已經包含 N.E.K.O 執行時資料。繼續後，遷移會覆蓋目標中的同名執行時資料目錄，目標目錄裡的其他檔案會保留。確認繼續嗎？',
        currentPath: '當前路徑',
        customPathPlaceholder: '選擇一個父目錄，應用會使用其中的 N.E.K.O 子資料夾',
        errorTitle: '暫時無法讀取存儲位置引導資訊',
        estimatedPayload: '預計遷移體量',
        legacyChoiceEmpty: '未檢測到可直接沿用的舊資料目錄，可直接選擇資料夾或手動輸入路徑。',
        loadingFetchBootstrapSubtitle: '正在準備存儲位置選擇頁面。',
        loadingSubtitle: '主業務介面會在存儲狀態確認完成後再繼續載入。',
        loadingTitle: '正在確認存儲布局狀態',
        loadingWaitSubtitle: '主業務介面會在存儲狀態確認完成後再繼續載入。',
        maintenanceClosingStatus: '正在等待當前實例完成關閉...',
        maintenanceNote: '連線短暫中斷屬於正常現象。頁面會在服務恢復後自動重新連線。',
        maintenanceOfflineStatus: '連線已暫時中斷，正在等待服務恢復。請不要關閉目前頁面。',
        maintenanceReconnectStatus: '請保持目前頁面開啟，首頁會自動恢復。',
        maintenanceReconnectSubtitle: '檢測到服務已恢復，正在重新連線應用。',
        maintenanceTargetStatus: '目標路徑已記錄，正在等待服務關閉並恢復：',
        maintenanceTitle: '正在優化存儲布局...',
        maintenanceWaitingStatus: '服務尚未恢復前，頁面會繼續停留在這裡並自動重試連線。',
        maintenanceWaitingSubtitle: '當前實例即將關閉，資料會在關閉後遷移並自動重啟。',
        migrationPending: '檢測到尚未完成的遷移計畫。目前首頁會繼續保持阻斷，直到服務恢復到可繼續狀態。',
        noWarnings: '目前未檢測到需要額外提示的風險項。',
        otherPanelNote: '你可以直接沿用舊資料目錄，也可以選擇新的資料夾作為後續執行位置。',
        otherPanelTitle: '其他位置',
        openActiveRoot: '開啟目前目錄',
        openDirectoryFailed: '開啟目錄失敗。',
        openDirectoryUnavailable: '目前環境不支援直接開啟目錄。',
        openRetainedRoot: '開啟舊目錄',
        pathOverview: '路徑總覽',
        permissionBlocked: '目前不可寫',
        permissionCheck: '目標路徑寫入權限',
        permissionOk: '目前可寫',
        pickFolder: '選擇資料夾',
        pickFolderFailed: '開啟資料夾選擇器失敗，請手動輸入路徑。',
        pickFolderUnavailable: '目前系統資料夾選擇器不可用，請手動輸入路徑。',
        previewBoundary: '目前不會在本次會話裡熱切根，也不會提前把穩定根改成新路徑。確認後會由後端按設計順序完成關閉、必要遷移、自動重啟與最終布局恢復。',
        previewOther: '提交此位置',
        previewStepClose: '1. 當前實例會先關閉。',
        previewStepMigrate: '2. 關閉後會恢復目標存儲布局；必要時再遷移資料。',
        previewStepRestart: '3. 遷移完成後會自動重啟。',
        previewStepRetain: '4. 舊資料預設不會自動刪除。',
        previewTitle: '此選擇需要後續關閉並遷移',
        progressCommitting: '正在提交新的存儲位置',
        progressCompleted: '遷移已完成，正在恢復服務',
        progressCopying: '正在遷移執行時資料',
        progressFailed: '遷移未能完成，正在等待恢復處理',
        progressPending: '目標路徑已記錄，正在準備關閉當前實例',
        progressPreflight: '正在檢查目標位置並準備遷移',
        progressRebindPreflight: '正在準備重連原始存儲位置',
        progressRebinding: '正在關閉當前實例並重連原始路徑',
        progressRecovered: '服務已恢復，正在重新連線頁面',
        progressRetaining: '正在保留舊資料目錄以便後續手動清理',
        progressStepCommit: '校驗並生效',
        progressStepRecover: '恢復服務',
        progressStepShutdown: '關閉當前實例',
        progressStepTransfer: '處理存儲目錄',
        progressVerifying: '正在校驗遷移結果',
        progressWaitingShutdown: '正在等待當前實例安全關閉',
        rebindPreviewNotice: '後端已確認：原路徑已可以重新連線。後續會關閉當前實例並自動重啟到該路徑，本次不會複製執行時資料。',
        recommendedPath: '建議路徑',
        recommendedPreviewNotice: '後端已確認：如果後續改用建議位置，需要先關閉當前實例，再遷移資料並自動重啟。',
        recoveryRequired: '檢測到需要恢復的存儲狀態，請先重新確認本次使用的存儲位置。',
        recoverySourceUnavailable: '原始資料路徑目前不可用。請先重新連接原路徑，或明確切回建議預設路徑繼續目前會話。',
        restartRequestFailed: '啟動關閉與遷移準備失敗，請稍後重試。',
        restartRequestUnexpected: '關閉與遷移準備介面返回了未識別的結果。',
        restartNotRequired: '目標路徑與目前路徑一致，不需要關閉目前實例。',
        restartScheduleFailed: '受控關閉啟動失敗，請稍後重試。',
        restartUnavailable: '目前實例暫時無法執行受控關閉，請稍後重試。',
        retainedRoot: '目前保留目錄',
        retainedSourceCleanupFailed: '清理舊資料保留目錄失敗，請稍後重試。',
        retainedSourceMismatch: '請求的清理路徑與目前保留目錄不一致，請重新整理後再試。',
        retainedSourceNotFound: '目前沒有可清理的舊資料保留目錄。',
        selectPathRequired: '請先提供目標路徑。',
        selectedRootInsideState: '該位置位於 N.E.K.O 執行時狀態目錄內，不能作為存儲根目錄。',
        selectedRootUnavailable: '原始資料路徑目前仍不可用，請先恢復該路徑後再試。',
        selectionSubmitFailed: '提交存儲位置選擇失敗，請稍後重試。',
        selectionSubmitUnexpected: '存儲位置選擇介面返回了未識別的結果。',
        selectionSubtitle: '應用已正常開啟。接下來請先在目前頁面內確認存儲位置，再繼續使用。',
        selectionTitle: '請選擇本次執行使用的存儲位置',
        sourceLabel: '原始路徑',
        statusUnexpected: '存儲維護狀態介面返回了未識別的結果。',
        storageBootstrapBlocking: '目前存儲狀態仍需恢復或遷移，暫時不能繼續目前會話。',
        targetNotEmpty: '目標路徑已經包含執行時資料，請確認目標目錄後再繼續遷移。',
        systemStatusUnavailable: '暫時無法確認本地服務狀態，請重試。',
        systemStatusUnexpected: '存儲啟動狀態介面返回了未識別的結果。',
        targetFreeSpace: '目標卷剩餘空間',
        targetLabel: '目標路徑',
        unknownBytes: '暫未估算',
        useCurrent: '保持當前路徑',
        useLegacyPath: '使用此舊資料路徑',
        useRecommended: '使用建議位置',
        warningExternalVolume: '目標路徑位於外接卷或掛載卷，穩定性取決於該卷是否持續可用。',
        warningNetworkShare: '目標路徑位於網路共享目錄，連線波動可能影響遷移穩定性。',
        warningSummary: '額外風險提示',
        warningSymlink: '目標路徑命中了符號連結或等價重導向目錄，請確認實際落點正確。',
        warningSyncFolder: '目標路徑位於同步碟目錄，遷移期間可能受到同步程式干擾。',
        warningTargetHasExistingContent: '目標路徑已經包含執行時資料，開始遷移前需要二次確認。'
    });

    var STORAGE_I18N_RESOURCES = {
        en: STORAGE_I18N_EN,
        ja: STORAGE_I18N_EN,
        ko: STORAGE_I18N_EN,
        ru: STORAGE_I18N_EN,
        'zh-CN': STORAGE_I18N_ZH_CN,
        'zh-TW': STORAGE_I18N_ZH_TW
    };

    var storageTranslationsRegistered = false;

    function resolveStorageLocale(language) {
        var normalized = String(language || '').trim();
        if (!normalized) return 'en';
        if (STORAGE_I18N_RESOURCES[normalized]) return normalized;

        var lower = normalized.toLowerCase();
        if (lower === 'zh-tw' || lower === 'zh-hk' || lower === 'zh-mo') return 'zh-TW';
        if (lower.indexOf('zh') === 0) return 'zh-CN';
        if (lower.indexOf('ja') === 0) return 'ja';
        if (lower.indexOf('ko') === 0) return 'ko';
        if (lower.indexOf('ru') === 0) return 'ru';
        return 'en';
    }

    function isI18nReady() {
        return !!(
            window.i18next
            && window.i18next.isInitialized === true
        );
    }

    function getCurrentLanguage() {
        if (isI18nReady()) {
            if (typeof window.i18next.resolvedLanguage === 'string' && window.i18next.resolvedLanguage) {
                return window.i18next.resolvedLanguage;
            }
            if (typeof window.i18next.language === 'string' && window.i18next.language) {
                return window.i18next.language;
            }
        }
        if (typeof window.currentLanguage === 'string' && window.currentLanguage) {
            return window.currentLanguage;
        }
        if (document && document.documentElement && document.documentElement.lang) {
            return document.documentElement.lang;
        }
        return '';
    }

    function registerStorageTranslations() {
        if (storageTranslationsRegistered) return;
        if (!isI18nReady() || typeof window.i18next.addResourceBundle !== 'function') return;

        Object.keys(STORAGE_I18N_RESOURCES).forEach(function (locale) {
            window.i18next.addResourceBundle(
                locale,
                'translation',
                { storage: STORAGE_I18N_RESOURCES[locale] },
                true,
                true
            );
        });
        storageTranslationsRegistered = true;
    }

    function getInlineStorageTranslation(key) {
        if (String(key || '').indexOf('storage.') !== 0) return '';
        var shortKey = String(key || '').slice('storage.'.length);
        var locale = resolveStorageLocale(getCurrentLanguage());
        var bundle = STORAGE_I18N_RESOURCES[locale] || STORAGE_I18N_EN;
        return String(bundle[shortKey] || STORAGE_I18N_EN[shortKey] || '').trim();
    }

    var state = {
        initialized: false,
        initPromise: null,
        submitting: false,
        phase: 'hidden',
        systemStatus: null,
        startupDecision: null,
        bootstrap: null,
        overlay: null,
        loadingView: null,
        loadingTitle: null,
        loadingSubtitle: null,
        maintenanceView: null,
        maintenanceTitle: null,
        maintenanceSubtitle: null,
        maintenanceStatus: null,
        maintenanceProgressBar: null,
        maintenanceProgressFill: null,
        maintenanceProgressLabel: null,
        maintenanceProgressValue: null,
        maintenanceProgressSteps: [],
        lastMaintenanceProgressPayload: null,
        // 记录最近一次 setSelectionStatus / showError 时使用的 i18n key（如有）。
        // rebuildModalForLocale 在切语言后会优先按 key 重新翻译，避免快照里塞回旧 locale 的字面文案。
        // 来自后端透传的运行时错误（error.message 等）则不带 key，rebuild 时按原文回填。
        selectionStatusI18nKey: '',
        selectionStatusI18nFallback: '',
        errorTextI18nKey: '',
        errorTextI18nFallback: '',
        maintenancePollPromise: null,
        completionPollTimer: null,
        completionPollAttempts: 0,
        completionNotice: null,
        completionCard: null,
        completionTitle: null,
        completionMessage: null,
        completionTarget: null,
        completionRetained: null,
        completionOpenTargetButton: null,
        completionOpenRetainedButton: null,
        completionCleanupButton: null,
        externalMaintenanceNoticeKey: '',
        selectionView: null,
        errorView: null,
        banner: null,
        currentPath: null,
        recommendedPath: null,
        otherPanel: null,
        legacyChoices: null,
        customInput: null,
        pickFolderButton: null,
        useOtherButton: null,
        previewPanel: null,
        previewText: null,
        previewSource: null,
        previewTarget: null,
        previewEstimated: null,
        previewFreeSpace: null,
        previewPermission: null,
        previewWarnings: null,
        previewBlocking: null,
        previewConfirmButton: null,
        previewActions: null,
        selectionStatus: null,
        errorText: null,
        actionButtons: [],
        pendingSelection: {
            path: '',
            source: '',
            preflight: null,
        },
        otherSelection: {
            key: '',
            path: '',
        },
    };

    function createDeferred() {
        var deferred = {
            settled: false,
            promise: null,
            resolve: null,
        };
        deferred.promise = new Promise(function (resolve) {
            deferred.resolve = function (value) {
                if (deferred.settled) return;
                deferred.settled = true;
                resolve(value);
            };
        });
        return deferred;
    }

    state.startupDecision = createDeferred();

    function translate(key, fallback) {
        var normalizedKey = String(key || '');
        var storageKey = normalizedKey.indexOf('storage.') === 0;

        if (!storageKey || isI18nReady()) {
            registerStorageTranslations();
            try {
                if (typeof window.safeT === 'function') {
                    var safeTranslated = window.safeT(key, fallback);
                    if (typeof safeTranslated === 'string' && safeTranslated && safeTranslated !== key) {
                        return safeTranslated;
                    }
                }
                if (typeof window.t === 'function') {
                    var translated = window.t(key, { defaultValue: fallback });
                    if (typeof translated === 'string' && translated && translated !== key) return translated;
                }
            } catch (_) {}
        }

        if (storageKey && isI18nReady()) {
            var inlineTranslation = getInlineStorageTranslation(key);
            if (inlineTranslation) return inlineTranslation;
        }

        return fallback || key;
    }

    function createElement(tag, className, text) {
        var element = document.createElement(tag);
        if (className) element.className = className;
        if (typeof text === 'string') element.textContent = text;
        return element;
    }

    function trimPathTrailingSeparators(value) {
        var pathText = String(value || '').trim();
        if (/^[A-Za-z]:[\\/]*$/.test(pathText)) {
            return pathText.replace(/[\\/]*$/, '\\');
        }
        if (/^\/+$/.test(pathText)) {
            return '/';
        }
        return pathText.replace(/[\\/]+$/, '');
    }

    function getPathLeafName(pathText) {
        var normalized = trimPathTrailingSeparators(pathText);
        if (!normalized || normalized === '/') return '';
        var parts = normalized.split(/[\\/]+/);
        return parts.length ? parts[parts.length - 1] : '';
    }

    function pathEndsWithAppFolder(pathText) {
        return getPathLeafName(pathText).toLowerCase() === STORAGE_APP_FOLDER_NAME.toLowerCase();
    }

    function normalizeCustomStorageRootForDisplay(pathText) {
        var normalized = trimPathTrailingSeparators(pathText);
        if (!normalized || pathEndsWithAppFolder(normalized)) {
            return normalized;
        }
        if (normalized === '/') {
            return '/' + STORAGE_APP_FOLDER_NAME;
        }
        if (/^[A-Za-z]:\\$/.test(normalized)) {
            return normalized + STORAGE_APP_FOLDER_NAME;
        }
        var separator = normalized.lastIndexOf('\\') > normalized.lastIndexOf('/') ? '\\' : '/';
        return normalized + separator + STORAGE_APP_FOLDER_NAME;
    }

    function applyCustomStorageRootDisplay(pathText) {
        var normalized = normalizeCustomStorageRootForDisplay(pathText);
        if (state.customInput) {
            state.customInput.value = normalized;
        }
        state.otherSelection.key = 'custom';
        state.otherSelection.path = normalized;
        return normalized;
    }

    async function requestHostWindowClose() {
        var host = window.nekoHost || {};
        if (host && typeof host.closeWindow === 'function') {
            try {
                var result = await host.closeWindow();
                if (result && result.ok === true) return;
            } catch (_) {}
        }

        try {
            window.close();
        } catch (_) {}
    }

    function buildStorageLocationCloseButton(onClick) {
        var closeButton = createElement('button', 'storage-location-close', '×');
        closeButton.type = 'button';
        closeButton.setAttribute('aria-label', translate('common.close', '关闭'));
        closeButton.setAttribute('title', translate('common.close', '关闭'));
        closeButton.addEventListener('click', onClick || requestHostWindowClose);
        return closeButton;
    }

    function pathEquals(left, right) {
        return String(left || '').trim() === String(right || '').trim();
    }

    function clearChildren(element) {
        if (!element) return;
        while (element.firstChild) {
            element.removeChild(element.firstChild);
        }
    }

    function formatBytes(value) {
        var size = Number(value || 0);
        if (!Number.isFinite(size) || size <= 0) {
            return translate('storage.unknownBytes', '暂未估算');
        }

        var units = ['B', 'KB', 'MB', 'GB', 'TB'];
        var index = 0;
        while (size >= 1024 && index < units.length - 1) {
            size /= 1024;
            index += 1;
        }
        var digits = size >= 100 || index === 0 ? 0 : 1;
        return size.toFixed(digits) + ' ' + units[index];
    }

    function normalizeWarningCodes(value) {
        return Array.isArray(value) ? value.filter(Boolean) : [];
    }

    function translateWarningCode(code) {
        switch (String(code || '').trim()) {
            case 'sync_folder':
                return translate('storage.warningSyncFolder', '目标路径位于同步盘目录，迁移期间可能受到同步程序干扰。');
            case 'external_volume':
                return translate('storage.warningExternalVolume', '目标路径位于外置卷或挂载卷，稳定性取决于卷是否持续可用。');
            case 'network_share':
                return translate('storage.warningNetworkShare', '目标路径位于网络共享目录，连接波动可能影响迁移稳定性。');
            case 'symlink_path':
                return translate('storage.warningSymlink', '目标路径命中了符号链接或等价重定向目录，请确认真实落点正确。');
            case 'target_has_existing_content':
                return translate('storage.warningTargetHasExistingContent', '目标路径已经包含运行时数据，开始迁移前需要二次确认。');
            default:
                return code;
        }
    }

    function existingTargetConfirmationText() {
        return translate(
            'storage.confirmExistingTargetContent',
            '目标文件夹已经包含 N.E.K.O 运行时数据。继续后，迁移会覆盖目标中的同名运行时数据目录，目标目录里的其他文件会保留。确认继续吗？'
        );
    }

    function translateResponseErrorCode(code, fallbackText) {
        switch (String(code || '').trim()) {
            case 'directory_picker_unavailable':
                return translate('storage.pickFolderUnavailable', '当前系统目录选择器不可用，请手动输入路径。');
            case 'insufficient_space':
                return translate('storage.blockingInsufficientSpace', '目标卷剩余空间不足，无法安全执行关闭后的迁移。');
            case 'recovery_source_unavailable':
                return translate('storage.recoverySourceUnavailable', '原始数据路径当前不可用。请先重连原路径，或显式切回推荐默认路径继续当前会话。');
            case 'restart_not_required':
                return translate('storage.restartNotRequired', '目标路径与当前路径一致，不需要关闭当前实例。');
            case 'restart_schedule_failed':
                return translate('storage.restartScheduleFailed', '受控关闭启动失败，请稍后重试。');
            case 'restart_unavailable':
                return translate('storage.restartUnavailable', '当前实例暂时无法执行受控关闭，请稍后重试。');
            case 'retained_source_cleanup_failed':
                return translate('storage.retainedSourceCleanupFailed', '清理旧数据保留目录失败，请稍后重试。');
            case 'retained_source_mismatch':
                return translate('storage.retainedSourceMismatch', '请求的清理路径与当前保留目录不一致，请刷新后重试。');
            case 'retained_source_not_found':
                return translate('storage.retainedSourceNotFound', '当前没有可清理的旧数据保留目录。');
            case 'selected_root_inside_state':
                return translate('storage.selectedRootInsideState', '该位置位于 N.E.K.O 运行时状态目录内，不能作为存储根目录。');
            case 'selected_root_unavailable':
                return translate('storage.selectedRootUnavailable', '原始数据路径当前仍不可用，请先恢复该路径后再重试。');
            case 'startup_release_failed':
                return translate('storage.selectionSubmitFailed', '提交存储位置选择失败，请稍后重试。');
            case 'storage_bootstrap_blocking':
                return translate('storage.storageBootstrapBlocking', '当前存储状态仍需恢复或迁移，暂时不能继续当前会话。');
            case 'target_confirmation_required':
                return existingTargetConfirmationText();
            case 'target_not_empty':
                return translate('storage.targetNotEmpty', '目标路径已经包含运行时数据，请确认目标目录后再继续迁移。');
            case 'target_not_writable':
                return translate('storage.blockingTargetNotWritable', '目标路径当前不可写，无法开始关闭后的迁移流程。');
            default:
                return fallbackText || '';
        }
    }

    function translatePreflightBlocking(preflight) {
        if (!preflight || !preflight.blocking_error_code) return '';
        return translateResponseErrorCode(
            preflight.blocking_error_code,
            translate('storage.blockingGeneric', '当前无法使用所选存储位置，请换一个位置或稍后重试。')
        );
    }

    function translateMaintenanceSubtitle(statusPayload, fallbackText) {
        var blockingReason = String(
            statusPayload && (
                statusPayload.blocking_reason
                || (statusPayload.storage && statusPayload.storage.blocking_reason)
            ) || ''
        ).trim();

        switch (blockingReason) {
            case 'migration_pending':
                return translate('storage.maintenanceWaitingSubtitle', '当前实例即将关闭，数据会在关闭后迁移并自动重启。');
            case 'recovery_required':
                return translate('storage.recoveryRequired', '检测到需要恢复的存储状态，请先重新确认本次使用的存储位置。');
            case 'selection_required':
                return translate('storage.selectionSubtitle', '应用已经正常打开。接下来请先在当前页面内确认存储位置，再继续使用。');
            default:
                return fallbackText || translate('storage.maintenanceWaitingSubtitle', '当前实例即将关闭，数据会在关闭后迁移并自动重启。');
        }
    }

    function extractPreflightDetails(payload, fallbackTargetPath) {
        if (!payload || typeof payload !== 'object') {
            return {
                target_root: String(fallbackTargetPath || '').trim(),
                estimated_required_bytes: 0,
                target_free_bytes: 0,
                permission_ok: true,
                warning_codes: [],
                target_has_existing_content: false,
                requires_existing_target_confirmation: false,
                existing_target_confirmation_message: '',
                blocking_error_code: '',
                blocking_error_message: ''
            };
        }

        return {
            target_root: String(payload.target_root || payload.selected_root || fallbackTargetPath || '').trim(),
            estimated_required_bytes: Number(payload.estimated_required_bytes || 0),
            target_free_bytes: Number(payload.target_free_bytes || 0),
            permission_ok: payload.permission_ok !== false,
            warning_codes: normalizeWarningCodes(payload.warning_codes),
            restart_mode: String(payload.restart_mode || 'migrate_after_shutdown').trim(),
            target_has_existing_content: payload.target_has_existing_content === true,
            requires_existing_target_confirmation: payload.requires_existing_target_confirmation === true,
            existing_target_confirmation_message: String(payload.existing_target_confirmation_message || '').trim(),
            blocking_error_code: String(payload.blocking_error_code || '').trim(),
            blocking_error_message: String(payload.blocking_error_message || '').trim()
        };
    }

    function registerActionButton(button) {
        if (!button) return button;
        state.actionButtons.push(button);
        return button;
    }

    function resolveStartupDecision(payload) {
        if (!state.startupDecision) {
            state.startupDecision = createDeferred();
        }
        state.startupDecision.resolve(payload || {
            canContinue: true,
            reason: 'continue_current_session',
        });
    }

    function setPhase(phase) {
        state.phase = phase;
        if (!state.overlay) return;

        state.overlay.hidden = phase === 'hidden';
        document.body.classList.toggle('storage-location-modal-open', phase !== 'hidden');

        state.loadingView.hidden = phase !== 'loading';
        state.maintenanceView.hidden = phase !== 'maintenance';
        state.selectionView.hidden = phase !== 'selection_required';
        state.errorView.hidden = phase !== 'error';
    }

    function hideOverlay() {
        // 正常模式下，覆盖层关闭后是否再次出现由后端状态决定：
        // 首次未完成、存在待迁移检查点或恢复态时仍会阻断，其余情况直接放行。
        setPhase('hidden');
    }

    function setSubmitting(submitting) {
        state.submitting = !!submitting;
        state.actionButtons.forEach(function (button) {
            button.disabled = state.submitting || !!button.dataset.forceDisabled;
        });
        if (state.customInput) {
            state.customInput.disabled = state.submitting;
        }
    }

    function setSelectionStatus(message, isError, options) {
        // options.i18nKey + options.i18nFallback 表示这条 status 是翻译出来的，
        // rebuildModalForLocale 切语言后会用 translate(key, fallback) 重新算文案。
        // 不传 options 则视作运行时动态文本（如后端 error.message），rebuild 时
        // 沿用旧文案不重译。
        var text = String(message || '').trim();
        if (text && options && options.i18nKey) {
            state.selectionStatusI18nKey = String(options.i18nKey);
            state.selectionStatusI18nFallback = String(options.i18nFallback || '');
        } else {
            state.selectionStatusI18nKey = '';
            state.selectionStatusI18nFallback = '';
        }
        if (!state.selectionStatus) return;
        state.selectionStatus.hidden = !text;
        state.selectionStatus.textContent = text;
        state.selectionStatus.classList.toggle('storage-location-note--error', !!isError && !!text);
    }

    function setSelectionStatusByKey(key, fallback, isError) {
        setSelectionStatus(translate(key, fallback), isError, {
            i18nKey: key,
            i18nFallback: fallback,
        });
    }

    function setLoadingCopy(title, subtitle) {
        if (state.loadingTitle && typeof title === 'string' && title) {
            state.loadingTitle.textContent = title;
        }
        if (state.loadingSubtitle && typeof subtitle === 'string' && subtitle) {
            state.loadingSubtitle.textContent = subtitle;
        }
    }

    function setMaintenanceCopy(title, subtitle, status) {
        if (state.maintenanceTitle && typeof title === 'string' && title) {
            state.maintenanceTitle.textContent = title;
        }
        if (state.maintenanceSubtitle && typeof subtitle === 'string' && subtitle) {
            state.maintenanceSubtitle.textContent = subtitle;
        }
        if (state.maintenanceStatus) {
            state.maintenanceStatus.textContent = String(status || '').trim();
        }
    }

    function sleep(ms) {
        return new Promise(function (resolve) {
            window.setTimeout(resolve, ms);
        });
    }

    function shouldBlockMainUi(statusPayload) {
        if (!statusPayload || typeof statusPayload !== 'object') {
            return true;
        }

        var storage = statusPayload.storage || {};
        return statusPayload.ready !== true
            || statusPayload.status === 'migration_required'
            || !!storage.selection_required
            || !!storage.migration_pending
            || !!storage.recovery_required;
    }

    function shouldShowSelectionView(bootstrapPayload) {
        if (!bootstrapPayload || typeof bootstrapPayload !== 'object') {
            return false;
        }

        var blockingReason = String(bootstrapPayload.blocking_reason || '').trim();
        return blockingReason === 'selection_required'
            || blockingReason === 'recovery_required'
            || !!bootstrapPayload.selection_required
            || !!bootstrapPayload.recovery_required;
    }

    function shouldShowMaintenanceView(bootstrapPayload) {
        if (!bootstrapPayload || typeof bootstrapPayload !== 'object') {
            return false;
        }

        var blockingReason = String(bootstrapPayload.blocking_reason || '').trim();
        return blockingReason === 'migration_pending'
            || (!!bootstrapPayload.migration_pending && !bootstrapPayload.recovery_required);
    }

    async function fetchSystemStatus() {
        var response = await fetch('/api/system/status', {
            cache: 'no-store',
            headers: {
                'Accept': 'application/json'
            }
        });
        if (!response.ok) {
            throw new Error('system status request failed: ' + response.status);
        }

        var payload = await response.json();
        if (!payload || payload.ok !== true) {
            throw new Error(
                translate('storage.systemStatusUnexpected', '存储启动状态接口返回了未识别的结果。')
            );
        }
        state.systemStatus = payload;
        return payload;
    }

    async function fetchStorageLocationStatus() {
        var response = await fetch('/api/storage/location/status', {
            cache: 'no-store',
            headers: {
                'Accept': 'application/json'
            }
        });
        if (!response.ok) {
            throw new Error('storage location status request failed: ' + response.status);
        }

        var payload = await response.json();
        if (!payload || payload.ok !== true) {
            throw new Error(
                translate('storage.statusUnexpected', '存储维护状态接口返回了未识别的结果。')
            );
        }
        return payload;
    }

    async function waitForSystemStatus() {
        var lastError = null;

        for (var attempt = 0; attempt < 20; attempt += 1) {
            try {
                var payload = await fetchSystemStatus();
                if (payload.status !== 'starting') {
                    return payload;
                }
            } catch (error) {
                lastError = error;
            }

            setLoadingCopy(
                translate('storage.loadingTitle', '正在确认存储布局状态'),
                translate('storage.loadingWaitSubtitle', '主业务界面会在存储状态确认完成后再继续加载。')
            );
            await sleep(250);
        }

        throw lastError || new Error(
            translate('storage.systemStatusUnavailable', '暂时无法确认本地服务状态，请重试。')
        );
    }

    function resetPreviewState() {
        state.pendingSelection.path = '';
        state.pendingSelection.source = '';
        state.pendingSelection.preflight = null;
        if (state.previewPanel) {
            state.previewPanel.hidden = true;
        }
    }

    function renderLegacyList() {
        if (!state.legacyChoices) return;
        clearChildren(state.legacyChoices);

        var legacySources = Array.isArray(state.bootstrap && state.bootstrap.legacy_sources)
            ? state.bootstrap.legacy_sources
            : [];

        if (!legacySources.length) {
            state.legacyChoices.appendChild(
                createElement(
                    'p',
                    'storage-location-empty',
                    translate('storage.legacyChoiceEmpty', '未检测到可直接复用的旧数据目录，可直接选择文件夹或手动输入路径。')
                )
            );
            return;
        }

        legacySources.forEach(function (path, index) {
            var choice = createElement('button', 'storage-location-choice');
            choice.type = 'button';
            if (state.otherSelection.key === 'legacy-' + index) {
                choice.classList.add('is-active');
            }
            choice.addEventListener('click', function () {
                state.otherSelection.key = 'legacy-' + index;
                state.otherSelection.path = path;
                renderLegacyList();
                updateOtherButtonState();
            });

            var title = createElement('div', 'storage-location-choice-title');
            title.appendChild(createElement('span', '', translate('storage.useLegacyPath', '使用该旧数据路径')));
            title.appendChild(createElement('span', 'storage-location-choice-check', state.otherSelection.key === 'legacy-' + index ? '✓' : ''));
            choice.appendChild(title);
            choice.appendChild(createElement('div', 'storage-location-path', path));
            state.legacyChoices.appendChild(choice);
        });
    }

    function updateSelectionSummary() {
        if (!state.bootstrap) return;

        var currentRoot = state.bootstrap.current_root || '';
        var recommendedRoot = state.bootstrap.recommended_root || '';
        var currentIsRecommended = pathEquals(currentRoot, recommendedRoot);

        state.currentPath.textContent = currentRoot;
        state.recommendedPath.textContent = recommendedRoot;

        if (state.bootstrap.migration_pending) {
            state.banner.hidden = false;
            state.banner.textContent = translate(
                'storage.migrationPending',
                '检测到尚未完成的迁移计划。当前主页会继续保持阻断，直到服务恢复到可继续状态。'
            );
        } else if (state.bootstrap.recovery_required) {
            state.banner.hidden = false;
            state.banner.textContent = translate('storage.recoveryRequired', '检测到需要恢复的存储状态，请先重新确认本次使用的存储位置。');
        } else {
            state.banner.hidden = true;
            state.banner.textContent = '';
        }

        renderLegacyList();
        updateOtherButtonState();
    }

    function updateOtherButtonState() {
        if (!state.useOtherButton) return;
        var disabled = !String(state.otherSelection.path || '').trim();
        state.useOtherButton.dataset.forceDisabled = disabled ? '1' : '';
        state.useOtherButton.disabled = state.submitting || disabled;
    }

    function openOtherPanel() {
        resetPreviewState();
        setSelectionStatus('', false);
        state.otherPanel.hidden = false;
        if (state.customInput) {
            state.customInput.focus();
        }
    }

    function backToSelection() {
        var pendingSource = state.pendingSelection.source;
        resetPreviewState();
        setSelectionStatus('', false);
        if (pendingSource === 'custom' || pendingSource === 'legacy') {
            state.otherPanel.hidden = false;
        }
        setPhase('selection_required');
    }

    function getDirectoryPickerStartPath() {
        var currentInputPath = String(state.customInput && state.customInput.value || '').trim();
        if (currentInputPath) return currentInputPath;
        if (String(state.otherSelection.path || '').trim()) return String(state.otherSelection.path || '').trim();
        if (state.bootstrap) {
            if (String(state.bootstrap.recommended_root || '').trim()) return String(state.bootstrap.recommended_root || '').trim();
            if (String(state.bootstrap.current_root || '').trim()) return String(state.bootstrap.current_root || '').trim();
        }
        return '';
    }

    async function pickDirectoryWithHostBridge(startPath) {
        var host = window.nekoHost;
        if (!host || typeof host.pickDirectory !== 'function') {
            return null;
        }

        try {
            var result = await host.pickDirectory({
                startPath: startPath,
                title: translate('storage.pickFolder', '选择文件夹')
            });
            if (!result || typeof result !== 'object') {
                throw new Error('Host directory picker returned an invalid result.');
            }
            if (result.cancelled) {
                return {
                    ok: true,
                    cancelled: true,
                    selected_root: ''
                };
            }
            var selectedRoot = String(result.selected_root || '').trim();
            if (!selectedRoot) {
                throw new Error('Host directory picker returned an empty path.');
            }
            return {
                ok: true,
                cancelled: false,
                selected_root: selectedRoot
            };
        } catch (error) {
            console.warn('[storage-location] host directory picker failed, falling back to backend picker', error);
            return null;
        }
    }

    async function pickDirectoryWithBackend(startPath) {
        var response = await fetch('/api/storage/location/pick-directory', {
            method: 'POST',
            headers: {
                'Accept': 'application/json',
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                start_path: startPath
            })
        });

        var payload = null;
        try {
            payload = await response.json();
        } catch (_) {}

        if (!response.ok || !payload || payload.ok !== true) {
            throw new Error(
                extractResponseError(
                    payload,
                    translate('storage.pickFolderFailed', '打开文件夹选择器失败，请手动输入路径。')
                )
            );
        }

        return payload;
    }

    function getHostBridge() {
        var host = window.nekoHost;
        return host && typeof host === 'object' ? host : null;
    }

    function canOpenPathWithHostBridge() {
        var host = getHostBridge();
        return !!(host && typeof host.openPath === 'function');
    }

    async function openPathWithHostBridge(targetPath) {
        var host = getHostBridge();
        if (!host || typeof host.openPath !== 'function') {
            throw new Error(translate('storage.openDirectoryUnavailable', '当前环境不支持直接打开目录。'));
        }

        var result = await host.openPath({
            path: targetPath
        });
        if (result && typeof result === 'object' && result.ok === false) {
            throw new Error(
                String(result.error || translate('storage.openDirectoryFailed', '打开目录失败。')).trim()
            );
        }
    }

    async function pickOtherDirectory() {
        if (!state.customInput) return;

        setSubmitting(true);
        setSelectionStatus('', false);
        try {
            var startPath = getDirectoryPickerStartPath();
            var payload = await pickDirectoryWithHostBridge(startPath);
            if (!payload) {
                payload = await pickDirectoryWithBackend(startPath);
            }

            if (payload.cancelled) {
                return;
            }

            var selectedRoot = String(payload.selected_root || '').trim();
            if (!selectedRoot) {
                return;
            }

            applyCustomStorageRootDisplay(selectedRoot);
            renderLegacyList();
            updateOtherButtonState();
            state.customInput.focus();
        } catch (error) {
            setSelectionStatus(
                String((error && error.message) || error || translate('storage.pickFolderFailed', '打开文件夹选择器失败，请手动输入路径。')),
                true
            );
        } finally {
            setSubmitting(false);
        }
    }

    function updateRestartPreviewPreflight(preflight) {
        if (!preflight) return;
        if (state.previewEstimated) {
            state.previewEstimated.textContent = formatBytes(preflight.estimated_required_bytes);
        }
        if (state.previewFreeSpace) {
            state.previewFreeSpace.textContent = formatBytes(preflight.target_free_bytes);
        }
        if (state.previewPermission) {
            state.previewPermission.textContent = preflight.permission_ok
                ? translate('storage.permissionOk', '当前可写')
                : translate('storage.permissionBlocked', '当前不可写');
        }
        if (state.previewWarnings) {
            var warningCodes = normalizeWarningCodes(preflight.warning_codes);
            if (preflight.target_has_existing_content && warningCodes.indexOf('target_has_existing_content') === -1) {
                warningCodes.push('target_has_existing_content');
            }
            var warnings = warningCodes.map(translateWarningCode);
            state.previewWarnings.textContent = warnings.length
                ? warnings.join('；')
                : translate('storage.noWarnings', '当前未检测到需要额外提示的风险项。');
        }
        if (state.previewBlocking) {
            var blockingText = translatePreflightBlocking(preflight);
            var confirmationText = preflight.requires_existing_target_confirmation === true
                ? existingTargetConfirmationText()
                : '';
            var noteText = blockingText || confirmationText;
            state.previewBlocking.hidden = !noteText;
            state.previewBlocking.textContent = noteText;
            state.previewBlocking.classList.toggle('storage-location-note--error', !!blockingText);
        }
        if (state.previewConfirmButton) {
            state.previewConfirmButton.dataset.forceDisabled = preflight.blocking_error_code ? '1' : '';
            state.previewConfirmButton.disabled = state.submitting || !!state.previewConfirmButton.dataset.forceDisabled;
        }
    }

    // 仅根据 preflight 填充预览面板的字段并显示预览面板，不切换 phase、
    // 不修改 otherPanel 的可见性、不清空 selectionStatus。供 showRestartRequired
    // 走完整流程，以及 rebuildModalForLocale 在快照恢复路径上单独使用。
    function populateRestartPreview(payload, fallbackTargetPath, selectionSource) {
        if (!state.bootstrap || !state.previewPanel) return null;

        var preflight = extractPreflightDetails(payload, fallbackTargetPath);
        state.pendingSelection.path = preflight.target_root || '';
        state.pendingSelection.source = selectionSource || '';
        state.pendingSelection.preflight = preflight;
        state.previewSource.textContent = state.bootstrap.current_root || '';
        state.previewTarget.textContent = preflight.target_root || '';
        if (preflight.restart_mode === 'rebind_only') {
            state.previewText.textContent = translate(
                'storage.rebindPreviewNotice',
                '后端已确认：原路径已经可以重新连接。后续会关闭当前实例并自动重启到该路径，本次不会复制运行时数据。'
            );
            if (state.previewConfirmButton) {
                state.previewConfirmButton.textContent = translate('storage.confirmReconnect', '确认关闭并重连路径');
            }
        } else {
            state.previewText.textContent = selectionSource === 'recommended'
                ? translate('storage.recommendedPreviewNotice', '后端已确认：如果后续改用推荐位置，需要先关闭当前实例，再迁移数据并自动重启。')
                : translate('storage.customPreviewNotice', '后端已确认：如果后续改用这个位置，也需要先关闭当前实例，再迁移数据并自动重启。');
            if (state.previewConfirmButton) {
                state.previewConfirmButton.textContent = translate('storage.confirmRestart', '确认关闭并迁移');
            }
        }
        updateRestartPreviewPreflight(preflight);
        state.previewPanel.hidden = false;
        return preflight;
    }

    function showRestartRequired(payload, fallbackTargetPath, selectionSource) {
        if (!populateRestartPreview(payload, fallbackTargetPath, selectionSource)) return;
        state.otherPanel.hidden = true;
        setSelectionStatus('', false);
        setPhase('selection_required');
    }

    function buildMaintenanceStatusText(statusPayload) {
        if (!statusPayload || typeof statusPayload !== 'object') {
            return translate('storage.maintenanceWaitingStatus', '服务尚未恢复前，页面会继续停留在这里并自动重试连接。');
        }

        var targetRoot = '';
        if (statusPayload.migration && typeof statusPayload.migration === 'object') {
            targetRoot = String(statusPayload.migration.target_root || '').trim();
        }
        if (!targetRoot && state.pendingSelection && state.pendingSelection.preflight) {
            targetRoot = String(state.pendingSelection.preflight.target_root || '').trim();
        }
        if (targetRoot) {
            return translate('storage.maintenanceTargetStatus', '目标路径已记录，正在等待服务关闭并恢复：') + ' ' + targetRoot;
        }
        if (String(statusPayload.blocking_reason || '').trim() === 'recovery_required') {
            return translate('storage.recoveryRequired', '检测到需要恢复的存储状态，请先重新确认本次使用的存储位置。');
        }
        return translate('storage.maintenanceWaitingStatus', '服务尚未恢复前，页面会继续停留在这里并自动重试连接。');
    }

    function buildMaintenanceProgressModel(statusPayload) {
        var lifecycleState = String(
            statusPayload && (statusPayload.lifecycle_state || statusPayload.status) || ''
        ).trim();
        var migrationStage = String(
            statusPayload && (statusPayload.migration_stage || (statusPayload.migration && statusPayload.migration.status)) || ''
        ).trim();
        var restartMode = String(
            statusPayload && statusPayload.restart_mode
            || state.pendingSelection && state.pendingSelection.preflight && state.pendingSelection.preflight.restart_mode
            || ''
        ).trim();
        var isRebindOnly = restartMode === 'rebind_only';
        var hasError = lifecycleState === 'recovery_required' || migrationStage === 'failed' || migrationStage === 'rollback_required';
        var percent = 14;
        var activeIndex = 0;
        var label = translate('storage.progressWaitingShutdown', '正在等待当前实例安全关闭');

        if (lifecycleState === 'ready') {
            percent = 100;
            activeIndex = 3;
            label = translate('storage.progressRecovered', '服务已恢复，正在重新连接页面');
        } else {
            switch (migrationStage) {
                case 'pending':
                    percent = 18;
                    activeIndex = 0;
                    label = translate('storage.progressPending', '目标路径已记录，正在准备关闭当前实例');
                    break;
                case 'preflight':
                    percent = 34;
                    activeIndex = 1;
                    label = isRebindOnly
                        ? translate('storage.progressRebindPreflight', '正在准备重连原始存储位置')
                        : translate('storage.progressPreflight', '正在检查目标位置并准备迁移');
                    break;
                case 'copying':
                    percent = 56;
                    activeIndex = 1;
                    label = translate('storage.progressCopying', '正在迁移运行时数据');
                    break;
                case 'verifying':
                    percent = 74;
                    activeIndex = 2;
                    label = translate('storage.progressVerifying', '正在校验迁移结果');
                    break;
                case 'committing':
                    percent = 86;
                    activeIndex = 2;
                    label = translate('storage.progressCommitting', '正在提交新的存储位置');
                    break;
                case 'retaining_source':
                    percent = 94;
                    activeIndex = 2;
                    label = translate('storage.progressRetaining', '正在保留旧数据目录以便后续手动清理');
                    break;
                case 'completed':
                    percent = 98;
                    activeIndex = 3;
                    label = translate('storage.progressCompleted', '迁移已完成，正在恢复服务');
                    break;
                case 'failed':
                case 'rollback_required':
                    percent = 100;
                    activeIndex = 2;
                    label = translate('storage.progressFailed', '迁移未能完成，正在等待恢复处理');
                    break;
                default:
                    percent = isRebindOnly ? 38 : 14;
                    activeIndex = isRebindOnly ? 1 : 0;
                    label = isRebindOnly
                        ? translate('storage.progressRebinding', '正在关闭当前实例并重连原始路径')
                        : translate('storage.progressWaitingShutdown', '正在等待当前实例安全关闭');
                    break;
            }
        }

        return {
            percent: percent,
            activeIndex: activeIndex,
            hasError: hasError,
            label: label,
            steps: [
                translate('storage.progressStepShutdown', '关闭当前实例'),
                translate('storage.progressStepTransfer', '处理存储目录'),
                translate('storage.progressStepCommit', '校验并生效'),
                translate('storage.progressStepRecover', '恢复服务')
            ]
        };
    }

    function applyMaintenanceProgress(statusPayload) {
        // 缓存最近一次驱动进度条渲染的 payload，供 rebuildModalForLocale 在
        // 语言切换重建后立刻按当前 locale 重渲一次进度条，避免等下一次轮询。
        state.lastMaintenanceProgressPayload = statusPayload || null;

        if (!state.maintenanceProgressBar || !state.maintenanceProgressFill) {
            return;
        }

        var progress = buildMaintenanceProgressModel(statusPayload);
        state.maintenanceProgressBar.setAttribute('aria-valuenow', String(progress.percent));
        state.maintenanceProgressBar.setAttribute('aria-valuetext', progress.label);
        state.maintenanceProgressFill.style.width = progress.percent + '%';
        if (state.maintenanceProgressLabel) {
            state.maintenanceProgressLabel.textContent = progress.label;
        }
        if (state.maintenanceProgressValue) {
            state.maintenanceProgressValue.textContent = progress.percent + '%';
        }
        state.maintenanceProgressBar.classList.toggle('is-error', !!progress.hasError);

        state.maintenanceProgressSteps.forEach(function (step, index) {
            step.textContent = progress.steps[index] || '';
            step.classList.toggle('is-active', index === progress.activeIndex);
            step.classList.toggle('is-completed', index < progress.activeIndex || progress.percent >= 100);
        });
    }

    function buildCompletionNoticeCard() {
        if (state.completionCard) return state.completionCard;

        var card = createElement('section', 'storage-location-completion-card');
        card.hidden = true;
        card.appendChild(buildStorageLocationCloseButton(function () {
            card.hidden = true;
        }));

        var title = createElement('h3', 'storage-location-panel-title', translate('storage.completionTitle', '存储迁移已完成'));
        var message = createElement('p', 'storage-location-note', translate('storage.completionMessage', '新的运行目录已经生效，旧数据目录目前仍保留，是否清理由你手动决定。'));
        var pathList = createElement('div', 'storage-location-path-list');

        var targetItem = buildInfoPathRow(translate('storage.targetLabel', '当前生效路径'), 'completionTarget');
        var retainedItem = buildInfoPathRow(translate('storage.retainedRoot', '当前保留目录'), 'completionRetained');
        pathList.appendChild(targetItem);
        pathList.appendChild(retainedItem);

        var actions = createElement('div', 'storage-location-actions');
        var openTargetButton = createElement('button', 'storage-location-btn storage-location-btn--secondary', translate('storage.openActiveRoot', '打开当前目录'));
        openTargetButton.type = 'button';
        openTargetButton.addEventListener('click', function () {
            openCompletionDirectory('target');
        });
        actions.appendChild(openTargetButton);

        var openRetainedButton = createElement('button', 'storage-location-btn storage-location-btn--secondary', translate('storage.openRetainedRoot', '打开旧目录'));
        openRetainedButton.type = 'button';
        openRetainedButton.addEventListener('click', function () {
            openCompletionDirectory('retained');
        });
        actions.appendChild(openRetainedButton);

        var cleanupButton = createElement('button', 'storage-location-btn storage-location-btn--primary', translate('storage.cleanupRetainedRoot', '清理旧数据目录'));
        cleanupButton.type = 'button';
        cleanupButton.addEventListener('click', cleanupRetainedSourceRoot);
        actions.appendChild(cleanupButton);

        state.completionCard = card;
        state.completionTitle = title;
        state.completionMessage = message;
        state.completionOpenTargetButton = openTargetButton;
        state.completionOpenRetainedButton = openRetainedButton;
        state.completionCleanupButton = cleanupButton;

        title.classList.add('storage-location-panel-title--with-close');
        title.classList.add('storage-location-completion-drag-handle');
        card.appendChild(title);
        card.appendChild(message);
        card.appendChild(pathList);
        card.appendChild(actions);
        document.body.appendChild(card);
        installCompletionCardDragging(card);
        return card;
    }

    function installCompletionCardDragging(card) {
        var dragState = null;

        function isInteractiveTarget(target) {
            return !!(
                target
                && target.closest
                && target.closest('button, a, input, textarea, select, [role="button"]')
            );
        }

        function moveCard(clientX, clientY) {
            if (!dragState) return;
            var nextLeft = clientX - dragState.offsetX;
            var nextTop = clientY - dragState.offsetY;
            var maxLeft = Math.max(0, window.innerWidth - dragState.width);
            var maxTop = Math.max(0, window.innerHeight - dragState.height);

            card.style.left = Math.min(Math.max(0, nextLeft), maxLeft) + 'px';
            card.style.top = Math.min(Math.max(0, nextTop), maxTop) + 'px';
            card.style.right = 'auto';
            card.style.bottom = 'auto';
        }

        function stopDragging() {
            if (!dragState) return;
            dragState = null;
            card.classList.remove('is-dragging');
            document.removeEventListener('pointermove', onPointerMove);
            document.removeEventListener('pointerup', stopDragging);
            document.removeEventListener('pointercancel', stopDragging);
        }

        function onPointerMove(event) {
            moveCard(event.clientX, event.clientY);
        }

        card.addEventListener('pointerdown', function (event) {
            if (event.button !== 0 || isInteractiveTarget(event.target)) {
                return;
            }

            var rect = card.getBoundingClientRect();
            dragState = {
                offsetX: event.clientX - rect.left,
                offsetY: event.clientY - rect.top,
                width: rect.width,
                height: rect.height
            };
            card.style.width = rect.width + 'px';
            card.style.left = rect.left + 'px';
            card.style.top = rect.top + 'px';
            card.style.right = 'auto';
            card.style.bottom = 'auto';
            card.classList.add('is-dragging');
            document.addEventListener('pointermove', onPointerMove);
            document.addEventListener('pointerup', stopDragging);
            document.addEventListener('pointercancel', stopDragging);
            event.preventDefault();
        });
    }

    function applyCompletionNotice(notice) {
        state.completionNotice = notice && typeof notice === 'object' ? notice : null;
        if (!state.completionNotice || state.completionNotice.completed !== true || !state.completionNotice.retained_root_exists) {
            if (state.completionCard) {
                state.completionCard.hidden = true;
            }
            return;
        }

        var card = buildCompletionNoticeCard();
        state.completionMessage.textContent = translate('storage.completionMessage', '新的运行目录已经生效，旧数据目录目前仍保留，是否清理由你手动决定。');
        state.completionTarget.textContent = String(state.completionNotice.target_root || '').trim();
        state.completionRetained.textContent = String(state.completionNotice.retained_root || '').trim();
        state.completionOpenTargetButton.hidden = !canOpenPathWithHostBridge() || !String(state.completionNotice.target_root || '').trim();
        state.completionOpenRetainedButton.hidden = !canOpenPathWithHostBridge() || !String(state.completionNotice.retained_root || '').trim();
        state.completionCleanupButton.hidden = !state.completionNotice.cleanup_available;
        card.hidden = false;
    }

    async function checkReadyStateCompletionNotice() {
        try {
            var statusPayload = await fetchStorageLocationStatus();
            if (statusPayload && statusPayload.ready === true) {
                applyCompletionNotice(statusPayload.completion_notice);
                return !!(
                    statusPayload.completion_notice
                    && statusPayload.completion_notice.completed === true
                    && statusPayload.completion_notice.retained_root_exists
                );
            }
        } catch (error) {
            console.warn('[storage-location] completion notice check failed', error);
        }
        return false;
    }

    function clearCompletionNoticePolling() {
        if (state.completionPollTimer) {
            window.clearTimeout(state.completionPollTimer);
            state.completionPollTimer = null;
        }
    }

    function scheduleCompletionNoticePolling() {
        clearCompletionNoticePolling();
        state.completionPollAttempts = 0;

        async function tick() {
            state.completionPollAttempts += 1;
            var completed = await checkReadyStateCompletionNotice();
            if (completed || state.completionPollAttempts >= 10) {
                clearCompletionNoticePolling();
                return;
            }
            state.completionPollTimer = window.setTimeout(tick, 500);
        }

        state.completionPollTimer = window.setTimeout(tick, 0);
    }

    async function openCompletionDirectory(kind) {
        if (!state.completionNotice || state.completionNotice.completed !== true) {
            return;
        }

        var isRetained = kind === 'retained';
        var targetPath = String(
            isRetained
                ? state.completionNotice.retained_root || ''
                : state.completionNotice.target_root || ''
        ).trim();
        if (!targetPath) {
            return;
        }

        var button = isRetained ? state.completionOpenRetainedButton : state.completionOpenTargetButton;
        if (button) {
            button.disabled = true;
        }

        try {
            await openPathWithHostBridge(targetPath);
        } catch (error) {
            console.warn('[storage-location] open directory failed', error);
            if (typeof window.showStatusToast === 'function') {
                window.showStatusToast(
                    String((error && error.message) || error || translate('storage.openDirectoryFailed', '打开目录失败。')),
                    4000
                );
            }
        } finally {
            if (button) {
                button.disabled = false;
            }
        }
    }

    async function cleanupRetainedSourceRoot() {
        if (!state.completionNotice || state.completionNotice.cleanup_available !== true) {
            return;
        }

        var retainedRoot = String(state.completionNotice.retained_root || '').trim();
        if (!retainedRoot) {
            return;
        }

        if (!window.confirm(translate('storage.cleanupRetainedRootConfirm', '这会删除当前保留的旧数据目录，且不会影响当前已经生效的新目录。要继续吗？'))) {
            return;
        }

        if (state.completionCleanupButton) {
            state.completionCleanupButton.disabled = true;
        }

        try {
            var response = await fetch('/api/storage/location/retained-source/cleanup', {
                method: 'POST',
                headers: {
                    'Accept': 'application/json',
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    retained_root: retainedRoot
                })
            });
            var payload = null;
            try {
                payload = await response.json();
            } catch (_) {}
            if (!response.ok || !payload || payload.ok !== true) {
                throw new Error(extractResponseError(payload, translate('storage.cleanupRetainedRootFailed', '清理旧数据目录失败，请稍后重试。')));
            }

            applyCompletionNotice({ completed: false });
            if (typeof window.showStatusToast === 'function') {
                window.showStatusToast(
                    translate('storage.cleanupRetainedRootDone', '旧数据目录已清理，当前仅保留新的运行目录。'),
                    4000
                );
            }
        } catch (error) {
            if (state.completionCleanupButton) {
                state.completionCleanupButton.disabled = false;
            }
            if (typeof window.showStatusToast === 'function') {
                window.showStatusToast(
                    String((error && error.message) || error || translate('storage.cleanupRetainedRootFailed', '清理旧数据目录失败，请稍后重试。')),
                    5000
                );
            }
        }
    }

    var STORAGE_ERROR_DETAIL_MAX_LEN = 200;

    function truncateErrorDetail(text) {
        var trimmed = String(text || '').trim();
        if (trimmed.length <= STORAGE_ERROR_DETAIL_MAX_LEN) return trimmed;
        return trimmed.slice(0, STORAGE_ERROR_DETAIL_MAX_LEN) + '…';
    }

    function extractResponseError(payload, fallbackText) {
        if (payload && typeof payload === 'object') {
            var rawError = typeof payload.error === 'string' ? String(payload.error).trim() : '';
            var code = String(payload.error_code || payload.blocking_error_code || '').trim();
            var codedText = translateResponseErrorCode(code, '');
            if (codedText) {
                // startup_release_failed 这类后端会把异常细节塞进 payload.error
                // （f"... {exc}" 风格）。完整字符串可能含路径/异常类名/栈片段，
                // 直接展示既不友好也可能泄露内部信息。所以：
                //   - 完整原文打到 console.warn 给开发者看
                //   - UI 只展示翻译后的概括语 + 裁短的尾巴（≤200 字符）
                if (code === 'startup_release_failed' && rawError && rawError !== codedText) {
                    try {
                        console.warn('[storage-location] startup_release_failed detail:', rawError);
                    } catch (_) {}
                    return codedText + ' ' + truncateErrorDetail(rawError);
                }
                return codedText;
            }
            // 未在 translateResponseErrorCode 命中的 error_code 走通用兜底：
            // 本仓 i18n 设计哲学是「错误码翻译完整性在评审时强制，不做运行时
            // hit 兜底」，所以这里不要把后端 raw payload.error 透出给 UI——
            // 详情打到 console 给开发者，UI 走调用方传入的 fallbackText 概括语。
            if (rawError) {
                try {
                    console.warn(
                        '[storage-location] unhandled storage error_code, raw detail:',
                        code || '(none)',
                        rawError
                    );
                } catch (_) {}
            }
        }
        return fallbackText;
    }

    async function submitSelection(targetPath, selectionSource) {
        if (!state.bootstrap) return;

        var normalizedTargetPath = String(targetPath || '').trim();
        if (!normalizedTargetPath) {
            setSelectionStatusByKey('storage.selectPathRequired', '请先提供目标路径。', true);
            return;
        }

        setSubmitting(true);
        setSelectionStatus('', false);

        try {
            var response = await fetch('/api/storage/location/select', {
                method: 'POST',
                headers: {
                    'Accept': 'application/json',
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    selected_root: normalizedTargetPath,
                    selection_source: selectionSource
                })
            });

            var payload = null;
            try {
                payload = await response.json();
            } catch (_) {}

            if (!response.ok) {
                throw new Error(
                    extractResponseError(
                        payload,
                        translate('storage.selectionSubmitFailed', '提交存储位置选择失败，请稍后重试。')
                    )
                );
            }

            if (!payload || payload.ok !== true) {
                throw new Error(
                    translate('storage.selectionSubmitUnexpected', '存储位置选择接口返回了未识别的结果。')
                );
            }

            if (payload.result === 'continue_current_session') {
                resetPreviewState();
                if (state.otherPanel) {
                    state.otherPanel.hidden = true;
                }
                hideOverlay();
                resolveStartupDecision({
                    canContinue: true,
                    reason: 'continue_current_session',
                });
                return;
            }

            if (payload.result === 'restart_required') {
                showRestartRequired(
                    payload,
                    String(payload.selected_root || normalizedTargetPath),
                    selectionSource
                );
                return;
            }

            throw new Error(
                translate('storage.selectionSubmitUnexpected', '存储位置选择接口返回了未识别的结果。')
            );
        } catch (error) {
            console.warn('[storage-location] select failed', error);
            resetPreviewState();
            setSelectionStatus(
                String((error && error.message) || error || translate('storage.selectionSubmitFailed', '提交存储位置选择失败，请稍后重试。')),
                true
            );
            setPhase('selection_required');
        } finally {
            setSubmitting(false);
        }
    }

    async function startMaintenancePolling() {
        if (state.maintenancePollPromise) {
            return state.maintenancePollPromise;
        }

        state.maintenancePollPromise = (async function () {
            var failureCount = 0;

            while (state.phase === 'maintenance') {
                var pollIntervalMs = 0;
                try {
                    var statusPayload = await fetchStorageLocationStatus();
                    failureCount = 0;
                    pollIntervalMs = Number(statusPayload.poll_interval_ms || 0);

                    if (statusPayload.ready === true) {
                        setMaintenanceCopy(
                            translate('storage.maintenanceTitle', '正在优化存储布局...'),
                            translate('storage.maintenanceReconnectSubtitle', '检测到服务已经恢复，正在重新连接应用。'),
                            translate('storage.maintenanceReconnectStatus', '请保持当前页面打开，主页会自动恢复。')
                        );
                        applyMaintenanceProgress({
                            ready: true,
                            status: 'ready',
                            lifecycle_state: 'ready',
                            migration_stage: 'completed'
                        });
                        window.location.reload();
                        return;
                    }

                    setMaintenanceCopy(
                        translate('storage.maintenanceTitle', '正在优化存储布局...'),
                        translateMaintenanceSubtitle(statusPayload),
                        buildMaintenanceStatusText(statusPayload)
                    );
                    applyMaintenanceProgress(statusPayload);
                } catch (_) {
                    try {
                        var fallbackStatusPayload = await fetchSystemStatus();
                        failureCount = 0;
                        if (!shouldBlockMainUi(fallbackStatusPayload)) {
                            setMaintenanceCopy(
                                translate('storage.maintenanceTitle', '正在优化存储布局...'),
                                translate('storage.maintenanceReconnectSubtitle', '检测到服务已经恢复，正在重新连接应用。'),
                                translate('storage.maintenanceReconnectStatus', '请保持当前页面打开，主页会自动恢复。')
                            );
                            applyMaintenanceProgress({
                                ready: true,
                                status: 'ready',
                                lifecycle_state: 'ready',
                                migration_stage: 'completed'
                            });
                            window.location.reload();
                            return;
                        }

                        setMaintenanceCopy(
                            translate('storage.maintenanceTitle', '正在优化存储布局...'),
                            translate('storage.maintenanceWaitingSubtitle', '当前实例即将关闭，数据会在关闭后迁移并自动重启。'),
                            buildMaintenanceStatusText(fallbackStatusPayload)
                        );
                        applyMaintenanceProgress(fallbackStatusPayload);
                    } catch (error) {
                        failureCount += 1;
                        setMaintenanceCopy(
                            translate('storage.maintenanceTitle', '正在优化存储布局...'),
                            translate('storage.maintenanceWaitingSubtitle', '当前实例即将关闭，数据会在关闭后迁移并自动重启。'),
                            failureCount <= 1
                                ? translate('storage.maintenanceClosingStatus', '正在等待当前实例完成关闭...')
                                : translate('storage.maintenanceOfflineStatus', '连接已暂时中断，正在等待服务恢复。请不要关闭当前页面。')
                        );
                        applyMaintenanceProgress({
                            status: 'maintenance',
                            lifecycle_state: 'maintenance',
                            restart_mode: state.pendingSelection && state.pendingSelection.preflight && state.pendingSelection.preflight.restart_mode
                        });
                    }
                }

                if (!(pollIntervalMs > 0)) {
                    pollIntervalMs = failureCount > 0 ? 1200 : 900;
                }
                await sleep(pollIntervalMs);
            }
        })();

        return state.maintenancePollPromise;
    }

    function enterMaintenanceMode(payload) {
        var migration = payload && payload.migration ? payload.migration : {};
        var targetRoot = String(
            migration.target_root
            || payload.target_root
            || payload.selected_root
            || state.pendingSelection.path
            || ''
        ).trim();

        setMaintenanceCopy(
            translate('storage.maintenanceTitle', '正在优化存储布局...'),
            translateMaintenanceSubtitle(payload),
            targetRoot
                ? translate('storage.maintenanceTargetStatus', '目标路径已记录，正在等待服务关闭并恢复：') + ' ' + targetRoot
                : buildMaintenanceStatusText(payload)
        );
        applyMaintenanceProgress(payload || {});
        setPhase('maintenance');
        startMaintenancePolling();
    }

    function enterExternalMaintenanceMode(payload) {
        var normalizedPayload = payload && typeof payload === 'object' ? payload : {};
        var migration = normalizedPayload.migration && typeof normalizedPayload.migration === 'object'
            ? normalizedPayload.migration
            : {};
        var targetRoot = String(
            normalizedPayload.target_root
            || normalizedPayload.selected_root
            || migration.target_root
            || ''
        ).trim();
        var noticeKey = [
            String(normalizedPayload.result || '').trim(),
            String(normalizedPayload.restart_mode || '').trim(),
            targetRoot
        ].join('|');
        if (noticeKey && noticeKey === state.externalMaintenanceNoticeKey && state.phase === 'maintenance') {
            return;
        }

        buildModalDom();
        clearCompletionNoticePolling();
        state.externalMaintenanceNoticeKey = noticeKey;
        state.maintenancePollPromise = null;
        state.pendingSelection.path = targetRoot;
        state.pendingSelection.source = String(normalizedPayload.selection_source || 'custom').trim();
        state.pendingSelection.preflight = extractPreflightDetails(normalizedPayload, targetRoot);
        enterMaintenanceMode(normalizedPayload);
    }

    function handleExternalStorageRestartMessage(message) {
        if (!message || typeof message !== 'object' || message.type !== STORAGE_RESTART_MESSAGE_TYPE) {
            return;
        }
        if (message.sender_id && message.sender_id === STORAGE_RESTART_PAGE_ID) {
            return;
        }
        enterExternalMaintenanceMode(message.payload || {});
    }

    function confirmExistingTargetContentForRestart(preflight) {
        return window.confirm(existingTargetConfirmationText());
    }

    async function requestRestart() {
        if (!state.pendingSelection.path) {
            setSelectionStatusByKey('storage.selectPathRequired', '请先提供目标路径。', true);
            return;
        }

        setSubmitting(true);
        setSelectionStatus('', false);

        try {
            var confirmExistingTargetContent = false;
            while (true) {
                var preflight = state.pendingSelection.preflight || {};
                if (!confirmExistingTargetContent && preflight.requires_existing_target_confirmation === true) {
                    if (!confirmExistingTargetContentForRestart(preflight)) {
                        return;
                    }
                    confirmExistingTargetContent = true;
                }

                var response = await fetch('/api/storage/location/restart', {
                    method: 'POST',
                    headers: {
                        'Accept': 'application/json',
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        selected_root: state.pendingSelection.path,
                        selection_source: state.pendingSelection.source || 'user_selected',
                        confirm_existing_target_content: confirmExistingTargetContent
                    })
                });

                var payload = null;
                try {
                    payload = await response.json();
                } catch (_) {}

                if (!response.ok) {
                    if (payload && state.previewPanel) {
                        state.pendingSelection.preflight = extractPreflightDetails(payload, state.pendingSelection.path);
                        updateRestartPreviewPreflight(state.pendingSelection.preflight);
                        state.previewPanel.hidden = false;
                    }
                    if (
                        payload
                        && payload.error_code === 'target_confirmation_required'
                        && !confirmExistingTargetContent
                        && state.pendingSelection.preflight.requires_existing_target_confirmation === true
                    ) {
                        if (!confirmExistingTargetContentForRestart(state.pendingSelection.preflight)) {
                            return;
                        }
                        confirmExistingTargetContent = true;
                        continue;
                    }
                    throw new Error(
                        extractResponseError(
                            payload,
                            translate('storage.restartRequestFailed', '启动关闭与迁移准备失败，请稍后重试。')
                        )
                    );
                }

                if (!payload || payload.ok !== true || payload.result !== 'restart_initiated') {
                    throw new Error(
                        translate('storage.restartRequestUnexpected', '关闭与迁移准备接口返回了未识别的结果。')
                    );
                }

                enterMaintenanceMode(payload);
                return;
            }
        } catch (error) {
            console.warn('[storage-location] restart failed', error);
            setSelectionStatus(
                String((error && error.message) || error || translate('storage.restartRequestFailed', '启动关闭与迁移准备失败，请稍后重试。')),
                true
            );
            setPhase('selection_required');
        } finally {
            setSubmitting(false);
        }
    }

    function showError(error) {
        if (error) {
            // 运行时透传的错误（fetch / parse 失败等）。文案由调用方/异常自带，
            // 不打 i18n key，rebuild 切语言时按原文回填。
            state.errorTextI18nKey = '';
            state.errorTextI18nFallback = '';
            state.errorText.textContent = String(error.message || error);
        } else {
            state.errorTextI18nKey = 'storage.bootstrapError';
            state.errorTextI18nFallback = '无法读取存储位置初始化信息，请重试。';
            state.errorText.textContent = translate(state.errorTextI18nKey, state.errorTextI18nFallback);
        }
        setPhase('error');
    }

    function buildInfoPathRow(labelText, targetRefName, modifierClass) {
        var item = createElement('div', 'storage-location-path-item' + (modifierClass ? ' ' + modifierClass : ''));
        item.appendChild(createElement('div', 'storage-location-label', labelText));
        var value = createElement('div', 'storage-location-path');
        state[targetRefName] = value;
        item.appendChild(value);
        return item;
    }

    function continueWithCurrentPath() {
        if (!state.bootstrap) return;
        submitSelection(state.bootstrap.current_root || '', 'current');
    }

    function useRecommendedPath() {
        if (!state.bootstrap) return;
        submitSelection(state.bootstrap.recommended_root || '', 'recommended');
    }

    function useOtherPath() {
        var isLegacySelection = state.otherSelection.key === 'legacy'
            || String(state.otherSelection.key || '').indexOf('legacy-') === 0;
        var selectionPath = state.otherSelection.path || '';
        if (!isLegacySelection) {
            selectionPath = applyCustomStorageRootDisplay(selectionPath);
            renderLegacyList();
            updateOtherButtonState();
        }
        submitSelection(selectionPath, isLegacySelection ? 'legacy' : 'custom');
    }

    function buildSelectionView() {
        var view = createElement('section', 'storage-location-view');
        var shell = createElement('div', 'storage-location-shell');

        var hero = createElement('div', 'storage-location-hero');
        hero.appendChild(createElement('span', 'storage-location-badge', translate('storage.badge', '存储位置')));
        hero.appendChild(createElement('h2', 'storage-location-title', translate('storage.selectionTitle', '请选择本次运行使用的存储位置')));
        hero.appendChild(createElement('p', 'storage-location-subtitle', translate('storage.selectionSubtitle', '应用已经正常打开。接下来请先在当前页面内确认存储位置，再继续使用。')));
        shell.appendChild(hero);

        var banner = createElement('div', 'storage-location-banner');
        banner.hidden = true;
        state.banner = banner;
        shell.appendChild(banner);

        var grid = createElement('div', 'storage-location-grid');

        var pathsPanel = createElement('section', 'storage-location-panel');
        pathsPanel.appendChild(createElement('h3', 'storage-location-panel-title', translate('storage.pathOverview', '路径总览')));
        var pathList = createElement('div', 'storage-location-path-list');
        pathList.appendChild(buildInfoPathRow(translate('storage.recommendedPath', '推荐路径'), 'recommendedPath', 'storage-location-path-item--recommended'));
        pathList.appendChild(buildInfoPathRow(translate('storage.currentPath', '当前路径'), 'currentPath'));
        pathsPanel.appendChild(pathList);
        grid.appendChild(pathsPanel);
        shell.appendChild(grid);

        var actions = createElement('div', 'storage-location-actions');

        var recommendedButton = registerActionButton(
            createElement('button', 'storage-location-btn storage-location-btn--primary', translate('storage.useRecommended', '使用推荐位置'))
        );
        recommendedButton.type = 'button';
        recommendedButton.addEventListener('click', useRecommendedPath);
        actions.appendChild(recommendedButton);

        var currentButton = registerActionButton(
            createElement('button', 'storage-location-btn storage-location-btn--secondary', translate('storage.useCurrent', '保持当前路径'))
        );
        currentButton.type = 'button';
        currentButton.addEventListener('click', continueWithCurrentPath);
        actions.appendChild(currentButton);

        var chooseOtherButton = registerActionButton(
            createElement('button', 'storage-location-btn storage-location-btn--secondary', translate('storage.chooseOther', '选择其他位置'))
        );
        chooseOtherButton.type = 'button';
        chooseOtherButton.addEventListener('click', openOtherPanel);
        actions.appendChild(chooseOtherButton);

        shell.appendChild(actions);

        var selectionStatus = createElement('p', 'storage-location-note');
        selectionStatus.hidden = true;
        state.selectionStatus = selectionStatus;
        shell.appendChild(selectionStatus);

        var otherPanel = createElement('section', 'storage-location-other');
        otherPanel.hidden = true;
        state.otherPanel = otherPanel;
        otherPanel.appendChild(createElement('h3', 'storage-location-panel-title', translate('storage.otherPanelTitle', '其他位置')));
        otherPanel.appendChild(createElement('p', 'storage-location-note', translate('storage.otherPanelNote', '你可以直接复用旧数据目录，也可以选择一个新的文件夹作为后续运行位置。')));

        var legacyChoices = createElement('div', 'storage-location-choice-list');
        state.legacyChoices = legacyChoices;
        otherPanel.appendChild(legacyChoices);

        var inputRow = createElement('div', 'storage-location-input-row');
        var customInput = createElement('input', 'storage-location-input');
        customInput.type = 'text';
        customInput.placeholder = translate('storage.customPathPlaceholder', '选择一个父目录，应用会使用其中的 N.E.K.O 子文件夹');
        customInput.addEventListener('focus', function () {
            state.otherSelection.key = 'custom';
        });
        customInput.addEventListener('input', function () {
            state.otherSelection.key = 'custom';
            state.otherSelection.path = String(customInput.value || '').trim();
            renderLegacyList();
            updateOtherButtonState();
        });
        state.customInput = customInput;
        inputRow.appendChild(customInput);

        var pickFolderButton = registerActionButton(
            createElement('button', 'storage-location-btn storage-location-btn--secondary storage-location-btn--compact', translate('storage.pickFolder', '选择文件夹'))
        );
        pickFolderButton.type = 'button';
        pickFolderButton.addEventListener('click', pickOtherDirectory);
        state.pickFolderButton = pickFolderButton;
        inputRow.appendChild(pickFolderButton);
        otherPanel.appendChild(inputRow);

        var otherActions = createElement('div', 'storage-location-actions');
        var useOtherButton = registerActionButton(
            createElement('button', 'storage-location-btn', translate('storage.previewOther', '提交该位置'))
        );
        useOtherButton.type = 'button';
        useOtherButton.dataset.forceDisabled = '1';
        useOtherButton.disabled = true;
        useOtherButton.addEventListener('click', useOtherPath);
        state.useOtherButton = useOtherButton;
        otherActions.appendChild(useOtherButton);
        otherPanel.appendChild(otherActions);

        shell.appendChild(otherPanel);

        var previewPanel = createElement('section', 'storage-location-panel');
        previewPanel.hidden = true;
        state.previewPanel = previewPanel;
        previewPanel.appendChild(createElement('h3', 'storage-location-panel-title', translate('storage.previewTitle', '该选择需要后续关闭并迁移')));
        var previewText = createElement('p', 'storage-location-note');
        state.previewText = previewText;
        previewPanel.appendChild(previewText);

        var previewList = createElement('div', 'storage-location-restart-list');
        var sourceItem = createElement('div', 'storage-location-path-item');
        sourceItem.appendChild(createElement('div', 'storage-location-label', translate('storage.sourceLabel', '当前路径')));
        var previewSource = createElement('div', 'storage-location-restart-path');
        state.previewSource = previewSource;
        sourceItem.appendChild(previewSource);
        previewList.appendChild(sourceItem);

        var targetItem = createElement('div', 'storage-location-path-item');
        targetItem.appendChild(createElement('div', 'storage-location-label', translate('storage.targetLabel', '目标路径')));
        var previewTarget = createElement('div', 'storage-location-restart-path');
        state.previewTarget = previewTarget;
        targetItem.appendChild(previewTarget);
        previewList.appendChild(targetItem);
        previewPanel.appendChild(previewList);

        var preflightList = createElement('div', 'storage-location-summary-list');

        var estimatedItem = createElement('div', 'storage-location-summary-item');
        estimatedItem.appendChild(createElement('div', 'storage-location-label', translate('storage.estimatedPayload', '预计迁移体量')));
        var previewEstimated = createElement('div', 'storage-location-summary-value');
        state.previewEstimated = previewEstimated;
        estimatedItem.appendChild(previewEstimated);
        preflightList.appendChild(estimatedItem);

        var freeSpaceItem = createElement('div', 'storage-location-summary-item');
        freeSpaceItem.appendChild(createElement('div', 'storage-location-label', translate('storage.targetFreeSpace', '目标卷剩余空间')));
        var previewFreeSpace = createElement('div', 'storage-location-summary-value');
        state.previewFreeSpace = previewFreeSpace;
        freeSpaceItem.appendChild(previewFreeSpace);
        preflightList.appendChild(freeSpaceItem);

        var permissionItem = createElement('div', 'storage-location-summary-item');
        permissionItem.appendChild(createElement('div', 'storage-location-label', translate('storage.permissionCheck', '目标路径写入权限')));
        var previewPermission = createElement('div', 'storage-location-summary-value');
        state.previewPermission = previewPermission;
        permissionItem.appendChild(previewPermission);
        preflightList.appendChild(permissionItem);

        var warningsItem = createElement('div', 'storage-location-summary-item');
        warningsItem.appendChild(createElement('div', 'storage-location-label', translate('storage.warningSummary', '额外风险提示')));
        var previewWarnings = createElement('div', 'storage-location-summary-value');
        state.previewWarnings = previewWarnings;
        warningsItem.appendChild(previewWarnings);
        preflightList.appendChild(warningsItem);

        previewPanel.appendChild(preflightList);

        var previewBlocking = createElement('p', 'storage-location-note');
        previewBlocking.hidden = true;
        state.previewBlocking = previewBlocking;
        previewPanel.appendChild(previewBlocking);

        var previewSteps = createElement('div', 'storage-location-preview-steps');
        previewSteps.appendChild(
            createElement(
                'div',
                'storage-location-preview-step',
                translate('storage.previewStepClose', '1. 当前实例会先关闭。')
            )
        );
        previewSteps.appendChild(
            createElement(
                'div',
                'storage-location-preview-step',
                translate('storage.previewStepMigrate', '2. 关闭后会恢复目标存储布局；必要时再迁移数据。')
            )
        );
        previewSteps.appendChild(
            createElement(
                'div',
                'storage-location-preview-step',
                translate('storage.previewStepRestart', '3. 迁移完成后会自动重启。')
            )
        );
        previewSteps.appendChild(
            createElement(
                'div',
                'storage-location-preview-step',
                translate('storage.previewStepRetain', '4. 旧数据默认不会自动删除。')
            )
        );
        previewPanel.appendChild(previewSteps);
        previewPanel.appendChild(
            createElement(
                'p',
                'storage-location-note',
                translate('storage.previewBoundary', '当前不会在本会话里热切根，也不会提前把稳定根改成新路径。确认后会由后端按设计顺序完成关闭、必要迁移、自动重启与最终布局恢复。')
            )
        );

        var previewActions = createElement('div', 'storage-location-restart-actions');
        var confirmRestartButton = registerActionButton(
            createElement('button', 'storage-location-btn storage-location-btn--primary', translate('storage.confirmRestart', '确认关闭并迁移'))
        );
        confirmRestartButton.type = 'button';
        confirmRestartButton.addEventListener('click', requestRestart);
        state.previewConfirmButton = confirmRestartButton;
        previewActions.appendChild(confirmRestartButton);

        var backButton = registerActionButton(
            createElement('button', 'storage-location-btn storage-location-btn--secondary', translate('common.back', '返回重新选择'))
        );
        backButton.type = 'button';
        backButton.addEventListener('click', backToSelection);
        previewActions.appendChild(backButton);
        previewPanel.appendChild(previewActions);
        state.previewActions = previewActions;
        shell.appendChild(previewPanel);

        view.appendChild(shell);
        state.selectionView = view;
        return view;
    }

    function buildLoadingView() {
        var view = createElement('section', 'storage-location-view');
        view.hidden = true;

        var shell = createElement('div', 'storage-location-shell');
        var hero = createElement('div', 'storage-location-hero');
        hero.appendChild(createElement('span', 'storage-location-badge', translate('storage.badge', '存储位置')));
        var loadingTitle = createElement('h2', 'storage-location-title', translate('storage.loadingTitle', '正在确认存储布局状态'));
        var loadingSubtitle = createElement('p', 'storage-location-subtitle', translate('storage.loadingSubtitle', '主业务界面会在存储状态确认完成后再继续加载。'));
        state.loadingTitle = loadingTitle;
        state.loadingSubtitle = loadingSubtitle;
        hero.appendChild(loadingTitle);
        hero.appendChild(loadingSubtitle);
        shell.appendChild(hero);
        shell.appendChild(createElement('div', 'storage-location-loader'));
        view.appendChild(shell);

        state.loadingView = view;
        return view;
    }

    function buildMaintenanceView() {
        var view = createElement('section', 'storage-location-view');
        view.hidden = true;

        var shell = createElement('div', 'storage-location-shell');
        var hero = createElement('div', 'storage-location-hero');
        hero.appendChild(createElement('span', 'storage-location-badge', translate('storage.badge', '存储位置')));

        var maintenanceTitle = createElement('h2', 'storage-location-title', translate('storage.maintenanceTitle', '正在优化存储布局...'));
        var maintenanceSubtitle = createElement('p', 'storage-location-subtitle', translate('storage.maintenanceWaitingSubtitle', '当前实例即将关闭，数据会在关闭后迁移并自动重启。'));
        var maintenanceStatus = createElement('p', 'storage-location-note');
        var maintenanceProgress = createElement('section', 'storage-location-progress');
        var progressMeta = createElement('div', 'storage-location-progress-meta');
        var progressLabel = createElement('div', 'storage-location-progress-label', translate('storage.progressWaitingShutdown', '正在等待当前实例安全关闭'));
        var progressValue = createElement('div', 'storage-location-progress-value', '14%');
        var progressTrack = createElement('div', 'storage-location-progress-track');
        progressTrack.setAttribute('role', 'progressbar');
        progressTrack.setAttribute('aria-valuemin', '0');
        progressTrack.setAttribute('aria-valuemax', '100');
        progressTrack.setAttribute('aria-valuenow', '14');
        progressTrack.setAttribute('aria-valuetext', translate('storage.progressWaitingShutdown', '正在等待当前实例安全关闭'));
        var progressFill = createElement('div', 'storage-location-progress-fill');
        progressTrack.appendChild(progressFill);
        progressMeta.appendChild(progressLabel);
        progressMeta.appendChild(progressValue);
        maintenanceProgress.appendChild(progressMeta);
        maintenanceProgress.appendChild(progressTrack);

        var progressSteps = createElement('div', 'storage-location-progress-steps');
        var maintenanceStepItems = [];
        [
            translate('storage.progressStepShutdown', '关闭当前实例'),
            translate('storage.progressStepTransfer', '处理存储目录'),
            translate('storage.progressStepCommit', '校验并生效'),
            translate('storage.progressStepRecover', '恢复服务')
        ].forEach(function (text, index) {
            var step = createElement('div', 'storage-location-progress-step', text);
            if (index === 0) {
                step.classList.add('is-active');
            }
            progressSteps.appendChild(step);
            maintenanceStepItems.push(step);
        });
        maintenanceProgress.appendChild(progressSteps);

        state.maintenanceTitle = maintenanceTitle;
        state.maintenanceSubtitle = maintenanceSubtitle;
        state.maintenanceStatus = maintenanceStatus;
        state.maintenanceProgressBar = progressTrack;
        state.maintenanceProgressFill = progressFill;
        state.maintenanceProgressLabel = progressLabel;
        state.maintenanceProgressValue = progressValue;
        state.maintenanceProgressSteps = maintenanceStepItems;

        hero.appendChild(maintenanceTitle);
        hero.appendChild(maintenanceSubtitle);
        hero.appendChild(maintenanceStatus);
        shell.appendChild(hero);
        shell.appendChild(maintenanceProgress);
        shell.appendChild(
            createElement(
                'p',
                'storage-location-note',
                translate('storage.maintenanceNote', '连接短暂中断属于正常现象。页面会在服务恢复后自动重新连接。')
            )
        );
        view.appendChild(shell);

        state.maintenanceView = view;
        return view;
    }

    function buildErrorView() {
        var view = createElement('section', 'storage-location-view');
        view.hidden = true;

        var shell = createElement('div', 'storage-location-shell');
        var hero = createElement('div', 'storage-location-hero');
        hero.appendChild(createElement('span', 'storage-location-badge', translate('storage.errorBadge', '读取失败')));
        hero.appendChild(createElement('h2', 'storage-location-title', translate('storage.errorTitle', '暂时无法读取存储位置引导信息')));
        var errorText = createElement('p', 'storage-location-error-text');
        state.errorText = errorText;
        hero.appendChild(errorText);
        shell.appendChild(hero);

        var actions = createElement('div', 'storage-location-error-actions');
        var retryButton = createElement('button', 'storage-location-btn storage-location-btn--primary', translate('common.retry', '重试'));
        retryButton.type = 'button';
        retryButton.addEventListener('click', function () {
            beginSentinelFlow();
        });
        actions.appendChild(retryButton);
        shell.appendChild(actions);
        view.appendChild(shell);

        state.errorView = view;
        return view;
    }

    function buildModalDom() {
        if (state.overlay) return;

        var overlay = createElement('div', 'storage-location-overlay');
        overlay.id = 'storage-location-overlay';
        overlay.hidden = true;

        var modal = createElement('div', 'storage-location-modal');
        modal.setAttribute('role', 'dialog');
        modal.setAttribute('aria-modal', 'true');
        modal.setAttribute('aria-label', translate('storage.dialogLabel', '存储位置选择'));

        modal.appendChild(buildStorageLocationCloseButton());
        modal.appendChild(buildLoadingView());
        modal.appendChild(buildMaintenanceView());
        modal.appendChild(buildSelectionView());
        modal.appendChild(buildErrorView());

        overlay.appendChild(modal);
        document.body.appendChild(overlay);
        state.overlay = overlay;
    }

    // 当 i18next 加载完成或用户切换语言时，模态框里通过 translate(key, fallback)
    // 取到的静态文本不会自动刷新（DOM 在更早就已经构建出来）。这里把模态框
    // 拆掉重建，并把不依赖 DOM 的关键状态恢复回去，保证语言切换可见。
    function rebuildModalForLocale() {
        if (!state.overlay) return;

        var snapshot = {
            phase: state.phase,
            submitting: state.submitting,
            otherPanelHidden: state.otherPanel ? state.otherPanel.hidden : true,
            previewPanelHidden: state.previewPanel ? state.previewPanel.hidden : true,
            customInputValue: state.customInput ? state.customInput.value : '',
            errorText: state.errorText ? state.errorText.textContent : '',
            errorTextI18nKey: state.errorTextI18nKey,
            errorTextI18nFallback: state.errorTextI18nFallback,
            selectionStatusText: state.selectionStatus ? state.selectionStatus.textContent : '',
            selectionStatusIsError: state.selectionStatus
                ? state.selectionStatus.classList.contains('storage-location-note--error')
                : false,
            selectionStatusI18nKey: state.selectionStatusI18nKey,
            selectionStatusI18nFallback: state.selectionStatusI18nFallback,
            // 处于 maintenance 阶段时，下一次轮询可能要 ~900ms-1200ms 才到，
            // 直接抓 DOM 文案先把视觉占住，避免重建瞬间退回构建期默认值。
            maintenanceTitleText: state.maintenanceTitle ? state.maintenanceTitle.textContent : '',
            maintenanceSubtitleText: state.maintenanceSubtitle ? state.maintenanceSubtitle.textContent : '',
            maintenanceStatusText: state.maintenanceStatus ? state.maintenanceStatus.textContent : '',
            lastMaintenanceProgressPayload: state.lastMaintenanceProgressPayload,
            pendingSelection: {
                path: state.pendingSelection.path,
                source: state.pendingSelection.source,
                preflight: state.pendingSelection.preflight
                    ? Object.assign({}, state.pendingSelection.preflight)
                    : null,
            },
            otherSelection: {
                key: state.otherSelection.key,
                path: state.otherSelection.path,
            },
            completionNotice: state.completionNotice,
            completionCardVisible: !!(state.completionCard && !state.completionCard.hidden),
        };

        if (state.overlay.parentNode) {
            state.overlay.parentNode.removeChild(state.overlay);
        }
        state.overlay = null;
        state.loadingView = null;
        state.maintenanceView = null;
        state.selectionView = null;
        state.errorView = null;
        state.banner = null;
        state.currentPath = null;
        state.recommendedPath = null;
        state.otherPanel = null;
        state.legacyChoices = null;
        state.customInput = null;
        state.pickFolderButton = null;
        state.useOtherButton = null;
        state.previewPanel = null;
        state.previewText = null;
        state.previewSource = null;
        state.previewTarget = null;
        state.previewEstimated = null;
        state.previewFreeSpace = null;
        state.previewPermission = null;
        state.previewWarnings = null;
        state.previewBlocking = null;
        state.previewConfirmButton = null;
        state.previewActions = null;
        state.selectionStatus = null;
        state.errorText = null;
        state.loadingTitle = null;
        state.loadingSubtitle = null;
        state.maintenanceTitle = null;
        state.maintenanceSubtitle = null;
        state.maintenanceStatus = null;
        state.maintenanceProgressBar = null;
        state.maintenanceProgressFill = null;
        state.maintenanceProgressLabel = null;
        state.maintenanceProgressValue = null;
        state.maintenanceProgressSteps = [];
        state.actionButtons = [];

        if (state.completionCard && state.completionCard.parentNode) {
            state.completionCard.parentNode.removeChild(state.completionCard);
        }
        state.completionCard = null;
        state.completionTitle = null;
        state.completionMessage = null;
        state.completionTarget = null;
        state.completionRetained = null;
        state.completionOpenTargetButton = null;
        state.completionOpenRetainedButton = null;
        state.completionCleanupButton = null;

        buildModalDom();

        state.pendingSelection = snapshot.pendingSelection;
        state.otherSelection = snapshot.otherSelection;

        if (state.customInput) {
            state.customInput.value = snapshot.customInputValue;
        }

        if (state.bootstrap) {
            updateSelectionSummary();
        }

        if (state.otherPanel) {
            state.otherPanel.hidden = snapshot.otherPanelHidden;
        }

        if (snapshot.pendingSelection.preflight && state.bootstrap && state.previewPanel) {
            // 用 populate-only 版本，避免它内部 setPhase 再被下面 setPhase(snapshot.phase) 盖掉。
            populateRestartPreview(
                snapshot.pendingSelection.preflight,
                snapshot.pendingSelection.path,
                snapshot.pendingSelection.source
            );
        }

        if (state.errorText) {
            // 优先按 i18n key 重新翻译，避免快照里塞回旧 locale 的字面文案；
            // 没有 key 的情况（运行时错误透传等）保留快照原文。
            if (snapshot.errorTextI18nKey) {
                state.errorTextI18nKey = snapshot.errorTextI18nKey;
                state.errorTextI18nFallback = snapshot.errorTextI18nFallback;
                state.errorText.textContent = translate(
                    snapshot.errorTextI18nKey,
                    snapshot.errorTextI18nFallback
                );
            } else if (snapshot.errorText) {
                state.errorTextI18nKey = '';
                state.errorTextI18nFallback = '';
                state.errorText.textContent = snapshot.errorText;
            }
        }

        if (snapshot.selectionStatusI18nKey) {
            setSelectionStatusByKey(
                snapshot.selectionStatusI18nKey,
                snapshot.selectionStatusI18nFallback,
                snapshot.selectionStatusIsError
            );
        } else if (snapshot.selectionStatusText) {
            setSelectionStatus(snapshot.selectionStatusText, snapshot.selectionStatusIsError);
        }

        if (snapshot.phase === 'maintenance') {
            // 先用快照的旧文案立刻填充（避免重建瞬间显示构建期默认值），下一次
            // 轮询会用新 locale 覆盖这层临时文案。再立刻按缓存 payload 重渲一遍
            // 进度条——这一步本身就走 translate()，所以进度条文案立刻就是新 locale 的了。
            setMaintenanceCopy(
                snapshot.maintenanceTitleText,
                snapshot.maintenanceSubtitleText,
                snapshot.maintenanceStatusText
            );
            if (snapshot.lastMaintenanceProgressPayload) {
                applyMaintenanceProgress(snapshot.lastMaintenanceProgressPayload);
            }
        }

        setPhase(snapshot.phase);
        setSubmitting(snapshot.submitting);

        if (snapshot.completionCardVisible && snapshot.completionNotice) {
            applyCompletionNotice(snapshot.completionNotice);
        }
    }

    function handleLocaleChange() {
        // 重建可能涉及修改大量 DOM；只有当模态框已经挂载时才需要刷新。
        if (!state.overlay) return;
        try {
            rebuildModalForLocale();
        } catch (error) {
            console.warn('[storage-location] locale rebuild failed', error);
        }
    }

    var localeListenerAttached = false;
    function attachLocaleListener() {
        if (localeListenerAttached) return;
        localeListenerAttached = true;
        window.addEventListener('localechange', handleLocaleChange);
    }

    async function fetchBootstrap() {
        setPhase('loading');
        setLoadingCopy(
            translate('storage.loadingTitle', '正在确认存储布局状态'),
            translate('storage.loadingFetchBootstrapSubtitle', '正在准备存储位置选择页面。')
        );
        try {
            var response = await fetch('/api/storage/location/bootstrap', {
                cache: 'no-store',
                headers: {
                    'Accept': 'application/json'
                }
            });
            if (!response.ok) {
                throw new Error('bootstrap request failed: ' + response.status);
            }

            state.bootstrap = await response.json();
            if (shouldShowMaintenanceView(state.bootstrap)) {
                enterMaintenanceMode(state.bootstrap);
                return;
            }
            updateSelectionSummary();
            if (shouldShowSelectionView(state.bootstrap)) {
                setPhase('selection_required');
                return;
            }

            hideOverlay();
            resolveStartupDecision({
                canContinue: true,
                reason: 'status_ready',
            });
            scheduleCompletionNoticePolling();
        } catch (error) {
            console.warn('[storage-location] bootstrap failed', error);
            showError(error);
        }
    }

    async function beginSentinelFlow() {
        buildModalDom();
        attachLocaleListener();
        setPhase('loading');
        setLoadingCopy(
            translate('storage.loadingTitle', '正在确认存储布局状态'),
            translate('storage.loadingSubtitle', '主业务界面会在存储状态确认完成后再继续加载。')
        );

        try {
            var statusPayload = await waitForSystemStatus();
            if (!shouldBlockMainUi(statusPayload)) {
                hideOverlay();
                resolveStartupDecision({
                    canContinue: true,
                    reason: 'status_ready',
                });
                scheduleCompletionNoticePolling();
                return;
            }

            await fetchBootstrap();
        } catch (error) {
            console.warn('[storage-location] sentinel init failed', error);
            showError(error);
        }
    }

    async function init() {
        if (state.initPromise) return state.initPromise;
        state.initialized = true;
        state.initPromise = state.startupDecision.promise;
        beginSentinelFlow();
        return state.initPromise;
    }

    function scheduleEarlyInit() {
        function start() {
            init();
        }

        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', start, { once: true });
            return;
        }

        window.setTimeout(start, 0);
    }

    window.appStorageLocation = {
        init: init,
        waitUntilMainUiAllowed: function () {
            return init();
        },
        refreshCompletionNotice: function () {
            return checkReadyStateCompletionNotice();
        },
        enterExternalMaintenanceMode: enterExternalMaintenanceMode,
        STORAGE_RESTART_MESSAGE_TYPE: STORAGE_RESTART_MESSAGE_TYPE,
        STORAGE_RESTART_CHANNEL: STORAGE_RESTART_CHANNEL,
    };

    window.addEventListener('message', function (event) {
        if (event.origin !== window.location.origin) return;
        handleExternalStorageRestartMessage(event.data);
    });

    try {
        if (typeof BroadcastChannel !== 'undefined') {
            var storageRestartChannel = new BroadcastChannel(STORAGE_RESTART_CHANNEL);
            storageRestartChannel.onmessage = function (event) {
                handleExternalStorageRestartMessage(event.data);
            };
        }
    } catch (error) {
        console.warn('[storage-location] restart channel setup failed', error);
    }

    if (autoStart) {
        window.waitForStorageLocationStartupBarrier = function waitForStorageLocationStartupBarrier() {
            return init();
        };
        window.__nekoStorageLocationStartupBarrier = init();
        scheduleEarlyInit();
    }
})();
