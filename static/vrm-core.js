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

    /**
     * 检测设备性能模式
     */
    detectPerformanceMode() {
        const savedMode = localStorage.getItem('vrm_performance_mode');
        if (savedMode && ['low', 'medium', 'high'].includes(savedMode)) {
            return savedMode;
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

    /**
     * 确保浮动按钮系统已初始化（如果不存在则创建）
     */
    ensureFloatingButtons() {
        // 检查是否在主页面（有chat-container）
        if (!document.getElementById('chat-container')) {
            return;
        }

        // 检查浮动按钮容器是否已存在
        let buttonsContainer = document.getElementById('live2d-floating-buttons');
        
        if (!buttonsContainer) {
            // 如果不存在，创建浮动按钮容器（参考 live2d-ui-buttons.js）
            buttonsContainer = document.createElement('div');
            buttonsContainer.id = 'live2d-floating-buttons';
            Object.assign(buttonsContainer.style, {
                position: 'fixed',
                zIndex: '99999',
                pointerEvents: 'none',
                display: 'none', // 初始隐藏，鼠标靠近时才显示
                flexDirection: 'column',
                gap: '12px'
            });

            // 阻止浮动按钮容器上的指针事件传播
            const stopContainerEvent = (e) => {
                e.stopPropagation();
            };
            buttonsContainer.addEventListener('pointerdown', stopContainerEvent, true);
            buttonsContainer.addEventListener('pointermove', stopContainerEvent, true);
            buttonsContainer.addEventListener('pointerup', stopContainerEvent, true);
            buttonsContainer.addEventListener('mousedown', stopContainerEvent, true);
            buttonsContainer.addEventListener('mousemove', stopContainerEvent, true);
            buttonsContainer.addEventListener('mouseup', stopContainerEvent, true);
            buttonsContainer.addEventListener('touchstart', stopContainerEvent, true);
            buttonsContainer.addEventListener('touchmove', stopContainerEvent, true);
            buttonsContainer.addEventListener('touchend', stopContainerEvent, true);

            document.body.appendChild(buttonsContainer);
        }

        // 如果 Live2D 管理器存在且有 setupFloatingButtons 方法，调用它来初始化按钮
        if (window.live2dManager && typeof window.live2dManager.setupFloatingButtons === 'function') {
            // 检查 pixi_app 是否已初始化，如果没有则先初始化
            if (!window.live2dManager.pixi_app) {
                // 尝试初始化 PixiJS 应用（如果还没有初始化）
                try {
                    if (typeof window.live2dManager.initPixiApp === 'function') {
                        window.live2dManager.initPixiApp();
                    } else if (typeof window.live2dManager.getPixiApp === 'function') {
                        window.live2dManager.getPixiApp();
                    }
                } catch (initError) {
                }
            }

            // 如果 pixi_app 仍然不存在，创建一个虚拟的 ticker
            if (!window.live2dManager.pixi_app) {
                // 创建一个虚拟的 pixi_app 对象，只包含 ticker
                window.live2dManager.pixi_app = {
                    ticker: {
                        add: (callback) => {
                            // 使用 requestAnimationFrame 来模拟 ticker
                            const animate = () => {
                                callback();
                                requestAnimationFrame(animate);
                            };
                            requestAnimationFrame(animate);
                        },
                        remove: () => {} // 空函数，不需要移除
                    }
                };
            }

            // 创建一个虚拟模型对象，用于位置更新（对于 VRM，位置更新将由 VRM 管理器处理）
            const virtualModel = {
                parent: true, // 让 tick 函数认为模型存在
                getBounds: () => {
                    // 返回 VRM 模型的屏幕边界（如果可用）
                    if (this.manager && this.manager.currentModel && this.manager.currentModel.vrm) {
                        // 尝试从 VRM 模型获取边界
                        const vrm = this.manager.currentModel.vrm;
                        const box = new THREE.Box3().setFromObject(vrm.scene);
                        const size = box.getSize(new THREE.Vector3());
                        const center = box.getCenter(new THREE.Vector3());
                        
                        // 将 3D 坐标转换为屏幕坐标（简化版）
                        if (this.manager.canvas) {
                            const rect = this.manager.canvas.getBoundingClientRect();
                            const width = rect.width;
                            const height = rect.height;
                            
                            // 简化计算：假设模型在画布中央
                            return {
                                left: rect.left + width * 0.3,
                                right: rect.left + width * 0.7,
                                top: rect.top + height * 0.2,
                                bottom: rect.top + height * 0.8
                            };
                        }
                    }
                    // 默认返回屏幕中央区域
                    return {
                        left: window.innerWidth * 0.3,
                        right: window.innerWidth * 0.7,
                        top: window.innerHeight * 0.2,
                        bottom: window.innerHeight * 0.8
                    };
                }
            };

            try {
                window.live2dManager.setupFloatingButtons(virtualModel);
            } catch (e) {
            }
        }
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
                    const exprCount = Object.keys(vrm.expressionManager.expressions).length;
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
     * 设置锁按钮（类似 Live2D）
     */
    setupLockIcon() {
        if (!this.manager.canvas) {
            return;
        }

        // 在 l2d_manager 等页面不显示
        if (!document.getElementById('chat-container')) {
            this.manager.isLocked = false;
            this.manager.canvas.style.pointerEvents = 'auto';
            return;
        }

        // 在观看模式下不显示锁图标，但允许交互
        if (window.isViewerMode) {
            this.manager.isLocked = false;
            this.manager.canvas.style.pointerEvents = 'auto';
            return;
        }

        // 检查锁图标是否已存在
        let lockIcon = document.getElementById('vrm-lock-icon');
        if (lockIcon) {
            return; // 已存在，不重复创建
        }

        lockIcon = document.createElement('div');
        lockIcon.id = 'vrm-lock-icon';
        Object.assign(lockIcon.style, {
            position: 'fixed',
            zIndex: '99999',
            width: '32px',
            height: '32px',
            cursor: 'pointer',
            userSelect: 'none',
            pointerEvents: 'auto',
            display: 'block'
        });

        // 添加版本号防止缓存
        const iconVersion = '?v=' + Date.now();

        // 创建图片容器
        const imgContainer = document.createElement('div');
        Object.assign(imgContainer.style, {
            position: 'relative',
            width: '32px',
            height: '32px'
        });

        // 创建锁定状态图片
        const imgLocked = document.createElement('img');
        imgLocked.src = '/static/icons/locked_icon.png' + iconVersion;
        imgLocked.alt = 'Locked';
        Object.assign(imgLocked.style, {
            position: 'absolute',
            width: '32px',
            height: '32px',
            objectFit: 'contain',
            pointerEvents: 'none',
            opacity: this.manager.isLocked ? '1' : '0',
            transition: 'opacity 0.3s ease'
        });

        // 创建解锁状态图片
        const imgUnlocked = document.createElement('img');
        imgUnlocked.src = '/static/icons/unlocked_icon.png' + iconVersion;
        imgUnlocked.alt = 'Unlocked';
        Object.assign(imgUnlocked.style, {
            position: 'absolute',
            width: '32px',
            height: '32px',
            objectFit: 'contain',
            pointerEvents: 'none',
            opacity: this.manager.isLocked ? '0' : '1',
            transition: 'opacity 0.3s ease'
        });

        imgContainer.appendChild(imgLocked);
        imgContainer.appendChild(imgUnlocked);
        lockIcon.appendChild(imgContainer);
        document.body.appendChild(lockIcon);

        // 存储引用
        this._lockIconElement = lockIcon;
        this._lockIconImages = {
            locked: imgLocked,
            unlocked: imgUnlocked
        };

        // 点击事件
        lockIcon.addEventListener('click', (e) => {
            e.stopPropagation();
            this.setLocked(!this.manager.isLocked);
        });

        // 确保默认状态为解锁（可以移动和缩放）
        this.setLocked(false);

        // 持续更新图标位置（使用 requestAnimationFrame）
        const updateLockIconPosition = () => {
            try {
                if (!this.manager.currentModel || !this.manager.currentModel.vrm) {
                    if (lockIcon) lockIcon.style.display = 'none';
                    return;
                }

                const vrm = this.manager.currentModel.vrm;
                if (!this.manager.canvas) return;

                const rect = this.manager.canvas.getBoundingClientRect();
                const screenWidth = window.innerWidth;
                const screenHeight = window.innerHeight;

                // 计算模型在屏幕上的边界（简化版）
                const modelLeft = rect.left + rect.width * 0.3;
                const modelRight = rect.left + rect.width * 0.7;
                const modelTop = rect.top + rect.height * 0.2;
                const modelBottom = rect.top + rect.height * 0.8;

                // 计算锁图标目标位置（右下角）
                const targetX = modelRight * 0.7 + modelLeft * 0.3;
                const targetY = modelTop * 0.3 + modelBottom * 0.7;

                // 边界限制
                lockIcon.style.left = `${Math.max(0, Math.min(targetX, screenWidth - 40))}px`;
                lockIcon.style.top = `${Math.max(0, Math.min(targetY, screenHeight - 40))}px`;
            } catch (_) {
                // 忽略单帧异常
            }
            requestAnimationFrame(updateLockIconPosition);
        };
        this._lockIconAnimationFrame = requestAnimationFrame(updateLockIconPosition);

    }

    /**
     * 设置锁定状态并同步更新 UI
     * @param {boolean} locked - 是否锁定
     */
    setLocked(locked) {
        this.manager.isLocked = locked;

        // 更新锁图标样式
        if (this._lockIconImages) {
            const { locked: imgLocked, unlocked: imgUnlocked } = this._lockIconImages;
            if (imgLocked) imgLocked.style.opacity = locked ? '1' : '0';
            if (imgUnlocked) imgUnlocked.style.opacity = locked ? '0' : '1';
        }

        // 更新 canvas 的 pointerEvents
        if (this.manager.canvas) {
            this.manager.canvas.style.pointerEvents = locked ? 'none' : 'auto';
        }

        // 更新交互模块的锁定状态
        if (this.manager.interaction && typeof this.manager.interaction.setLocked === 'function') {
            this.manager.interaction.setLocked(locked);
        }

        // 更新控制器的启用状态
        // 注意：缩放功能由 VRMInteraction 手动处理，不使用控制器的自动缩放
        if (this.manager.controls) {
            this.manager.controls.enablePan = !locked;
            // enableZoom 保持为 false，由 VRMInteraction 手动处理
        }

        // 同步更新 Live2D 管理器的锁定状态（用于浮动按钮显示控制）
        if (window.live2dManager) {
            window.live2dManager.isLocked = locked;
        }

        // 控制浮动按钮的显示/隐藏
        const buttonsContainer = document.getElementById('live2d-floating-buttons');
        if (buttonsContainer) {
            if (locked) {
                // 锁定时隐藏浮动按钮
                buttonsContainer.style.display = 'none';
            }
        }
    }

    /**
     * 应用性能设置
     */
    applyPerformanceSettings() {
        if (!this.manager.renderer) return;
        
        let pixelRatio = window.devicePixelRatio || 1;
        if (this.performanceMode === 'low') {
            pixelRatio = Math.max(1.5, Math.min(pixelRatio, 2.0));
        } else if (this.performanceMode === 'medium') {
            pixelRatio = Math.max(2.0, Math.min(pixelRatio, 2.5));
        } else {
            pixelRatio = Math.max(2.0, pixelRatio);
        }
        
        this.manager.renderer.setPixelRatio(pixelRatio);
    }

    /**
     * 优化材质设置
     */
    optimizeMaterials() {
        if (!this.manager.currentModel || !this.manager.currentModel.vrm || !this.manager.currentModel.vrm.scene) return;
        
        this.manager.currentModel.vrm.scene.traverse((object) => {
            if (object.material) {
                const materials = Array.isArray(object.material) ? object.material : [object.material];
                materials.forEach(material => {
                    material.castShadow = false;
                    material.receiveShadow = false;
                });
            }
        });
    }

    /**
     * 初始化场景
     */
    async init(canvasId, containerId) {
        const THREE = window.THREE;
        if (!THREE) {
            throw new Error('Three.js库未加载，请确保已引入three.js');
        }
        
        this.manager.container = document.getElementById(containerId);
        this.manager.canvas = document.getElementById(canvasId);

        // 确保canvas有正确的ID（以防万一）
        if (this.manager.canvas && !this.manager.canvas.id) {
            this.manager.canvas.id = canvasId;
        }
        
        if (!this.manager.container) {
            throw new Error(`找不到容器元素: ${containerId}`);
        }

        // 确保容器可见且有大小（参考 vrm.js）
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

        // 创建相机 - 如果容器大小为0，使用窗口大小
        let width = this.manager.container.clientWidth || this.manager.container.offsetWidth;
        let height = this.manager.container.clientHeight || this.manager.container.offsetHeight;
        
        if (width === 0 || height === 0) {
            width = window.innerWidth;
            height = window.innerHeight;
        }
        
        this.manager.camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 2000);
        // 调整相机位置，从正面看模型，提供更好的面部透视
        this.manager.camera.position.set(0, 1.1, 1.5);
        this.manager.camera.lookAt(0, 0.9, 0);

        // 创建渲染器 - 提高渲染质量设置（参考 vrm.js）
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
        this.manager.renderer.shadowMap.enabled = false;
        this.manager.renderer.outputEncoding = THREE.sRGBEncoding;
        this.manager.renderer.toneMapping = THREE.ACESFilmicToneMapping;
        this.manager.renderer.toneMappingExposure = 1.2; // 提高曝光度，使模型更亮
        this.manager.renderer.useLegacyLights = false;

        // 确保容器和 canvas 可以接收事件
        const canvas = this.manager.renderer.domElement;
        canvas.style.setProperty('pointer-events', 'auto', 'important');
        canvas.style.setProperty('touch-action', 'none', 'important');
        canvas.style.setProperty('user-select', 'none', 'important');
        canvas.style.cursor = 'grab';
        canvas.style.width = '100%';
        canvas.style.height = '100%';
        canvas.style.display = 'block';
            pointerEvents: canvas.style.pointerEvents,
            computedPointerEvents: window.getComputedStyle(canvas).pointerEvents
        });

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
            this.manager.controls.minDistance = 0.5;
            this.manager.controls.maxDistance = 10;
            this.manager.controls.update();
        }

        // 添加灯光 - 增强亮度和立体感
        // 环境光：提供基础照明，增加强度使模型更亮
        const ambientIntensity = this.performanceMode === 'low' ? 0.8 : 0.7;
        const ambientLight = new THREE.AmbientLight(0xffffff, ambientIntensity);
        this.manager.scene.add(ambientLight);

        // 主方向光：从斜上方照射，提供主要照明，增强强度
        const directionalLight1 = new THREE.DirectionalLight(0xffffff, 1.5);
        directionalLight1.position.set(1, 2, 1.5);
        directionalLight1.castShadow = false;
        this.manager.scene.add(directionalLight1);

        // 中等和高性能模式：添加轮廓光和补光，增强强度
        if (this.performanceMode !== 'low') {
            // 轮廓光：从侧后方照射，增强轮廓立体感，增加强度
            const rimLight = new THREE.DirectionalLight(0xffffff, 0.9);
            rimLight.position.set(-2, 1, -1);
            rimLight.castShadow = false;
            this.manager.scene.add(rimLight);

            // 高性能模式：添加柔和的补光，增强强度
            if (this.performanceMode === 'high') {
                const fillLight = new THREE.DirectionalLight(0xffffff, 0.6);
                fillLight.position.set(0, -1, 1);
                fillLight.castShadow = false;
                this.manager.scene.add(fillLight);
            }
        }

        // 添加柔和的顶部光源，提供整体照明，增强强度
        const topLight = new THREE.DirectionalLight(0xffffff, 1.0);
        topLight.position.set(0, 3, 0);
        topLight.castShadow = false;
        this.manager.scene.add(topLight);

        window.addEventListener('resize', () => this.manager.onWindowResize());
    }

    /**
     * 加载VRM模型
     */
    async loadModel(modelUrl, options = {}) {
        const THREE = window.THREE;
        
        try {
            // 动态导入 GLTFLoader 和 VRMLoaderPlugin（参考 vrm.js）
            let GLTFLoader, VRMLoaderPlugin;
            
            // 使用 ES 模块导入（与动画模块保持一致）
            try {
                const loaderModule = await import('three/addons/loaders/GLTFLoader.js');
                GLTFLoader = loaderModule.GLTFLoader;

                const vrmModule = await import('@pixiv/three-vrm');
                VRMLoaderPlugin = vrmModule.VRMLoaderPlugin;
            } catch (e) {
                throw new Error(`无法加载必要的VRM模块: ${e.message}`);
            }

            const loader = new GLTFLoader();
            loader.register((parser) => new VRMLoaderPlugin(parser));

            // 加载 VRM 模型
            const gltf = await new Promise((resolve, reject) => {
                loader.load(
                    modelUrl,
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

            // 如果已有模型，先移除
            if (this.manager.currentModel && this.manager.currentModel.vrm) {
                this.manager.scene.remove(this.manager.currentModel.vrm.scene);
                this.disposeVRM();
            }

            // 确保浮动按钮系统已初始化（如果不存在则创建）
            this.ensureFloatingButtons();

            // 获取 VRM 实例
            const vrm = gltf.userData.vrm;
            if (!vrm) {
                throw new Error('加载的模型不是有效的 VRM 格式');
            }
            
            // 检测 VRM 模型版本（0.0 或 1.0）
            this.vrmVersion = this.detectVRMVersion(vrm);

            // 计算模型的边界框，用于确定合适的初始大小
            const box = new THREE.Box3().setFromObject(vrm.scene);
            const size = box.getSize(new THREE.Vector3());
            const center = box.getCenter(new THREE.Vector3());
            
            // 调整模型位置（居中）
            vrm.scene.position.set(-center.x, -center.y, -center.z);
            
            // 确保模型正面朝向相机（参考 vrm.js 的朝向检测逻辑）
            let needsRotation = false;
            if (vrm.humanoid && vrm.humanoid.humanBones) {
                // 获取头部骨骼位置（通常头部在模型前方）
                const headBone = vrm.humanoid.humanBones.head?.node;
                const chestBone = vrm.humanoid.humanBones.chest?.node || 
                                 vrm.humanoid.humanBones.spine?.node;
                
                if (headBone && chestBone) {
                    // 计算从胸部到头部的向量（应该指向前方）
                    const headWorldPos = new THREE.Vector3();
                    const chestWorldPos = new THREE.Vector3();
                    headBone.getWorldPosition(headWorldPos);
                    chestBone.getWorldPosition(chestWorldPos);
                    
                    const forwardVec = new THREE.Vector3().subVectors(headWorldPos, chestWorldPos);
                    forwardVec.normalize();
                    
                    // 如果forward向量指向Z轴正方向（远离相机），说明是背面，需要旋转
                    if (forwardVec.z > 0.3) {
                        needsRotation = true;
                    }
                }
            }
            
            // 重置旋转并应用必要的旋转
            vrm.scene.rotation.set(0, needsRotation ? Math.PI : 0, 0);
            
            if (needsRotation) {
            }
            
            // 计算合适的初始缩放（参考Live2D的默认大小计算，参考 vrm.js）
            const isMobile = window.innerWidth <= 768;
            let targetScale;
            
            if (isMobile) {
                // 移动端：较小
                targetScale = Math.min(
                    0.5,
                    window.innerHeight * 1.3 / 4000,
                    window.innerWidth * 1.2 / 2000
                );
            } else {
                // 桌面端：参考Live2D的计算方式
                targetScale = Math.min(
                    0.5,
                    (window.innerHeight * 0.75) / 7000,
                    (window.innerWidth * 0.6) / 7000
                );
            }
            
            // 根据模型大小和屏幕大小计算合适的相机距离
            const modelHeight = size.y;
            const screenHeight = window.innerHeight;
            const screenWidth = window.innerWidth;
            
            // 目标：让模型在屏幕上的高度约为屏幕高度的0.4-0.5倍（类似Live2D）
            const targetScreenHeight = screenHeight * 0.45;
            const fov = this.manager.camera.fov * (Math.PI / 180);
            const distance = (modelHeight / 2) / Math.tan(fov / 2) / targetScreenHeight * screenHeight;
            
            // 设置模型初始缩放
            if (options.scale) {
                vrm.scene.scale.set(options.scale.x || 1, options.scale.y || 1, options.scale.z || 1);
            } else {
                vrm.scene.scale.set(1, 1, 1);
            }
            
            // 调整相机位置，使模型在屏幕中央合适的位置
            const cameraY = center.y + (isMobile ? modelHeight * 0.2 : modelHeight * 0.1);
            const cameraZ = Math.abs(distance);
            this.manager.camera.position.set(0, cameraY, cameraZ);
            this.manager.camera.lookAt(0, center.y, 0);
            
            // 添加到场景
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

                hasModel: !!this.manager.currentModel,
                hasScene: !!this.manager.currentModel.scene,
                hasVRM: !!this.manager.currentModel.vrm
            });

            // 更新口型表情映射（如果animation模块存在）
            if (this.manager.animation && typeof this.manager.animation.updateMouthExpressionMapping === 'function') {
                this.manager.animation.updateMouthExpressionMapping();
            }

            // 自动播放wait03动画（循环播放）
            // 延迟一点确保所有资源都已准备好
            setTimeout(async () => {
                if (this.manager.animation && typeof this.manager.animation.playVRMAAnimation === 'function') {
                    try {
                        const animationPath = '/static/vrm/animation/wait03.vrma';
                        await this.manager.animation.playVRMAAnimation(animationPath, {
                            loop: true,
                            fadeIn: 0.5,
                            fadeOut: 0.5
                        });
                    } catch (error) {
                        setTimeout(async () => {
                            try {
                                await this.manager.animation.playVRMAAnimation('/static/vrm/animation/wait03.vrma', {
                                    loop: true
                                });
                            } catch (retryError) {
                            }
                        }, 1000);
                    }
                }
            }, 500);

            // 设置锁按钮（在模型加载完成后）
            this.setupLockIcon();

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
    disposeVRM() {
        if (!this.manager.currentModel || !this.manager.currentModel.vrm) return;
        
        const vrm = this.manager.currentModel.vrm;
        
        // 清理 VRMA 动画（如果存在）
        if (this.manager.animation && typeof this.manager.animation.stopVRMAAnimation === 'function') {
            this.manager.animation.stopVRMAAnimation();
        }
        
        if (this.manager.animationMixer) {
            if (vrm.scene) {
                this.manager.animationMixer.uncacheRoot(vrm.scene);
            }
            this.manager.animationMixer = null;
        }

        if (vrm.scene) {
            vrm.scene.traverse((object) => {
                if (object.geometry) object.geometry.dispose();
                if (object.material) {
                    if (Array.isArray(object.material)) {
                        object.material.forEach(m => m.dispose());
                    } else {
                        object.material.dispose();
                    }
                }
            });
        }
    }
}

// 导出到全局
window.VRMCore = VRMCore;

