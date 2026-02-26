/**
    * VRM Animation - VRM 模型动画播放功能
    *   功能:   
    *  - 播放 VRM 模型动画
    *  - 切换动画
    *  - 设置动画播放速度
    *  - 处理动画事件（如循环播放、淡入淡出）
    *  - 管理模型骨骼动画（如 SpringBone 恢复）
    *  - 同步口型动画（如 LipSync）
    *  - 处理模型碰撞（如防止模型穿透）
    */
   
// 确保 THREE 可用（使用 var 避免重复声明错误）
var THREE = (typeof window !== 'undefined' && window.THREE) || (typeof globalThis !== 'undefined' && globalThis.THREE) || null;

if (!THREE) {
    console.error('[VRM Animation] THREE.js 未加载，动画功能将不可用');
}
class VRMAnimation {
    static MAX_DELTA_THRESHOLD = 0.1;
    static DEFAULT_FRAME_DELTA = 0.016;
    static _animationModuleCache = null;
    static _normalizedRootWarningShown = false;

    constructor(manager) {
        this.manager = manager;
        this._disposed = false;
        this.vrmaMixer = null;
        this.currentAction = null;
        this.vrmaIsPlaying = false;
        this._loaderPromise = null;
        this._fadeTimer = null;
        this._springBoneRestoreTimer = null;
        this.playbackSpeed = 1.0;
        this.skeletonHelper = null;
        this.debug = false;
        this.isIdleAnimation = false;  // 当前播放的是否为待机动画
        this.lipSyncActive = false;
        this.analyser = null;
        this.mouthExpressions = { 'aa': null, 'ih': null, 'ou': null, 'ee': null, 'oh': null };
        this.currentMouthWeight = 0;
        this.frequencyData = null;
        this._boundsUpdateFrameCounter = 0;
        this._boundsUpdateInterval = 5;
        this._skinnedMeshes = [];
        this._cachedSceneUuid = null; // 跟踪缓存的 scene UUID，防止跨模型僵尸引用
    }

    /**
     * 检查回退文件是否存在（启动时自检）
     * @returns {Promise<boolean>} 文件是否存在
     */
    static async _checkFallbackFileExists() {
        const fallbackPath = '/static/libs/three-vrm-animation.module.js';
        try {
            const response = await fetch(fallbackPath, { method: 'HEAD' });
            return response.ok;
        } catch (e) {
            return false;
        }
    }

    /**
     * 获取 three-vrm-animation 模块（带缓存）
     * 使用 importmap 中的映射，确保与 @pixiv/three-vrm 使用相同的 three-vrm-core 版本
     * @returns {Promise<object>} three-vrm-animation 模块对象
     */
    static async _getAnimationModule() {
        if (VRMAnimation._animationModuleCache) {
            return VRMAnimation._animationModuleCache;
        }
        let primaryError = null;
        try {
            // 使用 importmap 中的映射，确保与 @pixiv/three-vrm 使用相同的 three-vrm-core 版本
            VRMAnimation._animationModuleCache = await import('@pixiv/three-vrm-animation');
            return VRMAnimation._animationModuleCache;
        } catch (error) {
            primaryError = error;
            console.warn('[VRM Animation] 无法导入 @pixiv/three-vrm-animation，请检查 importmap 配置:', error);
            // 如果 importmap 失败，回退到硬编码路径（兼容性处理）；在尝试导入前检查回退文件是否存在
            try {
                const fallbackExists = await VRMAnimation._checkFallbackFileExists();
                if (!fallbackExists) {
                    console.warn('[VRM Animation] 回退文件不存在: /static/libs/three-vrm-animation.module.js，请确保文件已正确部署');
                }
                VRMAnimation._animationModuleCache = await import('/static/libs/three-vrm-animation.module.js');
                return VRMAnimation._animationModuleCache;
            } catch (fallbackError) {
                // fallback 也失败，抛出包含两次错误的详细错误信息
                const combinedError = new Error(
                    `[VRM Animation] 无法导入动画模块：\n` +
                    `  主路径失败 (@pixiv/three-vrm-animation): ${primaryError?.message || primaryError}\n` +
                    `  回退路径失败 (/static/libs/three-vrm-animation.module.js): ${fallbackError?.message || fallbackError}\n` +
                    `请检查 importmap 配置或确保回退文件存在且路径正确。`
                );
                console.error(combinedError.message, { primaryError, fallbackError });
                VRMAnimation._animationModuleCache = null; // 清除缓存，允许重试
                throw combinedError;
            }
        }
    }

    _detectVRMVersion(vrm) {
        try {
            if (vrm.meta) {
                if (vrm.meta.metaVersion !== undefined && vrm.meta.metaVersion !== null) {
                    const version = String(vrm.meta.metaVersion);
                    if (version === '1' || version === '1.0' || version.startsWith('1.')) {
                        return '1.0';
                    }
                    if (version === '0' || version === '0.0' || version.startsWith('0.')) {
                        return '0.0';
                    }
                }
                if (vrm.meta.vrmVersion) {
                    const version = String(vrm.meta.vrmVersion);
                    if (version.startsWith('1') || version.includes('1.0')) {
                        return '1.0';
                    }
                }
            }
            return '0.0';
        } catch (error) {
            return '0.0';
        }
    }

    update(delta) {
        const safeDelta = (delta <= 0 || delta > VRMAnimation.MAX_DELTA_THRESHOLD)
            ? VRMAnimation.DEFAULT_FRAME_DELTA
            : delta;
        const updateDelta = safeDelta * this.playbackSpeed;

        if (this.vrmaIsPlaying && this.vrmaMixer) {
            this.vrmaMixer.update(updateDelta);

            const vrm = this.manager.currentModel?.vrm;
            if (vrm?.scene) {
                // 检查 scene 是否变化，如果变化则重建缓存（防止僵尸引用）
                if (this._cachedSceneUuid !== vrm.scene.uuid) {
                    this._cacheSkinnedMeshes(vrm);
                }

                if (vrm.humanoid) {
                    const vrmVersion = this._detectVRMVersion(vrm);
                    if (vrmVersion === '1.0' && vrm.humanoid.autoUpdateHumanBones) {
                        vrm.humanoid.update();
                    } else if (vrmVersion === '0.0') {
                        const mixerRoot = this.vrmaMixer.getRoot();
                        const normalizedRoot = vrm.humanoid?._normalizedHumanBones?.root;
                        if (normalizedRoot && mixerRoot === normalizedRoot) {
                            if (vrm.humanoid.autoUpdateHumanBones !== undefined) {
                                vrm.humanoid.update();
                            }
                        }
                    }
                }
                vrm.scene.updateMatrixWorld(true);
                this._skinnedMeshes.forEach(mesh => {
                    if (mesh.skeleton) {
                        mesh.skeleton.update();
                    }
                });
            }
        }
        if (this.lipSyncActive && this.analyser) {
            this._updateLipSync(updateDelta);
        }

        if (this.manager?.interaction && typeof this.manager.interaction.updateModelBoundsCache === 'function') {
            this._boundsUpdateFrameCounter++;
            if (this._boundsUpdateFrameCounter >= this._boundsUpdateInterval) {
                this._boundsUpdateFrameCounter = 0;
                this.manager.interaction.updateModelBoundsCache();
            }
        }
    }

    async _initLoader() {
        if (this._loaderPromise) return this._loaderPromise;

        this._loaderPromise = (async () => {
            try {
                const { GLTFLoader } = await import('three/addons/loaders/GLTFLoader.js');
                const animationModule = await VRMAnimation._getAnimationModule();
                const { VRMAnimationLoaderPlugin } = animationModule;
                const loader = new GLTFLoader();
                loader.register((parser) => new VRMAnimationLoaderPlugin(parser));
                return loader;
            } catch (error) {
                console.error('[VRM Animation] 加载器初始化失败:', error);
                this._loaderPromise = null;
                throw error;
            }
        })();
        return await this._loaderPromise;
    }

    _cleanupOldMixer(vrm) {
        if (this.manager.animationMixer) {
            this.manager.animationMixer.stopAllAction();
            // 添加空值保护，避免传入 null/undefined 导致 Three.js bug
            if (vrm?.scene) {
                this.manager.animationMixer.uncacheRoot(vrm.scene);
            }
            this.manager.animationMixer = null;
        }

        if (this.vrmaMixer) {
            const oldRoot = this.vrmaMixer.getRoot();
            // 总是清理旧的 VRMA mixer，无论 oldRoot 是否等于当前的 vrm.scene 或 normalized root
            this.vrmaMixer.stopAllAction();
            // 添加空值保护，避免传入 null/undefined 导致 Three.js bug
            if (oldRoot) {
                this.vrmaMixer.uncacheRoot(oldRoot);
            }
            this.vrmaMixer = null;
            this.currentAction = null;
            this.vrmaIsPlaying = false;
        }
    }

    _ensureNormalizedRootInScene(vrm, vrmVersion) {
        const normalizedRoot = vrm.humanoid?._normalizedHumanBones?.root;
        if (!normalizedRoot) return;

        if (vrmVersion === '1.0') {
            if (!vrm.scene.getObjectByName(normalizedRoot.name)) {
                vrm.scene.add(normalizedRoot);
            }
            if (vrm.humanoid.autoUpdateHumanBones !== true) {
                vrm.humanoid.autoUpdateHumanBones = true;
            }
        } else {
            if (!vrm.scene.getObjectByName(normalizedRoot.name)) {
                vrm.scene.add(normalizedRoot);
            }
        }
    }

    async _createLookAtProxy(vrm) {
        if (!vrm.lookAt) return;
        const existingProxy = vrm.scene.getObjectByName('lookAtQuaternionProxy');
        if (!existingProxy) {
            const animationModule = await VRMAnimation._getAnimationModule();
            const { VRMLookAtQuaternionProxy } = animationModule;
            const lookAtQuatProxy = new VRMLookAtQuaternionProxy(vrm.lookAt);
            lookAtQuatProxy.name = 'lookAtQuaternionProxy';
            vrm.scene.add(lookAtQuatProxy);
        }
    }

    async _createAndValidateAnimationClip(vrmAnimation, vrm) {
        const animationModule = await VRMAnimation._getAnimationModule();
        const { createVRMAnimationClip } = animationModule;

        let clip;
        try {
            clip = createVRMAnimationClip(vrmAnimation, vrm);
        } catch (clipError) {
            console.error('[VRM Animation] createVRMAnimationClip 抛出异常:', clipError);
            const errorMsg = window.t ? window.t('vrm.error.animationClipError', { error: clipError.message }) : `创建动画 Clip 时出错: ${clipError.message}`;
            throw new Error(errorMsg);
        }

        if (!clip || !clip.tracks || clip.tracks.length === 0) {
            console.error('[VRM Animation] 创建的动画 Clip 没有有效的轨道');
            console.error('[VRM Animation] Clip 信息:', {
                name: clip?.name,
                duration: clip?.duration,
                tracksCount: clip?.tracks?.length,
                tracks: clip?.tracks?.map(t => t.name)
            });
            const errorMsg = window.t ? window.t('vrm.error.animationClipNoBones') : '动画 Clip 创建失败：没有找到匹配的骨骼';
            throw new Error(errorMsg);
        }

        return clip;
    }

    _processTracksForVersion(clip, vrmVersion) {
        if (vrmVersion === '1.0') {
            return;
        } else {
            clip.tracks.forEach(track => {
                if (track.name.startsWith('Normalized_')) {
                    const originalName = track.name.substring('Normalized_'.length);
                    track.name = originalName;
                }
            });
        }
    }

    _findBestMixerRoot(vrm, clip) {
        let mixerRoot = vrm.scene;
        const sampleTracks = clip.tracks.slice(0, 10);
        let foundCount = 0;
        sampleTracks.forEach(track => {
            const boneName = track.name.split('.')[0];
            const bone = mixerRoot.getObjectByName(boneName);
            if (bone) foundCount++;
        });

        let bestRoot = mixerRoot;
        let bestMatchCount = foundCount;

        const sceneMatchCount = sampleTracks.filter(track => {
            const boneName = track.name.split('.')[0];
            return !!vrm.scene.getObjectByName(boneName);
        }).length;
        if (sceneMatchCount > bestMatchCount) {
            bestRoot = vrm.scene;
            bestMatchCount = sceneMatchCount;
        }

        const normalizedRoot = vrm.humanoid?._normalizedHumanBones?.root;
        if (normalizedRoot) {
            if (!vrm.scene.getObjectByName(normalizedRoot.name)) {
                vrm.scene.add(normalizedRoot);
            }
            const normalizedMatchCount = sampleTracks.filter(track => {
                const boneName = track.name.split('.')[0];
                return !!normalizedRoot.getObjectByName(boneName);
            }).length;
            if (normalizedMatchCount > bestMatchCount) {
                bestRoot = normalizedRoot;
                bestMatchCount = normalizedMatchCount;
            }
        } else {
            if (!VRMAnimation._normalizedRootWarningShown) {
                console.warn('[VRM Animation] _normalizedHumanBones.root 不可用，使用 vrm.scene 作为动画根节点。如果动画播放异常，可能是 three-vrm 版本升级导致的。');
                VRMAnimation._normalizedRootWarningShown = true;
            }
        }

        if (bestRoot !== mixerRoot) {
            mixerRoot = bestRoot;
        }
        return mixerRoot;
    }

    _createAndConfigureAction(clip, mixerRoot, options) {
        if (this.vrmaMixer) {
            this.vrmaMixer.stopAllAction();
            // 添加空值保护，避免传入 null/undefined 导致 Three.js bug
            const root = this.vrmaMixer.getRoot();
            if (root) {
                this.vrmaMixer.uncacheRoot(root);
            }
            this.vrmaMixer = null;
            this.currentAction = null;
            this.vrmaIsPlaying = false;
        }

        this.vrmaMixer = new window.THREE.AnimationMixer(mixerRoot);
        const newAction = this.vrmaMixer.clipAction(clip);
        if (!newAction) {
            const root = this.vrmaMixer.getRoot();
            if (root) {
                this.vrmaMixer.uncacheRoot(root);
            }
            this.vrmaMixer = null;
            const errorMsg = window.t ? window.t('vrm.error.cannotCreateAnimationAction') : '无法创建动画动作';
            throw new Error(errorMsg);
        }

        newAction.enabled = true;
        newAction.setLoop(options.loop ? window.THREE.LoopRepeat : window.THREE.LoopOnce);
        newAction.clampWhenFinished = true;
        this.playbackSpeed = (options.timeScale !== undefined) ? options.timeScale : 1.0;
        newAction.timeScale = 1.0;

        return newAction;
    }

    _playAction(newAction, options, vrm) {
        if (!this.vrmaMixer) {
            console.error('[VRM Animation] _playAction: vrmaMixer 未初始化');
            return;
        }

        const fadeDuration = options.fadeDuration !== undefined ? options.fadeDuration : 0.4;
        const isImmediate = options.immediate === true;

        if (isImmediate) {
            if (this.currentAction) this.currentAction.stop();
            newAction.reset();
            newAction.enabled = true;
            newAction.play();
            this.vrmaMixer.update(0);
            if (vrm.scene) {
                vrm.scene.updateMatrixWorld(true);
            }
        } else {
            if (this.currentAction && this.currentAction !== newAction) {
                this.vrmaMixer.update(0);
                if (vrm.scene) vrm.scene.updateMatrixWorld(true);
                this.currentAction.fadeOut(fadeDuration);
                newAction.enabled = true;
                if (options.noReset) {
                    newAction.fadeIn(fadeDuration).play();
                } else {
                    newAction.reset().fadeIn(fadeDuration).play();
                }
            } else {
                newAction.enabled = true;
                newAction.reset().fadeIn(fadeDuration).play();
            }
        }

        this.currentAction = newAction;
        this.vrmaIsPlaying = true;

        if (newAction.paused) {
            newAction.play();
        }

        this.vrmaMixer.update(0.001);

        if (vrm.scene) {
            // 检查 scene 是否变化，如果变化则重建缓存（防止僵尸引用）
            if (this._cachedSceneUuid !== vrm.scene.uuid) {
                this._cacheSkinnedMeshes(vrm);
            }

            vrm.scene.updateMatrixWorld(true);
            this._skinnedMeshes.forEach(mesh => {
                if (mesh.skeleton) {
                    mesh.skeleton.update();
                }
            });
        }

        if (this.debug) this._updateSkeletonHelper();
    }

    /**
     * 缓存场景中的 SkinnedMesh 引用，避免每帧遍历场景
     * @param {Object} vrm - VRM 模型实例
     */
    _cacheSkinnedMeshes(vrm) {
        this._skinnedMeshes = [];
        if (vrm?.scene) {
            // 更新缓存的 scene UUID，用于检测 scene 变化
            this._cachedSceneUuid = vrm.scene.uuid;
            vrm.scene.traverse((object) => {
                if (object.isSkinnedMesh && object.skeleton) {
                    this._skinnedMeshes.push(object);
                }
            });
        } else {
            this._cachedSceneUuid = null;
        }
    }

    async playVRMAAnimation(vrmaPath, options = {}) {
        const vrm = this.manager.currentModel?.vrm;
        if (!vrm) {
            const error = new Error('没有加载的 VRM 模型');
            console.error('[VRM Animation]', error.message);
            throw error;
        }

        // 检查是否需要重建缓存：缓存为空、scene 不存在、或 scene UUID 变化（防止僵尸引用）
        if (this._skinnedMeshes.length === 0 || !vrm.scene || this._cachedSceneUuid !== vrm.scene.uuid) {
            this._cacheSkinnedMeshes(vrm);
        }

        try {
            // 设置 autoUpdateHumanBones = false，让 vrm.update() 只更新 SpringBone 物理
            // 不覆盖动画设置的 humanoid 骨骼位置
            // 这样头发等物理效果可以在动画播放期间正常工作
            const vrm = this.manager.currentModel?.vrm;
            if (vrm?.humanoid) {
                vrm.humanoid.autoUpdateHumanBones = false;
            }

            this._cleanupOldMixer(vrm);
            const loader = await this._initLoader();
            const gltf = await loader.loadAsync(vrmaPath);
            const vrmAnimations = gltf.userData?.vrmAnimations;
            if (!vrmAnimations || vrmAnimations.length === 0) {
                const error = new Error('动画文件加载成功，但没有找到 VRM 动画数据');
                console.error('[VRM Animation]', error.message);
                throw error;
            }

            const vrmAnimation = vrmAnimations[0];
            const vrmVersion = this._detectVRMVersion(vrm);
            this._ensureNormalizedRootInScene(vrm, vrmVersion);
            await this._createLookAtProxy(vrm);
            const clip = await this._createAndValidateAnimationClip(vrmAnimation, vrm);
            this._processTracksForVersion(clip, vrmVersion);

            // 判断是否为待机动画（仅在显式传入 isIdle: true 时才视为待机）
            this.isIdleAnimation = !!options.isIdle;

            const mixerRoot = this._findBestMixerRoot(vrm, clip);
            const newAction = this._createAndConfigureAction(clip, mixerRoot, options);
            this._playAction(newAction, options, vrm);

        } catch (error) {
            console.error('[VRM Animation] 播放失败:', error);
            this.vrmaIsPlaying = false;
            throw error;
        }
    }

    stopVRMAAnimation() {
        if (this._fadeTimer) {
            clearTimeout(this._fadeTimer);
            this._fadeTimer = null;
        }
        if (this._springBoneRestoreTimer) {
            clearTimeout(this._springBoneRestoreTimer);
            this._springBoneRestoreTimer = null;
        }

        if (this.currentAction) {
            // 捕获要停止的 action，防止竞态条件（新 action 可能在定时器回调执行前启动）
            const actionAtStop = this.currentAction;
            this.currentAction.fadeOut(0.5);

            this._fadeTimer = setTimeout(() => {
                if (this._disposed) return;
                // 只有当 currentAction 仍然是 actionAtStop 时才执行清理（防止取消新启动的 action）
                if (this.currentAction === actionAtStop) {
                    if (this.vrmaMixer) {
                        this.vrmaMixer.stopAllAction();
                    }
                    this.currentAction = null;
                    this.vrmaIsPlaying = false;
                    this.isIdleAnimation = false;
                    this._fadeTimer = null;

                    // 动画停止后恢复物理
                    this._springBoneRestoreTimer = setTimeout(() => {
                        if (this.currentAction === null) {
                            this._restorePhysics();
                        }
                        this._springBoneRestoreTimer = null;
                    }, 100);
                } else {
                    this._fadeTimer = null;
                }
            }, 500);
        } else {
            if (this.vrmaMixer) {
                this.vrmaMixer.stopAllAction();
            }
            this.vrmaIsPlaying = false;
            this.isIdleAnimation = false;
            this._restorePhysics();
        }
    }

    /**
     * 恢复物理系统并正确初始化 SpringBone
     * 在动画停止后调用
     */
    _restorePhysics() {
        if (!this.manager) return;

        const vrm = this.manager.currentModel?.vrm;

        // 恢复 autoUpdateHumanBones = true，让 vrm.update() 恢复正常的 humanoid 更新
        if (vrm?.humanoid) {
            vrm.humanoid.autoUpdateHumanBones = true;
        }

        // 方案3：不调用 reset() 和 setInitState()
        // 让 SpringBone 保持当前状态继续运行物理
    }

    toggleDebug() {
        this.debug = !this.debug;
        if (this.debug) {
            this._updateSkeletonHelper();
        } else {
            if (this.skeletonHelper) {
                this.manager.scene.remove(this.skeletonHelper);
                this.skeletonHelper = null;
            }
        }
    }

    _updateSkeletonHelper() {
        const vrm = this.manager.currentModel?.vrm;
        if (!vrm || !this.manager.scene) return;

        if (this.skeletonHelper) this.manager.scene.remove(this.skeletonHelper);

        this.skeletonHelper = new window.THREE.SkeletonHelper(vrm.scene);
        this.skeletonHelper.visible = true;
        this.manager.scene.add(this.skeletonHelper);
    }

    startLipSync(analyser) {
        this.analyser = analyser;
        this.lipSyncActive = true;
        this.updateMouthExpressionMapping();
        if (this.analyser) {
            this.frequencyData = new Uint8Array(this.analyser.frequencyBinCount);
        } else {
            console.debug('[VRM LipSync] analyser为空，口型同步将不可用');
        }
    }
    stopLipSync() {
        this.lipSyncActive = false;
        this.resetMouthExpressions();
        this.analyser = null;
        this.currentMouthWeight = 0;
    }
    updateMouthExpressionMapping() {
        const vrm = this.manager.currentModel?.vrm;
        if (!vrm?.expressionManager) return;

        let expressionNames = [];
        const exprs = vrm.expressionManager.expressions;
        if (exprs instanceof Map) {
            expressionNames = Array.from(exprs.keys());
        } else if (Array.isArray(exprs)) {
            expressionNames = exprs.map(e => e.expressionName || e.name || e.presetName).filter(n => n);
        } else if (typeof exprs === 'object') {
            expressionNames = Object.keys(exprs);
        }

        ['aa', 'ih', 'ou', 'ee', 'oh'].forEach(vowel => {
            const match = expressionNames.find(name => name.toLowerCase() === vowel || name.toLowerCase().includes(vowel));
            if (match) this.mouthExpressions[vowel] = match;
        });

    }
    resetMouthExpressions() {
        const vrm = this.manager.currentModel?.vrm;
        if (!vrm?.expressionManager) return;

        Object.values(this.mouthExpressions).forEach(name => {
            if (name) {
                try {
                    vrm.expressionManager.setValue(name, 0);
                } catch (e) {
                    console.warn(`[VRM LipSync] 重置表情失败: ${name}`, e);
                }
            }
        });

    }
    _updateLipSync(delta) {
        if (!this.manager.currentModel?.vrm?.expressionManager) return;
        if (!this.analyser) return;

        if (!this.frequencyData || this.frequencyData.length !== this.analyser.frequencyBinCount) {
            this.frequencyData = new Uint8Array(this.analyser.frequencyBinCount);
        }
        this.analyser.getByteFrequencyData(this.frequencyData);

        let lowFreqEnergy = 0;
        let midFreqEnergy = 0;
        const lowEnd = Math.floor(this.frequencyData.length * 0.1);
        const midEnd = Math.floor(this.frequencyData.length * 0.3);

        for (let i = 0; i < lowEnd; i++) lowFreqEnergy += this.frequencyData[i];
        for (let i = lowEnd; i < midEnd; i++) midFreqEnergy += this.frequencyData[i];

        lowFreqEnergy /= (lowEnd || 1);
        midFreqEnergy /= ((midEnd - lowEnd) || 1);

        const volume = Math.max(lowFreqEnergy, midFreqEnergy * 0.5);
        const targetWeight = Math.min(1.0, volume / 128.0);

        this.currentMouthWeight += (targetWeight - this.currentMouthWeight) * (12.0 * delta);
        const finalWeight = Math.max(0, this.currentMouthWeight);
        const mouthOpenName = this.mouthExpressions.aa || 'aa';

        try {
            this.manager.currentModel.vrm.expressionManager.setValue(mouthOpenName, finalWeight);
        } catch (e) {
            console.warn(`[VRM LipSync] 设置表情失败: ${mouthOpenName}`, e);
        }
    }

    reset() {
        if (this._fadeTimer) {
            clearTimeout(this._fadeTimer);
            this._fadeTimer = null;
        }
        if (this._springBoneRestoreTimer) {
            clearTimeout(this._springBoneRestoreTimer);
            this._springBoneRestoreTimer = null;
        }

        this._skinnedMeshes = [];
        this._cachedSceneUuid = null;

        if (this.vrmaMixer) {
            this.vrmaMixer.stopAllAction();
            const root = this.vrmaMixer.getRoot();
            if (root) {
                this.vrmaMixer.uncacheRoot(root);
            }
            this.vrmaMixer = null;
        }

        this.currentAction = null;
        this.vrmaIsPlaying = false;
        this.isIdleAnimation = false;
    }

    dispose() {
        this._disposed = true;
        this.reset();
        this.stopLipSync();
        if (this.skeletonHelper) {
            this.manager.scene.remove(this.skeletonHelper);
            this.skeletonHelper = null;
        }
    }
}

window.VRMAnimation = VRMAnimation;
