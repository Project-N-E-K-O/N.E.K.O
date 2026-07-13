(function (namespace) {
    'use strict';

    const {
        resolveGuideLocale,
        INTRO_GREETING_REPLY_TEXT,
        INTRO_GREETING_REPLY_TEXT_KEY,
        DEFAULT_SPOTLIGHT_PADDING,
        clamp,
        formatGuideDebugText,
        estimateGuideChatStreamDurationMs
    } = namespace;

    namespace.extendDirector({
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
        },

        getGuideAssistantName() {
            const candidates = [
                window.__NEKO_TUTORIAL_ASSISTANT_NAME_OVERRIDE__,
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
        },

        getGuideAssistantAvatarUrl() {
            if (window.appChatAvatar && typeof window.appChatAvatar.getCurrentAvatarDataUrl === 'function') {
                const avatarUrl = window.appChatAvatar.getCurrentAvatarDataUrl();
                if (typeof avatarUrl === 'string' && avatarUrl.trim()) {
                    return avatarUrl.trim();
                }
            }

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
        },

        scrollChatToBottom(options) {
            const messageList = this.resolveElement('#react-chat-window-root .message-list');
            if (!messageList) {
                return;
            }

            const normalizedOptions = options || {};
            const useSmoothScroll = normalizedOptions.behavior === 'smooth';
            const scroll = () => {
                try {
                    if (useSmoothScroll) {
                        messageList.scrollTo({
                            top: messageList.scrollHeight,
                            behavior: 'smooth'
                        });
                    } else {
                        messageList.scrollTop = messageList.scrollHeight;
                    }
                } catch (_) {
                    messageList.scrollTop = messageList.scrollHeight;
                }
            };

            scroll();
            window.requestAnimationFrame(scroll);
            if (useSmoothScroll) {
                this.schedule(scroll, 160);
            }
        },

        cloneGuideChatMessageWithText(message, text, status) {
            const cloned = Object.assign({}, message || {});
            cloned.blocks = [{ type: 'text', text: text }];
            cloned.status = status;
            return cloned;
        },

        updateGuideChatMessage(messageId, patch) {
            if (!messageId || !patch || typeof patch !== 'object') {
                return null;
            }

            if (this.isHomeChatExternalized()) {
                this.postExternalChatGuideMessage({
                    action: 'yui_guide_update_chat_message',
                    messageId: messageId,
                    patch: patch,
                    timestamp: Date.now()
                });
                return null;
            }

            const host = window.reactChatWindowHost;
            if (host && typeof host.updateMessage === 'function') {
                const updatedMessage = host.updateMessage(messageId, patch);
                this.scrollChatToBottom();
                return updatedMessage;
            }

            return null;
        },

        clearGuideChatMessages() {
            const now = Date.now();
            if (
                !this.destroyed
                && !this.terminationRequested
                && !this.angryExitTriggered
                && this.latestGuideChatMessageRetainId
                && this.latestGuideChatMessageRetainUntilMs > now
            ) {
                const retainedMessageId = this.latestGuideChatMessageRetainId;
                const delayMs = Math.max(0, this.latestGuideChatMessageRetainUntilMs - now);
                if (this.latestGuideChatMessageRetainTimer) {
                    window.clearTimeout(this.latestGuideChatMessageRetainTimer);
                    this.latestGuideChatMessageRetainTimer = null;
                }
                this.latestGuideChatMessageRetainTimer = window.setTimeout(() => {
                    this.latestGuideChatMessageRetainTimer = null;
                    if (
                        !this.destroyed
                        && this.latestGuideChatMessageRetainId === retainedMessageId
                        && Date.now() >= this.latestGuideChatMessageRetainUntilMs
                    ) {
                        this.latestGuideChatMessageRetainId = '';
                        this.latestGuideChatMessageRetainUntilMs = 0;
                        this.clearGuideChatMessages();
                    }
                }, delayMs);
                return false;
            }

            this.latestGuideChatMessageRetainId = '';
            this.latestGuideChatMessageRetainUntilMs = 0;
            if (this.isHomeChatExternalized()) {
                this.postExternalChatGuideMessage({
                    action: 'yui_guide_clear_chat_messages',
                    timestamp: Date.now()
                });
                return true;
            }

            const host = window.reactChatWindowHost;
            if (host && typeof host.clearGuideMessages === 'function') {
                return !!host.clearGuideMessages();
            }

            return false;
        },

        resolveGuideChatStreamDurationMs(content, options) {
            const normalizedOptions = options || {};
            if (Number.isFinite(normalizedOptions.streamDurationMs)) {
                const explicitDurationMs = Math.round(normalizedOptions.streamDurationMs);
                return explicitDurationMs > 0 ? clamp(explicitDurationMs, 720, 24000) : 0;
            }

            const voiceDurationMs = this.getGuideVoiceDurationMs(
                normalizedOptions.voiceKey,
                resolveGuideLocale()
            );
            if (voiceDurationMs > 0) {
                return voiceDurationMs;
            }

            return estimateGuideChatStreamDurationMs(content);
        },

        streamGuideChatMessage(message, content, options) {
            const fullText = typeof content === 'string' ? content : '';
            const textUnits = Array.from(fullText);
            const total = textUnits.length;
            if (!message || !message.id || total <= 0) {
                return;
            }

            let index = Math.min(
                total,
                Math.max(0, Math.round((options && options.initialVisibleTextLength) || 0))
            );
            const durationMs = Math.max(0, Math.round(
                this.resolveGuideChatStreamDurationMs(fullText, options)
            ));
            if (durationMs <= 0) {
                this.updateGuideChatMessage(message.id, {
                    blocks: message.blocks,
                    actions: message.actions,
                    status: 'sent'
                });
                return;
            }

            let elapsedActiveMs = 0;
            let lastTickAt = Date.now();
            let waitingForResume = false;
            const pauseWithScene = !(options && options.streamPauseWithScene === false);
            const allowDuringAngryExit = !!(options && options.streamAllowDuringAngryExit);
            const tickMs = clamp(Math.round(durationMs / Math.max(total, 1)), 28, 90);
            const step = () => {
                if (
                    this.destroyed
                    || this.terminationRequested
                    || (this.angryExitTriggered && !allowDuringAngryExit)
                ) {
                    return;
                }

                if (pauseWithScene && this.scenePausedForResistance) {
                    if (!waitingForResume) {
                        const pauseStartedAt = Number.isFinite(this.scenePausedAt) && this.scenePausedAt > 0
                            ? this.scenePausedAt
                            : Date.now();
                        elapsedActiveMs += Math.max(0, pauseStartedAt - lastTickAt);
                        waitingForResume = true;
                        this.waitUntilSceneResumed().then(() => {
                            waitingForResume = false;
                            lastTickAt = Date.now();
                            if (
                                !this.destroyed
                                && !this.terminationRequested
                                && (!this.angryExitTriggered || allowDuringAngryExit)
                            ) {
                                this.scheduleGuideChatStream(step, Math.min(80, tickMs));
                            }
                        });
                    }
                    return;
                }

                const now = Date.now();
                elapsedActiveMs += Math.max(0, now - lastTickAt);
                lastTickAt = now;
                if (elapsedActiveMs >= durationMs) {
                    this.updateGuideChatMessage(message.id, {
                        blocks: message.blocks,
                        actions: message.actions,
                        status: 'sent'
                    });
                    return;
                }

                const progress = clamp(elapsedActiveMs / durationMs, 0, 1);
                const nextIndex = Math.max(index, Math.min(total, Math.ceil(progress * total)));
                if (nextIndex > index) {
                    index = nextIndex;
                    this.updateGuideChatMessage(message.id, {
                        blocks: [{
                            type: 'text',
                            text: textUnits.slice(0, index).join('')
                        }],
                        actions: undefined,
                        status: 'streaming'
                    });
                }

                this.scheduleGuideChatStream(step, Math.min(tickMs, durationMs - elapsedActiveMs));
            };

            this.scheduleGuideChatStream(step, Math.min(80, tickMs));
        },

        appendGuideChatMessage(text, options) {
            const normalizedOptions = options || {};
            const content = formatGuideDebugText(
                normalizedOptions.textKey || '',
                typeof text === 'string' ? text.trim() : ''
            );
            if (!content) {
                return null;
            }

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

            const initialVisibleText = Array.from(content).slice(0, 1).join('');
            const streamOptions = Object.assign({}, normalizedOptions, {
                initialVisibleTextLength: Array.from(initialVisibleText).length
            });
            const streamingMessage = this.cloneGuideChatMessageWithText(message, initialVisibleText, 'streaming');
            streamingMessage.actions = undefined;
            const retainDurationMs = this.getGuideVoiceDurationMs(
                normalizedOptions.voiceKey || '',
                resolveGuideLocale()
            );
            if (retainDurationMs > 0) {
                this.latestGuideChatMessageRetainId = message.id;
                this.latestGuideChatMessageRetainUntilMs = createdAt + retainDurationMs;
            } else {
                this.latestGuideChatMessageRetainId = '';
                this.latestGuideChatMessageRetainUntilMs = 0;
            }

            // Electron Pet 模式下首页聊天被拆到独立 /chat 窗口，这里优先通过
            // BroadcastChannel 把教程消息转发过去；只有转发失败时才回落到 overlay。
            if (this.isHomeChatExternalized()) {
                if (this.postExternalChatGuideMessage({
                    action: 'yui_guide_append_chat_message',
                    message: streamingMessage,
                    timestamp: createdAt
                })) {
                    this.streamGuideChatMessage(message, content, streamOptions);
                    return message;
                }

                try {
                    this.showGuideBubble(content, {
                        title: this.getGuideAssistantName(),
                        emotion: 'neutral'
                    }, this.currentSceneId);
                } catch (error) {
                    console.warn('[YuiGuide] 兜底气泡展示失败:', error);
                }
                return null;
            }

            const host = window.reactChatWindowHost;
            if (host && typeof host.appendMessage === 'function') {
                const appendedMessage = host.appendMessage(streamingMessage);
                this.scrollChatToBottom();
                this.streamGuideChatMessage(message, content, streamOptions);
                return appendedMessage;
            }

            if (typeof window.appendMessage === 'function') {
                window.appendMessage(content, 'gemini', true);
                this.scrollChatToBottom();
            }

            return null;
        },

        focusAndHighlightChatInput(spotlightTarget) {
            const target = spotlightTarget || this.getChatInputTarget();
            const inputBox = this.resolveElement('#react-chat-window-root .composer-input')
                || this.resolveElement('#textInputBox');

            if (this.isHomeChatExternalized()) {
                if (this.interactionTakeover && typeof this.interactionTakeover.setExternalizedChatSpotlight === 'function') {
                    this.clearHomeSpotlightsForExternalizedChat();
                    this.interactionTakeover.setExternalizedChatSpotlight('input');
                }
                return;
            }

            if (!target) {
                return;
            }

            if (target && typeof target.scrollIntoView === 'function') {
                target.scrollIntoView({
                    behavior: 'auto',
                    block: 'center',
                    inline: 'nearest'
                });
            }

            if (target) {
                this.setSpotlightGeometryHint(target, {
                    padding: DEFAULT_SPOTLIGHT_PADDING + 3
                });
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
        },

        async playIntroGreetingReply() {
            const greetingReplyText = this.resolveGuideCopy(
                INTRO_GREETING_REPLY_TEXT_KEY,
                INTRO_GREETING_REPLY_TEXT
            );
            if (!greetingReplyText) {
                return;
            }

            this.appendGuideChatMessage(greetingReplyText, {
                textKey: INTRO_GREETING_REPLY_TEXT_KEY,
                voiceKey: 'intro_greeting_reply'
            });
            if (
                this.isHomeChatExternalized()
                && this.interactionTakeover
                && typeof this.interactionTakeover.setExternalizedChatCursor === 'function'
            ) {
                this.interactionTakeover.setExternalizedChatCursor('capsule-input', {
                    effect: '',
                    durationMs: 0
                });
            }
            await Promise.all([
                this.speakGuideLine(greetingReplyText, {
                    voiceKey: 'intro_greeting_reply'
                }),
                this.runIntroGreetingHugPerformance().catch(() => {}),
                this.runIntroGiftHeartPerformance().catch(() => {})
            ]);
            this.clearIntroGreetingChatHighlight();
        },

        async runDailyIntroGreetingPerformance(scene, day, options) {
            return this.runDailyIntroAvatarPerformance(Object.assign({}, scene || {}, {
                introAvatarPerformance: Object.assign({
                    preset: 'wave-zoom'
                }, (scene && scene.introAvatarPerformance) || {})
            }), day, options);
        },

        async runDailyIntroAvatarPerformance(scene, day, options) {
            const normalizedOptions = options || {};
            const api = window.YuiGuideAvatarStage;
            let revealed = false;
            const resolveOnReveal = normalizedOptions.isFirstDailyScene === true;
            let revealReadyResolve = null;
            let revealReadySettled = false;
            let revealReadyFallbackTimer = 0;
            const revealReadyPromise = new Promise((resolve) => {
                revealReadyResolve = resolve;
            });
            const revealReadyFallbackMs = Number.isFinite(Number(normalizedOptions.revealReadyFallbackMs))
                ? Math.max(0, Math.floor(Number(normalizedOptions.revealReadyFallbackMs)))
                : 1600;
            const resolveRevealReady = (value) => {
                if (revealReadySettled) {
                    return;
                }
                revealReadySettled = true;
                if (revealReadyFallbackTimer) {
                    window.clearTimeout(revealReadyFallbackTimer);
                    revealReadyFallbackTimer = 0;
                }
                if (typeof revealReadyResolve === 'function') {
                    revealReadyResolve(value);
                }
            };
            const revealPrepared = typeof normalizedOptions.revealPrepared === 'function'
                ? function revealDailyIntroPrepared(reason) {
                    if (revealed) {
                        return;
                    }
                    revealed = true;
                    normalizedOptions.revealPrepared(reason || 'daily-intro-avatar-performance');
                    resolveRevealReady(true);
                }
                : null;
            if (!api || typeof api.playAvatarMotion !== 'function') {
                if (revealPrepared) {
                    revealPrepared('daily-intro-avatar-stage-unavailable');
                }
                resolveRevealReady(false);
                return resolveOnReveal ? revealReadyPromise : null;
            }
            const performance = scene && scene.introAvatarPerformance
                ? scene.introAvatarPerformance
                : {};
            const voiceKey = scene && scene.voiceKey ? scene.voiceKey : '';
            const text = scene && scene.text ? scene.text : '';
            const durationMs = Number.isFinite(Number(performance.durationMs))
                ? Math.max(0, Math.floor(Number(performance.durationMs)))
                : this.getAvatarFloatingNarrationDurationMs(voiceKey, text);
            const motionPromise = api.playAvatarMotion({
                preset: performance.preset || 'wave-zoom',
                position: performance.position || performance.targetPosition || '',
                durationMs: durationMs,
                restore: performance.restore || 'half-body',
                approachMs: Number.isFinite(Number(performance.approachMs))
                    ? Math.max(0, Math.floor(Number(performance.approachMs)))
                    : (Number.isFinite(normalizedOptions.approachMs)
                        ? Math.max(0, Math.floor(normalizedOptions.approachMs))
                        : 2200),
                settleMs: Number.isFinite(Number(performance.settleMs))
                    ? Math.max(0, Math.floor(Number(performance.settleMs)))
                    : (Number.isFinite(normalizedOptions.settleMs)
                        ? Math.max(0, Math.floor(normalizedOptions.settleMs))
                        : 1250),
                frameScale: Number.isFinite(Number(performance.frameScale))
                    ? Number(performance.frameScale)
                    : undefined,
                frameY: Number.isFinite(Number(performance.frameY))
                    ? Number(performance.frameY)
                    : undefined,
                enterMs: Number.isFinite(Number(performance.enterMs))
                    ? Math.max(0, Math.floor(Number(performance.enterMs)))
                    : undefined,
                releaseMs: Number.isFinite(Number(performance.releaseMs))
                    ? Math.max(0, Math.floor(Number(performance.releaseMs)))
                    : undefined,
                readyWaitMs: Number.isFinite(Number(performance.readyWaitMs))
                    ? Math.max(0, Math.floor(Number(performance.readyWaitMs)))
                    : undefined,
                freezeFloatingButtons: performance.freezeFloatingButtons === false ? false : undefined,
                rotateFloatingButtons: performance.rotateFloatingButtons === true,
                revealPrepared: revealPrepared,
                reducedMotion: typeof normalizedOptions.reducedMotion === 'boolean'
                    ? normalizedOptions.reducedMotion
                    : this.shouldReduceTutorialMotion(),
                isCancelled: typeof normalizedOptions.isCancelled === 'function'
                    ? normalizedOptions.isCancelled
                    : () => this.isStopping()
            });
            if (resolveOnReveal) {
                if (!revealReadySettled && revealReadyFallbackMs > 0 && typeof window.setTimeout === 'function') {
                    revealReadyFallbackTimer = window.setTimeout(() => {
                        if (revealPrepared) {
                            revealPrepared('daily-intro-avatar-reveal-timeout');
                            return;
                        }
                        resolveRevealReady(false);
                    }, revealReadyFallbackMs);
                }
                motionPromise.then(
                    () => {
                        resolveRevealReady(true);
                    },
                    (error) => {
                        console.warn('[YuiGuide] 每日开场模型演出失败:', error);
                        if (revealPrepared) {
                            revealPrepared('daily-intro-avatar-motion-failed');
                            return;
                        }
                        resolveRevealReady(false);
                    }
                );
                return revealReadyPromise;
            }
            return motionPromise;
        },

        async runIntroGreetingHugPerformance() {
            return this.runDailyIntroGreetingPerformance({ id: 'day1_intro_greeting' });
        },

        async runIntroGiftHeartPerformance() {
            if (!(await this.waitForNarrationCue(
                'intro_greeting_reply',
                'showIntroGiftHeart'
            ))) {
                return null;
            }
            if (this.isStopping()) {
                return null;
            }

            const api = window.YuiGuideAvatarStage;
            if (!api || typeof api.playIntroGiftHeart !== 'function') {
                return null;
            }
            return api.playIntroGiftHeart({
                durationMs: 2600,
                releaseMs: 420,
                reducedMotion: this.shouldReduceTutorialMotion(),
                isCancelled: () => this.isStopping()
            });
        },

        async runReturnControlCueWavePerformance() {
            const api = window.YuiGuideAvatarStage;
            if (!api || typeof api.playReturnControlCueWave !== 'function') {
                return null;
            }
            return api.playReturnControlCueWave({
                durationMs: 4200,
                reducedMotion: this.shouldReduceTutorialMotion(),
                isCancelled: () => this.isStopping()
            });
        },

        async startIntroVoiceCursorLookAtPerformance() {
            const api = window.YuiGuideAvatarStage;
            if (!api || typeof api.startIntroVoiceCursorLookAt !== 'function') {
                return null;
            }
            try {
                return await api.startIntroVoiceCursorLookAt({
                    getPoint: () => this.overlay && typeof this.overlay.getCursorPosition === 'function'
                        ? this.overlay.getCursorPosition()
                        : null,
                    isCancelled: () => this.isStopping()
                });
            } catch (error) {
                console.warn('[YuiGuide] 语音入口目光跟随动作启动失败:', error);
                return null;
            }
        },

        async startGhostCursorLookAtPerformance(options) {
            const normalizedOptions = options || {};
            if (normalizedOptions.preferExistingHandle !== false) {
                const existingHandle = this.persistentGhostCursorLookAtHandle || this.preTakeoverGhostCursorLookAtHandle;
                if (existingHandle && typeof existingHandle.stop === 'function') {
                    return existingHandle;
                }
            }
            const api = window.YuiGuideAvatarStage;
            if (!api || typeof api.startIntroVoiceCursorLookAt !== 'function') {
                return null;
            }
            const cancelCheck = typeof normalizedOptions.isCancelled === 'function'
                ? normalizedOptions.isCancelled
                : () => this.isStopping();
            try {
                return await api.startIntroVoiceCursorLookAt({
                    getPoint: () => this.overlay && typeof this.overlay.getCursorPosition === 'function'
                        ? this.overlay.getCursorPosition()
                        : null,
                    isCancelled: cancelCheck
                });
            } catch (error) {
                console.warn('[YuiGuide] Ghost cursor 目光跟随动作启动失败:', error);
                return null;
            }
        },

        async ensurePreTakeoverGhostCursorLookAtPerformance(options) {
            const existingHandle = this.preTakeoverGhostCursorLookAtHandle;
            if (existingHandle && typeof existingHandle.stop === 'function') {
                return existingHandle;
            }

            const createdHandle = await this.startGhostCursorLookAtPerformance(options || {});
            if (createdHandle && typeof createdHandle.stop === 'function') {
                this.preTakeoverGhostCursorLookAtHandle = createdHandle;
            }
            return this.preTakeoverGhostCursorLookAtHandle;
        },

        async ensurePersistentGhostCursorLookAtPerformance(options) {
            const existingHandle = this.persistentGhostCursorLookAtHandle;
            if (existingHandle && typeof existingHandle.stop === 'function') {
                return existingHandle;
            }

            const createdHandle = await this.startGhostCursorLookAtPerformance(options || {});
            if (createdHandle && typeof createdHandle.stop === 'function') {
                this.persistentGhostCursorLookAtHandle = createdHandle;
            }
            return this.persistentGhostCursorLookAtHandle;
        },

        async stopIntroVoiceCursorLookAtPerformance(handle, reason) {
            if (!handle || typeof handle.stop !== 'function') {
                return;
            }
            if (this.preTakeoverGhostCursorLookAtHandle === handle) {
                this.preTakeoverGhostCursorLookAtHandle = null;
            }
            if (this.persistentGhostCursorLookAtHandle === handle) {
                this.persistentGhostCursorLookAtHandle = null;
            }
            try {
                await handle.stop(reason || 'intro_voice_showcase_complete');
            } catch (_) {}
        },

        adoptPreTakeoverGhostCursorLookAtHandle() {
            if (
                !this.persistentGhostCursorLookAtHandle
                && this.preTakeoverGhostCursorLookAtHandle
                && typeof this.preTakeoverGhostCursorLookAtHandle.stop === 'function'
            ) {
                this.persistentGhostCursorLookAtHandle = this.preTakeoverGhostCursorLookAtHandle;
            }
            this.preTakeoverGhostCursorLookAtHandle = null;
        },

        async stopPersistentGhostCursorLookAtPerformance(reason) {
            const handle = this.persistentGhostCursorLookAtHandle;
            this.persistentGhostCursorLookAtHandle = null;
            if (!handle || typeof handle.stop !== 'function') {
                return;
            }
            try {
                await handle.stop(reason || 'ghost_cursor_look_at_complete');
            } catch (_) {}
        },

        async ensureGuideIdleSwayPerformance() {
            const existingHandle = this.guideIdleSwayHandle;
            if (existingHandle && typeof existingHandle.stop === 'function') {
                return existingHandle;
            }

            const api = window.YuiGuideAvatarStage;
            if (!api || typeof api.startGuideIdleSway !== 'function') {
                return null;
            }
            try {
                const handle = await api.startGuideIdleSway({
                    reducedMotion: this.shouldReduceTutorialMotion(),
                    isCancelled: () => this.isStopping()
                });
                if (handle && typeof handle.stop === 'function') {
                    this.guideIdleSwayHandle = handle;
                }
                return this.guideIdleSwayHandle;
            } catch (error) {
                console.warn('[YuiGuide] 教程常驻轻微晃动启动失败:', error);
                return null;
            }
        },

        async stopGuideIdleSwayPerformance(reason) {
            const handle = this.guideIdleSwayHandle;
            this.guideIdleSwayHandle = null;
            if (!handle || typeof handle.stop !== 'function') {
                return;
            }
            try {
                await handle.stop(reason || 'guide_idle_sway_complete');
            } catch (_) {}
        },

        async startAvatarCornerPeekPerformance(options) {
            const api = window.YuiGuideAvatarStage;
            if (!api || typeof api.startAvatarCornerPeek !== 'function') {
                return null;
            }
            const normalizedOptions = options || {};
            try {
                return await api.startAvatarCornerPeek({
                    position: normalizedOptions.position,
                    targetPreset: normalizedOptions.targetPreset,
                    performanceLockKey: normalizedOptions.performanceLockKey,
                    reducedMotion: normalizedOptions.reducedMotion === true || this.shouldReduceTutorialMotion(),
                    isCancelled: typeof normalizedOptions.isCancelled === 'function'
                        ? normalizedOptions.isCancelled
                        : () => this.isStopping()
                });
            } catch (error) {
                console.warn('[YuiGuide] Live2D 探身动作启动失败:', error);
                return null;
            }
        },

        async runSettingsPeekPanicPerformance(options) {
            const api = window.YuiGuideAvatarStage;
            if (!api || typeof api.playSettingsPeekPanic !== 'function') {
                return null;
            }
            const normalizedOptions = options || {};
            try {
                return await api.playSettingsPeekPanic({
                    targetRect: normalizedOptions.targetRect || null,
                    totalDurationMs: normalizedOptions.totalDurationMs,
                    reducedMotion: this.shouldReduceTutorialMotion(),
                    isCancelled: () => (
                        (Number.isFinite(normalizedOptions.runId) && normalizedOptions.runId !== this.sceneRunId)
                        || this.isStopping()
                    )
                });
            } catch (error) {
                console.warn('[YuiGuide] 设置一瞥慌乱动作启动失败:', error);
                return null;
            }
        },

        async runInterruptResistPerformance(options) {
            const api = window.YuiGuideAvatarStage;
            if (!api || typeof api.playInterruptResist !== 'function') {
                return null;
            }
            const normalizedOptions = options || {};
            const voiceDurationMs = normalizedOptions.voiceKey
                ? this.getGuideVoiceDurationMs(normalizedOptions.voiceKey, resolveGuideLocale())
                : 0;
            const totalDurationMs = Number.isFinite(normalizedOptions.totalDurationMs)
                ? Math.max(0, Math.round(normalizedOptions.totalDurationMs))
                : (voiceDurationMs > 0 ? clamp(Math.round(voiceDurationMs), 960, 7600) : undefined);
            try {
                return await api.playInterruptResist({
                    pointerX: normalizedOptions.x,
                    pointerY: normalizedOptions.y,
                    totalDurationMs: totalDurationMs,
                    reducedMotion: this.shouldReduceTutorialMotion(),
                    isCancelled: () => this.isStopping()
                });
            } catch (error) {
                console.warn('[YuiGuide] 轻微打断动作启动失败:', error);
                return null;
            }
        },

        applyAngryExitEmotionFallback() {
            this.applyGuideEmotion('angry', {
                allowDuringInterrupt: true
            });
        },

        async runAngryExitPerformance(options) {
            const api = window.YuiGuideAvatarStage;
            if (!api || typeof api.playAngryExit !== 'function') {
                this.applyAngryExitEmotionFallback();
                return null;
            }
            const normalizedOptions = options || {};
            const voiceDurationMs = normalizedOptions.voiceKey
                ? this.getGuideVoiceDurationMs(normalizedOptions.voiceKey, resolveGuideLocale())
                : 0;
            const totalDurationMs = Number.isFinite(normalizedOptions.totalDurationMs)
                ? Math.max(0, Math.round(normalizedOptions.totalDurationMs))
                : (voiceDurationMs > 0 ? clamp(Math.round(voiceDurationMs), 1200, 16000) : undefined);
            try {
                const result = await api.playAngryExit({
                    pointerX: normalizedOptions.x,
                    pointerY: normalizedOptions.y,
                    totalDurationMs: totalDurationMs,
                    reducedMotion: this.shouldReduceTutorialMotion(),
                    isCancelled: () => this.isStopping()
                });
                if (result && result.result !== 'played') {
                    this.applyAngryExitEmotionFallback();
                }
                return result;
            } catch (error) {
                console.warn('[YuiGuide] 生气退出动作启动失败:', error);
                this.applyAngryExitEmotionFallback();
                return null;
            }
        },

        async stopPluginDashboardCornerPeekPerformance(handle, reason) {
            if (!handle || typeof handle.stop !== 'function') {
                return;
            }
            try {
                await handle.stop(reason || 'plugin_dashboard_closed');
            } catch (_) {}
        },

        async stopAvatarStandInPerformance(reason) {
            const handle = this.avatarStandInPerformanceHandle;
            this.avatarStandInPerformanceHandle = null;
            if (!handle || typeof handle.stop !== 'function') {
                return;
            }
            await this.stopAvatarCornerPeekPerformance(handle, reason || 'avatar_standin_clear');
        },

        async stopAvatarCornerPeekPerformance(handle, reason) {
            if (!handle || typeof handle.stop !== 'function') {
                return;
            }
            try {
                await handle.stop(reason || 'avatar_corner_peek_clear');
            } catch (_) {}
        },

        async runWakeupPrelude() {
            if (this.page !== 'home' || this.isStopping() || !this.wakeup || typeof this.wakeup.run !== 'function') {
                if (typeof document !== 'undefined' && document.body) {
                    document.body.classList.remove('yui-guide-live2d-preparing');
                }
                await this.ensureGuideIdleSwayPerformance();
                return;
            }

            if (this.interactionTakeover && typeof this.interactionTakeover.applyFaceForwardLock === 'function') {
                this.interactionTakeover.applyFaceForwardLock();
            }
            try {
                const result = await this.wakeup.run();
                this.recordExperienceMetric('wakeup_result', {
                    result: result && result.result ? result.result : '',
                    reason: result && result.reason ? result.reason : ''
                });
            } catch (error) {
                console.warn('[YuiGuide] 入场苏醒播放失败，继续教程:', error);
                this.recordExperienceMetric('wakeup_result', {
                    result: 'fallback',
                    reason: 'exception'
                });
            }
            await this.ensureGuideIdleSwayPerformance();
        },

        // Electron Pet 模式专用 prelude：聊天输入框不在首页窗口里，
        // 因此跳过首页点击激活，但后续旁白与高亮演示照常执行。
    });
})(window.__YuiGuideDirector);
