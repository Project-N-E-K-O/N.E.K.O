(function (namespace) {
    'use strict';

    const {
        resolveGuidePreferredLanguage,
        guideAudioSrc,
        shouldGuideAudioDriveMouth,
        wait,
        resumeKnownAudioContexts,
        fetchWithTimeout,
        clamp,
        estimateSpeechDurationMs
    } = namespace;

    class YuiGuideVoiceQueue {
        constructor() {
            // 修改原因：speak() 启动前有短暂等待窗口；用停止代号识别等待期间发生的 cancel/stop，
            // 避免旧轻对抗语音在生气退出后重新起播。
            this.stopGeneration = 0;
            this.currentUtterance = null;
            this.currentFallbackTimer = null;
            this.currentFinish = null;
            this.enabled = !!window.speechSynthesis;
            this.voicesReadyPromise = null;
            this.currentAudio = null;
            this.currentAudioMeta = null;
            this.voiceIdCache = {
                name: '',
                value: '',
                fetchedAt: 0
            };
            this.previewCache = new Map();
            this.currentMouthMotionSession = null;
            this.guideAudioContext = null;
        }

        stop() {
            // 修改原因：stop() 不只停止当前播放，也要让正在 48ms 启动等待中的 speak() 失效。
            this.stopGeneration += 1;
            const finish = this.currentFinish;
            this.stopGuideMouthMotion();

            if (this.currentFallbackTimer) {
                window.clearTimeout(this.currentFallbackTimer);
                this.currentFallbackTimer = null;
            }

            if (this.enabled && window.speechSynthesis) {
                try {
                    window.speechSynthesis.cancel();
                } catch (error) {
                    console.warn('[YuiGuide] 取消语音失败:', error);
                }
            }

            if (this.currentAudio) {
                try {
                    this.currentAudio.pause();
                    this.currentAudio.removeAttribute('src');
                    this.currentAudio.load();
                } catch (error) {
                    console.warn('[YuiGuide] 停止预览音频失败:', error);
                }
                this.currentAudio = null;
            }

            if (this.currentAudioMeta && this.currentAudioMeta.mode === 'buffer') {
                try {
                    if (this.currentAudioMeta.source) {
                        this.currentAudioMeta.source.onended = null;
                        this.currentAudioMeta.source.stop();
                        this.currentAudioMeta.source.disconnect();
                    }
                    if (this.currentAudioMeta.analyserNode) {
                        this.currentAudioMeta.analyserNode.disconnect();
                    }
                    if (this.currentAudioMeta.gainNode) {
                        this.currentAudioMeta.gainNode.disconnect();
                    }
                } catch (error) {
                    console.warn('[YuiGuide] 停止 AudioContext 教程语音失败:', error);
                }
            }
            this.currentAudioMeta = null;

            this.currentUtterance = null;
            this.currentFinish = null;

            if (typeof finish === 'function') {
                try {
                    finish();
                } catch (_) {}
            }
        }

        destroy() {
            this.stop();
            if (this.guideAudioContext && this.guideAudioContext.state !== 'closed') {
                try {
                    const closePromise = this.guideAudioContext.close();
                    if (closePromise && typeof closePromise.catch === 'function') {
                        closePromise.catch(() => {});
                    }
                } catch (_) {}
            }
            this.guideAudioContext = null;
            if (this.previewCache && typeof this.previewCache.clear === 'function') {
                this.previewCache.clear();
            }
        }

        stopGuideMouthMotion(session) {
            const activeSession = session || this.currentMouthMotionSession;
            if (!activeSession) {
                return;
            }

            if (!session || this.currentMouthMotionSession === session) {
                this.currentMouthMotionSession = null;
            }

            try {
                if (activeSession.animationFrameId) {
                    window.cancelAnimationFrame(activeSession.animationFrameId);
                    activeSession.animationFrameId = 0;
                }
                if (activeSession.mediaSourceNode) {
                    try {
                        activeSession.mediaSourceNode.disconnect();
                    } catch (_) {}
                    activeSession.mediaSourceNode = null;
                }
                if (activeSession.analyserNode) {
                    try {
                        activeSession.analyserNode.disconnect();
                    } catch (_) {}
                    activeSession.analyserNode = null;
                }
                if (window.LanLan1 && typeof window.LanLan1.setMouth === 'function') {
                    window.LanLan1.setMouth(0);
                }
            } catch (error) {
                console.warn('[YuiGuide] 停止教程嘴部动作失败:', error);
            }
        }

        createGuideAnalyser(context) {
            if (!context || typeof context.createAnalyser !== 'function') {
                return null;
            }

            const analyser = context.createAnalyser();
            analyser.fftSize = 2048;
            if ('smoothingTimeConstant' in analyser) {
                analyser.smoothingTimeConstant = 0.72;
            }
            return analyser;
        }

        startGuideMouthMotion(voiceKey, options) {
            if (!shouldGuideAudioDriveMouth(voiceKey)) {
                return null;
            }

            if (this.guideInterruptPresentationActive) {
                return null;
            }

            if (typeof window.requestAnimationFrame !== 'function'
                || !window.LanLan1
                || typeof window.LanLan1.setMouth !== 'function') {
                return null;
            }

            this.stopGuideMouthMotion();
            const normalizedOptions = options || {};
            const analyserNode = normalizedOptions.analyserNode || normalizedOptions.analyser || null;
            if (!analyserNode) {
                return null;
            }
            const session = {
                animationFrameId: 0,
                startedAt: performance.now(),
                lastMouthOpen: 0,
                quietFrames: 0,
                analyserNode: analyserNode,
                mediaSourceNode: normalizedOptions.mediaSourceNode || null,
                dataArray: analyserNode && Number.isFinite(analyserNode.fftSize)
                    ? new Uint8Array(analyserNode.fftSize)
                    : null
            };

            try {
                const animate = (now) => {
                    if (this.currentMouthMotionSession !== session) {
                        return;
                    }
                    session.animationFrameId = window.requestAnimationFrame(animate);
                    let target = 0;

                    if (session.analyserNode && session.dataArray) {
                        session.analyserNode.getByteTimeDomainData(session.dataArray);
                        let sum = 0;
                        for (let index = 0; index < session.dataArray.length; index += 1) {
                            const value = (session.dataArray[index] - 128) / 128;
                            sum += value * value;
                        }
                        const rms = Math.sqrt(sum / session.dataArray.length);
                        const noiseFloor = 0.022;
                        const fullOpenRms = 0.15;
                        if (rms <= noiseFloor) {
                            session.quietFrames += 1;
                            target = 0;
                        } else {
                            session.quietFrames = 0;
                            const normalizedRms = clamp((rms - noiseFloor) / (fullOpenRms - noiseFloor), 0, 1);
                            target = Math.pow(normalizedRms, 0.72) * 0.95;
                            if (target < 0.035) {
                                target = 0;
                            }
                        }
                        if (session.quietFrames >= 2) {
                            target = 0;
                        }
                    }

                    const smoothing = target > session.lastMouthOpen
                        ? 0.56
                        : (target === 0 ? 0.62 : 0.42);
                    let mouthOpen = (session.lastMouthOpen * (1 - smoothing)) + (target * smoothing);
                    if (mouthOpen < 0.025) {
                        mouthOpen = 0;
                    }
                    session.lastMouthOpen = mouthOpen;
                    window.LanLan1.setMouth(mouthOpen);
                };

                this.currentMouthMotionSession = session;
                session.animationFrameId = window.requestAnimationFrame(animate);
                return session;
            } catch (error) {
                console.warn('[YuiGuide] 启动教程嘴部动作失败:', error);
                return null;
            }
        }

        createGuideAudioElementMouthMotionNodes(audio) {
            if (!audio) {
                return null;
            }

            const context = this.getAvailableGuideAudioContext();
            if (!context || typeof context.createMediaElementSource !== 'function') {
                return null;
            }

            const analyserNode = this.createGuideAnalyser(context);
            if (!analyserNode) {
                return null;
            }

            try {
                const mediaSourceNode = context.createMediaElementSource(audio);
                mediaSourceNode.connect(analyserNode);
                analyserNode.connect(context.destination);
                return {
                    context: context,
                    analyserNode: analyserNode,
                    mediaSourceNode: mediaSourceNode
                };
            } catch (error) {
                try {
                    analyserNode.disconnect();
                } catch (_) {}
                console.warn('[YuiGuide] 创建教程音频口型分析器失败:', error);
                return null;
            }
        }

        capturePlaybackSnapshot() {
            if (this.currentAudio) {
                const currentTimeMs = Math.max(
                    0,
                    Math.round((Number.isFinite(this.currentAudio.currentTime) ? this.currentAudio.currentTime : 0) * 1000)
                );
                const durationMs = Number.isFinite(this.currentAudio.duration) && this.currentAudio.duration > 0
                    ? Math.round(this.currentAudio.duration * 1000)
                    : 0;

                return {
                    mode: 'audio',
                    voiceKey: this.currentAudioMeta && typeof this.currentAudioMeta.voiceKey === 'string'
                        ? this.currentAudioMeta.voiceKey
                        : '',
                    currentTimeMs: currentTimeMs,
                    durationMs: durationMs
                };
            }

            if (this.currentAudioMeta && this.currentAudioMeta.mode === 'buffer') {
                const context = this.currentAudioMeta.context || null;
                const startedAt = Number.isFinite(this.currentAudioMeta.startedAt)
                    ? this.currentAudioMeta.startedAt
                    : 0;
                const startOffsetMs = Number.isFinite(this.currentAudioMeta.startOffsetMs)
                    ? this.currentAudioMeta.startOffsetMs
                    : 0;
                const durationMs = Number.isFinite(this.currentAudioMeta.durationMs)
                    ? this.currentAudioMeta.durationMs
                    : 0;
                const elapsedMs = context && Number.isFinite(context.currentTime)
                    ? Math.max(0, Math.round((context.currentTime - startedAt) * 1000) + startOffsetMs)
                    : startOffsetMs;

                return {
                    mode: 'buffer',
                    voiceKey: typeof this.currentAudioMeta.voiceKey === 'string'
                        ? this.currentAudioMeta.voiceKey
                        : '',
                    currentTimeMs: durationMs > 0 ? Math.min(durationMs, elapsedMs) : elapsedMs,
                    durationMs: durationMs
                };
            }

            return null;
        }

        getAvailableGuideAudioContext() {
            const candidates = [
                this.guideAudioContext,
                window.lanlanAudioContext,
                window.appState && window.appState.audioPlayerContext,
                window.AM && window.AM.ctx
            ];

            for (let index = 0; index < candidates.length; index += 1) {
                const candidate = candidates[index];
                if (!candidate || typeof candidate.createBufferSource !== 'function') {
                    continue;
                }
                if (candidate.state === 'closed') {
                    continue;
                }
                return candidate;
            }

            const AudioContextConstructor = window.AudioContext || window.webkitAudioContext;
            if (typeof AudioContextConstructor !== 'function') {
                return null;
            }

            try {
                this.guideAudioContext = new AudioContextConstructor();
                return this.guideAudioContext;
            } catch (error) {
                console.warn('[YuiGuide] 创建教程 AudioContext 失败:', error);
                return null;
            }
        }

        decodeGuideAudioBuffer(context, arrayBuffer) {
            if (!context || !arrayBuffer) {
                return Promise.reject(new Error('missing_audio_context_or_buffer'));
            }

            try {
                const maybePromise = context.decodeAudioData(arrayBuffer.slice(0));
                if (maybePromise && typeof maybePromise.then === 'function') {
                    return maybePromise;
                }
            } catch (_) {}

            return new Promise((resolve, reject) => {
                try {
                    context.decodeAudioData(
                        arrayBuffer.slice(0),
                        (audioBuffer) => resolve(audioBuffer),
                        (error) => reject(error || new Error('decode_audio_failed'))
                    );
                } catch (error) {
                    reject(error);
                }
            });
        }

        async ensureVoicesReady() {
            if (!this.enabled || !window.speechSynthesis || typeof window.speechSynthesis.getVoices !== 'function') {
                return [];
            }

            try {
                const existingVoices = window.speechSynthesis.getVoices();
                if (Array.isArray(existingVoices) && existingVoices.length > 0) {
                    return existingVoices;
                }
            } catch (error) {
                console.warn('[YuiGuide] 读取语音列表失败:', error);
            }

            if (this.voicesReadyPromise) {
                return this.voicesReadyPromise;
            }

            this.voicesReadyPromise = new Promise((resolve) => {
                let settled = false;
                const finish = () => {
                    if (settled) {
                        return;
                    }
                    settled = true;
                    window.clearTimeout(timeoutId);
                    window.speechSynthesis.removeEventListener('voiceschanged', handleVoicesChanged);
                    this.voicesReadyPromise = null;
                    try {
                        resolve(window.speechSynthesis.getVoices() || []);
                    } catch (_) {
                        resolve([]);
                    }
                };
                const handleVoicesChanged = () => {
                    try {
                        const voices = window.speechSynthesis.getVoices();
                        if (Array.isArray(voices) && voices.length > 0) {
                            finish();
                        }
                    } catch (_) {}
                };
                const timeoutId = window.setTimeout(finish, 1800);

                window.speechSynthesis.addEventListener('voiceschanged', handleVoicesChanged);
                handleVoicesChanged();
            });

            return this.voicesReadyPromise;
        }

        getCurrentCatgirlName() {
            const candidates = [
                window.lanlan_config && window.lanlan_config.lanlan_name,
                window._currentCatgirl,
                window.currentCatgirl
            ];

            for (let index = 0; index < candidates.length; index += 1) {
                const candidate = typeof candidates[index] === 'string' ? candidates[index].trim() : '';
                if (candidate) {
                    return candidate;
                }
            }

            return '';
        }

        async getCurrentVoiceId() {
            const catgirlName = this.getCurrentCatgirlName();
            if (!catgirlName) {
                return '';
            }

            if (this.voiceIdCache.name === catgirlName && this.voiceIdCache.value) {
                return this.voiceIdCache.value;
            }

            try {
                const response = await fetch('/api/characters', {
                    credentials: 'same-origin'
                });
                if (!response.ok) {
                    return '';
                }

                const data = await response.json();
                const catgirlConfig = data && data['猫娘'] && data['猫娘'][catgirlName]
                    ? data['猫娘'][catgirlName]
                    : null;
                const voiceId = catgirlConfig && typeof catgirlConfig.voice_id === 'string'
                    ? catgirlConfig.voice_id.trim()
                    : '';

                this.voiceIdCache = {
                    name: catgirlName,
                    value: voiceId,
                    fetchedAt: Date.now()
                };
                return voiceId;
            } catch (error) {
                console.warn('[YuiGuide] 获取当前猫娘 voice_id 失败:', error);
                return '';
            }
        }

        async fetchPreviewAudioSrc() {
            const voiceId = await this.getCurrentVoiceId();
            if (!voiceId) {
                return null;
            }
            const previewLanguage = resolveGuidePreferredLanguage() || 'zh-CN';

            const cacheKey = voiceId;
            const cachedPreview = this.previewCache.get(cacheKey);
            if (
                cachedPreview
                && cachedPreview.language === previewLanguage
                && cachedPreview.audioSrc
            ) {
                return {
                    voiceId: voiceId,
                    audioSrc: cachedPreview.audioSrc
                };
            }

            try {
                const response = await fetch(
                    '/api/characters/voice_preview?voice_id='
                    + encodeURIComponent(voiceId)
                    + '&language='
                    + encodeURIComponent(previewLanguage),
                    {
                        credentials: 'same-origin'
                    }
                );
                if (!response.ok) {
                    return null;
                }

                const data = await response.json();
                if (!data || !data.success || !data.audio) {
                    return null;
                }

                const audioSrc = 'data:' + (data.mime_type || 'audio/mpeg') + ';base64,' + data.audio;
                this.previewCache.set(cacheKey, {
                    language: previewLanguage,
                    audioSrc: audioSrc
                });
                return {
                    voiceId: voiceId,
                    audioSrc: audioSrc
                };
            } catch (error) {
                console.warn('[YuiGuide] 获取语音预览失败:', error);
                return null;
            }
        }

        async playPreviewAudio(audioSrc, minimumDurationMs, startAtMs, meta) {
            if (!audioSrc) {
                return false;
            }

            await resumeKnownAudioContexts();
            const minDurationMs = Number.isFinite(minimumDurationMs) ? minimumDurationMs : 0;
            const initialTimeSeconds = Math.max(
                0,
                (Number.isFinite(startAtMs) ? startAtMs : 0) / 1000
            );

            return new Promise((resolve, reject) => {
                let settled = false;
                const audio = new Audio(audioSrc);
                let mouthMotionSession = null;
                let audioMouthMotionNodes = null;
                const finish = (success, error) => {
                    if (settled) {
                        return;
                    }
                    settled = true;
                    this.stopGuideMouthMotion(mouthMotionSession);
                    mouthMotionSession = null;
                    if (audioMouthMotionNodes) {
                        try {
                            if (audioMouthMotionNodes.mediaSourceNode) {
                                audioMouthMotionNodes.mediaSourceNode.disconnect();
                            }
                            if (audioMouthMotionNodes.analyserNode) {
                                audioMouthMotionNodes.analyserNode.disconnect();
                            }
                        } catch (_) {}
                        audioMouthMotionNodes = null;
                    }
                    if (this.currentFallbackTimer === fallbackTimerId) {
                        this.currentFallbackTimer = null;
                    }
                    window.clearTimeout(fallbackTimerId);
                    audio.onended = null;
                    audio.onerror = null;
                    audio.onpause = null;
                    audio.onloadedmetadata = null;
                    if (this.currentAudio === audio) {
                        this.currentAudio = null;
                    }
                    if (this.currentAudioMeta && this.currentAudioMeta.audio === audio) {
                        this.currentAudioMeta = null;
                    }
                    if (this.currentFinish === cancelPlayback) {
                        this.currentFinish = null;
                    }
                    if (success) {
                        resolve(true);
                        return;
                    }
                    reject(error || new Error('preview_audio_failed'));
                };
                const cancelPlayback = () => {
                    finish(true);
                };

                audio.preload = 'auto';
                audio.volume = 1;
                audio.onended = () => finish(true);
                audio.onerror = () => finish(false, new Error('preview_audio_error'));
                this.currentAudio = audio;
                this.currentAudioMeta = Object.assign({
                    audio: audio,
                    voiceKey: '',
                    text: ''
                }, meta || {});
                this.currentFinish = cancelPlayback;

                if (initialTimeSeconds > 0) {
                    const applyStartTime = () => {
                        try {
                            const maxSeek = Number.isFinite(audio.duration) && audio.duration > 0
                                ? Math.max(0, audio.duration - 0.05)
                                : initialTimeSeconds;
                            audio.currentTime = Math.min(initialTimeSeconds, maxSeek);
                        } catch (_) {}
                    };

                    audio.onloadedmetadata = applyStartTime;
                    if (audio.readyState >= 1) {
                        applyStartTime();
                    }
                }

                const fallbackTimerId = window.setTimeout(() => {
                    finish(true);
                }, Math.max(estimateSpeechDurationMs('x'), minDurationMs, 3000));
                this.currentFallbackTimer = fallbackTimerId;
                audioMouthMotionNodes = this.createGuideAudioElementMouthMotionNodes(audio);
                if (audioMouthMotionNodes
                    && audioMouthMotionNodes.context
                    && audioMouthMotionNodes.context.state === 'suspended'
                    && typeof audioMouthMotionNodes.context.resume === 'function') {
                    audioMouthMotionNodes.context.resume().catch(() => {});
                }
                mouthMotionSession = this.startGuideMouthMotion(
                    meta && typeof meta.voiceKey === 'string' ? meta.voiceKey : '',
                    audioMouthMotionNodes
                );

                try {
                    const playPromise = audio.play();
                    if (playPromise && typeof playPromise.then === 'function') {
                        playPromise.catch((error) => finish(false, error));
                    }
                } catch (error) {
                    finish(false, error);
                }
            });
        }

        async playPreviewAudioThroughContext(audioSrc, minimumDurationMs, startAtMs, meta) {
            const context = this.getAvailableGuideAudioContext();
            if (!context) {
                return false;
            }
            // 修改原因：stop()/angry exit 可能发生在音频上下文恢复、拉取或解码期间；
            // 真正启动 buffer source 前必须再次确认同一代次，避免已取消的旧语音重新起播。
            const stopGenerationAtStart = this.stopGeneration;

            await resumeKnownAudioContexts();
            if (context.state === 'suspended' && typeof context.resume === 'function') {
                await context.resume().catch(() => {});
            }
            const response = await fetchWithTimeout(audioSrc, {
                credentials: 'same-origin'
            }, 5500);
            if (!response.ok) {
                throw new Error('guide_audio_fetch_failed');
            }

            const arrayBuffer = await response.arrayBuffer();
            const audioBuffer = await this.decodeGuideAudioBuffer(context, arrayBuffer);
            const startOffsetMs = Number.isFinite(startAtMs) ? Math.max(0, startAtMs) : 0;
            const startOffsetSeconds = Math.max(0, startOffsetMs / 1000);
            if (this.stopGeneration !== stopGenerationAtStart) {
                return true;
            }

            return new Promise((resolve, reject) => {
                let settled = false;
                const source = context.createBufferSource();
                const gainNode = typeof context.createGain === 'function' ? context.createGain() : null;
                const analyserNode = this.createGuideAnalyser(context);
                let mouthMotionSession = null;
                const finish = (success, error) => {
                    if (settled) {
                        return;
                    }
                    settled = true;
                    this.stopGuideMouthMotion(mouthMotionSession);
                    mouthMotionSession = null;
                    if (this.currentFallbackTimer === fallbackTimerId) {
                        this.currentFallbackTimer = null;
                    }
                    window.clearTimeout(fallbackTimerId);
                    source.onended = null;
                    try {
                        source.disconnect();
                    } catch (_) {}
                    if (analyserNode) {
                        try {
                            analyserNode.disconnect();
                        } catch (_) {}
                    }
                    if (gainNode) {
                        try {
                            gainNode.disconnect();
                        } catch (_) {}
                    }
                    if (this.currentAudioMeta && this.currentAudioMeta.source === source) {
                        this.currentAudioMeta = null;
                    }
                    if (this.currentFinish === cancelPlayback) {
                        this.currentFinish = null;
                    }
                    if (success) {
                        resolve(true);
                        return;
                    }
                    reject(error || new Error('guide_audio_context_play_failed'));
                };
                const cancelPlayback = () => {
                    finish(true);
                };

                source.buffer = audioBuffer;
                if (analyserNode && gainNode) {
                    gainNode.gain.value = 1;
                    source.connect(analyserNode);
                    analyserNode.connect(gainNode);
                    gainNode.connect(context.destination);
                } else if (analyserNode) {
                    source.connect(analyserNode);
                    analyserNode.connect(context.destination);
                } else if (gainNode) {
                    gainNode.gain.value = 1;
                    source.connect(gainNode);
                    gainNode.connect(context.destination);
                } else {
                    source.connect(context.destination);
                }
                mouthMotionSession = this.startGuideMouthMotion(
                    meta && typeof meta.voiceKey === 'string' ? meta.voiceKey : '',
                    analyserNode ? { analyserNode: analyserNode } : null
                );

                this.currentFinish = cancelPlayback;
                this.currentAudioMeta = Object.assign({
                    mode: 'buffer',
                    context: context,
                    source: source,
                    analyserNode: analyserNode,
                    gainNode: gainNode,
                    startedAt: context.currentTime,
                    startOffsetMs: startOffsetMs,
                    durationMs: Math.round(audioBuffer.duration * 1000),
                    voiceKey: '',
                    text: ''
                }, meta || {});

                source.onended = () => finish(true);

                const fallbackTimerId = window.setTimeout(() => {
                    finish(true);
                }, Math.max(
                    estimateSpeechDurationMs('x'),
                    minimumDurationMs,
                    Math.max(3000, Math.round(audioBuffer.duration * 1000))
                ) + 1200);
                this.currentFallbackTimer = fallbackTimerId;

                try {
                    source.start(0, Math.min(startOffsetSeconds, Math.max(0, audioBuffer.duration - 0.05)));
                } catch (error) {
                    finish(false, error);
                }
            });
        }

        resolveGuideAudioSrc(voiceKey) {
            const normalizedKey = typeof voiceKey === 'string' ? voiceKey.trim() : '';
            if (!normalizedKey) {
                return '';
            }

            return guideAudioSrc(normalizedKey);
        }

        async speak(text, options) {
            const message = typeof text === 'string' ? text.trim() : '';
            const normalizedOptions = options || {};
            if (!message) {
                return;
            }
            this.stop();
            // 修改原因：cancelActiveNarration()/angry exit 可能发生在 stop() 后的启动缓冲期；
            // 等待结束后必须确认没有新的 stop()，否则旧语音会在取消后重新开始播放。
            const stopGenerationAtStart = this.stopGeneration;
            await wait(48);
            if (this.stopGeneration !== stopGenerationAtStart) {
                return;
            }

            const minimumDurationMs = Number.isFinite(normalizedOptions.minDurationMs)
                ? normalizedOptions.minDurationMs
                : 0;
            const fallbackDurationMs = Math.max(estimateSpeechDurationMs(message), minimumDurationMs);
            const localAudioSrc = this.resolveGuideAudioSrc(normalizedOptions.voiceKey);
            const startAtMs = Number.isFinite(normalizedOptions.startAtMs)
                ? Math.max(0, normalizedOptions.startAtMs)
                : 0;

            if (localAudioSrc) {
                try {
                    const playedByContext = await this.playPreviewAudioThroughContext(
                        localAudioSrc,
                        fallbackDurationMs,
                        startAtMs,
                        {
                            voiceKey: normalizedOptions.voiceKey,
                            text: message
                        }
                    );
                    if (playedByContext) {
                        return;
                    }
                } catch (error) {
                    console.warn('[YuiGuide] AudioContext 教程语音播放失败，尝试 HTMLAudio:', normalizedOptions.voiceKey, error);
                }
                if (this.stopGeneration !== stopGenerationAtStart) {
                    return;
                }

                try {
                    await this.playPreviewAudio(localAudioSrc, fallbackDurationMs, startAtMs, {
                        voiceKey: normalizedOptions.voiceKey,
                        text: message
                    });
                    return;
                } catch (error) {
                    console.warn('[YuiGuide] 本地教程语音播放失败，回退为静默等待:', normalizedOptions.voiceKey, error);
                }
            }

            await wait(fallbackDurationMs);
        }
    }

    namespace.YuiGuideVoiceQueue = YuiGuideVoiceQueue;
})(window.__YuiGuideDirector);
