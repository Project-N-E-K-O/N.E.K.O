(function (root, factory) {
    'use strict';

    const api = factory();
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    }
    if (root) {
        root.TutorialVisualRuntime = api;
    }
})(typeof window !== 'undefined' ? window : globalThis, function () {
    'use strict';

    function getLegacyScene(context) {
        if (context && context.legacyScene) {
            return context.legacyScene;
        }
        const scene = context && context.scene ? context.scene : null;
        if (scene && scene.legacyScene) {
            return scene.legacyScene;
        }
        return scene || {};
    }

    function getDirector(context) {
        return context && context.director ? context.director : null;
    }

    function getSceneAudio(context) {
        const scene = context && context.scene ? context.scene : {};
        return scene.audio || {};
    }

    function safeCall(target, methodName, fallbackValue, ...args) {
        if (!target || typeof target[methodName] !== 'function') {
            return fallbackValue;
        }
        return target[methodName](...args);
    }

    function shouldResolveViaSceneTarget(target, scene, role) {
        if (!scene || scene.id !== 'day4_model_lock' || typeof target !== 'string') {
            return false;
        }
        const targetKey = role === 'secondary' ? scene.secondary : scene.target;
        return !!targetKey && target === targetKey;
    }

    class VisualRuntime {
        constructor(director, options) {
            this.director = director || null;
            this.options = options || {};
        }

        async resolveTarget(target, context, role) {
            if (!target) {
                return null;
            }
            if (typeof target !== 'string') {
                return target;
            }
            const legacyScene = getLegacyScene(context);
            if (
                shouldResolveViaSceneTarget(target, legacyScene, role)
                && this.director
                && typeof this.director.resolveAvatarFloatingTarget === 'function'
            ) {
                const sceneTarget = await this.director.resolveAvatarFloatingTarget(legacyScene, role || 'primary');
                if (sceneTarget) {
                    return sceneTarget;
                }
            }
            if (this.director && typeof this.director.resolveAvatarFloatingSelector === 'function') {
                return await this.director.resolveAvatarFloatingSelector(target);
            }
            if (this.director && typeof this.director.resolveElement === 'function') {
                return this.director.resolveElement(target);
            }
            return target;
        }

        registerCommands(commandRegistry) {
            if (!commandRegistry || typeof commandRegistry.register !== 'function') {
                return false;
            }
            commandRegistry.register('chat.message', (event, context) => this.handleChatMessage(event, context));
            commandRegistry.register('emotion.set', (event, context) => this.handleEmotionSet(event, context));
            commandRegistry.register('spotlight.show', (event, context) => this.handleSpotlightShow(event, context));
            commandRegistry.register('spotlight.clear', (event, context) => this.handleSpotlightClear(event, context));
            commandRegistry.register('cursor.move', (event, context) => this.handleCursorMove(event, context));
            commandRegistry.register('cursor.hold', (event, context) => this.handleCursorHold(event, context));
            commandRegistry.register('cursor.click', (event, context) => this.handleCursorClick(event, context));
            commandRegistry.register('cursor.wobble', (event, context) => this.handleCursorWobble(event, context));
            commandRegistry.register('operation.run', (event, context) => this.handleOperationRun(event, context));
            commandRegistry.register('compactToolWheel.rotateGalgameIntoCenter', (event, context) => (
                this.handleCompactToolWheelRotateGalgameIntoCenter(event, context)
            ));
            commandRegistry.register('settingsTour.play', (event, context) => this.handleSettingsTourPlay(event, context));
            commandRegistry.register('settingsPanel.close', (event, context) => this.handleSettingsPanelClose(event, context));
            commandRegistry.register('petal.play', (event, context) => this.handlePetalPlay(event, context));
            commandRegistry.register('avatarStandIn.show', (event, context) => this.handleAvatarStandInShow(event, context));
            commandRegistry.register('lifecycle.cleanup', (event, context) => this.handleLifecycleCleanup(event, context));
            return true;
        }

        handleChatMessage(event, context) {
            const director = getDirector(context) || this.director;
            const legacyScene = getLegacyScene(context);
            const text = event.text || safeCall(director, 'resolveAvatarFloatingSceneText', '', legacyScene);
            if (!text || !director || typeof director.appendGuideChatMessage !== 'function') {
                return false;
            }
            director.appendGuideChatMessage(text, {
                textKey: event.textKey || legacyScene.textKey || '',
                voiceKey: event.voiceKey || (getSceneAudio(context).voiceKey || legacyScene.voiceKey || ''),
                buttons: Array.isArray(event.buttons) ? event.buttons : []
            });
            return true;
        }

        handleEmotionSet(event, context) {
            const director = getDirector(context) || this.director;
            const legacyScene = getLegacyScene(context);
            const emotion = event.emotion || legacyScene.emotion || '';
            if (!emotion || !director || typeof director.applyGuideEmotion !== 'function') {
                return false;
            }
            director.applyGuideEmotion(emotion);
            return true;
        }

        async handleSpotlightShow(event, context) {
            const director = getDirector(context) || this.director;
            if (!director) {
                return false;
            }
            const legacyScene = getLegacyScene(context);
            const isFirstDailyInputIntro = !!(
                context
                && context.isFirstDailyScene
                && typeof director.isAvatarFloatingInputIntroScene === 'function'
                && director.isAvatarFloatingInputIntroScene(legacyScene)
            );
            if (
                isFirstDailyInputIntro
                && typeof director.isHomeChatExternalized === 'function'
                && director.isHomeChatExternalized()
                && director.interactionTakeover
                && typeof director.interactionTakeover.setExternalizedChatSpotlight === 'function'
            ) {
                const introKind = typeof director.getAvatarFloatingIntroExternalizedSpotlightKind === 'function'
                    ? director.getAvatarFloatingIntroExternalizedSpotlightKind(legacyScene)
                    : 'capsule-input';
                const normalizedIntroKind = introKind || 'capsule-input';
                if (typeof director.clearHomeSpotlightsForExternalizedChat === 'function') {
                    director.clearHomeSpotlightsForExternalizedChat();
                }
                director.interactionTakeover.setExternalizedChatSpotlight(normalizedIntroKind);
                if (typeof director.interactionTakeover.setExternalizedChatCursor === 'function') {
                    const cursorOptions = typeof director.getAvatarFloatingIntroExternalizedCursorOptions === 'function'
                        ? director.getAvatarFloatingIntroExternalizedCursorOptions(legacyScene)
                        : { effect: '', durationMs: 0 };
                    director.interactionTakeover.setExternalizedChatCursor(normalizedIntroKind, cursorOptions);
                }
                if (typeof director.hideHomeCursorForExternalizedChat === 'function') {
                    director.hideHomeCursorForExternalizedChat();
                }
                return true;
            }
            if (
                !isFirstDailyInputIntro
                && typeof director.isHomeChatExternalized === 'function'
                && director.isHomeChatExternalized()
                && director.interactionTakeover
                && typeof director.interactionTakeover.setExternalizedChatSpotlight === 'function'
                && typeof director.getExternalizedChatTargetKind === 'function'
            ) {
                const persistentKind = typeof event.persistent === 'string' && event.persistent
                    ? director.getExternalizedChatTargetKind(event.persistent, legacyScene)
                    : '';
                const primaryKind = director.getExternalizedChatTargetKind(
                    event.target || event.primary || '',
                    legacyScene
                );
                const cursorAction = legacyScene && typeof legacyScene.cursorAction === 'string'
                    ? legacyScene.cursorAction
                    : '';
                const shouldPreferPrimaryKind = !!(cursorAction && cursorAction !== 'hold');
                const externalizedSpotlightKind = shouldPreferPrimaryKind
                    ? (primaryKind || persistentKind)
                    : (persistentKind || primaryKind);
                if (externalizedSpotlightKind) {
                    if (typeof director.clearHomeSpotlightsForExternalizedChat === 'function') {
                        director.clearHomeSpotlightsForExternalizedChat();
                    }
                    director.interactionTakeover.setExternalizedChatSpotlight(externalizedSpotlightKind);
                    return true;
                }
            }
            if (typeof director.applyGuideHighlights !== 'function') {
                return false;
            }
            const primary = isFirstDailyInputIntro && typeof director.getAvatarFloatingIntroSpotlightTarget === 'function'
                ? director.getAvatarFloatingIntroSpotlightTarget(legacyScene)
                : await this.resolveTarget(event.target || event.primary || '', context, 'primary');
            const persistent = await this.resolveTarget(event.persistent || '', context);
            const secondary = await this.resolveTarget(event.secondary || '', context, 'secondary');
            if (
                director
                && typeof director.applyAvatarFloatingSceneSpotlightVariant === 'function'
            ) {
                director.applyAvatarFloatingSceneSpotlightVariant(legacyScene, primary);
            }
            director.applyGuideHighlights({
                key: event.key || legacyScene.id || '',
                persistent,
                primary,
                secondary
            });
            return true;
        }

        handleSpotlightClear(event, context) {
            const director = getDirector(context) || this.director;
            if (!director) {
                return false;
            }
            if (event.channel === 'persistent' && director.overlay && typeof director.overlay.clearPersistentSpotlight === 'function') {
                director.overlay.clearPersistentSpotlight();
                return true;
            }
            if (event.channel === 'action' && director.overlay && typeof director.overlay.clearActionSpotlight === 'function') {
                director.overlay.clearActionSpotlight();
                return true;
            }
            if (typeof director.clearAllVirtualSpotlights === 'function') {
                director.clearAllVirtualSpotlights();
            }
            if (director.overlay && typeof director.overlay.clearPersistentSpotlight === 'function') {
                director.overlay.clearPersistentSpotlight();
            }
            if (director.overlay && typeof director.overlay.clearActionSpotlight === 'function') {
                director.overlay.clearActionSpotlight();
            }
            if (
                director.interactionTakeover
                && typeof director.interactionTakeover.setExternalizedChatSpotlight === 'function'
            ) {
                director.interactionTakeover.setExternalizedChatSpotlight('');
            }
            return true;
        }

        async handleCursorMove(event, context) {
            const director = getDirector(context) || this.director;
            if (!director) {
                return false;
            }
            const durationMs = Number.isFinite(event.durationMs) ? Math.max(0, Math.floor(event.durationMs)) : 760;
            const legacyScene = getLegacyScene(context);
            const cursorScene = Object.assign({}, legacyScene, {
                target: event.target || legacyScene.target || '',
                cursorTarget: event.cursorTarget || legacyScene.cursorTarget || '',
                cursorAction: event.action || legacyScene.cursorAction || 'move',
                cursorMoveDurationMs: durationMs
            });
            if (
                director
                && typeof director.isHomeChatExternalized === 'function'
                && director.isHomeChatExternalized()
                && typeof director.getExternalizedChatCursorTargetKind === 'function'
                && typeof director.setExternalizedChatCursorEffect === 'function'
                && typeof director.waitForExternalizedChatCursorMove === 'function'
            ) {
                const cursorKind = director.getExternalizedChatCursorTargetKind(cursorScene);
                if (cursorKind) {
                    director.setExternalizedChatCursorEffect(cursorKind, 'move', {
                        durationMs
                    });
                    const waitMs = durationMs > 0 ? durationMs + 500 : undefined;
                    return await director.waitForExternalizedChatCursorMove(cursorScene.id || '', waitMs);
                }
            }
            const target = await this.resolveTarget(event.target || '', context, 'primary');
            if (target && typeof director.moveCursorToElement === 'function') {
                return await director.moveCursorToElement(target, durationMs, {
                    exactDuration: true
                });
            }
            if (target && director.cursor && typeof director.getElementRect === 'function') {
                const rect = director.getElementRect(target);
                if (rect && typeof director.cursor.showAt === 'function') {
                    director.cursor.showAt(rect.left + rect.width / 2, rect.top + rect.height / 2);
                    return true;
                }
            }
            return false;
        }

        handleCursorHold(event, context) {
            const director = getDirector(context) || this.director;
            const legacyScene = getLegacyScene(context);
            const durationMs = Number.isFinite(event.durationMs)
                ? Math.max(0, Math.floor(event.durationMs))
                : 0;
            const effect = typeof event.effect === 'string' ? event.effect : '';
            if (
                director
                && typeof director.isHomeChatExternalized === 'function'
                && director.isHomeChatExternalized()
                && typeof director.getExternalizedChatCursorTargetKind === 'function'
                && typeof director.setExternalizedChatCursorEffect === 'function'
            ) {
                const cursorScene = Object.assign({}, legacyScene, {
                    target: event.target || legacyScene.cursorTarget || legacyScene.target || '',
                    cursorTarget: event.target || legacyScene.cursorTarget || legacyScene.target || '',
                    cursorAction: 'hold'
                });
                const cursorKind = director.getExternalizedChatCursorTargetKind(cursorScene);
                if (cursorKind) {
                    director.setExternalizedChatCursorEffect(cursorKind, effect, {
                        durationMs,
                        effectDurationMs: 0,
                        freezePoint: event.freezePoint === true
                    });
                }
            }
            return true;
        }

        async handleCursorClick(event, context) {
            const director = getDirector(context) || this.director;
            if (!director) {
                return false;
            }
            const durationMs = Number.isFinite(event.effectDurationMs)
                ? Math.max(0, Math.floor(event.effectDurationMs))
                : 420;
            const dispatchOnStart = () => {
                if (!Array.isArray(event.onStart) || !context || !context.commandRegistry) {
                    return Promise.resolve([]);
                }
                return Promise.all(event.onStart.map((command) => (
                    context.commandRegistry.dispatch(command, context)
                )));
            };
            const prepareOnStartDispatcher = async () => {
                if (!Array.isArray(event.onStart) || !context || !context.commandRegistry) {
                    return () => Promise.resolve([]);
                }
                const preparedCommands = await Promise.all(event.onStart.map(async (command) => {
                    if (!command || command.command !== 'operation.run') {
                        return command;
                    }
                    const operationScene = Object.assign({}, getLegacyScene(context), {
                        operation: command.operation || getLegacyScene(context).operation || ''
                    });
                    const primaryTarget = await this.resolveTarget(
                        command.target || operationScene.target || '',
                        context,
                        'primary'
                    );
                    return Object.assign({}, command, { primaryTarget });
                }));
                return () => Promise.all(preparedCommands.map((command) => (
                    context.commandRegistry.dispatch(command, context)
                )));
            };
            const legacyScene = getLegacyScene(context);
            const cursorScene = Object.assign({}, legacyScene, {
                target: event.target || legacyScene.target || '',
                cursorTarget: event.cursorTarget || legacyScene.cursorTarget || event.target || '',
                cursorAction: 'click'
            });
            if (
                typeof director.isHomeChatExternalized === 'function'
                && director.isHomeChatExternalized()
                && typeof director.getExternalizedChatCursorTargetKind === 'function'
                && typeof director.setExternalizedChatCursorEffect === 'function'
            ) {
                if (typeof director.waitForExternalizedChatCursorMove === 'function') {
                    const moveDurationMs = Number.isFinite(legacyScene.cursorMoveDurationMs)
                        ? Math.max(0, Math.floor(legacyScene.cursorMoveDurationMs))
                        : 760;
                    await director.waitForExternalizedChatCursorMove(
                        legacyScene.id || '',
                        moveDurationMs > 0 ? moveDurationMs + 500 : undefined
                    );
                }
                const cursorKind = director.getExternalizedChatCursorTargetKind(cursorScene);
                if (cursorKind) {
                    const clickStarted = director.setExternalizedChatCursorEffect(cursorKind, 'click', {
                        effectDurationMs: durationMs
                    });
                    const dispatchPreparedOnStart = await prepareOnStartDispatcher();
                    if (durationMs > 0 && typeof director.waitForSceneDelay === 'function') {
                        await director.waitForSceneDelay(durationMs);
                        await dispatchPreparedOnStart();
                    } else {
                        await dispatchPreparedOnStart();
                    }
                    return clickStarted !== false;
                }
            }
            if (typeof director.clickCursorAndWait === 'function') {
                await Promise.all([
                    director.clickCursorAndWait(durationMs),
                    dispatchOnStart()
                ]);
                return true;
            }
            if (director.cursor && typeof director.cursor.click === 'function') {
                director.cursor.click(durationMs);
                await dispatchOnStart();
                return true;
            }
            return false;
        }

        async handleCursorWobble(event, context) {
            const director = getDirector(context) || this.director;
            if (!director) {
                return false;
            }
            const durationMs = Number.isFinite(event.durationMs)
                ? Math.max(0, Math.floor(event.durationMs))
                : 360;
            const legacyScene = getLegacyScene(context);
            const cursorScene = Object.assign({}, legacyScene, {
                target: event.target || legacyScene.cursorTarget || legacyScene.target || '',
                cursorTarget: event.cursorTarget || legacyScene.cursorTarget || event.target || '',
                cursorAction: 'wobble'
            });
            if (
                typeof director.isHomeChatExternalized === 'function'
                && director.isHomeChatExternalized()
                && typeof director.getExternalizedChatCursorTargetKind === 'function'
                && typeof director.setExternalizedChatCursorEffect === 'function'
            ) {
                const cursorKind = director.getExternalizedChatCursorTargetKind(cursorScene);
                if (cursorKind) {
                    const started = director.setExternalizedChatCursorEffect(cursorKind, 'wobble', {
                        effectDurationMs: durationMs
                    });
                    if (durationMs > 0 && typeof director.waitForSceneDelay === 'function') {
                        await director.waitForSceneDelay(durationMs);
                    }
                    return started !== false;
                }
            }
            if (!director.cursor || typeof director.cursor.wobble !== 'function') {
                return false;
            }
            director.cursor.wobble(durationMs);
            if (durationMs > 0 && typeof director.waitForSceneDelay === 'function') {
                await director.waitForSceneDelay(durationMs);
            }
            return true;
        }

        async handleOperationRun(event, context) {
            const director = getDirector(context) || this.director;
            if (!director) {
                return false;
            }
            const legacyScene = Object.assign({}, getLegacyScene(context), {
                operation: event.operation || (getLegacyScene(context).operation || '')
            });
            if (
                event.trigger === 'afterCursorMove'
                && typeof director.isHomeChatExternalized === 'function'
                && director.isHomeChatExternalized()
                && typeof director.waitForExternalizedChatCursorMove === 'function'
            ) {
                const durationMs = Number.isFinite(legacyScene.cursorMoveDurationMs)
                    ? Math.max(0, Math.floor(legacyScene.cursorMoveDurationMs))
                    : 760;
                await director.waitForExternalizedChatCursorMove(
                    legacyScene.id || '',
                    durationMs > 0 ? durationMs + 500 : undefined
                );
            }
            let primaryTarget = Object.prototype.hasOwnProperty.call(event, 'primaryTarget')
                ? event.primaryTarget
                : null;
            if (!primaryTarget) {
                primaryTarget = await this.resolveTarget(event.target || legacyScene.target || '', context, 'primary');
            }
            if (typeof director.runAvatarFloatingSceneOperation === 'function') {
                return await director.runAvatarFloatingSceneOperation(
                    legacyScene,
                    primaryTarget,
                    context ? context.narrationStartedAt : 0,
                    context ? context.narrationPromise : null
                );
            }
            if (director.operationRegistry && typeof director.operationRegistry.run === 'function') {
                return await director.operationRegistry.run(
                    legacyScene,
                    primaryTarget,
                    context ? context.narrationStartedAt : 0,
                    context ? context.narrationPromise : null
                );
            }
            return false;
        }

        async handleCompactToolWheelRotateGalgameIntoCenter(event, context) {
            const director = getDirector(context) || this.director;
            if (!director || typeof director.runDay3GalgameWheelDragScene !== 'function') {
                return false;
            }
            const legacyScene = getLegacyScene(context);
            const target = await this.resolveTarget(event.target || legacyScene.target || 'chat-galgame');
            return await director.runDay3GalgameWheelDragScene(legacyScene, target);
        }

        async handleSettingsTourPlay(event, context) {
            const director = getDirector(context) || this.director;
            if (
                !director
                || !director.settingsTourFlow
                || typeof director.settingsTourFlow.play !== 'function'
            ) {
                return false;
            }
            const legacyScene = getLegacyScene(context);
            return await director.settingsTourFlow.play(legacyScene, {
                sceneRunId: context ? context.sceneRunId : undefined,
                previousSceneId: context ? context.previousSceneId : undefined,
                index: context ? context.index : undefined,
                total: context ? context.total : undefined
            });
        }

        async handleSettingsPanelClose(event, context) {
            const director = getDirector(context) || this.director;
            if (!director) {
                return false;
            }
            const panelId = event.panel || event.panelId || 'settings';
            if (typeof director.forceHideManagedPanel === 'function') {
                director.forceHideManagedPanel(panelId);
            } else if (panelId === 'settings' && typeof director.closeSettingsPanel === 'function') {
                await director.closeSettingsPanel();
            }
            if (
                event.collapseSidePanels !== false
                && typeof director.collapseAvatarFloatingSidePanelsExcept === 'function'
            ) {
                director.collapseAvatarFloatingSidePanelsExcept(null);
            }
            return true;
        }

        async handlePetalPlay(event, context) {
            const director = getDirector(context) || this.director;
            const legacyScene = getLegacyScene(context);
            const audio = getSceneAudio(context);
            if (
                director
                && typeof director.playAvatarFloatingPetalTransitionAtCue === 'function'
            ) {
                return await director.playAvatarFloatingPetalTransitionAtCue(
                    legacyScene,
                    context ? context.sceneRunId : 0,
                    audio.voiceKey || legacyScene.voiceKey || '',
                    audio.text || legacyScene.text || '',
                    context ? context.narrationStartedAt : 0
                );
            }
            if (director && director.cursor && typeof director.cursor.hide === 'function') {
                director.cursor.hide();
            }
            if (director && typeof director.clearAllVirtualSpotlights === 'function') {
                director.clearAllVirtualSpotlights();
            }
            return true;
        }

        handleAvatarStandInShow(event, context) {
            const director = getDirector(context) || this.director;
            if (!director || typeof director.scheduleAvatarStandInForScene !== 'function') {
                return false;
            }
            const legacyScene = Object.assign({}, getLegacyScene(context), {
                avatarStandIn: Object.assign({}, getLegacyScene(context).avatarStandIn || {}, event.avatarStandIn || {})
            });
            director.scheduleAvatarStandInForScene(
                legacyScene,
                context ? context.day : 0,
                context ? context.sceneRunId : 0
            );
            return true;
        }

        async handleLifecycleCleanup(event, context) {
            const director = getDirector(context) || this.director;
            if (!director) {
                return false;
            }
            if (typeof director.closeAvatarFloatingGuidePanels === 'function') {
                await director.closeAvatarFloatingGuidePanels({
                    clearCursor: event.clearCursor === true
                });
            }
            if (event.clearSpotlights !== false && typeof director.clearAllVirtualSpotlights === 'function') {
                director.clearAllVirtualSpotlights();
            }
            return true;
        }
    }

    function createTutorialVisualRuntime(director, options) {
        return new VisualRuntime(director, options);
    }

    return {
        VisualRuntime,
        createTutorialVisualRuntime
    };
});
