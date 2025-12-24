/**
 * VRM Manager - 主管理器类，整合所有模块
 * 注意：此文件依赖于以下模块文件（需要按顺序加载）：
 * - vrm-core.js
 * - vrm-expression.js
 * - vrm-animation.js
 * - vrm-interaction.js
 */

class VRMManager {
    constructor() {
        console.log('[VRM Manager] 开始初始化VRMManager...');

        // 核心属性
        this.scene = null;
        this.camera = null;
        this.renderer = null;
        this.currentModel = null;
        this.animationMixer = null;
        this.clock = null;
        this.container = null;
        this.canvas = null;
        this.controls = null;
        this.isLocked = false; // 锁定状态，锁定时不响应拖拽和缩放

        console.log('[VRM Manager] 检查依赖模块...');

        // 检查依赖的模块类是否已加载（如果未加载，给出警告但不抛出错误，避免影响Live2D）
        if (typeof window.VRMCore === 'undefined') {
            console.warn('[VRM Manager] VRMCore 未加载，请确保已加载 vrm-core.js');
            // 不抛出错误，避免影响Live2D加载
        } else {
            console.log('[VRM Manager] VRMCore 已加载');
        }
        if (typeof window.VRMExpression === 'undefined') {
            console.warn('VRMExpression 未加载，请确保已加载 vrm-expression.js');
        }
        if (typeof window.VRMAnimation === 'undefined') {
            console.warn('VRMAnimation 未加载，请确保已加载 vrm-animation.js');
        }
        if (typeof window.VRMInteraction === 'undefined') {
            console.warn('VRMInteraction 未加载，请确保已加载 vrm-interaction.js');
        }
        
        // 初始化模块（只有在模块类存在时才初始化）
        if (typeof window.VRMCore !== 'undefined') {
            this.core = new window.VRMCore(this);
        }
        if (typeof window.VRMExpression !== 'undefined') {
            this.expression = new window.VRMExpression(this);
        }
        if (typeof window.VRMAnimation !== 'undefined') {
            this.animation = new window.VRMAnimation(this);
        }
        if (typeof window.VRMInteraction !== 'undefined') {
            this.interaction = new window.VRMInteraction(this);
        }
    }

    /**
     * 初始化Three.js场景
     */
    async initThreeJS(canvasId, containerId) {
        if (!this.core) {
            throw new Error('VRMCore 未初始化，请确保已加载所有VRM模块');
        }
        await this.core.init(canvasId, containerId);
        if (this.interaction) {
            this.interaction.initDragAndZoom();
        }
        this.animate();
        console.log('Three.js场景初始化完成');
        return true;
    }

    /**
     * 处理窗口大小变化
     */
    onWindowResize() {
        if (!this.container || !this.camera || !this.renderer) return;

        const width = this.container.clientWidth;
        const height = this.container.clientHeight;

        this.camera.aspect = width / height;
        this.camera.updateProjectionMatrix();
        this.renderer.setSize(width, height);
        
        const pixelRatio = Math.min(window.devicePixelRatio, 2);
        this.renderer.setPixelRatio(pixelRatio);
    }

    /**
     * 渲染循环
     */
    animate() {
        // 如果已经被dispose，停止循环
        if (!this.renderer || !this.scene || !this.camera) {
            return;
        }

        this._animationFrameId = requestAnimationFrame(() => this.animate());

        // 每秒输出一次渲染状态（减少日志频率）
        if (this._frameCount === undefined) this._frameCount = 0;
        this._frameCount++;
        if (this._frameCount % 60 === 0) { // 大约每秒输出一次
            console.log('[VRM Animate] 渲染循环运行中，场景子对象数量:', this.scene.children.length, 'hasModel:', !!this.currentModel);
        }

        const delta = this.clock ? this.clock.getDelta() : 0.016;

        // 更新控制器
        if (this.controls) {
            this.controls.update();
        }

        // 更新动画模块
        if (this.animation) {
            this.animation.update(delta);
        }

        // 更新VRM模型
        if (this.currentModel && this.currentModel.vrm) {
            this.currentModel.vrm.update(delta);
        }

        // 渲染场景
        if (this.renderer && this.scene && this.camera) {
            this.renderer.render(this.scene, this.camera);
        }
    }

    /**
     * 加载VRM模型
     */
    async loadModel(modelUrl, options = {}) {
        if (!this.core) {
            throw new Error('VRMCore 未初始化，请确保已加载所有VRM模块');
        }
        return await this.core.loadModel(modelUrl, options);
    }

    /**
     * 移除当前模型
     */
    removeModel() {
        if (this.currentModel && this.currentModel.scene) {
            this.scene.remove(this.currentModel.scene);
            
            this.currentModel.scene.traverse((object) => {
                if (object.geometry) object.geometry.dispose();
                if (object.material) {
                    if (Array.isArray(object.material)) {
                        object.material.forEach(material => material.dispose());
                    } else {
                        object.material.dispose();
                    }
                }
            });

            this.currentModel = null;
        }

        if (this.animationMixer) {
            this.animationMixer = null;
        }
        
        // 清理动画模块
        if (this.animation) {
            this.animation.stopVRMAAnimation();
            this.animation.stopLipSync();
        }
    }

    /**
     * 获取当前模型
     */
    getCurrentModel() {
        return this.currentModel;
    }

    /**
     * 清理所有资源
     */
    dispose() {
        // 停止动画循环
        if (this._animationFrameId) {
            cancelAnimationFrame(this._animationFrameId);
            this._animationFrameId = null;
        }

        // 移除当前模型
        this.removeModel();

        // 清理动画模块
        if (this.animation && typeof this.animation.dispose === 'function') {
            this.animation.dispose();
        }

        // 清理交互模块
        if (this.interaction && typeof this.interaction.dispose === 'function') {
            this.interaction.dispose();
        }

        // 清理Three.js资源
        if (this.scene) {
            // 清理场景中的所有对象
            while (this.scene.children.length > 0) {
                const object = this.scene.children[0];
                this.scene.remove(object);
                
                if (object.geometry) object.geometry.dispose();
                if (object.material) {
                    if (Array.isArray(object.material)) {
                        object.material.forEach(material => material.dispose());
                    } else {
                        object.material.dispose();
                    }
                }
            }
            this.scene = null;
        }

        // 清理渲染器
        if (this.renderer) {
            this.renderer.dispose();
            this.renderer = null;
        }

        // 清理控制器
        if (this.controls && typeof this.controls.dispose === 'function') {
            this.controls.dispose();
            this.controls = null;
        }

        // 清理其他资源
        this.camera = null;
        this.clock = null;
        this.animationMixer = null;
        this.container = null;
        this.canvas = null;
    }

    /**
     * 设置模型位置
     */
    setModelPosition(x, y, z) {
        if (this.currentModel && this.currentModel.scene) {
            this.currentModel.scene.position.set(x, y, z);
        }
    }

    /**
     * 设置模型缩放
     */
    setModelScale(x, y, z) {
        if (this.currentModel && this.currentModel.scene) {
            this.currentModel.scene.scale.set(x, y, z);
        }
    }

    /**
     * 设置相机位置
     */
    setCameraPosition(x, y, z) {
        if (this.camera) {
            this.camera.position.set(x, y, z);
            this.camera.lookAt(0, 1, 0);
        }
    }

    /**
     * 加载并播放VRM动画（VRMA文件）
     * 兼容旧接口
     */
    async loadAndPlayAnimation(animationUrl, options = {}) {
        if (!this.animation) {
            throw new Error('VRMAnimation 未初始化，请确保已加载所有VRM模块');
        }
        return await this.animation.playVRMAAnimation(animationUrl, options);
    }

    /**
     * 停止当前动画
     * 兼容旧接口
     */
    stopAnimation() {
        if (this.animation) {
            this.animation.stopVRMAAnimation();
        }
    }

    /**
     * 暂停/恢复当前动画
     * 兼容旧接口
     */
    pauseAnimation() {
        if (this.animation) {
            this.animation.pauseVRMAAnimation();
        }
    }

    /**
     * 检查动画是否正在播放
     */
    isAnimationPlaying() {
        return this.animation && this.animation.vrmaAction && 
               this.animation.vrmaAction.isRunning() && 
               !this.animation.vrmaAction.paused;
    }

    /**
     * 获取当前动画信息
     */
    getAnimationInfo() {
        if (!this.animation || !this.animation.vrmaAction) {
            return null;
        }

        const clip = this.animation.vrmaAction.getClip();
        return {
            isPlaying: this.isAnimationPlaying(),
            time: this.animation.vrmaAction.time,
            duration: clip ? clip.duration : 0,
            weight: this.animation.vrmaAction.getEffectiveWeight(),
            loop: this.animation.vrmaAction.loop,
            timeScale: this.animation.vrmaAction.timeScale,
            paused: this.animation.vrmaAction.paused
        };
    }

    /**
     * 设置表情（委托给expression模块）
     */
    setExpression(expressionName, weight) {
        if (!this.expression) {
            return false;
        }
        return this.expression.setExpression(expressionName, weight);
    }

    /**
     * 获取所有可用表情（委托给expression模块）
     */
    getAvailableExpressions() {
        if (!this.expression) {
            return [];
        }
        return this.expression.getAvailableExpressions();
    }

    /**
     * 重置所有表情（委托给expression模块）
     */
    resetExpressions() {
        if (this.expression) {
            return this.expression.resetExpressions();
        }
    }

    /**
     * 启动口型同步（委托给animation模块）
     */
    startLipSync(analyser) {
        if (!this.animation) {
            return false;
        }
        return this.animation.startLipSync(analyser);
    }

    /**
     * 停止口型同步（委托给animation模块）
     */
    stopLipSync() {
        if (this.animation) {
            return this.animation.stopLipSync();
        }
    }

    /**
     * 启用/禁用鼠标跟踪（委托给interaction模块）
     */
    enableMouseTracking(enabled) {
        if (this.interaction) {
            return this.interaction.enableMouseTracking(enabled);
        }
    }
}

// 导出到全局
window.VRMManager = VRMManager;
console.log('[VRM Manager] VRMManager 已注册到全局对象');
