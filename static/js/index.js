/**
 * 主页模块
 * 负责初始化主页相关功能，包括页面配置加载、VRM 路径缓存等
 */
// 页面配置 - 从 URL 或 API 获取
let lanlan_config = {
    lanlan_name: ""
};
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

// 异步获取页面配置
async function loadPageConfig() {
    try {
        // 优先从 URL 获取 lanlan_name
        const urlParams = new URLSearchParams(window.location.search);
        let lanlanNameFromUrl = urlParams.get('lanlan_name') || "";

        // 从路径中提取 lanlan_name (例如 /{lanlan_name})
        if (!lanlanNameFromUrl) {
            const pathParts = window.location.pathname.split('/').filter(Boolean);
            if (pathParts.length > 0 && !['focus', 'api', 'static', 'templates', 'chat', 'toast'].includes(pathParts[0])) {
                lanlanNameFromUrl = decodeURIComponent(pathParts[0]);
            }
        }

        // 从 API 获取配置
        const apiUrl = lanlanNameFromUrl
            ? `/api/config/page_config?lanlan_name=${encodeURIComponent(lanlanNameFromUrl)}`
            : '/api/config/page_config';

        const response = await fetch(apiUrl, {
            cache: 'no-store',
        });
        const data = await response.json();

        if (data.success) {
            // 使用 URL 中的 lanlan_name（如果有），否则使用 API 返回的
            lanlan_config.lanlan_name = lanlanNameFromUrl || data.lanlan_name || "";
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
            window.master_name = lanlan_config.master_name;
            window.master_profile_name = lanlan_config.master_profile_name;
            window.master_nickname = lanlan_config.master_nickname;
            window.master_display_name = lanlan_config.master_display_name;
            window.lanlan_config = lanlan_config;
            // 根据model_type判断是Live2D还是Live3D (VRM/MMD)
            if (modelType === 'live3d' || modelType === 'vrm') {
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
                cubism4Model = modelPath;
                window.cubism4Model = cubism4Model;
                vrmModel = "";
                window.vrmModel = "";
            }

            // 动态设置页面标题
            document.title = `${lanlan_config.lanlan_name} Terminal - Project N.E.K.O.`;

            return true;
        } else {
            console.error('获取页面配置失败:', data.error);
            // 使用默认值
            lanlan_config.lanlan_name = "";
            lanlan_config.lighting = null;
            cubism4Model = "";
            vrmModel = "";
            window.lanlan_config = lanlan_config;
            window.cubism4Model = "";
            window.vrmModel = "";
            return false;
        }
    } catch (error) {
        console.error('加载页面配置时出错:', error);
        // 使用默认值
        lanlan_config.lanlan_name = "";
        lanlan_config.lighting = null;
        cubism4Model = "";
        vrmModel = "";
        window.lanlan_config = lanlan_config;
        window.cubism4Model = "";
        window.vrmModel = "";
        return false;
    }
}

// 多窗口模式下，Chat 独立窗口（/chat）通过 IPC 从 Pet 窗口注入配置，不走 HTTP API。
// Pet 窗口（/）虽然也设了 __NEKO_MULTI_WINDOW__，但它是主窗口，应走正常 HTTP 路径。
if (window.__NEKO_MULTI_WINDOW__ && window.location.pathname === '/chat') {
    window.pageConfigReady = new Promise(function (resolve) {
        // preload 通过 IPC 拿到 Pet 窗口的 lanlan_config 后派发此事件
        window.addEventListener('neko:config-injected', function handler(event) {
            window.removeEventListener('neko:config-injected', handler);
            var d = (event && event.detail) || {};
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
            // 头像：如果 IPC 注入了头像 dataUrl，设置到 appChatAvatar
            // appChatAvatar 可能尚未加载（脚本顺序靠后），先暂存到全局变量
            if (d.avatarDataUrl) {
                if (window.appChatAvatar && typeof window.appChatAvatar.setExternalAvatar === 'function') {
                    window.appChatAvatar.setExternalAvatar(d.avatarDataUrl, d.avatarModelType || '');
                } else {
                    window.__nekoPendingAvatar = { dataUrl: d.avatarDataUrl, modelType: d.avatarModelType || '' };
                }
            }
            resolve(d);
        });
        // 超时保护：5 秒后 fallback 到 HTTP API
        setTimeout(function () {
            if (!lanlan_config.lanlan_name) {
                console.warn('[主页] 多窗口 IPC 配置超时，fallback 到 API');
                loadPageConfig().then(resolve);
            }
        }, 5000);
    });
} else {
    // 标记配置是否已加载
    window.pageConfigReady = loadPageConfig();
}

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
