/**
 * VRM 核心模块 - 负责场景初始化、模型加载、性能管理等核心功能
 */

class VRMCore {
    constructor(manager) {
        this.manager = manager;
        this.vrmVersion = null;
        this.performanceMode = this.detectPerformanceMode();
        this.targetFPS = this.performanceMode === 'low' ? 30 : (this.performanceMode === 'medium' ? 45 : 60);
        this.frameTime = 1000 / this.targetFPS;
        this.lastFrameTime = 0;
        this.frameCount = 0;
        this.lastFPSUpdate = 0;
        this.currentFPS = 0;
    }

    static _vrmUtilsCache = null;
    static async _getVRMUtils() {
        if (VRMCore._vrmUtilsCache) {
            return VRMCore._vrmUtilsCache;
        }
        try {
            const { VRMUtils } = await import('@pixiv/three-vrm');
            VRMCore._vrmUtilsCache = VRMUtils;
            return VRMUtils;
        } catch (error) {
            console.warn('[VRM Core] 无法导入 VRMUtils:', error);
            return null;
        }
    }

    /**
     * 检测设备性能模式
     */
    detectPerformanceMode() {
        // 尝试从 localStorage 获取保存的模式（在受限环境中可能抛出异常）
        let savedMode = null;
        try {
            savedMode = localStorage.getItem('vrm_performance_mode');
            if (savedMode && ['low', 'medium', 'high'].includes(savedMode)) {
                return savedMode;
            }
        } catch (e) {
            // localStorage 访问失败，继续使用 canvas/feature 检测
        }
        
        try {
            const canvas = document.createElement('canvas');
            const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
            
            if (!gl) {
                return 'low';
            }
            
            const debugInfo = gl.getExtension('WEBGL_debug_renderer_info');
            if (debugInfo) {
                const renderer = gl.getParameter(debugInfo.UNMASKED_RENDERER_WEBGL);
                const isLowEndGPU = 
                    renderer.includes('Intel') && 
                    (renderer.includes('HD Graphics') || renderer.includes('Iris') || renderer.includes('UHD'));
                const isMobile = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent);
                const isLowEndMobile = isMobile && navigator.hardwareConcurrency <= 4;
                
                if (isLowEndGPU || isLowEndMobile) {
                    return 'low';
                }
            }
            
            const cores = navigator.hardwareConcurrency || 4;
            if (cores <= 2) {
                return 'low';
            } else if (cores <= 4) {
                return 'medium';
            }
            
            return 'high';
        } catch (e) {
            return 'medium';
        }
    }

    ensureFloatingButtons() {
        return;
    }

    /**
     * 检测 VRM 模型版本
     */
    detectVRMVersion(vrm) {
        try {
            if (vrm.meta) {
                if (vrm.meta.vrmVersion || vrm.meta.metaVersion) {
                    const version = vrm.meta.vrmVersion || vrm.meta.metaVersion;
                    if (version && (version.startsWith('1') || version.includes('1.0'))) {
                        return '1.0';
                    }
                }
                
                if (vrm.humanoid && vrm.humanoid.humanBones) {
                    const boneNames = Object.keys(vrm.humanoid.humanBones);
                    if (boneNames.length > 50) {
                        return '1.0';
                    }
                }
                
                if (vrm.expressionManager && vrm.expressionManager.expressions) {
                    let exprCount;
                    if (vrm.expressionManager.expressions instanceof Map) {
                        exprCount = vrm.expressionManager.expressions.size;
                    } else {
                        exprCount = Object.keys(vrm.expressionManager.expressions).length;
                    }
                    if (exprCount > 10) {
                        return '1.0';
                    }
                }
            }
            
            return '0.0';
        } catch (error) {
            return '0.0';
        }
    }

    /**
     * 设置锁定状态并同步更新 UI
     * @param {boolean} locked - 是否锁定
     */
    setLocked(locked) {
        this.manager.isLocked = locked;

        if (this._lockIconImages) {
            const { locked: imgLocked, unlocked: imgUnlocked } = this._lockIconImages;
            if (imgLocked) imgLocked.style.opacity = locked ? '1' : '0';
            if (imgUnlocked) imgUnlocked.style.opacity = locked ? '0' : '1';
        } else {
            const lockIcon = document.getElementById('vrm-lock-icon');
            if (lockIcon) {
                lockIcon.style.backgroundImage = locked ? 'url(/static/icons/locked_icon.png)' : 'url(/static/icons/unlocked_icon.png)';
            }
        }

        if (this.manager.interaction && typeof this.manager.interaction.setLocked === 'function') {
            this.manager.interaction.setLocked(locked);
        }

        if (this.manager.controls) {
            this.manager.controls.enablePan = !locked;
        }

        if (window.live2dManager) {
            window.live2dManager.isLocked = locked;
        }

        const buttonsContainer = document.getElementById('vrm-floating-buttons');
        if (buttonsContainer) {
            buttonsContainer.style.display = locked ? 'none' : 'flex';
        }
    }

    /**
     * 应用性能设置
     */
    applyPerformanceSettings() {
        if (!this.manager.renderer) return;
        
        const devicePixelRatio = window.devicePixelRatio || 1;
        let pixelRatio;
        
        if (this.performanceMode === 'low') {
            pixelRatio = Math.min(0.75, devicePixelRatio);
        } else if (this.performanceMode === 'medium') {
            pixelRatio = Math.min(1.0, devicePixelRatio);
        } else {
            // 高性能模式：可以使用更高的值
            pixelRatio = Math.min(2.0, devicePixelRatio);
        }
        
        // 确保 pixelRatio 至少为 0.5（避免过低的渲染质量）
        pixelRatio = Math.max(0.5, pixelRatio);
        
        this.manager.renderer.setPixelRatio(pixelRatio);
    }

    /**
     * 优化材质设置
     */
    optimizeMaterials() {
        if (!this.manager.currentModel || !this.manager.currentModel.vrm || !this.manager.currentModel.vrm.scene) return;
        
        this.manager.currentModel.vrm.scene.traverse((object) => {
            if (object.isMesh || object.isSkinnedMesh) {
                object.castShadow = true;
                object.receiveShadow = true;
                const objectName = object.name.toLowerCase();
                if (objectName.includes('face') || objectName.includes('skin') || objectName.includes('head')) {
                    object.receiveShadow = false;
                }
            }
        });
    }

    /**
     * 初始化场景
     */
    async init(canvasId, containerId) {
        const THREE = window.THREE;
        if (!THREE) {
            const errorMsg = window.t ? window.t('vrm.error.threeNotLoaded') : 'Three.js库未加载，请确保已引入three.js';
            throw new Error(errorMsg);
        }

        this.manager.container = document.getElementById(containerId);
        this.manager.canvas = document.getElementById(canvasId);

        if (this.manager.canvas && !this.manager.canvas.id) {
            this.manager.canvas.id = canvasId;
        }

        if (!this.manager.container) {
            const errorMsg = window.t ? window.t('vrm.error.containerNotFound', { id: containerId }) : `找不到容器元素: ${containerId}`;
            throw new Error(errorMsg);
        }

        if (!this.manager.canvas) {
            const errorMsg = window.t ? window.t('vrm.error.canvasNotFound', { id: canvasId }) : `找不到canvas元素: ${canvasId}`;
            throw new Error(errorMsg);
        }

        this.manager.container.style.display = 'block';
        this.manager.container.style.visibility = 'visible';
        this.manager.container.style.opacity = '1';
        this.manager.container.style.width = '100%';
        this.manager.container.style.height = '100%';
        this.manager.container.style.position = 'fixed';
        this.manager.container.style.top = '0';
        this.manager.container.style.left = '0';
        this.manager.container.style.setProperty('pointer-events', 'auto', 'important');

        this.manager.clock = new THREE.Clock();
        this.manager.scene = new THREE.Scene();
        this.manager.scene.background = null;

        let width = this.manager.container.clientWidth || this.manager.container.offsetWidth;
        let height = this.manager.container.clientHeight || this.manager.container.offsetHeight;
        if (width === 0 || height === 0) {
            width = window.innerWidth;
            height = window.innerHeight;
        }
        this.manager.camera = new THREE.PerspectiveCamera(30, width / height, 0.1, 2000);
        this.manager.camera.position.set(0, 1.1, 1.5);
        this.manager.camera.lookAt(0, 0.9, 0);

        const antialias = true;
        const precision = 'highp';
        this.manager.renderer = new THREE.WebGLRenderer({ 
            canvas: this.manager.canvas,
            alpha: true, 
            antialias: antialias,
            powerPreference: 'high-performance',
            precision: precision,
            preserveDrawingBuffer: false,
            stencil: false,
            depth: true
        });
        this.manager.renderer.setSize(width, height);
        this.applyPerformanceSettings();
        this.manager.renderer.shadowMap.enabled = true;
        this.manager.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
        if (THREE.SRGBColorSpace !== undefined) {
            // 新 API (r152+)
            this.manager.renderer.outputColorSpace = THREE.SRGBColorSpace;
        } else {
            // 旧 API (r150 及以下)
            this.manager.renderer.outputEncoding = THREE.sRGBEncoding;
        }
        
        //  Linear (最稳妥的方案)
        this.manager.renderer.toneMapping = THREE.LinearToneMapping; 
        this.manager.renderer.toneMappingExposure = 1.0;

        // 确保容器和 canvas 可以接收事件
        const canvas = this.manager.renderer.domElement;
        canvas.style.setProperty('pointer-events', 'auto', 'important');
        canvas.style.setProperty('touch-action', 'none', 'important');
        canvas.style.setProperty('user-select', 'none', 'important');
        canvas.style.cursor = 'grab';
        canvas.style.width = '100%';
        canvas.style.height = '100%';
        canvas.style.display = 'block';

        // 添加轨道控制器
        if (typeof window.OrbitControls !== 'undefined') {
            this.manager.controls = new window.OrbitControls(this.manager.camera, this.manager.renderer.domElement);
            // 禁用旋转功能，只允许平移
            // 缩放功能由 VRMInteraction 手动处理，确保功能正常
            this.manager.controls.enableRotate = false; // 禁用旋转
            this.manager.controls.enablePan = true; // 允许平移
            this.manager.controls.enableZoom = false; // 禁用自动缩放，由 VRMInteraction 手动处理
            // 设置缩放限制
            this.manager.controls.minDistance = 0.5;
            this.manager.controls.maxDistance = 10;
            this.manager.controls.target.set(0, 1, 0);
            this.manager.controls.enableDamping = true;
            this.manager.controls.dampingFactor = 0.1;
            this.manager.controls.update();
        }

        // 添加灯光 - 参考 vroidhub 等专业 VRM 展示平台的打光策略
        this.manager.scene.add(this.manager.camera);

        // 1. 半球光（HemisphereLight）：模拟天空和地面的自然光照，比 AmbientLight 更自然
        // 天空色（上方）使用略微冷色调，地面色（下方）使用略微暖色调
        const hemisphereLight = new THREE.HemisphereLight(
            0xffffff,  // 天空色（上方）
            0x444444,  // 地面色（下方）
            0.4        // 强度
        );
        this.manager.scene.add(hemisphereLight);
        this.manager.ambientLight = hemisphereLight; // 保持兼容性

        // 2. 主光源（Key Light）：从正面偏上45度照射，提供主要照明和立体感
        const mainLight = new THREE.DirectionalLight(0xffffff, 1.2);
        mainLight.position.set(1, 2.5, 2); // 从右上前方45度角照射
        mainLight.castShadow = true;
        mainLight.shadow.mapSize.width = 2048;
        mainLight.shadow.mapSize.height = 2048;
        mainLight.shadow.bias = -0.0001;
        mainLight.shadow.camera.near = 0.1;
        mainLight.shadow.camera.far = 20;
        mainLight.shadow.camera.left = -3;
        mainLight.shadow.camera.right = 3;
        mainLight.shadow.camera.top = 3;
        mainLight.shadow.camera.bottom = -3;
        this.manager.scene.add(mainLight);
        this.manager.mainLight = mainLight;

        // 3. 补光（Fill Light）：从左侧前方柔和补光，减少阴影对比度
        const fillLight = new THREE.DirectionalLight(0xffffff, 0.5);
        fillLight.position.set(-2, 1, 1.5); // 从左侧前方柔和照射
        fillLight.castShadow = false;
        this.manager.scene.add(fillLight);
        this.manager.fillLight = fillLight;

        // 4. 轮廓光（Rim Light）：从背后上方照射，勾勒头发和身体边缘
        const rimLight = new THREE.DirectionalLight(0xffffff, 0.8);
        rimLight.position.set(0, 2, -3); // 从背后上方照射
        rimLight.castShadow = false;
        this.manager.scene.add(rimLight);
        this.manager.rimLight = rimLight;

        // 5. 顶光（Top Light）：从上方照射，增加头顶和肩部的亮度
        const topLight = new THREE.DirectionalLight(0xffffff, 0.3);
        topLight.position.set(0, 4, 0); // 从正上方照射
        topLight.castShadow = false;
        this.manager.scene.add(topLight);
        this.manager.topLight = topLight;

        // 6. 底光（Bottom Light）：从下方轻微照射，减少下巴和颈部的阴影
        const bottomLight = new THREE.DirectionalLight(0xffffff, 0.15);
        bottomLight.position.set(0, -2, 0.5); // 从下方轻微照射
        bottomLight.castShadow = false;
        this.manager.scene.add(bottomLight);
        this.manager.bottomLight = bottomLight;

        // 注册窗口大小调整监听器（使用命名函数，便于后续移除）
        if (!this.manager._windowEventHandlers) {
            this.manager._windowEventHandlers = [];
        }
        
        const resizeHandler = () => {
            if (this.manager && typeof this.manager.onWindowResize === 'function') {
                this.manager.onWindowResize();
            }
        };
        
        this.manager._windowEventHandlers.push({ event: 'resize', handler: resizeHandler });
        window.addEventListener('resize', resizeHandler);
    }

    /**
     * 加载VRM模型
     */
    async loadModel(modelUrl, options = {}) {
        const THREE = window.THREE;
        if (!THREE) {
            const errorMsg = window.t ? window.t('vrm.error.threeNotLoadedForModel') : 'Three.js库未加载，无法加载VRM模型';
            throw new Error(errorMsg);
        }

        // 验证模型路径有效性（防止 undefined 或字符串 "undefined" 被传递）
        if (!modelUrl || 
            modelUrl === 'undefined' || 
            modelUrl === 'null' || 
            (typeof modelUrl === 'string' && (modelUrl.trim() === '' || modelUrl.includes('undefined')))) {
            console.error('[VRM Core] 模型路径无效:', modelUrl, '类型:', typeof modelUrl);
            const errorMsg = window.t ? window.t('vrm.error.invalidModelPath', { path: modelUrl, defaultValue: `VRM 模型路径无效: ${modelUrl}` }) : `VRM 模型路径无效: ${modelUrl}`;
            throw new Error(errorMsg);
        }

        try {
            // 使用全局THREE对象（避免动态import问题）
            const { GLTFLoader } = await import('three/addons/loaders/GLTFLoader.js');
            const VRMLoaderPlugin = (await import('@pixiv/three-vrm')).VRMLoaderPlugin;

            const loader = new GLTFLoader();
            loader.register((parser) => new VRMLoaderPlugin(parser));

            // 辅助函数：加载 GLTF 模型
            const loadGLTF = (url) => {
                return new Promise((resolve, reject) => {
                    loader.load(
                        url,
                        (gltf) => resolve(gltf),
                        (progress) => {
                            if (progress.total > 0) {
                                const percent = (progress.loaded / progress.total) * 100;
                                if (options.onProgress) {
                                    options.onProgress(progress);
                                }
                            }
                        },
                        (error) => reject(error)
                    );
                });
            };

            // 加载 VRM 模型（带备用路径机制）
            let gltf = null;
            
            try {
                gltf = await loadGLTF(modelUrl);
            } catch (error) {
                // 如果加载失败，尝试备用路径
                let fallbackUrl = null;
                if (modelUrl.startsWith('/static/vrm/')) {
                    const filename = modelUrl.replace('/static/vrm/', '');
                    fallbackUrl = `/user_vrm/${filename}`;
                } else if (modelUrl.startsWith('/user_vrm/')) {
                    const filename = modelUrl.replace('/user_vrm/', '');
                    fallbackUrl = `/static/vrm/${filename}`;
                }
                
                if (fallbackUrl) {
                    console.warn(`[VRM Core] 从 ${modelUrl} 加载失败，尝试备用路径: ${fallbackUrl}`);
                    try {
                        gltf = await loadGLTF(fallbackUrl);
                    } catch (fallbackError) {
                        console.error(`[VRM Core] 从备用路径 ${fallbackUrl} 也加载失败:`, fallbackError);
                        const errorMsg = window.t ? window.t('vrm.error.modelLoadFailed', { url: modelUrl, fallback: fallbackUrl }) : `无法加载 VRM 模型: ${modelUrl} 和 ${fallbackUrl} 都失败`;
                        throw new Error(errorMsg);
                    }
                } else {
                    // 其他情况，直接抛出原始错误
                    throw error;
                }
            }

            // 如果已有模型，先移除
            if (this.manager.currentModel && this.manager.currentModel.vrm) {
                // 清理交互模块的事件监听器
                if (this.manager.interaction && typeof this.manager.interaction.cleanupDragAndZoom === 'function') {
                    this.manager.interaction.cleanupDragAndZoom();
                }
                if (this.manager.interaction && typeof this.manager.interaction.cleanupFloatingButtonsMouseTracking === 'function') {
                    this.manager.interaction.cleanupFloatingButtonsMouseTracking();
                }
                
                this.manager.scene.remove(this.manager.currentModel.vrm.scene);
                this.disposeVRM();
            }

            // 确保浮动按钮系统已初始化（如果不存在则创建）
            this.ensureFloatingButtons();

            // 获取 VRM 实例
            const vrm = gltf.userData.vrm;
            if (!vrm) {
                console.error('[VRM] 加载失败: gltf.userData:', gltf.userData);
                console.error('[VRM] 加载失败: gltf.scene:', gltf.scene);
                const errorMsg = window.t ? window.t('vrm.error.invalidVRMFormat', { file: modelUrl }) : `加载的模型不是有效的 VRM 格式。文件: ${modelUrl}`;
                throw new Error(errorMsg);
            }

            // 检测 VRM 模型版本（0.0 或 1.0）
            this.vrmVersion = this.detectVRMVersion(vrm);

            // 计算模型的边界框，用于确定合适的初始大小
            const box = new THREE.Box3().setFromObject(vrm.scene);
            const size = box.getSize(new THREE.Vector3());
            const center = box.getCenter(new THREE.Vector3());

            // 获取保存的用户偏好设置
            let preferences = null;
            try {
                // 添加超时保护（5秒超时）
                const controller = new AbortController();
                const timeoutId = setTimeout(() => controller.abort(), 5000);
                
                let preferencesResponse;
                try {
                    preferencesResponse = await fetch('/api/config/preferences', {
                        signal: controller.signal
                    });
                    clearTimeout(timeoutId);
                } catch (error) {
                    clearTimeout(timeoutId);
                    if (error.name === 'AbortError') {
                        console.warn('[VRM Core] 获取偏好设置请求超时（5秒）');
                        throw new Error('请求超时');
                    }
                    throw error;
                }
                
                if (!preferencesResponse.ok) {
                    clearTimeout(timeoutId);
                    let errorText = '';
                    try {
                        const contentType = preferencesResponse.headers.get('content-type');
                        if (contentType && contentType.includes('application/json')) {
                            const errorData = await preferencesResponse.json();
                            errorText = JSON.stringify(errorData);
                        } else {
                            errorText = await preferencesResponse.text();
                        }
                    } catch (e) {
                        errorText = preferencesResponse.statusText || '未知错误';
                    }
                    throw new Error(`获取偏好设置失败: ${preferencesResponse.status} ${preferencesResponse.statusText}${errorText ? ` - ${errorText}` : ''}`);
                }
                
                const allPreferences = await preferencesResponse.json();

                // 【修复】API返回的格式可能是数组，也可能是 {models: [...]}
                let modelsArray = null;
                if (Array.isArray(allPreferences)) {
                    modelsArray = allPreferences;
                } else if (allPreferences && allPreferences.models && Array.isArray(allPreferences.models)) {
                    modelsArray = allPreferences.models;
                }

                if (modelsArray && modelsArray.length > 0) {
                    preferences = modelsArray.find(pref => pref && pref.model_path === modelUrl);
                }
            } catch (error) {
                console.error('[VRM Core] 获取用户偏好设置失败:', error);
            }

            // 根据是否有保存的偏好设置来决定位置、缩放和旋转
            if (preferences) {
                // 恢复保存的位置（如果有）
                if (preferences.position) {
                    const pos = preferences.position;
                    if (Number.isFinite(pos.x) && Number.isFinite(pos.y) && Number.isFinite(pos.z)) {
                        vrm.scene.position.set(pos.x, pos.y, pos.z);
                    } else {
                        // 如果保存的位置无效，使用默认居中位置
                        vrm.scene.position.set(-center.x, -center.y, -center.z);
                    }
                } else {
                    // 没有保存的位置，使用默认位置
                    vrm.scene.position.set(-center.x, -center.y, -center.z);
                }

                // 恢复保存的缩放（如果有）
                if (preferences.scale) {
                    const scl = preferences.scale;
                    if (Number.isFinite(scl.x) && Number.isFinite(scl.y) && Number.isFinite(scl.z) &&
                        scl.x > 0 && scl.y > 0 && scl.z > 0) {
                        vrm.scene.scale.set(scl.x, scl.y, scl.z);
                    }
                }

                // 【关键修复】恢复保存的旋转（如果有）- 即使没有position/scale也要应用
                if (preferences.rotation) {
                    const rot = preferences.rotation;
                    if (Number.isFinite(rot.x) && Number.isFinite(rot.y) && Number.isFinite(rot.z)) {
                        vrm.scene.rotation.set(rot.x, rot.y, rot.z);
                        vrm.scene.updateMatrixWorld(true);
                    } else {
                        console.warn(`[VRM Core] rotation值无效:`, rot);
                    }
                }
            } else {
                // 没有保存的偏好设置，使用默认位置
                vrm.scene.position.set(-center.x, -center.y, -center.z);
            }

            // 检测并处理模型朝向
            // 等待3帧，确保骨骼位置计算完成
            await new Promise(resolve => {
                let frameCount = 0;
                const waitFrames = () => {
                    frameCount++;
                    if (frameCount >= 3) {
                        resolve();
                    } else {
                        requestAnimationFrame(waitFrames);
                    }
                };
                requestAnimationFrame(waitFrames);
            });
            
            const savedRotation = preferences?.rotation;
            
            const detectedRotation = window.VRMOrientationDetector 
                ? window.VRMOrientationDetector.detectAndFixOrientation(vrm, savedRotation)
                : { x: 0, y: 0, z: 0 };
            
            // 应用检测到的旋转（在渲染前处理）
            if (window.VRMOrientationDetector) {
                window.VRMOrientationDetector.applyRotation(vrm, detectedRotation);
            } else {
                // 降级处理：如果没有检测模块，使用保存的rotation或默认值
                if (savedRotation && 
                    Number.isFinite(savedRotation.x) && 
                    Number.isFinite(savedRotation.y) && 
                    Number.isFinite(savedRotation.z)) {
                    vrm.scene.rotation.set(savedRotation.x, savedRotation.y, savedRotation.z);
                    vrm.scene.updateMatrixWorld(true);
                }
            }
            
            // 如果检测到新的rotation（没有保存的），自动保存
            const hasSavedRotation = savedRotation && 
                Number.isFinite(savedRotation.x) && 
                Number.isFinite(savedRotation.y) && 
                Number.isFinite(savedRotation.z);
            
            if (!hasSavedRotation && typeof this.saveUserPreferences === 'function') {
                // 标准化位置为普通对象 {x, y, z}
                let currentPosition;
                if (preferences?.position) {
                    currentPosition = {
                        x: preferences.position.x,
                        y: preferences.position.y,
                        z: preferences.position.z
                    };
                } else {
                    currentPosition = {
                        x: vrm.scene.position.x,
                        y: vrm.scene.position.y,
                        z: vrm.scene.position.z
                    };
                }
                
                // 标准化缩放为普通对象 {x, y, z}
                let currentScale;
                if (preferences?.scale) {
                    currentScale = {
                        x: preferences.scale.x,
                        y: preferences.scale.y,
                        z: preferences.scale.z
                    };
                } else {
                    currentScale = {
                        x: vrm.scene.scale.x,
                        y: vrm.scene.scale.y,
                        z: vrm.scene.scale.z
                    };
                }
                
                // 异步保存，不阻塞加载流程
                this.saveUserPreferences(
                    modelUrl,
                    currentPosition,
                    currentScale,
                    detectedRotation,
                    null // display
                ).catch(err => {
                    console.error(`[VRM Core] 自动保存rotation时出错:`, err);
                });
            }
            
            // 禁用自动面向相机，保持检测到的朝向
            if (this.manager.interaction) {
                this.manager.interaction.enableFaceCamera = false;
            }

            // 只在没有保存的偏好设置时才计算和应用默认缩放
            if (!preferences || !preferences.scale) {
                // 设置模型初始缩放
                if (options.scale) {
                    vrm.scene.scale.set(options.scale.x || 1, options.scale.y || 1, options.scale.z || 1);
                } else {
                    // 根据模型大小和屏幕大小计算合适的默认缩放
                    const modelHeight = size.y;
                    const screenHeight = window.innerHeight;
                    const screenWidth = window.innerWidth;

                    // 计算合适的初始缩放（参考Live2D的默认大小计算，参考 vrm.js）
                    const isMobile = window.innerWidth <= 768;
                    let targetScale;

                    if (isMobile) {
                        // 移动端：使用固定缩放值，确保模型可见但不会太大
                        targetScale = Math.max(0.4, Math.min(0.8, screenHeight / 1800));
                    } else {
                        // 桌面端：使用更平衡的计算方式
                        if (modelHeight > 0 && Number.isFinite(modelHeight)) {
                            // 目标：让模型在屏幕上的高度约为屏幕高度的0.4-0.5倍
                            const targetScreenHeight = screenHeight * 0.45;
                            
                            // 检查相机是否存在
                            if (this.manager.camera && this.manager.camera.fov) {
                                const fov = this.manager.camera.fov * (Math.PI / 180);
                                const cameraDistance = this.manager.camera.position?.z || 5; // 默认距离 5
                                const worldHeightAtDistance = 2 * Math.tan(fov / 2) * cameraDistance;
                                const scaleRatio = (targetScreenHeight / screenHeight) * (worldHeightAtDistance / modelHeight);
                                
                                // 限制在合理范围内：最小 0.5，最大 1.2
                                targetScale = Math.max(0.5, Math.min(1.2, scaleRatio));
                            } else {
                                // 如果相机不存在，使用简单的比例计算
                                // 假设标准 VRM 模型高度约为 1.5 单位，目标屏幕高度为屏幕的 0.45 倍
                                const standardModelHeight = 1.5;
                                const scaleRatio = (targetScreenHeight / screenHeight) * (standardModelHeight / modelHeight);
                                targetScale = Math.max(0.5, Math.min(1.2, scaleRatio));
                            }
                        } else {
                            // 如果模型高度无效，使用屏幕尺寸计算
                            targetScale = Math.max(0.5, Math.min(1.0, screenHeight / 1200));
                        }
                    }
                    
                    // 确保缩放值在合理范围内（最小 0.5，最大 1.2）
                    targetScale = Math.max(0.5, Math.min(1.2, targetScale));
                    
                    // 应用计算出的 targetScale
                    vrm.scene.scale.setScalar(targetScale);
                }
            }

            // 根据模型大小和屏幕大小计算合适的相机距离
            // 检查相机是否存在
            if (this.manager.camera && this.manager.camera.fov) {
                const modelHeight = size.y;
                const screenHeight = window.innerHeight;
                const screenWidth = window.innerWidth;

                // 目标：让模型在屏幕上的高度约为屏幕高度的0.4-0.5倍（类似Live2D）
                const targetScreenHeight = screenHeight * 0.45;
                const fov = this.manager.camera.fov * (Math.PI / 180);
                const distance = (modelHeight / 2) / Math.tan(fov / 2) / targetScreenHeight * screenHeight;
                
                // isMobile 变量在这个作用域没有定义，需要重新定义或直接判断
                const isMobileDevice = screenWidth <= 768;
                // 调整相机位置，使模型在屏幕中央合适的位置
                const cameraY = center.y + (isMobileDevice ? modelHeight * 0.2 : modelHeight * 0.1);
                const cameraZ = Math.abs(distance);
                this.manager.camera.position.set(0, cameraY, cameraZ);
                this.manager.camera.lookAt(0, center.y, 0);
            } else {
                console.warn('[VRM Core] 相机未初始化，跳过相机位置调整');
            }
            
            // 添加到场景 - 确保场景已初始化
            if (!this.manager.scene) {
                const errorMsg = window.t ? window.t('vrm.error.sceneNotInitializedForAdd') : '场景未初始化。请先调用 initThreeJS() 初始化场景。';
                throw new Error(errorMsg);
            }
            
            this.manager.scene.add(vrm.scene);

            // 优化材质设置（根据性能模式）
            this.optimizeMaterials();

            // 更新控制器目标
            if (this.manager.controls) {
                this.manager.controls.target.set(0, center.y, 0);
                this.manager.controls.update();
            }

            // 渲染一次
            if (this.manager.renderer && this.manager.scene && this.manager.camera) {
                this.manager.renderer.render(this.manager.scene, this.manager.camera);
            }

            // 确保 humanoid 正确更新（防止T-Pose）
            if (vrm.humanoid) {
                // 确保 autoUpdateHumanBones 已启用
                if (vrm.humanoid.autoUpdateHumanBones !== undefined && !vrm.humanoid.autoUpdateHumanBones) {
                    vrm.humanoid.autoUpdateHumanBones = true;
                }
                // 立即更新一次 humanoid，确保骨骼状态正确
                vrm.humanoid.update();
            }

            // 创建动画混合器
            this.manager.animationMixer = new THREE.AnimationMixer(vrm.scene);

            // 播放模型自带的动画（如果有）
            if (gltf.animations && gltf.animations.length > 0) {
                const action = this.manager.animationMixer.clipAction(gltf.animations[0]);
                action.play();
            }

            // 保存模型引用
            this.manager.currentModel = {
                vrm: vrm,
                gltf: gltf,
                scene: vrm.scene,
                url: modelUrl
            };

            // 更新口型表情映射（如果animation模块存在）
            if (this.manager.animation && typeof this.manager.animation.updateMouthExpressionMapping === 'function') {
                this.manager.animation.updateMouthExpressionMapping();
            }

            

            // 锁图标由 setupFloatingButtons() 创建，不需要单独设置

            // 启用鼠标跟踪（用于控制浮动按钮显示/隐藏）
            if (this.manager.interaction && typeof this.manager.interaction.enableMouseTracking === 'function') {
                this.manager.interaction.enableMouseTracking(true);
            }

            return this.manager.currentModel;
        } catch (error) {
            console.error('加载 VRM 模型失败:', error);
            throw error;
        }
    }

    /**
     * 清理 VRM 资源
     */
    async disposeVRM() {
        if (!this.manager.currentModel || !this.manager.currentModel.vrm) return;
        
        const vrm = this.manager.currentModel.vrm;
        
        // 清理表情模块的定时器和状态
        if (this.manager.expression) {
            if (this.manager.expression.neutralReturnTimer) {
                clearTimeout(this.manager.expression.neutralReturnTimer);
                this.manager.expression.neutralReturnTimer = null;
            }
            // 重置表情状态
            this.manager.expression.currentWeights = {};
            this.manager.expression.manualBlinkInProgress = null;
            this.manager.expression.manualExpressionInProgress = null;
            this.manager.expression.currentMood = 'neutral';
        }
        
        // 清理动画模块的定时器
        if (this.manager.animation) {
            if (this.manager.animation._springBoneTimer) {
                clearTimeout(this.manager.animation._springBoneTimer);
                this.manager.animation._springBoneTimer = null;
            }
            if (typeof this.manager.animation.stopVRMAAnimation === 'function') {
                this.manager.animation.stopVRMAAnimation();
            }
        }
        
        // 清理交互模块的定时器
        if (this.manager.interaction) {
            if (this.manager.interaction._hideButtonsTimer) {
                clearTimeout(this.manager.interaction._hideButtonsTimer);
                this.manager.interaction._hideButtonsTimer = null;
            }
            if (this.manager.interaction._savePositionDebounceTimer) {
                clearTimeout(this.manager.interaction._savePositionDebounceTimer);
                this.manager.interaction._savePositionDebounceTimer = null;
            }
        }
        
        // 清理自动播放动画的重试 timer
        if (this.manager._retryTimerId) {
            clearTimeout(this.manager._retryTimerId);
            this.manager._retryTimerId = null;
        }
        
        if (this.manager.animationMixer) {
            if (vrm.scene) {
                this.manager.animationMixer.uncacheRoot(vrm.scene);
            }
            this.manager.animationMixer.stopAllAction();
            this.manager.animationMixer = null;
        }

        if (vrm.scene) {
            // 使用 VRMUtils.deepDispose 清理场景（推荐方式，更安全且符合库的设计）
            // 这会自动清理所有几何体、材质、贴图等资源
            try {
                const VRMUtils = await VRMCore._getVRMUtils();
                if (VRMUtils && typeof VRMUtils.deepDispose === 'function') {
                    VRMUtils.deepDispose(vrm.scene);
                } else {
                    // 如果 VRMUtils 不可用，回退到手动清理
                    throw new Error('VRMUtils.deepDispose 不可用');
                }
            } catch (error) {
                // 如果导入失败，回退到手动清理（兼容性处理）
                console.warn('[VRM Core] 无法使用 VRMUtils.deepDispose，回退到手动清理:', error);
                // 清理 VRMLookAtQuaternionProxy（如果存在）
                const lookAtProxy = vrm.scene.getObjectByName('lookAtQuaternionProxy');
                if (lookAtProxy) {
                    vrm.scene.remove(lookAtProxy);
                }
                
                vrm.scene.traverse((object) => {
                    if (object.geometry) object.geometry.dispose();
                    if (object.material) {
                        if (Array.isArray(object.material)) {
                            object.material.forEach(m => {
                                if (m.map) m.map.dispose();
                                if (m.normalMap) m.normalMap.dispose();
                                if (m.roughnessMap) m.roughnessMap.dispose();
                                if (m.metalnessMap) m.metalnessMap.dispose();
                                if (m.emissiveMap) m.emissiveMap.dispose();
                                if (m.aoMap) m.aoMap.dispose();
                                m.dispose();
                            });
                        } else {
                            if (object.material.map) object.material.map.dispose();
                            if (object.material.normalMap) object.material.normalMap.dispose();
                            if (object.material.roughnessMap) object.material.roughnessMap.dispose();
                            if (object.material.metalnessMap) object.material.metalnessMap.dispose();
                            if (object.material.emissiveMap) object.material.emissiveMap.dispose();
                            if (object.material.aoMap) object.material.aoMap.dispose();
                            object.material.dispose();
                        }
                    }
                });
            }
        }
        
        // 清理 currentModel 引用（在清理完成后才设置为 null）
        this.manager.currentModel = null;
    }

    /**
     * 保存用户偏好设置（位置、缩放等）
     * @param {string} modelPath - 模型路径
     * @param {object} position - 位置 {x, y, z}
     * @param {object} scale - 缩放 {x, y, z}
     * @param {object} rotation - 旋转 {x, y, z}（可选）
     * @param {object} display - 显示器信息（可选）
     * @returns {Promise<boolean>} 是否保存成功
     */
    async saveUserPreferences(modelPath, position, scale, rotation, display) {
        try {
            // 验证位置值
            if (!position || typeof position !== 'object' ||
                !Number.isFinite(position.x) || !Number.isFinite(position.y) || !Number.isFinite(position.z)) {
                console.error('[VRM] 位置值无效:', position);
                return false;
            }

            // 验证缩放值（VRM使用统一缩放，但保存为对象格式以兼容Live2D的数据结构）
            if (!scale || typeof scale !== 'object' ||
                !Number.isFinite(scale.x) || !Number.isFinite(scale.y) || !Number.isFinite(scale.z)) {
                console.error('[VRM] 缩放值无效:', scale);
                return false;
            }

            // 验证缩放值必须为正数
            if (scale.x <= 0 || scale.y <= 0 || scale.z <= 0) {
                console.error('[VRM] 缩放值必须为正数:', scale);
                return false;
            }

            const preferences = {
                model_path: modelPath,
                position: position,
                scale: scale
            };

            // 如果有旋转信息，添加到偏好中
            if (rotation && typeof rotation === 'object' &&
                Number.isFinite(rotation.x) && Number.isFinite(rotation.y) && Number.isFinite(rotation.z)) {
                preferences.rotation = rotation;
            }

            // 如果有显示器信息，添加到偏好中（用于多屏幕位置恢复）
            if (display && typeof display === 'object' &&
                Number.isFinite(display.screenX) && Number.isFinite(display.screenY)) {
                preferences.display = {
                    screenX: display.screenX,
                    screenY: display.screenY
                };
            }
            
            // 添加超时保护（5秒超时）
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 5000);
            
            let response;
            try {
                response = await fetch('/api/config/preferences', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify(preferences),
                    signal: controller.signal
                });
                
                clearTimeout(timeoutId);
            } catch (error) {
                clearTimeout(timeoutId);
                if (error.name === 'AbortError') {
                    console.warn('[VRM Core] 保存偏好设置请求超时（5秒）');
                    throw new Error('请求超时');
                }
                throw error;
            }

            if (!response.ok) {
                clearTimeout(timeoutId);
                let errorText = '';
                try {
                    const contentType = response.headers.get('content-type');
                    if (contentType && contentType.includes('application/json')) {
                        const errorData = await response.json();
                        errorText = JSON.stringify(errorData);
                    } else {
                        errorText = await response.text();
                    }
                } catch (e) {
                    errorText = response.statusText || '未知错误';
                }
                throw new Error(`保存偏好设置失败: ${response.status} ${response.statusText}${errorText ? ` - ${errorText}` : ''}`);
            }

            const result = await response.json();
            
            return result.success || false;
        } catch (error) {
            console.error('[VRM] 保存用户偏好失败:', error);
            return false;
        }
    }
}

// 导出到全局
window.VRMCore = VRMCore;

