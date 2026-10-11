class FBXManager {
    static DEFAULT_MODEL_PATH = '';

    constructor() {
        this.scene = null;
        this.camera = null;
        this.renderer = null;
        this.canvas = null;
        this.container = null;
        this.currentModel = null;
        this.currentAnimationUrl = null;
        this.clock = null;
        this.mixer = null;
        this.ambientLight = null;
        this.directionalLight = null;
        this._loader = null;
        this._animationFrameId = null;
        this._shouldRender = false;
        this._isDisposed = false;
        this._activeLoadToken = 0;
        this._currentAnimationMode = null;
        this._resizeHandler = null;
        this._modelRoot = null;
        this._frameWaiters = [];
        this._actions = [];
    }

    async _getLoader() {
        if (this._loader) return this._loader;
        const mod = await import('three/addons/loaders/FBXLoader.js');
        this._loader = new mod.FBXLoader();
        return this._loader;
    }

    async init(canvasId = 'fbx-canvas', containerId = 'fbx-container', options = {}) {
        const THREE = window.THREE;
        if (!THREE) throw new Error('[FBX Manager] THREE 未加载');

        this.container = document.getElementById(containerId);
        this.canvas = document.getElementById(canvasId);
        if (!this.container) throw new Error('[FBX Manager] 找不到容器元素: ' + containerId);
        if (!this.canvas) throw new Error('[FBX Manager] 找不到 canvas 元素: ' + canvasId);

        this.container.style.display = 'block';
        this.container.style.visibility = 'visible';
        this.container.style.opacity = '1';

        this.clock = new THREE.Clock();
        this.scene = new THREE.Scene();
        this.scene.background = null;

        let width = this.container.clientWidth || this.container.offsetWidth;
        let height = this.container.clientHeight || this.container.offsetHeight;
        if (!width || !height) {
            width = window.innerWidth;
            height = window.innerHeight;
        }

        this.camera = new THREE.PerspectiveCamera(30, width / height, 0.1, 100000);
        this.camera.position.set(0, 100, 300);
        this.camera.lookAt(0, 100, 0);

        if (THREE.ColorManagement) {
            THREE.ColorManagement.enabled = true;
        }

        this.renderer = new THREE.WebGLRenderer({
            canvas: this.canvas,
            alpha: true,
            antialias: true,
            powerPreference: 'high-performance',
            precision: 'highp',
            preserveDrawingBuffer: false,
            depth: true
        });
        this.renderer.setSize(width, height);
        this.renderer.setClearColor(0x000000, 0);
        if (THREE.SRGBColorSpace !== undefined) {
            this.renderer.outputColorSpace = THREE.SRGBColorSpace;
        }
        if (THREE.NeutralToneMapping !== undefined) {
            this.renderer.toneMapping = THREE.NeutralToneMapping;
        }
        this.renderer.toneMappingExposure = 1.0;

        this.canvas.style.setProperty('pointer-events', 'auto', 'important');
        this.canvas.style.setProperty('touch-action', 'none', 'important');
        this.canvas.style.setProperty('user-select', 'none', 'important');
        this.canvas.style.cursor = 'default';

        this.ambientLight = new THREE.AmbientLight(0xffffff, 1.6);
        this.scene.add(this.ambientLight);
        this.directionalLight = new THREE.DirectionalLight(0xffffff, 2.0);
        this.directionalLight.position.set(1, 2, 1.5);
        this.scene.add(this.directionalLight);
        const fillLight = new THREE.DirectionalLight(0xffffff, 0.8);
        fillLight.position.set(-1, 1, -1);
        this.scene.add(fillLight);

        this._resizeHandler = () => this.onWindowResize();
        window.addEventListener('resize', this._resizeHandler);

        this._isDisposed = false;
        this._shouldRender = true;
        this._startRenderLoop();
        return this;
    }

    _startRenderLoop() {
        const loop = () => {
            if (this._isDisposed) return;
            this._animationFrameId = requestAnimationFrame(loop);
            if (!this._shouldRender) return;
            const delta = this.clock ? this.clock.getDelta() : 0;
            if (this.mixer) {
                this.mixer.update(delta);
            }
            if (this.renderer && this.scene && this.camera) {
                this.renderer.render(this.scene, this.camera);
            }
            this._resolveFrameWaiters();
        };
        loop();
    }

    _resolveFrameWaiters() {
        if (this._frameWaiters.length === 0) return;
        const waiters = this._frameWaiters;
        this._frameWaiters = [];
        for (const resolve of waiters) {
            resolve(true);
        }
    }

    waitForRenderFrame(timeoutMs = 2000) {
        if (this._isDisposed) return Promise.resolve(false);
        return new Promise((resolve) => {
            let settled = false;
            const timer = setTimeout(() => {
                if (settled) return;
                settled = true;
                resolve(false);
            }, timeoutMs);
            this._frameWaiters.push(() => {
                if (settled) return;
                settled = true;
                clearTimeout(timer);
                resolve(true);
            });
        });
    }

    _disposeObject(object) {
        if (!object) return;
        object.traverse((child) => {
            if (child.geometry && typeof child.geometry.dispose === 'function') {
                child.geometry.dispose();
            }
            const material = child.material;
            if (!material) return;
            const materials = Array.isArray(material) ? material : [material];
            for (const entry of materials) {
                for (const key of Object.keys(entry)) {
                    const value = entry[key];
                    if (value && value.isTexture && typeof value.dispose === 'function') {
                        value.dispose();
                    }
                }
                if (typeof entry.dispose === 'function') {
                    entry.dispose();
                }
            }
        });
    }

    _clearModel() {
        if (this.mixer) {
            this.mixer.stopAllAction();
            this.mixer = null;
        }
        if (this._modelRoot && this.scene) {
            this.scene.remove(this._modelRoot);
            this._disposeObject(this._modelRoot);
        }
        this._modelRoot = null;
        this.currentModel = null;
        this.currentAnimationUrl = null;
    }

    _fitCameraToObject(object) {
        const THREE = window.THREE;
        if (!THREE || !object) return;
        const box = new THREE.Box3().setFromObject(object);
        if (box.isEmpty()) return;
        const size = box.getSize(new THREE.Vector3());
        const center = box.getCenter(new THREE.Vector3());
        const maxDim = Math.max(size.x, size.y, size.z);
        if (!Number.isFinite(maxDim) || maxDim <= 0) return;

        object.position.x -= center.x;
        object.position.z -= center.z;
        object.position.y -= box.min.y;

        const fittedBox = new THREE.Box3().setFromObject(object);
        const fittedSize = fittedBox.getSize(new THREE.Vector3());
        const fittedCenter = fittedBox.getCenter(new THREE.Vector3());
        const fov = this.camera.fov * (Math.PI / 180);
        const distance = (Math.max(fittedSize.y, fittedSize.x) / 2) / Math.tan(fov / 2);
        const finalDistance = distance * 2.2;

        this.camera.position.set(fittedCenter.x, fittedCenter.y, fittedCenter.z + finalDistance);
        this.camera.lookAt(fittedCenter.x, fittedCenter.y, fittedCenter.z);
        this.camera.near = Math.max(0.01, finalDistance / 1000);
        this.camera.far = finalDistance * 100;
        this.camera.updateProjectionMatrix();
    }

    async loadModel(modelPath, options = {}) {
        if (!modelPath) throw new Error('[FBX Manager] 模型路径为空');
        const token = ++this._activeLoadToken;
        const loader = await this._getLoader();
        if (token !== this._activeLoadToken) return null;

        const object = await loader.loadAsync(modelPath, options.onProgress);
        if (token !== this._activeLoadToken) {
            this._disposeObject(object);
            return null;
        }

        this._clearModel();

        object.traverse((child) => {
            if (child.isMesh) {
                child.castShadow = false;
                child.receiveShadow = false;
                if (child.material) {
                    const materials = Array.isArray(child.material) ? child.material : [child.material];
                    for (const entry of materials) {
                        entry.side = window.THREE.DoubleSide;
                    }
                }
            }
        });

        this._modelRoot = object;
        this.currentModel = object;
        this.scene.add(object);
        this._fitCameraToObject(object);

        if (Array.isArray(object.animations) && object.animations.length > 0) {
            this.mixer = new window.THREE.AnimationMixer(object);
            this._bindActions(object.animations);
            this.currentAnimationUrl = modelPath;
            this.playAnimation('idle');
        }

        return object;
    }

    _bindActions(clips) {
        this._actions = [];
        if (!this.mixer) return this._actions;
        for (const clip of clips) {
            const action = this.mixer.clipAction(clip, this.currentModel);
            action.clampWhenFinished = false;
            action.loop = window.THREE.LoopRepeat;
            this._actions.push(action);
        }
        return this._actions;
    }

    async loadAnimation(animationPath, options = {}) {
        if (!animationPath) throw new Error('[FBX Manager] 动画路径为空');
        if (!this.currentModel) throw new Error('[FBX Manager] 尚未加载模型');

        const loader = await this._getLoader();
        const clipSource = await loader.loadAsync(animationPath, options.onProgress);

        if (!Array.isArray(clipSource.animations) || clipSource.animations.length === 0) {
            this._disposeObject(clipSource);
            throw new Error('[FBX Manager] 动画文件不含可播放的动画轨: ' + animationPath);
        }

        const clips = clipSource.animations.slice();
        this._disposeObject(clipSource);

        if (!this.mixer) {
            this.mixer = new window.THREE.AnimationMixer(this.currentModel);
        } else {
            this.mixer.stopAllAction();
        }

        const actions = this._bindActions(clips);

        this.currentAnimationUrl = animationPath;
        return { clips, actions, mixer: this.mixer };
    }

    playAnimation(mode = 'idle') {
        this._currentAnimationMode = mode;
        this._shouldRender = true;
        if (!this.mixer) return false;
        for (const action of this._actions) {
            action.paused = false;
            if (!action.isRunning()) {
                action.play();
            }
        }
        return true;
    }

    pauseAnimation() {
        if (!this.mixer) return;
        for (const action of this._actions) {
            action.paused = true;
        }
    }

    stopAnimation() {
        this._currentAnimationMode = null;
        if (this.mixer) {
            this.mixer.stopAllAction();
        }
        this.currentAnimationUrl = null;
    }

    setVisible(visible) {
        if (this._modelRoot) {
            this._modelRoot.visible = visible === true;
        }
    }

    pauseRendering() {
        this._shouldRender = false;
    }

    resumeRendering() {
        if (this._isDisposed) return;
        this._shouldRender = true;
        if (this.clock) {
            this.clock.getDelta();
        }
    }

    onWindowResize() {
        if (!this.container || !this.camera || !this.renderer) return;
        let width = this.container.clientWidth || this.container.offsetWidth;
        let height = this.container.clientHeight || this.container.offsetHeight;
        if (!width || !height) {
            width = window.innerWidth;
            height = window.innerHeight;
        }
        this.camera.aspect = width / height;
        this.camera.updateProjectionMatrix();
        this.renderer.setSize(width, height);
    }

    dispose() {
        this._isDisposed = true;
        this._shouldRender = false;
        if (this._animationFrameId !== null) {
            cancelAnimationFrame(this._animationFrameId);
            this._animationFrameId = null;
        }
        if (this._resizeHandler) {
            window.removeEventListener('resize', this._resizeHandler);
            this._resizeHandler = null;
        }
        this._frameWaiters = [];
        this._clearModel();
        if (this.renderer) {
            this.renderer.dispose();
            this.renderer = null;
        }
        if (this.scene) {
            this.scene.clear();
            this.scene = null;
        }
        this.camera = null;
        this.canvas = null;
        this.container = null;
        this.clock = null;
        this._loader = null;
    }
}

window.FBXManager = FBXManager;
