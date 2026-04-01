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
        if (!currentLanlanName) {
            console.warn('[WebSocket] lanlan_name is empty, wait for page config and retry');
            if (S.autoReconnectTimeoutId) {
                clearTimeout(S.autoReconnectTimeoutId);
            }
            S.autoReconnectTimeoutId = setTimeout(connectWebSocket, 500);
            return;
        }

        var protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
        var wsUrl = protocol + '://' + window.location.host + '/ws/' + currentLanlanName;
        console.log(window.t('console.websocketConnecting'), currentLanlanName, window.t('console.websocketUrl'), wsUrl);
        S.socket = new WebSocket(wsUrl);

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
        };

        // ---- onmessage ----
        S.socket.onmessage = function (event) {
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
                    if (isNewMessage) {
                        S.lastVoiceUserMessage = null;
                        S.lastVoiceUserMessageTime = 0;
                    }
                    if (typeof window.appendMessage === 'function') {
                        window.appendMessage(response.text, 'gemini', isNewMessage);
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
                    window.invalidatePendingMusicSearch();
                    var attempt = response.attempt || 0;
                    var maxAttempts = response.max_attempts || 0;
                    console.log('[Discard] AI回复被丢弃 reason=' + response.reason + ' attempt=' + attempt + '/' + maxAttempts + ' retry=' + response.will_retry);

                    window._realisticGeminiQueue = [];
                    window._realisticGeminiBuffer = '';
                    window._pendingMusicCommand = '';
                    window._realisticGeminiVersion = (window._realisticGeminiVersion || 0) + 1;

                    if (window.currentTurnGeminiBubbles && window.currentTurnGeminiBubbles.length > 0) {
                        window.currentTurnGeminiBubbles.forEach(function (bubble) {
                            if (bubble && bubble.parentNode) {
                                bubble.parentNode.removeChild(bubble);
                            }
                        });
                        window.currentTurnGeminiBubbles = [];
                    }

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
                    if ((!window.currentTurnGeminiBubbles || window.currentTurnGeminiBubbles.length === 0) &&
                        cc && cc.children && cc.children.length > 0) {
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

                    var retryMsg = window.t ? window.t('console.aiRetrying') : '猫娘链接出现异常，校准中…';
                    var failMsg = window.t ? window.t('console.aiFailed') : '猫娘链接出现异常';
                    if (typeof window.showStatusToast === 'function') {
                        window.showStatusToast(response.will_retry ? retryMsg : failMsg, 2500);
                    }

                    if (!response.will_retry && response.message) {
                        var translatedDiscardMsg = window.translateStatusMessage ? window.translateStatusMessage(response.message) : response.message;
                        var messageDiv = document.createElement('div');
                        messageDiv.classList.add('message', 'gemini');
                        messageDiv.textContent = '[' + (typeof window.getCurrentTimeString === 'function' ? window.getCurrentTimeString() : '') + '] \u{1F380} ' + translatedDiscardMsg;
                        var cc2 = chatContainer();
                        if (cc2) {
                            cc2.appendChild(messageDiv);
                            window.currentGeminiMessage = messageDiv;
                            window.currentTurnGeminiBubbles = [messageDiv];
                            cc2.scrollTop = cc2.scrollHeight;
                        }
                    } else {
                        var cc3 = chatContainer();
                        if (cc3) cc3.scrollTop = cc3.scrollHeight;
                    }

                // -------- user_transcript --------
                } else if (response.type === 'user_transcript') {
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
                    S.interruptedSpeechId = response.interrupted_speech_id || null;
                    S.pendingDecoderReset = true;
                    S.skipNextAudioBlob = false;
                    S.incomingAudioEpoch += 1;
                    S.incomingAudioBlobQueue = [];
                    S.pendingAudioChunkMetaQueue = [];

                    if (typeof window.clearAudioQueueWithoutDecoderReset === 'function') {
                        window.clearAudioQueueWithoutDecoderReset();
                    }

                // -------- audio_chunk --------
                } else if (response.type === 'audio_chunk') {
                    if (window.DEBUG_AUDIO) {
                        console.log(window.t('console.audioChunkHeaderReceived'), response);
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
                        shouldSkip: shouldSkip,
                        epoch: S.incomingAudioEpoch
                    });
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
                    try {
                        var parsed = JSON.parse(response.message);
                        if (parsed && parsed.code) statusCode = parsed.code;
                    } catch (_) { }

                    if (S.isSwitchingMode && (statusCode === 'CHARACTER_LEFT' || response.message.includes('已离开'))) {
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
                    if (typeof window.showStatusToast === 'function') window.showStatusToast(translatedMessage, 4000, { important: isCriticalError });

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
                                S.socket.send(JSON.stringify({ action: 'screenshot_response', data: dataUrl }));
                            }
                        } catch (e2) {
                            console.warn('[App] request_screenshot capture failed:', e2);
                        }
                    })();

                // -------- system turn end (agent_callback — no proactive chat) --------
                } else if (response.type === 'system' && response.data === 'turn end agent_callback') {
                    console.log('[WS] turn end (agent_callback) — skipping proactive chat schedule');
                    try {
                        window._pendingMusicCommand = '';
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

                // -------- system turn end --------
                } else if (response.type === 'system' && response.data === 'turn end') {
                    console.log(window.t('console.turnEndReceived'));
                    // Flush remaining buffer
                    try {
                        window._pendingMusicCommand = '';
                        var rest = typeof window._realisticGeminiBuffer === 'string'
                            ? window._realisticGeminiBuffer.replace(/\[play_music:[^\]]*(\]|$)/g, '')
                            : '';
                        rest = rest.replace(/\[play_music:[^\]]*(\]|$)/g, '');
                        var trimmed = rest.replace(/^\s+/, '').replace(/\s+$/, '');
                        if (trimmed) {
                            window._realisticGeminiQueue = window._realisticGeminiQueue || [];
                            window._realisticGeminiQueue.push(trimmed);
                            window._realisticGeminiBuffer = '';
                            if (typeof window.processRealisticQueue === 'function') {
                                window.processRealisticQueue(window._realisticGeminiVersion || 0);
                            }
                        }
                    } catch (e3) {
                        console.warn(window.t('console.turnEndFlushFailed'), e3);
                    }

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
                                }
                            } catch (emotionError) {
                                if (emotionError.message === '情感分析超时') {
                                    console.warn(window.t('console.emotionAnalysisTimeout'));
                                } else {
                                    console.warn(window.t('console.emotionAnalysisFailed'), emotionError);
                                }
                            }
                        }, 100);

                        // Frontend translation
                        (async function () {
                            try {
                                if (typeof getUserLanguage === 'function') {
                                    var _userLanguage = await getUserLanguage();
                                    // Only translate subtitle when subtitle toggle is on
                                    if (typeof subtitleEnabled !== 'undefined' && subtitleEnabled) {
                                        if (typeof translateAndShowSubtitle === 'function') {
                                            await translateAndShowSubtitle(fullText);
                                        }
                                    }
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

                    // Reset proactive chat backoff after AI turn completes
                    var hasChatMode = (typeof window.hasAnyChatModeEnabled === 'function') ? window.hasAnyChatModeEnabled() : false;
                    if (S.proactiveChatEnabled && hasChatMode && !S.isRecording) {
                        if (typeof window.resetProactiveChatBackoff === 'function') window.resetProactiveChatBackoff();
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
                    }
                    S.sessionStartedResolver = null;
                    S.sessionStartedRejecter = null;

                // -------- session_ended_by_server --------
                } else if (response.type === 'session_ended_by_server') {
                    console.log('[App] Session ended by server, input_mode:', response.input_mode);
                    S.isTextSessionActive = false;

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
                }

            } catch (parseError) {
                console.error(window.t('console.messageProcessingFailed'), parseError);
            }
        };

        // ---- onclose ----
        S.socket.onclose = function () {
            console.log(window.t('console.websocketClosed'));

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

            if (typeof window.syncFloatingMicButtonState === 'function') window.syncFloatingMicButtonState(false);
            if (typeof window.syncFloatingScreenButtonState === 'function') window.syncFloatingScreenButtonState(false);

            // Auto-reconnect (unless switching catgirl)
            if (!S.isSwitchingCatgirl) {
                S.autoReconnectTimeoutId = setTimeout(connectWebSocket, 3000);
            }
        };

        // ---- onerror ----
        S.socket.onerror = function (error) {
            console.error(window.t('console.websocketError'), error);
        };
    }
    mod.connectWebSocket = connectWebSocket;

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

    // ========================  Export module  ========================
    window.appWebSocket = mod;
})();
