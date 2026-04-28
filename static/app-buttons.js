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
    mod.addScreenshotToList = function addScreenshotToList(dataUrl, avatarPosition) {
        S.screenshotCounter++;

        const screenshotsList = S.dom.screenshotsList;
        const screenshotThumbnailContainer = S.dom.screenshotThumbnailContainer;

        // Create screenshot item container
        const item = document.createElement('div');
        item.className = 'screenshot-item';
        item.dataset.index = S.screenshotCounter;
        item.dataset.attachmentId = 'attachment-' + Date.now() + '-' + S.screenshotCounter;
        // Store avatar position metadata (captured at screenshot time)
        if (avatarPosition) {
            item.dataset.avatarPosition = JSON.stringify(avatarPosition);
        }

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

    var AVATAR_INTERACTION_ALLOWED_ACTIONS = Object.freeze({
        lollipop: Object.freeze(['offer', 'tease', 'tap_soft']),
        fist: Object.freeze(['poke']),
        hammer: Object.freeze(['bonk'])
    });
    var AVATAR_INTERACTION_ALLOWED_INTENSITIES = Object.freeze(['normal', 'rapid', 'burst', 'easter_egg']);
    var AVATAR_INTERACTION_ALLOWED_TOUCH_ZONES = Object.freeze(['ear', 'head', 'face', 'body']);
    var AVATAR_INTERACTION_SEED_FALLBACK_MS = 2200;
    var AVATAR_INTERACTION_ACK_TIMEOUT_MS = 8000;
    var AVATAR_INTERACTION_TURN_START_TIMEOUT_MS = 5000;
    var AVATAR_INTERACTION_TURN_COMPLETION_TIMEOUT_MS = 15000;
    var AVATAR_INTERACTION_HOST_COOLDOWN_MS = 600;
    var AVATAR_INTERACTION_HOST_SPEAK_COOLDOWN_MS = 1500;
    var AVATAR_INTERACTION_SEED_EMOTIONS = Object.freeze({
        lollipop: Object.freeze({
            offer: Object.freeze({
                normal: 'happy'
            }),
            tease: Object.freeze({
                normal: 'surprised'
            }),
            tap_soft: Object.freeze({
                rapid: 'happy',
                burst: 'happy'
            })
        }),
        fist: Object.freeze({
            poke: Object.freeze({
                normal: 'happy',
                rapid: 'surprised',
                reward_drop: 'happy'
            })
        }),
        hammer: Object.freeze({
            bonk: Object.freeze({
                normal: 'surprised',
                rapid: 'angry',
                burst: 'angry',
                easter_egg: 'angry'
            })
        })
    });
    var avatarInteractionSeedState = {
        interactionId: '',
        timerId: 0,
        previousEmotion: null,
        seedEmotion: null
    };
    var avatarInteractionTextContinuationState = {
        interactionId: '',
        expectedTurnId: '',
        activeTurnId: '',
        phase: 'idle',
        ackTimerId: 0,
        turnStartTimerId: 0,
        completionTimerId: 0,
        deferredTextSubmissions: [],
        deferredSendHandler: null,
        drainingDeferredTextSubmissions: false
    };
    var avatarInteractionDispatchGateState = {
        reservedInteractionId: '',
        activeInteractionId: '',
        activeDispatchAt: 0,
        lastDispatchAt: 0,
        speakCooldownUntil: 0
    };

    function hasReservedAvatarInteractionDispatch() {
        return !!avatarInteractionDispatchGateState.reservedInteractionId;
    }

    function reserveAvatarInteractionDispatch(interactionId) {
        if (!interactionId || hasReservedAvatarInteractionDispatch()) {
            return false;
        }
        avatarInteractionDispatchGateState.reservedInteractionId = interactionId;
        return true;
    }

    function releaseAvatarInteractionDispatchReservation(interactionId) {
        if (interactionId
                && avatarInteractionDispatchGateState.reservedInteractionId
                && avatarInteractionDispatchGateState.reservedInteractionId !== interactionId) {
            return;
        }
        avatarInteractionDispatchGateState.reservedInteractionId = '';
    }

    function setActiveAvatarInteractionDispatch(interactionId, dispatchedAt) {
        avatarInteractionDispatchGateState.activeInteractionId = interactionId || '';
        avatarInteractionDispatchGateState.activeDispatchAt = interactionId ? dispatchedAt : 0;
        if (interactionId) {
            avatarInteractionDispatchGateState.lastDispatchAt = dispatchedAt;
        }
    }

    function clearActiveAvatarInteractionDispatch(interactionId) {
        if (interactionId
                && avatarInteractionDispatchGateState.activeInteractionId
                && avatarInteractionDispatchGateState.activeInteractionId !== interactionId) {
            return;
        }
        avatarInteractionDispatchGateState.activeInteractionId = '';
        avatarInteractionDispatchGateState.activeDispatchAt = 0;
    }

    function noteAvatarInteractionSpeakCooldown(interactionId) {
        if (interactionId
                && avatarInteractionDispatchGateState.activeInteractionId
                && avatarInteractionDispatchGateState.activeInteractionId !== interactionId) {
            return;
        }
        var dispatchedAt = avatarInteractionDispatchGateState.activeDispatchAt || Date.now();
        var cooldownUntil = dispatchedAt + AVATAR_INTERACTION_HOST_SPEAK_COOLDOWN_MS;
        if (cooldownUntil > avatarInteractionDispatchGateState.speakCooldownUntil) {
            avatarInteractionDispatchGateState.speakCooldownUntil = cooldownUntil;
        }
    }

    function getAvatarInteractionDispatchThrottleReason(nowMs) {
        var now = Number.isFinite(nowMs) ? nowMs : Date.now();
        if (hasReservedAvatarInteractionDispatch()) {
            return 'host_pending_dispatch';
        }
        if (hasPendingAvatarInteractionContinuation()) {
            return 'host_pending_turn';
        }
        if (avatarInteractionDispatchGateState.speakCooldownUntil > now) {
            return 'host_speak_cooldown';
        }
        if (avatarInteractionDispatchGateState.lastDispatchAt
                && (now - avatarInteractionDispatchGateState.lastDispatchAt) < AVATAR_INTERACTION_HOST_COOLDOWN_MS) {
            return 'host_cooldown';
        }
        return '';
    }

    function clearAvatarInteractionContinuationTimer(timerKey) {
        if (!avatarInteractionTextContinuationState[timerKey]) {
            return;
        }
        window.clearTimeout(avatarInteractionTextContinuationState[timerKey]);
        avatarInteractionTextContinuationState[timerKey] = 0;
    }

    function clearAvatarInteractionContinuationTimers() {
        clearAvatarInteractionContinuationTimer('ackTimerId');
        clearAvatarInteractionContinuationTimer('turnStartTimerId');
        clearAvatarInteractionContinuationTimer('completionTimerId');
    }

    function hasPendingAvatarInteractionContinuation() {
        return avatarInteractionTextContinuationState.phase !== 'idle'
            && !!avatarInteractionTextContinuationState.interactionId;
    }

    function queueDeferredTextSubmission(text, options) {
        avatarInteractionTextContinuationState.deferredTextSubmissions.push({
            text: String(text || ''),
            options: Object.assign({}, options || {})
        });
    }

    function flushDeferredTextSubmissions() {
        if (hasPendingAvatarInteractionContinuation()) {
            return;
        }

        var sendHandler = avatarInteractionTextContinuationState.deferredSendHandler;
        if (typeof sendHandler !== 'function') {
            return;
        }

        if (avatarInteractionTextContinuationState.drainingDeferredTextSubmissions) {
            return;
        }

        if (!avatarInteractionTextContinuationState.deferredTextSubmissions.length) {
            return;
        }

        avatarInteractionTextContinuationState.drainingDeferredTextSubmissions = true;
        var pending = avatarInteractionTextContinuationState.deferredTextSubmissions.slice();
        avatarInteractionTextContinuationState.deferredTextSubmissions = [];
        var nextPendingIndex = 0;

        (async function () {
            for (var index = 0; index < pending.length; index += 1) {
                nextPendingIndex = index;
                var submission = pending[index];
                var sent = await sendHandler(submission.text, Object.assign({}, submission.options, {
                    skipAvatarInteractionDeferral: true
                }));
                if (sent === false) {
                    queueDeferredTextSubmission(submission.text, submission.options);
                }
                nextPendingIndex = index + 1;
            }
        })().catch(function (error) {
            console.error('[AvatarInteraction] deferred text flush failed:', error);
            avatarInteractionTextContinuationState.deferredTextSubmissions = pending.slice(nextPendingIndex).concat(
                avatarInteractionTextContinuationState.deferredTextSubmissions
            );
        }).finally(function () {
            avatarInteractionTextContinuationState.drainingDeferredTextSubmissions = false;
            if (!hasPendingAvatarInteractionContinuation()
                    && avatarInteractionTextContinuationState.deferredTextSubmissions.length > 0) {
                flushDeferredTextSubmissions();
            }
        });
    }

    function releaseDeferredTextAfterAvatarInteraction() {
        clearAvatarInteractionContinuationTimers();
        releaseAvatarInteractionDispatchReservation();
        clearActiveAvatarInteractionDispatch();
        avatarInteractionTextContinuationState.interactionId = '';
        avatarInteractionTextContinuationState.expectedTurnId = '';
        avatarInteractionTextContinuationState.activeTurnId = '';
        avatarInteractionTextContinuationState.phase = 'idle';
        flushDeferredTextSubmissions();
    }

    function beginAvatarInteractionTextContinuation(interactionId) {
        if (!interactionId || hasPendingAvatarInteractionContinuation()) {
            return;
        }

        clearAvatarInteractionContinuationTimers();
        avatarInteractionTextContinuationState.interactionId = interactionId;
        avatarInteractionTextContinuationState.expectedTurnId = '';
        avatarInteractionTextContinuationState.activeTurnId = '';
        avatarInteractionTextContinuationState.phase = 'awaiting_ack';
        avatarInteractionTextContinuationState.ackTimerId = window.setTimeout(function () {
            if (avatarInteractionTextContinuationState.phase !== 'awaiting_ack'
                    || avatarInteractionTextContinuationState.interactionId !== interactionId) {
                return;
            }
            releaseDeferredTextAfterAvatarInteraction();
        }, AVATAR_INTERACTION_ACK_TIMEOUT_MS);
    }

    function markAvatarInteractionAccepted(interactionId, turnId) {
        if (!interactionId || avatarInteractionTextContinuationState.interactionId !== interactionId) {
            return;
        }

        clearAvatarInteractionContinuationTimer('ackTimerId');
        if (avatarInteractionTextContinuationState.phase === 'active_turn') {
            return;
        }

        avatarInteractionTextContinuationState.expectedTurnId = String(turnId || '').trim();
        avatarInteractionTextContinuationState.activeTurnId = '';
        avatarInteractionTextContinuationState.phase = 'awaiting_turn';
        clearAvatarInteractionContinuationTimer('turnStartTimerId');
        avatarInteractionTextContinuationState.turnStartTimerId = window.setTimeout(function () {
            if (avatarInteractionTextContinuationState.phase !== 'awaiting_turn'
                    || avatarInteractionTextContinuationState.interactionId !== interactionId) {
                return;
            }
            releaseDeferredTextAfterAvatarInteraction();
        }, AVATAR_INTERACTION_TURN_START_TIMEOUT_MS);
    }

    function markAvatarInteractionTurnStarted(turnId) {
        if (!hasPendingAvatarInteractionContinuation()) {
            return;
        }
        var normalizedTurnId = String(turnId || '').trim();
        if (!normalizedTurnId || avatarInteractionTextContinuationState.phase !== 'awaiting_turn') {
            return;
        }
        if (avatarInteractionTextContinuationState.expectedTurnId
                && avatarInteractionTextContinuationState.expectedTurnId !== normalizedTurnId) {
            return;
        }

        clearAvatarInteractionContinuationTimer('ackTimerId');
        clearAvatarInteractionContinuationTimer('turnStartTimerId');
        avatarInteractionTextContinuationState.activeTurnId = normalizedTurnId;
        avatarInteractionTextContinuationState.phase = 'active_turn';
        clearAvatarInteractionContinuationTimer('completionTimerId');
        avatarInteractionTextContinuationState.completionTimerId = window.setTimeout(function () {
            if (avatarInteractionTextContinuationState.phase !== 'active_turn') {
                return;
            }
            releaseDeferredTextAfterAvatarInteraction();
        }, AVATAR_INTERACTION_TURN_COMPLETION_TIMEOUT_MS);
    }

    function bindAvatarInteractionTextContinuationLifecycle() {
        if (mod._avatarInteractionTextContinuationLifecycleBound) {
            return;
        }
        mod._avatarInteractionTextContinuationLifecycleBound = true;

        window.addEventListener('neko-avatar-interaction-ack', function (event) {
            var detail = event && event.detail ? event.detail : {};
            var interactionId = String(detail.interactionId || detail.interaction_id || '').trim();
            var turnId = String(detail.turnId || detail.turn_id || '').trim();
            if (!interactionId || avatarInteractionTextContinuationState.interactionId !== interactionId) {
                return;
            }
            if (detail.accepted === true) {
                noteAvatarInteractionSpeakCooldown(interactionId);
                if (String(detail.reason || '').trim() === 'delivered') {
                    releaseDeferredTextAfterAvatarInteraction();
                    return;
                }
                markAvatarInteractionAccepted(interactionId, turnId);
                return;
            }
            releaseDeferredTextAfterAvatarInteraction();
        });

        window.addEventListener('neko-assistant-turn-start', function (event) {
            if (!hasPendingAvatarInteractionContinuation()) {
                return;
            }
            var detail = event && event.detail ? event.detail : {};
            markAvatarInteractionTurnStarted(detail.turnId || detail.turn_id || '');
        });

        window.addEventListener('neko-assistant-turn-end', function (event) {
            if (!hasPendingAvatarInteractionContinuation()) {
                return;
            }
            var detail = event && event.detail ? event.detail : {};
            var turnId = String(detail.turnId || detail.turn_id || '').trim();
            if (!turnId || avatarInteractionTextContinuationState.activeTurnId !== turnId) {
                return;
            }
            releaseDeferredTextAfterAvatarInteraction();
        });

        window.addEventListener('neko-assistant-speech-cancel', function (event) {
            if (!hasPendingAvatarInteractionContinuation()) {
                return;
            }
            var detail = event && event.detail ? event.detail : {};
            var turnId = String(detail.turnId || detail.turn_id || '').trim();
            if (!turnId || avatarInteractionTextContinuationState.activeTurnId !== turnId) {
                return;
            }
            releaseDeferredTextAfterAvatarInteraction();
        });
    }

    function sanitizeAvatarInteractionTextContext(value) {
        var text = String(value || '').trim();
        if (!text) return '';
        return text.length > 80 ? text.slice(0, 80).trimEnd() : text;
    }

    function normalizeAvatarInteractionPayload(payload) {
        if (!payload || typeof payload !== 'object') {
            console.warn('[AvatarInteraction] ignored invalid payload:', payload);
            return null;
        }

        var toolId = String(payload.toolId || '').trim().toLowerCase();
        var actionId = String(payload.actionId || '').trim().toLowerCase();
        var allowedActions = AVATAR_INTERACTION_ALLOWED_ACTIONS[toolId];
        if (!allowedActions || allowedActions.indexOf(actionId) === -1) {
            console.warn('[AvatarInteraction] ignored unsupported tool/action:', toolId, actionId);
            return null;
        }

        if (String(payload.target || '').trim().toLowerCase() !== 'avatar') {
            console.warn('[AvatarInteraction] ignored non-avatar target:', payload.target);
            return null;
        }

        var interactionId = String(payload.interactionId || '').trim();
        if (!interactionId) {
            console.warn('[AvatarInteraction] ignored payload without interactionId');
            return null;
        }

        var timestamp = Number(payload.timestamp);
        if (!Number.isFinite(timestamp) || timestamp <= 0) {
            timestamp = Date.now();
        }

        var normalized = {
            action: 'avatar_interaction',
            interaction_id: interactionId,
            tool_id: toolId,
            action_id: actionId,
            target: 'avatar',
            timestamp: timestamp
        };

        if (payload.pointer && typeof payload.pointer === 'object') {
            var clientX = Number(payload.pointer.clientX);
            var clientY = Number(payload.pointer.clientY);
            if (Number.isFinite(clientX) && Number.isFinite(clientY)) {
                normalized.pointer = {
                    clientX: clientX,
                    clientY: clientY
                };
            }
        }

        var touchZone = String(payload.touchZone || payload.touch_zone || '').trim().toLowerCase();
        if (AVATAR_INTERACTION_ALLOWED_TOUCH_ZONES.indexOf(touchZone) !== -1) {
            normalized.touch_zone = touchZone;
        }

        var intensity = String(payload.intensity || '').trim().toLowerCase();
        if (AVATAR_INTERACTION_ALLOWED_INTENSITIES.indexOf(intensity) !== -1) {
            if (toolId === 'hammer' || intensity !== 'easter_egg') {
                normalized.intensity = intensity;
            }
        }

        var textContext = sanitizeAvatarInteractionTextContext(payload.textContext);
        if (textContext) {
            normalized.text_context = textContext;
        }

        if (toolId === 'fist' && payload.rewardDrop === true) {
            normalized.reward_drop = true;
        }

        if (toolId === 'hammer' && payload.easterEgg === true) {
            normalized.easter_egg = true;
        }

        return normalized;
    }

    function getCurrentAvatarEmotion() {
        try {
            if (window.live2dManager && typeof window.live2dManager.currentEmotion === 'string' && window.live2dManager.currentEmotion) {
                return window.live2dManager.currentEmotion;
            }
            if (window.mmdManager && window.mmdManager.expression && typeof window.mmdManager.expression.currentMood === 'string' && window.mmdManager.expression.currentMood) {
                return window.mmdManager.expression.currentMood;
            }
            if (window.vrmManager && window.vrmManager.expression && typeof window.vrmManager.expression.currentMood === 'string' && window.vrmManager.expression.currentMood) {
                return window.vrmManager.expression.currentMood;
            }
        } catch (_error) {
            return 'neutral';
        }
        return 'neutral';
    }

    function clearAvatarInteractionSeedTimer() {
        if (avatarInteractionSeedState.timerId) {
            window.clearTimeout(avatarInteractionSeedState.timerId);
            avatarInteractionSeedState.timerId = 0;
        }
    }

    function resolveAvatarInteractionSeedEmotion(payload) {
        if (!payload || typeof payload !== 'object') {
            return null;
        }

        var toolId = String(payload.tool_id || payload.toolId || '').trim().toLowerCase();
        var actionId = String(payload.action_id || payload.actionId || '').trim().toLowerCase();
        var intensity = String(payload.intensity || '').trim().toLowerCase() || 'normal';
        var toolMap = AVATAR_INTERACTION_SEED_EMOTIONS[toolId];
        var actionMap = toolMap && toolMap[actionId];
        if (!actionMap) {
            return null;
        }
        if (toolId === 'fist' && payload.reward_drop === true) {
            return actionMap.reward_drop || actionMap.normal || null;
        }
        if (toolId === 'hammer' && payload.easter_egg === true) {
            return actionMap.easter_egg || actionMap[intensity] || actionMap.normal || null;
        }
        return actionMap[intensity] || actionMap.normal || null;
    }

    function clearAvatarInteractionSeedState() {
        clearAvatarInteractionSeedTimer();
        avatarInteractionSeedState.interactionId = '';
        avatarInteractionSeedState.seedEmotion = null;
        avatarInteractionSeedState.previousEmotion = null;
    }

    function applyAvatarInteractionSeedEmotion(payload) {
        var interactionId = String(payload && (payload.interaction_id || payload.interactionId) || '').trim();
        var seedEmotion = resolveAvatarInteractionSeedEmotion(payload);
        if (!interactionId || !seedEmotion || typeof window.applyEmotion !== 'function') {
            return;
        }

        var previousEmotion = avatarInteractionSeedState.previousEmotion;
        if (!avatarInteractionSeedState.interactionId) {
            previousEmotion = getCurrentAvatarEmotion();
        }

        clearAvatarInteractionSeedTimer();
        avatarInteractionSeedState.interactionId = interactionId;
        avatarInteractionSeedState.seedEmotion = seedEmotion;
        avatarInteractionSeedState.previousEmotion = previousEmotion || 'neutral';

        window.applyEmotion(seedEmotion);

        avatarInteractionSeedState.timerId = window.setTimeout(function () {
            if (avatarInteractionSeedState.interactionId !== interactionId) {
                return;
            }
            var fallbackEmotion = avatarInteractionSeedState.previousEmotion || 'neutral';
            clearAvatarInteractionSeedState();
            if (typeof window.applyEmotion === 'function') {
                window.applyEmotion(fallbackEmotion);
            }
        }, AVATAR_INTERACTION_SEED_FALLBACK_MS);
    }

    function bindAvatarInteractionSeedLifecycle() {
        if (mod._avatarInteractionSeedLifecycleBound) {
            return;
        }
        mod._avatarInteractionSeedLifecycleBound = true;

        window.addEventListener('neko-assistant-emotion-ready', function () {
            clearAvatarInteractionSeedState();
        });
    }

    async function sendAvatarInteractionPayload(payload) {
        var normalized = normalizeAvatarInteractionPayload(payload);
        if (!normalized) {
            return false;
        }

        var throttleReason = getAvatarInteractionDispatchThrottleReason(Date.now());
        if (throttleReason) {
            console.debug(
                '[AvatarInteraction] host gate skipped:',
                throttleReason,
                normalized.tool_id,
                normalized.action_id
            );
            return false;
        }

        if (!reserveAvatarInteractionDispatch(normalized.interaction_id)) {
            console.debug('[AvatarInteraction] host gate skipped: host_pending_dispatch');
            return false;
        }

        beginAvatarInteractionTextContinuation(normalized.interaction_id);

        try {
            await window.ensureWebSocketOpen();
            if (!S.socket || S.socket.readyState !== WebSocket.OPEN) {
                throw new Error('WEBSOCKET_NOT_CONNECTED');
            }
            S.socket.send(JSON.stringify(normalized));
            setActiveAvatarInteractionDispatch(normalized.interaction_id, Date.now());
            applyAvatarInteractionSeedEmotion(normalized);
            return true;
        } catch (error) {
            console.error('[AvatarInteraction] send failed:', error);
            if (avatarInteractionTextContinuationState.interactionId === normalized.interaction_id) {
                releaseDeferredTextAfterAvatarInteraction();
            }
            return false;
        } finally {
            releaseAvatarInteractionDispatchReservation(normalized.interaction_id);
        }
    }

    mod.normalizeAvatarInteractionPayload = normalizeAvatarInteractionPayload;
    mod.sendAvatarInteractionPayload = sendAvatarInteractionPayload;

    function clearReactChatWindowHostBindingPoll() {
        if (!mod._reactChatWindowHostBindingPollId) {
            return;
        }
        window.clearInterval(mod._reactChatWindowHostBindingPollId);
        mod._reactChatWindowHostBindingPollId = 0;
    }

    function bindReactChatWindowHostCallbacks() {
        var host = window.reactChatWindowHost;
        if (!host
                || typeof host.setOnComposerSubmit !== 'function'
                || typeof host.setOnComposerImportImage !== 'function'
                || typeof host.setOnComposerScreenshot !== 'function'
                || typeof host.setOnComposerRemoveAttachment !== 'function'
                || typeof host.setOnAvatarInteraction !== 'function') {
            return false;
        }
        if (mod._boundReactChatWindowHost === host) {
            mod.syncPendingComposerAttachments();
            return true;
        }

        host.setOnComposerSubmit(function (detail) {
            return mod.sendTextPayload(detail && detail.text, {
                source: 'react-chat-window',
                requestId: detail && detail.requestId
            });
        });
        host.setOnComposerImportImage(function () {
            return mod.openImageImportPicker();
        });
        host.setOnComposerScreenshot(function () {
            if (window.__NEKO_MULTI_WINDOW__ && window.nekoScreenshotProxy) {
                window.nekoScreenshotProxy.request();
            } else {
                return mod.captureScreenshotToPendingList();
            }
        });
        host.setOnComposerRemoveAttachment(function (attachmentId) {
            return mod.removePendingAttachmentById(attachmentId);
        });
        host.setOnAvatarInteraction(function (payload) {
            return mod.sendAvatarInteractionPayload(payload);
        });

        mod._boundReactChatWindowHost = host;
        mod.syncPendingComposerAttachments();
        return true;
    }

    function ensureReactChatWindowHostCallbacks() {
        if (bindReactChatWindowHostCallbacks()) {
            clearReactChatWindowHostBindingPoll();
            return;
        }
        if (mod._reactChatWindowHostBindingPollId) {
            return;
        }

        var remainingAttempts = 80;
        mod._reactChatWindowHostBindingPollId = window.setInterval(function () {
            remainingAttempts--;
            if (bindReactChatWindowHostCallbacks() || remainingAttempts <= 0) {
                clearReactChatWindowHostBindingPoll();
            }
        }, 250);
    }

    // ======================== init — wire up all event listeners ========================

    mod.init = function init() {
        bindAvatarInteractionSeedLifecycle();
        bindAvatarInteractionTextContinuationLifecycle();

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

            // Deactivate tool cursor mode (lollipop/cat paw/hammer)
            // Prefer the React host cleanup path so cursor teardown stays in one place.
            if (window.reactChatWindowHost && typeof window.reactChatWindowHost.deactivateToolCursor === 'function') {
                window.reactChatWindowHost.deactivateToolCursor();
            } else {
                window.dispatchEvent(new CustomEvent('neko:deactivate-tool-cursor'));
                var _body = document.body;
                var _root = document.documentElement;
                _root.style.setProperty('cursor', 'auto', 'important');
                if (_body) {
                    _body.style.setProperty('cursor', 'auto', 'important');
                }
                _root.classList.remove('neko-tool-cursor-active');
                _root.style.removeProperty('--neko-chat-tool-cursor');
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

                window.dispatchEvent(new CustomEvent('neko:voice-session-started'));

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

        async function sendTextPayloadInternal(rawText, options) {
            options = options || {};
            var text = String(typeof rawText === 'string' ? rawText : '').trim();
            var hasScreenshots = screenshotsList.children.length > 0;
            var requestId = (typeof options.requestId === 'string' && options.requestId)
                ? options.requestId
                : ('req-' + Date.now() + '-' + Math.random().toString(36).slice(2, 8));

            // Store last submitted text for rollback on RESPONSE_TOO_LONG.
            // Clear stale text for pure-screenshot submissions.
            window._lastSubmittedText = text;
            window._lastSubmittedRequestId = text ? requestId : '';
            var isReactWindowSource = options.source === 'react-chat-window';
            var reactOptimisticMessageId = '';
            var reactOptimisticMessageAppended = null;
            var sentUserContent = false;

            if (!text && !hasScreenshots) return false;

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
                                var msg = {
                                    action: 'stream_data',
                                    data: img.src,
                                    input_type: U.isMobile() ? 'camera' : 'screen'
                                };
                                // Attach paired avatar position metadata (captured at screenshot time)
                                var storedPos = screenshotItems[i].dataset.avatarPosition;
                                if (storedPos) {
                                    try { msg.avatar_position = JSON.parse(storedPos); } catch (e) { /* ignore */ }
                                }
                                S.socket.send(JSON.stringify(msg));
                            }
                        }

                        if (!isReactWindowSource) {
                            var screenshotItemCount = screenshotItems.length;
                            window.appendMessage('\uD83D\uDCF8 [\u5DF2\u53D1\u9001' + screenshotItemCount + '\u5F20\u622A\u56FE]', 'user', true, {
                                skipReactSync: true
                            });
                        }
                        sentUserContent = true;

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
                            input_type: 'text',
                            request_id: requestId
                        }));

                        if (!options.preserveInputValue) {
                            textInputBox.value = '';
                        }
                        if (!isReactWindowSource) {
                            window.appendMessage(text, 'user', true, {
                                skipReactSync: sentImageUrls.length > 0
                            });
                        }
                        sentUserContent = true;

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

                    if (sentUserContent) {
                        window.dispatchEvent(new CustomEvent('neko:user-content-sent'));
                    }

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
                return false;
            }
        }

        avatarInteractionTextContinuationState.deferredSendHandler = sendTextPayloadInternal;
        flushDeferredTextSubmissions();

        async function sendTextPayload(rawText, options) {
            options = options || {};
            var text = String(typeof rawText === 'string' ? rawText : '').trim();
            var hasScreenshots = screenshotsList.children.length > 0;

            if (!text && !hasScreenshots) return;

            if (options.skipAvatarInteractionDeferral !== true
                    && text
                    && !hasScreenshots
                    && hasPendingAvatarInteractionContinuation()) {
                queueDeferredTextSubmission(text, options);
                textInputBox.value = '';
                textInputComposing = false;
                lastTextCompositionEndAt = 0;
                return true;
            }

            return sendTextPayloadInternal(rawText, Object.assign({}, options, {
                skipAvatarInteractionDeferral: true
            }));
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

        // ----------------------------------------------------------------
        // Hide NEKO UI, recapture screen, then restore
        // ----------------------------------------------------------------
        // 先前通过枚举固定 ID 列表逐个 display:none — 遗漏了动态挂载的浮层
        // (avatar popup / HUD / tutorial overlay / 第三方对话框) 以及 Electron 下
        // 另外开的透明窗口以外还残留在主窗口的各种子元素，导致重拍后 N.E.K.O 仍然
        // 出现在截图里。改为直接对 <html> 根元素切 visibility:hidden —— 一次把整页
        // 画面抹掉，OS 合成器拿到的只有 Electron 透明窗体后的桌面像素。
        function hideNekoUI() {
            var root = document.documentElement;
            var saved = {
                visibility: root.style.visibility,
                // 保险：有些 reaction bubble / toast 直接挂在 body，visibility 继承即可覆盖
            };
            root.style.visibility = 'hidden';
            return saved;
        }

        function restoreNekoUI(saved) {
            if (!saved) return;
            document.documentElement.style.visibility = saved.visibility || '';
        }

        async function recaptureWithoutNeko() {
            // Priority 0 (Electron PC): 主进程原子化路径 — 一次 IPC 完成
            //   隐藏所有 NEKO 窗口 → 等合成 → desktopCapturer 抓图 → 恢复窗口。
            //   把 hide/等待/抓图/show 全放主进程是因为渲染器端 setTimeout 在 Pet 窗口
            //   hide 后会被 backgroundThrottling 拖慢到秒级，且多次 IPC 之间有时序风险。
            var selectedSourceId = S.selectedScreenSourceId;
            if (selectedSourceId && window.electronDesktopCapturer
                && typeof window.electronDesktopCapturer.captureSourceWithoutNeko === 'function') {
                try {
                    var atomic = await window.electronDesktopCapturer.captureSourceWithoutNeko(selectedSourceId);
                    if (atomic && atomic.success && atomic.dataUrl) {
                        var atomicScaled = await downscaleDataUrlTo720p(atomic.dataUrl);
                        if (atomicScaled && atomicScaled.dataUrl) return atomicScaled.dataUrl;
                    } else if (atomic && atomic.error) {
                        console.warn('[隐藏NEKO] 主进程原子化路径失败:', atomic.error);
                        if (typeof window.maybeClearSourceOnNotFound === 'function') {
                            window.maybeClearSourceOnNotFound(atomic, 'recaptureWithoutNeko atomic Source not found');
                        }
                    }
                } catch (e) {
                    console.warn('[隐藏NEKO] 主进程原子化路径抛错，回退到渲染器路径:', e);
                }
                // 主进程路径失败则继续走下面 renderer 端的兜底（visibility:hidden + MediaStream）
            }

            // Fallback：web 浏览器模式或主进程路径失败 —— 渲染器侧 CSS 隐藏 + 常规抓屏兜底
            // Electron 下额外让主进程 hide 卫星窗口；Pet 自己的 DOM 用 visibility:hidden 处理。
            // MediaStream 抓帧（getDisplayMedia）会把卫星窗口也拍进去，CSS 隐藏覆盖不到它们。
            var saved = hideNekoUI();
            var fallbackHiddenIds = null;
            if (window.electronDesktopCapturer
                && typeof window.electronDesktopCapturer.hideNekoWindows === 'function') {
                try {
                    var hideRes = await window.electronDesktopCapturer.hideNekoWindows();
                    if (hideRes && Array.isArray(hideRes.hiddenIds)) {
                        fallbackHiddenIds = hideRes.hiddenIds;
                    }
                } catch (e) {
                    console.warn('[隐藏NEKO][fallback] hide 卫星窗口失败:', e);
                }
            }
            await new Promise(function (r) { setTimeout(r, 300); });
            try {
                // Priority 1: Electron direct capture (不隐藏卫星窗口版本，仅为向后兼容兜底)
                // 读当前的 S.selectedScreenSourceId —— Priority 0 若刚命中 'Source not found'
                // 已经通过 maybeClearSourceOnNotFound 把它清空，此时 selectedSourceId 这个本地
                // 快照已是僵尸 ID；继续用它只会让主进程再原样报一次 'Source not found'，
                // 多一次 IPC 往返。重读 S 直接跳到 Priority 2 流路径。
                var currentSourceId = S.selectedScreenSourceId;
                if (currentSourceId && window.electronDesktopCapturer
                    && typeof window.electronDesktopCapturer.captureSourceAsDataUrl === 'function') {
                    try {
                        var direct = await window.electronDesktopCapturer.captureSourceAsDataUrl(currentSourceId);
                        if (direct && direct.success && direct.dataUrl) {
                            var scaled = await downscaleDataUrlTo720p(direct.dataUrl);
                            if (scaled && scaled.dataUrl) return scaled.dataUrl;
                        } else if (typeof window.maybeClearSourceOnNotFound === 'function') {
                            window.maybeClearSourceOnNotFound(direct, 'recaptureWithoutNeko Priority 1 Source not found');
                        }
                    } catch (e) { /* fallback below */ }
                }

                // Priority 2: acquireOrReuseCachedStream / cached stream
                if (typeof window.acquireOrReuseCachedStream === 'function') {
                    try {
                        var acqStream = await window.acquireOrReuseCachedStream({ allowPrompt: false });
                        if (acqStream) {
                            var isCached = (acqStream === S.screenCaptureStream);
                            try {
                                var frame = await window.captureFrameFromStream(acqStream, 0.8);
                                if (frame && frame.dataUrl) return frame.dataUrl;
                            } finally {
                                if (!isCached && acqStream instanceof MediaStream) {
                                    acqStream.getTracks().forEach(function (t) { try { t.stop(); } catch (e) {} });
                                }
                            }
                        }
                    } catch (e) { /* fallback below */ }
                } else {
                    try {
                        if (S.screenCaptureStream && S.screenCaptureStream.active) {
                            var tracks = S.screenCaptureStream.getVideoTracks();
                            if (tracks.length > 0 && tracks.some(function (t) { return t.readyState === 'live'; })) {
                                var cachedFrame = await window.captureFrameFromStream(S.screenCaptureStream, 0.8);
                                if (cachedFrame && cachedFrame.dataUrl) return cachedFrame.dataUrl;
                            }
                        }
                    } catch (e) { /* fallback below */ }
                }

                // Priority 3: backend pyautogui
                var result = await window.fetchBackendScreenshot();
                if (result && result.dataUrl) {
                    var beScaled = await downscaleDataUrlTo720p(result.dataUrl);
                    return (beScaled && beScaled.dataUrl) || null;
                }
                return null;
            } finally {
                // 先恢复卫星窗口，再恢复 Pet 的 DOM visibility —— 反过来用户会看到
                // 孤零零的 Pet 一帧。
                if (fallbackHiddenIds && fallbackHiddenIds.length > 0
                    && window.electronDesktopCapturer
                    && typeof window.electronDesktopCapturer.restoreNekoWindows === 'function') {
                    try {
                        await window.electronDesktopCapturer.restoreNekoWindows(fallbackHiddenIds);
                    } catch (e) {
                        console.warn('[隐藏NEKO][fallback] 恢复卫星窗口失败:', e);
                    }
                }
                restoreNekoUI(saved);
            }
        }

        /**
         * 纯截图+裁剪逻辑，不操作 UI。
         * 返回 { dataUrl, originalDataUrl, avatarPos }；用户取消裁剪时返回 null。
         */
        var _captureScreenshotDataUrlBusy = false;

        mod.captureScreenshotDataUrl = async function captureScreenshotDataUrl() {
            if (_captureScreenshotDataUrlBusy) {
                console.warn('[截图] 截图流程进行中，忽略重复请求');
                throw new Error('SCREENSHOT_BUSY');
            }
            _captureScreenshotDataUrlBusy = true;
            var acquiredStream = null;
            var isCachedStream = false;
            var captureType = null;

            try {
                var dataUrl = null;
                var width = 0, height = 0;

                if (U.isMobile()) {
                    try {
                        acquiredStream = await window.getMobileCameraStream();
                    } catch (mobileErr) {
                        console.warn('[截图] 移动端摄像头获取失败:', mobileErr);
                        throw mobileErr;
                    }
                    if (acquiredStream) {
                        var mframe = await window.captureFrameFromStream(acquiredStream, 0.8);
                        if (mframe) {
                            dataUrl = mframe.dataUrl;
                            width = mframe.width;
                            height = mframe.height;
                            captureType = null;
                        }
                    }
                } else {
                    var selectedSourceId = S.selectedScreenSourceId;
                    if (selectedSourceId && window.electronDesktopCapturer
                        && typeof window.electronDesktopCapturer.captureSourceAsDataUrl === 'function') {
                        try {
                            var direct = await window.electronDesktopCapturer.captureSourceAsDataUrl(selectedSourceId);
                            if (direct && direct.success && direct.dataUrl) {
                                var scaled = await downscaleDataUrlTo720p(direct.dataUrl);
                                dataUrl = scaled.dataUrl;
                                width = scaled.width || direct.width || 0;
                                height = scaled.height || direct.height || 0;
                                captureType = window.detectScreenshotCaptureType
                                    ? window.detectScreenshotCaptureType(null, selectedSourceId)
                                    : null;
                                console.log('[截图] 主进程直接捕获成功:', selectedSourceId, width + 'x' + height);
                            } else if (direct && direct.error) {
                                console.warn('[截图] 主进程直接捕获失败:', direct.error);
                                if (typeof window.maybeClearSourceOnNotFound === 'function') {
                                    window.maybeClearSourceOnNotFound(direct, '主进程 capture-source-as-dataurl Source not found');
                                }
                            }
                        } catch (directErr) {
                            console.warn('[截图] 主进程直接捕获抛错，将回退到流路径:', directErr);
                        }
                    }

                    if (!dataUrl && typeof window.acquireOrReuseCachedStream === 'function') {
                        try {
                            acquiredStream = await window.acquireOrReuseCachedStream({ allowPrompt: true });
                        } catch (acqErr) {
                            if (acqErr && acqErr.name === 'NotAllowedError') throw acqErr;
                            console.warn('[截图] acquireOrReuseCachedStream 抛错:', acqErr);
                            acquiredStream = null;
                        }

                        if (acquiredStream) {
                            isCachedStream = (acquiredStream === S.screenCaptureStream);
                            var frame = await window.captureFrameFromStream(acquiredStream, 0.8);
                            if (frame) {
                                dataUrl = frame.dataUrl;
                                width = frame.width;
                                height = frame.height;
                                captureType = window.detectScreenshotCaptureType
                                    ? window.detectScreenshotCaptureType(acquiredStream, S.selectedScreenSourceId)
                                    : null;
                                if (isCachedStream) {
                                    S.screenCaptureStreamLastUsed = Date.now();
                                    if (window.scheduleScreenCaptureIdleCheck) window.scheduleScreenCaptureIdleCheck();
                                }
                            }
                        }
                    }

                    if (!dataUrl) {
                        try {
                            var backendResult = await window.fetchBackendScreenshot();
                            if (backendResult && backendResult.dataUrl) {
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

                var avatarPos = typeof window.getAvatarScreenPosition === 'function'
                    ? window.getAvatarScreenPosition(captureType) : null;

                if (!isCachedStream && acquiredStream instanceof MediaStream) {
                    acquiredStream.getTracks().forEach(function (track) {
                        try { track.stop(); } catch (e) { }
                    });
                    acquiredStream = null;
                }

                // 在显示裁剪 overlay 前隐藏其他 NEKO 窗口（如 Chat 窗口），
                // 避免它们的 z-order 遮挡 Pet 窗口中的全屏裁剪界面。
                var hiddenIds = null;
                if (window.electronDesktopCapturer
                    && typeof window.electronDesktopCapturer.hideNekoWindows === 'function') {
                    try {
                        var hideRes = await window.electronDesktopCapturer.hideNekoWindows();
                        if (hideRes && Array.isArray(hideRes.hiddenIds)) {
                            hiddenIds = hideRes.hiddenIds;
                        }
                    } catch (hideErr) {
                        console.warn('[截图] 隐藏其他窗口失败:', hideErr);
                    }
                }

                try {
                    if (window.appCrop && typeof window.appCrop.cropImage === 'function') {
                        var croppedUrl = await window.appCrop.cropImage(dataUrl, {
                            recaptureFn: function () { return recaptureWithoutNeko(); }
                        });
                        if (!croppedUrl) {
                            return null;
                        }
                        return { dataUrl: croppedUrl, originalDataUrl: dataUrl, avatarPos: avatarPos };
                    } else {
                        return { dataUrl: dataUrl, originalDataUrl: dataUrl, avatarPos: avatarPos };
                    }
                } finally {
                    if (hiddenIds && hiddenIds.length > 0
                        && window.electronDesktopCapturer
                        && typeof window.electronDesktopCapturer.restoreNekoWindows === 'function') {
                        try {
                            await window.electronDesktopCapturer.restoreNekoWindows(hiddenIds);
                        } catch (restoreErr) {
                            console.warn('[截图] 恢复其他窗口失败:', restoreErr);
                        }
                    }
                }
            } finally {
                _captureScreenshotDataUrlBusy = false;
                if (!isCachedStream && acquiredStream instanceof MediaStream) {
                    try {
                        acquiredStream.getTracks().forEach(function (track) {
                            try { track.stop(); } catch (e) { }
                        });
                    } catch (e) { }
                }
            }
        };
        window.captureScreenshotDataUrl = mod.captureScreenshotDataUrl;

        mod.captureScreenshotToPendingList = async function captureScreenshotToPendingList() {
            try {
                screenshotButton.disabled = true;
                window.showStatusToast(window.t ? window.t('app.capturing') : '\u6B63\u5728\u622A\u56FE...', 2000);

                var result = await mod.captureScreenshotDataUrl();
                if (!result) {
                    window.showStatusToast(window.t ? window.t('app.screenshotCancelled') : '\u5DF2\u53D6\u6D88\u622A\u56FE', 2000);
                    return;
                }

                mod.addScreenshotToList(result.dataUrl, result.dataUrl === result.originalDataUrl ? result.avatarPos : null);
                window.showStatusToast(window.t ? window.t('app.screenshotAdded') : '\u622A\u56FE\u5DF2\u6DFB\u52A0\uFF0C\u70B9\u51FB\u53D1\u9001\u4E00\u8D77\u53D1\u9001', 3000);
            } catch (err) {
                console.error(window.t('console.screenshotFailed'), err);

                if (err.message === 'SCREENSHOT_BUSY') {
                    return;
                }
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

        ensureReactChatWindowHostCallbacks();

        // ----------------------------------------------------------------
        // Clipboard paste → add image to pending screenshots
        // ----------------------------------------------------------------
        document.addEventListener('paste', function (e) {
            if (!e.clipboardData || !e.clipboardData.items) return;
            // Don't handle paste when crop overlay is open
            var cropOverlay = document.getElementById('crop-overlay');
            if (cropOverlay && cropOverlay.style.display !== 'none') return;
            var items = e.clipboardData.items;
            for (var i = 0; i < items.length; i++) {
                if (items[i].type.indexOf('image/') === 0) {
                    e.preventDefault();
                    var blob = items[i].getAsFile();
                    if (!blob) continue;
                    var reader = new FileReader();
                    reader.onload = function (ev) {
                        if (ev.target && ev.target.result) {
                            mod.addScreenshotToList(ev.target.result);
                            window.showStatusToast(
                                window.t ? window.t('app.screenshotAdded') : '\u622A\u56FE\u5DF2\u6DFB\u52A0\uFF0C\u70B9\u51FB\u53D1\u9001\u4E00\u8D77\u53D1\u9001',
                                3000
                            );
                        }
                    };
                    reader.onerror = function () {
                        console.warn('[粘贴] 读取剪贴板图片失败');
                    };
                    reader.readAsDataURL(blob);
                    break;
                }
            }
        });

        mod.ensureImportImageInput();
        mod.syncPendingComposerAttachments();
    };

    window.appButtons = mod;
})();
