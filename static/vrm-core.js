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
            zIndex: '99', // 确保在最上层
            width: '32px', // 增大点击区域
            height: '32px',
            cursor: 'pointer',
            userSelect: 'none',
            pointerEvents: 'auto',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center'
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
            width: '28px',
            height: '28px',
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
            width: '28px',
            height: '28px',
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

        // 点击事件 - 使用 mousedown 和 touchstart，提高响应速度
        const handleLockToggle = (e) => {
            e.stopPropagation();
            e.preventDefault();

            // 添加视觉反馈：点击时缩小
            lockIcon.style.transform = 'scale(0.9)';
            setTimeout(() => {
                lockIcon.style.transform = 'scale(1)';
            }, 150);

            this.setLocked(!this.manager.isLocked);
        };

        lockIcon.addEventListener('mousedown', handleLockToggle);
        lockIcon.addEventListener('touchstart', handleLockToggle);

        // 阻止 click 和 touchend 事件冒泡（防止双重触发）
        lockIcon.addEventListener('click', (e) => {
            e.stopPropagation();
            e.preventDefault();
        });
        lockIcon.addEventListener('touchend', (e) => {
            e.stopPropagation();
            e.preventDefault();
        });

        // 添加过渡动画
        lockIcon.style.transition = 'transform 0.15s ease';

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

                // 计算锁图标目标位置（脚部左侧）
                // 水平：在模型左侧
                const targetX = modelLeft + (modelRight - modelLeft) * 0.8;  // 20%位置（靠左）
                // 垂直：在模型中上部
                const targetY = modelTop + (modelBottom - modelTop) * 0.5;  // 50%位置（中间偏上）

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
            } else {
                // 解锁时不自动显示，保持原有的鼠标悬停逻辑
                // buttonsContainer.style.display 会由鼠标悬停事件控制
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
                    // 1. 全局开启阴影 (衣服、头发)
                    material.castShadow = true;
                    material.receiveShadow = true;
                    
                    // 2. 🔍 智能检测脸部
                    // 如果材质名称或物体名称包含 "Face"、"Skin"、"Body" 等关键词
                    const name = (object.name + (material.name || '')).toLowerCase();
                    if (name.includes('face') || name.includes('skin') || name.includes('head')) {
                        // ❌ 脸部不接收阴影 (防止出现奇怪的鼻影或黑脸)
                        // 这样脸永远是白净的，但头发还是会投射影子到脖子上
                        material.receiveShadow = false; 
                        
                        // 可选：稍微增加一点自发光，确保肤色通透
                        //if (material.emissiveIntensity !== undefined) material.emissiveIntensity = 0.1;
                    }
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

        if (!this.manager.canvas) {
            throw new Error(`找不到canvas元素: ${canvasId}`);
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
        //使用 30 度长焦视角，减少透视畸变，让角色更修长好看
        this.manager.camera = new THREE.PerspectiveCamera(30, width / height, 0.1, 2000);
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
        // 开启高质量软阴影 
        this.manager.renderer.shadowMap.enabled = true; // 开启阴影
        this.manager.renderer.shadowMap.type = THREE.PCFSoftShadowMap; // ✅ 使用柔和阴影
        this.manager.renderer.outputEncoding = THREE.sRGBEncoding;
        
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
            this.manager.controls.minDistance = 0.5;
            this.manager.controls.maxDistance = 10;
            this.manager.controls.update();
        }

        // 添加灯光 - 增强亮度和立体感
        // 1. 先把相机添加到场景中 
        this.manager.scene.add(this.manager.camera);

        // 2. 环境光 (Ambient): 稍微调暗，保证阴影部分有颜色但足够深
        const ambientLight = new THREE.AmbientLight(0xffffff, 0.08);  // 从0.1降到0.08
        this.manager.scene.add(ambientLight);
        this.manager.ambientLight = ambientLight; // 保存引用供UI控制

        // 3. 建立"跟随灯光组"
        const camLightGroup = new THREE.Group();


        // 4. 主光源 : 负责产生主要阴影和亮度
        const mainLight = new THREE.DirectionalLight(0xffffff, 0.06);  // 从0.1降到0.06，减少脸部亮度
        mainLight.position.set(-1, 1, 1); // 相对相机的位置
        mainLight.castShadow = true;
        // 优化阴影参数，去除锯齿
        mainLight.shadow.mapSize.width = 2048;
        mainLight.shadow.mapSize.height = 2048;
        mainLight.shadow.bias = -0.0001;
        // 限制阴影范围，聚焦角色
        mainLight.shadow.camera.near = 0.1;
        mainLight.shadow.camera.far = 20;
        mainLight.shadow.camera.left = -2;
        mainLight.shadow.camera.right = 2;
        mainLight.shadow.camera.top = 2;
        mainLight.shadow.camera.bottom = -2;

        this.manager.scene.add(mainLight);
        this.manager.mainLight = mainLight; // 保存引用供UI控制


        // 补光 (Fill Light): 位于相机右侧，柔和化阴影
        const fillLight = new THREE.DirectionalLight(0xffffff, 0.12);  // 从0.2降到0.12，减少正面补光
        fillLight.position.set(1, 0, 1); // 相对相机
        fillLight.castShadow = false;
        camLightGroup.add(fillLight);
        this.manager.fillLight = fillLight; // 保存引用供UI控制

        // 将灯光组挂载到相机上！
        this.manager.camera.add(camLightGroup);
        // 5. 轮廓光 (Rim Light): 依然固定在场景里 (世界坐标)
        // 从背后打光，勾勒头发边缘，增加通透感
        const rimLight = new THREE.DirectionalLight(0xffffff, 0.8);
        rimLight.position.set(0, 5, -5); // 从正后上方
        this.manager.scene.add(rimLight);
        this.manager.rimLight = rimLight; // 保存引用供UI控制

        window.addEventListener('resize', () => this.manager.onWindowResize());
    }

    /**
     * 加载VRM模型
     */
    async loadModel(modelUrl, options = {}) {
        const THREE = window.THREE;
        if (!THREE) {
            throw new Error('Three.js库未加载，无法加载VRM模型');
        }

        try {
            // 使用全局THREE对象（避免动态import问题）
            const { GLTFLoader } = await import('three/addons/loaders/GLTFLoader.js');
            const VRMLoaderPlugin = (await import('@pixiv/three-vrm')).VRMLoaderPlugin;

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
                console.error('[VRM] 加载失败: gltf.userData:', gltf.userData);
                console.error('[VRM] 加载失败: gltf.scene:', gltf.scene);
                throw new Error(`加载的模型不是有效的 VRM 格式。文件: ${modelUrl}`);
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
                } else {
                    console.warn('[VRM] 无法检测模型朝向：缺少头部或胸部骨骼');
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

            // 更新口型表情映射（如果animation模块存在）
            if (this.manager.animation && typeof this.manager.animation.updateMouthExpressionMapping === 'function') {
                this.manager.animation.updateMouthExpressionMapping();
            }

            

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

