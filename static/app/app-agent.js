/**
 * app-agent.js — Agent integration module
 * Extracted from app.js lines 6786-8532.
 *
 * Provides:
 *   - Agent popup state machine (AgentPopupState / agentStateMachine)
 *   - Agent health check / capability check helpers
 *   - Agent checkbox binding (hands over to static/js/agent_ui_v2.js)
 *   - Agent task HUD polling integration
 *   - Quota / content-filter popup helpers
 *
 * Globals exposed (backward compat):
 *   window.agentStateMachine
 *   window.stopAgentAvailabilityCheck
 *   window.startAgentTaskPolling
 *   window.stopAgentTaskPolling
 *   window.checkAndToggleTaskHUD
 *   window.appAgent  (module namespace)
 */
(function () {
    'use strict';

    const mod = {};
    // Shared state & constants (populated by app-state.js)
    // const S = window.appState;
    // const C = window.appConst;

    /**
     * Helper: find agent checkbox across Live2D / VRM / MMD / PNGTuber prefixes.
     * Only one model type is active at a time, so the first found wins.
     */
    function getAgentEl(suffix) {
        return document.getElementById('live2d-agent-' + suffix)
            || document.getElementById('vrm-agent-' + suffix)
            || document.getElementById('mmd-agent-' + suffix)
            || document.getElementById('pngtuber-agent-' + suffix);
    }

    // ====================================================================
    // Agent Popup State enum
    // ====================================================================
    const AgentPopupState = {
        IDLE: 'IDLE',
        CHECKING: 'CHECKING',
        ONLINE: 'ONLINE',
        OFFLINE: 'OFFLINE',
        PROCESSING: 'PROCESSING'
    };
    mod.AgentPopupState = AgentPopupState;

    // ====================================================================
    // Agent State Machine
    // ====================================================================
    const agentStateMachine = {
        _state: AgentPopupState.IDLE,
        _operationSeq: 0,
        _checkSeq: 0,
        _lastCheckTime: 0,
        _cachedServerOnline: null,
        _cachedFlags: null,
        _popupOpen: false,
        _checkLock: false,

        MIN_CHECK_INTERVAL: 3000,

        getState() { return this._state; },
        nextSeq() { return ++this._operationSeq; },
        isSeqExpired(seq) { return seq !== this._operationSeq; },
        nextCheckSeq() { return ++this._checkSeq; },
        getCheckSeq() { return this._checkSeq; },
        isCheckSeqExpired(seq) { return seq !== this._checkSeq; },

        transition(newState, reason) {
            const oldState = this._state;
            if (oldState === newState) return;
            this._state = newState;
            console.log(`[AgentStateMachine] ${oldState} -> ${newState} (${reason})`);
            this._updateUI();
        },

        openPopup() {
            this._popupOpen = true;
            if (this._state === AgentPopupState.IDLE) {
                this.transition(AgentPopupState.CHECKING, 'popup opened');
            }
        },

        closePopup() {
            this._popupOpen = false;
            const masterCheckbox = getAgentEl('master');
            if (this._state !== AgentPopupState.PROCESSING && (!masterCheckbox || !masterCheckbox.checked)) {
                this.transition(AgentPopupState.IDLE, 'popup closed');
                window.stopAgentAvailabilityCheck();
            }
        },

        startOperation() {
            this.transition(AgentPopupState.PROCESSING, 'user operation started');
            return this.nextSeq();
        },

        endOperation(success, serverOnline = true) {
            if (this._state !== AgentPopupState.PROCESSING) return;
            if (serverOnline) {
                this.transition(AgentPopupState.ONLINE, success ? 'operation success' : 'operation failed');
            } else {
                this.transition(AgentPopupState.OFFLINE, 'server offline');
            }
        },

        canCheck() {
            if (this._checkLock) return false;
            const now = Date.now();
            return (now - this._lastCheckTime) >= this.MIN_CHECK_INTERVAL;
        },

        recordCheck() {
            this._checkLock = true;
            this._lastCheckTime = Date.now();
        },

        releaseCheckLock() {
            this._checkLock = false;
        },

        updateCache(serverOnline, flags) {
            this._cachedServerOnline = serverOnline;
            if (flags) this._cachedFlags = flags;
        },

        isAgentActive() {
            const f = this._cachedFlags;
            if (!f) return false;
            const master = !!f.agent_enabled;
            const child = !!(f.computer_use_enabled || f.browser_use_enabled || f.user_plugin_enabled);
            return master && child;
        },

        _updateUI() {
            const master = getAgentEl('master');
            const keyboard = getAgentEl('keyboard');
            const browser = getAgentEl('browser');
            const userPlugin = getAgentEl('user-plugin');
            const status = getAgentEl('status');

            const syncUI = (cb) => {
                if (cb && typeof cb._updateStyle === 'function') cb._updateStyle();
            };

            switch (this._state) {
                case AgentPopupState.IDLE:
                    if (master) { master.disabled = true; master.title = ''; syncUI(master); }
                    if (keyboard) { keyboard.disabled = true; keyboard.checked = false; keyboard.title = ''; syncUI(keyboard); }
                    if (browser) { browser.disabled = true; browser.checked = false; browser.title = ''; syncUI(browser); }
                    if (userPlugin) { userPlugin.disabled = true; userPlugin.checked = false; userPlugin.title = ''; syncUI(userPlugin); }
                    break;

                case AgentPopupState.CHECKING:
                    if (master) {
                        master.disabled = true;
                        master.title = window.t ? window.t('settings.toggles.checking') : '\u67e5\u8be2\u4e2d...';
                        syncUI(master);
                    }
                    if (keyboard) {
                        keyboard.disabled = true;
                        keyboard.title = window.t ? window.t('settings.toggles.checking') : '\u67e5\u8be2\u4e2d...';
                        syncUI(keyboard);
                    }
                    if (browser) {
                        browser.disabled = true;
                        browser.title = window.t ? window.t('settings.toggles.checking') : '\u67e5\u8be2\u4e2d...';
                        syncUI(browser);
                    }
                    if (userPlugin) {
                        userPlugin.disabled = true;
                        userPlugin.title = window.t ? window.t('settings.toggles.checking') : '\u67e5\u8be2\u4e2d...';
                        syncUI(userPlugin);
                    }
                    if (status) status.textContent = window.t ? window.t('agent.status.connecting') : 'Agent\u670d\u52a1\u5668\u8fde\u63a5\u4e2d...';
                    break;

                case AgentPopupState.ONLINE:
                    if (master) {
                        master.disabled = false;
                        master.title = window.t ? window.t('settings.toggles.agentMaster') : 'Agent\u603b\u5f00\u5173';
                        syncUI(master);
                    }
                    break;

                case AgentPopupState.OFFLINE:
                    if (master) {
                        master.disabled = true;
                        master.checked = false;
                        master.title = window.t ? window.t('settings.toggles.serverOffline') : 'Agent\u670d\u52a1\u5668\u672a\u542f\u52a8';
                        syncUI(master);
                    }
                    if (keyboard) { keyboard.disabled = true; keyboard.checked = false; syncUI(keyboard); }
                    if (browser) { browser.disabled = true; browser.checked = false; syncUI(browser); }
                    if (status) status.textContent = window.t ? window.t('settings.toggles.serverOffline') : 'Agent\u670d\u52a1\u5668\u672a\u542f\u52a8';
                    if (userPlugin) { userPlugin.disabled = true; userPlugin.checked = false; syncUI(userPlugin); }
                    break;

                case AgentPopupState.PROCESSING:
                    if (master) { master.disabled = true; syncUI(master); }
                    if (keyboard) { keyboard.disabled = true; syncUI(keyboard); }
                    if (browser) { browser.disabled = true; syncUI(browser); }
                    if (userPlugin) { userPlugin.disabled = true; syncUI(userPlugin); }
                    break;
            }
        }
    };

    // Expose state machine globally
    window.agentStateMachine = agentStateMachine;
    window._agentStatusSnapshot = window._agentStatusSnapshot || null;

    // ====================================================================
    // Module-level state
    // ====================================================================
    let agentCheckInterval = null;

    // ====================================================================
    // Floating agent status helper
    // ====================================================================
    function setFloatingAgentStatus(msg, taskStatus) {
        ['live2d-agent-status', 'vrm-agent-status', 'mmd-agent-status', 'pngtuber-agent-status'].forEach(id => {
            const statusEl = document.getElementById(id);
            if (statusEl) {
                statusEl.textContent = msg || '';
                // 移除 data-i18n 防止语言刷新覆盖动态状态文本
                statusEl.removeAttribute('data-i18n');
                const colorMap = {
                    completed: '#52c41a',
                    partial: '#faad14',
                    failed: '#ff4d4f',
                };
                if (taskStatus && colorMap[taskStatus]) {
                    statusEl.style.color = colorMap[taskStatus];
                    clearTimeout(statusEl._statusResetTimer);
                    statusEl._statusResetTimer = setTimeout(() => {
                        statusEl.style.color = 'var(--neko-popup-accent, #2a7bc4)';
                    }, 6000);
                } else {
                    clearTimeout(statusEl._statusResetTimer);
                    statusEl.style.color = 'var(--neko-popup-accent, #2a7bc4)';
                }
            }
        });
    }
    mod.setFloatingAgentStatus = setFloatingAgentStatus;

    // ====================================================================
    // Content filter modal
    // ====================================================================
    let _contentFilterModalOpen = false;
    let _contentFilterModalCooldownUntil = 0;

    function _isContentFilterError(text) {
        if (!text) return false;
        const s = String(text).toLowerCase();
        return (
            s.includes('content_filter') ||
            s.includes('data_inspection_failed') ||
            s.includes('datainspectionfailed') ||
            s.includes('inappropriate content') ||
            s.includes('content filter') ||
            s.includes('responsible ai policy') ||
            s.includes('content management policy')
        );
    }

    function maybeShowContentFilterModal(rawMessage) {
        if (!_isContentFilterError(rawMessage)) return;
        if (typeof window.showAlert !== 'function') return;

        const now = Date.now();
        if (_contentFilterModalOpen || now < _contentFilterModalCooldownUntil) return;

        _contentFilterModalOpen = true;
        _contentFilterModalCooldownUntil = now + 5000;

        const title = window.t ? window.t('common.alert') : '\u63d0\u793a';
        const msg = window.t
            ? window.t('agent.contentFilterError')
            : 'Agent \u6d4f\u89c8\u7684\u7f51\u9875\u5185\u5bb9\u89e6\u53d1\u4e86 AI \u6a21\u578b\u7684\u5b89\u5168\u5ba1\u67e5\u8fc7\u6ee4\uff0c\u4efb\u52a1\u5df2\u4e2d\u6b62\u3002\u8fd9\u901a\u5e38\u53d1\u751f\u5728\u9875\u9762\u5305\u542b\u654f\u611f\u8bdd\u9898\u65f6\uff0c\u8bf7\u5c1d\u8bd5\u5176\u4ed6\u5173\u952e\u8bcd\u6216\u7f51\u7ad9\u3002';

        Promise.resolve(window.showAlert(msg, title))
            .catch(() => { /* ignore */ })
            .finally(() => {
                _contentFilterModalOpen = false;
            });
    }
    mod.maybeShowContentFilterModal = maybeShowContentFilterModal;

    // ====================================================================
    // Health check
    // ====================================================================
    async function checkToolServerHealth() {
        for (let i = 0; i < 3; i++) {
            try {
                const resp = await fetch('/api/agent/health');
                if (resp.ok) return true;
            } catch (e) {
                // continue retry
            }
            if (i < 2) {
                await new Promise(resolve => setTimeout(resolve, 350));
            }
        }
        return false;
    }
    mod.checkToolServerHealth = checkToolServerHealth;

    // ====================================================================
    // Capability check
    // ====================================================================
    async function checkCapability(kind, showError = true) {
        const apis = {
            computer_use: { url: '/api/agent/computer_use/availability', nameKey: 'keyboardControl' },
            browser_use: { url: '/api/agent/browser_use/availability', nameKey: 'browserUse' },
            user_plugin: { url: '/api/agent/user_plugin/availability', nameKey: 'userPlugin' },
        };
        const config = apis[kind];

        try {
            const r = await fetch(config.url);
            if (!r.ok) return false;
            const j = await r.json();
            if (!j.ready) {
                if (showError) {
                    const name = window.t ? window.t(`settings.toggles.${config.nameKey}`) : config.nameKey;
                    setFloatingAgentStatus(j.reasons?.[0] || (window.t ? window.t('settings.toggles.unavailable', { name }) : `${name}\u4e0d\u53ef\u7528`));
                }
                return false;
            }
            return true;
        } catch (e) {
            return false;
        }
    }
    mod.checkCapability = checkCapability;

    // ====================================================================
    // Stop availability check (agentStateMachine.closePopup still calls it)
    // ====================================================================
    window.stopAgentAvailabilityCheck = function () {
        if (agentCheckInterval) {
            clearInterval(agentCheckInterval);
            agentCheckInterval = null;
        }
    };

    // ====================================================================
    // setupAgentCheckboxListeners
    // ====================================================================
    const setupAgentCheckboxListeners = () => {
        // Agent UI v2 (static/js/agent_ui_v2.js): fully event-driven
        // single-store controller. It owns the master and sub toggles.
        if (typeof window.initAgentUiV2 === 'function') {
            try {
                window.initAgentUiV2();
            } catch (e) {
                console.warn('[App] initAgentUiV2 failed:', e);
            }
        }
    };
    mod.setupAgentCheckboxListeners = setupAgentCheckboxListeners;

    // ====================================================================
    // Agent Task HUD polling
    // ====================================================================
    let agentTaskPollingInterval = null;
    let agentTaskTimeUpdateInterval = null;

    function isGoodbyeAgentUiSuppressed() {
        try {
            if (typeof window.isNekoGoodbyeResourceSuspendingOrSuspended === 'function' &&
                window.isNekoGoodbyeResourceSuspendingOrSuspended()) {
                return true;
            }
            if (typeof window.isNekoGoodbyeModeActive === 'function' && window.isNekoGoodbyeModeActive()) {
                return true;
            }
        } catch (_) { /* ignore */ }
        return false;
    }

    window.startAgentTaskPolling = function () {
        console.trace('[App] startAgentTaskPolling');
        if (isGoodbyeAgentUiSuppressed()) {
            if (window.AgentHUD && window.AgentHUD.hideAgentTaskHUD) {
                window.AgentHUD.hideAgentTaskHUD();
            }
            return;
        }
        if (window.AgentHUD && window.AgentHUD.createAgentTaskHUD) {
            window.AgentHUD.createAgentTaskHUD();
            window.AgentHUD.showAgentTaskHUD();
        }

        if (agentTaskPollingInterval) return;

        console.log('[App] \u542f\u52a8 Agent \u4efb\u52a1\u72b6\u6001\u8f6e\u8be2');

        agentTaskTimeUpdateInterval = setInterval(updateTaskRunningTimes, 1000);
        agentTaskPollingInterval = agentTaskTimeUpdateInterval; // 复用 ID，使 stopAgentTaskPolling 能正确 clearInterval
    };

    window.stopAgentTaskPolling = function () {
        // 如果轮询已经停止，跳过重复清理
        if (!agentTaskPollingInterval && !agentTaskTimeUpdateInterval && !window._agentTaskTimeUpdateInterval) {
            // 仍然确保 HUD 隐藏（幂等操作）
            if (window.AgentHUD && window.AgentHUD.hideAgentTaskHUD &&
            (isGoodbyeAgentUiSuppressed() || !(window.NekoPluginViews && window.NekoPluginViews.hasContent()))) {
                window.AgentHUD.hideAgentTaskHUD();
            }
            return;
        }

        console.log('[App] 停止 Agent 任务状态轮询');

        if (agentTaskTimeUpdateInterval) {
            clearInterval(agentTaskTimeUpdateInterval);
            agentTaskTimeUpdateInterval = null;
        }
        // 同步清理 WebSocket 创建的全局定时器
        if (window._agentTaskTimeUpdateInterval) {
            clearInterval(window._agentTaskTimeUpdateInterval);
            window._agentTaskTimeUpdateInterval = null;
        }
        agentTaskPollingInterval = null;

        if (window.AgentHUD && window.AgentHUD.hideAgentTaskHUD &&
            (isGoodbyeAgentUiSuppressed() || !(window.NekoPluginViews && window.NekoPluginViews.hasContent()))) {
            window.AgentHUD.hideAgentTaskHUD();
        }
    };

    // ====================================================================
    // updateTaskRunningTimes
    // ====================================================================
    function updateTaskRunningTimes() {
        if (isGoodbyeAgentUiSuppressed()) {
            return;
        }
        const taskList = document.getElementById('agent-task-list');
        if (!taskList) {
            return;
        }

        const hasRunning = window._agentTaskMap && Array.from(window._agentTaskMap.values())
            .some(t => t.status === 'running' || t.status === 'queued' || t.status === 'waiting_user');
        if (!hasRunning) {
            if (agentTaskTimeUpdateInterval) {
                clearInterval(agentTaskTimeUpdateInterval);
                agentTaskTimeUpdateInterval = null;
                agentTaskPollingInterval = null;
            }
            // 同步清理 WebSocket 创建的全局定时器，避免重复间隔
            if (window._agentTaskTimeUpdateInterval) {
                clearInterval(window._agentTaskTimeUpdateInterval);
                window._agentTaskTimeUpdateInterval = null;
            }
            return;
        }

        const timeElements = taskList.querySelectorAll('[id^="task-time-"]');
        timeElements.forEach(timeEl => {
            const taskId = timeEl.id.replace('task-time-', '');
            const card = document.querySelector(`.task-card[data-task-id="${taskId}"]`);
            if (!card) return;

            const startTimeStr = card.dataset.startTime;
            if (startTimeStr) {
                const startTime = new Date(startTimeStr);
                const elapsed = Math.floor((Date.now() - startTime.getTime()) / 1000);
                const minutes = Math.floor(elapsed / 60);
                const seconds = elapsed % 60;
                timeEl.textContent = `\u23f1\ufe0f ${minutes}:${seconds.toString().padStart(2, '0')}`;
            }
        });
    }

    // ====================================================================
    // checkAndToggleTaskHUD
    // ====================================================================
    function checkAndToggleTaskHUD(event) {
        if (isGoodbyeAgentUiSuppressed()) {
            window.stopAgentTaskPolling();
            return;
        }
        const getEl = (ids) => {
            for (let id of ids) {
                const el = document.getElementById(id);
                if (el) return el;
            }
            return null;
        };

        const changedCheckbox = event && event.target && /^(live2d|vrm|mmd|pngtuber)-agent-taskhud$/.test(event.target.id)
            ? event.target
            : null;
        const taskhudCheckbox = changedCheckbox || getEl(['live2d-agent-taskhud', 'vrm-agent-taskhud', 'mmd-agent-taskhud', 'pngtuber-agent-taskhud']);
        let taskhudOn = true;
        try { taskhudOn = localStorage.getItem('neko-agent-taskhud-visible') !== 'false'; } catch (_) {}
        if (changedCheckbox) {
            taskhudOn = changedCheckbox.checked;
            try { localStorage.setItem('neko-agent-taskhud-visible', taskhudOn ? 'true' : 'false'); } catch (_) {}
        } else if (taskhudCheckbox && taskhudCheckbox.checked !== taskhudOn) {
            taskhudCheckbox.checked = taskhudOn;
            if (typeof taskhudCheckbox._updateStyle === 'function') taskhudCheckbox._updateStyle();
        }
        if (!taskhudOn) {
            window.stopAgentTaskPolling();
            return;
        }

        const masterCheckbox = getEl(['live2d-agent-master', 'vrm-agent-master', 'mmd-agent-master', 'pngtuber-agent-master']);
        const keyboardCheckbox = getEl(['live2d-agent-keyboard', 'vrm-agent-keyboard', 'mmd-agent-keyboard', 'pngtuber-agent-keyboard']);
        const browserCheckbox = getEl(['live2d-agent-browser', 'vrm-agent-browser', 'mmd-agent-browser', 'pngtuber-agent-browser']);
        const userPlugin = getEl(['live2d-agent-user-plugin', 'vrm-agent-user-plugin', 'mmd-agent-user-plugin', 'pngtuber-agent-user-plugin']);

        const domMaster = masterCheckbox ? masterCheckbox.checked : false;
        const domChild = (keyboardCheckbox && keyboardCheckbox.checked)
            || (browserCheckbox && browserCheckbox.checked)
            || (userPlugin && userPlugin.checked);

        const snap = window._agentStatusSnapshot;
        const machineFlags = window.agentStateMachine ? window.agentStateMachine._cachedFlags : null;

        const flags = (snap && snap.flags && Object.keys(snap.flags).length > 0) ? snap.flags : machineFlags;

        let optMaster = undefined;
        let optChild = undefined;
        if (window.agent_ui_v2_state && window.agent_ui_v2_state.optimistic) {
            const opt = window.agent_ui_v2_state.optimistic;
            if ('agent_enabled' in opt) optMaster = !!opt.agent_enabled;
            if ('computer_use_enabled' in opt || 'browser_use_enabled' in opt || 'user_plugin_enabled' in opt) {
                optChild = !!opt.computer_use_enabled || !!opt.browser_use_enabled || !!opt.user_plugin_enabled;
            }
        }

        let isMasterOn = false;
        let isChildOn = false;

        const isUiInteractive = masterCheckbox && !masterCheckbox.disabled;

        if (!isUiInteractive) {
            isMasterOn = optMaster !== undefined ? optMaster : (flags && !!flags.agent_enabled);
            isChildOn = optChild !== undefined ? optChild : (flags && !!(flags.computer_use_enabled || flags.browser_use_enabled || flags.user_plugin_enabled));
        } else {
            isMasterOn = optMaster !== undefined ? optMaster : domMaster;
            isChildOn = optChild !== undefined ? optChild : domChild;
        }

        if (isMasterOn && isChildOn) {
            console.log('[HUD] Starting polling. Master:', isMasterOn, 'Child:', isChildOn);
            window.startAgentTaskPolling();
        } else {
            const LINGER_MS = 10000;
            const now = Date.now();
            const hasActiveTasks = window._agentTaskMap && window._agentTaskMap.size > 0 &&
                Array.from(window._agentTaskMap.values()).some(t => {
                    if (t.status === 'running' || t.status === 'queued' || t.status === 'waiting_user') return true;
                    const isTerminal = t.status === 'completed' || t.status === 'failed' || t.status === 'cancelled' || t.status === 'blocked';
                    return isTerminal && t.terminal_at && (now - t.terminal_at < LINGER_MS);
                });
            if (hasActiveTasks) {
                // 开关已关但还有活跃任务，保持 HUD 可见
            } else {
                window.stopAgentTaskPolling();
            }
        }
    }

    window.checkAndToggleTaskHUD = checkAndToggleTaskHUD;
    mod.checkAndToggleTaskHUD = checkAndToggleTaskHUD;
    window.updateTaskRunningTimes = updateTaskRunningTimes;

    // ====================================================================
    // HUD sub-checkbox change listener binding
    // ====================================================================
    window.addEventListener('live2d-floating-buttons-ready', () => {
        const bindHUD = () => {
            const getEl = (ids) => {
                for (let id of ids) {
                    const el = document.getElementById(id);
                    if (el) return el;
                }
                return null;
            };

            const keyboardCheckbox = getEl(['live2d-agent-keyboard', 'vrm-agent-keyboard', 'mmd-agent-keyboard', 'pngtuber-agent-keyboard']);
            const browserCheckbox = getEl(['live2d-agent-browser', 'vrm-agent-browser', 'mmd-agent-browser', 'pngtuber-agent-browser']);
            const userPluginCheckbox = getEl(['live2d-agent-user-plugin', 'vrm-agent-user-plugin', 'mmd-agent-user-plugin', 'pngtuber-agent-user-plugin']);

            if (!keyboardCheckbox || !browserCheckbox) {
                setTimeout(bindHUD, 500);
                return;
            }

            keyboardCheckbox.removeEventListener('change', checkAndToggleTaskHUD);
            keyboardCheckbox.addEventListener('change', checkAndToggleTaskHUD);
            browserCheckbox.removeEventListener('change', checkAndToggleTaskHUD);
            browserCheckbox.addEventListener('change', checkAndToggleTaskHUD);
            if (userPluginCheckbox) {
                userPluginCheckbox.removeEventListener('change', checkAndToggleTaskHUD);
                userPluginCheckbox.addEventListener('change', checkAndToggleTaskHUD);
            }

            checkAndToggleTaskHUD();
            console.log('[App] Agent \u4efb\u52a1 HUD \u63a7\u5236\u5df2\u7ed1\u5b9a');
        };

        setTimeout(bindHUD, 100);
    });

    // ====================================================================
    // Floating buttons ready => bind agent checkbox listeners
    // ====================================================================
    window.addEventListener('live2d-floating-buttons-ready', () => {
        console.log('[App] \u6536\u5230\u6d6e\u52a8\u6309\u94ae\u5c31\u7eea\u4e8b\u4ef6\uff0c\u5f00\u59cb\u7ed1\u5b9aAgent\u5f00\u5173');
        setupAgentCheckboxListeners();
    });

    // ====================================================================
    // Expose module
    // ====================================================================
    window.appAgent = mod;
})();
