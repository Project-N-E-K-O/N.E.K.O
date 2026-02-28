/**
 * VRM Manager - 物理控制版 (修复更新顺序)
 */
class VRMManager {
    constructor() {
        this.scene = null;
        this.camera = null;
        this.renderer = null;
        this.currentModel = null;
        this.animationMixer = null;

        this.clock = (typeof window.THREE !== 'undefined') ? new window.THREE.Clock() : null;
        this.container = null;
        this._animationFrameId = null;
        this._uiUpdateLoopId = null;
        this.enablePhysics = true;
        this._lookAtTarget = null;
        this._mouseMoveHandler = null;
        this._mouseRaycaster = null;
        this._mouseNDC = null;
        this._lookAtHeadWorldPos = null;
        this._lookAtRayClosestPoint = null;
        this._lookAtDirection = null;
        this._lookAtBaseForward = null;
        this._lookAtBaseRight = null;
        this._lookAtBaseUp = null;
        this._lookAtWorldUp = null;
        this._lookAtDesiredPoint = null;
        // 跟随时间常数（秒）：值越大，跟随越慢、越平滑
        this._lookAtSmoothTime = 0.5;
        // 头部到视线目标的固定距离（球面半径，单位：米）
        this._lookAtDistance = 2.4;
        // 视线角度限制（度）：避免眼睛/头部打到极限位
        this._lookAtMaxYawDeg = 60;
        this._lookAtMaxPitchUpDeg = 35;
        this._lookAtMaxPitchDownDeg = 28;

        // 阴影资源引用（用于清理）
        this._shadowTexture = null;
        this._shadowMaterial = null;
        this._shadowGeometry = null;
        this._shadowMesh = null;

        // 模块作用域的 window 事件处理器数组（避免模块间冲突）：_coreWindowHandlers（VRMCore，如 resize）、_uiWindowHandlers（VRMUIButtons，如 resize, live2d-goodbye-click）、_initWindowHandlers（VRMInit，如 visibilitychange）
        this._coreWindowHandlers = [];
        this._uiWindowHandlers = [];
        this._initWindowHandlers = [];

        // CursorFollow 控制器（眼睛注视 + 头/脖子跟随）
        this._cursorFollow = null;
        this._mouseTrackingEnabled = window.mouseTrackingEnabled !== false; // 鼠标跟踪启用状态
        this._initThreePromise = null;
        this._isDisposed = false;
        this._activeLoadToken = 0;
        this._loadState = 'idle';
        this._isModelReadyForInteraction = false;

        // 向后兼容：保留 _windowEventHandlers 作为 core 的别名（避免破坏现有代码）；建议新代码使用 _coreWindowHandlers
        Object.defineProperty(this, '_windowEventHandlers', {
            get: () => this._coreWindowHandlers,
            set: (value) => { this._coreWindowHandlers = value; },
            enumerable: true,
            configurable: true
        });

        this._initModules();
    }

    _isLoadTokenActive(loadToken) {
        return this._activeLoadToken === loadToken;
    }

    _waitForSceneStability(scene, loadToken, options = {}) {
        const requiredStableFrames = options.requiredStableFrames || 2;
        const maxFrames = options.maxFrames || 24;
        const deltaThreshold = options.deltaThreshold || 0.02;
        const THREE = window.THREE;

        if (!scene || !THREE) {
            return Promise.resolve(false);
        }

        return new Promise((resolve) => {
            let stableFrames = 0;
            let frameCount = 0;
            let prevSize = null;

            const tick = () => {
                if (!this._isLoadTokenActive(loadToken) || !scene || !scene.parent) {
                    resolve(false);
                    return;
                }

                frameCount += 1;
                let size = null;
                try {
                    scene.updateMatrixWorld(true);
                    const box = new THREE.Box3().setFromObject(scene);
                    size = box.getSize(new THREE.Vector3());
                } catch (_) {
                    size = null;
                }

                const hasValidSize = size &&
                    Number.isFinite(size.x) &&
                    Number.isFinite(size.y) &&
                    Number.isFinite(size.z) &&
                    size.x > 0.001 &&
                    size.y > 0.001;
                const isStable = hasValidSize &&
                    prevSize &&
                    Math.abs(size.x - prevSize.x) <= deltaThreshold &&
                    Math.abs(size.y - prevSize.y) <= deltaThreshold &&
                    Math.abs(size.z - prevSize.z) <= deltaThreshold;

                if (isStable) {
                    stableFrames += 1;
                } else {
                    stableFrames = 0;
                }

                if (hasValidSize) {
                    prevSize = size.clone();
                }

                if ((hasValidSize && stableFrames >= requiredStableFrames) || frameCount >= maxFrames) {
                    resolve(!!hasValidSize);
                    return;
                }

                requestAnimationFrame(tick);
            };

            requestAnimationFrame(tick);
        });
    }

    _ensureMouseLookAtResources() {
        if (!window.THREE) return false;
        if (!this._mouseRaycaster) this._mouseRaycaster = new window.THREE.Raycaster();
        if (!this._mouseNDC) this._mouseNDC = new window.THREE.Vector2();
        if (!this._lookAtHeadWorldPos) this._lookAtHeadWorldPos = new window.THREE.Vector3();
        if (!this._lookAtRayClosestPoint) this._lookAtRayClosestPoint = new window.THREE.Vector3();
        if (!this._lookAtDirection) this._lookAtDirection = new window.THREE.Vector3();
        if (!this._lookAtBaseForward) this._lookAtBaseForward = new window.THREE.Vector3();
        if (!this._lookAtBaseRight) this._lookAtBaseRight = new window.THREE.Vector3();
        if (!this._lookAtBaseUp) this._lookAtBaseUp = new window.THREE.Vector3();
        if (!this._lookAtWorldUp) this._lookAtWorldUp = new window.THREE.Vector3(0, 1, 0);
        if (!this._lookAtDesiredPoint) this._lookAtDesiredPoint = new window.THREE.Vector3();
        return true;
    }

    _getLookAtHeadWorldPosition() {
        const headBone = this.currentModel?.vrm?.humanoid?.getNormalizedBoneNode('head');
        if (headBone) {
            headBone.updateMatrixWorld(true);
            headBone.getWorldPosition(this._lookAtHeadWorldPos);
            return this._lookAtHeadWorldPos;
        }
        if (this.currentModel?.vrm?.scene) {
            this.currentModel.vrm.scene.updateMatrixWorld(true);
            this.currentModel.vrm.scene.getWorldPosition(this._lookAtHeadWorldPos);
            this._lookAtHeadWorldPos.y += 1.4;
            return this._lookAtHeadWorldPos;
        }
        this._lookAtHeadWorldPos.set(0, 1.4, 0);
        return this._lookAtHeadWorldPos;
    }

    _clampLookAtDirectionByAngle(direction, headWorldPos) {
        if (!this.camera || !window.THREE) return;

        this._lookAtBaseForward.subVectors(this.camera.position, headWorldPos);
        if (this._lookAtBaseForward.lengthSq() < 1e-8) {
            this._lookAtBaseForward.set(0, 0, 1);
        } else {
            this._lookAtBaseForward.normalize();
        }

        this._lookAtBaseRight.crossVectors(this._lookAtWorldUp, this._lookAtBaseForward);
        if (this._lookAtBaseRight.lengthSq() < 1e-8) {
            this._lookAtBaseRight.set(1, 0, 0);
        } else {
            this._lookAtBaseRight.normalize();
        }
        this._lookAtBaseUp.crossVectors(this._lookAtBaseForward, this._lookAtBaseRight).normalize();

        const x = direction.dot(this._lookAtBaseRight);
        const y = direction.dot(this._lookAtBaseUp);
        const z = direction.dot(this._lookAtBaseForward);

        const yaw = Math.atan2(x, z);
        const horizontalLen = Math.sqrt(x * x + z * z);
        const pitch = Math.atan2(y, Math.max(1e-8, horizontalLen));

        const maxYaw = window.THREE.MathUtils.degToRad(this._lookAtMaxYawDeg);
        const maxPitchUp = window.THREE.MathUtils.degToRad(this._lookAtMaxPitchUpDeg);
        const maxPitchDown = window.THREE.MathUtils.degToRad(this._lookAtMaxPitchDownDeg);

        const clampedYaw = window.THREE.MathUtils.clamp(yaw, -maxYaw, maxYaw);
        const clampedPitch = window.THREE.MathUtils.clamp(pitch, -maxPitchDown, maxPitchUp);
        const cosPitch = Math.cos(clampedPitch);

        direction.copy(this._lookAtBaseRight).multiplyScalar(Math.sin(clampedYaw) * cosPitch)
            .addScaledVector(this._lookAtBaseUp, Math.sin(clampedPitch))
            .addScaledVector(this._lookAtBaseForward, Math.cos(clampedYaw) * cosPitch)
            .normalize();
    }

    _setLookAtTargetByMouse(clientX, clientY) {
        if (!this.camera || !this.renderer || !this._lookAtTarget) return;
        if (!this._ensureMouseLookAtResources()) return;

        const canvas = this.renderer.domElement;
        if (!canvas) return;

        const rect = canvas.getBoundingClientRect();
        if (!rect.width || !rect.height) return;

        this._mouseNDC.x = ((clientX - rect.left) / rect.width) * 2 - 1;
        this._mouseNDC.y = -((clientY - rect.top) / rect.height) * 2 + 1;

        this._mouseRaycaster.setFromCamera(this._mouseNDC, this.camera);
        const headWorldPos = this._getLookAtHeadWorldPosition();
        this._mouseRaycaster.ray.closestPointToPoint(headWorldPos, this._lookAtRayClosestPoint);
        this._lookAtDirection.subVectors(this._lookAtRayClosestPoint, headWorldPos);

        // 当鼠标刚好在头部投影附近时，回退到头部朝向相机方向，避免方向向量为零
        if (this._lookAtDirection.lengthSq() < 1e-8) {
            this._lookAtDirection.subVectors(this.camera.position, headWorldPos);
        }
        if (this._lookAtDirection.lengthSq() < 1e-8) return;

        this._lookAtDirection.normalize();
        this._clampLookAtDirectionByAngle(this._lookAtDirection, headWorldPos);
        this._lookAtDesiredPoint.copy(headWorldPos).addScaledVector(this._lookAtDirection, this._lookAtDistance);
    }

    _initMouseLookAtTracking() {
        if (!this.scene || !this.camera || !window.THREE) return;

        // ── 初始化 CursorFollowController（替代旧的 mousemove 跟踪） ──
        if (typeof window.CursorFollowController !== 'undefined') {
            if (!this._cursorFollow) {
                this._cursorFollow = new window.CursorFollowController();
            }
            if (!this._cursorFollow._initialized) {
                this._cursorFollow.init(this);
            }
            // 同步鼠标跟踪启用状态
            const isEnabled = Boolean(window.mouseTrackingEnabled);
            console.log(`[VRM] 鼠标跟踪检查: window.mouseTrackingEnabled=${window.mouseTrackingEnabled}, isEnabled=${isEnabled}`);
            if (this._cursorFollow.isEnabled() !== isEnabled) {
                this._cursorFollow.setEnabled(isEnabled);
            }
            // 同步内部状态
            this._mouseTrackingEnabled = isEnabled;
            // CursorFollow 拥有自己的 eyesTarget，旧 _lookAtTarget 不再需要
            return;
        }

        // ── 回退：旧的 lookAt 跟踪（CursorFollowController 未加载时） ──
        if (!this._ensureMouseLookAtResources()) return;

        if (!this._lookAtTarget) {
            this._lookAtTarget = new window.THREE.Object3D();
            this._lookAtTarget.name = 'VRMLookAtMouseTarget';
        }
        if (this._lookAtTarget.parent !== this.scene) {
            this.scene.add(this._lookAtTarget);
        }

        // 初始位置放在“头部->相机”方向的固定距离上，保持球面轨迹起点稳定
        const headWorldPos = this._getLookAtHeadWorldPosition();
        const initialDir = new window.THREE.Vector3().subVectors(this.camera.position, headWorldPos);
        if (initialDir.lengthSq() < 1e-8) {
            initialDir.set(0, 0, 1);
        } else {
            initialDir.normalize();
        }
        this._lookAtTarget.position.copy(headWorldPos).addScaledVector(initialDir, this._lookAtDistance);
        this._lookAtDesiredPoint.copy(this._lookAtTarget.position);

        if (!this._mouseMoveHandler) {
            this._mouseMoveHandler = (event) => {
                this._setLookAtTargetByMouse(event.clientX, event.clientY);
            };
            document.addEventListener('mousemove', this._mouseMoveHandler, { passive: true });
        }
    }

    _initModules() {
        if (!this.core && typeof window.VRMCore !== 'undefined') this.core = new window.VRMCore(this);
        if (!this.expression && typeof window.VRMExpression !== 'undefined') this.expression = new window.VRMExpression(this);
        if (!this.animation && typeof window.VRMAnimation !== 'undefined') {
            this.animation = new window.VRMAnimation(this);
        }
        if (!this.interaction && typeof window.VRMInteraction !== 'undefined') this.interaction = new window.VRMInteraction(this);
    }
    _createBlobShadowTexture() {
        // 在高 DPI 设备上使用更高分辨率，提升阴影质量
        const dpr = window.devicePixelRatio || 1;
        const baseSize = 64;
        const size = Math.max(baseSize, Math.round(baseSize * Math.min(dpr, 2)));

        const canvas = document.createElement('canvas');
        canvas.width = size;
        canvas.height = size;
        const ctx = canvas.getContext('2d');

        if (!ctx) {
            console.warn('[VRM Manager] 无法获取 2d context，返回透明纹理');
            return new window.THREE.CanvasTexture(canvas);
        }

        const center = size / 2;
        const radius = center;
        const gradient = ctx.createRadialGradient(center, center, 0, center, center, radius);
        gradient.addColorStop(0, 'rgba(0, 0, 0, 0.6)');
        gradient.addColorStop(0.5, 'rgba(0, 0, 0, 0.3)');
        gradient.addColorStop(1, 'rgba(0, 0, 0, 0)');

        ctx.fillStyle = gradient;
        ctx.fillRect(0, 0, size, size);

        const texture = new window.THREE.CanvasTexture(canvas);
        return texture;
    }

    _calculateAndAddShadow(result) {
        const SHADOW_SCALE_MULT = 0.5;
        const SHADOW_Y_OFFSET = 0.001;
        const FIX_CENTER_XZ = true;

        result.vrm.scene.updateMatrixWorld(true);

        const bodyBox = new window.THREE.Box3();
        let hasBodyMesh = false;

        result.vrm.scene.traverse((object) => {
            if (object.isSkinnedMesh) {
                object.updateMatrixWorld(true);
                const meshBox = new window.THREE.Box3();
                meshBox.setFromObject(object);
                bodyBox.union(meshBox);
                hasBodyMesh = true;
            }
        });

        if (!hasBodyMesh) {
            bodyBox.setFromObject(result.vrm.scene);
        }

        result.vrm.scene.updateMatrixWorld(true);
        const sceneInverseMatrix = result.vrm.scene.matrixWorld.clone().invert();
        const worldCorners = [
            new window.THREE.Vector3(bodyBox.min.x, bodyBox.min.y, bodyBox.min.z),
            new window.THREE.Vector3(bodyBox.max.x, bodyBox.min.y, bodyBox.min.z),
            new window.THREE.Vector3(bodyBox.min.x, bodyBox.max.y, bodyBox.min.z),
            new window.THREE.Vector3(bodyBox.max.x, bodyBox.max.y, bodyBox.min.z),
            new window.THREE.Vector3(bodyBox.min.x, bodyBox.min.y, bodyBox.max.z),
            new window.THREE.Vector3(bodyBox.max.x, bodyBox.min.y, bodyBox.max.z),
            new window.THREE.Vector3(bodyBox.min.x, bodyBox.max.y, bodyBox.max.z),
            new window.THREE.Vector3(bodyBox.max.x, bodyBox.max.y, bodyBox.max.z),
        ];
        const localBodyBox = new window.THREE.Box3();
        worldCorners.forEach(corner => {
            const localCorner = corner.clone().applyMatrix4(sceneInverseMatrix);
            localBodyBox.expandByPoint(localCorner);
        });

        // 获取包围盒尺寸（用于计算阴影大小，使用世界空间的尺寸）
        const bodySize = new window.THREE.Vector3();
        bodyBox.getSize(bodySize);

        // 计算阴影大小（使用身体宽度和深度的较大值作为基准）
        const shadowDiameter = Math.max(
            Math.max(bodySize.x, bodySize.z) * SHADOW_SCALE_MULT,
            0.3  // 最小尺寸保底
        );

        // 5. 清理之前的阴影资源（如果存在）
        this._disposeShadowResources();

        // 6. 创建阴影纹理和材质
        this._shadowTexture = this._createBlobShadowTexture();
        this._shadowMaterial = new window.THREE.MeshBasicMaterial({
            map: this._shadowTexture,
            transparent: true,
            opacity: 1.0,
            depthWrite: false,  // 不写入深度缓冲，避免遮挡模型
            side: window.THREE.DoubleSide
        });

        // 7. 创建阴影网格
        this._shadowGeometry = new window.THREE.PlaneGeometry(1, 1);
        this._shadowMesh = new window.THREE.Mesh(this._shadowGeometry, this._shadowMaterial);
        this._shadowMesh.rotation.x = -Math.PI / 2;  // 旋转到水平面
        this._shadowMesh.scale.set(shadowDiameter, shadowDiameter, 1);

        // 8. 计算阴影位置（使用多种回退策略）
        let shadowX = 0;
        let shadowY = 0;
        let shadowZ = 0;

        // 优先使用 humanoid 骨骼来精确定位（使用 getNormalizedBoneNode() API 保证 VRM0/VRM1 兼容性）
        if (result.vrm.humanoid) {
            try {
                // 优先使用脚趾骨骼（leftToes/rightToes），因为脚部骨骼在脚踝位置
                const leftToes = result.vrm.humanoid.getNormalizedBoneNode('leftToes');
                const rightToes = result.vrm.humanoid.getNormalizedBoneNode('rightToes');
                const leftFoot = result.vrm.humanoid.getNormalizedBoneNode('leftFoot');
                const rightFoot = result.vrm.humanoid.getNormalizedBoneNode('rightFoot');

                // 优先使用脚趾骨骼，如果不存在则使用脚部骨骼
                const leftTargetBone = leftToes || leftFoot;
                const rightTargetBone = rightToes || rightFoot;

                if (leftTargetBone && rightTargetBone) {
                    // 更新骨骼矩阵
                    leftTargetBone.updateMatrixWorld(true);
                    rightTargetBone.updateMatrixWorld(true);

                    // 获取两脚的世界位置
                    const leftFootPos = new window.THREE.Vector3();
                    const rightFootPos = new window.THREE.Vector3();
                    leftTargetBone.getWorldPosition(leftFootPos);
                    rightTargetBone.getWorldPosition(rightFootPos);

                    // 如果使用的是脚部骨骼（不是脚趾），需要向下偏移到脚底（脚部骨骼在脚踝，脚趾骨骼在脚底）
                    let leftBottomY = leftFootPos.y;
                    let rightBottomY = rightFootPos.y;

                    // 查找最低Y坐标的辅助函数
                    const findLowestY = (bone, currentY) => {
                        let lowest = currentY;
                        if (bone) {
                            bone.updateMatrixWorld(true);
                            const pos = new window.THREE.Vector3();
                            bone.getWorldPosition(pos);
                            if (pos.y < lowest) {
                                lowest = pos.y;
                            }
                            // 递归检查所有子骨骼
                            bone.children.forEach(child => {
                                lowest = findLowestY(child, lowest);
                            });
                        }
                        return lowest;
                    };

                    // 如果使用的是脚部骨骼，需要向下偏移（估算脚的长度）
                    if (!leftToes && leftFoot) {
                        leftBottomY = findLowestY(leftFoot, leftFootPos.y);
                    }

                    if (!rightToes && rightFoot) {
                        rightBottomY = findLowestY(rightFoot, rightFootPos.y);
                    }

                    // 转换为相对于 vrm.scene 的局部坐标
                    result.vrm.scene.updateMatrixWorld(true);
                    const currentSceneInverseMatrix = result.vrm.scene.matrixWorld.clone().invert();

                    // 将最低点转换为局部坐标
                    const leftBottomPos = new window.THREE.Vector3(leftFootPos.x, leftBottomY, leftFootPos.z);
                    const rightBottomPos = new window.THREE.Vector3(rightFootPos.x, rightBottomY, rightFootPos.z);
                    leftBottomPos.applyMatrix4(currentSceneInverseMatrix);
                    rightBottomPos.applyMatrix4(currentSceneInverseMatrix);

                    // Y轴：使用两脚中较低的 Y 值，确保阴影在脚底
                    shadowY = Math.min(leftBottomPos.y, rightBottomPos.y) + SHADOW_Y_OFFSET;

                    // X/Z轴：使用两脚的中点（如果 FIX_CENTER_XZ 为 false，当前固定为 true，此分支不会执行）
                    if (!FIX_CENTER_XZ) {
                        leftFootPos.applyMatrix4(currentSceneInverseMatrix);
                        rightFootPos.applyMatrix4(currentSceneInverseMatrix);
                        shadowX = (leftFootPos.x + rightFootPos.x) / 2;
                        shadowZ = (leftFootPos.z + rightFootPos.z) / 2;
                    }
                } else {
                    // 如果没有脚部骨骼，尝试使用 hips 骨骼
                    const hipsBone = result.vrm.humanoid.getNormalizedBoneNode('hips');
                    if (hipsBone) {
                        hipsBone.updateMatrixWorld(true);

                        const hipsPos = new window.THREE.Vector3();
                        hipsBone.getWorldPosition(hipsPos);

                        // 转换为局部坐标
                        result.vrm.scene.updateMatrixWorld(true);
                        const currentSceneInverseMatrix = result.vrm.scene.matrixWorld.clone().invert();
                        hipsPos.applyMatrix4(currentSceneInverseMatrix);

                        // 使用 hips 的 X/Z 位置（如果 FIX_CENTER_XZ 为 false，当前固定为 true，此分支不会执行）
                        if (!FIX_CENTER_XZ) {
                            shadowX = hipsPos.x;
                            shadowZ = hipsPos.z;
                        }

                        // Y轴：使用本地空间包围盒的最低点（因为 hips 在腰部，不是脚底）
                        shadowY = localBodyBox.min.y + SHADOW_Y_OFFSET;
                    } else {
                        // 如果连 hips 都没有，使用本地空间包围盒的最低点
                        shadowY = localBodyBox.min.y + SHADOW_Y_OFFSET;
                    }
                }
            } catch (e) {
                // 回退到使用本地空间包围盒
                shadowY = localBodyBox.min.y + SHADOW_Y_OFFSET;
            }
        } else {
            // 如果没有 humanoid，使用本地空间包围盒的最低点
            shadowY = localBodyBox.min.y + SHADOW_Y_OFFSET;
        }

        // 如果 FIX_CENTER_XZ 为 true，强制使用 (0, 0) 作为 X/Z
        if (FIX_CENTER_XZ) {
            shadowX = 0;
            shadowZ = 0;
        }

        // 9. 设置阴影位置
        this._shadowMesh.position.set(shadowX, shadowY, shadowZ);

        // 10. 添加到模型场景中
        result.vrm.scene.add(this._shadowMesh);
    }

    /**
     * 清理阴影资源（纹理、材质、几何体、网格）
     */
    _disposeShadowResources() {
        // 从场景中移除阴影网格
        if (this._shadowMesh) {
            if (this._shadowMesh.parent) {
                this._shadowMesh.parent.remove(this._shadowMesh);
            }
            this._shadowMesh = null;
        }

        // 清理几何体
        if (this._shadowGeometry) {
            this._shadowGeometry.dispose();
            this._shadowGeometry = null;
        }

        // 清理材质
        if (this._shadowMaterial) {
            if (this._shadowMaterial.map) {
                // 检查材质使用的纹理是否就是 _shadowTexture，避免双重释放
                if (this._shadowMaterial.map === this._shadowTexture) {
                    this._shadowMaterial.map.dispose();
                    this._shadowTexture = null;
                } else {
                    this._shadowMaterial.map.dispose();
                }
            }
            this._shadowMaterial.dispose();
            this._shadowMaterial = null;
        }

        // 清理纹理（如果材质没有清理它）
        if (this._shadowTexture) {
            this._shadowTexture.dispose();
            this._shadowTexture = null;
        }
    }

    async initThreeJS(canvasId, containerId, lightingConfig = null) {
        if (this._initThreePromise) {
            return await this._initThreePromise;
        }
        this._isDisposed = false;

        // 检查是否已完全初始化（不仅检查 scene，还要检查 camera 和 renderer）
        if (this.scene && this.camera && this.renderer) {
            this._initMouseLookAtTracking();
            this._isInitialized = true;
            return true;
        }

        this._initThreePromise = (async () => {
            if (!this.clock && window.THREE) this.clock = new window.THREE.Clock();
            this._initModules();
            if (!this.core) {
                const errorMsg = window.t ? window.t('vrm.error.coreNotLoaded') : 'VRMCore 尚未加载';
                throw new Error(errorMsg);
            }
            await this.core.init(canvasId, containerId, lightingConfig);
            if (this._isDisposed) return false;
            if (this.interaction) this.interaction.initDragAndZoom();
            this._initMouseLookAtTracking();
            this.startAnimateLoop();
            // 设置初始化标志
            this._isInitialized = true;
            return true;
        })();

        try {
            return await this._initThreePromise;
        } finally {
            this._initThreePromise = null;
        }
    }

    async ensureThreeReady(canvasId, containerId, lightingConfig = null) {
        if (this.scene && this.camera && this.renderer && this._isInitialized) {
            return true;
        }
        return await this.initThreeJS(canvasId, containerId, lightingConfig);
    }

    startAnimateLoop() {
        if (this._animationFrameId) cancelAnimationFrame(this._animationFrameId);
        this._lastRenderTime = 0;

        const animateLoop = () => {
            // 检查渲染器、场景和相机是否都存在，如果任何一个被 dispose 了则取消动画循环
            if (!this.renderer || !this.scene || !this.camera) {
                if (this._animationFrameId) {
                    cancelAnimationFrame(this._animationFrameId);
                    this._animationFrameId = null;
                }
                return;
            }

            this._animationFrameId = requestAnimationFrame(animateLoop);

            // 帧率限制：根据 targetFrameRate 跳帧
            const now = performance.now();
            const targetFps = window.targetFrameRate || 60;
            const frameInterval = 1000 / targetFps;
            if (now - this._lastRenderTime < frameInterval * 0.9) return;
            this._lastRenderTime = now;

            // 获取时间增量并限制最大值，防止切屏或卡顿导致物理"爆炸"
            let delta = this.clock ? this.clock.getDelta() : 0.016;
            delta = Math.min(delta, 0.05);

            if (!this.animation && typeof window.VRMAnimation !== 'undefined') this._initModules();

            if (this.currentModel && this.currentModel.vrm) {
                // 0. 主灯跟随相机
                if (this.mainLight && this.camera) {
                    const lightOffset = new window.THREE.Vector3(0.2, 0.5, 1.5);
                    lightOffset.applyQuaternion(this.camera.quaternion);
                    this.mainLight.position.copy(this.camera.position).add(lightOffset);

                    if (this.currentModel.vrm.scene) {
                        this.mainLight.target = this.currentModel.vrm.scene;
                    }
                }

                // 1. 表情更新
                if (this.expression) {
                    this.expression.update(delta);
                }

                // 2. CursorFollow：更新眼睛目标位置
                if (this._cursorFollow) {
                    this._cursorFollow.updateTarget(delta);
                }

                // 3. 设置 lookAt 目标
                if (this.currentModel.vrm.lookAt) {
                    if (this._cursorFollow && this._cursorFollow.eyesTarget) {
                        if (this.currentModel.vrm.lookAt.target !== this._cursorFollow.eyesTarget) {
                            this.currentModel.vrm.lookAt.target = this._cursorFollow.eyesTarget;
                        }
                    } else {
                        if (this._lookAtTarget && this._lookAtDesiredPoint) {
                            const smoothTime = Math.max(0.01, this._lookAtSmoothTime);
                            const alpha = Math.min(1, 1 - Math.exp(-delta / smoothTime));
                            this._lookAtTarget.position.lerp(this._lookAtDesiredPoint, alpha);
                        }
                        const fallbackLookAtTarget = this._lookAtTarget || this.camera;
                        if (this.currentModel.vrm.lookAt.target !== fallbackLookAtTarget) {
                            this.currentModel.vrm.lookAt.target = fallbackLookAtTarget;
                        }
                    }
                }

                // 4. 动画更新（mixer.update → VRMA 动作）
                if (this.animation) {
                    this.animation.update(delta);
                }

                // 5. VRM 核心更新（LookAt / SpringBone 物理）— 受画质设置影响
                // low: 仅 lookAt + expressions；medium: 隔帧物理；high: 每帧物理
                const quality = window.renderQuality || 'medium';
                if (this.enablePhysics && quality !== 'low') {
                    if (quality === 'medium') {
                        this._physicsFrameSkip = (this._physicsFrameSkip || 0) + 1;
                        if (this._physicsFrameSkip % 2 === 0) {
                            this.currentModel.vrm.update(delta * 2);
                        } else {
                            if (this.currentModel.vrm.lookAt) this.currentModel.vrm.lookAt.update(delta);
                            if (this.currentModel.vrm.expressionManager) this.currentModel.vrm.expressionManager.update(delta);
                        }
                    } else {
                        this.currentModel.vrm.update(delta);
                    }
                } else {
                    if (this.currentModel.vrm.lookAt) this.currentModel.vrm.lookAt.update(delta);
                    if (this.currentModel.vrm.expressionManager) this.currentModel.vrm.expressionManager.update(delta);
                }

                // 6. CursorFollow：头/颈加成旋转（在 vrm.update 之后，确保不被覆盖）
                if (this._cursorFollow) {
                    this._cursorFollow.applyHead(delta);
                }
            }

            // 7. 交互系统更新（浮动按钮跟随等）
            if (this.interaction) {
                this.interaction.update(delta);
            }

            // 8. 更新控制器
            if (this.controls) {
                this.controls.update();
            }

            // 9. 渲染场景
            if (this.renderer && this.scene && this.camera) {
                this.renderer.render(this.scene, this.camera);
            }
        };

        this._animationFrameId = requestAnimationFrame(animateLoop);
    }

    toggleSpringBone(enable) {
        this.enablePhysics = enable;
    }

    /**
     * 设置鼠标追踪性能档位
     * @param {'none'|'low'|'medium'|'high'} level
     */
    setCursorFollowPerformance(level = 'high') {
        const normalized = typeof level === 'string' ? level.toLowerCase() : 'high';
        const finalLevel = (normalized === 'none' || normalized === 'low' || normalized === 'medium' || normalized === 'high')
            ? normalized
            : 'high';

        if (this._cursorFollow && typeof this._cursorFollow.setPerformanceLevel === 'function') {
            this._cursorFollow.setPerformanceLevel(finalLevel);
        }
        // 保留全局状态，便于初始化前设置
        window.cursorFollowPerformanceLevel = finalLevel;
        return finalLevel;
    }

    /**
     * 获取当前鼠标追踪性能档位
     * @returns {'none'|'low'|'medium'|'high'}
     */
    getCursorFollowPerformance() {
        if (this._cursorFollow && typeof this._cursorFollow.getPerformanceLevel === 'function') {
            return this._cursorFollow.getPerformanceLevel();
        }
        return window.cursorFollowPerformanceLevel || 'high';
    }

    /**
     * 暂停渲染循环（用于节省资源，例如进入模型管理界面时）
     */
    pauseRendering() {
        if (this._animationFrameId) {
            cancelAnimationFrame(this._animationFrameId);
            this._animationFrameId = null;
            console.log('[VRM Manager] 渲染循环已暂停');
        }
    }

    /**
     * 恢复渲染循环（从暂停状态恢复）
     */
    resumeRendering() {
        if (!this._animationFrameId) {
            this.startAnimateLoop();
            console.log('[VRM Manager] 渲染循环已恢复');
        }
    }

    async loadModel(modelUrl, options = {}) {
        const loadToken = ++this._activeLoadToken;
        this._loadState = 'preparing';
        this._isModelReadyForInteraction = false;
        this._initModules();
        if (!this.core) this.core = new window.VRMCore(this);

        // 清理之前的阴影资源（如果存在旧模型）
        this._disposeShadowResources();

        // 确保场景已初始化
        if (!this.scene || !this.camera || !this.renderer) {
            const canvasId = options.canvasId || 'vrm-canvas';
            const containerId = options.containerId || 'vrm-container';

            const canvas = document.getElementById(canvasId);
            const container = document.getElementById(containerId);

            if (canvas && container) {
                const threeReady = await this.ensureThreeReady(canvasId, containerId);
                if (!threeReady) {
                    this._loadState = 'idle';
                    return null;
                }
            } else {
                const errorMsg = window.t
                    ? window.t('vrm.error.sceneNotInitialized')
                    : `无法加载模型：场景未初始化。找不到 canvas(#${canvasId}) 或 container(#${containerId})。`;
                throw new Error(errorMsg);
            }
        }


        // 先无过渡地立即隐藏画布，避免旧过渡导致加载期闪帧
        if (this.renderer && this.renderer.domElement) {
            this.renderer.domElement.style.transition = 'none';
            this.renderer.domElement.style.opacity = '0';
        }

        // 加载模型
        const result = await this.core.loadModel(modelUrl, options);
        if (!this._isLoadTokenActive(loadToken)) {
            this._loadState = 'idle';
            return result;
        }

        // 模型切换后重置头部跟踪状态（滤波器/权重/累计角度）
        if (this._cursorFollow) {
            this._cursorFollow.reset();
        }

        // 动态计算阴影位置和大小
        if (options.addShadow !== false && result && result.vrm && result.vrm.scene) {
            this._calculateAndAddShadow(result);
            result.vrm.scene.visible = false;
        }

        // 加载完保持 3D 对象不可见（防 T-Pose）
        if (result && result.vrm && result.vrm.scene) {
            result.vrm.scene.visible = false;
        }

        if (!this._animationFrameId) this.startAnimateLoop();

        // 获取默认循环动画路径：优先从 options 传入，其次从配置读取，最后使用默认值
        const DEFAULT_LOOP_ANIMATION = options.idleAnimation ||
            window.lanlan_config?.vrmIdleAnimation ||
            '/static/vrm/animation/wait03.vrma';

        // 确保 animation 模块已初始化
        if (!this.animation) {
            this._initModules();
        }

        // 辅助函数：显示模型并淡入画布
        const showAndFadeIn = () => {
            if (!this._isLoadTokenActive(loadToken)) return;
            if (this._loadState !== 'ready') return;
            if (this.currentModel?.vrm?.scene) {
                // 启用物理
                this.enablePhysics = true;

                // 更新世界矩阵，确保骨骼位置正确
                this.currentModel.vrm.scene.updateMatrixWorld(true);

                const springBoneManager = this.currentModel.vrm.springBoneManager;
                if (springBoneManager) {
                    // 记录信息（调试用）
                    const sceneRotY = this.currentModel.vrm.scene.rotation.y;
                    console.log('[VRM] Scene rotation Y:', sceneRotY);

                    // 减小碰撞体半径
                    // 原因：UniVRM 导出 bug (#673) 导致碰撞体普遍过大
                    // 50% 是经验值，修复所有测试模型
                    // 参考：https://github.com/vrm-c/UniVRM/issues/673
                    const COLLIDER_REDUCTION = 0.5;  // 可调整，经验默认值
                    const collidersSet = springBoneManager.colliders;
                    const colliders = collidersSet ? Array.from(collidersSet) : [];
                    colliders.forEach(collider => {
                        if (collider.shape?.radius !== undefined) {
                            // 保存真正的原始半径（只在第一次）
                            if (collider._rawOriginalRadius === undefined) {
                                collider._rawOriginalRadius = collider.shape.radius;
                            }
                            // 应用基础缩减
                            const reducedRadius = collider._rawOriginalRadius * COLLIDER_REDUCTION;
                            collider.shape.radius = reducedRadius;
                            // 保存缩减后的半径作为"工作半径"供场景缩放使用
                            collider._originalRadius = reducedRadius;
                        }
                    });
                    console.log(`[VRM] Reduced ${colliders.length} colliders by ${(1 - COLLIDER_REDUCTION) * 100}%`);
                }

                this.currentModel.vrm.scene.visible = true;
                requestAnimationFrame(() => {
                    if (!this._isLoadTokenActive(loadToken)) return;
                    if (this.renderer && this.renderer.domElement) {
                        this.renderer.domElement.style.transition = 'opacity 1.0s ease-in-out';
                        this.renderer.domElement.style.opacity = '1';
                    }
                });
            }
        };

        // 加载待机动画（作为 Promise，与场景稳定性并行等待）
        let animationReady = Promise.resolve();
        if (options.autoPlay !== false) {
            animationReady = (async () => {
                if (!this.currentModel || !this.currentModel.vrm) return;

                let retries = 10;
                while (retries > 0) {
                    if (!this._isLoadTokenActive(loadToken)) return;
                    if (!this.currentModel || !this.currentModel.vrm) return;

                    if (!this.animation) this._initModules();
                    if (this.animation) break;

                    if (typeof window.VRMAnimation !== 'undefined') {
                        this._initModules();
                        break;
                    }

                    await new Promise(resolve => {
                        this._retryTimerId = setTimeout(() => {
                            this._retryTimerId = null;
                            resolve();
                        }, 100);
                    });
                    retries--;
                }

                if (this._retryTimerId) {
                    clearTimeout(this._retryTimerId);
                    this._retryTimerId = null;
                }

                if (!this.animation) {
                    console.warn('[VRM Manager] VRMAnimation 模块未加载，跳过自动播放');
                    return;
                }
                const currentLoadToken = this._activeLoadToken;
                if (loadToken !== currentLoadToken) return;

                try {
                    if (!this._isLoadTokenActive(loadToken)) return;
                    await this.playVRMAAnimation(DEFAULT_LOOP_ANIMATION, {
                        loop: true,
                        immediate: true,
                        isIdle: true
                    });
                } catch (err) {
                    console.warn('[VRM Manager] 自动播放失败:', err);
                }
            })();
        }

        if (this.expression) {
            this.expression.setMood('neutral');
        }
        if (this.setupFloatingButtons) {
            this.setupFloatingButtons();
        }

        // 同时等待场景稳定和待机动画加载完成，确保模型不以 T-pose 显示
        this._loadState = 'settling';
        const stabilityPromise = (result && result.vrm && result.vrm.scene && this._isLoadTokenActive(loadToken))
            ? this._waitForSceneStability(result.vrm.scene, loadToken)
            : Promise.resolve(false);
        const [stabilityResult] = await Promise.all([stabilityPromise, animationReady]);

        if (this._isLoadTokenActive(loadToken) && stabilityResult === true) {
            this._loadState = 'ready';
            this._isModelReadyForInteraction = true;

            // 首次加载围栏：检查模型是否在屏幕外，如果是则立即校正（不动画）
            if (this.interaction && this.currentModel?.vrm?.scene) {
                try {
                    const currentPos = this.currentModel.vrm.scene.position.clone();
                    const correctedPos = this.interaction.clampModelPosition(currentPos, { minVisiblePixels: 300 });
                    if (!currentPos.equals(correctedPos)) {
                        this.currentModel.vrm.scene.position.copy(correctedPos);
                        console.log('[VRM Manager] 首次加载围栏已校正模型位置');
                    }
                } catch (e) {
                    console.warn('[VRM Manager] 首次加载围栏检查失败:', e);
                }
            }

            showAndFadeIn();
        } else if (this._isLoadTokenActive(loadToken)) {
            this._loadState = 'idle';
            this._isModelReadyForInteraction = false;
        }
        return result;
    }

    async playVRMAAnimation(url, opts) {
        if (!this.animation) this._initModules();
        if (this.animation) return this.animation.playVRMAAnimation(url, opts);
    }


    stopVRMAAnimation() {
        if (this.animation) this.animation.stopVRMAAnimation();
    }
    onWindowResize() {
        if (!this.camera || !this.renderer) return;

        let width, height;

        if (this.container && this.container.clientWidth > 0 && this.container.clientHeight > 0) {
            width = this.container.clientWidth;
            height = this.container.clientHeight;
        } else {
            width = window.innerWidth;
            height = window.innerHeight;
        }

        this.camera.aspect = width / height;
        this.camera.updateProjectionMatrix();
        this.renderer.setSize(width, height);
    }
    getCurrentModel() {
        return this.currentModel;
    }
    setModelPosition(x, y, z) {
        if (this.currentModel?.vrm?.scene) this.currentModel.vrm.scene.position.set(x, y, z);
    }

    /**
     * 设置模型缩放，同时同步缩放 SpringBone 碰撞体半径
     * @param {number} x - X 轴缩放
     * @param {number} y - Y 轴缩放  
     * @param {number} z - Z 轴缩放
     */
    setModelScale(x, y, z) {
        if (!this.currentModel?.vrm?.scene) return;

        // 使用统一缩放值（取 x 作为参考）
        const scaleFactor = x;

        // 1. 缩放场景
        this.currentModel.vrm.scene.scale.set(x, y, z);

        // 2. 同步缩放碰撞体半径
        const springBoneManager = this.currentModel.vrm.springBoneManager;
        if (springBoneManager) {
            const colliders = springBoneManager.colliders
                ? Array.from(springBoneManager.colliders)
                : [];

            colliders.forEach(collider => {
                if (collider.shape?.radius !== undefined) {
                    // 保存原始半径（只在第一次缩放时保存）
                    if (collider._originalRadius === undefined) {
                        collider._originalRadius = collider.shape.radius;
                    }
                    // 根据缩放因子调整半径
                    collider.shape.radius = collider._originalRadius * scaleFactor;
                }
            });

            console.log(`[VRM] Scaled ${colliders.length} colliders with factor ${scaleFactor.toFixed(3)}`);
        }
    }

    /**
     * 设置模型统一缩放（便捷方法）
     * @param {number} scale - 统一缩放值
     */
    setModelScaleScalar(scale) {
        this.setModelScale(scale, scale, scale);
    }

    /**
     * 完整清理 VRM 资源（用于模型切换）
     * 包括：取消动画循环、清理模型资源、清理场景/渲染器、重置初始化状态
     */
    async dispose() {
        console.log('[VRM Manager] 开始完整清理 VRM 资源...');
        this._isDisposed = true;

        // Invalidate any in-flight loadModel() async callbacks
        ++this._activeLoadToken;
        this._loadState = 'idle';
        this._isModelReadyForInteraction = false;

        // 1. 取消动画循环（最关键）
        if (this._animationFrameId) {
            cancelAnimationFrame(this._animationFrameId);
            this._animationFrameId = null;
        }

        // 2. 清理 UI 更新循环
        if (this._uiUpdateLoopId) {
            cancelAnimationFrame(this._uiUpdateLoopId);
            this._uiUpdateLoopId = null;
        }

        // 3. 清理重试定时器（loadModel 中的 tryPlayAnimation 重试）
        if (this._retryTimerId) {
            clearTimeout(this._retryTimerId);
            this._retryTimerId = null;
        }

        // 4. 清理阴影资源
        this._disposeShadowResources();

        // 5. 清理模型资源（调用 core.disposeVRM）
        if (this.core && typeof this.core.disposeVRM === 'function') {
            await this.core.disposeVRM();
            // 若 dispose 期间发生了重新初始化，则终止本次清理，避免清掉新实例
            if (!this._isDisposed) return;
        }

        // 6. 清理动画模块（先停止动画，再清理资源）
        if (this.animation) {
            if (typeof this.animation.stopVRMAAnimation === 'function') {
                this.animation.stopVRMAAnimation();
            }
            if (typeof this.animation.dispose === 'function') {
                this.animation.dispose();
            }
        }

        // 7. 清理交互模块的定时器
        if (this.interaction) {
            if (this.interaction._hideButtonsTimer) {
                clearTimeout(this.interaction._hideButtonsTimer);
                this.interaction._hideButtonsTimer = null;
            }
            if (this.interaction._savePositionDebounceTimer) {
                clearTimeout(this.interaction._savePositionDebounceTimer);
                this.interaction._savePositionDebounceTimer = null;
            }
            // 清理交互模块的初始化定时器
            if (this.interaction._initTimerId) {
                clearTimeout(this.interaction._initTimerId);
                this.interaction._initTimerId = null;
            }
            // 清理交互模块的拖拽和缩放事件监听器
            if (typeof this.interaction.cleanupDragAndZoom === 'function') {
                this.interaction.cleanupDragAndZoom();
            }
        }

        // 7.5 清理 CursorFollow 控制器
        if (this._cursorFollow) {
            this._cursorFollow.destroy();
            this._cursorFollow = null;
        }

        // 8. 清理场景中的所有对象（包括灯光）
        if (this._mouseMoveHandler) {
            document.removeEventListener('mousemove', this._mouseMoveHandler);
            this._mouseMoveHandler = null;
        }
        if (this._lookAtTarget && this._lookAtTarget.parent) {
            this._lookAtTarget.parent.remove(this._lookAtTarget);
        }
        this._lookAtTarget = null;
        this._mouseRaycaster = null;
        this._mouseNDC = null;
        this._lookAtHeadWorldPos = null;
        this._lookAtRayClosestPoint = null;
        this._lookAtDirection = null;
        this._lookAtBaseForward = null;
        this._lookAtBaseRight = null;
        this._lookAtBaseUp = null;
        this._lookAtWorldUp = null;
        this._lookAtDesiredPoint = null;

        if (this.scene) {
            // 遍历并清理所有子对象
            while (this.scene.children.length > 0) {
                const child = this.scene.children[0];
                this.scene.remove(child);

                // 如果是可清理的对象，调用 dispose
                if (child.geometry) child.geometry.dispose();
                if (child.material) {
                    // 辅助函数：清理单个材质的纹理资源
                    const disposeMaterialTextures = (material) => {
                        const textureKeys = ['map', 'normalMap', 'aoMap', 'emissiveMap', 'metalnessMap', 'roughnessMap'];
                        textureKeys.forEach(key => {
                            if (material[key] && typeof material[key].dispose === 'function') {
                                material[key].dispose();
                            }
                        });
                    };

                    if (Array.isArray(child.material)) {
                        child.material.forEach(m => {
                            disposeMaterialTextures(m);
                            m.dispose();
                        });
                    } else {
                        disposeMaterialTextures(child.material);
                        child.material.dispose();
                    }
                }
            }
        }

        // 9. 清理渲染器（但不销毁 canvas，因为后续可能还要用）
        if (this.renderer) {
            // 清理所有纹理
            this.renderer.dispose();
            // 重置 canvas 样式
            if (this.renderer.domElement) {
                this.renderer.domElement.style.display = 'none';
                this.renderer.domElement.style.opacity = '0';
            }
        }

        // 10. 清理轨道控制器
        if (this.controls) {
            if (typeof this.controls.dispose === 'function') {
                this.controls.dispose();
            }
            this.controls = null;
        }

        // 11. 清理 UI 元素（浮动按钮、锁图标等）
        if (typeof this.cleanupUI === 'function') {
            this.cleanupUI();
        }

        // 11.5. 关闭所有设置窗口并清理定时器（防止定时器泄漏）
        if (typeof this.closeAllSettingsWindows === 'function') {
            this.closeAllSettingsWindows();
        }

        // 清理 vrm-init.js 中的 visibilitychange 监听器
        if (typeof window.cleanupVRMInit === 'function') {
            window.cleanupVRMInit();
        }

        // 清理 window 事件监听器（按模块分别清理，避免模块间冲突）
        // 清理 Core 模块的 handlers（VRMCore.init() 中注册的 resize 监听器等）
        if (this._coreWindowHandlers && this._coreWindowHandlers.length > 0) {
            this._coreWindowHandlers.forEach(({ event, handler }) => {
                window.removeEventListener(event, handler);
            });
            this._coreWindowHandlers = [];
        }

        // 清理 UI 模块的 handlers（VRMUIButtons 中注册的 resize, live2d-goodbye-click 等）
        // 注意：UI 模块有自己的 cleanupUI() 方法，但这里也清理以确保完整性
        if (this._uiWindowHandlers && this._uiWindowHandlers.length > 0) {
            this._uiWindowHandlers.forEach(({ event, handler }) => {
                window.removeEventListener(event, handler);
            });
            this._uiWindowHandlers = [];
        }

        // 清理 Init 模块的 handlers（vrm-init.js 中的 visibilitychange 等）
        // 注意：Init 模块有自己的 cleanupVRMInit() 方法，但这里也清理以确保完整性
        if (this._initWindowHandlers && this._initWindowHandlers.length > 0) {
            this._initWindowHandlers.forEach(({ event, handler }) => {
                window.removeEventListener(event, handler);
            });
            this._initWindowHandlers = [];
        }

        // 12. 重置引用和状态
        this.currentModel = null;
        this.animationMixer = null;
        this.scene = null;
        this.camera = null;
        this.renderer = null;
        this.ambientLight = null;
        this.directionalLight = null;
        this.spotLight = null;
        this.canvas = null;
        this.container = null;

        // 13. 重置初始化标志（确保下次切回 VRM 时会重新初始化）
        this._isInitialized = false;

        console.log('[VRM Manager] VRM 资源清理完成');
    }

    /**
     * 设置鼠标跟踪是否启用
     * @param {boolean} enabled - 是否启用鼠标跟踪
     */
    setMouseTrackingEnabled(enabled) {
        this._mouseTrackingEnabled = enabled;
        window.mouseTrackingEnabled = enabled;

        if (this._cursorFollow) {
            this._cursorFollow.setEnabled(enabled);
        }
    }

    /**
     * 获取鼠标跟踪是否启用
     * @returns {boolean}
     */
    isMouseTrackingEnabled() {
        return this._mouseTrackingEnabled !== false;
    }
}

window.VRMManager = VRMManager;