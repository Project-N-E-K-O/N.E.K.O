/**
 * MMD Manager - 统一管理器，整合所有 MMD 子模块
 * 参考 vrm-manager.js 结构，提供统一 API
 */
class MMDManager {
    static DEFAULT_MODEL_PATH = '/static/mmd/Miku/Miku.pmx';

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
        this._userForcedOutline = false; // 用户是否手动设置过描边
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
        this.currentAnimationUrl = null;
        this._isDisposed = false;
        this._isModelReadyForInteraction = false;
        this._isInReturnState = false;
        this._activeLoadToken = 0;
        this._pendingModelLoadCount = 0;
        this._isLoadingModel = false;
        this._modelLoadState = 'idle';
        this._headScreenAnchorProjection = null;

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
            this.cursorFollow.setLocalTrackingEnabled(window.humanoidLocalTrackingEnabled === true);
        }

        // 设置浮动按钮
        if (typeof this.setupFloatingButtons === 'function' && !window._cardExportPage) {
            this.setupFloatingButtons();
        }

        console.log('[MMD Manager] 初始化完成');
    }

    // ═══════════════════ 模型加载 ═══════════════════

    async loadModel(modelPath, options = {}) {
        this._pendingModelLoadCount += 1;
        this._isLoadingModel = true;
        try {
            return await this._loadModelImplementation(modelPath, options);
        } finally {
            this._pendingModelLoadCount = Math.max(0, this._pendingModelLoadCount - 1);
            this._isLoadingModel = this._pendingModelLoadCount > 0;
        }
    }

    async _loadModelImplementation(modelPath, options = {}) {
        if (!this.core) throw new Error('MMDCore 未初始化');

        this._isModelReadyForInteraction = false;
        this._activeLoadToken++;
        const loadToken = this._activeLoadToken;
        this._modelLoadState = 'loading';

        try {
            const modelInfo = await this.core.loadModel(modelPath, options, loadToken);

            // 检查是否已被新的加载请求取代或已 dispose
            if (this._isDisposed || this._activeLoadToken !== loadToken) {
                console.log('[MMD Manager] 模型加载已被取代或已销毁');
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

            // 再次检查（loadMoodMap 是异步的）
            if (this._isDisposed || this._activeLoadToken !== loadToken) {
                console.log('[MMD Manager] 模型加载已被取代或已销毁（表情加载后）');
                return null;
            }

            this._isModelReadyForInteraction = true;

            // 应用保存的局部跟踪设置
            if (this.cursorFollow) {
                this.cursorFollow.setLocalTrackingEnabled(window.humanoidLocalTrackingEnabled === true);
            }

            // 兜底：模型加载成功后再 setup 一次浮动按钮（与 VRM 对齐）。
            // init() 阶段虽然也会调用，但若彼时 lanlan_config 还没切到 mmd（早期启动 / 跨模型切换），
            // 守卫会让 setup 静默退出，且不会再有第二次机会。loadModel 成功后必然处于 mmd 模式，
            // 在此补一刀确保按钮始终存在。
            if (typeof this.setupFloatingButtons === 'function' && !window._cardExportPage) {
                try {
                    this.setupFloatingButtons();
                } catch (err) {
                    console.warn('[MMD Manager] setupFloatingButtons 失败 (loadModel success path):', err);
                }
            }

            // 派发模型加载完成事件
            window.dispatchEvent(new CustomEvent('mmd-model-loaded', {
                detail: { modelInfo, modelPath }
            }));

            return modelInfo;
        } catch (error) {
            if (this._activeLoadToken !== loadToken) return null;
            console.error('[MMD Manager] 模型加载失败:', error);

            // 已被新的加载请求取代或已 dispose：不再回退，避免迟到的回退加载
            // 清掉新请求装好的模型
            if (this._isDisposed || this._activeLoadToken !== loadToken) {
                console.log('[MMD Manager] 模型加载已被取代或已销毁，跳过回退');
                return null;
            }

            // 尝试回退到默认模型
            const defaultModelPath = MMDManager.DEFAULT_MODEL_PATH;
            if (modelPath !== defaultModelPath) {
                console.warn('[MMD Manager] 模型加载失败，尝试回退到默认模型:', defaultModelPath);
                try {
                    const modelInfo = await this.core.loadModel(defaultModelPath, options, loadToken);

                    if (this._isDisposed || this._activeLoadToken !== loadToken) {
                        console.log('[MMD Manager] 回退模型加载已被取代或已销毁');
                        return null;
                    }

                    if (this.cursorFollow) {
                        this.cursorFollow.refresh();
                    }

                    if (this.expression && modelInfo.name) {
                        await this.expression.loadMoodMap(modelInfo.name);
                    }

                    if (this._isDisposed || this._activeLoadToken !== loadToken) {
                        return null;
                    }

                    this._isModelReadyForInteraction = true;

                    if (this.cursorFollow) {
                        this.cursorFollow.setLocalTrackingEnabled(window.humanoidLocalTrackingEnabled === true);
                    }

                    // 兜底：回退模型加载成功后也补一次 setupFloatingButtons（与 VRM 对齐）
                    if (typeof this.setupFloatingButtons === 'function' && !window._cardExportPage) {
                        try {
                            this.setupFloatingButtons();
                        } catch (err) {
                            console.warn('[MMD Manager] setupFloatingButtons 失败 (fallback model path):', err);
                        }
                    }

                    window.dispatchEvent(new CustomEvent('mmd-model-loaded', {
                        detail: { modelInfo, modelPath: defaultModelPath }
                    }));

                    console.log('[MMD Manager] 成功回退到默认模型:', defaultModelPath);
                    return modelInfo;
                } catch (fallbackError) {
                    console.error('[MMD Manager] 回退到默认模型也失败:', fallbackError);
                    throw new Error(`原始模型加载失败: ${error.message}，且回退模型也失败: ${fallbackError.message}`);
                }
            } else {
                throw error;
            }
        } finally {
            if (this._activeLoadToken === loadToken && this._modelLoadState === 'loading') {
                this._modelLoadState = this._isModelReadyForInteraction ? 'ready' : 'idle';
            }
        }
    }

    // ═══════════════════ 动画 ═══════════════════

    async loadAnimation(vmdPath, options = {}) {
        // 防御性检查：如果 animationModule 未初始化（并行加载竞态），尝试延迟初始化
        if (!this.animationModule) {
            if (typeof MMDAnimation !== 'undefined') {
                this.animationModule = new MMDAnimation(this);
                console.warn('[MMD Manager] animationModule 延迟初始化成功（并行加载竞态）');
            } else {
                throw new Error('MMDAnimation 未初始化');
            }
        }
        const clip = await this.animationModule.loadAnimation(vmdPath, options);
        this.currentAnimationUrl = vmdPath;
        return clip;
    }

    /**
     * 播放动画
     * @param {'idle'|'dance'} mode - 动画模式，影响视线跟踪权重
     */
    playAnimation(mode = 'idle') {
        // 供空闲低频 governor 判定：idle 循环动画按 Live2D 先例以地板帧率渲染，
        // 只有非 idle 播放（dance 等）才算需要满帧的活动
        this._currentAnimationMode = mode;
        if (this.cursorFollow) {
            this.cursorFollow.setAnimationMode(mode);
        }
        if (this.animationModule) {
            this.animationModule.play();
        }
        if (mode !== 'idle' && this.core && typeof this.core._boostInteractiveFPS === 'function') {
            this.core._boostInteractiveFPS();
        }
    }

    pauseAnimation() {
        if (this.animationModule) {
            this.animationModule.pause();
        }
    }

    stopAnimation() {
        this._currentAnimationMode = null;
        if (this.animationModule) {
            this.animationModule.stop();
        }
        this.currentAnimationUrl = null;
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

    waitForRenderFrame(timeoutMs = 2000) {
        if (this.core && typeof this.core.waitForRenderFrame === 'function') {
            return this.core.waitForRenderFrame(timeoutMs);
        }
        return Promise.resolve();
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
                this._userForcedOutline = true; // 用户手动设置描边
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

    // ═══════════════════ 模型位置/姿态重置 ═══════════════════

    resetModelPosition() {
        if (this.core && typeof this.core.resetModelPosition === 'function') {
            this.core.resetModelPosition();
        }
    }

    resetModelPose() {
        if (this.core && typeof this.core.resetModelPose === 'function') {
            this.core.resetModelPose();
        }
    }

    // ═══════════════════ 渲染控制 ═══════════════════

    pauseRendering() {
        this._shouldRender = false;
        // 空闲低频模式下 _animationFrameId 为 null（interval 驱动），
        // 必须显式退出该模式（不复跑），否则暂停对 interval 无效
        if (this.core && typeof this.core._exitIdleTickMode === 'function') {
            this.core._exitIdleTickMode(false);
        }
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

    setGameModeResourceProtection(phase) {
        const next = phase === 'deep_sleep' ? 'deep_sleep'
            : (phase === 'soft_protected' ? 'soft_protected' : 'idle');
        const wasProtected = this._gameModeResourcePhase && this._gameModeResourcePhase !== 'idle';
        if (!wasProtected && next !== 'idle') {
            this._gameModeResourceCursorFollowEnabled = this.cursorFollow
                ? this.cursorFollow.enabled !== false
                : null;
            this._gameModeResourceMouseTrackingEnabled = window.mouseTrackingEnabled !== false;
        }
        this._gameModeResourcePhase = next;
        if (this.cursorFollow && typeof this.cursorFollow.setEnabled === 'function') {
            if (next !== 'idle') {
                this.cursorFollow.setEnabled(false);
            } else if (this._gameModeResourceCursorFollowEnabled !== null) {
                const currentMouseTrackingEnabled = window.mouseTrackingEnabled !== false;
                const mouseTrackingChanged = this._gameModeResourceMouseTrackingEnabled !== null
                    && currentMouseTrackingEnabled !== this._gameModeResourceMouseTrackingEnabled;
                const restoreCursorFollow = mouseTrackingChanged
                    ? currentMouseTrackingEnabled && window.nekoYuiGuideFaceForwardLock !== true
                    : this._gameModeResourceCursorFollowEnabled === true;
                this.cursorFollow.setEnabled(restoreCursorFollow);
                this._gameModeResourceCursorFollowEnabled = null;
                this._gameModeResourceMouseTrackingEnabled = null;
            }
        }
        if (next === 'deep_sleep' && this._shouldRender) {
            this._gameModeResourcePausedRendering = true;
            this.pauseRendering();
        } else if (next !== 'deep_sleep' && this._gameModeResourcePausedRendering) {
            this._gameModeResourcePausedRendering = false;
            this.resumeRendering();
        }
    }

    translateModelByScreenPixels(deltaX, deltaY) {
        const mesh = this.currentModel && this.currentModel.mesh;
        const camera = this.camera;
        const canvas = this.renderer && this.renderer.domElement;
        if (!mesh || !camera || !canvas || !window.THREE) return false;
        const rect = canvas.getBoundingClientRect();
        const dx = Number(deltaX);
        const dy = Number(deltaY);
        if (!rect.width || !rect.height || !Number.isFinite(dx) || !Number.isFinite(dy)) return false;
        const distance = Math.max(0.001, camera.position.distanceTo(mesh.position));
        const worldHeight = 2 * Math.tan((camera.fov * Math.PI / 180) / 2) * distance;
        const worldWidth = worldHeight * (camera.aspect || (rect.width / rect.height));
        const right = new window.THREE.Vector3(1, 0, 0).applyQuaternion(camera.quaternion);
        const up = new window.THREE.Vector3(0, 1, 0).applyQuaternion(camera.quaternion);
        mesh.position.add(right.multiplyScalar(dx * worldWidth / rect.width));
        mesh.position.add(up.multiplyScalar(-dy * worldHeight / rect.height));
        return true;
    }

    // ═══════════════════ 清理 ═══════════════════

    cleanupUI() {
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

        console.log('[MMD Manager] UI 已清理');
    }

    /**
     * 获取 MMD 头部在屏幕上的锚点
     * @returns {Object|null} 锚点对象 { x, y } 或 null
     */
    getHeadScreenAnchor() {
        if (!this.currentModel || !this.camera || !this.renderer || !window.THREE) {
            return null;
        }
        if (!this.cursorFollow || typeof this.cursorFollow.getHeadWorldPosition !== 'function') {
            return null;
        }

        const headWorldPos = this.cursorFollow.getHeadWorldPosition();
        if (!headWorldPos) {
            return null;
        }

        const canvas = this.renderer.domElement;
        if (!canvas) return null;

        const canvasRect = canvas.getBoundingClientRect();
        if (!canvasRect.width || !canvasRect.height) return null;

        if (!this._headScreenAnchorProjection) {
            this._headScreenAnchorProjection = new window.THREE.Vector3();
        }

        this.camera.updateMatrixWorld(true);
        this._headScreenAnchorProjection.copy(headWorldPos).project(this.camera);

        if (!Number.isFinite(this._headScreenAnchorProjection.x) ||
            !Number.isFinite(this._headScreenAnchorProjection.y)) {
            return null;
        }

        return {
            x: canvasRect.left + (this._headScreenAnchorProjection.x * 0.5 + 0.5) * canvasRect.width,
            y: canvasRect.top + (-this._headScreenAnchorProjection.y * 0.5 + 0.5) * canvasRect.height
        };
    }

    getHeadDetectionGeometryInfo() {
        const bounds = this.getModelScreenBounds();
        if (!bounds) {
            return null;
        }

        const headAnchor = this.getHeadScreenAnchor();
        return {
            type: 'mmd',
            bounds,
            rawHeadAnchor: headAnchor || null,
            headAnchor: headAnchor || null,
            headRect: null,
            headMode: 'head',
            headSource: 'bone',
            bodyRect: null,
            bodySource: null,
            reliableHeadRect: false,
            preciseDisplayInfoRect: false,
            coarseHitAreaHeadRect: false
        };
    }

    /**
     * 获取 MMD 模型在屏幕上的边界（用于局部跟踪）
     * @returns {Object|null} 边界对象 { left, right, top, bottom, width, height, centerX, centerY } 或 null
     */
    getModelScreenBounds() {
        if (!this.currentModel || !this.camera || !this.renderer) {
            return null;
        }

        const canvasRect = this.renderer.domElement.getBoundingClientRect();
        const canvasWidth = canvasRect.width;
        const canvasHeight = canvasRect.height;
        if (!(canvasWidth > 0) || !(canvasHeight > 0)) {
            return null;
        }

        const mesh = this.currentModel.mesh;
        if (!mesh) return null;

        const box = new window.THREE.Box3().setFromObject(mesh);
        const corners = [
            new window.THREE.Vector3(box.min.x, box.min.y, box.min.z),
            new window.THREE.Vector3(box.min.x, box.min.y, box.max.z),
            new window.THREE.Vector3(box.min.x, box.max.y, box.min.z),
            new window.THREE.Vector3(box.min.x, box.max.y, box.max.z),
            new window.THREE.Vector3(box.max.x, box.min.y, box.min.z),
            new window.THREE.Vector3(box.max.x, box.min.y, box.max.z),
            new window.THREE.Vector3(box.max.x, box.max.y, box.min.z),
            new window.THREE.Vector3(box.max.x, box.max.y, box.max.z)
        ];

        let screenLeft = Infinity, screenRight = -Infinity;
        let screenTop = Infinity, screenBottom = -Infinity;

        for (const corner of corners) {
            corner.project(this.camera);
            const sx = canvasRect.left + (corner.x * 0.5 + 0.5) * canvasWidth;
            const sy = canvasRect.top + (-corner.y * 0.5 + 0.5) * canvasHeight;
            screenLeft = Math.min(screenLeft, sx);
            screenRight = Math.max(screenRight, sx);
            screenTop = Math.min(screenTop, sy);
            screenBottom = Math.max(screenBottom, sy);
        }

        if (!Number.isFinite(screenLeft) || !Number.isFinite(screenRight) ||
            !Number.isFinite(screenTop) || !Number.isFinite(screenBottom)) {
            return null;
        }

        const width = screenRight - screenLeft;
        const height = screenBottom - screenTop;
        if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 2 || height <= 2) {
            return null;
        }

        return {
            left: screenLeft,
            right: screenRight,
            top: screenTop,
            bottom: screenBottom,
            width: width,
            height: height,
            centerX: (screenLeft + screenRight) / 2,
            centerY: (screenTop + screenBottom) / 2
        };
    }

    dispose() {
        this._isDisposed = true;
        this._shouldRender = false;
        this._isModelReadyForInteraction = false;
        this._activeLoadToken++;  // 使进行中的 loadModel 失效

        // 先清理 UI
        this.cleanupUI();

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
        this._headScreenAnchorProjection = null;
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

window.MMDManager = MMDManager;
