/**
 * app-buttons.js — Button event handlers module
 * Extracted from app.js lines 4002-4910
 *
 * Handles: mic, screen, stop, mute, reset, return, text-send, screenshot,
 *          text-input keydown, screenshot thumbnail management, emotion analysis.
 */
(function () {
    'use strict';

    const mod = {};
    const S = window.appState;
    const C = window.appConst;
    const U = window.appUtils;

    // ======================== Screenshot helpers ========================

    /**
     * Add a screenshot thumbnail to the pending list.
     * @param {string} dataUrl - image data URL
     */
    mod.addScreenshotToList = function addScreenshotToList(dataUrl) {
        S.screenshotCounter++;

        const screenshotsList = S.dom.screenshotsList;
        const screenshotThumbnailContainer = S.dom.screenshotThumbnailContainer;

        // Create screenshot item container
        const item = document.createElement('div');
        item.className = 'screenshot-item';
        item.dataset.index = S.screenshotCounter;

        // Create thumbnail
        const img = document.createElement('img');
        img.className = 'screenshot-thumbnail';
        img.src = dataUrl;
        img.alt = window.t ? window.t('chat.screenshotAlt', { index: S.screenshotCounter }) : '\u622A\u56FE ' + S.screenshotCounter;
        img.title = window.t ? window.t('chat.screenshotTitle', { index: S.screenshotCounter }) : '\u70B9\u51FB\u67E5\u770B\u622A\u56FE ' + S.screenshotCounter;

        // Click thumbnail to view in new tab
        img.addEventListener('click', function () {
            window.open(dataUrl, '_blank');
        });

        // Create remove button
        const removeBtn = document.createElement('button');
        removeBtn.className = 'screenshot-remove';
        removeBtn.innerHTML = '\u00D7';
        removeBtn.title = window.t ? window.t('chat.removeScreenshot') : '\u79FB\u9664\u6B64\u622A\u56FE';
        removeBtn.addEventListener('click', function (e) {
            e.stopPropagation();
            mod.removeScreenshotFromList(item);
        });

        // Create index label
        const indexLabel = document.createElement('span');
        indexLabel.className = 'screenshot-index';
        indexLabel.textContent = '#' + S.screenshotCounter;

        // Assemble
        item.appendChild(img);
        item.appendChild(removeBtn);
        item.appendChild(indexLabel);

        // Add to list
        screenshotsList.appendChild(item);

        // Update count and show container
        mod.updateScreenshotCount();
        screenshotThumbnailContainer.classList.add('show');

        // Auto-scroll to latest screenshot
        setTimeout(function () {
            screenshotsList.scrollLeft = screenshotsList.scrollWidth;
        }, 100);
    };
    // Backward compat
    window.addScreenshotToList = mod.addScreenshotToList;

    /**
     * Remove a screenshot item from the list with animation.
     * @param {HTMLElement} item
     */
    mod.removeScreenshotFromList = function removeScreenshotFromList(item) {
        var screenshotsList = S.dom.screenshotsList;
        var screenshotThumbnailContainer = S.dom.screenshotThumbnailContainer;

        item.style.animation = 'slideOut 0.3s ease';
        setTimeout(function () {
            item.remove();
            mod.updateScreenshotCount();

            if (screenshotsList.children.length === 0) {
                screenshotThumbnailContainer.classList.remove('show');
            }
        }, 300);
    };
    window.removeScreenshotFromList = mod.removeScreenshotFromList;

    /**
     * Update the displayed screenshot count badge.
     */
    mod.updateScreenshotCount = function updateScreenshotCount() {
        var screenshotsList = S.dom.screenshotsList;
        var screenshotCountEl = S.dom.screenshotCount;
        var count = screenshotsList.children.length;
        screenshotCountEl.textContent = count;
    };
    window.updateScreenshotCount = mod.updateScreenshotCount;

    // ======================== Emotion analysis ========================

    /**
     * Call the backend emotion analysis API.
     * @param {string} text
     * @returns {Promise<Object|null>}
     */
    mod.analyzeEmotion = async function analyzeEmotion(text) {
        console.log(window.t('console.analyzeEmotionCalled'), text);
        try {
            var response = await fetch('/api/emotion/analysis', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    text: text,
                    lanlan_name: window.lanlan_config.lanlan_name
                })
            });

            if (!response.ok) {
                console.warn(window.t('console.emotionAnalysisRequestFailed'), response.status);
                return null;
            }

            var result = await response.json();
            console.log(window.t('console.emotionAnalysisApiResult'), result);

            if (result.error) {
                console.warn(window.t('console.emotionAnalysisError'), result.error);
                return null;
            }

            return result;
        } catch (error) {
            console.error(window.t('console.emotionAnalysisException'), error);
            return null;
        }
    };
    window.analyzeEmotion = mod.analyzeEmotion;

    /**
     * Apply an emotion to the Live2D model.
     * @param {string} emotion
     */
    mod.applyEmotion = function applyEmotion(emotion) {
        if (window.LanLan1 && window.LanLan1.setEmotion) {
            console.log('\u8C03\u7528window.LanLan1.setEmotion:', emotion);
            window.LanLan1.setEmotion(emotion);
        } else {
            console.warn('\u60C5\u611F\u529F\u80FD\u672A\u521D\u59CB\u5316');
        }
    };
    window.applyEmotion = mod.applyEmotion;

    // ======================== init — wire up all event listeners ========================

    mod.init = function init() {
        // Cache DOM references
        var micButton            = S.dom.micButton            = document.getElementById('micButton');
        var muteButton           = S.dom.muteButton           = document.getElementById('muteButton');
        var screenButton         = S.dom.screenButton         = document.getElementById('screenButton');
        var stopButton           = S.dom.stopButton           = document.getElementById('stopButton');
        var resetSessionButton   = S.dom.resetSessionButton   = document.getElementById('resetSessionButton');
        var returnSessionButton  = S.dom.returnSessionButton  = document.getElementById('returnSessionButton');
        var textSendButton       = S.dom.textSendButton       = document.getElementById('textSendButton');
        var textInputBox         = S.dom.textInputBox         = document.getElementById('textInputBox');
        var screenshotButton     = S.dom.screenshotButton     = document.getElementById('screenshotButton');
        var screenshotsList      = S.dom.screenshotsList      = document.getElementById('screenshots-list');
        var screenshotThumbnailContainer = S.dom.screenshotThumbnailContainer = document.getElementById('screenshot-thumbnail-container');
        var screenshotCountEl    = S.dom.screenshotCount      = document.getElementById('screenshot-count');
        var clearAllScreenshots  = S.dom.clearAllScreenshots   = document.getElementById('clear-all-screenshots');

        // ----------------------------------------------------------------
        // Mic button click
        // ----------------------------------------------------------------
        micButton.addEventListener('click', async function () {
            if (micButton.disabled || S.isRecording) return;
            if (micButton.classList.contains('active')) return;

            // Immediately activate
            micButton.classList.add('active');
            window.syncFloatingMicButtonState(true);
            window.isMicStarting = true;
            micButton.disabled = true;

            // Show preparing toast
            window.showVoicePreparingToast(window.t ? window.t('app.voiceSystemPreparing') : '\u8BED\u97F3\u7CFB\u7EDF\u51C6\u5907\u4E2D...');

            // If there is an active text session, end it first
            if (S.isTextSessionActive) {
                S.isSwitchingMode = true;
                if (S.socket && S.socket.readyState === WebSocket.OPEN) {
                    S.socket.send(JSON.stringify({ action: 'end_session' }));
                }
                S.isTextSessionActive = false;
                window.showStatusToast(window.t ? window.t('app.switchingToVoice') : '\u6B63\u5728\u5207\u6362\u5230\u8BED\u97F3\u6A21\u5F0F...', 3000);
                window.showVoicePreparingToast(window.t ? window.t('app.switchingToVoice') : '\u6B63\u5728\u5207\u6362\u5230\u8BED\u97F3\u6A21\u5F0F...');
                await new Promise(function (resolve) { setTimeout(resolve, 1500); });
            }

            // Hide text input area (desktop only)
            var textInputArea = document.getElementById('text-input-area');
            if (!U.isMobile()) {
                textInputArea.classList.add('hidden');
            }

            // Disable all voice buttons
            muteButton.disabled = true;
            screenButton.disabled = true;
            stopButton.disabled = true;
            resetSessionButton.disabled = true;
            returnSessionButton.disabled = true;

            window.showStatusToast(window.t ? window.t('app.initializingVoice') : '\u6B63\u5728\u521D\u59CB\u5316\u8BED\u97F3\u5BF9\u8BDD...', 3000);
            window.showVoicePreparingToast(window.t ? window.t('app.connectingToServer') : '\u6B63\u5728\u8FDE\u63A5\u670D\u52A1\u5668...');

            try {
                // Create a promise for session_started
                var sessionStartPromise = new Promise(function (resolve, reject) {
                    S.sessionStartedResolver = resolve;
                    S.sessionStartedRejecter = reject;

                    if (window.sessionTimeoutId) {
                        clearTimeout(window.sessionTimeoutId);
                        window.sessionTimeoutId = null;
                    }
                });

                // Send start session (ensure WS open)
                await window.ensureWebSocketOpen();
                S.socket.send(JSON.stringify({
                    action: 'start_session',
                    input_type: 'audio'
                }));

                // Timeout (15s)
                window.sessionTimeoutId = setTimeout(function () {
                    if (S.sessionStartedRejecter) {
                        var rejecter = S.sessionStartedRejecter;
                        S.sessionStartedResolver = null;
                        S.sessionStartedRejecter = null;
                        window.sessionTimeoutId = null;

                        if (S.socket && S.socket.readyState === WebSocket.OPEN) {
                            S.socket.send(JSON.stringify({ action: 'end_session' }));
                            console.log(window.t('console.sessionTimeoutEndSession'));
                        }

                        window.showVoicePreparingToast(window.t ? window.t('app.sessionTimeout') || '\u8FDE\u63A5\u8D85\u65F6' : '\u8FDE\u63A5\u8D85\u65F6\uFF0C\u8BF7\u68C0\u67E5\u7F51\u7EDC\u8FDE\u63A5');
                        rejecter(new Error(window.t ? window.t('app.sessionTimeout') : 'Session\u542F\u52A8\u8D85\u65F6'));
                    } else {
                        window.sessionTimeoutId = null;
                    }
                }, 15000);

                // Parallel: wait for session + init mic
                try {
                    await window.showCurrentModel();
                    window.showStatusToast(window.t ? window.t('app.initializingMic') : '\u6B63\u5728\u521D\u59CB\u5316\u9EA6\u514B\u98CE...', 3000);

                    await Promise.all([
                        sessionStartPromise,
                        window.startMicCapture()
                    ]);

                    if (window.sessionTimeoutId) {
                        clearTimeout(window.sessionTimeoutId);
                        window.sessionTimeoutId = null;
                    }
                } catch (error) {
                    if (window.sessionTimeoutId) {
                        clearTimeout(window.sessionTimeoutId);
                        window.sessionTimeoutId = null;
                    }
                    throw error;
                }

                // Start proactive vision during speech if enabled
                try {
                    if (S.proactiveVisionEnabled) {
                        if (typeof window.acquireProactiveVisionStream === 'function') {
                            await window.acquireProactiveVisionStream();
                        }
                        window.startProactiveVisionDuringSpeech();
                    }
                } catch (e) {
                    console.warn(window.t('console.startVoiceActiveVisionFailed'), e);
                }

                // Success — hide preparing toast, show ready
                window.hideVoicePreparingToast();

                setTimeout(function () {
                    window.showReadyToSpeakToast();
                    window.startSilenceDetection();
                    window.monitorInputVolume();
                }, 1000);

                window.isMicStarting = false;
                S.isSwitchingMode = false;

            } catch (error) {
                console.error(window.t('console.startVoiceSessionFailed'), error);

                // Cleanup
                if (window.sessionTimeoutId) {
                    clearTimeout(window.sessionTimeoutId);
                    window.sessionTimeoutId = null;
                }
                S.sessionStartedResolver = null;
                S.sessionStartedRejecter = null;

                if (S.socket && S.socket.readyState === WebSocket.OPEN) {
                    S.socket.send(JSON.stringify({ action: 'end_session' }));
                    console.log(window.t('console.sessionStartFailedEndSession'));
                }

                window.hideVoicePreparingToast();
                window.stopRecording();

                micButton.classList.remove('active');
                micButton.classList.remove('recording');

                S.isRecording = false;
                window.isRecording = false;

                window.syncFloatingMicButtonState(false);
                window.syncFloatingScreenButtonState(false);

                micButton.disabled = false;
                muteButton.disabled = true;
                screenButton.disabled = true;
                stopButton.disabled = true;
                resetSessionButton.disabled = false;
                textInputArea.classList.remove('hidden');
                window.showStatusToast(window.t ? window.t('app.startFailed', { error: error.message }) : '\u542F\u52A8\u5931\u8D25: ' + error.message, 5000);

                window.isMicStarting = false;
                S.isSwitchingMode = false;

                screenButton.classList.remove('active');
            }
        });

        // ----------------------------------------------------------------
        // Screen button click
        // ----------------------------------------------------------------
        screenButton.addEventListener('click', window.startScreenSharing);

        // ----------------------------------------------------------------
        // Stop button click
        // ----------------------------------------------------------------
        stopButton.addEventListener('click', window.stopScreenSharing);

        // ----------------------------------------------------------------
        // Mute button click
        // ----------------------------------------------------------------
        muteButton.addEventListener('click', window.stopMicCapture);

        // ----------------------------------------------------------------
        // Reset session button click
        // ----------------------------------------------------------------
        resetSessionButton.addEventListener('click', function () {
            console.log(window.t('console.resetButtonClicked'));
            S.isSwitchingMode = true;

            var isGoodbyeMode = window.live2dManager && window.live2dManager._goodbyeClicked;
            console.log(window.t('console.checkingGoodbyeMode'), isGoodbyeMode, window.t('console.goodbyeClicked'), window.live2dManager ? window.live2dManager._goodbyeClicked : 'undefined');

            var live2dContainer = document.getElementById('live2d-container');
            console.log(window.t('console.hideLive2dBeforeStatus'), {
                '\u5B58\u5728': !!live2dContainer,
                '\u5F53\u524D\u7C7B': live2dContainer ? live2dContainer.className : 'undefined',
                classList: live2dContainer ? live2dContainer.classList.toString() : 'undefined',
                display: live2dContainer ? getComputedStyle(live2dContainer).display : 'undefined'
            });

            window.hideLive2d();

            console.log(window.t('console.hideLive2dAfterStatus'), {
                '\u5B58\u5728': !!live2dContainer,
                '\u5F53\u524D\u7C7B': live2dContainer ? live2dContainer.className : 'undefined',
                classList: live2dContainer ? live2dContainer.classList.toString() : 'undefined',
                display: live2dContainer ? getComputedStyle(live2dContainer).display : 'undefined'
            });

            if (S.socket && S.socket.readyState === WebSocket.OPEN) {
                S._suppressCharacterLeft = true;
                S.socket.send(JSON.stringify({ action: 'end_session' }));
            }
            window.stopRecording();

            (async function () {
                await window.clearAudioQueue();
            })();

            S.isTextSessionActive = false;

            micButton.classList.remove('active');
            screenButton.classList.remove('active');

            // Clear all screenshots
            screenshotsList.innerHTML = '';
            screenshotThumbnailContainer.classList.remove('show');
            mod.updateScreenshotCount();
            S.screenshotCounter = 0;

            console.log(window.t('console.executingBranchJudgment'), isGoodbyeMode);

            if (!isGoodbyeMode) {
                console.log(window.t('console.executingNormalEndSession'));

                if (S.proactiveChatEnabled && window.hasAnyChatModeEnabled()) {
                    window.resetProactiveChatBackoff();
                }

                var textInputArea = document.getElementById('text-input-area');
                textInputArea.classList.remove('hidden');

                micButton.disabled = false;
                textSendButton.disabled = false;
                textInputBox.disabled = false;
                screenshotButton.disabled = false;

                muteButton.disabled = true;
                screenButton.disabled = true;
                stopButton.disabled = true;
                resetSessionButton.disabled = true;
                returnSessionButton.disabled = true;

                window.showStatusToast(window.t ? window.t('app.sessionEnded') : '\u4F1A\u8BDD\u5DF2\u7ED3\u675F', 3000);
            } else {
                console.log(window.t('console.executingGoodbyeMode'));
                console.log('[App] \u6267\u884C\u201C\u8BF7\u5979\u79BB\u5F00\u201D\u6A21\u5F0F\u903B\u8F91');

                var textInputArea = document.getElementById('text-input-area');
                textInputArea.classList.add('hidden');

                micButton.disabled = true;
                textSendButton.disabled = true;
                textInputBox.disabled = true;
                screenshotButton.disabled = true;
                muteButton.disabled = true;
                screenButton.disabled = true;
                stopButton.disabled = true;
                resetSessionButton.disabled = true;
                returnSessionButton.disabled = false;

                window.stopProactiveChatSchedule();

                window.showStatusToast('', 0);
            }

            setTimeout(function () {
                S.isSwitchingMode = false;
            }, 500);
        });

        // ----------------------------------------------------------------
        // Return session button click ("ask her back")
        // ----------------------------------------------------------------
        returnSessionButton.addEventListener('click', async function () {
            S.isSwitchingMode = true;

            try {
                if (window.live2dManager) {
                    window.live2dManager._goodbyeClicked = false;
                }
                if (window.vrmManager) {
                    window.vrmManager._goodbyeClicked = false;
                }

                micButton.classList.remove('recording');
                micButton.classList.remove('active');
                screenButton.classList.remove('active');

                S.isRecording = false;
                window.isRecording = false;

                var textInputArea = document.getElementById('text-input-area');
                if (textInputArea) {
                    textInputArea.classList.remove('hidden');
                }

                window.showStatusToast(window.t ? window.t('app.initializingText') : '\u6B63\u5728\u521D\u59CB\u5316\u6587\u672C\u5BF9\u8BDD...', 3000);

                // Wait for session_started
                var sessionStartPromise = new Promise(function (resolve, reject) {
                    S.sessionStartedResolver = resolve;
                    S.sessionStartedRejecter = reject;

                    if (window.sessionTimeoutId) {
                        clearTimeout(window.sessionTimeoutId);
                        window.sessionTimeoutId = null;
                    }

                    window.sessionTimeoutId = setTimeout(function () {
                        if (S.sessionStartedRejecter) {
                            var rejecter = S.sessionStartedRejecter;
                            S.sessionStartedResolver = null;
                            S.sessionStartedRejecter = null;
                            window.sessionTimeoutId = null;

                            if (S.socket && S.socket.readyState === WebSocket.OPEN) {
                                S.socket.send(JSON.stringify({ action: 'end_session' }));
                                console.log(window.t('console.returnSessionTimeoutEndSession'));
                            }

                            rejecter(new Error(window.t ? window.t('app.sessionTimeout') : 'Session\u542F\u52A8\u8D85\u65F6'));
                        }
                    }, 15000);
                });

                // Start text session
                await window.ensureWebSocketOpen();
                S.socket.send(JSON.stringify({
                    action: 'start_session',
                    input_type: 'text',
                    new_session: true
                }));

                await sessionStartPromise;
                S.isTextSessionActive = true;

                await window.showCurrentModel();

                // Restore chat container if minimized
                var chatContainerEl = document.getElementById('chat-container');
                if (chatContainerEl && (chatContainerEl.classList.contains('minimized') || chatContainerEl.classList.contains('mobile-collapsed'))) {
                    console.log('[App] \u81EA\u52A8\u6062\u590D\u5BF9\u8BDD\u533A');
                    chatContainerEl.classList.remove('minimized');
                    chatContainerEl.classList.remove('mobile-collapsed');

                    var chatContentWrapper = document.getElementById('chat-content-wrapper');
                    var chatHeader = document.getElementById('chat-header');
                    var tia = document.getElementById('text-input-area');
                    if (chatContentWrapper) chatContentWrapper.style.display = '';
                    if (chatHeader) chatHeader.style.display = '';
                    if (tia) tia.style.display = '';

                    var toggleChatBtn = document.getElementById('toggle-chat-btn');
                    if (toggleChatBtn) {
                        var iconImg = toggleChatBtn.querySelector('img');
                        if (iconImg) {
                            iconImg.src = '/static/icons/expand_icon_off.png';
                            iconImg.alt = window.t ? window.t('common.minimize') : '\u6700\u5C0F\u5316';
                        }
                        toggleChatBtn.title = window.t ? window.t('common.minimize') : '\u6700\u5C0F\u5316';

                        if (typeof window.scrollToBottom === 'function') {
                            setTimeout(window.scrollToBottom, 300);
                        }
                    }
                }

                // Enable basic input buttons
                micButton.disabled = false;
                textSendButton.disabled = false;
                textInputBox.disabled = false;
                screenshotButton.disabled = false;
                resetSessionButton.disabled = false;

                // Disable voice control buttons
                muteButton.disabled = true;
                screenButton.disabled = true;
                stopButton.disabled = true;
                returnSessionButton.disabled = true;

                // Reset proactive chat
                if (S.proactiveChatEnabled && window.hasAnyChatModeEnabled()) {
                    window.resetProactiveChatBackoff();
                }

                window.showStatusToast(
                    window.t
                        ? window.t('app.returning', { name: window.lanlan_config.lanlan_name })
                        : '\uD83E\uDEB4 ' + window.lanlan_config.lanlan_name + '\u56DE\u6765\u4E86\uFF01',
                    3000
                );

            } catch (error) {
                console.error(window.t('console.askHerBackFailed'), error);
                window.hideVoicePreparingToast();
                window.showStatusToast(
                    window.t
                        ? window.t('app.startFailed', { error: error.message })
                        : '\u56DE\u6765\u5931\u8D25: ' + error.message,
                    5000
                );

                if (window.sessionTimeoutId) {
                    clearTimeout(window.sessionTimeoutId);
                    window.sessionTimeoutId = null;
                }
                S.sessionStartedResolver = null;
                S.sessionStartedRejecter = null;

                returnSessionButton.disabled = false;
            } finally {
                setTimeout(function () {
                    S.isSwitchingMode = false;
                }, 500);
            }
        });

        // ----------------------------------------------------------------
        // Text send button click
        // ----------------------------------------------------------------
        textSendButton.addEventListener('click', async function () {
            var text = textInputBox.value.trim();
            var hasScreenshots = screenshotsList.children.length > 0;

            if (!text && !hasScreenshots) return;

            // Record user input time and reset proactive chat
            window.lastUserInputTime = Date.now();
            window.resetProactiveChatBackoff();

            // If no active text session, start one first
            if (!S.isTextSessionActive) {
                textSendButton.disabled = true;
                textInputBox.disabled = true;
                screenshotButton.disabled = true;
                resetSessionButton.disabled = false;

                window.showStatusToast(window.t ? window.t('app.initializingText') : '\u6B63\u5728\u521D\u59CB\u5316\u6587\u672C\u5BF9\u8BDD...', 3000);

                try {
                    var sessionStartPromise = new Promise(function (resolve, reject) {
                        S.sessionStartedResolver = resolve;
                        S.sessionStartedRejecter = reject;

                        if (window.sessionTimeoutId) {
                            clearTimeout(window.sessionTimeoutId);
                            window.sessionTimeoutId = null;
                        }
                    });

                    await window.ensureWebSocketOpen();
                    S.socket.send(JSON.stringify({
                        action: 'start_session',
                        input_type: 'text',
                        new_session: false
                    }));

                    // Timeout after WebSocket confirms connection
                    window.sessionTimeoutId = setTimeout(function () {
                        if (S.sessionStartedRejecter) {
                            var rejecter = S.sessionStartedRejecter;
                            S.sessionStartedResolver = null;
                            S.sessionStartedRejecter = null;
                            window.sessionTimeoutId = null;

                            if (S.socket && S.socket.readyState === WebSocket.OPEN) {
                                S.socket.send(JSON.stringify({ action: 'end_session' }));
                                console.log('[TextSession] timeout \u2192 sent end_session');
                            }

                            rejecter(new Error(window.t ? window.t('app.sessionTimeout') : 'Session\u542F\u52A8\u8D85\u65F6'));
                        }
                    }, 15000);

                    await sessionStartPromise;

                    S.isTextSessionActive = true;
                    await window.showCurrentModel();

                    textSendButton.disabled = false;
                    textInputBox.disabled = false;
                    screenshotButton.disabled = false;

                    window.showStatusToast(window.t ? window.t('app.textChattingShort') : '\u6B63\u5728\u6587\u672C\u804A\u5929\u4E2D', 2000);
                } catch (error) {
                    console.error(window.t('console.startTextSessionFailed'), error);
                    window.hideVoicePreparingToast();
                    window.showStatusToast(
                        window.t
                            ? window.t('app.startFailed', { error: error.message })
                            : '\u542F\u52A8\u5931\u8D25: ' + error.message,
                        5000
                    );

                    if (window.sessionTimeoutId) {
                        clearTimeout(window.sessionTimeoutId);
                        window.sessionTimeoutId = null;
                    }
                    S.sessionStartedResolver = null;
                    S.sessionStartedRejecter = null;

                    textSendButton.disabled = false;
                    textInputBox.disabled = false;
                    screenshotButton.disabled = false;

                    return; // Don't send if session start failed
                }
            }

            // Send message
            if (S.socket && S.socket.readyState === WebSocket.OPEN) {
                // Send screenshots first
                if (hasScreenshots) {
                    var screenshotItems = Array.from(screenshotsList.children);
                    for (var i = 0; i < screenshotItems.length; i++) {
                        var img = screenshotItems[i].querySelector('.screenshot-thumbnail');
                        if (img && img.src) {
                            S.socket.send(JSON.stringify({
                                action: 'stream_data',
                                data: img.src,
                                input_type: U.isMobile() ? 'camera' : 'screen'
                            }));
                        }
                    }

                    var screenshotItemCount = screenshotItems.length;
                    window.appendMessage('\uD83D\uDCF8 [\u5DF2\u53D1\u9001' + screenshotItemCount + '\u5F20\u622A\u56FE]', 'user', true);

                    // Achievement: send image
                    if (window.unlockAchievement) {
                        window.unlockAchievement('ACH_SEND_IMAGE').catch(function (err) {
                            console.error('\u89E3\u9501\u53D1\u9001\u56FE\u7247\u6210\u5C31\u5931\u8D25:', err);
                        });
                    }

                    // Clear screenshot list
                    screenshotsList.innerHTML = '';
                    screenshotThumbnailContainer.classList.remove('show');
                    mod.updateScreenshotCount();
                }

                // Then send text (if any)
                if (text) {
                    S.socket.send(JSON.stringify({
                        action: 'stream_data',
                        data: text,
                        input_type: 'text'
                    }));

                    textInputBox.value = '';
                    window.appendMessage(text, 'user', true);

                    // Achievement: meow detection
                    if (window.incrementAchievementCounter) {
                        var meowPattern = /\u55B5|miao|meow|nya|\u306B\u3083/i;
                        if (meowPattern.test(text)) {
                            try {
                                window.incrementAchievementCounter('meowCount');
                            } catch (error) {
                                console.debug('\u589E\u52A0\u55B5\u55B5\u8BA1\u6570\u5931\u8D25:', error);
                            }
                        }
                    }

                    // First user input check
                    if (window.appChat && window.appChat.isFirstUserInput()) {
                        window.appChat.markFirstUserInput();
                        console.log(window.t('console.userFirstInputDetected'));
                        window.checkAndUnlockFirstDialogueAchievement();
                    }
                }

                // Reset proactive chat timer
                if (S.proactiveChatEnabled && window.hasAnyChatModeEnabled()) {
                    window.resetProactiveChatBackoff();
                }

                window.showStatusToast(window.t ? window.t('app.textChattingShort') : '\u6B63\u5728\u6587\u672C\u804A\u5929\u4E2D', 2000);
            } else {
                window.showStatusToast(window.t ? window.t('app.websocketNotConnected') : 'WebSocket\u672A\u8FDE\u63A5\uFF01', 4000);
            }
        });

        // ----------------------------------------------------------------
        // Enter key sends text (Shift+Enter for newline)
        // ----------------------------------------------------------------
        textInputBox.addEventListener('keydown', function (e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                textSendButton.click();
            }
        });

        // ----------------------------------------------------------------
        // Screenshot button click
        // ----------------------------------------------------------------
        screenshotButton.addEventListener('click', async function () {
            var captureStream = null;

            try {
                screenshotButton.disabled = true;
                window.showStatusToast(window.t ? window.t('app.capturing') : '\u6B63\u5728\u622A\u56FE...', 2000);

                var dataUrl = null;
                var width = 0, height = 0;

                // 1. 优先尝试缓存流（不创建新缓存，即时截取无弹窗）
                try {
                    if (S.screenCaptureStream && S.screenCaptureStream.active) {
                        var tracks = S.screenCaptureStream.getVideoTracks();
                        if (tracks.length > 0 && tracks.some(function (t) { return t.readyState === 'live'; })) {
                            var cachedFrame = await window.captureFrameFromStream(S.screenCaptureStream, 0.8);
                            if (cachedFrame && cachedFrame.dataUrl) {
                                dataUrl = cachedFrame.dataUrl;
                                width = cachedFrame.width;
                                height = cachedFrame.height;
                                // 刷新缓存流最后使用时间
                                S.screenCaptureStreamLastUsed = Date.now();
                                if (window.scheduleScreenCaptureIdleCheck) window.scheduleScreenCaptureIdleCheck();
                            }
                        }
                    }
                } catch (cachedErr) {
                    console.warn('[截图] 缓存流截取失败，将尝试一次性流:', cachedErr);
                }

                // 2. 无缓存流或缓存流截取失败 → 一次性流（用后即释放，不存入缓存）
                if (!dataUrl) {
                    if (U.isMobile()) {
                        captureStream = await window.getMobileCameraStream();
                    } else {
                        // Desktop: try getDisplayMedia
                        try {
                            if (navigator.mediaDevices && navigator.mediaDevices.getDisplayMedia) {
                                captureStream = await navigator.mediaDevices.getDisplayMedia({
                                    video: { cursor: 'always' },
                                    audio: false,
                                });
                            } else {
                                throw new Error('UNSUPPORTED_API');
                            }
                        } catch (displayErr) {
                            if (displayErr.name === 'NotAllowedError') throw displayErr;

                            console.warn('[\u622A\u56FE] getDisplayMedia \u5931\u8D25\uFF0C\u5C1D\u8BD5\u540E\u7AEF\u622A\u56FE:', displayErr);
                            var result = await window.fetchBackendScreenshot();
                            if (result && result.dataUrl) {
                                dataUrl = result.dataUrl;
                            } else {
                                throw displayErr;
                            }
                        }
                    }

                    // Extract frame from one-time stream
                    if (!dataUrl && captureStream) {
                        var frame = await window.captureFrameFromStream(captureStream, 0.8);
                        if (frame) {
                            dataUrl = frame.dataUrl;
                            width = frame.width;
                            height = frame.height;
                        }
                    }
                }

                if (!dataUrl) {
                    throw new Error('\u6240\u6709\u622A\u56FE\u65B9\u5F0F\u5747\u5931\u8D25');
                }

                if (width && height) {
                    console.log(window.t('console.screenshotSuccess'), width + 'x' + height);
                }

                mod.addScreenshotToList(dataUrl);
                window.showStatusToast(window.t ? window.t('app.screenshotAdded') : '\u622A\u56FE\u5DF2\u6DFB\u52A0\uFF0C\u70B9\u51FB\u53D1\u9001\u4E00\u8D77\u53D1\u9001', 3000);

            } catch (err) {
                console.error(window.t('console.screenshotFailed'), err);

                var errorMsg = window.t ? window.t('app.screenshotFailed') : '\u622A\u56FE\u5931\u8D25';
                if (err.message === 'UNSUPPORTED_API') {
                    errorMsg = window.t ? window.t('app.screenshotUnsupported') : '\u5F53\u524D\u6D4F\u89C8\u5668\u4E0D\u652F\u6301\u5C4F\u5E55\u622A\u56FE\u529F\u80FD';
                } else if (err.name === 'NotAllowedError') {
                    errorMsg = window.t ? window.t('app.screenshotCancelled') : '\u7528\u6237\u53D6\u6D88\u4E86\u622A\u56FE';
                } else if (err.name === 'NotFoundError') {
                    errorMsg = window.t ? window.t('app.deviceNotFound') : '\u672A\u627E\u5230\u53EF\u7528\u7684\u5A92\u4F53\u8BBE\u5907';
                } else if (err.name === 'NotReadableError') {
                    errorMsg = window.t ? window.t('app.deviceNotAccessible') : '\u65E0\u6CD5\u8BBF\u95EE\u5A92\u4F53\u8BBE\u5907';
                } else if (err.message) {
                    errorMsg = (window.t ? window.t('app.screenshotFailed') : '\u622A\u56FE\u5931\u8D25') + ': ' + err.message;
                }

                window.showStatusToast(errorMsg, 5000);
            } finally {
                // 只释放一次性流，不碰缓存流
                if (captureStream instanceof MediaStream) {
                    captureStream.getTracks().forEach(function (track) { track.stop(); });
                }
                screenshotButton.disabled = false;
            }
        });

        // ----------------------------------------------------------------
        // Clear all screenshots button
        // ----------------------------------------------------------------
        clearAllScreenshots.addEventListener('click', async function () {
            if (screenshotsList.children.length === 0) return;

            if (await window.showConfirm(
                window.t ? window.t('dialogs.clearScreenshotsConfirm') : '\u786E\u5B9A\u8981\u6E05\u7A7A\u6240\u6709\u5F85\u53D1\u9001\u7684\u622A\u56FE\u5417\uFF1F',
                window.t ? window.t('dialogs.clearScreenshots') : '\u6E05\u7A7A\u622A\u56FE',
                { danger: true }
            )) {
                screenshotsList.innerHTML = '';
                screenshotThumbnailContainer.classList.remove('show');
                mod.updateScreenshotCount();
            }
        });
    };

    window.appButtons = mod;
})();
