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
        item.dataset.attachmentId = 'attachment-' + Date.now() + '-' + S.screenshotCounter;

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
        mod.syncPendingComposerAttachments();

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
            mod.syncPendingComposerAttachments();

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

    mod.getPendingComposerAttachments = function getPendingComposerAttachments() {
        var screenshotsList = S.dom.screenshotsList;
        if (!screenshotsList) return [];

        return Array.from(screenshotsList.children).map(function (item, index) {
            var img = item.querySelector('.screenshot-thumbnail');
            if (!img || !img.src) return null;
            var translatedAlt = window.t ? window.t('chat.pendingImageAlt', { index: index + 1 }) : '';
            return {
                id: String(item.dataset.attachmentId || item.dataset.index || ('attachment-' + index)),
                url: img.src,
                alt: img.alt || (typeof translatedAlt === 'string' && translatedAlt ? translatedAlt : '图片 ' + (index + 1))
            };
        }).filter(Boolean);
    };

    mod.syncPendingComposerAttachments = function syncPendingComposerAttachments() {
        if (window.reactChatWindowHost && typeof window.reactChatWindowHost.setComposerAttachments === 'function') {
            window.reactChatWindowHost.setComposerAttachments(mod.getPendingComposerAttachments());
        }
    };

    mod.ensureImportImageInput = function ensureImportImageInput() {
        if (mod._importImageInput && mod._importImageInput.isConnected) {
            return mod._importImageInput;
        }

        var input = document.getElementById('reactChatWindowImportImageInput');
        if (!input) {
            input = document.createElement('input');
            input.id = 'reactChatWindowImportImageInput';
            input.type = 'file';
            input.accept = 'image/*';
            input.multiple = true;
            input.hidden = true;
            document.body.appendChild(input);
        }

        input.addEventListener('change', function (event) {
            var files = event && event.target && event.target.files ? Array.from(event.target.files) : [];
            if (!files.length) return;

            Promise.allSettled(files.map(mod.importImageFileToPendingList))
                .then(function (results) {
                    var succeeded = 0;
                    for (var i = 0; i < results.length; i++) {
                        if (results[i].status === 'fulfilled') {
                            succeeded++;
                        } else {
                            console.error('[导入图片] 单张处理失败:', results[i].reason);
                        }
                    }
                    if (succeeded > 0) {
                        window.showStatusToast(
                            window.t ? window.t('app.importImageAdded', { count: succeeded }) : '已添加 ' + succeeded + ' 张图片，发送时会一并带上',
                            3000
                        );
                    } else {
                        window.showStatusToast(
                            window.t ? window.t('app.importImageFailed') : '导入图片失败',
                            4000
                        );
                    }
                })
                .finally(function () {
                    input.value = '';
                });
        });

        mod._importImageInput = input;
        return input;
    };

    mod.importImageFileToPendingList = function importImageFileToPendingList(file) {
        return new Promise(function (resolve, reject) {
            if (!(file instanceof File)) {
                reject(new Error('INVALID_FILE'));
                return;
            }

            if (!/^image\//i.test(file.type || '')) {
                reject(new Error('INVALID_IMAGE_TYPE'));
                return;
            }

            var reader = new FileReader();
            reader.onload = function () {
                try {
                    mod.addScreenshotToList(String(reader.result || ''));
                    resolve(reader.result);
                } catch (error) {
                    reject(error);
                }
            };
            reader.onerror = function () {
                reject(reader.error || new Error('READ_IMAGE_FAILED'));
            };
            reader.readAsDataURL(file);
        });
    };

    mod.openImageImportPicker = function openImageImportPicker() {
        var input = mod.ensureImportImageInput();
        input.click();
    };

    mod.removePendingAttachmentById = function removePendingAttachmentById(attachmentId) {
        if (!attachmentId) return;
        var screenshotsList = S.dom.screenshotsList;
        if (!screenshotsList) return;
        var items = Array.from(screenshotsList.children);
        var target = items.find(function (item) {
            return item.dataset.attachmentId === String(attachmentId);
        });
        if (target) {
            mod.removeScreenshotFromList(target);
        }
    };

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
        var textInputComposing = false;
        var lastTextCompositionEndAt = 0;

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

            // Hide text input area (desktop only) + React composer + IPC
            var textInputArea = document.getElementById('text-input-area');
            if (!U.isMobile()) {
                textInputArea.classList.add('hidden');
            }
            if (!U.isMobile() && typeof window.syncVoiceChatComposerHidden === 'function') {
                window.syncVoiceChatComposerHidden(true);
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

                        var timeoutMsg = (window.t && window.t('app.sessionTimeout')) || '\u542F\u52A8\u8D85\u65F6\uFF0C\u670D\u52A1\u5668\u53EF\u80FD\u7E41\u5FD9\uFF0C\u8BF7\u7A0D\u540E\u624B\u52A8\u91CD\u8BD5';
                        window.showVoicePreparingToast(timeoutMsg);
                        rejecter(new Error(timeoutMsg));
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
                if (typeof window.syncVoiceChatComposerHidden === 'function') {
                    window.syncVoiceChatComposerHidden(false);
                }
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
            mod.syncPendingComposerAttachments();
            S.screenshotCounter = 0;

            console.log(window.t('console.executingBranchJudgment'), isGoodbyeMode);

            if (!isGoodbyeMode) {
                console.log(window.t('console.executingNormalEndSession'));

                if (S.proactiveChatEnabled && window.hasAnyChatModeEnabled()) {
                    window.resetProactiveChatBackoff();
                }

                var textInputArea = document.getElementById('text-input-area');
                textInputArea.classList.remove('hidden');
                if (typeof window.syncVoiceChatComposerHidden === 'function') {
                    window.syncVoiceChatComposerHidden(false);
                }

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
                if (typeof window.syncVoiceChatComposerHidden === 'function') {
                    window.syncVoiceChatComposerHidden(true);
                }

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
                if (typeof window.stopProactiveVisionDuringSpeech === 'function') {
                    window.stopProactiveVisionDuringSpeech();
                }

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
                if (window.mmdManager) {
                    window.mmdManager._goodbyeClicked = false;
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
                if (typeof window.syncVoiceChatComposerHidden === 'function') {
                    window.syncVoiceChatComposerHidden(false);
                }

                // 切换猫娘期间会话建立耗时常 >5s（模型加载 + 后端冷加载），
                // 默认 3s toast 在真空期间消失会让用户误以为"没反应就报错"。
                var initToastMs1 = (S.isSwitchingCatgirl) ? 8000 : 3000;
                window.showStatusToast(window.t ? window.t('app.initializingText') : '\u6B63\u5728\u521D\u59CB\u5316\u6587\u672C\u5BF9\u8BDD...', initToastMs1);

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

                            var timeoutMsg = (window.t && window.t('app.sessionTimeout')) || '\u542F\u52A8\u8D85\u65F6\uFF0C\u670D\u52A1\u5668\u53EF\u80FD\u7E41\u5FD9\uFF0C\u8BF7\u7A0D\u540E\u624B\u52A8\u91CD\u8BD5';
                            rejecter(new Error(timeoutMsg));
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

        async function sendTextPayload(rawText, options) {
            options = options || {};
            var text = String(typeof rawText === 'string' ? rawText : '').trim();
            var hasScreenshots = screenshotsList.children.length > 0;
            var isReactWindowSource = options.source === 'react-chat-window';
            var reactOptimisticMessageId = '';
            var reactOptimisticMessageAppended = null;

            if (!text && !hasScreenshots) return;

            // Record user input time and reset proactive chat
            window.lastUserInputTime = Date.now();
            window.resetProactiveChatBackoff();

            if (isReactWindowSource && window.appChat && typeof window.appChat.appendReactUserMessage === 'function') {
                reactOptimisticMessageId = 'user-' + Date.now() + '-' + Math.random().toString(36).slice(2, 8);
                reactOptimisticMessageAppended = window.appChat.appendReactUserMessage({
                    id: reactOptimisticMessageId,
                    time: (typeof window.getCurrentTimeString === 'function')
                        ? window.getCurrentTimeString()
                        : new Date().toLocaleTimeString('en-US', {
                            hour12: false,
                            hour: '2-digit',
                            minute: '2-digit',
                            second: '2-digit'
                        }),
                    status: 'sending',
                    text: text,
                    imageUrls: mod.getPendingComposerAttachments().map(function (attachment) {
                        return attachment && attachment.url ? String(attachment.url) : '';
                    }).filter(Boolean)
                });
            }

            function updateReactOptimisticMessageStatus(status) {
                if (reactOptimisticMessageAppended === null || !reactOptimisticMessageId) return;
                if (window.reactChatWindowHost && typeof window.reactChatWindowHost.updateMessage === 'function') {
                    window.reactChatWindowHost.updateMessage(reactOptimisticMessageId, {
                        status: status
                    });
                }
            }

            // If no active text session, start one first
            if (!S.isTextSessionActive) {
                textSendButton.disabled = true;
                textInputBox.disabled = true;
                screenshotButton.disabled = true;
                resetSessionButton.disabled = false;

                // 同上：切换期间的初始化窗口比默认 3s 更长，延长 toast 避免真空感
                var initToastMs2 = (S.isSwitchingCatgirl) ? 8000 : 3000;
                window.showStatusToast(window.t ? window.t('app.initializingText') : '\u6B63\u5728\u521D\u59CB\u5316\u6587\u672C\u5BF9\u8BDD...', initToastMs2);

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

                            var timeoutMsg = (window.t && window.t('app.sessionTimeout')) || '\u542F\u52A8\u8D85\u65F6\uFF0C\u670D\u52A1\u5668\u53EF\u80FD\u7E41\u5FD9\uFF0C\u8BF7\u7A0D\u540E\u624B\u52A8\u91CD\u8BD5';
                            rejecter(new Error(timeoutMsg));
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

                    updateReactOptimisticMessageStatus('failed');
                    return; // Don't send if session start failed
                }
            }

            // Send message
            if (S.socket && S.socket.readyState === WebSocket.OPEN) {
                try {
                    var sentImageUrls = [];

                    // Send screenshots first
                    if (hasScreenshots) {
                        var screenshotItems = Array.from(screenshotsList.children);
                        for (var i = 0; i < screenshotItems.length; i++) {
                            var img = screenshotItems[i].querySelector('.screenshot-thumbnail');
                            if (img && img.src) {
                                sentImageUrls.push(img.src);
                                S.socket.send(JSON.stringify({
                                    action: 'stream_data',
                                    data: img.src,
                                    input_type: U.isMobile() ? 'camera' : 'screen'
                                }));
                            }
                        }

                        if (!isReactWindowSource) {
                            var screenshotItemCount = screenshotItems.length;
                            window.appendMessage('\uD83D\uDCF8 [\u5DF2\u53D1\u9001' + screenshotItemCount + '\u5F20\u622A\u56FE]', 'user', true, {
                                skipReactSync: true
                            });
                        }

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
                        mod.syncPendingComposerAttachments();
                    }

                    // Then send text (if any)
                    if (text) {
                        if (!isReactWindowSource && window.appChat && typeof window.appChat.ensureUserDisplayName === 'function') {
                            try {
                                await window.appChat.ensureUserDisplayName();
                            } catch (nameError) {
                                console.warn('[Chat] preload user display name failed:', nameError);
                            }
                        }

                        S.socket.send(JSON.stringify({
                            action: 'stream_data',
                            data: text,
                            input_type: 'text'
                        }));

                        if (!options.preserveInputValue) {
                            textInputBox.value = '';
                        }
                        if (!isReactWindowSource) {
                            window.appendMessage(text, 'user', true, {
                                skipReactSync: sentImageUrls.length > 0
                            });
                        }

                        // Achievement: meow detection
                        if (window.incrementAchievementCounter) {
                            var meowPattern = /\u55B5|miao|meow|nya[no]?|\u306B\u3083|\uB0E5|\u043C\u044F\u0443/i;
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

                    if (!isReactWindowSource && window.appChat && typeof window.appChat.appendReactUserMessage === 'function' && sentImageUrls.length > 0) {
                        window.appChat.appendReactUserMessage({
                            text: text,
                            imageUrls: sentImageUrls
                        });
                    }

                    updateReactOptimisticMessageStatus('sent');

                    // Reset proactive chat timer
                    if (S.proactiveChatEnabled && window.hasAnyChatModeEnabled()) {
                        window.resetProactiveChatBackoff();
                    }

                    window.showStatusToast(window.t ? window.t('app.textChattingShort') : '\u6B63\u5728\u6587\u672C\u804A\u5929\u4E2D', 2000);
                } catch (sendError) {
                    console.error('[Chat] send text payload failed:', sendError);
                    updateReactOptimisticMessageStatus('failed');
                    window.showStatusToast(
                        window.t
                            ? window.t('app.sendFailed', { error: sendError.message })
                            : '\u53D1\u9001\u5931\u8D25: ' + sendError.message,
                        5000
                    );
                }
            } else {
                updateReactOptimisticMessageStatus('failed');
                window.showStatusToast(window.t ? window.t('app.websocketNotConnected') : 'WebSocket\u672A\u8FDE\u63A5\uFF01', 4000);
            }
        }

        mod.sendTextPayload = sendTextPayload;
        window.sendTextPayload = sendTextPayload;

        // ----------------------------------------------------------------
        // Text send button click
        // ----------------------------------------------------------------
        textSendButton.addEventListener('click', async function () {
            await sendTextPayload(textInputBox.value, { source: 'legacy-text-button' });
        });

        // 中文输入法候选确认时，Enter 也会参与组合输入流程；这里单独跟踪，避免误发消息。
        textInputBox.addEventListener('compositionstart', function () {
            textInputComposing = true;
        });

        textInputBox.addEventListener('compositionend', function () {
            textInputComposing = false;
            lastTextCompositionEndAt = Date.now();
        });

        // ----------------------------------------------------------------
        // Enter key sends text (Shift+Enter for newline)
        // ----------------------------------------------------------------
        textInputBox.addEventListener('keydown', function (e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                var isImeEnter = e.isComposing || e.keyCode === 229 || textInputComposing;
                var justEndedComposition = lastTextCompositionEndAt > 0 && (Date.now() - lastTextCompositionEndAt) < 80;

                if (isImeEnter || justEndedComposition) {
                    return;
                }

                e.preventDefault();
                textSendButton.click();
            }
        });

        // 工具：将 dataUrl 图片降采样到 720p 上限并重新编码为 JPEG 0.8，保持与既有流水线一致。
        // 如果图片本身已经在 720p 以内，直接返回原 dataUrl，避免无谓的解码/再编码。
        // 返回 { dataUrl, width, height }：width/height 始终是"返回的这张图"的实际尺寸，
        // 避免调用方把源尺寸误当成最终尺寸写进日志/UI。
        async function downscaleDataUrlTo720p(srcDataUrl) {
            if (!srcDataUrl) return { dataUrl: null, width: 0, height: 0 };
            var maxW = (window.appConst && window.appConst.MAX_SCREENSHOT_WIDTH) || 1280;
            var maxH = (window.appConst && window.appConst.MAX_SCREENSHOT_HEIGHT) || 720;
            return await new Promise(function (resolve) {
                var img = new Image();
                img.onload = function () {
                    var w = img.naturalWidth, h = img.naturalHeight;
                    if (!w || !h) { resolve({ dataUrl: srcDataUrl, width: 0, height: 0 }); return; }
                    if (w <= maxW && h <= maxH) { resolve({ dataUrl: srcDataUrl, width: w, height: h }); return; }
                    var scale = Math.min(maxW / w, maxH / h);
                    var tw = Math.max(1, Math.round(w * scale));
                    var th = Math.max(1, Math.round(h * scale));
                    try {
                        var cv = document.createElement('canvas');
                        cv.width = tw; cv.height = th;
                        var cx = cv.getContext('2d');
                        cx.drawImage(img, 0, 0, tw, th);
                        resolve({ dataUrl: cv.toDataURL('image/jpeg', 0.8), width: tw, height: th });
                    } catch (e) {
                        console.warn('[截图] 降采样失败，使用原图:', e);
                        resolve({ dataUrl: srcDataUrl, width: w, height: h });
                    }
                };
                img.onerror = function (e) {
                    console.warn('[截图] 图片加载失败，使用原图:', e);
                    resolve({ dataUrl: srcDataUrl, width: 0, height: 0 });
                };
                img.src = srcDataUrl;
            });
        }

        mod.captureScreenshotToPendingList = async function captureScreenshotToPendingList() {
            // 桌面端优先级：
            //   1) 主进程直接 desktopCapturer 捕获选中源（最可靠，绕开所有 Chromium 桌面捕获管线问题）
            //   2) acquireOrReuseCachedStream（缓存流 / Electron chromeMediaSourceId / getDisplayMedia）
            //   3) 后端 pyautogui（只能截主屏）
            // isCachedStream 用于区分缓存流（绝不能关）与一次性流（finally 要关）。
            var acquiredStream = null;
            var isCachedStream = false;

            try {
                screenshotButton.disabled = true;
                window.showStatusToast(window.t ? window.t('app.capturing') : '\u6B63\u5728\u622A\u56FE...', 2000);

                var dataUrl = null;
                var width = 0, height = 0;

                if (U.isMobile()) {
                    // 移动端：沿用摄像头采集，永远是一次性流
                    try {
                        acquiredStream = await window.getMobileCameraStream();
                    } catch (mobileErr) {
                        console.warn('[截图] 移动端摄像头获取失败:', mobileErr);
                        // 无条件抛出：保留原始错误 name（NotAllowedError / NotFoundError /
                        // NotReadableError 等），让外层 catch 的分支能给出对应的本地化提示。
                        throw mobileErr;
                    }
                    if (acquiredStream) {
                        var mframe = await window.captureFrameFromStream(acquiredStream, 0.8);
                        if (mframe) {
                            dataUrl = mframe.dataUrl;
                            width = mframe.width;
                            height = mframe.height;
                        }
                    }
                } else {
                    // === 优先级 1：主进程直接捕获选中源 ===
                    // 只要渲染器知道用户选了某个源，就让主进程用 desktopCapturer 的高分辨率缩略图
                    // 对该源做一次静态快照。完全绕开 getUserMedia(chromeMediaSourceId) 和
                    // getDisplayMedia + setDisplayMediaRequestHandler 这条 Chromium 桌面捕获管线
                    // ——在 Electron 41 / Windows 11 + useSystemPicker:true 的组合下，这条管线对窗口
                    // 源常常返回整个屏幕。主进程 desktopCapturer 则直接由平台原生 API 支持，可靠。
                    var selectedSourceId = S.selectedScreenSourceId;
                    if (selectedSourceId && window.electronDesktopCapturer
                        && typeof window.electronDesktopCapturer.captureSourceAsDataUrl === 'function') {
                        try {
                            var direct = await window.electronDesktopCapturer.captureSourceAsDataUrl(selectedSourceId);
                            if (direct && direct.success && direct.dataUrl) {
                                var scaled = await downscaleDataUrlTo720p(direct.dataUrl);
                                dataUrl = scaled.dataUrl;
                                // 以降采样后的实际尺寸为准；解码失败时 scaled.width/height 为 0，
                                // 此时回退到主进程上报的原始尺寸，避免日志空值。
                                width = scaled.width || direct.width || 0;
                                height = scaled.height || direct.height || 0;
                                console.log('[截图] 主进程直接捕获成功:', selectedSourceId, width + 'x' + height);
                            } else if (direct && direct.error) {
                                console.warn('[截图] 主进程直接捕获失败:', direct.error);
                            }
                        } catch (directErr) {
                            console.warn('[截图] 主进程直接捕获抛错，将回退到流路径:', directErr);
                        }
                    }

                    // === 优先级 2：acquireOrReuseCachedStream 流路径 ===
                    if (!dataUrl && typeof window.acquireOrReuseCachedStream === 'function') {
                        try {
                            // 用户手势上下文（点击截图按钮）→ allowPrompt:true，允许 getDisplayMedia
                            acquiredStream = await window.acquireOrReuseCachedStream({ allowPrompt: true });
                        } catch (acqErr) {
                            if (acqErr && acqErr.name === 'NotAllowedError') throw acqErr;
                            console.warn('[截图] acquireOrReuseCachedStream 抛错:', acqErr);
                            acquiredStream = null;
                        }

                        if (acquiredStream) {
                            // 与全局缓存流等值比较 ⇒ acquireOrReuseCachedStream 新建的流一定写回 S.screenCaptureStream
                            isCachedStream = (acquiredStream === S.screenCaptureStream);
                            var frame = await window.captureFrameFromStream(acquiredStream, 0.8);
                            if (frame) {
                                dataUrl = frame.dataUrl;
                                width = frame.width;
                                height = frame.height;
                                if (isCachedStream) {
                                    S.screenCaptureStreamLastUsed = Date.now();
                                    if (window.scheduleScreenCaptureIdleCheck) window.scheduleScreenCaptureIdleCheck();
                                }
                            }
                        }
                    }

                    // === 优先级 3：后端 pyautogui（只能截主屏，且需 localhost）===
                    if (!dataUrl) {
                        try {
                            var backendResult = await window.fetchBackendScreenshot();
                            if (backendResult && backendResult.dataUrl) {
                                // 后端 pyautogui 返回原生分辨率（2K/4K 显示器会超过 720p 上限），
                                // 与主进程直接捕获路径保持一致，统一降采样到 MAX_SCREENSHOT_WIDTH/HEIGHT。
                                var beScaled = await downscaleDataUrlTo720p(backendResult.dataUrl);
                                dataUrl = beScaled.dataUrl;
                                width = beScaled.width || 0;
                                height = beScaled.height || 0;
                            }
                        } catch (beErr) {
                            console.warn('[截图] 后端兜底失败:', beErr);
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
                // 只释放一次性流；缓存流由 acquireOrReuseCachedStream 体系管理，绝不能在这里停
                if (!isCachedStream && acquiredStream instanceof MediaStream) {
                    try {
                        acquiredStream.getTracks().forEach(function (track) {
                            try { track.stop(); } catch (e) { }
                        });
                    } catch (e) { }
                }
                screenshotButton.disabled = false;
            }
        };

        // ----------------------------------------------------------------
        // Screenshot button click
        // ----------------------------------------------------------------
        screenshotButton.addEventListener('click', mod.captureScreenshotToPendingList);

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
                mod.syncPendingComposerAttachments();
            }
        });

        if (window.reactChatWindowHost && typeof window.reactChatWindowHost.setOnComposerSubmit === 'function') {
            window.reactChatWindowHost.setOnComposerSubmit(function (detail) {
                return mod.sendTextPayload(detail && detail.text, { source: 'react-chat-window' });
            });
        }
        if (window.reactChatWindowHost && typeof window.reactChatWindowHost.setOnComposerImportImage === 'function') {
            window.reactChatWindowHost.setOnComposerImportImage(function () {
                return mod.openImageImportPicker();
            });
        }
        if (window.reactChatWindowHost && typeof window.reactChatWindowHost.setOnComposerScreenshot === 'function') {
            window.reactChatWindowHost.setOnComposerScreenshot(function () {
                return mod.captureScreenshotToPendingList();
            });
        }
        if (window.reactChatWindowHost && typeof window.reactChatWindowHost.setOnComposerRemoveAttachment === 'function') {
            window.reactChatWindowHost.setOnComposerRemoveAttachment(function (attachmentId) {
                return mod.removePendingAttachmentById(attachmentId);
            });
        }

        mod.ensureImportImageInput();
        mod.syncPendingComposerAttachments();
    };

    window.appButtons = mod;
})();
