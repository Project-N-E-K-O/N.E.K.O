(function () {
    'use strict';

    function noop() {}

    const PROACTIVE_STATE_KEYS = Object.freeze([
        'proactiveChatEnabled',
        'proactiveVisionEnabled',
        'proactiveVisionChatEnabled',
        'proactiveNewsChatEnabled',
        'proactiveCommunityChatEnabled',
        'proactiveVideoChatEnabled',
        'proactivePersonalChatEnabled',
        'proactiveMusicEnabled',
        'proactiveMemeEnabled',
        'proactiveMiniGameInviteEnabled'
    ]);

    function readPersistedProactiveSettings() {
        // app-settings 的「接受真值」优先（含跨窗口出处校验结果，竞态中被
        // 拒绝的陈旧持久值不会被当成用户设置）；未加载时回退原始持久层。
        try {
            const appSettings = window.appSettings;
            if (appSettings && typeof appSettings.getProactiveUserTruth === 'function') {
                const truth = appSettings.getProactiveUserTruth();
                if (truth && typeof truth === 'object') return truth;
            }
        } catch (_) { }
        try {
            const storage = window.localStorage || null;
            const raw = storage ? storage.getItem('project_neko_settings') : null;
            if (!raw) return null;
            const settings = JSON.parse(raw);
            if (!settings || typeof settings !== 'object') return null;
            return settings;
        } catch (_) {
            return null;
        }
    }

    function snapshotProactiveState() {
        const appState = window.appState || null;
        const snapshot = {};
        // 持久化设置是用户意图的权威来源：本控制器与 home-tutorial feature
        // controller 是两层独立抑制，内存值可能已被另一层临时置 false，
        // 直接快照内存会导致恢复出全关。教程期间设置面板被锁、落盘有护栏，
        // localStorage 里的值就是教程前的用户真值。
        const persisted = readPersistedProactiveSettings();
        PROACTIVE_STATE_KEYS.forEach((key) => {
            if (persisted && typeof persisted[key] === 'boolean') {
                snapshot[key] = persisted[key];
            } else if (typeof window[key] !== 'undefined') {
                snapshot[key] = !!window[key];
            } else if (appState && typeof appState[key] !== 'undefined') {
                snapshot[key] = !!appState[key];
            } else {
                snapshot[key] = false;
            }
        });
        return snapshot;
    }

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

    function maybeRestartProactiveRuntime(snapshot) {
        if (!snapshot || !snapshot.proactiveChatEnabled) {
            return;
        }
        const hasMode = PROACTIVE_STATE_KEYS.some((key) => key !== 'proactiveChatEnabled' && !!snapshot[key]);
        const scheduler = window.appProactive && typeof window.appProactive.scheduleProactiveChat === 'function'
            ? window.appProactive.scheduleProactiveChat
            : window.scheduleProactiveChat;
        if (hasMode && typeof scheduler === 'function') {
            try {
                scheduler.call(window);
            } catch (error) {
                console.warn('[TutorialAvatarReloadController] 主动搭话调度恢复失败:', error);
            }
        }
    }

    function applyProactiveState(values, options) {
        if (!values || typeof values !== 'object') {
            return;
        }
        const appState = window.appState || null;
        PROACTIVE_STATE_KEYS.forEach((key) => {
            if (!Object.prototype.hasOwnProperty.call(values, key)) {
                return;
            }
            const next = !!values[key];
            window[key] = next;
            if (appState && typeof appState[key] !== 'undefined') {
                appState[key] = next;
            }
        });
        stopProactiveRuntime();
        if (options && options.restart) {
            maybeRestartProactiveRuntime(values);
        }
    }

    function buildDisabledProactiveState() {
        const state = {};
        PROACTIVE_STATE_KEYS.forEach((key) => {
            state[key] = false;
        });
        return state;
    }

    function readCurrentProactiveState() {
        const appState = window.appState || null;
        const current = {};
        PROACTIVE_STATE_KEYS.forEach((key) => {
            if (typeof window[key] !== 'undefined') {
                current[key] = !!window[key];
            } else if (appState && typeof appState[key] !== 'undefined') {
                current[key] = !!appState[key];
            } else {
                current[key] = false;
            }
        });
        return current;
    }

    function dispatchTutorialSuppressionEvent(active) {
        // 与 home 教程 feature controller 共用同一事件面：抑制开始要撤回
        // 展示中的情境弹窗（此期间「接受」会被持久化护栏回滚），解除要重放
        // 暂存的信号（feature controller 的 active:false 先于本层释放发出，
        // 当时被重新入队的提示靠这条补上）；app-proactive 的事件标志同步。
        try {
            // window.CustomEvent 优先：vm 测试沙箱只挂 window 属性，浏览器里
            // 两者等价。
            const EventConstructor = window.CustomEvent || CustomEvent;
            window.dispatchEvent(new EventConstructor('neko:home-tutorial-features-suppressed', {
                detail: { active: !!active, source: 'avatar-reload-override' }
            }));
        } catch (_) { }
    }

    function resolveRestoredProactiveState(snapshot) {
        // 教程期间被兄弟窗口修改、且经本窗口出处校验接受的新值（经
        // applySharedRuntimeSettings 传播：已进接受真值登记表与持久层）是比
        // begin 快照更新的用户意图：恢复前逐键重读 app-settings 的真值链，
        // begin 快照只兜真值链缺失的键，避免用旧快照覆盖教程期间的新改动。
        if (!snapshot || typeof snapshot !== 'object') {
            return snapshot;
        }
        try {
            const appSettings = window.appSettings;
            if (!appSettings || typeof appSettings.getProactiveUserTruth !== 'function') {
                return snapshot;
            }
            const truth = appSettings.getProactiveUserTruth();
            if (!truth || typeof truth !== 'object') {
                return snapshot;
            }
            const resolved = {};
            PROACTIVE_STATE_KEYS.forEach((key) => {
                if (typeof truth[key] === 'boolean') {
                    resolved[key] = truth[key];
                } else if (typeof snapshot[key] === 'boolean') {
                    resolved[key] = snapshot[key];
                }
            });
            return resolved;
        } catch (_) {
            return snapshot;
        }
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

        isProactiveSuppressed() {
            // 仅当 beginOverride 已真正把十个主动搭话键在内存里关闭后才为 true：
            // override 创建到实际关闭之间隔着 resolveCurrentName/fetchCharacters
            // 的异步窗口，那段时间内存值仍是用户真值，app-settings 的持久化
            // 护栏若按 hasActiveOverride 判定就会误 hold（例如把 boot merge 刚
            // 拿到的服务器新值替换回旧的本地值并上行）。restoreOverride 恢复
            // 后立即复位。
            return !!(this.override && this.override.proactiveSuppressed === true);
        }

        getProactiveUserValues() {
            // beginOverride 时的用户真值快照（仅本层确在抑制期间有效）：
            // 供 app-settings 的持久化护栏在持久层缺键时回退。
            if (!this.override || this.override.proactiveSuppressed !== true) {
                return null;
            }
            return this.override.proactiveSnapshot || null;
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
                const featureController = window.NekoHomeTutorialFeatureController;
                const proactiveManagedByTutorialLifecycle = !!(
                    featureController
                    && typeof featureController.isActive === 'function'
                    && featureController.isActive()
                );
                if (!proactiveManagedByTutorialLifecycle) {
                    this.override.proactiveSnapshot = snapshotProactiveState();
                    applyProactiveState(buildDisabledProactiveState());
                    override.proactiveSuppressed = true;
                    dispatchTutorialSuppressionEvent(true);
                }

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
            const proactiveSnapshot = override.proactiveSnapshot;
            override.restoring = true;

            const restorePromise = Promise.resolve().then(async () => {
                // 内存恢复与抑制标志释放前置（不排程）：模型恢复的异步窗口里
                // 设置面板已解锁（teardown 先清了 isInTutorial），用户此时的
                // 显式改动必须正常落盘生效，不能被持久化护栏回滚丢失。
                // 调度器启动留在模型恢复完成之后，保住「不对着临时教程模型
                // 开口」的语义（排程本身是间隔制，窗口内不会有立即触发）。
                const resolvedProactive = resolveRestoredProactiveState(proactiveSnapshot);
                applyProactiveState(resolvedProactive);
                override.proactiveSuppressed = false;
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
                    // 以当前内存为准重启调度：模型恢复窗口内用户的显式改动
                    // 优先于恢复时的快照值。
                    maybeRestartProactiveRuntime(readCurrentProactiveState());
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
