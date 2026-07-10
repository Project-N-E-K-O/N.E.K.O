(function (root, factory) {
    'use strict';

    const api = factory();
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    }
    if (root) {
        root.TutorialScriptNormalizer = api;
    }
})(typeof window !== 'undefined' ? window : globalThis, function () {
    'use strict';

    const DEFAULT_CURSOR_START_MS = 220;
    const DEFAULT_CURSOR_MOVE_MS = 760;
    const DEFAULT_CURSOR_FOLLOWUP_MS = 360;
    const DEFAULT_PETAL_CUE_BEFORE_AUDIO_END_MS = 2600;

    function clonePlain(value) {
        if (!value || typeof value !== 'object') {
            return value;
        }
        if (Array.isArray(value)) {
            return value.map(clonePlain);
        }
        const result = {};
        Object.keys(value).forEach((key) => {
            result[key] = clonePlain(value[key]);
        });
        return result;
    }

    function numberOrDefault(value, fallback) {
        return Number.isFinite(value) ? Math.max(0, Math.floor(value)) : fallback;
    }

    function getSceneTarget(scene) {
        if (!scene || typeof scene !== 'object') {
            return '';
        }
        if (typeof scene.cursorTarget === 'string' && scene.cursorTarget.trim()) {
            return scene.cursorTarget.trim();
        }
        if (typeof scene.target === 'string' && scene.target.trim()) {
            return scene.target.trim();
        }
        return '';
    }

    function pushIfCommand(timeline, event) {
        if (event && typeof event.command === 'string' && event.command.trim()) {
            timeline.push(event);
        }
    }

    function isGalgameWheelRotationOperation(scene) {
        return !!(
            scene
            && scene.operation === 'rotate-galgame-tool-into-center'
        );
    }

    function normalizeExistingTimeline(scene) {
        const timeline = Array.isArray(scene.timeline)
            ? scene.timeline.map(clonePlain)
            : [];
        return timeline
            .filter((event) => event && typeof event === 'object' && typeof event.command === 'string')
            .map((event, index) => Object.assign({
                id: event.id || (scene.id ? scene.id + ':cmd:' + index : 'cmd:' + index)
            }, event));
    }

    function normalizeLegacyTimeline(scene, options) {
        const normalizedOptions = options || {};
        const timeline = [];
        const sceneId = typeof scene.id === 'string' ? scene.id : '';
        const cursorAction = typeof scene.cursorAction === 'string'
            ? scene.cursorAction.trim()
            : '';
        const cursorTarget = getSceneTarget(scene);
        const cursorMoveDurationMs = numberOrDefault(
            scene.cursorMoveDurationMs,
            numberOrDefault(normalizedOptions.defaultCursorMoveDurationMs, DEFAULT_CURSOR_MOVE_MS)
        );
        const cursorStartMs = numberOrDefault(
            scene.cursorStartMs,
            numberOrDefault(normalizedOptions.defaultCursorStartMs, DEFAULT_CURSOR_START_MS)
        );
        const cursorFollowupMs = cursorStartMs + cursorMoveDurationMs;

        pushIfCommand(timeline, {
            id: sceneId + ':chat-message',
            at: 0,
            command: 'chat.message',
            textKey: scene.textKey || '',
            text: scene.text || '',
            voiceKey: scene.voiceKey || ''
        });

        if (scene.emotion) {
            pushIfCommand(timeline, {
                id: sceneId + ':emotion',
                at: 0,
                command: 'emotion.set',
                emotion: scene.emotion
            });
        }

        if (scene.spotlight !== false) {
            pushIfCommand(timeline, {
                id: sceneId + ':spotlight',
                at: 0,
                command: 'spotlight.show',
                key: scene.spotlightKey || sceneId,
                target: typeof scene.target === 'string' ? scene.target : '',
                persistent: typeof scene.persistent === 'string' ? scene.persistent : '',
                secondary: typeof scene.secondary === 'string' ? scene.secondary : ''
            });
        }

        if (cursorAction === 'hold') {
            const holdEvent = {
                id: sceneId + ':cursor-hold',
                at: 0,
                command: 'cursor.hold',
                target: cursorTarget
            };
            if (scene.cursorHoldFreezePoint === true) {
                holdEvent.freezePoint = true;
            }
            pushIfCommand(timeline, holdEvent);
            const cursorHoldSettleMs = numberOrDefault(scene.cursorHoldSettleMs, 0);
            if (cursorHoldSettleMs > 0) {
                const settleHoldEvent = Object.assign({}, holdEvent, {
                    id: sceneId + ':cursor-hold-settle',
                    at: cursorHoldSettleMs
                });
                pushIfCommand(timeline, settleHoldEvent);
            }
        } else if (cursorAction && cursorAction !== 'none') {
            const moveEvent = {
                id: sceneId + ':cursor-move',
                at: cursorStartMs,
                command: 'cursor.move',
                action: cursorAction,
                target: cursorTarget,
                secondary: typeof scene.secondary === 'string' ? scene.secondary : '',
                durationMs: cursorMoveDurationMs
            };
            if (scene.freezeCursorAfterMove === true) {
                moveEvent.freezePoint = true;
            }
            pushIfCommand(timeline, moveEvent);
            if (cursorAction === 'click') {
                const clickEvent = {
                    id: sceneId + ':cursor-click',
                    at: cursorFollowupMs,
                    command: 'cursor.click',
                    target: cursorTarget,
                    effectDurationMs: numberOrDefault(scene.cursorClickDurationMs, 420)
                };
                if (scene.operation && !isGalgameWheelRotationOperation(scene)) {
                    const operationEvent = {
                        id: sceneId + ':operation',
                        command: 'operation.run',
                        operation: scene.operation,
                        trigger: 'onClickStart',
                        blocking: true
                    };
                    if (scene.preserveExternalizedChatGuideTarget === true) {
                        operationEvent.preserveExternalizedChatGuideTarget = true;
                    }
                    clickEvent.blocking = true;
                    clickEvent.onStart = [operationEvent];
                }
                pushIfCommand(timeline, clickEvent);
            } else if (cursorAction === 'wobble' || cursorAction === 'tour') {
                pushIfCommand(timeline, {
                    id: sceneId + ':cursor-wobble',
                    at: cursorFollowupMs,
                    command: 'cursor.wobble',
                    target: cursorTarget,
                    durationMs: numberOrDefault(scene.cursorWobbleDurationMs, DEFAULT_CURSOR_FOLLOWUP_MS)
                });
            }
        }

        if (isGalgameWheelRotationOperation(scene)) {
            pushIfCommand(timeline, {
                id: sceneId + ':galgame-wheel-rotation',
                at: cursorFollowupMs,
                command: 'compactToolWheel.rotateGalgameIntoCenter',
                target: cursorTarget,
                blocking: true
            });
        } else if (scene.operation && cursorAction !== 'click') {
            const operationEvent = {
                id: sceneId + ':operation',
                at: cursorAction === 'click' ? cursorFollowupMs : cursorFollowupMs,
                command: 'operation.run',
                operation: scene.operation,
                trigger: cursorAction === 'click' ? 'onClickStart' : 'afterCursorMove',
                blocking: true
            };
            if (scene.preserveExternalizedChatGuideTarget === true) {
                operationEvent.preserveExternalizedChatGuideTarget = true;
            }
            pushIfCommand(timeline, operationEvent);
        }

        appendPetalTransitionEvent(timeline, scene, sceneId);

        return timeline;
    }

    function appendPetalTransitionEvent(timeline, scene, sceneId) {
        if (!Array.isArray(timeline) || !scene || scene.petalTransition !== true) {
            return false;
        }
        if (timeline.some((event) => event && event.command === 'petal.play')) {
            return false;
        }

        const beforeAudioEndMs = Number.isFinite(scene.petalCueBeforeAudioEndMs)
            ? Math.max(1, Math.floor(scene.petalCueBeforeAudioEndMs))
            : DEFAULT_PETAL_CUE_BEFORE_AUDIO_END_MS;
        // 修改原因：花瓣转场的真实开始点由 PetalTransitionController 按剩余时长精确等待，
        // normalizer 只负责提前进入通用 handler，不能再固定在音频 70% 处。
        pushIfCommand(timeline, {
            id: sceneId + ':petal',
            beforeAudioEndMs,
            command: 'petal.play',
            clear: ['cursor', 'spotlights'],
            blocking: true
        });
        return true;
    }

    function normalizeTutorialScene(scene, options) {
        const normalizedScene = scene && typeof scene === 'object' ? scene : {};
        const disableTimelineAudio = normalizedScene.timelineAudio === false;
        const audio = disableTimelineAudio
            ? {}
            : Object.assign({}, normalizedScene.audio || {});
        if (!disableTimelineAudio) {
            if (!audio.voiceKey && normalizedScene.voiceKey) {
                audio.voiceKey = normalizedScene.voiceKey;
            }
            if (!audio.textKey && normalizedScene.textKey) {
                audio.textKey = normalizedScene.textKey;
            }
            if (!audio.text && normalizedScene.text) {
                audio.text = normalizedScene.text;
            }
            if (!Number.isFinite(audio.minDurationMs)) {
                audio.minDurationMs = 1800;
            }
        }

        const timeline = Array.isArray(normalizedScene.timeline)
            ? normalizeExistingTimeline(normalizedScene)
            : normalizeLegacyTimeline(normalizedScene, options);
        appendPetalTransitionEvent(timeline, normalizedScene, typeof normalizedScene.id === 'string' ? normalizedScene.id : '');

        const completion = Object.assign({
            mode: normalizedScene.waitForUserAction ? 'user-action' : 'audio-and-blocking-commands',
            afterSceneDelayMs: Number.isFinite(normalizedScene.afterSceneDelayMs)
                ? Math.max(0, Math.floor(normalizedScene.afterSceneDelayMs))
                : 420
        }, normalizedScene.completion || {});
        completion.afterSceneDelayMs = Number.isFinite(completion.afterSceneDelayMs)
            ? Math.max(0, Math.floor(completion.afterSceneDelayMs))
            : 420;

        return {
            id: typeof normalizedScene.id === 'string' ? normalizedScene.id : '',
            audio,
            timeline,
            completion,
            cleanup: Object.assign({}, normalizedScene.cleanup || {}),
            legacyScene: clonePlain(normalizedScene)
        };
    }

    return {
        normalizeTutorialScene
    };
});
