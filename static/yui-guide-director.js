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
    const PREVIEW_ITEMS = Object.freeze([
        'WebSearch',
        'B站弹幕',
        '米家控制',
        '智能家居',
        '日程提醒'
    ]);

    function wait(ms) {
        return new Promise((resolve) => {
            window.setTimeout(resolve, ms);
        });
    }

    function clamp(value, min, max) {
        return Math.max(min, Math.min(max, value));
    }

    class YuiGuideVoiceQueue {
        constructor() {
            this.currentUtterance = null;
            this.currentFallbackTimer = null;
            this.currentFinish = null;
            this.enabled = !!window.speechSynthesis;
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

            this.currentUtterance = null;
            this.currentFinish = null;

            if (typeof finish === 'function') {
                try {
                    finish();
                } catch (_) {}
            }
        }

        async speak(text, options) {
            const message = typeof text === 'string' ? text.trim() : '';
            const normalizedOptions = options || {};
            if (!message) {
                return;
            }

            this.stop();
            await wait(48);

            if (this.enabled && window.speechSynthesis) {
                try {
                    if (window.speechSynthesis.speaking || window.speechSynthesis.pending) {
                        window.speechSynthesis.cancel();
                        await wait(24);
                    }
                } catch (error) {
                    console.warn('[YuiGuide] 重置语音播放状态失败:', error);
                }
            }

            if (!this.enabled || typeof SpeechSynthesisUtterance === 'undefined') {
                return;
            }

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
                    clamp(Math.round(message.length * 280), 7000, 24000)
                );

                try {
                    window.speechSynthesis.speak(utterance);
                } catch (error) {
                    console.warn('[YuiGuide] 播放语音失败，回退为静默模式:', error);
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
            this.introThirdMessageTimer = null;
            this.takeoverFlowStarted = false;
            this.takeoverFlowCompleted = false;
            this.takeoverFlowPromise = null;
            this.terminationRequested = false;
            this.activeNarration = null;
            this.narrationResumeTimer = null;
            this.chatIntroCleanupFns = [];
            this.keydownHandler = this.onKeyDown.bind(this);
            this.pointerMoveHandler = this.onPointerMove.bind(this);
            this.pointerDownHandler = this.onPointerDown.bind(this);
            this.pageHideHandler = this.onPageHide.bind(this);
            this.tutorialEndHandler = this.onTutorialEndEvent.bind(this);

            if (this.page === 'home') {
                document.body.classList.add('yui-guide-home-driver-hidden');
            }

            window.addEventListener('keydown', this.keydownHandler, true);
            window.addEventListener('pagehide', this.pageHideHandler, true);
            window.addEventListener('neko:yui-guide:tutorial-end', this.tutorialEndHandler, true);
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

        clearIntroFlow(preserveSpotlight) {
            if (this.introThirdMessageTimer) {
                window.clearTimeout(this.introThirdMessageTimer);
                this.introThirdMessageTimer = null;
            }

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
                return this.getChatInputTarget() || this.getChatWindowTarget() || fallbackTarget;
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
            this.focusAndHighlightChatInput(this.getChatInputTarget() || this.getChatWindowTarget());
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

            return this.waitForElement(() => this.getChatIntroTarget(), 5000);
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

        appendGuideChatMessage(text) {
            const content = typeof text === 'string' ? text.trim() : '';
            if (!content) {
                return;
            }

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

                host.appendMessage({
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
                });
                this.scrollChatToBottom();
                return;
            }

            if (typeof window.appendMessage === 'function') {
                window.appendMessage(content, 'gemini', true);
                this.scrollChatToBottom();
            }
        }

        focusAndHighlightChatInput(spotlightTarget) {
            const target = spotlightTarget || this.getChatInputTarget() || this.getChatWindowTarget();
            const inputBox = this.resolveElement('#react-chat-window-root .composer-input')
                || this.resolveElement('#textInputBox');

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
            const inputArea = this.getChatIntroActivationTarget();
            const inputBox = this.resolveElement('#react-chat-window-root .composer-input')
                || this.resolveElement('#textInputBox');
            if (!inputArea) {
                return;
            }

            const activate = () => {
                if (this.destroyed || this.introClickActivated) {
                    return;
                }
                if (!this.introFlowCompleted) {
                    return;
                }

                this.introClickActivated = true;
                inputArea.classList.remove('yui-guide-chat-target');
                this.clearIntroFlow(true);
                this.highlightChatWindow();
                this.sendIntroFollowups();
            };

            const clickHandler = function () {
                activate();
            };

            inputArea.addEventListener('click', clickHandler, true);
            this.chatIntroCleanupFns.push(() => {
                inputArea.removeEventListener('click', clickHandler, true);
            });

            if (inputBox && inputBox !== inputArea) {
                inputBox.addEventListener('click', clickHandler, true);
                this.chatIntroCleanupFns.push(() => {
                    inputBox.removeEventListener('click', clickHandler, true);
                });
            }
        }

        sendIntroFollowups() {
            const proactiveStep = this.getStep('intro_proactive');
            const catPawStep = this.getStep('intro_cat_paw');

            (async () => {
                if (proactiveStep && proactiveStep.performance) {
                    const proactiveText = proactiveStep.performance.bubbleText || '';
                    this.appendGuideChatMessage(proactiveText);
                    if (proactiveStep.performance.emotion) {
                        this.emotionBridge.apply(proactiveStep.performance.emotion);
                    }
                    await this.speakLineAndWait(proactiveText);
                }
                if (this.destroyed) {
                    return;
                }

                await new Promise((resolve) => {
                    this.introThirdMessageTimer = window.setTimeout(() => {
                        this.introThirdMessageTimer = null;
                        resolve();
                    }, 5000);
                });

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
            const spotlightTarget = await this.ensureChatVisible();
            this.focusAndHighlightChatInput(spotlightTarget);
            const introText = introStep.performance.bubbleText || '';
            this.appendGuideChatMessage(introText);
            if (introStep.performance.emotion) {
                this.emotionBridge.apply(introStep.performance.emotion);
            }
            this.attachChatIntroActivation();
            await this.speakLineAndWait(introText);
            if (this.destroyed) {
                return;
            }
            this.introFlowCompleted = true;
            this.highlightChatWindow();
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

            if (stepId === 'takeover_plugin_preview') {
                this.overlay.hidePluginPreview();
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
                const menuVisible = await this.ensureSettingsMenuVisible(settingsMenuId);
                if (runId !== this.sceneRunId || this.destroyed) {
                    return;
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

                        const spotlightTarget = menuVisible
                            ? this.getSettingsMenuElement(settingsMenuId)
                            : (this.isManagedPanelVisible('settings') ? this.getManagedPanelElement('settings') : null);
                        if (!spotlightTarget) {
                            return;
                        }

                        settingsMenuHighlighted = true;
                        this.overlay.activateSpotlight(spotlightTarget);
                    }
                });
                if (runId !== this.sceneRunId || this.destroyed) {
                    return;
                }
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
            this.cancelActiveNarration();
            this.clearIntroFlow();
            this.clearSceneTimers();
            this.disableInterrupts();
            this.voiceQueue.stop();
            this.cursor.cancel();
            this.cursor.hide();
            this.emotionBridge.clear();
            this.closeManagedPanels().catch((error) => {
                console.warn('[YuiGuide] 销毁时关闭首页面板失败:', error);
            });
            this.overlay.hidePluginPreview();
            this.overlay.hideBubble();
            this.overlay.setAngry(false);
            this.overlay.setTakingOver(false);
            this.overlay.destroy();
            window.removeEventListener('keydown', this.keydownHandler, true);
            window.removeEventListener('pagehide', this.pageHideHandler, true);
            window.removeEventListener('neko:yui-guide:tutorial-end', this.tutorialEndHandler, true);
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

        onTutorialEndEvent(event) {
            const detail = event && event.detail ? event.detail : null;
            if (!detail || detail.page !== this.page) {
                return;
            }

            this.lastTutorialEndReason = detail.reason || null;
        }
    }

    window.createYuiGuideDirector = function createYuiGuideDirector(options) {
        return new YuiGuideDirector(options);
    };
})();
