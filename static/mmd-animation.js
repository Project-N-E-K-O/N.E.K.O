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

        // ── 双 MixerSlot 架构（crossfade 支持） ──
        // 两个独立的动画槽位，交替用于 active / outgoing
        this._slotA = this._createEmptySlot();
        this._slotB = this._createEmptySlot();
        this._activeSlot = null;    // 指向当前 active 的 slot (A 或 B)
        this._outgoingSlot = null;  // 过渡期间指向正在淡出的 slot，非过渡时为 null

        // Crossfade 状态
        this._isCrossfading = false;
        this._blendWeight = 0.0;
        this._fadeDuration = 0.4;   // 默认过渡时长（秒）
        this._fadeElapsed = 0.0;    // 已经过的过渡时间

        // 绑定姿态备份（首次 skeleton.pose() 后保存，crossfade 时用于恢复干净的绑定姿态）
        this._bindPoseBackup = null;

        // 循环点过渡状态
        this._isLoopCrossfading = false;
        this._loopOutgoingSnapshot = null;
        this._loopFadeElapsed = 0.0;
        this._loopFadeDuration = 0.17;
        this._loopCrossfadeThreshold = 0.95; // dot product threshold

        // 模块缓存
        this._mmdModuleCache = null;
    }

    /**
     * 创建一个空的 MixerSlot 结构。
     * 每个 slot 包含运行一个完整动画流水线所需的全部状态。
     * @returns {MixerSlot} 所有字段初始化为 null 的 slot 对象
     */
    _createEmptySlot() {
        return {
            mixer: null,
            action: null,
            clip: null,
            ikSolver: null,
            grantSolver: null,
            boneBackup: null,
            ikSwitchTimeline: null,
            lastAppliedIkEntry: null,
            snapshot: null,
            vmdUrl: null,
        };
    }

    /**
     * 回收一个 MixerSlot 的所有资源，将字段重置为 null。
     * 保留 slot 对象引用本身以便复用。
     * 
     * 【关键】不调用 action.stop() 和 mixer.stopAllAction()，
     * 因为 Three.js 的 stop() 会触发 PropertyMixer.restoreOriginalState()，
     * 把共享 mesh 的骨骼重置到绑定姿态（_originals），导致 T-pose 闪烁。
     * 直接解除引用即可——mixer 和 action 会被 GC 回收。
     * @param {MixerSlot} slot - 要回收的 slot
     */
    _recycleSlot(slot) {
        if (slot.action) {
            // 不调用 action.stop()——它会触发 restoreOriginalState
            // 将 action 的 enabled 设为 false 并解除引用
            slot.action.enabled = false;
            slot.action = null;
        }
        if (slot.mixer) {
            // 不调用 mixer.stopAllAction()——同样会触发 restoreOriginalState
            // 直接解除引用，让 GC 回收
            slot.mixer = null;
        }
        slot.clip = null;
        slot.ikSolver = null;
        slot.grantSolver = null;
        slot.boneBackup = null;
        slot.ikSwitchTimeline = null;
        slot.lastAppliedIkEntry = null;
        slot.snapshot = null;
        slot.vmdUrl = null;
    }

    /**
     * 返回当前非 active 的那个 slot。
     * 如果 _activeSlot 为 null，默认返回 _slotA。
     * @returns {MixerSlot} 非 active 的 slot
     */
    _getInactiveSlot() {
        if (this._activeSlot === null) {
            return this._slotA;
        }
        return this._activeSlot === this._slotA ? this._slotB : this._slotA;
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

    async loadAnimation(vmdUrl, options = {}) {
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

        // 加载 VMD 文件（旧动画继续播放，不暴露 T-pose）
        const vmdLoader = new VMDLoader();
        const vmdObject = await new Promise((resolve, reject) => {
            vmdLoader.load(vmdUrl, resolve, undefined, reject);
        });
        if (requestId !== this._loadRequestId || this.manager.currentModel !== mmd) return null;

        // 解析 IK 开关时间轴
        const ikSwitchTimeline = this._buildIkSwitchTimeline(vmdObject.propertyKeyFrames);

        // 构建动画 Clip
        // 【关键】buildAnimation 内部用 bone.position.toArray() 作为 basePosition，
        // 把 VMD 的 position 偏移量加到当前骨骼 position 上。
        // 如果此时骨骼不是绑定姿态（outgoing 动画在播放），basePosition 就是错误的。
        // 必须在 buildAnimation 之前恢复绑定姿态。
        const hasActiveAnimation = !!(this._activeSlot && this._activeSlot.mixer);
        const needRestoreForBuild = this._bindPoseBackup && mmd.mesh.skeleton?.bones;
        let clip;
        if (needRestoreForBuild) {
            // 保存当前骨骼状态（outgoing 的动画状态）
            const savedPositions = mmd.mesh.skeleton.bones.map(b => b.position.clone());
            const savedQuaternions = mmd.mesh.skeleton.bones.map(b => b.quaternion.clone());
            // 恢复绑定姿态
            mmd.mesh.skeleton.bones.forEach((bone, i) => {
                if (this._bindPoseBackup[i]) {
                    bone.position.copy(this._bindPoseBackup[i].position);
                    bone.quaternion.copy(this._bindPoseBackup[i].quaternion);
                }
            });
            // 在绑定姿态上构建 clip
            clip = buildAnimation(vmdObject, mmd.mesh);
            // 恢复 outgoing 的动画状态
            mmd.mesh.skeleton.bones.forEach((bone, i) => {
                bone.position.copy(savedPositions[i]);
                bone.quaternion.copy(savedQuaternions[i]);
            });
        } else {
            clip = buildAnimation(vmdObject, mmd.mesh);
        }
        this._normalizeQuaternionTrackSigns(clip);

        // 判断过渡模式
        const fadeDuration = Math.max(0, Math.min(options.fadeDuration !== undefined ? options.fadeDuration : 0.4, 1.0));
        const immediate = options.immediate === true || fadeDuration === 0;

        // 如果 active slot 的 action 已被 stop（enabled=false），检查是否有 _stopSnapshot。
        // 有快照 → 走快照式 crossfade（outgoing 用固定快照，不跑 pipeline）
        // 无快照 → 强制硬切
        const activeDisabled = this._activeSlot?.action && !this._activeSlot.action.enabled;
        const hasStopSnapshot = activeDisabled && this._stopSnapshot;

        // ── 同步块开始（RAF 无法插入） ──

        if (hasActiveAnimation && !immediate && !activeDisabled) {
            // ═══ Crossfade 路径 ═══

            // 如果正在 crossfade，先完成当前过渡
            if (this._isCrossfading) {
                this._abortCrossfade();
            }

            // 跨 clip 同半球对齐
            this._alignClipToCurrentPose(clip, mmd.mesh);

            // 当前 active → outgoing
            this._outgoingSlot = this._activeSlot;

            // 初始化新 slot
            const newSlot = this._getInactiveSlot();

            // 【关键】在创建新 mixer/clipAction 之前，恢复骨骼到绑定姿态。
            if (this._bindPoseBackup && mmd.mesh.skeleton?.bones) {
                mmd.mesh.skeleton.bones.forEach((bone, i) => {
                    if (this._bindPoseBackup[i]) {
                        bone.position.copy(this._bindPoseBackup[i].position);
                        bone.quaternion.copy(this._bindPoseBackup[i].quaternion);
                    }
                });
            } else if (mmd.mesh.skeleton) {
                mmd.mesh.skeleton.pose();
            }

            newSlot.mixer = new THREE.AnimationMixer(mmd.mesh);
            newSlot.clip = clip;
            newSlot.action = newSlot.mixer.clipAction(clip);

            newSlot.action.setLoop(this.isLoop ? THREE.LoopRepeat : THREE.LoopOnce, Infinity);
            newSlot.action.clampWhenFinished = true;
            newSlot.ikSwitchTimeline = ikSwitchTimeline;
            newSlot.lastAppliedIkEntry = null;
            newSlot.vmdUrl = vmdUrl;

            // clipAction 已创建，绑定姿态的使命完成。
            // 在 await 之前恢复 outgoing 的动画状态，防止 await 期间
            // （isPlaying=false 时 update() 不跑）骨骼卡在绑定姿态被渲染。
            if (this._outgoingSlot?.boneBackup && mmd.mesh.skeleton?.bones) {
                this._restoreBonesFromSlot(this._outgoingSlot, mmd.mesh);
            }

            // 安全网：循环动画意外结束时自动重播
            newSlot.mixer.addEventListener('finished', (e) => {
                if (this.isLoop && this._activeSlot && e.action === this._activeSlot.action) {
                    console.warn('[MMD Animation] 循环动画意外结束，自动重播');
                    e.action.reset();
                    e.action.play();
                }
            });

            // 循环点过渡：监听 loop 事件
            newSlot.mixer.addEventListener('loop', (e) => {
                if (this._activeSlot && e.action === this._activeSlot.action) {
                    this._onLoopEvent(this._activeSlot, mmd.mesh);
                }
            });

            // IK 解算器（crossfade 路径）
            if (mmd.iks && mmd.iks.length > 0) {
                try {
                    // ★ C-D 延迟已关闭，当前测试 E3-F 延迟
                    // await new Promise(r => setTimeout(r, 2000));
                    const { CCDIKSolver } = await import('three/addons/animation/CCDIKSolver.js');
                    if (requestId !== this._loadRequestId || this.manager.currentModel !== mmd) return null;
                    newSlot.ikSolver = new CCDIKSolver(mmd.mesh, mmd.iks);
                    if (newSlot.ikSwitchTimeline) {
                        this._applyIkSwitchStateForSlot(newSlot, 0);
                    }
                } catch (e) {
                    console.warn('[MMD Animation] CCDIKSolver 不可用:', e);
                }
            }

            // Grant 解算器
            if (mmd.grants && mmd.grants.length > 0) {
                newSlot.grantSolver = new GrantSolver(mmd.mesh, mmd.grants);
            }

            // Pre-warm：应用第 0 帧
            if (this._bindPoseBackup && mmd.mesh.skeleton?.bones) {
                mmd.mesh.skeleton.bones.forEach((bone, i) => {
                    if (this._bindPoseBackup[i]) {
                        bone.position.copy(this._bindPoseBackup[i].position);
                        bone.quaternion.copy(this._bindPoseBackup[i].quaternion);
                    }
                });
            }
            newSlot.action.play();
            newSlot.mixer.update(0);

            // 初始化骨骼备份（Grant 之前！）
            this._initSlotBoneBackup(newSlot, mmd.mesh);

            if (newSlot.ikSolver) newSlot.ikSolver.update();
            if (newSlot.grantSolver) newSlot.grantSolver.update();
            mmd.mesh.updateMatrixWorld(true);

            // 设为 active
            this._activeSlot = newSlot;

            // 开始 crossfade
            this._beginCrossfade(fadeDuration);

            // crossfade 立即开始播放
            this.isPlaying = true;
            this.isPaused = false;
            if (!this.clock) {
                this.clock = new THREE.Clock();
            }
            this.clock.start();

            // 重置 cursorFollow
            this._resetCursorFollowEyes();

            this._processBones = processBones;

        } else if (hasStopSnapshot) {
            // ═══ 快照式 Crossfade 路径（stop 后恢复） ═══
            // outgoing 不跑 pipeline，直接用 _stopSnapshot 作为固定输出。
            // active 正常初始化和运行。

            // 清理旧 slot（不触发 restoreOriginalState）
            this._recycleSlot(this._slotA);
            this._recycleSlot(this._slotB);
            this._activeSlot = null;
            this._outgoingSlot = null;
            this._isCrossfading = false;
            this._blendWeight = 0.0;
            this._fadeElapsed = 0.0;

            // 清理循环点过渡状态，避免跨动画泄漏
            this._isLoopCrossfading = false;
            this._loopOutgoingSnapshot = null;
            this._loopFadeElapsed = 0.0;

            // 也清理 class-level
            if (this.currentAction) { this.currentAction.enabled = false; this.currentAction = null; }
            if (this.mixer) { this.mixer = null; }
            this.currentClip = null;
            this.ikSolver = null;
            this.grantSolver = null;
            this._boneBackup = null;
            this._ikSwitchTimeline = null;
            this._lastAppliedIkEntry = null;
            this.isPlaying = false;
            this.isPaused = false;
            if (this.clock) { this.clock.stop(); this.clock = null; }

            // 初始化新 slot
            const slot = this._slotA;
            slot.mixer = new THREE.AnimationMixer(mmd.mesh);
            slot.clip = clip;

            // 恢复绑定姿态给 clipAction
            if (this._bindPoseBackup && mmd.mesh.skeleton?.bones) {
                mmd.mesh.skeleton.bones.forEach((bone, i) => {
                    if (this._bindPoseBackup[i]) {
                        bone.position.copy(this._bindPoseBackup[i].position);
                        bone.quaternion.copy(this._bindPoseBackup[i].quaternion);
                    }
                });
            } else if (mmd.mesh.skeleton) {
                mmd.mesh.skeleton.pose();
            }

            slot.action = slot.mixer.clipAction(clip);
            slot.action.setLoop(this.isLoop ? THREE.LoopRepeat : THREE.LoopOnce, Infinity);
            slot.action.clampWhenFinished = true;
            slot.ikSwitchTimeline = ikSwitchTimeline;
            slot.lastAppliedIkEntry = null;
            slot.vmdUrl = vmdUrl;

            // 恢复 stop 快照到骨骼（await 前保持动画姿态）
            if (this._stopSnapshot && mmd.mesh.skeleton?.bones) {
                mmd.mesh.skeleton.bones.forEach((bone, i) => {
                    if (this._stopSnapshot[i]) {
                        bone.position.copy(this._stopSnapshot[i].position);
                        bone.quaternion.copy(this._stopSnapshot[i].quaternion);
                    }
                });
            }

            // 安全网 + loop 监听
            slot.mixer.addEventListener('finished', (e) => {
                if (this.isLoop && this._activeSlot && e.action === this._activeSlot.action) {
                    e.action.reset(); e.action.play();
                }
            });
            slot.mixer.addEventListener('loop', (e) => {
                if (this._activeSlot && e.action === this._activeSlot.action) {
                    this._onLoopEvent(this._activeSlot, mmd.mesh);
                }
            });

            // IK 解算器
            if (mmd.iks && mmd.iks.length > 0) {
                try {
                    const { CCDIKSolver } = await import('three/addons/animation/CCDIKSolver.js');
                    if (requestId !== this._loadRequestId || this.manager.currentModel !== mmd) return null;
                    slot.ikSolver = new CCDIKSolver(mmd.mesh, mmd.iks);
                    if (slot.ikSwitchTimeline) {
                        this._applyIkSwitchStateForSlot(slot, 0);
                    }
                } catch (e) {
                    console.warn('[MMD Animation] CCDIKSolver 不可用:', e);
                }
            }

            // Grant 解算器
            if (mmd.grants && mmd.grants.length > 0) {
                slot.grantSolver = new GrantSolver(mmd.mesh, mmd.grants);
            }

            // 恢复绑定姿态给 action.play()
            if (this._bindPoseBackup && mmd.mesh.skeleton?.bones) {
                mmd.mesh.skeleton.bones.forEach((bone, i) => {
                    if (this._bindPoseBackup[i]) {
                        bone.position.copy(this._bindPoseBackup[i].position);
                        bone.quaternion.copy(this._bindPoseBackup[i].quaternion);
                    }
                });
            }

            slot.action.play();
            slot.mixer.update(0);

            this._initSlotBoneBackup(slot, mmd.mesh);
            if (slot.ikSolver) slot.ikSolver.update();
            if (slot.grantSolver) slot.grantSolver.update();
            mmd.mesh.updateMatrixWorld(true);

            // 捕获 active 的快照
            this._captureSnapshot(slot, mmd.mesh);

            // 设为 active，用 _stopSnapshot 作为 outgoing 快照启动 crossfade
            this._activeSlot = slot;

            // 启动快照式 crossfade
            this._isCrossfading = true;
            this._blendWeight = 0.0;
            this._fadeElapsed = 0.0;
            this._fadeDuration = Math.max(0, Math.min(fadeDuration, 1.0));
            // 标记为快照模式——update() 中 outgoing 不跑 pipeline，直接用 _stopSnapshot
            this._snapshotCrossfade = true;

            this.isPlaying = true;
            this.isPaused = false;
            if (!this.clock) { this.clock = new THREE.Clock(); }
            this.clock.start();

            this._resetCursorFollowEyes();
            this._processBones = processBones;

            // 清除 _stopSnapshot 引用（已被 crossfade 接管）
            // 不在这里清——update() 还需要用。在 _completeCrossfade 中清。

        } else {
            // ═══ 首次加载 / 硬切路径 ═══

            // 清理所有 slot
            this._recycleSlot(this._slotA);
            this._recycleSlot(this._slotB);
            this._activeSlot = null;
            this._outgoingSlot = null;
            this._isCrossfading = false;
            this._blendWeight = 0.0;
            this._fadeElapsed = 0.0;

            // 清理循环点过渡状态，避免跨动画泄漏
            this._isLoopCrossfading = false;
            this._loopOutgoingSnapshot = null;
            this._loopFadeElapsed = 0.0;

            // 也清理旧的 class-level 状态（从旧 loadAnimation 迁移过来的）
            // 【关键】不调用 action.stop() / mixer.stopAllAction()——
            // 它们会触发 restoreOriginalState 重置骨骼到绑定姿态。
            if (this.currentAction) {
                this.currentAction.enabled = false;
                this.currentAction = null;
            }
            if (this.mixer) {
                // 不调用 stopAllAction，直接解除引用
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
            if (this.clock) { this.clock.stop(); this.clock = null; }

            // 初始化 slot A
            const slot = this._slotA;
            slot.mixer = new THREE.AnimationMixer(mmd.mesh);
            slot.clip = clip;
            slot.action = slot.mixer.clipAction(clip);
            slot.action.setLoop(this.isLoop ? THREE.LoopRepeat : THREE.LoopOnce, Infinity);
            slot.action.clampWhenFinished = true;
            slot.ikSwitchTimeline = ikSwitchTimeline;
            slot.lastAppliedIkEntry = null;
            slot.vmdUrl = vmdUrl;

            // 安全网
            slot.mixer.addEventListener('finished', (e) => {
                if (this.isLoop && this._activeSlot && e.action === this._activeSlot.action) {
                    console.warn('[MMD Animation] 循环动画意外结束，自动重播');
                    e.action.reset();
                    e.action.play();
                }
            });

            // 循环点过渡：监听 loop 事件
            slot.mixer.addEventListener('loop', (e) => {
                if (this._activeSlot && e.action === this._activeSlot.action) {
                    this._onLoopEvent(this._activeSlot, mmd.mesh);
                }
            });

            // IK 解算器（硬切路径）
            if (mmd.iks && mmd.iks.length > 0) {
                try {
                    const { CCDIKSolver } = await import('three/addons/animation/CCDIKSolver.js');
                    if (requestId !== this._loadRequestId || this.manager.currentModel !== mmd) return null;
                    slot.ikSolver = new CCDIKSolver(mmd.mesh, mmd.iks);
                    if (slot.ikSwitchTimeline) {
                        this._applyIkSwitchStateForSlot(slot, 0);
                    }
                } catch (e) {
                    console.warn('[MMD Animation] CCDIKSolver 不可用:', e);
                }
            }

            // Grant 解算器
            if (mmd.grants && mmd.grants.length > 0) {
                slot.grantSolver = new GrantSolver(mmd.mesh, mmd.grants);
            }

            // ── 以下是同步块，RAF 无法插入，不会暴露 T-pose ──
            // 重置骨骼到绑定姿态（必须在 await 之后，和 mixer.update(0) 在同一个同步块中）
            if (mmd.mesh.skeleton) mmd.mesh.skeleton.pose();

            // 保存绑定姿态（首次加载时 skeleton.pose() 是干净的，后续 crossfade 用这个备份）
            if (!this._bindPoseBackup && mmd.mesh.skeleton?.bones) {
                this._bindPoseBackup = mmd.mesh.skeleton.bones.map(bone => ({
                    position: bone.position.clone(),
                    quaternion: bone.quaternion.clone()
                }));
            }

            // 重置 cursorFollow
            this._resetCursorFollowEyes();

            this._processBones = processBones;

            if (!this.clock) {
                this.clock = new THREE.Clock();
            }

            // Pre-warm：应用第 0 帧
            slot.action.play();
            slot.mixer.update(0);

            // 初始化骨骼备份（Grant 之前！）
            this._initSlotBoneBackup(slot, mmd.mesh);

            if (slot.ikSolver) slot.ikSolver.update();
            if (slot.grantSolver) slot.grantSolver.update();
            mmd.mesh.updateMatrixWorld(true);

            // 设为 active
            this._activeSlot = slot;

            // 暂停，等待外部 play()
            slot.action.paused = true;
            this.clock.stop();
        }

        // 更新 backward-compatible 属性（供外部代码读取）
        this.mixer = this._activeSlot.mixer;
        this.currentAction = this._activeSlot.action;
        this.currentClip = this._activeSlot.clip;
        this.ikSolver = this._activeSlot.ikSolver;
        this.grantSolver = this._activeSlot.grantSolver;
        this._boneBackup = this._activeSlot.boneBackup;
        this._ikSwitchTimeline = this._activeSlot.ikSwitchTimeline;
        this._lastAppliedIkEntry = this._activeSlot.lastAppliedIkEntry;

        this.manager._isTPose = false;

        console.log('[MMD Animation] 动画加载完成:', vmdUrl,
            hasStopSnapshot ? `(快照式 crossfade ${fadeDuration}s)` :
            hasActiveAnimation && !immediate && !activeDisabled ? `(crossfade ${fadeDuration}s)` : '(直接加载)');

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

    // ═══════════════════ Crossfade 辅助 ═══════════════════

    _resetCursorFollowEyes() {
        if (this.manager?.cursorFollow) {
            this.manager.cursorFollow._eyeLastOffsetQuat?.identity();
            this.manager.cursorFollow._currentYaw = 0;
            this.manager.cursorFollow._currentPitch = 0;
            this.manager.cursorFollow._targetYaw = 0;
            this.manager.cursorFollow._targetPitch = 0;
        }
    }

    /**
     * 跨 clip 同半球对齐：把新 clip 每条 QuaternionKeyframeTrack 的关键帧
     * 整体翻到当前骨骼姿态的同半球（dot >= 0）。
     * 确保 crossfade 的 slerp 走最短旋转路径。
     * @param {THREE.AnimationClip} clip - 新动画 clip
     * @param {Object} mesh - 包含 skeleton.bones 的 mesh
     */
    _alignClipToCurrentPose(clip, mesh) {
        const THREE = window.THREE;
        if (!clip?.tracks || !THREE?.QuaternionKeyframeTrack || !mesh?.skeleton?.bones) return;

        // 建立骨骼名 → quaternion 的查找表
        const boneMap = new Map();
        for (const bone of mesh.skeleton.bones) {
            boneMap.set(bone.name, bone.quaternion);
        }

        const stride = 4;
        for (const track of clip.tracks) {
            if (!(track instanceof THREE.QuaternionKeyframeTrack)) continue;
            // MMD track name format: "boneName.quaternion"
            const boneName = track.name.split('.')[0];
            const bq = boneMap.get(boneName);
            if (!bq) continue;
            const v = track.values;
            if (!v || v.length < stride) continue;
            // Compare first keyframe with current bone quaternion
            const dot = bq.x * v[0] + bq.y * v[1] + bq.z * v[2] + bq.w * v[3];
            if (dot < 0) {
                // Flip entire track (internal consistency preserved by _normalizeQuaternionTrackSigns)
                for (let i = 0; i < v.length; i++) {
                    v[i] = -v[i];
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

    // ─── Slot-aware 骨骼缓存（crossfade 用） ───

    /**
     * 为指定 slot 初始化骨骼备份数组，从当前骨骼状态克隆。
     * @param {MixerSlot} slot - 目标 slot
     * @param {Object} mesh - 包含 skeleton.bones 的 mesh 对象
     */
    _initSlotBoneBackup(slot, mesh) {
        const THREE = window.THREE;
        if (!mesh?.skeleton?.bones || !THREE) return;
        slot.boneBackup = mesh.skeleton.bones.map(bone => ({
            position: bone.position.clone(),
            quaternion: bone.quaternion.clone()
        }));
    }

    /**
     * 将当前骨骼状态保存到指定 slot 的备份数组。
     * @param {MixerSlot} slot - 目标 slot
     * @param {Object} mesh - 包含 skeleton.bones 的 mesh 对象
     */
    _saveBonesToSlot(slot, mesh) {
        if (!slot.boneBackup || !mesh?.skeleton?.bones) return;
        mesh.skeleton.bones.forEach((bone, i) => {
            if (slot.boneBackup[i]) {
                slot.boneBackup[i].position.copy(bone.position);
                slot.boneBackup[i].quaternion.copy(bone.quaternion);
            }
        });
    }

    /**
     * 从指定 slot 的备份数组恢复骨骼状态。
     * @param {MixerSlot} slot - 源 slot
     * @param {Object} mesh - 包含 skeleton.bones 的 mesh 对象
     */
    _restoreBonesFromSlot(slot, mesh) {
        if (!slot.boneBackup || !mesh?.skeleton?.bones) return;
        mesh.skeleton.bones.forEach((bone, i) => {
            if (slot.boneBackup[i]) {
                bone.position.copy(slot.boneBackup[i].position);
                bone.quaternion.copy(slot.boneBackup[i].quaternion);
            }
        });
    }

    // ─── 骨骼快照（crossfade 混合用） ───

    /**
     * 捕获当前骨骼状态到 slot 的快照数组。
     * 快照在首次调用时分配，后续复用（对象池）。
     * @param {MixerSlot} slot - 目标 slot
     * @param {Object} mesh - 包含 skeleton.bones 的 mesh
     */
    _captureSnapshot(slot, mesh) {
        if (!mesh?.skeleton?.bones) return;
        const bones = mesh.skeleton.bones;
        if (!slot.snapshot || slot.snapshot.length !== bones.length) {
            const THREE = window.THREE;
            slot.snapshot = bones.map(bone => ({
                position: bone.position.clone(),
                quaternion: bone.quaternion.clone()
            }));
        } else {
            bones.forEach((bone, i) => {
                slot.snapshot[i].position.copy(bone.position);
                slot.snapshot[i].quaternion.copy(bone.quaternion);
            });
        }
    }

    /**
     * 将 slot 的快照写入实际骨骼。
     * @param {MixerSlot} slot - 源 slot
     * @param {Object} mesh - 包含 skeleton.bones 的 mesh
     */
    _applySnapshot(slot, mesh) {
        if (!slot.snapshot || !mesh?.skeleton?.bones) return;
        mesh.skeleton.bones.forEach((bone, i) => {
            if (slot.snapshot[i]) {
                bone.position.copy(slot.snapshot[i].position);
                bone.quaternion.copy(slot.snapshot[i].quaternion);
            }
        });
    }

    /**
     * 在骨骼层面混合两个快照的状态，结果直接写入实际骨骼。
     * position 使用 lerp，quaternion 使用 slerp。
     * @param {Array} outSnapshot - outgoing slot 的骨骼快照
     * @param {Array} inSnapshot - active slot 的骨骼快照
     * @param {number} weight - 混合权重 (0.0 = 完全 out, 1.0 = 完全 in)
     * @param {Object} mesh - 包含 skeleton.bones 的 mesh
     */
    _blendBones(outSnapshot, inSnapshot, weight, mesh) {
        if (!outSnapshot || !inSnapshot || !mesh?.skeleton?.bones) return;
        const bones = mesh.skeleton.bones;
        const len = Math.min(outSnapshot.length, inSnapshot.length, bones.length);
        for (let i = 0; i < len; i++) {
            bones[i].position.lerpVectors(outSnapshot[i].position, inSnapshot[i].position, weight);
            bones[i].quaternion.slerpQuaternions(outSnapshot[i].quaternion, inSnapshot[i].quaternion, weight);
        }
    }

    // ─── Crossfade 生命周期 ───

    /**
     * 开始 crossfade 过渡。
     * @param {number} fadeDuration - 过渡时长（秒）
     */
    _beginCrossfade(fadeDuration) {
        this._isCrossfading = true;
        this._blendWeight = 0.0;
        this._fadeElapsed = 0.0;
        this._fadeDuration = Math.max(0, Math.min(fadeDuration, 1.0)); // clamp to [0, 1.0]

        // 通知物理系统准备过渡（当前为 no-op，crossfade 的平滑混合不需要冻结物理）
        // 保留此钩子供未来扩展（例如：物理冻结、阻尼调整等）
    }

    /**
     * 完成 crossfade 过渡：回收 outgoing slot，恢复单 slot 运行。
     */
    _completeCrossfade() {
        this._blendWeight = 1.0;
        this._isCrossfading = false;
        this._fadeElapsed = 0.0;

        // 清理快照模式标记
        this._snapshotCrossfade = false;
        this._stopSnapshot = null;

        // 清理循环点过渡状态，避免跨动画泄漏
        this._isLoopCrossfading = false;
        this._loopOutgoingSnapshot = null;
        this._loopFadeElapsed = 0.0;

        if (this._outgoingSlot) {
            this._recycleSlot(this._outgoingSlot);
            this._outgoingSlot = null;
        }

        // 【关键】crossfade 完成后，active slot 的 boneBackup 可能是过时的
        // （crossfade 期间每帧都在更新，但最后一帧的 _blendBones 写入了混合后的
        // 骨骼状态，包含 IK/Grant 结果）。
        // 为了让下一帧 _restoreBonesFromSlot 恢复到正确的纯动画状态，
        // 这里让 active slot 单独跑一次 restore → mixer.update(0) → save，
        // 不推进时间（delta=0），只是刷新 boneBackup 到当前时间点的纯动画状态。
        const mesh = this.manager.currentModel?.mesh;
        if (this._activeSlot?.mixer && mesh) {
            this._restoreBonesFromSlot(this._activeSlot, mesh);
            this._activeSlot.mixer.update(0); // delta=0，不推进时间
            this._saveBonesToSlot(this._activeSlot, mesh);
            // 然后跑 IK/Grant 让骨骼回到完整状态
            mesh.updateMatrixWorld(true);
            if (this._activeSlot.ikSolver) {
                if (this._activeSlot.ikSwitchTimeline) {
                    const currentFrame = (this._activeSlot.mixer.time || 0) * 30;
                    this._applyIkSwitchStateForSlot(this._activeSlot, currentFrame);
                }
                this._activeSlot.ikSolver.update();
            }
            if (this._activeSlot.grantSolver) {
                this._activeSlot.grantSolver.update();
            }
        }

        // 不调用 physics.reset()——crossfade 的骨骼混合是平滑的，
        // 物理系统在 crossfade 期间已经在跟随平滑变化的骨骼。
        // physics.reset() 会清零物理模拟的惯性状态，反而导致跳变。
    }

    /**
     * 中断当前 crossfade：立即完成（snap weight to 1.0）。
     * 用于连续 loadAnimation 调用时先完成当前过渡。
     */
    _abortCrossfade() {
        if (!this._isCrossfading) return;
        this._completeCrossfade();
    }

    // ═══════════════════ 播放控制 ═══════════════════

    play() {
        const action = this._activeSlot?.action || this.currentAction;
        if (!action) return;
        action.paused = false;
        action.play();
        if (this.clock) {
            this.clock.start();
        } else {
            this.clock = new window.THREE.Clock();
            this.clock.start();
        }
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
        // 如果正在 crossfade，先完成
        if (this._isCrossfading) {
            this._abortCrossfade();
        }

        // 保存当前骨骼完整状态（含 IK/Grant 结果）到 _stopSnapshot。
        // 用于 stop 后 loadAnimation 走"快照式 crossfade"——
        // outgoing 不跑 pipeline，直接用这个快照作为固定输出。
        // 注意：这不是 boneBackup（boneBackup 必须保持 mixer.update 后、IK/Grant 前的语义）。
        const mesh = this.manager.currentModel?.mesh;
        if (mesh?.skeleton?.bones) {
            if (!this._stopSnapshot || this._stopSnapshot.length !== mesh.skeleton.bones.length) {
                this._stopSnapshot = mesh.skeleton.bones.map(bone => ({
                    position: bone.position.clone(),
                    quaternion: bone.quaternion.clone()
                }));
            } else {
                mesh.skeleton.bones.forEach((bone, i) => {
                    this._stopSnapshot[i].position.copy(bone.position);
                    this._stopSnapshot[i].quaternion.copy(bone.quaternion);
                });
            }
        }

        // 停止 active slot
        // 【关键】不调用 action.stop()——它会触发 restoreOriginalState 重置骨骼到绑定姿态。
        // 改为 paused + enabled=false，保持骨骼在最后一帧姿态。
        // 注意：不修改 boneBackup——它必须保持"mixer.update 之后、IK/Grant 之前"的语义。
        // crossfade 的 outgoing pipeline 会正确执行 restore → mixer.update → save → IK → Grant。
        if (this._activeSlot?.action) {
            this._activeSlot.action.paused = true;
            this._activeSlot.action.enabled = false;
        }

        // 也停止 class-level（兼容旧路径）
        if (this.currentAction) {
            this.currentAction.paused = true;
            this.currentAction.enabled = false;
        }

        if (this.clock) this.clock.stop();

        // 不调用 skeleton.pose()——保持最后一帧姿态

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
        this.isPaused = true;  // 设为 paused，防止渲染循环的静止状态 IK/Grant 在无 _restoreBones 的情况下双重应用
    }

    setLoop(loop) {
        const THREE = window.THREE;
        this.isLoop = loop;
        const action = this._activeSlot?.action || this.currentAction;
        if (action && THREE) {
            action.setLoop(loop ? THREE.LoopRepeat : THREE.LoopOnce, Infinity);
        }
    }

    setTimeScale(scale) {
        const action = this._activeSlot?.action || this.currentAction;
        if (action) {
            action.timeScale = scale;
        }
    }

    // ═══════════════════ 帧更新 ═══════════════════

    /**
     * 执行单个 MixerSlot 的完整动画流水线。
     * 顺序：restoreBones → mixer.update → saveBones → updateMatrixWorld → IK → Grant
     * @param {MixerSlot} slot - 要更新的 slot
     * @param {number} delta - 帧间隔时间
     * @param {Object} mesh - 模型 mesh
     */
    _updateSlotPipeline(slot, delta, mesh) {
        // 1. 恢复骨骼到上帧动画基准
        this._restoreBonesFromSlot(slot, mesh);

        // 2. AnimationMixer 更新
        slot.mixer.update(delta);

        // 3. 保存动画后的骨骼状态（Grant 之前！）
        this._saveBonesToSlot(slot, mesh);

        // 4. 更新世界矩阵
        mesh.updateMatrixWorld(true);

        // 5. IK 解算
        if (slot.ikSolver) {
            if (slot.ikSwitchTimeline) {
                const currentFrame = (slot.mixer.time || 0) * 30;
                this._applyIkSwitchStateForSlot(slot, currentFrame);
            }
            slot.ikSolver.update();
        }

        // 6. Grant 解算
        if (slot.grantSolver) {
            slot.grantSolver.update();
        }
    }

    update(delta) {
        if (!this.isPlaying) return;

        const mesh = this.manager.currentModel?.mesh;
        if (!mesh) return;

        if (this._isCrossfading && this._activeSlot) {
            // ── crossfade 路径 ──

            let outSnapshot;

            if (this._snapshotCrossfade && this._stopSnapshot) {
                // 快照模式：outgoing 不跑 pipeline，直接用 _stopSnapshot
                outSnapshot = this._stopSnapshot;
            } else if (this._outgoingSlot) {
                // 正常模式：outgoing 跑完整 pipeline
                try {
                    this._updateSlotPipeline(this._outgoingSlot, delta, mesh);
                } catch (e) {
                    console.warn('[MMD Animation] Outgoing slot pipeline error, completing crossfade:', e);
                    this._completeCrossfade();
                    return;
                }
                this._captureSnapshot(this._outgoingSlot, mesh);
                outSnapshot = this._outgoingSlot.snapshot;
            } else {
                // 没有 outgoing 也没有快照，直接完成
                this._completeCrossfade();
                return;
            }

            // 2. 更新 active slot 流水线
            this._updateSlotPipeline(this._activeSlot, delta, mesh);
            this._captureSnapshot(this._activeSlot, mesh);

            // 3. 推进混合权重
            this._fadeElapsed += delta;
            this._blendWeight = this._fadeDuration > 0
                ? Math.min(this._fadeElapsed / this._fadeDuration, 1.0)
                : 1.0;

            // 4. 骨骼混合
            this._blendBones(
                outSnapshot,
                this._activeSlot.snapshot,
                this._blendWeight,
                mesh
            );

            // 5. 检查完成
            if (this._blendWeight >= 1.0) {
                this._completeCrossfade();
            }

        } else if (this._activeSlot && this._activeSlot.mixer) {
            // ── 单 slot 路径（零额外开销） ──
            this._updateSlotPipeline(this._activeSlot, delta, mesh);

            // 循环点自过渡
            if (this._isLoopCrossfading && this._loopOutgoingSnapshot) {
                this._loopFadeElapsed += delta;
                const loopWeight = Math.min(this._loopFadeElapsed / this._loopFadeDuration, 1.0);
                // 捕获当前帧状态
                this._captureSnapshot(this._activeSlot, mesh);
                // 从 loop outgoing 向当前帧混合
                this._blendBones(this._loopOutgoingSnapshot, this._activeSlot.snapshot, loopWeight, mesh);
                if (loopWeight >= 1.0) {
                    this._isLoopCrossfading = false;
                    this._loopOutgoingSnapshot = null;
                }
            }

        } else if (this.mixer) {
            // ── 旧版兼容路径（loadAnimation 尚未迁移到 slot 时使用） ──
            this._restoreBones(mesh);
            this.mixer.update(delta);
            this._saveBones(mesh);
            mesh.updateMatrixWorld(true);

            if (this.ikSolver) {
                if (this._ikSwitchTimeline) {
                    const currentFrame = (this.mixer.time || 0) * 30;
                    this._applyIkSwitchState(currentFrame);
                }
                this.ikSolver.update();
            }
            if (this.grantSolver) {
                this.grantSolver.update();
            }
        }

        // 检查动画结束（非循环模式）— 适用于所有路径
        if (!this.isLoop) {
            const activeAction = this._activeSlot?.action || this.currentAction;
            const activeClip = this._activeSlot?.clip || this.currentClip;
            const activeMixer = this._activeSlot?.mixer || this.mixer;
            if (activeAction && activeClip && activeMixer) {
                if (activeMixer.time >= activeClip.duration) {
                    this.pause();
                    if (this.manager.core) {
                        this.manager.core.resetModelPose();
                    }
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

    /**
     * 根据当前帧号，从 slot 的 IK 开关时间轴中查找并应用对应的 IK 启用/禁用状态。
     * 与 _applyIkSwitchState 相同逻辑，但使用 slot-local 状态而非 class-level 状态。
     * @param {MixerSlot} slot - 包含 ikSwitchTimeline、ikSolver、lastAppliedIkEntry 的 slot
     * @param {number} currentFrame - 当前帧号
     */
    _applyIkSwitchStateForSlot(slot, currentFrame) {
        if (!slot.ikSwitchTimeline || !slot.ikSolver) return;

        const timeline = slot.ikSwitchTimeline;
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
        if (slot.lastAppliedIkEntry === activeEntry) return;
        slot.lastAppliedIkEntry = activeEntry;

        const iks = slot.ikSolver.iks;
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

    /**
     * 获取 slot 当前所有 IK 链的启用状态，返回可比较的字符串表示。
     * 用于判断两个 slot 的 IK 状态是否一致（优化：一致时只跑一套 IK）。
     * @param {MixerSlot} slot
     * @returns {string|null} IK 状态字符串，或 null（无 IK solver）
     */
    _getIkSwitchState(slot) {
        if (!slot.ikSolver || !slot.ikSolver.iks) return null;
        return slot.ikSolver.iks.map(ik => ik._ikEnabled !== false ? '1' : '0').join('');
    }

    // ═══════════════════ 循环点过渡 ═══════════════════

    /**
     * 测量循环动画首尾帧的骨骼姿态不连续程度。
     * 返回所有骨骼四元数 dot 积的最小值（越小 = 越不连续）。
     * @param {MixerSlot} slot - 包含 clip 的 slot
     * @param {Object} mesh - 包含 skeleton.bones 的 mesh
     * @returns {number} 最小 dot 积（0~1），1.0 = 完全连续
     */
    _measureLoopDiscontinuity(slot, mesh) {
        if (!slot.clip?.tracks || !mesh?.skeleton?.bones) return 1.0;
        const THREE = window.THREE;
        if (!THREE?.QuaternionKeyframeTrack) return 1.0;

        let minDot = 1.0;
        const stride = 4;
        for (const track of slot.clip.tracks) {
            if (!(track instanceof THREE.QuaternionKeyframeTrack)) continue;
            const v = track.values;
            if (!v || v.length < stride * 2) continue;
            // Compare first and last keyframe
            const lastIdx = v.length - stride;
            const dot = Math.abs(
                v[0] * v[lastIdx] + v[1] * v[lastIdx + 1] +
                v[2] * v[lastIdx + 2] + v[3] * v[lastIdx + 3]
            );
            if (dot < minDot) minDot = dot;
        }
        return minDot;
    }

    /**
     * 循环事件处理：检测循环点不连续性，必要时启动自过渡。
     * @param {MixerSlot} slot - 触发 loop 事件的 slot
     * @param {Object} mesh - 包含 skeleton.bones 的 mesh
     */
    _onLoopEvent(slot, mesh) {
        // 如果正在 inter-animation crossfade，不做 loop crossfade
        if (this._isCrossfading) return;

        const discontinuity = this._measureLoopDiscontinuity(slot, mesh);
        if (discontinuity < this._loopCrossfadeThreshold) {
            // 保存当前骨骼状态作为 loop outgoing
            if (!mesh?.skeleton?.bones) return;
            if (!this._loopOutgoingSnapshot) {
                this._loopOutgoingSnapshot = mesh.skeleton.bones.map(bone => ({
                    position: bone.position.clone(),
                    quaternion: bone.quaternion.clone()
                }));
            } else {
                mesh.skeleton.bones.forEach((bone, i) => {
                    if (this._loopOutgoingSnapshot[i]) {
                        this._loopOutgoingSnapshot[i].position.copy(bone.position);
                        this._loopOutgoingSnapshot[i].quaternion.copy(bone.quaternion);
                    }
                });
            }
            this._isLoopCrossfading = true;
            this._loopFadeElapsed = 0;
        }
    }

    // ═══════════════════ 清理 ═══════════════════

    _cleanupAnimation() {
        // 回收两个 slot
        this._recycleSlot(this._slotA);
        this._recycleSlot(this._slotB);
        this._activeSlot = null;
        this._outgoingSlot = null;

        // 重置 crossfade 状态
        this._isCrossfading = false;
        this._blendWeight = 0.0;
        this._fadeElapsed = 0.0;

        // 重置循环点过渡状态
        this._isLoopCrossfading = false;
        this._loopOutgoingSnapshot = null;
        this._loopFadeElapsed = 0.0;

        // 清理 class-level 兼容属性
        // 同 _recycleSlot：不调用 action.stop()/mixer.stopAllAction()，避免 restoreOriginalState
        if (this.currentAction) {
            this.currentAction.enabled = false;
            this.currentAction = null;
        }
        if (this.mixer) {
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
        this._bindPoseBackup = null;
        this._stopSnapshot = null;

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
