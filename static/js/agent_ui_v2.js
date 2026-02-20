(function () {
    const FLAG_KEYS = ['computer_use_enabled', 'browser_use_enabled', 'user_plugin_enabled'];

    const state = {
        snapshot: null,
        revision: -1,
        popupOpen: false,
        pending: new Set(),
        suppressChange: false,
        inited: false,
        masterOpSeq: 0,
        globalBusy: false,
        optimistic: {},
        busyTimer: null,
    };

    const byId = (id) => document.getElementById(id);
    const el = () => ({
        master: byId('live2d-agent-master'),
        keyboard: byId('live2d-agent-keyboard'),
        browser: byId('live2d-agent-browser'),
        userPlugin: byId('live2d-agent-user-plugin'),
        status: byId('live2d-agent-status'),
    });
    const sync = (cb) => {
        if (cb && typeof cb._updateStyle === 'function') cb._updateStyle();
    };
    const getName = (key) => {
        const map = {
            computer_use_enabled: window.t ? window.t('settings.toggles.keyboardControl') : '键鼠控制',
            browser_use_enabled: window.t ? window.t('settings.toggles.browserUse') : 'Browser Control',
            user_plugin_enabled: window.t ? window.t('settings.toggles.userPlugin') : '用户插件',
        };
        return map[key] || key;
    };
    const setStatus = (msg) => {
        const { status } = el();
        if (status) status.textContent = msg || '';
    };
    const setGlobalBusy = (busy, statusText) => {
        state.globalBusy = !!busy;
        if (state.busyTimer) {
            clearTimeout(state.busyTimer);
            state.busyTimer = null;
        }
        if (busy) {
            if (statusText) setStatus(statusText);
            // Safety valve: never keep UI locked forever.
            state.busyTimer = setTimeout(() => {
                state.globalBusy = false;
                state.optimistic = {};
                render('busy-timeout');
            }, 8000);
        }
    };
    const capabilityReady = (snapshot, key) => {
        const caps = (snapshot && snapshot.capabilities) || {};
        const map = {
            computer_use_enabled: 'computer_use',
            browser_use_enabled: 'browser_use',
            user_plugin_enabled: 'user_plugin',
        };
        const cap = caps[map[key]];
        if (!cap) return true;
        return !!cap.ready;
    };
    const capabilityReason = (snapshot, key) => {
        const caps = (snapshot && snapshot.capabilities) || {};
        const map = {
            computer_use_enabled: 'computer_use',
            browser_use_enabled: 'browser_use',
            user_plugin_enabled: 'user_plugin',
        };
        const cap = caps[map[key]];
        return (cap && cap.reason) || '';
    };

    async function fetchSnapshot() {
        const r = await fetch('/api/agent/state');
        if (!r.ok) throw new Error(`state status ${r.status}`);
        const j = await r.json();
        if (!j || j.success !== true || !j.snapshot) throw new Error('invalid state payload');
        applySnapshot(j.snapshot, 'http');
        return j.snapshot;
    }

    async function sendCommand(command, payload) {
        const requestId = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
        const t0 = performance.now();
        const r = await fetch('/api/agent/command', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ request_id: requestId, command, ...(payload || {}) }),
        });
        if (!r.ok) throw new Error(`command status ${r.status}`);
        const j = await r.json();
        if (!j || j.success !== true) throw new Error(j?.error || 'command failed');
        const roundtrip = Number((performance.now() - t0).toFixed(2));
        console.log('[AgentUIv2Timing]', { requestId, command, roundtrip_ms: roundtrip, timing: j.timing || {} });
        return j;
    }

    function applySnapshot(snapshot, source = 'ws') {
        if (!snapshot || typeof snapshot !== 'object') return;
        const rev = Number(snapshot.revision ?? -1);
        if (Number.isFinite(rev) && rev <= state.revision) return;
        state.snapshot = snapshot;
        if (Number.isFinite(rev)) state.revision = rev;
        window._agentStatusSnapshot = snapshot;
        render(source);
    }

    function render(source = 'render') {
        const { master, keyboard, browser, userPlugin } = el();
        if (!master) return;
        const snap = state.snapshot;
        if (!snap) {
            master.disabled = true;
            master.checked = false;
            sync(master);
            [keyboard, browser, userPlugin].forEach(cb => {
                if (!cb) return;
                cb.disabled = true;
                cb.checked = false;
                sync(cb);
            });
            setStatus(window.t ? window.t('agent.status.connecting') : 'Agent状态同步中...');
            return;
        }

        const online = snap.server_online !== false;
        const analyzerEnabled = !!snap.analyzer_enabled;
        const flags = snap.flags || {};
        const optimisticMaster = Object.prototype.hasOwnProperty.call(state.optimistic, 'agent_enabled')
            ? !!state.optimistic.agent_enabled
            : analyzerEnabled;
        const effectiveAnalyzerEnabled = state.globalBusy ? optimisticMaster : analyzerEnabled;

        state.suppressChange = true;
        if (!online) {
            master.checked = false;
            master.disabled = true;
            master.title = window.t ? window.t('settings.toggles.serverOffline') : 'Agent服务器未启动';
            sync(master);
            [keyboard, browser, userPlugin].forEach(cb => {
                if (!cb) return;
                cb.checked = false;
                cb.disabled = true;
                sync(cb);
            });
            setStatus(window.t ? window.t('settings.toggles.serverOffline') : 'Agent服务器未启动');
            state.suppressChange = false;
            return;
        }

        master.checked = effectiveAnalyzerEnabled;
        master.disabled = !!state.globalBusy;
        master.title = window.t ? window.t('settings.toggles.agentMaster') : 'Agent总开关';
        sync(master);

        FLAG_KEYS.forEach((k) => {
            const target = k === 'computer_use_enabled'
                ? keyboard
                : (k === 'browser_use_enabled' ? browser : (k === 'user_plugin_enabled' ? userPlugin : null));
            if (!target) return;
            const ready = capabilityReady(snap, k);
            const reason = capabilityReason(snap, k);
            const disabledByPending = state.pending.has(k);
            const optimisticValue = Object.prototype.hasOwnProperty.call(state.optimistic, k)
                ? !!state.optimistic[k]
                : !!flags[k];
            const canUse = effectiveAnalyzerEnabled && ready;
            target.checked = optimisticValue && canUse;
            target.disabled = !!state.globalBusy || disabledByPending || !canUse;
            target.title = canUse ? getName(k) : (reason || (window.t ? window.t('settings.toggles.masterRequired', { name: getName(k) }) : '请先开启Agent总开关'));
            sync(target);
        });

        if (state.globalBusy) {
            setStatus(window.t ? window.t('settings.toggles.checking') : '已接受操作，切换中...');
        } else if (!analyzerEnabled) {
            setStatus(window.t ? window.t('agent.status.ready') : 'Agent服务器就绪');
        } else {
            setStatus(window.t ? window.t('agent.status.enabled') : 'Agent模式已开启');
        }
        state.suppressChange = false;
    }

    function bindEvents() {
        const { master, keyboard, browser, userPlugin } = el();
        if (!master) return;
        const clearProcessing = (cb) => {
            if (!cb) return;
            cb._processing = false;
            cb._processingEvent = null;
            cb._processingTime = null;
        };

        const onMasterChange = async () => {
            if (state.suppressChange) {
                clearProcessing(master);
                return;
            }
            const enabled = !!master.checked;
            const opSeq = ++state.masterOpSeq;
            state.pending.add('agent_enabled');
            state.optimistic.agent_enabled = enabled;
            setGlobalBusy(true, window.t ? window.t('settings.toggles.checking') : '已接受操作，切换中...');
            render('command');
            try {
                await sendCommand('set_agent_enabled', { enabled });
                if (opSeq === state.masterOpSeq) {
                    const ts = performance.now();
                    await fetchSnapshot().catch(() => { });
                    console.log('[AgentUIv2Timing]', { phase: 'fetch_snapshot_after_master', ms: Number((performance.now() - ts).toFixed(2)) });
                }
            } catch (e) {
                if (opSeq === state.masterOpSeq) {
                    state.pending.delete('agent_enabled');
                    state.optimistic = {};
                    setGlobalBusy(false);
                    fetchSnapshot().catch(() => { });
                    if (typeof window.showStatusToast === 'function') {
                        window.showStatusToast(`Agent切换失败: ${e.message}`, 2500);
                    }
                }
                return;
            } finally {
                // live2d-ui-popup.js sets this flag for debounce; v2 must clear it explicitly.
                clearProcessing(master);
            }
            if (opSeq === state.masterOpSeq) {
                state.pending.delete('agent_enabled');
                state.optimistic = {};
                setGlobalBusy(false);
                render('command');
            }
        };
        master.addEventListener('change', onMasterChange);

        const bindFlag = (cb, key) => {
            if (!cb) return;
            cb.addEventListener('change', async () => {
                if (state.suppressChange) {
                    clearProcessing(cb);
                    return;
                }
                const value = !!cb.checked;
                state.pending.add(key);
                state.optimistic[key] = value;
                setGlobalBusy(true, window.t ? window.t('settings.toggles.checking') : '已接受操作，切换中...');
                render('command');
                try {
                    await sendCommand('set_flag', { key, value });
                    const ts = performance.now();
                    await fetchSnapshot().catch(() => { });
                    console.log('[AgentUIv2Timing]', { phase: 'fetch_snapshot_after_flag', key, ms: Number((performance.now() - ts).toFixed(2)) });
                } catch (e) {
                    state.pending.delete(key);
                    state.optimistic = {};
                    setGlobalBusy(false);
                    fetchSnapshot().catch(() => { });
                    if (typeof window.showStatusToast === 'function') {
                        window.showStatusToast(`${getName(key)}切换失败: ${e.message}`, 2500);
                    }
                    return;
                } finally {
                    clearProcessing(cb);
                }
                state.pending.delete(key);
                state.optimistic = {};
                setGlobalBusy(false);
                render('command');
            });
        };

        bindFlag(keyboard, 'computer_use_enabled');
        bindFlag(browser, 'browser_use_enabled');
        bindFlag(userPlugin, 'user_plugin_enabled');

        window.addEventListener('live2d-agent-popup-opening', async () => {
            state.popupOpen = true;
            render('popup');
            if (!state.snapshot) {
                await fetchSnapshot().catch(() => render('popup'));
                return;
            }
            // Open popup without waiting, then refresh in background.
            fetchSnapshot().catch(() => { });
        });
        window.addEventListener('live2d-agent-popup-closed', () => {
            state.popupOpen = false;
        });
    }

    window.applyAgentStatusSnapshotToUI = (snapshot) => {
        applySnapshot(snapshot, 'ws');
    };

    window.initAgentUiV2 = function initAgentUiV2() {
        if (state.inited) return true;
        state.inited = true;
        bindEvents();
        fetchSnapshot().catch(() => render('init'));
        return true;
    };
})();
