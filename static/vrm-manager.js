/**
 * VRM Manager - 智能修正版
 * 修复了模块加载顺序不同步导致“有动画但不播放”的问题
 */
class VRMManager {
    constructor() {
        console.log('[VRM Manager] 初始化...');
        
        // 核心属性
        this.scene = null;
        this.camera = null;
        this.renderer = null;
        this.currentModel = null;
        this.animationMixer = null;
        
        // 时钟 (Three.js 可能还未加载，延后初始化)
        this.clock = (typeof window.THREE !== 'undefined') ? new window.THREE.Clock() : null;
        
        this.container = null;
        this._animationFrameId = null;
        
        // 初始尝试加载模块
        this._initModules();
    }

    /**
     * 初始化或重新扫描模块 (关键修复)
     * 每次加载模型前都会检查一遍，防止因为加载顺序导致模块丢失
     */
    _initModules() {
        // 1. 核心 Core
        if (!this.core && typeof window.VRMCore !== 'undefined') {
            this.core = new window.VRMCore(this);
        }
        
        // 2. 表情 Expression
        if (!this.expression && typeof window.VRMExpression !== 'undefined') {
            this.expression = new window.VRMExpression(this);
        }
        
        // 3. 动画 Animation (这是之前丢失的!)
        if (!this.animation && typeof window.VRMAnimation !== 'undefined') {
            this.animation = new window.VRMAnimation(this);
            console.log('[VRM Manager] 成功连接 VRMAnimation 模块');
        }
        
        // 4. 交互 Interaction
        if (!this.interaction && typeof window.VRMInteraction !== 'undefined') {
            this.interaction = new window.VRMInteraction(this);
        }
    }

    async initThreeJS(canvasId, containerId) {
        if (this.scene) return true;
        
        // 确保 Clock 存在
        if (!this.clock && window.THREE) this.clock = new window.THREE.Clock();
        
        // 再次检查模块，以防万一
        this._initModules();
        if (!this.core) throw new Error("VRMCore 尚未加载，无法初始化 ThreeJS");

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

            // 1. 再次尝试获取动画模块 (防止 constructor 阶段丢失)
            if (!this.animation && typeof window.VRMAnimation !== 'undefined') {
                this.animation = new window.VRMAnimation(this);
            }

            // 2. 驱动动画 (核心驱动力)
            if (this.animation) {
                this.animation.update(delta);
            }

            // 3. 驱动物理 (头发/裙子)
            if (this.currentModel && this.currentModel.vrm) {
                this.currentModel.vrm.update(delta);
            }
            
            // 4. 驱动控制器
            if (this.controls) this.controls.update();

            // 5. 渲染
            this.renderer.render(this.scene, this.camera);
        };

        this._animationFrameId = requestAnimationFrame(animateLoop);
    }

    /**
     * 加载模型
     */
    async loadModel(modelUrl, options = {}) {
        // 【关键】加载模型前，必须确保所有模块都已连接
        this._initModules();

        if (!this.core) this.core = new window.VRMCore(this);
        
        const result = await this.core.loadModel(modelUrl, options);
        
        // 强制停止之前的动画 (保持 T-Pose)
        if (this.animation) {
            this.animation.stopVRMAAnimation();
        }

        // 确保循环开启
        if (!this._animationFrameId) this.startAnimateLoop();
        
        console.log('[VRM Manager] 模型已加载 (T-Pose)');
        return result;
    }

    // --- 代理方法 ---
    async playVRMAAnimation(url, opts) {
        // 播放前最后一次检查模块
        if (!this.animation) this._initModules();
        if (this.animation) {
            return this.animation.playVRMAAnimation(url, opts);
        } else {
            console.error('[VRM Manager] 无法播放：VRMAnimation 模块依然未加载');
        }
    }

    stopAnimation() { if(this.animation) this.animation.stopVRMAAnimation(); }
    onWindowResize() { this.core?.onWindowResize(); }
    
    // Getter/Setter 代理
    getCurrentModel() { return this.currentModel; }
    setModelPosition(x,y,z) { if(this.currentModel?.scene) this.currentModel.scene.position.set(x,y,z); }
    setModelScale(x,y,z) { if(this.currentModel?.scene) this.currentModel.scene.scale.set(x,y,z); }
}

window.VRMManager = VRMManager;
console.log('[VRM Manager] 智能修正版已加载 (自动重连模块)');