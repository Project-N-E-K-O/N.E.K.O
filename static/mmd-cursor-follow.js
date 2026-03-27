/**
 * MMD 鼠标跟踪模块 - 头部/眼睛跟随鼠标
 * 参考 hime-display MouseFocusHelper + vrm-cursor-follow.js
 *
 * MMD 模型的头部跟踪通过直接操作骨骼旋转实现（区别于 VRM 的 LookAt 系统）
 * 查找 "頭"(Head)/"首"(Neck) 骨骼，根据鼠标位置计算目标旋转
 */

var THREE = (typeof window !== 'undefined' && window.THREE) ||
    (typeof globalThis !== 'undefined' && globalThis.THREE) || null;

class MMDCursorFollow {
    constructor(manager) {
        this.manager = manager;

        this.enabled = true;
        this._rawMouseX = 0;
        this._rawMouseY = 0;
        this._hasPointerInput = false;

        // 骨骼引用（模型加载后查找）
        this._headBone = null;
        this._neckBone = null;

        // 基准四元数（每帧应用 cursor follow 前保存，用于防止累积旋转）
        this._headBaseQuat = null;
        this._neckBaseQuat = null;
        this._appliedLastFrame = false;

        // 预分配临时对象（减少 GC）
        this._tempEuler = null;
        this._tempQuat = null;
        this._raycaster = null;
        this._ndcVec = null;
        this._headWorldPos = null;
        this._tempVec3A = null;
        this._tempVec3B = null;
        this._tempVec3C = null;
        this._tempVec3D = null;

        // canvas rect 缓存
        this._lastCanvasRect = null;
        this._lastCanvasRectReadAt = 0;

        // 目标旋转（平滑插值目标）
        this._targetYaw = 0;
        this._targetPitch = 0;
        this._currentYaw = 0;
        this._currentPitch = 0;

        // 配置参数
        this.maxYawDeg = 30;      // 最大左右转角（度）
        this.maxPitchUpDeg = 20;  // 最大抬头角度
        this.maxPitchDownDeg = 15; // 最大低头角度
        this.smoothSpeed = 3.0;   // 平滑跟随速度（越大越跟手）
        this.lookAtDistance = 5.0; // 射线-球面交点球半径

        // 头/颈分配
        this.neckContribution = 0.4;
        this.headContribution = 0.6;

        // 事件监听
        this._pointerMoveHandler = null;

        // 动画播放时禁用
        this._disabledByAnimation = false;

        // 事件去重（Electron 透明窗口同时注入 pointermove + mousemove）
        this._ignoreMouseMoveUntil = 0;

        // 局部跟踪
        this._localTrackingEnabled = window.humanoidLocalTrackingEnabled === true;
        this._localTrackingMargin = 50; // 局部跟踪边界扩展（像素）
    }

    // ═══════════════════ 初始化 ═══════════════════

    init() {
        if (!THREE) return;

        this._tempEuler = new THREE.Euler();
        this._tempQuat = new THREE.Quaternion();
        this._headBaseQuat = new THREE.Quaternion();
        this._neckBaseQuat = new THREE.Quaternion();
        this._raycaster = new THREE.Raycaster();
        this._ndcVec = new THREE.Vector2();
        this._headWorldPos = new THREE.Vector3();
        this._tempVec3A = new THREE.Vector3();
        this._tempVec3B = new THREE.Vector3();
        this._tempVec3C = new THREE.Vector3();
        this._tempVec3D = new THREE.Vector3();

        this._findBones();
        this._setupEventListeners();
    }

    /**
     * 在模型骨骼中查找头部和颈部骨骼
     * MMD 模型的骨骼名称通常为日文
     */
    _findBones() {
        const mesh = this.manager.currentModel?.mesh;
        if (!mesh || !mesh.skeleton) return;

        const bones = mesh.skeleton.bones;

        // 精确匹配优先，避免 "頭先端" 误匹配为头骨
        const headExact = ['頭', 'head', 'Head', 'あたま'];
        const neckExact = ['首', 'neck', 'Neck', 'くび'];

        this._headBone = null;
        this._neckBone = null;

        // 优先精确匹配
        for (const bone of bones) {
            const name = bone.name;
            if (!this._headBone && headExact.some(n => name === n)) {
                this._headBone = bone;
            }
            if (!this._neckBone && neckExact.some(n => name === n)) {
                this._neckBone = bone;
            }
            if (this._headBone && this._neckBone) break;
        }

        // 回退：包含匹配（排除已知误匹配如 "頭先端"）
        if (!this._headBone || !this._neckBone) {
            const headExclude = ['先端', 'tip', 'Tip', 'end', 'End'];
            for (const bone of bones) {
                const name = bone.name;
                if (!this._headBone && headExact.some(n => name.includes(n)) &&
                    !headExclude.some(ex => name.includes(ex))) {
                    this._headBone = bone;
                }
                if (!this._neckBone && neckExact.some(n => name.includes(n))) {
                    this._neckBone = bone;
                }
                if (this._headBone && this._neckBone) break;
            }
        }

        if (this._headBone) {
            console.log('[MMD CursorFollow] 找到头部骨骼:', this._headBone.name);
        } else {
            console.warn('[MMD CursorFollow] 未找到头部骨骼，鼠标跟踪不可用');
        }
        if (this._neckBone) {
            console.log('[MMD CursorFollow] 找到颈部骨骼:', this._neckBone.name);
        }
    }

    _setupEventListeners() {
        this._pointerMoveHandler = (e) => {
            const now = performance.now();
            // 去重：pointermove 后常跟随合成 mousemove（Electron 透明窗口）
            if (e.type === 'mousemove' && now < this._ignoreMouseMoveUntil) return;
            if (e.type === 'pointermove') {
                this._ignoreMouseMoveUntil = now + 40;
            }
            this._rawMouseX = e.clientX;
            this._rawMouseY = e.clientY;
            this._hasPointerInput = true;
        };

        // 同时监听两种事件（Electron 透明窗口可能只产生 mousemove）
        window.addEventListener('pointermove', this._pointerMoveHandler, { passive: true });
        window.addEventListener('mousemove', this._pointerMoveHandler, { passive: true });
    }

    _getCanvasRect() {
        const canvas = this.manager.renderer?.domElement;
        if (!canvas) return null;
        const now = performance.now();
        if (!this._lastCanvasRect || (now - this._lastCanvasRectReadAt) > 120) {
            this._lastCanvasRect = canvas.getBoundingClientRect();
            this._lastCanvasRectReadAt = now;
        }
        return this._lastCanvasRect;
    }

    /**
     * 获取头骨（或颈骨）的世界坐标
     */
    _getHeadWorldPos() {
        const refBone = this._headBone || this._neckBone;
        if (!refBone) {
            this._headWorldPos.set(0, 10, 0);
            return this._headWorldPos;
        }
        refBone.getWorldPosition(this._headWorldPos);
        return this._headWorldPos;
    }

    // ═══════════════════ 帧更新 ═══════════════════

    update(delta) {
        if (!this.enabled || !THREE) return;
        if (this._disabledByAnimation) return;
        if (!this._headBone && !this._neckBone) return;
        if (!this._hasPointerInput) return;

        const camera = this.manager.camera;
        if (!camera) return;

        // ── 还原上帧 cursor follow 叠加的旋转，恢复到干净的基准姿态 ──
        if (this._appliedLastFrame) {
            if (this._neckBone) this._neckBone.quaternion.copy(this._neckBaseQuat);
            if (this._headBone) this._headBone.quaternion.copy(this._headBaseQuat);
        }

        // ── 保存当前帧的基准姿态（动画/物理后、cursor follow 前） ──
        if (this._neckBone) this._neckBaseQuat.copy(this._neckBone.quaternion);
        if (this._headBone) this._headBaseQuat.copy(this._headBone.quaternion);

        // ── 屏幕坐标 → NDC ──
        const rect = this._getCanvasRect();
        if (!rect || !rect.width || !rect.height) return;

        let localMouseX = this._rawMouseX;
        let localMouseY = this._rawMouseY;
        let isWithinLocalBounds = false;
        let boundsAvailable = false;

        // 局部跟踪：只在鼠标在模型边界范围内时跟随
        if (this._localTrackingEnabled && this.manager) {
            const bounds = this.manager.getModelScreenBounds();
            if (bounds) {
                boundsAvailable = true;
                const margin = this._localTrackingMargin;
                const clampedLeft = bounds.left - margin;
                const clampedRight = bounds.right + margin;
                const clampedTop = bounds.top - margin;
                const clampedBottom = bounds.bottom + margin;

                // 检查鼠标是否在边界范围内
                isWithinLocalBounds = this._rawMouseX >= clampedLeft &&
                                      this._rawMouseX <= clampedRight &&
                                      this._rawMouseY >= clampedTop &&
                                      this._rawMouseY <= clampedBottom;

                if (isWithinLocalBounds) {
                    localMouseX = this._rawMouseX;
                    localMouseY = this._rawMouseY;
                }
            }
        }

        // 如果未启用局部跟踪，或 bounds 无法获取（视为不可判定），使用原始坐标
        // 只有在确实取得到 bounds 并且鼠标位于 bounds 之外时才跳过跟踪
        this._isWithinLocalBounds = isWithinLocalBounds;

        // 局部跟踪时，只有在 bounds 可用且鼠标在边界外才跳过目标更新
        if (this._localTrackingEnabled && boundsAvailable && !isWithinLocalBounds) {
            // 跳过目标计算，但继续执行平滑插值和应用
            // 让骨骼保持当前朝向，不被动画系统覆盖
        } else {
            const ndcX = ((localMouseX - rect.left) / rect.width) * 2 - 1;
            const ndcY = -((localMouseY - rect.top) / rect.height) * 2 + 1;

            // ── 射线-球面求交：计算鼠标在头骨球面上的投影点 ──
            this._ndcVec.set(ndcX, ndcY);
            this._raycaster.setFromCamera(this._ndcVec, camera);

            const headPos = this._getHeadWorldPos();
            const ray = this._raycaster.ray;
            const R = this.lookAtDistance;

            // 射线-球面交点（优先使用远交点避免视线角度压缩）
            const oc = this._tempVec3C.subVectors(ray.origin, headPos);
            const b = oc.dot(ray.direction);
            const c = oc.lengthSq() - R * R;
            const h = b * b - c;

            if (h >= 0) {
                const s = Math.sqrt(h);
                const tNear = -b - s;
                const tFar = -b + s;
                let t = tFar;
                if (t < 0 && tNear >= 0) t = tNear;
                if (t >= 0) {
                    this._tempVec3A.copy(ray.origin).addScaledVector(ray.direction, t);
                } else {
                    ray.closestPointToPoint(headPos, this._tempVec3A);
                }
            } else {
                ray.closestPointToPoint(headPos, this._tempVec3A);
            }

            // ── 方向分解：使用相机坐标轴，保证左右/上下与屏幕一致 ──
            const dirWorld = this._tempVec3B.subVectors(this._tempVec3A, headPos);
            if (dirWorld.lengthSq() < 1e-8) {
                dirWorld.subVectors(camera.position, headPos);
            }
            if (dirWorld.lengthSq() >= 1e-8) {
                dirWorld.normalize();

                const baseRight = this._tempVec3A.set(1, 0, 0).applyQuaternion(camera.quaternion).normalize();
                const baseUp = this._tempVec3D.set(0, 1, 0).applyQuaternion(camera.quaternion).normalize();
                const baseForward = this._tempVec3C.set(0, 0, -1).applyQuaternion(camera.quaternion).normalize().negate();

                const dx = dirWorld.dot(baseRight);
                const dy = dirWorld.dot(baseUp);
                const dz = dirWorld.dot(baseForward);

                const rawYaw = Math.atan2(dx, dz);
                const horizLen = Math.sqrt(dx * dx + dz * dz);
                const rawPitch = Math.atan2(-dy, Math.max(horizLen, 1e-8));

                const maxYaw = this.maxYawDeg * (Math.PI / 180);
                const maxPitchUp = this.maxPitchUpDeg * (Math.PI / 180);
                const maxPitchDown = this.maxPitchDownDeg * (Math.PI / 180);

                this._targetYaw = THREE.MathUtils.clamp(rawYaw, -maxYaw, maxYaw);
                this._targetPitch = THREE.MathUtils.clamp(rawPitch, -maxPitchDown, maxPitchUp);
            }
        }

        // 平滑插值
        const t = 1 - Math.exp(-this.smoothSpeed * delta);
        this._currentYaw += (this._targetYaw - this._currentYaw) * t;
        this._currentPitch += (this._targetPitch - this._currentPitch) * t;

        // 应用旋转到骨骼（在干净的基准姿态上叠加）
        this._applyRotation();
        this._appliedLastFrame = true;
    }

    _applyRotation() {
        const euler = this._tempEuler;
        const offsetQuat = this._tempQuat;

        if (this._neckBone) {
            euler.set(
                this._currentPitch * this.neckContribution,
                this._currentYaw * this.neckContribution,
                0,
                'YXZ'
            );
            offsetQuat.setFromEuler(euler);
            // 基准姿态已在 update() 中恢复，这里安全叠加
            this._neckBone.quaternion.multiply(offsetQuat);
        }

        if (this._headBone) {
            euler.set(
                this._currentPitch * this.headContribution,
                this._currentYaw * this.headContribution,
                0,
                'YXZ'
            );
            offsetQuat.setFromEuler(euler);
            this._headBone.quaternion.multiply(offsetQuat);
        }
    }

    // ═══════════════════ 控制 ═══════════════════

    setEnabled(enabled) {
        this.enabled = enabled;
        if (!enabled) {
            this._restoreAndReset();
        }
    }

    setDisabledByAnimation(disabled) {
        this._disabledByAnimation = disabled;
        if (disabled) {
            this._restoreAndReset();
        }
    }

    /**
     * 设置局部跟踪是否启用
     * @param {boolean} enabled - 是否启用局部跟踪
     */
    setLocalTrackingEnabled(enabled) {
        this._localTrackingEnabled = enabled;
        window.humanoidLocalTrackingEnabled = enabled;
        console.log(`[MMD CursorFollow] 局部跟踪已${enabled ? '开启' : '关闭'}`);
    }

    /**
     * 获取局部跟踪是否启用
     * @returns {boolean}
     */
    isLocalTrackingEnabled() {
        return this._localTrackingEnabled === true;
    }

    /**
     * 还原骨骼到基准姿态并重置跟踪状态
     */
    _restoreAndReset() {
        if (this._appliedLastFrame) {
            if (this._neckBone && this._neckBaseQuat) {
                this._neckBone.quaternion.copy(this._neckBaseQuat);
            }
            if (this._headBone && this._headBaseQuat) {
                this._headBone.quaternion.copy(this._headBaseQuat);
            }
            this._appliedLastFrame = false;
        }
        this._currentYaw = 0;
        this._currentPitch = 0;
        this._targetYaw = 0;
        this._targetPitch = 0;
    }

    /**
     * 重新查找骨骼（模型加载后调用）
     */
    refresh() {
        this._findBones();
        this._currentYaw = 0;
        this._currentPitch = 0;
        this._targetYaw = 0;
        this._targetPitch = 0;
        this._appliedLastFrame = false;
    }

    /**
     * 从UI设置面板应用配置
     * @param {Object} config - { enabled, headYaw, headPitch, smoothSpeed }
     */
    applyConfig(config) {
        if (!config) return;
        if (config.enabled != null) this.setEnabled(config.enabled);
        if (config.headYaw != null) this.maxYawDeg = config.headYaw;
        if (config.headPitch != null) {
            this.maxPitchUpDeg = config.headPitch;
            this.maxPitchDownDeg = Math.round(config.headPitch * 0.75);
        }
        if (config.smoothSpeed != null) this.smoothSpeed = config.smoothSpeed;
    }

    // ═══════════════════ 清理 ═══════════════════

    dispose() {
        if (this._pointerMoveHandler) {
            window.removeEventListener('pointermove', this._pointerMoveHandler);
            window.removeEventListener('mousemove', this._pointerMoveHandler);
            this._pointerMoveHandler = null;
        }
        this._headBone = null;
        this._neckBone = null;
    }
}
