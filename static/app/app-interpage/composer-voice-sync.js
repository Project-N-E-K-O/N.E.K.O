/**
 * app-interpage/composer-voice-sync.js
 * Inter-page / cross-tab communication.
 *
 * Handles BroadcastChannel dispatch, postMessage listeners, model hot-reload, UI commands, and overlay cleanup.
 * Dependencies loaded before these parts:
 * - app-state.js -> window.appState, window.appConst
 * Runtime dependencies available by the time handlers fire:
 * - window.showStatusToast
 * - window.stopMicCapture / window.clearAudioQueue
 * - window.live2dManager / window.vrmManager
 * - initLive2DModel / initVRMModel globals
 * Load all parts in filename order; this is a classic global script (no import/export).
 */
(function () {
    'use strict';

    window.appInterpage = window.appInterpage || {};
    const I = window.__appInterpageParts || (window.__appInterpageParts = {});
    var VOICE_CONFIG_SWITCH_STALE_MS = 45000;
    var _voiceConfigSwitchOps = {};
    var _voiceConfigSwitchWaiters = [];
    var _pendingVoiceChatComposerHiddenByLanlan = {};
    var VOICE_CHAT_COMPOSER_PENDING_STALE_MS = 30000;
    var _pendingGoodbyeChatComposerHiddenByLanlan = {};
    var GOODBYE_CHAT_COMPOSER_PENDING_STALE_MS = 30000;
    var _lastGoodbyeChatComposerHiddenStateTimestamp = 0;

    I.getCurrentLanlanName = function getCurrentLanlanName() {
        try {
            if (window.appState && typeof window.appState.lanlan_name === 'string' && window.appState.lanlan_name) {
                return window.appState.lanlan_name;
            }
        } catch (_) {}
        return (window.lanlan_config && window.lanlan_config.lanlan_name) || '';
    }

    function isVoiceChatDesktopLayout() {
        return !(window.appUtils && typeof window.appUtils.isMobile === 'function' && window.appUtils.isMobile());
    }

    I.shouldKeepVoiceComposerHidden = function shouldKeepVoiceComposerHidden() {
        return isVoiceChatDesktopLayout() && !!(
            (I.S && (I.S.isRecording || I.S.voiceChatActive || I.S.voiceStartPending)) ||
            window.isMicStarting
        );
    }

    function applyVoiceChatComposerHidden(hidden) {
        hidden = !!hidden;
        if (window.reactChatWindowHost && typeof window.reactChatWindowHost.setComposerHidden === 'function') {
            window.reactChatWindowHost.setComposerHidden(hidden);
        }
        var textInputArea = document.getElementById('text-input-area');
        if (textInputArea) {
            if (hidden) {
                textInputArea.classList.add('hidden');
            } else {
                textInputArea.classList.remove('hidden');
            }
        }
    }

    function getVoiceChatComposerHiddenElectronBridge() {
        var bridge = window.nekoElectronVoiceChatComposerHidden;
        return bridge && typeof bridge.send === 'function' ? bridge : null;
    }

    I.postVoiceChatComposerHiddenElectron = function postVoiceChatComposerHiddenElectron(payload) {
        var bridge = getVoiceChatComposerHiddenElectronBridge();
        if (!bridge) return false;
        try {
            bridge.send(payload || {});
            return true;
        } catch (err) {
            console.warn('[VoiceChat] Electron composer hidden bridge failed:', err);
            return false;
        }
    }

    function postVoiceChatComposerHiddenPayload(payload) {
        I.postInterpageMessage(payload);
        I.postVoiceChatComposerHiddenElectron(payload);
    }

    function getVoiceChatComposerHiddenMessageTimestamp(data) {
        var timestamp = Number(data && data.timestamp);
        return Number.isFinite(timestamp) ? timestamp : Date.now();
    }

    function prunePendingVoiceChatComposerHiddenMessages(now) {
        now = now || Date.now();
        Object.keys(_pendingVoiceChatComposerHiddenByLanlan).forEach(function (lanlanName) {
            var data = _pendingVoiceChatComposerHiddenByLanlan[lanlanName];
            if (!data || now - getVoiceChatComposerHiddenMessageTimestamp(data) > VOICE_CHAT_COMPOSER_PENDING_STALE_MS) {
                delete _pendingVoiceChatComposerHiddenByLanlan[lanlanName];
            }
        });
    }

    function rememberPendingVoiceChatComposerHiddenMessage(data) {
        if (!data || !data.lanlan_name) return false;
        var lanlanName = String(data.lanlan_name);
        var timestamp = getVoiceChatComposerHiddenMessageTimestamp(data);
        prunePendingVoiceChatComposerHiddenMessages(timestamp);
        var previous = _pendingVoiceChatComposerHiddenByLanlan[lanlanName];
        if (!previous || timestamp >= getVoiceChatComposerHiddenMessageTimestamp(previous)) {
            _pendingVoiceChatComposerHiddenByLanlan[lanlanName] = Object.assign({}, data, {
                timestamp: timestamp
            });
        }
        return true;
    }

    I.applyVoiceComposerHiddenFromActive = function applyVoiceComposerHiddenFromActive(active) {
        var requestedHidden = !!active;
        if (I.S) {
            I.S.voiceChatActive = requestedHidden;
        }
        var effectiveHidden = requestedHidden || (!requestedHidden && I.shouldKeepVoiceComposerHidden());
        if (I.S) {
            I.S.voiceChatActive = effectiveHidden;
        }
        applyVoiceChatComposerHidden(effectiveHidden);
        return effectiveHidden;
    }

    function isVoiceChatComposerHiddenMessageForCurrentLanlan(data) {
        if (!data || !data.lanlan_name) return true;
        var currentName = I.getCurrentLanlanName();
        return !!currentName && data.lanlan_name === currentName;
    }

    I.handleVoiceChatComposerHiddenMessage = function handleVoiceChatComposerHiddenMessage(data) {
        if (!data || data.action !== 'voice_chat_active') return false;
        if (data.lanlan_name && !I.getCurrentLanlanName()) {
            rememberPendingVoiceChatComposerHiddenMessage(data);
            return true;
        }
        if (!isVoiceChatComposerHiddenMessageForCurrentLanlan(data)) return true;
        I.applyVoiceComposerHiddenFromActive(data.active);
        return true;
    }

    I.consumePendingVoiceChatComposerHiddenMessage = function consumePendingVoiceChatComposerHiddenMessage(lanlanName) {
        var currentName = lanlanName || I.getCurrentLanlanName();
        if (!currentName) return false;
        prunePendingVoiceChatComposerHiddenMessages(Date.now());
        var data = _pendingVoiceChatComposerHiddenByLanlan[currentName];
        if (!data) return false;
        delete _pendingVoiceChatComposerHiddenByLanlan[currentName];
        I.applyVoiceComposerHiddenFromActive(data.active);
        return true;
    }

    function readGoodbyeChatComposerHidden() {
        try {
            if (typeof window.isNekoGoodbyeModeActive === 'function'
                && window.isNekoGoodbyeModeActive()) {
                return true;
            }
        } catch (_) {}
        if (window.__nekoGoodbyeChatComposerHidden
            && typeof window.__nekoGoodbyeChatComposerHidden === 'object'
            && window.__nekoGoodbyeChatComposerHidden.hidden === true) {
            return true;
        }
        return !!(
            (window.live2dManager && window.live2dManager._goodbyeClicked)
            || (window.vrmManager && window.vrmManager._goodbyeClicked)
            || (window.mmdManager && window.mmdManager._goodbyeClicked)
            || (window.__nekoGoodbyeSilentState && window.__nekoGoodbyeSilentState.active === true)
        );
    }

    function normalizeCatLocalChatSnapshot(data) {
        var source = data && typeof data === 'object' ? data : {};
        return {
            active: source.cat_active === true,
            tier: typeof source.cat_tier === 'string' ? source.cat_tier : 'none',
            enteredAt: Number.isFinite(Number(source.cat_entered_at)) ? Number(source.cat_entered_at) : 0,
            items: Array.isArray(source.cat_items) ? source.cat_items : [],
            updatedAt: Number.isFinite(Number(source.timestamp)) ? Number(source.timestamp) : 0
        };
    }

    function readCatLocalChatSnapshot() {
        var manager = window.nekoCatLocalChatManager;
        if (manager && typeof manager.getSnapshot === 'function') {
            try {
                return manager.getSnapshot();
            } catch (_) {}
        }
        return { active: false, tier: 'none', enteredAt: 0, items: [] };
    }

    I.applyGoodbyeChatComposerHidden = function applyGoodbyeChatComposerHidden(hidden, reason, payload) {
        hidden = !!hidden;
        var detail = {
            hidden: hidden,
            reason: reason || (hidden ? 'goodbye' : 'return'),
            timestamp: Date.now()
        };
        window.__nekoGoodbyeChatComposerHidden = detail;
        if (window.reactChatWindowHost && typeof window.reactChatWindowHost.setGoodbyeComposerHidden === 'function') {
            window.reactChatWindowHost.setGoodbyeComposerHidden(hidden, detail.reason);
        } else {
            try {
                window.dispatchEvent(new CustomEvent('react-chat-window:set-goodbye-composer-hidden', {
                    detail: detail
                }));
            } catch (_) {}
        }
        var catSnapshot = normalizeCatLocalChatSnapshot(payload);
        window.__nekoCatLocalChatState = catSnapshot;
        try {
            window.dispatchEvent(new CustomEvent('neko:cat-local-chat-state', {
                detail: catSnapshot
            }));
        } catch (_) {}
    }

    I.getGoodbyeChatComposerHiddenElectronBridge = function getGoodbyeChatComposerHiddenElectronBridge() {
        var bridge = window.nekoElectronGoodbyeChatComposerHidden;
        return bridge && typeof bridge.send === 'function' ? bridge : null;
    }

    I.postGoodbyeChatComposerHiddenElectron = function postGoodbyeChatComposerHiddenElectron(payload) {
        var bridge = I.getGoodbyeChatComposerHiddenElectronBridge();
        if (!bridge) return false;
        try {
            bridge.send(payload || {});
            return true;
        } catch (err) {
            console.warn('[Goodbye] Electron composer hidden bridge failed:', err);
            return false;
        }
    }

    function postGoodbyeChatComposerHiddenPayload(payload) {
        if (I.nekoBroadcastChannel) {
            I.nekoBroadcastChannel.postMessage(payload);
        }
        I.postGoodbyeChatComposerHiddenElectron(payload);
    }

    I.requestGoodbyeChatComposerHiddenState = function requestGoodbyeChatComposerHiddenState(reason) {
        var lanlanName = I.getCurrentLanlanName();
        if (!lanlanName) return false;
        postGoodbyeChatComposerHiddenPayload({
            action: 'request_goodbye_chat_composer_hidden',
            reason: reason || 'request-goodbye-chat-composer-hidden',
            lanlan_name: lanlanName,
            timestamp: Date.now()
        });
        return true;
    }

    function isGoodbyeChatComposerHiddenMessageForCurrentLanlan(data) {
        if (!data || !data.lanlan_name) return false;
        var currentName = I.getCurrentLanlanName();
        return !!currentName && data.lanlan_name === currentName;
    }

    function rememberPendingGoodbyeChatComposerHiddenMessage(data) {
        if (!data || !data.lanlan_name) return false;
        var lanlanName = String(data.lanlan_name);
        var timestamp = Number(data.timestamp);
        if (!Number.isFinite(timestamp)) timestamp = Date.now();
        Object.keys(_pendingGoodbyeChatComposerHiddenByLanlan).forEach(function (name) {
            var pending = _pendingGoodbyeChatComposerHiddenByLanlan[name];
            var pendingTimestamp = Number(pending && pending.timestamp);
            if (!Number.isFinite(pendingTimestamp)
                || timestamp - pendingTimestamp > GOODBYE_CHAT_COMPOSER_PENDING_STALE_MS) {
                delete _pendingGoodbyeChatComposerHiddenByLanlan[name];
            }
        });
        var previous = _pendingGoodbyeChatComposerHiddenByLanlan[lanlanName];
        if (!previous || timestamp >= Number(previous.timestamp || 0)) {
            _pendingGoodbyeChatComposerHiddenByLanlan[lanlanName] = Object.assign({}, data, {
                timestamp: timestamp
            });
        }
        return true;
    }

    I.consumePendingGoodbyeChatComposerHiddenMessage = function consumePendingGoodbyeChatComposerHiddenMessage(lanlanName) {
        var currentName = lanlanName || I.getCurrentLanlanName();
        if (!currentName) return false;
        var data = _pendingGoodbyeChatComposerHiddenByLanlan[currentName];
        if (!data) return false;
        delete _pendingGoodbyeChatComposerHiddenByLanlan[currentName];
        var timestamp = Number(data.timestamp);
        if (Number.isFinite(timestamp)
            && Date.now() - timestamp > GOODBYE_CHAT_COMPOSER_PENDING_STALE_MS) {
            return false;
        }
        I.applyGoodbyeChatComposerHidden(!!data.hidden, data.reason || 'config-injected', data);
        return true;
    };

    I.handleGoodbyeChatComposerHiddenMessage = function handleGoodbyeChatComposerHiddenMessage(data, via) {
        if (!data || !data.action) return false;
        if (data.action === 'goodbye_chat_composer_hidden') {
            if (data.lanlan_name && !I.getCurrentLanlanName()) {
                rememberPendingGoodbyeChatComposerHiddenMessage(data);
                return true;
            }
            if (!isGoodbyeChatComposerHiddenMessageForCurrentLanlan(data)) return true;
            I.applyGoodbyeChatComposerHidden(!!data.hidden, data.reason || via || 'broadcast', data);
            return true;
        }
        if (data.action === 'request_goodbye_chat_composer_hidden') {
            if (I.isStandaloneChatPage()) return true;
            if (!isGoodbyeChatComposerHiddenMessageForCurrentLanlan(data)) return true;
            I.postGoodbyeChatComposerHiddenState(undefined, 'request-goodbye-chat-composer-hidden');
            return true;
        }
        if (data.action === 'cat_local_text_submit') {
            if (I.isStandaloneChatPage()) return true;
            if (!isGoodbyeChatComposerHiddenMessageForCurrentLanlan(data)) return true;
            try {
                window.dispatchEvent(new CustomEvent('neko:cat-local-chat-submit-request', {
                    detail: {
                        text: typeof data.text === 'string' ? data.text : '',
                        requestId: typeof data.request_id === 'string' ? data.request_id : '',
                        enteredAt: Number(data.cat_entered_at)
                    }
                }));
            } catch (_) {}
            return true;
        }
        return false;
    }

    I.postCatLocalTextSubmit = function postCatLocalTextSubmit(payload) {
        var detail = payload && typeof payload === 'object' ? payload : {};
        var text = typeof detail.text === 'string' ? detail.text.trim() : '';
        var requestId = typeof detail.requestId === 'string' ? detail.requestId.trim() : '';
        var lanlanName = I.getCurrentLanlanName();
        if (!text || !requestId || !lanlanName) return false;
        var message = {
            action: 'cat_local_text_submit',
            text: text,
            request_id: requestId,
            cat_entered_at: Number(detail.enteredAt) || 0,
            lanlan_name: lanlanName,
            timestamp: Date.now()
        };
        if (!I.isStandaloneChatPage()) {
            try {
                window.dispatchEvent(new CustomEvent('neko:cat-local-chat-submit-request', {
                    detail: {
                        text: text,
                        requestId: requestId,
                        enteredAt: message.cat_entered_at
                    }
                }));
                return true;
            } catch (_) {
                return false;
            }
        }
        postGoodbyeChatComposerHiddenPayload(message);
        return true;
    }

    I.postGoodbyeChatComposerHiddenState = function postGoodbyeChatComposerHiddenState(hidden, reason) {
        var lanlanName = I.getCurrentLanlanName();
        var nextHidden = hidden === undefined ? readGoodbyeChatComposerHidden() : !!hidden;
        var nextReason = reason || (nextHidden ? 'goodbye' : 'return');
        var catSnapshot = readCatLocalChatSnapshot();
        var stateTimestamp = Math.max(
            Date.now(),
            _lastGoodbyeChatComposerHiddenStateTimestamp + 1
        );
        _lastGoodbyeChatComposerHiddenStateTimestamp = stateTimestamp;
        var statePayload = {
            action: 'goodbye_chat_composer_hidden',
            hidden: nextHidden,
            reason: nextReason,
            lanlan_name: lanlanName,
            cat_active: catSnapshot.active === true,
            cat_tier: catSnapshot.active === true ? catSnapshot.tier : 'none',
            cat_entered_at: catSnapshot.active === true ? Number(catSnapshot.enteredAt) || 0 : 0,
            cat_items: catSnapshot.active === true && Array.isArray(catSnapshot.items) ? catSnapshot.items : [],
            timestamp: stateTimestamp
        };
        I.applyGoodbyeChatComposerHidden(nextHidden, nextReason, statePayload);
        if (!lanlanName) return;
        postGoodbyeChatComposerHiddenPayload(statePayload);
    }

    function pruneVoiceConfigSwitchOps(now) {
        now = now || Date.now();
        Object.keys(_voiceConfigSwitchOps).forEach(function (opId) {
            var op = _voiceConfigSwitchOps[opId];
            if (!op || now - (op.updatedAt || op.startedAt || 0) > VOICE_CONFIG_SWITCH_STALE_MS) {
                delete _voiceConfigSwitchOps[opId];
            }
        });
    }

    I.isVoiceConfigSwitching = function isVoiceConfigSwitching() {
        pruneVoiceConfigSwitchOps(Date.now());
        return Object.keys(_voiceConfigSwitchOps).length > 0;
    }

    function notifyVoiceConfigSwitchWaiters() {
        _voiceConfigSwitchWaiters.slice().forEach(function (waiter) {
            try { waiter(); } catch (_) { /* 等待器异常不影响状态同步 */ }
        });
    }

    function isVoiceConfigMessageForCurrentLanlan(data) {
        var currentName = I.getCurrentLanlanName();
        // 没带 lanlan_name 的广播视为通用通知，所有窗口都接受。
        // 带了 lanlan_name 但本窗口 config 还没注入（currentName 空）时拒绝：
        // 否则别的角色的 op 会被存入 _voiceConfigSwitchOps，配好后又收不到对应的
        // active=false（被 lanlan_name mismatch 滤掉），导致 waitForVoiceConfigSwitchReady
        // 在最长 30s 超时前一直阻塞，触发误报的"音色切换超时"。
        if (!data.lanlan_name) return true;
        return !!currentName && data.lanlan_name === currentName;
    }

    I.handleVoiceConfigSwitchingMessage = function handleVoiceConfigSwitchingMessage(data) {
        if (!data || !isVoiceConfigMessageForCurrentLanlan(data)) return;
        var now = Date.now();
        var opId = String(data.op_id || data.operation_id || data.lanlan_name || 'voice_config_switch');
        var active = !!data.active;

        if (active) {
            pruneVoiceConfigSwitchOps(now);
            _voiceConfigSwitchOps[opId] = {
                lanlanName: data.lanlan_name || '',
                startedAt: _voiceConfigSwitchOps[opId]?.startedAt || now,
                updatedAt: now
            };
        } else if (data.op_id || data.operation_id) {
            delete _voiceConfigSwitchOps[opId];
        } else {
            Object.keys(_voiceConfigSwitchOps).forEach(function (knownOpId) {
                var op = _voiceConfigSwitchOps[knownOpId];
                if (!data.lanlan_name || !op || !op.lanlanName || op.lanlanName === data.lanlan_name) {
                    delete _voiceConfigSwitchOps[knownOpId];
                }
            });
        }

        notifyVoiceConfigSwitchWaiters();
        window.dispatchEvent(new CustomEvent('neko:voice-config-switching-changed', {
            detail: { active: I.isVoiceConfigSwitching(), lanlan_name: data.lanlan_name || '' }
        }));
    }

    I.waitForVoiceConfigSwitchReady = function waitForVoiceConfigSwitchReady(options) {
        options = options || {};
        var timeoutMs = Number.isFinite(options.timeoutMs) ? Math.max(0, options.timeoutMs) : 30000;
        var stableMs = Number.isFinite(options.stableMs) ? Math.max(0, options.stableMs) : 0;
        var onWaiting = typeof options.onWaiting === 'function' ? options.onWaiting : null;
        var waitingNotified = false;

        return new Promise(function (resolve) {
            var done = false;
            var stableTimer = null;
            var timeoutTimer = null;

            function cleanup() {
                done = true;
                if (stableTimer) clearTimeout(stableTimer);
                if (timeoutTimer) clearTimeout(timeoutTimer);
                stableTimer = null;
                timeoutTimer = null;
                _voiceConfigSwitchWaiters = _voiceConfigSwitchWaiters.filter(function (waiter) {
                    return waiter !== evaluate;
                });
            }

            function resolveReady(timedOut) {
                cleanup();
                resolve({ timedOut: !!timedOut });
            }

            function notifyWaitingOnce() {
                if (!waitingNotified && onWaiting) {
                    waitingNotified = true;
                    try { onWaiting(); } catch (_) { /* 提示失败不影响启动等待 */ }
                }
            }

            function evaluate() {
                if (done) return;
                if (stableTimer) {
                    clearTimeout(stableTimer);
                    stableTimer = null;
                }
                if (I.isVoiceConfigSwitching()) {
                    notifyWaitingOnce();
                    return;
                }
                if (stableMs <= 0) {
                    resolveReady(false);
                    return;
                }
                stableTimer = setTimeout(function () {
                    stableTimer = null;
                    if (I.isVoiceConfigSwitching()) {
                        notifyWaitingOnce();
                        return;
                    }
                    resolveReady(false);
                }, stableMs);
            }

            _voiceConfigSwitchWaiters.push(evaluate);
            if (timeoutMs > 0) {
                timeoutTimer = setTimeout(function () {
                    resolveReady(true);
                }, timeoutMs);
            }
            evaluate();
        });
    }

    /**
     * 同步本地聊天输入栏状态，并广播给其它窗口。
     * app-buttons.js / app-audio-capture.js 会在语音开始和结束时调用。
     *
     * @param {boolean} hidden - true 表示收起输入栏；false 表示允许展开输入栏
     */
    I.syncVoiceChatComposerHidden = function syncVoiceChatComposerHidden(hidden) {
        var effectiveHidden = I.applyVoiceComposerHiddenFromActive(hidden);
        // 同步给其它页面（chat.html ↔ index.html）
        postVoiceChatComposerHiddenPayload({
            action: 'voice_chat_active',
            active: effectiveHidden,
            lanlan_name: I.getCurrentLanlanName(),
            timestamp: Date.now()
        });
    }

    // =====================================================================
    // BroadcastChannel initialisation
    // =====================================================================

    Object.assign(window.appInterpage, I.mod || {});
})();
