(function () {
    'use strict';

    const PARENT_ORIGIN = window.location.origin;
    let currentMemoryFile = null;
    let chatData = [];
    let currentCatName = '';
    let memoryFileRequestId = 0;
    let storageLocationState = {
        bootstrap: null,
        blockingReason: '',
        loadFailed: false,
        limited: false
    };
    let storagePreflightState = null;
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
        const canRestart = !!(
            storagePreflightState
            && storagePreflightState.ok === true
            && storagePreflightState.result === 'restart_required'
            && !storagePreflightState.blocking_error_code
            && !storagePreflightState.blocking_error_message
        );
        restartBtn.hidden = !canRestart;
        restartBtn.disabled = !canRestart;
    }

    function sleep(ms) {
        return new Promise(function (resolve) {
            window.setTimeout(resolve, ms);
        });
    }

    function setStoragePreflightBusy(busy) {
        const btn = document.getElementById('storage-location-preflight-btn');
        if (btn) {
            btn.disabled = !!busy;
        }
        const pickBtn = document.getElementById('storage-location-pick-btn');
        if (pickBtn) {
            pickBtn.disabled = !!busy;
        }
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
        setStoragePreflightResult('', '');
        renderStorageRestartButton();
        modal.hidden = false;
    }

    function closeStorageLocationManager() {
        const modal = document.getElementById('storage-location-modal');
        if (modal) {
            modal.hidden = true;
        }
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
            const input = document.getElementById('storage-target-root-input');
            if (input) {
                input.value = selectedRoot;
            }
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
            translate('memory.storagePreflightReady', '预检通过。确认后应用需要关闭，并在关闭后迁移数据。'),
            translate('memory.storagePreflightTarget', '目标位置：{{path}}', {
                path: String(payload.target_root || payload.selected_root || '')
            })
        ];
        if (payload.requires_existing_target_confirmation) {
            lines.push(payload.existing_target_confirmation_message || translate('memory.storageExistingTargetWarning', '目标位置已经包含现有数据，后续确认迁移前需要二次确认。'));
        }
        return lines.filter(Boolean).join('\n');
    }

    async function runStorageLocationPreflight() {
        const input = document.getElementById('storage-target-root-input');
        const selectedRoot = input ? String(input.value || '').trim() : '';
        if (!selectedRoot) {
            setStoragePreflightResult(translate('memory.storageTargetRequired', '请先选择或输入目标位置'), 'error');
            return;
        }
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
        } catch (e) {
            console.warn('[MemoryBrowser] storage location preflight failed:', e);
            storagePreflightState = null;
            setStoragePreflightResult(String(e && e.message ? e.message : translate('memory.storagePreflightFailed', '预检失败')), 'error');
            renderStorageRestartButton();
        } finally {
            setStoragePreflightBusy(false);
        }
    }

    async function restartWithStorageLocation() {
        if (!storagePreflightState || storagePreflightState.result !== 'restart_required') {
            setStoragePreflightResult(translate('memory.storagePreflightRequired', '请先完成预检'), 'error');
            renderStorageRestartButton();
            return;
        }
        const selectedRoot = String(storagePreflightState.selected_root || storagePreflightState.target_root || '').trim();
        if (!selectedRoot) {
            setStoragePreflightResult(translate('memory.storagePreflightFailed', '预检失败'), 'error');
            return;
        }

        let confirmExistingTargetContent = false;
        if (storagePreflightState.requires_existing_target_confirmation) {
            const message = storagePreflightState.existing_target_confirmation_message
                || translate('memory.storageExistingTargetWarning', '目标位置已经包含现有数据，后续确认迁移前需要二次确认。');
            if (!window.confirm(message)) {
                return;
            }
            confirmExistingTargetContent = true;
        }

        const restartBtn = document.getElementById('storage-location-restart-btn');
        if (restartBtn) {
            restartBtn.disabled = true;
        }
        let restartAccepted = false;
        setStoragePreflightBusy(true);
        setStoragePreflightResult(translate('memory.storageRestartStarting', '正在准备关闭并迁移...'), 'success');
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
                throw new Error(storageErrorMessage(payload, translate('memory.storageRestartFailed', '关闭并迁移请求失败')));
            }
            restartAccepted = true;
            setStoragePreflightResult(translate('memory.storageRestartInitiated', '已请求关闭并迁移。应用即将进入维护状态，请等待重启完成。'), 'success');
            notifyStorageRestartInitiated(payload, selectedRoot);
            storagePreflightState = null;
            const input = document.getElementById('storage-target-root-input');
            if (input) {
                input.disabled = true;
            }
            renderStorageRestartButton();
            await closeStorageManagerAfterRestartNotice(payload);
        } catch (e) {
            console.warn('[MemoryBrowser] storage location restart failed:', e);
            setStoragePreflightResult(String(e && e.message ? e.message : translate('memory.storageRestartFailed', '关闭并迁移请求失败')), 'error');
            renderStorageRestartButton();
        } finally {
            if (!restartAccepted) {
                setStoragePreflightBusy(false);
            }
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
        }
    }

    function renderChatEdit() {
        const div = document.getElementById('memory-chat-edit');
        // 清空并使用 DOM API 渲染每一条消息，避免将未转义的用户数据插入到 HTML 中
        while (div.firstChild) div.removeChild(div.firstChild);
        chatData.forEach((msg, i) => {
            const container = document.createElement('div');
            container.className = 'chat-item';

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

                const ta = document.createElement('textarea');
                ta.className = 'memo-textarea';
                ta.value = text;
                ta.addEventListener('change', function () { updateSystemContent(i, this.value); });
                contentWrapper.appendChild(ta);
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
        chatData.splice(idx, 1);
        renderChatEdit();
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
        // 只清空对话轮次（用户 / AI）；system＝先前对话的备忘录，一律保留
        chatData = chatData.filter(msg => msg && msg.role !== 'human' && msg.role !== 'ai');
        renderChatEdit();
        showSaveStatus(window.t ? window.t('memory.clearedChatKeptMemo') : '已清空对话记录，备忘录已保留（未保存）', false);
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
    // 页面加载时隐藏保存按钮
    document.addEventListener('DOMContentLoaded', async function () {
        const storagePanelState = await initStorageLocationPanel();
        if (storagePanelState && storagePanelState.limited) {
            renderMemoryBrowserLimitedState(storagePanelState);
        } else {
            setReviewControlsEnabled(true);
            loadMemoryFileList();
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
            });
        }

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
        const preflightStorageBtn = document.getElementById('storage-location-preflight-btn');
        if (preflightStorageBtn) {
            preflightStorageBtn.addEventListener('click', function () {
                runStorageLocationPreflight();
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
                restartWithStorageLocation();
            });
        }

        // 监听新手引导重置下拉框变化
        const tutorialSelect = document.getElementById('tutorial-reset-select');
        const tutorialResetBtn = document.getElementById('tutorial-reset-btn');
        if (tutorialSelect && tutorialResetBtn) {
            // 根据下拉框当前值初始化按钮状态（支持浏览器恢复的表单状态）
            tutorialResetBtn.disabled = !tutorialSelect.value;

            // 监听下拉框变化
            tutorialSelect.addEventListener('change', function() {
                // 当选择非空值时启用按钮，否则禁用
                tutorialResetBtn.disabled = !this.value;
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

})();
