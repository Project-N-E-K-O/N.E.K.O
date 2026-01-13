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
        
        // 阴影资源引用（用于清理）
        this._shadowTexture = null;
        this._shadowMaterial = null;
        this._shadowGeometry = null;
        this._shadowMesh = null; 
        
        this._initModules();
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
        const canvas = document.createElement('canvas');
        canvas.width = 64;
        canvas.height = 64;
        const ctx = canvas.getContext('2d');
        
        if (!ctx) {
            console.warn('[VRM Manager] 无法获取 2d context，返回透明纹理');
            return new window.THREE.CanvasTexture(canvas);
        }
        
        const gradient = ctx.createRadialGradient(32, 32, 0, 32, 32, 32);
        gradient.addColorStop(0, 'rgba(0, 0, 0, 0.6)');
        gradient.addColorStop(0.5, 'rgba(0, 0, 0, 0.3)');
        gradient.addColorStop(1, 'rgba(0, 0, 0, 0)');
        
        ctx.fillStyle = gradient;
        ctx.fillRect(0, 0, 64, 64);
        
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
        
        // 4. 计算阴影大小
        // 使用身体宽度和深度的较大值作为基准
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
                // 优先使用脚趾骨骼（leftToes/rightToes），因为脚部骨骼（leftFoot/rightFoot）在脚踝位置
                // 使用 getNormalizedBoneNode() API 来处理 VRM0/VRM1 的骨骼差异
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
                    
                    // 如果使用的是脚部骨骼（不是脚趾），需要向下偏移到脚底
                    // 脚部骨骼在脚踝，脚趾骨骼在脚底
                    let leftBottomY = leftFootPos.y;
                    let rightBottomY = rightFootPos.y;
                    
                    // 定义一次查找最低Y坐标的辅助函数（避免重复定义）
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
                    
                    // X/Z轴：使用两脚的中点（如果 FIX_CENTER_XZ 为 false）
                    // 注意：当前 FIX_CENTER_XZ 固定为 true，此分支不会执行，保留用于未来可能的配置需求
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
                        
                        // 使用 hips 的 X/Z 位置（如果 FIX_CENTER_XZ 为 false）
                        // 注意：当前 FIX_CENTER_XZ 固定为 true，此分支不会执行，保留用于未来可能的配置需求
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
            // 清理材质使用的纹理
            if (this._shadowMaterial.map) {
                // 检查材质使用的纹理是否就是 _shadowTexture，避免双重释放
                if (this._shadowMaterial.map === this._shadowTexture) {
                    // 如果是同一个对象，释放它并将 _shadowTexture 设为 null
                    this._shadowMaterial.map.dispose();
                    this._shadowTexture = null;
                } else {
                    // 如果不是同一个对象，只释放材质使用的纹理
                    this._shadowMaterial.map.dispose();
                }
            }
            this._shadowMaterial.dispose();
            this._shadowMaterial = null;
        }
        
        // 清理纹理（如果材质没有清理它，即不是同一个对象）
        if (this._shadowTexture) {
            this._shadowTexture.dispose();
            this._shadowTexture = null;
        }
    }

    async initThreeJS(canvasId, containerId) {
        // 检查是否已完全初始化（不仅检查 scene，还要检查 camera 和 renderer）
        if (this.scene && this.camera && this.renderer) {
            this._isInitialized = true;
            return true;
        }
        if (!this.clock && window.THREE) this.clock = new window.THREE.Clock();
        this._initModules();
        if (!this.core) {
            const errorMsg = window.t ? window.t('vrm.error.coreNotLoaded') : 'VRMCore 尚未加载';
            throw new Error(errorMsg);
        }
        await this.core.init(canvasId, containerId);
        if (this.interaction) this.interaction.initDragAndZoom();
        this.startAnimateLoop();
        // 设置初始化标志
        this._isInitialized = true;
        return true;
    }
    
    // ... 在 VRMManager 类中 ...

    startAnimateLoop() {
        if (this._animationFrameId) cancelAnimationFrame(this._animationFrameId);

        const animateLoop = () => {
            // 检查渲染器、场景和相机是否都存在，如果任何一个被 dispose 了则取消动画循环
            if (!this.renderer || !this.scene || !this.camera) {
                // 如果资源已被清理，取消动画帧并返回
                if (this._animationFrameId) {
                    cancelAnimationFrame(this._animationFrameId);
                    this._animationFrameId = null;
                }
                return;
            }

            this._animationFrameId = requestAnimationFrame(animateLoop);
            const delta = this.clock ? this.clock.getDelta() : 0.016;

            if (!this.animation && typeof window.VRMAnimation !== 'undefined') this._initModules();

            if (this.currentModel && this.currentModel.vrm) {
                // 1. 表情更新
                if (this.expression) {
                    this.expression.update(delta);
                }

                // 2. 视线更新
                if (this.currentModel.vrm.lookAt) {
                    this.currentModel.vrm.lookAt.target = this.camera;
                }
                
                // 3. 物理更新
                if (this.enablePhysics) {
                    this.currentModel.vrm.update(delta);
                } else {
                    if (this.currentModel.vrm.lookAt) this.currentModel.vrm.lookAt.update(delta);
                    if (this.currentModel.vrm.expressionManager) this.currentModel.vrm.expressionManager.update(delta);
                }

                
                
            }

            // 4. 交互系统更新（浮动按钮跟随等）
            if (this.interaction) {
                this.interaction.update(delta);
            }

            // 5. 动画更新
            if (this.animation) {
                this.animation.update(delta);
            }

            // 6. 更新控制器
            if (this.controls) {
                this.controls.update();
            }

            // 7. 渲染场景（在渲染前再次检查，防止在帧执行过程中被 dispose）
            if (this.renderer && this.scene && this.camera) {
                this.renderer.render(this.scene, this.camera);
            }
        };

        this._animationFrameId = requestAnimationFrame(animateLoop);
    }

    toggleSpringBone(enable) {
        this.enablePhysics = enable;
    }

    async loadModel(modelUrl, options = {}) {
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
                await this.initThreeJS(canvasId, containerId);
            } else {
                const errorMsg = window.t ? window.t('vrm.error.sceneNotInitialized') : '无法加载模型：场景未初始化。';
                throw new Error(errorMsg);
            }
        }

        
        // 设置画布初始状态为透明，并添加 CSS 过渡效果
        if (this.renderer && this.renderer.domElement) {
            this.renderer.domElement.style.opacity = '0';
            // 这里的 1.0s 是淡入时间，你可以改成 0.5s 或 2.0s
            this.renderer.domElement.style.transition = 'opacity 1.0s ease-in-out';
        }

        // 加载模型
        const result = await this.core.loadModel(modelUrl, options);

        // 动态计算阴影位置和大小
        if (options.addShadow !== false && result && result.vrm && result.vrm.scene) {
            this._calculateAndAddShadow(result);
            
            // 隐藏模型等待动画就绪
            result.vrm.scene.visible = false;
        }
        
        // 加载完保持 3D 对象不可见 (防 T-Pose)
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
            if (this.currentModel?.vrm?.scene) {
                if (this.currentModel.vrm.humanoid) {
                    if (this.currentModel.vrm.humanoid.autoUpdateHumanBones !== undefined && !this.currentModel.vrm.humanoid.autoUpdateHumanBones) {
                        this.currentModel.vrm.humanoid.autoUpdateHumanBones = true;
                    }
                    this.currentModel.vrm.humanoid.update();
                }
                
                if (this.currentModel.vrm.springBoneManager) {
                    this.currentModel.vrm.springBoneManager.reset();
                }
                this.currentModel.vrm.scene.visible = true;
                requestAnimationFrame(() => {
                    if (this.renderer && this.renderer.domElement) {
                        this.renderer.domElement.style.opacity = '1';
                    }
                });
            }
        };

        // 自动播放待机动画
        if (options.autoPlay !== false) {
            // 初始化重试 timer ID 实例变量（如果不存在）
            if (!this._retryTimerId) {
                this._retryTimerId = null;
            }
            
            const tryPlayAnimation = async (retries = 10) => {
                if (!this.currentModel || !this.currentModel.vrm) {
                    if (this._retryTimerId) {
                        clearTimeout(this._retryTimerId);
                        this._retryTimerId = null;
                    }
                    return;
                }
                
                if (!this.animation) {
                    this._initModules();
                    if (!this.animation && typeof window.VRMAnimation === 'undefined') {
                        if (retries > 0) {
                            if (this._retryTimerId) {
                                clearTimeout(this._retryTimerId);
                            }
                            this._retryTimerId = setTimeout(() => {
                                this._retryTimerId = null;
                                tryPlayAnimation(retries - 1);
                            }, 100);
                            return;
                        } else {
                            console.warn('[VRM Manager] VRMAnimation 模块未加载，跳过自动播放');
                            this._retryTimerId = null;
                            showAndFadeIn();
                            return;
                        }
                    }
                }
                
                if (this._retryTimerId) {
                    clearTimeout(this._retryTimerId);
                    this._retryTimerId = null;
                }
                
                if (this.animation) {
                    try {
                        await this.playVRMAAnimation(DEFAULT_LOOP_ANIMATION, { 
                            loop: true,
                            immediate: true 
                        });
                        showAndFadeIn();
                    } catch (err) {
                        console.warn('[VRM Manager] 自动播放失败，强制显示:', err);
                        showAndFadeIn();
                    }
                } else {
                    console.warn('[VRM Manager] animation 模块初始化失败，跳过自动播放');
                    showAndFadeIn();
                }
            };
            
            setTimeout(() => {
                tryPlayAnimation();
            }, 100);
        } else {
            showAndFadeIn();
        }
        
        if (this.expression) {
            this.expression.setMood('neutral'); 
        }
        if (this.setupFloatingButtons) {
            this.setupFloatingButtons();
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
        if (this.camera && this.renderer) {
            this.camera.aspect = window.innerWidth / window.innerHeight;
            this.camera.updateProjectionMatrix();
            this.renderer.setSize(window.innerWidth, window.innerHeight);
        }
    }
    getCurrentModel() { 
        return this.currentModel; 
    }
    setModelPosition(x,y,z) { 
        if(this.currentModel?.vrm?.scene) this.currentModel.vrm.scene.position.set(x,y,z); 
    }
    setModelScale(x,y,z) { 
        if(this.currentModel?.vrm?.scene) this.currentModel.vrm.scene.scale.set(x,y,z); 
    }

    /**
     * 完整清理 VRM 资源（用于模型切换）
     * 包括：取消动画循环、清理模型资源、清理场景/渲染器、重置初始化状态
     */
    async dispose() {
        console.log('[VRM Manager] 开始完整清理 VRM 资源...');
        
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
        }
        
        // 6. 清理动画模块
        if (this.animation) {
            if (typeof this.animation.dispose === 'function') {
                this.animation.dispose();
            }
            if (typeof this.animation.stopVRMAAnimation === 'function') {
                this.animation.stopVRMAAnimation();
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
        
        // 8. 清理场景中的所有对象（包括灯光）
        if (this.scene) {
            // 遍历并清理所有子对象
            while (this.scene.children.length > 0) {
                const child = this.scene.children[0];
                this.scene.remove(child);
                
                // 如果是可清理的对象，调用 dispose
                if (child.geometry) child.geometry.dispose();
                if (child.material) {
                    if (Array.isArray(child.material)) {
                        child.material.forEach(m => m.dispose());
                    } else {
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

        // 清理 window 事件监听器（包括 VRMCore.init() 中注册的 resize 监听器）
        // 无论 cleanupUI 是否存在，都需要清理 _windowEventHandlers（因为 VRMCore.init() 的 resize 监听器存储在这里）
        if (this._windowEventHandlers && this._windowEventHandlers.length > 0) {
            this._windowEventHandlers.forEach(({ event, handler }) => {
                window.removeEventListener(event, handler);
            });
            this._windowEventHandlers = [];
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
}

window.VRMManager = VRMManager;