/**
 * VRM Cursor Follow Controller
 * 实现「眼睛注视 + 头/脖子转动」跟踪鼠标光标
 *
 * 通道 A（眼睛）：高灵敏、低延迟 → vrm.lookAt.target
 * 通道 B（头部）：较慢惯性、小幅平滑 → neck/head 加成旋转
 *
 * 默认始终启用（无 UI 开关）。若 VRM 不支持相关骨骼/LookAt 则自动降级。
 */

// ─── 确保 THREE 可用 ────────────────────────────────────────────────
var THREE = (typeof window !== 'undefined' && window.THREE) ||
    (typeof globalThis !== 'undefined' && globalThis.THREE) || null;

// ─── 默认参数（集中配置，方便调参） ─────────────────────────────────
const CURSOR_FOLLOW_DEFAULTS = Object.freeze({
    // ── 死区 ──────────────────────────────────────────────
    deadzoneDeg: 1.2,                // 小于此角度变化不驱动

    // ── 眼睛通道 ──────────────────────────────────────────
    eyeMaxYawDeg: 30,
    eyeMaxPitchUpDeg: 18,
    eyeMaxPitchDownDeg: 14,
    eyeSmoothSpeed: 12.0,            // 指数阻尼速度（越大越跟手）
    eyeOneEuroMinCutoff: 1.5,        // One-Euro: 最小截止频率（越大越跟手、越不平滑）
    eyeOneEuroBeta: 0.5,             // One-Euro: 速度系数（越大快速运动越跟手）
    eyeOneEuroDCutoff: 1.0,

    // ── 头部通道 ──────────────────────────────────────────
    headMaxYawDeg: 20,
    headMaxPitchUpDeg: 12,
    headMaxPitchDownDeg: 10,
    headSmoothSpeed: 5.0,            // 比眼睛慢 → 实现"眼快头慢"
    headOneEuroMinCutoff: 0.8,
    headOneEuroBeta: 0.3,
    headOneEuroDCutoff: 1.0,

    // ── 头/颈分配 ─────────────────────────────────────────
    neckContribution: 0.6,           // 脖子承担 60%
    headContribution: 0.4,           // 头部承担 40%

    // ── 动作权重 ──────────────────────────────────────────
    headWeightIdle: 1.0,             // 无动画时（纯静止）
    headWeightIdleAnim: 0.7,         // 待机动画播放时（加成叠加，保留呼吸协调）
    headWeightAction: 0.0,           // 一次性动作播放时（完全让位）
    weightTransitionSec: 0.2,        // 权重过渡时间

    // ── 拖拽降权 ─────────────────────────────────────────
    reduceWhileDragging: true,       // 拖拽/右键 orbit 时降低 headWeight

    // ── 目标平面距离 ─────────────────────────────────────
    lookAtDistance: 2.4,             // 稳定平面到头部的距离（米）
});

// ─── One-Euro 滤波器 ────────────────────────────────────────────────
class OneEuroFilter {
    constructor(minCutoff, beta, dCutoff) {
        this.minCutoff = minCutoff;
        this.beta = beta;
        this.dCutoff = dCutoff;
        this._xPrev = null;
        this._dxPrev = 0;
        this._tPrev = null;
    }

    _alpha(te, cutoff) {
        const r = 2 * Math.PI * cutoff * te;
        return r / (r + 1);
    }

    filter(x, t) {
        if (this._tPrev === null) {
            this._xPrev = x;
            this._dxPrev = 0;
            this._tPrev = t;
            return x;
        }
        const te = t - this._tPrev;
        if (te <= 0) return this._xPrev;

        // 导数
        const ad = this._alpha(te, this.dCutoff);
        const dx = (x - this._xPrev) / te;
        const dxHat = ad * dx + (1 - ad) * this._dxPrev;

        // 自适应截止频率
        const cutoff = this.minCutoff + this.beta * Math.abs(dxHat);
        const a = this._alpha(te, cutoff);

        // 滤波值
        const xHat = a * x + (1 - a) * this._xPrev;

        this._xPrev = xHat;
        this._dxPrev = dxHat;
        this._tPrev = t;
        return xHat;
    }

    reset() {
        this._xPrev = null;
        this._dxPrev = 0;
        this._tPrev = null;
    }
}

// ─── CursorFollowController ─────────────────────────────────────────
class CursorFollowController {
    constructor() {
        this.manager = null;

        // ── 眼睛目标 Object3D ──
        this.eyesTarget = null;

        // ── 鼠标状态 ──
        this._rawMouseX = 0;
        this._rawMouseY = 0;
        this._hasPointerInput = false;  // 首次 pointermove 前不驱动跟踪
        this._ignoreMouseMoveUntil = 0;
        this._lastCanvasRect = null;
        this._lastCanvasRectReadAt = 0;

        // ── One-Euro 滤波器（NDC 层面） ──
        this._eyeFilterX = null;
        this._eyeFilterY = null;

        // ── 头部追踪状态 ──
        this._headYaw = 0;
        this._headPitch = 0;
        this._headFilterYaw = null;
        this._headFilterPitch = null;

        // ── 权重 ──
        this._headWeight = 1.0;
        this._targetHeadWeight = 1.0;

        // ── 计时 ──
        this._elapsedTime = 0;

        // ── 预分配临时对象（减少 GC） ──
        this._raycaster = null;
        this._ndcVec = null;
        this._desiredTargetPos = null;
        this._headWorldPos = null;
        this._plane = null;
        this._planeNormal = null;
        this._tempVec3A = null;
        this._tempVec3B = null;
        this._tempVec3C = null;
        this._tempVec3D = null;
        this._tempQuat = null;
        this._tempQuatB = null;
        this._tempQuatC = null;
        this._tempQuatD = null;
        this._tempQuatE = null;
        this._tempEuler = null;

        // ── 模型前方向符号（由 _detectModelForward() 动态检测） ──
        // VRM 0.x (worldZ>=0) → -1, VRM 1.0 (worldZ<0) → +1
        this._modelForwardZ = 1;

        // ── 事件处理器引用 ──
        this._onPointerMove = null;

        // ── 初始化标志 ──
        this._initialized = false;
    }

    // ════════════════════════════════════════════════════════════════
    //  初始化
    // ════════════════════════════════════════════════════════════════
    init(vrmManager) {
        if (!THREE) {
            console.warn('[CursorFollow] THREE.js 未加载，功能不可用');
            return;
        }
        this.manager = vrmManager;

        // 创建眼睛注视目标
        this.eyesTarget = new THREE.Object3D();
        this.eyesTarget.name = 'CursorFollowEyeTarget';
        if (vrmManager.scene) {
            vrmManager.scene.add(this.eyesTarget);
        }

        // 初始位置：头部前方
        const headPos = this._getHeadWorldPos();
        const camDir = new THREE.Vector3();
        if (vrmManager.camera) {
            camDir.subVectors(vrmManager.camera.position, headPos);
            if (camDir.lengthSq() < 1e-8) camDir.set(0, 0, 1);
            else camDir.normalize();
        } else {
            camDir.set(0, 0, 1);
        }
        this.eyesTarget.position.copy(headPos).addScaledVector(camDir, CURSOR_FOLLOW_DEFAULTS.lookAtDistance);

        // 预分配临时对象
        this._raycaster = new THREE.Raycaster();
        this._ndcVec = new THREE.Vector2();
        this._desiredTargetPos = this.eyesTarget.position.clone();
        this._headWorldPos = new THREE.Vector3();
        this._plane = new THREE.Plane();
        this._planeNormal = new THREE.Vector3();
        this._tempVec3A = new THREE.Vector3();
        this._tempVec3B = new THREE.Vector3();
        this._tempVec3C = new THREE.Vector3();
        this._tempVec3D = new THREE.Vector3();
        this._tempQuat = new THREE.Quaternion();
        this._tempQuatB = new THREE.Quaternion();
        this._tempQuatC = new THREE.Quaternion();
        this._tempQuatD = new THREE.Quaternion();
        this._tempQuatE = new THREE.Quaternion();
        this._tempEuler = new THREE.Euler();

        // 骨骼基准姿态快照（防止 premultiply 累加漂移）
        this._neckBaseQuat = new THREE.Quaternion();
        this._headBaseQuat = new THREE.Quaternion();

        // 初始化滤波器
        const D = CURSOR_FOLLOW_DEFAULTS;
        this._eyeFilterX = new OneEuroFilter(D.eyeOneEuroMinCutoff, D.eyeOneEuroBeta, D.eyeOneEuroDCutoff);
        this._eyeFilterY = new OneEuroFilter(D.eyeOneEuroMinCutoff, D.eyeOneEuroBeta, D.eyeOneEuroDCutoff);
        this._headFilterYaw = new OneEuroFilter(D.headOneEuroMinCutoff, D.headOneEuroBeta, D.headOneEuroDCutoff);
        this._headFilterPitch = new OneEuroFilter(D.headOneEuroMinCutoff, D.headOneEuroBeta, D.headOneEuroDCutoff);

        this._bindEvents();
        this._detectModelForward();
        this._initialized = true;
        console.log('[CursorFollow] 初始化完成');
    }

    // ════════════════════════════════════════════════════════════════
    //  事件绑定（可清理）
    // ════════════════════════════════════════════════════════════════
    _bindEvents() {
        this._onPointerMove = (e) => {
            const now = performance.now();
            if (e.type === 'mousemove' && now < this._ignoreMouseMoveUntil) {
                return;
            }
            if (e.type === 'pointermove') {
                // macOS / Safari 等环境下，pointermove 后常跟随合成 mousemove，短窗口去重即可。
                this._ignoreMouseMoveUntil = now + 40;
            }
            this._rawMouseX = e.clientX;
            this._rawMouseY = e.clientY;
            this._hasPointerInput = true;
        };

        // 同时监听 pointermove + mousemove，绑定到 window（非 document）
        // Electron 透明窗口事件转发可能只产生 mousemove；window 级别确保转发事件可达
        window.addEventListener('pointermove', this._onPointerMove, { passive: true });
        window.addEventListener('mousemove', this._onPointerMove, { passive: true });
    }

    _getCanvasRect(canvas) {
        const now = performance.now();
        if (!this._lastCanvasRect || (now - this._lastCanvasRectReadAt) > 120) {
            this._lastCanvasRect = canvas.getBoundingClientRect();
            this._lastCanvasRectReadAt = now;
        }
        return this._lastCanvasRect;
    }

    // ════════════════════════════════════════════════════════════════
    //  辅助：检测模型实际前方向
    //  基于 VRM 模型版本（由 vrm-core.js detectVRMVersion 从 GLTF
    //  extensionsUsed / meta 属性检测），不依赖 scene 世界旋转：
    //    VRM 1.0 → three-vrm 内部对 scene 做了 180° Y 翻转，forwardSign = +1
    //    VRM 0.x → forwardSign = -1
    // ════════════════════════════════════════════════════════════════
    _detectModelForward() {
        const vrmVersion = this.manager?.core?.vrmVersion;
        // VRM 1.0: three-vrm 内部已翻转 scene，forwardSign = -1
        // VRM 0.x: forwardSign = +1
        this._modelForwardZ = (vrmVersion === '1.0') ? -1 : 1;
        console.log(`[CursorFollow] 模型前方向检测: vrmVersion=${vrmVersion || 'unknown'}, forwardSign=${this._modelForwardZ}`);
    }

    // ════════════════════════════════════════════════════════════════
    //  辅助：获取头部世界坐标
    // ════════════════════════════════════════════════════════════════
    _getHeadWorldPos() {
        const vrm = this.manager?.currentModel?.vrm;
        if (vrm?.humanoid) {
            const headBone = vrm.humanoid.getRawBoneNode('head');
            if (headBone) {
                headBone.getWorldPosition(this._headWorldPos || (this._headWorldPos = new THREE.Vector3()));
                return this._headWorldPos;
            }
        }
        // 回退：使用 scene 位置 + 偏移
        if (vrm?.scene) {
            vrm.scene.getWorldPosition(this._headWorldPos || (this._headWorldPos = new THREE.Vector3()));
            this._headWorldPos.y += 1.4;
            return this._headWorldPos;
        }
        if (!this._headWorldPos) this._headWorldPos = new THREE.Vector3();
        this._headWorldPos.set(0, 1.4, 0);
        return this._headWorldPos;
    }

    // ════════════════════════════════════════════════════════════════
    //  判断当前是否处于"一次性动作播放中"（用于降权）
    //  待机动画不算"动作"，头部跟踪以较高权重加成叠加
    // ════════════════════════════════════════════════════════════════
    _isActionPlaying() {
        const anim = this.manager?.animation;
        if (!anim) return false;
        // 待机动画不降权
        if (anim.isIdleAnimation) return false;
        // 仅非 idle 的一次性动作才降权
        return anim.vrmaIsPlaying && anim.currentAction && anim.currentAction.isRunning();
    }

    // ════════════════════════════════════════════════════════════════
    //  判断当前是否处于"待机动画播放中"
    // ════════════════════════════════════════════════════════════════
    _isIdleAnimPlaying() {
        const anim = this.manager?.animation;
        if (!anim) return false;
        return anim.isIdleAnimation && anim.vrmaIsPlaying && anim.currentAction && anim.currentAction.isRunning();
    }

    // ════════════════════════════════════════════════════════════════
    //  判断是否正在拖拽/orbit
    // ════════════════════════════════════════════════════════════════
    _isDragging() {
        if (!CURSOR_FOLLOW_DEFAULTS.reduceWhileDragging) return false;
        return this.manager?.interaction?.isDragging === true;
    }

    // ════════════════════════════════════════════════════════════════
    //  updateTarget(delta) — 每帧更新眼睛目标位置
    //  调用时机：在 mixer.update 之前
    // ════════════════════════════════════════════════════════════════
    updateTarget(delta) {
        if (!this._initialized || !this.eyesTarget || !this.manager) return;
        // 首次 pointermove 前跳过，避免未知鼠标坐标导致首帧朝向异常
        if (!this._hasPointerInput) return;

        this._elapsedTime += delta;

        const D = CURSOR_FOLLOW_DEFAULTS;
        const camera = this.manager.camera;
        const canvas = this.manager.renderer?.domElement;
        if (!camera || !canvas) return;

        // ② 获取头部世界坐标
        const headPos = this._getHeadWorldPos();

        // ③ 屏幕坐标 → NDC
        const rect = this._getCanvasRect(canvas);
        if (!rect.width || !rect.height) return;

        const rawNdcX = ((this._rawMouseX - rect.left) / rect.width) * 2 - 1;
        const rawNdcY = -((this._rawMouseY - rect.top) / rect.height) * 2 + 1;

        // ④ One-Euro 滤波 NDC
        const filteredX = this._eyeFilterX.filter(rawNdcX, this._elapsedTime);
        const filteredY = this._eyeFilterY.filter(rawNdcY, this._elapsedTime);

        // ⑤ 射线与通过头部的平面求交
        this._ndcVec.set(filteredX, filteredY);
        this._raycaster.setFromCamera(this._ndcVec, camera);

        camera.getWorldDirection(this._planeNormal);
        this._planeNormal.negate(); // 平面法线朝向相机
        this._plane.setFromNormalAndCoplanarPoint(this._planeNormal, headPos);

        const hit = this._raycaster.ray.intersectPlane(this._plane, this._tempVec3A);
        if (hit) {
            this._desiredTargetPos.copy(hit);
        }

        // ⑦ 指数阻尼平滑
        const eyeAlpha = 1 - Math.exp(-delta * D.eyeSmoothSpeed);
        this.eyesTarget.position.lerp(this._desiredTargetPos, eyeAlpha);
    }

    // ════════════════════════════════════════════════════════════════
    //  applyHead(delta) — 每帧应用头/颈加成旋转
    //  调用时机：在 vrm.update(delta) 之后
    // ════════════════════════════════════════════════════════════════
    applyHead(delta) {
        if (!this._initialized || !this.manager) return;

        const vrm = this.manager?.currentModel?.vrm;
        if (!vrm?.humanoid) return;

        const D = CURSOR_FOLLOW_DEFAULTS;

        // ── 更新权重 ──
        this._updateHeadWeight(delta);
        if (this._headWeight < 0.001) return;

        // ── 获取骨骼 ──
        const neckBone = vrm.humanoid.getRawBoneNode('neck');
        const headBone = vrm.humanoid.getRawBoneNode('head');
        if (!neckBone && !headBone) return; // 降级：仅眼睛

        // ── 快照骨骼基准姿态（vrm.update 后的动画姿态） ──
        // 每帧从快照恢复后再叠加，避免 premultiply 累加漂移
        if (neckBone) this._neckBaseQuat.copy(neckBone.quaternion);
        if (headBone) this._headBaseQuat.copy(headBone.quaternion);

        // ── 参考位置 ──
        const refBone = headBone || neckBone;
        refBone.getWorldPosition(this._headWorldPos);

        // ── 目标方向（世界空间） ──
        const targetPos = this.eyesTarget.position;
        const dirWorld = this._tempVec3A.subVectors(targetPos, this._headWorldPos);

        // 方向向量足够大时才更新 yaw/pitch，否则保持上帧值
        // 注意：不能 return，否则骨骼旋转不应用会导致卡顿
        if (dirWorld.lengthSq() >= 0.001) {
            dirWorld.normalize();

            // ── 获取模型坐标系 ──
            const scene = vrm.scene;
            scene.getWorldQuaternion(this._tempQuat); // sceneWorldQuat

            // modelForward / modelUp / modelRight
            // 使用 _modelForwardZ 适配 VRM 0.x(-Z) 和 1.0(+Z) 的前方向差异
            const modelForward = this._tempVec3B.set(0, 0, this._modelForwardZ).applyQuaternion(this._tempQuat);
            const modelUp = this._tempVec3C.set(0, 1, 0).applyQuaternion(this._tempQuat);
            const modelRight = this._tempVec3D.crossVectors(modelUp, modelForward).normalize();

            // ── 分解方向到模型坐标系 ──
            const dx = dirWorld.dot(modelRight);
            const dy = dirWorld.dot(modelUp);
            const dz = dirWorld.dot(modelForward);

            // ── 原始 yaw / pitch ──
            let rawYaw = Math.atan2(-dx, Math.max(dz, 0.001));
            const horizLen = Math.sqrt(dx * dx + dz * dz);
            let rawPitch = Math.atan2(dy, Math.max(horizLen, 0.001));

            // ── One-Euro 滤波 ──
            const filteredYaw = this._headFilterYaw.filter(rawYaw, this._elapsedTime);
            const filteredPitch = this._headFilterPitch.filter(rawPitch, this._elapsedTime);

            // ── Clamp ──
            const maxYaw = D.headMaxYawDeg * (Math.PI / 180);
            const maxPitchUp = D.headMaxPitchUpDeg * (Math.PI / 180);
            const maxPitchDown = D.headMaxPitchDownDeg * (Math.PI / 180);

            const clampedYaw = THREE.MathUtils.clamp(filteredYaw, -maxYaw, maxYaw);
            const clampedPitch = THREE.MathUtils.clamp(filteredPitch, -maxPitchDown, maxPitchUp);

            // ── 指数阻尼平滑 ──
            const headAlpha = 1 - Math.exp(-delta * D.headSmoothSpeed);
            this._headYaw += (clampedYaw - this._headYaw) * headAlpha;
            this._headPitch += (clampedPitch - this._headPitch) * headAlpha;
        } else {
            // 方向向量过小时仍需获取 sceneQuat 供骨骼旋转使用
            vrm.scene.getWorldQuaternion(this._tempQuat);
        }

        // sceneQuat 始终指向 this._tempQuat（无论是否进入 if 分支都已赋值）
        const sceneQuat = this._tempQuat;

        const w = this._headWeight;

        // ── 对 neck 应用加成旋转 ──
        if (neckBone) {
            neckBone.quaternion.copy(this._neckBaseQuat); // 恢复基准姿态
            this._applyAdditiveRotation(
                neckBone, sceneQuat,
                this._headYaw * D.neckContribution * w,
                this._headPitch * D.neckContribution * w
            );
        }

        // ── 对 head 应用加成旋转 ──
        if (headBone) {
            headBone.quaternion.copy(this._headBaseQuat); // 恢复基准姿态
            this._applyAdditiveRotation(
                headBone, sceneQuat,
                this._headYaw * D.headContribution * w,
                this._headPitch * D.headContribution * w
            );
        }
    }

    // ════════════════════════════════════════════════════════════════
    //  核心：将 yaw/pitch 转换为骨骼本地空间偏移并 premultiply
    // ════════════════════════════════════════════════════════════════
    _applyAdditiveRotation(bone, sceneWorldQuat, yaw, pitch) {
        if (Math.abs(yaw) < 1e-6 && Math.abs(pitch) < 1e-6) return;

        // 构造模型空间偏移四元数（先 yaw 后 pitch → YXZ 顺序）
        this._tempEuler.set(pitch, yaw, 0, 'YXZ');
        const modelOffset = this._tempQuatB.setFromEuler(this._tempEuler);

        // 模型空间 → 世界空间
        //   worldOffset = sceneQuat * modelOffset * sceneQuat^-1
        const sceneQuatInv = this._tempQuatC.copy(sceneWorldQuat).invert();
        const worldOffset = this._tempQuatD
            .copy(sceneWorldQuat)
            .multiply(modelOffset)
            .multiply(sceneQuatInv);

        // 世界空间 → 骨骼父级本地空间
        //   localOffset = parentWorldQuat^-1 * worldOffset * parentWorldQuat
        const parentQuat = this._tempQuatE;
        if (bone.parent) {
            bone.parent.getWorldQuaternion(parentQuat);
        } else {
            parentQuat.identity();
        }
        // 计算: parentQuat^-1 * worldOffset * parentQuat
        // 注意：不能 in-place invert parentQuat，因为后面还要用
        const parentQuatInv = this._tempQuatC.copy(parentQuat).invert();
        const localOffset = parentQuatInv.multiply(worldOffset).multiply(parentQuat);

        // 加成旋转（premultiply = 在父空间叠加）
        bone.quaternion.premultiply(localOffset);
    }

    // ════════════════════════════════════════════════════════════════
    //  动画/拖拽感知权重
    // ════════════════════════════════════════════════════════════════
    _updateHeadWeight(delta) {
        const D = CURSOR_FOLLOW_DEFAULTS;

        // 目标权重（优先级：一次性动作 > 拖拽 > 待机动画 > 纯静止）
        if (this._isActionPlaying()) {
            this._targetHeadWeight = D.headWeightAction;       // 一次性动作 → 0
        } else if (this._isDragging()) {
            this._targetHeadWeight = 0.15;
        } else if (this._isIdleAnimPlaying()) {
            this._targetHeadWeight = D.headWeightIdleAnim;     // 待机动画 → 0.7（加成叠加）
        } else {
            this._targetHeadWeight = D.headWeightIdle;         // 纯静止 → 1.0
        }

        // 平滑过渡
        const speed = 1.0 / Math.max(0.01, D.weightTransitionSec);
        const alpha = 1 - Math.exp(-delta * speed);
        this._headWeight += (this._targetHeadWeight - this._headWeight) * alpha;
    }

    // ════════════════════════════════════════════════════════════════
    //  重置（模型切换时调用）
    // ════════════════════════════════════════════════════════════════
    reset() {
        this._headYaw = 0;
        this._headPitch = 0;
        this._headWeight = 1.0;
        this._targetHeadWeight = 1.0;
        this._elapsedTime = 0;

        if (this._eyeFilterX) this._eyeFilterX.reset();
        if (this._eyeFilterY) this._eyeFilterY.reset();
        if (this._headFilterYaw) this._headFilterYaw.reset();
        if (this._headFilterPitch) this._headFilterPitch.reset();

        // 重新检测新模型的前方向
        this._detectModelForward();

        // 重置眼睛目标到头部前方
        if (this.eyesTarget && this.manager?.camera) {
            const headPos = this._getHeadWorldPos();
            const camDir = new THREE.Vector3();
            camDir.subVectors(this.manager.camera.position, headPos);
            if (camDir.lengthSq() < 1e-8) camDir.set(0, 0, 1);
            else camDir.normalize();
            this.eyesTarget.position.copy(headPos).addScaledVector(camDir, CURSOR_FOLLOW_DEFAULTS.lookAtDistance);
            this._desiredTargetPos.copy(this.eyesTarget.position);
        }
    }

    // ════════════════════════════════════════════════════════════════
    //  销毁
    // ════════════════════════════════════════════════════════════════
    destroy() {
        // 移除事件监听（与 _bindEvents 对称）
        if (this._onPointerMove) {
            window.removeEventListener('pointermove', this._onPointerMove);
            window.removeEventListener('mousemove', this._onPointerMove);
            this._onPointerMove = null;
        }

        // 从场景移除目标对象
        if (this.eyesTarget?.parent) {
            this.eyesTarget.parent.remove(this.eyesTarget);
        }
        this.eyesTarget = null;

        // 清理预分配的 THREE.js 对象
        this._raycaster = null;
        this._ndcVec = null;
        this._desiredTargetPos = null;
        this._lastCanvasRect = null;
        this._headWorldPos = null;
        this._plane = null;
        this._planeNormal = null;
        this._tempVec3A = null;
        this._tempVec3B = null;
        this._tempVec3C = null;
        this._tempVec3D = null;
        this._tempQuat = null;
        this._tempQuatB = null;
        this._tempQuatC = null;
        this._tempQuatD = null;
        this._tempQuatE = null;
        this._tempEuler = null;
        this._neckBaseQuat = null;
        this._headBaseQuat = null;

        // 清理 One-Euro 滤波器实例
        this._eyeFilterX = null;
        this._eyeFilterY = null;
        this._headFilterYaw = null;
        this._headFilterPitch = null;

        this._initialized = false;
        this._hasPointerInput = false;
        this.manager = null;

        console.log('[CursorFollow] 已销毁');
    }
}

// ─── 全局导出 ───────────────────────────────────────────────────────
window.CursorFollowController = CursorFollowController;
