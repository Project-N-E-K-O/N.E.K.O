(function () {
    'use strict';

    const PARENT_ORIGIN = window.location.origin;
    let currentMemoryFile = null;
    let chatData = [];
    let currentCatName = '';
    let memoryFileRequestId = 0;
    let memoryDissolveInProgress = false;
    let memoryParticleCanvas = null;
    let memoryParticleContext = null;
    let memoryParticleFrame = 0;
    let memoryParticles = [];
    let memoryParticleCanvasResizeBound = false;
    let memoryDissolveRunId = 0;
    let storageLocationState = {
        bootstrap: null,
        blockingReason: '',
        loadFailed: false,
        limited: false
    };
    let memorySidebarResizeObserver = null;
    let memoryChatPanelHeightResizeBound = false;
    let storagePreflightState = null;
    let storagePreflightBusy = false;
    const STORAGE_APP_FOLDER_NAME = 'N.E.K.O';
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

    function getTutorialResetNoticeTitle() {
        const titleEl = document.querySelector('.tutorial-section .file-list-title');
        const domTitle = titleEl ? String(titleEl.textContent || '').trim() : '';
        return translate('memory.tutorialReset', domTitle || 'Tutorial');
    }

    let activeTutorialResetNotice = null;

    function showTutorialResetNotice(message, options) {
        const config = options && typeof options === 'object' ? options : {};
        const title = config.title || getTutorialResetNoticeTitle();
        const okText = config.okText || translate('common.ok', 'OK');
        const variant = config.variant === 'error' ? 'error' : 'success';
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

            function cleanup() {
                if (cleaned) return;
                cleaned = true;
                document.removeEventListener('keydown', onKeydown);
                if (backdrop.parentNode) {
                    backdrop.parentNode.removeChild(backdrop);
                }
                if (activeTutorialResetNotice && activeTutorialResetNotice.backdrop === backdrop) {
                    activeTutorialResetNotice = null;
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
                    cleanup();
                    settle(true);
                }, 160);
            }

            function dispose(result) {
                if (cleaned) return;
                closed = true;
                cleanup();
                settle(result);
            }

            function onKeydown(event) {
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
            activeTutorialResetNotice = { backdrop, dispose };
            window.setTimeout(function () {
                okButton.focus();
            }, 0);
        });
    }

    function syncMemoryChatPanelHeight() {
        const main = document.querySelector('.main');
        const sidebar = document.querySelector('.left-column');
        if (!main || !sidebar) return;
        const sidebarHeight = Math.ceil(sidebar.getBoundingClientRect().height);
        if (sidebarHeight > 0) {
            main.style.setProperty('--memory-sidebar-height', sidebarHeight + 'px');
        }
    }

    function initMemoryChatPanelHeightSync() {
        const sidebar = document.querySelector('.left-column');
        teardownMemoryChatPanelHeightSync();
        if (!sidebar) return;

        syncMemoryChatPanelHeight();
        requestAnimationFrame(syncMemoryChatPanelHeight);
        window.setTimeout(syncMemoryChatPanelHeight, 300);
        window.addEventListener('resize', syncMemoryChatPanelHeight);
        memoryChatPanelHeightResizeBound = true;

        if (typeof ResizeObserver === 'function') {
            memorySidebarResizeObserver = new ResizeObserver(syncMemoryChatPanelHeight);
            memorySidebarResizeObserver.observe(sidebar);
        }
    }

    function teardownMemoryChatPanelHeightSync() {
        if (memorySidebarResizeObserver) {
            memorySidebarResizeObserver.disconnect();
            memorySidebarResizeObserver = null;
        }
        if (memoryChatPanelHeightResizeBound) {
            window.removeEventListener('resize', syncMemoryChatPanelHeight);
            memoryChatPanelHeightResizeBound = false;
        }
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
        const label = document.querySelector("label[for='review-toggle-checkbox']");
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
        document.querySelectorAll('.tutorial-cascader-option[data-tutorial-home-all]').forEach(function (option) {
            option.textContent = getTutorialHomeAllResetLabel();
        });
        document.querySelectorAll('.tutorial-cascader-option[data-tutorial-day]').forEach(function (option) {
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
        const popup = document.querySelector('.tutorial-cascader-popup');
        const trigger = document.querySelector('.tutorial-cascader-trigger');
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
        const dayColumn = document.querySelector('.tutorial-cascader-day-column');
        const valueEl = document.querySelector('.tutorial-reset-value');
        if (!tutorialSelect || !tutorialResetBtn) return;

        const pageKey = String(tutorialSelect.value || '');
        if (pageKey !== 'home') {
            selectedTutorialDay = 0;
            selectedTutorialHomeAll = false;
        }
        if (dayColumn) {
            dayColumn.hidden = pageKey !== 'home';
        }
        document.querySelectorAll('.tutorial-cascader-option[data-tutorial-page]').forEach(function (option) {
            option.classList.toggle('is-selected', option.dataset.tutorialPage === pageKey);
        });
        document.querySelectorAll('.tutorial-cascader-option[data-tutorial-day]').forEach(function (option) {
            option.classList.toggle('is-selected', Number(option.dataset.tutorialDay) === selectedTutorialDay);
        });
        document.querySelectorAll('.tutorial-cascader-option[data-tutorial-home-all]').forEach(function (option) {
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

    async function getTutorialPromptResetHeaders() {
        const headers = { 'Content-Type': 'application/json' };
        const helper = window.nekoLocalMutationSecurity;
        if (helper && typeof helper.getMutationHeaders === 'function') {
            try {
                return Object.assign(headers, await helper.getMutationHeaders());
            } catch (error) {
                console.warn('[MemoryBrowser] 获取教程重置安全头失败，尝试页面配置:', error);
            }
        }

        try {
            const response = await fetch('/api/config/page_config', { cache: 'no-store' });
            if (!response.ok) return headers;
            const data = await response.json();
            if (data && typeof data.autostart_csrf_token === 'string' && data.autostart_csrf_token) {
                headers['X-CSRF-Token'] = data.autostart_csrf_token;
            }
        } catch (error) {
            console.warn('[MemoryBrowser] 读取页面配置失败，继续使用基础教程重置请求头:', error);
        }
        return headers;
    }

    async function resetHomeTutorialPromptStateViaApi(reason) {
        const normalizedReason = typeof reason === 'string' && reason.trim()
            ? reason.trim()
            : 'memory_browser_home_all_reset';
        const body = JSON.stringify({ reason: normalizedReason });
        const sendResetRequest = async () => fetch('/api/tutorial-prompt/reset', {
            method: 'POST',
            headers: await getTutorialPromptResetHeaders(),
            body,
        });

        let response = await sendResetRequest();
        if (response.status === 403 && window.nekoLocalMutationSecurity &&
            typeof window.nekoLocalMutationSecurity.refreshToken === 'function') {
            let shouldRetry = false;
            try {
                const payload = await response.clone().json();
                shouldRetry = payload && payload.error_code === 'csrf_validation_failed';
            } catch (_) {
                shouldRetry = false;
            }
            if (shouldRetry) {
                await window.nekoLocalMutationSecurity.refreshToken();
                response = await sendResetRequest();
            }
        }
        if (!response.ok) {
            throw new Error('tutorial prompt reset failed: ' + response.status);
        }
        return response.json();
    }

    async function resetHomeTutorialPromptState(reason) {
        if (window.universalTutorialManager && typeof window.universalTutorialManager.resetHomeTutorialPromptState === 'function') {
            return window.universalTutorialManager.resetHomeTutorialPromptState(reason);
        }
        return resetHomeTutorialPromptStateViaApi(reason);
    }

    async function resetSelectedTutorial() {
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
            await resetHomeTutorialPromptState('memory_browser_home_day_reset');
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
            await resetHomeTutorialPromptState('memory_browser_home_all_reset');
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
            await loadScriptOnce('/static/app-storage-location.js', function (script) {
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
        currentMemoryFile = null;
        currentCatName = '';
        chatData = [];
        memoryFileRequestId++;

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
        setReviewControlsEnabled(false);
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

    function ensureMemoryParticleCanvas() {
        if (!memoryParticleCanvas) {
            memoryParticleCanvas = document.createElement('canvas');
            memoryParticleCanvas.id = 'memory-particle-canvas';
            memoryParticleCanvas.className = 'memory-particle-canvas';
            memoryParticleCanvas.setAttribute('aria-hidden', 'true');
            document.body.appendChild(memoryParticleCanvas);
            memoryParticleContext = memoryParticleCanvas.getContext('2d');
        }
        ensureMemoryParticleResizeListener();
        resizeMemoryParticleCanvas();
        return memoryParticleCanvas;
    }

    function ensureMemoryParticleResizeListener() {
        if (memoryParticleCanvasResizeBound) return;
        window.addEventListener('resize', resizeMemoryParticleCanvas);
        memoryParticleCanvasResizeBound = true;
    }

    function resizeMemoryParticleCanvas() {
        if (!memoryParticleCanvas || !memoryParticleContext) return;
        const dpr = window.devicePixelRatio || 1;
        memoryParticleCanvas.width = Math.max(1, Math.floor(window.innerWidth * dpr));
        memoryParticleCanvas.height = Math.max(1, Math.floor(window.innerHeight * dpr));
        memoryParticleCanvas.style.width = window.innerWidth + 'px';
        memoryParticleCanvas.style.height = window.innerHeight + 'px';
        memoryParticleContext.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    function teardownMemoryParticleCanvas() {
        if (memoryParticleCanvasResizeBound) {
            window.removeEventListener('resize', resizeMemoryParticleCanvas);
            memoryParticleCanvasResizeBound = false;
        }
        cancelAnimationFrame(memoryParticleFrame);
        memoryParticleFrame = 0;
        memoryParticles = [];
        memoryDissolveInProgress = false;
        memoryDissolveRunId++;
        if (memoryParticleContext) {
            memoryParticleContext.clearRect(0, 0, window.innerWidth, window.innerHeight);
        }
        if (memoryParticleCanvas && memoryParticleCanvas.parentNode) {
            memoryParticleCanvas.parentNode.removeChild(memoryParticleCanvas);
        }
        memoryParticleCanvas = null;
        memoryParticleContext = null;
        setChatActionButtonsEnabled(true);
    }

    function randomBetween(min, max) {
        return min + Math.random() * (max - min);
    }

    function createMemoryParticle(x, y, color, delay) {
        const angle = randomBetween(-Math.PI * 0.92, -Math.PI * 0.08);
        const speed = randomBetween(0.8, 4.2);
        memoryParticles.push({
            x,
            y,
            vx: Math.cos(angle) * speed + randomBetween(-0.65, 0.65),
            vy: Math.sin(angle) * speed - randomBetween(0.2, 1.2),
            rotation: randomBetween(0, Math.PI),
            spin: randomBetween(-0.16, 0.16),
            size: randomBetween(2.2, 5.8),
            life: 0,
            maxLife: randomBetween(48, 86),
            delay: delay || 0,
            color,
            alpha: 1
        });
    }

    function roundedRectPath(context, x, y, width, height, radius) {
        const r = Math.min(radius, width / 2, height / 2);
        context.beginPath();
        context.moveTo(x + r, y);
        context.arcTo(x + width, y, x + width, y + height, r);
        context.arcTo(x + width, y + height, x, y + height, r);
        context.arcTo(x, y + height, x, y, r);
        context.arcTo(x, y, x + width, y, r);
        context.closePath();
    }

    function wrapParticleText(context, text, x, y, maxWidth, lineHeight) {
        const chars = Array.from(String(text || ''));
        let line = '';
        let cursorY = y;
        for (const char of chars) {
            const testLine = line + char;
            if (line && context.measureText(testLine).width > maxWidth) {
                context.fillText(line, x, cursorY);
                line = char;
                cursorY += lineHeight;
            } else {
                line = testLine;
            }
        }
        if (line) {
            context.fillText(line, x, cursorY);
        }
    }

    function sampleMemoryElementParticles(element, role, delay) {
        const rect = element.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) return;

        const offscreen = document.createElement('canvas');
        const scale = 0.7;
        offscreen.width = Math.max(1, Math.ceil(rect.width * scale));
        offscreen.height = Math.max(1, Math.ceil(rect.height * scale));
        const off = offscreen.getContext('2d');
        if (!off) return;
        off.scale(scale, scale);

        const computed = window.getComputedStyle(element);
        const foreground = computed.color || '#40c5f1';
        const isBubble = element.classList.contains('chat-bubble');
        const isDelete = element.classList.contains('delete-btn');
        const palette = role === 'ai'
            ? ['#40c5f1', '#96e8ff', '#f0f9ff', '#ffffff']
            : ['#40c5f1', '#e3f4ff', '#ffffff', '#b3e5fc'];

        if (isBubble || isDelete) {
            roundedRectPath(off, 1, 1, rect.width - 2, rect.height - 2, isDelete ? 999 : 20);
            off.fillStyle = computed.backgroundColor || (isDelete ? '#ff5252' : '#f0f9ff');
            off.fill();
            off.strokeStyle = isDelete ? 'rgba(255,255,255,0.2)' : '#e3f4ff';
            off.lineWidth = isDelete ? 0 : 2;
            if (!isDelete) off.stroke();
        }

        off.fillStyle = foreground;
        off.font = `${computed.fontWeight} ${computed.fontSize} ${computed.fontFamily}`;
        off.textBaseline = 'top';
        const startX = isBubble ? 18 : 0;
        const startY = isBubble ? 12 : 0;
        const maxWidth = Math.max(40, rect.width - (isBubble ? 36 : 0));
        const lineHeight = parseFloat(computed.lineHeight) || parseFloat(computed.fontSize) * 1.55;
        wrapParticleText(off, element.textContent || '', startX, startY, maxWidth, lineHeight);

        const image = off.getImageData(0, 0, offscreen.width, offscreen.height);
        const step = isBubble ? 5 : 4;
        for (let y = 0; y < image.height; y += step) {
            for (let x = 0; x < image.width; x += step) {
                const alpha = image.data[(y * image.width + x) * 4 + 3];
                if (alpha > 16 && Math.random() > 0.46) {
                    const color = isDelete
                        ? (Math.random() > 0.42 ? '#ff8a8a' : '#ffffff')
                        : palette[Math.floor(Math.random() * palette.length)];
                    createMemoryParticle(rect.left + x / scale, rect.top + y / scale, color, delay + randomBetween(0, 12));
                }
            }
        }
    }

    function addMemoryItemParticles(item, sequence) {
        ensureMemoryParticleCanvas();
        const delay = (sequence || 0) * 7;
        const role = item.getAttribute('data-role') || '';
        item.querySelectorAll('.chat-speaker, .chat-bubble, .chat-time, .delete-btn').forEach(node => {
            sampleMemoryElementParticles(node, role, delay);
        });

        const rect = item.getBoundingClientRect();
        if (rect.width > 0) {
            for (let i = 0; i < 28; i++) {
                createMemoryParticle(
                    randomBetween(rect.left + rect.width * 0.08, rect.right - rect.width * 0.1),
                    rect.top + randomBetween(0, 4),
                    i % 2 ? '#b3e5fc' : '#40c5f1',
                    delay + randomBetween(0, 16)
                );
            }
        }
    }

    function animateMemoryParticles() {
        if (!memoryParticleContext) return;
        memoryParticleContext.clearRect(0, 0, window.innerWidth, window.innerHeight);
        memoryParticles = memoryParticles.filter(particle => {
            if (particle.delay > 0) {
                particle.delay -= 1;
                return true;
            }

            particle.life += 1;
            const progress = particle.life / particle.maxLife;
            particle.vy += 0.018;
            particle.vx *= 0.992;
            particle.x += particle.vx;
            particle.y += particle.vy;
            particle.rotation += particle.spin;
            particle.alpha = Math.max(0, 1 - progress);

            memoryParticleContext.save();
            memoryParticleContext.globalAlpha = particle.alpha;
            memoryParticleContext.translate(particle.x, particle.y);
            memoryParticleContext.rotate(particle.rotation);
            memoryParticleContext.fillStyle = particle.color;
            memoryParticleContext.shadowColor = 'rgba(64, 197, 241, 0.38)';
            memoryParticleContext.shadowBlur = 9 * particle.alpha;
            memoryParticleContext.fillRect(-particle.size / 2, -particle.size / 2, particle.size, particle.size);
            memoryParticleContext.restore();

            return particle.life < particle.maxLife;
        });

        if (memoryParticles.length) {
            memoryParticleFrame = requestAnimationFrame(animateMemoryParticles);
        } else {
            cancelAnimationFrame(memoryParticleFrame);
            memoryParticleFrame = 0;
        }
    }

    function startMemoryParticles() {
        if (!memoryParticleFrame) {
            memoryParticleFrame = requestAnimationFrame(animateMemoryParticles);
        }
    }

    function collapseMemoryItem(item) {
        const height = item.offsetHeight;
        item.style.height = height + 'px';
        item.classList.add('is-collapsing');
        requestAnimationFrame(() => {
            item.style.height = '0px';
        });
    }

    function setChatActionButtonsEnabled(enabled) {
        const clearBtn = document.getElementById('clear-memory-btn');
        if (clearBtn) clearBtn.disabled = !enabled;
        document.querySelectorAll('#memory-chat-edit .delete-btn').forEach(btn => {
            btn.disabled = !enabled;
        });
    }

    function dissolveChatItems(items, onComplete) {
        const targets = (items || []).filter(Boolean);
        if (!targets.length) {
            if (typeof onComplete === 'function') onComplete();
            return;
        }
        const reduceMotion = window.matchMedia
            && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        const maxStaggeredItems = 6;
        const maxParticleItems = 40;

        if (reduceMotion || targets.length > maxParticleItems) {
            if (typeof onComplete === 'function') onComplete();
            return;
        }

        memoryDissolveInProgress = true;
        const dissolveRunId = ++memoryDissolveRunId;
        setChatActionButtonsEnabled(false);
        memoryParticles = [];
        cancelAnimationFrame(memoryParticleFrame);
        memoryParticleFrame = 0;

        targets.forEach((item, sequence) => {
            window.setTimeout(() => {
                if (dissolveRunId !== memoryDissolveRunId) return;
                addMemoryItemParticles(item, sequence);
                item.classList.add('is-dissolving');
                startMemoryParticles();
                window.setTimeout(() => {
                    if (dissolveRunId !== memoryDissolveRunId) return;
                    collapseMemoryItem(item);
                }, 620);
            }, Math.min(sequence, maxStaggeredItems) * 145);
        });

        window.setTimeout(() => {
            if (dissolveRunId !== memoryDissolveRunId) return;
            if (typeof onComplete === 'function') onComplete();
            memoryDissolveInProgress = false;
            setChatActionButtonsEnabled(true);
        }, 1120 + Math.min(targets.length, maxStaggeredItems) * 145);
    }

    async function loadMemoryFileList() {
        const ul = document.getElementById('memory-file-list');
        ul.innerHTML = `<li style="color:#888; padding: 8px;">${window.t ? window.t('memory.loading') : '加载中...'}</li>`;
        try {
            const resp = await fetch('/api/memory/recent_files');
            const data = await resp.json();
            ul.innerHTML = '';
            if (data.files && data.files.length) {
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
                data.files.forEach(f => {
                    // 提取猫娘名
                    let match = f.match(/^recent_(.+)\.json$/);
                    let catName = match ? match[1] : f;
                    const li = document.createElement('li');
                    // 按钮样式（使用 DOM API，避免插入未转义内容）
                    const btn = document.createElement('button');
                    btn.className = 'cat-btn';
                    btn.setAttribute('data-filename', f);
                    btn.setAttribute('data-catname', catName);
                    btn.textContent = catName;
                    btn.addEventListener('click', () => selectMemoryFile(f, li, catName));
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
                            selectMemoryFile(f, li, catName);
                        }, 100);
                    }
                });
            } else {
                ul.innerHTML = `<li style="color:#888; padding: 8px;">${window.t ? window.t('memory.noFiles') : '无文件'}</li>`;
            }
        } catch (e) {
            ul.innerHTML = `<li style="color:#e74c3c; padding: 8px;">${window.t ? window.t('memory.loadFailed') : '加载失败'}</li>`;
        } finally {
            requestAnimationFrame(syncMemoryChatPanelHeight);
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
        if (button) {
            button.disabled = !(currentCatName && input && input.files && input.files.length);
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
            return {
                character_name: targetCharacter,
                source_format: format ? format.value : 'auto',
                archive_b64: bytesToBase64(await zipFiles[0].arrayBuffer())
            };
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
        return {
            character_name: targetCharacter,
            source_format: format ? format.value : 'auto',
            files: files
        };
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

    async function importExternalMemory() {
        const button = document.getElementById('external-memory-import-btn');
        if (button) button.disabled = true;
        try {
            const targetCharacter = currentCatName;
            setExternalImportStatus(translate('memory.externalImportReading', 'Reading external memory...'), 'working');
            const payload = await buildExternalImportPayload(targetCharacter);
            const previewResponse = await fetch('/api/memory/external_import/preview', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
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
            setExternalImportStatus(translate('memory.externalImportWorking', 'Importing memory...'), 'working');
            const commitResponse = await fetch('/api/memory/external_import/commit', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const result = await commitResponse.json();
            if (!commitResponse.ok || !result.success) {
                if (result.error_code === 'external_import_partial') {
                    const partial = result.partial_import || {};
                    throw new Error(translate(
                        'memory.externalImportPartial',
                        'The import stopped after {{persona}} persona entries were saved. Retry to finish; duplicates will be skipped.',
                        { persona: partial.added_persona || 0 }
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
            broadcastExternalMemoryEdited(result.character_name);
        } catch (error) {
            setExternalImportStatus(
                String(error && error.message ? error.message : translate('memory.externalImportFailed', 'Import failed.')),
                'error'
            );
        } finally {
            updateExternalImportButton();
        }
    }

    function renderChatEdit() {
        const div = document.getElementById('memory-chat-edit');
        // 清空并使用 DOM API 渲染每一条消息，避免将未转义的用户数据插入到 HTML 中
        while (div.firstChild) div.removeChild(div.firstChild);
        chatData.forEach((msg, i) => {
            const container = document.createElement('div');
            container.className = 'chat-item';
            container.setAttribute('data-chat-index', String(i));
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
                ta.addEventListener('change', function () {
                    bodyValue = this.value;
                    commitMemo();
                });
                contentWrapper.appendChild(ta);

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
                    olderTa.addEventListener('change', function () {
                        olderValue = this.value;
                        commitMemo();
                    });
                    contentWrapper.appendChild(olderTa);
                }
            } else if (msg.role === 'ai') {
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
                const delBtn = document.createElement('button');
                delBtn.className = 'delete-btn';
                delBtn.textContent = window.t ? window.t('memory.delete') : '删除';
                delBtn.addEventListener('click', function () { deleteChat(i); });
                deleteWrapper.appendChild(delBtn);
                container.appendChild(deleteWrapper);
            } else {
                const contentWrapper = document.createElement('div');
                contentWrapper.className = 'chat-item-content';
                container.appendChild(contentWrapper);

                const speaker = document.createElement('div');
                speaker.className = 'chat-speaker';
                speaker.textContent = window.t ? window.t('memory.me') : '我：';
                contentWrapper.appendChild(speaker);

                const bubble = document.createElement('div');
                bubble.className = 'chat-bubble';
                bubble.textContent = msg.text;
                contentWrapper.appendChild(bubble);

                const deleteWrapper = document.createElement('div');
                deleteWrapper.className = 'delete-btn-wrapper';
                const delBtn = document.createElement('button');
                delBtn.className = 'delete-btn';
                delBtn.textContent = window.t ? window.t('memory.delete') : '删除';
                delBtn.addEventListener('click', function () { deleteChat(i); });
                deleteWrapper.appendChild(delBtn);
                container.appendChild(deleteWrapper);
            }

            div.appendChild(container);
        });
    }

    function deleteChat(idx) {
        if (memoryDissolveInProgress) return;
        const item = document.querySelector(`#memory-chat-edit .chat-item[data-chat-index="${idx}"]`);
        if (!item || idx < 0 || idx >= chatData.length) return;
        chatData.splice(idx, 1);
        dissolveChatItems([item], renderChatEdit);
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
    async function selectMemoryFile(filename, li, catName) {
        const requestId = ++memoryFileRequestId;
        currentMemoryFile = filename;
        currentCatName = catName || (li ? li.getAttribute('data-catname') : '');
        updateExternalImportButton();
        Array.from(document.getElementById('memory-file-list').children).forEach(x => x.classList.remove('selected'));
        if (li) li.classList.add('selected');
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
                editDiv.innerHTML = '<div style="color:#888; padding: 20px; text-align: center;">' + (window.t ? window.t('memory.noChatContent') : '无聊天内容') + '</div>';
            }
        } catch (e) {
            if (requestId !== memoryFileRequestId) {
                return;
            }
            chatData = [];
            editDiv.innerHTML = '<div style="color:#e74c3c; padding: 20px; text-align: center;">' + (window.t ? window.t('memory.loadFailed') : '加载失败') + '</div>';
        }
    }
    document.getElementById('save-memory-btn').onclick = async function () {
        if (!currentMemoryFile) {
            showSaveStatus(window.t ? window.t('memory.pleaseSelectFile') : '请先选择文件', false);
            return;
        }
        // 处理备忘录为空的情况
        const memoPrefix = window.t ? window.t('memory.previousMemo') : '先前对话的备忘录: ';
        const memoNone = window.t ? window.t('memory.memoNone') : '无。';
        chatData.forEach(msg => {
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
                body: JSON.stringify({ filename: currentMemoryFile, chat: chatData })
            });
            const data = await resp.json();
            if (data.success) {
                showSaveStatus(window.t ? window.t('memory.saveSuccess') : '保存成功', true);

                // 通知父窗口刷新对话上下文
                if (data.need_refresh) {
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
            } else {
                const errorMsg = data.error || (window.t ? window.t('common.unknownError') : '未知错误');
                showSaveStatus(window.t ? window.t('memory.saveFailed', { error: errorMsg }) : '保存失败：' + errorMsg, false);
            }
        } catch (e) {
            showSaveStatus(window.t ? window.t('memory.saveFailedGeneral') : '保存失败', false);
        }
    };
    document.getElementById('clear-memory-btn').onclick = function () {
        if (memoryDissolveInProgress) return;
        const itemsToDissolve = Array.from(
            document.querySelectorAll('#memory-chat-edit .chat-item[data-role="human"], #memory-chat-edit .chat-item[data-role="ai"]')
        );
        if (!itemsToDissolve.length) {
            showSaveStatus(window.t ? window.t('memory.clearedChatKeptMemo') : '已清空对话记录，备忘录已保留（未保存）', false);
            return;
        }
        // 只清空对话轮次（用户 / AI）；system＝先前对话的备忘录，一律保留
        chatData = chatData.filter(msg => msg && msg.role !== 'human' && msg.role !== 'ai');
        dissolveChatItems(itemsToDissolve, function () {
            renderChatEdit();
            showSaveStatus(window.t ? window.t('memory.clearedChatKeptMemo') : '已清空对话记录，备忘录已保留（未保存）', false);
        });
    };
    function showSaveStatus(msg, success) {
        const el = document.getElementById('save-status');
        el.textContent = msg;
        el.style.color = success ? '#27ae60' : '#e74c3c';
        if (success) {
            setTimeout(() => { el.textContent = ''; }, 3000);
        }
    }
    function closeMemoryBrowser() {
        teardownMemoryChatPanelHeightSync();
        teardownMemoryParticleCanvas();
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
    // 将函数暴露到全局作用域，供 HTML onclick 调用
    window.closeMemoryBrowser = closeMemoryBrowser;
    window.addEventListener('pagehide', function () {
        teardownMemoryChatPanelHeightSync();
        teardownMemoryParticleCanvas();
    });
    window.addEventListener('beforeunload', function () {
        teardownMemoryChatPanelHeightSync();
        teardownMemoryParticleCanvas();
    });
    // 页面加载时隐藏保存按钮
    document.addEventListener('DOMContentLoaded', async function () {
        initMemoryChatPanelHeightSync();
        const storagePanelState = await initStorageLocationPanel();
        if (storagePanelState && storagePanelState.limited) {
            renderMemoryBrowserLimitedState(storagePanelState);
        } else {
            setReviewControlsEnabled(true);
            await loadMemoryFileList();
            if (!currentCatName) {
                try {
                    const response = await fetch('/api/characters/current_catgirl');
                    const current = await response.json();
                    currentCatName = current.current_catgirl || '';
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
            });
        }
        window.addEventListener('localechange', function () {
            refreshTutorialCascaderDayLabels();
            syncTutorialResetCascader();
            syncExternalMemoryFormatDropdown();
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
                    setExternalMemoryFormatOpen(!(externalFormatPopup && !externalFormatPopup.hidden));
                });
            }
            if (externalFormatPopup) {
                externalFormatPopup.addEventListener('click', function (event) {
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
        if (tutorialSelect && tutorialResetBtn) {
            refreshTutorialCascaderDayLabels();
            syncTutorialResetCascader();
            const trigger = document.querySelector('.tutorial-cascader-trigger');
            const popup = document.querySelector('.tutorial-cascader-popup');
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
                const cascader = document.getElementById('tutorial-reset-cascader');
                if (cascader && !cascader.contains(event.target)) {
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
