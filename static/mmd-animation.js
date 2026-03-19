/**
 * MMD 动画模块 - VMD 动画加载、播放控制、IK/Grant 解算、口型同步
 * 基于 @moeru/three-mmd 的 VMDLoader + buildAnimation
 */

class MMDAnimation {
    constructor(manager) {
        this.manager = manager;

        // 动画状态
        this.mixer = null;
        this.currentAction = null;
        this.currentClip = null;
        this.clock = null;
        this.isPlaying = false;
        this.isLoop = true;

        // IK + Grant
        this.ikSolver = null;
        this.grantSolver = null;

        // 口型同步
        this._lipSyncEnabled = false;
        this._audioContext = null;
        this._analyser = null;
        this._audioSource = null;

        // 骨骼缓存（用于 IK/Grant 更新时保存/恢复）
        this._boneBackup = null;

        // 模块缓存
        this._mmdModuleCache = null;
    }

    async _getMMDModule() {
        if (this._mmdModuleCache) return this._mmdModuleCache;
        try {
            this._mmdModuleCache = await import('@moeru/three-mmd');
            return this._mmdModuleCache;
        } catch (error) {
            console.error('[MMD Animation] 无法导入 @moeru/three-mmd:', error);
            return null;
        }
    }

    // ═══════════════════ VMD 加载 ═══════════════════

    async loadAnimation(vmdUrl) {
        const THREE = window.THREE;
        if (!THREE) throw new Error('Three.js 未加载');

        const mmd = this.manager.currentModel;
        if (!mmd || !mmd.mesh) {
            throw new Error('未加载 MMD 模型');
        }

        const mmdModule = await this._getMMDModule();
        if (!mmdModule) throw new Error('three-mmd 模块不可用');

        const { VMDLoader, buildAnimation, GrantSolver, processBones } = mmdModule;

        // 加载 VMD 文件
        const vmdLoader = new VMDLoader();
        const vmdObject = await new Promise((resolve, reject) => {
            vmdLoader.load(
                vmdUrl,
                (vmd) => resolve(vmd),
                undefined,
                (error) => reject(error)
            );
        });

        // 清理之前的动画
        this._cleanupAnimation();

        // 构建动画 Clip
        const clip = buildAnimation(vmdObject, mmd.mesh);
        this.currentClip = clip;

        // 创建 AnimationMixer
        this.mixer = new THREE.AnimationMixer(mmd.mesh);
        this.currentAction = this.mixer.clipAction(clip);
        this.currentAction.setLoop(this.isLoop ? THREE.LoopRepeat : THREE.LoopOnce, Infinity);

        // IK 解算器
        if (mmd.iks && mmd.iks.length > 0) {
            try {
                const { CCDIKSolver } = await import('three/addons/animation/CCDIKSolver.js');
                this.ikSolver = new CCDIKSolver(mmd.mesh, mmd.iks);
            } catch (e) {
                console.warn('[MMD Animation] CCDIKSolver 不可用:', e);
            }
        }

        // Grant 解算器
        if (mmd.grants && mmd.grants.length > 0) {
            this.grantSolver = new GrantSolver(mmd.mesh, mmd.grants);
        }

        // 重置骨骼到绑定姿态，防止上一动画残留姿态污染备份
        if (mmd.mesh.skeleton) mmd.mesh.skeleton.pose();

        // 初始化骨骼缓存
        this._initBoneBackup(mmd.mesh);

        // 使用 processBones
        this._processBones = processBones;

        this.clock = new THREE.Clock();
        console.log('[MMD Animation] 动画加载完成:', vmdUrl);

        return clip;
    }

    // ═══════════════════ 骨骼缓存 ═══════════════════

    _initBoneBackup(mesh) {
        const THREE = window.THREE;
        if (!mesh?.skeleton?.bones || !THREE) return;

        this._boneBackup = mesh.skeleton.bones.map(bone => ({
            position: bone.position.clone(),
            quaternion: bone.quaternion.clone()
        }));
    }

    _saveBones(mesh) {
        if (!this._boneBackup || !mesh?.skeleton?.bones) return;
        mesh.skeleton.bones.forEach((bone, i) => {
            if (this._boneBackup[i]) {
                this._boneBackup[i].position.copy(bone.position);
                this._boneBackup[i].quaternion.copy(bone.quaternion);
            }
        });
    }

    _restoreBones(mesh) {
        if (!this._boneBackup || !mesh?.skeleton?.bones) return;
        mesh.skeleton.bones.forEach((bone, i) => {
            if (this._boneBackup[i]) {
                bone.position.copy(this._boneBackup[i].position);
                bone.quaternion.copy(this._boneBackup[i].quaternion);
            }
        });
    }

    // ═══════════════════ 播放控制 ═══════════════════

    play() {
        if (!this.currentAction) return;
        this.currentAction.play();
        if (this.clock) this.clock.start();
        this.isPlaying = true;
    }

    pause() {
        if (this.clock) this.clock.stop();
        this.isPlaying = false;
    }

    stop() {
        if (this.currentAction) {
            this.currentAction.stop();
        }
        if (this.clock) this.clock.stop();
        this.isPlaying = false;
    }

    setLoop(loop) {
        const THREE = window.THREE;
        this.isLoop = loop;
        if (this.currentAction && THREE) {
            this.currentAction.setLoop(loop ? THREE.LoopRepeat : THREE.LoopOnce, Infinity);
        }
    }

    setTimeScale(scale) {
        if (this.currentAction) {
            this.currentAction.timeScale = scale;
        }
    }

    // ═══════════════════ 帧更新 ═══════════════════

    update(delta) {
        if (!this.isPlaying || !this.mixer) return;

        const mesh = this.manager.currentModel?.mesh;
        if (!mesh) return;

        // 1. 恢复骨骼到上帧动画基准（PropertyMixer 缓存一致性）
        this._restoreBones(mesh);

        // 2. AnimationMixer 更新（在干净基准上应用新帧动画）
        this.mixer.update(delta);

        // 3. 保存动画后的骨骼状态（供下帧恢复）
        this._saveBones(mesh);

        // 4. 更新世界矩阵
        mesh.updateMatrixWorld(true);

        // 5. IK 解算
        if (this.ikSolver) {
            this.ikSolver.update();
        }

        // 6. Grant 解算
        if (this.grantSolver) {
            this.grantSolver.update();
        }

        // 7. 检查动画结束（非循环模式）
        if (!this.isLoop && this.currentAction) {
            const clipDuration = this.currentClip?.duration || 0;
            if (this.mixer.time >= clipDuration) {
                this.pause();
                // 重置到 T-Pose
                if (this.manager.core) {
                    this.manager.core.resetModelPose();
                }
            }
        }
    }

    // ═══════════════════ 口型同步 ═══════════════════

    enableLipSync(audioElement) {
        if (!audioElement) return;

        try {
            if (!this._audioContext) {
                this._audioContext = new (window.AudioContext || window.webkitAudioContext)();
            }

            // 防止对同一 audio element 重复创建 MediaElementSource
            if (this._audioSource) {
                if (this._lipSyncAudioElement === audioElement) {
                    // 同一 element 已经连接，直接返回
                    return;
                }
                // 不同 element，断开旧连接
                try { this._audioSource.disconnect(); } catch (_) {}
                this._audioSource = null;
            }
            if (this._analyser) {
                try { this._analyser.disconnect(); } catch (_) {}
                this._analyser = null;
            }

            this._analyser = this._audioContext.createAnalyser();
            this._analyser.fftSize = 256;
            this._analyser.smoothingTimeConstant = 0.8;

            this._audioSource = this._audioContext.createMediaElementSource(audioElement);
            this._lipSyncAudioElement = audioElement;
            this._audioSource.connect(this._analyser);
            this._analyser.connect(this._audioContext.destination);

            this._lipSyncEnabled = true;
            console.log('[MMD Animation] 口型同步已启用');
        } catch (error) {
            console.warn('[MMD Animation] 口型同步初始化失败:', error);
        }
    }

    getLipSyncValue() {
        if (!this._lipSyncEnabled || !this._analyser) return 0;

        const dataArray = new Uint8Array(this._analyser.frequencyBinCount);
        this._analyser.getByteFrequencyData(dataArray);

        // 计算人声频率范围（80-600Hz）的平均响度
        const sampleRate = this._audioContext.sampleRate;
        const binWidth = sampleRate / this._analyser.fftSize;
        const lowBin = Math.floor(80 / binWidth);
        const highBin = Math.min(Math.ceil(600 / binWidth), dataArray.length - 1);

        let sum = 0;
        let count = 0;
        for (let i = lowBin; i <= highBin; i++) {
            sum += dataArray[i];
            count++;
        }

        const average = count > 0 ? sum / count : 0;
        // 归一化到 0-1 范围
        return Math.min(1, Math.max(0, (average - 20) / 180));
    }

    // ═══════════════════ 清理 ═══════════════════

    _cleanupAnimation() {
        if (this.currentAction) {
            this.currentAction.stop();
            this.currentAction = null;
        }
        if (this.mixer) {
            this.mixer.stopAllAction();
            this.mixer = null;
        }
        this.currentClip = null;
        this.ikSolver = null;
        this.grantSolver = null;
        this._boneBackup = null;
        this.isPlaying = false;
        if (this.clock) {
            this.clock.stop();
            this.clock = null;
        }
    }

    dispose() {
        this._cleanupAnimation();

        if (this._audioSource) {
            try { this._audioSource.disconnect(); } catch (e) { /* ignore */ }
            this._audioSource = null;
        }
        if (this._analyser) {
            try { this._analyser.disconnect(); } catch (e) { /* ignore */ }
            this._analyser = null;
        }
        if (this._audioContext && this._audioContext.state !== 'closed') {
            this._audioContext.close().catch(() => {});
            this._audioContext = null;
        }
        this._lipSyncEnabled = false;

        console.log('[MMD Animation] 资源已清理');
    }
}
