/**
 * app-websocket.js -- WebSocket connection, heartbeat, reconnect & message dispatch
 * Extracted from app.js lines 434-1617.
 *
 * Depends on:
 *   window.appState   (S) -- shared mutable state
 *   window.appConst   (C) -- frozen constants
 *   window.appAudioPlayback  -- audio playback helpers
 *   window.appChat           -- chat rendering helpers
 *   window.appScreen         -- screen sharing helpers
 *   window.appUi             -- UI helpers (toasts, buttons)
 */
(function () {
    'use strict';

    const mod = {};
    const S = window.appState;
    const C = window.appConst;
    const USER_ACTIVITY_CANCEL_GRACE_MS = 700;
    const GREETING_CHECK_RETRY_BASE_MS = 800;
    const GREETING_CHECK_RETRY_MAX_MS = 5000;
    let _pendingUserActivityCancelTimer = 0;
    let _pendingUserActivityCancelTurnId = null;

    // ---- DOM element shortcuts (resolved lazily / once) ----
    function $id(id) { return document.getElementById(id); }
    function micButton()          { return $id('micButton'); }
    function muteButton()         { return $id('muteButton'); }
    function screenButton()       { return $id('screenButton'); }
    function stopButton()         { return $id('stopButton'); }
    function resetSessionButton() { return $id('resetSessionButton'); }
    function returnSessionButton(){ return $id('returnSessionButton'); }
    function textInputBox()       { return $id('textInputBox'); }
    function textSendButton()     { return $id('textSendButton'); }
    function screenshotButton()   { return $id('screenshotButton'); }
    function chatContainer()      { return $id('chatContainer'); }

    function isHomeTutorialLockedForGreeting() {
        try {
            if (typeof window.isNekoHomeTutorialBlockingGreeting === 'function') {
                return window.isNekoHomeTutorialBlockingGreeting() === true;
            }
            if (typeof window.isNekoHomeTutorialInteractionLocked === 'function') {
                return window.isNekoHomeTutorialInteractionLocked() === true;
            }
        } catch (_) {}
        return false;
    }

    function sendHomeTutorialState(reason) {
        if (!S.socket || S.socket.readyState !== WebSocket.OPEN) return;
        try {
            S.socket.send(JSON.stringify({
                action: 'home_tutorial_state',
                blocking_greeting: isHomeTutorialLockedForGreeting(),
                reason: reason || 'state-sync',
                timestamp: Date.now(),
            }));
        } catch (error) {
            console.warn('[HomeTutorial] failed to sync tutorial state:', error);
        }
    }

    function normalizeAssistantTurnId(turnId) {
        if (turnId === undefined || turnId === null || turnId === '') {
            return null;
        }
        return String(turnId);
    }

    function allocateAssistantTurnId(serverTurnId) {
        var normalized = normalizeAssistantTurnId(serverTurnId);
        if (normalized) {
            return normalized;
        }
        S.assistantTurnSeq = (S.assistantTurnSeq || 0) + 1;
        return 'local-' + S.assistantTurnSeq;
    }

    function emitAssistantLifecycleEvent(eventName, detail) {
        window.dispatchEvent(new CustomEvent(eventName, {
            detail: Object.assign({
                timestamp: Date.now()
            }, detail || {})
        }));
    }

    function getRenderableAssistantChunkText(text) {
        return String(text || '')
            .replace(/\[play_music:[^\]]*(\]|$)/g, '')
            .trim();
    }

    function getAssistantDisplayName() {
        var name = '';
        try {
            name = (window.__NEKO_TUTORIAL_ASSISTANT_NAME_OVERRIDE__
                || (window.lanlan_config && window.lanlan_config.lanlan_name)
                || window._currentCatgirl
                || window.currentCatgirl
                || '');
        } catch (_) {}
        return String(name || '').trim() || 'Neko';
    }

    function appendAssistantStatusMessage(text) {
        var cleanText = getRenderableAssistantChunkText(text);
        if (!cleanText) return false;

        var timeStr = (typeof window.getCurrentTimeString === 'function')
            ? window.getCurrentTimeString()
            : new Date().toLocaleTimeString('en-US', {
                hour12: false,
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit'
            });
        var assistantName = getAssistantDisplayName();
        var messageId = 'assistant-status-' + Date.now() + '-' + Math.random().toString(36).slice(2, 8);
        var appendedToReact = false;

        if (window.reactChatWindowHost && typeof window.reactChatWindowHost.appendMessage === 'function') {
            try {
                var avatarUrl = '';
                if (window.appChatAvatar && typeof window.appChatAvatar.getCurrentAvatarDataUrl === 'function') {
                    avatarUrl = window.appChatAvatar.getCurrentAvatarDataUrl() || '';
                }
                window.reactChatWindowHost.appendMessage({
                    id: messageId,
                    role: 'assistant',
                    author: assistantName,
                    time: timeStr,
                    createdAt: Date.now(),
                    avatarLabel: assistantName ? String(assistantName).slice(0, 1).toUpperCase() : undefined,
                    avatarUrl: avatarUrl || undefined,
                    blocks: [{ type: 'text', text: cleanText }],
                    status: 'failed'
                });
                appendedToReact = true;
            } catch (reactAppendError) {
                console.warn('[WS] failed to append assistant status to React chat:', reactAppendError);
            }
        }

        if (appendedToReact) {
            window.currentTurnGeminiBubbles = [{
                dataset: { reactChatMessageId: messageId },
                parentNode: null,
                isConnected: true,
                textContent: '[' + timeStr + '] \u{1F380} ' + cleanText,
                nodeType: 1
            }];
            return true;
        }

        var messageDiv = document.createElement('div');
        messageDiv.classList.add('message', 'gemini');
        messageDiv.textContent = '[' + timeStr + '] \u{1F380} ' + cleanText;
        var cc = chatContainer();
        if (!cc) return false;
        cc.appendChild(messageDiv);
        window.currentTurnGeminiBubbles = [messageDiv];
        cc.scrollTop = cc.scrollHeight;
        return true;
    }

    function websocketTraceEnabled() {
        return window.NEKO_DEBUG_BUBBLE_LIFECYCLE === true;
    }

    function logAssistantLifecycle(label, extra) {
        if (!websocketTraceEnabled()) {
            return;
        }
        console.log('[WSTrace]', label, Object.assign({
            assistantTurnId: S.assistantTurnId,
            pendingTurnServerId: S.assistantPendingTurnServerId,
            assistantTurnAwaitingBubble: S.assistantTurnAwaitingBubble,
            assistantTurnCompletedId: S.assistantTurnCompletedId,
            assistantSpeechActiveTurnId: S.assistantSpeechActiveTurnId,
            currentPlayingSpeechId: S.currentPlayingSpeechId,
            pendingAudioMetaQueue: S.pendingAudioChunkMetaQueue.length,
            incomingAudioBlobQueue: S.incomingAudioBlobQueue.length
        }, extra || {}));
    }

    function clearPendingAssistantTurnStart() {
        S.assistantPendingTurnServerId = null;
        S.assistantTurnAwaitingBubble = false;
    }

    function clearPendingUserActivityCancel() {
        if (_pendingUserActivityCancelTimer) {
            clearTimeout(_pendingUserActivityCancelTimer);
            _pendingUserActivityCancelTimer = 0;
        }
        _pendingUserActivityCancelTurnId = null;
    }

    function hasBufferedAssistantAudioForTurn(turnId) {
        var normalizedTurnId = normalizeAssistantTurnId(turnId);
        if (!normalizedTurnId) {
            return false;
        }

        if (S.scheduledSources.some(function (source) {
            return normalizeAssistantTurnId(source && source._nekoAssistantTurnId) === normalizedTurnId;
        })) {
            return true;
        }

        if (S.audioBufferQueue.some(function (item) {
            return normalizeAssistantTurnId(item && item.turnId) === normalizedTurnId;
        })) {
            return true;
        }

        return S.incomingAudioBlobQueue.some(function (item) {
            return item &&
                !item.shouldSkip &&
                item.epoch === S.incomingAudioEpoch &&
                normalizeAssistantTurnId(item.turnId) === normalizedTurnId;
        });
    }

    function hasPendingAssistantAudioHeaderForTurn(turnId) {
        var normalizedTurnId = normalizeAssistantTurnId(turnId);
        if (!normalizedTurnId) {
            return false;
        }

        return S.pendingAudioChunkMetaQueue.some(function (item) {
            return item &&
                !item.shouldSkip &&
                item.epoch === S.incomingAudioEpoch &&
                normalizeAssistantTurnId(item.turnId) === normalizedTurnId;
        });
    }

    function resolveAssistantLifecycleTurnId(turnId) {
        return normalizeAssistantTurnId(
            turnId ||
            S.assistantTurnId ||
            S.assistantPendingTurnServerId ||
            S.assistantTurnCompletedId ||
            S.assistantSpeechActiveTurnId
        );
    }

    function ensureAssistantTurnStarted(source, serverTurnId) {
        if (S.assistantTurnId) {
            clearPendingAssistantTurnStart();
            logAssistantLifecycle('ensureAssistantTurnStarted:reuse_existing', {
                source: source || 'visible_gemini_bubble',
                serverTurnId: normalizeAssistantTurnId(serverTurnId)
            });
            return S.assistantTurnId;
        }
        if (!S.assistantTurnAwaitingBubble && serverTurnId === undefined) {
            logAssistantLifecycle('ensureAssistantTurnStarted:skip', {
                source: source || 'visible_gemini_bubble'
            });
            return null;
        }

        S.assistantTurnId = allocateAssistantTurnId(
            serverTurnId === undefined ? S.assistantPendingTurnServerId : serverTurnId
        );
        S.assistantTurnStartedAt = Date.now();
        clearPendingAssistantTurnStart();
        emitAssistantLifecycleEvent('neko-assistant-turn-start', {
            turnId: S.assistantTurnId,
            source: source || 'visible_gemini_bubble'
        });
        logAssistantLifecycle('ensureAssistantTurnStarted:emitted', {
            source: source || 'visible_gemini_bubble',
            serverTurnId: normalizeAssistantTurnId(serverTurnId),
            turnId: S.assistantTurnId
        });
        return S.assistantTurnId;
    }

    function emitAssistantSpeechCancel(source) {
        var currentTurnId = resolveAssistantLifecycleTurnId();
        S.assistantSpeechActiveTurnId = null;
        logAssistantLifecycle('emitAssistantSpeechCancel', {
            source: source,
            turnId: currentTurnId
        });
        if (currentTurnId) {
            emitAssistantLifecycleEvent('neko-assistant-speech-cancel', {
                turnId: currentTurnId,
                source: source
            });
        } else {
            emitAssistantLifecycleEvent('neko-assistant-speech-cancel', {
                source: source
            });
        }
    }

    function applyUserActivityCancel(interruptedSpeechId, source) {
        clearPendingUserActivityCancel();
        emitAssistantSpeechCancel(source || 'user_activity');
        S.assistantTurnId = null;
        clearPendingAssistantTurnStart();
        S.interruptedSpeechId = interruptedSpeechId || null;
        S.pendingDecoderReset = true;
        S.skipNextAudioBlob = false;
        S.incomingAudioEpoch += 1;
        S.incomingAudioBlobQueue = [];
        S.pendingAudioChunkMetaQueue = [];

        if (typeof window.clearAudioQueueWithoutDecoderReset === 'function') {
            window.clearAudioQueueWithoutDecoderReset();
        }
    }

    function shouldDelayUserActivityCancel(turnId) {
        var normalizedTurnId = normalizeAssistantTurnId(turnId);
        if (!normalizedTurnId) {
            return false;
        }

        if (normalizeAssistantTurnId(S.assistantSpeechActiveTurnId) === normalizedTurnId) {
            return false;
        }

        if (hasBufferedAssistantAudioForTurn(normalizedTurnId)) {
            return false;
        }

        if (hasPendingAssistantAudioHeaderForTurn(normalizedTurnId)) {
            return true;
        }

        return normalizeAssistantTurnId(S.assistantTurnCompletedId) === normalizedTurnId;
    }

    function scheduleUserActivityCancel(turnId, interruptedSpeechId) {
        clearPendingUserActivityCancel();

        var normalizedTurnId = normalizeAssistantTurnId(turnId);
        if (!normalizedTurnId) {
            applyUserActivityCancel(interruptedSpeechId, 'user_activity');
            return;
        }

        _pendingUserActivityCancelTurnId = normalizedTurnId;
        logAssistantLifecycle('scheduleUserActivityCancel:scheduled', {
            turnId: normalizedTurnId,
            delayMs: USER_ACTIVITY_CANCEL_GRACE_MS
        });
        _pendingUserActivityCancelTimer = window.setTimeout(function () {
            var pendingTurnId = _pendingUserActivityCancelTurnId;
            _pendingUserActivityCancelTimer = 0;
            _pendingUserActivityCancelTurnId = null;

            if (!pendingTurnId || pendingTurnId !== normalizedTurnId) {
                logAssistantLifecycle('scheduleUserActivityCancel:skip_turn_mismatch', {
                    turnId: normalizedTurnId
                });
                return;
            }

            if (normalizeAssistantTurnId(S.assistantSpeechActiveTurnId) === pendingTurnId ||
                hasBufferedAssistantAudioForTurn(pendingTurnId)) {
                logAssistantLifecycle('scheduleUserActivityCancel:skip_audio_resumed', {
                    turnId: pendingTurnId
                });
                return;
            }

            applyUserActivityCancel(interruptedSpeechId, 'user_activity_delayed');
        }, USER_ACTIVITY_CANCEL_GRACE_MS);
    }

    function clearAssistantLifecycleOnDisconnect(source) {
        clearPendingUserActivityCancel();
        emitAssistantSpeechCancel(source || 'socket_close');
        S.assistantSpeechActiveTurnId = null;
        S.assistantTurnId = null;
        S.assistantTurnCompletedId = null;
        S.assistantTurnCompletionSource = null;
        clearPendingAssistantTurnStart();
        S.currentPlayingSpeechId = null;
        S.interruptedSpeechId = null;
        S.pendingDecoderReset = false;
        S.skipNextAudioBlob = false;
        S.incomingAudioEpoch += 1;
        S.incomingAudioBlobQueue = [];
        S.pendingAudioChunkMetaQueue = [];
        logAssistantLifecycle('clearAssistantLifecycleOnDisconnect', {
            source: source || 'socket_close'
        });
    }

    window.addEventListener('neko-assistant-turn-start', clearPendingUserActivityCancel);
    window.addEventListener('neko-assistant-speech-start', clearPendingUserActivityCancel);
    window.addEventListener('neko-assistant-speech-cancel', clearPendingUserActivityCancel);

    // ========================  Convenience helpers  ========================

    /** Check whether the WebSocket is open */
    mod.isOpen = function () {
        return S.socket && S.socket.readyState === WebSocket.OPEN;
    };

    // ========================  ensureWebSocketOpen  ========================

    /**
     * Wait for the WebSocket to reach OPEN state.
     *   - Already OPEN  -> resolves immediately
     *   - CONNECTING     -> waits via addEventListener('open')
     *   - CLOSED/CLOSING -> cancels queued auto-reconnect, calls connectWebSocket(), waits
     * @param {number} timeoutMs  timeout in ms (default 5000)
     * @returns {Promise<void>}
     */
    function ensureWebSocketOpen(timeoutMs = 5000) {
        return new Promise(function (resolve, reject) {
            if (S.socket && S.socket.readyState === WebSocket.OPEN) {
                return resolve();
            }

            var settled = false;
            var timer = null;

            var settle = function (fn, arg) {
                if (settled) return;
                settled = true;
                if (timer) { clearTimeout(timer); timer = null; }
                fn(arg);
            };

            // Timeout
            timer = setTimeout(function () {
                settle(reject, new Error(window.t ? window.t('app.websocketNotConnectedError') : 'WebSocket未连接'));
            }, timeoutMs);

            // Attach listener to current or future socket
            var attachOpenListener = function (ws) {
                if (!ws || settled) return;
                if (ws.readyState === WebSocket.OPEN) {
                    settle(resolve); return;
                }
                if (ws.readyState === WebSocket.CONNECTING) {
                    ws.addEventListener('open', function () { settle(resolve); }, { once: true });
                    ws.addEventListener('error', function () { /* wait for new socket */ }, { once: true });
                    return;
                }
                // CLOSING / CLOSED -- fall through to polling
            };

            if (S.socket && S.socket.readyState === WebSocket.CONNECTING) {
                attachOpenListener(S.socket);
            } else if (S.isSwitchingCatgirl) {
                // 切换期间 handleCatgirlSwitch 独家负责新建 socket（close → sleep → connect）。
                // 如果这里也发起 connectWebSocket，会和 handleCatgirlSwitch 的 connect 双重重连：
                // 前一个新 socket 被后一个覆盖变成孤儿，polling 被迫重绑，5s 超时即报
                // "WebSocket not connected"。改为仅靠下面的 polling 等新 socket 就位。
            } else {
                // socket does not exist or CLOSED/CLOSING -> rebuild
                if (S.autoReconnectTimeoutId) {
                    clearTimeout(S.autoReconnectTimeoutId);
                    S.autoReconnectTimeoutId = null;
                }
                connectWebSocket();
            }

            // Polling fallback: track socket reference; re-attach when replaced
            var lastAttachedWs = null;
            var waitForNewSocket = function () {
                if (settled) return;
                if (S.socket) {
                    if (S.socket !== lastAttachedWs) {
                        lastAttachedWs = S.socket;
                        attachOpenListener(S.socket);
                    }
                    if (!settled) {
                        setTimeout(waitForNewSocket, S.socket.readyState === WebSocket.CONNECTING ? 200 : 50);
                    }
                } else {
                    setTimeout(waitForNewSocket, 50);
                }
            };
            setTimeout(waitForNewSocket, 10);
        });
    }
    mod.ensureWebSocketOpen = ensureWebSocketOpen;

    // ========================  connectWebSocket  ========================

    function connectWebSocket() {
        var currentLanlanName = (window.lanlan_config && window.lanlan_config.lanlan_name)
            ? window.lanlan_config.lanlan_name
            : '';
        // 进入 connectWebSocket 即意味着"当前已经在主动重连"，排队中的 auto-reconnect 不再需要。
        // 切换档案时 Chat 窗口曾出现这样的 stale 序列：handleCatgirlSwitch 刚 connect 的新代理被
        // 旧 WS 生命周期的 CLOSED IPC 误触发 close，onclose 排了一个 3s auto-reconnect；紧接着
        // READY IPC 让代理变 OPEN 恢复正常，但 3s 到期后这个 stale 定时器又跑一次 connectWebSocket，
        // 产出一个永远停在 CONNECTING 的僵尸代理，直接复现 "Start failed: WebSocket not connected"。
        if (S.autoReconnectTimeoutId) {
            clearTimeout(S.autoReconnectTimeoutId);
            S.autoReconnectTimeoutId = null;
        }
        if (!currentLanlanName) {
            console.warn('[WebSocket] lanlan_name is empty, wait for page config and retry');
            S.autoReconnectTimeoutId = setTimeout(connectWebSocket, 500);
            return;
        }

        var protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
        // 对 lanlan_name 做 percent-encode：WebSocket.url 会把非 ASCII 字符（中文角色名）
        // 编成 %XX，下面幂等守卫用 S.socket.url === wsUrl 比对，两侧编码口径必须一致，
        // 否则中文名时守卫永远失败、造不出真正的幂等。
        var wsUrl = protocol + '://' + window.location.host + '/ws/' + encodeURIComponent(currentLanlanName);

        // 幂等兜底：如果当前 socket 已经 OPEN 且指向同一个 URL，说明有 stale 路径
        // （比如 Chat 窗口里被误触发 onclose 排队的 auto-reconnect）到了这一步。
        // 此时再 new WebSocket 等同于主动造一个僵尸 socket：旧的 OPEN 失去引用、
        // 新的在 CONNECTING 里干等（Chat 代理不会再收 READY）。直接跳过即可。
        if (S.socket && S.socket.readyState === WebSocket.OPEN && S.socket.url === wsUrl) {
            return;
        }

        // 新连接重置模型就绪标志，等待模型重新加载
        S._modelReady = false;

        console.log(window.t('console.websocketConnecting'), currentLanlanName, window.t('console.websocketUrl'), wsUrl);
        S.socket = new WebSocket(wsUrl);
        var _thisSocket = S.socket; // 闭包捕获，供 onclose 判断是否已被替换

        // ---- onopen ----
        S.socket.onopen = function () {
            console.log(window.t('console.websocketConnected'));

            // Warm up Agent snapshot once websocket is ready.
            Promise.all([
                fetch('/api/agent/health').then(function (r) { return r.ok; }).catch(function () { return false; }),
                fetch('/api/agent/flags').then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; }),
                fetch('/api/agent/state').then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; })
            ]).then(function (results) {
                var healthOk = results[0];
                var flagsResp = results[1];
                var stateResp = results[2];

                if (flagsResp && flagsResp.success) {
                    window._agentStatusSnapshot = {
                        server_online: !!healthOk,
                        analyzer_enabled: !!flagsResp.analyzer_enabled,
                        flags: flagsResp.agent_flags || {},
                        agent_api_gate: flagsResp.agent_api_gate || {},
                        capabilities: (window._agentStatusSnapshot && window._agentStatusSnapshot.capabilities) || {},
                        updated_at: new Date().toISOString()
                    };
                    if (window.agentStateMachine && typeof window.agentStateMachine.updateCache === 'function') {
                        var warmFlags = flagsResp.agent_flags || {};
                        warmFlags.agent_enabled = !!flagsResp.analyzer_enabled;
                        window.agentStateMachine.updateCache(!!healthOk, warmFlags);
                    }
                }
                // Restore active tasks from state snapshot (covers page refresh / reconnect)
                if (stateResp && stateResp.success && stateResp.snapshot) {
                    var curName = (window.lanlan_config && window.lanlan_config.lanlan_name) || '';
                    var activeTasks = stateResp.snapshot.active_tasks || [];
                    var filteredTasks = curName
                        ? activeTasks.filter(function (t) { return !t.lanlan_name || t.lanlan_name === curName; })
                        : activeTasks;
                    window._agentTaskMap = new Map();
                    filteredTasks.forEach(function (t) { if (t && t.id) window._agentTaskMap.set(t.id, t); });
                    var tasks = Array.from(window._agentTaskMap.values());
                    var hasRunning = tasks.some(function (t) { return t.status === 'running' || t.status === 'queued'; });
                    if (tasks.length > 0 && window.AgentHUD && typeof window.AgentHUD.updateAgentTaskHUD === 'function') {
                        window.AgentHUD.showAgentTaskHUD();
                        window.AgentHUD.updateAgentTaskHUD({
                            success: true, tasks: tasks,
                            running_count: tasks.filter(function (t) { return t.status === 'running'; }).length,
                            queued_count: tasks.filter(function (t) { return t.status === 'queued'; }).length,
                        });
                        if (hasRunning && !window._agentTaskTimeUpdateInterval) {
                            window._agentTaskTimeUpdateInterval = setInterval(function () {
                                if (typeof window.updateTaskRunningTimes === 'function') window.updateTaskRunningTimes();
                            }, 1000);
                        }
                    } else if (typeof window.checkAndToggleTaskHUD === 'function') {
                        window.checkAndToggleTaskHUD();
                    } else if (window.AgentHUD && typeof window.AgentHUD.hideAgentTaskHUD === 'function') {
                        window.AgentHUD.hideAgentTaskHUD();
                    }
                }
            }).catch(function () { });

            // Start heartbeat
            if (S.heartbeatInterval) {
                clearInterval(S.heartbeatInterval);
            }
            S.heartbeatInterval = setInterval(function () {
                if (S.socket && S.socket.readyState === WebSocket.OPEN) {
                    S.socket.send(JSON.stringify({ action: 'ping' }));
                }
            }, C.HEARTBEAT_INTERVAL);
            console.log(window.t('console.heartbeatStarted'));

            sendHomeTutorialState('ws-open');

            // ── 首次连接 / 切换角色：标记 greeting 意图，若模型已就绪则立即发送 ──
            _resetGreetingCheckRetry(true);
            S._greetingCheckPending = true;
            S._greetingCheckIsSwitch = !!S._pendingGreetingSwitch;
            S._pendingGreetingSwitch = false;
            _sendGreetingCheckIfReady();

            // ── game-window-state 重连兜底（codex P2）──
            // game_window_state_change 是 edge-triggered WS 事件——只在 activate
            // / finalize 那一瞬推。WS 在 game 期间断开 + 期间 close 事件丢失 →
            // _gameWindowActive 卡在 true，UI 永远停在收缩态。onopen 同时覆盖
            // 首次连接和重连，主动查 /api/game/route/active 拿当前权威状态，
            // dispatch 对应 CustomEvent 让既有 listener 走正常 minimize / restore
            // 路径。idempotent：active=true + 已 minimize → _gameMinimizeForGame
            // 早返回；active=false + 无 snap → _gameRestoreAfterGame 早返回。
            (function syncGameWindowStateOnWsConnect() {
                var lan = '';
                try {
                    if (window.appState && typeof window.appState.lanlan_name === 'string') {
                        lan = window.appState.lanlan_name;
                    }
                    if (!lan && window.lanlan_config && typeof window.lanlan_config.lanlan_name === 'string') {
                        lan = window.lanlan_config.lanlan_name;
                    }
                } catch (_) {}
                if (!lan) return; // greeting 流水线还没解析角色 → 跳过本次，下次 onopen 再来
                fetch('/api/game/route/active?lanlan_name=' + encodeURIComponent(lan))
                    .then(function (resp) { return resp && resp.ok ? resp.json() : null; })
                    .then(function (data) {
                        if (!data) return;
                        var action = data.active ? 'opened' : 'closed';
                        try {
                            window.dispatchEvent(new CustomEvent('neko-game-window-state-change', {
                                detail: {
                                    action: action,
                                    lanlanName: data.lanlan_name || lan,
                                    gameType: data.game_type || '',
                                    sessionId: data.session_id || ''
                                }
                            }));
                        } catch (_) {}
                    })
                    .catch(function () {});
            })();
        };

        // ---- onmessage ----
        S.socket.onmessage = function (event) {
            if (S.socket !== _thisSocket) {
                console.log('[WS] stale onmessage skipped (socket already replaced)');
                return;
            }

            // Binary audio data
            if (event.data instanceof Blob) {
                if (window.DEBUG_AUDIO) {
                    console.log(window.t('console.audioBinaryReceived'), event.data.size, window.t('console.audioBinaryBytes'));
                }
                if (typeof window.enqueueIncomingAudioBlob === 'function') {
                    window.enqueueIncomingAudioBlob(event.data);
                }
                return;
            }

            try {
                var response = JSON.parse(event.data);
                if (response.type === 'catgirl_switched') {
                    console.log(window.t('console.catgirlSwitchedReceived'), response);
                }

                // -------- gemini_response --------
                if (response.type === 'gemini_response') {
                    var isNewMessage = response.isNewMessage || false;
                    if (response.metadata && response.metadata.game_route) {
                        var gameMeta = response.metadata.game_route;
                        var gameEvent = gameMeta.event || {};
                        console.log(`[GameMirror] 主聊天栏收到游戏台词 | game=${gameMeta.game_type || '-'} session=${gameMeta.session_id || '-'} kind=${gameEvent.kind || '-'} round=${gameEvent.round || '-'} source=${response.metadata.source || '-'}`);
                    }
                    if (isNewMessage) {
                        // voice chat 中，AI 新消息到来时若上一条人类消息为纯空白则替换为 ...
                        if (S.lastVoiceUserMessage && S.lastVoiceUserMessage.isConnected &&
                            !S.lastVoiceUserMessage.textContent.trim()) {
                            S.lastVoiceUserMessage.textContent = '...';
                        }
                        S.lastVoiceUserMessage = null;
                        S.lastVoiceUserMessageTime = 0;
                        S.assistantTurnId = null;
                        S.assistantPendingTurnServerId = normalizeAssistantTurnId(response.turn_id);
                        S.assistantTurnAwaitingBubble = true;
                    }
                    if (!S.assistantTurnId
                            && S.assistantTurnAwaitingBubble
                            && getRenderableAssistantChunkText(response.text)) {
                        ensureAssistantTurnStarted('gemini_response_first_chunk', response.turn_id);
                    }
                    var createdVisibleBubble = false;
                    if (typeof window.appendMessage === 'function') {
                        createdVisibleBubble = window.appendMessage(response.text, 'gemini', isNewMessage) === true;
                    }
                    if (createdVisibleBubble && response.request_id) {
                        if (window.reactChatWindowHost && typeof window.reactChatWindowHost.clearPendingRollbackDraft === 'function') {
                            window.reactChatWindowHost.clearPendingRollbackDraft(response.request_id);
                        }
                        if (window._lastSubmittedRequestId === response.request_id) {
                            window._lastSubmittedText = '';
                            window._lastSubmittedRequestId = '';
                        }
                    }
                    if (!S.assistantTurnId && S.assistantTurnAwaitingBubble && createdVisibleBubble) {
                        ensureAssistantTurnStarted('gemini_response_visible_bubble', response.turn_id);
                    }
                    if (response.turn_id) {
                        window.realisticGeminiCurrentTurnId = response.turn_id;
                        // 如果有暂存的主动搭话附件，立即展示
                        if (window.appProactive && typeof window.appProactive._flushProactiveAttachments === 'function') {
                            window.appProactive._flushProactiveAttachments(response.turn_id);
                        }
                    }

                // -------- response_discarded --------
                } else if (response.type === 'response_discarded') {
                    clearPendingUserActivityCancel();
                    window.invalidatePendingMusicSearch();
                    emitAssistantSpeechCancel('response_discarded');
                    S.assistantTurnId = null;
                    clearPendingAssistantTurnStart();
                    var attempt = response.attempt || 0;
                    var maxAttempts = response.max_attempts || 0;
                    console.log('[Discard] AI回复被丢弃 reason=' + response.reason + ' attempt=' + attempt + '/' + maxAttempts + ' retry=' + response.will_retry);

                    window._realisticGeminiQueue = [];
                    window._realisticGeminiBuffer = '';
                    window._pendingMusicCommand = '';
                    window._realisticGeminiVersion = (window._realisticGeminiVersion || 0) + 1;
                    // 重置并发锁，确保正在 sleep 的 processRealisticQueue 循环
                    // 醒来后通过 version 检查退出，且不会阻塞下一轮启动
                    window._isProcessingRealisticQueue = false;
                    window._realisticProcessingOwner = null;

                    // 同时清理 host 未就绪期间缓存的待发消息（防止 discard 的消息在 host ready 后被重放）
                    var hadTrackedBubbles = window.currentTurnGeminiBubbles && window.currentTurnGeminiBubbles.length > 0;
                    if (hadTrackedBubbles) {
                        var _discardIds = [];
                        window.currentTurnGeminiBubbles.forEach(function (bubble) {
                            if (bubble && bubble.dataset && bubble.dataset.reactChatMessageId) {
                                _discardIds.push(bubble.dataset.reactChatMessageId);
                            }
                        });
                        if (_discardIds.length > 0 && typeof window._clearPendingHostMessagesByIds === 'function') {
                            window._clearPendingHostMessagesByIds(_discardIds);
                        }
                        var _discardHost = window.reactChatWindowHost;
                        window.currentTurnGeminiBubbles.forEach(function (bubble) {
                            // Remove paired React mirror message
                            if (_discardHost && typeof _discardHost.removeMessage === 'function' &&
                                bubble && bubble.dataset && bubble.dataset.reactChatMessageId) {
                                _discardHost.removeMessage(bubble.dataset.reactChatMessageId);
                            }
                            if (bubble && bubble.parentNode) {
                                bubble.parentNode.removeChild(bubble);
                            }
                        });
                        window.currentTurnGeminiBubbles = [];
                    }
                    window.currentGeminiMessage = null;

                    if (window.currentTurnGeminiAttachments && window.currentTurnGeminiAttachments.length > 0) {
                        window.currentTurnGeminiAttachments.forEach(function (attachment) {
                            if (attachment && attachment.parentNode) {
                                attachment.parentNode.removeChild(attachment);
                            }
                        });
                        window.currentTurnGeminiAttachments = [];
                    }
                    window.realisticGeminiCurrentTurnId = null;

                    // Fallback: clear trailing gemini bubbles not tracked
                    var cc = chatContainer();
                    if (!hadTrackedBubbles &&
                        cc && cc.children && cc.children.length > 0) {
                        var _fallbackHost = window.reactChatWindowHost;
                        var toRemove = [];
                        for (var i = cc.children.length - 1; i >= 0; i--) {
                            var el = cc.children[i];
                            if (el.classList && el.classList.contains('message') && el.classList.contains('gemini')) {
                                toRemove.push(el);
                            } else {
                                break;
                            }
                        }
                        toRemove.forEach(function (el) {
                            if (_fallbackHost && typeof _fallbackHost.removeMessage === 'function' &&
                                el && el.dataset && el.dataset.reactChatMessageId) {
                                _fallbackHost.removeMessage(el.dataset.reactChatMessageId);
                            }
                            if (el && el.parentNode) el.parentNode.removeChild(el);
                        });
                    }

                    window._geminiTurnFullText = '';
                    window._pendingMusicCommand = '';

                    // 推进 epoch 并清空入站音频队列，防止在途 TTS blob 被消费播放
                    S.incomingAudioEpoch += 1;
                    S.incomingAudioBlobQueue = [];
                    S.pendingAudioChunkMetaQueue = [];

                    (async function () {
                        if (typeof window.clearAudioQueue === 'function') await window.clearAudioQueue();
                    })();

                    // Check the discard code:
                    //   RESPONSE_TOO_LONG          — reroll exhausted with no recoverable
                    //                                sentence-end. UI rolls back the user's
                    //                                input so they can retry.
                    //   RESPONSE_LENGTH_TRUNCATED  — reroll exhausted but text was salvaged
                    //                                by truncating to the last sentence-end;
                    //                                the truncated text arrives via the
                    //                                normal gemini_response stream, so we
                    //                                must NOT rollback the input here.
                    var _isResponseTooLong = false;
                    var _isLengthTruncated = false;
                    if (!response.will_retry && response.message) {
                        try {
                            var _pdm = typeof response.message === 'string' ? JSON.parse(response.message) : response.message;
                            if (_pdm && _pdm.code === 'RESPONSE_TOO_LONG') _isResponseTooLong = true;
                            else if (_pdm && _pdm.code === 'RESPONSE_LENGTH_TRUNCATED') _isLengthTruncated = true;
                        } catch (_) { /* ignore */ }
                    }

                    if (_isResponseTooLong) {
                        // Suppress toast — backend sends cute text via gemini_response
                        // Only rollback user input here
                        if (window.reactChatWindowHost && typeof window.reactChatWindowHost.rollbackLastDraft === 'function') {
                            window.reactChatWindowHost.rollbackLastDraft(response.request_id);
                        }
                        var legacyInput = document.getElementById('textInputBox');
                        if (legacyInput && !legacyInput.value &&
                            response.request_id && window._lastSubmittedRequestId === response.request_id &&
                            window._lastSubmittedText) {
                            legacyInput.value = window._lastSubmittedText;
                            window._lastSubmittedText = '';
                            window._lastSubmittedRequestId = '';
                        }
                    } else if (_isLengthTruncated) {
                        // Suppress toast / error bubble. Keep the user's input cleared
                        // (truncated answer is a valid completion, no retry needed).
                        if (window.reactChatWindowHost && typeof window.reactChatWindowHost.clearPendingRollbackDraft === 'function') {
                            window.reactChatWindowHost.clearPendingRollbackDraft(response.request_id);
                        }
                        if (response.request_id && window._lastSubmittedRequestId === response.request_id) {
                            window._lastSubmittedText = '';
                            window._lastSubmittedRequestId = '';
                        }
                    } else {
                        if (!response.will_retry) {
                            if (window.reactChatWindowHost && typeof window.reactChatWindowHost.clearPendingRollbackDraft === 'function') {
                                window.reactChatWindowHost.clearPendingRollbackDraft(response.request_id);
                            }
                            if (response.request_id && window._lastSubmittedRequestId === response.request_id) {
                                window._lastSubmittedText = '';
                                window._lastSubmittedRequestId = '';
                            }
                        }
                        var retryMsg = window.t ? window.t('console.aiRetrying') : '猫娘链接出现异常，校准中…';
                        var failMsg = window.t ? window.t('console.aiFailed') : '猫娘链接出现异常';
                        if (typeof window.showStatusToast === 'function') {
                            window.showStatusToast(response.will_retry ? retryMsg : failMsg, 2500);
                        }

                        if (!response.will_retry && response.message) {
                            var translatedDiscardMsg = window.translateStatusMessage ? window.translateStatusMessage(response.message) : response.message;
                            appendAssistantStatusMessage(translatedDiscardMsg);
                        } else {
                            var cc3 = chatContainer();
                            if (cc3) cc3.scrollTop = cc3.scrollHeight;
                        }
                    }

                // -------- user_transcript --------
                } else if (response.type === 'user_transcript') {
                    // 语音转写也属于用户首次输入；这里只标记，成就仍等 AI 首次可见回复时触发
                    if (window.appChat && typeof window.appChat.isFirstUserInput === 'function' && window.appChat.isFirstUserInput()) {
                        window.appChat.markFirstUserInput();
                        console.log(window.t('console.userFirstInputDetected'));
                    }

                    // 收到 transcription，清除 session 初始 5 秒计时器
                    if (S._voiceSessionInitialTimer) {
                        clearTimeout(S._voiceSessionInitialTimer);
                        S._voiceSessionInitialTimer = null;
                    }
                    // 真用户语音到达 → 等同于一次"用户输入"：清退避级别 +
                    // 复位语音模式无回复计数。否则连续被 preempt / 长时间没
                    // 回应都不会复位 _voiceProactiveNoResponseCount，10 轮后
                    // 主动搭话会被永久关闭，即使用户其实一直在讲话。
                    // 跨窗口通过 BroadcastChannel 广播，让 leader 同步。
                    if (typeof window.resetProactiveChatBackoff === 'function') {
                        window.resetProactiveChatBackoff();
                    }
                    var now = Date.now();
                    var shouldMerge = S.isRecording &&
                        S.lastVoiceUserMessage &&
                        S.lastVoiceUserMessage.isConnected &&
                        (now - S.lastVoiceUserMessageTime) < C.VOICE_TRANSCRIPT_MERGE_WINDOW;

                    if (shouldMerge) {
                        S.lastVoiceUserMessage.textContent += response.text;
                        S.lastVoiceUserMessageTime = now;
                    } else {
                        if (typeof window.appendMessage === 'function') {
                            window.appendMessage(response.text, 'user', true);
                        }
                        if (S.isRecording) {
                            var cc4 = chatContainer();
                            if (cc4) {
                                var userMessages = cc4.querySelectorAll('.message.user');
                                if (userMessages.length > 0) {
                                    S.lastVoiceUserMessage = userMessages[userMessages.length - 1];
                                    S.lastVoiceUserMessageTime = now;
                                }
                            }
                        }
                    }

                // -------- user_activity --------
                } else if (response.type === 'user_activity') {
                    var userActivityTurnId = resolveAssistantLifecycleTurnId();
                    if (shouldDelayUserActivityCancel(userActivityTurnId)) {
                        logAssistantLifecycle('user_activity:delay_cancel', {
                            turnId: userActivityTurnId,
                            interruptedSpeechId: response.interrupted_speech_id || null
                        });
                        scheduleUserActivityCancel(userActivityTurnId, response.interrupted_speech_id || null);
                    } else {
                        logAssistantLifecycle('user_activity:immediate_cancel', {
                            turnId: userActivityTurnId,
                            interruptedSpeechId: response.interrupted_speech_id || null
                        });
                        applyUserActivityCancel(response.interrupted_speech_id || null, 'user_activity');
                    }

                // -------- audio_chunk --------
                } else if (response.type === 'audio_chunk') {
                    if (window.DEBUG_AUDIO) {
                        console.log(window.t('console.audioChunkHeaderReceived'), response);
                    }
                    if (!S.assistantTurnId && S.assistantTurnAwaitingBubble) {
                        ensureAssistantTurnStarted('audio_chunk_header_fallback');
                    }
                    var speechId = response.speech_id;
                    var shouldSkip = false;

                    if (speechId && S.interruptedSpeechId && speechId === S.interruptedSpeechId) {
                        if (window.DEBUG_AUDIO) {
                            console.log(window.t('console.discardInterruptedAudio'), speechId);
                        }
                        shouldSkip = true;
                    } else if (speechId && speechId !== S.currentPlayingSpeechId) {
                        if (S.pendingDecoderReset) {
                            console.log(window.t('console.newConversationResetDecoder'), speechId);
                            S.decoderResetPromise = (async function () {
                                if (typeof window.resetOggOpusDecoder === 'function') {
                                    await window.resetOggOpusDecoder();
                                }
                                S.pendingDecoderReset = false;
                            })();
                        } else {
                            S.pendingDecoderReset = false;
                        }
                        S.currentPlayingSpeechId = speechId;
                        S.interruptedSpeechId = null;
                    }

                    S.pendingAudioChunkMetaQueue.push({
                        speechId: speechId || S.currentPlayingSpeechId || null,
                        turnId: resolveAssistantLifecycleTurnId(),
                        shouldSkip: shouldSkip,
                        epoch: S.incomingAudioEpoch,
                        receivedAt: Date.now()
                    });
                    logAssistantLifecycle('ws:audio_chunk_header', {
                        speechId: speechId || S.currentPlayingSpeechId || null,
                        turnId: resolveAssistantLifecycleTurnId(),
                        shouldSkip: shouldSkip,
                        epoch: S.incomingAudioEpoch
                    });
                    if (window.appAudioPlayback &&
                        typeof window.appAudioPlayback.schedulePendingAudioMetaStallCheck === 'function') {
                        window.appAudioPlayback.schedulePendingAudioMetaStallCheck();
                    }
                    S.skipNextAudioBlob = false;

                // -------- cozy_audio --------
                } else if (response.type === 'cozy_audio') {
                    console.log(window.t('console.newAudioHeaderReceived'));
                    var isNewMsg = response.isNewMessage || false;
                    if (isNewMsg) {
                        (async function () {
                            if (typeof window.clearAudioQueue === 'function') await window.clearAudioQueue();
                        })();
                    }
                    if (response.format === 'base64') {
                        if (typeof window.handleBase64Audio === 'function') {
                            window.handleBase64Audio(response.audioData, isNewMsg);
                        }
                    }

                // -------- screen_share_error --------
                } else if (response.type === 'screen_share_error') {
                    var translatedMsg = window.translateStatusMessage ? window.translateStatusMessage(response.message) : response.message;
                    if (typeof window.showStatusToast === 'function') window.showStatusToast(translatedMsg, 4000);

                    if (typeof window.stopScreening === 'function') window.stopScreening();

                    if (S.screenCaptureStream) {
                        S.screenCaptureStream.getTracks().forEach(function (track) { track.stop(); });
                        S.screenCaptureStream = null;
                    }

                    if (S.isRecording) {
                        var mb = micButton(); if (mb) mb.disabled = true;
                        var mu = muteButton(); if (mu) mu.disabled = false;
                        var sb = screenButton(); if (sb) sb.disabled = false;
                        var st = stopButton(); if (st) st.disabled = true;
                        var rs = resetSessionButton(); if (rs) rs.disabled = false;
                    } else if (S.isTextSessionActive) {
                        var ss = screenshotButton(); if (ss) ss.disabled = false;
                    }

                // -------- catgirl_switched --------
                } else if (response.type === 'catgirl_switched') {
                    var newCatgirl = response.new_catgirl;
                    var oldCatgirl = response.old_catgirl;
                    console.log(window.t('console.catgirlSwitchNotification'), oldCatgirl, window.t('console.catgirlSwitchTo'), newCatgirl);
                    console.log(window.t('console.currentFrontendCatgirl'), window.lanlan_config.lanlan_name);
                    if (typeof window.handleCatgirlSwitch === 'function') {
                        window.handleCatgirlSwitch(newCatgirl, oldCatgirl);
                    }

                // -------- status --------
                } else if (response.type === 'status') {
                    var statusCode = null;
                    var statusPayload = null;
                    var statusDetails = null;
                    try {
                        statusPayload = JSON.parse(response.message);
                        if (statusPayload && statusPayload.code) statusCode = statusPayload.code;
                        if (statusPayload && statusPayload.details && typeof statusPayload.details === 'object') {
                            statusDetails = statusPayload.details;
                        }
                    } catch (_) { }

                    if (statusCode === 'GAME_ROUTE_ENDED') {
                        var shouldResumeAudio = !!(statusDetails && statusDetails.should_resume_external_on_exit);
                        var realtimeRestore = statusDetails && statusDetails.realtime_restore;
                        var wasRecording = !!S.isRecording;
                        // Stale-event guard: a delayed GAME_ROUTE_ENDED for a previous
                        // session can arrive AFTER /route/start has finalized that one
                        // and activated a new session_id. Without this check the handler
                        // would unconditionally clear S.gameRoute* state and tear down
                        // the freshly-activated STT gate. We keep an empty current
                        // session_id permissive (legacy fallback) so events that
                        // genuinely lack a session_id still process.
                        var endedSessionId = (statusDetails && statusDetails.session_id) || '';
                        var currentSessionId = S.gameRouteSessionId || '';
                        if (endedSessionId && currentSessionId && endedSessionId !== currentSessionId) {
                            console.log(`[GameVoiceSTT] 忽略过期的 GAME_ROUTE_ENDED | ended_session=${endedSessionId} current_session=${currentSessionId}`);
                            return;
                        }
                        S.gameRouteActive = false;
                        S.gameRouteGameType = '';
                        S.gameRouteLanlanName = '';
                        S.gameRouteSessionId = '';
                        console.log(`[GameVoiceSTT] 游戏语音路由已结束 | resume=${shouldResumeAudio} recording=${wasRecording} realtime_restore=${realtimeRestore && realtimeRestore.ok === false ? realtimeRestore.reason : 'ok'}`);
                        if (realtimeRestore && realtimeRestore.attempted && realtimeRestore.ok === false) {
                            console.warn('[GameVoiceSTT] 游戏退出后 Realtime 恢复未确认:', realtimeRestore.reason || 'unknown');
                        }
                        if (typeof window.stopGameVoiceSttGate === 'function') {
                            window.stopGameVoiceSttGate({ restoreOrdinaryMic: false });
                        } else {
                            S.gameVoiceSttGateActive = false;
                            S.gameVoiceSttGameType = '';
                            S.gameVoiceSttSessionId = '';
                        }
                        if (shouldResumeAudio && wasRecording && !S.isMicMuted) {
                            var micPipelineAlive = !!(S.stream && S.audioContext && S.workletNode);
                            if (!micPipelineAlive && typeof window.startMicCapture === 'function') {
                                Promise.resolve(window.startMicCapture()).catch(function (error) {
                                    console.warn('[GameVoiceSTT] 游戏退出后恢复普通语音采集失败:', error);
                                });
                            }
                        }
                        if (S.proactiveChatWasStoppedByGameRoute && S.proactiveChatEnabled && typeof window.scheduleProactiveChat === 'function') {
                            window.scheduleProactiveChat();
                        }
                        S.proactiveChatWasStoppedByGameRoute = false;
                        return;
                    }

                    if (statusCode === 'GAME_VOICE_STT_GATE_ACTIVE') {
                        var sttProvider = (statusDetails && statusDetails.stt_provider) || 'browser';
                        S.gameRouteActive = true;
                        S.gameRouteGameType = (statusDetails && statusDetails.game_type) || 'soccer';
                        S.gameRouteLanlanName = (statusDetails && statusDetails.lanlan_name) || '';
                        S.gameRouteSessionId = (statusDetails && statusDetails.session_id) || '';
                        S.gameVoiceSttGameType = (statusDetails && statusDetails.game_type) || 'soccer';
                        S.gameVoiceSttSessionId = (statusDetails && statusDetails.session_id) || '';
                        console.log(`[GameVoiceSTT] 游戏语音接管已激活 | game=${S.gameVoiceSttGameType} provider=${sttProvider} recording=${!!S.isRecording} muted=${!!S.isMicMuted}`);
                        if (S._voiceSessionInitialTimer) {
                            clearTimeout(S._voiceSessionInitialTimer);
                            S._voiceSessionInitialTimer = null;
                        }
                        if (typeof window.stopProactiveChatSchedule === 'function') {
                            S.proactiveChatWasStoppedByGameRoute = !!S.proactiveChatEnabled;
                            window.stopProactiveChatSchedule();
                        }
                        if (sttProvider === 'realtime') {
                            if (typeof window.stopGameVoiceSttGate === 'function') {
                                window.stopGameVoiceSttGate();
                            } else {
                                S.gameVoiceSttGateActive = false;
                            }
                            console.log('[GameVoiceSTT] 复用原 Realtime STT，继续发送普通麦克风音频，普通回复由后端丢弃');
                            return;
                        }
                        S.gameVoiceSttGateActive = true;
                        if (typeof window.startGameVoiceSttGate === 'function') {
                            window.startGameVoiceSttGate();
                        } else {
                            console.warn('[GameVoiceSTT] startGameVoiceSttGate unavailable');
                        }
                        return;
                    }

                    if (statusCode === 'GAME_ROUTE_MEDIA_SKIPPED') {
                        return;
                    }

                    var isGoodbyeActive = (window.live2dManager && window.live2dManager._goodbyeClicked) || (window.vrmManager && window.vrmManager._goodbyeClicked) || (window.mmdManager && window.mmdManager._goodbyeClicked);
                    if ((S.isSwitchingMode || isGoodbyeActive || S._suppressCharacterLeft) && (statusCode === 'CHARACTER_LEFT' || response.message.includes('已离开'))) {
                        S._suppressCharacterLeft = false;
                        console.log(window.t('console.modeSwitchingIgnoreLeft'));
                        return;
                    }

                    var criticalErrorCodes = ['SESSION_START_CRITICAL', 'MEMORY_SERVER_CRASHED', 'API_KEY_REJECTED', 'API_RATE_LIMIT_SESSION', 'ERROR_1007_ARREARS', 'AGENT_QUOTA_EXCEEDED', 'RESPONSE_TIMEOUT', 'CONNECTION_TIMEOUT'];
                    var isCriticalError = statusCode && criticalErrorCodes.indexOf(statusCode) !== -1;
                    if (isCriticalError) {
                        console.log(window.t('console.seriousErrorHidePreparing'));
                        if (typeof window.hideVoicePreparingToast === 'function') window.hideVoicePreparingToast();
                    }

                    var translatedMessage = window.translateStatusMessage ? window.translateStatusMessage(response.message) : response.message;

                    // TTS 水印提示需要更长显示时间和更高优先级，避免被后续消息覆盖
                    var stickyInfoCodes = ['TTS_WATERMARK_DETECTED'];
                    var isStickyInfo = statusCode && stickyInfoCodes.indexOf(statusCode) !== -1;

                    if (typeof window.showStatusToast === 'function') window.showStatusToast(translatedMessage, isStickyInfo ? 8000 : 4000, { important: isCriticalError, priority: isStickyInfo ? 50 : undefined });

                    if (statusCode === 'CHARACTER_DISCONNECTED') {
                        if (S.isRecording === false && !S.isTextSessionActive) {
                            if (typeof window.showStatusToast === 'function') {
                                window.showStatusToast(window.t ? window.t('app.catgirlResting', { name: window.lanlan_config.lanlan_name }) : (window.lanlan_config.lanlan_name + '正在打盹...'), 5000);
                            }
                        } else if (S.isTextSessionActive) {
                            if (typeof window.showStatusToast === 'function') {
                                window.showStatusToast(window.t ? window.t('app.textChatting') : '正在文本聊天中...', 5000);
                            }
                        } else {
                            // Recording mode: stop and auto-restart
                            if (typeof window.stopRecording === 'function') window.stopRecording();
                            if (typeof window.syncFloatingMicButtonState === 'function') window.syncFloatingMicButtonState(false);
                            if (typeof window.syncFloatingScreenButtonState === 'function') window.syncFloatingScreenButtonState(false);

                            if (S.socket && S.socket.readyState === WebSocket.OPEN) {
                                S.socket.send(JSON.stringify({ action: 'end_session' }));
                            }
                            if (typeof window.hideLive2d === 'function') window.hideLive2d();

                            var _mb = micButton(); if (_mb) _mb.disabled = true;
                            var _mu = muteButton(); if (_mu) _mu.disabled = true;
                            var _sb = screenButton(); if (_sb) _sb.disabled = true;
                            var _st = stopButton(); if (_st) _st.disabled = true;
                            var _rs = resetSessionButton(); if (_rs) _rs.disabled = true;
                            var _rt = returnSessionButton(); if (_rt) _rt.disabled = true;

                            setTimeout(async function () {
                                try {
                                    var sessionStartPromise = new Promise(function (resolve, reject) {
                                        S.sessionStartedResolver = resolve;
                                        S.sessionStartedRejecter = reject;
                                        if (window.sessionTimeoutId) {
                                            clearTimeout(window.sessionTimeoutId);
                                            window.sessionTimeoutId = null;
                                        }
                                    });

                                    await ensureWebSocketOpen();
                                    S.socket.send(JSON.stringify({ action: 'start_session', input_type: 'audio' }));

                                    window.sessionTimeoutId = setTimeout(function () {
                                        if (S.sessionStartedRejecter) {
                                            var rejecter = S.sessionStartedRejecter;
                                            S.sessionStartedResolver = null;
                                            S.sessionStartedRejecter = null;
                                            window.sessionTimeoutId = null;

                                            if (S.socket && S.socket.readyState === WebSocket.OPEN) {
                                                S.socket.send(JSON.stringify({ action: 'end_session' }));
                                                console.log(window.t('console.autoRestartTimeoutEndSession'));
                                            }
                                            var timeoutMsg = (window.t && window.t('app.sessionTimeout')) || '\u542F\u52A8\u8D85\u65F6\uFF0C\u670D\u52A1\u5668\u53EF\u80FD\u7E41\u5FD9\uFF0C\u8BF7\u7A0D\u540E\u624B\u52A8\u91CD\u8BD5';
                                            rejecter(new Error(timeoutMsg));
                                        }
                                    }, 15000);

                                    await sessionStartPromise;

                                    if (typeof window.showCurrentModel === 'function') await window.showCurrentModel();
                                    if (typeof window.startMicCapture === 'function') await window.startMicCapture();
                                    if (S.screenCaptureStream != null) {
                                        if (typeof window.startScreenSharing === 'function') await window.startScreenSharing();
                                    }

                                    if (window.live2dManager && window.live2dManager._floatingButtons) {
                                        if (typeof window.syncFloatingMicButtonState === 'function') window.syncFloatingMicButtonState(true);
                                        if (S.screenCaptureStream != null) {
                                            if (typeof window.syncFloatingScreenButtonState === 'function') window.syncFloatingScreenButtonState(true);
                                        }
                                    }

                                    if (typeof window.showStatusToast === 'function') {
                                        window.showStatusToast(window.t ? window.t('app.restartComplete', { name: window.lanlan_config.lanlan_name }) : ('重启完成，' + window.lanlan_config.lanlan_name + '回来了！'), 4000);
                                    }
                                } catch (error) {
                                    console.error(window.t('console.restartError'), error);

                                    if (window.sessionTimeoutId) {
                                        clearTimeout(window.sessionTimeoutId);
                                        window.sessionTimeoutId = null;
                                    }
                                    S.sessionStartedResolver = null;
                                    S.sessionStartedRejecter = null;

                                    if (S.socket && S.socket.readyState === WebSocket.OPEN) {
                                        S.socket.send(JSON.stringify({ action: 'end_session' }));
                                        console.log(window.t('console.autoRestartFailedEndSession'));
                                    }

                                    if (typeof window.hideVoicePreparingToast === 'function') window.hideVoicePreparingToast();
                                    if (typeof window.showStatusToast === 'function') {
                                        window.showStatusToast(window.t ? window.t('app.restartFailed', { error: error.message }) : ('重启失败: ' + error.message), 5000);
                                    }

                                    var mb2 = micButton();
                                    if (mb2) { mb2.classList.remove('recording'); mb2.classList.remove('active'); }
                                    var sb2 = screenButton();
                                    if (sb2) sb2.classList.remove('active');

                                    S.isRecording = false;
                                    window.isRecording = false;

                                    if (typeof window.syncFloatingMicButtonState === 'function') window.syncFloatingMicButtonState(false);
                                    if (typeof window.syncFloatingScreenButtonState === 'function') window.syncFloatingScreenButtonState(false);

                                    var mb3 = micButton(); if (mb3) mb3.disabled = false;
                                    var ts2 = textSendButton(); if (ts2) ts2.disabled = false;
                                    var ti2 = textInputBox(); if (ti2) ti2.disabled = false;
                                    var ss2 = screenshotButton(); if (ss2) ss2.disabled = false;
                                    var rs2 = resetSessionButton(); if (rs2) rs2.disabled = false;

                                    var mu2 = muteButton(); if (mu2) mu2.disabled = true;
                                    var sb3 = screenButton(); if (sb3) sb3.disabled = true;
                                    var st2 = stopButton(); if (st2) st2.disabled = true;

                                    var tia = document.getElementById('text-input-area');
                                    if (tia) tia.classList.remove('hidden');
                                    if (typeof window.syncVoiceChatComposerHidden === 'function') window.syncVoiceChatComposerHidden(false);
                                }
                            }, 7500);
                        }
                    }

                // -------- expression --------
                } else if (response.type === 'expression') {
                    var lanlan = window.LanLan1;
                    var registry = lanlan && lanlan.registered_expressions;
                    var fn = registry && registry[response.message];
                    if (typeof fn === 'function') {
                        fn();
                    } else {
                        console.warn(window.t('console.unknownExpressionCommand'), response.message);
                    }

                // -------- agent_status_update --------
                } else if (response.type === 'agent_status_update') {
                    var snapshot = response.snapshot || {};
                    window._agentStatusSnapshot = snapshot;
                    var serverOnline = snapshot.server_online !== false;
                    var flags = snapshot.flags || {};
                    if (!('agent_enabled' in flags) && snapshot.analyzer_enabled !== undefined) {
                        flags.agent_enabled = !!snapshot.analyzer_enabled;
                    }
                    if (window.agentStateMachine && typeof window.agentStateMachine.updateCache === 'function') {
                        window.agentStateMachine.updateCache(serverOnline, flags);
                    }
                    if (typeof window.applyAgentStatusSnapshotToUI === 'function') {
                        window.applyAgentStatusSnapshotToUI(snapshot);
                    }
                    try {
                        var masterOn = !!flags.agent_enabled;
                        var anyChildOn = !!(flags.computer_use_enabled || flags.browser_use_enabled || flags.user_plugin_enabled || flags.openclaw_enabled || flags.openfang_enabled);
                        if (masterOn && anyChildOn && typeof window.startAgentTaskPolling === 'function') {
                            window.startAgentTaskPolling();
                        }
                        var curName2 = (window.lanlan_config && window.lanlan_config.lanlan_name) || '';
                        var snapshotTasks = snapshot.active_tasks || [];
                        var filteredSnapshotTasks = curName2
                            ? snapshotTasks.filter(function (t) { return !t.lanlan_name || t.lanlan_name === curName2; })
                            : snapshotTasks;
                        if (!window._agentTaskMap) window._agentTaskMap = new Map();
                        var now2 = Date.now();
                        var LINGER_MS = 10000;
                        var newMap = new Map();
                        filteredSnapshotTasks.forEach(function (t) {
                            if (t && t.id) newMap.set(t.id, t);
                        });
                        window._agentTaskMap.forEach(function (t, id) {
                            if (!newMap.has(id)) {
                                if (curName2 && t.lanlan_name && t.lanlan_name !== curName2) return;
                                var isTerminal = t.status === 'completed' || t.status === 'failed' || t.status === 'cancelled';
                                if (isTerminal && t.terminal_at && (now2 - t.terminal_at < LINGER_MS)) {
                                    newMap.set(id, t);
                                }
                            }
                        });
                        window._agentTaskMap = newMap;
                        var tasks2 = Array.from(window._agentTaskMap.values());
                        if (tasks2.length > 0) {
                            if (window.AgentHUD && typeof window.AgentHUD.updateAgentTaskHUD === 'function') {
                                window.AgentHUD.updateAgentTaskHUD({
                                    success: true,
                                    tasks: tasks2,
                                    total_count: tasks2.length,
                                    running_count: tasks2.filter(function (t) { return t.status === 'running'; }).length,
                                    queued_count: tasks2.filter(function (t) { return t.status === 'queued'; }).length,
                                    completed_count: tasks2.filter(function (t) { return t.status === 'completed'; }).length,
                                    failed_count: tasks2.filter(function (t) { return t.status === 'failed'; }).length,
                                    timestamp: new Date().toISOString()
                                });
                            }
                        } else if (typeof window.checkAndToggleTaskHUD === 'function') {
                            window.checkAndToggleTaskHUD();
                        } else if (window.AgentHUD && typeof window.AgentHUD.hideAgentTaskHUD === 'function') {
                            window.AgentHUD.hideAgentTaskHUD();
                        }
                    } catch (_e) { /* ignore */ }

                // -------- avatar_interaction_ack --------
                } else if (response.type === 'avatar_interaction_ack') {
                    emitAssistantLifecycleEvent('neko-avatar-interaction-ack', {
                        interactionId: response.interaction_id || '',
                        accepted: response.accepted === true,
                        reason: response.reason || '',
                        turnId: response.turn_id || ''
                    });

                // -------- agent_notification --------
                } else if (response.type === 'agent_notification') {
                    var notifMsg = typeof response.text === 'string' ? response.text : '';
                    if (notifMsg) {
                        if (typeof window.setFloatingAgentStatus === 'function') window.setFloatingAgentStatus(notifMsg, response.status || 'completed');
                        if (typeof window.maybeShowAgentQuotaExceededModal === 'function') window.maybeShowAgentQuotaExceededModal(notifMsg);
                        if (typeof window.maybeShowContentFilterModal === 'function') window.maybeShowContentFilterModal(notifMsg);
                        if (response.error_message && typeof window.maybeShowContentFilterModal === 'function') {
                            window.maybeShowContentFilterModal(response.error_message);
                        }
                    }

                // -------- agent_task_update --------
                } else if (response.type === 'agent_task_update') {
                    try {
                        if (!window._agentTaskMap) window._agentTaskMap = new Map();
                        if (!window._agentTaskRemoveTimers) window._agentTaskRemoveTimers = new Map();
                        var task = response.task || {};
                        if (task.id) {
                            var existing = window._agentTaskMap.get(task.id);
                            var merged = existing ? Object.assign({}, existing, task) : task;
                            if (existing && existing.params && typeof task.params === 'undefined') {
                                merged.params = existing.params;
                            }
                            if (['completed', 'failed', 'cancelled'].indexOf(task.status) !== -1) {
                                if (!existing || ['completed', 'failed', 'cancelled'].indexOf(existing.status) === -1) {
                                    merged.terminal_at = Date.now();
                                }
                            }
                            window._agentTaskMap.set(task.id, merged);
                            if (['completed', 'failed', 'cancelled'].indexOf(task.status) !== -1) {
                                if (window._agentTaskRemoveTimers.has(task.id)) clearTimeout(window._agentTaskRemoveTimers.get(task.id));
                                window._agentTaskRemoveTimers.set(task.id, setTimeout(function () {
                                    var current = window._agentTaskMap.get(task.id);
                                    if (current && ['completed', 'failed', 'cancelled'].indexOf(current.status) !== -1) {
                                        window._agentTaskMap.delete(task.id);
                                    }
                                    window._agentTaskRemoveTimers.delete(task.id);
                                    var remaining = Array.from(window._agentTaskMap.values());
                                    if (window.AgentHUD && typeof window.AgentHUD.updateAgentTaskHUD === 'function') {
                                        window.AgentHUD.updateAgentTaskHUD({
                                            success: true, tasks: remaining,
                                            total_count: remaining.length,
                                            running_count: remaining.filter(function (t) { return t.status === 'running'; }).length,
                                            queued_count: remaining.filter(function (t) { return t.status === 'queued'; }).length,
                                            completed_count: remaining.filter(function (t) { return t.status === 'completed'; }).length,
                                            failed_count: remaining.filter(function (t) { return t.status === 'failed'; }).length,
                                            timestamp: new Date().toISOString()
                                        });
                                    }
                                }, 10000));
                            } else if (window._agentTaskRemoveTimers.has(task.id)) {
                                clearTimeout(window._agentTaskRemoveTimers.get(task.id));
                                window._agentTaskRemoveTimers.delete(task.id);
                            }
                        }
                        var tasks3 = Array.from(window._agentTaskMap.values());
                        var hasRunning2 = tasks3.some(function (t) { return t.status === 'running' || t.status === 'queued'; });
                        if (tasks3.length > 0 && window.AgentHUD) {
                            if (typeof window.AgentHUD.showAgentTaskHUD === 'function') {
                                window.AgentHUD.showAgentTaskHUD();
                            }
                            if (hasRunning2 && !window._agentTaskTimeUpdateInterval) {
                                window._agentTaskTimeUpdateInterval = setInterval(function () {
                                    if (typeof window.updateTaskRunningTimes === 'function') window.updateTaskRunningTimes();
                                }, 1000);
                            }
                        }
                        if (window.AgentHUD && typeof window.AgentHUD.updateAgentTaskHUD === 'function') {
                            window.AgentHUD.updateAgentTaskHUD({
                                success: true,
                                tasks: tasks3,
                                total_count: tasks3.length,
                                running_count: tasks3.filter(function (t) { return t.status === 'running'; }).length,
                                queued_count: tasks3.filter(function (t) { return t.status === 'queued'; }).length,
                                completed_count: tasks3.filter(function (t) { return t.status === 'completed'; }).length,
                                failed_count: tasks3.filter(function (t) { return t.status === 'failed'; }).length,
                                timestamp: new Date().toISOString()
                            });
                        }
                        if (task && task.status === 'failed') {
                            var errMsg = task.error || task.reason || '';
                            if (errMsg) {
                                if (typeof window.maybeShowAgentQuotaExceededModal === 'function') window.maybeShowAgentQuotaExceededModal(errMsg);
                                if (typeof window.maybeShowContentFilterModal === 'function') window.maybeShowContentFilterModal(errMsg);
                            }
                        }
                    } catch (e) {
                        console.warn('[App] 处理 agent_task_update 失败:', e);
                    }

                // -------- request_screenshot --------
                } else if (response.type === 'request_screenshot') {
                    (async function () {
                        try {
                            var dataUrl = null;
                            if (typeof window.captureProactiveChatScreenshot === 'function') {
                                dataUrl = await window.captureProactiveChatScreenshot();
                            }
                            if (dataUrl && S.socket && S.socket.readyState === WebSocket.OPEN) {
                                var respMsg = { action: 'screenshot_response', data: dataUrl };
                                // Determine capture type for correct coordinate mapping
                                // null = 窗口截图或无法确定 → 不叠加；仅无流无源时默认 'screen'
                                var captureType = null;
                                if (typeof window.detectScreenshotCaptureType === 'function') {
                                    captureType = window.detectScreenshotCaptureType(
                                        S.screenCaptureStream, S.selectedScreenSourceId
                                    );
                                }
                                if (captureType === null && !S.screenCaptureStream && !S.selectedScreenSourceId) {
                                    captureType = 'screen';
                                }
                                var avatarPos = typeof window.getAvatarScreenPosition === 'function'
                                    ? window.getAvatarScreenPosition(captureType) : null;
                                if (avatarPos) {
                                    respMsg.avatar_position = avatarPos;
                                }
                                S.socket.send(JSON.stringify(respMsg));
                            }
                        } catch (e2) {
                            console.warn('[App] request_screenshot capture failed:', e2);
                        }
                    })();

                // -------- system turn end (agent_callback — no proactive chat) --------
                } else if (response.type === 'system' && response.data === 'turn end agent_callback') {
                    if (window.reactChatWindowHost && typeof window.reactChatWindowHost.clearPendingRollbackDraft === 'function') {
                        window.reactChatWindowHost.clearPendingRollbackDraft(response.request_id);
                    }
                    if (response.request_id && window._lastSubmittedRequestId === response.request_id) {
                        window._lastSubmittedText = '';
                        window._lastSubmittedRequestId = '';
                    }
                    console.log('[WS] turn end (agent_callback) — skipping proactive chat schedule');
                    logAssistantLifecycle('ws:turn_end_agent_callback:received');
                    try {
                        if (typeof window.setReactMessageStatus === 'function' && window.currentGeminiMessage) {
                            window.setReactMessageStatus(window.currentGeminiMessage, 'assistant', 'sent');
                        }
                        window._pendingMusicCommand = '';
                        if (window._structuredGeminiStreaming) {
                            window._realisticGeminiBuffer = '';
                            window._structuredGeminiStreaming = false;
                            return;
                        }
                        var rest = typeof window._realisticGeminiBuffer === 'string'
                            ? window._realisticGeminiBuffer.replace(/\[play_music:[^\]]*(\]|$)/g, '')
                            : '';
                        rest = rest.replace(/\[play_music:[^\]]*(\]|$)/g, '');
                        window._realisticGeminiBuffer = '';
                        var trimmed = rest.replace(/^\s+/, '').replace(/\s+$/, '');
                        if (trimmed) {
                            window._realisticGeminiQueue = window._realisticGeminiQueue || [];
                            window._realisticGeminiQueue.push(trimmed);
                            if (typeof window.processRealisticQueue === 'function') {
                                window.processRealisticQueue(window._realisticGeminiVersion || 0);
                            }
                        }
                    } catch (e3) {
                        console.warn('[WS] turn end agent_callback flush failed:', e3);
                    }
                    if (!S.assistantTurnId && S.assistantTurnAwaitingBubble) {
                        ensureAssistantTurnStarted('turn_end_agent_callback_fallback');
                    }
                    var agentCallbackTurnId = resolveAssistantLifecycleTurnId();
                    if (agentCallbackTurnId) {
                        logAssistantLifecycle('ws:turn_end_agent_callback:emit', {
                            turnId: agentCallbackTurnId
                        });
                        emitAssistantLifecycleEvent('neko-assistant-turn-end', {
                            turnId: agentCallbackTurnId,
                            source: 'turn_end_agent_callback'
                        });
                    } else {
                        logAssistantLifecycle('ws:turn_end_agent_callback:clear_pending');
                    }
                    clearPendingAssistantTurnStart();

                    // 主动消息 / 热切换回调也产生了 AI 文本（来自 send_lanlan_response），
                    // 同样需要为字幕翻译。情感分析 / proactive backoff 维持原行为不动。
                    (function () {
                        var bufferedFullText = typeof window._geminiTurnFullText === 'string'
                            ? window._geminiTurnFullText
                            : '';
                        var fallbackFromBubble = (window.currentGeminiMessage &&
                            window.currentGeminiMessage.nodeType === Node.ELEMENT_NODE &&
                            window.currentGeminiMessage.isConnected &&
                            typeof window.currentGeminiMessage.textContent === 'string')
                            ? window.currentGeminiMessage.textContent.replace(/^\[\d{2}:\d{2}:\d{2}\] \u{1F380} /, '')
                            : '';
                        var fullText = (bufferedFullText && bufferedFullText.trim()) ? bufferedFullText : fallbackFromBubble;
                        fullText = fullText.replace(/\[play_music:[^\]]*(\]|$)/g, '').trim();
                        if (!fullText) return;
                        // 结构化 turn（markdown/code/table/latex）→ 字幕收尾为 [markdown] 占位，不翻译
                        if (window._turnIsStructured) {
                            if (typeof window.finalizeSubtitleAsStructured === 'function') {
                                try { window.finalizeSubtitleAsStructured(); } catch (_) {}
                            }
                            return;
                        }
                        (async function () {
                            try {
                                if (typeof window.translateAndShowSubtitle === 'function') {
                                    await window.translateAndShowSubtitle(fullText);
                                }
                            } catch (transError) {
                                console.error('[Subtitle] agent_callback translate failed:', transError);
                            }
                        })();
                    })();

                // -------- system turn end --------
                } else if (response.type === 'system' && response.data === 'turn end') {
                    if (window.reactChatWindowHost && typeof window.reactChatWindowHost.clearPendingRollbackDraft === 'function') {
                        window.reactChatWindowHost.clearPendingRollbackDraft(response.request_id);
                    }
                    if (response.request_id && window._lastSubmittedRequestId === response.request_id) {
                        window._lastSubmittedText = '';
                        window._lastSubmittedRequestId = '';
                    }
                    console.log(window.t('console.turnEndReceived'));
                    logAssistantLifecycle('ws:turn_end:received');
                    // Flush remaining buffer
                    try {
                        if (typeof window.setReactMessageStatus === 'function' && window.currentGeminiMessage) {
                            window.setReactMessageStatus(window.currentGeminiMessage, 'assistant', 'sent');
                        }
                        window._pendingMusicCommand = '';
                        if (window._structuredGeminiStreaming) {
                            window._realisticGeminiBuffer = '';
                            window._structuredGeminiStreaming = false;
                        } else {
                        var rest = typeof window._realisticGeminiBuffer === 'string'
                            ? window._realisticGeminiBuffer.replace(/\[play_music:[^\]]*(\]|$)/g, '')
                            : '';
                        rest = rest.replace(/\[play_music:[^\]]*(\]|$)/g, '');
                        window._realisticGeminiBuffer = '';
                        var trimmed = rest.replace(/^\s+/, '').replace(/\s+$/, '');
                        if (trimmed) {
                            window._realisticGeminiQueue = window._realisticGeminiQueue || [];
                            window._realisticGeminiQueue.push(trimmed);
                            if (typeof window.processRealisticQueue === 'function') {
                                window.processRealisticQueue(window._realisticGeminiVersion || 0);
                            }
                        }
                        }
                    } catch (e3) {
                        console.warn(window.t('console.turnEndFlushFailed'), e3);
                    }
                    if (!S.assistantTurnId && S.assistantTurnAwaitingBubble) {
                        ensureAssistantTurnStarted('turn_end_fallback');
                    }
                    var assistantTurnId = resolveAssistantLifecycleTurnId();
                    if (assistantTurnId) {
                        logAssistantLifecycle('ws:turn_end:emit', {
                            turnId: assistantTurnId
                        });
                        emitAssistantLifecycleEvent('neko-assistant-turn-end', {
                            turnId: assistantTurnId,
                            source: 'turn_end'
                        });
                    } else {
                        logAssistantLifecycle('ws:turn_end:clear_pending');
                    }
                    clearPendingAssistantTurnStart();

                    // Emotion analysis & translation on turn completion
                    (function () {
                        var bufferedFullText = typeof window._geminiTurnFullText === 'string'
                            ? window._geminiTurnFullText
                            : '';
                        var fallbackFromBubble = (window.currentGeminiMessage &&
                            window.currentGeminiMessage.nodeType === Node.ELEMENT_NODE &&
                            window.currentGeminiMessage.isConnected &&
                            typeof window.currentGeminiMessage.textContent === 'string')
                            ? window.currentGeminiMessage.textContent.replace(/^\[\d{2}:\d{2}:\d{2}\] \u{1F380} /, '')
                            : '';

                        var fullText = (bufferedFullText && bufferedFullText.trim()) ? bufferedFullText : fallbackFromBubble;

                        // Trigger music bubble generation
                        if (typeof window.processMusicCommands === 'function' && fullText) {
                            window.processMusicCommands(fullText);
                        }

                        // Strip music commands before emotion analysis / subtitle translation
                        fullText = fullText.replace(/\[play_music:[^\]]*(\]|$)/g, '').trim();

                        if (!fullText || !fullText.trim()) {
                            return;
                        }

                        // Emotion analysis (5s timeout)
                        setTimeout(async function () {
                            try {
                                var emotionPromise = (typeof window.analyzeEmotion === 'function')
                                    ? window.analyzeEmotion(fullText)
                                    : Promise.resolve(null);
                                var timeoutPromise = new Promise(function (_, reject2) {
                                    setTimeout(function () { reject2(new Error('情感分析超时')); }, 5000);
                                });
                                var emotionResult = await Promise.race([emotionPromise, timeoutPromise]);
                                if (emotionResult && emotionResult.emotion) {
                                    console.log(window.t('console.emotionAnalysisComplete'), emotionResult);
                                    if (typeof window.applyEmotion === 'function') window.applyEmotion(emotionResult.emotion);
                                    if (assistantTurnId) {
                                        emitAssistantLifecycleEvent('neko-assistant-emotion-ready', {
                                            turnId: assistantTurnId,
                                            emotion: emotionResult.emotion,
                                            source: 'emotion_analysis'
                                        });
                                    }
                                }
                            } catch (emotionError) {
                                if (emotionError.message === '情感分析超时') {
                                    console.warn(window.t('console.emotionAnalysisTimeout'));
                                } else {
                                    console.warn(window.t('console.emotionAnalysisFailed'), emotionError);
                                }
                            }
                        }, 100);

                        // Frontend subtitle finalization: subtitle.js 内部根据开关决定是否
                        // 真正发请求；不需要的语言会保留流式累积的原文，不会清空字幕。
                        // 结构化 turn 收尾为 [markdown] 占位，跳过翻译链路。
                        if (window._turnIsStructured) {
                            if (typeof window.finalizeSubtitleAsStructured === 'function') {
                                try { window.finalizeSubtitleAsStructured(); } catch (_) {}
                            }
                            return;
                        }
                        (async function () {
                            try {
                                if (typeof window.translateAndShowSubtitle === 'function') {
                                    await window.translateAndShowSubtitle(fullText);
                                }
                            } catch (transError) {
                                console.error(window.t('console.translationProcessFailed'), {
                                    error: transError.message,
                                    stack: transError.stack,
                                    fullText: fullText.substring(0, 50) + '...'
                                });
                                if (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') {
                                    console.warn(window.t('console.translationUnavailable'));
                                }
                            }
                        })();
                    })();

                    // AI turn_end 后只 reschedule，不 reset backoff。
                    // 理由：turn_end 无法区分"用户发话引发的 turn"和"proactive 自己引发的 turn"，
                    // 如果一律 reset 会让 proactive 自己的 turn 把退避清零 → 指数退避形同虚设。
                    // 用户真的说话时会由 sendTextPayload / 录音开关等路径单独 reset，
                    // 不依赖 turn_end。语音模式本来就不退避，只是"从 turn end 开始算下一个间隔"。
                    var hasChatMode = (typeof window.hasAnyChatModeEnabled === 'function') ? window.hasAnyChatModeEnabled() : false;
                    if (S.proactiveChatEnabled && hasChatMode) {
                        if (typeof window.scheduleProactiveChat === 'function') {
                            window.scheduleProactiveChat();
                        }
                    }

                // -------- session_preparing --------
                } else if (response.type === 'session_preparing') {
                    console.log(window.t('console.sessionPreparingReceived'), response.input_mode);
                    if (response.input_mode !== 'text') {
                        var preparingMessage = window.t ? window.t('app.voiceSystemPreparing') : '语音系统准备中，请稍候...';
                        if (typeof window.showVoicePreparingToast === 'function') window.showVoicePreparingToast(preparingMessage);
                    }

                // -------- session_started --------
                } else if (response.type === 'session_started') {
                    console.log(window.t('console.sessionStartedReceived'), response.input_mode);
                    S.isTextSessionActive = response.input_mode === 'text';

                    // Multi-window 文本框对偶 hide：每个 webview（index.html 主窗口、
                    // chat.html 子窗口）都通过自己的 ws 收到 session_started，借此
                    // 各自 hide 自己的 #text-input-area，不依赖
                    // startMicCapture/syncVoiceChatComposerHidden 的 BroadcastChannel
                    // 链路。原来 hide 只挂在主窗口 startMicCapture 上：
                    //   - chat.html 子窗口无麦按钮永不调 startMicCapture
                    //   - reload 后某些 audio session 启动路径不走 startMicCapture
                    //   - BroadcastChannel 在 reload init 时序窗口里错过事件
                    // 都会让子窗口的 #text-input-area 始终可见可输入，用户在
                    // audio session 中打字 → 后端 start_session(text) → 撕重建
                    // → 撞 PR #1176 修的 race（"neko 已离开"）。本路径与下方
                    // session_ended_by_server 1844-1846 的 unhide 对偶，移动端
                    // 维持原来"不 hide"设计（UI 上手机屏小希望保留文本框可见）。
                    var _tiaStarted = document.getElementById('text-input-area');
                    if (_tiaStarted) {
                        if (response.input_mode === 'text') {
                            _tiaStarted.classList.remove('hidden');
                        } else if (!window.appUtils || !window.appUtils.isMobile()) {
                            _tiaStarted.classList.add('hidden');
                        }
                    }
                    if (typeof window.syncVoiceChatComposerHidden === 'function') {
                        var _shouldHide = response.input_mode !== 'text'
                            && (!window.appUtils || !window.appUtils.isMobile());
                        window.syncVoiceChatComposerHidden(_shouldHide);
                    }

                    setTimeout(function () {
                        if (typeof window.hideVoicePreparingToast === 'function') window.hideVoicePreparingToast();
                        if (S.sessionStartedResolver) {
                            if (window.sessionTimeoutId) {
                                clearTimeout(window.sessionTimeoutId);
                                window.sessionTimeoutId = null;
                            }
                            S.sessionStartedResolver(response.input_mode);
                            S.sessionStartedResolver = null;
                            S.sessionStartedRejecter = null;
                        }
                    }, 500);

                    // 语音模式：session 开始 5 秒内无 transcription，启动 proactive chat 计时器
                    if (response.input_mode !== 'text' && S.proactiveChatEnabled && !S.gameRouteActive) {
                        if (S._voiceSessionInitialTimer) {
                            clearTimeout(S._voiceSessionInitialTimer);
                        }
                        S._voiceSessionInitialTimer = setTimeout(function () {
                            S._voiceSessionInitialTimer = null;
                            if (S.isRecording && S.proactiveChatEnabled) {
                                console.log('[ProactiveChat] Session 开始 5 秒无 transcription，启动计时器');
                                if (typeof window.scheduleProactiveChat === 'function') window.scheduleProactiveChat();
                            }
                        }, 5000);
                    }

                // -------- session_failed --------
                } else if (response.type === 'session_failed') {
                    console.log(window.t('console.sessionFailedReceived'), response.input_mode);
                    if (typeof window.hideVoicePreparingToast === 'function') window.hideVoicePreparingToast();
                    if (window.sessionTimeoutId) {
                        clearTimeout(window.sessionTimeoutId);
                        window.sessionTimeoutId = null;
                    }
                    if (S.sessionStartedRejecter) {
                        S.sessionStartedRejecter(new Error(response.message || (window.t ? window.t('app.sessionFailed') : 'Session启动失败')));
                    } else {
                        // Fallback: reset UI when Promise already consumed
                        var _mb2 = micButton();
                        if (_mb2) { _mb2.classList.remove('active'); _mb2.classList.remove('recording'); _mb2.disabled = false; }
                        var _mu2 = muteButton(); if (_mu2) _mu2.disabled = true;
                        var _sb2 = screenButton(); if (_sb2) _sb2.disabled = true;
                        var _st2 = stopButton(); if (_st2) _st2.disabled = true;
                        var _rs2 = resetSessionButton(); if (_rs2) _rs2.disabled = false;
                        if (typeof window.syncFloatingMicButtonState === 'function') window.syncFloatingMicButtonState(false);
                        if (typeof window.syncFloatingScreenButtonState === 'function') window.syncFloatingScreenButtonState(false);
                        window.isMicStarting = false;
                        S.isSwitchingMode = false;
                        var _tia = document.getElementById('text-input-area');
                        if (_tia) _tia.classList.remove('hidden');
                        if (typeof window.syncVoiceChatComposerHidden === 'function') window.syncVoiceChatComposerHidden(false);
                    }
                    S.sessionStartedResolver = null;
                    S.sessionStartedRejecter = null;

                // -------- session_ended_by_server --------
                } else if (response.type === 'session_ended_by_server') {
                    console.log('[App] Session ended by server, input_mode:', response.input_mode);
                    S.isTextSessionActive = false;
                    clearAssistantLifecycleOnDisconnect('session_ended_by_server');

                    if (S.sessionStartedRejecter) {
                        try { S.sessionStartedRejecter(new Error('Session ended by server')); } catch (_e2) { }
                    }
                    S.sessionStartedResolver = null;
                    S.sessionStartedRejecter = null;

                    if (window.sessionTimeoutId) {
                        clearTimeout(window.sessionTimeoutId);
                        window.sessionTimeoutId = null;
                    }

                    if (S.isRecording) {
                        if (typeof window.stopRecording === 'function') window.stopRecording();
                    }

                    (async function () {
                        if (typeof window.clearAudioQueue === 'function') await window.clearAudioQueue();
                    })();

                    if (typeof window.hideVoicePreparingToast === 'function') window.hideVoicePreparingToast();

                    // Restore UI to idle state
                    var _mb3 = micButton();
                    if (_mb3) { _mb3.classList.remove('active'); _mb3.classList.remove('recording'); _mb3.disabled = false; }
                    var _sb3 = screenButton(); if (_sb3) _sb3.classList.remove('active');
                    var _ts = textSendButton(); if (_ts) _ts.disabled = false;
                    var _ti = textInputBox(); if (_ti) _ti.disabled = false;
                    var _ss = screenshotButton(); if (_ss) _ss.disabled = false;
                    var _mu3 = muteButton(); if (_mu3) _mu3.disabled = true;
                    var _sb4 = screenButton(); if (_sb4) _sb4.disabled = true;
                    var _st3 = stopButton(); if (_st3) _st3.disabled = true;
                    var _rs3 = resetSessionButton(); if (_rs3) _rs3.disabled = true;
                    var _rt2 = returnSessionButton(); if (_rt2) _rt2.disabled = true;

                    var _tia2 = document.getElementById('text-input-area');
                    if (_tia2) _tia2.classList.remove('hidden');
                    if (typeof window.syncVoiceChatComposerHidden === 'function') window.syncVoiceChatComposerHidden(false);

                    if (typeof window.syncFloatingMicButtonState === 'function') window.syncFloatingMicButtonState(false);
                    if (typeof window.syncFloatingScreenButtonState === 'function') window.syncFloatingScreenButtonState(false);

                    window.isMicStarting = false;
                    S.isSwitchingMode = false;

                // -------- reload_page --------
                } else if (response.type === 'reload_page') {
                    console.log(window.t('console.reloadPageReceived'), response.message);
                    var reloadMsg = window.translateStatusMessage ? window.translateStatusMessage(response.message) : response.message;
                    if (typeof window.showStatusToast === 'function') {
                        window.showStatusToast(reloadMsg || (window.t ? window.t('app.configUpdated') : '配置已更新，页面即将刷新'), 3000);
                    }
                    // 后端在发 reload_page 之前已经 end_session，前端 2.5s 后才真
                    // reload。这 2.5s 内 isTextSessionActive 若残留 true，用户敲
                    // 文字会绕过 start_session action 直接送 stream_data，错过
                    // websocket_router 的 reset_session_start_circuit 守卫，触发
                    // 后端"未指定 ↔ 免费 音色切换后概率连接失败"那条路径。
                    S.isTextSessionActive = false;
                    setTimeout(function () {
                        console.log(window.t('console.reloadPageStarting'));
                        if (window.closeAllSettingsWindows) window.closeAllSettingsWindows();
                        window.location.reload();
                    }, 2500);

                // -------- auto_close_mic --------
                } else if (response.type === 'auto_close_mic') {
                    console.log(window.t('console.autoCloseMicReceived'));
                    if (S.isRecording) {
                        var _mu4 = muteButton(); if (_mu4) _mu4.click();
                        if (typeof window.showStatusToast === 'function') {
                            window.showStatusToast(response.message || (window.t ? window.t('app.autoMuteTimeout') : '长时间无语音输入，已自动关闭麦克风'), 4000);
                        }
                    } else {
                        var _mb4 = micButton();
                        if (_mb4) { _mb4.classList.remove('active'); _mb4.classList.remove('recording'); }
                        if (typeof window.syncFloatingMicButtonState === 'function') window.syncFloatingMicButtonState(false);
                        if (typeof window.showStatusToast === 'function') {
                            window.showStatusToast(response.message || (window.t ? window.t('app.autoMuteTimeout') : '长时间无语音输入，已自动关闭麦克风'), 4000);
                        }
                    }

                // -------- music action --------
                } else if (response.action === 'music') {
                    var searchTerm = response.search_term;
                    if (searchTerm) {
                        console.log('[Music] Received music action with search term: ' + searchTerm);
                        if (typeof window.showStatusToast === 'function') {
                            var searchMsg = window.t('music.searching', { query: searchTerm, defaultValue: '正在为您搜索: ' + searchTerm });
                            window.showStatusToast(searchMsg, 2000);
                        }

                        window._currentMusicSearchEpoch = (window._currentMusicSearchEpoch || 0) + 1;
                        var myEpoch = window._currentMusicSearchEpoch;

                        fetch('/api/music/search?query=' + encodeURIComponent(searchTerm))
                            .then(function (res) { return res.json(); })
                            .then(function (result) {
                                if (typeof myEpoch !== 'undefined' && typeof window._currentMusicSearchEpoch !== 'undefined') {
                                    if (myEpoch !== window._currentMusicSearchEpoch) {
                                        console.log('[Music] 丢弃过期的搜索结果: ' + searchTerm);
                                        return;
                                    }
                                }
                                if (result.netease_cookie_invalid && typeof window.showStatusToast === 'function') {
                                    var now2 = Date.now();
                                    if (!window._cookieWarnLastTime || now2 - window._cookieWarnLastTime > 300000) {
                                        var musiccookieWarnMsg2 = (window.t && window.t('music.cookieExpired')) || '音乐Cookie已失效';
                                        window.showStatusToast(musiccookieWarnMsg2, 5000);
                                        window._cookieWarnLastTime = now2;
                                    }
                                }

                                if (result.success) {
                                    if (result.data && result.data.length > 0) {
                                        var track = result.data[0];
                                        if (typeof window.dispatchMusicPlay === 'function') window.dispatchMusicPlay(track);
                                    } else {
                                        console.warn('[Music] API did not find a song for: ' + searchTerm);
                                        if (typeof window.showStatusToast === 'function') {
                                            var notFoundMsg = window.t('music.notFound', { query: searchTerm, defaultValue: '找不到歌曲: ' + searchTerm });
                                            window.showStatusToast(notFoundMsg, 3000);
                                        }
                                    }
                                } else {
                                    console.error('[Music] Music search API returned error:', result.message || result.error);
                                    if (typeof window.showStatusToast === 'function') {
                                        var failMsg2 = window.safeT ? window.safeT('music.searchFailed', '音乐搜索失败') : '音乐搜索失败';
                                        var detailMsg = result.message || result.error || failMsg2;
                                        window.showStatusToast(detailMsg, 3000);
                                    }
                                }
                            })
                            .catch(function (e4) {
                                if (typeof myEpoch !== 'undefined' && typeof window._currentMusicSearchEpoch !== 'undefined') {
                                    if (myEpoch !== window._currentMusicSearchEpoch) return;
                                }
                                console.error('[Music] Music search API call failed:', e4);
                                if (typeof window.showStatusToast === 'function') {
                                    var failMsg3 = window.safeT ? window.safeT('music.searchFailed', '音乐搜索失败') : '音乐搜索失败';
                                    window.showStatusToast(failMsg3, 3000);
                                }
                            });
                    }
                // -------- music allowlist add --------
                } else if (response.type === 'music_allowlist_add') {
                    if (window.MusicPluginAPI && response.domains) {
                        console.log('[Music] Received allowlist update from backend:', response.domains);
                        window.MusicPluginAPI.addAllowlist(response.domains);
                    }

                // -------- music play url --------
                } else if (response.type === 'music_play_url') {
                    if (response.url && typeof window.dispatchMusicPlay === 'function') {
                        console.log('[Music] Received direct play command from backend:', response.url);
                        window.dispatchMusicPlay({
                            name: response.name || 'Plugin Music',
                            artist: response.artist || 'External',
                            url: response.url
                        });
                    }

                // -------- repetition_warning --------
                } else if (response.type === 'repetition_warning') {
                    console.log(window.t('console.repetitionWarningReceived'), response.name);
                    var warningMessage = window.t
                        ? window.t('app.repetitionDetected', { name: response.name })
                        : ('检测到高重复度对话。建议您终止对话，让' + response.name + '休息片刻。');
                    if (typeof window.showStatusToast === 'function') window.showStatusToast(warningMessage, 8000);

                // -------- mini_game_invite_options --------
                // 后端投递 mini-game 邀请时跟 invite text 一起 push 这条 options。
                // 通用 ChoicePrompt 抽象，前端 ChoiceWindow 渲染三按钮（accept /
                // decline / later）。多窗口模式下消息走 RAW_MESSAGE forwarding 自然
                // 转给 chat.html，无需新 IPC channel。
                } else if (response.type === 'mini_game_invite_options') {
                    if (window.reactChatWindowHost
                            && typeof window.reactChatWindowHost.setMiniGameInvitePrompt === 'function') {
                        window.reactChatWindowHost.setMiniGameInvitePrompt({
                            sessionId: response.session_id || '',
                            gameType: response.game_type || '',
                            options: Array.isArray(response.options) ? response.options : [],
                        });
                    }

                // -------- mini_game_invite_resolved --------
                // 邀请被 resolve（任一 outcome：accept / cooldown / suppress）→
                // 前端 dismiss prompt UI（cross-window 一致性，pet + chat.html
                // 多窗口同时显示 prompt 时全部清掉）。accept 时 payload 同时带
                // game_url 当 launch 信号——前端 window.open 让 Electron 主进程
                // setWindowOpenHandler 拦截开独立 BrowserWindow，dedupe 由
                // launched session_id 保护防止双开。
                } else if (response.type === 'mini_game_invite_resolved') {
                    if (window.reactChatWindowHost
                            && typeof window.reactChatWindowHost.handleMiniGameInviteResolved === 'function') {
                        window.reactChatWindowHost.handleMiniGameInviteResolved({
                            sessionId: response.session_id || '',
                            action: response.action || '',
                            gameType: response.game_type || '',
                            url: response.game_url || '',
                        });
                    }

                // -------- game_window_state_change --------
                // 后端 game_route_start 激活后推 'opened'，_finalize 翻 inactive
                // 后推 'closed'。前端把它转成 DOM 自定义事件让 chat.html / pet
                // index.html 各自挂监听做布局联动（chat.html → 触发内部 collapse
                // + 移到左下角；index.html → 加 body class 隐藏 live2d/vrm/mmd
                // 容器）。多窗口模式下 RAW_MESSAGE forwarding 把同一条 WS 转给
                // chat.html，两边监听同一个 DOM 事件名即可。
                } else if (response.type === 'game_window_state_change') {
                    try {
                        var detail = {
                            action: response.action || '',
                            lanlanName: response.lanlan_name || '',
                            gameType: response.game_type || '',
                            sessionId: response.session_id || ''
                        };
                        window.dispatchEvent(new CustomEvent('neko-game-window-state-change', { detail: detail }));
                    } catch (gwErr) {
                        console.warn('[GameWindow] dispatch failed:', gwErr);
                    }
                }

            } catch (parseError) {
                console.error(window.t('console.messageProcessingFailed'), parseError);
            }
        };

        // ---- onclose ----
        S.socket.onclose = function () {
            // Stale onclose guard: background-tab throttling (or async scheduling) can
            // delay an old socket's onclose until after a replacement connectWebSocket()
            // has already run onopen and started a new session. In that case the mutations
            // below (heartbeat clear, recording/session reset, button state, audio queue)
            // would corrupt the live new session. Skip everything when this socket is stale.
            if (S.socket !== _thisSocket) {
                console.log('[WS] stale onclose skipped (socket already replaced)');
                return;
            }
            console.log(window.t('console.websocketClosed'));
            clearAssistantLifecycleOnDisconnect('socket_close');

            // Clear heartbeat
            if (S.heartbeatInterval) {
                clearInterval(S.heartbeatInterval);
                S.heartbeatInterval = null;
                console.log(window.t('console.heartbeatStopped'));
            }

            // Reset text session state
            if (S.isTextSessionActive) {
                S.isTextSessionActive = false;
                console.log(window.t('console.websocketDisconnectedResetText'));
            }

            // Reset voice recording state & resources
            if (S.isRecording || window.isMicStarting) {
                console.log('WebSocket断开时重置语音录制状态');
                S.isRecording = false;
                window.isRecording = false;
                window.isMicStarting = false;
                window.currentGeminiMessage = null;
                S.lastVoiceUserMessage = null;
                S.lastVoiceUserMessageTime = 0;

                if (typeof window.stopSilenceDetection === 'function') window.stopSilenceDetection();
                S.inputAnalyser = null;

                if (S.stream) {
                    S.stream.getTracks().forEach(function (track) { track.stop(); });
                    S.stream = null;
                }

                if (S.audioContext && S.audioContext.state !== 'closed') {
                    S.audioContext.close();
                    S.audioContext = null;
                    S.workletNode = null;
                }
            }

            // Reset mode switching flag
            if (S.isSwitchingMode) {
                console.log('WebSocket断开时重置模式切换标志');
                S.isSwitchingMode = false;
            }

            // Clean up session Promise
            if (S.sessionStartedResolver || S.sessionStartedRejecter) {
                console.log('WebSocket断开时清理session Promise');
                if (S.sessionStartedRejecter) {
                    try { S.sessionStartedRejecter(new Error('WebSocket连接断开')); } catch (_e3) { }
                }
                S.sessionStartedResolver = null;
                S.sessionStartedRejecter = null;
            }

            if (window.sessionTimeoutId) {
                clearTimeout(window.sessionTimeoutId);
                window.sessionTimeoutId = null;
            }

            // Clear audio queue
            (async function () {
                if (typeof window.clearAudioQueue === 'function') await window.clearAudioQueue();
            })();

            if (typeof window.hideVoicePreparingToast === 'function') window.hideVoicePreparingToast();

            // Reset button states
            var _mb5 = micButton();
            if (_mb5) { _mb5.classList.remove('active'); _mb5.classList.remove('recording'); _mb5.disabled = false; }
            var _sb5 = screenButton(); if (_sb5) _sb5.classList.remove('active');
            var _ts2 = textSendButton(); if (_ts2) _ts2.disabled = false;
            var _ti2 = textInputBox(); if (_ti2) _ti2.disabled = false;
            var _ss2 = screenshotButton(); if (_ss2) _ss2.disabled = false;

            var _mu5 = muteButton(); if (_mu5) _mu5.disabled = true;
            var _sb6 = screenButton(); if (_sb6) _sb6.disabled = true;
            var _st4 = stopButton(); if (_st4) _st4.disabled = true;
            var _rs4 = resetSessionButton(); if (_rs4) _rs4.disabled = true;
            var _rt3 = returnSessionButton(); if (_rt3) _rt3.disabled = true;

            var _tia3 = document.getElementById('text-input-area');
            if (_tia3) _tia3.classList.remove('hidden');
            if (typeof window.syncVoiceChatComposerHidden === 'function') window.syncVoiceChatComposerHidden(false);

            if (typeof window.syncFloatingMicButtonState === 'function') window.syncFloatingMicButtonState(false);
            if (typeof window.syncFloatingScreenButtonState === 'function') window.syncFloatingScreenButtonState(false);

            // Auto-reconnect: skip if switching catgirl OR this socket was already
            // replaced by a newer connectWebSocket() call (prevents reconnect storm
            // when the old socket's onclose fires after the switch completes).
            if (!S.isSwitchingCatgirl && S.socket === _thisSocket) {
                S.autoReconnectTimeoutId = setTimeout(connectWebSocket, 3000);
            }
        };

        // ---- onerror ----
        S.socket.onerror = function (error) {
            console.error(window.t('console.websocketError'), error);
        };
    }
    mod.connectWebSocket = connectWebSocket;
    mod.ensureAssistantTurnStarted = ensureAssistantTurnStarted;
    mod.clearPendingAssistantTurnStart = clearPendingAssistantTurnStart;

    // ========================  Exported methods  ========================

    /** Send raw JSON action over WebSocket */
    mod.send = function (payload) {
        if (S.socket && S.socket.readyState === WebSocket.OPEN) {
            S.socket.send(typeof payload === 'string' ? payload : JSON.stringify(payload));
        }
    };

    /** Stop heartbeat (e.g. before intentional disconnect) */
    mod.stopHeartbeat = function () {
        if (S.heartbeatInterval) {
            clearInterval(S.heartbeatInterval);
            S.heartbeatInterval = null;
        }
    };

    /** Cancel any pending auto-reconnect timer */
    mod.cancelAutoReconnect = function () {
        if (S.autoReconnectTimeoutId) {
            clearTimeout(S.autoReconnectTimeoutId);
            S.autoReconnectTimeoutId = null;
        }
    };

    // ========================  Backward-compat globals  ========================
    window.connectWebSocket = connectWebSocket;
    window.ensureWebSocketOpen = ensureWebSocketOpen;
    window.ensureAssistantTurnStarted = ensureAssistantTurnStarted;
    window.clearPendingAssistantTurnStart = clearPendingAssistantTurnStart;

    // ========================  Greeting check (after model loaded)  ========================
    // 需要 WS 已连接 AND 模型已加载 两个条件同时满足才发送，
    // 无论哪个先就绪都由后到的那个触发。
    function _isElementVisible(el) {
        if (!el || el.hidden) return false;
        var style = window.getComputedStyle ? window.getComputedStyle(el) : null;
        if (style && (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0')) {
            return false;
        }
        var rect = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
        return !rect || rect.width > 0 || rect.height > 0;
    }
    function _hasVisibleGreetingBlocker(selectors) {
        for (var i = 0; i < selectors.length; i += 1) {
            var nodes = document.querySelectorAll(selectors[i]);
            for (var j = 0; j < nodes.length; j += 1) {
                if (_isElementVisible(nodes[j])) return true;
            }
        }
        return false;
    }
    function _isHomeTutorialPage() {
        if (window.location && typeof window.location.pathname === 'string') {
            var path = window.location.pathname || '/';
            return path === '/' || path === '/index.html';
        }
        var manager = window.universalTutorialManager || null;
        return !!(manager && manager.currentPage === 'home');
    }
    function _isTutorialBlockingGreeting() {
        if (!_isHomeTutorialPage()) return false;
        try {
            if (isHomeTutorialLockedForGreeting()) {
                return true;
            }
        } catch (_) {}
        return window.isInTutorial === true
            || !!(window.universalTutorialManager && window.universalTutorialManager.isTutorialRunning);
    }
    function _isGreetingCheckBlocked() {
        if (!S.socket || S.socket.readyState !== WebSocket.OPEN) return true;
        if (_isTutorialBlockingGreeting()) return true;
        if (S.isRecording || S.isPlaying) return true;
        if (S.assistantTurnId && S.assistantTurnId !== S.assistantTurnCompletedId) return true;
        if (S.assistantTurnAwaitingBubble || S.assistantSpeechActiveTurnId) return true;
        return _hasVisibleGreetingBlocker([
            '#prominent-notice-overlay',
            '.modal-overlay',
            '.modal-dialog',
            '.driver-popover',
            '.driver-overlay',
            '.storage-location-completion-card',
            '#storage-location-overlay',
            '.storage-location-modal'
        ]);
    }
    function _resetGreetingCheckRetry(clearTimer) {
        S._greetingCheckRetryDelay = 0;
        if (clearTimer && S._greetingCheckRetryTimer) {
            clearTimeout(S._greetingCheckRetryTimer);
            S._greetingCheckRetryTimer = 0;
        }
    }
    function _scheduleGreetingCheckRetry() {
        if (S._greetingCheckRetryTimer) {
            clearTimeout(S._greetingCheckRetryTimer);
        }
        var delay = Number(S._greetingCheckRetryDelay) || GREETING_CHECK_RETRY_BASE_MS;
        S._greetingCheckRetryDelay = Math.min(delay * 2, GREETING_CHECK_RETRY_MAX_MS);
        S._greetingCheckRetryTimer = setTimeout(function () {
            S._greetingCheckRetryTimer = 0;
            _sendGreetingCheckIfReady();
        }, delay);
    }
    function _sendGreetingCheckIfReady() {
        if (!S._greetingCheckPending || !S._modelReady) {
            if (!S._greetingCheckPending) _resetGreetingCheckRetry(true);
            return;
        }
        if (_isGreetingCheckBlocked()) {
            _scheduleGreetingCheckRetry();
            return;
        }
        try {
            if (S.socket && S.socket.readyState === WebSocket.OPEN) {
                // greeting_check 是 ws 链路上唯一会推 mgr.user_language 的消息。
                // 后端 set_user_language 见空串就 no-op（保留旧值，旧值是
                // start_session seed 的全局缓存），所以这里宁可送 navigator
                // 的 BCP47 也别送空串——至少能纠正 Steam SDK race 失败后留下
                // 的错误英文（例如 Steam=zh / 系统=en，i18next 还在异步拉
                // Steam API 时，navigator.language 通常已经是 zh-CN）。
                var greetingLang = '';
                try {
                    if (window.i18next && typeof window.i18next.language === 'string' && window.i18next.language) {
                        greetingLang = window.i18next.language;
                    } else if (typeof localStorage !== 'undefined') {
                        greetingLang = localStorage.getItem('i18nextLng') || '';
                    }
                    if (!greetingLang && typeof navigator !== 'undefined' && navigator.language) {
                        greetingLang = navigator.language;
                    }
                } catch (_) { greetingLang = ''; }
                S.socket.send(JSON.stringify({
                    action: 'greeting_check',
                    is_switch: !!S._greetingCheckIsSwitch,
                    language: greetingLang
                }));
                S._greetingCheckPending = false;
                _resetGreetingCheckRetry(true);
                console.log('[greeting_check] sent, is_switch=' + !!S._greetingCheckIsSwitch);
            }
        } catch (e) {
            console.warn('[greeting_check] send failed:', e);
            _scheduleGreetingCheckRetry();
        }
    }
    function _onModelReady() {
        S._modelReady = true;
        _sendGreetingCheckIfReady();
    }
    // Live2D
    var _origOnModelLoaded = null;
    function _hookLive2dModelLoaded() {
        if (window.live2dManager && typeof window.live2dManager.onModelLoaded === 'function') {
            if (window.live2dManager.onModelLoaded._greetingHooked) return;
            _origOnModelLoaded = window.live2dManager.onModelLoaded;
        }
        var prevCb = _origOnModelLoaded;
        var hookedFn = function () {
            if (prevCb) prevCb.apply(this, arguments);
            _onModelReady();
        };
        hookedFn._greetingHooked = true;
        if (window.live2dManager) window.live2dManager.onModelLoaded = hookedFn;
    }
    // 延迟 hook：live2dManager 可能还没创建
    if (window.live2dManager) _hookLive2dModelLoaded();
    else window.addEventListener('DOMContentLoaded', function () { setTimeout(_hookLive2dModelLoaded, 500); });
    // VRM / MMD
    window.addEventListener('vrm-model-loaded', _onModelReady);
    window.addEventListener('mmd-model-loaded', _onModelReady);

    // i18next 'languageChanged' → 重新把 i18n 真值同步到后端 mgr.user_language。
    // 关键场景：socket open 早于 i18next bootstrap 完成时，首次 greeting_check
    // 用 navigator/localStorage 兜底（可能跟 Steam 真值不同），i18next 异步从
    // /api/config/steam_language 拉到对的值后 fire 'languageChanged'，这里重发
    // 一条只携带 language 的 ws 消息，让后端 line 136-139 通用 language handler
    // 把 mgr.user_language 纠正回真值。不复用 greeting_check action，避免再次
    // 触发 greeting fire 逻辑——后端任何消息带 language 字段都会先调
    // set_user_language（main_routers/websocket_router.py:136-139），用任意 action
    // 即可。
    function _syncLanguageToBackend(lng) {
        if (!lng || typeof lng !== 'string') return;
        if (!S.socket || S.socket.readyState !== WebSocket.OPEN) return;
        try {
            S.socket.send(JSON.stringify({
                action: 'language_update',
                language: lng,
            }));
        } catch (e) {
            console.warn('[language_update] send failed:', e);
        }
    }
    if (window.i18next && typeof window.i18next.on === 'function') {
        window.i18next.on('languageChanged', _syncLanguageToBackend);
    } else {
        // i18next 还没就绪：监听 i18n-i18next.js 完成时 dispatch 的 localechange。
        window.addEventListener('localechange', function () {
            try {
                var lng = (window.i18next && typeof window.i18next.language === 'string')
                    ? window.i18next.language : '';
                _syncLanguageToBackend(lng);
            } catch (_) { /* noop */ }
        });
    }

    window.addEventListener('neko:home-tutorial-lock-changed', function (event) {
        var detail = event && event.detail ? event.detail : {};
        sendHomeTutorialState(detail.reason || 'lock-changed');
        if (detail.locked === false) {
            _sendGreetingCheckIfReady();
        }
    });

    // ========================  Export module  ========================
    window.appWebSocket = mod;
})();
