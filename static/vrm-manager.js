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
            // 尝试自动检测 canvas 和 container
            const canvasId = options.canvasId || 'vrm-canvas';
            const containerId = options.containerId || 'vrm-container';
            
            // 检查元素是否存在
            const canvas = document.getElementById(canvasId);
            const container = document.getElementById(containerId);
            
            if (canvas && container) {
                console.log(`[VRM Manager] 场景未初始化，自动初始化场景 (canvas: ${canvasId}, container: ${containerId})`);
                await this.initThreeJS(canvasId, containerId);
            } else {
                throw new Error(`无法加载模型：场景未初始化。请先调用 initThreeJS('${canvasId}', '${containerId}') 或确保这些元素存在于页面中。`);
            }
        }
        
        const result = await this.core.loadModel(modelUrl, options);
        
        if (!this._animationFrameId) this.startAnimateLoop();

        const DEFAULT_LOOP_ANIMATION = '/static/vrm/animation/wait03.vrma';

        // 确保动画模块已初始化
        if (!this.animation) {
            this._initModules();
        }

        if (options.autoPlay !== false && this.animation) {
            // 延迟播放，确保模型完全加载
            setTimeout(() => {
                this.playVRMAAnimation(DEFAULT_LOOP_ANIMATION, { loop: true }).catch(err => {
                    console.warn('[VRM Manager] 自动播放默认动作失败:', err);
                });
            }, 100);
        }
        
        // 设置初始表情
        if (this.expression) {
            this.expression.setMood('neutral'); 
        }
        // 初始化 VRM 专属 UI
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