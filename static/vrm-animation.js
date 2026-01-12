/**
 * VRM 动画模块 - 使用官方 @pixiv/three-vrm-animation 库
 * 1. 使用官方库自动处理骨骼重定向和四元数问题
 * 2. 保留自定义功能：口型同步、调试模式、播放速度控制等
 */
class VRMAnimation {
    constructor(manager) {
        this.manager = manager;
        this.vrmaMixer = null;
        this.currentAction = null;
        this.vrmaIsPlaying = false;
        this._loaderPromise = null;
        this._springBoneTimer = null;

        // 播放速度
        this.playbackSpeed = 1.0; 
        
        // 调试辅助
        this.skeletonHelper = null;
        this.debug = false; // 默认关闭，可调用 toggleDebug() 开启

        // 口型同步
        this.lipSyncActive = false;
        this.analyser = null;
        this.mouthExpressions = { 'aa': null, 'ih': null, 'ou': null, 'ee': null, 'oh': null };
        this.currentMouthWeight = 0;
        this.frequencyData = null;
    }

    /**
     * 检测 VRM 版本
     */
    _detectVRMVersion(vrm) {
        try {
            if (vrm.meta) {
                // 优先使用 metaVersion（更准确）
                if (vrm.meta.metaVersion !== undefined && vrm.meta.metaVersion !== null) {
                    const version = String(vrm.meta.metaVersion);
                    // metaVersion 可能是 "0" 或 "1"（字符串）
                    if (version === '1' || version === '1.0' || version.startsWith('1.')) {
                        return '1.0';
                    }
                    if (version === '0' || version === '0.0' || version.startsWith('0.')) {
                        return '0.0';
                    }
                }
                // 兼容 vrmVersion
                if (vrm.meta.vrmVersion) {
                    const version = String(vrm.meta.vrmVersion);
                    if (version.startsWith('1') || version.includes('1.0')) {
                        return '1.0';
                    }
                }
            }
            // 默认返回 0.0（向后兼容）
            return '0.0';
        } catch (error) {
            console.warn('[VRM Animation] 版本检测失败，默认使用 0.0:', error);
            return '0.0';
        }
    }

    update(delta) {
        if (this.vrmaIsPlaying && this.vrmaMixer) {
            // 强制接管时间增量，确保速度控制绝对准确
            const safeDelta = (delta <= 0 || delta > 0.1) ? 0.016 : delta;
            const updateDelta = safeDelta * this.playbackSpeed;
            
            // 更新 Mixer（这会更新所有 Action 的时间）
            this.vrmaMixer.update(updateDelta);

            // 必须在动画更新后立即更新矩阵，确保骨骼状态同步
            const vrm = this.manager.currentModel?.vrm;
            if (vrm?.scene) {
                // 对于 VRM 1.0，需要调用 humanoid.update() 将 normalized 骨骼同步到 raw 骨骼
                // 对于 VRM 0.x，如果使用 normalized root，也需要更新
                if (vrm.humanoid) {
                    const vrmVersion = this._detectVRMVersion(vrm);
                    if (vrmVersion === '1.0' && vrm.humanoid.autoUpdateHumanBones) {
                        vrm.humanoid.update();
                    } else if (vrmVersion === '0.0') {
                        // VRM 0.x：检查 Mixer root 是否是 normalized
                        const mixerRoot = this.vrmaMixer.getRoot();
                        const normalizedRoot = vrm.humanoid?._normalizedHumanBones?.root;
                        if (normalizedRoot && mixerRoot === normalizedRoot) {
                            // 如果使用 normalized root，需要同步到 raw bones
                            if (vrm.humanoid.autoUpdateHumanBones !== undefined) {
                                vrm.humanoid.update();
                            }
                        }
                    }
                }
                
                // 更新所有骨骼的世界矩阵（AnimationMixer 已经更新了本地变换）
                vrm.scene.updateMatrixWorld(true);
                
                // 确保所有 SkinnedMesh 的骨骼矩阵已更新
                vrm.scene.traverse((object) => {
                    if (object.isSkinnedMesh && object.skeleton) {
                        // 更新 Skeleton 的 boneMatrices，确保 SkinnedMesh 使用最新的骨骼变换
                        object.skeleton.update();
                    }
                });
            }
        }
        if (this.lipSyncActive && this.analyser) {
            this._updateLipSync(delta);
        }
    }

    /**
     * 初始化加载器（使用官方 VRMAnimationLoaderPlugin）
     */
    async _initLoader() {
        if (this._loaderPromise) return this._loaderPromise;
        
        this._loaderPromise = (async () => {
            
            try {
                // 动态导入必要的模块
                const { GLTFLoader } = await import('three/addons/loaders/GLTFLoader.js');
                // 直接使用完整路径，因为 vrm-animation.js 是普通脚本，importmap 可能无法正确解析
                const animationModule = await import('/static/libs/three-vrm-animation.module.js');
                const { VRMAnimationLoaderPlugin } = animationModule;
                
                // 创建加载器并注册官方插件
                const loader = new GLTFLoader();
                loader.register((parser) => new VRMAnimationLoaderPlugin(parser));
                
                return loader;
            } catch (error) {
                console.error('[VRM Animation] 加载器初始化失败:', error);
                this._loaderPromise = null; // 重置，允许重试
                throw error;
            }
        })();

        return await this._loaderPromise;
    }

    async playVRMAAnimation(vrmaPath, options = {}) {
        const vrm = this.manager.currentModel?.vrm;
        if (!vrm) {
            const error = new Error('没有加载的 VRM 模型');
            console.error('[VRM Animation]', error.message);
            throw error;
        }

        try {
            // 物理防抖：暂时关闭 SpringBone
            if (this.manager.toggleSpringBone) {
                this.manager.toggleSpringBone(false);
            }

            // 清理旧 Mixer（确保完全清理）
            if (this.manager.animationMixer) {
                this.manager.animationMixer.stopAllAction();
                this.manager.animationMixer.uncacheRoot(vrm.scene);
                this.manager.animationMixer = null;
            }
            
            // 确保清理旧的 vrmaMixer（如果存在且绑定的是旧模型）
            if (this.vrmaMixer) {
                const oldRoot = this.vrmaMixer.getRoot();
                // 如果 mixer 的 root 不是当前模型的场景，说明是旧模型的 mixer
                if (oldRoot !== vrm.scene && oldRoot !== vrm.humanoid?._normalizedHumanBones?.root) {
                    this.vrmaMixer.stopAllAction();
                    this.vrmaMixer.uncacheRoot(oldRoot);
                    this.vrmaMixer = null;
                    this.currentAction = null;
                    this.vrmaIsPlaying = false;
                }
            }

            // 确保加载器已初始化
            const loader = await this._initLoader();

            // 使用官方库加载 VRMA 文件
            const gltf = await loader.loadAsync(vrmaPath);
            
            // 获取官方库解析的动画数据
            const vrmAnimations = gltf.userData?.vrmAnimations;
            if (!vrmAnimations || vrmAnimations.length === 0) {
                const error = new Error('动画文件加载成功，但没有找到 VRM 动画数据');
                console.error('[VRM Animation]', error.message);
                throw error;
            }

            // 使用第一个动画（通常只有一个）
            const vrmAnimation = vrmAnimations[0];

            // 针对当前模型创建新的 Mixer
            if (this.vrmaMixer) {
                this.vrmaMixer.stopAllAction();
                this.vrmaMixer.uncacheRoot(this.vrmaMixer.getRoot());
                this.vrmaMixer = null;
            }
            
            // 检测 VRM 版本（在创建 Mixer 之前）
            const vrmVersion = this._detectVRMVersion(vrm);
            
            // 初始 Mixer root（会在创建 clip 后根据匹配情况自动调整）
            let mixerRoot = vrm.scene;
            
            // 确保 normalized 节点在场景中（如果需要）
            if (vrmVersion === '1.0') {
                const normalizedRoot = vrm.humanoid?._normalizedHumanBones?.root;
                if (normalizedRoot) {
                    if (!vrm.scene.getObjectByName(normalizedRoot.name)) {
                        vrm.scene.add(normalizedRoot);
                    }
                    if (vrm.humanoid.autoUpdateHumanBones !== true) {
                        vrm.humanoid.autoUpdateHumanBones = true;
                    }
                }
            } else {
                // VRM 0.x：也确保 normalized 节点在场景中（如果需要）
                const normalizedRoot = vrm.humanoid?._normalizedHumanBones?.root;
                if (normalizedRoot && !vrm.scene.getObjectByName(normalizedRoot.name)) {
                    vrm.scene.add(normalizedRoot);
                }
            }
            
            // 使用官方库的 createVRMAnimationClip 创建动画 Clip
            // 这会自动处理骨骼重定向和四元数问题
            const animationModule = await import('/static/libs/three-vrm-animation.module.js');
            const { createVRMAnimationClip, VRMLookAtQuaternionProxy } = animationModule;

            // 在创建 clip 之前，确保 VRMLookAtQuaternionProxy 已创建并添加到场景
            // 这可以避免 createVRMAnimationClip 内部的警告
            if (vrm.lookAt) {
                // 检查场景中是否已存在代理
                const existingProxy = vrm.scene.getObjectByName('lookAtQuaternionProxy');
                if (!existingProxy) {
                    const lookAtQuatProxy = new VRMLookAtQuaternionProxy(vrm.lookAt);
                    lookAtQuatProxy.name = 'lookAtQuaternionProxy';
                    vrm.scene.add(lookAtQuatProxy);
                }
            }
            
            let clip;
            try {
                clip = createVRMAnimationClip(vrmAnimation, vrm);
            } catch (clipError) {
                console.error('[VRM Animation] createVRMAnimationClip 抛出异常:', clipError);
                throw new Error(`创建动画 Clip 时出错: ${clipError.message}`);
            }
            
            if (!clip || !clip.tracks || clip.tracks.length === 0) {
                console.error('[VRM Animation] 创建的动画 Clip 没有有效的轨道');
                console.error('[VRM Animation] Clip 信息:', { 
                    name: clip?.name, 
                    duration: clip?.duration, 
                    tracksCount: clip?.tracks?.length,
                    tracks: clip?.tracks?.map(t => t.name)
                });
                throw new Error('动画 Clip 创建失败：没有找到匹配的骨骼');
            }
            
            // 根据版本处理 tracks 名称
            if (vrmVersion === '1.0') {
                // VRM 1.0：使用 Normalized_ 前缀的 normalized 节点
            } else {
                // VRM 0.x：去掉 Normalized_ 前缀，直接使用 raw bones
                clip.tracks.forEach(track => {
                    if (track.name.startsWith('Normalized_')) {
                        const originalName = track.name.substring('Normalized_'.length);
                        track.name = originalName;
                    }
                });
            }
            
            // 检查 tracks 是否能找到对应的骨骼，并自动选择最佳的 root
            const sampleTracks = clip.tracks.slice(0, 10);
            let foundCount = 0;
            sampleTracks.forEach(track => {
                const boneName = track.name.split('.')[0];
                const bone = mixerRoot.getObjectByName(boneName);
                if (bone) foundCount++;
            });
            
            // 自动选择最佳的 root（在创建 Mixer 之前）
            let bestRoot = mixerRoot;
            let bestMatchCount = foundCount;
            
            // 测试 vrm.scene
            const sceneMatchCount = sampleTracks.filter(track => {
                const boneName = track.name.split('.')[0];
                return !!vrm.scene.getObjectByName(boneName);
            }).length;
            if (sceneMatchCount > bestMatchCount) {
                bestRoot = vrm.scene;
                bestMatchCount = sceneMatchCount;
            }
            
            // 测试 normalized root
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
            }
            
            // 如果找到更好的 root，切换它
            if (bestRoot !== mixerRoot) {
                mixerRoot = bestRoot;
            }
            
            // 创建绑定到场景的混合器（使用最佳 root）
            this.vrmaMixer = new window.THREE.AnimationMixer(mixerRoot);

            const newAction = this.vrmaMixer.clipAction(clip);
            if (!newAction) {
                throw new Error('无法创建动画动作');
            }
            
            // 确保 Action 已启用
            newAction.enabled = true;
            
            newAction.setLoop(options.loop ? window.THREE.LoopRepeat : window.THREE.LoopOnce);
            newAction.clampWhenFinished = true;
            
            // 设置速度 (优先使用传入参数，否则用默认)
            this.playbackSpeed = (options.timeScale !== undefined) ? options.timeScale : 1.0;
            newAction.timeScale = 1.0; // Mixer 内部保持 1，我们在 update 里控制
            
            // 处理"立即播放"逻辑
            const fadeDuration = options.fadeDuration !== undefined ? options.fadeDuration : 0.4;
            const isImmediate = options.immediate === true;

            if (isImmediate) {
                // 如果要求立即播放（如初始加载）
                if (this.currentAction) this.currentAction.stop();
                
                newAction.reset();
                newAction.enabled = true; // 确保启用
                newAction.play();
                
                // 强制 Mixer 立即计算第 0 帧的数据
                this.vrmaMixer.update(0);
                
                // 强制应用骨骼变换到场景中
                if (vrm.scene) {
                    vrm.scene.updateMatrixWorld(true);
                }
            } else {
                // 如果是切换动作（保持原有的丝滑过渡）
                if (this.currentAction && this.currentAction !== newAction) {
                    // 同步旧状态防止跳变
                    this.vrmaMixer.update(0); 
                    if (vrm.scene) vrm.scene.updateMatrixWorld(true);
                    
                    this.currentAction.fadeOut(fadeDuration);
                    newAction.enabled = true; // 确保启用
                    if (options.noReset) {
                        newAction.fadeIn(fadeDuration).play();
                    } else {
                        newAction.reset().fadeIn(fadeDuration).play();
                    }
                } else {
                    // 首次播放但非强制立即，使用淡入效果
                    newAction.enabled = true; // 确保启用
                    newAction.reset().fadeIn(fadeDuration).play();
                }
            }

            this.currentAction = newAction;
            this.vrmaIsPlaying = true;
            
            // 确保 Action 真的在播放
            if (newAction.paused) {
                console.warn('[VRM Animation] ⚠️ Action 处于暂停状态，强制播放');
                newAction.play();
            }
            
            // 立即更新一次，确保状态正确
            this.vrmaMixer.update(0.001);
            if (vrm.scene) {
                vrm.scene.updateMatrixWorld(true);
            }
            

            // 如果开启了调试，更新骨骼辅助线
            if (this.debug) this._updateSkeletonHelper();

            // 立即更新一次，确保第一帧正确显示
            this.vrmaMixer.update(0.001);
            if (vrm.scene) {
                vrm.scene.updateMatrixWorld(true);
                // 确保 SkinnedMesh 的骨骼矩阵已更新
                vrm.scene.traverse((object) => {
                    if (object.isSkinnedMesh && object.skeleton) {
                        object.skeleton.update();
                    }
                });
            }


        } catch (error) {
            console.error('[VRM Animation] 播放失败:', error);
            this.vrmaIsPlaying = false;
            throw error;
        }
    }

    stopVRMAAnimation() {
        // 清理之前的定时器，防止冲突
        if (this._springBoneTimer) {
            clearTimeout(this._springBoneTimer);
            this._springBoneTimer = null;
        }

        if (this.currentAction) {
            this.currentAction.fadeOut(0.5);
            // 这里的定时器也要保存引用
            this._springBoneTimer = setTimeout(() => {
                if (this.vrmaMixer) this.vrmaMixer.stopAllAction();
                this.currentAction = null;
                this.vrmaIsPlaying = false;

                // 再次延迟启用物理
                setTimeout(() => {
                    if (this.manager.toggleSpringBone) {
                        this.manager.toggleSpringBone(true);
                    }
                }, 100);
            }, 500);
        } else {
            if (this.vrmaMixer) this.vrmaMixer.stopAllAction();
            this.vrmaIsPlaying = false;

            // 立即重新启用 SpringBone
            if (this.manager.toggleSpringBone) {
                this.manager.toggleSpringBone(true);
            }
        }
    }

    // 调试工具
    /**
     * 开启/关闭骨骼显示
     * 在浏览器控制台输入: vrmManager.animation.toggleDebug() 即可看到骨骼
     */
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

    // 口型同步代码
    startLipSync(analyser) {
        this.analyser = analyser;
        this.lipSyncActive = true;
        this.updateMouthExpressionMapping();
        if (this.analyser) {
            this.frequencyData = new Uint8Array(this.analyser.frequencyBinCount);
        } else {
            console.warn('[VRM LipSync] analyser为空，无法启动口型同步');
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

        // 获取所有表情名称（兼容Map和Object）
        let expressionNames = [];
        const exprs = vrm.expressionManager.expressions;
        if (exprs instanceof Map) {
            expressionNames = Array.from(exprs.keys());
        } else if (Array.isArray(exprs)) {
            expressionNames = exprs.map(e => e.expressionName || e.name || e.presetName).filter(n => n);
        } else if (typeof exprs === 'object') {
            expressionNames = Object.keys(exprs);
        }

        // 映射口型表情
        ['aa', 'ih', 'ou', 'ee', 'oh'].forEach(vowel => {
            const match = expressionNames.find(name => name.toLowerCase() === vowel || name.toLowerCase().includes(vowel));
            if (match) this.mouthExpressions[vowel] = match;
        });

    }
    resetMouthExpressions() {
        const vrm = this.manager.currentModel?.vrm;
        if (!vrm?.expressionManager) return;

        // 重置所有已映射的口型表情
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
        
        // 确保 analyser 存在，否则无法获取数据
        if (!this.analyser) return;

        // 检查数组是否存在，或者长度是否匹配
        if (!this.frequencyData || this.frequencyData.length !== this.analyser.frequencyBinCount) {
            this.frequencyData = new Uint8Array(this.analyser.frequencyBinCount);
        }
        // 获取频率数据进行音频分析
        this.analyser.getByteFrequencyData(this.frequencyData);

        // 计算低频能量 (人声主要在低频段)
        let lowFreqEnergy = 0;
        let midFreqEnergy = 0;
        const lowEnd = Math.floor(this.frequencyData.length * 0.1); // 前10%为低频
        const midEnd = Math.floor(this.frequencyData.length * 0.3); // 前30%为中频

        for(let i = 0; i < lowEnd; i++) lowFreqEnergy += this.frequencyData[i];
        for(let i = lowEnd; i < midEnd; i++) midFreqEnergy += this.frequencyData[i];

        lowFreqEnergy /= lowEnd;
        midFreqEnergy /= (midEnd - lowEnd);

        // 使用低频能量作为嘴巴开合的主要指标 (人声能量主要集中在低频)
        const volume = Math.max(lowFreqEnergy, midFreqEnergy * 0.5);
        const targetWeight = Math.min(1.0, volume / 128.0); // 0-255范围，128为中等音量

        // 平滑插值
        this.currentMouthWeight += (targetWeight - this.currentMouthWeight) * (12.0 * delta);

        // 使用平滑后的权重，允许完全闭合
        const finalWeight = Math.max(0, this.currentMouthWeight);

        // 获取嘴巴张开表情名称
        const mouthOpenName = this.mouthExpressions.aa || 'aa';

        try {
            this.manager.currentModel.vrm.expressionManager.setValue(mouthOpenName, finalWeight);
        } catch (e) {
            console.warn(`[VRM LipSync] 设置表情失败: ${mouthOpenName}`, e);
        }
    }

    /**
     * 立即清理所有动画状态（用于角色切换）
     */
    reset() {
        // 清理定时器
        if (this._springBoneTimer) {
            clearTimeout(this._springBoneTimer);
            this._springBoneTimer = null;
        }
        
        // 立即停止所有动画
        if (this.vrmaMixer) {
            this.vrmaMixer.stopAllAction();
            // 清理 mixer 的 root
            const root = this.vrmaMixer.getRoot();
            if (root) {
                this.vrmaMixer.uncacheRoot(root);
            }
            this.vrmaMixer = null;
        }
        
        // 重置状态
        this.currentAction = null;
        this.vrmaIsPlaying = false;
    }

    dispose() {
        this.reset();
        this.stopLipSync();
        this.vrmaMixer = null;
        if (this.skeletonHelper) {
            this.manager.scene.remove(this.skeletonHelper);
        }
    }
}

window.VRMAnimation = VRMAnimation;
