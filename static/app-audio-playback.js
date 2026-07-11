/**
 * app-audio-playback.js — Audio playback, scheduling, lip-sync & speaker volume
 *
 * Extracted from the monolithic app.js.
 * Exposes functions via  window.appAudioPlayback  (mod)  and backward-compatible
 * window.xxx globals where the rest of the code expects them.
 *
 * Dependencies (must be loaded first):
 *   - app-state.js           → window.appState  (S), window.appConst (C), window.appUtils
 *   - ogg-opus-decoder-wrapper.js → resetOggOpusDecoder(), decodeOggOpusChunk()
 */
(function () {
    'use strict';

    const mod = {};
    const S = window.appState;
    const C = window.appConst;

    function normalizeAssistantTurnId(turnId) {
        if (turnId === undefined || turnId === null || turnId === '') {
            return null;
        }
        return String(turnId);
    }

    const ASSISTANT_TURN_COMPLETION_FALLBACK_MS = 700;
    const ASSISTANT_AUDIO_HEADER_STALL_MS = 1800;
    // 最后兜底：如果 turn-end 包压根没到（server 漏发 / packet 掉），
    // maybeFinalizeAssistantSpeech 永远 skip_completion_mismatch，
    // S.isPlaying / S.assistantSpeechActiveTurnId 没人清，proactive gate
    // 和 mic focus gate 都会卡死。此处守门：所有音频队列空了且 flag
    // 还粘着，30s 后强制 cancel 收尾。比 ASSISTANT_TURN_COMPLETION_FALLBACK_MS
    // 长一个数量级——前者覆盖正常 race，后者覆盖 server 漏包。
    const STUCK_SPEAKING_FALLBACK_MS = 30000;
    let _assistantTurnCompletionFallbackTimer = 0;
    let _assistantTurnCompletionFallbackTurnId = null;
    let _pendingAudioMetaStallTimer = 0;
    let _stuckSpeakingFallbackTimer = 0;
    const SPEECH_PLAYBACK_STATE_KEY = 'neko_speech_playback_state';
    const SPEECH_PLAYBACK_CHANNEL_NAME = 'neko_speech_playback_channel';
    const SPEECH_PLAYBACK_STATE_HEARTBEAT_MS = 200;
    let _speechPlaybackChannel = null;
    let _speechPlaybackStateHeartbeatTimer = 0;

    function getSpeechPlaybackChannel() {
        if (_speechPlaybackChannel !== null) {
            return _speechPlaybackChannel;
        }
        _speechPlaybackChannel = false;
        try {
            if (typeof BroadcastChannel !== 'undefined') {
                _speechPlaybackChannel = new BroadcastChannel(SPEECH_PLAYBACK_CHANNEL_NAME);
            }
        } catch (err) {
            _speechPlaybackChannel = false;
            if (window.DEBUG_AUDIO) {
                console.warn('[Audio] playback BroadcastChannel init failed:', err);
            }
        }
        return _speechPlaybackChannel || null;
    }

    function clearSpeechPlaybackStateHeartbeat() {
        if (_speechPlaybackStateHeartbeatTimer) {
            clearTimeout(_speechPlaybackStateHeartbeatTimer);
            _speechPlaybackStateHeartbeatTimer = 0;
        }
    }

    function scheduleSpeechPlaybackStateHeartbeat() {
        if (_speechPlaybackStateHeartbeatTimer) {
            return;
        }
        _speechPlaybackStateHeartbeatTimer = setTimeout(function () {
            _speechPlaybackStateHeartbeatTimer = 0;
            publishSpeechPlaybackState('heartbeat');
        }, SPEECH_PLAYBACK_STATE_HEARTBEAT_MS);
    }

    function publishSpeechPlaybackState(reason, patch) {
        var audioTime = S.audioPlayerContext ? S.audioPlayerContext.currentTime : 0;
        var scheduledEnd = patch && Number.isFinite(patch.scheduledEndAudioTime)
            ? patch.scheduledEndAudioTime
            : (S.nextChunkTime || 0);
        var remaining = Math.max(0, scheduledEnd - audioTime);
        var pendingAudioWork = (
            S.pendingAudioChunkMetaQueue.length > 0 ||
            S.incomingAudioBlobQueue.length > 0 ||
            !!S.pendingDecoderReset ||
            !!S.decoderResetPromise ||
            !!S.isProcessingIncomingAudioBlob
        );
        var state = Object.assign({
            type: 'speech_playback_state',
            active: remaining > 0.05 || S.scheduledSources.length > 0 || S.audioBufferQueue.length > 0 || pendingAudioWork,
            speechId: S.currentPlayingSpeechId || null,
            turnId: S.assistantSpeechActiveTurnId || S.assistantTurnId || null,
            playbackTurnId: S.assistantSpeechPlaybackTurnId || null,
            playbackStartAudioTime: Number.isFinite(S.assistantSpeechPlaybackStartAudioTime) ? S.assistantSpeechPlaybackStartAudioTime : 0,
            playbackEndAudioTime: Number.isFinite(S.assistantSpeechPlaybackEndAudioTime) ? S.assistantSpeechPlaybackEndAudioTime : 0,
            scheduledEndAudioTime: scheduledEnd,
            audioContextTime: audioTime,
            audioContextState: S.audioPlayerContext ? S.audioPlayerContext.state : '',
            remainingSeconds: remaining,
            updatedAt: Date.now(),
            reason: reason || 'update',
            source: 'audio_playback'
        }, patch || {});

        if (!state.active) {
            state.remainingSeconds = 0;
        }

        window.NekoSpeechPlaybackState = state;
        try {
            localStorage.setItem(SPEECH_PLAYBACK_STATE_KEY, JSON.stringify(state));
        } catch (_) { /* noop */ }

        var channel = getSpeechPlaybackChannel();
        if (channel) {
            try { channel.postMessage(state); } catch (_) { /* noop */ }
        }

        if (state.active) {
            scheduleSpeechPlaybackStateHeartbeat();
        } else {
            clearSpeechPlaybackStateHeartbeat();
        }
        try {
            window.dispatchEvent(new CustomEvent('neko-speech-playback-state', {
                detail: state
            }));
        } catch (_) { /* noop */ }
        return state;
    }

    function audioTraceEnabled() {
        return window.NEKO_DEBUG_BUBBLE_LIFECYCLE === true;
    }

    function logAudioLifecycle(label, extra) {
        if (!audioTraceEnabled()) {
            return;
        }
        console.log('[AudioTrace]', label, Object.assign({
            assistantTurnId: S.assistantTurnId,
            pendingTurnServerId: S.assistantPendingTurnServerId,
            assistantTurnCompletedId: S.assistantTurnCompletedId,
            assistantTurnCompletionSource: S.assistantTurnCompletionSource,
            assistantSpeechActiveTurnId: S.assistantSpeechActiveTurnId,
            assistantSpeechStartedTurnId: S.assistantSpeechStartedTurnId,
            currentPlayingSpeechId: S.currentPlayingSpeechId,
            scheduledSources: S.scheduledSources.length,
            audioBufferQueue: S.audioBufferQueue.length,
            pendingAudioMetaQueue: S.pendingAudioChunkMetaQueue.length,
            incomingAudioBlobQueue: S.incomingAudioBlobQueue.length,
            isPlaying: S.isPlaying
        }, extra || {}));
    }

    function emitAssistantSpeechLifecycleEvent(eventName, detail) {
        window.dispatchEvent(new CustomEvent(eventName, {
            detail: Object.assign({
                timestamp: Date.now()
            }, detail || {})
        }));
    }

    // Report REAL audio playback boundaries to the backend so the proactive
    // inject gate keys off actual playback (queue drained) rather than the
    // realtime API's response.done (generation finished while audio is still
    // buffered/playing). Rides the same ws as every other action, including
    // the Electron chat.html WSProxy/IPC bridge → Pet real ws. readyState
    // may be undefined on a proxy socket — send anyway (try/catch guards).
    function sendVoicePlaybackSignal(action, turnId) {
        try {
            var sock = S.socket;
            if (sock && typeof sock.send === 'function' &&
                (sock.readyState === 1 || typeof sock.readyState === 'undefined')) {
                sock.send(JSON.stringify({
                    action: action,
                    turnId: turnId || null,
                    source: 'audio_playback'
                }));
            }
        } catch (_) { /* noop — best-effort signal */ }
    }

    function getActiveAvatarModelType() {
        // 优先按当前可见容器判断，避免 Live2D 全局引用残留时抢走 VRM/MMD 的口型同步。
        var vrmContainer = document.getElementById('vrm-container');
        if (vrmContainer && vrmContainer.style.display !== 'none' && !vrmContainer.classList.contains('hidden')) {
            return 'vrm';
        }

        var mmdContainer = document.getElementById('mmd-container');
        if (mmdContainer && mmdContainer.style.display !== 'none' && !mmdContainer.classList.contains('hidden')) {
            return 'mmd';
        }

        var pngtuberContainer = document.getElementById('pngtuber-container');
        if (pngtuberContainer && pngtuberContainer.style.display !== 'none' && !pngtuberContainer.classList.contains('hidden')) {
            return 'pngtuber';
        }

        var cfg = window.lanlan_config || {};
        var modelType = String(cfg.model_type || '').toLowerCase();
        if (modelType === 'pngtuber') {
            return 'pngtuber';
        }
        if (modelType === 'live3d') {
            var subType = String(cfg.live3d_sub_type || '').toLowerCase();
            if (subType === 'vrm' || subType === 'mmd') {
                return subType;
            }
        }
        if (modelType === 'vrm' || modelType === 'mmd') {
            return modelType;
        }
        return 'live2d';
    }

    function clearPendingAudioMetaStallTimer() {
        if (_pendingAudioMetaStallTimer) {
            clearTimeout(_pendingAudioMetaStallTimer);
            _pendingAudioMetaStallTimer = 0;
        }
    }

    function pruneStalledPendingAudioMetaQueue(nowMs) {
        var currentTimeMs = Number.isFinite(nowMs) ? nowMs : Date.now();
        if (!Array.isArray(S.pendingAudioChunkMetaQueue) || S.pendingAudioChunkMetaQueue.length === 0) {
            return [];
        }

        var retained = [];
        var removed = [];
        S.pendingAudioChunkMetaQueue.forEach(function (item) {
            if (!item) {
                return;
            }

            if (item.shouldSkip) {
                removed.push(item);
                return;
            }

            if (item.epoch !== S.incomingAudioEpoch ||
                !Number.isFinite(item.receivedAt)) {
                retained.push(item);
                return;
            }

            if (currentTimeMs - item.receivedAt >= ASSISTANT_AUDIO_HEADER_STALL_MS) {
                removed.push(item);
                return;
            }

            retained.push(item);
        });

        if (removed.length === 0) {
            return removed;
        }

        S.pendingAudioChunkMetaQueue = retained;
        if (removed.some(function (item) { return item && item.speechId && item.speechId === S.currentPlayingSpeechId; }) &&
            S.scheduledSources.length === 0 &&
            S.audioBufferQueue.length === 0 &&
            S.incomingAudioBlobQueue.length === 0 &&
            !S.assistantSpeechActiveTurnId) {
            S.currentPlayingSpeechId = null;
        }

        logAudioLifecycle('pruneStalledPendingAudioMetaQueue:removed', {
            removedCount: removed.length,
            stallMs: ASSISTANT_AUDIO_HEADER_STALL_MS,
            turnIds: removed.map(function (item) { return item && item.turnId ? String(item.turnId) : null; }),
            speechIds: removed.map(function (item) { return item && item.speechId ? String(item.speechId) : null; })
        });
        return removed;
    }

    function schedulePendingAudioMetaStallCheck() {
        clearPendingAudioMetaStallTimer();

        var nextDueAt = 0;
        S.pendingAudioChunkMetaQueue.forEach(function (item) {
            if (!item ||
                item.shouldSkip ||
                item.epoch !== S.incomingAudioEpoch ||
                !Number.isFinite(item.receivedAt)) {
                return;
            }

            var dueAt = item.receivedAt + ASSISTANT_AUDIO_HEADER_STALL_MS;
            if (!nextDueAt || dueAt < nextDueAt) {
                nextDueAt = dueAt;
            }
        });

        if (!nextDueAt) {
            return;
        }

        _pendingAudioMetaStallTimer = window.setTimeout(function () {
            _pendingAudioMetaStallTimer = 0;
            var removed = pruneStalledPendingAudioMetaQueue(Date.now());
            if (removed.length > 0) {
                var candidateTurnId = null;
                removed.some(function (item) {
                    candidateTurnId = resolveAssistantAudioTurnId(item && item.turnId, item && item.speechId);
                    return !!candidateTurnId;
                });
                if (candidateTurnId) {
                    maybeFinalizeAssistantSpeech(candidateTurnId);
                } else {
                    maybeFinalizeAssistantSpeech();
                }
            }
            schedulePendingAudioMetaStallCheck();
        }, Math.max(0, nextDueAt - Date.now()));
    }

    function dispatchAssistantSpeechStart(turnId) {
        var normalizedTurnId = normalizeAssistantTurnId(turnId);
        // 新 chunk 入队 = 真实音频活动，无论是不是同一 turn 都要撤掉 stuck watchdog。
        // 否则 choppy stream 里：第一段播完 arm 表 → 第二段到来仍是同一 turn 早 return →
        // 表不撤 → 30s 到点时如果刚好在第 N 段间隙 → 误 fire cancel。
        if (normalizedTurnId) {
            clearStuckSpeakingFallback();
        }
        if (!normalizedTurnId || S.assistantSpeechActiveTurnId === normalizedTurnId) {
            return;
        }
        S.assistantSpeechActiveTurnId = normalizedTurnId;
        S.assistantSpeechStartedTurnId = normalizedTurnId;
        clearAssistantTurnCompletionFallback();
        logAudioLifecycle('dispatchAssistantSpeechStart', {
            turnId: normalizedTurnId
        });
        emitAssistantSpeechLifecycleEvent('neko-assistant-speech-start', {
            turnId: normalizedTurnId,
            source: 'audio_playback'
        });
        sendVoicePlaybackSignal('voice_play_start', normalizedTurnId);
    }

    function dispatchAssistantSpeechEnd(turnId) {
        var normalizedTurnId = normalizeAssistantTurnId(turnId);
        if (!normalizedTurnId || S.assistantSpeechActiveTurnId !== normalizedTurnId) {
            logAudioLifecycle('dispatchAssistantSpeechEnd:skip', {
                turnId: normalizedTurnId
            });
            return;
        }
        S.assistantSpeechActiveTurnId = null;
        if (S.assistantSpeechPlaybackTurnId === normalizedTurnId) {
            S.assistantSpeechPlaybackTurnId = null;
            S.assistantSpeechPlaybackStartAudioTime = 0;
            S.assistantSpeechPlaybackEndAudioTime = 0;
        }
        clearStuckSpeakingFallback();
        logAudioLifecycle('dispatchAssistantSpeechEnd', {
            turnId: normalizedTurnId
        });
        emitAssistantSpeechLifecycleEvent('neko-assistant-speech-end', {
            turnId: normalizedTurnId,
            source: 'audio_playback'
        });
        sendVoicePlaybackSignal('voice_play_end', normalizedTurnId);
    }

    function resolveAssistantSpeechCancelTurnId() {
        pruneStalledPendingAudioMetaQueue(Date.now());
        schedulePendingAudioMetaStallCheck();
        var normalizedTurnId = normalizeAssistantTurnId(S.assistantSpeechActiveTurnId);
        if (normalizedTurnId) {
            return normalizedTurnId;
        }

        var scheduledTurnId = null;
        S.scheduledSources.some(function (source) {
            scheduledTurnId = normalizeAssistantTurnId(source && source._nekoAssistantTurnId);
            return !!scheduledTurnId;
        });
        if (scheduledTurnId) {
            return scheduledTurnId;
        }

        var queuedTurnId = null;
        S.audioBufferQueue.some(function (item) {
            queuedTurnId = resolveAssistantAudioTurnId(item && item.turnId, item && item.speechId);
            return !!queuedTurnId;
        });
        if (queuedTurnId) {
            return queuedTurnId;
        }

        var pendingMetaTurnId = null;
        S.pendingAudioChunkMetaQueue.some(function (item) {
            if (!item || item.shouldSkip) {
                return false;
            }
            pendingMetaTurnId = resolveAssistantAudioTurnId(item.turnId, item.speechId);
            return !!pendingMetaTurnId;
        });
        if (pendingMetaTurnId) {
            return pendingMetaTurnId;
        }

        var incomingBlobTurnId = null;
        S.incomingAudioBlobQueue.some(function (item) {
            if (!item || item.shouldSkip) {
                return false;
            }
            incomingBlobTurnId = resolveAssistantAudioTurnId(item.turnId, item.speechId);
            return !!incomingBlobTurnId;
        });
        return incomingBlobTurnId;
    }

    function dispatchAssistantSpeechCancel(source) {
        var normalizedTurnId = resolveAssistantSpeechCancelTurnId();
        if (!normalizedTurnId) {
            logAudioLifecycle('dispatchAssistantSpeechCancel:skip', {
                source: source || 'audio_playback'
            });
            return;
        }
        S.assistantSpeechActiveTurnId = null;
        S.assistantSpeechPlaybackTurnId = null;
        S.assistantSpeechPlaybackStartAudioTime = 0;
        S.assistantSpeechPlaybackEndAudioTime = 0;
        clearStuckSpeakingFallback();
        logAudioLifecycle('dispatchAssistantSpeechCancel', {
            turnId: normalizedTurnId,
            source: source || 'audio_playback'
        });
        emitAssistantSpeechLifecycleEvent('neko-assistant-speech-cancel', {
            turnId: normalizedTurnId,
            source: source || 'audio_playback'
        });
        // Cancel/interruption also means audio playback has stopped → open
        // the proactive gate (same as a natural end).
        sendVoicePlaybackSignal('voice_play_end', normalizedTurnId);
    }

    function clearAssistantTurnCompletionFallback() {
        if (_assistantTurnCompletionFallbackTimer) {
            clearTimeout(_assistantTurnCompletionFallbackTimer);
            _assistantTurnCompletionFallbackTimer = 0;
        }
        _assistantTurnCompletionFallbackTurnId = null;
    }

    function clearStuckSpeakingFallback() {
        if (_stuckSpeakingFallbackTimer) {
            clearTimeout(_stuckSpeakingFallbackTimer);
            _stuckSpeakingFallbackTimer = 0;
        }
    }

    // 4 个队列 + 3 个 in-flight async flag，覆盖所有"音频还在路上"的状态。
    // - 4 queue 对齐 isAssistantTurnPlaybackDrained（少查 pendingAudioChunkMetaQueue
    //   会在 header 到了 blob 还没到的窗口里误判空）
    // - 3 async flag 对齐 publishSpeechPlaybackState:86-92 的 pendingAudioWork：
    //   processIncomingAudioBlobQueue 会先 shift 出 blob 再 await handleAudioBlob，
    //   shift 之后 incomingAudioBlobQueue.length 是 0 但解码还在跑；decoder reset
    //   同理是个 Promise 在 flight。这些都属于"未真正 idle"，arm watchdog 就是误伤。
    // filter shouldSkip 是因为上游可能 mark 跳过项。
    function _hasPendingAudioWork() {
        var pendingMeta = S.pendingAudioChunkMetaQueue.some(function (item) {
            return item && !item.shouldSkip;
        });
        return (
            S.scheduledSources.length > 0 ||
            S.audioBufferQueue.length > 0 ||
            pendingMeta ||
            S.incomingAudioBlobQueue.length > 0 ||
            !!S.pendingDecoderReset ||
            !!S.decoderResetPromise ||
            !!S.isProcessingIncomingAudioBlob
        );
    }

    // 兜底：所有音频队列空了且 isPlaying / assistantSpeechActiveTurnId 还粘着
    // → STUCK_SPEAKING_FALLBACK_MS 后强制走 cancel 路径收尾。
    // 触发点是 source.onended（最后一段音频刚播完，maybeFinalizeAssistantSpeech
    // 因为没收到 turn-end 而 skip 的那一刻），fire 时再 re-check 一次，
    // 期间如果新音频进来或正常 finalize 走完，会被 clearStuckSpeakingFallback 撤掉。
    function maybeArmStuckSpeakingFallback() {
        if (_stuckSpeakingFallbackTimer) return;
        var flagsSet = !!(S.isPlaying || S.assistantSpeechActiveTurnId);
        if (_hasPendingAudioWork() || !flagsSet) return;

        logAudioLifecycle('stuckSpeakingFallback:armed', {
            isPlaying: S.isPlaying,
            assistantSpeechActiveTurnId: S.assistantSpeechActiveTurnId,
            assistantTurnId: S.assistantTurnId,
            assistantTurnCompletedId: S.assistantTurnCompletedId,
            delayMs: STUCK_SPEAKING_FALLBACK_MS
        });

        _stuckSpeakingFallbackTimer = window.setTimeout(function () {
            _stuckSpeakingFallbackTimer = 0;
            var hasPending = _hasPendingAudioWork();
            var flagsStillSet = !!(S.isPlaying || S.assistantSpeechActiveTurnId);
            if (hasPending || !flagsStillSet) {
                logAudioLifecycle('stuckSpeakingFallback:skip_resolved', {
                    hasPendingAudioWork: hasPending,
                    flagsStillSet: flagsStillSet
                });
                return;
            }
            var snapshot = {
                isPlaying: S.isPlaying,
                assistantSpeechActiveTurnId: S.assistantSpeechActiveTurnId,
                assistantTurnId: S.assistantTurnId,
                assistantTurnCompletedId: S.assistantTurnCompletedId,
                assistantTurnStartedAt: S.assistantTurnStartedAt,
                scheduledSources: S.scheduledSources.length,
                audioBufferQueue: S.audioBufferQueue.length,
                pendingAudioChunkMetaQueue: S.pendingAudioChunkMetaQueue.length,
                incomingAudioBlobQueue: S.incomingAudioBlobQueue.length,
                pendingDecoderReset: !!S.pendingDecoderReset,
                decoderResetPromise: !!S.decoderResetPromise,
                isProcessingIncomingAudioBlob: !!S.isProcessingIncomingAudioBlob
            };
            console.warn('[Audio] sticky speaking flag detected, force-resetting via cancel after ' +
                STUCK_SPEAKING_FALLBACK_MS + 'ms with empty queues', snapshot);
            logAudioLifecycle('stuckSpeakingFallback:fire', snapshot);
            // 走 cancel 通道：dispatchAssistantSpeechCancel 会清 assistantSpeechActiveTurnId
            // 并 dispatch neko-assistant-speech-cancel，下方 handler 会清 isPlaying。
            try { dispatchAssistantSpeechCancel('stuck_speaking_fallback'); } catch (_) { /* noop */ }
            // 双保险：上面任一步没把 isPlaying 抹掉就强清。
            if (S.isPlaying) {
                S.isPlaying = false;
            }
            if (S.assistantSpeechActiveTurnId) {
                S.assistantSpeechActiveTurnId = null;
            }
        }, STUCK_SPEAKING_FALLBACK_MS);
    }

    function clearAssistantTurnCompletion() {
        clearAssistantTurnCompletionFallback();
        S.assistantTurnCompletedId = null;
        S.assistantTurnCompletionSource = null;
        S.assistantSpeechStartedTurnId = null;
        // settled 标记随完成状态一起清：turn-start / speech-cancel / clearAudioQueue
        // 都经由本函数，等于把 settledId 接进完整的 turn 生命周期收尾。
        // maybeFinalizeAssistantSpeech 在调用本函数之后再设 settledId（见那里），
        // 所以"干净收尾"路径的 settledId 不会被这里误清。
        S.assistantTurnSettledId = null;
    }

    function scheduleAssistantTurnCompletionFallback(turnId, source) {
        var normalizedTurnId = normalizeAssistantTurnId(turnId);
        clearAssistantTurnCompletionFallback();
        if (!normalizedTurnId) {
            return;
        }

        _assistantTurnCompletionFallbackTurnId = normalizedTurnId;
        logAudioLifecycle('scheduleAssistantTurnCompletionFallback:scheduled', {
            turnId: normalizedTurnId,
            source: source || null,
            delayMs: ASSISTANT_TURN_COMPLETION_FALLBACK_MS
        });
        _assistantTurnCompletionFallbackTimer = window.setTimeout(function () {
            var fallbackTurnId = _assistantTurnCompletionFallbackTurnId;
            _assistantTurnCompletionFallbackTimer = 0;
            _assistantTurnCompletionFallbackTurnId = null;

            if (!fallbackTurnId || S.assistantTurnCompletedId !== fallbackTurnId) {
                logAudioLifecycle('scheduleAssistantTurnCompletionFallback:skip_completion_mismatch', {
                    turnId: fallbackTurnId || normalizedTurnId
                });
                return;
            }
            if (hasAssistantSpeechActivity(fallbackTurnId)) {
                logAudioLifecycle('scheduleAssistantTurnCompletionFallback:skip_activity_resumed', {
                    turnId: fallbackTurnId
                });
                return;
            }

            logAudioLifecycle('scheduleAssistantTurnCompletionFallback:fire', {
                turnId: fallbackTurnId
            });
            maybeFinalizeAssistantSpeech(fallbackTurnId);
        }, ASSISTANT_TURN_COMPLETION_FALLBACK_MS);
    }

    function resolveAssistantAudioTurnId(turnId, speechId) {
        return normalizeAssistantTurnId(
            turnId ||
            S.assistantTurnId ||
            S.assistantPendingTurnServerId ||
            S.assistantTurnCompletedId ||
            S.assistantSpeechActiveTurnId ||
            speechId
        );
    }

    function isAssistantTurnPlaybackDrained(turnId) {
        pruneStalledPendingAudioMetaQueue(Date.now());
        schedulePendingAudioMetaStallCheck();
        var normalizedTurnId = normalizeAssistantTurnId(turnId);
        if (!normalizedTurnId) {
            return false;
        }

        var hasScheduledSource = S.scheduledSources.some(function (source) {
            return normalizeAssistantTurnId(source && source._nekoAssistantTurnId) === normalizedTurnId;
        });
        if (hasScheduledSource) {
            return false;
        }

        var hasQueuedBuffer = S.audioBufferQueue.some(function (item) {
            return resolveAssistantAudioTurnId(item && item.turnId, item && item.speechId) === normalizedTurnId;
        });
        if (hasQueuedBuffer) {
            return false;
        }

        var hasPendingMeta = S.pendingAudioChunkMetaQueue.some(function (item) {
            return item &&
                !item.shouldSkip &&
                item.epoch === S.incomingAudioEpoch &&
                resolveAssistantAudioTurnId(item.turnId, item.speechId) === normalizedTurnId;
        });
        if (hasPendingMeta) {
            return false;
        }

        return !S.incomingAudioBlobQueue.some(function (item) {
            return item &&
                !item.shouldSkip &&
                item.epoch === S.incomingAudioEpoch &&
                resolveAssistantAudioTurnId(item.turnId, item.speechId) === normalizedTurnId;
        });
    }

    function hasAssistantSpeechActivity(turnId) {
        var normalizedTurnId = normalizeAssistantTurnId(turnId);
        if (!normalizedTurnId) {
            return false;
        }

        if (normalizeAssistantTurnId(S.assistantSpeechActiveTurnId) === normalizedTurnId) {
            return true;
        }

        return !isAssistantTurnPlaybackDrained(normalizedTurnId);
    }

    function stopActiveLipSync() {
        var activeModelType = getActiveAvatarModelType();
        if (activeModelType === 'vrm' && window.vrmManager && window.vrmManager.currentModel && window.vrmManager.animation) {
            if (typeof window.vrmManager.animation.stopLipSync === 'function') {
                window.vrmManager.animation.stopLipSync();
            }
            S.lipSyncActive = false;
        } else if (activeModelType === 'mmd' && window.mmdManager && window.mmdManager.currentModel && window.mmdManager.animationModule) {
            if (typeof window.mmdManager.animationModule.stopLipSync === 'function') {
                window.mmdManager.animationModule.stopLipSync();
                console.log('[Audio] MMD 口型同步已停止');
            }
            S.lipSyncActive = false;
        } else if (activeModelType === 'pngtuber' && window.pngtuberManager) {
            if (typeof window.pngtuberManager.stopLipSync === 'function') {
                window.pngtuberManager.stopLipSync();
            }
            S.lipSyncActive = false;
        } else if (window.LanLan1 && window.LanLan1.live2dModel) {
            stopLipSync(window.LanLan1.live2dModel);
        } else {
            S.lipSyncActive = false;
        }
    }

    function maybeFinalizeAssistantSpeech(turnId) {
        var normalizedTurnId = normalizeAssistantTurnId(
            turnId || S.assistantSpeechActiveTurnId || S.assistantTurnCompletedId
        );
        logAudioLifecycle('maybeFinalizeAssistantSpeech:enter', {
            requestedTurnId: normalizedTurnId
        });
        if (!normalizedTurnId || S.assistantTurnCompletedId !== normalizedTurnId) {
            logAudioLifecycle('maybeFinalizeAssistantSpeech:skip_completion_mismatch', {
                requestedTurnId: normalizedTurnId
            });
            return false;
        }
        if (!isAssistantTurnPlaybackDrained(normalizedTurnId)) {
            logAudioLifecycle('maybeFinalizeAssistantSpeech:skip_not_drained', {
                requestedTurnId: normalizedTurnId
            });
            return false;
        }

        stopActiveLipSync();
        S.isPlaying = false;
        dispatchAssistantSpeechEnd(normalizedTurnId);
        var completionSource = S.assistantTurnCompletionSource;
        clearAssistantTurnCompletion();
        // 这一轮已干净收尾。clearAssistantTurnCompletion 刚把 completedId 清成 null，
        // 但 assistantTurnId 仍指向本轮（要等下条用户消息才清），若不标记 settled，
        // isAssistantTextResponseInFlight 会一直把"已说完的轮"误判成在路上 → 切语音
        // 干等 15s。这里在清空之后再标 settled，记下"turnId 这轮已收尾"。
        S.assistantTurnSettledId = normalizedTurnId;
        logAudioLifecycle('maybeFinalizeAssistantSpeech:completed', {
            requestedTurnId: normalizedTurnId,
            completionSource: completionSource
        });

        if (completionSource !== 'turn_end_agent_callback' && S.isRecording && S.proactiveChatEnabled) {
            if (typeof window.scheduleProactiveChat === 'function') {
                console.log('[ProactiveChat] AI 音频播放完成，重新调度计时器');
                window.scheduleProactiveChat();
            }
        }
        return true;
    }

    let _assistantSpeechLifecycleEventsBound = false;

    function bindAssistantSpeechLifecycleEvents() {
        if (_assistantSpeechLifecycleEventsBound) {
            return;
        }
        _assistantSpeechLifecycleEventsBound = true;

        window.addEventListener('neko-assistant-turn-start', function () {
            clearAssistantTurnCompletion();
            logAudioLifecycle('event:turn-start');
        });

        window.addEventListener('neko-assistant-turn-end', function (event) {
            var turnId = normalizeAssistantTurnId(event.detail && event.detail.turnId);
            var source = event.detail && event.detail.source;
            var speechStartedForTurn = normalizeAssistantTurnId(S.assistantSpeechStartedTurnId) === turnId;
            logAudioLifecycle('event:turn-end', {
                turnId: turnId,
                source: source,
                speechStartedForTurn: speechStartedForTurn
            });
            if (!turnId) {
                return;
            }
            // Some flows only emit the agent callback turn-end before audio drains.
            S.assistantTurnCompletedId = turnId;
            S.assistantTurnCompletionSource = source || null;
            if (!hasAssistantSpeechActivity(turnId)) {
                if (!speechStartedForTurn) {
                    clearAssistantTurnCompletionFallback();
                    logAudioLifecycle('event:turn-end:await_late_speech_start', {
                        turnId: turnId,
                        source: source
                    });
                    return;
                }
                logAudioLifecycle('event:turn-end:defer_finalize_until_speech', {
                    turnId: turnId,
                    source: source
                });
                scheduleAssistantTurnCompletionFallback(turnId, source);
                return;
            }
            maybeFinalizeAssistantSpeech(turnId);
        });

        window.addEventListener('neko-assistant-speech-cancel', function () {
            clearAssistantTurnCompletion();
            clearStuckSpeakingFallback();
            // [BUGFIX] 切换猫娘后语音模式 mic 永远 skip=focus 的根因：
            // 原来只清 turn-tracking 标志，S.isPlaying 留在 true。
            // 切换瞬间 emitAssistantSpeechCancel('character_switch') 被调，
            // 但 S.isPlaying 没人重置，mic workletNode.onmessage 里 focus
            // 模式把每一帧音频都 skip 掉，sent 永远是 0。
            // 这里强制重置 isPlaying，并把 turn-bound 的音频状态一起收尾，
            // 和正常 maybeFinalizeAssistantSpeech 的清理对齐。
            if (S.isPlaying) {
                S.isPlaying = false;
            }
            S.audioStartTime = 0;
            publishSpeechPlaybackState('speech_cancel', {
                active: false,
                speechId: S.currentPlayingSpeechId || null,
                turnId: S.assistantSpeechActiveTurnId || S.assistantTurnId || null,
                scheduledEndAudioTime: S.audioPlayerContext ? S.audioPlayerContext.currentTime : 0,
                remainingSeconds: 0
            });
            try { stopActiveLipSync(); } catch (_e) { /* ignore */ }
        });
    }

    // ======================== Lip-sync smoothing (module-local) ========================
    let _lastMouthOpen = 0;
    let _lipSyncSkipCounter = 0;
    const LIP_SYNC_EVERY_N_FRAMES = 2;

    // ======================== Audio queue management ========================

    /**
     * clearAudioQueue — stop all scheduled sources, empty the buffer queue
     * and reset the OGG Opus decoder.
     */
    async function clearAudioQueue() {
        dispatchAssistantSpeechCancel('clear_audio_queue');
        clearAssistantTurnCompletion();
        clearPendingAudioMetaStallTimer();
        S.scheduledSources.forEach(function (source) {
            try { source.stop(); } catch (_) { /* noop */ }
        });
        stopActiveLipSync();
        S.scheduledSources = [];
        S.audioBufferQueue = [];
        S.pendingAudioChunkMetaQueue = [];
        S.incomingAudioBlobQueue = [];
        S.isPlaying = false;
        S.audioStartTime = 0;
        S.nextChunkTime = 0;
        publishSpeechPlaybackState('clear_audio_queue', {
            active: false,
            speechId: null,
            turnId: null,
            scheduledEndAudioTime: S.audioPlayerContext ? S.audioPlayerContext.currentTime : 0,
            remainingSeconds: 0
        });

        await resetOggOpusDecoder();
    }

    /**
     * clearAudioQueueWithoutDecoderReset — same as clearAudioQueue but does NOT
     * reset the decoder.  Used for precise interrupt control so that header info
     * is preserved until the next speech_id arrives.
     */
    function clearAudioQueueWithoutDecoderReset() {
        dispatchAssistantSpeechCancel('clear_audio_queue_without_decoder_reset');
        clearAssistantTurnCompletion();
        clearPendingAudioMetaStallTimer();
        S.scheduledSources.forEach(function (source) {
            try { source.stop(); } catch (_) { /* noop */ }
        });
        stopActiveLipSync();
        S.scheduledSources = [];
        S.audioBufferQueue = [];
        S.pendingAudioChunkMetaQueue = [];
        S.incomingAudioBlobQueue = [];
        S.isPlaying = false;
        S.audioStartTime = 0;
        S.nextChunkTime = 0;
        publishSpeechPlaybackState('clear_audio_queue_without_decoder_reset', {
            active: false,
            speechId: null,
            turnId: null,
            scheduledEndAudioTime: S.audioPlayerContext ? S.audioPlayerContext.currentTime : 0,
            remainingSeconds: 0
        });
        // Note: decoder is NOT reset here.
    }

    // ======================== Global analyser initialisation ========================

    function initializeGlobalAnalyser() {
        if (S.audioPlayerContext) {
            if (S.audioPlayerContext.state === 'suspended') {
                S.audioPlayerContext.resume().catch(function (err) {
                    console.warn('[Audio] resume() failed:', err);
                });
            }
            if (!S.globalAnalyser) {
                try {
                    S.globalAnalyser = S.audioPlayerContext.createAnalyser();
                    S.globalAnalyser.fftSize = 2048;
                    // Audio graph:
                    //   source -> analyser -> spatialPanner -> spatialDistanceGain -> speakerGain -> destination
                    // spatialPanner / spatialDistanceGain 始终存在；当空间音频关闭时
                    // pan=0 / gain=1 形成 transparent passthrough，避免动态切换图结构。
                    S.spatialPannerNode = S.audioPlayerContext.createStereoPanner();
                    S.spatialPannerNode.pan.value = 0;
                    S.spatialDistanceGainNode = S.audioPlayerContext.createGain();
                    S.spatialDistanceGainNode.gain.value = 1;

                    S.speakerGainNode = S.audioPlayerContext.createGain();
                    var vol = (typeof window.getSpeakerVolume === 'function')
                        ? window.getSpeakerVolume() : 100;
                    S.speakerGainNode.gain.value = vol / 100;

                    S.globalAnalyser.connect(S.spatialPannerNode);
                    S.spatialPannerNode.connect(S.spatialDistanceGainNode);
                    S.spatialDistanceGainNode.connect(S.speakerGainNode);
                    S.speakerGainNode.connect(S.audioPlayerContext.destination);
                    console.log('[Audio] 全局分析器、空间音频与扬声器增益节点已创建并连接');

                    if (window.appSpatialAudio && typeof window.appSpatialAudio.attach === 'function') {
                        window.appSpatialAudio.attach();
                    }
                } catch (e) {
                    console.error('[Audio] 创建分析器失败:', e);
                    // 任意节点构造失败时，把整条链路上的 ref 全部 null 掉，
                    // 让 scheduleAudioChunks 的 hasAnalyser=!!globalAnalyser 路径
                    // 退化为 source.connect(destination) 直连，避免把音频灌进
                    // 一个未连接到 destination 的 dangling analyser 而静音。
                    S.globalAnalyser = null;
                    S.spatialPannerNode = null;
                    S.spatialDistanceGainNode = null;
                    S.speakerGainNode = null;
                }
            }
            // Always sync global references (even when no new nodes were created)
            window.syncAudioGlobals();

            if (window.DEBUG_AUDIO) {
                console.debug('[Audio] globalAnalyser 状态:', !!S.globalAnalyser);
            }
        } else {
            if (window.DEBUG_AUDIO) {
                console.warn('[Audio] audioPlayerContext 未初始化，无法创建分析器');
            }
        }
    }

    // ======================== Lip-sync ========================

    function startLipSync(model, analyser) {
        console.log('[LipSync] 开始口型同步', { hasModel: !!model, hasAnalyser: !!analyser });
        if (S.animationFrameId) {
            cancelAnimationFrame(S.animationFrameId);
        }

        _lastMouthOpen = 0;
        _lipSyncSkipCounter = 0;

        var dataArray = new Uint8Array(analyser.fftSize);

        function animate() {
            if (!analyser) return;
            S.animationFrameId = requestAnimationFrame(animate);

            if (++_lipSyncSkipCounter < LIP_SYNC_EVERY_N_FRAMES) return;
            _lipSyncSkipCounter = 0;

            analyser.getByteTimeDomainData(dataArray);

            var sum = 0;
            for (var i = 0; i < dataArray.length; i++) {
                var val = (dataArray[i] - 128) / 128;
                sum += val * val;
            }
            var rms = Math.sqrt(sum / dataArray.length);

            var mouthOpen = Math.min(1, rms * 10);
            mouthOpen = _lastMouthOpen * 0.5 + mouthOpen * 0.5;
            _lastMouthOpen = mouthOpen;

            if (window.LanLan1 && typeof window.LanLan1.setMouth === 'function') {
                window.LanLan1.setMouth(mouthOpen);
            }
        }

        animate();
    }

    function stopLipSync(model) {
        console.log('[LipSync] 停止口型同步');
        if (S.animationFrameId) {
            cancelAnimationFrame(S.animationFrameId);
            S.animationFrameId = null;
        }
        if (window.LanLan1 && typeof window.LanLan1.setMouth === 'function') {
            window.LanLan1.setMouth(0);
        } else if (model && model.internalModel && model.internalModel.coreModel) {
            // Fallback
            try { model.internalModel.coreModel.setParameterValueById("ParamMouthOpenY", 0); } catch (_) { /* noop */ }
        }
        S.lipSyncActive = false;
    }

    // ======================== Audio chunk scheduling ========================

    function scheduleAudioChunks() {
        if (S.scheduleAudioChunksRunning) return;
        S.scheduleAudioChunksRunning = true;

        try {
            var scheduleAheadTime = 5;

            initializeGlobalAnalyser();
            // If init still failed, fall back to connecting sources directly to destination
            var hasAnalyser = !!S.globalAnalyser;

            // Pre-schedule all chunks within the lookahead window.
            // 只在有 chunk 可 schedule 时才 clamp nextChunkTime，
            // 避免空转循环中把 nextChunkTime 无谓前推——对于 qwen-tts 等
            // server_commit 模式 provider，服务端在韵律边界有天然的处理间隙
            // （200-300ms），空转 clamp 会把这个间隙转化为用户可感知的停顿。
            while (S.nextChunkTime < S.audioPlayerContext.currentTime + scheduleAheadTime) {
                if (S.audioBufferQueue.length > 0) {
                    // Clamp: 防止 stale nextChunkTime 导致多个 chunk 被 schedule 到过去
                    // （Web Audio 会同时播放过去时刻的 source），只在真正要 schedule 时才修正。
                    if (S.nextChunkTime < S.audioPlayerContext.currentTime) {
                        S.nextChunkTime = S.audioPlayerContext.currentTime;
                    }
                    var item = S.audioBufferQueue.shift();
                    var nextBuffer = item.buffer;
                    if (window.DEBUG_AUDIO) {
                        console.log('ctx', S.audioPlayerContext.sampleRate,
                            'buf', nextBuffer.sampleRate);
                    }

                    var source = S.audioPlayerContext.createBufferSource();
                    source.buffer = nextBuffer;
                    source._nekoAssistantTurnId = resolveAssistantAudioTurnId(item.turnId, item.speechId);
                    if (hasAnalyser) {
                        source.connect(S.globalAnalyser);
                    } else {
                        source.connect(S.audioPlayerContext.destination);
                    }

                    if (source._nekoAssistantTurnId) {
                        dispatchAssistantSpeechStart(source._nekoAssistantTurnId);
                    }

                    if (hasAnalyser && !S.lipSyncActive) {
                        if (window.DEBUG_AUDIO) {
                            console.log('[Audio] 尝试启动口型同步:', {
                                hasLanLan1: !!window.LanLan1,
                                hasLive2dModel: !!(window.LanLan1 && window.LanLan1.live2dModel),
                                hasVrmManager: !!window.vrmManager,
                                hasVrmModel: !!(window.vrmManager && window.vrmManager.currentModel),
                                hasMmdManager: !!window.mmdManager,
                                hasMmdCurrentModel: !!(window.mmdManager && window.mmdManager.currentModel),
                                hasMmdAnimationModule: !!(window.mmdManager && window.mmdManager.animationModule),
                                hasAnalyser: hasAnalyser
                            });
                        }
                        var activeModelType = getActiveAvatarModelType();
                        if (activeModelType === 'vrm' && window.vrmManager && window.vrmManager.currentModel && window.vrmManager.animation) {
                            if (typeof window.vrmManager.animation.startLipSync === 'function') {
                                window.vrmManager.animation.startLipSync(S.globalAnalyser);
                                S.lipSyncActive = true;
                            }
                        } else if (activeModelType === 'mmd' && window.mmdManager && window.mmdManager.currentModel && window.mmdManager.animationModule) {
                            if (typeof window.mmdManager.animationModule.startLipSync === 'function') {
                                window.mmdManager.animationModule.startLipSync(S.globalAnalyser);
                                S.lipSyncActive = true;
                                console.log('[Audio] MMD 口型同步已启动');
                            }
                        } else if (activeModelType === 'pngtuber' && window.pngtuberManager) {
                            if (typeof window.pngtuberManager.startLipSync === 'function') {
                                window.pngtuberManager.startLipSync(S.globalAnalyser);
                                S.lipSyncActive = true;
                                console.log('[Audio] PNGTuber lip sync started');
                            }
                        } else if (window.LanLan1 && window.LanLan1.live2dModel) {
                            startLipSync(window.LanLan1.live2dModel, S.globalAnalyser);
                            S.lipSyncActive = true;
                        } else {
                            if (window.DEBUG_AUDIO) {
                                console.warn('[Audio] 无法启动口型同步：没有可用的模型');
                            }
                        }
                    }

                    var scheduledStartTime = S.nextChunkTime;
                    var scheduledEndTime = scheduledStartTime + nextBuffer.duration;
                    if (source._nekoAssistantTurnId) {
                        if (S.assistantSpeechPlaybackTurnId !== source._nekoAssistantTurnId ||
                            !Number.isFinite(S.assistantSpeechPlaybackStartAudioTime) ||
                            S.assistantSpeechPlaybackStartAudioTime <= 0 ||
                            scheduledStartTime < S.assistantSpeechPlaybackStartAudioTime) {
                            S.assistantSpeechPlaybackTurnId = source._nekoAssistantTurnId;
                            S.assistantSpeechPlaybackStartAudioTime = scheduledStartTime;
                        }
                        S.assistantSpeechPlaybackEndAudioTime = Math.max(
                            Number.isFinite(S.assistantSpeechPlaybackEndAudioTime) ? S.assistantSpeechPlaybackEndAudioTime : 0,
                            scheduledEndTime
                        );
                    }

                    // Precise time scheduling
                    source.start(scheduledStartTime);
                    source._nekoSpeechId = normalizeAssistantTurnId(item.speechId);
                    source._nekoScheduledEndAudioTime = scheduledEndTime;

                    // On-ended callback: handle lip sync stop & cleanup
                    source.onended = (function (src) {
                        return function () {
                            var index = S.scheduledSources.indexOf(src);
                            if (index !== -1) {
                                S.scheduledSources.splice(index, 1);
                            }
                            publishSpeechPlaybackState('source_ended', {
                                active: S.scheduledSources.length > 0 || S.audioBufferQueue.length > 0 || S.incomingAudioBlobQueue.length > 0,
                                speechId: S.currentPlayingSpeechId || src._nekoSpeechId || null,
                                turnId: src._nekoAssistantTurnId || null
                            });
                            var finalized = maybeFinalizeAssistantSpeech(src._nekoAssistantTurnId);
                            // 兜底：finalize 没走通（多半是 turn-end 没到），队列已空但 flag 还粘着 → 30s 后强制收尾。
                            if (!finalized) {
                                maybeArmStuckSpeakingFallback();
                            }
                        };
                    })(source);

                    // Update next chunk time
                    S.nextChunkTime = scheduledEndTime;

                    S.scheduledSources.push(source);
                    publishSpeechPlaybackState('chunk_scheduled', {
                        active: true,
                        speechId: normalizeAssistantTurnId(item.speechId) || S.currentPlayingSpeechId || null,
                        turnId: source._nekoAssistantTurnId || null,
                        playbackTurnId: S.assistantSpeechPlaybackTurnId || null,
                        playbackStartAudioTime: S.assistantSpeechPlaybackStartAudioTime || 0,
                        playbackEndAudioTime: S.assistantSpeechPlaybackEndAudioTime || scheduledEndTime,
                        scheduledEndAudioTime: S.nextChunkTime
                    });
                } else {
                    break;
                }
            }

            // Continue the scheduling loop
            setTimeout(scheduleAudioChunks, 25);

        } finally {
            S.scheduleAudioChunksRunning = false;
        }
    }

    // ======================== Audio blob handling ========================

    async function handleAudioBlob(blob, expectedEpoch, speechId, turnId) {
        if (expectedEpoch === undefined) expectedEpoch = S.incomingAudioEpoch;

        var arrayBuffer = await blob.arrayBuffer();
        if (expectedEpoch !== S.incomingAudioEpoch) {
            return;
        }
        if (!arrayBuffer || arrayBuffer.byteLength === 0) {
            console.warn('收到空的音频数据，跳过处理');
            return;
        }

        if (!S.audioPlayerContext) {
            S.audioPlayerContext = new (window.AudioContext || window.webkitAudioContext)();
            window.syncAudioGlobals();
        }

        if (S.audioPlayerContext.state === 'suspended') {
            await S.audioPlayerContext.resume();
            if (expectedEpoch !== S.incomingAudioEpoch) {
                return;
            }
        }

        // Detect OGG format (magic number "OggS" = 0x4F 0x67 0x67 0x53)
        var header = new Uint8Array(arrayBuffer, 0, 4);
        var isOgg = header[0] === 0x4F && header[1] === 0x67 && header[2] === 0x67 && header[3] === 0x53;

        var float32Data;
        var sampleRate = 48000;

        if (isOgg) {
            // OGG OPUS: decode with WASM streaming decoder
            try {
                var result = await decodeOggOpusChunk(new Uint8Array(arrayBuffer));
                if (expectedEpoch !== S.incomingAudioEpoch) {
                    return;
                }
                if (!result) {
                    // Not enough data yet
                    return;
                }
                float32Data = result.float32Data;
                sampleRate = result.sampleRate;
            } catch (e) {
                console.error('OGG OPUS 解码失败:', e);
                return;
            }
        } else {
            // PCM Int16: direct conversion
            var int16Array = new Int16Array(arrayBuffer);
            float32Data = new Float32Array(int16Array.length);
            for (var i = 0; i < int16Array.length; i++) {
                float32Data[i] = int16Array[i] / 32768.0;
            }
        }

        if (!float32Data || float32Data.length === 0) {
            return;
        }
        if (expectedEpoch !== S.incomingAudioEpoch) {
            return;
        }

        var audioBuffer = S.audioPlayerContext.createBuffer(1, float32Data.length, sampleRate);
        audioBuffer.copyToChannel(float32Data, 0);

        var bufferObj = {
            seq: S.seqCounter++,
            buffer: audioBuffer,
            turnId: resolveAssistantAudioTurnId(turnId, speechId),
            speechId: normalizeAssistantTurnId(speechId)
        };
        S.audioBufferQueue.push(bufferObj);

        var j = S.audioBufferQueue.length - 1;
        while (j > 0 && S.audioBufferQueue[j].seq < S.audioBufferQueue[j - 1].seq) {
            var tmp = S.audioBufferQueue[j];
            S.audioBufferQueue[j] = S.audioBufferQueue[j - 1];
            S.audioBufferQueue[j - 1] = tmp;
            j--;
        }

        if (!S.isPlaying) {
            var gap = (S.seqCounter <= 1) ? 0.03 : 0;
            S.nextChunkTime = Math.max(
                S.audioPlayerContext.currentTime + gap,
                S.nextChunkTime
            );
            S.isPlaying = true;
            scheduleAudioChunks();
        }
        // When isPlaying is already true the scheduler loop is already running via
        // its own setTimeout; no need to spawn an extra call.
    }

    // ======================== Incoming audio blob queue ========================

    function enqueueIncomingAudioBlob(blob) {
        pruneStalledPendingAudioMetaQueue(Date.now());
        var meta = null;
        while (S.pendingAudioChunkMetaQueue.length > 0) {
            meta = S.pendingAudioChunkMetaQueue.shift();
            if (!meta) {
                continue;
            }
            if (meta.shouldSkip) {
                logAudioLifecycle('enqueueIncomingAudioBlob:discard_skip_meta', {
                    turnId: meta.turnId || null,
                    speechId: meta.speechId || null
                });
                meta = null;
                continue;
            }
            break;
        }
        schedulePendingAudioMetaStallCheck();
        if (!meta) {
            logAudioLifecycle('enqueueIncomingAudioBlob:missing_meta');
            if (window.DEBUG_AUDIO) {
                console.warn('[Audio] 收到无匹配 header 的音频 blob，已丢弃');
            }
            return;
        }
        if (!meta.speechId) {
            logAudioLifecycle('enqueueIncomingAudioBlob:missing_speech_id', {
                turnId: meta.turnId || null
            });
            if (window.DEBUG_AUDIO) {
                console.warn('[Audio] 收到 speechId 为空的音频 blob，已丢弃');
            }
            return;
        }
        logAudioLifecycle('enqueueIncomingAudioBlob', {
            turnId: meta.turnId || null,
            speechId: meta.speechId,
            shouldSkip: !!meta.shouldSkip
        });
        S.incomingAudioBlobQueue.push({
            blob: blob,
            shouldSkip: !!meta.shouldSkip,
            speechId: meta.speechId,
            turnId: resolveAssistantAudioTurnId(meta.turnId, meta.speechId),
            epoch: meta.epoch
        });
        if (!S.isProcessingIncomingAudioBlob) {
            void processIncomingAudioBlobQueue();
        }
    }

    async function processIncomingAudioBlobQueue() {
        if (S.isProcessingIncomingAudioBlob) return;
        S.isProcessingIncomingAudioBlob = true;

        try {
            while (S.incomingAudioBlobQueue.length > 0) {
                var item = S.incomingAudioBlobQueue.shift();
                if (!item) continue;
                if (item.epoch !== S.incomingAudioEpoch) {
                    continue;
                }

                if (item.shouldSkip) {
                    logAudioLifecycle('processIncomingAudioBlobQueue:skip_item', {
                        turnId: item.turnId || null,
                        speechId: item.speechId
                    });
                    if (window.DEBUG_AUDIO) {
                        console.log('[Audio] 跳过被打断的音频 blob', item.speechId);
                    }
                    continue;
                }

                if (S.decoderResetPromise) {
                    var resetTask = S.decoderResetPromise;
                    try {
                        await resetTask;
                    } catch (e) {
                        console.warn('等待 OGG OPUS 解码器重置失败:', e);
                    } finally {
                        // Only clear current task; avoid overwriting a newly-set promise
                        if (S.decoderResetPromise === resetTask) {
                            S.decoderResetPromise = null;
                        }
                    }
                }
                if (item.epoch !== S.incomingAudioEpoch) {
                    continue;
                }

                await handleAudioBlob(item.blob, item.epoch, item.speechId, item.turnId);
                logAudioLifecycle('processIncomingAudioBlobQueue:handled', {
                    turnId: item.turnId || null,
                    speechId: item.speechId
                });
            }
        } finally {
            S.isProcessingIncomingAudioBlob = false;
            maybeFinalizeAssistantSpeech();
            schedulePendingAudioMetaStallCheck();
            if (S.incomingAudioBlobQueue.length > 0) {
                void processIncomingAudioBlobQueue();
            }
        }
    }

    // ======================== Speaker volume control ========================

    function saveSpeakerVolumeSetting() {
        try {
            localStorage.setItem('neko_speaker_volume', String(S.speakerVolume));
            console.log('扬声器音量设置已保存: ' + S.speakerVolume + '%');
        } catch (err) {
            console.error('保存扬声器音量设置失败:', err);
        }
    }

    function loadSpeakerVolumeSetting() {
        try {
            var saved = localStorage.getItem('neko_speaker_volume');
            if (saved !== null) {
                var vol = parseInt(saved, 10);
                if (!isNaN(vol) && vol >= 0 && vol <= C.MAX_SPEAKER_VOLUME) {
                    S.speakerVolume = vol;
                    console.log('已加载扬声器音量设置: ' + S.speakerVolume + '%');
                } else {
                    console.warn('无效的扬声器音量值 ' + saved + '，使用默认值 ' + C.DEFAULT_SPEAKER_VOLUME + '%');
                    S.speakerVolume = C.DEFAULT_SPEAKER_VOLUME;
                }
            } else {
                console.log('未找到扬声器音量设置，使用默认值 ' + C.DEFAULT_SPEAKER_VOLUME + '%');
                S.speakerVolume = C.DEFAULT_SPEAKER_VOLUME;
            }

            // Apply immediately to audio pipeline if already initialised
            if (S.speakerGainNode) {
                S.speakerGainNode.gain.setTargetAtTime(S.speakerVolume / 100, S.speakerGainNode.context.currentTime, 0.05);
            }
        } catch (err) {
            console.error('加载扬声器音量设置失败:', err);
            S.speakerVolume = C.DEFAULT_SPEAKER_VOLUME;
        }
    }

    // ======================== Window-level backward-compat exports ========================

    window.setSpeakerVolume = function (vol) {
        if (vol >= 0 && vol <= C.MAX_SPEAKER_VOLUME) {
            S.speakerVolume = vol;
            if (S.speakerGainNode) {
                S.speakerGainNode.gain.setTargetAtTime(vol / 100, S.speakerGainNode.context.currentTime, 0.05);
            }
            saveSpeakerVolumeSetting();
            // Update UI slider if it exists — slider 走非线性轨道(0..1000)，需反向映射；
            // 色值与 app-audio-capture.js 的 applySpeakerVolumeVisual 保持一致
            var slider = document.getElementById('speaker-volume-slider');
            var valueDisplay = document.getElementById('speaker-volume-value');
            var color = vol > C.DEFAULT_SPEAKER_VOLUME ? '#ff9f43' : '#4f8cff';
            if (slider) {
                slider.value = String(Math.round(window.appUtils.valueToKneeTrack(
                    vol, C.DEFAULT_SPEAKER_VOLUME, C.MAX_SPEAKER_VOLUME, C.SPEAKER_VOLUME_KNEE_RATIO
                ) * 1000));
                slider.style.accentColor = color;
            }
            if (valueDisplay) {
                valueDisplay.textContent = vol + '%';
                valueDisplay.style.color = color;
            }
            console.log('扬声器音量已设置: ' + vol + '%');
        }
    };

    window.getSpeakerVolume = function () {
        return S.speakerVolume;
    };

    // ======================== Module exports ========================

    mod.clearAudioQueue = clearAudioQueue;
    mod.clearAudioQueueWithoutDecoderReset = clearAudioQueueWithoutDecoderReset;
    mod.initializeGlobalAnalyser = initializeGlobalAnalyser;
    mod.startLipSync = startLipSync;
    mod.stopLipSync = stopLipSync;
    mod.scheduleAudioChunks = scheduleAudioChunks;
    mod.handleAudioBlob = handleAudioBlob;
    mod.enqueueIncomingAudioBlob = enqueueIncomingAudioBlob;
    mod.processIncomingAudioBlobQueue = processIncomingAudioBlobQueue;
    mod.schedulePendingAudioMetaStallCheck = schedulePendingAudioMetaStallCheck;
    mod.saveSpeakerVolumeSetting = saveSpeakerVolumeSetting;
    mod.loadSpeakerVolumeSetting = loadSpeakerVolumeSetting;

    bindAssistantSpeechLifecycleEvents();

    // Backward-compatible window globals so existing callers keep working
    window.clearAudioQueue = clearAudioQueue;
    window.clearAudioQueueWithoutDecoderReset = clearAudioQueueWithoutDecoderReset;
    window.initializeGlobalAnalyser = initializeGlobalAnalyser;
    window.startLipSync = startLipSync;
    window.stopLipSync = stopLipSync;
    window.scheduleAudioChunks = scheduleAudioChunks;
    window.handleAudioBlob = handleAudioBlob;
    window.enqueueIncomingAudioBlob = enqueueIncomingAudioBlob;
    window.processIncomingAudioBlobQueue = processIncomingAudioBlobQueue;
    window.schedulePendingAudioMetaStallCheck = schedulePendingAudioMetaStallCheck;
    window.saveSpeakerVolumeSetting = saveSpeakerVolumeSetting;
    window.loadSpeakerVolumeSetting = loadSpeakerVolumeSetting;

    window.appAudioPlayback = mod;
})();
