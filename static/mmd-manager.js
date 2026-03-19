/**
 * MMD Manager - 统一管理器，整合所有 MMD 子模块
 * 参考 vrm-manager.js 结构，提供统一 API
 */
class MMDManager {
    constructor() {
        this.scene = null;
        this.camera = null;
        this.renderer = null;
        this.canvas = null;
        this.container = null;
        this.currentModel = null; // MMD 对象 (from three-mmd)
        this.clock = null;
        this.controls = null;
        this.effect = null; // OutlineEffect
        this.useOutlineEffect = true;
        this.enablePhysics = true;
        this.physicsStrength = 1.0;
        this._baseGravityY = -98;  // Ammo.js 默认重力 Y 分量
        this.isLocked = false;

        // 光照
        this.ambientLight = null;
        this.directionalLight = null;

        // 模块引用
        this.core = null;
        this.animationModule = null;
        this.expression = null;
        this.interaction = null;
        this.cursorFollow = null;

        // 状态
        this._animationFrameId = null;
        this._shouldRender = false;
        this._isDisposed = false;
        this._isModelReadyForInteraction = false;
        this._isInReturnState = false;
        this._activeLoadToken = 0;

        // 事件处理器
        this._coreWindowHandlers = [];

        // 侧边面板跟踪
        this._sidePanels = new Set();
        this._uiWindowHandlers = [];

        this._initModules();
    }

    _initModules() {
        // 核心模块
        if (typeof MMDCore !== 'undefined') {
            this.core = new MMDCore(this);
        }

        // 动画模块
        if (typeof MMDAnimation !== 'undefined') {
            this.animationModule = new MMDAnimation(this);
        }

        // 表情模块
        if (typeof MMDExpression !== 'undefined') {
            this.expression = new MMDExpression(this);
        }

        // 交互模块
        if (typeof MMDInteraction !== 'undefined') {
            this.interaction = new MMDInteraction(this);
        }

        // 鼠标跟踪模块
        if (typeof MMDCursorFollow !== 'undefined') {
            this.cursorFollow = new MMDCursorFollow(this);
        }
    }

    // ═══════════════════ 初始化 ═══════════════════

    async init(canvasId = 'mmd-canvas', containerId = 'mmd-container') {
        if (!this.core) {
            throw new Error('[MMD Manager] MMDCore 模块未加载');
        }

        await this.core.init(canvasId, containerId);

        // 初始化交互
        if (this.interaction) {
            this.interaction.initDragAndZoom();
        }

        // 初始化鼠标跟踪
        if (this.cursorFollow) {
            this.cursorFollow.init();
        }

        // 设置浮动按钮
        if (typeof this.setupFloatingButtons === 'function') {
            this.setupFloatingButtons();
        }

        console.log('[MMD Manager] 初始化完成');
    }

    // ═══════════════════ 模型加载 ═══════════════════

    async loadModel(modelPath) {
        if (!this.core) throw new Error('MMDCore 未初始化');

        this._isModelReadyForInteraction = false;
        this._activeLoadToken++;
        const loadToken = this._activeLoadToken;

        try {
            const modelInfo = await this.core.loadModel(modelPath);

            // 检查是否已被新的加载请求取代
            if (this._activeLoadToken !== loadToken) {
                console.log('[MMD Manager] 模型加载已被取代');
                return null;
            }

            // 刷新鼠标跟踪骨骼
            if (this.cursorFollow) {
                this.cursorFollow.refresh();
            }

            // 加载表情映射
            if (this.expression && modelInfo.name) {
                await this.expression.loadMoodMap(modelInfo.name);
            }

            this._isModelReadyForInteraction = true;

            // 派发模型加载完成事件
            window.dispatchEvent(new CustomEvent('mmd-model-loaded', {
                detail: { modelInfo, modelPath }
            }));

            return modelInfo;
        } catch (error) {
            console.error('[MMD Manager] 模型加载失败:', error);
            throw error;
        }
    }

    // ═══════════════════ 动画 ═══════════════════

    async loadAnimation(vmdPath) {
        if (!this.animationModule) throw new Error('MMDAnimation 未初始化');
        const clip = await this.animationModule.loadAnimation(vmdPath);
        return clip;
    }

    playAnimation() {
        // 播放动画时禁用鼠标跟踪
        if (this.cursorFollow) {
            this.cursorFollow.setDisabledByAnimation(true);
        }
        if (this.animationModule) {
            this.animationModule.play();
        }
    }

    pauseAnimation() {
        if (this.animationModule) {
            this.animationModule.pause();
        }
    }

    stopAnimation() {
        if (this.animationModule) {
            this.animationModule.stop();
        }
        // 恢复鼠标跟踪
        if (this.cursorFollow) {
            this.cursorFollow.setDisabledByAnimation(false);
        }
    }

    // ═══════════════════ 表情/口型 ═══════════════════

    setEmotion(emotion) {
        if (this.expression) {
            this.expression.setEmotion(emotion);
        }
    }

    setMouth(value) {
        if (this.expression) {
            this.expression.setMouth(value);
        }
    }

    getMorphNames() {
        if (this.expression) {
            return this.expression.getMorphNames();
        }
        return [];
    }

    setMorphWeight(name, weight) {
        if (this.expression) {
            return this.expression.setMorphWeight(name, weight);
        }
        return false;
    }

    // ═══════════════════ hitTest ═══════════════════

    hitTest(clientX, clientY) {
        if (!this._isModelReadyForInteraction) return false;
        if (this.interaction) {
            return this.interaction._hitTestModel(clientX, clientY);
        }
        return false;
    }

    hitTestBounds(clientX, clientY) {
        if (!this._isModelReadyForInteraction) return false;
        if (this.interaction) {
            this.interaction.updateScreenBounds();
            return this.interaction.hitTestBounds(clientX, clientY);
        }
        return false;
    }

    // ═══════════════════ 窗口 resize ═══════════════════

    onWindowResize() {
        if (this.core) {
            this.core.onWindowResize();
        }
    }

    // ═══════════════════ 应用设置 (来自UI) ═══════════════════

    applySettings(settings) {
        if (!settings) return;
        // 光照
        if (settings.lighting) {
            const l = settings.lighting;
            if (this.ambientLight) {
                if (l.ambientIntensity != null) this.ambientLight.intensity = l.ambientIntensity;
                if (l.ambientColor) this.ambientLight.color.set(l.ambientColor);
            }
            if (this.directionalLight) {
                if (l.directionalIntensity != null) this.directionalLight.intensity = l.directionalIntensity;
                if (l.directionalColor) this.directionalLight.color.set(l.directionalColor);
            }
        }
        // 渲染
        if (settings.rendering && this.renderer) {
            const r = settings.rendering;
            if (r.toneMapping != null) {
                this.renderer.toneMapping = Number(r.toneMapping);
                // 更新所有材质（MMD 对象的 mesh 才是 THREE.Object3D）
                const mesh = this.currentModel?.mesh;
                if (mesh) {
                    mesh.traverse((obj) => {
                        if (obj.material) {
                            if (Array.isArray(obj.material)) {
                                obj.material.forEach(m => { m.needsUpdate = true; });
                            } else {
                                obj.material.needsUpdate = true;
                            }
                        }
                    });
                }
            }
            if (r.exposure != null) this.renderer.toneMappingExposure = r.exposure;
            if (r.pixelRatio != null) {
                const ratio = r.pixelRatio === 0 ? (window.devicePixelRatio || 1) : r.pixelRatio;
                this.renderer.setPixelRatio(Math.max(0.1, ratio));
                // setPixelRatio 后需主动触发 setSize 才能生效
                const container = this.container;
                if (container) {
                    const w = container.clientWidth || container.offsetWidth || window.innerWidth;
                    const h = container.clientHeight || container.offsetHeight || window.innerHeight;
                    this.renderer.setSize(w, h, false);
                    if (this.camera) {
                        this.camera.aspect = w / h;
                        this.camera.updateProjectionMatrix();
                    }
                    if (this.effect) {
                        this.effect.setSize(w, h);
                    }
                }
            }
            if (r.outline != null) {
                this.useOutlineEffect = r.outline;
            }
        }
        // 物理
        if (settings.physics) {
            if (settings.physics.enabled != null) {
                this.enablePhysics = settings.physics.enabled;
            }
            if (settings.physics.strength != null) {
                const newStrength = Math.max(0.1, Math.min(2.0, settings.physics.strength));
                this.physicsStrength = newStrength;
                // 通过缩放重力控制物理强度
                const physics = this.currentModel?.physics;
                if (physics && typeof physics.setGravity === 'function') {
                    const THREE = window.THREE;
                    if (THREE) {
                        physics.setGravity(new THREE.Vector3(0, this._baseGravityY * newStrength, 0));
                    }
                }
            }
        }
        // 鼠标跟踪
        if (settings.cursorFollow && this.cursorFollow) {
            if (typeof this.cursorFollow.applyConfig === 'function') {
                this.cursorFollow.applyConfig(settings.cursorFollow);
            }
        }
    }

    // ═══════════════════ 渲染控制 ═══════════════════

    pauseRendering() {
        this._shouldRender = false;
        if (this._animationFrameId) {
            cancelAnimationFrame(this._animationFrameId);
            this._animationFrameId = null;
        }
    }

    resumeRendering() {
        if (this._isDisposed) return;
        this._shouldRender = true;
        if (!this._animationFrameId && this.core) {
            this.core._startRenderLoop();
        }
    }

    // ═══════════════════ 清理 ═══════════════════

    dispose() {
        this._isDisposed = true;
        this._shouldRender = false;

        // 清理浮动按钮
        if (typeof this.cleanupFloatingButtons === 'function') {
            this.cleanupFloatingButtons();
        }

        // 清理侧边面板
        if (this._sidePanels) {
            for (const panel of this._sidePanels) {
                if (window.AvatarPopupUI && window.AvatarPopupUI.unregisterSidePanel) {
                    window.AvatarPopupUI.unregisterSidePanel(panel);
                }
                panel.remove();
            }
            this._sidePanels.clear();
        }

        // 清理调试面板
        if (typeof window.cleanupMMDDebugPanel === 'function') {
            window.cleanupMMDDebugPanel();
        }

        // 清理各模块
        if (this.animationModule) {
            this.animationModule.dispose();
            this.animationModule = null;
        }
        if (this.expression) {
            this.expression.dispose();
            this.expression = null;
        }
        if (this.interaction) {
            this.interaction.dispose();
            this.interaction = null;
        }
        if (this.cursorFollow) {
            this.cursorFollow.dispose();
            this.cursorFollow = null;
        }
        if (this.core) {
            this.core.dispose();
            this.core = null;
        }

        this.currentModel = null;
        this.scene = null;
        this.camera = null;
        this.renderer = null;
        this.canvas = null;
        this.container = null;

        console.log('[MMD Manager] 已完全销毁');
    }
}
