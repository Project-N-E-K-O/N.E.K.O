/**
 * VRM 动画模块 - 终极修复版 (数据清洗 + 可视化诊断)
 * 1. 新增：四元数连续性修复 (解决旋转抽搐的数学根源)
 * 2. 新增：调试模式 (显示骨骼辅助线)
 * 3. 优化：严格的骨骼白名单
 */
class VRMAnimation {
    constructor(manager) {
        this.manager = manager;
        this.vrmaMixer = null;
        this.currentAction = null;
        this.vrmaIsPlaying = false;
        this._loaderPromise = null;
        
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

    update(delta) {
        if (this.vrmaIsPlaying && this.vrmaMixer) {
            // 强制接管时间增量，确保速度控制绝对准确
            const safeDelta = (delta <= 0 || delta > 0.1) ? 0.016 : delta;
            this.vrmaMixer.update(safeDelta * this.playbackSpeed);

            // 【关键修复】强制更新骨骼矩阵，避免手臂抽动
            const vrm = this.manager.currentModel?.vrm;
            if (vrm?.scene) {
                vrm.scene.updateMatrixWorld(true);
            }
        }
        if (this.lipSyncActive && this.analyser) {
            this._updateLipSync(delta);
        }
    }

    async _getLoaderAndUtils() {
        if (this._loaderPromise) return this._loaderPromise;
        this._loaderPromise = (async () => {
            const { GLTFLoader } = await import('three/addons/loaders/GLTFLoader.js');
            const { VRMLoaderPlugin } = await import('@pixiv/three-vrm'); // 简化导入
            const loader = new GLTFLoader();
            loader.register((parser) => new VRMLoaderPlugin(parser));
            return loader;
        })();
        return this._loaderPromise;
    }

    async playVRMAAnimation(vrmaPath, options = {}) {
        const vrm = this.manager.currentModel?.vrm;
        if (!vrm) return;

        try {
            console.log(`[VRM Animation] 加载: ${vrmaPath}`);
            
            // 物理防抖：暂时关闭 SpringBone
            if (this.manager.toggleSpringBone) {
                this.manager.toggleSpringBone(false);
                console.log('[VRM Animation] SpringBone 已临时禁用');
            } 

            // 清理旧 Mixer
            if (this.manager.animationMixer) {
                this.manager.animationMixer.stopAllAction();
                this.manager.animationMixer.uncacheRoot(vrm.scene);
                this.manager.animationMixer = null;
            }

            const loader = await this._getLoaderAndUtils();
            const gltf = await new Promise((resolve, reject) => loader.load(vrmaPath, resolve, undefined, reject));
            const originalClip = gltf.animations[0];

            // 针对【当前模型】创建新的 Mixer
            if (this.vrmaMixer) {
                this.vrmaMixer.stopAllAction();
                this.vrmaMixer.uncacheRoot(this.vrmaMixer.getRoot()); // 清理旧绑定
                this.vrmaMixer = null;
            }
            
            // 创建绑定到【当前新模型】(vrm.scene) 的混合器
            this.vrmaMixer = new window.THREE.AnimationMixer(vrm.scene);

            // 1. 严格映射重定向
            let clip = this._strictRetargetClip(originalClip, vrm);
            
            // 2. 【核心修复】数据清洗：修复四元数跳变
            // 这步操作会修改 clip 内部的数据，消除数学上的“翻转”
            this._ensureQuaternionContinuity(clip);

            const newAction = this.vrmaMixer.clipAction(clip);
            newAction.setLoop(options.loop ? window.THREE.LoopRepeat : window.THREE.LoopOnce);
            newAction.clampWhenFinished = true;

            // 【关键修复】强制设置旋转轨道的插值模式
            // 对于四元数（旋转），使用球面线性插值可以避免关节抽动
            clip.tracks.forEach(track => {
                if (track.name.endsWith('.quaternion')) {
                    // 设置为线性插值，但我们已经在 _ensureQuaternionContinuity 中处理了连续性
                    track.setInterpolation(window.THREE.InterpolateLinear);
                }
            });
            
            // 设置速度 (优先使用传入参数，否则用默认)
            this.playbackSpeed = (options.timeScale !== undefined) ? options.timeScale : 1.0;
            newAction.timeScale = 1.0; // Mixer 内部保持 1，我们在 update 里控制

            // 柔和过渡
            const fadeDuration = 0.4;
            if (this.currentAction && this.currentAction !== newAction) {
                this.currentAction.fadeOut(fadeDuration);
                newAction.reset().fadeIn(fadeDuration).play();
            } else {
                newAction.reset().fadeIn(fadeDuration).play();
            }
            
            this.currentAction = newAction;
            this.vrmaIsPlaying = true;
            
            console.log(`[VRM Animation] 播放中... (速度: ${this.playbackSpeed})`);

            // 如果开启了调试，更新骨骼辅助线
            if (this.debug) this._updateSkeletonHelper();

        } catch (error) {
            console.error('[VRM Animation] 播放失败:', error);
            this.vrmaIsPlaying = false;
        }
    }

    stopVRMAAnimation() {
        console.log('[VRM Animation] 停止动画播放');
        if (this.currentAction) {
            this.currentAction.fadeOut(0.5);
            setTimeout(() => {
                if (this.vrmaMixer) this.vrmaMixer.stopAllAction();
                this.currentAction = null;
                this.vrmaIsPlaying = false;

                // 【关键修复】延迟重新启用 SpringBone，避免立即冲突
                setTimeout(() => {
                    if (this.manager.toggleSpringBone) {
                        this.manager.toggleSpringBone(true);
                        console.log('[VRM Animation] SpringBone 已重新启用');
                    }
                }, 100);

            }, 500);
        } else {
            if (this.vrmaMixer) this.vrmaMixer.stopAllAction();
            this.vrmaIsPlaying = false;

            // 立即重新启用 SpringBone
            if (this.manager.toggleSpringBone) {
                this.manager.toggleSpringBone(true);
                console.log('[VRM Animation] SpringBone 已重新启用');
            }
        }
    }

    /**
     * 【核心修复算法】四元数连续性清洗
     * 遍历所有旋转轨道，确保相邻帧的旋转路径最短。
     * 解决“手指乱跳”和“手臂反转”的数学根源。
     */
    _ensureQuaternionContinuity(clip) {
        const THREE = window.THREE;
        
        clip.tracks.forEach(track => {
            // 只处理旋转轨道 (quaternion)
            if (!track.name.endsWith('.quaternion')) return;

            const values = track.values; // flat array [x, y, z, w, x, y, z, w...]
            const numKeys = values.length / 4;
            
            // 临时变量
            const prevQuat = new THREE.Quaternion();
            const currQuat = new THREE.Quaternion();

            // 从第二帧开始检查
            for (let i = 1; i < numKeys; i++) {
                // 读取前一帧
                prevQuat.fromArray(values, (i - 1) * 4);
                // 读取当前帧
                currQuat.fromArray(values, i * 4);

                // 计算点积。如果点积 < 0，说明两个四元数虽然代表相同/相似的角度，
                // 但是处于四维球面的对面（导致插值时会绕地球一圈）。
                if (prevQuat.dot(currQuat) < 0) {
                    // 修复：将当前四元数所有分量取反
                    // (q 和 -q 代表相同的旋转，但取反后离前一帧更近)
                    values[i * 4]     = -values[i * 4];     // x
                    values[i * 4 + 1] = -values[i * 4 + 1]; // y
                    values[i * 4 + 2] = -values[i * 4 + 2]; // z
                    values[i * 4 + 3] = -values[i * 4 + 3]; // w
                }
            }
        });
    }

    /**
     * 严格白名单重定向 (含手指保护)
     */
    _strictRetargetClip(originalClip, vrm) {
        const tracks = [];
        const validBoneMap = new Map();
        
        if (vrm.humanoid) {
             const getBoneNode = (name) => {
                 return vrm.humanoid.getRawBoneNode ? vrm.humanoid.getRawBoneNode(name) : 
                        (vrm.humanoid.humanBones?.[name]?.node || vrm.humanoid.humanBones?.[name]);
             };

             // 仅允许标准骨骼 (不包含 Twist/Metacarpal)
             const standardBones = [
                 'hips','spine','chest','upperChest','neck','head',
                 'leftEye','rightEye',
                 'leftShoulder','rightShoulder','leftUpperArm','rightUpperArm','leftLowerArm','rightLowerArm','leftHand','rightHand',
                 'leftUpperLeg','rightUpperLeg','leftLowerLeg','rightLowerLeg','leftFoot','rightFoot','leftToes','rightToes',
                 // 手指 (仅 3 节，过滤掌骨 Metacarpal 以防手掌变形)
                 'leftThumbProximal', 'leftThumbIntermediate', 'leftThumbDistal',
                 'leftIndexProximal', 'leftIndexIntermediate', 'leftIndexDistal',
                 'leftMiddleProximal', 'leftMiddleIntermediate', 'leftMiddleDistal',
                 'leftRingProximal', 'leftRingIntermediate', 'leftRingDistal',
                 'leftLittleProximal', 'leftLittleIntermediate', 'leftLittleDistal',
                 'rightThumbProximal', 'rightThumbIntermediate', 'rightThumbDistal',
                 'rightIndexProximal', 'rightIndexIntermediate', 'rightIndexDistal',
                 'rightMiddleProximal', 'rightMiddleIntermediate', 'rightMiddleDistal',
                 'rightRingProximal', 'rightRingIntermediate', 'rightRingDistal',
                 'rightLittleProximal', 'rightLittleIntermediate', 'rightLittleDistal'
             ];
             
             standardBones.forEach(boneName => {
                 const node = getBoneNode(boneName);
                 if (node) validBoneMap.set(node.name.toLowerCase(), { node, type: boneName });
             });
        }

        originalClip.tracks.forEach((track) => {
            //允许动作文件控制表情
            //if (track.name.toLowerCase().includes('expression') || track.name.toLowerCase().includes('blendshape')) return;
            const lastDotIndex = track.name.lastIndexOf('.');
            const property = track.name.substring(lastDotIndex + 1);
            let nodeName = track.name.substring(0, lastDotIndex);

            if (property === 'scale') return; // 禁止缩放

            const targetEntry = validBoneMap.get(nodeName.toLowerCase());
            if (targetEntry) {
                const { node, type } = targetEntry;
                // 仅 Hips 允许位移，防止手指脱臼
                if (property === 'position' && type !== 'hips') return;

                const newTrack = track.clone();
                newTrack.name = `${node.name}.${property}`;

                // 【调试】记录映射的骨骼信息
                if (this.debug && (type.includes('Arm') || type.includes('Hand'))) {
                    console.log(`[VRM Animation] 映射手臂骨骼: ${nodeName} -> ${type} (${property})`);
                }

                tracks.push(newTrack);
            } else if (this.debug && (nodeName.toLowerCase().includes('arm') || nodeName.toLowerCase().includes('hand'))) {
                // 【调试】记录未映射的手臂相关骨骼
                console.warn(`[VRM Animation] 未映射的手臂骨骼: ${nodeName} (${property})`);
            }
        });

        const THREE = window.THREE;
        return new THREE.AnimationClip(originalClip.name, originalClip.duration, tracks);
    }

    // --- 调试工具 ---
    /**
     * 开启/关闭骨骼显示
     * 在浏览器控制台输入: vrmManager.animation.toggleDebug() 即可看到骨骼
     */
    toggleDebug() {
        this.debug = !this.debug;
        console.log(`[VRM Debug] 骨骼显示: ${this.debug ? 'ON' : 'OFF'}`);
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

    //口型同步代码
    startLipSync(analyser) {
        this.analyser = analyser;
        this.lipSyncActive = true;
        this.updateMouthExpressionMapping();
        if (this.analyser) this.frequencyData = new Uint8Array(this.analyser.frequencyBinCount);
    }
    stopLipSync() {
        this.lipSyncActive = false;
        this.resetMouthExpressions();
        this.analyser = null;
    }
    updateMouthExpressionMapping() {
        const vrm = this.manager.currentModel?.vrm;
        if (!vrm?.expressionManager) return;
        const expressionNames = Object.keys(vrm.expressionManager.expressions);
        ['aa', 'ih', 'ou', 'ee', 'oh'].forEach(vowel => {
            const match = expressionNames.find(name => name.toLowerCase() === vowel || name.toLowerCase().includes(vowel));
            if (match) this.mouthExpressions[vowel] = match;
        });
    }
    resetMouthExpressions() {
        const vrm = this.manager.currentModel?.vrm;
        if (!vrm?.expressionManager) return;
        Object.values(this.mouthExpressions).forEach(name => {
            if (name) vrm.expressionManager.setValue(name, 0);
        });
    }
    _updateLipSync(delta) {
        if (!this.manager.currentModel?.vrm?.expressionManager) return;
        this.analyser.getByteFrequencyData(this.frequencyData);
        let volume = 0;
        for(let i = 0; i < this.frequencyData.length; i++) volume += this.frequencyData[i];
        volume /= this.frequencyData.length;
        const targetWeight = Math.min(1.0, (volume / 50) * 1.5);
        this.currentMouthWeight += (targetWeight - this.currentMouthWeight) * (15.0 * delta);
        const mouthOpenName = this.mouthExpressions.aa || 'aa';
        if (mouthOpenName) this.manager.currentModel.vrm.expressionManager.setValue(mouthOpenName, this.currentMouthWeight);
    }

    dispose() {
        this.stopVRMAAnimation();
        this.stopLipSync();
        this.vrmaMixer = null;
        if (this.skeletonHelper) {
            this.manager.scene.remove(this.skeletonHelper);
        }
    }
}

window.VRMAnimation = VRMAnimation;
console.log('[VRM Animation] 终极修复版已加载 (含自动数据清洗)');