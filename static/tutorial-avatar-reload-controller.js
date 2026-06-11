(function () {
    'use strict';

    function noop() {}

    class TutorialAvatarReloadController {
        constructor(options) {
            const normalizedOptions = options || {};
            this.host = normalizedOptions.host || null;
            this.timeoutMs = Number.isFinite(normalizedOptions.timeoutMs) ? normalizedOptions.timeoutMs : 8000;
            this.tutorialModelName = normalizedOptions.tutorialModelName || 'yui-origin';
            this.resolveCurrentName = normalizedOptions.resolveCurrentName || noop;
            this.fetchCharacters = normalizedOptions.fetchCharacters || noop;
            this.buildSnapshotPayload = normalizedOptions.buildSnapshotPayload || noop;
            this.reloadModel = normalizedOptions.reloadModel || noop;
            this.setPreparing = normalizedOptions.setPreparing || noop;
            this.revealPrepared = normalizedOptions.revealPrepared || noop;
            this.captureAvatarPreview = normalizedOptions.captureAvatarPreview || noop;
            this.applyIdentityOverride = normalizedOptions.applyIdentityOverride || noop;
            this.sleep = normalizedOptions.sleep || function () { return Promise.resolve(); };
            this.clearViewportWatcher = normalizedOptions.clearViewportWatcher || noop;
            this.override = null;
            this.overridePromise = null;
        }

        hasActiveOverride() {
            return !!this.override;
        }

        getPendingPromise() {
            return this.overridePromise;
        }

        beginOverride() {
            const host = this.host;
            if (!host) {
                return Promise.reject(new Error('tutorial avatar reload host is required'));
            }

            if (this.overridePromise) {
                if (this.override && (this.override.restoring || this.override.restoreRequested)) {
                    return this.overridePromise.then(() => this.beginOverride());
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

                this.setPreparing(true);
                await this.reloadModel(currentName, tutorialModelPayload, { temporary: true });
                ensureOverrideActive();
                await this.sleep(350);
                ensureOverrideActive();
                const tutorialAvatar = await this.captureAvatarPreview();
                ensureOverrideActive();
                this.applyIdentityOverride({
                    active: true,
                    displayName: 'YUI',
                    avatarDataUrl: tutorialAvatar && tutorialAvatar.dataUrl ? tutorialAvatar.dataUrl : '',
                    modelType: tutorialAvatar && tutorialAvatar.modelType ? tutorialAvatar.modelType : 'live2d'
                });
                console.log('[TutorialAvatarReloadController] 新手教程期间已临时切换到 yui-origin 模型（未写入用户配置）:', tutorialModelPayload);
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
                console.warn('[TutorialAvatarReloadController] 临时切换 yui-origin 模型失败:', error);
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
                    console.log('[TutorialAvatarReloadController] 已恢复新手教程前的用户模型:', override.activePrefix || 'unknown');
                } catch (error) {
                    console.warn('[TutorialAvatarReloadController] 恢复新手教程前用户模型失败:', error);
                    if (typeof window.showCurrentModel === 'function') {
                        try {
                            await window.showCurrentModel();
                        } catch (_) {}
                    }
                } finally {
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
