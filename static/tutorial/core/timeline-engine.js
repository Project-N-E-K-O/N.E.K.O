(function (root, factory) {
    'use strict';

    const api = factory();
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    }
    if (root) {
        root.TutorialTimelineEngine = api;
    }
})(typeof window !== 'undefined' ? window : globalThis, function () {
    'use strict';

    function defaultNow() {
        return Date.now();
    }

    function defaultWait(delayMs) {
        const root = typeof window !== 'undefined' ? window : globalThis;
        return new Promise((resolve) => root.setTimeout(resolve, Math.max(0, delayMs)));
    }

    function clamp(value, min, max) {
        return Math.max(min, Math.min(max, value));
    }

    function getAudioDurationMs(scene, audioRuntime) {
        const audio = scene && scene.audio ? scene.audio : {};
        if (
            audioRuntime
            && typeof audioRuntime.getDurationMs === 'function'
            && audio.voiceKey
        ) {
            const durationMs = Number(audioRuntime.getDurationMs(audio.voiceKey, audio.locale || ''));
            if (Number.isFinite(durationMs) && durationMs > 0) {
                return Math.floor(durationMs);
            }
        }
        if (Number.isFinite(audio.durationMs) && audio.durationMs > 0) {
            return Math.floor(audio.durationMs);
        }
        if (Number.isFinite(audio.minDurationMs) && audio.minDurationMs > 0) {
            return Math.floor(audio.minDurationMs);
        }
        return 0;
    }

    function resolveEventTimeMs(event, scene, audioRuntime) {
        if (!event || typeof event !== 'object') {
            return 0;
        }
        if (Number.isFinite(event.atMs)) {
            return Math.max(0, Math.floor(event.atMs));
        }
        if (Number.isFinite(event.at)) {
            return Math.max(0, Math.floor(event.at));
        }
        if (Number.isFinite(event.atRatio)) {
            const durationMs = getAudioDurationMs(scene, audioRuntime);
            return Math.max(0, Math.floor(durationMs * clamp(event.atRatio, 0, 1)));
        }
        if (Number.isFinite(event.beforeAudioEndMs)) {
            const durationMs = getAudioDurationMs(scene, audioRuntime);
            const beforeAudioEndMs = Math.max(0, Math.floor(event.beforeAudioEndMs));
            // 修改原因：花瓣转场要按“音频结束前的动画窗口”触发，但仍需等音频启动后再派发，
            // 这样 handler 才能拿到 narrationStartedAt 并继续做精确等待。
            return Math.max(1, Math.floor(durationMs - beforeAudioEndMs));
        }
        if (
            event.cue
            && audioRuntime
            && typeof audioRuntime.resolveCueMs === 'function'
        ) {
            const audio = scene && scene.audio ? scene.audio : {};
            const cueMs = Number(audioRuntime.resolveCueMs(audio.voiceKey || '', event.cue, scene));
            if (Number.isFinite(cueMs)) {
                return Math.max(0, Math.floor(cueMs));
            }
        }
        return 0;
    }

    function createRunToken(sceneId, runId) {
        let cancelled = false;
        return {
            sceneId,
            runId,
            cancel() {
                cancelled = true;
            },
            isCancelled() {
                return cancelled;
            }
        };
    }

    class TimelineEngine {
        constructor(options) {
            const normalizedOptions = options || {};
            this.commandRegistry = normalizedOptions.commandRegistry || null;
            this.audioRuntime = normalizedOptions.audioRuntime || null;
            this.now = typeof normalizedOptions.now === 'function' ? normalizedOptions.now : defaultNow;
            this.wait = typeof normalizedOptions.wait === 'function' ? normalizedOptions.wait : defaultWait;
            this.isPaused = typeof normalizedOptions.isPaused === 'function' ? normalizedOptions.isPaused : () => false;
            this.waitUntilResumed = typeof normalizedOptions.waitUntilResumed === 'function'
                ? normalizedOptions.waitUntilResumed
                : () => Promise.resolve();
            this.isCancelled = typeof normalizedOptions.isCancelled === 'function'
                ? normalizedOptions.isCancelled
                : () => false;
            this.nextRunId = 0;
            this.activeRunToken = null;
        }

        cancelActiveRun() {
            if (this.activeRunToken && typeof this.activeRunToken.cancel === 'function') {
                this.activeRunToken.cancel();
            }
            this.activeRunToken = null;
        }

        isRunCancelled(runToken) {
            return !!(
                !runToken
                || runToken.isCancelled()
                || this.isCancelled(runToken)
                || this.activeRunToken !== runToken
            );
        }

        createContext(scene, runToken, extraContext) {
            const context = extraContext && typeof extraContext === 'object' ? extraContext : {};
            return Object.assign(context, {
                scene,
                runToken,
                audioRuntime: this.audioRuntime,
                commandRegistry: this.commandRegistry
            });
        }

        prepareEvents(scene) {
            const timeline = Array.isArray(scene && scene.timeline) ? scene.timeline : [];
            return timeline
                .filter((event) => event && typeof event === 'object' && typeof event.command === 'string')
                .map((event, index) => Object.assign({}, event, {
                    id: event.id || ((scene && scene.id ? scene.id : 'scene') + ':cmd:' + index),
                    atMs: resolveEventTimeMs(event, scene, this.audioRuntime),
                    index
                }))
                .sort((left, right) => {
                    if (left.atMs !== right.atMs) {
                        return left.atMs - right.atMs;
                    }
                    return left.index - right.index;
                });
        }

        dispatchEvent(event, context, triggered) {
            triggered.push(event.id);
            const resultPromise = this.commandRegistry && typeof this.commandRegistry.dispatch === 'function'
                ? this.commandRegistry.dispatch(event, context)
                : Promise.resolve(null);
            return Promise.resolve(resultPromise);
        }

        watchNonBlockingEvent(event, resultPromise) {
            Promise.resolve(resultPromise).catch((error) => {
                console.warn('[TutorialTimeline] non-blocking event failed:', event && event.command, error);
            });
        }

        async waitForTimelineTime(targetMs, startedAt, pausedDurationRef, runToken) {
            while (!this.isRunCancelled(runToken)) {
                if (this.isPaused()) {
                    const pausedAt = this.now();
                    await this.waitUntilResumed();
                    pausedDurationRef.value += Math.max(0, this.now() - pausedAt);
                    continue;
                }
                const elapsedMs = Math.max(0, this.now() - startedAt - pausedDurationRef.value);
                const remainingMs = targetMs - elapsedMs;
                if (remainingMs <= 0) {
                    return true;
                }
                await this.wait(Math.min(remainingMs, 60));
            }
            return false;
        }

        async playScene(scene, extraContext) {
            const normalizedScene = scene && typeof scene === 'object' ? scene : {};
            const runToken = createRunToken(normalizedScene.id || '', ++this.nextRunId);
            this.activeRunToken = runToken;
            const context = this.createContext(normalizedScene, runToken, extraContext);
            const triggered = [];
            const pausedDurationRef = { value: 0 };
            const events = this.prepareEvents(normalizedScene);
            const postAudioEvents = events.filter((event) => event.afterAudioEnd === true);
            const preAudioEvents = events.filter((event) => event.afterAudioEnd !== true && event.atMs <= 0);
            const timelineEvents = events.filter((event) => event.afterAudioEnd !== true && event.atMs > 0);

            if (preAudioEvents.length > 0) {
                for (let index = 0; index < preAudioEvents.length; index += 1) {
                    const event = preAudioEvents[index];
                    const resultPromise = this.dispatchEvent(event, context, triggered);
                    if (event.blocking === false) {
                        this.watchNonBlockingEvent(event, resultPromise);
                        await Promise.resolve();
                    } else {
                        await Promise.resolve(resultPromise);
                    }
                    if (this.isRunCancelled(runToken)) {
                        return {
                            completed: false,
                            cancelled: true,
                            triggered
                        };
                    }
                }
            }

            if (
                this.audioRuntime
                && typeof this.audioRuntime.play === 'function'
                && normalizedScene.audio
                && normalizedScene.audio.voiceKey
            ) {
                await Promise.resolve(this.audioRuntime.play(normalizedScene.audio.voiceKey, normalizedScene.audio));
            }

            const startedAt = this.now();
            for (let index = 0; index < timelineEvents.length; index += 1) {
                const event = timelineEvents[index];
                const waited = await this.waitForTimelineTime(event.atMs, startedAt, pausedDurationRef, runToken);
                if (!waited || this.isRunCancelled(runToken)) {
                    return {
                        completed: false,
                        cancelled: true,
                        triggered
                    };
                }
                const resultPromise = this.dispatchEvent(event, context, triggered);
                if (event.blocking === true) {
                    await Promise.resolve(resultPromise);
                    if (this.isRunCancelled(runToken)) {
                        return {
                            completed: false,
                            cancelled: true,
                            triggered
                        };
                    }
                } else {
                    this.watchNonBlockingEvent(event, resultPromise);
                    await Promise.resolve();
                }
            }

            if (this.audioRuntime && typeof this.audioRuntime.waitForEnd === 'function') {
                await Promise.resolve(this.audioRuntime.waitForEnd(normalizedScene.audio || {}));
            }
            if (this.isRunCancelled(runToken)) {
                return {
                    completed: false,
                    cancelled: true,
                    triggered
                };
            }
            for (let index = 0; index < postAudioEvents.length; index += 1) {
                const event = postAudioEvents[index];
                const resultPromise = this.dispatchEvent(event, context, triggered);
                if (event.blocking === true) {
                    await Promise.resolve(resultPromise);
                } else {
                    this.watchNonBlockingEvent(event, resultPromise);
                }
                if (this.isRunCancelled(runToken)) {
                    return {
                        completed: false,
                        cancelled: true,
                        triggered
                    };
                }
            }

            const cancelled = this.isRunCancelled(runToken);
            if (this.activeRunToken === runToken) {
                this.activeRunToken = null;
            }
            return {
                completed: !cancelled,
                cancelled,
                triggered
            };
        }
    }

    function createTutorialTimelineEngine(options) {
        return new TimelineEngine(options);
    }

    return {
        TimelineEngine,
        createTutorialTimelineEngine,
        resolveEventTimeMs
    };
});
