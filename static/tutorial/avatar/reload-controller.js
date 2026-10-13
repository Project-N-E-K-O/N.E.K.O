(function () {
    'use strict';

    function noop() {}

    // 纯运行时抑制（与小剧场 lockProactiveChatForTheater 约定一致）：
    // 本控制器不改写任何 proactive 设置的内存镜像——那些值会被 saveSettings
    // 持久化并同步到其他窗口，临时改写会在抑制期间的任意一次保存里被固化
    // 成用户设置。抑制 = 停运行时 + 登记 proactiveRuntimeHold + 派发事件；
    // 触发面由 app-proactive 的 isHomeTutorialFeatureSuppressed 统一拦截
    // （经 isProactiveRuntimeHeld），标志存活到模型恢复完成为止。

    function stopProactiveRuntime() {
        [
            'stopProactiveChatSchedule',
            'stopProactiveVisionDuringSpeech',
            'releaseProactiveVisionStream'
        ].forEach((methodName) => {
            if (typeof window[methodName] === 'function') {
                try {
                    window[methodName]();
                } catch (error) {
                    console.warn('[TutorialAvatarReloadController] 主动搭话运行时停止失败:', methodName, error);
                }
            }
        });
    }

    function dispatchTutorialSuppressionEvent(active) {
        // 与 home 教程 feature controller 共用同一事件面：抑制开始要停调度、
        // 撤回展示中的情境弹窗（教程期间不弹 modal），解除要重排调度、补挂
        // 视觉、重放暂存的提示。本层的释放事件在模型恢复完成后才派发——
        // feature controller 的 end 先于模型恢复，那一次的 active:false 被
        // 本层 hold 拦下的部分由这条补上。
        try {
            // window.CustomEvent 优先：vm 测试沙箱只挂 window 属性，浏览器里
            // 两者等价。
            const EventConstructor = window.CustomEvent || CustomEvent;
            window.dispatchEvent(new EventConstructor('neko:home-tutorial-features-suppressed', {
                detail: { active: !!active, source: 'avatar-reload-override' }
            }));
        } catch (_) { }
    }

    class TutorialAvatarReloadController {
        constructor(options) {
            const normalizedOptions = options || {};
            this.host = normalizedOptions.host || null;
            this.timeoutMs = Number.isFinite(normalizedOptions.timeoutMs) ? normalizedOptions.timeoutMs : 8000;
            this.tutorialModelName = normalizedOptions.tutorialModelName || 'yui-lolita';
            this.resolveCurrentName = normalizedOptions.resolveCurrentName || noop;
            this.fetchCharacters = normalizedOptions.fetchCharacters || noop;
            this.buildSnapshotPayload = normalizedOptions.buildSnapshotPayload || noop;
            this.fadeOutCurrentModel = normalizedOptions.fadeOutCurrentModel || noop;
            this.reloadModel = normalizedOptions.reloadModel || noop;
            this.setPreparing = normalizedOptions.setPreparing || noop;
            this.revealPrepared = normalizedOptions.revealPrepared || noop;
            this.applyIdentityOverride = normalizedOptions.applyIdentityOverride || noop;
            this.clearViewportWatcher = normalizedOptions.clearViewportWatcher || noop;
            this.override = null;
            this.overridePromise = null;
        }

        hasActiveOverride() {
            return !!this.override;
        }

        isProactiveRuntimeHeld() {
            // 头像层唯一的抑制信号：beginOverride 实际停掉运行时后为 true，
            // restoreOverride 在模型恢复完成后复位。app-proactive 的
            // isHomeTutorialFeatureSuppressed 据此拦截触发面——覆盖
            // feature controller begin 之前的窗口，以及它的 end 先于模型
            // 恢复完成的 teardown 窗口（调度可以排，触发必须拦）。
            return !!(this.override && this.override.proactiveRuntimeHold === true);
        }

        getPendingPromise() {
            return this.overridePromise;
        }

        beginOverride(options) {
            const normalizedOptions = options || {};
            const deferRevealPrepared = normalizedOptions.deferRevealPrepared === true;
            const skipSourceModelFade = normalizedOptions.skipSourceModelFade === true;
            const host = this.host;
            if (!host) {
                return Promise.reject(new Error('tutorial avatar reload host is required'));
            }

            if (this.overridePromise) {
                if (this.override && (this.override.restoring || this.override.restoreRequested)) {
                    return this.overridePromise.then(() => this.beginOverride(options));
                }
                return this.overridePromise;
            }
            if (this.override) {
                return Promise.resolve();
            }

            const activePrefix = host.constructor && typeof host.constructor.detectModelPrefix === 'function'
                ? host.constructor.detectModelPrefix()
                : '';
            this.override = {
                activePrefix: activePrefix,
                restoreRequested: false
            };
            const override = this.override;
            const ensureOverrideActive = () => {
                if (this.override !== override || override.cancelled) {
                    throw new Error('tutorial avatar override setup cancelled');
                }
            };
            const setupDeadline = new Promise((_, reject) => {
                setTimeout(() => {
                    reject(new Error(`tutorial avatar override setup timed out after ${this.timeoutMs}ms`));
                }, this.timeoutMs);
            });

            const setupPromise = Promise.race([(async () => {
                const currentName = await this.resolveCurrentName();
                ensureOverrideActive();
                if (!currentName) {
                    throw new Error('current tutorial catgirl name unavailable');
                }

                const characters = await this.fetchCharacters();
                ensureOverrideActive();
                const catgirls = (characters && characters['猫娘']) || {};
                const currentConfig = catgirls[currentName];
                if (!currentConfig) {
                    throw new Error(`current catgirl config not found: ${currentName}`);
                }

                const snapshotPayload = this.buildSnapshotPayload(currentConfig);
                const tutorialModelPayload = {
                    model_type: 'live2d',
                    live2d: this.tutorialModelName,
                    live2d_idle_animation: ''
                };
                this.override.currentName = currentName;
                this.override.snapshotPayload = snapshotPayload;
                // 纯运行时抑制：停调度/视觉、登记标志、派发事件。不写任何
                // 设置内存，也不管 feature controller 是否已激活——本层标志
                // 要活到模型恢复完成，比 controller 的生命周期更长（它的 end
                // 先于 restoreOverride），两层各管各的、由同一道闸门聚合。
                override.proactiveRuntimeHold = true;
                dispatchTutorialSuppressionEvent(true);
                stopProactiveRuntime();

                if (!skipSourceModelFade) {
                    await Promise.resolve(this.fadeOutCurrentModel({
                        deferRevealPrepared
                    }));
                }
                ensureOverrideActive();
                this.setPreparing(true);
                await this.reloadModel(currentName, tutorialModelPayload, {
                    temporary: true,
                    deferRevealPrepared
                });
                ensureOverrideActive();
                this.setPreparing(true);
                this.applyIdentityOverride({
                    active: true,
                    displayName: 'YUI',
                    avatarDataUrl: '',
                    modelType: 'live2d'
                });
            })(), setupDeadline]).catch(async (error) => {
                override.cancelled = true;
                this.revealPrepared();
                try {
                    await Promise.resolve(this.applyIdentityOverride({ active: false }));
                } catch (identityError) {
                    console.warn('[TutorialAvatarReloadController] 清理临时聊天身份失败:', identityError);
                }
                if (this.override === override) {
                    if (this.overridePromise === setupPromise) {
                        this.overridePromise = null;
                    }
                    await this.restoreOverride();
                }
                console.warn('[TutorialAvatarReloadController] 临时切换 yui-lolita 模型失败:', error);
                throw error;
            });

            this.overridePromise = setupPromise;
            setupPromise.then(
                () => null,
                () => null
            ).then(() => {
                if (this.overridePromise === setupPromise) {
                    this.overridePromise = null;
                }
                if (this.override && this.override.restoreRequested) {
                    this.restoreOverride().catch(error => {
                        console.warn('[TutorialAvatarReloadController] 延迟恢复新手教程头像失败:', error);
                    });
                }
            }).catch(error => {
                console.warn('[TutorialAvatarReloadController] 清理新手教程头像准备状态失败:', error);
            });

            return setupPromise;
        }

        restoreOverride() {
            const host = this.host;
            if (!host) {
                return Promise.resolve();
            }

            const override = this.override;
            if (!override) {
                return Promise.resolve();
            }

            if (this.overridePromise) {
                override.restoreRequested = true;
                return this.overridePromise.then(() => {
                    if (this.override === override && !override.restoring) {
                        return this.restoreOverride();
                    }
                    return this.overridePromise || Promise.resolve();
                });
            }

            const currentName = override.currentName;
            const snapshotPayload = override.snapshotPayload;
            override.restoring = true;

            const restorePromise = Promise.resolve().then(async () => {
                try {
                    this.clearViewportWatcher();
                    this.revealPrepared();
                    this.applyIdentityOverride({ active: false });
                    if (!currentName) {
                        return;
                    }

                    await this.reloadModel(currentName, snapshotPayload || {});
                } catch (error) {
                    console.warn('[TutorialAvatarReloadController] 恢复新手教程前用户模型失败:', error);
                    if (typeof window.showCurrentModel === 'function') {
                        try {
                            await window.showCurrentModel();
                        } catch (_) {}
                    }
                } finally {
                    // 恢复尝试（含 showCurrentModel 降级）结束后解除 hold：
                    // 即使恢复失败也不能永久锁住已结束的教程，当前没有保证
                    // 再次恢复的重试调度；失败已记录，不能将此处视为恢复成功。
                    // app-proactive 监听后重排调度、为进行中的录音补挂视觉，
                    // 情境弹窗模块重放暂存提示。没有任何设置值需要恢复。
                    override.proactiveRuntimeHold = false;
                    dispatchTutorialSuppressionEvent(false);
                    this.revealPrepared();
                    this.clearViewportWatcher();
                    if (this.override === override) {
                        this.override = null;
                    }
                    if (this.overridePromise === restorePromise) {
                        this.overridePromise = null;
                    }
                }
            });

            this.overridePromise = restorePromise;
            return restorePromise;
        }
    }

    window.TutorialAvatarReloadController = {
        createController: function (options) {
            return new TutorialAvatarReloadController(options);
        }
    };
})();
