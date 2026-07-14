(function (namespace) {
    'use strict';

    const {
        getAvatarFloatingGuideActiveRound,
        recordAvatarFloatingGuideRoundEnd,
        DEFAULT_USER_CURSOR_REVEAL_DISTANCE,
        DEFAULT_USER_CURSOR_REVEAL_INTERVAL_MS,
        DEFAULT_USER_CURSOR_REVEAL_MOVES,
        DEFAULT_INTERRUPT_COUNT_CURSOR_REVEAL_MS,
        PLUGIN_DASHBOARD_READY_EVENT,
        PLUGIN_DASHBOARD_DONE_EVENT,
        PLUGIN_DASHBOARD_INTERRUPT_REQUEST_EVENT,
        PLUGIN_DASHBOARD_INTERRUPT_ACK_EVENT,
        DESKTOP_PLUGIN_DASHBOARD_SKIP_REQUEST_EVENT,
        PLUGIN_DASHBOARD_SKIP_REQUEST_EVENT
    } = namespace;

    namespace.extendDirector({
        onPointerMove(event) {
            this.handleInterrupt(event);
        },

        onPointerDown(event) {
            this.resistanceController.recordPointerDown(event);
        },

        handleInterrupt(event) {
            return this.resistanceController.handleInterrupt(event);
        },

        noteUserCursorRevealSuppressionAttempt(distance, now) {
            if (
                this.userCursorRevealSuppressed
                || !Number.isFinite(distance)
                || distance < DEFAULT_USER_CURSOR_REVEAL_DISTANCE
                || !document.body.classList.contains('yui-taking-over')
            ) {
                return;
            }

            if (now - this.lastUserCursorRevealMoveAt < DEFAULT_USER_CURSOR_REVEAL_INTERVAL_MS) {
                return;
            }

            this.lastUserCursorRevealMoveAt = now;
            this.userCursorRevealMoveCount += 1;
            if (this.userCursorRevealMoveCount >= DEFAULT_USER_CURSOR_REVEAL_MOVES) {
                this.suppressUserCursorReveal();
            }
        },

        suppressUserCursorReveal() {
            if (this.destroyed || !document.body) {
                return;
            }

            if (this.resistanceCursorTimer) {
                window.clearTimeout(this.resistanceCursorTimer);
                this.resistanceCursorTimer = null;
            }

            this.userCursorRevealSuppressed = true;
            this.clearInterruptCountCursorReveal(false);
            document.documentElement.style.cursor = '';
            document.body.style.cursor = '';
            document.documentElement.classList.remove('yui-user-cursor-revealed');
            document.documentElement.classList.remove('yui-resistance-cursor-reveal');
            document.body.classList.remove('yui-user-cursor-revealed');
            document.body.classList.remove('yui-resistance-cursor-reveal');
            this.syncSystemCursorHidden(true, 'user_cursor_reveal_suppressed');
        },

        clearUserCursorRevealSuppression(resetCursor) {
            if (this.resistanceCursorTimer) {
                window.clearTimeout(this.resistanceCursorTimer);
                this.resistanceCursorTimer = null;
            }

            this.userCursorRevealSuppressed = false;
            this.userCursorRevealMoveCount = 0;
            this.lastUserCursorRevealMoveAt = 0;
            this.clearInterruptCountCursorReveal(false);

            if (document.body) {
                document.documentElement.classList.remove('yui-user-cursor-revealed');
                document.documentElement.classList.remove('yui-resistance-cursor-reveal');
                document.body.classList.remove('yui-user-cursor-revealed');
                document.body.classList.remove('yui-resistance-cursor-reveal');
            }

            if (resetCursor) {
                document.documentElement.style.cursor = '';
                if (document.body) {
                    document.body.style.cursor = '';
                }
            }
        },

        suppressResistanceCursorReveal() {
            if (this.userCursorRevealSuppressed) {
                this.suppressUserCursorReveal();
                return;
            }

            if (this.resistanceCursorTimer) {
                window.clearTimeout(this.resistanceCursorTimer);
                this.resistanceCursorTimer = null;
            }
            this.clearInterruptCountCursorReveal(false);
            document.documentElement.style.cursor = '';
            document.body.style.cursor = '';
            document.documentElement.classList.remove('yui-user-cursor-revealed');
            document.documentElement.classList.remove('yui-resistance-cursor-reveal');
            document.body.classList.remove('yui-user-cursor-revealed');
            document.body.classList.remove('yui-resistance-cursor-reveal');
            this.syncSystemCursorHidden(true, 'resistance_cursor_reveal_suppressed');
        },

        clearInterruptCountCursorReveal(resetCursor) {
            if (this.resistanceCursorTimer) {
                window.clearTimeout(this.resistanceCursorTimer);
                this.resistanceCursorTimer = null;
            }
            if (document.body) {
                document.documentElement.classList.remove('yui-interrupt-count-cursor-revealed');
                document.body.classList.remove('yui-interrupt-count-cursor-revealed');
            }
            if (resetCursor) {
                document.documentElement.style.cursor = '';
                if (document.body) {
                    document.body.style.cursor = '';
                }
            }
        },

        revealRealCursorForInterruptCount(durationMs = DEFAULT_INTERRUPT_COUNT_CURSOR_REVEAL_MS) {
            if (this.destroyed || !document.body) {
                return;
            }
            this.clearInterruptCountCursorReveal(false);
            document.documentElement.style.cursor = '';
            document.body.style.cursor = '';
            document.documentElement.classList.add('yui-interrupt-count-cursor-revealed');
            document.body.classList.add('yui-interrupt-count-cursor-revealed');
            this.syncSystemCursorHidden(false, 'interrupt_count_reveal');
            this.resistanceCursorTimer = window.setTimeout(() => {
                this.resistanceCursorTimer = null;
                if (this.angryExitTriggered) {
                    return;
                }
                this.clearInterruptCountCursorReveal(true);
                if (
                    this.destroyed
                    || !document.body
                    || !document.body.classList.contains('yui-taking-over')
                ) {
                    return;
                }
                this.syncSystemCursorHidden(true, 'interrupt_count_reveal_timeout');
            }, Math.max(0, Math.floor(Number(durationMs) || 0)));
        },

        syncSystemCursorHidden(hidden, reason = 'tutorial') {
            if (
                window.YuiGuideCommon
                && typeof window.YuiGuideCommon.syncPcSystemCursorHidden === 'function'
            ) {
                window.YuiGuideCommon.syncPcSystemCursorHidden(hidden === true, reason);
            }
        },

        revealSystemCursorTemporarily(durationMs = 2000, reason = 'tutorial-temporary-reveal') {
            const normalizedDurationMs = Math.min(10000, Math.max(0, Math.floor(Number(durationMs) || 0)));
            if (this.resistanceCursorTimer) {
                window.clearTimeout(this.resistanceCursorTimer);
                this.resistanceCursorTimer = null;
            }
            if (document.body) {
                document.documentElement.classList.add('yui-user-cursor-revealed', 'yui-resistance-cursor-reveal');
                document.body.classList.add('yui-user-cursor-revealed', 'yui-resistance-cursor-reveal');
            }
            if (
                window.YuiGuideCommon
                && typeof window.YuiGuideCommon.syncPcSystemCursorTemporaryReveal === 'function'
            ) {
                window.YuiGuideCommon.syncPcSystemCursorTemporaryReveal(normalizedDurationMs, reason);
            }
            this.resistanceCursorTimer = window.setTimeout(() => {
                this.resistanceCursorTimer = null;
                this.suppressResistanceCursorReveal();
            }, normalizedDurationMs);
        },

        playLightResistance(x, y, options) {
            return this.resistanceController.playLightResistance(x, y, options);
        },

        async abortAsAngryExit(source) {
            return this.resistanceController.abortAsAngryExit(source);
        },

        waitForAngryExitPresentationCompletion() {
            const promise = this.angryExitPresentationPromise;
            if (promise && typeof promise.then === 'function') {
                return promise.catch(() => {});
            }
            return Promise.resolve();
        },

        recordAvatarFloatingGuideRoundEndForTermination(reason) {
            if (getAvatarFloatingGuideActiveRound() === 1) {
                recordAvatarFloatingGuideRoundEnd(1);
            }
        },

        requestTermination(reason, tutorialReason) {
            return this.terminationRouter.requestTermination(reason, tutorialReason);
        },

        skip(reason, tutorialReason) {
            return this.terminationRouter.skip(reason, tutorialReason);
        },

        destroy() {
            if (this.destroyed) {
                return;
            }

            this.destroyed = true;
            this.terminationRequested = true;
            this.clearInterruptCountCursorReveal(true);
            this.syncSystemCursorHidden(false, 'destroy');
            this.setHomePcCursorOutputSuppressedForExternalizedChat(false);
            this.restoreDay1TakeoverAgentSwitches('destroy').catch((error) => {
                console.warn('[YuiGuide] 销毁时恢复 Day1 Agent 开关失败:', error);
            });
            this.stopPluginDashboardCornerPeekPerformance(this.takeoverTopPeekHandle, 'destroy').catch(() => {});
            this.takeoverTopPeekHandle = null;
            this.stopGuideIdleSwayPerformance('destroy').catch(() => {});
            if (this.preTakeoverGhostCursorLookAtHandle) {
                this.stopIntroVoiceCursorLookAtPerformance(
                    this.preTakeoverGhostCursorLookAtHandle,
                    'destroy'
                ).catch(() => {});
            }
            this.stopPersistentGhostCursorLookAtPerformance('destroy').catch(() => {});
            if (this.interactionTakeover && typeof this.interactionTakeover.releaseFaceForwardLock === 'function') {
                this.interactionTakeover.releaseFaceForwardLock();
            }
            this.resumeCurrentSceneAfterResistance();
            if (this.interactionTakeover && typeof this.interactionTakeover.clearExternalizedChatFx === 'function') {
                this.interactionTakeover.clearExternalizedChatFx();
            }
            if (this.latestGuideChatMessageRetainTimer) {
                window.clearTimeout(this.latestGuideChatMessageRetainTimer);
                this.latestGuideChatMessageRetainTimer = null;
            }
            this.latestGuideChatMessageRetainId = '';
            this.latestGuideChatMessageRetainUntilMs = 0;
            this.clearGuideChatStreamTimers();
            this.clearGuideChatMessages();
            this.clearQueuedGuideChatBridgeMessages();
            if (this.interactionTakeover && typeof this.interactionTakeover.setExternalizedChatButtonsDisabled === 'function') {
                this.interactionTakeover.setExternalizedChatButtonsDisabled(false);
            }
            this.setGuideChatInputLocked(false, 'avatar-floating-guide-destroy');
            if (this.page === 'home') {
                document.body.classList.remove('yui-guide-home-ui-suppressed');
            }
            this.clearUserCursorRevealSuppression(true);
            this.manualPluginDashboardOpenAllowed = false;
            this.manualPluginDashboardOpenTarget = null;
            this.manualPluginDashboardOpenUserClicked = false;
            if (this.pluginDashboardHandoff && typeof this.pluginDashboardHandoff.resolve === 'function') {
                this.pluginDashboardHandoff.resolve(false);
            }
            this.cancelActiveNarration();
	            this.clearIntroFlow();
	            this.clearSceneTimers();
	            this.clearGuideChatStreamTimers();
            this.clearAvatarStandIn({ clearPending: true, restoreModel: true });
            this.clearPendingGuideMessageAction();
            this.uninstallGuideMessageActionHandler();
            if (this.wakeup && typeof this.wakeup.destroy === 'function') {
                this.wakeup.destroy();
            }
            if (this.resistanceController && typeof this.resistanceController.destroy === 'function') {
                this.resistanceController.destroy();
            }
            if (this.overlay && typeof this.overlay.setSpotlightSuppressed === 'function') {
                this.overlay.setSpotlightSuppressed(true);
            }
            this.disableInterrupts();
            if (this.voiceQueue && typeof this.voiceQueue.destroy === 'function') {
                this.voiceQueue.destroy();
            } else {
                this.voiceQueue.stop();
            }
            this.cursor.cancel();
            this.cursor.hide();
            this.clearAllVirtualSpotlights();
            this.clearSpotlightVariantHints();
            this.clearSpotlightGeometryHints();
            this.clearAllExtraSpotlights();
            if (this.spotlightController && typeof this.spotlightController.destroy === 'function') {
                this.spotlightController.destroy();
            }
            this.cleanupTutorialReturnButtons();
            this.customSecondarySpotlightTarget = null;
            this.clearGuidePresentation();
            this.forceHideAvatarFloatingGuideManagedSurfaces();
            this.hideTemporaryAvatarFloatingGuideHud('destroy');
            this.setAvatarFloatingToolbarVisible(true, 'destroy');
            this.closeManagedPanels().catch((error) => {
                console.warn('[YuiGuide] 销毁时关闭首页面板失败:', error);
            });
            this.notifyPluginDashboardTerminationRequested(this.lastTutorialEndReason || 'destroy');
            this.closePluginDashboardWindowIfCreatedByGuide('销毁');
            if (typeof window.handleShowMainUI === 'function') {
                try {
                    window.handleShowMainUI();
                } catch (error) {
                    console.warn('[YuiGuide] 销毁时恢复主界面失败:', error);
                }
            }
            this.performFullCleanup({
                destroyInteractionTakeover: true,
                destroyOverlay: true
            });
            window.removeEventListener('keydown', this.keydownHandler, true);
            window.removeEventListener('pagehide', this.pageHideHandler, true);
            window.removeEventListener('neko:yui-guide:external-chat-ready', this.externalChatReadyHandler, true);
            window.removeEventListener('neko:yui-guide:external-chat-cursor-anchor', this.externalChatCursorAnchorHandler, true);
            window.removeEventListener('neko:yui-guide:remote-termination-request', this.remoteTerminationRequestHandler, true);
            window.removeEventListener(DESKTOP_PLUGIN_DASHBOARD_SKIP_REQUEST_EVENT, this.desktopPluginDashboardSkipHandler, true);
            window.removeEventListener('neko:yui-guide:desktop-interrupt-request', this.desktopPluginDashboardInterruptHandler, true);
            window.removeEventListener('neko:yui-guide:tutorial-end', this.tutorialEndHandler, true);
            window.removeEventListener('message', this.messageHandler, true);
        },

        onKeyDown(event) {
            if (this.destroyed || !event || event.key !== 'Escape') {
                return;
            }

            if (this.hasOpenSystemDialog()) {
                return;
            }

            event.stopPropagation();
            this.skip('escape', 'skip');
        },

        onPageHide() {
            if (this.tutorialManager && typeof this.tutorialManager.requestTutorialEnd === 'function') {
                try {
                    Promise.resolve(this.tutorialManager.requestTutorialEnd('pagehide')).catch((error) => {
                        console.warn('[YuiGuide] pagehide tutorial end failed, falling back to destroy:', error);
                        this.destroy();
                    });
                } catch (error) {
                    console.warn('[YuiGuide] pagehide tutorial end threw, falling back to destroy:', error);
                    this.destroy();
                }
                return;
            }
            this.destroy();
        },

        hasOpenSystemDialog() {
            return !!document.querySelector([
                '#prominent-notice-overlay',
                '.modal-overlay',
                '.storage-location-completion-card:not([hidden])',
                '#storage-location-overlay:not([hidden])'
            ].join(', '));
        },

        onTutorialEndEvent(event) {
            const detail = event && event.detail ? event.detail : null;
            if (!detail || detail.page !== this.page) {
                return;
            }

            this.lastTutorialEndReason = detail.reason || null;
            this.destroy();
        },

        onRemoteTerminationRequest(event) {
            if (this.destroyed) {
                return;
            }

            const detail = event && event.detail ? event.detail : null;
            if (!detail) {
                return;
            }

            const targetPage = typeof detail.targetPage === 'string' ? detail.targetPage.trim() : '';
            if (targetPage && targetPage !== this.page) {
                return;
            }

            this.requestTermination(detail.reason || 'skip', detail.tutorialReason || 'skip');
        },

        async handlePluginDashboardInterruptRequest(event, handoff, data) {
            const requestId = typeof data.requestId === 'string' ? data.requestId : '';
            if (!requestId) {
                return;
            }

            const windowRef = handoff && handoff.windowRef ? handoff.windowRef : null;
            const targetOrigin = handoff && handoff.targetOrigin
                ? handoff.targetOrigin
                : this.getPluginDashboardExpectedOrigin();
            const postAck = () => {
                const ackPayload = {
                    type: PLUGIN_DASHBOARD_INTERRUPT_ACK_EVENT,
                    sessionId: typeof data.sessionId === 'string' ? data.sessionId : '',
                    requestId: requestId
                };
                this.dispatchDesktopPluginDashboardInterruptAck(ackPayload);

                if (!windowRef || windowRef.closed) {
                    return;
                }

                try {
                    windowRef.postMessage(ackPayload, targetOrigin);
                } catch (error) {
                    console.warn('[YuiGuide] 向插件面板发送 interrupt ack 失败:', error);
                }
            };

            if (this.pluginDashboardLastInterruptRequestId === requestId) {
                postAck();
                return;
            }
            this.pluginDashboardLastInterruptRequestId = requestId;

            const detail = data.detail && typeof data.detail === 'object' ? data.detail : {};
            const kind = typeof detail.kind === 'string' ? detail.kind : '';
            const text = typeof detail.text === 'string' ? detail.text : '';
            const textKey = typeof detail.textKey === 'string' ? detail.textKey : '';
            const voiceKey = typeof detail.voiceKey === 'string' ? detail.voiceKey : '';
            const resolvedText = this.resolveGuideCopy(textKey, text);
            const interruptCount = Number.isFinite(detail.interruptCount) ? Math.max(0, Math.floor(detail.interruptCount)) : null;
            const x = Number.isFinite(detail.x) ? detail.x : null;
            const y = Number.isFinite(detail.y) ? detail.y : null;

            if (interruptCount !== null) {
                this.interruptCount = Math.max(
                    Math.max(0, Math.floor(Number.isFinite(this.interruptCount) ? this.interruptCount : 0)),
                    interruptCount
                );
            }

            if (kind === 'interrupt_angry_exit') {
                await this.abortAsAngryExit('pointer_interrupt');
                postAck();
                return;
            }

            if (kind === 'interrupt_resist_light' && x !== null && y !== null) {
                try {
                    this.notifyPluginDashboardSystemCursorTemporaryReveal(2000, 'interrupt_resist_light');
                    await this.playLightResistance(x, y, {
                        suppressCursorReveal: true,
                        forceSystemCursorReveal: true
                    });
                } catch (error) {
                    console.warn('[YuiGuide] 执行插件面板轻微抵抗失败:', error);
                }
                postAck();
                return;
            }

            if (resolvedText) {
                this.appendGuideChatMessage(resolvedText, {
                    textKey: textKey,
                    voiceKey: voiceKey
                });
            }

            if (resolvedText) {
                try {
                    await this.speakGuideLine(resolvedText, {
                        voiceKey: voiceKey
                    });
                } catch (error) {
                    console.warn('[YuiGuide] 播放插件面板打断语音失败:', error);
                }
            }

            postAck();
        },

        handleDesktopYuiGuideSkipRequest(event) {
            if (this.destroyed) {
                return;
            }

            const payload = event && event.detail && typeof event.detail === 'object'
                ? event.detail
                : {};
            const handoff = this.pluginDashboardHandoff;
            if (!handoff || !handoff.sessionId) {
                return;
            }

            const sessionId = typeof payload.sessionId === 'string' ? payload.sessionId : '';
            if (sessionId && handoff.sessionId && sessionId !== handoff.sessionId) {
                return;
            }

            const detail = payload.detail && typeof payload.detail === 'object'
                ? Object.assign({}, payload.detail)
                : {};
            if (!detail.source && typeof payload.source === 'string') {
                detail.source = payload.source;
            }

            void this.handlePluginDashboardSkipRequest({
                type: PLUGIN_DASHBOARD_SKIP_REQUEST_EVENT,
                sessionId: sessionId,
                detail: detail
            });
        },

        onDesktopPluginDashboardInterruptRequest(event) {
            if (this.destroyed) {
                return;
            }

            const payload = event && event.detail && typeof event.detail === 'object'
                ? event.detail
                : {};
            const requestId = typeof payload.requestId === 'string'
                ? payload.requestId
                : (payload.detail && typeof payload.detail.requestId === 'string' ? payload.detail.requestId : '');
            if (!requestId) {
                return;
            }

            const handoff = this.pluginDashboardHandoff;
            if (!handoff || !handoff.sessionId) {
                return;
            }

            const sessionId = typeof payload.sessionId === 'string' ? payload.sessionId : '';
            if (sessionId && handoff.sessionId && sessionId !== handoff.sessionId) {
                return;
            }

            const detail = payload.detail && typeof payload.detail === 'object'
                ? Object.assign({}, payload.detail)
                : {};
            delete detail.requestId;
            void this.handlePluginDashboardInterruptRequest(null, handoff, {
                type: PLUGIN_DASHBOARD_INTERRUPT_REQUEST_EVENT,
                sessionId: sessionId,
                requestId: requestId,
                detail: detail
            });
        },

        isPointInsideScreenRect(point, rect) {
            if (!point || !rect) {
                return false;
            }

            const screenX = Number(point.screenX);
            const screenY = Number(point.screenY);
            if (!Number.isFinite(screenX) || !Number.isFinite(screenY)) {
                return false;
            }

            return (
                screenX >= Number(rect.left)
                && screenX <= Number(rect.right)
                && screenY >= Number(rect.top)
                && screenY <= Number(rect.bottom)
            );
        },

        async forwardPluginDashboardSkipRequestToButton(detail) {
            const skipButton = document.getElementById('neko-tutorial-skip-btn');
            if (!skipButton || typeof skipButton.click !== 'function') {
                return 'unavailable';
            }

            if (!detail || typeof detail !== 'object') {
                return 'rejected';
            }

            const point = {
                screenX: Number(detail.screenX),
                screenY: Number(detail.screenY)
            };
            if (!Number.isFinite(point.screenX) || !Number.isFinite(point.screenY)) {
                return 'rejected';
            }

            const currentRect = await this.getSkipButtonScreenRect();
            if (!currentRect || !this.isPointInsideScreenRect(point, currentRect)) {
                return 'rejected';
            }

            skipButton.click();
            return 'forwarded';
        },

        isPluginDashboardDirectSkipRequest(data) {
            const detail = data && data.detail && typeof data.detail === 'object'
                ? data.detail
                : {};
            const source = typeof detail.source === 'string'
                ? detail.source
                : (data && typeof data.source === 'string' ? data.source : '');
            return source === 'plugin_dashboard_button' || source === 'plugin_dashboard_angry_exit';
        },

        async handlePluginDashboardSkipRequest(data) {
            return this.terminationRouter.handlePluginDashboardSkipRequest(data);
        },

        onWindowMessage(event) {
            const data = event && event.data ? event.data : null;
            if (!data || typeof data !== 'object') {
                return;
            }

            const handoff = this.pluginDashboardHandoff;
            if (!handoff || !handoff.windowRef || event.source !== handoff.windowRef) {
                return;
            }
            const expectedOrigin = handoff.targetOrigin || this.getPluginDashboardExpectedOrigin();
            if (expectedOrigin && event.origin !== expectedOrigin) {
                if (!handoff.ready && this.isTrustedPluginDashboardOrigin(event.origin)) {
                    handoff.targetOrigin = event.origin;
                } else {
                    return;
                }
            }

            if (data.type === PLUGIN_DASHBOARD_INTERRUPT_REQUEST_EVENT) {
                void this.handlePluginDashboardInterruptRequest(event, handoff, data);
                return;
            }

            if (data.type === PLUGIN_DASHBOARD_SKIP_REQUEST_EVENT) {
                if (data.sessionId && handoff.sessionId && data.sessionId !== handoff.sessionId) {
                    return;
                }
                void this.handlePluginDashboardSkipRequest(data);
                return;
            }

            if (data.sessionId && handoff.sessionId && data.sessionId !== handoff.sessionId) {
                return;
            }

            if (data.type === PLUGIN_DASHBOARD_READY_EVENT) {
                handoff.ready = true;
                handoff.readyAt = Date.now();
                if (this.isTrustedPluginDashboardOrigin(event.origin)) {
                    handoff.targetOrigin = event.origin;
                }
                return;
            }

            if (data.type === PLUGIN_DASHBOARD_DONE_EVENT) {
                handoff.resolve(true);
            }
        },
    });
})(window.__YuiGuideDirector);
