(function () {
    'use strict';

    const DEFAULT_INTERRUPT_DISTANCE = 18;
    const DEFAULT_STEP_DELAY_MS = 120;
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
            this.enabled = !!window.speechSynthesis;
        }

        stop() {
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
        }

        speak(text) {
            const message = typeof text === 'string' ? text.trim() : '';
            if (!message) {
                return Promise.resolve();
            }

            this.stop();

            if (!this.enabled || typeof SpeechSynthesisUtterance === 'undefined') {
                return Promise.resolve();
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
                    resolve();
                };

                const utterance = new SpeechSynthesisUtterance(message);
                utterance.lang = 'zh-CN';
                utterance.rate = 1.02;
                utterance.pitch = 1.05;
                utterance.volume = 0.9;
                utterance.onend = finish;
                utterance.onerror = finish;
                this.currentUtterance = utterance;
                this.currentFallbackTimer = window.setTimeout(finish, 6500);

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
            this.lastInterruptAt = 0;
            this.lastPointerPoint = null;
            this.angryExitTriggered = false;
            this.destroyed = false;
            this.lastTutorialEndReason = null;
            this.introFlowStarted = false;
            this.introFlowCompleted = false;
            this.introClickActivated = false;
            this.introThirdMessageTimer = null;
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

        clearIntroFlow() {
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

            this.overlay.clearSpotlight();
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

        disableInterrupts() {
            if (!this.interruptsEnabled) {
                return;
            }

            window.removeEventListener('mousemove', this.pointerMoveHandler, true);
            window.removeEventListener('mousedown', this.pointerDownHandler, true);
            this.interruptsEnabled = false;
            this.lastPointerPoint = null;
        }

        enableInterrupts(step) {
            const performance = (step && step.performance) || {};
            if (performance.interruptible === false) {
                this.disableInterrupts();
                return;
            }

            this.disableInterrupts();
            this.interruptCount = 0;
            this.lastInterruptAt = 0;
            this.lastPointerPoint = null;
            window.addEventListener('mousemove', this.pointerMoveHandler, true);
            window.addEventListener('mousedown', this.pointerDownHandler, true);
            this.interruptsEnabled = true;
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

        appendGuideChatMessage(text) {
            const content = typeof text === 'string' ? text.trim() : '';
            if (!content || typeof window.appendMessage !== 'function') {
                return;
            }

            window.appendMessage(content, 'gemini', true);
        }

        focusAndHighlightChatInput(spotlightTarget) {
            const target = spotlightTarget || this.getChatIntroTarget();
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
                this.overlay.activateSpotlight(target);
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

                this.introClickActivated = true;
                inputArea.classList.remove('yui-guide-chat-target');
                this.clearIntroFlow();
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

            if (proactiveStep && proactiveStep.performance) {
                const proactiveText = proactiveStep.performance.bubbleText || '';
                this.appendGuideChatMessage(proactiveText);
                this.voiceQueue.speak(proactiveText);
                if (proactiveStep.performance.emotion) {
                    this.emotionBridge.apply(proactiveStep.performance.emotion);
                }
            }

            this.introThirdMessageTimer = window.setTimeout(() => {
                this.introThirdMessageTimer = null;
                if (this.destroyed) {
                    return;
                }

                if (catPawStep && catPawStep.performance) {
                    const catPawText = catPawStep.performance.bubbleText || '';
                    this.appendGuideChatMessage(catPawText);
                    this.voiceQueue.speak(catPawText);
                    if (catPawStep.performance.emotion) {
                        this.emotionBridge.apply(catPawStep.performance.emotion);
                    }
                }

                this.introFlowCompleted = true;
            }, 5000);
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
            this.appendGuideChatMessage(introStep.performance.bubbleText || '');
            this.voiceQueue.speak(introStep.performance.bubbleText || '');
            if (introStep.performance.emotion) {
                this.emotionBridge.apply(introStep.performance.emotion);
            }
            this.attachChatIntroActivation();
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

            this.currentSceneId = stepId;
            this.currentStep = this.getStep(stepId);
            this.currentContext = context || null;
            await this.playScene(stepId, {
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

            this.clearSceneTimers();
            this.overlay.setAngry(false);

            if (isTakeoverScene && stepId !== 'takeover_return_control') {
                this.overlay.setTakingOver(true);
            }

            if (stepId !== 'takeover_plugin_preview') {
                this.overlay.hidePluginPreview();
            }

            if (performance.bubbleText) {
                this.overlay.showBubble(performance.bubbleText, {
                    title: 'Yui',
                    emotion: performance.emotion || 'neutral',
                    anchorRect: anchorRect
                });
            } else {
                this.overlay.hideBubble();
            }

            if (performance.emotion) {
                this.emotionBridge.apply(performance.emotion);
            }

            this.voiceQueue.speak(performance.bubbleText || '');

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

            if (performance.cursorAction === 'move' || performance.cursorAction === 'click' || performance.cursorAction === 'wobble') {
                if (cursorTargetRect) {
                    await this.cursor.moveToRect(cursorTargetRect, { durationMs: durationMs });
                    if (runId !== this.sceneRunId || this.destroyed) {
                        return;
                    }
                }
            }

            if (performance.cursorAction === 'click') {
                this.cursor.click();
            } else if (performance.cursorAction === 'wobble') {
                this.cursor.wobble();
            }

            if (stepId === 'takeover_plugin_preview') {
                this.overlay.showPluginPreview(PREVIEW_ITEMS, {
                    title: '插件管理预演'
                });
            }

            if (stepId === 'takeover_return_control') {
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
                this.disableInterrupts();
                this.overlay.setTakingOver(false);
            }

            if (performance.interruptible === false) {
                this.disableInterrupts();
            } else if (isTakeoverScene || stepId === 'intro_cat_paw') {
                this.enableInterrupts(step);
            } else {
                this.disableInterrupts();
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
            this.handleInterrupt(event);
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

            if (this.lastPointerPoint) {
                const dx = x - this.lastPointerPoint.x;
                const dy = y - this.lastPointerPoint.y;
                if (Math.hypot(dx, dy) < DEFAULT_INTERRUPT_DISTANCE) {
                    return;
                }
            }
            this.lastPointerPoint = { x: x, y: y };

            const now = Date.now();
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

            this.overlay.showBubble(message, {
                title: 'Yui',
                emotion: performance.emotion || 'surprised',
                anchorRect: null
            });
            this.emotionBridge.apply(performance.emotion || 'surprised');
            this.voiceQueue.speak(message);
            this.cursor.resistTo(x, y);

            this.schedule(() => {
                if (this.destroyed || this.angryExitTriggered || !this.currentStep) {
                    return;
                }

                const currentPerformance = this.currentStep.performance || {};
                if (currentPerformance.bubbleText) {
                    this.overlay.showBubble(currentPerformance.bubbleText, {
                        title: 'Yui',
                        emotion: currentPerformance.emotion || 'neutral',
                        anchorRect: this.resolveRect(this.currentStep.anchor)
                    });
                }
                if (currentPerformance.emotion) {
                    this.emotionBridge.apply(currentPerformance.emotion);
                }
            }, 900);
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
            this.overlay.showBubble(performance.bubbleText || '人类~~~~！你真的很没礼貌喵！', {
                title: 'Yui',
                emotion: performance.emotion || 'angry',
                anchorRect: null
            });
            this.emotionBridge.apply(performance.emotion || 'angry');
            this.voiceQueue.speak(performance.bubbleText || '');

            await wait(1200);
            if (this.destroyed) {
                return;
            }

            this.skip(source || 'angry_exit', 'angry_exit');
        }

        skip(reason, tutorialReason) {
            if (this.destroyed) {
                return;
            }

            const finalReason = tutorialReason || reason || 'skip';
            if (this.tutorialManager && typeof this.tutorialManager.requestTutorialDestroy === 'function') {
                this.tutorialManager.requestTutorialDestroy(finalReason);
            } else {
                this.destroy();
            }
        }

        destroy() {
            if (this.destroyed) {
                return;
            }

            this.destroyed = true;
            if (this.page === 'home') {
                document.body.classList.remove('yui-guide-home-driver-hidden');
            }
            this.clearIntroFlow();
            this.clearSceneTimers();
            this.disableInterrupts();
            this.voiceQueue.stop();
            this.cursor.cancel();
            this.cursor.hide();
            this.emotionBridge.clear();
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
