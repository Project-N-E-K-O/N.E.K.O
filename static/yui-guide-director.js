(function () {
    'use strict';

    const DEFAULT_INTERRUPT_DISTANCE = 32;
    const DEFAULT_INTERRUPT_SPEED_THRESHOLD = 1.8;
    const DEFAULT_INTERRUPT_ACCELERATION_THRESHOLD = 0.09;
    const DEFAULT_INTERRUPT_ACCELERATION_STREAK = 3;
    const DEFAULT_PASSIVE_RESISTANCE_DISTANCE = 10;
    const DEFAULT_PASSIVE_RESISTANCE_INTERVAL_MS = 140;
    const DEFAULT_STEP_DELAY_MS = 120;
    const DEFAULT_SCENE_SETTLE_MS = 260;
    const DEFAULT_CURSOR_DURATION_MS = 520;
    const INTRO_PRACTICE_TEXT = '现在来可以试试跟我说说话吧，看看我们是不是超有默契的喵～';
    const INTRO_SKIP_ACTION_ID = 'yui-guide-intro-skip-chat';
    const REACT_CHAT_ACTION_EVENT = 'react-chat-window:action';
    const REACT_CHAT_SUBMIT_EVENT = 'react-chat-window:submit';
    const PLUGIN_DASHBOARD_WINDOW_NAME = 'plugin_dashboard';
    const PLUGIN_DASHBOARD_HANDOFF_EVENT = 'neko:yui-guide:plugin-dashboard:start';
    const PLUGIN_DASHBOARD_READY_EVENT = 'neko:yui-guide:plugin-dashboard:ready';
    const PLUGIN_DASHBOARD_DONE_EVENT = 'neko:yui-guide:plugin-dashboard:done';
    const DEFAULT_TUTORIAL_MODEL_MANAGER_LANLAN_NAME = 'ATLS';
    const PREVIEW_ITEMS = Object.freeze([
        'WebSearch',
        'B站弹幕',
        '米家控制',
        '智能家居',
        '日程提醒'
    ]);
    const TAKEOVER_CAPTURE_SELECTORS = Object.freeze({
        catPaw: '[alt="猫爪"]',
        agentMaster: 'div[aria-label="猫爪总开关"][title="猫爪总开关"]',
        userPlugin: 'div[aria-label="用户插件"][title="用户插件"]',
        managementPanel: 'div#neko-sidepanel-action-agent-user-plugin-management-panel'
    });
    function wait(ms) {
        return new Promise((resolve) => {
            window.setTimeout(resolve, ms);
        });
    }

    function clamp(value, min, max) {
        return Math.max(min, Math.min(max, value));
    }

    function unionRects(rects) {
        const items = Array.isArray(rects) ? rects.filter(Boolean) : [];
        if (items.length === 0) {
            return null;
        }

        const left = Math.min.apply(null, items.map((rect) => rect.left));
        const top = Math.min.apply(null, items.map((rect) => rect.top));
        const right = Math.max.apply(null, items.map((rect) => rect.right));
        const bottom = Math.max.apply(null, items.map((rect) => rect.bottom));
        const width = Math.max(0, right - left);
        const height = Math.max(0, bottom - top);

        if (width <= 0 || height <= 0) {
            return null;
        }

        return {
            left: left,
            top: top,
            right: right,
            bottom: bottom,
            width: width,
            height: height
        };
    }

    function estimateSpeechDurationMs(text) {
        const message = typeof text === 'string' ? text.trim() : '';
        if (!message) {
            return 0;
        }

        return clamp(Math.round(message.length * 280), 2200, 24000);
    }

    function waitForFirstUserGesture(timeoutMs) {
        const normalizedTimeoutMs = Number.isFinite(timeoutMs) ? timeoutMs : 12000;
        if (navigator.userActivation && (navigator.userActivation.isActive || navigator.userActivation.hasBeenActive)) {
            return Promise.resolve(true);
        }

        return new Promise((resolve) => {
            let settled = false;
            let timeoutId = 0;

            const finish = (activated) => {
                if (settled) {
                    return;
                }
                settled = true;
                if (timeoutId) {
                    window.clearTimeout(timeoutId);
                    timeoutId = 0;
                }
                window.removeEventListener('pointerdown', handleGesture, true);
                window.removeEventListener('mousedown', handleGesture, true);
                window.removeEventListener('keydown', handleGesture, true);
                window.removeEventListener('touchstart', handleGesture, true);
                resolve(!!activated);
            };

            const handleGesture = () => {
                finish(true);
            };

            window.addEventListener('pointerdown', handleGesture, true);
            window.addEventListener('mousedown', handleGesture, true);
            window.addEventListener('keydown', handleGesture, true);
            window.addEventListener('touchstart', handleGesture, true);
            timeoutId = window.setTimeout(() => finish(false), normalizedTimeoutMs);
        });
    }

    async function resumeKnownAudioContexts() {
        const tasks = [];

        if (window.AM && typeof window.AM.unlock === 'function') {
            try {
                window.AM.unlock();
            } catch (_) {}
        }

        const playerContext = window.appState && window.appState.audioPlayerContext;
        if (playerContext && playerContext.state === 'suspended' && typeof playerContext.resume === 'function') {
            tasks.push(playerContext.resume().catch(() => {}));
        }

        if (window.lanlanAudioContext && window.lanlanAudioContext.state === 'suspended' && typeof window.lanlanAudioContext.resume === 'function') {
            tasks.push(window.lanlanAudioContext.resume().catch(() => {}));
        }

        if (tasks.length > 0) {
            await Promise.all(tasks);
        }
    }

    function normalizeVoiceLang(voice) {
        const lang = voice && typeof voice.lang === 'string' ? voice.lang.trim().toLowerCase() : '';
        return lang.replace('_', '-');
    }

    function scoreSpeechVoice(voice) {
        if (!voice) {
            return 0;
        }

        const name = typeof voice.name === 'string' ? voice.name.trim().toLowerCase() : '';
        const lang = normalizeVoiceLang(voice);
        let score = 0;

        if (lang === 'zh-cn') {
            score += 100;
        } else if (lang.indexOf('zh') === 0) {
            score += 80;
        } else if (lang === 'cmn-cn') {
            score += 90;
        }

        if (name.indexOf('chinese') >= 0 || name.indexOf('mandarin') >= 0 || name.indexOf('中文') >= 0) {
            score += 20;
        }

        if (voice.default) {
            score += 5;
        }

        return score;
    }

    class YuiGuideVoiceQueue {
        constructor() {
            this.currentUtterance = null;
            this.currentFallbackTimer = null;
            this.currentFinish = null;
            this.enabled = !!window.speechSynthesis;
            this.voicesReadyPromise = null;
            this.currentAudio = null;
            this.voiceIdCache = {
                name: '',
                value: '',
                fetchedAt: 0
            };
            this.previewCache = new Map();
        }

        stop() {
            const finish = this.currentFinish;

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

            this.currentUtterance = null;
            this.currentFinish = null;

            if (typeof finish === 'function') {
                try {
                    finish();
                } catch (_) {}
            }
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

        async fetchPreviewAudioSrc(text) {
            const message = typeof text === 'string' ? text.trim() : '';
            if (!message) {
                return null;
            }

            const voiceId = await this.getCurrentVoiceId();
            if (!voiceId) {
                return null;
            }

            const cacheKey = voiceId + '::' + message;
            if (this.previewCache.has(cacheKey)) {
                return {
                    voiceId: voiceId,
                    audioSrc: this.previewCache.get(cacheKey)
                };
            }

            try {
                const response = await fetch(
                    '/api/characters/voice_preview?voice_id='
                    + encodeURIComponent(voiceId)
                    + '&text='
                    + encodeURIComponent(message),
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
                this.previewCache.set(cacheKey, audioSrc);
                return {
                    voiceId: voiceId,
                    audioSrc: audioSrc
                };
            } catch (error) {
                console.warn('[YuiGuide] 获取语音预览失败:', error);
                return null;
            }
        }

        async playPreviewAudio(audioSrc, minimumDurationMs) {
            if (!audioSrc) {
                return false;
            }

            const minDurationMs = Number.isFinite(minimumDurationMs) ? minimumDurationMs : 0;

            return new Promise((resolve, reject) => {
                let settled = false;
                const audio = new Audio(audioSrc);
                const maxBlockedWaitMs = 12000;
                const finish = (success, error) => {
                    if (settled) {
                        return;
                    }
                    settled = true;
                    if (this.currentFallbackTimer === fallbackTimerId) {
                        this.currentFallbackTimer = null;
                    }
                    window.clearTimeout(fallbackTimerId);
                    audio.onended = null;
                    audio.onerror = null;
                    audio.onpause = null;
                    if (this.currentAudio === audio) {
                        this.currentAudio = null;
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
                this.currentFinish = cancelPlayback;

                const fallbackTimerId = window.setTimeout(() => {
                    finish(true);
                }, Math.max(estimateSpeechDurationMs('x'), minDurationMs, 3000) + maxBlockedWaitMs);
                this.currentFallbackTimer = fallbackTimerId;

                const attemptPlayback = async () => {
                    try {
                        const playPromise = audio.play();
                        if (playPromise && typeof playPromise.then === 'function') {
                            await playPromise;
                        }
                    } catch (error) {
                        const message = error && typeof error.message === 'string' ? error.message : '';
                        const errorName = error && typeof error.name === 'string' ? error.name : '';
                        const blockedByAutoplay = errorName === 'NotAllowedError'
                            || /gesture|user activation|autoplay/i.test(message);

                        if (!blockedByAutoplay) {
                            finish(false, error);
                            return;
                        }

                        console.warn('[YuiGuide] 教程语音等待用户手势后重试播放:', error);
                        const unlocked = await waitForFirstUserGesture(maxBlockedWaitMs);
                        if (!unlocked || settled) {
                            finish(false, error);
                            return;
                        }

                        await resumeKnownAudioContexts();
                        try {
                            audio.currentTime = 0;
                        } catch (_) {}

                        try {
                            const retryPromise = audio.play();
                            if (retryPromise && typeof retryPromise.then === 'function') {
                                await retryPromise;
                            }
                        } catch (retryError) {
                            finish(false, retryError);
                        }
                    }
                };

                void attemptPlayback();
            });
        }

        async speak(text, options) {
            const message = typeof text === 'string' ? text.trim() : '';
            const normalizedOptions = options || {};
            if (!message) {
                return;
            }
            this.stop();
            await wait(48);

            const minimumDurationMs = Number.isFinite(normalizedOptions.minDurationMs)
                ? normalizedOptions.minDurationMs
                : 0;
            const fallbackDurationMs = Math.max(estimateSpeechDurationMs(message), minimumDurationMs);

            if (!this.enabled || typeof SpeechSynthesisUtterance === 'undefined' || !window.speechSynthesis) {
                await wait(fallbackDurationMs);
                return;
            }

            await this.ensureVoicesReady();

            return new Promise((resolve) => {
                let settled = false;
                const finish = () => {
                    if (settled) {
                        return;
                    }
                    settled = true;
                    if (this.currentFallbackTimer) {
                        window.clearTimeout(this.currentFallbackTimer);
                        this.currentFallbackTimer = null;
                    }
                    if (this.currentUtterance === utterance) {
                        this.currentUtterance = null;
                    }
                    if (this.currentFinish === finish) {
                        this.currentFinish = null;
                    }
                    resolve();
                };

                const utterance = new SpeechSynthesisUtterance(message);
                utterance.lang = 'zh-CN';
                utterance.rate = 1.02;
                utterance.pitch = 1.05;
                utterance.volume = 0.9;

                try {
                    const voices = window.speechSynthesis.getVoices();
                    if (Array.isArray(voices) && voices.length > 0) {
                        let bestVoice = null;
                        let bestScore = -1;
                        voices.forEach((voice) => {
                            const score = scoreSpeechVoice(voice);
                            if (score > bestScore) {
                                bestScore = score;
                                bestVoice = voice;
                            }
                        });
                        if (bestVoice) {
                            utterance.voice = bestVoice;
                        }
                    }
                } catch (error) {
                    console.warn('[YuiGuide] 选择系统语音失败:', error);
                }

                utterance.onboundary = (event) => {
                    if (typeof normalizedOptions.onBoundary === 'function') {
                        try {
                            normalizedOptions.onBoundary(event);
                        } catch (error) {
                            console.warn('[YuiGuide] 语音边界回调失败:', error);
                        }
                    }
                };
                utterance.onend = finish;
                utterance.onerror = finish;
                this.currentUtterance = utterance;
                this.currentFinish = finish;
                this.currentFallbackTimer = window.setTimeout(
                    finish,
                    Math.max(fallbackDurationMs, 3000)
                );

                try {
                    window.speechSynthesis.cancel();
                    window.speechSynthesis.speak(utterance);
                } catch (error) {
                    console.warn('[YuiGuide] 播放系统语音失败，回退为静默等待:', error);
                    finish();
                }
            });
        }
    }

    class YuiGuideEmotionBridge {
        apply(emotion) {
            if (!emotion || !window.LanLan1 || typeof window.LanLan1.setEmotion !== 'function') {
                return;
            }

            try {
                window.LanLan1.setEmotion(emotion);
            } catch (error) {
                console.warn('[YuiGuide] 设置情绪失败:', error);
            }
        }

        clear() {
            if (window.LanLan1 && typeof window.LanLan1.clearEmotionEffects === 'function') {
                try {
                    window.LanLan1.clearEmotionEffects();
                    return;
                } catch (error) {
                    console.warn('[YuiGuide] 清理情绪失败:', error);
                }
            }

            if (window.LanLan1 && typeof window.LanLan1.clearExpression === 'function') {
                try {
                    window.LanLan1.clearExpression();
                } catch (error) {
                    console.warn('[YuiGuide] 清理表情失败:', error);
                }
            }
        }
    }

    class YuiGuideGhostCursor {
        constructor(overlay) {
            this.overlay = overlay;
            this.motionToken = 0;
            this.lastTarget = null;
            this.reactionToken = 0;
        }

        hasPosition() {
            return this.overlay.hasCursorPosition();
        }

        showAt(x, y) {
            this.overlay.showCursorAt(x, y);
        }

        moveToPoint(x, y, options) {
            this.motionToken += 1;
            this.lastTarget = { x: x, y: y };
            return this.overlay.moveCursorTo(x, y, options);
        }

        moveToRect(rect, options) {
            if (!rect) {
                return Promise.resolve();
            }

            const point = {
                x: rect.left + (rect.width / 2),
                y: rect.top + (rect.height / 2)
            };

            return this.moveToPoint(point.x, point.y, options);
        }

        async resistTo(userX, userY) {
            const current = this.overlay.getCursorPosition();
            if (!current) {
                return;
            }

            const dx = userX - current.x;
            const dy = userY - current.y;
            const distance = Math.max(1, Math.hypot(dx, dy));
            const pullDistance = clamp(distance * 0.22, 12, 36);
            const pullX = current.x + ((dx / distance) * pullDistance);
            const pullY = current.y + ((dy / distance) * pullDistance);
            const returnTarget = this.lastTarget || current;

            await this.overlay.moveCursorTo(pullX, pullY, { durationMs: 120 });
            this.overlay.wobbleCursor();
            await this.overlay.moveCursorTo(returnTarget.x, returnTarget.y, { durationMs: 260 });
        }

        async reactToUserMotion(userX, userY, options) {
            const current = this.overlay.getCursorPosition();
            if (!current) {
                return;
            }

            const normalizedOptions = options || {};
            const dx = userX - current.x;
            const dy = userY - current.y;
            const distance = Math.max(1, Math.hypot(dx, dy));
            const reactionDistance = clamp(
                distance * (Number.isFinite(normalizedOptions.scale) ? normalizedOptions.scale : 0.12),
                6,
                18
            );
            const targetX = current.x + ((dx / distance) * reactionDistance);
            const targetY = current.y + ((dy / distance) * reactionDistance);
            const returnTarget = this.lastTarget || current;
            const token = ++this.reactionToken;

            await this.overlay.moveCursorTo(targetX, targetY, {
                durationMs: Number.isFinite(normalizedOptions.outDurationMs) ? normalizedOptions.outDurationMs : 80
            });
            if (token !== this.reactionToken) {
                return;
            }

            await this.overlay.moveCursorTo(returnTarget.x, returnTarget.y, {
                durationMs: Number.isFinite(normalizedOptions.backDurationMs) ? normalizedOptions.backDurationMs : 150
            });
        }

        click() {
            this.overlay.clickCursor();
        }

        wobble() {
            this.overlay.wobbleCursor();
        }

        hide() {
            this.overlay.hideCursor();
        }

        cancel() {
            this.motionToken += 1;
        }
    }

    class YuiGuideDirector {
        constructor(options) {
            this.options = options || {};
            this.tutorialManager = this.options.tutorialManager || null;
            this.page = this.options.page || 'home';
            this.registry = this.options.registry || null;
            this.overlay = new window.YuiGuideOverlay(document);
            this.voiceQueue = new YuiGuideVoiceQueue();
            this.emotionBridge = new YuiGuideEmotionBridge();
            this.cursor = new YuiGuideGhostCursor(this.overlay);
            this.currentSceneId = null;
            this.currentStep = null;
            this.currentContext = null;
            this.sceneRunId = 0;
            this.sceneTimers = new Set();
            this.interruptsEnabled = false;
            this.interruptCount = 0;
            this.interruptAccelerationStreak = 0;
            this.lastInterruptAt = 0;
            this.lastPassiveResistanceAt = 0;
            this.lastPointerPoint = null;
            this.angryExitTriggered = false;
            this.destroyed = false;
            this.lastTutorialEndReason = null;
            this.introFlowStarted = false;
            this.introFlowCompleted = false;
            this.introClickActivated = false;
            this.introChoicePending = false;
            this.introPracticeMessageId = null;
            this.introThirdMessageTimer = null;
            this.introReplyPollTimer = null;
            this.takeoverFlowStarted = false;
            this.takeoverFlowCompleted = false;
            this.takeoverFlowPromise = null;
            this.terminationRequested = false;
            this.activeNarration = null;
            this.narrationResumeTimer = null;
            this.chatIntroCleanupFns = [];
            this.virtualSpotlights = new Map();
            this.preciseHighlightElements = new Set();
            this.spotlightVariantElements = new Set();
            this.retainedExtraSpotlightElements = [];
            this.sceneExtraSpotlightElements = [];
            this.pluginDashboardHandoff = null;
            this.customSecondarySpotlightTarget = null;
            this.keydownHandler = this.onKeyDown.bind(this);
            this.pointerMoveHandler = this.onPointerMove.bind(this);
            this.pointerDownHandler = this.onPointerDown.bind(this);
            this.pageHideHandler = this.onPageHide.bind(this);
            this.tutorialEndHandler = this.onTutorialEndEvent.bind(this);
            this.messageHandler = this.onWindowMessage.bind(this);
            this.skipButtonClickHandler = this.onSkipButtonClick.bind(this);

            if (this.page === 'home') {
                document.body.classList.add('yui-guide-home-driver-hidden');
            }

            window.addEventListener('keydown', this.keydownHandler, true);
            window.addEventListener('pagehide', this.pageHideHandler, true);
            window.addEventListener('neko:yui-guide:tutorial-end', this.tutorialEndHandler, true);
            window.addEventListener('message', this.messageHandler, true);
            document.addEventListener('click', this.skipButtonClickHandler, true);
        }

        getPreludeSceneIds() {
            if (this.tutorialManager && typeof this.tutorialManager.getYuiGuidePreludeSceneIds === 'function') {
                return this.tutorialManager.getYuiGuidePreludeSceneIds(this.page) || [];
            }

            if (!this.registry || !this.registry.sceneOrder) {
                return [];
            }

            const pageOrder = Array.isArray(this.registry.sceneOrder[this.page]) ? this.registry.sceneOrder[this.page] : [];
            return pageOrder.filter(function (sceneId) {
                return typeof sceneId === 'string' && sceneId.indexOf('intro_') === 0;
            });
        }

        getStep(stepId) {
            if (!stepId) {
                return null;
            }

            if (this.registry && typeof this.registry.getStep === 'function') {
                return this.registry.getStep(stepId) || null;
            }

            return null;
        }

        resolveModelPrefix() {
            if (this.tutorialManager && this.tutorialManager._tutorialModelPrefix) {
                return this.tutorialManager._tutorialModelPrefix;
            }

            if (this.tutorialManager && this.tutorialManager.constructor && typeof this.tutorialManager.constructor.detectModelPrefix === 'function') {
                return this.tutorialManager.constructor.detectModelPrefix();
            }

            if (window.universalTutorialManager &&
                window.universalTutorialManager.constructor &&
                typeof window.universalTutorialManager.constructor.detectModelPrefix === 'function') {
                return window.universalTutorialManager.constructor.detectModelPrefix();
            }

            return 'live2d';
        }

        expandSelector(selector) {
            if (typeof selector !== 'string' || !selector.trim()) {
                return '';
            }

            return selector.replace(/\$\{p\}/g, this.resolveModelPrefix());
        }

        resolveElement(selector) {
            const expanded = this.expandSelector(selector);
            if (!expanded) {
                return null;
            }

            try {
                return document.querySelector(expanded);
            } catch (error) {
                console.warn('[YuiGuide] 查询元素失败:', expanded, error);
                return null;
            }
        }

        queryDocumentSelector(selector) {
            const normalizedSelector = typeof selector === 'string' ? selector.trim() : '';
            if (!normalizedSelector) {
                return null;
            }

            try {
                return document.querySelector(normalizedSelector);
            } catch (error) {
                console.warn('[YuiGuide] document.querySelector 查询失败:', normalizedSelector, error);
                return null;
            }
        }

        resolveRect(selector) {
            if (selector === 'body') {
                return {
                    left: 0,
                    top: 0,
                    right: window.innerWidth,
                    bottom: window.innerHeight,
                    width: window.innerWidth,
                    height: window.innerHeight
                };
            }

            const element = this.resolveElement(selector);
            if (!element || typeof element.getBoundingClientRect !== 'function') {
                return null;
            }

            return element.getBoundingClientRect();
        }

        getDefaultCursorOrigin() {
            const prefix = this.resolveModelPrefix();
            const modelRect = this.resolveRect('#' + prefix + '-container');
            if (modelRect) {
                return {
                    x: modelRect.left + (modelRect.width / 2),
                    y: modelRect.top + Math.min(modelRect.height * 0.55, modelRect.height - 16)
                };
            }

            return {
                x: Math.max(120, window.innerWidth * 0.72),
                y: Math.max(120, window.innerHeight * 0.45)
            };
        }

        getViewportCenter() {
            return {
                x: window.innerWidth / 2,
                y: window.innerHeight / 2
            };
        }

        getElementRect(element) {
            if (!element || typeof element.getBoundingClientRect !== 'function') {
                return null;
            }

            const rect = element.getBoundingClientRect();
            if (!rect || rect.width <= 0 || rect.height <= 0) {
                return null;
            }

            return rect;
        }

        createVirtualSpotlight(key, rect, options) {
            if (!key || !rect) {
                return null;
            }

            const normalizedOptions = options || {};
            const padding = Number.isFinite(normalizedOptions.padding) ? normalizedOptions.padding : 12;
            const elementKey = String(key);
            let element = this.virtualSpotlights.get(elementKey) || null;
            if (!element) {
                element = document.createElement('div');
                element.setAttribute('data-yui-guide-virtual-spotlight', elementKey);
                Object.assign(element.style, {
                    position: 'fixed',
                    pointerEvents: 'none',
                    opacity: '0',
                    zIndex: '-1'
                });
                document.body.appendChild(element);
                this.virtualSpotlights.set(elementKey, element);
            }

            const left = Math.max(0, Math.floor(rect.left - padding));
            const top = Math.max(0, Math.floor(rect.top - padding));
            const right = Math.min(window.innerWidth, Math.ceil(rect.right + padding));
            const bottom = Math.min(window.innerHeight, Math.ceil(rect.bottom + padding));
            element.style.left = left + 'px';
            element.style.top = top + 'px';
            element.style.width = Math.max(0, right - left) + 'px';
            element.style.height = Math.max(0, bottom - top) + 'px';
            element.style.borderRadius = (Number.isFinite(normalizedOptions.radius) ? normalizedOptions.radius : 20) + 'px';
            return element;
        }

        createUnionSpotlight(key, elements, options) {
            const rect = unionRects((Array.isArray(elements) ? elements : []).map((element) => this.getElementRect(element)));
            return rect ? this.createVirtualSpotlight(key, rect, options) : null;
        }

        clearVirtualSpotlight(key) {
            if (!key) {
                return;
            }

            const element = this.virtualSpotlights.get(String(key));
            if (element && element.parentNode) {
                element.parentNode.removeChild(element);
            }
            this.virtualSpotlights.delete(String(key));
        }

        clearAllVirtualSpotlights() {
            this.virtualSpotlights.forEach((element) => {
                if (element && element.parentNode) {
                    element.parentNode.removeChild(element);
                }
            });
            this.virtualSpotlights.clear();
        }

        clearPreciseHighlights() {
            this.preciseHighlightElements.forEach((element) => {
                if (!element || !element.classList) {
                    return;
                }

                element.classList.remove('yui-guide-precise-highlight');
                element.removeAttribute('data-yui-guide-precise-highlight');
            });
            this.preciseHighlightElements.clear();
        }

        setPreciseHighlightTargets(elements) {
            const targets = (Array.isArray(elements) ? elements : [])
                .filter((element) => !!element && !!element.classList);

            this.clearPreciseHighlights();
            targets.forEach((element) => {
                element.classList.add('yui-guide-precise-highlight');
                element.setAttribute('data-yui-guide-precise-highlight', 'true');
                this.preciseHighlightElements.add(element);
            });
        }

        clearSpotlightVariantHints() {
            this.spotlightVariantElements.forEach((element) => {
                if (!element || typeof element.removeAttribute !== 'function') {
                    return;
                }

                element.removeAttribute('data-yui-guide-spotlight-variant');
            });
            this.spotlightVariantElements.clear();
        }

        setSpotlightVariantHints(entries) {
            this.clearSpotlightVariantHints();
            (Array.isArray(entries) ? entries : []).forEach((entry) => {
                const element = entry && entry.element;
                const variant = entry && entry.variant;
                if (!element || typeof element.setAttribute !== 'function' || !variant) {
                    return;
                }

                element.setAttribute('data-yui-guide-spotlight-variant', String(variant));
                this.spotlightVariantElements.add(element);
            });
        }

        syncExtraSpotlights() {
            const nextElements = [];
            const seen = new Set();
            [this.retainedExtraSpotlightElements, this.sceneExtraSpotlightElements].forEach((elements) => {
                (Array.isArray(elements) ? elements : []).forEach((element) => {
                    const isVirtualSpotlight = !!(
                        element
                        && typeof element.getAttribute === 'function'
                        && element.getAttribute('data-yui-guide-virtual-spotlight')
                    );
                    if (
                        !element
                        || typeof element.getBoundingClientRect !== 'function'
                        || (!isVirtualSpotlight && element.isConnected === false)
                        || seen.has(element)
                    ) {
                        return;
                    }
                    seen.add(element);
                    nextElements.push(element);
                });
            });
            this.overlay.setExtraSpotlights(nextElements);
        }

        addRetainedExtraSpotlight(element) {
            if (!element || typeof element.getBoundingClientRect !== 'function') {
                return;
            }

            if (!this.retainedExtraSpotlightElements.includes(element)) {
                this.retainedExtraSpotlightElements.push(element);
            }
            this.syncExtraSpotlights();
        }

        replaceRetainedExtraSpotlight(matcher, element) {
            const normalizedMatcher = typeof matcher === 'function'
                ? matcher
                : (candidate) => candidate === matcher;
            this.retainedExtraSpotlightElements = this.retainedExtraSpotlightElements.filter((candidate) => {
                try {
                    return !normalizedMatcher(candidate);
                } catch (_) {
                    return true;
                }
            });
            if (element && typeof element.getBoundingClientRect === 'function') {
                this.retainedExtraSpotlightElements.push(element);
            }
            this.syncExtraSpotlights();
        }

        clearRetainedExtraSpotlights() {
            this.retainedExtraSpotlightElements = [];
            this.syncExtraSpotlights();
        }

        setSceneExtraSpotlights(elements) {
            this.sceneExtraSpotlightElements = (Array.isArray(elements) ? elements : [])
                .filter((element) => !!element && typeof element.getBoundingClientRect === 'function');
            this.syncExtraSpotlights();
        }

        clearSceneExtraSpotlights() {
            this.sceneExtraSpotlightElements = [];
            this.syncExtraSpotlights();
        }

        clearAllExtraSpotlights() {
            this.retainedExtraSpotlightElements = [];
            this.sceneExtraSpotlightElements = [];
            this.overlay.clearExtraSpotlights();
        }

        cleanupTutorialReturnButtons() {
            [
                '#live2d-btn-return',
                '#live2d-return-button-container',
                '#vrm-btn-return',
                '#vrm-return-button-container',
                '#mmd-btn-return',
                '#mmd-return-button-container'
            ].forEach((selector) => {
                document.querySelectorAll(selector).forEach((element) => {
                    if (element && typeof element.remove === 'function') {
                        element.remove();
                    }
                });
            });
        }

        getAgentToggleElement(toggleId) {
            if (!toggleId) {
                return null;
            }

            return this.resolveElement('#${p}-toggle-' + toggleId);
        }

        getAgentToggleCheckbox(toggleId) {
            if (!toggleId) {
                return null;
            }

            return this.resolveElement('#${p}-' + toggleId);
        }

        getAgentSidePanelButton(toggleId, actionId) {
            if (!toggleId || !actionId) {
                return null;
            }

            return document.getElementById('neko-sidepanel-action-' + toggleId + '-' + actionId);
        }

        getAgentSidePanel(toggleId) {
            if (!toggleId) {
                return null;
            }

            return document.querySelector('[data-neko-sidepanel-type="' + toggleId + '-actions"]');
        }

        isAgentSidePanelVisible(toggleId) {
            const sidePanel = this.getAgentSidePanel(toggleId);
            return !!(sidePanel && sidePanel.style.display === 'flex' && sidePanel.style.opacity !== '0');
        }

        getCharacterAppearanceMenuId() {
            const prefix = this.resolveModelPrefix();
            if (prefix === 'vrm') {
                return 'vrm-manage';
            }
            if (prefix === 'mmd') {
                return 'mmd-manage';
            }
            return 'live2d-manage';
        }

        getTutorialModelManagerLanlanName() {
            const explicitName = typeof window.NEKO_YUI_GUIDE_MODEL_MANAGER_LANLAN_NAME === 'string'
                ? window.NEKO_YUI_GUIDE_MODEL_MANAGER_LANLAN_NAME.trim()
                : '';
            if (explicitName) {
                return explicitName;
            }

            return DEFAULT_TUTORIAL_MODEL_MANAGER_LANLAN_NAME;
        }

        getModelManagerWindowName(lanlanName, appearanceMenuId) {
            const name = typeof lanlanName === 'string' && lanlanName.trim()
                ? lanlanName.trim()
                : this.getTutorialModelManagerLanlanName();
            const menuId = appearanceMenuId || this.getCharacterAppearanceMenuId();
            if (menuId === 'vrm-manage') {
                return 'vrm-manage_' + encodeURIComponent(name);
            }
            if (menuId === 'mmd-manage') {
                return 'mmd-manage_' + encodeURIComponent(name);
            }
            return 'live2d-manage_' + encodeURIComponent(name);
        }

        getCharacterMenuElement(menuId) {
            if (!menuId) {
                return null;
            }

            return this.resolveElement('#${p}-sidepanel-' + menuId);
        }

        getCharacterSettingsSidePanel() {
            return document.querySelector('[data-neko-sidepanel-type="character-settings"]');
        }

        getFloatingButtonShell(element) {
            if (!element) {
                return null;
            }

            if (typeof element.closest === 'function') {
                const shell = element.closest(
                    '#live2d-btn-agent, #vrm-btn-agent, #mmd-btn-agent, ' +
                    '#live2d-btn-settings, #vrm-btn-settings, #mmd-btn-settings, ' +
                    '[id$="-btn-agent"], [id$="-btn-settings"]'
                );
                if (shell) {
                    return shell;
                }
            }

            return element;
        }

        getSettingsPeekTargets() {
            const appearanceMenuId = this.getCharacterAppearanceMenuId();
            return {
                characterMenu: this.getSettingsMenuElement('character'),
                appearanceItem: this.getCharacterMenuElement(appearanceMenuId),
                voiceCloneItem: this.getCharacterMenuElement('voice-clone')
            };
        }

        refreshSettingsPeekSpotlights(settingsButton) {
            const targets = this.getSettingsPeekTargets();
            const normalizeVisibleTarget = (element) => this.isElementVisible(element) ? element : null;
            const settingsButtonTarget = normalizeVisibleTarget(
                this.getFloatingButtonShell(
                    settingsButton
                    || this.getFallbackFloatingButton('settings')
                    || this.resolveElement('#${p}-btn-settings')
                )
            );
            const characterMenu = normalizeVisibleTarget(targets.characterMenu);
            const appearanceItem = normalizeVisibleTarget(targets.appearanceItem);
            const voiceCloneItem = normalizeVisibleTarget(targets.voiceCloneItem);
            const characterChildrenBundle = (appearanceItem && voiceCloneItem)
                ? this.createUnionSpotlight(
                    'settings-character-children-bundle',
                    [appearanceItem, voiceCloneItem],
                    {
                        padding: 10,
                        radius: 18
                    }
                )
                : null;
            this.setSceneExtraSpotlights([
                settingsButtonTarget,
                characterMenu,
                characterChildrenBundle
            ].filter(Boolean));

            return {
                settingsButton: settingsButtonTarget,
                characterMenu: characterMenu,
                appearanceItem: appearanceItem,
                voiceCloneItem: voiceCloneItem,
                characterChildrenBundle: characterChildrenBundle
            };
        }

        async ensureCharacterSettingsSidePanelVisible() {
            const sidePanel = this.getCharacterSettingsSidePanel();
            const anchor = this.getSettingsMenuElement('character');
            if (!sidePanel || !anchor) {
                return false;
            }

            if (typeof sidePanel._expand === 'function') {
                sidePanel._expand();
            } else {
                anchor.dispatchEvent(new MouseEvent('mouseenter', {
                    bubbles: true,
                    cancelable: true,
                    view: window
                }));
            }

            const visiblePanel = await this.waitForVisibleElement(() => this.getCharacterSettingsSidePanel(), 1600);
            return !!visiblePanel;
        }

        normalizeHighlightTarget(target, fallbackKey) {
            if (!target) {
                return null;
            }

            if (Array.isArray(target)) {
                return this.createUnionSpotlight(fallbackKey || 'highlight-union', target, {
                    padding: 12,
                    radius: 18
                });
            }

            if (typeof target === 'string') {
                return this.resolveElement(target);
            }

            if (target && typeof target === 'object') {
                if (target.element) {
                    return target.element;
                }
                if (target.selector) {
                    return this.resolveElement(target.selector);
                }
                if (Array.isArray(target.elements)) {
                    return this.createUnionSpotlight(
                        target.key || fallbackKey || 'highlight-union',
                        target.elements,
                        target.options || {}
                    );
                }
                if (target.rect) {
                    return this.createVirtualSpotlight(
                        target.key || fallbackKey || 'highlight-rect',
                        target.rect,
                        target.options || {}
                    );
                }
            }

            return target;
        }

        applyGuideHighlights(config) {
            const normalized = config || {};
            const keyBase = normalized.key || 'guide-highlight';
            const persistentTarget = Object.prototype.hasOwnProperty.call(normalized, 'persistent')
                ? this.normalizeHighlightTarget(normalized.persistent, keyBase + '-persistent')
                : null;
            const primaryTarget = Object.prototype.hasOwnProperty.call(normalized, 'primary')
                ? this.normalizeHighlightTarget(normalized.primary, keyBase + '-primary')
                : null;
            const secondaryTarget = Object.prototype.hasOwnProperty.call(normalized, 'secondary')
                ? this.normalizeHighlightTarget(normalized.secondary, keyBase + '-secondary')
                : null;

            if (Object.prototype.hasOwnProperty.call(normalized, 'persistent')) {
                if (persistentTarget) {
                    this.overlay.setPersistentSpotlight(persistentTarget);
                } else {
                    this.overlay.clearPersistentSpotlight();
                }
            }

            if (Object.prototype.hasOwnProperty.call(normalized, 'primary')) {
                if (primaryTarget) {
                    this.overlay.activateSpotlight(primaryTarget);
                } else {
                    this.overlay.clearActionSpotlight();
                }
            }

            if (Object.prototype.hasOwnProperty.call(normalized, 'secondary')) {
                this.customSecondarySpotlightTarget = secondaryTarget || null;
                if (secondaryTarget) {
                    this.overlay.activateSecondarySpotlight(secondaryTarget);
                } else if (!Object.prototype.hasOwnProperty.call(normalized, 'primary')) {
                    this.overlay.clearActionSpotlight();
                }
            }

            return {
                persistent: persistentTarget,
                primary: primaryTarget,
                secondary: secondaryTarget
            };
        }

        clearIntroFlow(preserveSpotlight) {
            if (this.introThirdMessageTimer) {
                window.clearTimeout(this.introThirdMessageTimer);
                this.introThirdMessageTimer = null;
            }

            if (this.introReplyPollTimer) {
                window.clearTimeout(this.introReplyPollTimer);
                this.introReplyPollTimer = null;
            }

            this.introChoicePending = false;
            this.introPracticeMessageId = null;

            while (this.chatIntroCleanupFns.length > 0) {
                const cleanup = this.chatIntroCleanupFns.pop();
                try {
                    cleanup();
                } catch (error) {
                    console.warn('[YuiGuide] 清理 intro flow 监听器失败:', error);
                }
            }

            if (!preserveSpotlight) {
                this.overlay.clearSpotlight();
            }
        }

        waitForElement(resolveElement, timeoutMs) {
            const resolver = typeof resolveElement === 'function' ? resolveElement : function () { return null; };
            const timeout = Number.isFinite(timeoutMs) ? timeoutMs : 4000;

            return new Promise((resolve) => {
                const startedAt = Date.now();
                const tick = () => {
                    if (this.destroyed) {
                        resolve(null);
                        return;
                    }

                    const element = resolver();
                    if (element) {
                        resolve(element);
                        return;
                    }

                    if ((Date.now() - startedAt) >= timeout) {
                        resolve(null);
                        return;
                    }

                    window.setTimeout(tick, 80);
                };

                tick();
            });
        }

        isElementVisible(element) {
            if (!element || typeof element.getBoundingClientRect !== 'function') {
                return false;
            }

            const rect = element.getBoundingClientRect();
            if (!rect || rect.width <= 0 || rect.height <= 0) {
                return false;
            }

            if (element.offsetParent !== null) {
                return true;
            }

            try {
                return window.getComputedStyle(element).position === 'fixed';
            } catch (_) {
                return false;
            }
        }

        waitForVisibleElement(resolveElement, timeoutMs) {
            return this.waitForElement(() => {
                const element = typeof resolveElement === 'function' ? resolveElement() : null;
                return this.isElementVisible(element) ? element : null;
            }, timeoutMs);
        }

        waitForDocumentSelector(selector, timeoutMs, requireVisible) {
            const normalizedSelector = typeof selector === 'string' ? selector.trim() : '';
            if (!normalizedSelector) {
                return Promise.resolve(null);
            }

            const shouldRequireVisible = requireVisible !== false;
            return this.waitForElement(() => {
                const element = this.queryDocumentSelector(normalizedSelector);
                if (!element) {
                    return null;
                }

                if (!shouldRequireVisible) {
                    return element;
                }

                return this.isElementVisible(element) ? element : null;
            }, timeoutMs);
        }

        waitForAnyDocumentSelector(selectors, timeoutMs, requireVisible) {
            const normalizedSelectors = (Array.isArray(selectors) ? selectors : [])
                .map((selector) => typeof selector === 'string' ? selector.trim() : '')
                .filter(Boolean);
            if (normalizedSelectors.length === 0) {
                return Promise.resolve(null);
            }

            const shouldRequireVisible = requireVisible !== false;
            return this.waitForElement(() => {
                for (let index = 0; index < normalizedSelectors.length; index += 1) {
                    const element = this.queryDocumentSelector(normalizedSelectors[index]);
                    if (!element) {
                        continue;
                    }

                    if (!shouldRequireVisible || this.isElementVisible(element)) {
                        return element;
                    }
                }

                return null;
            }, timeoutMs);
        }

        waitForVisibleTarget(targets, timeoutMs) {
            const normalizedTargets = Array.isArray(targets) ? targets.slice() : [];
            if (normalizedTargets.length === 0) {
                return Promise.resolve(null);
            }

            return this.waitForElement(() => {
                for (let index = 0; index < normalizedTargets.length; index += 1) {
                    const target = normalizedTargets[index];
                    let element = null;

                    if (typeof target === 'function') {
                        try {
                            element = target.call(this);
                        } catch (error) {
                            console.warn('[YuiGuide] 解析目标元素失败:', error);
                            element = null;
                        }
                    } else if (typeof target === 'string') {
                        element = this.queryDocumentSelector(target);
                    }

                    if (this.isElementVisible(element)) {
                        return element;
                    }
                }

                return null;
            }, timeoutMs);
        }

        waitForStableElementRect(element, timeoutMs) {
            const normalizedTimeoutMs = Number.isFinite(timeoutMs) ? timeoutMs : 900;
            if (!element) {
                return Promise.resolve(null);
            }

            return new Promise((resolve) => {
                const startedAt = Date.now();
                let lastRect = null;
                let stableCount = 0;

                const tick = () => {
                    if (this.destroyed) {
                        resolve(null);
                        return;
                    }

                    if (!this.isElementVisible(element)) {
                        if ((Date.now() - startedAt) >= normalizedTimeoutMs) {
                            resolve(null);
                            return;
                        }
                        window.setTimeout(tick, 80);
                        return;
                    }

                    const rect = element.getBoundingClientRect();
                    if (!rect || rect.width <= 0 || rect.height <= 0) {
                        if ((Date.now() - startedAt) >= normalizedTimeoutMs) {
                            resolve(null);
                            return;
                        }
                        window.setTimeout(tick, 80);
                        return;
                    }

                    if (lastRect) {
                        const delta = Math.max(
                            Math.abs(rect.left - lastRect.left),
                            Math.abs(rect.top - lastRect.top),
                            Math.abs(rect.width - lastRect.width),
                            Math.abs(rect.height - lastRect.height)
                        );
                        stableCount = delta <= 1 ? (stableCount + 1) : 0;
                    }
                    lastRect = {
                        left: rect.left,
                        top: rect.top,
                        width: rect.width,
                        height: rect.height
                    };

                    if (stableCount >= 2) {
                        resolve(element);
                        return;
                    }

                    if ((Date.now() - startedAt) >= normalizedTimeoutMs) {
                        resolve(element);
                        return;
                    }

                    window.setTimeout(tick, 80);
                };

                tick();
            });
        }

        getChatIntroTarget() {
            return this.getChatInputTarget() || this.getChatWindowTarget();
        }

        getChatInputTarget() {
            const preferredSelectors = [
                '#react-chat-window-root .composer-input',
                '#react-chat-window-root .composer-input-shell',
                '#react-chat-window-root .composer-panel',
                '#text-input-area'
            ];

            for (let index = 0; index < preferredSelectors.length; index += 1) {
                const element = this.resolveElement(preferredSelectors[index]);
                if (!element) {
                    continue;
                }

                const rect = typeof element.getBoundingClientRect === 'function'
                    ? element.getBoundingClientRect()
                    : null;
                if (!rect || rect.width <= 0 || rect.height <= 0) {
                    continue;
                }

                return element;
            }

            return null;
        }

        getChatWindowTarget() {
            const preferredSelectors = [
                '#react-chat-window-shell',
                '#react-chat-window-root .chat-window',
                '#react-chat-window-root',
                '#react-chat-window-root .composer-input-shell',
                '#react-chat-window-root .composer-panel',
                '#react-chat-window-root .composer-input',
                '#text-input-area'
            ];

            for (let index = 0; index < preferredSelectors.length; index += 1) {
                const element = this.resolveElement(preferredSelectors[index]);
                if (!element) {
                    continue;
                }

                const rect = typeof element.getBoundingClientRect === 'function'
                    ? element.getBoundingClientRect()
                    : null;
                if (!rect || rect.width <= 0 || rect.height <= 0) {
                    continue;
                }

                return element;
            }

            return null;
        }

        shouldNarrateInChat(stepId) {
            return this.page === 'home' && typeof stepId === 'string' && !!stepId;
        }

        getSceneSpotlightTarget(stepId, performance) {
            const selector = (performance && (performance.cursorTarget || this.currentStep && this.currentStep.anchor))
                || (this.currentStep && this.currentStep.anchor)
                || '';
            const fallbackTarget = selector ? this.resolveElement(selector) : null;
            if (this.page !== 'home') {
                return fallbackTarget;
            }

            if (stepId === 'intro_basic' && !this.introFlowCompleted) {
                return this.getChatInputTarget() || null;
            }

            if (stepId === 'takeover_settings_peek') {
                return this.getChatWindowTarget() || this.getChatInputTarget() || null;
            }

            if (this.shouldNarrateInChat(stepId)) {
                return this.getChatWindowTarget() || fallbackTarget;
            }

            return fallbackTarget;
        }

        getActionSpotlightTarget(stepId, performance) {
            const selector = (performance && (performance.cursorTarget || this.currentStep && this.currentStep.anchor))
                || (this.currentStep && this.currentStep.anchor)
                || '';
            const fallbackTarget = selector ? this.resolveElement(selector) : null;
            if (this.page !== 'home') {
                return fallbackTarget;
            }

            if (stepId === 'takeover_capture_cursor' || stepId === 'takeover_plugin_preview') {
                return fallbackTarget;
            }

            if (stepId === 'takeover_settings_peek') {
                const settingsMenuId = this.normalizeSettingsMenuId((performance && performance.settingsMenuId) || 'character');
                if (this.isManagedPanelVisible('settings')) {
                    return this.getSettingsMenuElement(settingsMenuId)
                        || this.getManagedPanelElement('settings')
                        || fallbackTarget;
                }

                return fallbackTarget;
            }

            return null;
        }

        highlightChatInput() {
            this.focusAndHighlightChatInput(this.getChatInputTarget());
        }

        highlightChatWindow() {
            const target = this.getChatWindowTarget() || this.getChatInputTarget();
            if (!target) {
                return;
            }

            if (typeof target.scrollIntoView === 'function') {
                try {
                    target.scrollIntoView({
                        behavior: 'smooth',
                        block: 'center',
                        inline: 'nearest'
                    });
                } catch (_) {
                    target.scrollIntoView();
                }
            }

            this.overlay.setPersistentSpotlight(target);
        }

        getChatIntroActivationTarget() {
            const preferredSelectors = [
                '#react-chat-window-root .composer-input-shell',
                '#react-chat-window-root .composer-panel',
                '#react-chat-window-root .composer-input',
                '#text-input-area'
            ];

            for (let index = 0; index < preferredSelectors.length; index += 1) {
                const element = this.resolveElement(preferredSelectors[index]);
                if (!element) {
                    continue;
                }

                const rect = typeof element.getBoundingClientRect === 'function'
                    ? element.getBoundingClientRect()
                    : null;
                if (!rect || rect.width <= 0 || rect.height <= 0) {
                    continue;
                }

                return element;
            }

            return this.getChatIntroTarget();
        }

        clearSceneTimers() {
            this.sceneTimers.forEach(function (timerId) {
                window.clearTimeout(timerId);
            });
            this.sceneTimers.clear();
        }

        schedule(callback, delayMs) {
            const timerId = window.setTimeout(() => {
                this.sceneTimers.delete(timerId);
                callback();
            }, delayMs);
            this.sceneTimers.add(timerId);
            return timerId;
        }

        clearNarrationResumeTimer() {
            if (this.narrationResumeTimer) {
                window.clearTimeout(this.narrationResumeTimer);
                this.narrationResumeTimer = null;
            }
        }

        cancelActiveNarration() {
            const narration = this.activeNarration;
            this.activeNarration = null;
            this.clearNarrationResumeTimer();

            if (!narration) {
                return;
            }

            narration.cancelled = true;
            this.voiceQueue.stop();
            if (typeof narration.resolve === 'function') {
                narration.resolve();
            }
        }

        async runNarration(narration) {
            if (!narration || narration.cancelled || this.destroyed) {
                return;
            }

            if (narration.running) {
                return;
            }

            const playbackStartIndex = clamp(
                Number.isFinite(narration.resumeIndex) ? narration.resumeIndex : 0,
                0,
                narration.text.length
            );
            const playbackText = narration.text.slice(playbackStartIndex);

            if (!playbackText.trim()) {
                narration.resumeIndex = narration.text.length;
                if (this.activeNarration === narration) {
                    this.activeNarration = null;
                }
                if (typeof narration.resolve === 'function') {
                    narration.resolve();
                }
                return;
            }

            narration.running = true;
            narration.playbackStartIndex = playbackStartIndex;
            narration.playbackStartAt = Date.now();
            await this.voiceQueue.speak(playbackText, {
                minDurationMs: Number.isFinite(narration.minDurationMs)
                    ? narration.minDurationMs
                    : 0,
                onBoundary: (event) => {
                    const charIndex = event && Number.isFinite(event.charIndex) ? event.charIndex : 0;
                    const absoluteCharIndex = clamp(
                        narration.playbackStartIndex + charIndex,
                        narration.playbackStartIndex,
                        narration.text.length
                    );
                    narration.resumeIndex = absoluteCharIndex;
                    if (typeof narration.onBoundary === 'function') {
                        try {
                            narration.onBoundary(Object.assign({}, event, {
                                absoluteCharIndex: absoluteCharIndex,
                                fullText: narration.text
                            }));
                        } catch (error) {
                            console.warn('[YuiGuide] 旁白边界扩展回调失败:', error);
                        }
                    }
                }
            });
            narration.running = false;

            if (this.destroyed || narration.cancelled) {
                if (this.activeNarration === narration) {
                    this.activeNarration = null;
                }
                if (typeof narration.resolve === 'function') {
                    narration.resolve();
                }
                return;
            }

            if (narration.interrupted) {
                return;
            }

            narration.resumeIndex = narration.text.length;
            if (this.activeNarration === narration) {
                this.activeNarration = null;
            }
            if (typeof narration.resolve === 'function') {
                narration.resolve();
            }
        }

        async speakLineAndWait(text, options) {
            const content = typeof text === 'string' ? text.trim() : '';
            if (!content || this.destroyed) {
                return;
            }

            this.cancelActiveNarration();
            const normalizedOptions = options || {};

            await new Promise((resolve) => {
                const narration = {
                    text: content,
                    resumeIndex: 0,
                    playbackStartIndex: 0,
                    playbackStartAt: 0,
                    minDurationMs: Number.isFinite(normalizedOptions.minDurationMs)
                        ? normalizedOptions.minDurationMs
                        : 0,
                    onBoundary: typeof normalizedOptions.onBoundary === 'function' ? normalizedOptions.onBoundary : null,
                    resolve: resolve,
                    interrupted: false,
                    cancelled: false,
                    running: false
                };
                this.activeNarration = narration;
                this.runNarration(narration).catch((error) => {
                    console.warn('[YuiGuide] 等待语音结束失败:', error);
                    if (this.activeNarration === narration) {
                        this.activeNarration = null;
                    }
                    resolve();
                });
            });
        }

        interruptNarrationForResistance() {
            const narration = this.activeNarration;
            if (!narration || narration.cancelled) {
                return false;
            }

            if (narration.running) {
                const playbackStartIndex = Number.isFinite(narration.playbackStartIndex) ? narration.playbackStartIndex : 0;
                const playbackStartAt = Number.isFinite(narration.playbackStartAt) ? narration.playbackStartAt : 0;
                const elapsedMs = playbackStartAt > 0 ? Math.max(0, Date.now() - playbackStartAt) : 0;
                const estimatedChars = Math.floor(elapsedMs / 280);
                const estimatedIndex = clamp(
                    playbackStartIndex + estimatedChars,
                    playbackStartIndex,
                    narration.text.length
                );
                narration.resumeIndex = Math.max(
                    Number.isFinite(narration.resumeIndex) ? narration.resumeIndex : playbackStartIndex,
                    estimatedIndex
                );
            }

            narration.interrupted = true;
            this.clearNarrationResumeTimer();
            this.voiceQueue.stop();
            return true;
        }

        scheduleNarrationResume() {
            this.clearNarrationResumeTimer();

            const attemptResume = () => {
                const narration = this.activeNarration;
                if (!narration || narration.cancelled || this.destroyed) {
                    this.restoreCurrentScenePresentation();
                    return;
                }

                if (!narration.interrupted) {
                    return;
                }

                const lastMotionAt = this.lastPointerPoint && Number.isFinite(this.lastPointerPoint.t)
                    ? this.lastPointerPoint.t
                    : 0;
                if ((Date.now() - lastMotionAt) < 720) {
                    this.narrationResumeTimer = window.setTimeout(attemptResume, 240);
                    return;
                }

                narration.interrupted = false;
                this.restoreCurrentScenePresentation();
                this.runNarration(narration).catch((error) => {
                    console.warn('[YuiGuide] 恢复教程语音失败:', error);
                });
            };

            this.narrationResumeTimer = window.setTimeout(attemptResume, 720);
        }

        setCurrentScene(stepId, context) {
            this.currentSceneId = stepId || null;
            this.currentStep = stepId ? this.getStep(stepId) : null;
            this.currentContext = context || null;
        }

        restoreCurrentScenePresentation() {
            if (this.destroyed || this.angryExitTriggered || !this.currentStep) {
                return;
            }

            const performance = this.currentStep.performance || {};
            const spotlightTarget = this.getSceneSpotlightTarget(this.currentSceneId, performance);
            if (spotlightTarget) {
                this.overlay.setPersistentSpotlight(spotlightTarget);
            } else {
                this.overlay.clearPersistentSpotlight();
            }

            const actionSpotlightTarget = this.getActionSpotlightTarget(this.currentSceneId, performance);
            if (actionSpotlightTarget) {
                this.overlay.activateSpotlight(actionSpotlightTarget);
            } else {
                this.overlay.clearActionSpotlight();
            }

            if (this.customSecondarySpotlightTarget) {
                this.overlay.activateSecondarySpotlight(this.customSecondarySpotlightTarget);
            }

            if (this.shouldNarrateInChat(this.currentSceneId)) {
                this.overlay.hideBubble();
            } else if (performance.bubbleText) {
                this.overlay.showBubble(performance.bubbleText, {
                    title: 'Yui',
                    emotion: performance.emotion || 'neutral',
                    anchorRect: this.resolveRect(this.currentStep.anchor)
                });
            } else {
                this.overlay.hideBubble();
            }

            if (performance.emotion) {
                this.emotionBridge.apply(performance.emotion);
            }
        }

        async playManagedScene(stepId, meta) {
            this.setCurrentScene(stepId, meta && meta.context ? meta.context : null);
            await this.playScene(stepId, meta || {});
        }

        disableInterrupts() {
            if (!this.interruptsEnabled) {
                return;
            }

            window.removeEventListener('mousemove', this.pointerMoveHandler, true);
            window.removeEventListener('mousedown', this.pointerDownHandler, true);
            this.interruptsEnabled = false;
            this.lastPointerPoint = null;
            this.interruptAccelerationStreak = 0;
            this.lastPassiveResistanceAt = 0;
        }

        enableInterrupts(step) {
            const performance = (step && step.performance) || {};
            const interrupts = (step && step.interrupts) || {};
            if (performance.interruptible === false) {
                this.disableInterrupts();
                return;
            }

            this.disableInterrupts();
            if (interrupts.resetOnStepAdvance !== false) {
                this.interruptCount = 0;
            }
            this.interruptAccelerationStreak = 0;
            this.lastInterruptAt = 0;
            this.lastPassiveResistanceAt = 0;
            this.lastPointerPoint = null;
            window.addEventListener('mousemove', this.pointerMoveHandler, true);
            window.addEventListener('mousedown', this.pointerDownHandler, true);
            this.interruptsEnabled = true;
        }

        maybePlayPassiveResistance(x, y, distance, speed, now) {
            if (!this.cursor.hasPosition()) {
                return;
            }

            if (distance < DEFAULT_PASSIVE_RESISTANCE_DISTANCE) {
                return;
            }

            if (speed < 0.2) {
                return;
            }

            if (now - this.lastPassiveResistanceAt < DEFAULT_PASSIVE_RESISTANCE_INTERVAL_MS) {
                return;
            }

            this.lastPassiveResistanceAt = now;
            this.cursor.reactToUserMotion(x, y, {
                scale: 0.16,
                outDurationMs: 90,
                backDurationMs: 180
            });
        }

        // Dev B boundary: Director only talks to this API surface.
        // Dev C can later provide a real implementation via options.homeInteractionApi,
        // window.getYuiGuideHomeInteractionApi(), window.YuiGuideHomeInteractionApi,
        // or the broader window.YuiGuidePageHandoff module.
        getHomeInteractionApi() {
            if (this.options && this.options.homeInteractionApi) {
                return this.options.homeInteractionApi;
            }

            if (typeof window.getYuiGuideHomeInteractionApi === 'function') {
                try {
                    return window.getYuiGuideHomeInteractionApi() || null;
                } catch (error) {
                    console.warn('[YuiGuide] 获取首页交互 API 失败:', error);
                }
            }

            return window.YuiGuideHomeInteractionApi || window.YuiGuidePageHandoff || null;
        }

        async callHomeInteractionApi(methodName, args, fallback) {
            const api = this.getHomeInteractionApi();
            if (api && typeof api[methodName] === 'function') {
                try {
                    return !!(await api[methodName].apply(api, Array.isArray(args) ? args : []));
                } catch (error) {
                    console.warn('[YuiGuide] 首页交互 API 调用失败，回退到本地实现:', methodName, error);
                }
            }

            if (typeof fallback === 'function') {
                return !!(await fallback());
            }

            return false;
        }

        getManagedPanelElement(panelId) {
            if (!panelId) {
                return null;
            }

            return document.getElementById(this.resolveModelPrefix() + '-popup-' + panelId);
        }

        isManagedPanelVisible(panelId) {
            const popup = this.getManagedPanelElement(panelId);
            return !!(popup && popup.style.display === 'flex' && popup.style.opacity !== '0');
        }

        getFallbackFloatingButton(buttonId) {
            if (!buttonId) {
                return null;
            }

            return this.resolveElement('#${p}-btn-' + buttonId);
        }

        async setFallbackFloatingPopupVisible(buttonId, visible) {
            const desiredVisible = !!visible;
            if (this.isManagedPanelVisible(buttonId) === desiredVisible) {
                return desiredVisible;
            }

            const button = this.getFallbackFloatingButton(buttonId);
            if (!button || typeof button.click !== 'function') {
                return this.isManagedPanelVisible(buttonId) === desiredVisible;
            }

            button.click();

            const result = await this.waitForElement(() => {
                const popup = this.getManagedPanelElement(buttonId);
                const isVisible = this.isManagedPanelVisible(buttonId);
                return isVisible === desiredVisible ? (popup || button) : null;
            }, 1200);

            return !!result && this.isManagedPanelVisible(buttonId) === desiredVisible;
        }

        async openAgentPanel() {
            return this.callHomeInteractionApi('openAgentPanel', [], () => {
                return this.setFallbackFloatingPopupVisible('agent', true);
            });
        }

        async closeAgentPanel() {
            return this.callHomeInteractionApi('closeAgentPanel', [], () => {
                return this.setFallbackFloatingPopupVisible('agent', false);
            });
        }

        async ensureAgentToggleChecked(toggleId, checked) {
            return this.callHomeInteractionApi('ensureAgentToggleChecked', [toggleId, checked], async () => {
                const panelReady = await this.openAgentPanel();
                if (!panelReady) {
                    return false;
                }

                const checkbox = await this.waitForElement(() => {
                    const input = this.getAgentToggleCheckbox(toggleId);
                    return input && !input.disabled ? input : null;
                }, 5000);
                const toggleItem = this.getAgentToggleElement(toggleId);
                if (!checkbox || !toggleItem) {
                    return false;
                }

                const desiredChecked = checked !== false;
                if (!!checkbox.checked === desiredChecked) {
                    return true;
                }

                toggleItem.click();
                const result = await this.waitForElement(() => {
                    return !!checkbox.checked === desiredChecked ? checkbox : null;
                }, 1500);
                return !!result;
            });
        }

        async ensureAgentSidePanelVisible(toggleId) {
            return this.callHomeInteractionApi('ensureAgentSidePanelVisible', [toggleId], async () => {
                const panelReady = await this.openAgentPanel();
                if (!panelReady) {
                    return false;
                }

                const toggleItem = this.getAgentToggleElement(toggleId);
                const sidePanel = this.getAgentSidePanel(toggleId);
                if (!toggleItem || !sidePanel) {
                    return false;
                }

                if (typeof sidePanel._expand === 'function') {
                    if (sidePanel._hoverCollapseTimer) {
                        window.clearTimeout(sidePanel._hoverCollapseTimer);
                        sidePanel._hoverCollapseTimer = null;
                    }
                    sidePanel._expand();
                } else {
                    toggleItem.dispatchEvent(new MouseEvent('mouseenter', {
                        bubbles: true,
                        cancelable: true,
                        view: window
                    }));
                }

                try {
                    toggleItem.dispatchEvent(new MouseEvent('mouseenter', {
                        bubbles: true,
                        cancelable: true,
                        view: window
                    }));
                    sidePanel.dispatchEvent(new MouseEvent('mouseenter', {
                        bubbles: true,
                        cancelable: true,
                        view: window
                    }));
                } catch (_) {}

                const result = await this.waitForElement(() => {
                    return this.isAgentSidePanelVisible(toggleId) ? sidePanel : null;
                }, 1500);
                return !!result;
            });
        }

        async waitForAgentSidePanelActionVisible(toggleId, actionId, timeoutMs) {
            const normalizedTimeoutMs = Number.isFinite(timeoutMs) ? timeoutMs : 1800;
            const sidePanelReady = await this.ensureAgentSidePanelVisible(toggleId);
            if (!sidePanelReady) {
                return null;
            }

            return this.waitForVisibleElement(() => {
                const button = this.getAgentSidePanelButton(toggleId, actionId);
                if (!button || !this.isAgentSidePanelVisible(toggleId)) {
                    return null;
                }
                return button;
            }, normalizedTimeoutMs);
        }

        async ensureAgentSidePanelActionVisible(toggleId, actionId, timeoutMs) {
            const normalizedTimeoutMs = Number.isFinite(timeoutMs) ? timeoutMs : 1800;
            const api = this.getHomeInteractionApi();
            if (api && typeof api.ensureAgentSidePanelActionVisible === 'function') {
                try {
                    return await api.ensureAgentSidePanelActionVisible(toggleId, actionId, normalizedTimeoutMs);
                } catch (error) {
                    console.warn('[YuiGuide] ensureAgentSidePanelActionVisible 调用失败，改用本地兜底:', error);
                }
            }

            return this.waitForAgentSidePanelActionVisible(toggleId, actionId, normalizedTimeoutMs);
        }

        async waitForAgentToggleState(toggleId, checked, timeoutMs) {
            const desiredChecked = checked !== false;
            return this.waitForElement(() => {
                const checkbox = this.getAgentToggleCheckbox(toggleId);
                if (!checkbox) {
                    return null;
                }
                return !!checkbox.checked === desiredChecked ? checkbox : null;
            }, Number.isFinite(timeoutMs) ? timeoutMs : 1800);
        }

        async clickAgentSidePanelAction(toggleId, actionId) {
            return this.callHomeInteractionApi('clickAgentSidePanelAction', [toggleId, actionId], async () => {
                const button = await this.waitForAgentSidePanelActionVisible(toggleId, actionId, 1800);
                if (!button || typeof button.click !== 'function') {
                    return false;
                }

                button.click();
                return true;
            });
        }

        async openSettingsPanel() {
            return this.callHomeInteractionApi('openSettingsPanel', [], () => {
                return this.setFallbackFloatingPopupVisible('settings', true);
            });
        }

        async closeSettingsPanel() {
            return this.callHomeInteractionApi('closeSettingsPanel', [], () => {
                return this.setFallbackFloatingPopupVisible('settings', false);
            });
        }

        normalizeSettingsMenuId(menuId) {
            const normalized = typeof menuId === 'string'
                ? menuId.trim().toLowerCase().replace(/[^a-z0-9_-]/g, '-')
                : '';
            return normalized || '';
        }

        getSettingsMenuSelector(menuId) {
            const normalizedMenuId = this.normalizeSettingsMenuId(menuId);
            if (!normalizedMenuId) {
                return '';
            }

            return '#' + this.resolveModelPrefix() + '-menu-' + normalizedMenuId;
        }

        getSettingsMenuElement(menuId) {
            const selector = this.getSettingsMenuSelector(menuId);
            if (!selector) {
                return null;
            }

            return this.resolveElement(selector);
        }

        async ensureSettingsMenuVisible(menuId) {
            return this.callHomeInteractionApi('ensureSettingsMenuVisible', [menuId], async () => {
                const panelReady = await this.openSettingsPanel();
                if (!panelReady) {
                    return false;
                }

                if (!menuId) {
                    return true;
                }

                const selector = this.getSettingsMenuSelector(menuId);
                if (!selector) {
                    return false;
                }

                const menuLabel = await this.waitForElement(() => this.resolveElement(selector), 1200);
                if (!menuLabel) {
                    return false;
                }

                const menuItem = menuLabel.closest('.' + this.resolveModelPrefix() + '-settings-menu-item') || menuLabel.parentElement;
                if (menuItem && typeof menuItem.scrollIntoView === 'function') {
                    try {
                        menuItem.scrollIntoView({
                            behavior: 'smooth',
                            block: 'nearest',
                            inline: 'nearest'
                        });
                    } catch (_) {
                        menuItem.scrollIntoView();
                    }
                }

                return true;
            });
        }

        async closeManagedPanels() {
            const results = await Promise.all([
                this.closeAgentPanel(),
                this.closeSettingsPanel()
            ]);

            return results.every(Boolean);
        }

        async waitForOpenedWindow(windowName, timeoutMs) {
            const api = this.getHomeInteractionApi();
            if (api && typeof api.waitForWindowOpen === 'function') {
                try {
                    return await api.waitForWindowOpen(windowName, timeoutMs);
                } catch (error) {
                    console.warn('[YuiGuide] 等待子窗口打开失败，改用本地兜底:', error);
                }
            }

            const normalizedName = api && typeof api.normalizeWindowName === 'function'
                ? api.normalizeWindowName(windowName)
                : String(windowName || '');
            return this.waitForElement(() => {
                if (!normalizedName) {
                    return null;
                }

                const tracked = window._openedWindows && window._openedWindows[normalizedName];
                return tracked && !tracked.closed ? tracked : null;
            }, timeoutMs || 6000);
        }

        async closeNamedWindow(windowName) {
            const api = this.getHomeInteractionApi();
            if (api && typeof api.closeWindow === 'function') {
                try {
                    return !!(await api.closeWindow(windowName));
                } catch (error) {
                    console.warn('[YuiGuide] 关闭子窗口失败，改用本地兜底:', error);
                }
            }

            const normalizedName = api && typeof api.normalizeWindowName === 'function'
                ? api.normalizeWindowName(windowName)
                : String(windowName || '');
            const target = normalizedName && window._openedWindows
                ? window._openedWindows[normalizedName]
                : null;
            if (!target) {
                return true;
            }

            try {
                target.close();
                delete window._openedWindows[normalizedName];
                return true;
            } catch (error) {
                console.warn('[YuiGuide] 本地关闭子窗口失败:', error);
                return false;
            }
        }

        async setAgentMasterEnabled(enabled) {
            return this.callHomeInteractionApi('setAgentMasterEnabled', [enabled], async () => {
                const response = await fetch('/api/agent/command', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        request_id: Date.now() + '-' + Math.random().toString(36).slice(2, 8),
                        command: 'set_agent_enabled',
                        enabled: !!enabled
                    })
                });
                if (!response.ok) {
                    return false;
                }

                const data = await response.json();
                return !!(data && data.success === true);
            });
        }

        async setAgentFlagEnabled(flagKey, enabled) {
            return this.callHomeInteractionApi('setAgentFlagEnabled', [flagKey, enabled], async () => {
                const response = await fetch('/api/agent/command', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        request_id: Date.now() + '-' + Math.random().toString(36).slice(2, 8),
                        command: 'set_flag',
                        key: flagKey,
                        value: !!enabled
                    })
                });
                if (!response.ok) {
                    return false;
                }

                const data = await response.json();
                return !!(data && data.success === true);
            });
        }

        async openPluginDashboardWindow() {
            const api = this.getHomeInteractionApi();
            if (api && typeof api.openPluginDashboard === 'function') {
                try {
                    return await api.openPluginDashboard();
                } catch (error) {
                    console.warn('[YuiGuide] openPluginDashboard 失败，改用本地兜底:', error);
                }
            }

            if (api && typeof api.openPage === 'function') {
                try {
                    return await api.openPage('/api/agent/user_plugin/dashboard', 'plugin_dashboard');
                } catch (error) {
                    console.warn('[YuiGuide] openPage(plugin_dashboard) 失败:', error);
                }
            }

            return null;
        }

        async openModelManagerPage(lanlanName) {
            const api = this.getHomeInteractionApi();
            const targetLanlanName = typeof lanlanName === 'string' && lanlanName.trim()
                ? lanlanName.trim()
                : this.getTutorialModelManagerLanlanName();
            if (api && typeof api.openModelManagerPage === 'function') {
                try {
                    return await api.openModelManagerPage(targetLanlanName);
                } catch (error) {
                    console.warn('[YuiGuide] openModelManagerPage 失败，改用本地兜底:', error);
                }
            }

            const appearanceMenuId = this.getCharacterAppearanceMenuId();
            const windowName = this.getModelManagerWindowName(targetLanlanName, appearanceMenuId);
            if (api && typeof api.openPage === 'function') {
                try {
                    return await api.openPage(
                        '/model_manager?lanlan_name=' + encodeURIComponent(targetLanlanName),
                        windowName
                    );
                } catch (error) {
                    console.warn('[YuiGuide] openPage(model_manager) 失败:', error);
                }
            }

            return null;
        }

        async performCaptureCursorPrelude(durationMs) {
            const totalDurationMs = Number.isFinite(durationMs) ? Math.max(600, durationMs) : 2000;
            const origin = this.cursor.hasPosition()
                ? this.overlay.getCursorPosition()
                : this.getDefaultCursorOrigin();
            if (!origin) {
                return;
            }

            if (!this.cursor.hasPosition()) {
                this.cursor.showAt(origin.x, origin.y);
                await wait(120);
            }

            const points = [
                { x: origin.x - 60, y: origin.y - 36 },
                { x: origin.x + 54, y: origin.y - 24 },
                { x: origin.x + 42, y: origin.y + 48 },
                { x: origin.x - 48, y: origin.y + 36 },
                { x: origin.x, y: origin.y }
            ];
            const segmentDurationMs = Math.max(180, Math.round(totalDurationMs / points.length));

            for (let index = 0; index < points.length; index += 1) {
                const point = points[index];
                await this.cursor.moveToPoint(point.x, point.y, { durationMs: segmentDurationMs });
                if (this.destroyed || this.angryExitTriggered) {
                    return;
                }
                this.cursor.wobble();
                await wait(60);
            }
        }

        async moveCursorToElement(element, durationMs) {
            const rect = this.getElementRect(element);
            if (!rect) {
                return false;
            }

            await this.cursor.moveToRect(rect, {
                durationMs: Number.isFinite(durationMs) ? durationMs : DEFAULT_CURSOR_DURATION_MS
            });
            return true;
        }

        async resolveElementCenterPoint(element, timeoutMs) {
            const normalizedTimeoutMs = Number.isFinite(timeoutMs) ? timeoutMs : 800;
            const startedAt = Date.now();

            while ((Date.now() - startedAt) < normalizedTimeoutMs) {
                if (this.destroyed || this.angryExitTriggered) {
                    return null;
                }

                const rect = this.getElementRect(element);
                if (rect) {
                    return {
                        x: rect.left + (rect.width / 2),
                        y: rect.top + (rect.height / 2),
                        rect: rect
                    };
                }

                await wait(80);
            }

            const finalRect = this.getElementRect(element);
            if (!finalRect) {
                return null;
            }

            return {
                x: finalRect.left + (finalRect.width / 2),
                y: finalRect.top + (finalRect.height / 2),
                rect: finalRect
            };
        }

        async moveCursorToTrackedElement(element, durationMs, options) {
            const normalizedOptions = options || {};
            const totalDurationMs = Number.isFinite(durationMs) ? durationMs : DEFAULT_CURSOR_DURATION_MS;
            const firstLegMs = Math.max(180, Math.round(totalDurationMs * 0.7));
            const secondLegMs = Math.max(140, totalDurationMs - firstLegMs);
            const recheckDelayMs = Number.isFinite(normalizedOptions.recheckDelayMs)
                ? normalizedOptions.recheckDelayMs
                : 320;
            const settleDelayMs = Number.isFinite(normalizedOptions.settleDelayMs)
                ? normalizedOptions.settleDelayMs
                : 0;

            const initialPoint = await this.resolveElementCenterPoint(element, 420);
            if (!initialPoint) {
                return false;
            }
            await this.cursor.moveToPoint(initialPoint.x, initialPoint.y, {
                durationMs: firstLegMs
            });

            if (settleDelayMs > 0) {
                await wait(settleDelayMs);
            }
            if (recheckDelayMs > 0) {
                await wait(recheckDelayMs);
            }
            if (this.destroyed || this.angryExitTriggered) {
                return false;
            }

            const finalPoint = await this.resolveElementCenterPoint(element, 420);
            if (!finalPoint) {
                return false;
            }

            await this.cursor.moveToPoint(finalPoint.x, finalPoint.y, {
                durationMs: secondLegMs
            });
            return true;
        }

        async clickCursorAndWait(holdMs) {
            this.cursor.click();
            await wait(Number.isFinite(holdMs) ? holdMs : 180);
        }

        hoverElement(element) {
            if (!element) {
                return;
            }

            try {
                element.dispatchEvent(new MouseEvent('mouseenter', {
                    bubbles: true,
                    cancelable: true,
                    view: window
                }));
                element.dispatchEvent(new MouseEvent('mouseover', {
                    bubbles: true,
                    cancelable: true,
                    view: window
                }));
            } catch (_) {}
        }

        getVisibleHomeModelElement() {
            const candidates = [
                document.getElementById('live2d-container'),
                document.getElementById('vrm-container'),
                document.getElementById('mmd-container')
            ];

            for (let index = 0; index < candidates.length; index += 1) {
                const element = candidates[index];
                if (this.isElementVisible(element)) {
                    return element;
                }
            }

            return null;
        }

        async waitForHomeMainUIReady(timeoutMs) {
            if (typeof window.handleShowMainUI === 'function') {
                try {
                    window.handleShowMainUI();
                } catch (error) {
                    console.warn('[YuiGuide] 恢复主界面失败:', error);
                }
            }

            return this.waitForElement(() => {
                const settingsButton = this.getFallbackFloatingButton('settings');
                const modelElement = this.getVisibleHomeModelElement();
                if (this.isElementVisible(settingsButton) && modelElement) {
                    return settingsButton;
                }

                return null;
            }, Number.isFinite(timeoutMs) ? timeoutMs : 3200);
        }

        async performHighlightedApiClick(options) {
            const normalized = options || {};
            const target = normalized.target || null;
            if (!target) {
                return false;
            }

            this.applyGuideHighlights({
                primary: target,
                secondary: normalized.secondary || null
            });
            const moved = await this.moveCursorToElement(target, normalized.durationMs);
            if (!moved) {
                return false;
            }
            if (normalized.runId !== this.sceneRunId || this.destroyed || this.angryExitTriggered) {
                return false;
            }

            this.cursor.click();
            if (typeof normalized.action !== 'function') {
                return true;
            }

            return !!(await normalized.action());
        }

        async runTakeoverCaptureActionSequence(step, performance, runId) {
            this.customSecondarySpotlightTarget = null;
            this.clearPreciseHighlights();
            this.clearSceneExtraSpotlights();
            this.overlay.clearActionSpotlight();
            this.clearRetainedExtraSpotlights();

            const guardFailed = () => {
                return runId !== this.sceneRunId || this.destroyed || this.angryExitTriggered;
            };

            const catPawButtonImage = await this.waitForDocumentSelector(TAKEOVER_CAPTURE_SELECTORS.catPaw, 2200);
            const catPawButton = this.getFloatingButtonShell(catPawButtonImage);
            if (!catPawButton || guardFailed()) {
                return null;
            }

            // 1-3. 高亮猫爪 -> 平滑移动 -> 点击并打开猫爪面板
            this.addRetainedExtraSpotlight(catPawButton);
            const movedToCatPaw = await this.moveCursorToElement(catPawButton, 1500);
            if (!movedToCatPaw || guardFailed()) {
                return null;
            }

            this.cursor.click();
            const agentPanelOpened = await this.openAgentPanel();
            if (!agentPanelOpened || guardFailed()) {
                return null;
            }

            const agentMasterToggle = await this.waitForDocumentSelector(TAKEOVER_CAPTURE_SELECTORS.agentMaster, 4000);
            if (!agentMasterToggle || guardFailed()) {
                return null;
            }

            // 4-6. 高亮猫爪总开关 -> 平滑移动 -> 点击并同步打开
            this.addRetainedExtraSpotlight(agentMasterToggle);
            const movedToAgentMaster = await this.moveCursorToElement(agentMasterToggle, 1200);
            if (!movedToAgentMaster || guardFailed()) {
                return null;
            }

            this.cursor.click();
            const agentMasterEnabled = await this.setAgentMasterEnabled(true);
            if (!agentMasterEnabled || guardFailed()) {
                return null;
            }

            const agentMasterState = await this.waitForAgentToggleState('agent-master', true, 1800);
            if (!agentMasterState || guardFailed()) {
                return null;
            }
            await wait(420);
            if (guardFailed()) {
                return null;
            }

            const pluginToggle = await this.waitForDocumentSelector(TAKEOVER_CAPTURE_SELECTORS.userPlugin, 2200);
            if (!pluginToggle || guardFailed()) {
                return null;
            }

            // 7-9. 高亮用户插件 -> 平滑移动 -> 点击并同步打开
            this.addRetainedExtraSpotlight(pluginToggle);
            const movedToPluginToggle = await this.moveCursorToElement(pluginToggle, 1300);
            if (!movedToPluginToggle || guardFailed()) {
                return null;
            }

            this.cursor.click();
            const pluginToggleEnabled = await this.setAgentFlagEnabled('user_plugin_enabled', true);
            if (!pluginToggleEnabled || guardFailed()) {
                return null;
            }

            const pluginToggleState = await this.waitForAgentToggleState('agent-user-plugin', true, 1800);
            if (!pluginToggleState || guardFailed()) {
                return null;
            }

            await wait(180);

            // 10. 通过悬停让管理面板显现
            this.hoverElement(pluginToggle);

            const managementButton = await this.ensureAgentSidePanelActionVisible(
                'agent-user-plugin',
                'management-panel',
                2600
            );
            if (!managementButton || guardFailed()) {
                console.warn('[YuiGuide] 阶段四未找到管理面板按钮');
                return null;
            }

            const stableManagementButton = await this.waitForStableElementRect(managementButton, 320);
            const managementMovementTarget = stableManagementButton || managementButton;
            if (!managementMovementTarget || guardFailed()) {
                console.warn('[YuiGuide] 阶段四管理面板按钮不可用于移动');
                return null;
            }

            // 11-13. 高亮管理面板 -> 移动到高亮中心点 -> 点击并同步打开真实页面
            this.addRetainedExtraSpotlight(managementButton);
            await wait(60);
            const managementRectBeforeMove = this.getElementRect(managementMovementTarget);
            console.info('[YuiGuide] 阶段四管理面板移动前 rect:', managementRectBeforeMove
                ? {
                    left: Math.round(managementRectBeforeMove.left),
                    top: Math.round(managementRectBeforeMove.top),
                    width: Math.round(managementRectBeforeMove.width),
                    height: Math.round(managementRectBeforeMove.height)
                }
                : null);
            const movedToManagementButton = await this.moveCursorToTrackedElement(managementMovementTarget, 1900, {
                recheckDelayMs: 180
            });
            if (!movedToManagementButton || guardFailed()) {
                console.warn('[YuiGuide] 阶段四管理面板 Ghost Cursor 移动失败');
                return null;
            }

            await wait(90);
            await this.clickCursorAndWait(180);
            const clicked = await this.clickAgentSidePanelAction('agent-user-plugin', 'management-panel');
            if (!clicked && runId === this.sceneRunId && !this.destroyed && !this.angryExitTriggered) {
                const pluginDashboardWindow = await this.openPluginDashboardWindow();
                if (pluginDashboardWindow && !pluginDashboardWindow.closed) {
                    return pluginDashboardWindow;
                }
            }
            return this.waitForOpenedWindow(PLUGIN_DASHBOARD_WINDOW_NAME, 6000);
        }

        waitForPluginDashboardPerformance(windowRef, payload) {
            if (!windowRef || windowRef.closed) {
                return Promise.resolve(false);
            }

            if (this.pluginDashboardHandoff && typeof this.pluginDashboardHandoff.reject === 'function') {
                this.pluginDashboardHandoff.reject(new Error('plugin-dashboard handoff superseded'));
            }

            return new Promise((resolve, reject) => {
                const sessionId = 'plugin-dashboard-' + Date.now() + '-' + Math.random().toString(36).slice(2, 8);
                const startedAt = Date.now();
                const preloadTimeoutMs = 15000;
                const executionTimeoutMs = clamp(
                    estimateSpeechDurationMs(payload && payload.line ? payload.line : '') + 12000,
                    12000,
                    42000
                );
                const handoff = {
                    sessionId: sessionId,
                    windowRef: windowRef,
                    ready: false,
                    readyAt: 0,
                    resolve: (result) => {
                        if (this.pluginDashboardHandoff !== handoff) {
                            return;
                        }
                        if (handoff.intervalId) {
                            window.clearInterval(handoff.intervalId);
                            handoff.intervalId = 0;
                        }
                        if (handoff.timeoutId) {
                            window.clearTimeout(handoff.timeoutId);
                            handoff.timeoutId = 0;
                        }
                        this.pluginDashboardHandoff = null;
                        resolve(result);
                    },
                    reject: (error) => {
                        if (this.pluginDashboardHandoff !== handoff) {
                            return;
                        }
                        if (handoff.intervalId) {
                            window.clearInterval(handoff.intervalId);
                            handoff.intervalId = 0;
                        }
                        if (handoff.timeoutId) {
                            window.clearTimeout(handoff.timeoutId);
                            handoff.timeoutId = 0;
                        }
                        this.pluginDashboardHandoff = null;
                        reject(error);
                    },
                    post: () => {
                        if (!windowRef || windowRef.closed) {
                            handoff.resolve(false);
                            return;
                        }
                        try {
                            windowRef.postMessage({
                                type: PLUGIN_DASHBOARD_HANDOFF_EVENT,
                                sessionId: sessionId,
                                payload: payload || {}
                            }, '*');
                        } catch (error) {
                            console.warn('[YuiGuide] 向插件面板发送 handoff 消息失败:', error);
                        }
                    }
                };

                handoff.intervalId = window.setInterval(() => {
                    if (!windowRef || windowRef.closed) {
                        handoff.resolve(false);
                        return;
                    }

                    if (!handoff.ready && (Date.now() - startedAt) >= preloadTimeoutMs) {
                        handoff.resolve(false);
                        return;
                    }

                    if (handoff.ready && handoff.readyAt > 0 && (Date.now() - handoff.readyAt) >= executionTimeoutMs) {
                        handoff.resolve(false);
                        return;
                    }
                    handoff.post();
                }, 450);
                handoff.timeoutId = window.setTimeout(() => {
                    handoff.resolve(false);
                }, preloadTimeoutMs + executionTimeoutMs);

                this.pluginDashboardHandoff = handoff;
                handoff.post();
            });
        }

        async runPluginDashboardPreviewScene(step, runId) {
            this.highlightChatWindow();
            if (step && step.performance && step.performance.bubbleText) {
                this.appendGuideChatMessage(step.performance.bubbleText);
            }
            await this.ensureAgentSidePanelVisible('agent-user-plugin');
            const pluginToggle = this.getAgentToggleElement('agent-user-plugin');
            const managementButton = await this.waitForVisibleElement(
                () => this.getAgentSidePanelButton('agent-user-plugin', 'management-panel'),
                1800
            );
            this.clearSceneExtraSpotlights();
            if (managementButton) {
                this.replaceRetainedExtraSpotlight(
                    (element) => !!(element && element.id === 'neko-sidepanel-action-agent-user-plugin-management-panel'),
                    managementButton
                );
            } else if (pluginToggle) {
                this.setSceneExtraSpotlights([pluginToggle]);
            }

            let dashboardWindow = await this.waitForOpenedWindow(PLUGIN_DASHBOARD_WINDOW_NAME, 2200);
            if (!dashboardWindow && runId === this.sceneRunId && !this.destroyed && !this.angryExitTriggered) {
                const reopened = await this.clickAgentSidePanelAction('agent-user-plugin', 'management-panel');
                if (!reopened) {
                    await this.openPluginDashboardWindow();
                }
                dashboardWindow = await this.waitForOpenedWindow(PLUGIN_DASHBOARD_WINDOW_NAME, 5000);
            }
            if (!dashboardWindow || runId !== this.sceneRunId || this.destroyed || this.angryExitTriggered) {
                return;
            }

            const completed = await this.waitForPluginDashboardPerformance(dashboardWindow, {
                line: step && step.performance ? step.performance.bubbleText || '' : '',
                closeOnDone: true
            }).catch((error) => {
                console.warn('[YuiGuide] 插件管理跨页演出失败:', error);
                return false;
            });
            this.customSecondarySpotlightTarget = null;
            this.clearSceneExtraSpotlights();
            this.overlay.clearActionSpotlight();
            this.clearVirtualSpotlight('plugin-management-entry');
            await this.closeNamedWindow(PLUGIN_DASHBOARD_WINDOW_NAME);
            await this.waitForHomeMainUIReady(3600);
            if (runId !== this.sceneRunId || this.destroyed || this.angryExitTriggered) {
                return;
            }

            this.overlay.clearActionSpotlight();
        }

        async runSettingsPeekScene(step, performance, runId) {
            this.customSecondarySpotlightTarget = null;
            const settingsButton = this.resolveElement(performance.cursorTarget || step.anchor);
            await this.closeAgentPanel();
            this.clearPreciseHighlights();
            this.clearSceneExtraSpotlights();
            const openedSettings = settingsButton
                ? await this.performHighlightedApiClick({
                    target: settingsButton,
                    durationMs: 900,
                    runId: runId,
                    action: () => this.openSettingsPanel()
                })
                : await this.openSettingsPanel();
            if (!openedSettings) {
                return;
            }
            if (runId !== this.sceneRunId || this.destroyed || this.angryExitTriggered) {
                return;
            }

            this.overlay.clearActionSpotlight();
            this.highlightChatWindow();

            let characterMenu = await this.waitForVisibleTarget([
                () => this.getSettingsPeekTargets().characterMenu
            ], 1600);
            if (!characterMenu) {
                return;
            }

            await this.ensureCharacterSettingsSidePanelVisible();

            let appearanceItem = await this.waitForVisibleTarget([
                () => this.getSettingsPeekTargets().appearanceItem
            ], 2200);
            let voiceCloneItem = await this.waitForVisibleTarget([
                () => this.getSettingsPeekTargets().voiceCloneItem
            ], 2200);
            if (!appearanceItem || !voiceCloneItem || runId !== this.sceneRunId || this.destroyed || this.angryExitTriggered) {
                return;
            }
            this.overlay.clearActionSpotlight();
            let settingsButtonTarget = null;
            ({
                settingsButton: settingsButtonTarget,
                characterMenu,
                appearanceItem,
                voiceCloneItem
            } = this.refreshSettingsPeekSpotlights(settingsButton));
            if (!characterMenu || !appearanceItem || !voiceCloneItem) {
                return;
            }

            if (performance.bubbleText) {
                this.appendGuideChatMessage(performance.bubbleText);
            }
            if (performance.emotion) {
                this.emotionBridge.apply(performance.emotion);
            }

            let openedModelManagerWindow = null;
            let openedModelManagerWindowName = null;
            let settingsPeekHighlightsCleared = false;
            const clearSettingsPeekHighlights = () => {
                if (settingsPeekHighlightsCleared) {
                    return;
                }

                settingsPeekHighlightsCleared = true;
                this.clearSceneExtraSpotlights();
                this.clearVirtualSpotlight('settings-character-children-bundle');
                this.clearVirtualSpotlight('settings-entry-bundle');
                this.clearPreciseHighlights();
                this.customSecondarySpotlightTarget = null;
                this.overlay.clearActionSpotlight();
                this.highlightChatWindow();
            };
            const narrationPromise = this.speakLineAndWait(performance.bubbleText || '').finally(() => {
                if (runId !== this.sceneRunId || this.destroyed || this.angryExitTriggered) {
                    return;
                }

                clearSettingsPeekHighlights();
            });
            const actionPromise = (async () => {
                await wait(2000);
                if (!appearanceItem || runId !== this.sceneRunId || this.destroyed || this.angryExitTriggered) {
                    return;
                }

                await this.moveCursorToElement(appearanceItem, 780);
                if (runId !== this.sceneRunId || this.destroyed || this.angryExitTriggered) {
                    return;
                }

                await this.clickCursorAndWait(180);
                const tutorialLanlanName = this.getTutorialModelManagerLanlanName();
                const appearanceMenuId = this.getCharacterAppearanceMenuId();
                openedModelManagerWindowName = this.getModelManagerWindowName(tutorialLanlanName, appearanceMenuId);
                openedModelManagerWindow = await this.openModelManagerPage(tutorialLanlanName);
                if (!openedModelManagerWindow) {
                    openedModelManagerWindow = await this.waitForOpenedWindow(openedModelManagerWindowName, 4000);
                }
                this.cleanupTutorialReturnButtons();
                await wait(180);
                await this.ensureCharacterSettingsSidePanelVisible();
                if (settingsPeekHighlightsCleared || runId !== this.sceneRunId || this.destroyed || this.angryExitTriggered) {
                    return;
                }
                ({
                    settingsButton: settingsButtonTarget,
                    characterMenu,
                    appearanceItem,
                    voiceCloneItem
                } = this.refreshSettingsPeekSpotlights(settingsButton));
            })();

            await Promise.all([narrationPromise, actionPromise]);
            if (openedModelManagerWindowName) {
                await this.closeNamedWindow(openedModelManagerWindowName);
            } else if (openedModelManagerWindow && !openedModelManagerWindow.closed) {
                try {
                    openedModelManagerWindow.close();
                } catch (error) {
                    console.warn('[YuiGuide] 关闭模型管理页失败:', error);
                }
            }

            await this.waitForHomeMainUIReady(3600);
            this.cleanupTutorialReturnButtons();
            await this.ensureCharacterSettingsSidePanelVisible();
            clearSettingsPeekHighlights();
        }

        beginTerminationVisualCleanup() {
            this.clearSceneTimers();
            this.disableInterrupts();
            this.clearPreciseHighlights();
            this.clearAllExtraSpotlights();
            this.cleanupTutorialReturnButtons();
            this.customSecondarySpotlightTarget = null;
            if (this.page === 'home') {
                document.body.classList.remove('yui-guide-home-driver-hidden');
            }
            this.cursor.cancel();
            this.cursor.hide();
            this.overlay.hidePluginPreview();
            this.overlay.hideBubble();
            this.overlay.setAngry(false);
            this.overlay.setTakingOver(false);
            this.overlay.clearSpotlight();
            this.closeManagedPanels().catch((error) => {
                console.warn('[YuiGuide] 终止时关闭首页面板失败:', error);
            });
            this.closeNamedWindow(PLUGIN_DASHBOARD_WINDOW_NAME).catch((error) => {
                console.warn('[YuiGuide] 终止时关闭插件面板失败:', error);
            });
            if (typeof window.handleShowMainUI === 'function') {
                try {
                    window.handleShowMainUI();
                } catch (error) {
                    console.warn('[YuiGuide] 终止时恢复主界面失败:', error);
                }
            }
        }

        async runTakeoverMainFlow() {
            if (this.takeoverFlowStarted || this.destroyed || this.angryExitTriggered) {
                return this.takeoverFlowPromise;
            }

            this.takeoverFlowStarted = true;
            this.takeoverFlowPromise = (async () => {
                await this.playManagedScene('takeover_capture_cursor', {
                    source: 'auto-takeover'
                });
                if (this.destroyed || this.angryExitTriggered) {
                    return;
                }

                await wait(360);
                if (this.destroyed || this.angryExitTriggered) {
                    return;
                }

                await this.playManagedScene('takeover_plugin_preview', {
                    source: 'auto-takeover'
                });
                if (this.destroyed || this.angryExitTriggered) {
                    return;
                }

                await wait(1200);
                if (this.destroyed || this.angryExitTriggered) {
                    return;
                }

                await this.playManagedScene('takeover_settings_peek', {
                    source: 'auto-takeover'
                });
                if (this.destroyed || this.angryExitTriggered) {
                    return;
                }

                await wait(1400);
                if (this.destroyed || this.angryExitTriggered) {
                    return;
                }

                await this.playManagedScene('takeover_return_control', {
                    source: 'auto-takeover'
                });
                this.takeoverFlowCompleted = true;
                if (this.destroyed || this.angryExitTriggered) {
                    return;
                }
                this.requestTermination('complete', 'complete');
            })().catch((error) => {
                console.error('[YuiGuide] 接管主流程执行失败:', error);
            });

            return this.takeoverFlowPromise;
        }

        async ensureChatVisible() {
            const chatContainer = document.getElementById('chat-container');
            const chatContentWrapper = document.getElementById('chat-content-wrapper');
            const chatHeader = document.getElementById('chat-header');
            const inputArea = document.getElementById('text-input-area');
            const reactChatOverlay = document.getElementById('react-chat-window-overlay');
            const reactChatHost = window.reactChatWindowHost;

            if (reactChatHost && typeof reactChatHost.ensureBundleLoaded === 'function') {
                try {
                    await reactChatHost.ensureBundleLoaded();
                } catch (error) {
                    console.warn('[YuiGuide] 预加载聊天窗失败:', error);
                }
            }

            if (reactChatHost && typeof reactChatHost.openWindow === 'function') {
                try {
                    reactChatHost.openWindow();
                } catch (error) {
                    console.warn('[YuiGuide] 打开聊天窗失败:', error);
                }
            }

            if (chatContainer) {
                chatContainer.classList.remove('minimized');
                chatContainer.classList.remove('mobile-collapsed');
            }
            if (chatContentWrapper) {
                chatContentWrapper.style.display = '';
            }
            if (chatHeader) {
                chatHeader.style.display = '';
            }
            if (inputArea) {
                inputArea.style.display = '';
                inputArea.classList.remove('hidden');
            }
            if (reactChatOverlay) {
                reactChatOverlay.hidden = false;
            }

            const inputTarget = await this.waitForElement(() => this.getChatInputTarget(), 5000);
            if (inputTarget) {
                return inputTarget;
            }

            return this.waitForElement(() => this.getChatWindowTarget(), 1200);
        }

        getGuideAssistantName() {
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

            return 'Neko';
        }

        getGuideAssistantAvatarUrl() {
            const host = window.reactChatWindowHost;
            if (!host || typeof host.getState !== 'function') {
                return undefined;
            }

            try {
                const snapshot = host.getState();
                const messages = snapshot && Array.isArray(snapshot.messages) ? snapshot.messages : [];
                for (let index = messages.length - 1; index >= 0; index -= 1) {
                    const message = messages[index];
                    if (!message || message.role !== 'assistant') {
                        continue;
                    }

                    const avatarUrl = typeof message.avatarUrl === 'string' ? message.avatarUrl.trim() : '';
                    if (avatarUrl) {
                        return avatarUrl;
                    }
                }
            } catch (error) {
                console.warn('[YuiGuide] 读取聊天头像失败:', error);
            }

            return undefined;
        }

        scrollChatToBottom() {
            const messageList = this.resolveElement('#react-chat-window-root .message-list');
            if (!messageList) {
                return;
            }

            const scroll = () => {
                try {
                    messageList.scrollTo({
                        top: messageList.scrollHeight,
                        behavior: 'smooth'
                    });
                } catch (_) {
                    messageList.scrollTop = messageList.scrollHeight;
                }
            };

            scroll();
            window.requestAnimationFrame(scroll);
            this.schedule(scroll, 160);
        }

        appendGuideChatMessage(text, options) {
            const content = typeof text === 'string' ? text.trim() : '';
            if (!content) {
                return null;
            }

            const normalizedOptions = options || {};
            const host = window.reactChatWindowHost;
            if (host && typeof host.appendMessage === 'function') {
                const createdAt = Date.now();
                let time = '';

                try {
                    time = new Date(createdAt).toLocaleTimeString([], {
                        hour: '2-digit',
                        minute: '2-digit'
                    });
                } catch (_) {}

                const message = {
                    id: 'yui-guide-' + createdAt + '-' + Math.random().toString(36).slice(2, 8),
                    role: 'assistant',
                    author: this.getGuideAssistantName(),
                    time: time,
                    createdAt: createdAt,
                    avatarUrl: this.getGuideAssistantAvatarUrl(),
                    blocks: [{
                        type: 'text',
                        text: content
                    }],
                    status: 'sent'
                };

                if (Array.isArray(normalizedOptions.buttons) && normalizedOptions.buttons.length > 0) {
                    message.blocks.push({
                        type: 'buttons',
                        buttons: normalizedOptions.buttons.map(function (button) {
                            if (!button || typeof button !== 'object') {
                                return null;
                            }

                            return {
                                id: button.id,
                                label: button.label,
                                action: button.action,
                                variant: button.variant,
                                disabled: !!button.disabled,
                                payload: button.payload || undefined
                            };
                        }).filter(Boolean)
                    });
                }

                if (Array.isArray(normalizedOptions.actions) && normalizedOptions.actions.length > 0) {
                    message.actions = normalizedOptions.actions.map(function (action) {
                        if (!action || typeof action !== 'object') {
                            return null;
                        }

                        return {
                            id: action.id,
                            label: action.label,
                            action: action.action,
                            variant: action.variant,
                            disabled: !!action.disabled,
                            payload: action.payload || undefined
                        };
                    }).filter(Boolean);
                }

                const appendedMessage = host.appendMessage(message);
                this.scrollChatToBottom();
                return appendedMessage;
            }

            if (typeof window.appendMessage === 'function') {
                window.appendMessage(content, 'gemini', true);
                this.scrollChatToBottom();
            }

            return null;
        }

        getGuideChatMessage(messageId) {
            const host = window.reactChatWindowHost;
            if (!host || typeof host.getState !== 'function' || !messageId) {
                return null;
            }

            try {
                const snapshot = host.getState();
                const messages = snapshot && Array.isArray(snapshot.messages) ? snapshot.messages : [];
                return messages.find(function (message) {
                    return message && String(message.id) === String(messageId);
                }) || null;
            } catch (error) {
                console.warn('[YuiGuide] 读取引导消息失败:', error);
                return null;
            }
        }

        updateGuideChatMessage(messageId, patch) {
            const host = window.reactChatWindowHost;
            if (!host || typeof host.updateMessage !== 'function' || !messageId) {
                return null;
            }

            try {
                return host.updateMessage(messageId, patch || {});
            } catch (error) {
                console.warn('[YuiGuide] 更新引导消息失败:', error);
                return null;
            }
        }

        clearGuideChatMessageActions(messageId) {
            if (!messageId) {
                return null;
            }

            const existingMessage = this.getGuideChatMessage(messageId);
            const nextBlocks = existingMessage && Array.isArray(existingMessage.blocks)
                ? existingMessage.blocks.filter(function (block) {
                    return block && block.type !== 'buttons';
                })
                : undefined;

            return this.updateGuideChatMessage(messageId, {
                blocks: nextBlocks,
                actions: []
            });
        }

        focusAndHighlightChatInput(spotlightTarget) {
            const target = spotlightTarget || this.getChatInputTarget();
            const inputBox = this.resolveElement('#react-chat-window-root .composer-input')
                || this.resolveElement('#textInputBox');

            if (!target) {
                return;
            }

            if (target && typeof target.scrollIntoView === 'function') {
                target.scrollIntoView({
                    behavior: 'smooth',
                    block: 'center',
                    inline: 'nearest'
                });
            }

            if (target) {
                this.overlay.setPersistentSpotlight(target);
            }

            if (inputBox && typeof inputBox.focus === 'function') {
                this.schedule(() => {
                    try {
                        inputBox.focus({ preventScroll: true });
                    } catch (_) {
                        inputBox.focus();
                    }
                }, 180);
            }
        }

        attachChatIntroActivation() {
            const actionHandler = (event) => {
                if (this.destroyed || !this.introChoicePending) {
                    return;
                }

                const detail = event && event.detail ? event.detail : null;
                const message = detail && detail.message ? detail.message : null;
                const action = detail && detail.action ? detail.action : null;
                const messageId = message && message.id ? String(message.id) : '';
                const actionId = action && action.id ? String(action.id) : '';
                const actionName = action && action.action ? String(action.action) : '';

                if (!messageId || messageId !== this.introPracticeMessageId) {
                    return;
                }

                if (actionId !== INTRO_SKIP_ACTION_ID && actionName !== INTRO_SKIP_ACTION_ID) {
                    return;
                }

                this.resolveChatIntroChoice('skip');
            };

            const submitHandler = (event) => {
                if (this.destroyed || !this.introChoicePending) {
                    return;
                }

                const detail = event && event.detail ? event.detail : null;
                const text = detail && typeof detail.text === 'string' ? detail.text : '';
                if (!text.trim()) {
                    return;
                }

                this.resolveChatIntroChoice('chat', {
                    submittedAt: Date.now()
                });
            };

            window.addEventListener(REACT_CHAT_ACTION_EVENT, actionHandler, true);
            window.addEventListener(REACT_CHAT_SUBMIT_EVENT, submitHandler, true);
            this.chatIntroCleanupFns.push(() => {
                window.removeEventListener(REACT_CHAT_ACTION_EVENT, actionHandler, true);
            });
            this.chatIntroCleanupFns.push(() => {
                window.removeEventListener(REACT_CHAT_SUBMIT_EVENT, submitHandler, true);
            });
        }

        waitForFirstAssistantReplyAfter(submittedAt) {
            const replyStartAt = Number.isFinite(submittedAt) ? submittedAt : Date.now();
            const maxWaitMs = 120000;

            return new Promise((resolve) => {
                const startedAt = Date.now();
                const initialHost = window.reactChatWindowHost;
                const initialSnapshot = initialHost && typeof initialHost.getState === 'function'
                    ? initialHost.getState()
                    : null;
                const knownAssistantMessageIds = new Set(
                    initialSnapshot && Array.isArray(initialSnapshot.messages)
                        ? initialSnapshot.messages.reduce((ids, message) => {
                            if (!message || message.role !== 'assistant') {
                                return ids;
                            }
                            const messageId = typeof message.id === 'string' ? message.id : '';
                            if (messageId && messageId.indexOf('yui-guide-') !== 0) {
                                ids.push(messageId);
                            }
                            return ids;
                        }, [])
                        : []
                );
                let seenReplyMessage = null;
                let settled = false;
                let replyTurnId = null;
                let replyTurnStartedAt = 0;
                let replyTurnEnded = false;
                let replySpeechStarted = false;

                const finish = (replyMessage) => {
                    if (settled) {
                        return;
                    }
                    settled = true;
                    window.removeEventListener('neko-assistant-turn-start', handleAssistantTurnStart, true);
                    window.removeEventListener('neko-assistant-speech-start', handleAssistantSpeechStart, true);
                    window.removeEventListener('neko-assistant-speech-end', handleAssistantSpeechEnd, true);
                    window.removeEventListener('neko-assistant-turn-end', handleAssistantTurnEnd, true);
                    if (this.introReplyPollTimer) {
                        window.clearTimeout(this.introReplyPollTimer);
                        this.introReplyPollTimer = null;
                    }
                    resolve(replyMessage || seenReplyMessage || null);
                };

                const handleAssistantTurnStart = (event) => {
                    const detail = event && event.detail ? event.detail : null;
                    const turnId = detail && detail.turnId ? String(detail.turnId) : '';
                    const timestamp = detail && Number.isFinite(detail.timestamp)
                        ? detail.timestamp
                        : Date.now();
                    if (!turnId) {
                        return;
                    }
                    if (timestamp < replyStartAt) {
                        return;
                    }
                    if (replyTurnId && replyTurnId !== turnId) {
                        return;
                    }
                    replyTurnId = turnId;
                    replyTurnStartedAt = timestamp;
                };

                const handleAssistantSpeechStart = (event) => {
                    const detail = event && event.detail ? event.detail : null;
                    const turnId = detail && detail.turnId ? String(detail.turnId) : '';
                    const timestamp = detail && Number.isFinite(detail.timestamp)
                        ? detail.timestamp
                        : Date.now();
                    if (!turnId || timestamp < replyStartAt) {
                        return;
                    }
                    if (replyTurnId && replyTurnId !== turnId) {
                        return;
                    }
                    replyTurnId = turnId;
                    replySpeechStarted = true;
                };

                const handleAssistantSpeechEnd = (event) => {
                    const detail = event && event.detail ? event.detail : null;
                    const turnId = detail && detail.turnId ? String(detail.turnId) : '';
                    const timestamp = detail && Number.isFinite(detail.timestamp)
                        ? detail.timestamp
                        : Date.now();
                    if (!turnId || timestamp < replyStartAt) {
                        return;
                    }
                    if (replyTurnId && turnId !== replyTurnId) {
                        return;
                    }
                    replyTurnId = turnId;
                    finish(seenReplyMessage || null);
                };

                const handleAssistantTurnEnd = (event) => {
                    const detail = event && event.detail ? event.detail : null;
                    const turnId = detail && detail.turnId ? String(detail.turnId) : '';
                    const timestamp = detail && Number.isFinite(detail.timestamp)
                        ? detail.timestamp
                        : Date.now();
                    if (!turnId || timestamp < replyStartAt) {
                        return;
                    }
                    if (replyTurnId && turnId !== replyTurnId) {
                        return;
                    }
                    if (replyTurnStartedAt && timestamp < replyTurnStartedAt) {
                        return;
                    }
                    replyTurnId = turnId;
                    replyTurnEnded = true;
                    if (!replySpeechStarted && seenReplyMessage && seenReplyMessage.status === 'sent') {
                        finish(seenReplyMessage);
                    }
                };

                window.addEventListener('neko-assistant-turn-start', handleAssistantTurnStart, true);
                window.addEventListener('neko-assistant-speech-start', handleAssistantSpeechStart, true);
                window.addEventListener('neko-assistant-speech-end', handleAssistantSpeechEnd, true);
                window.addEventListener('neko-assistant-turn-end', handleAssistantTurnEnd, true);

                const poll = () => {
                    if (this.destroyed) {
                        finish(null);
                        return;
                    }

                    const host = window.reactChatWindowHost;
                    const snapshot = host && typeof host.getState === 'function'
                        ? host.getState()
                        : null;
                    const messages = snapshot && Array.isArray(snapshot.messages)
                        ? snapshot.messages
                        : [];

                    const replyMessage = messages.find((message) => {
                        if (!message || message.role !== 'assistant') {
                            return false;
                        }

                        const messageId = typeof message.id === 'string' ? message.id : '';
                        if (messageId.indexOf('yui-guide-') === 0) {
                            return false;
                        }

                        if (knownAssistantMessageIds.has(messageId)) {
                            return false;
                        }

                        const createdAt = Number.isFinite(message.createdAt) ? message.createdAt : 0;
                        if (createdAt < replyStartAt) {
                            return false;
                        }

                        return true;
                    }) || null;

                    if (replyMessage) {
                        seenReplyMessage = replyMessage;
                        if (replyTurnEnded && !replySpeechStarted && replyMessage.status === 'sent') {
                            finish(replyMessage);
                            return;
                        }
                    }

                    if ((Date.now() - startedAt) >= maxWaitMs) {
                        finish(seenReplyMessage);
                        return;
                    }

                    this.introReplyPollTimer = window.setTimeout(poll, 280);
                };

                poll();
            });
        }

        resolveChatIntroChoice(mode, options) {
            if (this.destroyed || !this.introChoicePending) {
                return;
            }

            const normalizedMode = mode === 'chat' ? 'chat' : 'skip';
            const promptMessageId = this.introPracticeMessageId;
            const submittedAt = options && Number.isFinite(options.submittedAt)
                ? options.submittedAt
                : Date.now();

            this.introChoicePending = false;
            this.introClickActivated = true;
            this.introFlowCompleted = true;

            if (promptMessageId) {
                this.clearGuideChatMessageActions(promptMessageId);
            }

            this.cancelActiveNarration();
            this.clearIntroFlow(true);
            this.highlightChatWindow();

            if (normalizedMode === 'chat') {
                this.waitForFirstAssistantReplyAfter(submittedAt).then(() => {
                    if (this.destroyed) {
                        return;
                    }

                    this.sendIntroFollowups({
                        includeProactive: false
                    });
                }).catch((error) => {
                    console.warn('[YuiGuide] 等待首次聊天回复失败:', error);
                    if (this.destroyed) {
                        return;
                    }

                    this.sendIntroFollowups({
                        includeProactive: false
                    });
                });
                return;
            }

            this.sendIntroFollowups({
                includeProactive: true
            });
        }

        sendIntroFollowups(options) {
            const proactiveStep = this.getStep('intro_proactive');
            const catPawStep = this.getStep('intro_cat_paw');
            const normalizedOptions = options || {};
            const includeProactive = normalizedOptions.includeProactive !== false;
            const catPawDelayMs = includeProactive ? 5000 : 280;

            (async () => {
                if (includeProactive && proactiveStep && proactiveStep.performance) {
                    const proactiveText = proactiveStep.performance.bubbleText || '';
                    this.appendGuideChatMessage(proactiveText);
                    if (proactiveStep.performance.emotion) {
                        this.emotionBridge.apply(proactiveStep.performance.emotion);
                    }
                    await this.speakLineAndWait(proactiveText);
                    if (!this.cursor.hasPosition()) {
                        const origin = this.getDefaultCursorOrigin();
                        this.cursor.showAt(origin.x, origin.y);
                    }
                }
                if (this.destroyed) {
                    return;
                }

                if (catPawDelayMs > 0) {
                    await new Promise((resolve) => {
                        this.introThirdMessageTimer = window.setTimeout(() => {
                            this.introThirdMessageTimer = null;
                            resolve();
                        }, catPawDelayMs);
                    });
                }

                if (this.destroyed) {
                    return;
                }

                if (catPawStep && catPawStep.performance) {
                    const catPawText = catPawStep.performance.bubbleText || '';
                    this.appendGuideChatMessage(catPawText);
                    if (catPawStep.performance.emotion) {
                        this.emotionBridge.apply(catPawStep.performance.emotion);
                    }
                    await this.speakLineAndWait(catPawText);
                }

                this.introFlowCompleted = true;
                this.schedule(() => {
                    this.runTakeoverMainFlow();
                }, 320);
            })();
        }

        async runChatIntroPrelude() {
            if (this.introFlowStarted || this.destroyed) {
                return;
            }

            const introStep = this.getStep('intro_basic');
            if (!introStep || !introStep.performance) {
                return;
            }

            this.introFlowStarted = true;
            this.overlay.hideBubble();
            this.overlay.hidePluginPreview();
            await this.ensureChatVisible();
            this.focusAndHighlightChatInput(this.getChatInputTarget());
            const introText = introStep.performance.bubbleText || '';
            this.appendGuideChatMessage(introText);
            if (introStep.performance.emotion) {
                this.emotionBridge.apply(introStep.performance.emotion);
            }
            await this.speakLineAndWait(introText, {
                minDurationMs: 4200
            });
            if (this.destroyed) {
                return;
            }

            this.highlightChatWindow();
            await wait(240);
            if (this.destroyed) {
                return;
            }

            const practiceMessage = this.appendGuideChatMessage(INTRO_PRACTICE_TEXT, {
                buttons: [{
                    id: INTRO_SKIP_ACTION_ID,
                    label: '暂时先不聊天',
                    action: INTRO_SKIP_ACTION_ID,
                    variant: 'secondary',
                    disabled: false
                }]
            });
            this.introPracticeMessageId = practiceMessage && practiceMessage.id
                ? String(practiceMessage.id)
                : null;
            this.introChoicePending = true;
            this.attachChatIntroActivation();
            await this.speakLineAndWait(INTRO_PRACTICE_TEXT, {
                minDurationMs: 3600
            });
            if (this.destroyed) {
                return;
            }
            this.introFlowCompleted = true;
        }

        async startPrelude() {
            const preludeSceneIds = this.getPreludeSceneIds();
            if (!Array.isArray(preludeSceneIds) || preludeSceneIds.length === 0) {
                return;
            }

            const firstSceneId = preludeSceneIds[0];
            if (firstSceneId === 'intro_basic' && this.page === 'home') {
                await this.runChatIntroPrelude();
                return;
            }

            await this.playScene(firstSceneId, {
                source: 'prelude'
            });
        }

        async enterStep(stepId, context) {
            if (this.destroyed || !stepId) {
                return;
            }

            if ((stepId === 'intro_proactive' || stepId === 'intro_cat_paw') && this.introFlowStarted) {
                this.currentSceneId = stepId;
                this.currentStep = this.getStep(stepId);
                this.currentContext = context || null;
                return;
            }

            if (this.takeoverFlowStarted && stepId.indexOf('takeover_') === 0) {
                this.setCurrentScene(stepId, context || null);
                return;
            }

            await this.playManagedScene(stepId, {
                source: (context && context.source) || 'step-enter',
                context: context || null
            });
        }

        async leaveStep(stepId) {
            if (this.destroyed) {
                return;
            }

            if (stepId && this.currentSceneId && stepId !== this.currentSceneId) {
                return;
            }

            this.clearSceneTimers();
            this.disableInterrupts();
            this.customSecondarySpotlightTarget = null;
            this.clearPreciseHighlights();
            this.clearSceneExtraSpotlights();

            if (stepId === 'takeover_plugin_preview') {
                this.overlay.hidePluginPreview();
            }

            if (stepId === 'takeover_capture_cursor' || stepId === 'takeover_plugin_preview') {
                this.clearVirtualSpotlight('plugin-management-entry');
            }

            if (stepId === 'takeover_settings_peek') {
                this.clearVirtualSpotlight('settings-character-children-bundle');
                this.clearVirtualSpotlight('settings-entry-bundle');
            }
        }

        async playScene(stepId, meta) {
            const step = this.getStep(stepId);
            if (!step) {
                return;
            }

            const runId = ++this.sceneRunId;
            const performance = step.performance || {};
            const anchorRect = this.resolveRect(step.anchor);
            const cursorTargetRect = this.resolveRect(performance.cursorTarget || step.anchor);
            const isTakeoverScene = stepId.indexOf('takeover_') === 0 || stepId.indexOf('interrupt_') === 0;
            const cursorSpeed = Number.isFinite(performance.cursorSpeedMultiplier) ? performance.cursorSpeedMultiplier : 1;
            const delayMs = Number.isFinite(performance.delayMs) ? performance.delayMs : DEFAULT_STEP_DELAY_MS;
            const durationMs = clamp(Math.round(DEFAULT_CURSOR_DURATION_MS / Math.max(0.35, cursorSpeed)), 160, 900);
            const spotlightElement = this.resolveElement(performance.cursorTarget || step.anchor);
            const shouldNarrateInChat = this.shouldNarrateInChat(stepId);
            const shouldNarrateAfterMove = (
                stepId === 'takeover_capture_cursor'
                || stepId === 'takeover_plugin_preview'
                || stepId === 'takeover_settings_peek'
            );
            const shouldNarrateDuringMove = stepId === 'takeover_capture_cursor';
            const shouldKeepInterruptsEnabled = (
                performance.interruptible !== false
                && (isTakeoverScene || stepId === 'intro_cat_paw')
            );
            const shouldOpenPanelAfterNarration = (
                stepId === 'takeover_plugin_preview'
                || stepId === 'takeover_settings_peek'
            );

            this.clearSceneTimers();
            this.overlay.setAngry(false);
            this.clearPreciseHighlights();
            this.clearSceneExtraSpotlights();
            if (stepId !== 'takeover_capture_cursor' && stepId !== 'takeover_plugin_preview') {
                this.clearRetainedExtraSpotlights();
            }

            if (isTakeoverScene && stepId !== 'takeover_return_control') {
                this.overlay.setTakingOver(true);
            }

            const persistentSpotlightTarget = this.getSceneSpotlightTarget(stepId, performance);
            if (persistentSpotlightTarget) {
                this.overlay.setPersistentSpotlight(persistentSpotlightTarget);
            }

            const actionSpotlightTarget = this.getActionSpotlightTarget(stepId, performance);
            if (actionSpotlightTarget) {
                this.overlay.activateSpotlight(actionSpotlightTarget);
            } else {
                this.overlay.clearActionSpotlight();
            }

            if (stepId !== 'takeover_plugin_preview') {
                this.overlay.hidePluginPreview();
            }

            if (stepId === 'takeover_capture_cursor') {
                this.clearVirtualSpotlight('plugin-management-entry');
                this.clearVirtualSpotlight('settings-entry-bundle');
                this.clearVirtualSpotlight('settings-character-children-bundle');
                this.overlay.hideBubble();
                this.highlightChatWindow();
                this.enableInterrupts(step);
                await this.performCaptureCursorPrelude(2000);
                if (runId !== this.sceneRunId || this.destroyed || this.angryExitTriggered) {
                    return;
                }

                if (performance.bubbleText && shouldNarrateInChat) {
                    this.appendGuideChatMessage(performance.bubbleText);
                }
                if (performance.emotion) {
                    this.emotionBridge.apply(performance.emotion);
                }

                await Promise.all([
                    this.speakLineAndWait(performance.bubbleText || '', {
                        minDurationMs: 8000
                    }),
                    this.runTakeoverCaptureActionSequence(step, performance, runId)
                ]);
                if (runId !== this.sceneRunId || this.destroyed || this.angryExitTriggered) {
                    return;
                }

                await wait(DEFAULT_SCENE_SETTLE_MS);
                return;
            }

            if (stepId === 'takeover_plugin_preview') {
                this.clearVirtualSpotlight('plugin-management-entry');
                this.clearVirtualSpotlight('settings-entry-bundle');
                this.overlay.hideBubble();
                this.enableInterrupts(step);
                await this.runPluginDashboardPreviewScene(step, runId);
                if (runId !== this.sceneRunId || this.destroyed || this.angryExitTriggered) {
                    return;
                }

                await wait(DEFAULT_SCENE_SETTLE_MS);
                return;
            }

            if (stepId === 'takeover_settings_peek') {
                this.clearVirtualSpotlight('settings-character-children-bundle');
                this.clearVirtualSpotlight('settings-entry-bundle');
                this.overlay.hideBubble();
                this.enableInterrupts(step);
                await this.runSettingsPeekScene(step, performance, runId);
                if (runId !== this.sceneRunId || this.destroyed || this.angryExitTriggered) {
                    return;
                }

                await wait(DEFAULT_SCENE_SETTLE_MS);
                return;
            }

            if (performance.bubbleText && !shouldNarrateAfterMove && !shouldNarrateInChat) {
                this.overlay.showBubble(performance.bubbleText, {
                    title: 'Yui',
                    emotion: performance.emotion || 'neutral',
                    anchorRect: anchorRect
                });
            } else if (!shouldNarrateAfterMove) {
                this.overlay.hideBubble();
            }

            if (performance.emotion && !shouldNarrateAfterMove) {
                this.emotionBridge.apply(performance.emotion);
            }

            const shouldIntroduceCursor = stepId === 'takeover_capture_cursor' && !this.cursor.hasPosition();
            if (shouldIntroduceCursor) {
                const origin = this.getDefaultCursorOrigin();
                this.cursor.showAt(origin.x, origin.y);
                await wait(260);
                if (runId !== this.sceneRunId || this.destroyed) {
                    return;
                }
                this.cursor.wobble();
                await wait(260);
                if (runId !== this.sceneRunId || this.destroyed) {
                    return;
                }
                this.cursor.wobble();
                await wait(260);
                if (runId !== this.sceneRunId || this.destroyed) {
                    return;
                }
            }

            if (cursorTargetRect && !this.cursor.hasPosition()) {
                const origin = this.getDefaultCursorOrigin();
                this.cursor.showAt(origin.x, origin.y);
            }

            if (delayMs > 0) {
                await wait(delayMs);
                if (runId !== this.sceneRunId || this.destroyed) {
                    return;
                }
            }

            let narrationPromise = null;
            if (shouldKeepInterruptsEnabled && shouldNarrateDuringMove) {
                this.enableInterrupts(step);
            }

            if (performance.bubbleText && shouldNarrateDuringMove && !shouldNarrateInChat) {
                this.overlay.showBubble(performance.bubbleText, {
                    title: 'Yui',
                    emotion: performance.emotion || 'neutral',
                    anchorRect: anchorRect
                });
            }

            if (performance.emotion && shouldNarrateDuringMove) {
                this.emotionBridge.apply(performance.emotion);
            }

            if (shouldNarrateDuringMove) {
                if (performance.bubbleText && shouldNarrateInChat) {
                    this.appendGuideChatMessage(performance.bubbleText);
                    this.overlay.hideBubble();
                }
                narrationPromise = this.speakLineAndWait(performance.bubbleText || '');
            }

            const shouldMoveCursor = (
                stepId === 'takeover_capture_cursor'
                || stepId === 'takeover_plugin_preview'
                || stepId === 'takeover_settings_peek'
                || (!shouldIntroduceCursor || stepId !== 'takeover_capture_cursor')
            );
            if (shouldMoveCursor && (performance.cursorAction === 'move' || performance.cursorAction === 'click' || performance.cursorAction === 'wobble')) {
                if (cursorTargetRect) {
                    const movePromise = this.cursor.moveToRect(cursorTargetRect, { durationMs: durationMs });
                    if (narrationPromise) {
                        await Promise.all([movePromise, narrationPromise]);
                    } else {
                        await movePromise;
                    }
                    if (runId !== this.sceneRunId || this.destroyed) {
                        return;
                    }
                }
            } else if (narrationPromise) {
                await narrationPromise;
            }

            if (shouldKeepInterruptsEnabled && !shouldNarrateDuringMove) {
                this.enableInterrupts(step);
            } else if (!shouldKeepInterruptsEnabled) {
                this.disableInterrupts();
            }

            let handledPostMove = false;

            if (stepId === 'takeover_plugin_preview') {
                handledPostMove = true;
                this.cursor.click();
                await this.openAgentPanel();
                if (runId !== this.sceneRunId || this.destroyed) {
                    return;
                }
                this.overlay.showPluginPreview(PREVIEW_ITEMS, {
                    title: '插件管理预演'
                });
                this.highlightChatWindow();
                if (performance.emotion) {
                    this.emotionBridge.apply(performance.emotion);
                }
                if (performance.bubbleText) {
                    this.appendGuideChatMessage(performance.bubbleText);
                }
                await this.speakLineAndWait(performance.bubbleText || '');
                if (runId !== this.sceneRunId || this.destroyed) {
                    return;
                }
                this.highlightChatWindow();
            }

            if (stepId === 'takeover_settings_peek') {
                handledPostMove = true;
                this.cursor.click();
                await this.closeAgentPanel();
                const settingsMenuId = this.normalizeSettingsMenuId(performance.settingsMenuId || 'character');
                const settingsButtonTarget = this.resolveElement(performance.cursorTarget || this.currentStep && this.currentStep.anchor || '');
                const menuVisible = await this.ensureSettingsMenuVisible(settingsMenuId);
                if (runId !== this.sceneRunId || this.destroyed) {
                    return;
                }

                if (settingsButtonTarget) {
                    this.overlay.activateSpotlight(settingsButtonTarget);
                }
                this.highlightChatWindow();
                if (performance.emotion) {
                    this.emotionBridge.apply(performance.emotion);
                }
                if (performance.bubbleText) {
                    this.appendGuideChatMessage(performance.bubbleText);
                }

                const characterPhrase = '你看，这里可以穿我的新衣服、给我换一个好听的声音';
                const highlightStartIndex = typeof performance.bubbleText === 'string'
                    ? performance.bubbleText.indexOf(characterPhrase)
                    : -1;
                let settingsMenuHighlighted = false;
                let settingsMenuFallbackTimer = 0;

                const activateSettingsMenuSpotlight = () => {
                    if (settingsMenuHighlighted) {
                        return;
                    }

                    const spotlightTarget = menuVisible
                        ? this.getSettingsMenuElement(settingsMenuId)
                        : (this.isManagedPanelVisible('settings') ? this.getManagedPanelElement('settings') : null);
                    if (!spotlightTarget) {
                        return;
                    }

                    settingsMenuHighlighted = true;
                    this.overlay.activateSecondarySpotlight(spotlightTarget);
                };

                if (highlightStartIndex >= 0 && performance.bubbleText) {
                    const estimatedDurationMs = Math.max(
                        estimateSpeechDurationMs(performance.bubbleText),
                        3000
                    );
                    const highlightDelayMs = clamp(
                        Math.round((highlightStartIndex / Math.max(performance.bubbleText.length, 1)) * estimatedDurationMs),
                        200,
                        Math.max(estimatedDurationMs - 200, 200)
                    );
                    settingsMenuFallbackTimer = window.setTimeout(() => {
                        settingsMenuFallbackTimer = 0;
                        if (runId !== this.sceneRunId || this.destroyed) {
                            return;
                        }
                        activateSettingsMenuSpotlight();
                    }, highlightDelayMs);
                }

                try {
                    await this.speakLineAndWait(performance.bubbleText || '', {
                        onBoundary: (event) => {
                            if (settingsMenuHighlighted || highlightStartIndex < 0) {
                                return;
                            }

                            const absoluteCharIndex = event && Number.isFinite(event.absoluteCharIndex)
                                ? event.absoluteCharIndex
                                : -1;
                            if (absoluteCharIndex < highlightStartIndex) {
                                return;
                            }

                            activateSettingsMenuSpotlight();
                        }
                    });
                } finally {
                    if (settingsMenuFallbackTimer) {
                        window.clearTimeout(settingsMenuFallbackTimer);
                        settingsMenuFallbackTimer = 0;
                    }
                }
                if (runId !== this.sceneRunId || this.destroyed) {
                    return;
                }
                this.overlay.clearActionSpotlight();
                this.highlightChatWindow();
            }

            if (!handledPostMove) {
                if (performance.bubbleText && shouldNarrateAfterMove && !shouldNarrateInChat) {
                    this.overlay.showBubble(performance.bubbleText, {
                        title: 'Yui',
                        emotion: performance.emotion || 'neutral',
                        anchorRect: anchorRect
                    });
                } else if (shouldNarrateAfterMove) {
                    this.overlay.hideBubble();
                }

                if (performance.emotion && shouldNarrateAfterMove) {
                    this.emotionBridge.apply(performance.emotion);
                }

                if (!shouldNarrateDuringMove) {
                    if (performance.bubbleText && shouldNarrateInChat) {
                        this.appendGuideChatMessage(performance.bubbleText);
                        this.overlay.hideBubble();
                    }
                    await this.speakLineAndWait(performance.bubbleText || '');
                    if (runId !== this.sceneRunId || this.destroyed) {
                        return;
                    }
                }

                if (performance.cursorAction === 'click' && !shouldOpenPanelAfterNarration) {
                    this.cursor.click();
                } else if (performance.cursorAction === 'wobble') {
                    this.cursor.wobble();
                }
            }

            if (stepId === 'takeover_return_control') {
                await this.closeManagedPanels();
                if (runId !== this.sceneRunId || this.destroyed) {
                    return;
                }
                this.highlightChatWindow();
                const centerPoint = this.getViewportCenter();
                await wait(140);
                if (runId !== this.sceneRunId || this.destroyed) {
                    return;
                }
                if (!this.cursor.hasPosition()) {
                    this.cursor.showAt(centerPoint.x, centerPoint.y);
                } else {
                    await this.cursor.moveToPoint(centerPoint.x, centerPoint.y, { durationMs: 360 });
                }
                this.cursor.wobble();
                await wait(260);
                if (runId !== this.sceneRunId || this.destroyed) {
                    return;
                }
                this.cursor.hide();
                this.overlay.clearActionSpotlight();
                this.disableInterrupts();
                this.overlay.setTakingOver(false);
            }

            if (!shouldKeepInterruptsEnabled) {
                this.disableInterrupts();
            }

            await wait(DEFAULT_SCENE_SETTLE_MS);
            if (runId !== this.sceneRunId || this.destroyed) {
                return;
            }

            if (meta && meta.source === 'prelude') {
                this.schedule(() => {
                    if (!this.currentSceneId && !this.destroyed) {
                        this.overlay.hideBubble();
                    }
                }, 2600);
            }
        }

        onPointerMove(event) {
            this.handleInterrupt(event);
        }

        onPointerDown(event) {
            if (!event || event.isTrusted === false) {
                return;
            }

            const x = Number.isFinite(event.clientX) ? event.clientX : null;
            const y = Number.isFinite(event.clientY) ? event.clientY : null;
            if (x === null || y === null) {
                return;
            }

            this.lastPointerPoint = {
                x: x,
                y: y,
                t: Date.now(),
                speed: 0
            };
            this.interruptAccelerationStreak = 0;
        }

        handleInterrupt(event) {
            if (this.destroyed || this.angryExitTriggered || !this.interruptsEnabled || !event || event.isTrusted === false) {
                return;
            }

            const step = this.currentStep;
            const performance = (step && step.performance) || {};
            const interrupts = (step && step.interrupts) || {};
            if (performance.interruptible === false) {
                return;
            }

            const x = Number.isFinite(event.clientX) ? event.clientX : null;
            const y = Number.isFinite(event.clientY) ? event.clientY : null;
            if (x === null || y === null) {
                return;
            }

            if (!document.body.classList.contains('yui-taking-over')) {
                return;
            }

            const now = Date.now();
            const previousPoint = this.lastPointerPoint;
            if (!previousPoint || !Number.isFinite(previousPoint.t)) {
                this.lastPointerPoint = {
                    x: x,
                    y: y,
                    t: now,
                    speed: 0
                };
                this.interruptAccelerationStreak = 0;
                return;
            }

            const dx = x - previousPoint.x;
            const dy = y - previousPoint.y;
            const distance = Math.hypot(dx, dy);
            const dt = Math.max(1, now - previousPoint.t);
            const speed = distance / dt;
            const previousSpeed = Number.isFinite(previousPoint.speed) ? previousPoint.speed : 0;
            const acceleration = (speed - previousSpeed) / dt;

            this.lastPointerPoint = {
                x: x,
                y: y,
                t: now,
                speed: speed
            };

            this.maybePlayPassiveResistance(x, y, distance, speed, now);

            if (distance < DEFAULT_INTERRUPT_DISTANCE) {
                this.interruptAccelerationStreak = 0;
                return;
            }

            if (speed < DEFAULT_INTERRUPT_SPEED_THRESHOLD) {
                this.interruptAccelerationStreak = 0;
                return;
            }

            if (acceleration < DEFAULT_INTERRUPT_ACCELERATION_THRESHOLD) {
                this.interruptAccelerationStreak = 0;
                return;
            }

            this.interruptAccelerationStreak += 1;
            if (this.interruptAccelerationStreak < DEFAULT_INTERRUPT_ACCELERATION_STREAK) {
                return;
            }
            this.interruptAccelerationStreak = 0;

            const throttleMs = Number.isFinite(interrupts.throttleMs) ? interrupts.throttleMs : 500;
            if (now - this.lastInterruptAt < throttleMs) {
                return;
            }
            this.lastInterruptAt = now;

            this.interruptCount += 1;
            const threshold = Number.isFinite(interrupts.threshold) ? interrupts.threshold : 3;

            if (this.interruptCount >= threshold) {
                this.abortAsAngryExit('pointer_interrupt');
                return;
            }

            this.playLightResistance(x, y);
        }

        playLightResistance(x, y) {
            const resistanceStep = this.getStep('interrupt_resist_light');
            if (!resistanceStep) {
                return;
            }

            const performance = resistanceStep.performance || {};
            const voices = Array.isArray(performance.resistanceVoices) ? performance.resistanceVoices : [];
            const message = voices.length > 0
                ? voices[(this.interruptCount - 1) % voices.length]
                : performance.bubbleText || '不要拽我啦，还没结束呢！';

            this.interruptNarrationForResistance();

            this.overlay.hideBubble();
            this.appendGuideChatMessage(message);
            this.emotionBridge.apply(performance.emotion || 'surprised');
            this.voiceQueue.speak(message).finally(() => {
                const narration = this.activeNarration;
                if (narration && narration.interrupted) {
                    this.scheduleNarrationResume();
                    return;
                }

                this.restoreCurrentScenePresentation();
            });
            this.cursor.resistTo(x, y);
        }

        async abortAsAngryExit(source) {
            if (this.destroyed || this.angryExitTriggered) {
                return;
            }

            this.angryExitTriggered = true;
            this.clearSceneTimers();
            this.disableInterrupts();

            const angryStep = this.getStep('interrupt_angry_exit');
            const performance = (angryStep && angryStep.performance) || {};

            this.overlay.setTakingOver(true);
            this.overlay.setAngry(true);
            this.overlay.hidePluginPreview();
            this.overlay.hideBubble();
            this.appendGuideChatMessage(performance.bubbleText || '人类~~~~！你真的很没礼貌喵！');
            this.emotionBridge.apply(performance.emotion || 'angry');
            await this.speakLineAndWait(performance.bubbleText || '');
            if (this.destroyed) {
                return;
            }

            this.requestTermination(source || 'angry_exit', 'angry_exit');
        }

        requestTermination(reason, tutorialReason) {
            if (this.destroyed || this.terminationRequested) {
                return;
            }

            this.terminationRequested = true;
            this.beginTerminationVisualCleanup();
            const finalReason = tutorialReason || reason || 'skip';
            if (this.tutorialManager && typeof this.tutorialManager.requestTutorialDestroy === 'function') {
                this.tutorialManager.requestTutorialDestroy(finalReason);
            } else {
                this.destroy();
            }
        }

        skip(reason, tutorialReason) {
            this.requestTermination(reason, tutorialReason);
        }

        destroy() {
            if (this.destroyed) {
                return;
            }

            this.destroyed = true;
            this.terminationRequested = true;
            if (this.page === 'home') {
                document.body.classList.remove('yui-guide-home-driver-hidden');
            }
            if (this.pluginDashboardHandoff && typeof this.pluginDashboardHandoff.resolve === 'function') {
                this.pluginDashboardHandoff.resolve(false);
            }
            this.cancelActiveNarration();
            this.clearIntroFlow();
            this.clearSceneTimers();
            this.disableInterrupts();
            this.voiceQueue.stop();
            this.cursor.cancel();
            this.cursor.hide();
            this.clearAllVirtualSpotlights();
            this.clearPreciseHighlights();
            this.clearSpotlightVariantHints();
            this.clearAllExtraSpotlights();
            this.cleanupTutorialReturnButtons();
            this.customSecondarySpotlightTarget = null;
            this.emotionBridge.clear();
            this.closeManagedPanels().catch((error) => {
                console.warn('[YuiGuide] 销毁时关闭首页面板失败:', error);
            });
            if (typeof window.handleShowMainUI === 'function') {
                try {
                    window.handleShowMainUI();
                } catch (error) {
                    console.warn('[YuiGuide] 销毁时恢复主界面失败:', error);
                }
            }
            this.overlay.hidePluginPreview();
            this.overlay.hideBubble();
            this.overlay.setAngry(false);
            this.overlay.setTakingOver(false);
            this.overlay.destroy();
            window.removeEventListener('keydown', this.keydownHandler, true);
            window.removeEventListener('pagehide', this.pageHideHandler, true);
            window.removeEventListener('neko:yui-guide:tutorial-end', this.tutorialEndHandler, true);
            window.removeEventListener('message', this.messageHandler, true);
            document.removeEventListener('click', this.skipButtonClickHandler, true);
        }

        onKeyDown(event) {
            if (this.destroyed || !event || event.key !== 'Escape') {
                return;
            }

            event.stopPropagation();
            this.skip('escape', 'skip');
        }

        onPageHide() {
            this.destroy();
        }

        onSkipButtonClick(event) {
            if (this.destroyed || !event || !event.target || typeof event.target.closest !== 'function') {
                return;
            }

            const skipButton = event.target.closest('#neko-tutorial-skip-btn');
            if (!skipButton) {
                return;
            }

            if (typeof event.preventDefault === 'function') {
                event.preventDefault();
            }
            if (typeof event.stopImmediatePropagation === 'function') {
                event.stopImmediatePropagation();
            }
            event.stopPropagation();
            this.skip('skip', 'skip');
        }

        onTutorialEndEvent(event) {
            const detail = event && event.detail ? event.detail : null;
            if (!detail || detail.page !== this.page) {
                return;
            }

            this.lastTutorialEndReason = detail.reason || null;
            this.destroy();
        }

        onWindowMessage(event) {
            const data = event && event.data ? event.data : null;
            if (!data || typeof data !== 'object') {
                return;
            }

            const handoff = this.pluginDashboardHandoff;
            if (!handoff || !handoff.windowRef || event.source !== handoff.windowRef) {
                return;
            }

            if (data.sessionId && handoff.sessionId && data.sessionId !== handoff.sessionId) {
                return;
            }

            if (data.type === PLUGIN_DASHBOARD_READY_EVENT) {
                handoff.ready = true;
                handoff.readyAt = Date.now();
                return;
            }

            if (data.type === PLUGIN_DASHBOARD_DONE_EVENT) {
                handoff.resolve(true);
            }
        }
    }

    window.createYuiGuideDirector = function createYuiGuideDirector(options) {
        return new YuiGuideDirector(options);
    };
})();
