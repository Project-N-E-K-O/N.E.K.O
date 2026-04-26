/**
 * MMD 动画模块 - VMD 动画加载、播放控制、IK/Grant 解算、口型同步
 * 基于 @moeru/three-mmd 的 VMDLoader + buildAnimation
 */

class MMDAnimation {
    constructor(manager) {
        this.manager = manager;

        // 异步加载请求 ID（用于取消过期请求）
        this._loadRequestId = 0;

        // 动画状态
        this.mixer = null;
        this.currentAction = null;
        this.currentClip = null;
        this.clock = null;
        this.isPlaying = false;
        this.isPaused = false;  // 区分暂停 vs 停止（IK/Grant 仅在非暂停、非播放时运行）
        this.isLoop = true;

        // IK + Grant
        this.ikSolver = null;
        this.grantSolver = null;

        // 口型同步
        this._lipSyncEnabled = false;
        this._lipSyncActive = false;
        this._audioContext = null;
        this._analyser = null;
        this._audioSource = null;
        this._lipSyncAudioElement = null;
        this._ownsAnalyser = false;

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
        const requestId = ++this._loadRequestId;
        const THREE = window.THREE;
        if (!THREE) throw new Error('Three.js 未加载');

        const mmd = this.manager.currentModel;
        if (!mmd || !mmd.mesh) {
            throw new Error('未加载 MMD 模型');
        }

        const mmdModule = await this._getMMDModule();
        if (requestId !== this._loadRequestId || this.manager.currentModel !== mmd) return null;
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
        if (requestId !== this._loadRequestId || this.manager.currentModel !== mmd) return null;

        // 解析 VMD 的 IK 开关时间轴（propertyKeyFrames）
        // VMD 动画可以在不同帧切换 IK 的启用/禁用状态
        const ikSwitchTimeline = this._buildIkSwitchTimeline(vmdObject.propertyKeyFrames);

        // 清理之前的动画
        this._cleanupAnimation();

        // 保存 IK 开关时间轴（在 cleanup 之后设置，避免被清掉）
        this._ikSwitchTimeline = ikSwitchTimeline;

        // 构建动画 Clip
        const clip = buildAnimation(vmdObject, mmd.mesh);

        // 防御：把每条四元数轨道的相邻关键帧翻到同半球（dot >= 0），
        // 避免个别 VMD 在动画切换初始几帧被插值成长路径 / 奇点甩动。
        this._normalizeQuaternionTrackSigns(clip);
        this.currentClip = clip;

        // 创建 AnimationMixer
        this.mixer = new THREE.AnimationMixer(mmd.mesh);
        this.currentAction = this.mixer.clipAction(clip);
        this.currentAction.setLoop(this.isLoop ? THREE.LoopRepeat : THREE.LoopOnce, Infinity);
        this.currentAction.clampWhenFinished = true; // 防止动画结束后 action 被 disable 导致 T-Pose

        // 安全网：如果循环动画意外触发 finished 事件，自动重播
        this.mixer.addEventListener('finished', (e) => {
            if (this.isLoop && e.action === this.currentAction) {
                console.warn('[MMD Animation] 循环动画意外结束，自动重播');
                e.action.reset();
                e.action.play();
            }
        });

        // IK 解算器
        if (mmd.iks && mmd.iks.length > 0) {
            try {
                const { CCDIKSolver } = await import('three/addons/animation/CCDIKSolver.js');
                if (requestId !== this._loadRequestId || this.manager.currentModel !== mmd) return null;
                this.ikSolver = new CCDIKSolver(mmd.mesh, mmd.iks);

                // 根据 VMD 的 IK 开关设置初始状态（第 0 帧）
                this._applyIkSwitchState(0);
            } catch (e) {
                console.warn('[MMD Animation] CCDIKSolver 不可用:', e);
            }
        }

        // Grant 解算器
        if (mmd.grants && mmd.grants.length > 0) {
            this.grantSolver = new GrantSolver(mmd.mesh, mmd.grants);
        }

        // 重置骨骼到绑定姿态（干净的基准状态）
        if (mmd.mesh.skeleton) mmd.mesh.skeleton.pose();

        // 重置 cursorFollow 的眼骨偏移状态（新动画的骨骼基准已重置）
        if (this.manager?.cursorFollow) {
            this.manager.cursorFollow._eyeLastOffsetQuat?.identity();
            this.manager.cursorFollow._currentYaw = 0;
            this.manager.cursorFollow._currentPitch = 0;
            this.manager.cursorFollow._targetYaw = 0;
            this.manager.cursorFollow._targetPitch = 0;
        }

        // 使用 processBones
        this._processBones = processBones;

        this.clock = new THREE.Clock();

        // Pre-warm：立即应用第 0 帧，避免 T-pose 闪烁
        this.currentAction.play();
        this.mixer.update(0);

        // 在第 0 帧动画姿态上初始化骨骼备份（Grant 之前！）
        // 这样 _restoreBones 恢复的是纯动画状态，Grant 不会累积
        this._initBoneBackup(mmd.mesh);

        if (this.ikSolver) this.ikSolver.update();
        if (this.grantSolver) this.grantSolver.update();
        mmd.mesh.updateMatrixWorld(true);

        // 暂停，等待外部调用 play()
        this.currentAction.paused = true;
        this.clock.stop();

        console.log('[MMD Animation] 动画加载完成:', vmdUrl);

        return clip;
    }

    // ═══════════════════ 轨道防御 ═══════════════════

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
        if (!this.currentAction) {
            return;
        }
        this.currentAction.paused = false;
        this.currentAction.play();
        if (this.clock) this.clock.start();
        this.isPlaying = true;
        this.isPaused = false;
        this.manager._isTPose = false;
    }

    pause() {
        if (this.clock) this.clock.stop();
        this.isPlaying = false;
        this.isPaused = true;
    }

    stop() {
        if (this.currentAction) {
            this.currentAction.stop();
        }
        if (this.clock) this.clock.stop();

        const mesh = this.manager.currentModel?.mesh;
        if (mesh?.skeleton) {
            mesh.skeleton.pose();
        }

        if (this.manager?.cursorFollow) {
            const cf = this.manager.cursorFollow;
            cf._appliedLastFrame = false;
            cf._targetWeight = 0;
            cf._trackingWeight = 0;
            cf._eyeLastOffsetQuat?.identity();
            cf._currentYaw = 0;
            cf._currentPitch = 0;
            cf._targetYaw = 0;
            cf._targetPitch = 0;
            if (cf._neckBone) cf._neckBaseQuat.copy(cf._neckBone.quaternion);
            if (cf._headBone) cf._headBaseQuat.copy(cf._headBone.quaternion);
        }

        this.isPlaying = false;
        this.isPaused = false;
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

        // 5. IK 解算（根据 VMD 的 IK 开关状态动态启用/禁用）
        if (this.ikSolver) {
            if (this._ikSwitchTimeline) {
                const currentFrame = (this.mixer.time || 0) * 30;
                this._applyIkSwitchState(currentFrame);
            }
            this.ikSolver.update();
        }

        // 6. Grant 解算（在 IK 之后，这样 D 骨骼能收到 IK 的结果）
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
            if (!this._audioContext || this._audioContext.state === 'closed') {
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
            this._ownsAnalyser = true; // 自己创建的 analyser 由我们管理

            // 使用 captureStream 避免 createMediaElementSource 的单次绑定限制
            if (audioElement.captureStream) {
                const stream = audioElement.captureStream();
                this._audioSource = this._audioContext.createMediaStreamSource(stream);
            } else {
                this._audioSource = this._audioContext.createMediaElementSource(audioElement);
            }
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
        // 优先使用 analyser 自己的 context，否则回退到 _audioContext，最后使用默认值
        let sampleRate = 48000;
        if (this._analyser.context) {
            sampleRate = this._analyser.context.sampleRate;
        } else if (this._audioContext) {
            sampleRate = this._audioContext.sampleRate;
        }
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
        const value = Math.min(1, Math.max(0, (average - 20) / 180));
        
        if (window.DEBUG_AUDIO && value > 0.1) {
            console.log('[MMD Animation] getLipSyncValue:', value, 'average:', average);
        }
        return value;
    }

    // ═══════════════════ 兼容 VRMAnimation 的口型同步 API ═══════════════════

    startLipSync(analyser) {
        console.log('[MMD Animation] startLipSync 被调用', { 
            hasAnalyser: !!analyser, 
            hasManager: !!this.manager,
            hasExpression: !!this.manager.expression 
        });
        if (analyser) {
            this._analyser = analyser;
            this._ownsAnalyser = false; // 外部传入的 analyser 不由我们管理
        }
        this._lipSyncActive = true;
        this._lipSyncEnabled = true;
        console.log('[MMD Animation] 口型同步已启动 (startLipSync)');
    }

    stopLipSync() {
        this._lipSyncActive = false;
        this._lipSyncEnabled = false;
        if (this.manager.expression) {
            this.manager.expression.setMouth(0);
        }
        console.log('[MMD Animation] 口型同步已停止 (stopLipSync)');
    }

    // ═══════════════════ IK 开关管理 ═══════════════════

    /**
     * 从 VMD 的 propertyKeyFrames 构建 IK 开关时间轴。
     * 返回按帧号排序的数组，每个元素包含 { frame, ikStates: Map<ikName, enabled> }。
     * 如果没有 propertyKeyFrames 或为空，返回 null。
     */
    _buildIkSwitchTimeline(propertyKeyFrames) {
        if (!propertyKeyFrames || propertyKeyFrames.length === 0) return null;

        const timeline = propertyKeyFrames
            .filter(pkf => pkf.ikStates && pkf.ikStates.length > 0)
            .map(pkf => ({
                frame: pkf.frameNumber,
                ikStates: new Map(pkf.ikStates) // [[ikName, enabled], ...]
            }))
            .sort((a, b) => a.frame - b.frame);

        if (timeline.length === 0) return null;

        // 日志：输出 IK 开关信息
        for (const entry of timeline) {
            const states = [];
            for (const [name, enabled] of entry.ikStates) {
                states.push(`${name}=${enabled ? 'ON' : 'OFF'}`);
            }
            console.log(`[MMD Animation] IK 开关 Frame ${entry.frame}: ${states.join(', ')}`);
        }

        return timeline;
    }

    /**
     * 根据当前帧号，从 IK 开关时间轴中查找并应用对应的 IK 启用/禁用状态。
     * 使用"最近的不超过当前帧的 keyframe"（即 floor 查找）。
     */
    _applyIkSwitchState(currentFrame) {
        if (!this._ikSwitchTimeline || !this.ikSolver) return;

        const timeline = this._ikSwitchTimeline;
        const bones = this.manager.currentModel?.mesh?.skeleton?.bones;
        if (!bones) return;

        // 找到当前帧对应的 IK 开关状态（floor 查找）
        let activeEntry = null;
        for (let i = timeline.length - 1; i >= 0; i--) {
            if (timeline[i].frame <= currentFrame) {
                activeEntry = timeline[i];
                break;
            }
        }
        if (!activeEntry) return;

        // 避免重复应用同一个 entry
        if (this._lastAppliedIkEntry === activeEntry) return;
        this._lastAppliedIkEntry = activeEntry;

        const iks = this.ikSolver.iks;
        for (let i = 0; i < iks.length; i++) {
            const ik = iks[i];
            // IK 的 target 是 IK 骨骼在 skeleton.bones 中的索引
            const ikBoneName = bones[ik.target]?.name;
            if (!ikBoneName) continue;

            // 检查 VMD 中是否有这个 IK 的开关设置
            // 注意：VMD 中可能用半角（左足IK），模型中可能用全角（左足ＩＫ）
            let matched = false;
            let enabled = true;
            if (activeEntry.ikStates.has(ikBoneName)) {
                enabled = activeEntry.ikStates.get(ikBoneName);
                matched = true;
            } else {
                // 尝试半角/全角互转匹配
                const normalized = ikBoneName.normalize('NFKC'); // 全角→半角
                for (const [vmdName, vmdEnabled] of activeEntry.ikStates) {
                    const vmdNormalized = vmdName.normalize('NFKC');
                    if (vmdNormalized === normalized) {
                        enabled = vmdEnabled;
                        matched = true;
                        break;
                    }
                }
            }

            if (matched) {
                console.log(`[MMD Animation] IK "${ikBoneName}" → enabled=${enabled}`);
                // 设置 IK 链中所有 link 的 enabled 状态
                for (const link of ik.links) {
                    link.enabled = enabled;
                }
                // 同时在 ik 对象上标记整体启用状态（供 updateOne 检查）
                ik._ikEnabled = enabled;
            } else {
                console.warn(`[MMD Animation] IK "${ikBoneName}" 在 VMD IK 开关中未找到匹配`);
            }
        }
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
        this._ikSwitchTimeline = null;
        this._lastAppliedIkEntry = null;
        this.isPlaying = false;
        this.isPaused = false;
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
        // 仅当自己创建的 analyser 时才断开（外部传入的由外部管理）
        if (this._analyser && this._ownsAnalyser) {
            try { this._analyser.disconnect(); } catch (e) { /* ignore */ }
        }
        this._analyser = null;
        this._ownsAnalyser = false;
        if (this._audioContext && this._audioContext.state !== 'closed') {
            this._audioContext.close().catch(() => {});
            this._audioContext = null;
        }
        this._lipSyncEnabled = false;
        this._lipSyncActive = false;
        this._lipSyncAudioElement = null;

        console.log('[MMD Animation] 资源已清理');
    }
}
