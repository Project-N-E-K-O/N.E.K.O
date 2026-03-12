/**
 * app-agent.js — Agent integration module
 * Extracted from app.js lines 6786-8532.
 *
 * Provides:
 *   - Agent popup state machine (AgentPopupState / agentStateMachine)
 *   - Agent health check / capability check helpers
 *   - Agent checkbox event listeners (master + sub toggles)
 *   - Agent task HUD polling integration
 *   - Quota / content-filter popup helpers
 *   - syncFlagsFromBackend / openAgentStatusPopupWhenEnabled
 *
 * Globals exposed (backward compat):
 *   window.agentStateMachine
 *   window.startAgentAvailabilityCheck
 *   window.stopAgentAvailabilityCheck
 *   window.startAgentTaskPolling
 *   window.stopAgentTaskPolling
 *   window.checkAndToggleTaskHUD
 *   window.syncAgentFlagsFromBackend
 *   window.openAgentStatusPopupWhenEnabled
 *   window.applyAgentStatusSnapshotToUI
 *   window.appAgent  (module namespace)
 */
(function () {
    'use strict';

    const mod = {};
    // Shared state & constants (populated by app-state.js)
    // const S = window.appState;
    // const C = window.appConst;

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
            const masterCheckbox = document.getElementById('live2d-agent-master');
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
            const master = document.getElementById('live2d-agent-master');
            const keyboard = document.getElementById('live2d-agent-keyboard');
            const browser = document.getElementById('live2d-agent-browser');
            const userPlugin = document.getElementById('live2d-agent-user-plugin');
            const status = document.getElementById('live2d-agent-status');

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
    let lastFlagsSyncTime = 0;
    const FLAGS_SYNC_INTERVAL = 3000;
    let connectionFailureCount = 0;
    let isAgentPopupOpen = false;

    // ====================================================================
    // Floating agent status helper
    // ====================================================================
    function setFloatingAgentStatus(msg, taskStatus) {
        ['live2d-agent-status', 'vrm-agent-status'].forEach(id => {
            const statusEl = document.getElementById(id);
            if (statusEl) {
                statusEl.textContent = msg || '';
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
    // Quota exceeded modal
    // ====================================================================
    let _agentQuotaModalOpen = false;
    let _agentQuotaModalCooldownUntil = 0;

    function _isAgentQuotaExceededMessage(text) {
        if (!text) return false;
        const s = String(text).toLowerCase();
        return (
            s.includes('\u514d\u8d39 agent \u6a21\u578b\u4eca\u65e5\u8bd5\u7528\u6b21\u6570\u5df2\u8fbe\u4e0a\u9650') ||
            s.includes('agent quota exceeded') ||
            (s.includes('agent') && s.includes('\u4e0a\u9650') && s.includes('\u8bd5\u7528'))
        );
    }

    function maybeShowAgentQuotaExceededModal(rawMessage) {
        if (!_isAgentQuotaExceededMessage(rawMessage)) return;
        if (typeof window.showAlert !== 'function') return;

        const now = Date.now();
        if (_agentQuotaModalOpen || now < _agentQuotaModalCooldownUntil) return;

        _agentQuotaModalOpen = true;
        _agentQuotaModalCooldownUntil = now + 3000;

        const title = window.t ? window.t('common.alert') : '\u63d0\u793a';
        const msg = window.t
            ? window.t('agent.quotaExceeded', { limit: 300 })
            : '\u514d\u8d39 Agent \u6a21\u578b\u4eca\u65e5\u8bd5\u7528\u6b21\u6570\u5df2\u8fbe\u4e0a\u9650\uff08300\u6b21\uff09\uff0c\u8bf7\u660e\u65e5\u518d\u8bd5\u3002';

        Promise.resolve(window.showAlert(msg, title))
            .catch(() => { /* ignore */ })
            .finally(() => {
                _agentQuotaModalOpen = false;
            });
    }
    mod.maybeShowAgentQuotaExceededModal = maybeShowAgentQuotaExceededModal;

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
            user_plugin: { url: '/api/agent/user_plugin/availability', nameKey: 'userPlugin' }
        };
        const config = apis[kind];
        if (!config) return false;

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
    // checkAgentCapabilities — polling loop body
    // ====================================================================
    const checkAgentCapabilities = async () => {
        const agentMasterCheckbox = document.getElementById('live2d-agent-master');
        const agentKeyboardCheckbox = document.getElementById('live2d-agent-keyboard');
        const agentBrowserCheckbox = document.getElementById('live2d-agent-browser');
        const agentUserPluginCheckbox = document.getElementById('live2d-agent-user-plugin');

        if (agentStateMachine.getState() === AgentPopupState.PROCESSING) {
            console.log('[App] \u72b6\u6001\u673a\u5904\u4e8ePROCESSING\u72b6\u6001\uff0c\u8df3\u8fc7\u8f6e\u8be2');
            return;
        }

        if (!agentMasterCheckbox || (!agentMasterCheckbox.checked && !agentStateMachine._popupOpen)) {
            console.log('[App] Agent\u603b\u5f00\u5173\u672a\u5f00\u542f\u4e14\u5f39\u7a97\u5df2\u5173\u95ed\uff0c\u505c\u6b62\u53ef\u7528\u6027\u8f6e\u8be2');
            window.stopAgentAvailabilityCheck();
            return;
        }

        if (!agentMasterCheckbox.checked) {
            if (!agentStateMachine.canCheck()) {
                if (agentStateMachine._cachedServerOnline === true) {
                    agentStateMachine.transition(AgentPopupState.ONLINE, 'cached online');
                } else if (agentStateMachine._cachedServerOnline === false) {
                    agentStateMachine.transition(AgentPopupState.OFFLINE, 'cached offline');
                }
                return;
            }

            agentStateMachine.recordCheck();
            try {
                const healthOk = await checkToolServerHealth();
                agentStateMachine.updateCache(healthOk, null);

                if (!agentStateMachine._popupOpen) {
                    console.log('[App] \u8f6e\u8be2\u68c0\u67e5\u5b8c\u6210\u4f46\u5f39\u7a97\u5df2\u5173\u95ed\uff0c\u8df3\u8fc7UI\u66f4\u65b0');
                    return;
                }

                if (healthOk) {
                    const wasOffline = agentStateMachine.getState() !== AgentPopupState.ONLINE;
                    agentStateMachine.transition(AgentPopupState.ONLINE, 'server online');
                    if (wasOffline) {
                        setFloatingAgentStatus(window.t ? window.t('agent.status.ready') : 'Agent\u670d\u52a1\u5668\u5c31\u7eea');
                    }
                    connectionFailureCount = 0;
                } else {
                    setFloatingAgentStatus(window.t ? window.t('settings.toggles.serverOffline') : 'Agent\u670d\u52a1\u5668\u672a\u542f\u52a8');
                    agentStateMachine.transition(AgentPopupState.OFFLINE, 'server offline');
                }
            } catch (e) {
                agentStateMachine.updateCache(false, null);
                if (agentStateMachine._popupOpen) {
                    agentStateMachine.transition(AgentPopupState.OFFLINE, 'check failed');
                }
            } finally {
                agentStateMachine.releaseCheckLock();
            }
            return;
        }

        // --- Master checkbox IS checked: full capability check ---
        const capabilityResults = {};
        let capabilityCheckFailed = false;

        const checks = [
            { id: 'live2d-agent-keyboard', capability: 'computer_use', flagKey: 'computer_use_enabled', nameKey: 'keyboardControl' },
            { id: 'live2d-agent-browser', capability: 'browser_use', flagKey: 'browser_use_enabled', nameKey: 'browserUse' },
            { id: 'live2d-agent-user-plugin', capability: 'user_plugin', flagKey: 'user_plugin_enabled', nameKey: 'userPlugin' }
        ];
        for (const { id, capability, flagKey, nameKey } of checks) {
            const cb = document.getElementById(id);
            if (!cb) continue;

            const name = window.t ? window.t(`settings.toggles.${nameKey}`) : nameKey;

            if (cb._processing) continue;

            if (!agentMasterCheckbox.checked) {
                cb.disabled = true;
                if (typeof cb._updateStyle === 'function') cb._updateStyle();
                continue;
            }

            try {
                const available = await checkCapability(capability, false);
                capabilityResults[flagKey] = available;

                if (!agentMasterCheckbox.checked) {
                    cb.disabled = true;
                    if (typeof cb._updateStyle === 'function') cb._updateStyle();
                    continue;
                }

                cb.disabled = !available;
                cb.title = available ? name : (window.t ? window.t('settings.toggles.unavailable', { name: name }) : `${name}\u4e0d\u53ef\u7528`);
                if (typeof cb._updateStyle === 'function') cb._updateStyle();

                if (!available && cb.checked) {
                    console.log(`[App] ${name}\u53d8\u4e3a\u4e0d\u53ef\u7528\uff0c\u81ea\u52a8\u5173\u95ed`);
                    cb.checked = false;
                    cb._autoDisabled = true;
                    cb.dispatchEvent(new Event('change', { bubbles: true }));
                    cb._autoDisabled = false;
                    try {
                        await fetch('/api/agent/flags', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                lanlan_name: window.lanlan_config.lanlan_name,
                                flags: { [flagKey]: false }
                            })
                        });
                    } catch (e) {
                        console.warn(`[App] \u901a\u77e5\u540e\u7aef\u5173\u95ed${name}\u5931\u8d25:`, e);
                    }
                    setFloatingAgentStatus(`${name}\u5df2\u65ad\u5f00`);
                }
            } catch (e) {
                capabilityCheckFailed = true;
                console.warn(`[App] \u68c0\u67e5${name}\u80fd\u529b\u5931\u8d25:`, e);
            }
        }

        if (capabilityCheckFailed) {
            connectionFailureCount++;
        }

        // Periodic flag sync from backend
        const now = Date.now();
        if (now - lastFlagsSyncTime >= FLAGS_SYNC_INTERVAL) {
            lastFlagsSyncTime = now;
            try {
                const resp = await fetch('/api/agent/flags');
                if (resp.ok) {
                    connectionFailureCount = 0;

                    const data = await resp.json();
                    if (data.success) {
                        const analyzerEnabled = data.analyzer_enabled || false;
                        const flags = data.agent_flags || {};
                        flags.agent_enabled = !!analyzerEnabled;

                        const notification = data.notification;
                        if (notification) {
                            console.log('[App] \u6536\u5230\u540e\u7aef\u901a\u77e5:', notification);
                            const translatedNotification = window.translateStatusMessage ? window.translateStatusMessage(notification) : notification;
                            setFloatingAgentStatus(translatedNotification);
                            maybeShowContentFilterModal(notification);

                            let isErrorNotification = false;
                            try {
                                const parsed = JSON.parse(notification);
                                if (parsed && parsed.code) {
                                    const errorCodes = ['AGENT_AUTO_DISABLED_COMPUTER', 'AGENT_AUTO_DISABLED_BROWSER', 'AGENT_LLM_CHECK_ERROR', 'AGENT_CU_UNAVAILABLE', 'AGENT_CU_ENABLE_FAILED', 'AGENT_CU_CAPABILITY_LOST'];
                                    isErrorNotification = errorCodes.includes(parsed.code);
                                }
                            } catch (_) {
                                isErrorNotification = notification.includes('\u5931\u8d25') || notification.includes('\u65ad\u5f00') || notification.includes('\u9519\u8bef');
                            }
                            if (isErrorNotification) {
                                window.showStatusToast(translatedNotification, 3000);
                            }
                        }

                        agentStateMachine.updateCache(true, flags);

                        // If backend analyzer was disabled, sync frontend master toggle off
                        if (!analyzerEnabled && agentMasterCheckbox.checked && !agentMasterCheckbox._processing) {
                            console.log('[App] \u540e\u7aef analyzer \u5df2\u5173\u95ed\uff0c\u540c\u6b65\u5173\u95ed\u524d\u7aef\u603b\u5f00\u5173');
                            agentMasterCheckbox.checked = false;
                            agentMasterCheckbox._autoDisabled = true;
                            agentMasterCheckbox.dispatchEvent(new Event('change', { bubbles: true }));
                            agentMasterCheckbox._autoDisabled = false;
                            if (typeof agentMasterCheckbox._updateStyle === 'function') agentMasterCheckbox._updateStyle();
                            [agentKeyboardCheckbox, agentBrowserCheckbox, agentUserPluginCheckbox].forEach(cb => {
                                if (cb) {
                                    cb.checked = false;
                                    cb.disabled = true;
                                    if (typeof cb._updateStyle === 'function') cb._updateStyle();
                                }
                            });
                            if (!notification) {
                                setFloatingAgentStatus(window.t ? window.t('agent.status.disabled') : 'Agent\u6a21\u5f0f\u5df2\u5173\u95ed');
                            }

                            if (!agentStateMachine._popupOpen) {
                                window.stopAgentAvailabilityCheck();
                            }
                            window.stopAgentTaskPolling();
                            return;
                        }

                        // Sync sub-checkbox checked state
                        if (agentKeyboardCheckbox && !agentKeyboardCheckbox._processing) {
                            const flagEnabled = flags.computer_use_enabled || false;
                            const isAvailable = capabilityCheckFailed ? agentKeyboardCheckbox.checked : (capabilityResults['computer_use_enabled'] !== false);
                            const shouldBeChecked = flagEnabled && isAvailable;

                            if (agentKeyboardCheckbox.checked !== shouldBeChecked) {
                                if (shouldBeChecked) {
                                    agentKeyboardCheckbox.checked = true;
                                    agentKeyboardCheckbox._autoDisabled = true;
                                    agentKeyboardCheckbox.dispatchEvent(new Event('change', { bubbles: true }));
                                    agentKeyboardCheckbox._autoDisabled = false;
                                    if (typeof agentKeyboardCheckbox._updateStyle === 'function') agentKeyboardCheckbox._updateStyle();
                                } else if (!flagEnabled) {
                                    agentKeyboardCheckbox.checked = false;
                                    agentKeyboardCheckbox._autoDisabled = true;
                                    agentKeyboardCheckbox.dispatchEvent(new Event('change', { bubbles: true }));
                                    agentKeyboardCheckbox._autoDisabled = false;
                                    if (typeof agentKeyboardCheckbox._updateStyle === 'function') agentKeyboardCheckbox._updateStyle();
                                }
                            }
                        }

                        // Browser control flag sync
                        if (agentBrowserCheckbox && !agentBrowserCheckbox._processing) {
                            const flagEnabled = flags.browser_use_enabled || false;
                            const isAvailable = capabilityCheckFailed
                                ? agentBrowserCheckbox.checked
                                : (capabilityResults['browser_use_enabled'] !== false);
                            const shouldBeChecked = flagEnabled && isAvailable;

                            if (agentBrowserCheckbox.checked !== shouldBeChecked) {
                                if (shouldBeChecked) {
                                    agentBrowserCheckbox.checked = true;
                                    agentBrowserCheckbox._autoDisabled = true;
                                    agentBrowserCheckbox.dispatchEvent(new Event('change', { bubbles: true }));
                                    agentBrowserCheckbox._autoDisabled = false;
                                    if (typeof agentBrowserCheckbox._updateStyle === 'function') agentBrowserCheckbox._updateStyle();
                                } else if (!flagEnabled) {
                                    agentBrowserCheckbox.checked = false;
                                    agentBrowserCheckbox._autoDisabled = true;
                                    agentBrowserCheckbox.dispatchEvent(new Event('change', { bubbles: true }));
                                    agentBrowserCheckbox._autoDisabled = false;
                                    if (typeof agentBrowserCheckbox._updateStyle === 'function') agentBrowserCheckbox._updateStyle();
                                }
                            }
                        }

                        // User plugin flag sync
                        if (agentUserPluginCheckbox && !agentUserPluginCheckbox._processing) {
                            const flagEnabled = flags.user_plugin_enabled || false;
                            const isAvailable = capabilityCheckFailed
                                ? agentUserPluginCheckbox.checked
                                : (capabilityResults['user_plugin_enabled'] !== false);
                            const shouldBeChecked = flagEnabled && isAvailable;

                            if (agentUserPluginCheckbox.checked !== shouldBeChecked) {
                                if (shouldBeChecked) {
                                    agentUserPluginCheckbox.checked = true;
                                    agentUserPluginCheckbox._autoDisabled = true;
                                    agentUserPluginCheckbox.dispatchEvent(new Event('change', { bubbles: true }));
                                    agentUserPluginCheckbox._autoDisabled = false;
                                    if (typeof agentUserPluginCheckbox._updateStyle === 'function') agentUserPluginCheckbox._updateStyle();
                                } else if (!flagEnabled) {
                                    agentUserPluginCheckbox.checked = false;
                                    agentUserPluginCheckbox._autoDisabled = true;
                                    agentUserPluginCheckbox.dispatchEvent(new Event('change', { bubbles: true }));
                                    agentUserPluginCheckbox._autoDisabled = false;
                                    if (typeof agentUserPluginCheckbox._updateStyle === 'function') agentUserPluginCheckbox._updateStyle();
                                }
                            }
                        }
                    }
                } else {
                    throw new Error(`Status ${resp.status}`);
                }
            } catch (e) {
                console.warn('[App] \u8f6e\u8be2\u540c\u6b65 flags \u5931\u8d25:', e);
                connectionFailureCount++;
            }
        }

        // Connection failure auto-disable
        if (connectionFailureCount >= 3) {
            console.error('[App] Agent\u670d\u52a1\u5668\u8fde\u7eed\u8fde\u63a5\u5931\u8d25\uff0c\u5224\u5b9a\u4e3a\u5931\u8054\uff0c\u81ea\u52a8\u5173\u95ed');
            if (agentMasterCheckbox.checked && !agentMasterCheckbox._processing) {
                agentMasterCheckbox.checked = false;
                agentMasterCheckbox._autoDisabled = true;
                agentMasterCheckbox.dispatchEvent(new Event('change', { bubbles: true }));
                agentMasterCheckbox._autoDisabled = false;
                if (typeof agentMasterCheckbox._updateStyle === 'function') agentMasterCheckbox._updateStyle();

                [agentKeyboardCheckbox, agentBrowserCheckbox, agentUserPluginCheckbox].forEach(cb => {
                    if (cb) {
                        cb.checked = false;
                        cb.disabled = true;
                        if (typeof cb._updateStyle === 'function') cb._updateStyle();
                    }
                });

                setFloatingAgentStatus(window.t ? window.t('agent.status.disconnected') : '\u670d\u52a1\u5668\u8fde\u63a5\u5df2\u65ad\u5f00');
                window.showStatusToast(window.t ? window.t('agent.status.agentDisconnected') : 'Agent \u670d\u52a1\u5668\u8fde\u63a5\u5df2\u65ad\u5f00', 3000);

                agentStateMachine.transition(AgentPopupState.OFFLINE, 'connection lost');
                window.stopAgentTaskPolling();

                connectionFailureCount = 0;
            }
        }
    };

    // ====================================================================
    // Start / stop availability check
    // ====================================================================
    window.startAgentAvailabilityCheck = function () {
        if (agentCheckInterval) {
            clearInterval(agentCheckInterval);
            agentCheckInterval = null;
        }
        lastFlagsSyncTime = 0;
        connectionFailureCount = 0;
        checkAgentCapabilities();
    };

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
        // Agent UI v2: fully event-driven single-store controller.
        if (typeof window.initAgentUiV2 === 'function') {
            try {
                window.initAgentUiV2();
                return;
            } catch (e) {
                console.warn('[App] initAgentUiV2 failed, fallback to legacy agent UI:', e);
            }
        }

        const agentMasterCheckbox = document.getElementById('live2d-agent-master');
        const agentKeyboardCheckbox = document.getElementById('live2d-agent-keyboard');
        const agentBrowserCheckbox = document.getElementById('live2d-agent-browser');
        const agentUserPluginCheckbox = document.getElementById('live2d-agent-user-plugin');

        if (!agentMasterCheckbox) {
            console.warn('[App] Agent\u5f00\u5173\u5143\u7d20\u672a\u627e\u5230\uff0c\u8df3\u8fc7\u7ed1\u5b9a');
            return;
        }

        console.log('[App] Agent\u5f00\u5173\u5143\u7d20\u5df2\u627e\u5230\uff0c\u5f00\u59cb\u7ed1\u5b9a\u4e8b\u4ef6\u76d1\u542c\u5668');

        let keyboardOperationSeq = 0;
        let browserOperationSeq = 0;
        let userPluginOperationSeq = 0;

        agentMasterCheckbox._hasExternalHandler = true;
        if (agentKeyboardCheckbox) agentKeyboardCheckbox._hasExternalHandler = true;
        if (agentBrowserCheckbox) agentBrowserCheckbox._hasExternalHandler = true;
        if (agentUserPluginCheckbox) agentUserPluginCheckbox._hasExternalHandler = true;

        const syncCheckboxUI = (checkbox) => {
            if (checkbox && typeof checkbox._updateStyle === 'function') {
                checkbox._updateStyle();
            }
        };

        // ----------------------------------------------------------------
        // applyAgentStatusSnapshotToUI
        // ----------------------------------------------------------------
        const applyAgentStatusSnapshotToUI = (snapshot) => {
            if (!snapshot || agentStateMachine.getState() === AgentPopupState.PROCESSING) return;
            const serverOnline = snapshot.server_online !== false;
            const flags = snapshot.flags || {};
            if (!('agent_enabled' in flags) && snapshot.analyzer_enabled !== undefined) {
                flags.agent_enabled = !!snapshot.analyzer_enabled;
            }
            const analyzerEnabled = !!snapshot.analyzer_enabled;
            const caps = snapshot.capabilities || {};

            agentStateMachine.updateCache(serverOnline, flags);

            if (!serverOnline) {
                agentStateMachine.transition(AgentPopupState.OFFLINE, 'snapshot offline');
                if (agentMasterCheckbox) {
                    agentMasterCheckbox.checked = false;
                    agentMasterCheckbox.disabled = true;
                    syncCheckboxUI(agentMasterCheckbox);
                }
                resetSubCheckboxes();
                setFloatingAgentStatus(window.t ? window.t('settings.toggles.serverOffline') : 'Agent\u670d\u52a1\u5668\u672a\u542f\u52a8');
                return;
            }

            agentStateMachine.transition(AgentPopupState.ONLINE, 'snapshot online');
            if (agentMasterCheckbox) {
                agentMasterCheckbox.disabled = false;
                agentMasterCheckbox.checked = analyzerEnabled;
                agentMasterCheckbox.title = window.t ? window.t('settings.toggles.agentMaster') : 'Agent\u603b\u5f00\u5173';
                syncCheckboxUI(agentMasterCheckbox);
            }

            if (!analyzerEnabled) {
                resetSubCheckboxes();
                setFloatingAgentStatus(window.t ? window.t('agent.status.ready') : 'Agent\u670d\u52a1\u5668\u5c31\u7eea');
                return;
            }

            const applySub = (cb, enabled, ready, name) => {
                if (!cb) return;
                const hasReady = typeof ready === 'boolean';
                cb.disabled = hasReady ? !ready : false;
                cb.checked = !!enabled && (hasReady ? !!ready : true);
                cb.title = cb.disabled
                    ? (window.t ? window.t('settings.toggles.unavailable', { name }) : `${name}\u4e0d\u53ef\u7528`)
                    : name;
                syncCheckboxUI(cb);
            };

            applySub(
                agentKeyboardCheckbox,
                flags.computer_use_enabled,
                caps.computer_use_ready,
                window.t ? window.t('settings.toggles.keyboardControl') : '\u952e\u9f20\u63a7\u5236'
            );

            applySub(
                agentBrowserCheckbox,
                flags.browser_use_enabled,
                caps.browser_use_ready,
                window.t ? window.t('settings.toggles.browserUse') : 'Browser Control'
            );

            applySub(
                agentUserPluginCheckbox,
                flags.user_plugin_enabled,
                caps.user_plugin_ready,
                window.t ? window.t('settings.toggles.userPlugin') : '\u7528\u6237\u63d2\u4ef6'
            );
            setFloatingAgentStatus(window.t ? window.t('agent.status.enabled') : 'Agent\u6a21\u5f0f\u5df2\u5f00\u542f');
        };
        window.applyAgentStatusSnapshotToUI = applyAgentStatusSnapshotToUI;

        // ----------------------------------------------------------------
        // resetSubCheckboxes
        // ----------------------------------------------------------------
        const resetSubCheckboxes = () => {
            const names = {
                'live2d-agent-keyboard': window.t ? window.t('settings.toggles.keyboardControl') : '\u952e\u9f20\u63a7\u5236',
                'live2d-agent-browser': window.t ? window.t('settings.toggles.browserUse') : 'Browser Control',
                'live2d-agent-user-plugin': window.t ? window.t('settings.toggles.userPlugin') : '\u7528\u6237\u63d2\u4ef6'
            };
            [agentKeyboardCheckbox, agentBrowserCheckbox, agentUserPluginCheckbox].forEach(cb => {
                if (cb) {
                    cb.disabled = true;
                    cb.checked = false;
                    const name = names[cb.id] || '';
                    cb.title = window.t ? window.t('settings.toggles.masterRequired', { name: name }) : '\u8bf7\u5148\u5f00\u542fAgent\u603b\u5f00\u5173';
                    syncCheckboxUI(cb);
                }
            });
        };

        // Initial state
        if (!agentMasterCheckbox.checked) {
            resetSubCheckboxes();
        }

        // ----------------------------------------------------------------
        // Master checkbox change handler
        // ----------------------------------------------------------------
        agentMasterCheckbox.addEventListener('change', async () => {
            const currentSeq = agentStateMachine.startOperation();
            const isChecked = agentMasterCheckbox.checked;
            console.log('[App] Agent\u603b\u5f00\u5173\u72b6\u6001\u53d8\u5316:', isChecked, '\u5e8f\u5217\u53f7:', currentSeq);

            const isExpired = () => {
                if (agentStateMachine.isSeqExpired(currentSeq)) {
                    console.log('[App] \u603b\u5f00\u5173\u64cd\u4f5c\u5df2\u8fc7\u671f\uff0c\u5e8f\u5217\u53f7:', currentSeq, '\u5f53\u524d:', agentStateMachine._operationSeq);
                    return true;
                }
                return false;
            };

            if (!agentMasterCheckbox._processing) {
                agentMasterCheckbox._processing = true;
            }

            try {
                if (isChecked) {
                    setFloatingAgentStatus(window.t ? window.t('agent.status.connecting') : 'Agent\u670d\u52a1\u5668\u8fde\u63a5\u4e2d...');

                    let healthOk = false;
                    try {
                        healthOk = await checkToolServerHealth();
                        if (!healthOk) throw new Error('tool server down');
                        agentStateMachine.updateCache(true, null);
                    } catch (e) {
                        if (isExpired()) return;
                        agentStateMachine.updateCache(false, null);
                        agentStateMachine.endOperation(false, false);
                        setFloatingAgentStatus(window.t ? window.t('settings.toggles.serverOffline') : 'Agent\u670d\u52a1\u5668\u672a\u542f\u52a8');
                        agentMasterCheckbox.checked = false;
                        agentMasterCheckbox.disabled = false;
                        agentMasterCheckbox.title = window.t ? window.t('settings.toggles.agentMaster') : 'Agent\u603b\u5f00\u5173';
                        syncCheckboxUI(agentMasterCheckbox);
                        return;
                    }

                    if (isExpired()) return;

                    agentMasterCheckbox.disabled = false;
                    agentMasterCheckbox.title = window.t ? window.t('settings.toggles.agentMaster') : 'Agent\u603b\u5f00\u5173';
                    syncCheckboxUI(agentMasterCheckbox);
                    setFloatingAgentStatus(window.t ? window.t('agent.status.enabled') : 'Agent\u6a21\u5f0f\u5df2\u5f00\u542f');

                    if (agentKeyboardCheckbox) {
                        agentKeyboardCheckbox.disabled = true;
                        agentKeyboardCheckbox.title = window.t ? window.t('settings.toggles.checking') : '\u68c0\u67e5\u4e2d...';
                        syncCheckboxUI(agentKeyboardCheckbox);
                    }

                    if (agentBrowserCheckbox) {
                        agentBrowserCheckbox.disabled = true;
                        agentBrowserCheckbox.title = window.t ? window.t('settings.toggles.checking') : '\u68c0\u67e5\u4e2d...';
                        syncCheckboxUI(agentBrowserCheckbox);
                    }

                    if (agentUserPluginCheckbox) {
                        agentUserPluginCheckbox.disabled = true;
                        agentUserPluginCheckbox.title = window.t ? window.t('settings.toggles.checking') : '\u68c0\u67e5\u4e2d...';
                        syncCheckboxUI(agentUserPluginCheckbox);
                    }

                    // Check capabilities in parallel
                    await Promise.all([
                        (async () => {
                            if (!agentKeyboardCheckbox) return;
                            const available = await checkCapability('computer_use', false);
                            if (isExpired() || !agentMasterCheckbox.checked) {
                                agentKeyboardCheckbox.disabled = true;
                                agentKeyboardCheckbox.checked = false;
                                syncCheckboxUI(agentKeyboardCheckbox);
                                return;
                            }
                            agentKeyboardCheckbox.disabled = !available;
                            agentKeyboardCheckbox.title = available ? (window.t ? window.t('settings.toggles.keyboardControl') : '\u952e\u9f20\u63a7\u5236') : (window.t ? window.t('settings.toggles.unavailable', { name: window.t('settings.toggles.keyboardControl') }) : '\u952e\u9f20\u63a7\u5236\u4e0d\u53ef\u7528');
                            syncCheckboxUI(agentKeyboardCheckbox);
                        })(),

                        (async () => {
                            if (!agentBrowserCheckbox) return;
                            const available = await checkCapability('browser_use', false);
                            if (isExpired() || !agentMasterCheckbox.checked) {
                                agentBrowserCheckbox.disabled = true;
                                agentBrowserCheckbox.checked = false;
                                syncCheckboxUI(agentBrowserCheckbox);
                                return;
                            }
                            agentBrowserCheckbox.disabled = !available;
                            agentBrowserCheckbox.title = available ? (window.t ? window.t('settings.toggles.browserUse') : 'Browser Control') : (window.t ? window.t('settings.toggles.unavailable', { name: window.t('settings.toggles.browserUse') }) : 'Browser Control\u4e0d\u53ef\u7528');
                            syncCheckboxUI(agentBrowserCheckbox);
                        })(),

                        (async () => {
                            if (!agentUserPluginCheckbox) return;
                            const available = await checkCapability('user_plugin', false);
                            if (isExpired() || !agentMasterCheckbox.checked) {
                                agentUserPluginCheckbox.disabled = true;
                                agentUserPluginCheckbox.checked = false;
                                syncCheckboxUI(agentUserPluginCheckbox);
                                return;
                            }
                            agentUserPluginCheckbox.disabled = !available;
                            agentUserPluginCheckbox.title = available ? (window.t ? window.t('settings.toggles.userPlugin') : '\u7528\u6237\u63d2\u4ef6') : (window.t ? window.t('settings.toggles.unavailable', { name: window.t('settings.toggles.userPlugin') }) : '\u7528\u6237\u63d2\u4ef6\u4e0d\u53ef\u7528');
                            syncCheckboxUI(agentUserPluginCheckbox);
                        })()
                    ]);

                    if (isExpired()) return;

                    try {
                        const r = await fetch('/api/agent/flags', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                lanlan_name: window.lanlan_config.lanlan_name,
                                flags: { agent_enabled: true, computer_use_enabled: false, browser_use_enabled: false, user_plugin_enabled: false }
                            })
                        });
                        if (!r.ok) throw new Error('main_server rejected');
                        const flagsResult = await r.json();

                        if (isExpired()) {
                            console.log('[App] flags API \u5b8c\u6210\u540e\u64cd\u4f5c\u5df2\u8fc7\u671f');
                            return;
                        }

                        // Enable analyzer
                        await fetch('/api/agent/admin/control', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ action: 'enable_analyzer' })
                        });

                        if (isExpired() || !agentMasterCheckbox.checked) {
                            console.log('[App] API\u8bf7\u6c42\u5b8c\u6210\u540e\u64cd\u4f5c\u5df2\u8fc7\u671f\u6216\u603b\u5f00\u5173\u5df2\u5173\u95ed\uff0c\u4e0d\u542f\u52a8\u8f6e\u8be2');
                            resetSubCheckboxes();
                            return;
                        }

                        agentStateMachine.endOperation(true, true);
                        window.startAgentAvailabilityCheck();
                    } catch (e) {
                        if (isExpired()) return;
                        agentStateMachine.endOperation(false, true);
                        agentMasterCheckbox.checked = false;
                        agentMasterCheckbox.disabled = false;
                        agentMasterCheckbox.title = window.t ? window.t('settings.toggles.agentMaster') : 'Agent\u603b\u5f00\u5173';
                        syncCheckboxUI(agentMasterCheckbox);
                        resetSubCheckboxes();
                        window.stopAgentTaskPolling();
                        setFloatingAgentStatus(window.t ? window.t('agent.status.enableFailed') : '\u5f00\u542f\u5931\u8d25');
                    }
                } else {
                    // --- Turn OFF ---
                    window.stopAgentAvailabilityCheck();
                    window.stopAgentTaskPolling();
                    resetSubCheckboxes();
                    setFloatingAgentStatus(window.t ? window.t('agent.status.disabled') : 'Agent\u6a21\u5f0f\u5df2\u5173\u95ed');
                    syncCheckboxUI(agentMasterCheckbox);

                    try {
                        await fetch('/api/agent/admin/control', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ action: 'disable_analyzer' })
                        });

                        if (isExpired()) {
                            console.log('[App] \u5173\u95ed\u64cd\u4f5c\u5df2\u8fc7\u671f\uff0c\u8df3\u8fc7\u540e\u7eedAPI\u8c03\u7528');
                            return;
                        }

                        await fetch('/api/agent/flags', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                lanlan_name: window.lanlan_config.lanlan_name,
                                flags: { agent_enabled: false, computer_use_enabled: false, browser_use_enabled: false, user_plugin_enabled: false }
                            })
                        });

                        if (isExpired()) {
                            console.log('[App] \u5173\u95edflags API\u5b8c\u6210\u540e\u64cd\u4f5c\u5df2\u8fc7\u671f\uff0c\u8df3\u8fc7\u72b6\u6001\u8f6c\u6362');
                            return;
                        }

                        agentStateMachine.endOperation(true, true);
                    } catch (e) {
                        if (!isExpired()) {
                            agentStateMachine.endOperation(false, true);
                            setFloatingAgentStatus(window.t ? window.t('agent.status.disabledError') : 'Agent\u6a21\u5f0f\u5df2\u5173\u95ed\uff08\u90e8\u5206\u6e05\u7406\u5931\u8d25\uff09');
                        }
                    }
                }
            } finally {
                agentMasterCheckbox._processing = false;
            }
        });

        // ----------------------------------------------------------------
        // Sub-checkbox generic handler
        // ----------------------------------------------------------------
        const setupSubCheckbox = (checkbox, capability, flagKey, nameKey, getSeq, setSeq) => {
            if (!checkbox) return;
            checkbox.addEventListener('change', async () => {
                const currentSeq = setSeq();
                const isChecked = checkbox.checked;

                const getName = () => window.t ? window.t(`settings.toggles.${nameKey}`) : nameKey;
                const name = getName();

                const isExpired = () => {
                    if (currentSeq !== getSeq()) {
                        console.log(`[App] ${name}\u5f00\u5173\u64cd\u4f5c\u5df2\u8fc7\u671f\uff0c\u5e8f\u5217\u53f7:`, currentSeq, '\u5f53\u524d:', getSeq());
                        return true;
                    }
                    return false;
                };

                if (checkbox._autoDisabled) {
                    console.log(`[App] ${name}\u5f00\u5173\u81ea\u52a8\u5173\u95ed\uff0c\u8df3\u8fc7change\u5904\u7406`);
                    return;
                }

                console.log(`[App] ${name}\u5f00\u5173\u72b6\u6001\u53d8\u5316:`, isChecked, '\u5e8f\u5217\u53f7:', currentSeq);
                if (!agentMasterCheckbox?.checked) {
                    checkbox.checked = false;
                    syncCheckboxUI(checkbox);
                    checkbox._processing = false;
                    return;
                }

                if (!checkbox._processing) {
                    checkbox._processing = true;
                }

                try {
                    const enabled = isChecked;
                    if (enabled) {
                        const ok = await checkCapability(capability);

                        if (isExpired() || !agentMasterCheckbox?.checked) {
                            console.log(`[App] ${name}\u68c0\u67e5\u671f\u95f4\u64cd\u4f5c\u5df2\u8fc7\u671f\u6216\u603b\u5f00\u5173\u5df2\u5173\u95ed\uff0c\u53d6\u6d88\u64cd\u4f5c`);
                            checkbox.checked = false;
                            checkbox.disabled = true;
                            syncCheckboxUI(checkbox);
                            return;
                        }

                        if (!ok) {
                            setFloatingAgentStatus(window.t ? window.t('settings.toggles.unavailable', { name }) : `${name}\u4e0d\u53ef\u7528`);
                            checkbox.checked = false;
                            syncCheckboxUI(checkbox);
                            return;
                        }
                    }

                    try {
                        const r = await fetch('/api/agent/flags', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                lanlan_name: window.lanlan_config.lanlan_name,
                                flags: { [flagKey]: enabled }
                            })
                        });
                        if (!r.ok) throw new Error('main_server rejected');

                        if (isExpired() || !agentMasterCheckbox?.checked) {
                            console.log(`[App] ${name}\u8bf7\u6c42\u5b8c\u6210\u540e\u64cd\u4f5c\u5df2\u8fc7\u671f\u6216\u603b\u5f00\u5173\u5df2\u5173\u95ed\uff0c\u5f3a\u5236\u5173\u95ed`);
                            checkbox.checked = false;
                            checkbox.disabled = true;
                            syncCheckboxUI(checkbox);
                            return;
                        }

                        if (window.t) {
                            setFloatingAgentStatus(enabled ? window.t('settings.toggles.enabled', { name }) : window.t('settings.toggles.disabled', { name }));
                        } else {
                            setFloatingAgentStatus(enabled ? `${name}\u5df2\u5f00\u542f` : `${name}\u5df2\u5173\u95ed`);
                        }
                        if (!enabled) {
                            syncCheckboxUI(checkbox);
                        }
                    } catch (e) {
                        if (isExpired()) return;
                        if (enabled) {
                            checkbox.checked = false;
                            syncCheckboxUI(checkbox);
                            setFloatingAgentStatus(window.t ? window.t('settings.toggles.enableFailed', { name }) : `${name}\u5f00\u542f\u5931\u8d25`);
                        }
                    }
                } finally {
                    checkbox._processing = false;
                    checkbox._processingChangeId = null;
                }
            });
        };

        setupSubCheckbox(
            agentKeyboardCheckbox,
            'computer_use',
            'computer_use_enabled',
            'keyboardControl',
            () => keyboardOperationSeq,
            () => ++keyboardOperationSeq
        );

        setupSubCheckbox(
            agentBrowserCheckbox,
            'browser_use',
            'browser_use_enabled',
            'browserUse',
            () => browserOperationSeq,
            () => ++browserOperationSeq
        );

        setupSubCheckbox(
            agentUserPluginCheckbox,
            'user_plugin',
            'user_plugin_enabled',
            'userPlugin',
            () => userPluginOperationSeq,
            () => ++userPluginOperationSeq
        );

        // ----------------------------------------------------------------
        // openAgentStatusPopupWhenEnabled
        // ----------------------------------------------------------------
        function openAgentStatusPopupWhenEnabled() {
            if (agentStateMachine._popupOpen) return;
            const master = document.getElementById('live2d-agent-master');
            if (!master || !master.checked) return;
            const popup = master.closest('[id="live2d-popup-agent"], [id="vrm-popup-agent"]');
            if (!popup) return;
            const isVisible = popup.style.display === 'flex' && popup.style.opacity === '1';
            if (isVisible) return;
            const manager = popup.id === 'live2d-popup-agent' ? window.live2dManager : window.vrmManager;
            if (!manager || typeof manager.showPopup !== 'function') return;
            manager.showPopup('agent', popup);
        }
        window.openAgentStatusPopupWhenEnabled = openAgentStatusPopupWhenEnabled;

        // ----------------------------------------------------------------
        // syncFlagsFromBackend
        // ----------------------------------------------------------------
        async function syncFlagsFromBackend() {
            try {
                const resp = await fetch('/api/agent/flags');
                if (!resp.ok) return false;
                const data = await resp.json();
                if (!data.success) return false;

                const flags = data.agent_flags || {};
                const analyzerEnabled = data.analyzer_enabled || false;
                flags.agent_enabled = !!analyzerEnabled;

                console.log('[App] \u4ece\u540e\u7aef\u83b7\u53d6 flags \u72b6\u6001:', { analyzerEnabled, flags });

                agentStateMachine.updateCache(true, flags);

                if (agentMasterCheckbox) {
                    if (agentMasterCheckbox.checked !== analyzerEnabled && !agentMasterCheckbox._processing) {
                        console.log('[App] \u5f3a\u5236\u540c\u6b65\u603b\u5f00\u5173\u72b6\u6001:', analyzerEnabled);
                        agentMasterCheckbox.checked = analyzerEnabled;

                        if (analyzerEnabled) {
                            if (!agentStateMachine._popupOpen) {
                                window.startAgentAvailabilityCheck();
                            }
                        } else {
                            window.stopAgentAvailabilityCheck();
                            window.stopAgentTaskPolling();
                        }
                    }

                    agentMasterCheckbox.disabled = false;
                    agentMasterCheckbox.title = window.t ? window.t('settings.toggles.agentMaster') : 'Agent\u603b\u5f00\u5173';
                    syncCheckboxUI(agentMasterCheckbox);
                }

                // Sub-checkboxes: keep disabled waiting for capability check
                if (agentKeyboardCheckbox) {
                    if (analyzerEnabled) {
                        agentKeyboardCheckbox.checked = false;
                        agentKeyboardCheckbox.disabled = true;
                        agentKeyboardCheckbox.title = window.t ? window.t('settings.toggles.checking') : '\u68c0\u67e5\u4e2d...';
                    } else {
                        agentKeyboardCheckbox.checked = false;
                        agentKeyboardCheckbox.disabled = true;
                        agentKeyboardCheckbox.title = window.t ? window.t('settings.toggles.masterRequired', { name: window.t ? window.t('settings.toggles.keyboardControl') : '\u952e\u9f20\u63a7\u5236' }) : '\u8bf7\u5148\u5f00\u542fAgent\u603b\u5f00\u5173';
                    }
                    syncCheckboxUI(agentKeyboardCheckbox);
                }

                if (agentBrowserCheckbox) {
                    if (analyzerEnabled) {
                        agentBrowserCheckbox.checked = false;
                        agentBrowserCheckbox.disabled = true;
                        agentBrowserCheckbox.title = window.t ? window.t('settings.toggles.checking') : '\u68c0\u67e5\u4e2d...';
                    } else {
                        agentBrowserCheckbox.checked = false;
                        agentBrowserCheckbox.disabled = true;
                        agentBrowserCheckbox.title = window.t ? window.t('settings.toggles.masterRequired', { name: window.t ? window.t('settings.toggles.browserUse') : 'Browser Control' }) : '\u8bf7\u5148\u5f00\u542fAgent\u603b\u5f00\u5173';
                    }
                    syncCheckboxUI(agentBrowserCheckbox);
                }

                if (agentUserPluginCheckbox) {
                    if (analyzerEnabled) {
                        agentUserPluginCheckbox.checked = false;
                        agentUserPluginCheckbox.disabled = true;
                        agentUserPluginCheckbox.title = window.t ? window.t('settings.toggles.checking') : '\u68c0\u67e5\u4e2d...';
                    } else {
                        agentUserPluginCheckbox.checked = false;
                        agentUserPluginCheckbox.disabled = true;
                        agentUserPluginCheckbox.title = window.t ? window.t('settings.toggles.masterRequired', { name: window.t ? window.t('settings.toggles.userPlugin') : '\u7528\u6237\u63d2\u4ef6' }) : '\u8bf7\u5148\u5f00\u542fAgent\u603b\u5f00\u5173';
                    }
                    syncCheckboxUI(agentUserPluginCheckbox);
                }

                if (analyzerEnabled) {
                    setTimeout(() => openAgentStatusPopupWhenEnabled(), 0);
                }
                return analyzerEnabled;
            } catch (e) {
                console.warn('[App] \u540c\u6b65 flags \u72b6\u6001\u5931\u8d25:', e);
                return false;
            }
        }

        window.syncAgentFlagsFromBackend = syncFlagsFromBackend;

        // ----------------------------------------------------------------
        // Agent popup opening event
        // ----------------------------------------------------------------
        window.addEventListener('live2d-agent-popup-opening', async () => {
            agentStateMachine.openPopup();
            isAgentPopupOpen = true;

            // Prefer backend snapshot for instant render
            if (window._agentStatusSnapshot) {
                applyAgentStatusSnapshotToUI(window._agentStatusSnapshot);
                setTimeout(() => {
                    if (agentStateMachine._popupOpen) {
                        checkAgentCapabilities();
                    }
                }, 0);
                return;
            }

            if (agentStateMachine.getState() === AgentPopupState.PROCESSING) {
                console.log('[App] \u5f39\u7a97\u6253\u5f00\u65f6\u72b6\u6001\u673a\u5904\u4e8ePROCESSING\uff0c\u8df3\u8fc7\u68c0\u67e5');
                return;
            }

            agentStateMachine.transition(AgentPopupState.CHECKING, 'popup opened');

            const currentCheckSeq = agentStateMachine.nextCheckSeq();

            // Force-disable all buttons while checking
            if (agentMasterCheckbox) {
                agentMasterCheckbox.disabled = true;
                agentMasterCheckbox.title = window.t ? window.t('settings.toggles.checking') : '\u67e5\u8be2\u4e2d...';
                syncCheckboxUI(agentMasterCheckbox);
            }
            [agentKeyboardCheckbox, agentBrowserCheckbox, agentUserPluginCheckbox].forEach(cb => {
                if (cb) {
                    cb.disabled = true;
                    cb.title = window.t ? window.t('settings.toggles.checking') : '\u67e5\u8be2\u4e2d...';
                    syncCheckboxUI(cb);
                }
            });

            // Gather mode: parallel fetch all status
            try {
                agentStateMachine.recordCheck();

                const [healthOk, flagsData, keyboardAvailable, browserAvailable, userPluginAvailable] = await Promise.all([
                    checkToolServerHealth(),
                    fetch('/api/agent/flags').then(r => r.ok ? r.json() : { success: false }),
                    checkCapability('computer_use', false),
                    checkCapability('browser_use', false),
                    checkCapability('user_plugin', false)
                ]);

                if (agentStateMachine.isCheckSeqExpired(currentCheckSeq)) {
                    console.log('[App] \u68c0\u67e5\u8bf7\u6c42\u5df2\u8fc7\u671f\uff08\u53ef\u80fd\u662f\u5feb\u901f\u91cd\u65b0\u6253\u5f00\uff09\uff0c\u8df3\u8fc7UI\u66f4\u65b0');
                    return;
                }

                if (!agentStateMachine._popupOpen || agentStateMachine.getState() !== AgentPopupState.CHECKING) {
                    console.log('[App] \u5f39\u7a97\u5df2\u5173\u95ed\u6216\u72b6\u6001\u5df2\u6539\u53d8\uff0c\u8df3\u8fc7UI\u66f4\u65b0');
                    return;
                }

                const analyzerEnabled = flagsData.success ? (flagsData.analyzer_enabled || false) : false;
                const flags = flagsData.success ? (flagsData.agent_flags || {}) : {};
                flags.agent_enabled = !!analyzerEnabled;

                agentStateMachine.updateCache(healthOk, flags);

                if (healthOk) {
                    agentStateMachine.transition(AgentPopupState.ONLINE, 'server online');

                    if (analyzerEnabled) {
                        agentMasterCheckbox.checked = true;
                        agentMasterCheckbox.disabled = false;
                        agentMasterCheckbox.title = window.t ? window.t('settings.toggles.agentMaster') : 'Agent\u603b\u5f00\u5173';
                        syncCheckboxUI(agentMasterCheckbox);

                        if (agentKeyboardCheckbox) {
                            const shouldEnable = flags.computer_use_enabled && keyboardAvailable;
                            agentKeyboardCheckbox.checked = shouldEnable;
                            agentKeyboardCheckbox.disabled = !keyboardAvailable;
                            agentKeyboardCheckbox.title = keyboardAvailable ? (window.t ? window.t('settings.toggles.keyboardControl') : '\u952e\u9f20\u63a7\u5236') : (window.t ? window.t('settings.toggles.unavailable', { name: window.t('settings.toggles.keyboardControl') }) : '\u952e\u9f20\u63a7\u5236\u4e0d\u53ef\u7528');
                            syncCheckboxUI(agentKeyboardCheckbox);
                        }

                        if (agentBrowserCheckbox) {
                            const shouldEnable = flags.browser_use_enabled && browserAvailable;
                            agentBrowserCheckbox.checked = shouldEnable;
                            agentBrowserCheckbox.disabled = !browserAvailable;
                            agentBrowserCheckbox.title = browserAvailable ? (window.t ? window.t('settings.toggles.browserUse') : 'Browser Control') : (window.t ? window.t('settings.toggles.unavailable', { name: window.t('settings.toggles.browserUse') }) : 'Browser Control\u4e0d\u53ef\u7528');
                            syncCheckboxUI(agentBrowserCheckbox);
                        }

                        if (agentUserPluginCheckbox) {
                            const shouldEnable = flags.user_plugin_enabled && userPluginAvailable;
                            agentUserPluginCheckbox.checked = shouldEnable;
                            agentUserPluginCheckbox.disabled = !userPluginAvailable;
                            agentUserPluginCheckbox.title = userPluginAvailable ? (window.t ? window.t('settings.toggles.userPlugin') : '\u7528\u6237\u63d2\u4ef6') : (window.t ? window.t('settings.toggles.unavailable', { name: window.t('settings.toggles.userPlugin') }) : '\u7528\u6237\u63d2\u4ef6\u4e0d\u53ef\u7528');
                            syncCheckboxUI(agentUserPluginCheckbox);
                        }

                        setFloatingAgentStatus(window.t ? window.t('agent.status.enabled') : 'Agent\u6a21\u5f0f\u5df2\u5f00\u542f');
                        checkAndToggleTaskHUD();
                    } else {
                        agentMasterCheckbox.checked = false;
                        agentMasterCheckbox.disabled = false;
                        agentMasterCheckbox.title = window.t ? window.t('settings.toggles.agentMaster') : 'Agent\u603b\u5f00\u5173';
                        syncCheckboxUI(agentMasterCheckbox);

                        resetSubCheckboxes();

                        setFloatingAgentStatus(window.t ? window.t('agent.status.ready') : 'Agent\u670d\u52a1\u5668\u5c31\u7eea');

                        window.stopAgentTaskPolling();

                        if (flags.computer_use_enabled || flags.browser_use_enabled || flags.user_plugin_enabled) {
                            console.log('[App] \u603b\u5f00\u5173\u5173\u95ed\u4f46\u68c0\u6d4b\u5230\u5b50flag\u5f00\u542f\uff0c\u5f3a\u5236\u540c\u6b65\u5173\u95ed');
                            fetch('/api/agent/flags', {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({
                                    lanlan_name: window.lanlan_config.lanlan_name,
                                    flags: { agent_enabled: false, computer_use_enabled: false, browser_use_enabled: false, user_plugin_enabled: false }
                                })
                            }).catch(e => console.warn('[App] \u5f3a\u5236\u5173\u95edflags\u5931\u8d25:', e));
                        }
                    }

                    window.startAgentAvailabilityCheck();

                } else {
                    agentStateMachine.transition(AgentPopupState.OFFLINE, 'server offline');
                    agentMasterCheckbox.checked = false;
                    agentMasterCheckbox.disabled = true;
                    agentMasterCheckbox.title = window.t ? window.t('settings.toggles.serverOffline') : 'Agent\u670d\u52a1\u5668\u672a\u542f\u52a8';
                    syncCheckboxUI(agentMasterCheckbox);

                    resetSubCheckboxes();

                    setFloatingAgentStatus(window.t ? window.t('settings.toggles.serverOffline') : 'Agent\u670d\u52a1\u5668\u672a\u542f\u52a8');

                    window.startAgentAvailabilityCheck();
                }

            } catch (e) {
                console.error('[App] Agent \u521d\u59cb\u68c0\u67e5\u5931\u8d25:', e);
                agentStateMachine.updateCache(false, null);

                if (agentStateMachine._popupOpen) {
                    agentStateMachine.transition(AgentPopupState.OFFLINE, 'check failed');
                    agentMasterCheckbox.checked = false;
                    resetSubCheckboxes();
                    window.startAgentAvailabilityCheck();
                }
            } finally {
                agentStateMachine.releaseCheckLock();
            }
        });

        // ----------------------------------------------------------------
        // Agent popup closing event
        // ----------------------------------------------------------------
        window.addEventListener('live2d-agent-popup-closed', () => {
            isAgentPopupOpen = false;
            agentStateMachine.closePopup();
            console.log('[App] Agent\u5f39\u7a97\u5df2\u5173\u95ed');

            if (!agentMasterCheckbox || !agentMasterCheckbox.checked) {
                window.stopAgentAvailabilityCheck();
            }
        });

        console.log('[App] Agent\u5f00\u5173\u4e8b\u4ef6\u76d1\u542c\u5668\u7ed1\u5b9a\u5b8c\u6210');
    };
    mod.setupAgentCheckboxListeners = setupAgentCheckboxListeners;

    // ====================================================================
    // Agent Task HUD polling
    // ====================================================================
    let agentTaskPollingInterval = null;
    let agentTaskTimeUpdateInterval = null;

    window.startAgentTaskPolling = function () {
        console.trace('[App] startAgentTaskPolling');
        if (window.AgentHUD && window.AgentHUD.createAgentTaskHUD) {
            window.AgentHUD.createAgentTaskHUD();
            window.AgentHUD.showAgentTaskHUD();
        }

        if (agentTaskPollingInterval) return;

        console.log('[App] \u542f\u52a8 Agent \u4efb\u52a1\u72b6\u6001\u8f6e\u8be2');

        agentTaskPollingInterval = true;
        agentTaskTimeUpdateInterval = setInterval(updateTaskRunningTimes, 1000);
    };

    window.stopAgentTaskPolling = function () {
        console.log('[App] \u505c\u6b62 Agent \u4efb\u52a1\u72b6\u6001\u8f6e\u8be2');
        console.trace('[App] stopAgentTaskPolling caller trace');

        if (agentTaskPollingInterval) {
            if (typeof agentTaskPollingInterval !== 'boolean') {
                clearInterval(agentTaskPollingInterval);
            }
            agentTaskPollingInterval = null;
        }

        if (agentTaskTimeUpdateInterval) {
            clearInterval(agentTaskTimeUpdateInterval);
            agentTaskTimeUpdateInterval = null;
        }

        if (window.AgentHUD && window.AgentHUD.hideAgentTaskHUD) {
            window.AgentHUD.hideAgentTaskHUD();
        }
    };

    // ====================================================================
    // updateTaskRunningTimes
    // ====================================================================
    function updateTaskRunningTimes() {
        const taskList = document.getElementById('agent-task-list');
        if (!taskList) {
            return;
        }

        const hasRunning = window._agentTaskMap && Array.from(window._agentTaskMap.values())
            .some(t => t.status === 'running' || t.status === 'queued');
        if (!hasRunning) {
            if (agentTaskTimeUpdateInterval) {
                clearInterval(agentTaskTimeUpdateInterval);
                agentTaskTimeUpdateInterval = null;
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
                timeEl.innerHTML = `<span style="color: #64748b;">\u23f1\ufe0f</span> ${minutes}:${seconds.toString().padStart(2, '0')}`;
            }
        });
    }

    // ====================================================================
    // checkAndToggleTaskHUD
    // ====================================================================
    function checkAndToggleTaskHUD() {
        const getEl = (ids) => {
            for (let id of ids) {
                const el = document.getElementById(id);
                if (el) return el;
            }
            return null;
        };

        const masterCheckbox = getEl(['live2d-agent-master', 'vrm-agent-master']);
        const keyboardCheckbox = getEl(['live2d-agent-keyboard', 'vrm-agent-keyboard']);
        const browserCheckbox = getEl(['live2d-agent-browser', 'vrm-agent-browser']);
        const userPlugin = getEl(['live2d-agent-user-plugin', 'vrm-agent-user-plugin']);

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
            console.log('[DEBUG HUD] Starting polling. Master:', isMasterOn, 'Child:', isChildOn, 'DOM:', domMaster, domChild, 'Flag:', flags?.agent_enabled, 'Opt:', optMaster, optChild);
            window.startAgentTaskPolling();
        } else {
            const LINGER_MS = 10000;
            const now = Date.now();
            const hasActiveTasks = window._agentTaskMap && window._agentTaskMap.size > 0 &&
                Array.from(window._agentTaskMap.values()).some(t => {
                    if (t.status === 'running' || t.status === 'queued') return true;
                    const isTerminal = t.status === 'completed' || t.status === 'failed' || t.status === 'cancelled';
                    return isTerminal && t.terminal_at && (now - t.terminal_at < LINGER_MS);
                });
            if (hasActiveTasks) {
                console.log('[DEBUG HUD] Flags off but active tasks exist, keeping HUD visible. Master:', isMasterOn, 'Child:', isChildOn);
            } else {
                console.log('[DEBUG HUD] Stopping polling. Master:', isMasterOn, 'Child:', isChildOn, 'DOM:', domMaster, domChild, 'Flag:', flags?.agent_enabled, 'Opt:', optMaster, optChild);
                window.stopAgentTaskPolling();
            }
        }
    }

    window.checkAndToggleTaskHUD = checkAndToggleTaskHUD;
    mod.checkAndToggleTaskHUD = checkAndToggleTaskHUD;

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

            const keyboardCheckbox = getEl(['live2d-agent-keyboard', 'vrm-agent-keyboard']);
            const browserCheckbox = getEl(['live2d-agent-browser', 'vrm-agent-browser']);
            const userPluginCheckbox = getEl(['live2d-agent-user-plugin', 'vrm-agent-user-plugin']);

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
        setTimeout(() => {
            if (typeof window.openAgentStatusPopupWhenEnabled === 'function') {
                window.openAgentStatusPopupWhenEnabled();
            }
        }, 400);
    }, { once: true });

    // ====================================================================
    // Expose module
    // ====================================================================
    window.appAgent = mod;
})();
