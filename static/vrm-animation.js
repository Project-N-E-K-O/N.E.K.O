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
        // crossfade 时给每个 outgoing action schedule 的 stop 定时器。Set 允许
        // 多个 in-flight fadeOut 同时等待 stop，reset() 时统一清干净，防止
        // 重建 VRMAnimation 实例后残留回调打到旧 action。
        this._outgoingStopTimers = new Set();
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
        // 只清理通用 animationMixer；vrmaMixer 由 _createAndConfigureAction 管理（复用 / 重建）
        if (this.manager.animationMixer) {
            this.manager.animationMixer.stopAllAction();
            if (vrm?.scene) {
                this.manager.animationMixer.uncacheRoot(vrm.scene);
            }
            this.manager.animationMixer = null;
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
        let proxy = vrm.scene.getObjectByName('lookAtQuaternionProxy');
        if (!proxy) {
            const animationModule = await VRMAnimation._getAnimationModule();
            const { VRMLookAtQuaternionProxy } = animationModule;
            proxy = new VRMLookAtQuaternionProxy(vrm.lookAt);
            proxy.name = 'lookAtQuaternionProxy';
            vrm.scene.add(proxy);
        }
        // 用基于前向向量的稳定提取替换 proxy 的 Euler 拆分（shadow 原型方法）。
        //
        // 原实现：setFromQuaternion(q, 'YXZ') → Euler → yaw = y, pitch = x。
        //   pitch ≈ ±π/2 附近 YXZ 拆分退化（Euler 矩阵奇异），yaw 由退化矩阵任意
        //   输出 → 眼球奇点甩动。
        //
        // 新实现：把 proxy.quaternion 作用于本地 -Z（VRM 头部前方），从结果向量
        //   提取 pitch=asin(fy), yaw=atan2(-fx, -fz)。
        //   对无 roll 的 LookAt quaternion（authored gaze 的标准形态）与 Euler YXZ
        //   数学上完全等价——Y 旋转给 yaw，X 旋转给 pitch，Z 旋转(roll)在 YXZ
        //   里被塞进 euler.z 被 proxy 忽略，forward-vector 提取同样忽略 roll
        //   （绕前向轴的旋转不改变前向）。
        //   关键区别：pitch=±π/2 时 asin 域边界稳定返回 π/2，atan2(0,0)=0 给
        //   出明确的 yaw=0 回退，而不是退化 YXZ 的任意值。
        //
        // 不再用 no-op：no-op 会静默丢弃 VRMA 文件里 authored 的 LookAt quaternion
        // 轨道（createVRMAnimationClip 把它们映射到 proxy.quaternion），影响
        // 手动播放的带 gaze 动画的正常表现（Project-N-E-K-O/N.E.K.O#772 Codex P1）。
        if (!Object.prototype.hasOwnProperty.call(proxy, '_applyToLookAt')) {
            const RAD2DEG = 180 / Math.PI;
            proxy._applyToLookAt = function () {
                const q = this.quaternion;
                const x = q.x, y = q.y, z = q.z, w = q.w;
                // forward = q * (0,0,-1) * q⁻¹，展开后的分量
                const fx = -2 * (x * z + w * y);
                const fy =  2 * (w * x - y * z);
                const fz =  2 * (x * x + y * y) - 1;
                // asin 定义域 [-1, 1]，浮点积累可能越界，clamp 防 NaN
                const cy = fy > 1 ? 1 : (fy < -1 ? -1 : fy);
                this.vrmLookAt.pitch = Math.asin(cy) * RAD2DEG;
                this.vrmLookAt.yaw = Math.atan2(-fx, -fz) * RAD2DEG;
            };
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

    /**
     * 把新 clip 每条 QuaternionKeyframeTrack 的关键帧整体翻到当前骨骼姿态的同半球。
     * crossfade 期间 mixer 在"旧 clip 当前采样"和"新 clip 第 0 帧"之间做加权 slerp；
     * 若两者 dot < 0，slerp 会取反向长路径——即使两个旋转在 3D 空间里完全相同，
     * 视觉上就是骨骼绕反向轴甩一大圈再归位（偶发脖子折 90° 甩几圈的根因）。
     * _normalizeQuaternionTrackSigns 只解决单 clip 内部相邻帧的符号一致性，解决不了跨 clip。
     * 这里用 "新 clip 首帧 vs 骨骼当前 quaternion" 的 dot 决定是否整条轨道翻号；
     * 翻号不改变旋转本身（q 与 -q 等价），只改变插值路径。
     * 必须在 _normalizeQuaternionTrackSigns 之后调用——那一步保证 clip 内部自洽，
     * 所以整条翻号不会破坏内部相邻帧的同半球关系。
     */
    _alignClipToCurrentPose(clip) {
        const THREE = window.THREE;
        if (!clip?.tracks || !THREE?.QuaternionKeyframeTrack) return;
        // 优先用正在运行的 vrmaMixer 的 root（_findBestMixerRoot 通常返回
        // normalizedRoot——VRM 标准化骨架树），确保查到的 bone.quaternion
        // 反映当前动画姿态。
        // 但 `lookAtQuaternionProxy` 是挂在 vrm.scene 直下的 sibling，不在
        // normalizedRoot 树里——此时回退到 vrm.scene.getObjectByName 才能
        // 拿到 proxy.quaternion 做同半球对齐，否则 authored LookAt 轨道
        // 在 crossfade 时仍可能走长路径（CodeRabbit on PR #772）。
        const root = this.vrmaMixer?.getRoot?.() || this.manager?.currentModel?.vrm?.scene;
        if (!root || typeof root.getObjectByName !== 'function') return;
        const scene = this.manager?.currentModel?.vrm?.scene;
        const stride = 4;
        for (const track of clip.tracks) {
            if (!(track instanceof THREE.QuaternionKeyframeTrack)) continue;
            const boneName = track.name.split('.')[0];
            let bone = root.getObjectByName(boneName);
            if (!bone && scene && scene !== root) {
                bone = scene.getObjectByName(boneName);
            }
            const bq = bone?.quaternion;
            if (!bq) continue;
            const v = track.values;
            if (!v || v.length < stride) continue;
            const dot = bq.x * v[0] + bq.y * v[1] + bq.z * v[2] + bq.w * v[3];
            if (dot < 0) {
                for (let i = 0; i < v.length; i++) {
                    v[i] = -v[i];
                }
            }
        }
    }

    /**
     * 把 clip 里每条 QuaternionKeyframeTrack 的相邻关键帧翻到同一半球（dot >= 0）。
     * slerpFlat 成对处理能自洽，但链式经过 3+ 关键帧或配合 LookAt 的 Euler 拆分时，
     * 偶尔会被反向四元数骗出长路径 / 奇点甩动（表现为脖子折 90 度甩几圈后自愈）。
     * 同一旋转的 q 和 -q 在表现上等价，翻符号是无副作用的防御。
     */
    _normalizeQuaternionTrackSigns(clip) {
        const THREE = window.THREE;
        if (!clip?.tracks || !THREE?.QuaternionKeyframeTrack) return;
        const stride = 4;
        for (const track of clip.tracks) {
            if (!(track instanceof THREE.QuaternionKeyframeTrack)) continue;
            const v = track.values;
            if (!v || v.length < stride * 2) continue;
            for (let i = stride; i < v.length; i += stride) {
                const dot =
                    v[i - 4] * v[i] +
                    v[i - 3] * v[i + 1] +
                    v[i - 2] * v[i + 2] +
                    v[i - 1] * v[i + 3];
                if (dot < 0) {
                    v[i]     = -v[i];
                    v[i + 1] = -v[i + 1];
                    v[i + 2] = -v[i + 2];
                    v[i + 3] = -v[i + 3];
                }
            }
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
        const existingRoot = this.vrmaMixer ? this.vrmaMixer.getRoot() : null;

        if (this.vrmaMixer && existingRoot === mixerRoot) {
            // mixer root 相同 → 复用 mixer，保留 currentAction 供 _playAction crossfade
            // 只取消旧 clip 的缓存，避免内存泄漏
            this.vrmaMixer.uncacheClip(clip);
        } else {
            // mixer root 变了或首次创建 → 必须重建 mixer
            if (this.vrmaMixer) {
                this.vrmaMixer.stopAllAction();
                if (existingRoot) {
                    this.vrmaMixer.uncacheRoot(existingRoot);
                }
            }
            this.currentAction = null;
            this.vrmaIsPlaying = false;
            this.vrmaMixer = new window.THREE.AnimationMixer(mixerRoot);
        }
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
                // fadeOut 只把 weight 归零，action 本身仍在 mixer 的 _actions 列表里
                // 以 weight=0 的状态持续 update。如果不 stop，后续每次 _playAction 都
                // 会让残留 action 越堆越多（idle ↔ manual VRMA 反复切换尤其明显）。
                // 这里按本次 fadeDuration schedule 一个与 action 绑定的 stop，
                // 跟调用方（idle/manual）解耦——任何 VRMA 播放路径都能正确收尾
                // （Project-N-E-K-O/N.E.K.O#772 Codex P2）。
                const outgoing = this.currentAction;
                outgoing.fadeOut(fadeDuration);
                const stopDelayMs = Math.ceil(fadeDuration * 1000) + 50;
                // 定时器登记到 _outgoingStopTimers，reset() 统一清——防止
                // 实例 dispose 后回调仍打到已释放的 action（CodeRabbit on PR #772）。
                let timerId;
                timerId = setTimeout(() => {
                    this._outgoingStopTimers.delete(timerId);
                    if (this._disposed) return;
                    try {
                        // 只有当 outgoing 已经不是当前 action 时才 stop，避免把同一
                        // action 反复切入/切出时误杀正在 fadeIn 的自己。
                        if (this.currentAction !== outgoing) {
                            outgoing.stop();
                        }
                    } catch (e) {
                        // action 可能已被 mixer 清理；stop 幂等，忽略异常
                    }
                }, stopDelayMs);
                this._outgoingStopTimers.add(timerId);
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
            // 清除上一次 stopVRMAAnimation 的 fadeOut 定时器，防止它在新动画播放后误杀 action
            if (this._fadeTimer) {
                clearTimeout(this._fadeTimer);
                this._fadeTimer = null;
            }

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
            this._normalizeQuaternionTrackSigns(clip);
            // 跨 clip 同半球对齐：必须在 _normalizeQuaternionTrackSigns 之后、
            // _createAndConfigureAction 之前。此刻 vrmaMixer 上仍是上一条 action 在跑，
            // 骨骼 quaternion 反映当前姿态；后续 _playAction 的 crossfade slerp 才能走最短路径。
            this._alignClipToCurrentPose(clip);

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
            // paused action 上 fadeOut 无效（mixer 不 update paused action 的权重），
            // 直接立即清理，避免 500ms 后骨骼硬跳到 rest pose
            if (this.currentAction.paused) {
                if (this.vrmaMixer) {
                    this.vrmaMixer.stopAllAction();
                }
                this.currentAction = null;
                this.vrmaIsPlaying = false;
                this.isIdleAnimation = false;
                this._restorePhysics();
            } else {
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
            }
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
        if (this._outgoingStopTimers && this._outgoingStopTimers.size > 0) {
            for (const id of this._outgoingStopTimers) clearTimeout(id);
            this._outgoingStopTimers.clear();
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
