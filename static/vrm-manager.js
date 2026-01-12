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
        this.enablePhysics = true; 
        
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
    async initThreeJS(canvasId, containerId) {
        if (this.scene) return true;
        if (!this.clock && window.THREE) this.clock = new window.THREE.Clock();
        this._initModules();
        if (!this.core) throw new Error("VRMCore 尚未加载");
        await this.core.init(canvasId, containerId);
        if (this.interaction) this.interaction.initDragAndZoom();
        this.startAnimateLoop();
        return true;
    }
    
    // ... 在 VRMManager 类中 ...

    startAnimateLoop() {
        if (this._animationFrameId) cancelAnimationFrame(this._animationFrameId);

        const animateLoop = () => {
            if (!this.renderer) return;

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

            // 7. 渲染场景
            this.renderer.render(this.scene, this.camera);
        };

        this._animationFrameId = requestAnimationFrame(animateLoop);
    }

    toggleSpringBone(enable) {
        this.enablePhysics = enable;
    }

    async loadModel(modelUrl, options = {}) {
        this._initModules();
        if (!this.core) this.core = new window.VRMCore(this);
        
        // 确保场景已初始化
        if (!this.scene || !this.camera || !this.renderer) {
            const canvasId = options.canvasId || 'vrm-canvas';
            const containerId = options.containerId || 'vrm-container';
            
            const canvas = document.getElementById(canvasId);
            const container = document.getElementById(containerId);
            
            if (canvas && container) {
                await this.initThreeJS(canvasId, containerId);
            } else {
                throw new Error(`无法加载模型：场景未初始化。`);
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
            
            // 获取包围盒尺寸（用于计算阴影大小）
            const bodySize = new window.THREE.Vector3();
            bodyBox.getSize(bodySize);
            
            // 4. 计算阴影大小
            // 使用身体宽度和深度的较大值作为基准
            const shadowDiameter = Math.max(
                Math.max(bodySize.x, bodySize.z) * SHADOW_SCALE_MULT,
                0.3  // 最小尺寸保底
            );
            
            // 5. 创建阴影纹理和材质
            const shadowTexture = this._createBlobShadowTexture();
            const shadowMaterial = new window.THREE.MeshBasicMaterial({
                map: shadowTexture,
                transparent: true,
                opacity: 1.0,
                depthWrite: false,  // 不写入深度缓冲，避免遮挡模型
                side: window.THREE.DoubleSide
            });
            
            // 6. 创建阴影网格
            const shadowGeo = new window.THREE.PlaneGeometry(1, 1);
            const shadowMesh = new window.THREE.Mesh(shadowGeo, shadowMaterial);
            shadowMesh.rotation.x = -Math.PI / 2;  // 旋转到水平面
            shadowMesh.scale.set(shadowDiameter, shadowDiameter, 1);
            
            // 计算阴影位置
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
                        
                        // 如果使用的是脚部骨骼，需要向下偏移（估算脚的长度）
                        if (!leftToes?.node && leftFoot?.node) {
                            // 尝试找到脚部骨骼的最底部（遍历子骨骼）
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
                            leftBottomY = findLowestY(leftFoot.node, leftFootPos.y);
                        }
                        
                        if (!rightToes?.node && rightFoot?.node) {
                            const findLowestY = (bone, currentY) => {
                                let lowest = currentY;
                                if (bone) {
                                    bone.updateMatrixWorld(true);
                                    const pos = new window.THREE.Vector3();
                                    bone.getWorldPosition(pos);
                                    if (pos.y < lowest) {
                                        lowest = pos.y;
                                    }
                                    bone.children.forEach(child => {
                                        lowest = findLowestY(child, lowest);
                                    });
                                }
                                return lowest;
                            };
                            rightBottomY = findLowestY(rightFoot.node, rightFootPos.y);
                        }
                        
                        // 转换为相对于 vrm.scene 的局部坐标
                        result.vrm.scene.updateMatrixWorld(true);
                        const sceneInverseMatrix = result.vrm.scene.matrixWorld.clone().invert();
                        
                        // 将最低点转换为局部坐标
                        const leftBottomPos = new window.THREE.Vector3(leftFootPos.x, leftBottomY, leftFootPos.z);
                        const rightBottomPos = new window.THREE.Vector3(rightFootPos.x, rightBottomY, rightFootPos.z);
                        leftBottomPos.applyMatrix4(sceneInverseMatrix);
                        rightBottomPos.applyMatrix4(sceneInverseMatrix);
                        
                        // Y轴：使用两脚中较低的 Y 值，确保阴影在脚底
                        shadowY = Math.min(leftBottomPos.y, rightBottomPos.y) + SHADOW_Y_OFFSET;
                        
                        // X/Z轴：使用两脚的中点（如果 FIX_CENTER_XZ 为 false）
                        if (!FIX_CENTER_XZ) {
                            leftFootPos.applyMatrix4(sceneInverseMatrix);
                            rightFootPos.applyMatrix4(sceneInverseMatrix);
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
                            const sceneInverseMatrix = result.vrm.scene.matrixWorld.clone().invert();
                            hipsPos.applyMatrix4(sceneInverseMatrix);
                            
                            // 使用 hips 的 X/Z 位置（如果 FIX_CENTER_XZ 为 false）
                            if (!FIX_CENTER_XZ) {
                                shadowX = hipsPos.x;
                                shadowZ = hipsPos.z;
                            }
                            
                            // Y轴：使用包围盒的最低点（因为 hips 在腰部，不是脚底）
                            shadowY = bodyBox.min.y + SHADOW_Y_OFFSET;
                        } else {
                            // 如果连 hips 都没有，使用包围盒的最低点
                            shadowY = bodyBox.min.y + SHADOW_Y_OFFSET;
                        }
                    }
                } catch (e) {
                    // 回退到使用包围盒
                    shadowY = bodyBox.min.y + SHADOW_Y_OFFSET;
                }
            } else {
                // 如果没有 humanoid，使用包围盒的最低点
                shadowY = bodyBox.min.y + SHADOW_Y_OFFSET;
            }
            
            // 如果 FIX_CENTER_XZ 为 true，强制使用 (0, 0) 作为 X/Z
            if (FIX_CENTER_XZ) {
                shadowX = 0;
                shadowZ = 0;
            }
            
            // 8. 设置阴影位置
            shadowMesh.position.set(shadowX, shadowY, shadowZ);
            
            // 9. 添加到模型场景中
            result.vrm.scene.add(shadowMesh);
            
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
            // 等待 animation 模块初始化（如果还没加载）
            const tryPlayAnimation = async (retries = 10) => {
                if (!this.animation) {
                    this._initModules();
                    // 如果 VRMAnimation 类还没加载，等待一下
                    if (!this.animation && typeof window.VRMAnimation === 'undefined') {
                        if (retries > 0) {
                            setTimeout(() => tryPlayAnimation(retries - 1), 100);
                            return;
                        } else {
                            console.warn('[VRM Manager] VRMAnimation 模块未加载，跳过自动播放');
                            showAndFadeIn();
                            return;
                        }
                    }
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
        if(this.currentModel?.scene) this.currentModel.scene.position.set(x,y,z); 
    }
    setModelScale(x,y,z) { 
        if(this.currentModel?.scene) this.currentModel.scene.scale.set(x,y,z); 
    }
}

window.VRMManager = VRMManager;