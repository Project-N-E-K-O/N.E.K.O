(function () {
    'use strict';

    const PARENT_ORIGIN = window.location.origin;
    let currentMemoryFile = null;
    let currentMemoryFingerprint = null;
    let currentMemoryIdentityToken = null;
    let chatData = [];
    let currentCatName = '';
    let memoryFileRequestId = 0;
    let memorySaveRequestId = 0;
    let memoryEditRevision = 0;
    let memorySaveInFlight = null;
    let memoryRowExitInProgress = false;
    let memoryRowExitTimer = 0;
    let memoryRowExitOperationId = 0;
    const memoryRowAnimations = new Set();
    const memoryChatRowKeys = new WeakMap();
    let memoryChatRowKeySequence = 0;
    let memorySaveStatusTimer = 0;
    let memorySaveStatusHideTimer = 0;
    let memoryHasUnsavedChanges = false;
    let pendingMemorySelection = null;
    let pendingMemoryClose = false;
    let memoryUnsavedSwitchRestoreFocus = null;
    let memoryUnsavedSwitchPositionFrame = 0;
    let memoryUnsavedSwitchBusy = false;
    let memoryUnsavedSwitchSaveError = '';
    let memoryUnloadPromptSuppressed = false;
    let memoryUnloadPromptSuppressionTimer = 0;
    const MEMORY_ROW_EXIT_MS = 160;
    const MEMORY_ROW_REFLOW_MS = 240;
    const MEMORY_ROW_EXIT_STAGGER_MS = 40;
    const MEMORY_ROW_EXIT_MAX_STAGGER_MS = 400;
    const MEMORY_ROW_EXIT_FALLBACK_MS = 260;
    let storageLocationState = {
        bootstrap: null,
        blockingReason: '',
        loadFailed: false,
        limited: false
    };
    let storagePreflightState = null;
    let storagePreflightBusy = false;
    const STORAGE_APP_FOLDER_NAME = 'N.E.K.O';
    const MEMORY_ROLE_COMPACT_MEDIA_QUERY = window.__memoryRoleCompactMediaQuery;
    const memoryRoleCompactMediaQuery = window.matchMedia
        ? window.matchMedia(MEMORY_ROLE_COMPACT_MEDIA_QUERY)
        : null;
    const memoryReducedMotionMediaQuery = window.matchMedia
        ? window.matchMedia('(prefers-reduced-motion: reduce)')
        : null;
    const MEMORY_RESPONSIVE_LAYOUT_MS = 170;
    let memoryResponsiveLayoutTimer = 0;
    let memoryResponsiveLayoutFrame = 0;
    let memoryResponsiveLayoutToken = 0;
    let memoryWideSidebarExpanded = true;
    let memoryRolePanelPositionFrame = 0;
    let memoryRolePanelResizeObserver = null;
    let memoryRolePanelTop = '';
    const MEMORY_ROLE_HOVER_CLOSE_MS = 120;
    let memoryRoleHoverPreviewOpen = false;
    let memoryRoleHoverCloseTimer = 0;
    let memoryRolePanelInitialized = false;
    let memoryRoleListRequestId = 0;
    const MEMORY_AUXILIARY_PANEL_NAMES = ['settings', 'guide', 'import', 'insights'];
    let activeMemoryAuxiliaryPanel = '';
    let memoryAuxiliaryPanelOpener = null;
    let repetitionInsightsReport = null;
    let repetitionInsightsBusy = false;
    let repetitionInsightsRequestId = 0;
    let repetitionInsightsStatus = null;
    let repetitionInsightsLanguageTouched = false;
    const repetitionInsightsIgnored = new Set();
    let memoryExportLogsBusy = false;
    let memoryExportLogsStatusTimer = 0;
    const MEMORY_EXPORT_LOGS_SUCCESS_MS = 2600;
    const MEMORY_EXPORT_LOGS_ERROR_MS = 5200;
    // 单一来源：app-storage-location.js 在 memory_browser.html 里先于本文件加载并把常量
    // 挂到 window.appStorageLocation 上；这里直接复用，避免两份字面量随时间漂移。
    const STORAGE_RESTART_MESSAGE_TYPE = (window.appStorageLocation && window.appStorageLocation.STORAGE_RESTART_MESSAGE_TYPE)
        || 'storage_location_restart_initiated';
    const STORAGE_RESTART_CHANNEL = (window.appStorageLocation && window.appStorageLocation.STORAGE_RESTART_CHANNEL)
        || 'neko_storage_location_channel';
    const STORAGE_RESTART_SENDER_ID = window.__nekoStorageLocationPageId || (
        'memory-browser-' + Date.now() + '-' + Math.random().toString(36).slice(2)
    );

    const STORAGE_BLOCKING_STATUS_KEYS = {
        selection_required: 'memory.storageSelectionRequired',
        migration_pending: 'memory.storageMigrationPending',
        recovery_required: 'memory.storageRecoveryRequired'
    };

    // selection_required / recovery_required 这两种阻断态本身就需要用户在存储管理弹窗里
    // 完成确认或重连。如果这里也禁用入口就会变成死锁：主内容被 limited-mode 挡着、
    // 但唯一能解锁的按钮也按不动。
    const RECOVERABLE_STORAGE_BLOCKING_REASONS = new Set([
        'selection_required',
        'recovery_required'
    ]);

    function interpolateText(text, options) {
        const values = options && typeof options === 'object' ? options : {};
        return String(text || '').replace(/\{\{\s*([\w.-]+)\s*\}\}/g, function (match, name) {
            if (!Object.prototype.hasOwnProperty.call(values, name)) return match;
            const value = values[name];
            return value === undefined || value === null ? '' : String(value);
        });
    }

    function translate(key, fallback, options) {
        let text = fallback;
        if (window.t) {
            const translated = window.t(key, options || {});
            if (typeof translated === 'string' && translated && translated !== key) {
                text = translated;
            }
        }
        return interpolateText(text, options);
    }

    function setElementText(id, text) {
        const el = document.getElementById(id);
        if (el) {
            el.textContent = text;
        }
    }

    function canExportDesktopLogs() {
        return Boolean(window.nekoHost && typeof window.nekoHost.exportLogs === 'function');
    }

    function clearMemoryExportLogsStatusTimer() {
        if (!memoryExportLogsStatusTimer) return;
        window.clearTimeout(memoryExportLogsStatusTimer);
        memoryExportLogsStatusTimer = 0;
    }

    function getMemoryExportLogsButtonLabel(state) {
        if (state === 'pending') {
            return translate('memory.exportLogsPendingShort', 'Exporting…');
        }
        if (state === 'success') {
            return translate('memory.exportLogsSuccessShort', 'Exported');
        }
        if (state === 'error') {
            return translate('memory.exportLogsErrorShort', 'Failed');
        }
        return translate('memory.exportLogs', 'Export logs');
    }

    function setMemoryExportLogsStatus(message, state, dismissAfterMs) {
        const status = document.getElementById('memory-export-logs-status');
        if (!status) return;
        clearMemoryExportLogsStatusTimer();
        status.textContent = String(message || '');
        status.dataset.state = state || '';
        syncMemoryExportLogsCapability();
        if (dismissAfterMs > 0) {
            memoryExportLogsStatusTimer = window.setTimeout(function () {
                memoryExportLogsStatusTimer = 0;
                setMemoryExportLogsStatus('', '');
            }, dismissAfterMs);
        }
    }

    function syncMemoryExportLogsCapability() {
        const trigger = document.getElementById('memory-export-logs-trigger');
        if (!trigger) return;
        const label = trigger.querySelector('[data-memory-export-label]');
        const status = document.getElementById('memory-export-logs-status');
        const statusState = status ? String(status.dataset.state || '') : '';
        const statusMessage = status ? String(status.textContent || '') : '';
        const available = canExportDesktopLogs();
        trigger.disabled = memoryExportLogsBusy || !available;
        trigger.setAttribute('aria-busy', memoryExportLogsBusy ? 'true' : 'false');
        trigger.dataset.exportState = statusState;
        if (label) label.textContent = getMemoryExportLogsButtonLabel(statusState);
        if (available) {
            trigger.title = statusMessage || translate(
                    'memory.exportLogsReview',
                    'Logs are automatically redacted. Review them before sharing.'
                );
            trigger.setAttribute(
                'aria-label',
                statusMessage || translate('memory.exportLogsAria', 'Export logs')
            );
        } else {
            trigger.title = translate(
                'memory.exportLogsDesktopOnly',
                'Local logs can only be exported from the desktop app.'
            );
            trigger.setAttribute('aria-label', translate(
                'memory.exportLogsDesktopOnlyAria',
                'Export logs (desktop app only)'
            ));
        }
    }

    async function handleMemoryExportLogs() {
        if (memoryExportLogsBusy || !canExportDesktopLogs()) return;
        memoryExportLogsBusy = true;
        setMemoryExportLogsStatus(
            translate('memory.exportLogsPending', 'Preparing logs…'),
            'pending'
        );

        try {
            const result = await window.nekoHost.exportLogs();
            if (result && result.ok && result.cancelled) {
                setMemoryExportLogsStatus('', '');
            } else if (result && result.ok && result.empty) {
                setMemoryExportLogsStatus(
                    translate('memory.exportLogsEmpty', 'No logs were found. A diagnostics note was exported.'),
                    'success',
                    MEMORY_EXPORT_LOGS_SUCCESS_MS
                );
            } else if (result && result.ok) {
                setMemoryExportLogsStatus(
                    translate('memory.exportLogsSuccess', 'Logs exported'),
                    'success',
                    MEMORY_EXPORT_LOGS_SUCCESS_MS
                );
            } else if (result && result.code === 'EXPORT_WRITE_FAILED') {
                setMemoryExportLogsStatus(
                    translate('memory.exportLogsWriteError', 'Could not save to that location. Choose another location and try again.'),
                    'error',
                    MEMORY_EXPORT_LOGS_ERROR_MS
                );
            } else {
                setMemoryExportLogsStatus(
                    translate('memory.exportLogsPackageError', 'Could not package logs. Try again.'),
                    'error',
                    MEMORY_EXPORT_LOGS_ERROR_MS
                );
            }
        } catch (_) {
            setMemoryExportLogsStatus(
                translate('memory.exportLogsPackageError', 'Could not package logs. Try again.'),
                'error',
                MEMORY_EXPORT_LOGS_ERROR_MS
            );
        } finally {
            memoryExportLogsBusy = false;
            syncMemoryExportLogsCapability();
        }
    }

    function initMemoryExportLogs() {
        const trigger = document.getElementById('memory-export-logs-trigger');
        if (!trigger) return;
        trigger.addEventListener('click', handleMemoryExportLogs);
        window.addEventListener('localechange', syncMemoryExportLogsCapability);
        window.addEventListener('pagehide', clearMemoryExportLogsStatusTimer);
        window.addEventListener('beforeunload', clearMemoryExportLogsStatusTimer);
        syncMemoryExportLogsCapability();
    }

    function getTutorialResetNoticeTitle() {
        return translate('memory.tutorialReset', 'Tutorial');
    }

    let activeTutorialResetNotice = null;

    function showTutorialResetNotice(message, options) {
        const config = options && typeof options === 'object' ? options : {};
        const title = config.title || getTutorialResetNoticeTitle();
        const okText = config.okText || translate('common.ok', 'OK');
        const variant = config.variant === 'error' ? 'error' : 'success';
        const focusOrigin = activeTutorialResetNotice
            ? activeTutorialResetNotice.focusOrigin
            : document.activeElement;
        if (activeTutorialResetNotice) {
            activeTutorialResetNotice.dispose(false);
        }

        return new Promise(function (resolve) {
            const backdrop = document.createElement('div');
            backdrop.className = 'tutorial-reset-notice-backdrop';

            const card = document.createElement('div');
            card.className = 'tutorial-reset-notice-card';
            card.setAttribute('role', 'dialog');
            card.setAttribute('aria-modal', 'true');
            card.setAttribute('aria-labelledby', 'tutorial-reset-notice-title');
            card.setAttribute('aria-describedby', 'tutorial-reset-notice-message');
            card.dataset.variant = variant;

            const header = document.createElement('div');
            header.className = 'tutorial-reset-notice-header';

            const mark = document.createElement('span');
            mark.className = 'tutorial-reset-notice-mark';
            mark.setAttribute('aria-hidden', 'true');

            const titleEl = document.createElement('h3');
            titleEl.className = 'tutorial-reset-notice-title';
            titleEl.id = 'tutorial-reset-notice-title';
            titleEl.textContent = title;

            header.appendChild(mark);
            header.appendChild(titleEl);

            const body = document.createElement('div');
            body.className = 'tutorial-reset-notice-body';

            const messageEl = document.createElement('p');
            messageEl.className = 'tutorial-reset-notice-message';
            messageEl.id = 'tutorial-reset-notice-message';
            messageEl.textContent = String(message || '');
            body.appendChild(messageEl);

            const actions = document.createElement('div');
            actions.className = 'tutorial-reset-notice-actions';

            const okButton = document.createElement('button');
            okButton.type = 'button';
            okButton.className = 'tutorial-reset-notice-ok';
            okButton.textContent = okText;
            actions.appendChild(okButton);

            card.appendChild(header);
            card.appendChild(body);
            card.appendChild(actions);
            backdrop.appendChild(card);

            let closed = false;
            let cleaned = false;
            let settled = false;

            function cleanup(restoreFocus) {
                if (cleaned) return;
                cleaned = true;
                document.removeEventListener('keydown', onKeydown);
                if (backdrop.parentNode) {
                    backdrop.parentNode.removeChild(backdrop);
                }
                if (activeTutorialResetNotice && activeTutorialResetNotice.backdrop === backdrop) {
                    activeTutorialResetNotice = null;
                }
                if (restoreFocus && focusOrigin && focusOrigin.isConnected
                    && typeof focusOrigin.focus === 'function') {
                    focusOrigin.focus();
                }
            }

            function settle(result) {
                if (settled) return;
                settled = true;
                resolve(result);
            }

            function close() {
                if (closed) return;
                closed = true;
                backdrop.classList.add('is-closing');
                window.setTimeout(function () {
                    cleanup(true);
                    settle(true);
                }, 160);
            }

            function dispose(result) {
                if (cleaned) return;
                closed = true;
                cleanup(false);
                settle(result);
            }

            function onKeydown(event) {
                if (event.key === 'Tab') {
                    event.preventDefault();
                    okButton.focus();
                    return;
                }
                if (event.key === 'Escape' || event.key === 'Enter') {
                    event.preventDefault();
                    close();
                }
            }

            okButton.addEventListener('click', close);
            backdrop.addEventListener('click', function (event) {
                if (event.target === backdrop) {
                    close();
                }
            });
            document.addEventListener('keydown', onKeydown);
            document.body.appendChild(backdrop);
            activeTutorialResetNotice = { backdrop, dispose, focusOrigin };
            window.setTimeout(function () {
                okButton.focus();
            }, 0);
        });
    }

    function isCompactRolePanelMode() {
        return memoryRoleCompactMediaQuery
            ? memoryRoleCompactMediaQuery.matches
            : window.innerWidth <= 839;
    }

    function getMemoryLayoutMode() {
        return isCompactRolePanelMode() ? 'compact' : 'wide';
    }

    function shouldReduceMemoryMotion() {
        return memoryReducedMotionMediaQuery
            ? memoryReducedMotionMediaQuery.matches
            : Boolean(
                window.matchMedia
                && window.matchMedia('(prefers-reduced-motion: reduce)').matches
            );
    }

    function applyMemoryLayoutMode(mode) {
        setMemoryRoleHoverPreviewOpen(false);
        document.body.dataset.memoryLayout = mode;
        setMemoryRolePanelOpen(false, false);
    }

    function cancelMemoryResponsiveLayoutSchedule() {
        memoryResponsiveLayoutToken += 1;
        if (memoryResponsiveLayoutTimer) {
            window.clearTimeout(memoryResponsiveLayoutTimer);
            memoryResponsiveLayoutTimer = 0;
        }
        if (memoryResponsiveLayoutFrame) {
            window.cancelAnimationFrame(memoryResponsiveLayoutFrame);
            memoryResponsiveLayoutFrame = 0;
        }
    }

    function clearMemoryResponsiveLayoutClasses() {
        document.body.classList.remove(
            'is-memory-responsive-transitioning',
            'is-memory-responsive-collapsing',
            'is-memory-responsive-expanding',
            'is-memory-responsive-wide-start',
            'is-memory-manual-sidebar-transitioning',
            'is-memory-manual-sidebar-collapsing',
            'is-memory-manual-sidebar-expanding',
            'is-memory-manual-sidebar-wide-start'
        );
    }

    function finishMemoryResponsiveLayoutTransition(mode, token) {
        if (token !== memoryResponsiveLayoutToken) return;
        memoryResponsiveLayoutTimer = 0;
        if (getMemoryLayoutMode() !== mode) {
            updateMemoryLayoutMode(getMemoryLayoutMode());
            return;
        }
        if (mode === 'compact') {
            applyMemoryLayoutMode('compact');
        }
        clearMemoryResponsiveLayoutClasses();
    }

    function scheduleMemoryResponsiveLayoutFinish(mode, token) {
        memoryResponsiveLayoutTimer = window.setTimeout(function () {
            finishMemoryResponsiveLayoutTransition(mode, token);
        }, MEMORY_RESPONSIVE_LAYOUT_MS);
    }

    function runMemoryResponsiveLayoutTransition(mode) {
        cancelMemoryResponsiveLayoutSchedule();
        const token = memoryResponsiveLayoutToken;
        const body = document.body;
        const currentMode = body.dataset.memoryLayout;
        const trigger = document.getElementById('memory-role-panel-trigger');

        body.classList.add('is-memory-responsive-transitioning');
        body.classList.remove(
            'is-memory-responsive-collapsing',
            'is-memory-responsive-expanding',
            'is-memory-responsive-wide-start',
            'is-memory-manual-sidebar-transitioning',
            'is-memory-manual-sidebar-collapsing',
            'is-memory-manual-sidebar-expanding',
            'is-memory-manual-sidebar-wide-start'
        );
        if (trigger) {
            trigger.setAttribute(
                'aria-expanded',
                mode === 'wide' && memoryWideSidebarExpanded ? 'true' : 'false'
            );
        }

        if (mode === 'compact') {
            body.classList.add('is-memory-responsive-collapsing');
            scheduleMemoryResponsiveLayoutFinish(mode, token);
            return;
        }

        if (currentMode === 'compact') {
            body.classList.add('is-memory-responsive-wide-start');
            applyMemoryLayoutMode('wide');
            memoryResponsiveLayoutFrame = window.requestAnimationFrame(function () {
                memoryResponsiveLayoutFrame = window.requestAnimationFrame(function () {
                    memoryResponsiveLayoutFrame = 0;
                    if (token !== memoryResponsiveLayoutToken) return;
                    body.classList.remove('is-memory-responsive-wide-start');
                    body.classList.add('is-memory-responsive-expanding');
                    scheduleMemoryResponsiveLayoutFinish(mode, token);
                });
            });
            return;
        }

        body.classList.add('is-memory-responsive-expanding');
        scheduleMemoryResponsiveLayoutFinish(mode, token);
    }

    function finishMemoryWideSidebarTransition(expanded, token) {
        if (token !== memoryResponsiveLayoutToken) return;
        memoryResponsiveLayoutTimer = 0;
        if (getMemoryLayoutMode() !== 'wide') {
            updateMemoryLayoutMode(getMemoryLayoutMode());
            return;
        }
        if (memoryWideSidebarExpanded !== expanded) {
            runMemoryWideSidebarTransition(memoryWideSidebarExpanded);
            return;
        }
        setMemoryRolePanelOpen(false, false);
        clearMemoryResponsiveLayoutClasses();
    }

    function scheduleMemoryWideSidebarFinish(expanded, token) {
        memoryResponsiveLayoutTimer = window.setTimeout(function () {
            finishMemoryWideSidebarTransition(expanded, token);
        }, MEMORY_RESPONSIVE_LAYOUT_MS);
    }

    function runMemoryWideSidebarTransition(expanded) {
        cancelMemoryResponsiveLayoutSchedule();
        const token = memoryResponsiveLayoutToken;
        const body = document.body;
        const currentState = body.dataset.memorySidebar;
        const trigger = document.getElementById('memory-role-panel-trigger');

        body.classList.add('is-memory-manual-sidebar-transitioning');
        body.classList.remove(
            'is-memory-responsive-transitioning',
            'is-memory-responsive-collapsing',
            'is-memory-responsive-expanding',
            'is-memory-responsive-wide-start',
            'is-memory-manual-sidebar-collapsing',
            'is-memory-manual-sidebar-expanding',
            'is-memory-manual-sidebar-wide-start'
        );
        if (trigger) trigger.setAttribute('aria-expanded', expanded ? 'true' : 'false');

        if (!expanded) {
            body.classList.add('is-memory-manual-sidebar-collapsing');
            scheduleMemoryWideSidebarFinish(false, token);
            return;
        }

        if (currentState === 'collapsed') {
            body.classList.add('is-memory-manual-sidebar-wide-start');
            setMemoryRolePanelOpen(false, false);
            memoryResponsiveLayoutFrame = window.requestAnimationFrame(function () {
                memoryResponsiveLayoutFrame = window.requestAnimationFrame(function () {
                    memoryResponsiveLayoutFrame = 0;
                    if (token !== memoryResponsiveLayoutToken) return;
                    body.classList.remove('is-memory-manual-sidebar-wide-start');
                    body.classList.add('is-memory-manual-sidebar-expanding');
                    scheduleMemoryWideSidebarFinish(true, token);
                });
            });
            return;
        }

        body.classList.add('is-memory-manual-sidebar-expanding');
        scheduleMemoryWideSidebarFinish(true, token);
    }

    function teardownMemoryResponsiveLayoutTransition() {
        cancelMemoryResponsiveLayoutSchedule();
        clearMemoryResponsiveLayoutClasses();
    }

    function updateMemoryLayoutMode(mode) {
        const responsiveTransitioning = document.body.classList.contains(
            'is-memory-responsive-transitioning'
        );
        if (
            !responsiveTransitioning
            && document.body.dataset.memoryLayout === mode
        ) {
            return;
        }
        // Keep responsive motion in the live DOM so the editor fills the released
        // column continuously and rapid reversals continue from their current frame.
        if (shouldReduceMemoryMotion() || !memoryWideSidebarExpanded) {
            teardownMemoryResponsiveLayoutTransition();
            applyMemoryLayoutMode(mode);
            return;
        }
        runMemoryResponsiveLayoutTransition(mode);
    }

    function updateWideMemorySidebarExpanded(expanded) {
        const nextExpanded = Boolean(expanded);
        const currentState = document.body.dataset.memorySidebar;
        const manualTransitioning = document.body.classList.contains(
            'is-memory-manual-sidebar-transitioning'
        );
        if (
            !manualTransitioning
            && memoryWideSidebarExpanded === nextExpanded
            && currentState === (nextExpanded ? 'expanded' : 'collapsed')
        ) {
            return;
        }
        setMemoryRoleHoverPreviewOpen(false);
        memoryWideSidebarExpanded = nextExpanded;
        if (shouldReduceMemoryMotion() || getMemoryLayoutMode() !== 'wide') {
            teardownMemoryResponsiveLayoutTransition();
            setMemoryRolePanelOpen(false, false);
            return;
        }
        runMemoryWideSidebarTransition(nextExpanded);
    }

    function initMemoryLayoutMode() {
        applyMemoryLayoutMode(getMemoryLayoutMode());

        const handleModeChange = function () {
            updateMemoryLayoutMode(getMemoryLayoutMode());
        };
        if (memoryRoleCompactMediaQuery) {
            if (typeof memoryRoleCompactMediaQuery.addEventListener === 'function') {
                memoryRoleCompactMediaQuery.addEventListener('change', handleModeChange);
            } else if (typeof memoryRoleCompactMediaQuery.addListener === 'function') {
                memoryRoleCompactMediaQuery.addListener(handleModeChange);
            }
        }
        if (memoryReducedMotionMediaQuery) {
            const handleReducedMotionChange = function (event) {
                if (!event.matches) return;
                teardownMemoryLayoutTransitionAndCommit();
            };
            if (typeof memoryReducedMotionMediaQuery.addEventListener === 'function') {
                memoryReducedMotionMediaQuery.addEventListener('change', handleReducedMotionChange);
            } else if (typeof memoryReducedMotionMediaQuery.addListener === 'function') {
                memoryReducedMotionMediaQuery.addListener(handleReducedMotionChange);
            }
        }
    }

    function teardownMemoryLayoutTransitionAndCommit() {
        setMemoryRoleHoverPreviewOpen(false);
        teardownMemoryResponsiveLayoutTransition();
        applyMemoryLayoutMode(getMemoryLayoutMode());
    }

    function syncMemoryRoleTriggerLabel() {
        const trigger = document.getElementById('memory-role-panel-trigger');
        const node = document.getElementById('memory-compact-current-role-name');
        if (!trigger) return;
        const libraryLabel = translate('memory.compactLibraryLabel', '角色列表');
        const roleName = node ? String(node.textContent || '').trim() : '';
        const label = roleName ? `${libraryLabel} · ${roleName}` : libraryLabel;
        trigger.setAttribute('aria-label', label);
    }

    function setMemoryCurrentRoleName(name) {
        const normalized = String(name || '');
        const node = document.getElementById('memory-compact-current-role-name');
        if (node) node.textContent = normalized;
        syncMemoryRoleTriggerLabel();
    }

    function syncMemoryRolePanelPosition() {
        const panel = document.getElementById('memory-role-panel');
        const utilityBar = document.querySelector('.memory-utility-bar');
        if (!utilityBar) return;
        const nextTop = Math.ceil(utilityBar.getBoundingClientRect().bottom) + 'px';
        if (memoryRolePanelTop === nextTop) return;
        memoryRolePanelTop = nextTop;
        document.body.style.setProperty('--memory-floating-panel-top', nextTop);
        if (panel) panel.style.setProperty('--memory-role-panel-top', nextTop);
    }

    function scheduleMemoryRolePanelPositionSync() {
        if (memoryRolePanelPositionFrame) return;
        memoryRolePanelPositionFrame = window.requestAnimationFrame(function () {
            memoryRolePanelPositionFrame = 0;
            syncMemoryRolePanelPosition();
        });
    }

    function teardownMemoryRolePanelPositionSync() {
        clearMemoryRoleHoverCloseTimer();
        memoryRoleHoverPreviewOpen = false;
        document.body.classList.remove('is-memory-role-hover-preview-open');
        if (memoryRolePanelPositionFrame) {
            window.cancelAnimationFrame(memoryRolePanelPositionFrame);
            memoryRolePanelPositionFrame = 0;
        }
        if (memoryRolePanelResizeObserver) {
            memoryRolePanelResizeObserver.disconnect();
            memoryRolePanelResizeObserver = null;
        }
    }

    function canOpenMemoryRoleHoverPreview() {
        return getMemoryLayoutMode() === 'wide'
            && !memoryWideSidebarExpanded
            && document.body.dataset.memorySidebar === 'collapsed'
            && !document.body.classList.contains('is-memory-manual-sidebar-transitioning')
            && !activeMemoryAuxiliaryPanel;
    }

    function clearMemoryRoleHoverCloseTimer() {
        if (!memoryRoleHoverCloseTimer) return;
        window.clearTimeout(memoryRoleHoverCloseTimer);
        memoryRoleHoverCloseTimer = 0;
    }

    function setMemoryRoleHoverPreviewOpen(open) {
        clearMemoryRoleHoverCloseTimer();
        const nextOpen = Boolean(open && canOpenMemoryRoleHoverPreview());
        memoryRoleHoverPreviewOpen = nextOpen;
        document.body.classList.toggle('is-memory-role-hover-preview-open', nextOpen);
    }

    function scheduleMemoryRoleHoverPreviewClose() {
        if (
            !memoryRoleHoverPreviewOpen
            || memoryRoleHoverCloseTimer
            || document.body.classList.contains('is-memory-switch-confirming')
        ) return;
        memoryRoleHoverCloseTimer = window.setTimeout(function () {
            memoryRoleHoverCloseTimer = 0;
            if (document.body.classList.contains('is-memory-switch-confirming')) return;
            setMemoryRoleHoverPreviewOpen(false);
        }, MEMORY_ROLE_HOVER_CLOSE_MS);
    }

    function isMemoryRoleHoverPreviewSurface(target, hoverTarget, leftColumn, trigger) {
        if (!target) return false;
        return hoverTarget.contains(target)
            || leftColumn.contains(target)
            || trigger.contains(target);
    }

    function isMemoryRoleHoverPreviewCloseTarget(target, editor) {
        return Boolean(target && editor && editor.contains(target));
    }

    function isMemoryRoleHoverEdgeEvent(event, hoverTarget) {
        if (!event || !hoverTarget) return false;
        const clientX = Number(event.clientX);
        const clientY = Number(event.clientY);
        if (!Number.isFinite(clientX) || !Number.isFinite(clientY)) return false;
        const bounds = hoverTarget.getBoundingClientRect();
        return clientX <= bounds.right
            && clientY >= bounds.top
            && clientY <= bounds.bottom;
    }

    function didMemoryRolePointerCrossHoverEdge(event, hoverTarget) {
        if (!event || !hoverTarget || !canOpenMemoryRoleHoverPreview()) return false;
        const clientX = Number(event.clientX);
        const clientY = Number(event.clientY);
        const movementX = Number(event.movementX);
        if (
            !Number.isFinite(clientX)
            || !Number.isFinite(clientY)
            || !Number.isFinite(movementX)
            || movementX <= 0
        ) {
            return false;
        }
        const bounds = hoverTarget.getBoundingClientRect();
        return clientX > bounds.right
            && clientX - movementX <= bounds.right
            && clientY >= bounds.top
            && clientY <= bounds.bottom;
    }

    function setMemoryRolePanelOpen(open, restoreFocus) {
        const trigger = document.getElementById('memory-role-panel-trigger');
        const panel = document.getElementById('memory-role-panel');
        if (!trigger || !panel) return;

        const compact = isCompactRolePanelMode();
        if (!compact) {
            panel.hidden = !memoryWideSidebarExpanded;
            panel.classList.remove('is-open');
            trigger.setAttribute('aria-expanded', memoryWideSidebarExpanded ? 'true' : 'false');
            document.body.dataset.memorySidebar = memoryWideSidebarExpanded
                ? 'expanded'
                : 'collapsed';
            document.body.classList.remove('memory-role-panel-open');
            return;
        }
        syncMemoryRolePanelPosition();
        const shouldOpen = Boolean(open && compact);
        panel.hidden = !shouldOpen;
        panel.classList.toggle('is-open', shouldOpen);
        trigger.setAttribute('aria-expanded', shouldOpen ? 'true' : 'false');
        document.body.classList.toggle('memory-role-panel-open', shouldOpen);
        if (!shouldOpen && restoreFocus && compact) {
            trigger.focus();
        }
    }

    function initMemoryRolePanel() {
        const trigger = document.getElementById('memory-role-panel-trigger');
        const panel = document.getElementById('memory-role-panel');
        const utilityBar = document.querySelector('.memory-utility-bar');
        const hoverTarget = document.getElementById('memory-role-hover-target');
        const leftColumn = document.querySelector('.left-column');
        const editor = document.querySelector('.editor');
        if (!trigger || !panel || memoryRolePanelInitialized) return;
        memoryRolePanelInitialized = true;

        setMemoryRolePanelOpen(false, false);
        window.addEventListener('resize', scheduleMemoryRolePanelPositionSync);
        window.addEventListener('localechange', scheduleMemoryRolePanelPositionSync);
        if (utilityBar && window.ResizeObserver) {
            memoryRolePanelResizeObserver = new ResizeObserver(
                scheduleMemoryRolePanelPositionSync
            );
            memoryRolePanelResizeObserver.observe(utilityBar);
        }
        if (hoverTarget && leftColumn) {
            const handleHoverPreviewLeave = function (event) {
                if (!memoryRoleHoverPreviewOpen) return;
                if (!event.relatedTarget) {
                    clearMemoryRoleHoverCloseTimer();
                    return;
                }
                if (isMemoryRoleHoverPreviewSurface(event.relatedTarget, hoverTarget, leftColumn, trigger)) {
                    clearMemoryRoleHoverCloseTimer();
                    return;
                }
                if (!isMemoryRoleHoverPreviewCloseTarget(event.relatedTarget, editor)) {
                    clearMemoryRoleHoverCloseTimer();
                    return;
                }
                scheduleMemoryRoleHoverPreviewClose();
            };
            hoverTarget.addEventListener('mouseenter', function () {
                setMemoryRoleHoverPreviewOpen(true);
            });
            const openHoverPreviewFromWindowEdge = function (event) {
                if (isMemoryRoleHoverEdgeEvent(event, hoverTarget)) {
                    setMemoryRoleHoverPreviewOpen(true);
                }
            };
            document.addEventListener('mouseenter', openHoverPreviewFromWindowEdge);
            document.addEventListener('mouseleave', function (event) {
                if (!event.relatedTarget) openHoverPreviewFromWindowEdge(event);
            });
            document.addEventListener('mousemove', function (event) {
                if (didMemoryRolePointerCrossHoverEdge(event, hoverTarget)) {
                    setMemoryRoleHoverPreviewOpen(true);
                }
            });
            hoverTarget.addEventListener('mouseleave', handleHoverPreviewLeave);
            leftColumn.addEventListener('mouseenter', function () {
                if (memoryRoleHoverPreviewOpen) clearMemoryRoleHoverCloseTimer();
            });
            leftColumn.addEventListener('mouseleave', handleHoverPreviewLeave);
            document.addEventListener('mouseover', function (event) {
                if (!memoryRoleHoverPreviewOpen) return;
                if (isMemoryRoleHoverPreviewSurface(event.target, hoverTarget, leftColumn, trigger)) {
                    clearMemoryRoleHoverCloseTimer();
                    return;
                }
                if (!isMemoryRoleHoverPreviewCloseTarget(event.target, editor)) {
                    clearMemoryRoleHoverCloseTimer();
                    return;
                }
                scheduleMemoryRoleHoverPreviewClose();
            }, true);
        }
        trigger.addEventListener('click', function () {
            if (isCompactRolePanelMode()) {
                setMemoryRolePanelOpen(trigger.getAttribute('aria-expanded') !== 'true', false);
                return;
            }
            updateWideMemorySidebarExpanded(!memoryWideSidebarExpanded);
        });
        document.addEventListener('keydown', function (event) {
            if (event.key === 'Escape' && memoryRoleHoverPreviewOpen) {
                event.preventDefault();
                setMemoryRoleHoverPreviewOpen(false);
                return;
            }
            if (
                isCompactRolePanelMode()
                && event.key === 'Escape'
                && trigger.getAttribute('aria-expanded') === 'true'
            ) {
                event.preventDefault();
                setMemoryRolePanelOpen(false, true);
            }
        });
        document.addEventListener('click', function (event) {
            const unsavedSwitchDialog = document.getElementById('memory-unsaved-switch-dialog');
            if (
                isCompactRolePanelMode()
                &&
                trigger.getAttribute('aria-expanded') === 'true'
                && !document.body.classList.contains('is-memory-switch-confirming')
                && !document.body.classList.contains('page-tutorial-running')
                && !trigger.contains(event.target)
                && !panel.contains(event.target)
                && (!unsavedSwitchDialog || !unsavedSwitchDialog.contains(event.target))
            ) {
                setMemoryRolePanelOpen(false, false);
            }
        });

    }

    function getMemoryAuxiliaryPanel(name) {
        return document.getElementById('memory-' + name + '-panel');
    }

    function repetitionInsightLanguageFromLocale() {
        const raw = String(
            (window.i18n && window.i18n.language)
            || document.documentElement.lang
            || 'en'
        ).replace('_', '-').toLowerCase();
        if (raw === 'zh-tw' || raw === 'zh-hant') return 'zh-TW';
        if (raw === 'zh' || raw === 'zh-cn' || raw === 'zh-hans') return 'zh-CN';
        const base = raw.split('-')[0];
        return ['en', 'es', 'pt', 'ru', 'ja', 'ko'].includes(base) ? base : 'en';
    }

    function repetitionInsightUsableMessageCount(summary) {
        // The local analysis budget can narrow the selected range, so
        // `assistant_message_count` is what the user asked for while
        // `analyzed_message_count` is what was actually mined. Minimum-sample
        // checks must use the latter: narrowed to one or two replies the
        // three-message threshold is impossible to satisfy, and reporting
        // "no candidates found" would read as an absence rather than as a
        // sample too small to evaluate.
        const source = summary || {};
        const analyzed = source.analyzed_message_count;
        if (analyzed !== undefined && analyzed !== null) return Number(analyzed) || 0;
        return Number(source.assistant_message_count || 0) || 0;
    }

    function repetitionInsightCandidateKey(candidate) {
        return String(candidate.language || '') + '\u0000' + String(candidate.normalized_phrase || '');
    }

    function repetitionInsightAssociations(candidate) {
        if (!repetitionInsightsReport || !Array.isArray(repetitionInsightsReport.associations)) {
            return [];
        }
        const key = repetitionInsightCandidateKey(candidate);
        return repetitionInsightsReport.associations.filter(function (association) {
            return repetitionInsightCandidateKey(association) === key;
        });
    }

    function setRepetitionInsightsStatus(key, fallback, kind, options) {
        repetitionInsightsStatus = key
            ? { key: key, fallback: fallback, kind: kind || '', options: options || {} }
            : null;
        refreshRepetitionInsightsStatus();
    }

    function refreshRepetitionInsightsStatus() {
        const status = document.getElementById('memory-insights-status');
        if (!status) return;
        status.textContent = repetitionInsightsStatus
            ? translate(
                repetitionInsightsStatus.key,
                repetitionInsightsStatus.fallback,
                repetitionInsightsStatus.options
            )
            : '';
        status.className = 'memory-insights-status'
            + (repetitionInsightsStatus && repetitionInsightsStatus.kind
                ? ' is-' + repetitionInsightsStatus.kind
                : '');
    }

    // The recent-memory buttons only cover characters that have a recent.json.
    // A configured character, or one restored from a cloud snapshot carrying
    // time-indexed history without the optional recent file, is analyzable but
    // has no button, so the identity list has to supply it.
    let repetitionInsightExtraCharacters = [];
    // IN FLIGHT, not "has ever succeeded". Latching a successful load for
    // the life of the page meant a character created, renamed, restored or
    // deleted while this window stayed open never reached the selector --
    // permanently, since nothing cleared it. That is the ordinary case in
    // the Electron multi-window flow, where the window that edits
    // characters is not the window showing this panel.
    let repetitionInsightCharactersInFlight = false;
    // When the list last loaded. Zero means never.
    let repetitionInsightCharactersLoadedAt = 0;
    // Retry a failed identity load, but rate-limit it: the loader runs on every
    // panel sync, so a dead endpoint would be re-hit on each character switch
    // and each analyze start/stop. A time window rather than an attempt count --
    // an attempt count is spent by the three syncs that fire during bootstrap,
    // ~126ms apart, so a momentary blip at page load would give up permanently.
    let repetitionInsightCharacterThrottleUntil = 0;
    const REPETITION_INSIGHT_CHARACTER_THROTTLE_MS = 5000;
    // How long a loaded list is trusted before the next panel sync
    // refetches it. Short enough that an identity change shows up on the
    // next look, long enough that repeated syncs do not each hit the
    // endpoint.
    const REPETITION_INSIGHT_CHARACTER_TTL_MS = 30000;

    // The panel analyses a CHARACTER; the editor opens a FILE. Those are the
    // same identity for anyone with a recent.json, and the panel piggybacked on
    // `currentCatName` on that assumption. The identity list breaks it: a
    // configured or imported character with time-indexed history and no
    // recent.json is analysable but has no file to open, so picking it used to
    // leave `currentCatName` untouched and silently analyse whatever the editor
    // still had open. The panel keeps its own target for exactly that case.
    let repetitionInsightsCharacterOverride = '';
    // A range change that FAILED still counts as "the user asked for a report".
    // Dropping the stale report on error is right, but it also made the next
    // range change take the no-report path -- which issues no request and
    // silently wipes the red error, so the selector stopped being a retry.
    let repetitionInsightsRangeRetryPending = false;
    // Storage not settled (selection required, migration pending, recovery).
    // The insights panel used to be disarmed by accident: the limited state
    // blanks `currentCatName`, and every insights control gated on it. A
    // panel-owned target re-arms them, so the lockdown has to be explicit --
    // "clear anti-repeat statistics" writes to disk, and must not run while a
    // migration is still in flight.
    // Starts LOCKED: the flag is lowered only once the storage bootstrap has
    // reported a settled root. Starting unlocked left the whole await window --
    // during which the panel is already interactive -- outside the lockdown.
    let memoryStorageLimited = true;

    function repetitionInsightsTarget() {
        return repetitionInsightsCharacterOverride || currentCatName || '';
    }

    async function loadRepetitionInsightCharacters() {
        // Not before storage settles. `initRepetitionInsights` runs BEFORE
        // `await initStorageLocationPanel()`, so a request issued here would
        // answer for the PRE-settle root -- and the latch below only clears on
        // failure, so a successful stale load would stick for the life of the
        // page, hiding every character in the settled root that has no
        // `recent.json` to fall back on.
        if (memoryStorageLimited) return;
        if (repetitionInsightCharactersInFlight) return;
        if (Date.now() < repetitionInsightCharacterThrottleUntil) return;
        // A bounded TTL rather than a permanent latch. Overlapping syncs
        // still collapse into one request through the in-flight flag,
        // and this is not a poll: the fetch only happens when something
        // syncs the panel, which is the same action that would reveal a
        // stale list.
        if (repetitionInsightCharactersLoadedAt
            && Date.now() - repetitionInsightCharactersLoadedAt
                < REPETITION_INSIGHT_CHARACTER_TTL_MS) return;
        repetitionInsightCharactersInFlight = true;
        let loaded = false;
        try {
            const response = await fetch('/api/memory/insight_characters');
            if (!response.ok) return;
            const data = await response.json();
            if (!data || !Array.isArray(data.characters)) return;
            repetitionInsightExtraCharacters = data.characters
                .map(function (name) { return String(name || '').trim(); })
                .filter(Boolean);
            loaded = true;
            syncRepetitionInsightsCharacterSelect();
        } catch (error) {
            // Best effort: the button-derived list still works without it.
            console.error('Failed to load analyzable characters:', error);
        } finally {
            // Latch a SUCCESSFUL load. The flag stays set across the await, so
            // overlapping syncs still collapse into one request, but a transient
            // failure must not silence the selector for the rest of the window's
            // life -- the identities it carries have no file-list button to fall
            // back on.
            //
            // This is a THROTTLE, not a scheduled retry, and the name says so.
            // Nothing sets a timer here, deliberately: opening the insights
            // panel calls syncRepetitionInsightsControls, and the selector is
            // only visible inside that panel -- so the action that would reveal
            // a stale list is the same action that reloads it. A timer would
            // instead poll a dead endpoint every few seconds for the life of an
            // idle tab, logging each failure. The throttle keeps a persistently
            // dead endpoint down to one request every few seconds rather than
            // one per sync.
            repetitionInsightCharactersInFlight = false;
            if (loaded) {
                repetitionInsightCharactersLoadedAt = Date.now();
            } else {
                repetitionInsightCharacterThrottleUntil = Date.now()
                    + REPETITION_INSIGHT_CHARACTER_THROTTLE_MS;
            }
        }
    }

    function repetitionInsightCharacterEntries() {
        const entries = Array.from(document.querySelectorAll('#memory-file-list .cat-btn[data-filename]'))
            .map(function (button) {
                return {
                    name: String(button.dataset.catname || '').trim(),
                    filename: String(button.dataset.filename || '').trim()
                };
            })
            .filter(function (entry) { return entry.name && entry.filename; });
        const seen = new Set(entries.map(function (entry) { return entry.name; }));
        repetitionInsightExtraCharacters.forEach(function (name) {
            if (!name || seen.has(name)) return;
            seen.add(name);
            // No filename: the selector already handles that shape -- it is how
            // the current character is added -- and only switching the editor
            // needs a file.
            entries.push({ name: name, filename: '' });
        });
        return entries;
    }

    function syncRepetitionInsightsCharacterSelect() {
        const select = document.getElementById('memory-insights-character-select');
        if (!select) return;
        const entries = repetitionInsightCharacterEntries();
        const target = repetitionInsightsTarget();
        if (target && !entries.some(function (entry) { return entry.name === target; })) {
            entries.unshift({ name: target, filename: '' });
        }
        const noCharacterLabel = translate(
            'memory.repetitionInsightsNoCharacter',
            'No character selected'
        );
        const signature = JSON.stringify({ entries: entries, noCharacterLabel: noCharacterLabel });
        if (select.dataset.entries !== signature) {
            select.textContent = '';
            if (!target) {
                const placeholder = document.createElement('option');
                placeholder.value = '';
                placeholder.textContent = noCharacterLabel;
                placeholder.disabled = true;
                select.appendChild(placeholder);
            }
            entries.forEach(function (entry) {
                const option = document.createElement('option');
                option.value = entry.name;
                option.dataset.filename = entry.filename;
                option.textContent = entry.name;
                select.appendChild(option);
            });
            select.dataset.entries = signature;
        }
        select.value = target;
        select.disabled = repetitionInsightsBusy || memoryStorageLimited
            || !!window._memoryImportInProgress || !entries.length;
    }

    function syncRepetitionInsightsControls() {
        const character = document.getElementById('memory-insights-character');
        if (character) {
            const target = repetitionInsightsTarget();
            character.textContent = target || translate(
                'memory.repetitionInsightsNoCharacter',
                'No character selected'
            );
            character.title = target;
        }
        syncRepetitionInsightsCharacterSelect();
        loadRepetitionInsightCharacters();
        const analyze = document.getElementById('memory-insights-analyze');
        const clear = document.getElementById('memory-insights-clear');
        const exportButton = document.getElementById('memory-insights-export');
        const resetEffects = document.getElementById('memory-insights-reset-effects');
        const filterIds = [
            'memory-insights-query',
            'memory-insights-coverage-filter',
            'memory-insights-effect-filter'
        ];
        const scopeIds = [
            'memory-insights-language',
            'memory-insights-limit'
        ];
        // Same three locks the character dropdown carries. Analyze reads from the
        // store and "clear anti-repeat statistics" WRITES to it, so neither may
        // run while an import is replacing the very files they touch.
        const ready = !memoryStorageLimited && !window._memoryImportInProgress
            && !!repetitionInsightsTarget();
        if (analyze) analyze.disabled = repetitionInsightsBusy || !ready;
        if (clear) clear.disabled = repetitionInsightsBusy || !repetitionInsightsReport;
        if (exportButton) exportButton.disabled = repetitionInsightsBusy || !repetitionInsightsReport;
        if (resetEffects) {
            resetEffects.disabled = repetitionInsightsBusy || !ready;
        }
        filterIds.forEach(function (id) {
            const control = document.getElementById(id);
            if (control) control.disabled = repetitionInsightsBusy || !repetitionInsightsReport;
        });
        scopeIds.forEach(function (id) {
            const control = document.getElementById(id);
            // Changing the scope re-runs the analysis, so it belongs behind the
            // same lock as the Analyze button rather than staying live while
            // everything around it is disabled.
            if (control) control.disabled = repetitionInsightsBusy || !ready;
        });
    }

    function setRepetitionInsightsBusy(busy) {
        repetitionInsightsBusy = !!busy;
        syncRepetitionInsightsControls();
    }

    function appendRepetitionInsightsEmptyState(container, key, fallback) {
        const empty = document.createElement('p');
        empty.className = 'memory-insights-description';
        empty.textContent = translate(key, fallback);
        container.appendChild(empty);
    }

    function exportableRepetitionInsightCandidates() {
        if (!repetitionInsightsReport || !Array.isArray(repetitionInsightsReport.candidates)) {
            return [];
        }
        return repetitionInsightsReport.candidates.filter(function (candidate) {
            return candidate
                && candidate.status === 'pending'
                && !repetitionInsightsIgnored.has(repetitionInsightCandidateKey(candidate));
        });
    }

    function visibleRepetitionInsightCandidates() {
        const queryControl = document.getElementById('memory-insights-query');
        const coverageControl = document.getElementById('memory-insights-coverage-filter');
        const effectControl = document.getElementById('memory-insights-effect-filter');
        const query = String(queryControl ? queryControl.value : '').trim().toLocaleLowerCase();
        const coverage = coverageControl ? coverageControl.value : 'all';
        const effectStatus = effectControl ? effectControl.value : 'all';
        return exportableRepetitionInsightCandidates().filter(function (candidate) {
            const rules = Array.isArray(candidate.covered_by_rule_ids)
                ? candidate.covered_by_rule_ids.filter(Boolean)
                : [];
            if (coverage === 'covered' && !rules.length) return false;
            if (coverage === 'uncovered' && rules.length) return false;
            const processed = repetitionInsightAssociations(candidate).length > 0;
            if (effectStatus === 'processed' && !processed) return false;
            if (effectStatus === 'residual' && processed) return false;
            if (!query) return true;
            return [candidate.phrase, candidate.normalized_phrase].concat(rules)
                .some(function (value) {
                    return String(value || '').toLocaleLowerCase().includes(query);
                });
        });
    }

    function appendRepetitionEffectMetric(container, value, key, fallback, options) {
        const settings = options || {};
        const metric = document.createElement('div');
        metric.className = 'memory-insights-effect-metric';
        if (settings.highlight) metric.classList.add('is-highlight');
        if (settings.warning) metric.classList.add('is-warning');
        const number = document.createElement('strong');
        number.textContent = settings.displayValue === undefined
            ? String(Number(value || 0))
            : String(settings.displayValue);
        const label = document.createElement('span');
        label.textContent = translate(key, fallback, settings.translationOptions || {});
        metric.append(number, label);
        container.appendChild(metric);
        return metric;
    }

    function renderRepetitionInsightsEffectiveness() {
        const container = document.getElementById('memory-insights-effectiveness');
        if (!container) return;
        container.textContent = '';
        if (!repetitionInsightsReport) return;
        const effects = repetitionInsightsReport.effectiveness || {};
        const parameters = repetitionInsightsReport.parameters || {};
        // Two scopes reach this panel. Message-scoped aggregates exist only when
        // the persisted replies carried runtime response IDs; otherwise the
        // backend answers with the day-scoped aggregate, which must not be
        // labelled "the latest N replies".
        const messageScoped = effects.scope_type === 'assistant_messages';
        const title = document.createElement('h4');
        title.className = 'memory-insights-section-title';
        if (messageScoped) {
            const scopeLimit = Number(
                effects.assistant_message_limit
                || parameters.assistant_message_limit
                || 0
            );
            title.textContent = translate(
                'memory.repetitionInsightsEffectivenessTitle',
                'Anti-repeat handling in the latest {{count}} replies',
                { count: scopeLimit }
            );
        } else {
            title.textContent = translate(
                'memory.repetitionInsightsEffectivenessTitleDays',
                'Anti-repeat handling over the last {{count}} days',
                { count: Number(effects.period_days || 0) }
            );
        }
        container.appendChild(title);
        if (effects.query_failed === true) {
            appendRepetitionInsightsEmptyState(
                container,
                'memory.repetitionInsightsEffectsUnavailable',
                'Anti-repeat records could not be read. Try checking again.'
            );
            return;
        }
        if (effects.source_available === false) {
            appendRepetitionInsightsEmptyState(
                container,
                messageScoped
                    ? 'memory.repetitionInsightsNoEffects'
                    : 'memory.repetitionInsightsNoEffectsDays',
                messageScoped
                    ? 'No linked anti-repeat records are available for these replies.'
                    : 'No anti-repeat records have been kept for this period yet.'
            );
            return;
        }
        const totals = effects.totals || {};
        const reportSummary = repetitionInsightsReport.summary || {};
        const bm25 = effects.bm25 || {};
        const comparableRewrites = Number(bm25.pair_count || 0);
        // Classify on the RAW ratio and round only for display. Math.round of a
        // ratio in (-0.005, 0) yields -0, and `-0 < 0` is false, so a genuine
        // repetition increase used to be labelled a reduction.
        const reductionRatio = comparableRewrites > 0
            ? Number(bm25.reduction_ratio || 0)
            : null;
        const reductionPercent = reductionRatio === null
            ? null
            : Math.round(reductionRatio * 100);
        const repetitionIncreased = reductionRatio !== null && reductionRatio < 0;
        const summary = document.createElement('div');
        summary.className = 'memory-insights-effect-summary';
        appendRepetitionEffectMetric(
            summary,
            reportSummary.candidate_count,
            'memory.repetitionInsightsRemaining',
            'Repeated expressions still found in final replies'
        );
        appendRepetitionEffectMetric(
            summary,
            totals.detected,
            'memory.repetitionInsightsDetected',
            'Repeated drafts detected'
        );
        appendRepetitionEffectMetric(
            summary,
            totals.regen_guard_passed,
            'memory.repetitionInsightsPassed',
            'Rewrites that passed the checks'
        );
        if (reductionPercent !== null) {
            appendRepetitionEffectMetric(
                summary,
                0,
                repetitionIncreased
                    ? 'memory.repetitionInsightsIncrease'
                    : 'memory.repetitionInsightsReduction',
                repetitionIncreased
                    ? 'Average repetition increase across {{count}} comparable rewrites'
                    : 'Average repetition reduction across {{count}} comparable rewrites',
                {
                    displayValue: Math.abs(reductionPercent) + '%',
                    highlight: true,
                    warning: repetitionIncreased,
                    translationOptions: { count: comparableRewrites }
                }
            );
        }
        container.appendChild(summary);
    }

    function renderRepetitionInsightsResults() {
        const results = document.getElementById('memory-insights-results');
        const visibleCount = document.getElementById('memory-insights-visible-count');
        if (!results) return;
        results.textContent = '';
        if (visibleCount) visibleCount.textContent = '';
        renderRepetitionInsightsEffectiveness();
        if (!repetitionInsightsReport) return;

        const summary = repetitionInsightsReport.summary || {};
        if (summary.source_available === false) {
            appendRepetitionInsightsEmptyState(
                results,
                'memory.repetitionInsightsNoSource',
                'No persisted assistant history is available for this character.'
            );
            return;
        }
        if (summary.content_truncated === true) {
            const clipped = document.createElement('p');
            clipped.className = 'memory-insights-scope-note';
            clipped.textContent = translate(
                'memory.repetitionInsightsReplyClipped',
                'One reply was too long to analyze in full; only its beginning was checked.'
            );
            results.appendChild(clipped);
        }
        if (summary.messages_truncated === true) {
            // The local budget narrowed the window instead of failing; say so,
            // otherwise the counts silently describe fewer replies than asked for.
            //
            // Not "the LATEST n": the fair-share eviction drops the oldest
            // message that is itself over budget, which can be an interior
            // one, so four replies can analyze source lines [1, 3, 4]. The
            // count is honest; the ordering claim was not.
            const trimmed = document.createElement('p');
            trimmed.className = 'memory-insights-scope-note';
            trimmed.textContent = translate(
                'memory.repetitionInsightsScopeTrimmed',
                'These replies were long, so only {{analyzed}} of {{total}} fit the local analysis budget.',
                {
                    analyzed: Number(summary.analyzed_message_count || 0),
                    total: Number(summary.assistant_message_count || 0)
                }
            );
            results.appendChild(trimmed);
        }

        // After the truncation note, so a narrowed window explains itself
        // instead of showing a bare "not enough history".
        if (repetitionInsightUsableMessageCount(summary) < 3) {
            appendRepetitionInsightsEmptyState(
                results,
                'memory.repetitionInsightsInsufficient',
                'At least three persisted assistant messages are required.'
            );
            return;
        }

        const visibleCandidates = visibleRepetitionInsightCandidates();
        const exportableCandidates = exportableRepetitionInsightCandidates();
        if (visibleCount) {
            visibleCount.textContent = translate(
                'memory.repetitionInsightsVisibleCount',
                'Showing {{visible}} of {{total}} final residual fragments',
                {
                    visible: visibleCandidates.length,
                    total: exportableCandidates.length
                }
            );
        }
        if (!visibleCandidates.length) {
            const query = document.getElementById('memory-insights-query');
            const coverage = document.getElementById('memory-insights-coverage-filter');
            const effect = document.getElementById('memory-insights-effect-filter');
            const hasActiveFilter = Boolean(query && query.value.trim())
                || Boolean(coverage && coverage.value !== 'all')
                || Boolean(effect && effect.value !== 'all');
            appendRepetitionInsightsEmptyState(
                results,
                hasActiveFilter
                    ? 'memory.repetitionInsightsNoFilterMatches'
                    : repetitionInsightsIgnored.size
                    ? 'memory.repetitionInsightsAllIgnored'
                    : 'memory.repetitionInsightsNoCandidates',
                hasActiveFilter
                    ? 'No final residual fragments match the current filters.'
                    : repetitionInsightsIgnored.size
                    ? 'All candidates in this result have been ignored.'
                    : 'No repeated-expression candidates were found.'
            );
            return;
        }

        visibleCandidates.forEach(function (candidate) {
            const card = document.createElement('article');
            card.className = 'memory-insights-card';
            const phrase = document.createElement('h4');
            const phraseText = String(candidate.phrase || '');
            phrase.textContent = phraseText;

            const header = document.createElement('div');
            header.className = 'memory-insights-card-header';
            const badges = document.createElement('div');
            badges.className = 'memory-insights-card-badges';

            const meta = document.createElement('div');
            meta.className = 'memory-insights-card-meta';
            const occurrences = document.createElement('span');
            occurrences.className = 'memory-insights-card-meta-item is-occurrences';
            occurrences.textContent = translate(
                'memory.repetitionInsightsOccurrences',
                '{{count}} occurrences',
                { count: Number(candidate.occurrence_count || 0) }
            );
            const messages = document.createElement('span');
            messages.className = 'memory-insights-card-meta-item is-messages';
            messages.textContent = translate(
                'memory.repetitionInsightsMessages',
                '{{count}} messages',
                { count: Number(candidate.message_count || 0) }
            );
            const associations = repetitionInsightAssociations(candidate);
            header.append(phrase, badges);
            meta.append(occurrences, messages);

            const coveredBy = Array.isArray(candidate.covered_by_rule_ids)
                ? candidate.covered_by_rule_ids.filter(Boolean)
                : [];
            const rules = document.createElement('div');
            rules.className = 'memory-insights-card-rules';
            if (coveredBy.length) {
                rules.textContent = translate(
                    'memory.repetitionInsightsCoveredBy',
                    'Covered by rules: {{rules}}',
                    { rules: coveredBy.join(', ') }
                );
            }

            let effectSummary = null;
            if (associations.length) {
                const totals = associations.reduce(function (accumulator, association) {
                    accumulator.detected += Number(association.detected_count || 0);
                    accumulator.regenerated += Number(association.regen_triggered_count || 0);
                    accumulator.passed += Number(association.regen_guard_passed_count || 0);
                    accumulator.blocked += Number(association.blocked_count || 0);
                    return accumulator;
                }, { detected: 0, regenerated: 0, passed: 0, blocked: 0 });
                effectSummary = document.createElement('div');
                effectSummary.className = 'memory-insights-card-effect';
                const detectedMarker = '__NEKO_DETECTED__';
                const passedMarker = '__NEKO_PASSED__';
                const localizedEffect = translate(
                    'memory.repetitionInsightsResidualEffect',
                    'Past handling: detected {{detected}} · rewrites passed {{passed}}',
                    Object.assign({}, totals, {
                        detected: detectedMarker,
                        passed: passedMarker
                    })
                );
                localizedEffect.split(/(__NEKO_DETECTED__|__NEKO_PASSED__)/).forEach(function (part) {
                    if (!part) return;
                    if (part === detectedMarker || part === passedMarker) {
                        const value = document.createElement('strong');
                        value.className = part === detectedMarker
                            ? 'memory-insights-card-effect-value is-detected'
                            : 'memory-insights-card-effect-value is-passed';
                        value.textContent = String(
                            part === detectedMarker ? totals.detected : totals.passed
                        );
                        effectSummary.appendChild(value);
                        return;
                    }
                    effectSummary.appendChild(document.createTextNode(part));
                });
            }

            const ignore = document.createElement('button');
            ignore.type = 'button';
            ignore.className = 'memory-insights-card-ignore';
            ignore.textContent = translate(
                'memory.repetitionInsightsIgnore',
                'Hide from this result'
            );
            ignore.addEventListener('click', function () {
                repetitionInsightsIgnored.add(repetitionInsightCandidateKey(candidate));
                renderRepetitionInsightsResults();
            });
            badges.appendChild(ignore);
            const footer = document.createElement('div');
            footer.className = 'memory-insights-card-footer';
            footer.hidden = coveredBy.length === 0;
            footer.appendChild(rules);

            card.appendChild(header);
            card.appendChild(meta);
            if (effectSummary) card.appendChild(effectSummary);
            card.appendChild(footer);
            results.appendChild(card);
        });
    }

    function resetRepetitionInsightsState() {
        repetitionInsightsRequestId++;
        repetitionInsightsReport = null;
        repetitionInsightsRangeRetryPending = false;
        repetitionInsightsIgnored.clear();
        repetitionInsightsStatus = null;
        const query = document.getElementById('memory-insights-query');
        const coverage = document.getElementById('memory-insights-coverage-filter');
        const effect = document.getElementById('memory-insights-effect-filter');
        if (query) query.value = '';
        if (coverage) coverage.value = 'all';
        if (effect) effect.value = 'all';
        setRepetitionInsightsBusy(false);
        refreshRepetitionInsightsStatus();
        renderRepetitionInsightsResults();
        syncRepetitionInsightsControls();
    }

    function refreshRepetitionInsightsAfterRangeChange() {
        if (repetitionInsightsReport || repetitionInsightsRangeRetryPending) {
            // Keep the existing cards up WHILE the replacement loads, but a
            // FAILED replacement must not leave them standing under a range they
            // were never mined from: the selector already shows the new limit, so
            // the pane and an enabled Export would describe a scope the data does
            // not have, and only a red status line would contradict them.
            analyzeRepetitionInsights({ dropReportOnError: true });
            return;
        }
        resetRepetitionInsightsState();
    }

    async function analyzeRepetitionInsights(options) {
        if (memoryStorageLimited) return;
        if (!repetitionInsightsTarget() || repetitionInsightsBusy) return;
        const languageSelect = document.getElementById('memory-insights-language');
        const limitSelect = document.getElementById('memory-insights-limit');
        if (!languageSelect || !limitSelect) return;

        const targetCharacter = repetitionInsightsTarget();
        // Captured, not watched. The analysis language is written by more than
        // one listener -- i18next dispatches `localechange` synchronously from
        // inside its FIRST `languageChanged` subscriber, so a bump added to the
        // later listener never runs: the value is already updated by then.
        // Comparing at the single point of consumption cannot be defeated by
        // listener order, or by a writer added later.
        const requestedLanguage = languageSelect.value;
        const requestId = ++repetitionInsightsRequestId;
        setRepetitionInsightsBusy(true);
        setRepetitionInsightsStatus(
            'memory.repetitionInsightsLoading',
            'Analyzing persisted assistant messages locally...'
        );
        try {
            const response = await fetch('/api/memory/repetition_insights', {
                method: 'POST',
                cache: 'no-store',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    character_name: targetCharacter,
                    language: languageSelect.value,
                    assistant_message_limit: Number(limitSelect.value)
                })
            });
            const report = await response.json();
            if (requestId !== repetitionInsightsRequestId
                || repetitionInsightsTarget() !== targetCharacter) return;
            if (languageSelect.value !== requestedLanguage) {
                // The app locale re-selected the analysis language while this was
                // in flight, so this response describes a language the panel no
                // longer claims. Returning bare left the loading status latched
                // forever -- the finally only clears the busy flag -- and it was
                // re-translated into every locale the user visited afterwards.
                //
                // Clear the STATUS only. A full reset is too broad here: it also
                // clears the pending range retry, which would leave the range
                // selector inert, and wipes the query and filters the user typed.
                // Nothing else needs clearing -- the locale sync that triggers
                // this only fires when there is no report to begin with.
                repetitionInsightsStatus = null;
                refreshRepetitionInsightsStatus();
                return;
            }
            if (!response.ok || !report || !Array.isArray(report.candidates)) {
                throw new Error('local analysis unavailable');
            }
            repetitionInsightsReport = report;
            repetitionInsightsIgnored.clear();
            repetitionInsightsRangeRetryPending = false;
            renderRepetitionInsightsResults();
            const summary = report.summary || {};
            if (summary.source_available === false) {
                setRepetitionInsightsStatus(
                    'memory.repetitionInsightsNoSource',
                    'No persisted assistant history is available for this character.'
                );
            } else if (repetitionInsightUsableMessageCount(summary) < 3) {
                setRepetitionInsightsStatus(
                    'memory.repetitionInsightsInsufficient',
                    'At least three persisted assistant messages are required.'
                );
            } else {
                const candidatesTruncated = summary.candidates_truncated === true;
                setRepetitionInsightsStatus(
                    candidatesTruncated
                        ? 'memory.repetitionInsightsFoundTruncated'
                        : 'memory.repetitionInsightsFound',
                    candidatesTruncated
                        ? 'Found {{count}} repeated fragments; showing the top {{shown}}.'
                        : 'Found {{count}} repeated fragments.',
                    '',
                    {
                        count: Number(summary.candidate_count || 0),
                        shown: Number(summary.returned_candidate_count || report.candidates.length)
                    }
                );
            }
        } catch (error) {
            if (requestId !== repetitionInsightsRequestId) return;
            if (languageSelect.value !== requestedLanguage) {
                // Same stale-language discard as the success path. A network
                // failure or a non-JSON body throws before that check runs, and
                // reporting the error would attach it to a language the panel no
                // longer claims -- and leave the loading status latched behind it.
                repetitionInsightsStatus = null;
                refreshRepetitionInsightsStatus();
                return;
            }
            if (options && options.dropReportOnError) {
                repetitionInsightsReport = null;
                repetitionInsightsIgnored.clear();
                repetitionInsightsRangeRetryPending = true;
                renderRepetitionInsightsResults();
            }
            setRepetitionInsightsStatus(
                'memory.repetitionInsightsError',
                'Local analysis is unavailable. Please try again.',
                'error'
            );
        } finally {
            if (requestId === repetitionInsightsRequestId) setRepetitionInsightsBusy(false);
        }
    }

    async function resetRepetitionEffectRecords() {
        // Writes to disk. Never on an unsettled storage root -- a migration may
        // still be moving the very directory this would clear.
        if (memoryStorageLimited) return;
        if (!repetitionInsightsTarget() || repetitionInsightsBusy) return;
        if (!window.confirm(translate(
            'memory.repetitionInsightsResetConfirm',
            'Clear this character\'s local anti-repeat effect statistics? Saved chat history and repetition candidates will not be deleted.'
        ))) return;
        const targetCharacter = repetitionInsightsTarget();
        const requestId = ++repetitionInsightsRequestId;
        setRepetitionInsightsBusy(true);
        try {
            const response = await fetch('/api/memory/repetition_effects/reset', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ character_name: targetCharacter })
            });
            const result = await response.json();
            if (requestId !== repetitionInsightsRequestId
                || repetitionInsightsTarget() !== targetCharacter) return;
            if (!response.ok || !result || result.success !== true) {
                throw new Error('effect reset unavailable');
            }
            if (repetitionInsightsReport) {
                // Keep whatever scope the report already had; fabricating a
                // message-scoped shell over a day-scoped report would relabel the
                // panel "the latest N replies" after a reset.
                const previousEffects = repetitionInsightsReport.effectiveness || {};
                const clearedEffects = {
                    source_available: false,
                    totals: {},
                    reason_counts: {},
                    bm25: {},
                    patterns: []
                };
                if (previousEffects.scope_type === 'assistant_messages') {
                    clearedEffects.scope_type = 'assistant_messages';
                    clearedEffects.assistant_message_limit = Number(
                        previousEffects.assistant_message_limit
                        || document.getElementById('memory-insights-limit')?.value
                        || 100
                    );
                    clearedEffects.linked_message_count = 0;
                } else {
                    clearedEffects.period_days = Number(previousEffects.period_days || 30);
                }
                repetitionInsightsReport.effectiveness = clearedEffects;
                repetitionInsightsReport.associations = [];
            }
            renderRepetitionInsightsResults();
            setRepetitionInsightsStatus(
                'memory.repetitionInsightsResetDone',
                'Runtime anti-repeat effect statistics were cleared. Saved replies were not changed.',
                'success'
            );
        } catch (error) {
            if (requestId !== repetitionInsightsRequestId) return;
            setRepetitionInsightsStatus(
                'memory.repetitionInsightsResetError',
                'Could not clear runtime anti-repeat effect statistics. Please try again.',
                'error'
            );
        } finally {
            if (requestId === repetitionInsightsRequestId) setRepetitionInsightsBusy(false);
        }
    }

    function sortRepetitionInsightsJson(value) {
        if (Array.isArray(value)) return value.map(sortRepetitionInsightsJson);
        if (!value || typeof value !== 'object') return value;
        return Object.keys(value).sort().reduce(function (sorted, key) {
            sorted[key] = sortRepetitionInsightsJson(value[key]);
            return sorted;
        }, {});
    }

    function exportRepetitionInsights() {
        const candidates = exportableRepetitionInsightCandidates();
        if (!repetitionInsightsReport || !candidates.length) {
            setRepetitionInsightsStatus(
                'memory.repetitionInsightsExportEmpty',
                'There are no candidates to export.'
            );
            return;
        }
        if (!window.confirm(translate(
            'memory.repetitionInsightsExportWarning',
            'This optional feedback file may contain private wording. Exporting it will not change future replies. Review it before sharing.'
        ))) return;

        const summary = Object.assign({}, repetitionInsightsReport.summary || {}, {
            exported_candidate_count: candidates.length
        });
        const artifact = {
            artifact_type: repetitionInsightsReport.artifact_type,
            candidates: candidates,
            character_name: repetitionInsightsReport.character_name,
            language: repetitionInsightsReport.language,
            parameters: repetitionInsightsReport.parameters,
            schema_version: repetitionInsightsReport.schema_version,
            summary: summary
        };
        const serialized = JSON.stringify(sortRepetitionInsightsJson(artifact), null, 2) + '\n';
        const blob = new Blob([serialized], { type: 'application/json;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const safeCharacter = String(repetitionInsightsTarget() || 'character')
            .normalize('NFKC')
            .replace(/[<>:"/\\|?*\u0000-\u001f]/g, '-')
            .replace(/\s+/g, '-')
            .slice(0, 48) || 'character';
        const link = document.createElement('a');
        link.href = url;
        link.download = 'natural-expression-candidates-'
            + safeCharacter + '-' + String(repetitionInsightsReport.language || 'unknown') + '.json';
        link.hidden = true;
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.setTimeout(function () { URL.revokeObjectURL(url); }, 0);
        setRepetitionInsightsStatus(
            'memory.repetitionInsightsExported',
            'Candidates exported. Future replies were not changed.',
            'success'
        );
    }

    function initRepetitionInsights() {
        const character = document.getElementById('memory-insights-character-select');
        const language = document.getElementById('memory-insights-language');
        const limit = document.getElementById('memory-insights-limit');
        const analyze = document.getElementById('memory-insights-analyze');
        const clear = document.getElementById('memory-insights-clear');
        const exportButton = document.getElementById('memory-insights-export');
        const resetEffects = document.getElementById('memory-insights-reset-effects');
        const query = document.getElementById('memory-insights-query');
        const coverage = document.getElementById('memory-insights-coverage-filter');
        const effectFilter = document.getElementById('memory-insights-effect-filter');
        if (!character || !language || !limit || !analyze || !clear
            || !exportButton || !resetEffects || !query || !coverage || !effectFilter) return;
        character.addEventListener('change', function () {
            const option = character.options[character.selectedIndex];
            const name = String(character.value || '');
            const filename = option ? String(option.dataset.filename || '') : '';
            const button = filename ? findMemoryRoleButton(filename) : null;
            const listItem = button ? button.closest('li') : null;
            if (name === repetitionInsightsTarget()) {
                syncRepetitionInsightsControls();
                return;
            }
            if (name === currentCatName) {
                // The editor is ALREADY on this character; only the panel needs
                // re-coupling. Routing this through requestMemoryFileSelection
                // opened the unsaved-switch dialog for the file already in the
                // editor, and "discard" then reloaded it from disk -- destroying
                // the user's unsaved edits for something that was never a switch.
                repetitionInsightsCharacterOverride = '';
                resetRepetitionInsightsState();
                return;
            }
            if (button && listItem) {
                // A different character that has an editor file: switch to it,
                // which resets the panel and clears any override on the way
                // through selectMemoryFile.
                requestMemoryFileSelection(filename, listItem, name);
                syncRepetitionInsightsControls();
                return;
            }
            // No editor file for this identity. Dropping the selection here left
            // the previous character as the analysis target while the dropdown
            // snapped back -- so Analyze silently reported on the wrong
            // character. Take the selection as the panel's own target instead.
            repetitionInsightsCharacterOverride = name;
            resetRepetitionInsightsState();
        });
        language.value = repetitionInsightLanguageFromLocale();
        language.addEventListener('change', function () {
            repetitionInsightsLanguageTouched = true;
            resetRepetitionInsightsState();
        });
        limit.addEventListener('change', refreshRepetitionInsightsAfterRangeChange);
        // Wrapped, not passed directly: the handler now takes an options object,
        // and a click listener would hand it the Event instead.
        analyze.addEventListener('click', function () {
            analyzeRepetitionInsights();
        });
        clear.addEventListener('click', resetRepetitionInsightsState);
        exportButton.addEventListener('click', exportRepetitionInsights);
        resetEffects.addEventListener('click', resetRepetitionEffectRecords);
        query.addEventListener('input', renderRepetitionInsightsResults);
        coverage.addEventListener('change', renderRepetitionInsightsResults);
        effectFilter.addEventListener('change', renderRepetitionInsightsResults);
        syncRepetitionInsightsControls();
    }

    function getMemoryAuxiliaryTrigger(name) {
        return document.getElementById('memory-' + name + '-trigger');
    }

    function getMemoryPanelFocusableElements(panel) {
        if (!panel) return [];
        return Array.from(panel.querySelectorAll(
            'button:not([disabled]), input:not([disabled]):not([type="hidden"]), '
            + 'select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])'
        )).filter(function (element) {
            if (element.hidden || element.getAttribute('aria-hidden') === 'true') return false;
            const style = window.getComputedStyle(element);
            return style.display !== 'none'
                && style.visibility !== 'hidden'
                && element.getClientRects().length > 0;
        });
    }

    function restoreMemoryAuxiliaryPanelFocus(element, showFocusRing) {
        if (!element || !document.contains(element) || element.disabled) return;
        element.classList.toggle('is-pointer-focus-restored', !showFocusRing);
        element.focus({ preventScroll: true });
        if (showFocusRing) return;

        const clearPointerFocusState = function () {
            element.classList.remove('is-pointer-focus-restored');
        };
        element.addEventListener('blur', clearPointerFocusState, { once: true });
        element.addEventListener('keydown', clearPointerFocusState, { once: true });
    }

    function closeMemoryAuxiliaryPanel(restoreFocus, showFocusRing) {
        if (!activeMemoryAuxiliaryPanel) return;
        if (activeMemoryAuxiliaryPanel === 'import' && window._memoryImportInProgress) return;

        const closingName = activeMemoryAuxiliaryPanel;
        const opener = memoryAuxiliaryPanelOpener;
        activeMemoryAuxiliaryPanel = '';
        memoryAuxiliaryPanelOpener = null;
        MEMORY_AUXILIARY_PANEL_NAMES.forEach(function (name) {
            const panel = getMemoryAuxiliaryPanel(name);
            const trigger = getMemoryAuxiliaryTrigger(name);
            if (panel) panel.hidden = true;
            if (trigger) trigger.setAttribute('aria-expanded', 'false');
        });
        const backdrop = document.getElementById('memory-aux-panel-backdrop');
        if (backdrop) backdrop.hidden = true;
        document.body.classList.remove('memory-aux-panel-open');

        if (restoreFocus && opener && document.contains(opener) && !opener.disabled) {
            restoreMemoryAuxiliaryPanelFocus(opener, showFocusRing);
        } else if (restoreFocus) {
            const fallback = getMemoryAuxiliaryTrigger(closingName);
            restoreMemoryAuxiliaryPanelFocus(fallback, showFocusRing);
        }
    }

    function openMemoryAuxiliaryPanel(name, opener) {
        const panel = getMemoryAuxiliaryPanel(name);
        const trigger = getMemoryAuxiliaryTrigger(name);
        if (!panel || !trigger || trigger.disabled) return;
        if (activeMemoryAuxiliaryPanel === 'import' && window._memoryImportInProgress && name !== 'import') return;

        setMemoryRoleHoverPreviewOpen(false);
        MEMORY_AUXILIARY_PANEL_NAMES.forEach(function (panelName) {
            const candidate = getMemoryAuxiliaryPanel(panelName);
            const candidateTrigger = getMemoryAuxiliaryTrigger(panelName);
            if (candidate) candidate.hidden = panelName !== name;
            if (candidateTrigger) {
                candidateTrigger.setAttribute('aria-expanded', panelName === name ? 'true' : 'false');
            }
        });
        const backdrop = document.getElementById('memory-aux-panel-backdrop');
        if (backdrop) backdrop.hidden = false;
        setMemoryRolePanelOpen(false, false);
        activeMemoryAuxiliaryPanel = name;
        memoryAuxiliaryPanelOpener = opener || trigger;
        document.body.classList.add('memory-aux-panel-open');
        if (name === 'insights') syncRepetitionInsightsControls();
        panel.focus({ preventScroll: true });
    }

    function createMemoryAuxiliaryFocusRingResolver(element) {
        let activatedByPointer = false;
        element.addEventListener('pointerdown', function () {
            activatedByPointer = true;
        });
        element.addEventListener('pointercancel', function () {
            activatedByPointer = false;
        });
        element.addEventListener('keydown', function () {
            activatedByPointer = false;
        });
        return function (event) {
            const showFocusRing = !activatedByPointer && event.detail === 0;
            activatedByPointer = false;
            return showFocusRing;
        };
    }

    function initMemoryAuxiliaryPanels() {
        MEMORY_AUXILIARY_PANEL_NAMES.forEach(function (name) {
            const trigger = getMemoryAuxiliaryTrigger(name);
            const panel = getMemoryAuxiliaryPanel(name);
            if (!trigger || !panel) return;
            const shouldShowTriggerFocusRing = createMemoryAuxiliaryFocusRingResolver(trigger);
            trigger.addEventListener('click', function (event) {
                if (activeMemoryAuxiliaryPanel === name) {
                    closeMemoryAuxiliaryPanel(true, shouldShowTriggerFocusRing(event));
                    return;
                }
                shouldShowTriggerFocusRing(event);
                openMemoryAuxiliaryPanel(name, trigger);
            });
            panel.querySelectorAll('[data-memory-panel-close]').forEach(function (button) {
                const shouldShowCloseFocusRing = createMemoryAuxiliaryFocusRingResolver(button);
                button.addEventListener('click', function (event) {
                    closeMemoryAuxiliaryPanel(true, shouldShowCloseFocusRing(event));
                });
            });
        });

        const backdrop = document.getElementById('memory-aux-panel-backdrop');
        if (backdrop) {
            backdrop.addEventListener('click', function () {
                closeMemoryAuxiliaryPanel(true, false);
            });
        }

        document.addEventListener('keydown', function (event) {
            if (!activeMemoryAuxiliaryPanel) return;
            const storageModal = document.getElementById('storage-location-modal');
            if (storageModal && !storageModal.hidden) return;
            if (event.key === 'Escape') {
                event.preventDefault();
                closeMemoryAuxiliaryPanel(true, true);
                return;
            }
            if (event.key !== 'Tab') return;
            const panel = getMemoryAuxiliaryPanel(activeMemoryAuxiliaryPanel);
            const focusable = getMemoryPanelFocusableElements(panel);
            if (!focusable.length) {
                event.preventDefault();
                return;
            }
            const first = focusable[0];
            const last = focusable[focusable.length - 1];
            if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first.focus();
            } else if (!panel.contains(document.activeElement)) {
                event.preventDefault();
                first.focus();
            }
        });
    }

    function displayPath(path) {
        const normalized = String(path || '').trim();
        return normalized || '-';
    }

    function parentPath(path) {
        const normalized = String(path || '').trim();
        if (!normalized) return '';
        const trimmed = normalized.replace(/[\\/]+$/, '');
        const separatorIndex = Math.max(trimmed.lastIndexOf('/'), trimmed.lastIndexOf('\\'));
        if (separatorIndex <= 0) return '';
        return trimmed.slice(0, separatorIndex);
    }

    function pathEndsWithAppFolder(path) {
        const normalized = String(path || '').trim().replace(/[\\/]+$/, '');
        if (!normalized) return false;
        const separatorIndex = Math.max(normalized.lastIndexOf('/'), normalized.lastIndexOf('\\'));
        const lastSegment = separatorIndex >= 0 ? normalized.slice(separatorIndex + 1) : normalized;
        return lastSegment === STORAGE_APP_FOLDER_NAME;
    }

    function normalizeStorageRootForDisplay(pathText) {
        const original = String(pathText || '').trim();
        if (original === '/') {
            return '/' + STORAGE_APP_FOLDER_NAME;
        }
        if (/^[A-Za-z]:\\$/.test(original)) {
            return original + STORAGE_APP_FOLDER_NAME;
        }
        const normalized = original.replace(/[\\/]+$/, '');
        if (!normalized || pathEndsWithAppFolder(original)) {
            return normalized;
        }
        const separator = normalized.lastIndexOf('\\') > normalized.lastIndexOf('/') ? '\\' : '/';
        return normalized + separator + STORAGE_APP_FOLDER_NAME;
    }

    function applyStorageTargetRootDisplay(pathText) {
        const normalized = normalizeStorageRootForDisplay(pathText);
        const input = document.getElementById('storage-target-root-input');
        if (input) {
            input.value = normalized;
        }
        return normalized;
    }

    function getStorageDirectoryPickerStartPath() {
        const input = document.getElementById('storage-target-root-input');
        const inputPath = input ? String(input.value || '').trim() : '';
        if (inputPath) return inputPath;

        const bootstrap = storageLocationState.bootstrap || {};
        const recommendedRoot = String(bootstrap.recommended_root || '').trim();
        const currentRoot = String(bootstrap.current_root || '').trim();
        if (recommendedRoot && recommendedRoot !== currentRoot) {
            return parentPath(recommendedRoot) || recommendedRoot;
        }
        return parentPath(currentRoot) || currentRoot;
    }

    async function readJsonResponse(resp) {
        try {
            return await resp.json();
        } catch (e) {
            return null;
        }
    }

    function storageErrorMessage(payload, fallback) {
        if (!payload || typeof payload !== 'object') {
            return fallback;
        }
        return String(
            payload.error
            || payload.blocking_error_message
            || payload.error_code
            || fallback
        );
    }

    function getStorageBlockingReason(bootstrapPayload) {
        if (!bootstrapPayload || typeof bootstrapPayload !== 'object') {
            return '';
        }
        const explicitReason = String(bootstrapPayload.blocking_reason || '').trim();
        if (explicitReason) {
            return explicitReason;
        }
        if (bootstrapPayload.selection_required) {
            return 'selection_required';
        }
        if (bootstrapPayload.migration_pending) {
            return 'migration_pending';
        }
        if (bootstrapPayload.recovery_required) {
            return 'recovery_required';
        }
        return '';
    }

    function describeStorageState(state) {
        if (!state || state.loadFailed) {
            return translate('memory.storageLoadFailed', '存储位置加载失败');
        }
        const blockingReason = state.blockingReason || '';
        if (!blockingReason) {
            return '';
        }
        const statusKey = STORAGE_BLOCKING_STATUS_KEYS[blockingReason] || 'memory.storageStatusBlocked';
        return translate(statusKey, '当前需要先处理存储位置状态');
    }

    function setReviewControlsEnabled(enabled) {
        const checkbox = document.getElementById('review-toggle-checkbox');
        const label = document.querySelector("label.auto-review-toggle-btn[for='review-toggle-checkbox']");
        if (checkbox) {
            checkbox.disabled = !enabled;
            if (!enabled) {
                checkbox.checked = false;
            }
        }
        if (label) {
            label.classList.toggle('is-disabled', !enabled);
        }
        if (!enabled) {
            updateToggleText(false);
        }
    }

    function setPowerfulMemoryControlsEnabled(enabled) {
        const checkbox = document.getElementById('strong-memory-toggle-checkbox');
        const label = document.querySelector("label.auto-review-toggle-btn[for='strong-memory-toggle-checkbox']");
        if (checkbox) {
            checkbox.disabled = !enabled;
            if (!enabled) checkbox.checked = false;
        }
        if (label) label.classList.toggle('is-disabled', !enabled);
        if (!enabled) updatePowerfulMemoryToggleText(false);
    }

    function renderStorageLocationPanel() {
        const state = storageLocationState || {};
        const bootstrap = state.bootstrap || {};
        setElementText('storage-current-root', state.loadFailed ? '-' : displayPath(bootstrap.current_root));
        setElementText('storage-location-status', describeStorageState(state));

        const manageBtn = document.getElementById('storage-location-manage-btn');
        if (manageBtn) {
            const blockingReason = String(state.blockingReason || '').trim();
            const blockingNonRecoverable = blockingReason && !RECOVERABLE_STORAGE_BLOCKING_REASONS.has(blockingReason);
            manageBtn.disabled = state.loadFailed || blockingNonRecoverable || !String(bootstrap.current_root || '').trim();
            manageBtn.title = manageBtn.disabled
                ? translate('memory.storageManagementUnavailable', '当前存储位置暂不可用')
                : '';
        }

        const openBtn = document.getElementById('storage-location-open-btn');
        if (openBtn) {
            openBtn.disabled = state.loadFailed || !String(bootstrap.current_root || '').trim();
        }
    }

    async function initStorageLocationPanel() {
        try {
            const resp = await fetch('/api/storage/location/bootstrap', {
                headers: { 'Cache-Control': 'no-cache' }
            });
            if (!resp.ok) {
                throw new Error('storage bootstrap failed: ' + resp.status);
            }
            const bootstrap = await resp.json();
            const blockingReason = getStorageBlockingReason(bootstrap);
            storageLocationState = {
                bootstrap,
                blockingReason,
                loadFailed: false,
                limited: !!blockingReason
            };
        } catch (e) {
            console.warn('[MemoryBrowser] storage location bootstrap failed:', e);
            storageLocationState = {
                bootstrap: null,
                blockingReason: 'bootstrap_failed',
                loadFailed: true,
                limited: true
            };
        }
        renderStorageLocationPanel();
        return storageLocationState;
    }

    function setStoragePreflightResult(message, type) {
        const resultEl = document.getElementById('storage-location-preflight-result');
        if (!resultEl) return;
        resultEl.textContent = message || '';
        resultEl.classList.toggle('is-error', type === 'error');
        resultEl.classList.toggle('is-success', type === 'success');
    }

    function renderStorageRestartButton() {
        const restartBtn = document.getElementById('storage-location-restart-btn');
        if (!restartBtn) return;
        const input = document.getElementById('storage-target-root-input');
        const restartAccepted = !!(input && input.disabled);
        restartBtn.hidden = restartAccepted;
        restartBtn.disabled = storagePreflightBusy || restartAccepted;
    }

    let selectedTutorialDay = 0;
    let selectedTutorialHomeAll = false;

    const TUTORIAL_CASCADER_PAGE_LABELS = {
        all: '全部页面',
        home: '主页',
        model_manager: '模型设置',
        parameter_editor: '捏脸系统',
        emotion_manager: '情感管理',
        chara_manager: '角色管理',
        settings: 'API设置',
        voice_clone: '语音克隆',
        memory_browser: '记忆浏览',
        current_personality: '当前角色性格'
    };

    function getTutorialPageLabel(pageKey) {
        const option = document.querySelector('#tutorial-reset-select option[value="' + pageKey + '"]');
        return option ? String(option.textContent || '').trim() : (TUTORIAL_CASCADER_PAGE_LABELS[pageKey] || pageKey);
    }

    function getTutorialDayLabel(day) {
        const fallback = '第 ' + day + ' 天';
        if (!window.t || typeof window.t !== 'function') {
            return fallback;
        }
        const translated = window.t('memory.tutorialHomeDayLabel', { day: day });
        return translated && translated !== 'memory.tutorialHomeDayLabel' ? translated : fallback;
    }

    function getTutorialHomeAllResetLabel() {
        const fallback = '全部重置';
        if (!window.t || typeof window.t !== 'function') {
            return fallback;
        }
        const translated = window.t('memory.tutorialHomeAllReset', fallback);
        return translated && translated !== 'memory.tutorialHomeAllReset' ? translated : fallback;
    }

    function getTutorialHomeAllResetSuccessMessage() {
        const fallback = '已重置主页 7 天新手教程，请重新加载 Neko 后从第 1 天开始。';
        if (!window.t || typeof window.t !== 'function') {
            return fallback;
        }
        const translated = window.t('memory.tutorialHomeAllResetSuccess', fallback);
        return translated && translated !== 'memory.tutorialHomeAllResetSuccess' ? translated : fallback;
    }

    function refreshTutorialCascaderDayLabels() {
        const tutorialCascader = document.getElementById('tutorial-reset-cascader');
        if (!tutorialCascader) return;
        tutorialCascader.querySelectorAll('.tutorial-cascader-option[data-tutorial-home-all]').forEach(function (option) {
            option.textContent = getTutorialHomeAllResetLabel();
        });
        tutorialCascader.querySelectorAll('.tutorial-cascader-option[data-tutorial-day]').forEach(function (option) {
            const day = Number(option.dataset.tutorialDay || 0);
            if (day > 0) {
                option.textContent = getTutorialDayLabel(day);
            }
        });
    }

    function resolveSelectedTutorialReset() {
        const tutorialSelect = document.getElementById('tutorial-reset-select');
        const pageKey = tutorialSelect ? String(tutorialSelect.value || '') : '';
        if (pageKey !== 'home') {
            return { type: pageKey ? 'page' : '', pageKey: pageKey };
        }
        if (selectedTutorialHomeAll) {
            return {
                type: 'home-all',
                pageKey: 'home'
            };
        }
        return {
            type: selectedTutorialDay ? 'home-day' : '',
            pageKey: 'home',
            day: selectedTutorialDay
        };
    }

    function setTutorialCascaderOpen(open) {
        const tutorialCascader = document.getElementById('tutorial-reset-cascader');
        const popup = tutorialCascader && tutorialCascader.querySelector(':scope > .tutorial-cascader-popup');
        const trigger = tutorialCascader && tutorialCascader.querySelector(':scope > .tutorial-cascader-trigger');
        if (popup) {
            popup.hidden = !open;
        }
        if (trigger) {
            trigger.setAttribute('aria-expanded', open ? 'true' : 'false');
            trigger.classList.toggle('is-open', !!open);
        }
    }

    function syncTutorialResetCascader() {
        const tutorialSelect = document.getElementById('tutorial-reset-select');
        const tutorialResetBtn = document.getElementById('tutorial-reset-btn');
        const tutorialCascader = document.getElementById('tutorial-reset-cascader');
        const dayColumn = tutorialCascader && tutorialCascader.querySelector('.tutorial-cascader-day-column');
        const valueEl = tutorialCascader && tutorialCascader.querySelector('.tutorial-reset-value');
        if (!tutorialSelect || !tutorialResetBtn || !tutorialCascader) return;

        const pageKey = String(tutorialSelect.value || '');
        if (pageKey !== 'home') {
            selectedTutorialDay = 0;
            selectedTutorialHomeAll = false;
        }
        if (dayColumn) {
            dayColumn.hidden = pageKey !== 'home';
        }
        tutorialCascader.querySelectorAll('.tutorial-cascader-option[data-tutorial-page]').forEach(function (option) {
            option.classList.toggle('is-selected', option.dataset.tutorialPage === pageKey);
        });
        tutorialCascader.querySelectorAll('.tutorial-cascader-option[data-tutorial-day]').forEach(function (option) {
            option.classList.toggle('is-selected', Number(option.dataset.tutorialDay) === selectedTutorialDay);
        });
        tutorialCascader.querySelectorAll('.tutorial-cascader-option[data-tutorial-home-all]').forEach(function (option) {
            option.classList.toggle('is-selected', selectedTutorialHomeAll);
        });
        if (valueEl) {
            if (!pageKey) {
                valueEl.textContent = getTutorialPageLabel('');
            } else if (pageKey === 'home' && selectedTutorialHomeAll) {
                valueEl.textContent = getTutorialPageLabel('home') + ' / ' + getTutorialHomeAllResetLabel();
            } else if (pageKey === 'home' && selectedTutorialDay) {
                valueEl.textContent = getTutorialPageLabel('home') + ' / ' + getTutorialDayLabel(selectedTutorialDay);
            } else {
                valueEl.textContent = getTutorialPageLabel(pageKey);
            }
        }

        const selection = resolveSelectedTutorialReset();
        tutorialResetBtn.disabled = selection.type !== 'page' && selection.type !== 'home-day' && selection.type !== 'home-all';
    }

    async function performSelectedTutorialReset() {
        const selection = resolveSelectedTutorialReset();
        if (selection.type === 'home-day') {
            if (window.AvatarFloatingGuideReset && typeof window.AvatarFloatingGuideReset.resetAvatarFloatingGuideDay === 'function') {
                await window.AvatarFloatingGuideReset.resetAvatarFloatingGuideDay(selection.day, {
                    source: 'memory_browser_reset_select',
                });
            } else if (window.AvatarFloatingGuideReset && typeof window.AvatarFloatingGuideReset.resetHomeTutorialDay === 'function') {
                await window.AvatarFloatingGuideReset.resetHomeTutorialDay(selection.day, {
                    source: 'memory_browser_reset_select',
                });
            } else if (typeof window.resetHomeTutorialDay === 'function') {
                await window.resetHomeTutorialDay(selection.day, {
                    source: 'memory_browser_reset_select',
                });
            }
            return;
        }
        if (selection.type === 'home-all') {
            if (window.AvatarFloatingGuideReset && typeof window.AvatarFloatingGuideReset.resetAllAvatarFloatingGuideDays === 'function') {
                await window.AvatarFloatingGuideReset.resetAllAvatarFloatingGuideDays({
                    source: 'memory_browser_reset_home_all',
                });
            } else if (typeof window.resetAllAvatarFloatingGuideDays === 'function') {
                await window.resetAllAvatarFloatingGuideDays({
                    source: 'memory_browser_reset_home_all',
                });
            }
            await showTutorialResetNotice(getTutorialHomeAllResetSuccessMessage());
            return;
        }
        if (selection.type === 'page' && typeof window.resetTutorialForPage === 'function') {
            if (selection.pageKey === 'all') {
                if (window.AvatarFloatingGuideReset && typeof window.AvatarFloatingGuideReset.resetAllAvatarFloatingGuideDays === 'function') {
                    await window.AvatarFloatingGuideReset.resetAllAvatarFloatingGuideDays({
                        source: 'memory_browser_reset_all',
                    });
                } else if (typeof window.resetAllAvatarFloatingGuideDays === 'function') {
                    await window.resetAllAvatarFloatingGuideDays({
                        source: 'memory_browser_reset_all',
                    });
                }
            }
            await window.resetTutorialForPage(selection.pageKey);
        }
    }

    async function resetSelectedTutorial() {
        try {
            return await performSelectedTutorialReset();
        } catch (error) {
            console.error('[MemoryBrowser] 新手教程重置失败:', error);
            await showTutorialResetNotice(
                translate('tutorial.reset.dayFailed', '新手教程重置失败，请稍后再试。'),
                { variant: 'error' }
            );
            return false;
        }
    }

    function sleep(ms) {
        return new Promise(function (resolve) {
            window.setTimeout(resolve, ms);
        });
    }

    function setStoragePreflightBusy(busy) {
        storagePreflightBusy = !!busy;
        const pickBtn = document.getElementById('storage-location-pick-btn');
        if (pickBtn) {
            pickBtn.disabled = !!busy;
        }
        renderStorageRestartButton();
    }

    function openStorageLocationManager() {
        const state = storageLocationState || {};
        const bootstrap = state.bootstrap || {};
        const blockingReason = String(state.blockingReason || '').trim();
        const blockingNonRecoverable = blockingReason && !RECOVERABLE_STORAGE_BLOCKING_REASONS.has(blockingReason);
        if (state.loadFailed || blockingNonRecoverable || !String(bootstrap.current_root || '').trim()) {
            setElementText('storage-location-status', translate('memory.storageManagementUnavailable', '当前存储位置暂不可用'));
            return;
        }

        const modal = document.getElementById('storage-location-modal');
        if (!modal) return;
        setElementText('storage-modal-current-root', displayPath(bootstrap.current_root));

        const input = document.getElementById('storage-target-root-input');
        if (input) {
            input.value = '';
            input.placeholder = translate('memory.storageTargetPlaceholder', '选择或输入新的数据位置');
        }
        storagePreflightState = null;
        setStoragePreflightBusy(false);
        setStoragePreflightResult('', '');
        renderStorageRestartButton();
        modal.hidden = false;
        document.body.classList.add('storage-location-memory-modal-open');
    }

    function closeStorageLocationManager() {
        const modal = document.getElementById('storage-location-modal');
        if (modal) {
            modal.hidden = true;
        }
        document.body.classList.remove('storage-location-memory-modal-open');
        const input = document.getElementById('storage-target-root-input');
        if (input) {
            input.disabled = false;
        }
    }

    async function pickStorageTargetDirectory() {
        const startPath = getStorageDirectoryPickerStartPath();
        setStoragePreflightBusy(true);
        try {
            let payload = null;
            const host = window.nekoHost;
            if (host && typeof host.pickDirectory === 'function') {
                try {
                    const result = await host.pickDirectory({
                        startPath,
                        title: translate('memory.storagePickTarget', '选择位置')
                    });
                    if (!result || typeof result !== 'object') {
                        console.warn('[MemoryBrowser] host directory picker returned invalid result, falling back to backend:', result);
                    } else {
                        payload = result;
                    }
                } catch (e) {
                    console.warn('[MemoryBrowser] host directory picker failed, falling back to backend:', e);
                }
            }
            if (!payload) {
                const resp = await fetch('/api/storage/location/pick-directory', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ start_path: startPath })
                });
                payload = await readJsonResponse(resp);
                if (!resp.ok || !payload || payload.ok !== true) {
                    throw new Error(storageErrorMessage(payload, translate('memory.storagePickTargetFailed', '选择目标位置失败，请手动输入路径')));
                }
            }
            if (payload.cancelled) {
                return;
            }
            const selectedRoot = String(payload.selected_root || '').trim();
            if (!selectedRoot) {
                throw new Error('empty selected_root');
            }
            applyStorageTargetRootDisplay(selectedRoot);
            storagePreflightState = null;
            setStoragePreflightResult('', '');
            renderStorageRestartButton();
        } catch (e) {
            console.warn('[MemoryBrowser] pick storage target failed:', e);
            setStoragePreflightResult(translate('memory.storagePickTargetFailed', '选择目标位置失败，请手动输入路径'), 'error');
        } finally {
            setStoragePreflightBusy(false);
        }
    }

    function formatPreflightResult(payload) {
        if (!payload || payload.ok !== true) {
            return translate('memory.storagePreflightFailed', '预检失败');
        }
        if (payload.blocking_error_code || payload.blocking_error_message) {
            return storageErrorMessage(payload, translate('memory.storagePreflightFailed', '预检失败'));
        }
        if (payload.result === 'restart_not_required') {
            return translate('memory.storageAlreadyCurrentRoot', '当前已在该位置');
        }

        const lines = [
            translate('memory.storagePreflightReady', '预检通过。更改存储位置后会重启，旧数据默认保留。'),
            translate('memory.storagePreflightTarget', '目标位置：{{path}}', {
                path: String(payload.target_root || payload.selected_root || '')
            })
        ];
        if (payload.requires_existing_target_confirmation) {
            lines.push(payload.existing_target_confirmation_message || translate('memory.storageExistingTargetWarning', '目标位置已经包含现有数据，后续确认迁移前需要二次确认。'));
        }
        return lines.filter(Boolean).join('\n');
    }

    async function runStorageLocationPreflight(options) {
        const keepBusy = !!(options && options.keepBusy);
        const input = document.getElementById('storage-target-root-input');
        let selectedRoot = input ? String(input.value || '').trim() : '';
        if (!selectedRoot) {
            setStoragePreflightResult(translate('memory.storageTargetRequired', '请先选择或输入目标位置'), 'error');
            return null;
        }
        selectedRoot = applyStorageTargetRootDisplay(selectedRoot);
        setStoragePreflightBusy(true);
        setStoragePreflightResult(translate('memory.storagePreflightRunning', '正在预检...'), 'success');
        try {
            const resp = await fetch('/api/storage/location/preflight', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    selected_root: selectedRoot,
                    selection_source: 'custom'
                })
            });
            const payload = await readJsonResponse(resp);
            if (!resp.ok || !payload || payload.ok !== true) {
                throw new Error(storageErrorMessage(payload, translate('memory.storagePreflightFailed', '预检失败')));
            }
            storagePreflightState = payload;
            const isBlocked = !!(payload.blocking_error_code || payload.blocking_error_message);
            setStoragePreflightResult(formatPreflightResult(payload), isBlocked ? 'error' : 'success');
            renderStorageRestartButton();
            return payload;
        } catch (e) {
            console.warn('[MemoryBrowser] storage location preflight failed:', e);
            storagePreflightState = null;
            setStoragePreflightResult(String(e && e.message ? e.message : translate('memory.storagePreflightFailed', '预检失败')), 'error');
            renderStorageRestartButton();
            return null;
        } finally {
            if (!keepBusy) {
                setStoragePreflightBusy(false);
            }
        }
    }

    async function restartWithStorageLocation(options) {
        const keepBusy = !!(options && options.keepBusy);
        if (!storagePreflightState || storagePreflightState.result !== 'restart_required') {
            setStoragePreflightResult(translate('memory.storagePreflightRequired', '请先完成预检'), 'error');
            renderStorageRestartButton();
            return false;
        }
        const selectedRoot = String(storagePreflightState.selected_root || storagePreflightState.target_root || '').trim();
        if (!selectedRoot) {
            setStoragePreflightResult(translate('memory.storagePreflightFailed', '预检失败'), 'error');
            return false;
        }

        let confirmExistingTargetContent = false;
        if (storagePreflightState.requires_existing_target_confirmation) {
            const message = storagePreflightState.existing_target_confirmation_message
                || translate('memory.storageExistingTargetWarning', '目标位置已经包含现有数据，后续确认迁移前需要二次确认。');
            if (!window.confirm(message)) {
                return false;
            }
            confirmExistingTargetContent = true;
        }

        const restartBtn = document.getElementById('storage-location-restart-btn');
        if (restartBtn) {
            restartBtn.disabled = true;
        }
        let restartAccepted = false;
        setStoragePreflightBusy(true);
        setStoragePreflightResult(translate('memory.storageRestartStarting', '正在准备重启...'), 'success');
        try {
            const resp = await fetch('/api/storage/location/restart', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    selected_root: selectedRoot,
                    selection_source: storagePreflightState.selection_source || 'custom',
                    confirm_existing_target_content: confirmExistingTargetContent
                })
            });
            const payload = await readJsonResponse(resp);
            if (!resp.ok || !payload || payload.ok !== true) {
                throw new Error(storageErrorMessage(payload, translate('memory.storageRestartFailed', '重启请求失败')));
            }
            restartAccepted = true;
            setStoragePreflightResult(translate('memory.storageRestartInitiated', '已请求重启。应用即将进入维护状态，请等待重启完成。'), 'success');
            notifyStorageRestartInitiated(payload, selectedRoot);
            storagePreflightState = null;
            const input = document.getElementById('storage-target-root-input');
            if (input) {
                input.disabled = true;
            }
            renderStorageRestartButton();
            await closeStorageManagerAfterRestartNotice(payload);
            return true;
        } catch (e) {
            console.warn('[MemoryBrowser] storage location restart failed:', e);
            setStoragePreflightResult(String(e && e.message ? e.message : translate('memory.storageRestartFailed', '重启请求失败')), 'error');
            renderStorageRestartButton();
            return false;
        } finally {
            if (!restartAccepted && !keepBusy) {
                setStoragePreflightBusy(false);
            }
        }
    }

    async function preflightAndRestartWithStorageLocation() {
        const payload = await runStorageLocationPreflight({ keepBusy: true });
        if (
            !payload
            || payload.result !== 'restart_required'
            || payload.blocking_error_code
            || payload.blocking_error_message
        ) {
            setStoragePreflightBusy(false);
            return;
        }

        const restartAccepted = await restartWithStorageLocation({ keepBusy: true });
        if (!restartAccepted) {
            setStoragePreflightBusy(false);
        }
    }

    function buildStorageRestartMessage(payload, selectedRoot) {
        const normalizedPayload = payload && typeof payload === 'object' ? payload : {};
        return {
            type: STORAGE_RESTART_MESSAGE_TYPE,
            sender_id: STORAGE_RESTART_SENDER_ID,
            payload: Object.assign({}, normalizedPayload, {
                selected_root: String(normalizedPayload.selected_root || selectedRoot || '').trim(),
                target_root: String(normalizedPayload.target_root || normalizedPayload.selected_root || selectedRoot || '').trim()
            })
        };
    }

    function notifyStorageRestartInitiated(payload, selectedRoot) {
        const message = buildStorageRestartMessage(payload, selectedRoot);
        try {
            if (typeof BroadcastChannel !== 'undefined') {
                const channel = new BroadcastChannel(STORAGE_RESTART_CHANNEL);
                channel.postMessage(message);
                channel.close();
            }
        } catch (e) {
            console.warn('[MemoryBrowser] storage restart broadcast failed:', e);
        }

        try {
            if (window.opener && !window.opener.closed) {
                window.opener.postMessage(message, PARENT_ORIGIN);
            }
        } catch (e) {
            console.warn('[MemoryBrowser] storage restart opener notification failed:', e);
        }

        try {
            if (window.parent && window.parent !== window) {
                window.parent.postMessage(message, PARENT_ORIGIN);
            }
        } catch (e) {
            console.warn('[MemoryBrowser] storage restart parent notification failed:', e);
        }
    }

    async function closeStorageManagerAfterRestartNotice(payload) {
        await sleep(250);
        const host = window.nekoHost;
        if (host && typeof host.closeWindow === 'function') {
            try {
                const result = await host.closeWindow();
                if (!result || result.ok !== false) {
                    return;
                }
            } catch (e) {
                console.warn('[MemoryBrowser] host closeWindow failed after storage restart:', e);
            }
        }

        const hasExternalOwner = !!(
            (window.opener && !window.opener.closed)
            || (window.parent && window.parent !== window)
        );
        if (hasExternalOwner) {
            try {
                window.close();
                await sleep(150);
                if (window.closed) {
                    return;
                }
            } catch (_) {}
        }
        document.body.classList.remove('storage-location-memory-modal-open');
        await showStandaloneStorageMaintenanceOverlay(payload);
    }

    function ensureStylesheet(href) {
        if (document.querySelector('link[href="' + href + '"]')) {
            return;
        }
        const link = document.createElement('link');
        link.rel = 'stylesheet';
        link.href = href;
        document.head.appendChild(link);
    }

    function loadScriptOnce(src, configureScript) {
        return new Promise(function (resolve, reject) {
            const existing = document.querySelector('script[src="' + src + '"]');
            if (existing) {
                resolve();
                return;
            }
            const script = document.createElement('script');
            script.src = src;
            if (typeof configureScript === 'function') {
                configureScript(script);
            }
            script.onload = function () { resolve(); };
            script.onerror = function () { reject(new Error('failed to load ' + src)); };
            document.body.appendChild(script);
        });
    }

    async function showStandaloneStorageMaintenanceOverlay(payload) {
        try {
            ensureStylesheet('/static/css/storage-location.css');
            await loadScriptOnce('/static/app/app-storage-location.js', function (script) {
                script.setAttribute('data-storage-location-auto-start', 'false');
            });
            if (
                window.appStorageLocation
                && typeof window.appStorageLocation.enterExternalMaintenanceMode === 'function'
            ) {
                window.appStorageLocation.enterExternalMaintenanceMode(payload || {});
            }
        } catch (e) {
            console.warn('[MemoryBrowser] standalone storage maintenance overlay failed:', e);
        }
    }

    function renderMemoryBrowserLimitedState(state) {
        memoryStorageLimited = true;
        currentMemoryFile = null;
        currentMemoryIdentityToken = null;
        currentCatName = '';
        repetitionInsightsCharacterOverride = '';
        chatData = [];
        memoryFileRequestId++;
        resetRepetitionInsightsState();

        const list = document.getElementById('memory-file-list');
        if (list) {
            list.innerHTML = '';
            const item = document.createElement('li');
            item.style.cssText = 'color:#40C5F1; padding: 8px; line-height: 1.5;';
            item.textContent = describeStorageState(state);
            list.appendChild(item);
        }

        const editDiv = document.getElementById('memory-chat-edit');
        if (editDiv) {
            editDiv.textContent = '';
            const placeholder = document.createElement('div');
            placeholder.className = 'memory-limited-state';
            placeholder.textContent = translate(
                'memory.storageMemoryLimitedState',
                '当前存储位置还未就绪。请先完成存储位置选择、恢复或等待迁移完成，然后再查看记忆。'
            );
            editDiv.appendChild(placeholder);
        }

        const saveRow = document.getElementById('save-row');
        if (saveRow) {
            saveRow.style.display = 'none';
        }
        setMemoryCurrentRoleName('');
        setReviewControlsEnabled(false);
        setPowerfulMemoryControlsEnabled(false);
        updateExternalImportButton();
    }

    async function openCurrentStorageRoot() {
        const currentRoot = String(storageLocationState.bootstrap && storageLocationState.bootstrap.current_root || '').trim();
        if (!currentRoot) {
            setElementText('storage-location-status', translate('memory.storageManagementUnavailable', '当前存储位置暂不可用'));
            return;
        }
        const openBtn = document.getElementById('storage-location-open-btn');
        if (openBtn) {
            openBtn.disabled = true;
        }
        try {
            const host = window.nekoHost;
            if (host && typeof host.openPath === 'function') {
                try {
                    const result = await host.openPath({ path: currentRoot });
                    if (result && result.ok === false) {
                        throw new Error(result.error || 'openPath failed');
                    }
                    setElementText('storage-location-status', '');
                    return;
                } catch (hostError) {
                    console.warn('[MemoryBrowser] host openPath failed, falling back to backend:', hostError);
                }
            }
            const resp = await fetch('/api/storage/location/open-current', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });
            const payload = await readJsonResponse(resp);
            if (resp.ok && payload && payload.ok === true) {
                setElementText('storage-location-status', '');
                return;
            }
            if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
                await navigator.clipboard.writeText(currentRoot);
                setElementText('storage-location-status', translate('memory.storagePathCopied', '已复制当前目录路径'));
                return;
            }
            setElementText('storage-location-status', translate('memory.storageOpenPathUnavailable', '当前环境无法直接打开目录，请手动复制路径'));
        } catch (e) {
            console.warn('[MemoryBrowser] open current storage root failed:', e);
            setElementText('storage-location-status', translate('memory.storageOpenPathFailed', '打开当前目录失败'));
        } finally {
            if (openBtn) {
                openBtn.disabled = storageLocationState.loadFailed || !currentRoot;
            }
        }
    }

    /** Normalize message body from recent_*.json (string or OpenAI-style content blocks). */
    function extractDataContent(data) {
        if (!data || data.content === undefined || data.content === null) {
            return '';
        }
        const c = data.content;
        if (typeof c === 'string') {
            return c;
        }
        if (Array.isArray(c)) {
            const parts = [];
            for (let i = 0; i < c.length; i++) {
                const block = c[i];
                if (block && typeof block === 'object' && block.type === 'text' && block.text != null) {
                    parts.push(String(block.text));
                } else if (typeof block === 'string') {
                    parts.push(block);
                }
            }
            return parts.join('\n');
        }
        return String(c);
    }

    function setMemoryRowDeleteButtonsEnabled(enabled) {
        document.querySelectorAll('#memory-chat-edit .delete-btn').forEach(btn => {
            btn.disabled = !enabled;
        });
    }

    function teardownMemoryRowExit() {
        const editor = document.getElementById('memory-chat-edit');
        const hadPendingExit = memoryRowExitInProgress || Boolean(
            editor && editor.querySelector('.chat-item.is-exit-ready, .chat-item.is-reflowing')
        );
        memoryRowExitOperationId += 1;
        if (memoryRowExitTimer) {
            window.clearTimeout(memoryRowExitTimer);
            memoryRowExitTimer = 0;
        }
        memoryRowAnimations.forEach(animation => animation.cancel());
        memoryRowAnimations.clear();
        memoryRowExitInProgress = false;
        if (hadPendingExit && editor) {
            // pagehide may enter the back-forward cache instead of destroying the page.
            // Re-render from the already-updated chatData so cancelled animations cannot
            // leave restored rows invisible or permanently pointer-blocked.
            renderChatEdit();
        }
        setMemoryRowDeleteButtonsEnabled(true);
    }

    function getMemoryChatRowKey(message) {
        if (!message || typeof message !== 'object') return '';
        if (!memoryChatRowKeys.has(message)) {
            memoryChatRowKeySequence += 1;
            memoryChatRowKeys.set(message, `memory-row-${memoryChatRowKeySequence}`);
        }
        return memoryChatRowKeys.get(message);
    }

    function trackMemoryRowAnimation(animation) {
        memoryRowAnimations.add(animation);
        animation.finished.then(
            () => memoryRowAnimations.delete(animation),
            () => memoryRowAnimations.delete(animation)
        );
        return animation;
    }

    function captureMemoryRowPositions(excludedItems) {
        const excluded = new Set(excludedItems || []);
        const positions = new Map();
        document.querySelectorAll('#memory-chat-edit .chat-item').forEach(item => {
            if (excluded.has(item)) return;
            const key = item.getAttribute('data-chat-row-key');
            if (key) positions.set(key, item.getBoundingClientRect().top);
        });
        return positions;
    }

    function animateMemoryRowReflow(previousPositions) {
        if (!previousPositions || !previousPositions.size || !Element.prototype.animate) return [];
        const animations = [];
        document.querySelectorAll('#memory-chat-edit .chat-item').forEach(item => {
            const key = item.getAttribute('data-chat-row-key');
            if (!key || !previousPositions.has(key)) return;
            const deltaY = previousPositions.get(key) - item.getBoundingClientRect().top;
            if (Math.abs(deltaY) < 0.5) return;
            item.classList.add('is-reflowing');
            const animation = trackMemoryRowAnimation(item.animate([
                { transform: `translateY(${deltaY}px)` },
                { transform: 'translateY(0)' }
            ], {
                duration: MEMORY_ROW_REFLOW_MS,
                easing: 'cubic-bezier(0.2, 0, 0, 1)'
            }));
            animation.finished.then(
                () => item.classList.remove('is-reflowing'),
                () => item.classList.remove('is-reflowing')
            );
            animations.push(animation);
        });
        return animations;
    }

    function exitChatItems(items, onComplete, options) {
        const targets = (items || []).filter(Boolean);
        const config = options && typeof options === 'object' ? options : {};
        if (!targets.length) {
            if (typeof onComplete === 'function') onComplete();
            return;
        }

        const reduceMotion = window.matchMedia
            && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        if (reduceMotion) {
            if (typeof onComplete === 'function') onComplete();
            return;
        }

        memoryRowExitInProgress = true;
        setMemoryRowDeleteButtonsEnabled(false);
        const operationId = ++memoryRowExitOperationId;
        const previousPositions = captureMemoryRowPositions(targets);
        const batchExit = config.batch === true || targets.length > 1;
        const batchStaggerSpan = batchExit
            ? Math.min(
                (targets.length - 1) * MEMORY_ROW_EXIT_STAGGER_MS,
                MEMORY_ROW_EXIT_MAX_STAGGER_MS
            )
            : 0;
        let reflowStarted = false;
        let finished = false;

        const finish = () => {
            if (finished || operationId !== memoryRowExitOperationId) return;
            finished = true;
            if (memoryRowExitTimer) {
                window.clearTimeout(memoryRowExitTimer);
            }
            memoryRowExitTimer = 0;
            memoryRowExitInProgress = false;
            setMemoryRowDeleteButtonsEnabled(true);
        };

        const beginReflow = () => {
            if (reflowStarted || operationId !== memoryRowExitOperationId) return;
            reflowStarted = true;
            if (memoryRowExitTimer) window.clearTimeout(memoryRowExitTimer);
            memoryRowExitTimer = 0;
            if (typeof onComplete === 'function') onComplete();
            setMemoryRowDeleteButtonsEnabled(false);
            const reflowAnimations = animateMemoryRowReflow(previousPositions);
            if (!reflowAnimations.length) {
                finish();
                return;
            }
            Promise.allSettled(reflowAnimations.map(animation => animation.finished)).then(finish);
            memoryRowExitTimer = window.setTimeout(finish, MEMORY_ROW_REFLOW_MS + 80);
        };

        if (!Element.prototype.animate) {
            beginReflow();
            return;
        }

        const exitAnimations = targets.map((item, index) => {
            const delay = batchExit
                ? (index / Math.max(1, targets.length - 1)) * batchStaggerSpan
                : 0;
            const keyframes = batchExit
                ? [
                    { opacity: 1, transform: 'translateY(0)' },
                    { offset: 0.55, opacity: 0.48, transform: 'translateY(-4px)' },
                    { opacity: 0, transform: 'translateY(-10px)' }
                ]
                : [
                    { opacity: 1, transform: 'translateX(0) scale(1)' },
                    { offset: 0.55, opacity: 0.42, transform: 'translateX(4px) scale(0.995)' },
                    { opacity: 0, transform: 'translateX(10px) scale(0.985)' }
                ];
            item.classList.add('is-exit-ready', 'is-leaving');
            return trackMemoryRowAnimation(item.animate(keyframes, {
                duration: MEMORY_ROW_EXIT_MS,
                delay,
                easing: 'cubic-bezier(0.4, 0, 1, 1)',
                fill: 'forwards'
            }));
        });
        Promise.allSettled(exitAnimations.map(animation => animation.finished)).then(beginReflow);
        memoryRowExitTimer = window.setTimeout(
            beginReflow,
            MEMORY_ROW_EXIT_FALLBACK_MS + batchStaggerSpan
        );
    }

    function setRoleSelected(item, selected) {
        if (!item) return;
        item.classList.toggle('selected', selected);
        const button = item.querySelector('.cat-btn');
        if (!button) return;
        button.setAttribute('aria-current', selected ? 'true' : 'false');
    }

    const MEMORY_ROLE_ORIGIN_KEYS = {
        self: 'character.cardOriginSelf',
        imported: 'character.cardOriginImported',
        steam: 'character.cardOriginSteam'
    };

    function syncMemoryRoleNameOverflow(button) {
        const viewport = button?.querySelector('.memory-role-name-viewport');
        const roleName = viewport?.querySelector('.memory-role-name');
        if (!viewport || !roleName) return;

        const overflow = Math.max(0, roleName.scrollWidth - viewport.clientWidth);
        const isOverflowing = overflow > 1;
        button.classList.toggle('is-name-overflowing', isOverflowing);
        roleName.style.setProperty('--memory-role-name-shift', `${-overflow}px`);
        roleName.style.setProperty(
            '--memory-role-name-duration',
            `${Math.max(1800, Math.min(7000, Math.round(overflow / 32 * 1000)))}ms`
        );
    }

    function getMemoryRoleOriginLabel(origin) {
        const key = MEMORY_ROLE_ORIGIN_KEYS[origin];
        if (!key || !window.t) return origin;
        return window.t(key);
    }

    function getMemoryRoleSourceText(origin) {
        const sourceLabel = getMemoryRoleOriginLabel(origin);
        return window.t
            ? window.t('memory.roleSourceTooltip', { source: sourceLabel })
            : `Source: ${sourceLabel}`;
    }

    function createMemoryRoleSourceIcon(origin) {
        const svgNamespace = 'http://www.w3.org/2000/svg';
        const svg = document.createElementNS(svgNamespace, 'svg');
        svg.classList.add('memory-role-source-icon');
        svg.classList.add(`is-${origin}`);
        svg.setAttribute('viewBox', '0 0 24 24');
        svg.setAttribute('aria-hidden', 'true');

        const elements = {
            self: [
                ['path', { d: 'M5 5h14l2 6v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-7l2-6Z' }],
                ['path', { d: 'M3 11h18' }],
                ['circle', { cx: '17', cy: '15.5', r: '1' }]
            ],
            imported: [
                ['path', { d: 'M12 3v12' }],
                ['path', { d: 'm7 10 5 5 5-5' }],
                ['path', { d: 'M5 18v2h14v-2' }]
            ],
            // Steam glyph from Simple Icons (CC0-1.0):
            // https://github.com/simple-icons/simple-icons/blob/develop/icons/steam.svg
            steam: [
                ['path', {
                    fill: 'currentColor',
                    stroke: 'none',
                    d: 'M11.979 0C5.678 0 .511 4.86.022 11.037l6.432 2.658c.545-.371 1.203-.59 1.912-.59.063 0 .125.004.188.006l2.861-4.142V8.91c0-2.495 2.028-4.524 4.524-4.524 2.494 0 4.524 2.031 4.524 4.527s-2.03 4.525-4.524 4.525h-.105l-4.076 2.911c0 .052.004.105.004.159 0 1.875-1.515 3.396-3.39 3.396-1.635 0-3.016-1.173-3.331-2.727L.436 15.27C1.862 20.307 6.486 24 11.979 24c6.627 0 11.999-5.373 11.999-12S18.605 0 11.979 0zM7.54 18.21l-1.473-.61c.262.543.714.999 1.314 1.25 1.297.539 2.793-.076 3.332-1.375.263-.63.264-1.319.005-1.949s-.75-1.121-1.377-1.383c-.624-.26-1.29-.249-1.878-.03l1.523.63c.956.4 1.409 1.5 1.009 2.455-.397.957-1.497 1.41-2.454 1.012H7.54zm11.415-9.303c0-1.662-1.353-3.015-3.015-3.015-1.665 0-3.015 1.353-3.015 3.015 0 1.665 1.35 3.015 3.015 3.015 1.663 0 3.015-1.35 3.015-3.015zm-5.273-.005c0-1.252 1.013-2.266 2.265-2.266 1.249 0 2.266 1.014 2.266 2.266 0 1.251-1.017 2.265-2.266 2.265-1.253 0-2.265-1.014-2.265-2.265z'
                }]
            ]
        };

        (elements[origin] || []).forEach(([tagName, attributes]) => {
            const child = document.createElementNS(svgNamespace, tagName);
            Object.entries(attributes).forEach(([name, value]) => {
                child.setAttribute(name, value);
            });
            svg.appendChild(child);
        });
        return svg;
    }

    function syncMemoryRoleSourceCopy(button) {
        if (!button) return;
        const origin = button.dataset.memoryRoleOrigin;
        if (!MEMORY_ROLE_ORIGIN_KEYS[origin]) return;

        const catName = button.dataset.catname || '';
        const sourceText = getMemoryRoleSourceText(origin);
        const tooltip = button.querySelector('.memory-role-source-tooltip');
        if (tooltip) tooltip.textContent = sourceText;
        button.setAttribute('aria-label', `${catName}. ${sourceText}`);
    }

    function syncMemoryRoleSourceCopies() {
        document.querySelectorAll(
            '#memory-file-list .cat-btn[data-memory-role-origin]'
        ).forEach(syncMemoryRoleSourceCopy);
    }

    function applyMemoryRoleSources(cardMetas, requestId) {
        if (requestId !== memoryRoleListRequestId) return;
        document.querySelectorAll('#memory-file-list .cat-btn[data-catname]').forEach(
            function (button, index) {
                const catName = button.dataset.catname || '';
                const origin = cardMetas?.[catName]?.origin;
                if (!MEMORY_ROLE_ORIGIN_KEYS[origin]) return;

                button.dataset.memoryRoleOrigin = origin;
                const source = document.createElement('span');
                source.className = `memory-role-source is-${origin}`;
                source.setAttribute('aria-hidden', 'true');
                const sourceIcon = createMemoryRoleSourceIcon(origin);
                const sourceTooltip = document.createElement('span');
                sourceTooltip.id = `memory-role-source-tooltip-${index}`;
                sourceTooltip.className = 'memory-role-source-tooltip';
                sourceTooltip.setAttribute('role', 'tooltip');
                source.append(sourceIcon, sourceTooltip);
                button.appendChild(source);
                syncMemoryRoleSourceCopy(button);
                syncMemoryRoleNameOverflow(button);
            }
        );
    }

    async function loadMemoryFileList() {
        const roleListRequestId = ++memoryRoleListRequestId;
        const ul = document.getElementById('memory-file-list');
        ul.innerHTML = `<li style="color:#888; padding: 8px;">${window.t ? window.t('memory.loading') : '加载中...'}</li>`;
        try {
            const resp = await fetch('/api/memory/recent_files');
            const data = await resp.json();
            ul.innerHTML = '';
            if (data.files && data.files.length) {
                const cardMetasPromise = fetch('/api/characters/card-metas')
                    .then(function (response) {
                        if (!response.ok) return {};
                        return response.json();
                    })
                    .then(function (metasData) {
                        return metasData?.metas || {};
                    })
                    .catch(function (error) {
                        console.error('Failed to load character origins:', error);
                        return {};
                    });

                // 获取当前猫娘名称
                let currentCatgirl = null;
                try {
                    const catgirlResp = await fetch('/api/characters/current_catgirl');
                    const catgirlData = await catgirlResp.json();
                    currentCatgirl = catgirlData.current_catgirl || null;
                } catch (e) {
                    console.error('获取当前猫娘失败:', e);
                }

                let foundCurrentCatgirl = false;
                data.files.forEach((f) => {
                    // 提取猫娘名
                    let match = f.match(/^recent_(.+)\.json$/);
                    let catName = match ? match[1] : f;
                    const li = document.createElement('li');
                    // 按钮样式（使用 DOM API，避免插入未转义内容）
                    const btn = document.createElement('button');
                    btn.className = 'cat-btn';
                    btn.setAttribute('data-filename', f);
                    btn.setAttribute('data-catname', catName);
                    btn.setAttribute('aria-current', 'false');
                    const roleNameViewport = document.createElement('span');
                    roleNameViewport.className = 'memory-role-name-viewport';
                    const roleName = document.createElement('span');
                    roleName.className = 'memory-role-name';
                    roleName.textContent = catName;
                    roleName.title = catName;
                    roleNameViewport.appendChild(roleName);
                    btn.appendChild(roleNameViewport);

                    const syncNameOverflow = () => syncMemoryRoleNameOverflow(btn);
                    btn.addEventListener('pointerenter', syncNameOverflow);
                    btn.addEventListener('focus', syncNameOverflow);
                    btn.addEventListener('click', () => requestMemoryFileSelection(f, li, catName));
                    li.appendChild(btn);
                    ul.appendChild(li);

                    // 如果是当前猫娘，自动选择
                    if (currentCatgirl && catName === currentCatgirl && !foundCurrentCatgirl) {
                        foundCurrentCatgirl = true;
                        // 延迟一下确保DOM已渲染
                        setTimeout(() => {
                            // 如果用户已经手动选中了其他 recent 文件，就不要再用自动选择覆盖它。
                            if (currentMemoryFile) {
                                return;
                            }
                            // 编辑器照常打开当前角色的文件（否则这里会留一个空白
                            // 编辑区），但不夺走洞察面板里用户显式选中的无文件角色：
                            // 那不设置 currentMemoryFile，此前会被这个定时器连同
                            // 已经跑完的分析结果一起清掉，且不需要任何用户操作。
                            requestMemoryFileSelection(f, li, catName, {
                                keepInsightsTarget: true,
                            });
                        }, 100);
                    }
                });
                void cardMetasPromise.then(function (cardMetas) {
                    applyMemoryRoleSources(cardMetas, roleListRequestId);
                });
            } else {
                ul.innerHTML = `<li style="color:#888; padding: 8px;">${window.t ? window.t('memory.noFiles') : '无文件'}</li>`;
            }
        } catch (e) {
            ul.innerHTML = `<li style="color:#e74c3c; padding: 8px;">${window.t ? window.t('memory.loadFailed') : '加载失败'}</li>`;
        } finally {
            syncRepetitionInsightsCharacterSelect();
        }
    }

    function setExternalImportStatus(message, kind) {
        const status = document.getElementById('external-memory-import-status');
        if (!status) return;
        status.textContent = message || '';
        status.className = 'external-memory-import-status' + (kind ? ' is-' + kind : '');
    }

    function setExternalMemoryFormatOpen(open) {
        const cascader = document.getElementById('external-memory-format-cascader');
        if (!cascader) return;
        const popup = cascader.querySelector('.external-memory-format-popup');
        const trigger = cascader.querySelector('.external-memory-format-trigger');
        if (popup) popup.hidden = !open;
        if (trigger) {
            trigger.setAttribute('aria-expanded', open ? 'true' : 'false');
            trigger.classList.toggle('is-open', !!open);
        }
    }

    function syncExternalMemoryFormatDropdown() {
        const select = document.getElementById('external-memory-format');
        const cascader = document.getElementById('external-memory-format-cascader');
        if (!select || !cascader) return;
        const selectedValue = String(select.value || 'auto');
        const valueEl = cascader.querySelector('.external-memory-format-value');
        if (valueEl) {
            valueEl.textContent = selectedValue === 'auto'
                ? translate('memory.externalImportAuto', 'Auto detect')
                : (selectedValue === 'openclaw' ? 'OpenClaw' : 'Hermes');
        }
        cascader.querySelectorAll('[data-external-memory-format]').forEach(function (option) {
            const selected = option.dataset.externalMemoryFormat === selectedValue;
            option.classList.toggle('is-selected', selected);
            option.setAttribute('aria-selected', selected ? 'true' : 'false');
        });
    }

    function updateExternalImportButton() {
        const input = document.getElementById('external-memory-files');
        const button = document.getElementById('external-memory-import-btn');
        const panelTrigger = document.getElementById('memory-import-trigger');
        if (panelTrigger) {
            panelTrigger.disabled = !currentCatName;
        }
        if (button) {
            // 导入进行中一律保持禁用——否则切角色 / 换文件会重新启用按钮，放行第二
            // 次导入去撞后端正在跑的 fold/CAS（Codex P2）。
            button.disabled = !!window._memoryImportInProgress
                || !(currentCatName && input && input.files && input.files.length);
        }
        setElementText(
            'external-memory-target',
            currentCatName
                ? translate('memory.externalImportTarget', 'Target character: {{name}}', { name: currentCatName })
                : translate('memory.externalImportSelectCharacter', 'Select a target character first.')
        );
    }

    function bytesToBase64(buffer) {
        const bytes = new Uint8Array(buffer);
        const chunkSize = 0x8000;
        let binary = '';
        for (let offset = 0; offset < bytes.length; offset += chunkSize) {
            binary += String.fromCharCode.apply(null, bytes.subarray(offset, offset + chunkSize));
        }
        return btoa(binary);
    }

    function getExternalImportRenderLanguage() {
        const language = (window.i18next && window.i18next.language)
            || (window.i18n && window.i18n.language)
            || '';
        return typeof language === 'string' ? language.trim() : '';
    }

    async function buildExternalImportPayload(targetCharacter) {
        const input = document.getElementById('external-memory-files');
        const format = document.getElementById('external-memory-format');
        const selected = Array.from((input && input.files) || []);
        if (!targetCharacter) {
            throw new Error(translate('memory.externalImportSelectCharacter', 'Select a target character first.'));
        }
        if (!selected.length) {
            throw new Error(translate('memory.externalImportNoSelection', 'No files selected.'));
        }
        const zipFiles = selected.filter(file => /\.zip$/i.test(file.name));
        if (zipFiles.length) {
            if (selected.length !== 1) {
                throw new Error(translate('memory.externalImportZipOnly', 'Choose one ZIP archive, or one or more Markdown files.'));
            }
            if (zipFiles[0].size > 8 * 1024 * 1024) {
                throw new Error(translate('memory.externalImportTooLarge', 'The selected archive is too large.'));
            }
            const payload = {
                character_name: targetCharacter,
                source_format: format ? format.value : 'auto',
                archive_b64: bytesToBase64(await zipFiles[0].arrayBuffer())
            };
            return payload;
        }
        const files = [];
        let total = 0;
        for (const file of selected) {
            if (!/\.md$/i.test(file.name)) {
                throw new Error(translate('memory.externalImportUnsupported', 'Only Markdown and ZIP files are supported.'));
            }
            total += file.size;
            if (file.size > 2 * 1024 * 1024 || total > 8 * 1024 * 1024) {
                throw new Error(translate('memory.externalImportTooLarge', 'The selected files are too large.'));
            }
            files.push({
                path: file.webkitRelativePath || file.name,
                content: await file.text()
            });
        }
        const payload = {
            character_name: targetCharacter,
            source_format: format ? format.value : 'auto',
            files: files
        };
        return payload;
    }

    function broadcastExternalMemoryEdited(characterName) {
        if (typeof BroadcastChannel !== 'undefined') {
            let channel = null;
            try {
                channel = new BroadcastChannel('neko_page_channel');
                channel.postMessage({ action: 'memory_edited', catgirl_name: characterName });
                return;
            } catch (error) {
                console.warn('[MemoryBrowser] External-memory refresh broadcast failed:', error);
            } finally {
                if (channel) channel.close();
            }
        }
        if (window.parent && window.parent !== window) {
            window.parent.postMessage({ type: 'memory_edited', catgirl_name: characterName }, PARENT_ORIGIN);
        }
    }

    async function refreshImportedMemoryView(characterName) {
        if (!characterName || characterName !== currentCatName) return;
        await loadMemoryFileList();
        const button = Array.from(document.querySelectorAll('#memory-file-list .cat-btn')).find(
            item => item.dataset.catname === characterName
        );
        if (!button) return;
        const listItem = button.closest('li');
        if (memoryHasUnsavedChanges) {
            Array.from(document.getElementById('memory-file-list').children).forEach(function (item) {
                setRoleSelected(item, item === listItem);
            });
            return;
        }
        await selectMemoryFile(
            button.dataset.filename,
            listItem,
            characterName,
            { allowDuringImport: true }
        );
    }

    async function fetchExternalMemoryWithTimeout(url, options, timeoutMs) {
        const controller = new AbortController();
        const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);
        try {
            return await fetch(url, { ...options, signal: controller.signal });
        } catch (error) {
            if (error && error.name === 'AbortError') {
                throw new Error(translate('memory.externalImportFailed', 'External-memory import failed.'));
            }
            throw error;
        } finally {
            window.clearTimeout(timeoutId);
        }
    }

    async function importExternalMemory() {
        const button = document.getElementById('external-memory-import-btn');
        if (button) button.disabled = true;
        // 从预览阶段就置 in-flight 标志：updateExternalImportButton 与 beforeunload
        // 都据此拦截，防用户在预览 / 确认期间切角色或换文件重新启用按钮、起第二次
        // 导入（Codex P2）。finally 统一清除。
        window._memoryImportInProgress = true;
        // Nothing else syncs the insights panel at either edge of an import, so
        // its controls kept whatever state the last sync left them in: the lock
        // below never engaged during the import, and a sync that happened to run
        // while the flag was up left them disabled after it finished.
        syncRepetitionInsightsControls();
        // 冻结文件 / 格式选择：payload 在预览前已快照，期间若改选，commit 仍发旧
        // payload，会导入与界面所示不同的 workspace（Codex P2）。finally 复原。
        const fileInput = document.getElementById('external-memory-files');
        const formatSelect = document.getElementById('external-memory-format');
        if (fileInput) fileInput.disabled = true;
        if (formatSelect) formatSelect.disabled = true;
        // 融合期间每秒刷新「已用 Ns」的计时器；try/catch 任一出口都要清（finally 兜底）。
        let etaTimer = null;
        let persistedCharacterName = '';
        try {
            const targetCharacter = currentCatName;
            setExternalImportStatus(translate('memory.externalImportReading', 'Reading external memory...'), 'working');
            const payload = await buildExternalImportPayload(targetCharacter);
            const previewResponse = await fetchExternalMemoryWithTimeout('/api/memory/external_import/preview', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            }, 60000);
            const preview = await previewResponse.json();
            if (!previewResponse.ok || !preview.success) {
                throw new Error(preview.error || translate('memory.externalImportFailed', 'Import failed.'));
            }
            if (preview.character_name !== targetCharacter) {
                throw new Error(translate('memory.externalImportFailed', 'Import failed.'));
            }
            let confirmation = translate(
                'memory.externalImportConfirm',
                'Import {{persona}} persona entries and {{facts}} facts into {{character}}? Suspicious content warnings: {{warnings}}.',
                {
                    persona: preview.counts.persona,
                    facts: preview.counts.facts,
                    character: targetCharacter,
                    warnings: preview.warning_count
                }
            );
            if (preview.counts.daily) {
                // daily 日记在 commit 阶段经 LLM 抽取，最终 fact 数会与 preview 不同。
                confirmation += '\n\n' + translate(
                    'memory.externalImportDailyNote',
                    'Daily journals ({{daily}}) are extracted into facts by an LLM on import; the final fact count may differ from this preview.',
                    { daily: preview.counts.daily }
                );
            }
            if (Array.isArray(preview.warnings) && preview.warnings.length) {
                const warningDetails = preview.warnings.slice(0, 5).map(item => {
                    const patterns = Array.isArray(item.patterns) ? item.patterns.join(', ') : '';
                    return `- ${item.source_file}: ${item.text}${patterns ? ` [${patterns}]` : ''}`;
                }).join('\n');
                confirmation += `\n\n${warningDetails}`;
            }
            if (!window.confirm(confirmation)) {
                setExternalImportStatus(translate('memory.externalImportCancelled', 'Import cancelled.'), '');
                return;
            }
            payload.acknowledge_warnings = true;
            // 预估融合耗时：persona 融合按 entity(neko/master)分组每组一次 LLM 往返，
            // daily 日记每天一次 LLM 抽取；MEMORY.md facts 不调 LLM。
            // ⚠️ 后端两类调用都已并发执行（persona gather、daily 有界并发），但这里
            // 刻意按「全部串行」sum 估算——保守高估是产品决策（宁可提前完成也不要
            // 卡超预估），改并发系数前先确认这一点。固定标注 240s 后端上限；
            // 0 次 LLM 调用（纯 MEMORY.md 导入）不显示预估。
            const fusionCalls = Number(preview.persona_fusion_calls) || 0;
            const personaTokens = Number(preview.persona_candidate_tokens) || 0;
            const dailyCalls = Number(preview.daily_extraction_calls) || 0;
            const dailyTokens = Number(preview.daily_candidate_tokens) || 0;
            const llmCalls = fusionCalls + dailyCalls;
            const llmTokens = personaTokens + dailyTokens;
            const etaSeconds = llmCalls > 0
                ? Math.min(230, Math.max(8, Math.round(llmCalls * 10 + llmTokens / 300)))
                : 0;
            const workingStartedAt = Date.now();
            // 状态区追加「勿关闭」提示——现代 Chromium 会忽略 beforeunload 的自定义
            // 文案，真正的中文提示只能落在这里（in-flight 标志已在预览前置好）。
            const renderWorkingStatus = () => {
                const base = translate('memory.externalImportWorking', 'Importing memory...');
                const doNotClose = translate(
                    'memory.externalImportDoNotClose',
                    'Fusing memories — do not close this window or quit the app, or the import will fail.'
                );
                let etaText = '';
                if (etaSeconds > 0) {
                    const elapsed = Math.round((Date.now() - workingStartedAt) / 1000);
                    etaText = ' ' + translate(
                        'memory.externalImportEta',
                        'Estimated ~{{est}}s (up to 4 min) · {{elapsed}}s elapsed.',
                        { est: etaSeconds, elapsed }
                    );
                }
                setExternalImportStatus(base + etaText + ' ' + doNotClose, 'working');
            };
            renderWorkingStatus();
            if (etaSeconds > 0) {
                etaTimer = window.setInterval(renderWorkingStatus, 1000);
            }
            // 前端超时略大于后端 commit 转发窗口（memory_router timeout=240s），
            // 覆盖 persona 融合的整段耗时。
            // The UI locale is only a render fallback. Read it at commit time so a
            // language switch made while reviewing the preview is not stale, and
            // keep it separate from the durable per-character preference.
            const renderLanguage = getExternalImportRenderLanguage();
            if (renderLanguage) {
                payload.render_language = renderLanguage;
            } else {
                delete payload.render_language;
            }
            const commitResponse = await fetchExternalMemoryWithTimeout('/api/memory/external_import/commit', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            }, 270000);
            // 融合网络返回即停表（成功/失败/太大都已无意义再计时）；finally 兜底覆盖
            // commit fetch 抛错（超时/网络）根本没走到这行的情况。
            if (etaTimer) { window.clearInterval(etaTimer); etaTimer = null; }
            const result = await commitResponse.json();
            if (!commitResponse.ok || !result.success) {
                if (result.error_code === 'external_import_partial') {
                    const partial = result.partial_import || {};
                    // persona / facts 任一已落盘 → 即使整体 partial 也要广播
                    // memory_edited，否则主聊天窗口继续用过期记忆上下文（daily
                    // 失败但 MEMORY.md 已写入时尤甚）(Codex P2 / CodeRabbit)。
                    if ((partial.added_persona > 0 || partial.added_facts > 0) && partial.character_name) {
                        persistedCharacterName = partial.character_name;
                        broadcastExternalMemoryEdited(partial.character_name);
                    }
                    throw new Error(translate(
                        'memory.externalImportPartial',
                        'The import stopped after {{persona}} persona entries and {{facts}} facts were saved. Retry to finish; duplicates will be skipped.',
                        { persona: partial.added_persona || 0, facts: partial.added_facts || 0 }
                    ));
                }
                if (result.error_code === 'external_import_too_large') {
                    // 确定性「太大」失败：重试无益，提示拆分 workspace（Codex P2）。
                    const big = result.partial_import || {};
                    if ((big.added_persona > 0 || big.added_facts > 0) && big.character_name) {
                        persistedCharacterName = big.character_name;
                        broadcastExternalMemoryEdited(big.character_name);
                    }
                    throw new Error(translate(
                        'memory.externalImportTooLargeToFuse',
                        'This import has too many memories to fuse in one pass. Split the workspace into smaller files and import them separately.'
                    ));
                }
                throw new Error(result.error || translate('memory.externalImportFailed', 'Import failed.'));
            }
            setExternalImportStatus(
                translate(
                    'memory.externalImportSuccess',
                    'Imported {{persona}} persona entries and {{facts}} facts; skipped {{duplicates}} duplicates.',
                    {
                        persona: result.added_persona,
                        facts: result.added_facts,
                        duplicates: result.skipped_duplicates
                    }
                ),
                result.warning_count ? 'warning' : 'success'
            );
            persistedCharacterName = result.character_name;
            broadcastExternalMemoryEdited(result.character_name);
        } catch (error) {
            setExternalImportStatus(
                String(error && error.message ? error.message : translate('memory.externalImportFailed', 'Import failed.')),
                'error'
            );
        } finally {
            if (etaTimer) { window.clearInterval(etaTimer); etaTimer = null; }
            try {
                if (persistedCharacterName) {
                    await refreshImportedMemoryView(persistedCharacterName);
                }
            } catch (refreshError) {
                console.error('Failed to refresh imported memory view:', refreshError);
            } finally {
                window._memoryImportInProgress = false;
                if (fileInput) fileInput.disabled = false;
                if (formatSelect) formatSelect.disabled = false;
                updateExternalImportButton();
                syncRepetitionInsightsControls();
            }
        }
    }

    // 让备忘录 textarea 随内容撑高，把滚动权交还给外层列表。CSS 侧用 max-height
    // 封顶，超过封顶才由 textarea 自己滚——常规长度不会再出现两条并排的滚动条。
    function autoGrowMemoTextarea(textarea) {
        if (!textarea) return;
        // 必须先归零：否则内容变短时 scrollHeight 仍是旧高度，高度只涨不落。
        textarea.style.height = 'auto';
        const contentHeight = textarea.scrollHeight;
        if (contentHeight > 0) {
            textarea.style.height = contentHeight + 'px';
        }
    }

    // 空态复用已有的 i18n key（noChatContent + tip1），不新增 8 份语言包条目。
    function renderMemoryEmptyState(container) {
        if (!container) return;
        while (container.firstChild) container.removeChild(container.firstChild);

        const wrapper = document.createElement('div');
        wrapper.className = 'memory-empty-state';

        const icon = document.createElement('img');
        icon.className = 'memory-empty-state-icon';
        icon.src = '/static/icons/exclamation.png';
        icon.alt = '';
        icon.draggable = false;
        icon.setAttribute('aria-hidden', 'true');
        wrapper.appendChild(icon);

        const title = document.createElement('div');
        title.className = 'memory-empty-state-title';
        title.textContent = translate('memory.noChatContent', '无聊天内容');
        wrapper.appendChild(title);

        const hint = document.createElement('div');
        hint.className = 'memory-empty-state-hint';
        hint.textContent = translate(
            'memory.tip1',
            '刚刚结束的对话内容要稍等片刻才会载入，可以重新点击猫娘名称刷新。'
        );
        wrapper.appendChild(hint);

        container.appendChild(wrapper);
    }

    // 滚到底时撤掉底部渐隐，否则最后一条气泡会一直半透明，看起来像没渲染完。
    function syncMemoryChatScrollMask() {
        const editor = document.getElementById('memory-chat-edit');
        if (!editor) return;
        const scrollable = editor.scrollHeight - editor.clientHeight > 1;
        const atEnd = editor.scrollTop + editor.clientHeight >= editor.scrollHeight - 1;
        editor.classList.toggle('is-not-scrollable', !scrollable);
        editor.classList.toggle('is-scrolled-to-end', scrollable && atEnd);
    }

    function createDeleteButton(index, speaker, position) {
        const speakerLabel = String(speaker || 'AI').trim().replace(/[：:]\s*$/, '');
        const label = translate(
            'memory.deleteEntryAria',
            '删除 {{speaker}} 的第 {{position}} 条记忆',
            { speaker: speakerLabel, position: position }
        );
        const button = document.createElement('button');
        button.className = 'delete-btn';
        button.type = 'button';
        button.setAttribute('aria-label', label);
        const icon = document.createElement('img');
        icon.src = '/static/icons/delete.png';
        icon.alt = '';
        icon.draggable = false;
        icon.setAttribute('aria-hidden', 'true');
        button.appendChild(icon);
        button.addEventListener('click', function () { deleteChat(index); });
        return button;
    }

    function renderChatEdit() {
        const div = document.getElementById('memory-chat-edit');
        // 清空并使用 DOM API 渲染每一条消息，避免将未转义的用户数据插入到 HTML 中
        while (div.firstChild) div.removeChild(div.firstChild);
        if (!chatData.length) {
            renderMemoryEmptyState(div);
            syncMemoryChatScrollMask();
            return;
        }
        let conversationPosition = 0;
        chatData.forEach((msg, i) => {
            const container = document.createElement('div');
            container.className = 'chat-item';
            container.setAttribute('data-chat-index', String(i));
            container.setAttribute('data-chat-row-key', getMemoryChatRowKey(msg));
            container.setAttribute('data-role', msg.role || '');

            if (msg.role === 'system') {
                let text = msg.text;
                if (typeof text !== 'string') {
                    text = extractDataContent({ content: text });
                } else {
                    text = text || '';
                }
                // 去掉任何现有的前缀（支持多语言切换时的旧前缀）
                // 定义已知的备忘录前缀列表
                const knownPrefixes = [
                    '先前对话的备忘录: ',
                    'Previous conversation memo: ',
                    '前回の会話のメモ: ',
                    '先前對話的備忘錄: '
                ];
                // 尝试移除已知前缀
                for (const prefix of knownPrefixes) {
                    if (text.startsWith(prefix)) {
                        text = text.slice(prefix.length);
                        break;
                    }
                }

                const contentWrapper = document.createElement('div');
                contentWrapper.className = 'chat-item-content';
                container.appendChild(contentWrapper);

                const memoPrefix = window.t ? window.t('memory.previousMemo') : '先前对话的备忘录: ';
                const label = document.createElement('span');
                label.className = 'memo-label';
                label.textContent = memoPrefix;
                contentWrapper.appendChild(label);

                // LLM 在压缩时按 SUMMARY_STALE_HINT 要求，把"较久前"段用单独
                // 一行 `---` 与主体分隔。这里识别该分隔符并拆成两块独立 textarea
                // 渲染，让阅读 / 编辑时能清楚区分"当前进行中"和"已归档"。
                // 保存时再用 composeMemo 拼回 `\n\n---\n\n` 单一规范形式。
                let bodyValue;
                let olderValue;
                ({ body: bodyValue, older: olderValue } = splitMemoOnDivider(text));
                const commitMemo = function () {
                    updateSystemContent(i, composeMemo(bodyValue, olderValue));
                };

                const ta = document.createElement('textarea');
                ta.className = 'memo-textarea';
                ta.value = bodyValue;
                ta.addEventListener('input', function () {
                    bodyValue = this.value;
                    commitMemo();
                    setMemoryDirty(true);
                    autoGrowMemoTextarea(this);
                });
                contentWrapper.appendChild(ta);
                autoGrowMemoTextarea(ta);

                if (olderValue) {
                    const olderLabel = document.createElement('span');
                    olderLabel.className = 'memo-older-label';
                    olderLabel.textContent = window.t
                        ? window.t('memory.olderSection', '较久前')
                        : '较久前';
                    contentWrapper.appendChild(olderLabel);

                    const olderTa = document.createElement('textarea');
                    olderTa.className = 'memo-textarea memo-textarea--older';
                    olderTa.value = olderValue;
                    olderTa.addEventListener('input', function () {
                        olderValue = this.value;
                        commitMemo();
                        setMemoryDirty(true);
                        autoGrowMemoTextarea(this);
                    });
                    contentWrapper.appendChild(olderTa);
                    autoGrowMemoTextarea(olderTa);
                }
            } else if (msg.role === 'ai') {
                conversationPosition += 1;
                // 提取时间戳和正文，健壮处理
                const m = msg.text.match(/^(\[[^\]]+\])([\s\S]*)$/);
                const timeStr = m ? m[1] : '';
                const content = (m && m[2]) ? (m[2] || '').trim() : msg.text;

                const contentWrapper = document.createElement('div');
                contentWrapper.className = 'chat-item-content';
                container.appendChild(contentWrapper);

                const catLabel = currentCatName ? currentCatName : 'AI';
                const speaker = document.createElement('div');
                speaker.className = 'chat-speaker';
                speaker.textContent = catLabel;
                contentWrapper.appendChild(speaker);

                const bubble = document.createElement('div');
                bubble.className = 'chat-bubble';
                bubble.textContent = content;
                contentWrapper.appendChild(bubble);

                if (timeStr) {
                    const timeDiv = document.createElement('div');
                    timeDiv.className = 'chat-time';
                    timeDiv.textContent = timeStr;
                    contentWrapper.appendChild(timeDiv);
                }

                const deleteWrapper = document.createElement('div');
                deleteWrapper.className = 'delete-btn-wrapper';
                deleteWrapper.appendChild(createDeleteButton(i, catLabel, conversationPosition));
                container.appendChild(deleteWrapper);
            } else {
                conversationPosition += 1;
                const contentWrapper = document.createElement('div');
                contentWrapper.className = 'chat-item-content';
                container.appendChild(contentWrapper);

                const speaker = document.createElement('div');
                speaker.className = 'chat-speaker';
                const meLabel = window.t ? window.t('memory.me') : '我：';
                speaker.textContent = meLabel;
                contentWrapper.appendChild(speaker);

                const bubble = document.createElement('div');
                bubble.className = 'chat-bubble';
                bubble.textContent = msg.text;
                contentWrapper.appendChild(bubble);

                const deleteWrapper = document.createElement('div');
                deleteWrapper.className = 'delete-btn-wrapper';
                deleteWrapper.appendChild(createDeleteButton(i, meLabel, conversationPosition));
                container.appendChild(deleteWrapper);
            }

            div.appendChild(container);
        });
        syncMemoryChatScrollMask();
    }

    function deleteChat(idx) {
        if (memoryRowExitInProgress) return;
        const item = document.querySelector(`#memory-chat-edit .chat-item[data-chat-index="${idx}"]`);
        if (!item || idx < 0 || idx >= chatData.length) return;
        chatData.splice(idx, 1);
        setMemoryDirty(true);
        exitChatItems([item], renderChatEdit);
    }
    // 新增：AI输入框内容变更时，自动拼接时间戳
    function updateAIContent(idx, value) {
        const msg = chatData[idx];
        const m = msg.text.match(/^(\[[^\]]+\])/);
        if (m) {
            chatData[idx].text = m[1] + value;
        } else {
            chatData[idx].text = value;
        }
    }
    // 备忘录正文里 LLM 按 SUMMARY_STALE_HINT 约定，用 `---` 单独占行的分隔符
    // 把"较久前"尾段从主体切开。这里识别"`---` 单独成行（前后都换行了）"——
    // 前后空行数量都不强求，吃下 LLM 漏空行 / 多空行 / 多输几个连字符的常见漂移；
    // 切成 body / older 两段后 composeMemo 再统一拼回规范 `\n\n---\n\n`。
    // 整段里出现多次匹配（违反 prompt 约束）只取第一次。
    const MEMO_DIVIDER_RE = /(?:\r?\n)+[ \t]*-{3,}[ \t]*(?:\r?\n)+/;

    function splitMemoOnDivider(text) {
        const src = String(text == null ? '' : text);
        const m = MEMO_DIVIDER_RE.exec(src);
        if (!m) return { body: src, older: '' };
        return {
            body: src.slice(0, m.index),
            older: src.slice(m.index + m[0].length),
        };
    }

    function composeMemo(body, older) {
        // body 的尾部 / older 的首部都只去掉"整行空白"——也就是 trailing blank
        // lines / leading blank lines——保留段内有意义的前导缩进（用户在 older
        // textarea 里手写嵌套列表 / 代码片段时不被吃）。
        // 拼回时再用规范 `\n\n---\n\n` 形式，splitter 端会容忍换行漂移。
        const cleanBody = String(body == null ? '' : body).replace(/(?:[ \t]*\r?\n)+$/, '');
        const cleanOlder = String(older == null ? '' : older).replace(/^(?:[ \t]*\r?\n)+/, '');
        if (!cleanOlder) return cleanBody;
        return cleanBody + '\n\n---\n\n' + cleanOlder;
    }

    function updateSystemContent(idx, value) {
        // 存储时先移除任何现有的前缀，然后加上当前语言的前缀
        // 定义已知的备忘录前缀列表
        const knownPrefixes = [
            '先前对话的备忘录: ',
            'Previous conversation memo: ',
            '前回の会話のメモ: ',
            '先前對話的備忘錄: '
        ];
        // 尝试移除已知前缀
        for (const prefix of knownPrefixes) {
            if (value.startsWith(prefix)) {
                value = value.slice(prefix.length);
                break;
            }
        }
        const memoPrefix = window.t ? window.t('memory.previousMemo') : '先前对话的备忘录: ';
        chatData[idx].text = memoPrefix + value;
    }

    function getMemoryUnsavedSwitchTargetButton() {
        if (pendingMemoryClose) {
            return document.querySelector('.close-page-btn');
        }
        if (!pendingMemorySelection) return null;
        return findMemoryRoleButton(pendingMemorySelection.filename);
    }

    function findMemoryRoleButton(filename) {
        return Array.from(
            document.querySelectorAll('#memory-file-list .cat-btn[data-filename]')
        ).find(function (button) {
            return button.dataset.filename === filename;
        }) || null;
    }

    function findMemoryRoleListItem(filename) {
        const button = findMemoryRoleButton(filename);
        return button ? button.closest('li') : null;
    }

    function updateMemoryUnsavedSwitchCopy() {
        const dialog = document.getElementById('memory-unsaved-switch-dialog');
        if (!dialog || dialog.hidden || (!pendingMemorySelection && !pendingMemoryClose)) return;
        setElementText(
            'memory-unsaved-switch-title',
            pendingMemoryClose
                ? translate('memory.closeUnsavedConfirmTitle', '关闭记忆整理？')
                : translate('memory.switchCharacterConfirmTitle', '切换角色？')
        );
        const message = memoryUnsavedSwitchSaveError || translate(
            'memory.switchCharacterUnsaved',
            '{{name}} 的修改尚未保存',
            {
                name: currentCatName
                    || (pendingMemorySelection && pendingMemorySelection.catName)
                    || '',
            }
        );
        setElementText('memory-unsaved-switch-message', message);
        dialog.classList.toggle('is-error', Boolean(memoryUnsavedSwitchSaveError));
        setElementText(
            'memory-unsaved-switch-cancel',
            translate('memory.cancelSwitch', '取消')
        );
        setElementText(
            'memory-unsaved-switch-discard',
            translate('memory.discardAndSwitch', '放弃修改')
        );
        setElementText(
            'memory-unsaved-switch-save',
            pendingMemoryClose
                ? translate('memory.saveAndClose', '保存并关闭')
                : translate('memory.saveAndSwitch', '保存并切换')
        );
    }

    function syncMemoryUnsavedSwitchPosition() {
        memoryUnsavedSwitchPositionFrame = 0;
        const dialog = document.getElementById('memory-unsaved-switch-dialog');
        const target = getMemoryUnsavedSwitchTargetButton();
        if (!dialog || dialog.hidden || !target) return;

        const viewportPadding = 12;
        const gap = 12;
        const targetRect = target.getBoundingClientRect();
        const dialogRect = dialog.getBoundingClientRect();
        let left = targetRect.right + gap;
        let top = targetRect.top - 8;

        if (pendingMemorySelection) {
            const editor = document.querySelector('.editor');
            if (editor) {
                const editorRect = editor.getBoundingClientRect();
                const editorCenteredLeft = editorRect.left + ((editorRect.width - dialogRect.width) / 2);
                if (editorCenteredLeft > left) {
                    left = Math.min(left + 32, editorCenteredLeft);
                }
            }
        }

        if (left + dialogRect.width > window.innerWidth - viewportPadding) {
            left = Math.max(viewportPadding, window.innerWidth - dialogRect.width - viewportPadding);
        }
        top = Math.max(
            viewportPadding,
            Math.min(top, window.innerHeight - dialogRect.height - viewportPadding)
        );
        dialog.style.left = Math.round(left) + 'px';
        dialog.style.top = Math.round(top) + 'px';
    }

    function scheduleMemoryUnsavedSwitchPosition() {
        if ((!pendingMemorySelection && !pendingMemoryClose) || memoryUnsavedSwitchPositionFrame) return;
        memoryUnsavedSwitchPositionFrame = window.requestAnimationFrame(syncMemoryUnsavedSwitchPosition);
    }

    function setMemoryUnsavedSwitchBusy(busy) {
        memoryUnsavedSwitchBusy = Boolean(busy);
        const dialog = document.getElementById('memory-unsaved-switch-dialog');
        if (!dialog) return;
        dialog.setAttribute('aria-busy', memoryUnsavedSwitchBusy ? 'true' : 'false');
        dialog.querySelectorAll('button').forEach(function (button) {
            button.disabled = memoryUnsavedSwitchBusy;
        });
    }

    function closeMemoryUnsavedSwitchDialog(restoreFocus) {
        const dialog = document.getElementById('memory-unsaved-switch-dialog');
        const blocker = document.getElementById('memory-unsaved-switch-blocker');
        const target = getMemoryUnsavedSwitchTargetButton();
        if (memoryUnsavedSwitchPositionFrame) {
            window.cancelAnimationFrame(memoryUnsavedSwitchPositionFrame);
            memoryUnsavedSwitchPositionFrame = 0;
        }
        if (target) {
            target.classList.remove('is-switch-target');
            target.removeAttribute('aria-controls');
            target.removeAttribute('aria-expanded');
        }
        if (dialog) {
            dialog.hidden = true;
            dialog.style.removeProperty('left');
            dialog.style.removeProperty('top');
        }
        if (blocker) blocker.hidden = true;
        document.body.classList.remove('is-memory-switch-confirming');
        setMemoryUnsavedSwitchBusy(false);
        memoryUnsavedSwitchSaveError = '';
        pendingMemorySelection = null;
        pendingMemoryClose = false;
        const focusTarget = memoryUnsavedSwitchRestoreFocus;
        memoryUnsavedSwitchRestoreFocus = null;
        if (restoreFocus && focusTarget && focusTarget.isConnected) {
            focusTarget.focus();
        }
    }

    function openMemoryUnsavedSwitchDialog(filename, li, catName) {
        const dialog = document.getElementById('memory-unsaved-switch-dialog');
        const blocker = document.getElementById('memory-unsaved-switch-blocker');
        const target = findMemoryRoleButton(filename)
            || (li ? li.querySelector('.cat-btn') : null);
        if (!dialog || !blocker || !target) return false;

        pendingMemorySelection = { filename, catName };
        pendingMemoryClose = false;
        memoryUnsavedSwitchSaveError = '';
        memoryUnsavedSwitchRestoreFocus = target;
        target.classList.add('is-switch-target');
        target.setAttribute('aria-controls', 'memory-unsaved-switch-dialog');
        target.setAttribute('aria-expanded', 'true');
        document.body.classList.add('is-memory-switch-confirming');
        blocker.hidden = false;
        dialog.hidden = false;
        updateMemoryUnsavedSwitchCopy();
        scheduleMemoryUnsavedSwitchPosition();
        window.requestAnimationFrame(function () {
            const cancel = document.getElementById('memory-unsaved-switch-cancel');
            if (cancel && !dialog.hidden) cancel.focus();
        });
        return true;
    }

    function openMemoryUnsavedCloseDialog() {
        const dialog = document.getElementById('memory-unsaved-switch-dialog');
        const blocker = document.getElementById('memory-unsaved-switch-blocker');
        const target = document.querySelector('.close-page-btn');
        if (!dialog || !blocker || !target) return false;

        pendingMemorySelection = null;
        pendingMemoryClose = true;
        memoryUnsavedSwitchSaveError = '';
        memoryUnsavedSwitchRestoreFocus = document.activeElement && document.activeElement.isConnected
            ? document.activeElement
            : target;
        target.classList.add('is-switch-target');
        target.setAttribute('aria-controls', 'memory-unsaved-switch-dialog');
        target.setAttribute('aria-expanded', 'true');
        document.body.classList.add('is-memory-switch-confirming');
        blocker.hidden = false;
        dialog.hidden = false;
        updateMemoryUnsavedSwitchCopy();
        scheduleMemoryUnsavedSwitchPosition();
        window.requestAnimationFrame(function () {
            const cancel = document.getElementById('memory-unsaved-switch-cancel');
            if (cancel && !dialog.hidden) cancel.focus();
        });
        return true;
    }

    function requestMemoryFileSelection(filename, li, catName, options) {
        if (window._memoryImportInProgress || memoryUnsavedSwitchBusy) return;
        if (memoryHasUnsavedChanges && currentMemoryFile) {
            // The dialog path drops `options`, which is sound for the only option
            // there is: `keepInsightsTarget` comes from the startup auto-select,
            // and that runs only while `currentMemoryFile` is unset, so this
            // branch cannot be reached from it.
            openMemoryUnsavedSwitchDialog(filename, li, catName);
            return;
        }
        selectMemoryFile(filename, li, catName, options);
    }

    function initMemoryUnsavedSwitchDialog() {
        const dialog = document.getElementById('memory-unsaved-switch-dialog');
        const cancel = document.getElementById('memory-unsaved-switch-cancel');
        const discard = document.getElementById('memory-unsaved-switch-discard');
        const save = document.getElementById('memory-unsaved-switch-save');
        if (!dialog || !cancel || !discard || !save) return;

        cancel.addEventListener('click', function () {
            if (!memoryUnsavedSwitchBusy) closeMemoryUnsavedSwitchDialog(true);
        });
        discard.addEventListener('click', function () {
            if (memoryUnsavedSwitchBusy || (!pendingMemorySelection && !pendingMemoryClose)) return;
            const selection = pendingMemorySelection;
            const closeRequested = pendingMemoryClose;
            closeMemoryUnsavedSwitchDialog(false);
            if (closeRequested) {
                performCloseMemoryBrowser(true);
            } else {
                selectMemoryFile(
                    selection.filename,
                    findMemoryRoleListItem(selection.filename),
                    selection.catName
                );
            }
        });
        save.addEventListener('click', async function () {
            if (memoryUnsavedSwitchBusy || (!pendingMemorySelection && !pendingMemoryClose)) return;
            const selection = pendingMemorySelection;
            const closeRequested = pendingMemoryClose;
            memoryUnsavedSwitchSaveError = '';
            updateMemoryUnsavedSwitchCopy();
            setMemoryUnsavedSwitchBusy(true);
            const saved = await saveCurrentMemory();
            if (!saved) {
                const statusMessage = document.querySelector('#save-status .memory-save-toast-message');
                memoryUnsavedSwitchSaveError = statusMessage
                    ? String(statusMessage.textContent || '')
                    : '';
                updateMemoryUnsavedSwitchCopy();
                setMemoryUnsavedSwitchBusy(false);
                save.focus();
                return;
            }
            closeMemoryUnsavedSwitchDialog(false);
            if (closeRequested) {
                performCloseMemoryBrowser(false);
            } else {
                selectMemoryFile(
                    selection.filename,
                    findMemoryRoleListItem(selection.filename),
                    selection.catName
                );
            }
        });
        document.addEventListener('keydown', function (event) {
            if (dialog.hidden) return;
            if (event.key === 'Escape') {
                event.preventDefault();
                event.stopImmediatePropagation();
                if (!memoryUnsavedSwitchBusy) closeMemoryUnsavedSwitchDialog(true);
                return;
            }
            if (event.key !== 'Tab') return;
            const focusable = Array.from(dialog.querySelectorAll('button:not([disabled])'));
            if (!focusable.length) {
                event.preventDefault();
                return;
            }
            const first = focusable[0];
            const last = focusable[focusable.length - 1];
            if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first.focus();
            }
        }, true);
        window.addEventListener('resize', scheduleMemoryUnsavedSwitchPosition);
        document.addEventListener('scroll', scheduleMemoryUnsavedSwitchPosition, true);
        window.addEventListener('localechange', updateMemoryUnsavedSwitchCopy);
        window.addEventListener('pagehide', function () {
            closeMemoryUnsavedSwitchDialog(false);
        });
    }

    async function selectMemoryFile(filename, li, catName, options) {
        // 导入进行中冻结角色 / 文件切换：commit 用的是已快照的 targetCharacter，
        // 放行切换只会让侧栏与正在导入的选择不一致（Codex P2）。
        const allowDuringImport = !!(options && options.allowDuringImport);
        if (window._memoryImportInProgress && !allowDuringImport) return;
        const repeatsCurrentSelection = currentMemoryFile === filename
            && !!li
            && li.classList.contains('selected');
        const requestId = ++memoryFileRequestId;
        currentMemoryFile = filename;
        currentMemoryFingerprint = null;
        currentMemoryIdentityToken = null;
        const previousTarget = repetitionInsightsTarget();
        currentCatName = catName || (li ? li.getAttribute('data-catname') : '');
        // Opening a file re-couples the panel to the editor: an override set by
        // picking a file-less identity must not survive a deliberate switch. The
        // startup auto-select is not deliberate, so it opts out.
        if (!(options && options.keepInsightsTarget)) {
            repetitionInsightsCharacterOverride = '';
        }
        if (previousTarget !== repetitionInsightsTarget()) {
            resetRepetitionInsightsState();
        } else {
            syncRepetitionInsightsControls();
        }
        setMemoryDirty(false);
        dismissSaveStatus(true);
        updateExternalImportButton();
        if (!repeatsCurrentSelection) {
            Array.from(document.getElementById('memory-file-list').children).forEach(function (item) {
                setRoleSelected(item, item === li);
            });
        }
        setMemoryCurrentRoleName(currentCatName);
        setMemoryRolePanelOpen(false, false);
        const editDiv = document.getElementById('memory-chat-edit');

        // 清空并使用 textContent 设置加载中状态
        editDiv.textContent = '';
        const loadingDiv = document.createElement('div');
        loadingDiv.style.cssText = 'color:#888; padding: 20px; text-align: center;';
        loadingDiv.textContent = window.t ? window.t('memory.loading') : '加载中...';
        editDiv.appendChild(loadingDiv);

        const saveRow = document.getElementById('save-row');
        if (saveRow) {
            saveRow.style.display = 'flex';
        }
        try {
            // 直接获取原始JSON内容
            const resp = await fetch('/api/memory/recent_file?filename=' + encodeURIComponent(filename));
            const data = await resp.json();
            if (requestId !== memoryFileRequestId) {
                return;
            }
            currentMemoryFingerprint = typeof data.fingerprint === 'string'
                ? data.fingerprint
                : null;
            currentMemoryIdentityToken = typeof data.identity_token === 'string'
                ? data.identity_token
                : null;
            if (data.content) {
                let arr = [];
                try { arr = JSON.parse(data.content); } catch (e) { arr = []; }
                if (requestId !== memoryFileRequestId) {
                    return;
                }
                chatData = arr.map(item => {
                    if (item.type === 'system') {
                        return { role: 'system', text: extractDataContent(item.data) };
                    }
                    if (item.type === 'ai' || item.type === 'human') {
                        return { role: item.type, text: extractDataContent(item.data) };
                    }
                    if (item.role === 'system') {
                        return { role: 'system', text: extractDataContent({ content: item.content }) };
                    }
                    if (item.role === 'user' || item.role === 'assistant') {
                        const role = item.role === 'assistant' ? 'ai' : 'human';
                        return { role, text: extractDataContent({ content: item.content }) };
                    }
                    return null;
                }).filter(Boolean);
                renderChatEdit();
            } else {
                if (requestId !== memoryFileRequestId) {
                    return;
                }
                chatData = [];
                renderMemoryEmptyState(editDiv);
            }
        } catch (e) {
            if (requestId !== memoryFileRequestId) {
                return;
            }
            chatData = [];
            editDiv.innerHTML = '<div style="color:#e74c3c; padding: 20px; text-align: center;">' + (window.t ? window.t('memory.loadFailed') : '加载失败') + '</div>';
        }
    }
    async function saveCurrentMemory() {
        const requestedFile = currentMemoryFile;
        const requestedSelectionId = memoryFileRequestId;
        const requestedContentRevision = memoryEditRevision;
        if (
            memorySaveInFlight
            && memorySaveInFlight.file === requestedFile
            && memorySaveInFlight.selectionId === requestedSelectionId
        ) {
            if (memorySaveInFlight.contentRevision === requestedContentRevision) {
                return memorySaveInFlight.promise;
            }
            return memorySaveInFlight.promise.then(function (saved) {
                if (!saved) return false;
                if (
                    currentMemoryFile !== requestedFile
                    || memoryFileRequestId !== requestedSelectionId
                ) {
                    return false;
                }
                return saveCurrentMemory();
            });
        }

        const promise = saveCurrentMemoryOnce();
        const activeSave = {
            file: requestedFile,
            selectionId: requestedSelectionId,
            contentRevision: requestedContentRevision,
            promise
        };
        memorySaveInFlight = activeSave;
        try {
            return await promise;
        } finally {
            if (memorySaveInFlight === activeSave) {
                memorySaveInFlight = null;
            }
        }
    }

    async function saveCurrentMemoryOnce() {
        if (!currentMemoryFile) {
            showSaveStatus(window.t ? window.t('memory.pleaseSelectFile') : '请先选择文件', false);
            return false;
        }
        if (!currentMemoryFingerprint || !currentMemoryIdentityToken) {
            const loadFailed = window.t ? window.t('memory.loadFailed') : '加载失败';
            const refreshTip = window.t
                ? window.t('memory.tip1')
                : '请重新点击角色名加载后再保存';
            showSaveStatus(loadFailed + '。' + refreshTip, false);
            return false;
        }
        const saveFile = currentMemoryFile;
        const saveSelectionId = memoryFileRequestId;
        const saveRequestId = ++memorySaveRequestId;
        const saveContentRevision = memoryEditRevision;
        const saveFingerprint = currentMemoryFingerprint;
        const saveIdentityToken = currentMemoryIdentityToken;
        const stillTargetsSavedSelection = () => (
            currentMemoryFile === saveFile
            && memoryFileRequestId === saveSelectionId
            && memorySaveRequestId === saveRequestId
        );
        const stillMatchesSavedRevision = () => (
            stillTargetsSavedSelection()
            && memoryEditRevision === saveContentRevision
        );
        const saveChat = chatData.map(msg => ({ ...msg }));
        // 处理备忘录为空的情况
        const memoPrefix = window.t ? window.t('memory.previousMemo') : '先前对话的备忘录: ';
        const memoNone = window.t ? window.t('memory.memoNone') : '无。';
        saveChat.forEach(msg => {
            if (msg.role === 'system') {
                let text = msg.text || '';
                if (text.startsWith(memoPrefix)) {
                    text = text.slice(memoPrefix.length);
                }
                if (!text.trim()) {
                    msg.text = memoPrefix + memoNone;
                }
            }
        });
        try {
            const resp = await fetch('/api/memory/recent_file/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    filename: saveFile,
                    chat: saveChat,
                    fingerprint: saveFingerprint,
                    identity_token: saveIdentityToken
                })
            });
            const data = await resp.json();
            if (data.success) {
                if (stillTargetsSavedSelection()) {
                    currentMemoryFingerprint = typeof data.fingerprint === 'string'
                        ? data.fingerprint
                        : null;
                    currentMemoryIdentityToken = typeof data.identity_token === 'string'
                        ? data.identity_token
                        : null;
                }
                if (stillMatchesSavedRevision()) {
                    setMemoryDirty(false);
                    showSaveStatus(window.t ? window.t('memory.saveSuccess') : '保存成功', 'success', 3000);
                }

                // 通知父窗口刷新对话上下文
                if (data.need_refresh && stillTargetsSavedSelection()) {
                    let broadcastSent = false;
                    
                    // 优先使用 BroadcastChannel（跨页面通信）
                    if (typeof BroadcastChannel !== 'undefined') {
                        let channel = null;
                        try {
                            channel = new BroadcastChannel('neko_page_channel');
                            channel.postMessage({
                                action: 'memory_edited',
                                catgirl_name: data.catgirl_name
                            });
                            console.log('[MemoryBrowser] 已通过 BroadcastChannel 发送 memory_edited 消息');
                            broadcastSent = true;
                        } catch (e) {
                            console.error('[MemoryBrowser] BroadcastChannel 发送失败:', e);
                        } finally {
                            if (channel) {
                                channel.close();
                            }
                        }
                    }
                    
                    // 仅当 BroadcastChannel 不可用时，使用 postMessage 作为后备（iframe 场景）
                    if (!broadcastSent && window.parent && window.parent !== window) {
                        window.parent.postMessage({
                            type: 'memory_edited',
                            catgirl_name: data.catgirl_name
                        }, PARENT_ORIGIN);
                        console.log('[MemoryBrowser] 已通过 postMessage 发送 memory_edited 消息（后备方案）');
                    }
                }
                return true;
            } else {
                const errorMsg = data.error || (window.t ? window.t('common.unknownError') : '未知错误');
                if (stillTargetsSavedSelection()) {
                    showSaveStatus(window.t ? window.t('memory.saveFailed', { error: errorMsg }) : '保存失败：' + errorMsg, false);
                }
                return false;
            }
        } catch (e) {
            if (stillTargetsSavedSelection()) {
                showSaveStatus(window.t ? window.t('memory.saveFailedGeneral') : '保存失败', false);
            }
            return false;
        }
    }
    document.getElementById('save-memory-btn').onclick = saveCurrentMemory;
    document.getElementById('clear-memory-btn').onclick = function () {
        if (memoryRowExitInProgress) return;
        const itemsToDissolve = Array.from(
            document.querySelectorAll('#memory-chat-edit .chat-item[data-role="human"], #memory-chat-edit .chat-item[data-role="ai"]')
        );
        if (!itemsToDissolve.length) {
            showSaveStatus(
                window.t ? window.t('memory.clearedChatKeptMemo') : '已清空对话记录，备忘录已保留',
                'info',
                3200
            );
            return;
        }
        // 只清空对话轮次（用户 / AI）；system＝先前对话的备忘录，一律保留
        chatData = chatData.filter(msg => msg && msg.role !== 'human' && msg.role !== 'ai');
        setMemoryDirty(true);
        exitChatItems(itemsToDissolve, function () {
            renderChatEdit();
            showSaveStatus(
                window.t ? window.t('memory.clearedChatKeptMemo') : '已清空对话记录，备忘录已保留',
                'info',
                3200
            );
        }, { batch: true });
    };
    function setMemoryDirty(dirty) {
        if (dirty) memoryEditRevision++;
        memoryHasUnsavedChanges = Boolean(dirty);
        const indicator = document.getElementById('memory-unsaved-status');
        const saveButton = document.getElementById('save-memory-btn');
        if (!indicator || !saveButton) return;
        const indicatorText = indicator.querySelector('[data-i18n="memory.unsavedChanges"]');
        if (indicatorText) {
            indicatorText.textContent = translate('memory.unsavedChanges', '未保存');
        }
        indicator.hidden = !memoryHasUnsavedChanges;
        indicator.classList.toggle('is-visible', memoryHasUnsavedChanges);
        saveButton.classList.toggle('is-dirty', memoryHasUnsavedChanges);
        if (memoryHasUnsavedChanges) {
            saveButton.setAttribute('aria-describedby', 'memory-unsaved-status');
        } else {
            saveButton.removeAttribute('aria-describedby');
        }
    }
    function clearSaveStatusTimers() {
        if (memorySaveStatusTimer) {
            window.clearTimeout(memorySaveStatusTimer);
            memorySaveStatusTimer = 0;
        }
        if (memorySaveStatusHideTimer) {
            window.clearTimeout(memorySaveStatusHideTimer);
            memorySaveStatusHideTimer = 0;
        }
    }
    function finishHidingSaveStatus(el) {
        el.hidden = true;
        el.classList.remove('is-visible', 'is-leaving', 'is-success', 'is-error', 'is-info');
        const message = el.querySelector('.memory-save-toast-message');
        if (message) message.textContent = '';
    }
    function dismissSaveStatus(immediate) {
        const el = document.getElementById('save-status');
        if (!el) return;
        clearSaveStatusTimers();
        if (el.hidden) return;
        const reducedMotion = window.matchMedia
            && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        if (immediate || reducedMotion) {
            finishHidingSaveStatus(el);
            return;
        }
        el.classList.remove('is-visible');
        el.classList.add('is-leaving');
        memorySaveStatusHideTimer = window.setTimeout(function () {
            memorySaveStatusHideTimer = 0;
            finishHidingSaveStatus(el);
        }, 140);
    }
    function showSaveStatus(msg, state, dismissAfterMs) {
        const el = document.getElementById('save-status');
        if (!el) return;
        clearSaveStatusTimers();
        const normalizedState = state === 'success' || state === 'info' ? state : 'error';
        const message = el.querySelector('.memory-save-toast-message');
        if (message) message.textContent = msg;
        el.hidden = false;
        el.classList.remove('is-leaving');
        el.classList.toggle('is-success', normalizedState === 'success');
        el.classList.toggle('is-error', normalizedState === 'error');
        el.classList.toggle('is-info', normalizedState === 'info');
        el.classList.add('is-visible');
        el.setAttribute('role', normalizedState === 'error' ? 'alert' : 'status');
        el.setAttribute('aria-live', normalizedState === 'error' ? 'assertive' : 'polite');
        if (Number(dismissAfterMs) > 0) {
            memorySaveStatusTimer = window.setTimeout(function () {
                memorySaveStatusTimer = 0;
                dismissSaveStatus(false);
            }, Number(dismissAfterMs));
        }
    }
    function suppressMemoryUnloadPromptTemporarily() {
        memoryUnloadPromptSuppressed = true;
        if (memoryUnloadPromptSuppressionTimer) {
            window.clearTimeout(memoryUnloadPromptSuppressionTimer);
        }
        memoryUnloadPromptSuppressionTimer = window.setTimeout(function () {
            memoryUnloadPromptSuppressionTimer = 0;
            memoryUnloadPromptSuppressed = false;
        }, 2000);
    }

    function performCloseMemoryBrowser(suppressUnsavedPrompt) {
        if (suppressUnsavedPrompt) {
            suppressMemoryUnloadPromptTemporarily();
        }
        teardownMemoryRowExit();
        if (window.opener) {
            // 如果是通过 window.open() 打开的，直接关闭
            window.close();
        } else if (window.parent && window.parent !== window) {
            // 如果在 iframe 中，通知父窗口关闭
            window.parent.postMessage({ type: 'close_memory_browser' }, PARENT_ORIGIN);
        } else {
            // 否则尝试关闭窗口
            // 注意：如果是用户直接访问的页面，浏览器可能不允许关闭
            // 在这种情况下，可以尝试返回上一页或显示提示
            if (window.history.length > 1) {
                window.history.back();
            } else {
                window.close();
                // 如果 window.close() 失败（页面仍然存在），可以显示提示
                setTimeout(() => {
                    if (!window.closed) {
                        // 窗口未能关闭，返回主页
                        window.location.href = '/';
                    }
                }, 100);
            }
        }
    }

    function closeMemoryBrowser() {
        if (memoryHasUnsavedChanges && openMemoryUnsavedCloseDialog()) {
            return;
        }
        performCloseMemoryBrowser(false);
    }
    // 将函数暴露到全局作用域，供 HTML onclick 调用
    window.closeMemoryBrowser = closeMemoryBrowser;
    window.addEventListener('pagehide', function () {
        teardownMemoryRowExit();
        dismissSaveStatus(true);
    });
    window.addEventListener('beforeunload', function (e) {
        if (window._memoryImportInProgress) {
            // 记忆融合进行中：拦住关闭，避免中断同步导入。真正的中文提示已在状态区
            // 常驻（现代 Chromium 会忽略这里的自定义文案，只弹通用确认框）。
            const message = translate(
                'memory.externalImportDoNotClose',
                'Fusing memories — do not close this window or quit the app, or the import will fail.'
            );
            e.preventDefault();
            e.returnValue = message;
            return message;
        }
        if (memoryHasUnsavedChanges && !memoryUnloadPromptSuppressed) {
            const message = translate(
                'memory.switchCharacterUnsaved',
                '{{name}} 的修改尚未保存',
                { name: currentCatName || '' }
            );
            e.preventDefault();
            e.returnValue = message;
            return message;
        }
        teardownMemoryRowExit();
    });
    window.addEventListener('pagehide', teardownMemoryLayoutTransitionAndCommit);
    window.addEventListener('beforeunload', teardownMemoryLayoutTransitionAndCommit);
    window.addEventListener('pagehide', teardownMemoryRolePanelPositionSync);
    window.addEventListener('beforeunload', teardownMemoryRolePanelPositionSync);
    // 页面加载时隐藏保存按钮
    document.addEventListener('DOMContentLoaded', async function () {
        initMemoryExportLogs();
        initMemoryLayoutMode();
        initMemoryRolePanel();
        initMemoryUnsavedSwitchDialog();
        initRepetitionInsights();
        initMemoryAuxiliaryPanels();
        const chatEditor = document.getElementById('memory-chat-edit');
        if (chatEditor) {
            chatEditor.addEventListener('scroll', syncMemoryChatScrollMask, { passive: true });
            window.addEventListener('resize', syncMemoryChatScrollMask);
        }
        const storagePanelState = await initStorageLocationPanel();
        if (storagePanelState && storagePanelState.limited) {
            renderMemoryBrowserLimitedState(storagePanelState);
        } else {
            memoryStorageLimited = false;
            setReviewControlsEnabled(true);
            setPowerfulMemoryControlsEnabled(true);
            // Load the identity list now that the root is settled. Measured
            // as redundant today -- a later control sync reaches it anyway,
            // and removing this line keeps the guard test green -- but that
            // is an incidental property of the current init sequence, not a
            // contract. The gate above is the load-bearing half.
            loadRepetitionInsightCharacters();
            await loadMemoryFileList();
            if (!currentCatName) {
                try {
                    const response = await fetch('/api/characters/current_catgirl');
                    const current = await response.json();
                    currentCatName = current.current_catgirl || '';
                    syncRepetitionInsightsControls();
                } catch (error) {
                    console.warn('[MemoryBrowser] Failed to resolve external-memory target:', error);
                }
            }
            loadReviewConfig();
            loadPowerfulMemoryConfig();
        }
        document.getElementById('save-row').style.display = 'none';

        // 监听checkbox变化
        const checkbox = document.getElementById('review-toggle-checkbox');
        if (checkbox) {
            checkbox.addEventListener('change', function () {
                toggleReview(this.checked);
            });
        }
        const strongCheckbox = document.getElementById('strong-memory-toggle-checkbox');
        if (strongCheckbox) {
            strongCheckbox.addEventListener('change', function () {
                togglePowerfulMemory(this.checked);
            });
        }

        // 监听i18n语言变化
        if (window.i18n) {
            window.i18n.on('languageChanged', function () {
                const checkbox = document.getElementById('review-toggle-checkbox');
                renderStorageLocationPanel();
                if (checkbox) {
                    updateToggleText(checkbox.checked);
                }
                const strongCheckbox = document.getElementById('strong-memory-toggle-checkbox');
                if (strongCheckbox) {
                    updatePowerfulMemoryToggleText(strongCheckbox.checked);
                }
                if (storageLocationState && storageLocationState.limited) {
                    renderMemoryBrowserLimitedState(storageLocationState);
                }
                refreshTutorialCascaderDayLabels();
                syncTutorialResetCascader();
                syncExternalMemoryFormatDropdown();
                syncMemoryRoleTriggerLabel();
                if (!repetitionInsightsLanguageTouched && !repetitionInsightsReport) {
                    const insightsLanguage = document.getElementById('memory-insights-language');
                    if (insightsLanguage) insightsLanguage.value = repetitionInsightLanguageFromLocale();
                }
                refreshRepetitionInsightsStatus();
                renderRepetitionInsightsResults();
                syncRepetitionInsightsControls();
            });
        }
        window.addEventListener('localechange', function () {
            refreshTutorialCascaderDayLabels();
            syncTutorialResetCascader();
            syncExternalMemoryFormatDropdown();
            syncMemoryRoleTriggerLabel();
            syncMemoryRoleSourceCopies();
            if (!repetitionInsightsLanguageTouched && !repetitionInsightsReport) {
                const insightsLanguage = document.getElementById('memory-insights-language');
                if (insightsLanguage) insightsLanguage.value = repetitionInsightLanguageFromLocale();
            }
            refreshRepetitionInsightsStatus();
            renderRepetitionInsightsResults();
            syncRepetitionInsightsControls();
        });

        const externalFiles = document.getElementById('external-memory-files');
        const externalPick = document.getElementById('external-memory-pick-btn');
        const externalImport = document.getElementById('external-memory-import-btn');
        const externalFormatSelect = document.getElementById('external-memory-format');
        const externalFormatCascader = document.getElementById('external-memory-format-cascader');
        if (externalFormatSelect && externalFormatCascader) {
            const externalFormatTrigger = externalFormatCascader.querySelector('.external-memory-format-trigger');
            const externalFormatPopup = externalFormatCascader.querySelector('.external-memory-format-popup');
            syncExternalMemoryFormatDropdown();
            if (externalFormatTrigger) {
                externalFormatTrigger.addEventListener('click', function () {
                    if (window._memoryImportInProgress) return;  // 导入期间冻结格式选择 (Codex P2)
                    setExternalMemoryFormatOpen(!(externalFormatPopup && !externalFormatPopup.hidden));
                });
            }
            if (externalFormatPopup) {
                externalFormatPopup.addEventListener('click', function (event) {
                    if (window._memoryImportInProgress) return;  // 导入期间冻结格式选择 (Codex P2)
                    const option = event.target.closest('[data-external-memory-format]');
                    if (!option) return;
                    externalFormatSelect.value = option.dataset.externalMemoryFormat || 'auto';
                    externalFormatSelect.dispatchEvent(new Event('change', { bubbles: true }));
                    syncExternalMemoryFormatDropdown();
                    setExternalMemoryFormatOpen(false);
                    if (externalFormatTrigger) externalFormatTrigger.focus();
                });
            }
            document.addEventListener('click', function (event) {
                if (!externalFormatCascader.contains(event.target)) {
                    setExternalMemoryFormatOpen(false);
                }
            });
            externalFormatCascader.addEventListener('keydown', function (event) {
                if (event.key === 'Escape') {
                    setExternalMemoryFormatOpen(false);
                    if (externalFormatTrigger) externalFormatTrigger.focus();
                }
            });
        }
        if (externalPick && externalFiles) {
            externalPick.addEventListener('click', function () { externalFiles.click(); });
            externalFiles.addEventListener('change', function () {
                const names = Array.from(externalFiles.files || []).map(file => file.name);
                setElementText(
                    'external-memory-selection',
                    names.length
                        ? names.join(', ')
                        : translate('memory.externalImportNoSelection', 'No files selected')
                );
                setExternalImportStatus('', '');
                updateExternalImportButton();
            });
        }
        if (externalImport) {
            externalImport.addEventListener('click', importExternalMemory);
        }
        updateExternalImportButton();

        const openStorageBtn = document.getElementById('storage-location-open-btn');
        if (openStorageBtn) {
            openStorageBtn.addEventListener('click', function () {
                openCurrentStorageRoot();
            });
        }
        const manageStorageBtn = document.getElementById('storage-location-manage-btn');
        if (manageStorageBtn) {
            manageStorageBtn.addEventListener('click', function () {
                openStorageLocationManager();
            });
        }
        const closeStorageModalBtn = document.getElementById('storage-location-modal-close');
        if (closeStorageModalBtn) {
            closeStorageModalBtn.addEventListener('click', function () {
                closeStorageLocationManager();
            });
        }
        const storageModal = document.getElementById('storage-location-modal');
        if (storageModal) {
            storageModal.addEventListener('click', function (event) {
                if (event.target === storageModal) {
                    closeStorageLocationManager();
                }
            });
        }
        const pickStorageBtn = document.getElementById('storage-location-pick-btn');
        if (pickStorageBtn) {
            pickStorageBtn.addEventListener('click', function () {
                pickStorageTargetDirectory();
            });
        }
        const storageTargetInput = document.getElementById('storage-target-root-input');
        if (storageTargetInput) {
            storageTargetInput.addEventListener('input', function () {
                storagePreflightState = null;
                setStoragePreflightResult('', '');
                renderStorageRestartButton();
            });
        }
        const restartStorageBtn = document.getElementById('storage-location-restart-btn');
        if (restartStorageBtn) {
            restartStorageBtn.addEventListener('click', function () {
                preflightAndRestartWithStorageLocation();
            });
        }

        // 监听新手引导重置级联选择器变化
        const tutorialSelect = document.getElementById('tutorial-reset-select');
        const tutorialResetBtn = document.getElementById('tutorial-reset-btn');
        const tutorialCascader = document.getElementById('tutorial-reset-cascader');
        if (tutorialSelect && tutorialResetBtn && tutorialCascader) {
            refreshTutorialCascaderDayLabels();
            syncTutorialResetCascader();
            const trigger = tutorialCascader.querySelector(':scope > .tutorial-cascader-trigger');
            const popup = tutorialCascader.querySelector(':scope > .tutorial-cascader-popup');
            if (trigger) {
                trigger.addEventListener('click', function () {
                    setTutorialCascaderOpen(!(popup && !popup.hidden));
                });
            }
            if (popup) {
                popup.addEventListener('click', function (event) {
                    const pageOption = event.target.closest('[data-tutorial-page]');
                    if (pageOption) {
                        tutorialSelect.value = pageOption.dataset.tutorialPage || '';
                        if (tutorialSelect.value !== 'home') {
                            selectedTutorialDay = 0;
                            selectedTutorialHomeAll = false;
                            setTutorialCascaderOpen(false);
                        }
                        syncTutorialResetCascader();
                        return;
                    }
                    const homeAllOption = event.target.closest('[data-tutorial-home-all]');
                    if (homeAllOption) {
                        selectedTutorialHomeAll = true;
                        selectedTutorialDay = 0;
                        syncTutorialResetCascader();
                        setTutorialCascaderOpen(false);
                        return;
                    }
                    const dayOption = event.target.closest('[data-tutorial-day]');
                    if (dayOption) {
                        selectedTutorialHomeAll = false;
                        selectedTutorialDay = Number(dayOption.dataset.tutorialDay || 0);
                        syncTutorialResetCascader();
                        setTutorialCascaderOpen(false);
                    }
                });
            }
            document.addEventListener('click', function (event) {
                if (!tutorialCascader.contains(event.target)) {
                    setTutorialCascaderOpen(false);
                }
            });
        }

        // Electron白屏修复
        if (document.body) {
            void document.body.offsetHeight;
            const currentOpacity = document.body.style.opacity || '1';
            document.body.style.opacity = '0.99';
            requestAnimationFrame(() => {
                document.body.style.opacity = currentOpacity;
            });
        }
    });

    window.addEventListener('load', function () {
        // 再次强制重绘以确保资源加载后显示
        if (document.body) void document.body.offsetHeight;
    });


    async function loadReviewConfig() {
        try {
            const resp = await fetch('/api/memory/review_config');
            const data = await resp.json();
            const checkbox = document.getElementById('review-toggle-checkbox');

            if (checkbox) {
                checkbox.checked = data.enabled;
            }
            updateToggleText(data.enabled);
        } catch (e) {
            console.error('加载审阅配置失败:', e);
        }
    }

    function updateToggleText(enabled) {
        const textSpan = document.getElementById('review-toggle-text');
        if (!textSpan) return;
        if (enabled) {
            textSpan.setAttribute('data-i18n', 'memory.enabled');
            textSpan.textContent = window.t ? window.t('memory.enabled') : '已开启';
        } else {
            textSpan.setAttribute('data-i18n', 'memory.disabled');
            textSpan.textContent = window.t ? window.t('memory.disabled') : '已关闭';
        }
    }

    async function toggleReview(enabled) {
        try {
            const resp = await fetch('/api/memory/review_config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ enabled: enabled })
            });
            const data = await resp.json();

            if (data.success) {
                updateToggleText(enabled);
            } else {
                // 如果保存失败，恢复原来的状态
                const checkbox = document.getElementById('review-toggle-checkbox');
                if (checkbox) {
                    checkbox.checked = !enabled;
                }
                updateToggleText(!enabled);
            }
        } catch (e) {
            console.error('更新审阅配置失败:', e);
            // 如果请求失败，恢复原来的状态
            const checkbox = document.getElementById('review-toggle-checkbox');
            if (checkbox) {
                checkbox.checked = !enabled;
            }
            updateToggleText(!enabled);
        }
    }

    // ── 强力记忆开关（与 review 开关对偶，仿同样 load/update/toggle 模板） ──

    async function loadPowerfulMemoryConfig() {
        try {
            const resp = await fetch('/api/memory/powerful_memory_config');
            const data = await resp.json();
            const checkbox = document.getElementById('strong-memory-toggle-checkbox');
            if (checkbox) {
                checkbox.checked = data.enabled;
            }
            updatePowerfulMemoryToggleText(data.enabled);
        } catch (e) {
            console.error('加载强力记忆配置失败:', e);
        }
    }

    function updatePowerfulMemoryToggleText(enabled) {
        const textSpan = document.getElementById('strong-memory-toggle-text');
        if (!textSpan) return;
        if (enabled) {
            textSpan.setAttribute('data-i18n', 'memory.enabled');
            textSpan.textContent = window.t ? window.t('memory.enabled') : '已开启';
        } else {
            textSpan.setAttribute('data-i18n', 'memory.disabled');
            textSpan.textContent = window.t ? window.t('memory.disabled') : '已关闭';
        }
    }

    async function togglePowerfulMemory(enabled) {
        try {
            const resp = await fetch('/api/memory/powerful_memory_config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ enabled: enabled })
            });
            const data = await resp.json();

            if (data.success) {
                updatePowerfulMemoryToggleText(enabled);
            } else {
                const checkbox = document.getElementById('strong-memory-toggle-checkbox');
                if (checkbox) {
                    checkbox.checked = !enabled;
                }
                updatePowerfulMemoryToggleText(!enabled);
            }
        } catch (e) {
            console.error('更新强力记忆配置失败:', e);
            const checkbox = document.getElementById('strong-memory-toggle-checkbox');
            if (checkbox) {
                checkbox.checked = !enabled;
            }
            updatePowerfulMemoryToggleText(!enabled);
        }
    }

    window.resetSelectedTutorial = resetSelectedTutorial;
    window.showTutorialResetNotice = showTutorialResetNotice;

})();
