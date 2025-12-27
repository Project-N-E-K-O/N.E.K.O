/**
 * VRM Manager - 物理控制版
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
        this.enablePhysics = true; // 新增：物理开关
        
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

            // 1. 先让 VRM 计算 (包括 LookAt, SpringBone 等)
            if (this.currentModel && this.currentModel.vrm) {
                // 如果开启了物理，正常更新；否则跳过 SpringBone 更新
                if (this.enablePhysics) {
                    this.currentModel.vrm.update(delta);
                } else {
                    // 【关键修复】当物理被禁用时，完全跳过 VRM 更新
                    // 防止 SpringBone 与动画冲突导致手臂抽动
                    // 仅更新必要的组件，但跳过物理计算
                    if (this.currentModel.vrm.lookAt) {
                        this.currentModel.vrm.lookAt.update(delta);
                    }
                    if (this.currentModel.vrm.expressionManager) {
                        this.currentModel.vrm.expressionManager.update(delta);
                    }
                    // 故意跳过 springBoneManager.update() 来避免与动画冲突
                }
            }

            // 2. 强行覆盖动画 (这是解决 T-Pose 重置的关键)
            if (this.animation) {
                this.animation.update(delta);
            }
            
            if (this.controls) this.controls.update();
            this.renderer.render(this.scene, this.camera);
        };

        this._animationFrameId = requestAnimationFrame(animateLoop);
    }

    // --- 新增：物理开关 ---
    toggleSpringBone(enable) {
        this.enablePhysics = enable;
        console.log(`[VRM Manager] 物理系统已${enable ? '开启' : '关闭'}`);
    }

    async loadModel(modelUrl, options = {}) {
        this._initModules();
        if (!this.core) this.core = new window.VRMCore(this);
        const result = await this.core.loadModel(modelUrl, options);
        if (this.animation) this.animation.stopVRMAAnimation();
        if (!this._animationFrameId) this.startAnimateLoop();
        return result;
    }

    async playVRMAAnimation(url, opts) {
        if (!this.animation) this._initModules();
        if (this.animation) return this.animation.playVRMAAnimation(url, opts);
    }

    stopAnimation() { if(this.animation) this.animation.stopVRMAAnimation(); }
    onWindowResize() { this.core?.onWindowResize(); }
    getCurrentModel() { return this.currentModel; }
    setModelPosition(x,y,z) { if(this.currentModel?.scene) this.currentModel.scene.position.set(x,y,z); }
    setModelScale(x,y,z) { if(this.currentModel?.scene) this.currentModel.scene.scale.set(x,y,z); }
}

window.VRMManager = VRMManager;
console.log('[VRM Manager] 物理控制版已加载');