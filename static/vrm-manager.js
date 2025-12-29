/**
 * VRM Manager - 物理控制版 (修复更新顺序)
 */
class VRMManager {
    constructor() {
        console.log('[VRM Manager] 初始化...');
        
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

    startAnimateLoop() {
        if (this._animationFrameId) cancelAnimationFrame(this._animationFrameId);

        const animateLoop = () => {
            if (!this.renderer) return;

            this._animationFrameId = requestAnimationFrame(animateLoop);
            const delta = this.clock ? this.clock.getDelta() : 0.016;

            if (!this.animation && typeof window.VRMAnimation !== 'undefined') this._initModules();

            if (this.currentModel && this.currentModel.vrm) {
                
                // 1. 【关键修复】先计算表情权重
                // 必须在 vrm.update 之前设置好 blendShape 的值
                if (this.expression) {
                    this.expression.update(delta);
                }

                // 2. 确保 LookAt 看着相机
                if (this.currentModel.vrm.lookAt) {
                    this.currentModel.vrm.lookAt.target = this.camera;
                }
                
                // 3. 执行 VRM 物理和渲染更新
                if (this.enablePhysics) {
                    this.currentModel.vrm.update(delta);
                } else {
                    // 物理禁用时的保底更新
                    if (this.currentModel.vrm.lookAt) {
                        this.currentModel.vrm.lookAt.update(delta);
                    }
                    if (this.currentModel.vrm.expressionManager) {
                        this.currentModel.vrm.expressionManager.update(delta);
                    }
                }
            }

            // 4. 动画 Mixer 更新
            if (this.animation) {
                this.animation.update(delta);
            }
            
            // 5. 控制器更新
            if (this.controls) this.controls.update();
            
            this.renderer.render(this.scene, this.camera);
        };

        this._animationFrameId = requestAnimationFrame(animateLoop);
    }

    toggleSpringBone(enable) {
        this.enablePhysics = enable;
        console.log(`[VRM Manager] 物理系统已${enable ? '开启' : '关闭'}`);
    }

    async loadModel(modelUrl, options = {}) {
        this._initModules();
        if (!this.core) this.core = new window.VRMCore(this);
        
        const result = await this.core.loadModel(modelUrl, options);
        
        if (!this._animationFrameId) this.startAnimateLoop();

        const DEFAULT_LOOP_ANIMATION = '/static/vrm/animation/wait03.vrma';
        
        if (options.autoPlay !== false && this.animation) {
            console.log('[VRM Manager] 模型加载完成，自动播放默认循环动作...');
            this.playVRMAAnimation(DEFAULT_LOOP_ANIMATION, { loop: true }).catch(err => {
                console.warn('[VRM Manager] 自动播放默认动作失败:', err);
            });
        }
        
        // 设置初始表情
        if (this.expression) {
            this.expression.pickRandomMood(); 
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
    stopAnimation() { 
        if(this.animation) this.animation.stopVRMAAnimation(); 
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
console.log('[VRM Manager] 顺序修复版已加载');