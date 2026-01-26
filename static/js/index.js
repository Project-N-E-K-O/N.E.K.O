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
console.log('[主页] VRM 路径配置已从 vrm-init.js 加载:', VRM_PATHS_CACHE);
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
if (!VRM_PATHS_CACHE || !VRM_PATHS_CACHE.user_vrm) {
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
if (pathParts.length > 0 && !['focus', 'api', 'static', 'templates'].includes(pathParts[0])) {
lanlanNameFromUrl = decodeURIComponent(pathParts[0]);
}
}

// 从 API 获取配置
const apiUrl = lanlanNameFromUrl
? `/api/config/page_config?lanlan_name=${encodeURIComponent(lanlanNameFromUrl)}`
: '/api/config/page_config';

const response = await fetch(apiUrl);
const data = await response.json();

if (data.success) {
// 使用 URL 中的 lanlan_name（如果有），否则使用 API 返回的
lanlan_config.lanlan_name = lanlanNameFromUrl || data.lanlan_name || "";
const modelPath = data.model_path || "";
// 使用API返回的model_type，并转换为小写以防后端/旧数据大小写不一致
const modelType = (data.model_type || 'live2d').toLowerCase();
// 将 model_type 写回 lanlan_config，减少各处"猜模式"的分支
lanlan_config.model_type = modelType;
window.lanlan_config = lanlan_config;
// 根据model_type判断是Live2D还是VRM
if (modelType === 'vrm') {
if (modelPath && 
modelPath !== 'undefined' && 
modelPath !== 'null' && 
typeof modelPath === 'string' && 
modelPath.trim() !== '') {
vrmModel = modelPath;
window.vrmModel = vrmModel;
} else {
// 如果路径无效，设置为空字符串，让 vrm-init.js 使用默认模型
vrmModel = "";
window.vrmModel = "";
}
cubism4Model = "";
window.cubism4Model = "";
} else {
cubism4Model = modelPath;
window.cubism4Model = cubism4Model;
vrmModel = "";
window.vrmModel = "";
}

// 动态设置页面标题
document.title = `${lanlan_config.lanlan_name} Terminal - Project N.E.K.O.`;

console.log('页面配置加载成功:', { 
lanlan_name: lanlan_config.lanlan_name, 
live2d_model: cubism4Model,
vrm_model: vrmModel 
});
return true;
} else {
console.error('获取页面配置失败:', data.error);
// 使用默认值
lanlan_config.lanlan_name = "";
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
cubism4Model = "";
vrmModel = "";
window.lanlan_config = lanlan_config;
window.cubism4Model = "";
window.vrmModel = "";
return false;
}
}

// 标记配置是否已加载
window.pageConfigReady = loadPageConfig();
