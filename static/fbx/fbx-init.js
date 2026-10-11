(function () {
    if (window.__NEKO_FBX_INIT__) return;
    window.__NEKO_FBX_INIT__ = true;

    function readConfig() {
        var cfg = window.lanlan_config || {};
        var modelType = String(cfg.model_type || '').toLowerCase();
        var subType = String(cfg.live3d_sub_type || '').toLowerCase();
        if (modelType !== 'live3d' || subType !== 'fbx') return null;
        return cfg;
    }

    function readModelPath(cfg) {
        var raw = cfg.fbxModel || cfg.fbx_model || cfg.fbx || '';
        var value = String(raw || '').trim();
        if (!value || value === 'undefined' || value === 'null') return '';
        return value;
    }

    function hideOtherContainers() {
        for (const id of ['vrm-container', 'mmd-container', 'live2d-container']) {
            const node = document.getElementById(id);
            if (node) {
                node.style.display = 'none';
                node.classList.add('hidden');
            }
        }
    }

    async function waitForThree(timeoutMs = 15000) {
        if (window.THREE) return true;
        return new Promise((resolve) => {
            let settled = false;
            const finish = (ok) => {
                if (settled) return;
                settled = true;
                clearTimeout(timer);
                window.removeEventListener('three-ready', onReady);
                resolve(ok);
            };
            const onReady = () => finish(true);
            const timer = setTimeout(() => finish(!!window.THREE), timeoutMs);
            window.addEventListener('three-ready', onReady);
            const poll = setInterval(() => {
                if (window.THREE) {
                    clearInterval(poll);
                    finish(true);
                }
            }, 100);
            setTimeout(() => clearInterval(poll), timeoutMs);
        });
    }

    async function initFbxModel() {
        const cfg = readConfig();
        if (!cfg) return null;
        const modelPath = readModelPath(cfg);
        if (!modelPath) {
            console.warn('[FBX Init] FBX 模型路径为空，跳过加载');
            return null;
        }

        const ready = await waitForThree();
        if (!ready) {
            console.error('[FBX Init] THREE 未就绪，无法初始化 FBX 渲染器');
            return null;
        }

        hideOtherContainers();
        const container = document.getElementById('fbx-container');
        if (container) {
            container.classList.remove('hidden');
            container.style.display = 'block';
            container.style.visibility = 'visible';
        }

        if (!window.FBXManager) {
            console.error('[FBX Init] FBXManager 未加载');
            return null;
        }

        if (!window.fbxManager || window.fbxManager._isDisposed) {
            window.fbxManager = new window.FBXManager();
        }
        const manager = window.fbxManager;

        if (!manager.scene) {
            await manager.init('fbx-canvas', 'fbx-container', {});
        }

        try {
            await manager.loadModel(modelPath, {});
        } catch (error) {
            console.error('[FBX Init] FBX 模型加载失败:', error);
            return manager;
        }

        const canvas = document.getElementById('fbx-canvas');
        if (canvas) {
            canvas.style.visibility = 'visible';
            canvas.style.pointerEvents = 'auto';
        }

        const animation = String(cfg.fbxAnimation || cfg.fbx_animation || '').trim();
        if (animation && animation !== 'undefined' && animation !== 'null') {
            try {
                await manager.loadAnimation(animation, {});
                manager.playAnimation('dance');
            } catch (error) {
                console.warn('[FBX Init] FBX 动作加载失败，保留模型自带动画:', error);
            }
        }

        return manager;
    }

    async function autoInitFbxOnMainPage() {
        if (window._cardExportPage) return;
        if (window.location.pathname.includes('model_manager')) return;
        if (window.__nekoStorageLocationStartupBarrier
            && typeof window.__nekoStorageLocationStartupBarrier.then === 'function') {
            await window.__nekoStorageLocationStartupBarrier;
        }
        if (window.pageConfigReady && typeof window.pageConfigReady.then === 'function') {
            await window.pageConfigReady;
        }
        await initFbxModel();
    }

    window._waitForFbxModules = async function (timeoutMs = 15000) {
        return waitForThree(timeoutMs);
    };
    window._initFbxModel = initFbxModel;
    window._autoInitFbxOnMainPage = autoInitFbxOnMainPage;

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => {
            autoInitFbxOnMainPage().catch((error) => {
                console.error('[FBX Init] 自动初始化失败:', error);
            });
        });
    } else {
        autoInitFbxOnMainPage().catch((error) => {
            console.error('[FBX Init] 自动初始化失败:', error);
        });
    }
})();