/**
 * VRM 动画模块 - 最终投产版 (完整修复)
 * 功能：全场景骨骼匹配、自动重定向、口型同步支持
 * 状态：无调试陷阱，无自动播放，修复了语法错误
 */
class VRMAnimation {
    constructor(manager) {
        this.manager = manager;
        this.vrmaMixer = null;
        this.vrmaAction = null;
        this.vrmaIsPlaying = false;
        this._loaderPromise = null;

        // 口型同步相关
        this.lipSyncActive = false;
        this.analyser = null;
        this.mouthExpressions = { 'aa': null, 'ih': null, 'ou': null, 'ee': null, 'oh': null };
        this.currentMouthWeight = 0;
        this.frequencyData = null;
    }

    /**
     * 每帧更新 (由 vrm-manager 驱动)
     */
    update(delta) {
        // 1. 驱动 VRMA 动画
        if (this.vrmaIsPlaying && this.vrmaMixer) {
            // 安全的时间增量
            const safeDelta = (delta <= 0 || delta > 0.1) ? 0.016 : delta;
            this.vrmaMixer.update(safeDelta);
        }

        // 2. 驱动口型同步
        if (this.lipSyncActive && this.analyser) {
            this._updateLipSync(delta);
        }
    }

    /**
     * 获取 GLTF 加载器 (单例)
     */
    async _getLoader() {
        if (this._loaderPromise) return this._loaderPromise;
        this._loaderPromise = (async () => {
            const { GLTFLoader } = await import('three/addons/loaders/GLTFLoader.js');
            const { VRMLoaderPlugin } = await import('@pixiv/three-vrm');
            const loader = new GLTFLoader();
            loader.register((parser) => new VRMLoaderPlugin(parser));
            return loader;
        })();
        return this._loaderPromise;
    }

    /**
     * 播放 VRMA 动画
     */
    async playVRMAAnimation(vrmaPath, options = {}) {
        const vrm = this.manager.currentModel?.vrm;
        if (!vrm) throw new Error('VRM 模型未加载');

        try {
            // 切换动作前先清理旧的
            this.stopVRMAAnimation();

            const loader = await this._getLoader();
            console.log(`[VRM Animation] 加载动作: ${vrmaPath}`);
            
            const gltf = await new Promise((resolve, reject) => {
                loader.load(vrmaPath, resolve, undefined, reject);
            });

            const originalClip = gltf.animations[0];
            if (!originalClip) throw new Error('VRMA 动画为空');

            this.vrmaMixer = new window.THREE.AnimationMixer(vrm.scene);
            
            // 优先尝试官方重定向工具
            let clip = null;
            let createVRMAnimationClip = null;
            try {
                const ThreeVRM = await import('@pixiv/three-vrm');
                createVRMAnimationClip = ThreeVRM.createVRMAnimationClip || (ThreeVRM.VRMUtils && ThreeVRM.VRMUtils.createVRMAnimationClip);
            } catch (e) {}

            if (typeof createVRMAnimationClip === 'function') {
                clip = createVRMAnimationClip(originalClip, vrm);
            } else {
                clip = this._universalRetargetClip(originalClip, vrm);
            }
            
            this.vrmaAction = this.vrmaMixer.clipAction(clip);
            this.vrmaAction.setLoop(options.loop ? window.THREE.LoopRepeat : window.THREE.LoopOnce);
            this.vrmaAction.clampWhenFinished = true;
            this.vrmaAction.timeScale = options.timeScale || 1.0;
            
            // 强制重置并播放
            this.vrmaAction.reset();
            this.vrmaAction.play();
            
            this.vrmaIsPlaying = true;
            console.log(`[VRM Animation] 开始播放: ${clip.name}`);
            
        } catch (error) {
            console.error('VRMA 播放失败:', error);
            this.vrmaIsPlaying = false;
            throw error;
        }
    }

    /**
     * 停止动画 (恢复 T-Pose)
     */
    stopVRMAAnimation() {
        if (this.vrmaAction) {
            this.vrmaAction.stop();
            this.vrmaAction = null;
        }
        if (this.vrmaMixer) {
            this.vrmaMixer.stopAllAction();
            this.vrmaMixer = null; 
        }
        this.vrmaIsPlaying = false;
    }

    /**
     * 全场景通用重定向
     */
    _universalRetargetClip(originalClip, vrm) {
        const tracks = [];
        const THREE = window.THREE;
        const nodeMap = new Map();
        
        vrm.scene.traverse((node) => {
            nodeMap.set(node.name.toLowerCase(), node);
        });

        if (vrm.humanoid) {
            const humanBones = vrm.humanoid.humanoidBones || vrm.humanoid._humanoidBones;
            if (humanBones) {
                const iterator = humanBones.entries ? humanBones.entries() : Object.entries(humanBones);
                for (const [boneName, bone] of iterator) {
                    const node = bone.node || bone;
                    if (node) {
                        nodeMap.set(boneName.toLowerCase(), node);
                        nodeMap.set(`humanoid.${boneName.toLowerCase()}`, node);
                    }
                }
            }
        }

        let validCount = 0;
        originalClip.tracks.forEach((track) => {
            if (track.name.toLowerCase().includes('expression') || track.name.toLowerCase().includes('blendshape')) return; 

            const lastDotIndex = track.name.lastIndexOf('.');
            const property = track.name.substring(lastDotIndex + 1);
            let nodeName = track.name.substring(0, lastDotIndex);
            
            let targetNode = nodeMap.get(nodeName.toLowerCase());
            if (!targetNode) {
                const strippedName = nodeName.replace(/^humanoid\./i, '').replace(/^vrm\./i, '');
                targetNode = nodeMap.get(strippedName.toLowerCase());
            }

            if (targetNode) {
                const newTrack = track.clone();
                newTrack.name = `${targetNode.name}.${property}`;
                tracks.push(newTrack);
                validCount++;
            }
        });

        if (validCount === 0) return originalClip;
        return new THREE.AnimationClip(originalClip.name, originalClip.duration, tracks);
    }

    // --- 口型同步 ---
    startLipSync(analyser) {
        this.analyser = analyser;
        this.lipSyncActive = true;
        this.updateMouthExpressionMapping();
        if (this.analyser) {
            this.frequencyData = new Uint8Array(this.analyser.frequencyBinCount);
        }
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
    }
}

// 导出到全局
window.VRMAnimation = VRMAnimation;
console.log('[VRM Animation] 最终完整版已加载');