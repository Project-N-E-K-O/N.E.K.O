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
    /**
     * 创建一个圆形渐变纹理 (Blob Shadow)
     */
    _createBlobShadowTexture() {
        const canvas = document.createElement('canvas');
        canvas.width = 64;
        canvas.height = 64;
        const ctx = canvas.getContext('2d');
        
        // 创建径向渐变 (从中心向外)
        const gradient = ctx.createRadialGradient(32, 32, 0, 32, 32, 32);
        gradient.addColorStop(0, 'rgba(0, 0, 0, 0.6)'); // 中心：黑色，60%透明度
        gradient.addColorStop(0.5, 'rgba(0, 0, 0, 0.3)'); // 中间：过渡
        gradient.addColorStop(1, 'rgba(0, 0, 0, 0)');   // 边缘：完全透明
        
        ctx.fillStyle = gradient;
        ctx.fillRect(0, 0, 64, 64);
        
        const texture = new window.THREE.CanvasTexture(canvas);
        return texture;
    }

    /**
     * 计算并添加模型阴影
     * 考虑了多种骨骼回退策略（脚趾→脚部→臀部→包围盒）
     * @param {Object} result - loadModel 返回的结果对象，包含 vrm 实例
     */
    _calculateAndAddShadow(result) {
        // 1. 可调节参数
        const SHADOW_SCALE_MULT = 0.5;     // 大小倍率：数字越大阴影越大
        const SHADOW_Y_OFFSET = 0.001;      // Y轴偏移：紧贴脚底的微小偏移（防止 Z-fighting）
        const FIX_CENTER_XZ = true;        // true: 强制阴影在 (0,0); false: 使用骨骼位置
        
        // 2. 确保场景矩阵已更新
        result.vrm.scene.updateMatrixWorld(true);
        
        // 3. 计算身体部分的包围盒（只计算 SkinnedMesh，排除头发、武器等）
        const bodyBox = new window.THREE.Box3();
        let hasBodyMesh = false;
        
        result.vrm.scene.traverse((object) => {
            if (object.isSkinnedMesh) {
                // 更新对象的世界矩阵
                object.updateMatrixWorld(true);
                // 计算该 mesh 的世界包围盒
                const meshBox = new window.THREE.Box3();
                meshBox.setFromObject(object);
                // 合并到总包围盒
                bodyBox.union(meshBox);
                hasBodyMesh = true;
            }
        });
        
        // 如果没找到 SkinnedMesh，使用整个场景的包围盒
        if (!hasBodyMesh) {
            bodyBox.setFromObject(result.vrm.scene);
        }
        
        // 将 bodyBox 转换为 vrm.scene 的本地空间（用于回退分支）
        result.vrm.scene.updateMatrixWorld(true);
        const sceneInverseMatrix = result.vrm.scene.matrixWorld.clone().invert();
        // 创建本地空间的包围盒：转换所有8个角点，然后重新计算包围盒
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
        
        // 优先使用 humanoid 骨骼来精确定位
        if (result.vrm.humanoid && result.vrm.humanoid.humanBones) {
            try {
                // 优先使用脚趾骨骼（leftToes/rightToes），因为脚部骨骼（leftFoot/rightFoot）在脚踝位置
                const leftToes = result.vrm.humanoid.humanBones.leftToes;
                const rightToes = result.vrm.humanoid.humanBones.rightToes;
                const leftFoot = result.vrm.humanoid.humanBones.leftFoot;
                const rightFoot = result.vrm.humanoid.humanBones.rightFoot;
                
                // 优先使用脚趾骨骼，如果不存在则使用脚部骨骼
                const leftTargetBone = (leftToes?.node) ? leftToes : leftFoot;
                const rightTargetBone = (rightToes?.node) ? rightToes : rightFoot;
                
                if (leftTargetBone?.node && rightTargetBone?.node) {
                    // 更新骨骼矩阵
                    leftTargetBone.node.updateMatrixWorld(true);
                    rightTargetBone.node.updateMatrixWorld(true);
                    
                    // 获取两脚的世界位置
                    const leftFootPos = new window.THREE.Vector3();
                    const rightFootPos = new window.THREE.Vector3();
                    leftTargetBone.node.getWorldPosition(leftFootPos);
                    rightTargetBone.node.getWorldPosition(rightFootPos);
                    
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
                    if (!leftToes?.node && leftFoot?.node) {
                        leftBottomY = findLowestY(leftFoot.node, leftFootPos.y);
                    }
                    
                    if (!rightToes?.node && rightFoot?.node) {
                        rightBottomY = findLowestY(rightFoot.node, rightFootPos.y);
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
                    if (!FIX_CENTER_XZ) {
                        leftFootPos.applyMatrix4(currentSceneInverseMatrix);
                        rightFootPos.applyMatrix4(currentSceneInverseMatrix);
                        shadowX = (leftFootPos.x + rightFootPos.x) / 2;
                        shadowZ = (leftFootPos.z + rightFootPos.z) / 2;
                    }
                } else {
                    // 如果没有脚部骨骼，尝试使用 hips 骨骼
                    const hipsBone = result.vrm.humanoid.humanBones.hips;
                    if (hipsBone?.node) {
                        hipsBone.node.updateMatrixWorld(true);
                        
                        const hipsPos = new window.THREE.Vector3();
                        hipsBone.node.getWorldPosition(hipsPos);
                        
                        // 转换为局部坐标
                        result.vrm.scene.updateMatrixWorld(true);
                        const currentSceneInverseMatrix = result.vrm.scene.matrixWorld.clone().invert();
                        hipsPos.applyMatrix4(currentSceneInverseMatrix);
                        
                        // 使用 hips 的 X/Z 位置（如果 FIX_CENTER_XZ 为 false）
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

        const DEFAULT_LOOP_ANIMATION = '/static/vrm/animation/wait03.vrma';

        // 确保 animation 模块已初始化
        if (!this.animation) {
            this._initModules();
        }

        // 辅助函数：显示模型并淡入画布
        const showAndFadeIn = () => {
            if (this.currentModel?.vrm?.scene) {
                // 确保 humanoid 已更新（防止T-Pose）
                if (this.currentModel.vrm.humanoid) {
                    if (this.currentModel.vrm.humanoid.autoUpdateHumanBones !== undefined && !this.currentModel.vrm.humanoid.autoUpdateHumanBones) {
                        this.currentModel.vrm.humanoid.autoUpdateHumanBones = true;
                    }
                    this.currentModel.vrm.humanoid.update();
                }
                
                // 强制重置物理骨骼状态
                if (this.currentModel.vrm.springBoneManager) {
                    this.currentModel.vrm.springBoneManager.reset();
                }
                // 先让 3D 物体可见
                this.currentModel.vrm.scene.visible = true;
                // 下一帧将画布透明度设为 1，触发 CSS 淡入动画
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
            
            // 等待 animation 模块初始化（如果还没加载）
            const tryPlayAnimation = async (retries = 10) => {
                // 检查组件是否仍然有效（防止在重试过程中被销毁）
                if (!this.currentModel || !this.currentModel.vrm) {
                    if (this._retryTimerId) {
                        clearTimeout(this._retryTimerId);
                        this._retryTimerId = null;
                    }
                    return;
                }
                
                if (!this.animation) {
                    this._initModules();
                    // 如果 VRMAnimation 类还没加载，等待一下
                    if (!this.animation && typeof window.VRMAnimation === 'undefined') {
                        if (retries > 0) {
                            // 清除之前的 timer（如果存在）
                            if (this._retryTimerId) {
                                clearTimeout(this._retryTimerId);
                            }
                            // 保存新的 timer ID 到实例变量
                            this._retryTimerId = setTimeout(() => {
                                this._retryTimerId = null; // 执行后清空引用
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
                
                // 清除 timer 引用（成功或失败都清除）
                if (this._retryTimerId) {
                    clearTimeout(this._retryTimerId);
                    this._retryTimerId = null;
                }
                
                // 确保 animation 已初始化
                if (this.animation) {
                    try {
                        await this.playVRMAAnimation(DEFAULT_LOOP_ANIMATION, { 
                            loop: true,
                            immediate: true 
                        });
                        // 动画应用成功，执行淡入
                        showAndFadeIn();
                    } catch (err) {
                        console.warn('[VRM Manager] 自动播放失败，强制显示:', err);
                        showAndFadeIn();
                    }
                } else {
                    // animation 初始化失败，直接显示
                    console.warn('[VRM Manager] animation 模块初始化失败，跳过自动播放');
                    showAndFadeIn();
                }
            };
            
            // 延迟一点确保模型完全加载
            setTimeout(() => {
                tryPlayAnimation();
            }, 100);
        } else {
            // 不自动播放，直接淡入
            showAndFadeIn();
        }
        
        // 设置初始表情
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
    dispose() {
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
            this.core.disposeVRM();
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
        } else {
            // 手动清理 window 事件监听器（如果 cleanupUI 不存在）
            if (this._windowEventHandlers && this._windowEventHandlers.length > 0) {
                this._windowEventHandlers.forEach(({ event, handler }) => {
                    window.removeEventListener(event, handler);
                });
                this._windowEventHandlers = [];
            }
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