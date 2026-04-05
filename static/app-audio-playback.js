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
    let _assistantTurnCompletionFallbackTimer = 0;
    let _assistantTurnCompletionFallbackTurnId = null;

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

    function dispatchAssistantSpeechStart(turnId) {
        var normalizedTurnId = normalizeAssistantTurnId(turnId);
        if (!normalizedTurnId || S.assistantSpeechActiveTurnId === normalizedTurnId) {
            return;
        }
        S.assistantSpeechActiveTurnId = normalizedTurnId;
        clearAssistantTurnCompletionFallback();
        logAudioLifecycle('dispatchAssistantSpeechStart', {
            turnId: normalizedTurnId
        });
        emitAssistantSpeechLifecycleEvent('neko-assistant-speech-start', {
            turnId: normalizedTurnId,
            source: 'audio_playback'
        });
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
        logAudioLifecycle('dispatchAssistantSpeechEnd', {
            turnId: normalizedTurnId
        });
        emitAssistantSpeechLifecycleEvent('neko-assistant-speech-end', {
            turnId: normalizedTurnId,
            source: 'audio_playback'
        });
    }

    function resolveAssistantSpeechCancelTurnId() {
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
        logAudioLifecycle('dispatchAssistantSpeechCancel', {
            turnId: normalizedTurnId,
            source: source || 'audio_playback'
        });
        emitAssistantSpeechLifecycleEvent('neko-assistant-speech-cancel', {
            turnId: normalizedTurnId,
            source: source || 'audio_playback'
        });
    }

    function clearAssistantTurnCompletionFallback() {
        if (_assistantTurnCompletionFallbackTimer) {
            clearTimeout(_assistantTurnCompletionFallbackTimer);
            _assistantTurnCompletionFallbackTimer = 0;
        }
        _assistantTurnCompletionFallbackTurnId = null;
    }

    function clearAssistantTurnCompletion() {
        clearAssistantTurnCompletionFallback();
        S.assistantTurnCompletedId = null;
        S.assistantTurnCompletionSource = null;
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
        if (window.LanLan1 && window.LanLan1.live2dModel) {
            stopLipSync(window.LanLan1.live2dModel);
        } else if (window.vrmManager && window.vrmManager.currentModel && window.vrmManager.animation) {
            if (typeof window.vrmManager.animation.stopLipSync === 'function') {
                window.vrmManager.animation.stopLipSync();
            }
            S.lipSyncActive = false;
        } else if (window.mmdManager && window.mmdManager.currentModel && window.mmdManager.animationModule) {
            if (typeof window.mmdManager.animationModule.stopLipSync === 'function') {
                window.mmdManager.animationModule.stopLipSync();
                console.log('[Audio] MMD 口型同步已停止');
            }
            S.lipSyncActive = false;
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
            logAudioLifecycle('event:turn-end', {
                turnId: turnId,
                source: source
            });
            if (!turnId) {
                return;
            }
            // Some flows only emit the agent callback turn-end before audio drains.
            S.assistantTurnCompletedId = turnId;
            S.assistantTurnCompletionSource = source || null;
            if (!hasAssistantSpeechActivity(turnId)) {
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
        S.scheduledSources.forEach(function (source) {
            try { source.stop(); } catch (_) { /* noop */ }
        });
        stopActiveLipSync();
        S.scheduledSources = [];
        S.audioBufferQueue = [];
        S.isPlaying = false;
        S.audioStartTime = 0;
        S.nextChunkTime = 0;

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
        S.scheduledSources.forEach(function (source) {
            try { source.stop(); } catch (_) { /* noop */ }
        });
        stopActiveLipSync();
        S.scheduledSources = [];
        S.audioBufferQueue = [];
        S.isPlaying = false;
        S.audioStartTime = 0;
        S.nextChunkTime = 0;
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
                    // Insert speaker gain node: source -> analyser -> gainNode -> destination
                    S.speakerGainNode = S.audioPlayerContext.createGain();
                    var vol = (typeof window.getSpeakerVolume === 'function')
                        ? window.getSpeakerVolume() : 100;
                    S.speakerGainNode.gain.value = vol / 100;
                    S.globalAnalyser.connect(S.speakerGainNode);
                    S.speakerGainNode.connect(S.audioPlayerContext.destination);
                    console.log('[Audio] 全局分析器和扬声器增益节点已创建并连接');
                } catch (e) {
                    console.error('[Audio] 创建分析器失败:', e);
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

            // Clamp nextChunkTime to now so stale values never cause past-scheduled
            // (and therefore simultaneously playing) audio chunks.
            var now = S.audioPlayerContext.currentTime;
            if (S.nextChunkTime < now) {
                S.nextChunkTime = now;
            }

            // Pre-schedule all chunks within the lookahead window
            while (S.nextChunkTime < S.audioPlayerContext.currentTime + scheduleAheadTime) {
                if (S.audioBufferQueue.length > 0) {
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
                        if (window.LanLan1 && window.LanLan1.live2dModel) {
                            startLipSync(window.LanLan1.live2dModel, S.globalAnalyser);
                            S.lipSyncActive = true;
                        } else if (window.vrmManager && window.vrmManager.currentModel && window.vrmManager.animation) {
                            if (typeof window.vrmManager.animation.startLipSync === 'function') {
                                window.vrmManager.animation.startLipSync(S.globalAnalyser);
                                S.lipSyncActive = true;
                            }
                        } else if (window.mmdManager && window.mmdManager.currentModel && window.mmdManager.animationModule) {
                            if (typeof window.mmdManager.animationModule.startLipSync === 'function') {
                                window.mmdManager.animationModule.startLipSync(S.globalAnalyser);
                                S.lipSyncActive = true;
                                console.log('[Audio] MMD 口型同步已启动');
                            }
                        } else {
                            if (window.DEBUG_AUDIO) {
                                console.warn('[Audio] 无法启动口型同步：没有可用的模型');
                            }
                        }
                    }

                    // Precise time scheduling
                    source.start(S.nextChunkTime);

                    // On-ended callback: handle lip sync stop & cleanup
                    source.onended = (function (src) {
                        return function () {
                            var index = S.scheduledSources.indexOf(src);
                            if (index !== -1) {
                                S.scheduledSources.splice(index, 1);
                            }
                            maybeFinalizeAssistantSpeech(src._nekoAssistantTurnId);
                        };
                    })(source);

                    // Update next chunk time
                    S.nextChunkTime += nextBuffer.duration;

                    S.scheduledSources.push(source);
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
        var meta = S.pendingAudioChunkMetaQueue.shift();
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
                if (!isNaN(vol) && vol >= 0 && vol <= 100) {
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
        if (vol >= 0 && vol <= 100) {
            S.speakerVolume = vol;
            if (S.speakerGainNode) {
                S.speakerGainNode.gain.setTargetAtTime(vol / 100, S.speakerGainNode.context.currentTime, 0.05);
            }
            saveSpeakerVolumeSetting();
            // Update UI slider if it exists
            var slider = document.getElementById('speaker-volume-slider');
            var valueDisplay = document.getElementById('speaker-volume-value');
            if (slider) slider.value = String(vol);
            if (valueDisplay) valueDisplay.textContent = vol + '%';
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
    window.saveSpeakerVolumeSetting = saveSpeakerVolumeSetting;
    window.loadSpeakerVolumeSetting = loadSpeakerVolumeSetting;

    window.appAudioPlayback = mod;
})();
