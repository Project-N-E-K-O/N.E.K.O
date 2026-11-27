/**
 * 主页模块
 * 负责初始化主页相关功能，包括页面配置加载、VRM 路径缓存等
 */
// 页面配置 - 从 URL 或 API 获取
let lanlan_config = {
    lanlan_name: ""
};

const RESERVED_PAGE_PATHS = new Set([
    'api',
    'chat',
    'chat_full',
    'focus',
    'static',
    'templates',
    'toast',
    'web_chat_compact',
]);

function isReservedPagePath(pathname) {
    const pathParts = String(pathname || '').split('/').filter(Boolean);
    return pathParts.length > 0 && RESERVED_PAGE_PATHS.has(pathParts[0]);
}
window.lanlan_config = lanlan_config;
let cubism4Model = "";
let vrmModel = "";

// VRM 路径配置缓存（从后端获取）
let VRM_PATHS_CACHE = {
    user_vrm: '/user_vrm',
    static_vrm: '/static/vrm'
};

// 初始化 VRM 路径配置（使用默认值，等待 vrm-init.js 的 fetchVRMConfig 完成）
function loadVRMPathsConfig() {
    // 初始化 window.VRM_PATHS（使用默认值，供 window.convertVRMModelPath 使用）
    window.VRM_PATHS = window.VRM_PATHS || {
        user_vrm: '/user_vrm',
        static_vrm: '/static/vrm',
        isLoaded: false
    };

    // 使用事件机制等待 vrm-init.js 中的 fetchVRMConfig 完成
    const handleVRMPathsLoaded = (event) => {
        const paths = event.detail?.paths || window.VRM_PATHS;
        if (paths && paths.user_vrm && paths.static_vrm) {
            VRM_PATHS_CACHE = {
                user_vrm: paths.user_vrm,
                static_vrm: paths.static_vrm
            };
            window.VRM_PATHS.isLoaded = true;
        }
        window.removeEventListener('vrm-paths-loaded', handleVRMPathsLoaded);
    };

    // 监听配置加载完成事件
    window.addEventListener('vrm-paths-loaded', handleVRMPathsLoaded);

    // 如果配置已经加载（事件可能已经派发），立即处理
    if (window.VRM_PATHS && window.VRM_PATHS.isLoaded) {
        handleVRMPathsLoaded({ detail: { paths: window.VRM_PATHS } });
    } else {
        // 超时保护：如果 5 秒后仍未加载，使用默认值
        setTimeout(() => {
            if (!window.VRM_PATHS?.isLoaded) {
                console.warn('[主页] VRM 路径配置加载超时，使用默认值');
                window.removeEventListener('vrm-paths-loaded', handleVRMPathsLoaded);
            }
        }, 5000);
    }
}

// 同步设置默认值（不阻塞页面加载）
loadVRMPathsConfig();

function validPageSwitchRevision(value) {
    return Number.isSafeInteger(value) && value > 0 ? value : 0;
}

function latestPageSwitchRevision() {
    return Math.max(validPageSwitchRevision(window.lanlan_config?.switch_revision),
        validPageSwitchRevision(window.appState?._latestCatgirlSwitchRevision));
}

// Stamp checks happen before any model/DOM writes. If a live switch already owns
// the runtime, use its existing pending/rollback path instead of racing its model load.
function handoffPageSnapshotToSwitch(name, revision) {
    const state = window.appState;
    if (revision && name && typeof window.handleCatgirlSwitch === 'function'
        && (state?.isSwitchingCatgirl
            || (latestPageSwitchRevision() && name !== window.lanlan_config?.lanlan_name)
            || window.appCharacter?.needsCurrentCharacterRecovery?.(name, { fromSnapshot: true }))) {
        return Promise.resolve(window.handleCatgirlSwitch(name, window.lanlan_config?.lanlan_name || '', {
            switchRevision: revision
        }));
    }
    return null;
}

// Leave time for the backend's bounded wait on an in-progress rename of the
// current role (30s); the rest of the UI keeps its separate startup deadline.
const MODEL_SNAPSHOT_TIMEOUT_MS = 32000;
const MODEL_SNAPSHOT_RETRY_DELAYS = [600, 1200, 1800, 2400, 3000, 3600];
const MODEL_BOOT_RETIRED = Symbol('model boot retired');

async function readPageSnapshot(url, { timeoutMs = 0, retired = null } = {}) {
    const controller = timeoutMs && typeof AbortController !== 'undefined' ? new AbortController() : null;
    const read = async () => {
        const resp = await fetch(url, { cache: 'no-store', ...(controller ? { signal: controller.signal } : {}) });
        if (resp.ok === false) {
            const error = new Error('Snapshot HTTP ' + resp.status);
            error.status = resp.status;
            throw error;
        }
        return resp.json();
    };
    if (!timeoutMs) return read();
    let timer;
    try {
        const contenders = [read(), new Promise((_, reject) => {
            timer = setTimeout(() => {
                controller?.abort();
                reject(new Error('Snapshot deadline exceeded'));
            }, timeoutMs);
        })];
        if (retired) contenders.push(retired.then(() => {
            controller?.abort();
            throw MODEL_BOOT_RETIRED;
        }));
        return await Promise.race(contenders);
    } finally {
        clearTimeout(timer);
    }
}

function waitForModelRetry(delay, retired) {
    let timer;
    return Promise.race([new Promise(resolve => {
        timer = setTimeout(resolve, delay);
    }), retired]).finally(() => clearTimeout(timer));
}

// The original pageConfigReady promise remains pending across a small retry
// budget. All local renderers continue to consume that one validated snapshot.
async function finishModelBootstrap(expected) {
    for (let attempt = 0; attempt <= MODEL_SNAPSHOT_RETRY_DELAYS.length; attempt++) {
        if (window.__nekoPageModelConfigPending !== expected) return false;
        expected.retryable = false;
        const loaded = await loadPageConfig({ modelBoot: expected });
        if (loaded || window.__nekoPageModelConfigPending !== expected) return loaded;
        // A real stamped receipt can take over a failed/stalled boot, including
        // during retry. It cannot take over a healthy first read by name alone.
        expected.failed = true;
        const receipt = expected.receipt;
        if (receipt) {
            expected.receipt = null;
            Promise.resolve(window.handleCatgirlSwitch(expected.name, receipt.oldCatgirl, {
                switchRevision: receipt.switchRevision,
            })).catch(error => console.warn('[主页] 角色配置恢复失败:', error));
        }
        if (!expected.retryable || attempt === MODEL_SNAPSHOT_RETRY_DELAYS.length) return false;
        await waitForModelRetry(MODEL_SNAPSHOT_RETRY_DELAYS[attempt], expected.retired);
    }
    return false;
}

// 异步获取页面配置
async function loadPageConfig({ bounded = false, modelBoot = null } = {}) {
    try {
        // 优先从 URL 获取 lanlan_name
        const urlParams = new URLSearchParams(window.location.search);
        let lanlanNameFromUrl = urlParams.get('lanlan_name') || "";

        // 从路径中提取 lanlan_name (例如 /{lanlan_name})
        if (!lanlanNameFromUrl) {
            const pathParts = window.location.pathname.split('/').filter(Boolean);
            if (pathParts.length > 0 && !RESERVED_PAGE_PATHS.has(pathParts[0])) {
                lanlanNameFromUrl = decodeURIComponent(pathParts[0]);
            }
        }

        // 从 API 获取配置
        const apiUrl = lanlanNameFromUrl
            ? `/api/config/page_config?lanlan_name=${encodeURIComponent(lanlanNameFromUrl)}`
            : '/api/config/page_config';

        const data = await readPageSnapshot(apiUrl, modelBoot
            ? { timeoutMs: MODEL_SNAPSHOT_TIMEOUT_MS, retired: modelBoot.retired }
            : { timeoutMs: bounded ? 1500 : 0 });
        if (modelBoot && window.__nekoPageModelConfigPending !== modelBoot) return false;

        if (data.success) {
            // Check before applying any part of the snapshot, including paths, DOM and boot side effects.
            const switchRevision = validPageSwitchRevision(data.switch_revision);
            const expected = window.__nekoPageModelConfigPending;
            // Explicit named previews are intentionally outside current-character
            // ordering when the backend returns revision 0. This is true both for
            // the initial pinned bootstrap and later reloadPageConfig() reads.
            const explicitPreview = !!lanlanNameFromUrl && switchRevision === 0;
            if (!expected?.pinned && !explicitPreview && switchRevision < latestPageSwitchRevision()) {
                if (modelBoot) modelBoot.retryable = true;
                return false;
            }
            if (expected && (
                !data.lanlan_name || typeof data.model_type !== 'string' || !data.model_type.trim()
                || typeof data.model_path !== 'string'
                || (expected.pinned
                    ? data.lanlan_name !== expected.name
                    : switchRevision < expected.revision
                        || (switchRevision === expected.revision && data.lanlan_name !== expected.name))
            )) return false;
            const name = lanlanNameFromUrl || data.lanlan_name || "";
            const liveSwitch = handoffPageSnapshotToSwitch(name, switchRevision);
            if (liveSwitch) return (await liveSwitch) === true;
            lanlan_config.lanlan_name = name;
            lanlan_config.switch_revision = switchRevision;
            const modelPath = data.model_path || "";
            // 使用API返回的model_type，并转换为小写以防后端/旧数据大小写不一致
            const modelType = (data.model_type || 'live2d').toLowerCase();
            // 将 model_type 写回 lanlan_config，减少各处"猜模式"的分支
            lanlan_config.model_type = modelType;
            // 保存 live3d_sub_type 供 vrm-init.js / mmd-init.js 判断
            const live3dSubType = (data.live3d_sub_type || '').toLowerCase();
            lanlan_config.live3d_sub_type = live3dSubType;
            // master 信息（与 index.html 内联脚本对齐）
            lanlan_config.master_name = data.master_name || '';
            lanlan_config.master_profile_name = data.master_profile_name || '';
            lanlan_config.master_nickname = data.master_nickname || '';
            lanlan_config.master_display_name = data.master_display_name || data.master_nickname || data.master_name || '';
            lanlan_config.lighting = (data.lighting && typeof data.lighting === 'object')
                ? Object.assign({}, data.lighting)
                : null;
            lanlan_config.pngtuber = (data.pngtuber && typeof data.pngtuber === 'object')
                ? Object.assign({}, data.pngtuber)
                : null;
            window.master_name = lanlan_config.master_name;
            window.master_profile_name = lanlan_config.master_profile_name;
            window.master_nickname = lanlan_config.master_nickname;
            window.master_display_name = lanlan_config.master_display_name;
            window.lanlan_config = lanlan_config;
            if (expected) {
                window.cubism4Model = cubism4Model = "";
                window.vrmModel = vrmModel = "";
                window.mmdModel = "";
            }
            // 根据model_type判断是Live2D还是Live3D (VRM/MMD)
            if (modelType === 'pngtuber') {
                cubism4Model = "";
                window.cubism4Model = "";
                vrmModel = "";
                window.vrmModel = "";
                window.mmdModel = "";
                const live2dC = document.getElementById('live2d-container');
                const vrmC = document.getElementById('vrm-container');
                const mmdC = document.getElementById('mmd-container');
                if (live2dC) {
                    live2dC.style.display = 'none';
                    live2dC.classList.add('hidden');
                }
                const live2dCanvas = document.getElementById('live2d-canvas');
                if (live2dCanvas) {
                    live2dCanvas.style.visibility = 'hidden';
                    live2dCanvas.style.pointerEvents = 'none';
                }
                if (vrmC) vrmC.style.display = 'none';
                if (mmdC) mmdC.style.display = 'none';
                if (typeof window.hideOtherAvatarRuntimesForPNGTuber === 'function') {
                    window.hideOtherAvatarRuntimesForPNGTuber();
                }
                const shouldSkipPngtuberBoot = window.NekoAvatarFloatingBoot
                    && typeof window.NekoAvatarFloatingBoot.shouldSkipUserModelBoot === 'function'
                    && window.NekoAvatarFloatingBoot.shouldSkipUserModelBoot();
                if (shouldSkipPngtuberBoot) {
                    if (typeof window.NekoAvatarFloatingBoot.markUserModelBootSkipped === 'function') {
                        window.NekoAvatarFloatingBoot.markUserModelBootSkipped('pngtuber-init');
                    }
                    console.log('[主页] 新手教程启动预测命中，跳过用户 PNGTuber 模型加载');
                } else if (typeof window.loadPNGTuberAvatar === 'function') {
                    window.loadPNGTuberAvatar(lanlan_config.pngtuber || { idle_image: modelPath });
                }
            } else if (modelType === 'live3d' || modelType === 'vrm') {
                const validPath = modelPath &&
                    modelPath !== 'undefined' &&
                    modelPath !== 'null' &&
                    typeof modelPath === 'string' &&
                    modelPath.trim() !== '';
                if (validPath) {
                    if (live3dSubType === 'mmd') {
                        // MMD 子类型：路径给 mmdModel，不设置 vrmModel
                        window.mmdModel = modelPath;
                        vrmModel = '';
                        window.vrmModel = '';
                    } else {
                        // VRM 子类型（默认）
                        vrmModel = modelPath;
                        window.vrmModel = vrmModel;
                        window.mmdModel = '';
                    }
                    cubism4Model = "";
                    window.cubism4Model = "";

                    // 尽早切换容器可见性，避免空白 live2d-container 闪烁
                    const live2dC = document.getElementById('live2d-container');
                    if (live2dC) { live2dC.style.display = 'none'; }
                    if (live3dSubType === 'mmd') {
                        const mmdC = document.getElementById('mmd-container');
                        if (mmdC) { mmdC.style.display = 'block'; mmdC.style.visibility = 'visible'; }
                    } else {
                        const vrmC = document.getElementById('vrm-container');
                        if (vrmC) { vrmC.style.display = 'block'; }
                    }
                }
            } else {
                if (window.pngtuberManager && typeof window.pngtuberManager.hide === 'function') {
                    window.pngtuberManager.hide();
                }
                if (window.cleanupPNGTuberOverlayUI && typeof window.cleanupPNGTuberOverlayUI === 'function') {
                    window.cleanupPNGTuberOverlayUI();
                }
                const pngtuberC = document.getElementById('pngtuber-container');
                if (pngtuberC) {
                    pngtuberC.style.display = 'none';
                    pngtuberC.classList.add('hidden');
                }
                const live2dCanvas = document.getElementById('live2d-canvas');
                if (live2dCanvas) {
                    live2dCanvas.style.visibility = 'visible';
                    live2dCanvas.style.pointerEvents = '';
                }
                const live2dC = document.getElementById('live2d-container');
                if (live2dC) live2dC.classList.remove('hidden');
                cubism4Model = modelPath;
                window.cubism4Model = cubism4Model;
                vrmModel = "";
                window.vrmModel = "";
            }

            // 动态设置页面标题
            document.title = `${lanlan_config.lanlan_name} Terminal - Project N.E.K.O.`;

            if (expected) window.__nekoPageModelConfigPending = null;
            return true;
        }
        if (modelBoot) modelBoot.retryable = true;
        console.error('获取页面配置失败:', data.error);
    } catch (error) {
        if (error === MODEL_BOOT_RETIRED || (modelBoot && window.__nekoPageModelConfigPending !== modelBoot)) return false;
        if (modelBoot) {
            const status = error?.status;
            modelBoot.retryable = !status || status === 408 || status === 429 || status >= 500;
        }
        console.error('加载页面配置时出错:', error);
    }
    // A failed read cannot invalidate a switch or a prior successful bootstrap.
    if (!lanlan_config.lanlan_name && !latestPageSwitchRevision()) {
        lanlan_config.lanlan_name = "";
        lanlan_config.lighting = null;
        cubism4Model = "";
        vrmModel = "";
        window.lanlan_config = lanlan_config;
        window.cubism4Model = "";
        window.vrmModel = "";
    }
    return false;
}

// 暴露给模型初始化层做"模型路径缺失"时的有界自愈重取（live2d-init.js scheduleLive2DConfigRetry）。
// 重新拉取 page_config 并刷新 window.cubism4Model / window.vrmModel 等全局，再由调用方重试初始化。
window.reloadPageConfig = options => {
    const modelBoot = window.__nekoPageModelConfigPending;
    return modelBoot?.inFlight || loadPageConfig(options);
};

let resolvePageConfigReady = null;
window.pageConfigReady = new Promise(function (resolve) {
    resolvePageConfigReady = resolve;
});

let pageConfigLoadStarted = false;
let pageConfigLoadPromise = null;

function resolvePageConfig(result) {
    if (typeof resolvePageConfigReady === 'function') {
        resolvePageConfigReady(result);
        resolvePageConfigReady = null;
    }
    return result;
}

function startMultiWindowPageConfigLoad() {
    return new Promise(function (resolve, reject) {
        var settled = false;
        var emptyConfigRetryTimer = null;
        function requestInjectedConfig(delay) {
            if (typeof window.__nekoRequestConfigInjection !== 'function') {
                return;
            }
            if (emptyConfigRetryTimer) {
                clearTimeout(emptyConfigRetryTimer);
                emptyConfigRetryTimer = null;
            }
            emptyConfigRetryTimer = setTimeout(function () {
                emptyConfigRetryTimer = null;
                if (!settled && typeof window.__nekoRequestConfigInjection === 'function') {
                    window.__nekoRequestConfigInjection();
                }
            }, typeof delay === 'number' ? delay : 0);
        }
        async function loadMatchingPageConfig(name, revision, pinned = false) {
            if (!pinned && revision && revision < latestPageSwitchRevision()) return false;
            // An early live receipt may already have committed this precise
            // identity while desktop IPC was pairing its metadata. Do not
            // replace established model ownership with a new boot attempt.
            if (window.appCharacter?.hasCommittedPageModel?.(name, revision)) return true;
            let retire;
            const retired = new Promise(resolve => { retire = resolve; });
            const expected = { name, revision, pinned, failed: false, retired, retire };
            window.__nekoPageModelConfigPending = expected;
            // Identity is useful to the chat/socket layer, but is not model readiness.
            // Leave the existing full fetch in charge of boot; app.js already
            // bounds how long the rest of the UI waits for pageConfigReady.
            if (!window.lanlan_config?.lanlan_name && !latestPageSwitchRevision() && !window.appState?.isSwitchingCatgirl) {
                lanlan_config.lanlan_name = name;
                lanlan_config.switch_revision = revision;
                document.title = name + ' Terminal - Project N.E.K.O.';
            }
            expected.inFlight = finishModelBootstrap(expected);
            try {
                return await expected.inFlight;
            } finally {
                expected.inFlight = null;
            }
        }
        async function applyInjectedConfig(detail) {
            if (settled) {
                return;
            }
            var d = detail || {};
            if (!Object.prototype.hasOwnProperty.call(d, 'lanlan_name')) {
                requestInjectedConfig(500);
                return;
            }
            settled = true;
            if (emptyConfigRetryTimer) {
                clearTimeout(emptyConfigRetryTimer);
                emptyConfigRetryTimer = null;
            }
            window.removeEventListener('neko:config-injected', handler);
            const explicitName = new URLSearchParams(window.location.search).get('lanlan_name');
            if (explicitName && explicitName !== d.lanlan_name) {
                resolve(await loadMatchingPageConfig(explicitName, 0, true));
                return;
            }
            let switchRevision = validPageSwitchRevision(d.switch_revision);
            if (!switchRevision) {
                // Legacy desktop IPC includes the name but not its commit order. Pair it with
                // the small current-character snapshot; never borrow another character's revision.
                try {
                    const current = await readPageSnapshot('/api/characters/current_catgirl', { timeoutMs: 1500 });
                    if (current.current_catgirl === d.lanlan_name) {
                        switchRevision = validPageSwitchRevision(current.switch_revision);
                    } else if (current.current_catgirl && !explicitName) {
                        resolve(await loadMatchingPageConfig(current.current_catgirl, validPageSwitchRevision(current.switch_revision)));
                        return;
                    }
                } catch (error) {
                    console.warn('[主页] Could not verify injected character revision:', error);
                }
            }
            if (switchRevision < latestPageSwitchRevision()) {
                resolve(false);
                return;
            }
            if (!/^\/chat(?:_full)?(?:\/|$)/.test(window.location.pathname || '')) {
                // IPC conveys chat display data, not model URLs needed by the local renderers.
                resolve(await loadMatchingPageConfig(d.lanlan_name, switchRevision, !!explicitName));
                return;
            }
            const liveSwitch = handoffPageSnapshotToSwitch(d.lanlan_name, switchRevision);
            if (liveSwitch) {
                resolve((await liveSwitch) === true);
                return;
            }
            lanlan_config.switch_revision = switchRevision;
            lanlan_config.lanlan_name = d.lanlan_name || '';
            lanlan_config.model_type = (d.model_type || 'live2d').toLowerCase();
            lanlan_config.live3d_sub_type = (d.live3d_sub_type || '').toLowerCase();
            lanlan_config.lighting = (d.lighting && typeof d.lighting === 'object')
                ? Object.assign({}, d.lighting)
                : null;
            window.lanlan_config = lanlan_config;
            // master 信息
            window.master_name = d.master_name || '';
            window.master_profile_name = d.master_profile_name || '';
            window.master_nickname = d.master_nickname || '';
            window.master_display_name = d.master_display_name || d.master_nickname || d.master_name || '';
            lanlan_config.master_name = window.master_name;
            lanlan_config.master_profile_name = window.master_profile_name;
            lanlan_config.master_nickname = window.master_nickname;
            lanlan_config.master_display_name = window.master_display_name;
            var pageTitleName = lanlan_config.master_display_name || lanlan_config.lanlan_name;
            document.title = pageTitleName ? `${pageTitleName} Terminal - Project N.E.K.O.` : 'Project N.E.K.O.';
            // 头像：如果 IPC 注入了头像 dataUrl，设置到 appChatAvatar
            // appChatAvatar 可能尚未加载（脚本顺序靠后），先暂存到全局变量
            if (d.avatarDataUrl) {
                if (window.appChatAvatar && typeof window.appChatAvatar.setExternalAvatar === 'function') {
                    await window.appChatAvatar.setExternalAvatar(d.avatarDataUrl, d.avatarModelType || '');
                } else {
                    window.__nekoPendingAvatar = { dataUrl: d.avatarDataUrl, modelType: d.avatarModelType || '' };
                }
            }
            // resolve 类型与 5s 超时分支（loadPageConfig() → bool）保持一致，
            // 避免未来有 consumer 做 result === true 判断时 IPC 路径悄悄失效。
            resolve(true);
        }
        // preload 通过 IPC 拿到 Pet 窗口的 lanlan_config 后派发此事件
        var handler = function (event) {
            applyInjectedConfig((event && event.detail) || {}).catch(reject);
        };
        window.addEventListener('neko:config-injected', handler);
        if (window.__nekoInjectedConfig && Object.prototype.hasOwnProperty.call(window.__nekoInjectedConfig, 'lanlan_name')) {
            applyInjectedConfig(window.__nekoInjectedConfig).catch(reject);
            return;
        }
        requestInjectedConfig(0);
        // 超时保护：5 秒后 fallback 到 HTTP API
        setTimeout(function () {
            if (settled) {
                return;
            }
            settled = true;
            if (emptyConfigRetryTimer) {
                clearTimeout(emptyConfigRetryTimer);
                emptyConfigRetryTimer = null;
            }
            window.removeEventListener('neko:config-injected', handler);
            console.warn('[主页] 多窗口 IPC 配置超时，fallback 到 API');
            loadPageConfig().then(resolve);
        }, 5000);
    });
}

window.startPageConfigLoad = function startPageConfigLoad() {
    if (pageConfigLoadStarted) {
        return pageConfigLoadPromise || window.pageConfigReady;
    }

    pageConfigLoadStarted = true;
    pageConfigLoadPromise = (async function () {
        try {
            // 存储位置首屏哨兵要先放行，再开始页面配置与主业务加载；
            // 这样网页端启动更贴近“先意图捕获、后主界面”的受限启动语义。
            if (window.__nekoStorageLocationStartupBarrier
                && typeof window.__nekoStorageLocationStartupBarrier.then === 'function') {
                await window.__nekoStorageLocationStartupBarrier;
            }
            if (window.__nekoSevenDayTutorialStateReady
                && typeof window.__nekoSevenDayTutorialStateReady.then === 'function') {
                await window.__nekoSevenDayTutorialStateReady;
            }
            await window.NekoAvatarFloatingBoot?.waitForAuthoritativeState?.();

            if (window.__NEKO_MULTI_WINDOW__ && isReservedPagePath(window.location.pathname)) {
                return resolvePageConfig(await startMultiWindowPageConfigLoad());
            }

            return resolvePageConfig(await loadPageConfig());
        } catch (error) {
            console.warn('[主页] 页面配置加载失败，继续使用回退配置:', error);
            return resolvePageConfig(false);
        }
    })();

    return pageConfigLoadPromise;
};

// 对话区提示自动消失功能
function initChatTooltipAutoHide() {
    const tooltip = document.getElementById('chat-tooltip');
    if (tooltip) {
        setTimeout(() => {
            tooltip.classList.add('hidden');
        }, 3000);
    }
}

// 页面加载完成后初始化提示框自动消失
window.addEventListener('load', initChatTooltipAutoHide);
