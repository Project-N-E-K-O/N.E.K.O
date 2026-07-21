/**
 * 状态管理模块
 * 负责管理代理的状态和变更通知
 * 包含状态快照、变更通知、繁忙状态等
 */
(function () {
    const FLAG_KEYS = ['computer_use_enabled', 'browser_use_enabled', 'user_plugin_enabled', 'openclaw_enabled', 'openfang_enabled'];

    const state = {
        snapshot: null,
        revision: -1,
        popupOpen: false,
        pending: new Set(),
        suppressChange: false,
        inited: false,
        masterOpSeq: 0,
        snapshotGeneration: 0,
        expectedCharacter: '',
        globalBusy: false,
        optimistic: {},
        busyTimer: null,
        openclawReady: null,
        openclawReason: '',
        globalEventsBound: false,
    };
    
    // 暴露状态供 app.js 等外部脚本使用乐观更新检测
    window.agent_ui_v2_state = state;

    const byId = (id) => document.getElementById(id);
    const getEls = (...ids) => ids.map(id => byId(id)).filter(Boolean);
    const el = () => ({
        master: getEls('live2d-agent-master', 'vrm-agent-master', 'mmd-agent-master', 'pngtuber-agent-master'),
        taskhud: getEls('live2d-agent-taskhud', 'vrm-agent-taskhud', 'mmd-agent-taskhud', 'pngtuber-agent-taskhud'),
        keyboard: getEls('live2d-agent-keyboard', 'vrm-agent-keyboard', 'mmd-agent-keyboard', 'pngtuber-agent-keyboard'),
        browser: getEls('live2d-agent-browser', 'vrm-agent-browser', 'mmd-agent-browser', 'pngtuber-agent-browser'),
        userPlugin: getEls('live2d-agent-user-plugin', 'vrm-agent-user-plugin', 'mmd-agent-user-plugin', 'pngtuber-agent-user-plugin'),
        openfang: getEls('live2d-agent-openfang', 'vrm-agent-openfang', 'mmd-agent-openfang', 'pngtuber-agent-openfang'),
        openclaw: getEls('live2d-agent-openclaw', 'vrm-agent-openclaw', 'mmd-agent-openclaw', 'pngtuber-agent-openclaw'),
        status: getEls('live2d-agent-status', 'vrm-agent-status', 'mmd-agent-status', 'pngtuber-agent-status'),
    });
    const sync = (cbs) => {
        if (!cbs) return;
        (Array.isArray(cbs) ? cbs : [cbs]).forEach(cb => {
            if (cb && typeof cb._updateStyle === 'function') cb._updateStyle();
        });
    };
    const getName = (key) => {
        const map = {
            computer_use_enabled: window.t ? window.t('settings.toggles.keyboardControl') : '键鼠控制',
            browser_use_enabled: window.t ? window.t('settings.toggles.browserUse') : 'Browser Control',
            user_plugin_enabled: window.t ? window.t('settings.toggles.userPlugin') : '用户插件',
            openclaw_enabled: window.t ? window.t('settings.toggles.openclawConnect') : 'OpenClaw',
            openfang_enabled: window.t ? window.t('settings.toggles.openfang') : '虚拟机',
        };
        return map[key] || key;
    };
    const isTutorialAgentStatusLocked = () => {
        if (window.isInTutorial !== true) return false;
        if (window.isNekoHomeTutorialPending === true) return true;
        if (window.universalTutorialManager && window.universalTutorialManager.isTutorialRunning === true) return true;
        if (typeof window.isNekoHomeTutorialInteractionLocked === 'function') {
            try {
                if (window.isNekoHomeTutorialInteractionLocked() === true) return true;
            } catch (_) {}
        }
        if (
            window.NekoHomeTutorialFeatureController
            && typeof window.NekoHomeTutorialFeatureController.isActive === 'function'
        ) {
            try {
                if (window.NekoHomeTutorialFeatureController.isActive() === true) return true;
            } catch (_) {}
        }
        return false;
    };
    const setStatus = (msg, options) => {
        const { status } = el();
        const shouldStabilizeTutorialText = options
            && options.stabilizeTutorialText === true
            && isTutorialAgentStatusLocked();
        const text = shouldStabilizeTutorialText ? 'NekoClaw server ready' : (msg || '');
        status.forEach(s => { if (s) s.textContent = text; });
    };
    const currentLanlanName = () => {
        const fromConfig = window.lanlan_config && typeof window.lanlan_config.lanlan_name === 'string'
            ? window.lanlan_config.lanlan_name
            : '';
        const fromAppState = window.appState && typeof window.appState.lanlan_name === 'string'
            ? window.appState.lanlan_name
            : '';
        return String(fromConfig || fromAppState || '').trim();
    };
    const expectedCharacterName = () => String(state.expectedCharacter || currentLanlanName() || '').trim();
    const makeSnapshotToken = () => ({
        generation: state.snapshotGeneration,
        expectedCharacter: expectedCharacterName(),
    });
    const isSnapshotTokenCurrent = (token) => {
        if (!token) return true;
        if (token.generation !== undefined && token.generation !== state.snapshotGeneration) return false;
        const expected = expectedCharacterName();
        const tokenCharacter = String(token.expectedCharacter || '').trim();
        if (expected && tokenCharacter && tokenCharacter !== expected) return false;
        const sourceCharacter = String(token.sourceCharacter || '').trim();
        if (expected && sourceCharacter && sourceCharacter !== expected) return false;
        return true;
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
            openclaw_enabled: 'openclaw',
            openfang_enabled: 'openfang',
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
            openclaw_enabled: 'openclaw',
            openfang_enabled: 'openfang',
        };
        const cap = caps[map[key]];
        return (cap && cap.reason) || '';
    };
    const sanitizeOpenClawReason = (reason) => String(reason || '')
            .replace(/OpenClaw\(QwenPaw\)/g, 'OpenClaw')
            .replace(/QwenPaw/g, 'OpenClaw service')
            .trim();
    const translateAgentReasonCode = (reason) => {
        const reasonText = String(reason || '').trim();
        if (reasonText && /^AGENT[A-Z0-9_-]*$/i.test(reasonText) && window.t) {
            const normalizedReason = reasonText
                .replace(/[^A-Za-z0-9]+/g, '_')
                .replace(/^_+|_+$/g, '')
                .toUpperCase();
            const precheckKey = `agent.precheck.${normalizedReason}`;
            const translated = window.t(precheckKey);
            if (translated && translated !== precheckKey) return translated;
        }
        return reasonText;
    };
    const formatOpenClawUnavailable = (reason, name) => {
        const reasonText = sanitizeOpenClawReason(reason);
        if (reasonText && !reasonText.includes('PENDING')) {
            const displayReason = translateAgentReasonCode(reasonText);
            return window.t
                ? window.t('settings.toggles.openclawUnavailableReason', { name, reason: displayReason })
                : `${name}不可用：${displayReason}。请确认猫爪连接服务已启动，并监听 127.0.0.1:8088。`;
        }
        return window.t
            ? window.t('settings.toggles.capabilityNotReady', { name })
            : `${name}尚未就绪，点击尝试启用`;
    };

    async function refreshOpenClawAvailability() {
        try {
            const r = await fetch('/api/agent/openclaw/availability');
            if (!r.ok) {
                state.openclawReady = null;
                state.openclawReason = `status ${r.status}`;
                if (state.snapshot) render('openclaw-refresh-error');
                return false;
            }
            const payload = await r.json();
            state.openclawReady = !!payload.ready;
            state.openclawReason = Array.isArray(payload.reasons) ? String(payload.reasons[0] || '') : '';
            if (state.snapshot) render('openclaw-refresh');
            return state.openclawReady;
        } catch (e) {
            state.openclawReady = null;
            state.openclawReason = String(e && e.message ? e.message : e || '');
            if (state.snapshot) render('openclaw-refresh-error');
            return false;
        }
    }

    async function fetchSnapshotRaw() {
        const r = await fetch('/api/agent/state');
        if (!r.ok) throw new Error(`state status ${r.status}`);
        const j = await r.json();
        if (!j || j.success !== true || !j.snapshot) throw new Error('invalid state payload');
        return j.snapshot;
    }

    async function fetchSnapshot() {
        const token = makeSnapshotToken();
        const snapshot = await fetchSnapshotRaw();
        applySnapshot(snapshot, 'http', token);
        return snapshot;
    }

    async function sendCommand(command, payload) {
        const requestId = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
        const t0 = performance.now();
        const body = { request_id: requestId, command, ...(payload || {}) };
        if (!body.lanlan_name) {
            const lanlanName = currentLanlanName();
            if (lanlanName) body.lanlan_name = lanlanName;
        }
        const r = await fetch('/api/agent/command', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!r.ok) throw new Error(`command status ${r.status}`);
        const j = await r.json();
        if (!j || j.success !== true) throw new Error(j?.error || 'command failed');
        const roundtrip = Number((performance.now() - t0).toFixed(2));
        console.log('[AgentUIv2Timing]', { requestId, command, roundtrip_ms: roundtrip, timing: j.timing || {} });
        return j;
    }

    function applyLocalAgentOff(reason) {
        const base = state.snapshot && typeof state.snapshot === 'object' ? state.snapshot : {};
        const snapshot = {
            ...base,
            server_online: base.server_online !== false,
            analyzer_enabled: false,
            flags: {
                agent_enabled: false,
                computer_use_enabled: false,
                browser_use_enabled: false,
                user_plugin_enabled: false,
                openclaw_enabled: false,
                openfang_enabled: false,
            },
            active_tasks: [],
            notification: null,
            updated_at: new Date().toISOString(),
        };
        state.pending.clear();
        state.optimistic = {};
        state.openclawReady = null;
        state.openclawReason = '';
        setGlobalBusy(false);
        state.snapshot = snapshot;
        // 本地快照只用于立即收起 UI，下一次权威快照必须能覆盖它。
        state.revision = -1;
        window._agentStatusSnapshot = snapshot;
        if (typeof window.stopAgentTaskPolling === 'function') {
            window.stopAgentTaskPolling();
        }
        render(reason || 'agent-off-local');
    }

    function applySnapshot(snapshot, source = 'ws', token) {
        if (!snapshot || typeof snapshot !== 'object') return;
        if (!isSnapshotTokenCurrent(token)) return;
        const rev = Number(snapshot.revision ?? -1);
        if (Number.isFinite(rev) && rev <= state.revision) return;

        // Detect precheck failure transitions: PENDING → specific failure reason.
        // Only fire the toast when the user actually opted into that capability —
        // otherwise background daemons (OpenFang / browser-use install / startup
        // LLM probe) flipping their own seeded PENDING → *_UNREACHABLE produce
        // bogus "猫爪预检失败" popups even when the agent is working fine.
        const CAP_TO_FLAG = {
            computer_use: 'computer_use_enabled',
            browser_use: 'browser_use_enabled',
            user_plugin: 'user_plugin_enabled',
            openclaw: 'openclaw_enabled',
            openfang: 'openfang_enabled',
        };
        const prevCaps = (state.snapshot && state.snapshot.capabilities) || {};
        const prevFlags = (state.snapshot && state.snapshot.flags) || {};
        const newCaps = snapshot.capabilities || {};
        const analyzerOn = !!snapshot.analyzer_enabled;
        const snapFlags = snapshot.flags || {};
        for (const [capName, capInfo] of Object.entries(newCaps)) {
            if (!capInfo || capInfo.ready) continue;
            if (!analyzerOn) continue;
            const flagKey = CAP_TO_FLAG[capName];
            // 用户是否真的请求了这个能力：新快照、上一帧快照、pending 队列、乐观更新里有任一为真即算。
            // 只看 snapFlags 会吞掉"用户刚开启 → 后端同帧检查失败立刻关掉"的失败提示。
            const userRequested = !!(
                flagKey && (
                    snapFlags[flagKey] ||
                    prevFlags[flagKey] ||
                    state.pending.has(flagKey) ||
                    state.optimistic[flagKey]
                )
            );
            if (!userRequested) continue;
            const prevInfo = prevCaps[capName];
            const wasPending = prevInfo && !prevInfo.ready && prevInfo.reason && prevInfo.reason.includes('PENDING');
            const nowFailed = capInfo.reason && !capInfo.reason.includes('PENDING');
            if (capName === 'openclaw') continue;
            if (wasPending && nowFailed && typeof window.showStatusToast === 'function' && window.t) {
                const precheckKey = `agent.precheck.${capInfo.reason}`;
                let reasonText = window.t(precheckKey);
                if (reasonText === precheckKey) reasonText = capInfo.reason;
                window.showStatusToast(window.t('agent.status.precheckFailed', { reason: reasonText }), 5000);
            }
        }

        state.snapshot = snapshot;
        if (Number.isFinite(rev)) state.revision = rev;
        window._agentStatusSnapshot = snapshot;
        if (snapshot.notification && typeof window.showStatusToast === 'function') {
            const msg = window.translateStatusMessage ? window.translateStatusMessage(snapshot.notification) : snapshot.notification;
            window.showStatusToast(msg, 4000);
        }
        render(source);
    }

    function render(source = 'render') {
        const { master, keyboard, browser, userPlugin, openfang, openclaw } = el();
        if (!master.length) return;
        const snap = state.snapshot;
        if (!snap) {
            master.forEach(m => {
                m.disabled = true;
                m.checked = false;
            });
            sync(master);
            [keyboard, browser, userPlugin, openfang, openclaw].forEach(list => {
                list.forEach(cb => {
                    cb.disabled = true;
                    cb.checked = false;
                });
                sync(list);
            });
            setStatus(window.t ? window.t('agent.status.connecting') : 'Agent状态同步中...', {
                stabilizeTutorialText: true
            });
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
            master.forEach(m => {
                m.checked = false;
                m.disabled = true;
                m.title = window.t ? window.t('settings.toggles.serverOffline') : 'Agent服务器未启动';
            });
            sync(master);
            [keyboard, browser, userPlugin, openfang, openclaw].forEach(list => {
                list.forEach(cb => {
                    cb.checked = false;
                    cb.disabled = true;
                });
                sync(list);
            });
            setStatus(window.t ? window.t('settings.toggles.serverOffline') : 'Agent服务器未启动');
            state.suppressChange = false;
            return;
        }

        master.forEach(m => {
            m.checked = effectiveAnalyzerEnabled;
            m.disabled = !!state.globalBusy;
            m.title = window.t ? window.t('settings.toggles.agentMaster') : 'Agent总开关';
        });
        sync(master);

        FLAG_KEYS.forEach((k) => {
            const flagElMap = {
                computer_use_enabled: keyboard,
                browser_use_enabled: browser,
                user_plugin_enabled: userPlugin,
                openfang_enabled: openfang,
            };
            const list = flagElMap[k] || [];
            if (!list.length) return;
            const ready = capabilityReady(snap, k);
            const reason = capabilityReason(snap, k);
            const disabledByPending = state.pending.has(k);
            const optimisticValue = Object.prototype.hasOwnProperty.call(state.optimistic, k)
                ? !!state.optimistic[k]
                : !!flags[k];
            const canUse = effectiveAnalyzerEnabled && ready;
            list.forEach(target => {
                target.checked = optimisticValue && canUse;
                target.disabled = !!state.globalBusy || disabledByPending || !effectiveAnalyzerEnabled || !ready;
                if (canUse) {
                    target.title = getName(k);
                } else if (!effectiveAnalyzerEnabled) {
                    target.title = window.t ? window.t('settings.toggles.masterRequired', { name: getName(k) }) : '请先开启Agent总开关';
                } else {
                    // Translate precheck reason code via i18n
                    const reasonText = translateAgentReasonCode(reason);
                    target.title = reasonText
                        ? (window.t ? window.t('agent.status.precheckFailed', { reason: reasonText }) : reasonText)
                        : (window.t ? window.t('settings.toggles.capabilityNotReady', { name: getName(k) }) : `${getName(k)}尚未就绪，点击尝试启用`);
                }
            });
            sync(list);
        });

        if (openclaw.length) {
            const capabilityOpenClawReady = capabilityReady(snap, 'openclaw_enabled');
            const capabilityOpenClawReason = capabilityReason(snap, 'openclaw_enabled');
            const disabledByPending = state.pending.has('openclaw_enabled');
            const activating = !!flags['openclaw_enabled'] && capabilityOpenClawReason && capabilityOpenClawReason.includes('PENDING');
            const ready = flags['openclaw_enabled'] && capabilityOpenClawReady
                ? true
                : (typeof state.openclawReady === 'boolean' ? state.openclawReady : capabilityOpenClawReady);
            const reason = flags['openclaw_enabled'] && capabilityOpenClawReady
                ? capabilityOpenClawReason
                : (state.openclawReason || capabilityOpenClawReason);
            const canUse = effectiveAnalyzerEnabled && (ready || activating) && !disabledByPending;
            const openclawName = window.t ? window.t('settings.toggles.openclawConnect') : 'OpenClaw';
            const optimisticVal = Object.prototype.hasOwnProperty.call(state.optimistic, 'openclaw_enabled')
                ? !!state.optimistic['openclaw_enabled']
                : !!flags['openclaw_enabled'];
            openclaw.forEach(cb => {
                cb.checked = optimisticVal && (canUse || disabledByPending);
                cb.disabled = !!state.globalBusy || disabledByPending || activating || !effectiveAnalyzerEnabled || !ready;
                if (disabledByPending || activating) {
                    cb.title = window.t ? window.t('settings.toggles.checking') : '切换中...';
                } else if (canUse) {
                    cb.title = openclawName;
                } else if (!effectiveAnalyzerEnabled) {
                    cb.title = window.t ? window.t('settings.toggles.masterRequired', { name: openclawName }) : '\u8bf7\u5148\u5f00\u542fAgent\u603b\u5f00\u5173';
                } else {
                    cb.title = formatOpenClawUnavailable(reason, openclawName);
                }
            });
            sync(openclaw);
        }

        const anyPending = Object.values(snap.capabilities || {}).some(
            c => c && typeof c.reason === 'string' && c.reason.includes('PENDING')
        );
        if (state.globalBusy) {
            setStatus(window.t ? window.t('settings.toggles.checking') : '已接受操作，切换中...', {
                stabilizeTutorialText: true
            });
        } else if (anyPending) {
            setStatus(window.t ? window.t('agent.status.connectivityCheck') : 'Agent LLM 连接检查中...', {
                stabilizeTutorialText: true
            });
        } else if (!analyzerEnabled) {
            setStatus(window.t ? window.t('agent.status.ready') : 'Agent服务器就绪', {
                stabilizeTutorialText: true
            });
        } else {
            setStatus(window.t ? window.t('agent.status.enabled') : 'Agent模式已开启', {
                stabilizeTutorialText: true
            });
        }
        state.suppressChange = false;


        if (typeof window.checkAndToggleTaskHUD === 'function') {
            window.checkAndToggleTaskHUD();
        }

    }

    function bindEvents() {
        const { master, taskhud, keyboard, browser, userPlugin, openfang, openclaw } = el();
        if (!master.length) return;
        const bindChangeOnce = (cb, key, handler) => {
            if (!cb) return;
            if (!cb.__agentUiV2BoundKeys) {
                Object.defineProperty(cb, '__agentUiV2BoundKeys', {
                    value: {},
                    configurable: true,
                });
            }
            if (cb.__agentUiV2BoundKeys[key]) return;
            cb.__agentUiV2BoundKeys[key] = true;
            cb.addEventListener('change', handler);
        };
        const clearProcessing = (cbs) => {
            (Array.isArray(cbs) ? cbs : [cbs]).forEach(cb => {
                if (!cb) return;
                cb._processing = false;
                cb._processingEvent = null;
                cb._processingTime = null;
            });
        };

        const onMasterChange = async (e) => {
            if (state.suppressChange) {
                clearProcessing(master);
                return;
            }
            const enabled = !!e.target.checked;
            const opSeq = ++state.masterOpSeq;
            state.pending.add('agent_enabled');
            state.optimistic.agent_enabled = enabled;
            setGlobalBusy(true, window.t ? window.t('settings.toggles.checking') : '已接受操作，切换中...');
            render('command');
            try {
                const cmdResult = await sendCommand('set_agent_enabled', { enabled });
                const isFreeVersion = !!(
                    cmdResult && (
                        cmdResult.is_free_version ||
                        (cmdResult.agent_api_gate && cmdResult.agent_api_gate.is_free_version) ||
                        (cmdResult.snapshot && cmdResult.snapshot.gate && cmdResult.snapshot.gate.is_free_version)
                    )
                );
                if (enabled && isFreeVersion && window.showAlert) {
                    const msg = window.t
                        ? window.t('agent.status.freeModelWarning')
                        : '由于限额问题，免费模型使用Agent模式容易阻塞，建议您切换至自费模型。\n\n如果您已经配置好自费API，请尝试重启NEKO。';
                    const title = window.t
                        ? window.t('agent.status.freeModelWarningTitle')
                        : '免费模型提示';
                    window.showAlert(msg, title);
                }
                if (opSeq === state.masterOpSeq) {
                    const ts = performance.now();
                    await fetchSnapshot().catch(() => { });
                    console.log('[AgentUIv2Timing]', { phase: 'fetch_snapshot_after_master', ms: Number((performance.now() - ts).toFixed(2)) });
                    if (opSeq === state.masterOpSeq && enabled) {
                        const openclawTs = performance.now();
                        await refreshOpenClawAvailability();
                        console.log('[AgentUIv2Timing]', { phase: 'refresh_openclaw_after_master', ms: Number((performance.now() - openclawTs).toFixed(2)) });
                    }
                }
            } catch (e) {
                if (opSeq === state.masterOpSeq) {
                    state.pending.delete('agent_enabled');
                    state.optimistic = {};
                    setGlobalBusy(false);
                    fetchSnapshot().catch(() => { });
                    if (typeof window.showStatusToast === 'function') {
                        window.showStatusToast(window.t ? window.t('agent.status.toggleFailed', { error: e.message }) : `Agent切换失败: ${e.message}`, 2500);
                    }
                }
                return;
            } finally {
                clearProcessing(master);
            }
            if (opSeq === state.masterOpSeq) {
                state.pending.delete('agent_enabled');
                state.optimistic = {};
                setGlobalBusy(false);
                render('command');
            }
        };
        master.forEach(m => bindChangeOnce(m, 'master', onMasterChange));

        const bindFlag = (cbs, key) => {
            if (!cbs || !cbs.length) return;
            cbs.forEach(cb => {
                bindChangeOnce(cb, `flag:${key}`, async (e) => {
                    if (state.suppressChange) {
                        clearProcessing(cbs);
                        return;
                    }
                    const value = !!e.target.checked;
                    const opToken = makeSnapshotToken();
                    state.pending.add(key);
                    state.optimistic[key] = value;
                    setGlobalBusy(true, window.t ? window.t('settings.toggles.checking') : '已接受操作，切换中...');
                    render('command');
                    try {
                        await sendCommand('set_flag', { key, value });
                        if (!isSnapshotTokenCurrent(opToken)) return;
                        const ts = performance.now();
                        await fetchSnapshot().catch(() => { });
                        console.log('[AgentUIv2Timing]', { phase: 'fetch_snapshot_after_flag', key, ms: Number((performance.now() - ts).toFixed(2)) });
                    } catch (err) {
                        if (!isSnapshotTokenCurrent(opToken)) return;
                        state.pending.delete(key);
                        state.optimistic = {};
                        setGlobalBusy(false);
                        fetchSnapshot().catch(() => { });
                        if (typeof window.showStatusToast === 'function') {
                            window.showStatusToast(window.t ? window.t('settings.toggles.toggleFailed', { name: getName(key), error: err.message }) : `${getName(key)}切换失败: ${err.message}`, 2500);
                        }
                        return;
                    } finally {
                        clearProcessing(cbs);
                    }
                    if (!isSnapshotTokenCurrent(opToken)) return;
                    state.pending.delete(key);
                    state.optimistic = {};
                    setGlobalBusy(false);
                    render('command');
                });
            });
        };

        bindFlag(keyboard, 'computer_use_enabled');
        bindFlag(browser, 'browser_use_enabled');
        bindFlag(userPlugin, 'user_plugin_enabled');
        bindFlag(openfang, 'openfang_enabled');

        openclaw.forEach(cb => {
            bindChangeOnce(cb, 'flag:openclaw_enabled', async (e) => {
                if (state.suppressChange) { clearProcessing(openclaw); return; }
                const value = !!e.target.checked;
                const opToken = makeSnapshotToken();
                const openclawName = window.t ? window.t('settings.toggles.openclawConnect') : 'OpenClaw';
                state.pending.add('openclaw_enabled');
                state.optimistic['openclaw_enabled'] = value;
                setGlobalBusy(true, window.t ? window.t('settings.toggles.checking') : '\u5df2\u63a5\u53d7\u64cd\u4f5c\uff0c\u5207\u6362\u4e2d...');
                render('command');
                try {
                    await sendCommand('set_flag', { key: 'openclaw_enabled', value });
                    if (!isSnapshotTokenCurrent(opToken)) return;
                    await fetchSnapshot().catch(() => {});
                } catch (err) {
                    if (!isSnapshotTokenCurrent(opToken)) return;
                    state.pending.delete('openclaw_enabled');
                    state.optimistic = {};
                    setGlobalBusy(false);
                    fetchSnapshot().catch(() => {});
                    if (typeof window.showStatusToast === 'function') {
                        window.showStatusToast(window.t ? window.t('settings.toggles.toggleFailed', { name: openclawName, error: err.message }) : `${openclawName}切换失败: ${err.message}`, 2500);
                    }
                    return;
                } finally {
                    clearProcessing(openclaw);
                }
                if (!isSnapshotTokenCurrent(opToken)) return;
                state.pending.delete('openclaw_enabled');
                state.optimistic = {};
                setGlobalBusy(false);
                render('command');
            });
        });

        if (state.globalEventsBound) return;
        state.globalEventsBound = true;

        window.addEventListener('neko-popup-opening', async () => {
            state.popupOpen = true;
            render('popup');
            refreshOpenClawAvailability().catch(() => {});
            if (!state.snapshot) {
                await fetchSnapshot().catch(() => render('popup'));
                return;
            }
            // Open popup without waiting, then refresh in background.
            fetchSnapshot().catch(() => { });
        });
        window.addEventListener('neko-popup-closed', () => {
            state.popupOpen = false;
        });
    }

    window.applyAgentStatusSnapshotToUI = (snapshot, meta) => {
        applySnapshot(snapshot, 'ws', meta);
    };
    window.isAgentStatusSnapshotCurrent = (meta) => isSnapshotTokenCurrent(meta);

    window.resetAgentUiForCharacterSwitch = async function resetAgentUiForCharacterSwitch() {
        const resetMasterSeq = ++state.masterOpSeq;
        state.snapshotGeneration += 1;
        state.expectedCharacter = currentLanlanName();
        const resetToken = makeSnapshotToken();
        applyLocalAgentOff('character-switch-local');
        try {
            const snapshot = await fetchSnapshotRaw();
            // 用户已经手动打开猫爪时，不允许切换后的慢刷新覆盖乐观开关状态。
            if (resetMasterSeq === state.masterOpSeq && state.pending.size === 0 && isSnapshotTokenCurrent(resetToken)) {
                applySnapshot(snapshot, 'character-switch-refresh', resetToken);
            }
        } catch (e) {
            if (resetMasterSeq === state.masterOpSeq && state.pending.size === 0 && isSnapshotTokenCurrent(resetToken)) {
                render('character-switch-refresh-failed');
            }
        }
        if (resetMasterSeq === state.masterOpSeq && state.pending.size === 0 && isSnapshotTokenCurrent(resetToken)) {
            await refreshOpenClawAvailability().catch(() => {});
        }
    };

    window.initAgentUiV2 = function initAgentUiV2() {
        const firstInit = !state.inited;
        state.inited = true;
        bindEvents();
        if (state.snapshot) {
            render(firstInit ? 'init-render' : 'rebind');
        }
        fetchSnapshot().catch(() => render(firstInit ? 'init' : 'rebind'));
        return true;
    };
})();
