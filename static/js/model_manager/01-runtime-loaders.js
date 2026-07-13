(async function initVRMModules() {
    const loadModules = async () => {
        console.log(window.t ? window.t('modelManager.vrmLoadingDependencies') : '[VRM] 开始加载依赖模块');

        // 提前设置加载中标志，防止 vrm-init.js 加载时其内部 IIFE 再次触发模块加载
        // 注意：不能用 vrmModuleLoaded，因为下游 waitForVRM 会误判为已完成
        window._vrmModulesLoading = true;

        // avatar-popup-common, avatar-ui-popup, avatar-ui-popup-config, avatar-ui-buttons
        // 已由 model_manager.html 静态 <script> 加载，此处不再重复加载
        const vrmModules = [
            '/static/vrm/vrm-orientation.js',
            '/static/vrm/vrm-core.js',
            '/static/vrm/vrm-expression.js',
            '/static/vrm/vrm-animation.js',
            '/static/vrm/vrm-interaction.js',
            '/static/vrm/vrm-cursor-follow.js',
            '/static/vrm/vrm-manager.js',
            '/static/vrm/vrm-ui-buttons.js',
            '/static/vrm/vrm-init.js'
        ];

        const failedModules = [];
        for (const moduleSrc of vrmModules) {
            const script = document.createElement('script');
            script.src = `${moduleSrc}?v=${Date.now()}`;
            await new Promise((resolve) => {
                script.onload = resolve;
                script.onerror = () => {
                    console.error(`[VRM] 模块加载失败: ${moduleSrc}`);
                    failedModules.push(moduleSrc);
                    resolve(); // 即使失败也继续，防止死锁
                };
                document.body.appendChild(script);
            });
        }

        if (failedModules.length > 0) {
            window.vrmModuleLoaded = false;
            console.error('[VRM] 以下模块加载失败:', failedModules);
            window.dispatchEvent(new CustomEvent('vrm-modules-failed', {
                detail: { failedModules }
            }));
        } else {
            window.dispatchEvent(new CustomEvent('vrm-modules-ready'));
        }
    };

    // 如果 THREE 还没好，就等事件；好了就直接加载
    if (typeof window.THREE === 'undefined') {
        window.addEventListener('three-ready', loadModules, { once: true });
    } else {
        loadModules();
    }
})();
// ====================== MMD 模块动态加载 ======================
(async function initMMDModules() {
    const loadModules = async () => {
        console.log('[MMD] 开始加载依赖模块');
        window._mmdModulesLoading = true;

        // avatar-popup-common, avatar-ui-popup, avatar-ui-popup-config, avatar-ui-buttons
        // 已由 model_manager.html 静态 <script> 加载，此处不再重复加载
        const mmdModules = [
            '/static/mmd/mmd-init.js',
            '/static/mmd/mmd-core.js',
            '/static/mmd/mmd-animation.js',
            '/static/mmd/mmd-expression.js',
            '/static/mmd/mmd-interaction.js',
            '/static/mmd/mmd-cursor-follow.js',
            '/static/mmd/mmd-manager.js',
            '/static/mmd/mmd-ui-buttons.js'
        ];

        const failedModules = [];
        for (const moduleSrc of mmdModules) {
            const script = document.createElement('script');
            const baseSrc = moduleSrc.split('?')[0];
            script.src = `${baseSrc}?v=${Date.now()}`;
            await new Promise((resolve) => {
                script.onload = resolve;
                script.onerror = () => {
                    console.error(`[MMD] 模块加载失败: ${moduleSrc}`);
                    failedModules.push(moduleSrc);
                    resolve();
                };
                document.body.appendChild(script);
            });
        }

        if (failedModules.length > 0) {
            window.mmdModuleLoaded = false;
            window._mmdModulesLoading = false;
            window._mmdModulesFailed = failedModules.slice();
            console.error('[MMD] 以下模块加载失败:', failedModules);
            window.dispatchEvent(new CustomEvent('mmd-modules-failed', {
                detail: { failedModules }
            }));
        } else {
            window.mmdModuleLoaded = true;
            window._mmdModulesLoading = false;
            window._mmdModulesFailed = null;
            window.dispatchEvent(new CustomEvent('mmd-modules-ready'));
        }
    };

    if (typeof window.THREE === 'undefined') {
        window.addEventListener('three-ready', loadModules, { once: true });
    } else {
        loadModules();
    }
})();
