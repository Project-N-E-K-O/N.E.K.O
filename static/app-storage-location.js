(function () {
    if (window.appStorageLocation) return;

    var STORAGE_APP_FOLDER_NAME = 'N.E.K.O';
    var STORAGE_RESTART_MESSAGE_TYPE = 'storage_location_restart_initiated';
    var STORAGE_RESTART_CHANNEL = 'neko_storage_location_channel';
    var STORAGE_COMPLETION_NOTICE_DISMISSED_KEY = 'neko.storageLocation.completionNoticeDismissedKey.v1';
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
        completionTarget: null,
        completionRetained: null,
        completionCleanupButton: null,
        externalMaintenanceNoticeKey: '',
        selectionIntroView: null,
        selectionView: null,
        errorView: null,
        banner: null,
        currentPath: null,
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
        var response = await fetch('/api/storage/location/exit', {
            method: 'POST',
            cache: 'no-store',
            headers: {
                'Accept': 'application/json',
                'X-Neko-Storage-Action': 'exit'
            }
        });

        var payload = null;
        try {
            payload = await response.json();
        } catch (_) {}

        if (!response.ok || !payload || payload.ok !== true) {
            throw new Error(
                extractResponseError(
                    payload,
                    translate('storage.restartUnavailable', '当前实例暂时无法执行受控关闭，请稍后重试。')
                )
            );
        }
    }

    async function closeHostWindowOnly() {
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

    async function requestHostWindowClose() {
        if (state.submitting) return;
        if (shouldRequestAppShutdownBeforeClose()) {
            try {
                await requestStorageLocationAppShutdown();
            } catch (error) {
                console.warn('[storage-location] app shutdown request before close failed', error);
                return;
            }
        }
        await closeHostWindowOnly();
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
            '更改存储位置会先关闭当前实例，关闭后迁移数据并自动重启；旧数据默认保留。'
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
                return '';
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
            || normalized === translate('storage.maintenanceClosingStatus', '正在等待当前实例完成关闭...');
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
        if (state.customInput && !String(state.customInput.value || '').trim()) {
            state.customInput.value = recommendedRoot;
            state.otherSelection.key = 'custom';
            state.otherSelection.path = recommendedRoot;
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
                '后端已确认：原路径已经可以重新连接。后续会关闭当前实例并自动重启到该路径，本次不会复制运行时数据。'
            );
            if (state.previewConfirmButton) {
                state.previewConfirmButton.textContent = translate('storage.confirmReconnect', '确认关闭并重连路径');
            }
        } else {
            state.previewText.textContent = buildRestartPreviewReminder(preflight);
            if (state.previewConfirmButton) {
                state.previewConfirmButton.textContent = translate('storage.confirmRestart', '确认关闭并迁移');
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
                    label = translate('storage.progressPending', '正在准备关闭当前实例');
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
        card.appendChild(buildStorageLocationCloseButton(dismissCompletionNotice));

        var title = createElement('h3', 'storage-location-panel-title', translate('storage.completionTitle', '存储迁移已完成'));
        var pathList = createElement('div', 'storage-location-path-list');

        var targetItem = buildInfoPathRow(translate('storage.targetLabel', '当前生效路径'), 'completionTarget');
        var retainedItem = buildInfoPathRow(translate('storage.retainedRoot', '旧数据目录'), 'completionRetained');
        pathList.appendChild(targetItem);
        pathList.appendChild(retainedItem);

        var actions = createElement('div', 'storage-location-actions');
        var cleanupButton = createElement('button', 'storage-location-btn storage-location-btn--primary', translate('storage.cleanupRetainedRoot', '清理旧数据目录'));
        cleanupButton.type = 'button';
        cleanupButton.addEventListener('click', cleanupRetainedSourceRoot);
        actions.appendChild(cleanupButton);

        state.completionCard = card;
        state.completionTitle = title;
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
        state.completionNotice = notice && typeof notice === 'object' ? notice : null;
        if (!state.completionNotice || state.completionNotice.completed !== true || !state.completionNotice.retained_root_exists) {
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

        var card = buildCompletionNoticeCard();
        state.completionTarget.textContent = String(state.completionNotice.target_root || '').trim();
        state.completionRetained.textContent = String(state.completionNotice.retained_root || '').trim();
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
        setMaintenanceCopy(
            translate('storage.maintenanceTitle', '正在优化存储布局...'),
            translateMaintenanceSubtitle(payload),
            buildMaintenanceStatusText(payload)
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

        var selectedPathItem = createElement('div', 'storage-location-path-item storage-location-path-item--recommended storage-location-path-item--selection-input');
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
        pathList.appendChild(buildInfoPathRow(
            translate('storage.currentPath', '当前路径'),
            'currentPath',
            'storage-location-path-item--inline'
        ));
        pathsPanel.appendChild(pathList);
        grid.appendChild(pathsPanel);
        shell.appendChild(grid);

        var actions = createElement('div', 'storage-location-actions storage-location-selection-actions');

        var useOtherButton = registerActionButton(
            createElement('button', 'storage-location-btn storage-location-btn--primary', translate('storage.previewOther', '提交该位置'))
        );
        useOtherButton.type = 'button';
        useOtherButton.addEventListener('click', useOtherPath);
        state.useOtherButton = useOtherButton;
        actions.appendChild(useOtherButton);

        var currentButton = registerActionButton(
            createElement('button', 'storage-location-btn storage-location-btn--secondary', translate('storage.useCurrent', '保持当前路径'))
        );
        currentButton.type = 'button';
        currentButton.addEventListener('click', continueWithCurrentPath);
        actions.appendChild(currentButton);

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
            createElement('button', 'storage-location-btn storage-location-btn--primary', translate('storage.confirmRestart', '确认关闭并迁移'))
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
            createElement('button', 'storage-location-btn storage-location-btn--primary storage-location-intro-button storage-location-intro-button--primary', translate('storage.selectionIntroPickNew', '使用专属小窝'))
        );
        useDedicatedButton.type = 'button';
        useDedicatedButton.addEventListener('click', continueWithCurrentPath);
        actions.appendChild(useDedicatedButton);

        var chooseOtherButton = registerActionButton(
            createElement('button', 'storage-location-btn storage-location-btn--secondary storage-location-intro-button storage-location-intro-button--secondary', translate('storage.selectionIntroUseCurrent', '其他小窝'))
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
        var maintenanceSubtitle = createElement('p', 'storage-location-subtitle', translate('storage.maintenanceWaitingSubtitle', '当前实例即将关闭，数据会在关闭后迁移并自动重启。'));
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
        state.maintenanceProgressBar = progressTrack;
        state.maintenanceProgressFill = progressFill;
        state.maintenanceProgressLabel = progressLabel;
        state.maintenanceProgressValue = progressValue;
        state.maintenanceProgressSteps = maintenanceStepItems;

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
        state.currentPath = null;
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
        state.actionButtons = [];

        if (state.completionCard && state.completionCard.parentNode) {
            state.completionCard.parentNode.removeChild(state.completionCard);
        }
        state.completionCard = null;
        state.completionTitle = null;
        state.completionTarget = null;
        state.completionRetained = null;
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
            var response = await fetch('/api/storage/location/bootstrap', {
                cache: 'no-store',
                headers: {
                    'Accept': 'application/json'
                }
            });
            clearTimeout(showTimer);
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
