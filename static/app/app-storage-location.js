(function () {
    if (window.appStorageLocation) return;

    var STORAGE_APP_FOLDER_NAME = 'N.E.K.O';
    var STORAGE_RESTART_MESSAGE_TYPE = 'storage_location_restart_initiated';
    var STORAGE_RESTART_CHANNEL = 'neko_storage_location_channel';
    var STORAGE_COMPLETION_NOTICE_DISMISSED_KEY = 'neko.storageLocation.completionNoticeDismissedKey.v1';
    var HOME_TUTORIAL_STARTUP_RELEASE_EVENT = 'neko:startup-greeting-release';
    var STORAGE_STATUS_REQUEST_TIMEOUT_MS = 4000;
    var STORAGE_EXIT_REQUEST_TIMEOUT_MS = 8000;
    var STORAGE_MUTATION_REQUEST_TIMEOUT_MS = 15000;
    var STORAGE_DIRECTORY_PICKER_TIMEOUT_MS = 125000;
    var STORAGE_HOST_CAPABILITY_TIMEOUT_MS = 3000;
    var STORAGE_RESTART_PAGE_ID = window.__nekoStorageLocationPageId || (
        'storage-location-' + Date.now() + '-' + Math.random().toString(36).slice(2)
    );
    window.__nekoStorageLocationPageId = STORAGE_RESTART_PAGE_ID;
    var autoStart = !(
        document.currentScript
        && document.currentScript.getAttribute('data-storage-location-auto-start') === 'false'
    );

    var state = {
        initialized: false,
        initPromise: null,
        submitting: false,
        phase: 'hidden',
        systemStatus: null,
        csrfToken: '',
        startupDecision: null,
        bootstrap: null,
        overlay: null,
        loadingView: null,
        loadingTitle: null,
        loadingSubtitle: null,
        maintenanceView: null,
        maintenanceTitle: null,
        maintenanceSubtitle: null,
        maintenanceProgressBar: null,
        maintenanceProgressFill: null,
        maintenanceProgressLabel: null,
        maintenanceProgressValue: null,
        maintenanceProgressSteps: [],
        maintenanceFallback: null,
        maintenanceFallbackMessage: null,
        maintenanceRetryButton: null,
        maintenanceQuitButton: null,
        maintenanceRollbackRequired: false,
        maintenanceStatusUnavailable: false,
        maintenanceAwaitingShutdown: false,
        lastMaintenanceProgressPayload: null,
        // 记录最近一次 setSelectionStatus / showError 时使用的 i18n key（如有）。
        // rebuildModalForLocale 在切语言后会优先按 key 重新翻译，避免快照里塞回旧 locale 的字面文案。
        // 来自后端透传的运行时错误（error.message 等）则不带 key，rebuild 时按原文回填。
        selectionStatusI18nKey: '',
        selectionStatusI18nFallback: '',
        errorTextI18nKey: '',
        errorTextI18nFallback: '',
        maintenancePollPromise: null,
        maintenancePollGeneration: 0,
        maintenanceEntryInstanceId: '',
        maintenanceObservedMigrationBlock: false,
        maintenanceRestartOperationId: '',
        maintenanceOutcomeUnknown: false,
        maintenanceRecoveryActionInFlight: false,
        completionPollTimer: null,
        completionPollAttempts: 0,
        completionNotice: null,
        completionNoticeStartupReleased: false,
        completionNoticeDeferredKey: '',
        completionCleanupBlockedKey: '',
        completionCard: null,
        completionTitle: null,
        completionTarget: null,
        completionRetained: null,
        completionOpenTargetButton: null,
        completionOpenRetainedButton: null,
        completionCleanupButton: null,
        externalMaintenanceNoticeKey: '',
        selectionIntroView: null,
        selectionView: null,
        errorView: null,
        banner: null,
        recommendedPath: null,
        currentPath: null,
        recommendedButton: null,
        customInput: null,
        pickFolderButton: null,
        useOtherButton: null,
        selectionActions: null,
        previewPanel: null,
        previewText: null,
        previewEstimated: null,
        previewFreeSpace: null,
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

    function storageRequestTimeoutError() {
        var error = new Error('storage request timed out');
        error.name = 'TimeoutError';
        return error;
    }

    function waitBeforeDeadline(promise, deadline, controller) {
        var remainingMs = deadline - Date.now();
        if (!(remainingMs > 0)) {
            if (controller) controller.abort();
            return Promise.reject(storageRequestTimeoutError());
        }

        var timer = null;
        var timeoutPromise = new Promise(function (_, reject) {
            timer = window.setTimeout(function () {
                if (controller) controller.abort();
                reject(storageRequestTimeoutError());
            }, remainingMs);
        });
        return Promise.race([promise, timeoutPromise]).finally(function () {
            if (timer !== null) window.clearTimeout(timer);
        });
    }

    async function fetchWithTimeout(url, options, timeoutMs) {
        if (!(timeoutMs > 0)) return fetch(url, options);

        var deadline = Date.now() + timeoutMs;
        var controller = typeof AbortController === 'function' ? new AbortController() : null;
        var requestOptions = Object.assign({}, options || {});
        if (controller) requestOptions.signal = controller.signal;
        var response = await waitBeforeDeadline(fetch(url, requestOptions), deadline, controller);

        // fetch() resolves as soon as the response headers arrive. Keep the same absolute
        // deadline for response-body parsing so a half-open backend cannot freeze the gate.
        if (response && typeof response.json === 'function') {
            var parseJson = response.json.bind(response);
            response.json = function () {
                return waitBeforeDeadline(parseJson(), deadline, controller);
            };
        }
        return response;
    }

    function captureStorageCsrfToken(payload) {
        var token = payload && typeof payload.autostart_csrf_token === 'string'
            ? payload.autostart_csrf_token.trim()
            : '';
        if (token) state.csrfToken = token;
    }

    function storageMutationHeaders(headers) {
        var result = Object.assign({}, headers || {});
        if (state.csrfToken) result['X-CSRF-Token'] = state.csrfToken;
        return result;
    }

    async function ensureStorageCsrfToken() {
        if (state.csrfToken) return;
        var response = await fetchWithTimeout('/api/storage/location/status', {
            cache: 'no-store',
            headers: { 'Accept': 'application/json' }
        }, STORAGE_STATUS_REQUEST_TIMEOUT_MS);
        if (!response.ok) throw new Error('storage csrf bootstrap failed: ' + response.status);
        captureStorageCsrfToken(await response.json());
        if (!state.csrfToken) throw new Error('storage csrf token is unavailable');
    }

    function translate(key, fallback) {
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

        return fallback || key;
    }

    function createElement(tag, className, text) {
        var element = document.createElement(tag);
        if (className) element.className = className;
        if (typeof text === 'string') element.textContent = text;
        return element;
    }

    function readStorageItem(key) {
        try {
            if (!window.localStorage) return '';
            return String(window.localStorage.getItem(key) || '');
        } catch (_) {
            return '';
        }
    }

    function writeStorageItem(key, value) {
        try {
            if (!window.localStorage) return;
            window.localStorage.setItem(key, String(value || ''));
        } catch (_) {}
    }

    function buildCompletionNoticeDismissKey(notice) {
        if (!notice || typeof notice !== 'object') {
            return '';
        }
        return JSON.stringify([
            notice.completed_at,
            notice.target_root,
            notice.retained_root,
            notice.source_root,
            notice.selection_source
        ].map(function (value) {
            return String(value || '').trim();
        }));
    }

    function isCompletionNoticeDismissed(notice) {
        var key = buildCompletionNoticeDismissKey(notice);
        return !!key && readStorageItem(STORAGE_COMPLETION_NOTICE_DISMISSED_KEY) === key;
    }

    function dismissCompletionNotice() {
        if (state.completionNotice && state.completionNotice.completed === true) {
            writeStorageItem(
                STORAGE_COMPLETION_NOTICE_DISMISSED_KEY,
                buildCompletionNoticeDismissKey(state.completionNotice)
            );
        }
        if (state.completionCard) {
            state.completionCard.hidden = true;
        }
    }

    function deferCompletionNoticeForSession() {
        state.completionNoticeDeferredKey = buildCompletionNoticeDismissKey(state.completionNotice);
        if (state.completionCard) {
            state.completionCard.hidden = true;
        }
    }

    function isCompletionNoticeDeferredForSession(notice) {
        var key = buildCompletionNoticeDismissKey(notice);
        return !!key && state.completionNoticeDeferredKey === key;
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

    function shouldRequestAppShutdownBeforeClose() {
        if (state.phase === 'selection_intro'
            || state.phase === 'selection_required'
            || state.phase === 'maintenance'
            || state.phase === 'error') {
            return true;
        }
        return shouldBlockMainUi(state.bootstrap);
    }

    async function requestStorageLocationAppShutdown() {
        await ensureStorageCsrfToken();
        var response = await fetchWithTimeout('/api/storage/location/exit', {
            method: 'POST',
            cache: 'no-store',
            headers: storageMutationHeaders({
                'Accept': 'application/json',
                'X-Neko-Storage-Action': 'exit'
            })
        }, STORAGE_EXIT_REQUEST_TIMEOUT_MS);

        var payload = null;
        try {
            payload = await response.json();
        } catch (_) {}

        if (!response.ok || !payload || payload.ok !== true) {
            throw new Error(
                extractResponseError(
                    payload,
                    translate('storage.restartUnavailable', '当前应用暂时无法执行受控重启，请稍后重试。')
                )
            );
        }
    }

    async function closeHostWindowOnly() {
        var host = window.nekoHost || {};
        if (host && typeof host.closeWindow === 'function') {
            try {
                var result = await waitBeforeDeadline(
                    Promise.resolve(host.closeWindow()),
                    Date.now() + STORAGE_HOST_CAPABILITY_TIMEOUT_MS,
                    null
                );
                return !!(result && result.ok === true);
            } catch (error) {
                console.warn('[storage-location] host close failed', error);
                return false;
            }
        }

        try {
            window.close();
            return true;
        } catch (_) {
            return false;
        }
    }

    async function requestHostWindowClose() {
        if (state.submitting) return;
        var host = window.nekoHost || {};
        var hasHostRecoveryCapability = typeof host.getBackendRecoveryState === 'function';
        var hostRecoveryState = await getHostBackendRecoveryState();
        if (state.phase === 'maintenance' && hasHostRecoveryCapability && !hostRecoveryState) {
            // An installed host bridge that cannot report ownership is an
            // unknown state, not permission to interrupt a possible migration.
            return;
        }
        if (hostRecoveryState && (
            hostRecoveryState.state === 'active'
            || hostRecoveryState.state === 'transient'
        )) {
            // Do not ask the backend to exit while a migration generation is
            // active. The intentionally non-dismissible gate protects the copy
            // and owner handoff from a renderer-triggered interruption.
            setMaintenanceFallbackState(hostRecoveryState);
            return;
        }
        if (hostRecoveryState && hostRecoveryState.state === 'terminal') {
            await requestTerminalHostQuit();
            return;
        }
        if (hostRecoveryState && hostRecoveryState.state === 'ready'
            && hostRecoveryState.reason === 'unmanaged_backend') {
            // Attached/remote backends are not owned by this PC shell.
            await closeHostWindowOnly();
            return;
        }
        if (hostRecoveryState && hostRecoveryState.state === 'ready'
            && state.phase === 'maintenance'
            && !normalizeStorageLifecycle(state.lastMaintenanceProgressPayload).rollbackRequired
            && !state.maintenanceStatusUnavailable) {
            // A just-entered maintenance page may beat the main-process phase
            // notification by one IPC turn. Fail closed unless the backend has
            // explicitly reported that migration stopped at rollback_required.
            return;
        }
        if (shouldRequestAppShutdownBeforeClose()) {
            try {
                await requestStorageLocationAppShutdown();
            } catch (error) {
                console.warn('[storage-location] app shutdown request before close failed', error);
                await requestTerminalHostQuit();
                return;
            }
        }
        await closeHostWindowOnly();
    }

    async function getHostBackendRecoveryState() {
        var host = window.nekoHost || {};
        if (!host || typeof host.getBackendRecoveryState !== 'function') return null;
        try {
            var result = await waitBeforeDeadline(
                Promise.resolve(host.getBackendRecoveryState()),
                Date.now() + STORAGE_HOST_CAPABILITY_TIMEOUT_MS,
                null
            );
            return result && typeof result === 'object' ? result : null;
        } catch (error) {
            console.warn('[storage-location] host backend recovery state failed', error);
            return null;
        }
    }

    function resetMaintenanceFallbackState() {
        state.maintenanceRollbackRequired = false;
        state.maintenanceStatusUnavailable = false;
        setMaintenanceFallbackState(null);
    }

    function setMaintenanceFallbackState(recoveryState, message) {
        var isTerminal = !!(recoveryState && recoveryState.state === 'terminal');
        var isRollbackRequired = state.maintenanceRollbackRequired;
        var isStatusUnavailable = state.maintenanceStatusUnavailable;
        var isAwaitingShutdown = state.maintenanceAwaitingShutdown;
        var isUnmanagedRollback = isRollbackRequired
            && recoveryState
            && recoveryState.reason === 'unmanaged_backend';
        if (state.maintenanceFallback) {
            state.maintenanceFallback.hidden = !isTerminal
                && !isRollbackRequired
                && !isStatusUnavailable
                && !isAwaitingShutdown;
        }
        if (!isTerminal && !isRollbackRequired && !isStatusUnavailable && !isAwaitingShutdown) return;

        if (state.maintenanceFallbackMessage) {
            state.maintenanceFallbackMessage.textContent = String(message || '').trim() || translate(
                isStatusUnavailable
                    ? 'storage.storageStatusUnavailableAction'
                    : (isAwaitingShutdown
                    ? 'storage.awaitingShutdownFallback'
                    : (isUnmanagedRollback
                        ? 'storage.rollbackRequiredUnmanagedAction'
                        : (isRollbackRequired
                        ? 'storage.rollbackRequiredAction'
                        : 'storage.maintenanceRecoveryFallback'))),
                isStatusUnavailable
                    ? '存储状态文件当前无法可靠读取。应用会保持阻断；你可以安全退出，修复文件或挂载后再重新启动。'
                    : (isAwaitingShutdown
                    ? '当前服务尚未完成受控关闭，数据迁移还没有开始。你可以安全地重试关闭请求。'
                    : (isUnmanagedRollback
                        ? '当前后端不由桌面端管理。关闭桌面端不会停止迁移服务；请在后端所在主机安全重启并恢复。'
                        : (isRollbackRequired
                        ? '迁移已经停止，不会继续自动重启；请安全退出，并在下次启动时重试恢复。'
                        : '存储服务未能自动恢复。你可以重试恢复服务，或在确认迁移已经停止后安全退出应用。')))
            );
        }
        if (state.maintenanceRetryButton) {
            state.maintenanceRetryButton.hidden = isRollbackRequired || isStatusUnavailable;
            state.maintenanceRetryButton.textContent = translate(
                isAwaitingShutdown
                    ? 'storage.retryControlledShutdown'
                    : 'storage.maintenanceRetryService',
                isAwaitingShutdown ? '重试安全关闭' : '重试恢复服务'
            );
            state.maintenanceRetryButton.disabled = state.maintenanceRecoveryActionInFlight
                || (!isAwaitingShutdown && (
                    !recoveryState
                    || recoveryState.retry_allowed !== true
                ));
        }
        if (state.maintenanceQuitButton) {
            state.maintenanceQuitButton.hidden = isAwaitingShutdown;
            state.maintenanceQuitButton.textContent = translate(
                isUnmanagedRollback
                    ? 'storage.rollbackRequiredUnmanagedQuit'
                    : (isRollbackRequired
                        ? 'storage.rollbackRequiredQuit'
                        : 'storage.maintenanceSafeQuit'),
                isUnmanagedRollback
                    ? '仅关闭桌面端'
                    : (isRollbackRequired ? '安全退出并在下次启动恢复' : '安全退出应用')
            );
            var hostBlocksStatusExit = isStatusUnavailable && !!(
                recoveryState && (
                    recoveryState.state === 'active'
                    || recoveryState.state === 'transient'
                    || (recoveryState.state === 'terminal' && recoveryState.quit_allowed !== true)
                )
            );
            var hostStateIsUnknown = isStatusUnavailable
                && !recoveryState
                && typeof (window.nekoHost || {}).getBackendRecoveryState === 'function';
            state.maintenanceQuitButton.disabled = state.maintenanceRecoveryActionInFlight
                || hostBlocksStatusExit
                || hostStateIsUnknown
                || (!isRollbackRequired && !isStatusUnavailable && !isAwaitingShutdown && (
                    !recoveryState
                    || recoveryState.quit_allowed !== true
                ));
        }
    }

    async function refreshHostRecoveryFallback(pollGeneration, message) {
        var recoveryState = await getHostBackendRecoveryState();
        if (pollGeneration && pollGeneration !== state.maintenancePollGeneration) {
            return null;
        }
        setMaintenanceFallbackState(
            recoveryState,
            message || recoveryState && recoveryState.message
        );
        return recoveryState;
    }

    async function requestTerminalHostQuit() {
        var host = window.nekoHost || {};
        var recoveryState = await getHostBackendRecoveryState();
        if (recoveryState && recoveryState.state === 'ready' && (
            recoveryState.reason === 'unmanaged_backend' || state.phase !== 'maintenance'
        )) {
            await closeHostWindowOnly();
            return true;
        }
        if (!recoveryState || recoveryState.state !== 'terminal' || recoveryState.quit_allowed !== true) {
            setMaintenanceFallbackState(recoveryState);
            return false;
        }
        if (typeof host.requestSafeQuit !== 'function') return false;

        try {
            var result = await waitBeforeDeadline(
                Promise.resolve(host.requestSafeQuit()),
                Date.now() + STORAGE_HOST_CAPABILITY_TIMEOUT_MS,
                null
            );
            if (result && result.ok === true) return true;
        } catch (error) {
            console.warn('[storage-location] safe host quit failed', error);
        }
        setMaintenanceFallbackState(recoveryState, translate(
            'storage.maintenanceSafeQuitFailed',
            '安全退出未能启动，请重试；应用不会在迁移状态不明时强制关闭。'
        ));
        return false;
    }

    async function retryTerminalBackendRecovery() {
        if (state.maintenanceRecoveryActionInFlight) return;
        var host = window.nekoHost || {};
        if (!host || typeof host.retryBackendRecovery !== 'function') return;

        var recoveryState = await getHostBackendRecoveryState();
        if (!recoveryState || recoveryState.state !== 'terminal' || recoveryState.retry_allowed !== true) {
            setMaintenanceFallbackState(recoveryState);
            return;
        }

        state.maintenanceRecoveryActionInFlight = true;
        setMaintenanceFallbackState(recoveryState);
        try {
            var result = await waitBeforeDeadline(
                Promise.resolve(host.retryBackendRecovery()),
                Date.now() + STORAGE_HOST_CAPABILITY_TIMEOUT_MS,
                null
            );
            if (!result || typeof result !== 'object') {
                throw new Error('host backend retry returned an invalid result');
            }
            setMaintenanceFallbackState(result, result.message);
        } catch (error) {
            console.warn('[storage-location] host backend recovery retry failed', error);
            setMaintenanceFallbackState(recoveryState, translate(
                'storage.maintenanceRetryFailed',
                '恢复服务仍未成功，请稍后重试或安全退出应用。'
            ));
        } finally {
            state.maintenanceRecoveryActionInFlight = false;
            var latestState = await getHostBackendRecoveryState();
            setMaintenanceFallbackState(latestState || recoveryState);
        }
    }

    async function retryMaintenanceRecoveryAction() {
        if (!state.maintenanceAwaitingShutdown) {
            await retryTerminalBackendRecovery();
            return;
        }
        if (state.maintenanceRecoveryActionInFlight) return;

        state.maintenanceRecoveryActionInFlight = true;
        var resultMessage = '';
        setMaintenanceFallbackState(null, translate(
            'storage.awaitingShutdownRetrying',
            '正在重新请求服务安全关闭；迁移开始后页面会继续显示真实进度。'
        ));
        try {
            await requestStorageLocationAppShutdown();
            resultMessage = translate(
                'storage.awaitingShutdownAccepted',
                '安全关闭请求已接受，正在等待迁移进程接管。'
            );
        } catch (error) {
            console.warn('[storage-location] controlled shutdown retry failed', error);
            resultMessage = translate(
                'storage.awaitingShutdownRetryFailed',
                '安全关闭请求仍未完成；应用保持阻断，你可以稍后再次重试。'
            );
        } finally {
            state.maintenanceRecoveryActionInFlight = false;
            setMaintenanceFallbackState(null, resultMessage);
        }
    }

    async function requestMaintenanceSafeQuit() {
        if (state.maintenanceRecoveryActionInFlight) return;
        state.maintenanceRecoveryActionInFlight = true;
        var recoveryState = await getHostBackendRecoveryState();
        setMaintenanceFallbackState(recoveryState);
        try {
            if (state.maintenanceRollbackRequired || state.maintenanceStatusUnavailable) {
                await requestHostWindowClose();
            } else {
                await requestTerminalHostQuit();
            }
        } finally {
            state.maintenanceRecoveryActionInFlight = false;
            var latestState = await getHostBackendRecoveryState();
            setMaintenanceFallbackState(latestState || recoveryState);
        }
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

    function buildRestartPreviewReminder() {
        return translate(
            'storage.restartPreviewReminder',
            '更改存储位置后会重启，旧数据默认保留。'
        );
    }

    function existingTargetConfirmationText() {
        return translate(
            'storage.confirmExistingTargetContent',
            '目标文件夹已经包含 N.E.K.O 运行时数据。继续后，迁移会覆盖目标中的同名运行时数据目录，目标目录里的其他文件会保留。确认继续吗？'
        );
    }

    function translateResponseErrorCode(code, fallbackText) {
        switch (String(code || '').trim()) {
            case 'cloudsave_local_state_unavailable':
                return translate('storage.cloudsaveLocalStateUnavailable', '本机状态目录不可用，当前会话已禁用云存档。请先修复 state 路径并重启应用，再进行存储位置变更。');
            case 'directory_picker_unavailable':
                return translate('storage.pickFolderUnavailable', '当前系统目录选择器不可用，请手动输入路径。');
            case 'insufficient_space':
                return translate('storage.blockingInsufficientSpace', '目标卷剩余空间不足，无法安全迁移。');
            case 'recovery_source_unavailable':
                return translate('storage.recoverySourceUnavailable', '原始数据路径当前不可用。请先重连原路径，或显式切回推荐默认路径继续当前会话。');
            case 'restart_not_required':
                return translate('storage.restartNotRequired', '目标路径与当前路径一致，不需要重启。');
            case 'restart_schedule_failed':
                return translate('storage.restartScheduleFailed', '受控重启启动失败，请稍后重试。');
            case 'restart_schedule_rollback_failed':
                return translate(
                    'storage.restartScheduleRollbackFailed',
                    '无法确认受控重启是否已撤销，正在重新读取实际存储状态。'
                );
            case 'restart_outcome_unknown':
                return translate(
                    'storage.restartOutcomeUnknown',
                    '无法确认受控重启结果，正在重新读取实际存储状态。'
                );
            case 'restart_unavailable':
                return translate('storage.restartUnavailable', '当前应用暂时无法执行受控重启，请稍后重试。');
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
            case 'storage_rollback_required':
                return translate(
                    'storage.rollbackRequiredMessage',
                    '迁移未能安全回滚。应用已停止继续迁移；请使用下方恢复操作，或安全退出后重新启动。'
                );
            case 'target_confirmation_required':
                return existingTargetConfirmationText();
            case 'target_not_empty':
                return translate('storage.targetNotEmpty', '目标路径已经包含运行时数据，请确认目标目录后再继续迁移。');
            case 'target_not_writable':
                return translate('storage.blockingTargetNotWritable', '目标路径当前不可写，无法开始迁移流程。');
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
        var lifecycle = normalizeStorageLifecycle(statusPayload);
        if (lifecycle.rollbackRequired) {
            var storage = statusPayload && typeof statusPayload.storage === 'object'
                ? statusPayload.storage
                : {};
            return String(
                statusPayload && statusPayload.maintenance_message
                || statusPayload && statusPayload.last_error_summary
                || storage.last_error_summary
                || ''
            ).trim() || translate(
                'storage.rollbackRequiredMessage',
                '迁移未能安全回滚。应用已停止继续迁移；请使用下方恢复操作，或安全退出后重新启动。'
            );
        }

        var blockingReason = lifecycle.blockingReason;

        switch (blockingReason) {
            case 'migration_pending':
                return translate('storage.maintenanceWaitingSubtitle', '正在关闭，数据会在关闭后迁移并自动重启。');
            case 'recovery_required':
                return translate('storage.recoveryRequired', '检测到需要恢复的存储状态，请先重新确认本次使用的存储位置。');
            case 'selection_required':
                return '';
            default:
                return fallbackText || translate('storage.maintenanceWaitingSubtitle', '正在关闭，数据会在关闭后迁移并自动重启。');
        }
    }

    function extractPreflightDetails(payload, fallbackTargetPath) {
        if (!payload || typeof payload !== 'object') {
            return {
                target_root: String(fallbackTargetPath || '').trim(),
                restart_operation_id: '',
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
            restart_operation_id: String(payload.restart_operation_id || '').trim(),
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
        state.selectionIntroView.hidden = phase !== 'selection_intro';
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
        if (state.maintenanceSubtitle) {
            state.maintenanceSubtitle.textContent = buildMaintenanceSubtitleText(subtitle, status);
        }
    }

    function buildMaintenanceSubtitleText(subtitle, status) {
        var subtitleText = String(subtitle || '').trim();
        var statusText = String(status || '').trim();
        if (!statusText || statusText === subtitleText || isRedundantMaintenanceStatus(statusText)) {
            return subtitleText;
        }
        return subtitleText ? subtitleText + ' ' + statusText : statusText;
    }

    function isRedundantMaintenanceStatus(text) {
        var normalized = String(text || '').trim();
        return normalized === translate('storage.maintenanceWaitingStatus', '服务尚未恢复前，页面会继续停留在这里并自动重试连接。')
            || normalized === translate('storage.maintenanceClosingStatus', '正在关闭...');
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

    function normalizeStorageLifecycle(payload) {
        var source = payload && typeof payload === 'object' ? payload : {};
        var storage = source.storage && typeof source.storage === 'object' ? source.storage : {};
        var migration = source.migration && typeof source.migration === 'object' ? source.migration : {};
        return {
            blockingReason: String(source.blocking_reason || storage.blocking_reason || '').trim(),
            lifecycleState: String(source.lifecycle_state || source.status || '').trim(),
            migrationStage: String(source.migration_stage || migration.status || '').trim(),
            selectionRequired: !!(source.selection_required || storage.selection_required),
            migrationPending: !!(source.migration_pending || storage.migration_pending),
            recoveryRequired: !!(source.recovery_required || storage.recovery_required),
            rollbackRequired: !!(
                source.rollback_required
                || storage.rollback_required
                || source.lifecycle_state === 'rollback_required'
                || source.status === 'rollback_required'
                || migration.status === 'rollback_required'
            ),
            migrationPhase: String(
                source.migration_phase
                || storage.migration_phase
                || ''
            ).trim(),
            shutdownRetryAllowed: !!(
                source.shutdown_retry_allowed
                || storage.shutdown_retry_allowed
            ),
            storageStatusUnavailable: !!(
                source.storage_status_unavailable
                || storage.status_unavailable
                || source.error_code === 'storage_status_unavailable'
                || storage.error_code === 'storage_status_unavailable'
            )
        };
    }

    function isObservedMigrationBlock(payload) {
        var lifecycle = normalizeStorageLifecycle(payload);
        return lifecycle.migrationPending
            || lifecycle.blockingReason === 'migration_pending'
            || lifecycle.lifecycleState === 'maintenance'
            || lifecycle.migrationPhase === 'awaiting_shutdown'
            || lifecycle.migrationStage === 'pending'
            || lifecycle.migrationStage === 'copying'
            || lifecycle.migrationStage === 'verifying'
            || lifecycle.migrationStage === 'publishing';
    }

    function recordMaintenanceObservation(payload) {
        if (isObservedMigrationBlock(payload)) {
            state.maintenanceObservedMigrationBlock = true;
        }
    }

    function isConfirmedMaintenanceReady(payload) {
        if (!payload || payload.ready !== true
            || shouldBlockMainUi(payload)
            || isObservedMigrationBlock(payload)) return false;

        var restartOperation = payload.restart_operation
            && typeof payload.restart_operation === 'object'
            ? payload.restart_operation
            : {};
        var operationState = String(restartOperation.state || '').trim();
        var operationId = String(restartOperation.operation_id || '').trim();
        var operationTarget = String(restartOperation.target_root || '').trim();
        var operationInstanceId = String(restartOperation.instance_id || '').trim();
        var payloadInstanceId = String(payload.instance_id || '').trim();
        var entryInstanceId = String(state.maintenanceEntryInstanceId || '').trim();
        var readyInstanceId = String(payload.instance_id || '').trim();
        var restartedInstance = !!entryInstanceId
            && !!readyInstanceId
            && entryInstanceId !== readyInstanceId;
        if (state.maintenanceOutcomeUnknown
            && state.maintenanceRestartOperationId) {
            return restartedInstance || (
                operationId === state.maintenanceRestartOperationId
                && operationTarget === String(state.pendingSelection.path || '').trim()
                && operationInstanceId
                && operationInstanceId === payloadInstanceId
                && ['cancelled', 'rejected', 'expired'].indexOf(operationState) !== -1
            );
        }

        if (state.maintenanceObservedMigrationBlock) return true;

        // A successful migration/rebind always starts a new backend instance.
        // If the first poll races straight to ready, the instance transition is
        // equivalent to observing the intermediate maintenance generation. A
        // same-instance or identity-less ready can be stale, so keep the gate.
        return restartedInstance;
    }

    function shouldLeaveMaintenanceForSelection(statusPayload) {
        var lifecycle = normalizeStorageLifecycle(statusPayload);
        return lifecycle.blockingReason === 'recovery_required'
            || lifecycle.blockingReason === 'selection_required'
            || lifecycle.lifecycleState === 'recovery_required'
            || lifecycle.lifecycleState === 'selection_required'
            || lifecycle.recoveryRequired
            || lifecycle.selectionRequired
            || lifecycle.migrationStage === 'failed';
    }

    async function fetchStorageLocationBootstrap() {
        var response = await fetchWithTimeout('/api/storage/location/bootstrap', {
            cache: 'no-store',
            headers: {
                'Accept': 'application/json'
            }
        }, STORAGE_STATUS_REQUEST_TIMEOUT_MS);
        if (!response.ok) {
            throw new Error('bootstrap request failed: ' + response.status);
        }

        var payload = await response.json();
        if (!payload || typeof payload !== 'object') {
            throw new Error(
                translate('storage.bootstrapError', '无法读取存储位置初始化信息，请重试。')
            );
        }
        captureStorageCsrfToken(payload);
        return payload;
    }

    async function leaveMaintenanceForSelection(statusPayload, pollGeneration) {
        if (!shouldLeaveMaintenanceForSelection(statusPayload)) return false;

        if (state.maintenanceOutcomeUnknown && state.maintenanceRestartOperationId) {
            var restartOperation = statusPayload && statusPayload.restart_operation;
            var operationState = String(restartOperation && restartOperation.state || '').trim();
            var operationId = String(restartOperation && restartOperation.operation_id || '').trim();
            var operationTarget = String(restartOperation && restartOperation.target_root || '').trim();
            var operationInstanceId = String(restartOperation && restartOperation.instance_id || '').trim();
            var payloadInstanceId = String(statusPayload && statusPayload.instance_id || '').trim();
            var entryInstanceId = String(state.maintenanceEntryInstanceId || '').trim();
            var restartedInstance = !!entryInstanceId
                && !!payloadInstanceId
                && entryInstanceId !== payloadInstanceId;
            var correlatedTerminal = operationId === state.maintenanceRestartOperationId
                && operationTarget === String(state.pendingSelection.path || '').trim()
                && !!operationInstanceId
                && operationInstanceId === payloadInstanceId
                && ['cancelled', 'rejected', 'expired', 'indeterminate'].indexOf(operationState) !== -1;

            // A prepared/in-flight (or uncorrelated) operation may still mutate
            // storage after this poll. Do not expose a second selection request
            // until the same backend proves a terminal outcome, or a new backend
            // generation proves the old request can no longer run.
            if (!restartedInstance && !correlatedTerminal) {
                return false;
            }
        }

        var bootstrapPayload = await fetchStorageLocationBootstrap();
        if (pollGeneration && pollGeneration !== state.maintenancePollGeneration) {
            return false;
        }
        if (shouldShowMaintenanceView(bootstrapPayload) || !shouldShowSelectionView(bootstrapPayload)) {
            return false;
        }

        state.bootstrap = bootstrapPayload;
        resetPreviewState();
        updateSelectionSummary();
        var lastError = String(
            bootstrapPayload.last_error_summary || statusPayload.last_error_summary || ''
        ).trim();
        setSelectionStatus(lastError, !!lastError);
        resetMaintenanceFallbackState();
        setPhase('selection_required');
        return true;
    }

    async function fetchSystemStatus() {
        var response = await fetchWithTimeout('/api/system/status', {
            cache: 'no-store',
            headers: {
                'Accept': 'application/json'
            }
        }, STORAGE_STATUS_REQUEST_TIMEOUT_MS);
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
        captureStorageCsrfToken(payload);
        return payload;
    }

    async function fetchStorageLocationStatus() {
        var statusUrl = '/api/storage/location/status';
        if (state.maintenanceOutcomeUnknown && state.maintenanceRestartOperationId) {
            statusUrl += '?restart_operation_id='
                + encodeURIComponent(state.maintenanceRestartOperationId);
        }
        var response = await fetchWithTimeout(statusUrl, {
            cache: 'no-store',
            headers: {
                'Accept': 'application/json'
            }
        }, STORAGE_STATUS_REQUEST_TIMEOUT_MS);
        if (!response.ok) {
            throw new Error('storage location status request failed: ' + response.status);
        }

        var payload = await response.json();
        if (!payload || payload.ok !== true) {
            throw new Error(
                translate('storage.statusUnexpected', '存储维护状态接口返回了未识别的结果。')
            );
        }
        captureStorageCsrfToken(payload);
        return payload;
    }

    async function reconcileUnknownRestartOperation(statusPayload) {
        if (!state.maintenanceOutcomeUnknown || !state.maintenanceRestartOperationId) {
            return statusPayload;
        }
        var operation = statusPayload && statusPayload.restart_operation;
        if (!operation || String(operation.state || '').trim() !== 'prepared') {
            return statusPayload;
        }
        try {
            var response = await fetchWithTimeout('/api/storage/location/restart/cancel', {
                method: 'POST',
                headers: storageMutationHeaders({
                    'Accept': 'application/json',
                    'Content-Type': 'application/json'
                }),
                body: JSON.stringify({
                    restart_operation_id: state.maintenanceRestartOperationId
                })
            }, STORAGE_STATUS_REQUEST_TIMEOUT_MS);
            var payload = await response.json();
            if (response.ok && payload && payload.ok === true && payload.restart_operation) {
                statusPayload.restart_operation = payload.restart_operation;
            }
        } catch (error) {
            console.warn('[storage-location] restart operation reconcile failed', error);
        }
        return statusPayload;
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
        if (state.selectionActions) {
            state.selectionActions.hidden = false;
        }
    }

    function updateSelectionSummary() {
        if (!state.bootstrap) return;

        var currentRoot = state.bootstrap.current_root || '';
        var recommendedRoot = state.bootstrap.recommended_root || '';

        if (state.currentPath) {
            state.currentPath.textContent = currentRoot;
            state.currentPath.title = currentRoot;
        }
        if (state.recommendedPath) {
            state.recommendedPath.textContent = recommendedRoot;
            state.recommendedPath.title = recommendedRoot;
        }
        if (state.recommendedButton) {
            var recommendedDisabled = !String(recommendedRoot || '').trim();
            state.recommendedButton.dataset.forceDisabled = recommendedDisabled ? '1' : '';
            state.recommendedButton.disabled = state.submitting || recommendedDisabled;
            state.recommendedButton.title = recommendedDisabled ? '' : recommendedRoot;
        }

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

        updateOtherButtonState();
    }

    function updateOtherButtonState() {
        if (!state.useOtherButton) return;
        var disabled = !String(state.otherSelection.path || '').trim();
        state.useOtherButton.dataset.forceDisabled = disabled ? '1' : '';
        state.useOtherButton.disabled = state.submitting || disabled;
    }

    function backToSelection() {
        resetPreviewState();
        setSelectionStatus('', false);
        if (state.customInput) state.customInput.focus();
        setPhase('selection_required');
    }

    function shouldShowSelectionIntro(bootstrapPayload) {
        if (!shouldShowSelectionView(bootstrapPayload)) {
            return false;
        }
        if (!bootstrapPayload || typeof bootstrapPayload !== 'object') {
            return false;
        }
        var blockingReason = String(bootstrapPayload.blocking_reason || '').trim();
        return blockingReason === 'selection_required'
            || (!!bootstrapPayload.selection_required && !bootstrapPayload.recovery_required && !bootstrapPayload.migration_pending);
    }

    function continueFromSelectionIntro() {
        updateSelectionSummary();
        setPhase('selection_required');
        if (state.customInput) {
            state.customInput.focus();
        }
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
        var response = await fetchWithTimeout('/api/storage/location/pick-directory', {
            method: 'POST',
            headers: storageMutationHeaders({
                'Accept': 'application/json',
                'Content-Type': 'application/json'
            }),
            body: JSON.stringify({
                start_path: startPath
            })
        }, STORAGE_DIRECTORY_PICKER_TIMEOUT_MS);

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

    async function openPathWithHostBridge(pathText) {
        var normalizedPath = String(pathText || '').trim();
        if (!normalizedPath) return;

        var host = getHostBridge();
        if (!host || typeof host.openPath !== 'function') {
            if (typeof window.showStatusToast === 'function') {
                window.showStatusToast(
                    translate('storage.openPathFailed', '当前环境无法打开该目录，请手动前往路径。'),
                    4000
                );
            }
            return;
        }

        try {
            var result = await host.openPath({ path: normalizedPath });
            if (result && result.ok === false) {
                throw new Error(result.error || 'openPath failed');
            }
        } catch (error) {
            console.warn('[storage-location] host openPath failed', error);
            if (typeof window.showStatusToast === 'function') {
                window.showStatusToast(
                    translate('storage.openPathFailed', '当前环境无法打开该目录，请手动前往路径。'),
                    4000
                );
            }
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
        if (state.previewText) {
            state.previewText.textContent = buildRestartPreviewReminder(preflight);
        }
        if (state.previewEstimated) {
            state.previewEstimated.textContent = formatBytes(preflight.estimated_required_bytes);
        }
        if (state.previewFreeSpace) {
            state.previewFreeSpace.textContent = formatBytes(preflight.target_free_bytes);
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
    // 不清空 selectionStatus。供 showRestartRequired 走完整流程，以及
    // rebuildModalForLocale 在快照恢复路径上单独使用。
    function populateRestartPreview(payload, fallbackTargetPath, selectionSource) {
        if (!state.bootstrap || !state.previewPanel) return null;

        var preflight = extractPreflightDetails(payload, fallbackTargetPath);
        state.pendingSelection.path = preflight.target_root || '';
        state.pendingSelection.source = selectionSource || '';
        state.pendingSelection.preflight = preflight;
        if (preflight.restart_mode === 'rebind_only') {
            state.previewText.textContent = translate(
                'storage.rebindPreviewNotice',
                '后端已确认：原路径已经可以重新连接。后续会重启到该路径，本次不会复制运行时数据。'
            );
            if (state.previewConfirmButton) {
                state.previewConfirmButton.textContent = translate('storage.confirmReconnect', '确认并重启到原路径');
            }
        } else {
            state.previewText.textContent = buildRestartPreviewReminder(preflight);
            if (state.previewConfirmButton) {
                state.previewConfirmButton.textContent = translate('storage.confirmRestart', '确认并重启');
            }
        }
        updateRestartPreviewPreflight(preflight);
        state.previewPanel.hidden = false;
        if (state.selectionActions) {
            state.selectionActions.hidden = true;
        }
        return preflight;
    }

    function showRestartRequired(payload, fallbackTargetPath, selectionSource) {
        if (!populateRestartPreview(payload, fallbackTargetPath, selectionSource)) return;
        setSelectionStatus('', false);
        setPhase('selection_required');
    }

    function buildMaintenanceStatusText(statusPayload) {
        if (!statusPayload || typeof statusPayload !== 'object') {
            return translate('storage.maintenanceWaitingStatus', '服务尚未恢复前，页面会继续停留在这里并自动重试连接。');
        }

        var lifecycle = normalizeStorageLifecycle(statusPayload);
        if (lifecycle.rollbackRequired) {
            return translate(
                'storage.rollbackRequiredAction',
                '迁移已经停止，不会继续自动重启；请选择恢复服务或安全退出。'
            );
        }
        if (lifecycle.blockingReason === 'recovery_required' || lifecycle.recoveryRequired) {
            return translate('storage.recoveryRequired', '检测到需要恢复的存储状态，请先重新确认本次使用的存储位置。');
        }
        return translate('storage.maintenanceWaitingStatus', '服务尚未恢复前，页面会继续停留在这里并自动重试连接。');
    }

    function buildMaintenanceProgressModel(statusPayload) {
        var lifecycle = normalizeStorageLifecycle(statusPayload);
        var lifecycleState = lifecycle.lifecycleState;
        var migrationStage = lifecycle.migrationStage;
        var restartMode = String(
            statusPayload && statusPayload.restart_mode
            || state.pendingSelection && state.pendingSelection.preflight && state.pendingSelection.preflight.restart_mode
            || ''
        ).trim();
        var isRebindOnly = restartMode === 'rebind_only';
        var hasError = lifecycleState === 'recovery_required'
            || lifecycleState === 'recovery_failed'
            || migrationStage === 'failed'
            || migrationStage === 'rollback_required';
        var percent = 14;
        var activeIndex = 0;
        var label = translate('storage.progressWaitingShutdown', '正在关闭');

        if (lifecycleState === 'ready') {
            percent = 100;
            activeIndex = 3;
            label = translate('storage.progressRecovered', '服务已恢复，正在重新连接页面');
        } else if (lifecycleState === 'recovery_failed') {
            percent = 100;
            activeIndex = 3;
            label = translate('storage.progressRecoveryFailed', '服务未能自动恢复');
        } else {
            switch (migrationStage) {
                case 'pending':
                    percent = 18;
                    activeIndex = 0;
                    label = translate('storage.progressPending', '正在关闭');
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
                    percent = 100;
                    activeIndex = 2;
                    label = translate('storage.progressFailed', '迁移未能完成，正在等待恢复处理');
                    break;
                case 'rollback_required':
                    percent = 100;
                    activeIndex = 2;
                    label = translate('storage.progressRollbackRequired', '迁移已停止，需要恢复处理');
                    break;
                default:
                    percent = isRebindOnly ? 38 : 14;
                    activeIndex = isRebindOnly ? 1 : 0;
                    label = isRebindOnly
                        ? translate('storage.progressRebinding', '正在关闭并重连原始路径')
                        : translate('storage.progressWaitingShutdown', '正在关闭');
                    break;
            }
        }

        return {
            percent: percent,
            activeIndex: activeIndex,
            hasError: hasError,
            label: label,
            steps: [
                translate('storage.progressStepShutdown', '正在关闭'),
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
        card.appendChild(buildStorageLocationCloseButton(dismissCompletionNotice));

        var title = createElement('h3', 'storage-location-panel-title', translate('storage.completionTitle', '存储迁移已完成'));
        var pathList = createElement('div', 'storage-location-path-list storage-location-completion-path-list');

        var targetItem = buildInfoPathRow(translate('storage.targetLabel', '当前生效路径'), 'completionTarget', 'storage-location-completion-path-item');
        var retainedItem = buildInfoPathRow(translate('storage.retainedRoot', '旧数据目录'), 'completionRetained', 'storage-location-completion-path-item');
        var openTargetButton = createElement('button', 'storage-location-completion-link', translate('storage.openTargetRoot', '打开当前路径'));
        openTargetButton.type = 'button';
        openTargetButton.addEventListener('click', function () {
            openPathWithHostBridge(state.completionNotice && state.completionNotice.target_root);
        });
        targetItem.appendChild(openTargetButton);

        var openRetainedButton = createElement('button', 'storage-location-completion-link', translate('storage.openRetainedRoot', '打开旧数据目录'));
        openRetainedButton.type = 'button';
        openRetainedButton.addEventListener('click', function () {
            openPathWithHostBridge(state.completionNotice && state.completionNotice.retained_root);
        });
        retainedItem.appendChild(openRetainedButton);
        pathList.appendChild(targetItem);
        pathList.appendChild(retainedItem);

        var actions = createElement('div', 'storage-location-actions storage-location-completion-actions');
        var cleanupButton = createElement('button', 'storage-location-btn storage-location-btn--primary', translate('storage.cleanupRetainedRoot', '清理旧数据'));
        cleanupButton.type = 'button';
        cleanupButton.addEventListener('click', cleanupRetainedSourceRoot);
        actions.appendChild(cleanupButton);

        var deferButton = createElement('button', 'storage-location-btn storage-location-completion-later', translate('storage.deferRetainedRootCleanup', '暂时不处理'));
        deferButton.type = 'button';
        deferButton.addEventListener('click', deferCompletionNoticeForSession);
        actions.appendChild(deferButton);

        state.completionCard = card;
        state.completionTitle = title;
        state.completionOpenTargetButton = openTargetButton;
        state.completionOpenRetainedButton = openRetainedButton;
        state.completionCleanupButton = cleanupButton;

        title.classList.add('storage-location-panel-title--with-close');
        title.classList.add('storage-location-completion-drag-handle');
        card.appendChild(title);
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
        var nextNotice = notice && typeof notice === 'object' ? notice : null;
        var nextNoticeKey = buildCompletionNoticeDismissKey(nextNotice);
        if (state.completionCleanupBlockedKey
            && state.completionCleanupBlockedKey !== nextNoticeKey) {
            state.completionCleanupBlockedKey = '';
        }
        state.completionNotice = nextNotice;
        if (!state.completionNotice || state.completionNotice.completed !== true || !state.completionNotice.retained_root_exists) {
            state.completionCleanupBlockedKey = '';
            if (state.completionCard) {
                state.completionCard.hidden = true;
            }
            return;
        }
        if (isCompletionNoticeDismissed(state.completionNotice)) {
            if (state.completionCard) {
                state.completionCard.hidden = true;
            }
            return;
        }
        if (isCompletionNoticeDeferredForSession(state.completionNotice)) {
            if (state.completionCard) {
                state.completionCard.hidden = true;
            }
            return;
        }
        if (!state.completionNoticeStartupReleased) {
            if (state.completionCard) {
                state.completionCard.hidden = true;
            }
            return;
        }

        var card = buildCompletionNoticeCard();
        state.completionTarget.textContent = String(state.completionNotice.target_root || '').trim();
        state.completionRetained.textContent = String(state.completionNotice.retained_root || '').trim();
        state.completionOpenTargetButton.hidden = !String(state.completionNotice.target_root || '').trim();
        state.completionOpenRetainedButton.hidden = !String(state.completionNotice.retained_root || '').trim();
        state.completionCleanupButton.hidden = !state.completionNotice.cleanup_available;
        state.completionCleanupButton.disabled = (
            !!nextNoticeKey && state.completionCleanupBlockedKey === nextNoticeKey
        );
        card.hidden = false;
    }

    function handleHomeTutorialStartupRelease(event) {
        var detail = event && event.detail;
        if (!detail || typeof detail.released !== 'boolean') {
            return;
        }

        // Storage initializes before the tutorial manager. Keep this gate closed by default,
        // then follow the manager's teardown-safe release signal instead of tutorial-completed.
        state.completionNoticeStartupReleased = detail.released === true;
        if (!state.completionNoticeStartupReleased) {
            if (state.completionCard) {
                state.completionCard.hidden = true;
            }
            return;
        }

        applyCompletionNotice(state.completionNotice);
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

    async function reconcileRetainedCleanupOutcome(retainedRoot) {
        var lastConfirmedPresent = false;
        for (var attempt = 0; attempt < 6; attempt += 1) {
            if (attempt > 0) await sleep(400);
            try {
                var response = await fetchWithTimeout('/api/storage/location/retained-source', {
                    cache: 'no-store',
                    headers: { 'Accept': 'application/json' }
                }, STORAGE_STATUS_REQUEST_TIMEOUT_MS);
                if (!response.ok) continue;
                var payload = await response.json();
                if (!payload || payload.ok !== true) continue;
                if (payload.completed !== true || payload.retained_root_exists === false) {
                    return 'cleaned';
                }
                if (pathEquals(payload.retained_root, retainedRoot)
                    && payload.retained_root_exists === true) {
                    lastConfirmedPresent = true;
                }
            } catch (_) {}
        }
        return lastConfirmedPresent ? 'present' : 'unknown';
    }

    function finishRetainedCleanupUi() {
        state.completionCleanupBlockedKey = '';
        applyCompletionNotice({ completed: false });
        if (typeof window.showStatusToast === 'function') {
            window.showStatusToast(
                translate('storage.cleanupRetainedRootDone', '旧数据目录已清理，当前仅保留新的运行目录。'),
                4000
            );
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

        state.completionCleanupBlockedKey = buildCompletionNoticeDismissKey(state.completionNotice);
        if (state.completionCleanupButton) {
            state.completionCleanupButton.disabled = true;
        }

        var cleanupError = null;
        try {
            var response = await fetchWithTimeout('/api/storage/location/retained-source/cleanup', {
                method: 'POST',
                headers: storageMutationHeaders({
                    'Accept': 'application/json',
                    'Content-Type': 'application/json'
                }),
                body: JSON.stringify({
                    retained_root: retainedRoot
                })
            }, STORAGE_MUTATION_REQUEST_TIMEOUT_MS);
            var payload = null;
            try {
                payload = await response.json();
            } catch (_) {}
            if (!response.ok || !payload || payload.ok !== true) {
                throw new Error(extractResponseError(payload, translate('storage.cleanupRetainedRootFailed', '清理旧数据目录失败，请稍后重试。')));
            }

            finishRetainedCleanupUi();
            return;
        } catch (error) {
            cleanupError = error;
        }

        // A timeout, truncated body, or late 5xx can arrive after the delete
        // actually committed. Query the authoritative retained-source state
        // before enabling a second destructive request.
        var reconciledOutcome = await reconcileRetainedCleanupOutcome(retainedRoot);
        if (reconciledOutcome === 'cleaned') {
            finishRetainedCleanupUi();
            return;
        }
        if (reconciledOutcome === 'present' && state.completionCleanupButton) {
            state.completionCleanupBlockedKey = '';
            state.completionCleanupButton.disabled = false;
        }
        if (typeof window.showStatusToast === 'function') {
            if (reconciledOutcome === 'unknown') {
                window.showStatusToast(
                    translate(
                        'storage.cleanupRetainedRootOutcomeUnknown',
                        '暂时无法确认旧数据是否已清理。为避免重复删除，按钮会保持锁定；请刷新状态后再操作。'
                    ),
                    6000
                );
            } else {
                window.showStatusToast(
                    String((cleanupError && cleanupError.message) || cleanupError || translate('storage.cleanupRetainedRootFailed', '清理旧数据目录失败，请稍后重试。')),
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
        var selectionOutcomeUnknown = false;

        try {
            var response = null;
            try {
                response = await fetchWithTimeout('/api/storage/location/select', {
                    method: 'POST',
                    headers: storageMutationHeaders({
                        'Accept': 'application/json',
                        'Content-Type': 'application/json'
                    }),
                    body: JSON.stringify({
                        selected_root: normalizedTargetPath,
                        selection_source: selectionSource
                    })
                }, STORAGE_MUTATION_REQUEST_TIMEOUT_MS);
            } catch (requestError) {
                selectionOutcomeUnknown = true;
                throw requestError;
            }

            var payload = null;
            try {
                payload = await response.json();
            } catch (_) {}

            if (!response.ok) {
                var selectionErrorCode = String(payload && payload.error_code || '').trim();
                if (selectionErrorCode === 'restart_schedule_rollback_failed'
                    || selectionErrorCode === 'restart_outcome_unknown') {
                    selectionOutcomeUnknown = true;
                }
                throw new Error(
                    extractResponseError(
                        payload,
                        translate('storage.selectionSubmitFailed', '提交存储位置选择失败，请稍后重试。')
                    )
                );
            }

            if (!payload || payload.ok !== true) {
                selectionOutcomeUnknown = true;
                throw new Error(
                    translate('storage.selectionSubmitUnexpected', '存储位置选择接口返回了未识别的结果。')
                );
            }

            if (payload.result === 'continue_current_session') {
                resetPreviewState();
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

            if (payload.result === 'restart_initiated') {
                state.pendingSelection.path = String(payload.selected_root || normalizedTargetPath).trim();
                state.pendingSelection.source = String(payload.selection_source || selectionSource).trim();
                state.pendingSelection.preflight = extractPreflightDetails(
                    payload,
                    state.pendingSelection.path
                );
                enterMaintenanceMode(payload);
                return;
            }

            throw new Error(
                translate('storage.selectionSubmitUnexpected', '存储位置选择接口返回了未识别的结果。')
            );
        } catch (error) {
            console.warn('[storage-location] select failed', error);
            if (selectionOutcomeUnknown) {
                setPhase('loading');
                setLoadingCopy(
                    translate('storage.loadingTitle', '正在确认存储布局状态'),
                    translate('storage.loadingWaitSubtitle', '主业务界面会在存储状态确认完成后再继续加载。')
                );
                beginSentinelFlow();
                return;
            }
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

    async function renderRollbackRequiredState(statusPayload, pollGeneration) {
        if (!normalizeStorageLifecycle(statusPayload).rollbackRequired) return false;

        state.maintenanceRollbackRequired = true;
        state.maintenanceStatusUnavailable = false;
        state.maintenanceAwaitingShutdown = false;
        await refreshHostRecoveryFallback(pollGeneration);
        if (pollGeneration !== state.maintenancePollGeneration) return true;
        setMaintenanceCopy(
            translate('storage.rollbackRequiredTitle', '存储迁移需要恢复'),
            translateMaintenanceSubtitle(statusPayload),
            buildMaintenanceStatusText(statusPayload)
        );
        applyMaintenanceProgress(statusPayload);
        return true;
    }

    async function renderStorageStatusUnavailableState(statusPayload, pollGeneration) {
        if (!normalizeStorageLifecycle(statusPayload).storageStatusUnavailable) return false;

        state.maintenanceRollbackRequired = false;
        state.maintenanceStatusUnavailable = true;
        state.maintenanceAwaitingShutdown = false;
        var actionMessage = translate(
            'storage.storageStatusUnavailableAction',
            '存储状态文件当前无法可靠读取。应用会保持阻断；你可以安全退出，修复文件或挂载后再重新启动。'
        );
        setMaintenanceFallbackState(null, actionMessage);
        setMaintenanceCopy(
            translate('storage.errorTitle', '暂时无法读取存储位置引导信息'),
            translate('storage.systemStatusUnavailable', '暂时无法确认本地服务状态，请重试。'),
            String(
                statusPayload && (
                    statusPayload.last_error_summary
                    || statusPayload.storage && statusPayload.storage.last_error_summary
                ) || ''
            ).trim()
        );
        applyMaintenanceProgress({
            status: 'storage_status_unavailable',
            lifecycle_state: 'storage_status_unavailable',
            storage_status_unavailable: true,
            error_code: statusPayload && statusPayload.error_code || 'storage_status_unavailable'
        });
        var recoveryState = await getHostBackendRecoveryState();
        if (pollGeneration && pollGeneration !== state.maintenancePollGeneration) return true;
        setMaintenanceFallbackState(recoveryState, actionMessage);
        return true;
    }

    function renderAwaitingShutdownState(statusPayload) {
        var lifecycle = normalizeStorageLifecycle(statusPayload);
        if (lifecycle.migrationPhase !== 'awaiting_shutdown'
            || lifecycle.shutdownRetryAllowed !== true) {
            state.maintenanceAwaitingShutdown = false;
            return false;
        }

        state.maintenanceRollbackRequired = false;
        state.maintenanceStatusUnavailable = false;
        state.maintenanceAwaitingShutdown = true;
        setMaintenanceFallbackState(null, translate(
            'storage.awaitingShutdownFallback',
            '当前服务尚未完成受控关闭，数据迁移还没有开始。你可以安全地重试关闭请求。'
        ));
        return true;
    }

    async function startMaintenancePolling() {
        if (state.maintenancePollPromise) {
            return state.maintenancePollPromise;
        }

        var pollGeneration = ++state.maintenancePollGeneration;
        var pollPromise = (async function () {
            var failureCount = 0;

            while (state.phase === 'maintenance' && pollGeneration === state.maintenancePollGeneration) {
                var pollIntervalMs = 0;
                try {
                    var statusPayload = await fetchStorageLocationStatus();
                    if (pollGeneration !== state.maintenancePollGeneration) return;
                    statusPayload = await reconcileUnknownRestartOperation(statusPayload);
                    if (pollGeneration !== state.maintenancePollGeneration) return;
                    failureCount = 0;
                    pollIntervalMs = Number(statusPayload.poll_interval_ms || 0);
                    if (await renderStorageStatusUnavailableState(statusPayload, pollGeneration)) {
                        if (pollGeneration !== state.maintenancePollGeneration) return;
                        await sleep(pollIntervalMs > 0 ? pollIntervalMs : 900);
                        continue;
                    }
                    if (await renderRollbackRequiredState(statusPayload, pollGeneration)) {
                        if (pollGeneration !== state.maintenancePollGeneration) return;
                        await sleep(pollIntervalMs > 0 ? pollIntervalMs : 900);
                        continue;
                    }
                    recordMaintenanceObservation(statusPayload);
                    renderAwaitingShutdownState(statusPayload);
                    resetMaintenanceFallbackState();

                    if (isConfirmedMaintenanceReady(statusPayload)) {
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

                    if (await leaveMaintenanceForSelection(statusPayload, pollGeneration)) {
                        return;
                    }
                    if (pollGeneration !== state.maintenancePollGeneration) return;

                    setMaintenanceCopy(
                        translate('storage.maintenanceTitle', '正在优化存储布局...'),
                        translateMaintenanceSubtitle(statusPayload),
                        buildMaintenanceStatusText(statusPayload)
                    );
                    applyMaintenanceProgress(statusPayload);
                } catch (_) {
                    try {
                        var fallbackStatusPayload = await fetchSystemStatus();
                        if (pollGeneration !== state.maintenancePollGeneration) return;
                        var fallbackLifecycle = normalizeStorageLifecycle(fallbackStatusPayload);
                        if (fallbackLifecycle.storageStatusUnavailable) {
                            if (await renderStorageStatusUnavailableState(
                                fallbackStatusPayload,
                                pollGeneration
                            )) {
                                if (pollGeneration !== state.maintenancePollGeneration) return;
                                await sleep(pollIntervalMs > 0 ? pollIntervalMs : 900);
                                continue;
                            }
                        }
                        failureCount = 0;
                        pollIntervalMs = Number(fallbackStatusPayload.poll_interval_ms || 0);
                        if (await renderRollbackRequiredState(fallbackStatusPayload, pollGeneration)) {
                            if (pollGeneration !== state.maintenancePollGeneration) return;
                            await sleep(pollIntervalMs > 0 ? pollIntervalMs : 900);
                            continue;
                        }
                        recordMaintenanceObservation(fallbackStatusPayload);
                        renderAwaitingShutdownState(fallbackStatusPayload);
                        resetMaintenanceFallbackState();
                        if (!shouldBlockMainUi(fallbackStatusPayload)
                            && isConfirmedMaintenanceReady(fallbackStatusPayload)) {
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

                        if (await leaveMaintenanceForSelection(fallbackStatusPayload, pollGeneration)) {
                            return;
                        }
                        if (pollGeneration !== state.maintenancePollGeneration) return;

                        setMaintenanceCopy(
                            translate('storage.maintenanceTitle', '正在优化存储布局...'),
                            translateMaintenanceSubtitle(
                                fallbackStatusPayload,
                                translate('storage.maintenanceWaitingSubtitle', '正在关闭，数据会在关闭后迁移并自动重启。')
                            ),
                            buildMaintenanceStatusText(fallbackStatusPayload)
                        );
                        applyMaintenanceProgress(fallbackStatusPayload);
                    } catch (error) {
                        failureCount += 1;
                        state.maintenanceAwaitingShutdown = false;
                        var hostRecoveryState = await refreshHostRecoveryFallback(pollGeneration);
                        if (pollGeneration !== state.maintenancePollGeneration) return;
                        if (hostRecoveryState && hostRecoveryState.state === 'terminal') {
                            setMaintenanceCopy(
                                translate('storage.maintenanceRecoveryTitle', '存储服务需要恢复'),
                                translate(
                                    'storage.maintenanceRecoveryFallback',
                                    '存储服务未能自动恢复。你可以重试恢复服务，或在确认迁移已经停止后安全退出应用。'
                                ),
                                ''
                            );
                            applyMaintenanceProgress({
                                status: 'recovery_failed',
                                lifecycle_state: 'recovery_failed'
                            });
                        } else {
                            setMaintenanceCopy(
                                translate('storage.maintenanceTitle', '正在优化存储布局...'),
                                translate('storage.maintenanceWaitingSubtitle', '正在关闭，数据会在关闭后迁移并自动重启。'),
                                failureCount <= 1
                                    ? translate('storage.maintenanceClosingStatus', '正在关闭...')
                                    : translate('storage.maintenanceOfflineStatus', '连接已暂时中断，正在等待服务恢复。请不要关闭当前页面。')
                            );
                            applyMaintenanceProgress({
                                status: 'maintenance',
                                lifecycle_state: 'maintenance',
                                restart_mode: state.pendingSelection && state.pendingSelection.preflight && state.pendingSelection.preflight.restart_mode
                            });
                        }
                    }
                }

                if (!(pollIntervalMs > 0)) {
                    pollIntervalMs = failureCount > 0 ? 1200 : 900;
                }
                await sleep(pollIntervalMs);
            }
        })();

        state.maintenancePollPromise = pollPromise;
        try {
            await pollPromise;
        } finally {
            if (state.maintenancePollPromise === pollPromise) {
                state.maintenancePollPromise = null;
            }
        }
    }

    function restartMaintenancePolling() {
        var previousPoll = state.maintenancePollPromise;
        state.maintenancePollGeneration += 1;
        if (!previousPoll) {
            startMaintenancePolling();
            return;
        }
        var startSuccessor = function () {
            window.setTimeout(function () {
                if (state.phase === 'maintenance' && !state.maintenancePollPromise) {
                    startMaintenancePolling();
                }
            }, 0);
        };
        previousPoll.then(startSuccessor, startSuccessor);
    }

    function enterMaintenanceMode(payload, options) {
        var previousSystemStatus = state.systemStatus && typeof state.systemStatus === 'object'
            ? state.systemStatus
            : {};
        state.maintenanceEntryInstanceId = String(
            previousSystemStatus.instance_id || payload && payload.instance_id || ''
        ).trim();
        state.maintenanceObservedMigrationBlock = false;
        state.maintenanceRestartOperationId = String(
            payload && payload.restart_operation_id || ''
        ).trim();
        state.maintenanceOutcomeUnknown = !!(
            payload
            && payload.result === 'restart_outcome_unknown'
            && state.maintenanceRestartOperationId
        );
        state.maintenanceRollbackRequired = false;
        state.maintenanceStatusUnavailable = false;
        state.maintenanceAwaitingShutdown = false;
        resetMaintenanceFallbackState();
        setMaintenanceCopy(
            translate('storage.maintenanceTitle', '正在优化存储布局...'),
            translateMaintenanceSubtitle(payload),
            buildMaintenanceStatusText(payload)
        );
        applyMaintenanceProgress(payload || {});
        setPhase('maintenance');
        if (options && options.restartPolling) {
            restartMaintenancePolling();
        } else {
            startMaintenancePolling();
        }
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
        state.pendingSelection.path = targetRoot;
        state.pendingSelection.source = String(normalizedPayload.selection_source || 'custom').trim();
        state.pendingSelection.preflight = extractPreflightDetails(normalizedPayload, targetRoot);
        enterMaintenanceMode(normalizedPayload, { restartPolling: true });
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
        var restartOutcomeUnknown = false;

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

                var response = null;
                try {
                    response = await fetchWithTimeout('/api/storage/location/restart', {
                        method: 'POST',
                        headers: storageMutationHeaders({
                            'Accept': 'application/json',
                            'Content-Type': 'application/json'
                        }),
                        body: JSON.stringify({
                            selected_root: state.pendingSelection.path,
                            selection_source: state.pendingSelection.source || 'user_selected',
                            confirm_existing_target_content: confirmExistingTargetContent,
                            restart_operation_id: String(
                                state.pendingSelection.preflight
                                && state.pendingSelection.preflight.restart_operation_id
                                || ''
                            ).trim()
                        })
                    }, STORAGE_MUTATION_REQUEST_TIMEOUT_MS);
                } catch (requestError) {
                    restartOutcomeUnknown = true;
                    throw requestError;
                }

                var payload = null;
                try {
                    payload = await response.json();
                } catch (_) {}

                if (!response.ok) {
                    var responseErrorCode = String(payload && payload.error_code || '').trim();
                    if (responseErrorCode === 'restart_schedule_rollback_failed'
                        || responseErrorCode === 'restart_outcome_unknown') {
                        restartOutcomeUnknown = true;
                    }
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
                            translate('storage.restartRequestFailed', '准备重启与迁移失败，请稍后重试。')
                        )
                    );
                }

                if (!payload || payload.ok !== true || payload.result !== 'restart_initiated') {
                    restartOutcomeUnknown = true;
                    throw new Error(
                        translate('storage.restartRequestUnexpected', '重启和迁移准备接口返回了未识别的结果。')
                    );
                }

                enterMaintenanceMode(payload);
                return;
            }
        } catch (error) {
            console.warn('[storage-location] restart failed', error);
            if (restartOutcomeUnknown) {
                enterMaintenanceMode({
                    result: 'restart_outcome_unknown',
                    restart_operation_id: String(
                        state.pendingSelection.preflight
                        && state.pendingSelection.preflight.restart_operation_id
                        || ''
                    ).trim(),
                    restart_mode: state.pendingSelection
                        && state.pendingSelection.preflight
                        && state.pendingSelection.preflight.restart_mode,
                    target_root: state.pendingSelection.path,
                    selection_source: state.pendingSelection.source,
                    migration: {
                        status: 'pending',
                        target_root: state.pendingSelection.path
                    }
                });
                return;
            }
            setSelectionStatus(
                String((error && error.message) || error || translate('storage.restartRequestFailed', '准备重启与迁移失败，请稍后重试。')),
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

    function continueWithRecommendedPath() {
        if (!state.bootstrap) return;
        var recommendedRoot = String(state.bootstrap.recommended_root || '').trim();
        if (!recommendedRoot) {
            updateSelectionSummary();
            return;
        }
        submitSelection(recommendedRoot, 'recommended');
    }

    function useOtherPath() {
        var selectionPath = String(
            state.customInput && state.customInput.value
            || state.otherSelection.path
            || ''
        ).trim();
        selectionPath = applyCustomStorageRootDisplay(selectionPath);
        updateOtherButtonState();
        submitSelection(selectionPath, 'custom');
    }

    function buildSelectionView() {
        var view = createElement('section', 'storage-location-view');
        var shell = createElement('div', 'storage-location-shell storage-location-shell--selection');

        var hero = createElement('div', 'storage-location-hero');
        hero.appendChild(createElement('h2', 'storage-location-title', translate('storage.selectionTitle', '存储位置选择')));
        shell.appendChild(hero);

        var banner = createElement('div', 'storage-location-banner');
        banner.hidden = true;
        state.banner = banner;
        shell.appendChild(banner);

        var grid = createElement('div', 'storage-location-grid');

        var pathsPanel = createElement('section', 'storage-location-panel storage-location-selection-panel');
        var pathList = createElement('div', 'storage-location-path-list');

        pathList.appendChild(buildInfoPathRow(
            translate('storage.recommendedPath', '推荐路径'),
            'recommendedPath',
            'storage-location-path-item--recommended'
        ));
        pathList.appendChild(buildInfoPathRow(
            translate('storage.currentPath', '当前路径'),
            'currentPath',
            'storage-location-path-item--inline'
        ));

        var selectedPathItem = createElement('div', 'storage-location-path-item storage-location-path-item--selection-input');
        selectedPathItem.appendChild(createElement('div', 'storage-location-label', translate('storage.selectedPath', '选择路径')));
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
        selectedPathItem.appendChild(inputRow);
        pathList.appendChild(selectedPathItem);
        pathsPanel.appendChild(pathList);
        grid.appendChild(pathsPanel);
        shell.appendChild(grid);

        var actions = createElement('div', 'storage-location-actions storage-location-selection-actions');

        var recommendedButton = registerActionButton(
            createElement('button', 'storage-location-btn storage-location-btn--primary', translate('storage.useRecommended', '使用推荐路径'))
        );
        recommendedButton.type = 'button';
        recommendedButton.addEventListener('click', continueWithRecommendedPath);
        state.recommendedButton = recommendedButton;
        actions.appendChild(recommendedButton);

        var currentButton = registerActionButton(
            createElement('button', 'storage-location-btn storage-location-btn--secondary', translate('storage.useCurrent', '保持当前路径'))
        );
        currentButton.type = 'button';
        currentButton.addEventListener('click', continueWithCurrentPath);
        actions.appendChild(currentButton);

        var useOtherButton = registerActionButton(
            createElement('button', 'storage-location-btn storage-location-btn--secondary', translate('storage.previewOther', '提交该位置'))
        );
        useOtherButton.type = 'button';
        useOtherButton.addEventListener('click', useOtherPath);
        state.useOtherButton = useOtherButton;
        actions.appendChild(useOtherButton);

        shell.appendChild(actions);
        state.selectionActions = actions;

        var selectionStatus = createElement('p', 'storage-location-note');
        selectionStatus.hidden = true;
        state.selectionStatus = selectionStatus;
        shell.appendChild(selectionStatus);

        var previewPanel = createElement('section', 'storage-location-panel storage-location-preview-panel');
        previewPanel.hidden = true;
        state.previewPanel = previewPanel;
        var previewText = createElement('p', 'storage-location-note storage-location-preview-note');
        state.previewText = previewText;
        previewPanel.appendChild(previewText);

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

        previewPanel.appendChild(preflightList);

        var previewBlocking = createElement('p', 'storage-location-note');
        previewBlocking.hidden = true;
        state.previewBlocking = previewBlocking;
        previewPanel.appendChild(previewBlocking);

        var previewActions = createElement('div', 'storage-location-restart-actions');
        var backButton = registerActionButton(
            createElement('button', 'storage-location-btn storage-location-btn--secondary', translate('common.back', '返回重新选择'))
        );
        backButton.type = 'button';
        backButton.addEventListener('click', backToSelection);
        previewActions.appendChild(backButton);

        var confirmRestartButton = registerActionButton(
            createElement('button', 'storage-location-btn storage-location-btn--primary', translate('storage.confirmRestart', '确认并重启'))
        );
        confirmRestartButton.type = 'button';
        confirmRestartButton.addEventListener('click', requestRestart);
        state.previewConfirmButton = confirmRestartButton;
        previewActions.appendChild(confirmRestartButton);
        previewPanel.appendChild(previewActions);
        state.previewActions = previewActions;
        shell.appendChild(previewPanel);

        view.appendChild(shell);
        state.selectionView = view;
        return view;
    }

    function buildSelectionIntroView() {
        var view = createElement('section', 'storage-location-view');
        view.hidden = true;

        var shell = createElement('div', 'storage-location-shell storage-location-shell--intro');
        var card = createElement('section', 'storage-location-intro-card');
        var artwork = createElement('div', 'storage-location-intro-art');
        var image = document.createElement('img');
        image.className = 'storage-location-intro-image';
        image.src = '/static/icons/small_easter_egg.png';
        image.alt = translate('storage.selectionIntroImageAlt', 'N.E.K.O 存储位置迁移插图');
        image.loading = 'eager';
        artwork.appendChild(image);
        card.appendChild(artwork);

        var content = createElement('div', 'storage-location-intro-content');
        var introText = createElement(
            'p',
            'storage-location-intro-text',
            translate(
                'storage.selectionIntroBody',
                '喵呜～人类注意啦！为了让你的角色、记忆和设定更安全，我们把数据搬到更稳的专属小窝啦！再也不怕目录乱跑、权限捣乱弄丢数据咯～你可以继续用原来的窝，也可以给她们选个新的小窝哦！'
            )
        );
        content.appendChild(introText);

        var actions = createElement('div', 'storage-location-actions storage-location-intro-actions');
        var useDedicatedButton = registerActionButton(
            createElement('button', 'storage-location-btn storage-location-btn--primary storage-location-intro-button storage-location-intro-button--primary', translate('storage.selectionIntroPickNew', '推荐存储位置'))
        );
        useDedicatedButton.type = 'button';
        useDedicatedButton.addEventListener('click', continueWithCurrentPath);
        actions.appendChild(useDedicatedButton);

        var chooseOtherButton = registerActionButton(
            createElement('button', 'storage-location-btn storage-location-btn--secondary storage-location-intro-button storage-location-intro-button--secondary', translate('storage.selectionIntroUseCurrent', '其他位置'))
        );
        chooseOtherButton.type = 'button';
        chooseOtherButton.addEventListener('click', continueFromSelectionIntro);
        actions.appendChild(chooseOtherButton);
        content.appendChild(actions);

        card.appendChild(content);
        shell.appendChild(card);

        view.appendChild(shell);
        state.selectionIntroView = view;
        return view;
    }

    function buildLoadingView() {
        var view = createElement('section', 'storage-location-view');
        view.hidden = true;

        var shell = createElement('div', 'storage-location-shell');
        var hero = createElement('div', 'storage-location-hero');
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
        var maintenanceTitle = createElement('h2', 'storage-location-title', translate('storage.maintenanceTitle', '正在优化存储布局...'));
        var maintenanceSubtitle = createElement('p', 'storage-location-subtitle', translate('storage.maintenanceWaitingSubtitle', '正在关闭，数据会在关闭后迁移并自动重启。'));
        var maintenanceProgress = createElement('section', 'storage-location-progress');
        var progressMeta = createElement('div', 'storage-location-progress-meta');
        var progressLabel = createElement('div', 'storage-location-progress-label', translate('storage.progressWaitingShutdown', '正在关闭'));
        var progressValue = createElement('div', 'storage-location-progress-value', '14%');
        var progressTrack = createElement('div', 'storage-location-progress-track');
        progressTrack.setAttribute('role', 'progressbar');
        progressTrack.setAttribute('aria-valuemin', '0');
        progressTrack.setAttribute('aria-valuemax', '100');
        progressTrack.setAttribute('aria-valuenow', '14');
        progressTrack.setAttribute('aria-valuetext', translate('storage.progressWaitingShutdown', '正在关闭'));
        var progressFill = createElement('div', 'storage-location-progress-fill');
        progressTrack.appendChild(progressFill);
        progressMeta.appendChild(progressLabel);
        progressMeta.appendChild(progressValue);
        maintenanceProgress.appendChild(progressMeta);
        maintenanceProgress.appendChild(progressTrack);

        var progressSteps = createElement('div', 'storage-location-progress-steps');
        var maintenanceStepItems = [];
        [
            translate('storage.progressStepShutdown', '正在关闭'),
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

        var maintenanceFallback = createElement('section', 'storage-location-maintenance-fallback');
        maintenanceFallback.hidden = true;
        var maintenanceFallbackMessage = createElement(
            'p',
            'storage-location-note storage-location-note--error',
            translate(
                'storage.maintenanceRecoveryFallback',
                '存储服务未能自动恢复。你可以重试恢复服务，或在确认迁移已经停止后安全退出应用。'
            )
        );
        var maintenanceFallbackActions = createElement('div', 'storage-location-error-actions');
        var maintenanceRetryButton = createElement(
            'button',
            'storage-location-btn storage-location-btn--primary',
            translate('storage.maintenanceRetryService', '重试恢复服务')
        );
        maintenanceRetryButton.type = 'button';
        maintenanceRetryButton.addEventListener('click', retryMaintenanceRecoveryAction);
        var maintenanceQuitButton = createElement(
            'button',
            'storage-location-btn',
            translate('storage.maintenanceSafeQuit', '安全退出应用')
        );
        maintenanceQuitButton.type = 'button';
        maintenanceQuitButton.addEventListener('click', requestMaintenanceSafeQuit);
        maintenanceFallbackActions.appendChild(maintenanceRetryButton);
        maintenanceFallbackActions.appendChild(maintenanceQuitButton);
        maintenanceFallback.appendChild(maintenanceFallbackMessage);
        maintenanceFallback.appendChild(maintenanceFallbackActions);
        maintenanceProgress.appendChild(maintenanceFallback);

        state.maintenanceTitle = maintenanceTitle;
        state.maintenanceSubtitle = maintenanceSubtitle;
        state.maintenanceProgressBar = progressTrack;
        state.maintenanceProgressFill = progressFill;
        state.maintenanceProgressLabel = progressLabel;
        state.maintenanceProgressValue = progressValue;
        state.maintenanceProgressSteps = maintenanceStepItems;
        state.maintenanceFallback = maintenanceFallback;
        state.maintenanceFallbackMessage = maintenanceFallbackMessage;
        state.maintenanceRetryButton = maintenanceRetryButton;
        state.maintenanceQuitButton = maintenanceQuitButton;

        hero.appendChild(maintenanceTitle);
        hero.appendChild(maintenanceSubtitle);
        shell.appendChild(hero);
        shell.appendChild(maintenanceProgress);
        view.appendChild(shell);

        state.maintenanceView = view;
        return view;
    }

    function buildErrorView() {
        var view = createElement('section', 'storage-location-view');
        view.hidden = true;

        var shell = createElement('div', 'storage-location-shell');
        var hero = createElement('div', 'storage-location-hero');
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
        modal.appendChild(buildSelectionIntroView());
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
            maintenanceFallbackVisible: !!(state.maintenanceFallback && !state.maintenanceFallback.hidden),
            maintenanceFallbackMessage: state.maintenanceFallbackMessage
                ? state.maintenanceFallbackMessage.textContent
                : '',
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
        state.selectionIntroView = null;
        state.selectionView = null;
        state.errorView = null;
        state.banner = null;
        state.recommendedPath = null;
        state.currentPath = null;
        state.recommendedButton = null;
        state.customInput = null;
        state.pickFolderButton = null;
        state.useOtherButton = null;
        state.selectionActions = null;
        state.previewPanel = null;
        state.previewText = null;
        state.previewEstimated = null;
        state.previewFreeSpace = null;
        state.previewBlocking = null;
        state.previewConfirmButton = null;
        state.previewActions = null;
        state.selectionStatus = null;
        state.errorText = null;
        state.loadingTitle = null;
        state.loadingSubtitle = null;
        state.maintenanceTitle = null;
        state.maintenanceSubtitle = null;
        state.maintenanceProgressBar = null;
        state.maintenanceProgressFill = null;
        state.maintenanceProgressLabel = null;
        state.maintenanceProgressValue = null;
        state.maintenanceProgressSteps = [];
        state.maintenanceFallback = null;
        state.maintenanceFallbackMessage = null;
        state.maintenanceRetryButton = null;
        state.maintenanceQuitButton = null;
        state.actionButtons = [];

        if (state.completionCard && state.completionCard.parentNode) {
            state.completionCard.parentNode.removeChild(state.completionCard);
        }
        state.completionCard = null;
        state.completionTitle = null;
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
                ''
            );
            if (snapshot.lastMaintenanceProgressPayload) {
                applyMaintenanceProgress(snapshot.lastMaintenanceProgressPayload);
            }
            if (snapshot.maintenanceFallbackVisible && state.maintenanceFallback) {
                state.maintenanceFallback.hidden = false;
                if (state.maintenanceFallbackMessage) {
                    state.maintenanceFallbackMessage.textContent = snapshot.maintenanceFallbackMessage;
                }
                void refreshHostRecoveryFallback();
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

    async function fetchBootstrap(flowStartTime) {
        // 延迟显示 loading overlay，避免从状态确认到 bootstrap 请求整体很快完成时闪屏
        var elapsedTime = Date.now() - flowStartTime;
        var remainingDelay = Math.max(0, 150 - elapsedTime);
        var showTimer = setTimeout(function () {
            setPhase('loading');
            setLoadingCopy(
                translate('storage.loadingTitle', '正在确认存储布局状态'),
                translate('storage.loadingFetchBootstrapSubtitle', '正在准备存储位置选择页面。')
            );
        }, remainingDelay);
        try {
            state.bootstrap = await fetchStorageLocationBootstrap();
            clearTimeout(showTimer);
            if (shouldShowMaintenanceView(state.bootstrap)) {
                enterMaintenanceMode(state.bootstrap);
                return;
            }
            updateSelectionSummary();
            if (shouldShowSelectionView(state.bootstrap)) {
                setPhase(shouldShowSelectionIntro(state.bootstrap) ? 'selection_intro' : 'selection_required');
                return;
            }

            hideOverlay();
            resolveStartupDecision({
                canContinue: true,
                reason: 'status_ready',
            });
            scheduleCompletionNoticePolling();
        } catch (error) {
            clearTimeout(showTimer);
            console.warn('[storage-location] bootstrap failed', error);
            showError(error);
        }
    }

    async function beginSentinelFlow() {
        buildModalDom();
        attachLocaleListener();
        // 延迟显示 loading overlay，避免 waitForSystemStatus 很快完成时闪屏
        var flowStartTime = Date.now();
        var showTimer = setTimeout(function () {
            setPhase('loading');
            setLoadingCopy(
                translate('storage.loadingTitle', '正在确认存储布局状态'),
                translate('storage.loadingWaitSubtitle', '主业务界面会在存储状态确认完成后再继续加载。')
            );
        }, 150);

        try {
            var statusPayload = await waitForSystemStatus();
            clearTimeout(showTimer);
            if (!shouldBlockMainUi(statusPayload)) {
                hideOverlay();
                resolveStartupDecision({
                    canContinue: true,
                    reason: 'status_ready',
                });
                scheduleCompletionNoticePolling();
                return;
            }

            if (normalizeStorageLifecycle(statusPayload).storageStatusUnavailable) {
                enterExternalMaintenanceMode(statusPayload);
                await renderStorageStatusUnavailableState(
                    statusPayload,
                    state.maintenancePollGeneration
                );
                return;
            }

            await fetchBootstrap(flowStartTime);
        } catch (error) {
            clearTimeout(showTimer);
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
    window.addEventListener(HOME_TUTORIAL_STARTUP_RELEASE_EVENT, handleHomeTutorialStartupRelease);

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
