(async function initVRMModules() {
    const loadModules = async () => {
        console.log(window.t ? window.t('modelManager.vrmLoadingDependencies') : '[VRM] 开始加载依赖模块');

        // 提前设置加载中标志，防止 vrm-init.js 加载时其内部 IIFE 再次触发模块加载
        // 注意：不能用 vrmModuleLoaded，因为下游 waitForVRM 会误判为已完成
        window._vrmModulesLoading = true;

        const vrmModules = [
            '/static/vrm-orientation.js',
            '/static/vrm-core.js',
            '/static/vrm-expression.js',
            '/static/vrm-animation.js',
            '/static/vrm-interaction.js',
            '/static/vrm-cursor-follow.js',
            '/static/vrm-manager.js',
            '/static/vrm-ui-popup.js',
            '/static/vrm-ui-buttons.js',
            '/static/vrm-init.js'
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

// ===== 选项条统一管理器 =====
/**
 * 选项条统一管理器
 * 封装所有选项条的通用功能，减少重复代码
 */
class DropdownManager {
    static instances = [];

    constructor(config) {
        this.config = {
            buttonId: config.buttonId,
            selectId: config.selectId,
            dropdownId: config.dropdownId,
            textSpanId: config.textSpanId,
            iconClass: config.iconClass,
            iconSrc: config.iconSrc,
            defaultText: config.defaultText || '选择',
            defaultTextKey: config.defaultTextKey || null,  // i18n key for dynamic translation
            iconAlt: config.iconAlt || config.defaultText,
            iconAltKey: config.iconAltKey || null,  // i18n key for icon alt
            onChange: config.onChange || (() => { }),
            getText: config.getText || ((option) => option.textContent),
            shouldSkipOption: config.shouldSkipOption || ((option) => {
                const value = option.value;
                const text = option.textContent;
                return value === '' && (
                    text.includes('请先加载') ||
                    text.includes('请选择') ||
                    text.includes('没有') ||
                    text.includes('加载中')
                );
            }),
            disabled: config.disabled || false,
            ...config
        };

        this.button = document.getElementById(this.config.buttonId);
        this.select = document.getElementById(this.config.selectId);
        this.dropdown = document.getElementById(this.config.dropdownId);
        this.textSpan = null;

        if (!this.button) {
            console.warn(`[DropdownManager] Button not found: ${this.config.buttonId}`);
            return;
        }

        DropdownManager.instances.push(this);
        this.init();
    }

    init() {
        this.ensureButtonStructure();
        if (!this.config.disabled && this.select && this.dropdown) {
            this.initDropdown();
        }
        this.updateButtonText();
    }

    ensureButtonStructure() {
        this.textSpan = document.getElementById(this.config.textSpanId);
        const icon = this.button.querySelector(`.${this.config.iconClass}`);

        if (!this.textSpan || !icon) {
            this.button.innerHTML = `
                <img src="${this.config.iconSrc}" alt="${this.config.iconAlt}" 
                     class="${this.config.iconClass}" 
                     style="height: 40px; width: auto; max-width: 80px; image-rendering: crisp-edges; margin-right: 10px; flex-shrink: 0; object-fit: contain; display: inline-block;">
                <span class="round-stroke-text" id="${this.config.textSpanId}" data-text="${this.config.defaultText}">${this.config.defaultText}</span>
            `;
            this.textSpan = document.getElementById(this.config.textSpanId);
        }
    }

    updateButtonText() {
        if (!this.textSpan) {
            this.ensureButtonStructure();
            if (!this.textSpan) return;
        }

        // 动态获取翻译文本（如果配置了 i18n key）
        let defaultText = this.config.defaultText;
        if (this.config.defaultTextKey && window.t && typeof window.t === 'function') {
            const translated = window.t(this.config.defaultTextKey);
            if (translated && translated !== this.config.defaultTextKey) {
                defaultText = translated;
            }
        }

        let text = defaultText;

        // 如果配置了 alwaysShowDefault，始终显示默认文字
        if (this.config.alwaysShowDefault) {
            text = defaultText;
        } else if (this.select) {
            if (this.select.value) {
                // 有选择的值，显示选中的选项
                const selectedOption = this.select.options[this.select.selectedIndex];
                if (selectedOption) {
                    text = this.config.getText(selectedOption);
                }
            } else if (this.select.options.length > 0) {
                // 没有选择，但有选项：显示第一个“可显示”的选项
                // 这里不能简单跳过空值选项，否则会导致动作/表情在未选择时显示第一个文件名
                //（看起来像自动选中），而不是“增加动作/增加表情”。
                const firstDisplayOption = Array.from(this.select.options)
                    .find(opt => !this.config.shouldSkipOption(opt));
                if (firstDisplayOption) {
                    text = this.config.getText(firstDisplayOption);
                }
            }
        }

        this.textSpan.textContent = text;
        this.textSpan.setAttribute('data-text', text);
    }

    updateDropdown() {
        if (!this.dropdown || !this.select) return;
        this.dropdown.innerHTML = '';

        // 辅助函数：尝试翻译 i18n 键
        const translateText = (text) => {
            if (!text) return text;
            // 如果文本看起来像 i18n 键（包含点号，如 "live2d.addMotion"）
            if (typeof text === 'string' && text.includes('.') && !text.includes(' ')) {
                try {
                    if (window.t && typeof window.t === 'function') {
                        const translated = window.t(text);
                        // 如果翻译成功（返回的不是键本身），使用翻译结果
                        if (translated && translated !== text) {
                            return translated;
                        }
                    }
                } catch (e) {
                    // 翻译失败，继续使用原文本
                }
            }
            return text;
        };

        Array.from(this.select.options).forEach(option => {
            if (this.config.shouldSkipOption(option)) return;

            const item = document.createElement('div');
            item.className = 'dropdown-item';
            item.dataset.value = option.value;
            if (option.dataset.itemId) {
                item.dataset.itemId = option.dataset.itemId;
            }

            let text = this.config.getText(option);
            // 尝试翻译文本（如果是 i18n 键）
            text = translateText(text);

            const textSpan = document.createElement('span');
            textSpan.className = 'dropdown-item-text';
            textSpan.textContent = text;
            textSpan.setAttribute('data-text', text);
            item.appendChild(textSpan);

            if (option.dataset.itemId) {
                const steamBadge = document.createElement('span');
                steamBadge.className = 'steam-badge';
                steamBadge.textContent = 'Steam';
                item.appendChild(steamBadge);
            }

            item.addEventListener('click', (e) => {
                e.stopPropagation();
                this.selectItem(option.value);
            });
            this.dropdown.appendChild(item);
        });
    }

    selectItem(value) {
        if (!this.select) return;
        this.select.value = value;
        this.select.dispatchEvent(new Event('change', { bubbles: true }));
        this.updateButtonText();
        this.hideDropdown();
        if (this.config.onChange) {
            this.config.onChange(value, this.select.options[this.select.selectedIndex]);
        }
    }

    static hideAll() {
        DropdownManager.instances.forEach(instance => { instance.hideDropdown(); });
    }

    static updateAllButtonText() {
        DropdownManager.instances.forEach(instance => { instance.updateButtonText(); });
    }

    async showDropdown() {
        if (!this.dropdown || this.config.disabled) return;

        // 在显示当前下拉菜单前，先隐藏所有其他的下拉菜单
        DropdownManager.hideAll();

        // 如果有 onBeforeShow 回调，先执行它
        if (typeof this.config.onBeforeShow === 'function') {
            await this.config.onBeforeShow();
        }

        this.updateDropdown();
        this.dropdown.style.display = 'block';
    }

    hideDropdown() {
        if (this.dropdown) {
            this.dropdown.style.display = 'none';
        }
    }

    async toggleDropdown() {
        if (this.config.disabled) return;
        const isVisible = this.dropdown && this.dropdown.style.display === 'block';
        if (isVisible) {
            this.hideDropdown();
        } else {
            await this.showDropdown();
        }
    }

    initDropdown() {
        if (!this.button || !this.dropdown) return;
        this.button.addEventListener('click', (e) => {
            e.stopPropagation();
            if (this.button.disabled) {
                return;
            }
            this.toggleDropdown().catch(err => console.error('[DropdownManager] toggle failed:', err));
        });
        document.addEventListener('click', (e) => {
            if (!this.button.contains(e.target) && !this.dropdown.contains(e.target)) {
                this.hideDropdown();
            }
        });
    }

    enable() {
        if (this.button) this.button.disabled = false;
        if (this.select) this.select.disabled = false;
    }

    disable() {
        if (this.button) this.button.disabled = true;
        if (this.select) this.select.disabled = true;
        this.hideDropdown();
    }
}

// ===== 跨页面通信系统 =====
const CHANNEL_NAME = 'neko_page_channel';
let modelManagerBroadcastChannel = null;

// 初始化 BroadcastChannel（如果支持）
try {
    if (typeof BroadcastChannel !== 'undefined') {
        modelManagerBroadcastChannel = new BroadcastChannel(CHANNEL_NAME);
        console.log('[CrossPageComm] model_manager BroadcastChannel 已初始化');
    }
} catch (e) {
    console.log('[CrossPageComm] BroadcastChannel 不可用，将使用 localStorage 后备方案');
}

// 用于页面间通信的事件处理
function sendMessageToMainPage(action, payload = {}) {
    try {
        const safePayload = {};
        if (payload && typeof payload === 'object') {
            for (const [key, value] of Object.entries(payload)) {
                if (key === 'action' || key === 'timestamp') continue;
                safePayload[key] = value;
            }
        }

        const message = {
            ...safePayload,
            action: action,
            timestamp: Date.now()
        };

        // 优先使用 BroadcastChannel
        if (modelManagerBroadcastChannel) {
            modelManagerBroadcastChannel.postMessage(message);
            console.log('[CrossPageComm] 通过 BroadcastChannel 发送消息:', action);
        }

        // 方式1: 如果是在弹出窗口中，使用 postMessage（更可靠）
        if (window.opener && !window.opener.closed) {
            console.log(`[消息发送] 使用 postMessage 发送消息: ${action}`);
            window.opener.postMessage(message, window.location.origin);
        }

        // 方式2: 使用localStorage事件机制发送消息给主页面（备用方案）
        try {
            localStorage.setItem('nekopage_message', JSON.stringify(message));
            localStorage.removeItem('nekopage_message'); // 立即移除以允许重复发送相同消息
            console.log(`[消息发送] 使用 localStorage 发送消息: ${action}`);
        } catch (e) {
            console.warn('localStorage 消息发送失败:', e);
        }
    } catch (e) {
        console.error('发送消息给主页面失败:', e);
    }
}



// 全局变量：跟踪未保存的更改
window.hasUnsavedChanges = false;

// 仅当本页确实保存过配置时，才触发主界面重载（避免退出就把主界面模型/位置“复位”）
window._modelManagerHasSaved = false;
window._modelManagerLanlanName = new URLSearchParams(window.location.search).get('lanlan_name') || '';
/**
 * ===== 代码质量改进：路径处理统一化 (DRY 原则) =====
 * 
 * ModelPathHelper: 统一处理所有模型路径标准化逻辑
 * 
 * 改进原因：
 * - 之前路径处理逻辑分散在多个地方（上传回调、模型选择、加载等）
 * - 重复代码导致维护困难，容易出现不一致
 * 
 * 功能：
 * - normalizeModelPath(): 标准化模型路径，处理 Windows 反斜杠、/user_vrm/ 前缀等
 * - vrmToUrl(): VRM 专用路径转换（内部调用 normalizeModelPath）
 * 
 * 使用位置：
 * - loadCurrentCharacterModel()
 * - vrmModelSelect change 事件监听器
 * - saveModelToCharacter()
 * - 以及其他所有需要路径标准化的地方
 */
const ModelPathHelper = {
    /**
     * 标准化模型路径
     * 处理 Windows 反斜杠、/user_vrm/ 前缀和本地文件路径
     * @param {string} rawPath - 原始路径
     * @param {string} type - 类型：'model' 或 'animation'（默认 'model'）
     * @returns {string} 标准化后的路径
     */
    normalizeModelPath(rawPath, type = 'model') {
        if (!rawPath) return '';

        // 确保 path 是字符串类型
        let path = String(rawPath).trim();

        // 如果已经是 URL 格式 (http/https) 或 Web 绝对路径 (/)，直接返回
        if (path.startsWith('http') || path.startsWith('/')) {
            // 统一将 Windows 的反斜杠转换为正斜杠
            return path.replace(/\\/g, '/');
        }

        // 统一将 Windows 的反斜杠转换为正斜杠
        const normalizedPath = path.replace(/\\/g, '/');
        const filename = normalizedPath.split('/').pop();

        // 1. 优先检测是否是项目内置的 static 目录
        if (normalizedPath.includes('static/vrm')) {
            return type === 'animation'
                ? `/static/vrm/animation/${filename}`
                : `/static/vrm/${filename}`;
        }

        // 2. 检测其他可能的目录结构
        else if (normalizedPath.includes('models/vrm')) {
            return type === 'animation'
                ? `/models/vrm/animations/${filename}`
                : `/models/vrm/${filename}`;
        }

        // 3. 默认 Fallback：如果是只有文件名，或者无法识别路径，默认去 user_vrm 找
        return `/user_vrm/${type === 'animation' ? 'animation/' : ''}${filename}`;
    },

    /**
     * 将后端返回的相对路径或本地路径转换为前端可用的 URL（VRM 专用）
     * @param {string} path - 原始路径
     * @param {string} type - 类型：'animation' 或 'model'（默认 'animation'）
     * @returns {string} 转换后的 URL
     */
    vrmToUrl(path, type = 'animation') {
        return this.normalizeModelPath(path, type);
    }
};
/**
 * ===== 代码质量改进：API 请求标准化 =====
 * 
 * RequestHelper: 统一处理所有网络请求，确保一致的错误处理和超时机制
 * 
 * 改进原因：
 * - 之前使用原生 fetch() 导致错误处理不一致
 * - 缺少统一的超时机制
 * - 错误信息不够详细
 * 
 * 功能：
 * - fetchJson(): 统一的 JSON API 请求方法
 *   - 自动超时处理（默认10秒）
 *   - 统一的错误处理和错误信息提取
 *   - 自动验证响应格式（确保是 JSON）
 * 
 * 已替换的 fetch() 调用：
 * - getLanlanName() 中的 /api/config/page_config
 * - saveModelToCharacter() 中的 /api/characters 相关调用
 * - loadCurrentCharacterModel() 中的 /api/characters 相关调用
 * - loadCharacterLighting() 中的 /api/characters/
 * - checkVoiceModeStatus() 中的 /api/characters/catgirl/{name}/voice_mode_status
 * - loadUserModels() 中的 /api/live2d/user_models
 * - 删除模型功能中的 /api/live2d/model/{name} (DELETE)
 * - 表情映射相关中的 /api/live2d/emotion_mapping/{name}
 * - loadEmotionMappingForModel() 中的 /api/live2d/emotion_mapping/{name}
 * - 模型配置文件加载中的 modelJsonUrl
 * - 以及其他所有 JSON API 调用
 * 
 * 注意：文件上传（FormData）的 fetch() 调用保留原样，因为需要特殊处理
 */
const RequestHelper = {
    /**
     * 统一的 JSON API 请求方法
     * @param {string} url - 请求 URL
     * @param {object} options - fetch 选项（method, headers, body 等）
     * @param {number} timeout - 超时时间（毫秒），默认 10000
     * @returns {Promise<object>} 解析后的 JSON 数据
     * @throws {Error} 如果请求失败、超时或响应不是有效的 JSON
     */
    async fetchJson(url, options = {}, timeout = 10000) {
        const controller = new AbortController();
        const id = setTimeout(() => controller.abort(), timeout);

        try {
            const response = await fetch(url, {
                ...options,
                signal: controller.signal
            });
            clearTimeout(id);

            // 检查 HTTP 状态码
            if (!response.ok) {
                // 尝试读取错误响应体以获取详细错误信息
                let errorMessage = `网络请求失败 (HTTP ${response.status})`;
                try {
                    const errorData = await response.json();
                    if (errorData.error) {
                        errorMessage = errorData.error;
                        // 如果有错误类型和堆栈跟踪，也记录到控制台
                        if (errorData.error_type) {
                            console.error(`错误类型: ${errorData.error_type}`);
                        }
                        if (errorData.traceback && errorData.traceback.length > 0) {
                            console.error('错误堆栈:', errorData.traceback.join('\n'));
                        }
                    }
                } catch (parseError) {
                    // 如果无法解析 JSON，使用默认错误消息
                    console.warn('无法解析错误响应:', parseError);
                }
                throw new Error(errorMessage);
            }

            // 检查内容类型，确保是 JSON
            const contentType = response.headers.get("content-type");
            if (!contentType || !contentType.includes("application/json")) {
                throw new Error("服务器未返回有效的 JSON 数据");
            }

            const data = await response.json();
            return data;
        } catch (error) {
            clearTimeout(id);
            if (error.name === 'AbortError') throw new Error("请求超时，请检查后端服务");
            throw error;
        }
    }
};

// 全屏控制函数
const requestFullscreen = () => {
    const elem = document.documentElement;
    if (elem.requestFullscreen) {
        return elem.requestFullscreen();
    } else if (elem.webkitRequestFullscreen) {
        return elem.webkitRequestFullscreen();
    } else if (elem.mozRequestFullScreen) {
        return elem.mozRequestFullScreen();
    } else if (elem.msRequestFullscreen) {
        return elem.msRequestFullscreen();
    }
    return Promise.reject(new Error('Fullscreen not supported'));
};

const exitFullscreen = () => {
    if (document.exitFullscreen) {
        return document.exitFullscreen();
    } else if (document.webkitExitFullscreen) {
        return document.webkitExitFullscreen();
    } else if (document.mozCancelFullScreen) {
        return document.mozCancelFullScreen();
    } else if (document.msExitFullscreen) {
        return document.msExitFullscreen();
    }
    return Promise.reject(new Error('Exit fullscreen not supported'));
};

const isFullscreen = () => {
    return !!(document.fullscreenElement ||
        document.webkitFullscreenElement ||
        document.mozFullScreenElement ||
        document.msFullscreenElement);
};

document.addEventListener('DOMContentLoaded', async () => {
    // 更新i18n翻译
    if (window.updatePageTexts && typeof window.updatePageTexts === 'function') {
        window.updatePageTexts();
    }
    // 延迟再次更新，确保i18next完全初始化
    setTimeout(() => {
        if (window.updatePageTexts && typeof window.updatePageTexts === 'function') {
            window.updatePageTexts();
        }
        // i18next更新后，重新保护按钮结构（延迟执行，确保函数已定义）
        setTimeout(() => {
            // 保护状态文本结构（如果被 i18n 覆盖）
            const statusDiv = document.getElementById('status');
            const statusTextSpan = document.getElementById('status-text');
            if (!statusTextSpan && statusDiv) {
                const currentText = statusDiv.textContent || '正在初始化...';
                statusDiv.innerHTML = `<img src="/static/icons/reminder_icon.png?v=1" alt="提示" class="reminder-icon" style="height: 16px; width: 16px; vertical-align: middle; margin-right: 6px; display: inline-block; image-rendering: crisp-edges;"><span id="status-text">${currentText}</span>`;
            }
            if (typeof updateBackToMainButtonText === 'function') {
                updateBackToMainButtonText();
            }
            if (typeof updateUploadButtonText === 'function') {
                updateUploadButtonText();
            }
            if (typeof updateModelTypeButtonText === 'function') {
                updateModelTypeButtonText();
            }
            if (typeof updatePersistentExpressionButtonText === 'function') {
                updatePersistentExpressionButtonText();
            }
        }, 50);
    }, 500);

    // Electron白屏修复
    if (document.body) {
        void document.body.offsetHeight;
        const currentOpacity = document.body.style.opacity || '1';
        document.body.style.opacity = '0.99';
        requestAnimationFrame(() => {
            document.body.style.opacity = currentOpacity;
        });
    }

    const statusDiv = document.getElementById('status');
    const statusTextSpan = document.getElementById('status-text');

    // 初始化状态文本（带图标）
    const updateStatusText = (text) => {
        if (statusTextSpan) {
            statusTextSpan.textContent = text;
        } else {
            // 如果 span 不存在，重建结构
            statusDiv.innerHTML = `<img src="/static/icons/reminder_icon.png?v=1" alt="提示" class="reminder-icon" style="height: 16px; width: 16px; vertical-align: middle; margin-right: 6px; display: inline-block; image-rendering: crisp-edges;"><span id="status-text">${text}</span>`;
        }
    };
    const modelTypeSelect = document.getElementById('model-type-select');
    const modelTypeSelectBtn = document.getElementById('model-type-select-btn');
    const modelTypeDropdown = document.getElementById('model-type-dropdown');
    const live2dModelSelectBtn = document.getElementById('live2d-model-select-btn');
    const live2dModelDropdown = document.getElementById('live2d-model-dropdown');
    const modelSelect = document.getElementById('model-select');
    const vrmModelSelect = document.getElementById('vrm-model-select');
    const vrmModelSelectBtn = document.getElementById('vrm-model-select-btn');
    const vrmModelSelectText = document.getElementById('vrm-model-select-text');
    const vrmModelDropdown = document.getElementById('vrm-model-dropdown');
    const vrmAnimationSelect = document.getElementById('vrm-animation-select');
    const vrmAnimationSelectBtn = document.getElementById('vrm-animation-select-btn');
    const vrmAnimationSelectText = document.getElementById('vrm-animation-select-text');
    const vrmAnimationDropdown = document.getElementById('vrm-animation-dropdown');
    const vrmExpressionSelect = document.getElementById('vrm-expression-select');
    const vrmExpressionSelectBtn = document.getElementById('vrm-expression-select-btn');
    const vrmExpressionSelectText = document.getElementById('vrm-expression-select-text');
    const vrmExpressionDropdown = document.getElementById('vrm-expression-dropdown');
    const live2dModelGroup = document.getElementById('live2d-model-group');
    const vrmModelGroup = document.getElementById('vrm-model-group');
    const vrmAnimationGroup = document.getElementById('vrm-animation-group');
    const vrmExpressionGroup = document.getElementById('vrm-expression-group');
    const triggerVrmExpressionBtn = document.getElementById('trigger-vrm-expression-btn');
    const live2dContainer = document.getElementById('live2d-container');
    const vrmContainer = document.getElementById('vrm-container');
    const motionSelect = document.getElementById('motion-select');
    const expressionSelect = document.getElementById('expression-select');
    const playMotionBtn = document.getElementById('play-motion-btn');
    const playExpressionBtn = document.getElementById('play-expression-btn');
    const savePositionBtn = document.getElementById('save-position-btn');
    const emotionConfigBtn = document.getElementById('emotion-config-btn');

    // 初始化保存设置按钮的样式
    // 注意：按钮宽度统一设置为270px（Live2D和VRM模式一致）
    // switchModelDisplay() 会根据实际模式设置正确的宽度
    const savePositionWrapper = document.getElementById('save-position-wrapper');
    if (savePositionBtn) {
        // 初始宽度设置为270px（与VRM模式一致），switchModelDisplay() 会根据模式调整
        savePositionBtn.style.setProperty('width', '270px', 'important');
        savePositionBtn.style.setProperty('flex', '0 0 270px', 'important');
        savePositionBtn.style.setProperty('max-width', '270px', 'important');
        savePositionBtn.style.setProperty('min-width', '270px', 'important');
        savePositionBtn.style.setProperty('display', 'flex', 'important');
    }
    // 初始化父容器样式
    if (savePositionWrapper) {
        savePositionWrapper.style.setProperty('width', '100%', 'important');
        savePositionWrapper.style.setProperty('max-width', '270px', 'important');
    }
    const uploadBtn = document.getElementById('upload-btn');
    const modelUpload = document.getElementById('model-upload');
    const vrmFileUpload = document.getElementById('vrm-file-upload');
    const motionFileUpload = document.getElementById('motion-file-upload');
    const expressionFileUpload = document.getElementById('expression-file-upload');
    const vrmAnimationFileUpload = document.getElementById('vrm-animation-file-upload');
    const uploadStatus = document.getElementById('upload-status');
    const backToMainBtn = document.getElementById('backToMainBtn');
    const deleteModelBtn = document.getElementById('delete-model-btn');
    const deleteModelModal = document.getElementById('delete-model-modal');
    const closeDeleteModal = document.getElementById('close-delete-modal');
    const cancelDeleteBtn = document.getElementById('cancel-delete-btn');
    const confirmDeleteBtn = document.getElementById('confirm-delete-btn');
    const userModelList = document.getElementById('user-model-list');
    const playVrmAnimationBtn = document.getElementById('play-vrm-animation-btn');
    let isVrmAnimationPlaying = false; // 跟踪VRM动作播放状态
    let isVrmExpressionPlaying = false; // 跟踪VRM表情播放状态

    // 更新模型类型按钮文字的函数（使用统一管理器）
    function updateModelTypeButtonText() {
        if (modelTypeManager) {
            modelTypeManager.updateButtonText();
        }
    }

    // 更新Live2D模型选择器按钮文字的函数（使用统一管理器）
    function updateLive2DModelSelectButtonText() {
        console.log('[updateLive2DModelSelectButtonText] 被调用, live2dModelManager:', live2dModelManager);
        if (live2dModelManager) {
            live2dModelManager.updateButtonText();
        } else {
            console.warn('[updateLive2DModelSelectButtonText] live2dModelManager 未初始化');
        }
    }


    // 更新Live2D模型下拉菜单（使用统一管理器）
    function updateLive2DModelDropdown() {
        if (live2dModelManager) {
            live2dModelManager.updateDropdown();
        }
    }

    // 初始化模型类型下拉菜单（使用统一管理器）
    // 注意：需要在 DOM 元素获取之后创建
    let modelTypeManager = null;
    let live2dModelManager = null;
    let motionManager = null;
    let expressionManager = null;
    let persistentExpressionManager = null;
    let vrmModelManager = null;
    let vrmAnimationManager = null;
    let vrmExpressionManager = null;

    // 延迟初始化管理器（确保 DOM 已加载）
    function initDropdownManagers() {
        if (!modelTypeManager) {
            modelTypeManager = new DropdownManager({
                buttonId: 'model-type-select-btn',
                selectId: 'model-type-select',
                dropdownId: 'model-type-dropdown',
                textSpanId: 'model-type-text',
                iconClass: 'model-type-icon',
                iconSrc: '/static/icons/model_type_icon.png?v=1',
                defaultText: window.i18next?.t('live2d.modelType') || '模型类型',
                defaultTextKey: 'live2d.modelType',
                iconAlt: window.i18next?.t('live2d.modelType') || '模型类型',
                alwaysShowDefault: false
            });
        }

        if (!live2dModelManager) {
            console.log('[Model Manager] 初始化 live2dModelManager');
            live2dModelManager = new DropdownManager({
                buttonId: 'live2d-model-select-btn',
                selectId: 'model-select',
                dropdownId: 'live2d-model-dropdown',
                textSpanId: 'live2d-model-select-text',
                iconClass: 'live2d-model-select-icon',
                iconSrc: '/static/icons/live2d_model_select_icon.png?v=1',
                defaultText: window.i18next?.t('live2d.selectModel') || '选择模型',
                defaultTextKey: 'live2d.selectModel',  // i18n key
                iconAlt: window.i18next?.t('live2d.selectModel') || '选择模型',
                alwaysShowDefault: false,  // 显示选中的模型名字，而不是默认文本
                shouldSkipOption: (option) => {
                    return option.value === '' && (
                        option.textContent.includes('请选择') ||
                        option.textContent.includes('选择模型') ||
                        option.textContent.includes('Select')
                    );
                },
                onChange: () => {
                    updateLive2DModelSelectButtonText();
                }
            });
        }

        if (!motionManager) {
            motionManager = new DropdownManager({
                buttonId: 'motion-select-btn',
                selectId: 'motion-select',
                dropdownId: 'motion-dropdown',
                textSpanId: 'motion-select-text',
                iconClass: 'motion-select-icon',
                iconSrc: '/static/icons/motion_select_icon.png?v=1',
                defaultText: window.i18next?.t('live2d.selectMotion') || '选择动作',
                iconAlt: window.i18next?.t('live2d.selectMotion') || '选择动作',
                shouldSkipOption: (option) => {
                    return option.value === '' && (
                        option.textContent.includes('请先加载') ||
                        option.textContent.includes('没有动作') ||
                        option.textContent.includes('Select')
                    );
                },
                onChange: () => {
                    updateMotionSelectButtonText();
                }
            });
        }

        if (!expressionManager) {
            expressionManager = new DropdownManager({
                buttonId: 'expression-select-btn',
                selectId: 'expression-select',
                dropdownId: 'expression-dropdown',
                textSpanId: 'expression-select-text',
                iconClass: 'expression-select-icon',
                iconSrc: '/static/icons/expression_chosen.png?v=1',
                defaultText: window.i18next?.t('live2d.selectExpression') || '选择表情',
                iconAlt: window.i18next?.t('live2d.selectExpression') || '选择表情',
                shouldSkipOption: (option) => {
                    return option.value === '' && (
                        option.textContent.includes('请先加载') ||
                        option.textContent.includes('没有表情') ||
                        option.textContent.includes('Select')
                    );
                },
                onChange: () => {
                    updateExpressionSelectButtonText();
                }
            });
        }

        if (!persistentExpressionManager) {
            persistentExpressionManager = new DropdownManager({
                buttonId: 'persistent-expression-select-btn',
                selectId: 'persistent-expression-select',
                dropdownId: 'persistent-expression-dropdown',
                textSpanId: 'persistent-expression-text',
                iconClass: 'persistent-expression-icon',
                iconSrc: '/static/icons/persistent_expression_icon.png?v=1',
                defaultText: window.i18next?.t('live2d.selectPersistentExpression') || '常驻表情',
                defaultTextKey: 'live2d.selectPersistentExpression',
                iconAlt: window.i18next?.t('live2d.selectPersistentExpression') || '常驻表情',
                alwaysShowDefault: true  // 始终显示默认文字，不显示选中的选项
                // 移除 disabled: true，让按钮可以正常使用
            });
        }

        if (!vrmModelManager) {
            vrmModelManager = new DropdownManager({
                buttonId: 'vrm-model-select-btn',
                selectId: 'vrm-model-select',
                dropdownId: 'vrm-model-dropdown',
                textSpanId: 'vrm-model-select-text',
                iconClass: 'vrm-model-select-icon',
                iconSrc: '/static/icons/live2d_model_select_icon.png?v=1',
                defaultText: window.i18next?.t('live2d.selectVRMModel') || '选择模型',
                iconAlt: window.i18next?.t('live2d.selectVRMModel') || '选择模型',
                alwaysShowDefault: false,
                shouldSkipOption: (option) => {
                    return option.value === '' && (
                        option.textContent.includes('加载中') ||
                        option.textContent.includes('Select')
                    );
                },
                onChange: () => {
                    if (typeof updateVRMModelSelectButtonText === 'function') {
                        updateVRMModelSelectButtonText();
                    }
                }
            });
        }

        if (!vrmAnimationManager) {
            vrmAnimationManager = new DropdownManager({
                buttonId: 'vrm-animation-select-btn',
                selectId: 'vrm-animation-select',
                dropdownId: 'vrm-animation-dropdown',
                textSpanId: 'vrm-animation-select-text',
                iconClass: 'vrm-animation-select-icon',
                iconSrc: '/static/icons/motion_select_icon.png?v=1',
                defaultText: window.i18next?.t('live2d.vrmAnimation.selectAnimation') || '选择动作',
                iconAlt: window.i18next?.t('live2d.vrmAnimation.selectAnimation') || '选择动作',
                shouldSkipOption: (option) => {
                    return option.value === '' && (
                        option.textContent.includes('请先加载') ||
                        option.textContent.includes('没有动作') ||
                        option.textContent.includes('Select')
                    );
                },
                onBeforeShow: async () => {
                    // 首次点击时加载动作列表
                    if (!animationsLoaded && currentModelType === 'vrm') {
                        animationsLoaded = true; // 防止重复加载
                        try {
                            await loadVRMAnimations(false);
                        } catch (error) {
                            console.error('加载VRM动作列表失败:', error);
                            animationsLoaded = false; // 加载失败时重置标记，允许重试
                        }
                    }
                },
                onChange: () => {
                    if (typeof updateVRMAnimationSelectButtonText === 'function') {
                        updateVRMAnimationSelectButtonText();
                    }
                }
            });
        }

        if (!vrmExpressionManager) {
            vrmExpressionManager = new DropdownManager({
                buttonId: 'vrm-expression-select-btn',
                selectId: 'vrm-expression-select',
                dropdownId: 'vrm-expression-dropdown',
                textSpanId: 'vrm-expression-select-text',
                iconClass: 'vrm-expression-select-icon',
                iconSrc: '/static/icons/expression_chosen.png?v=1',
                defaultText: window.i18next?.t('live2d.vrmExpression.selectExpression') || '选择表情',
                iconAlt: window.i18next?.t('live2d.vrmExpression.selectExpression') || '选择表情',
                shouldSkipOption: (option) => {
                    return option.value === '' && (
                        option.textContent.includes('请先加载') ||
                        option.textContent.includes('没有表情') ||
                        option.textContent.includes('Select')
                    );
                },
                onChange: () => {
                    if (typeof updateVRMExpressionSelectButtonText === 'function') {
                        updateVRMExpressionSelectButtonText();
                    }
                }
            });
        }
    }

    // 在 DOMContentLoaded 时初始化
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initDropdownManagers);
    } else {
        initDropdownManagers();
    }


    // 更新动作选择器按钮文字的函数（使用统一管理器）
    function updateMotionSelectButtonText() {
        if (motionManager) {
            motionManager.updateButtonText();
        }
    }

    // 更新动作下拉菜单（使用统一管理器）
    function updateMotionDropdown() {
        if (motionManager) {
            motionManager.updateDropdown();
        }
    }


    // 更新表情选择器按钮文字的函数（使用统一管理器）
    function updateExpressionSelectButtonText() {
        if (expressionManager) {
            expressionManager.updateButtonText();
        }
    }

    // 更新表情下拉菜单（使用统一管理器）
    function updateExpressionDropdown() {
        if (expressionManager) {
            expressionManager.updateDropdown();
        }
    }

    // 更新动作播放按钮图标（始终显示播放图标，不再切换）
    function updateMotionPlayButtonIcon() {
        if (!playMotionBtn) return;
        const icon = playMotionBtn.querySelector('.motion-play-icon');
        if (icon) {
            // 始终显示播放图标，强制设置为播放图标，绝不使用暂停图标
            icon.src = '/static/icons/motion_play_icon.png?v=3';
            icon.alt = '播放';
            // 确保图标路径正确，如果检测到暂停图标路径，立即修正
            if (icon.src.includes('pause')) {
                icon.src = '/static/icons/motion_play_icon.png?v=3';
            }
        }
    }

    // 动作播放状态
    let isMotionPlaying = false;

    // 确保播放按钮初始状态正确（始终显示播放图标）
    if (playMotionBtn) {
        updateMotionPlayButtonIcon();
    }


    // 更新常驻表情按钮文字的函数（使用统一管理器）
    function updatePersistentExpressionButtonText() {
        if (persistentExpressionManager) {
            persistentExpressionManager.updateButtonText();
        }
    }

    // 更新返回按钮文字的函数（支持i18n）- 使用CSS文字
    function updateBackToMainButtonText() {
        // 确保按钮结构存在
        let textSpan = document.getElementById('back-text');
        let backImg = backToMainBtn.querySelector('.back-icon');
        let pawImg = backToMainBtn.querySelector('.paw-icon');

        // 如果结构被破坏了，重新创建
        if (!textSpan || !backImg || !pawImg) {
            backToMainBtn.innerHTML = '<img src="/static/icons/back_to_main_button.png?v=1" alt="返回" class="back-icon" style="height: 40px; width: auto; max-width: 80px; image-rendering: crisp-edges; margin-right: 10px; flex-shrink: 0; object-fit: contain; display: inline-block;"><span class="round-stroke-text" id="back-text" data-text="返回主页">返回主页</span><img src="/static/icons/paw_ui.png?v=1" alt="猫爪" class="paw-icon" style="height: 70px; width: auto; max-width: 60px; image-rendering: crisp-edges; margin-left: auto; flex-shrink: 0; object-fit: contain; display: inline-block;">';
            textSpan = document.getElementById('back-text');
        }

        const isPopupWindow = window.opener !== null;
        if (textSpan) {
            let text;
            if (isPopupWindow) {
                text = t('common.close', '✖ 关闭');
            } else {
                text = t('live2d.backToMain', '返回主页');
            }
            textSpan.textContent = text;
            textSpan.setAttribute('data-text', text);
        }
    }

    // 检测页面来源，设置返回按钮文本
    updateBackToMainButtonText();

    // 监听语言变化事件，更新按钮文字
    window.addEventListener('localechange', () => {
        updateBackToMainButtonText();
    });

    // 更新上传按钮文字的函数（支持i18n）- 使用CSS文字实现圆角描边
    function updateUploadButtonText() {
        // 确保按钮结构存在
        let textSpan = document.getElementById('upload-text');
        let importImg = uploadBtn.querySelector('.import-icon');

        // 如果结构被破坏了，重新创建
        if (!textSpan || !importImg) {
            uploadBtn.innerHTML = '<img src="/static/icons/import_model_button_icon.png?v=1" alt="导入模型" class="import-icon" style="height: 40px; width: auto; max-width: 80px; image-rendering: crisp-edges; margin-right: 10px; flex-shrink: 0; object-fit: contain; display: inline-block;"><span class="round-stroke-text" id="upload-text" data-text="导入模型">导入模型</span>';
            textSpan = document.getElementById('upload-text');
        }

        // 根据模型类型更新文字 - 统一显示"导入模型"
        if (textSpan) {
            // 直接使用中文，不依赖翻译（避免翻译未初始化时显示键名）
            // 如果翻译已初始化，尝试获取翻译，否则直接使用中文
            let text = '导入模型';
            if (window.t && typeof window.t === 'function') {
                try {
                    const translated = window.t('live2d.importModel');
                    // 如果翻译返回的不是键名本身，且不是空，则使用翻译结果
                    if (translated && translated !== 'live2d.importModel' && translated !== 'importModel') {
                        text = translated.replace(/[:：]$/, ''); // 去掉冒号
                    }
                } catch (e) {
                    // 翻译失败，使用默认值
                    console.warn('翻译失败，使用默认值:', e);
                }
            }
            textSpan.textContent = text;
            textSpan.setAttribute('data-text', text);
        }
    }

    // 初始化时调用（延迟到i18next初始化后）
    // 等待更长时间确保i18next完全初始化
    setTimeout(() => {
        updateUploadButtonText();
        updateModelTypeButtonText();
        updatePersistentExpressionButtonText();
    }, 800);

    // 如果i18next已经初始化，立即调用一次
    if (window.t && typeof window.t === 'function' && window.i18n && window.i18n.isInitialized) {
        updateUploadButtonText();
        updateModelTypeButtonText();
        updatePersistentExpressionButtonText();
    }

    // 监听语言变化事件
    window.addEventListener('localechange', () => {
        updateUploadButtonText();
        DropdownManager.updateAllButtonText();
    });

    // 监听i18next的languageChanged事件（更可靠）
    if (window.i18n && window.i18n.on) {
        window.i18n.on('languageChanged', () => {
            updateUploadButtonText();
            DropdownManager.updateAllButtonText();
        });
    }

    // 页面加载时发送消息隐藏主界面（仅在弹出窗口模式下）
    const isPopupWindow = window.opener !== null;
    if (isPopupWindow) {
        sendMessageToMainPage('hide_main_ui');
    }

    // 翻译辅助函数：简化翻译调用并处理错误
    function t(key, fallback, params = {}) {
        try {
            if (window.t && typeof window.t === 'function') {
                const translated = window.t(key, params);
                // i18next 在缺失 key 时通常会直接返回 key 本身，这里统一回退到 fallback
                if (translated && translated !== key) {
                    return translated;
                }
            }
        } catch (e) {
            console.error(`[i18n] Translation failed for key "${key}":`, e);
        }
        return fallback;
    }

    let currentModelInfo = null;
    let availableModels = [];
    let currentModelFiles = { motion_files: [], expression_files: [] };
    let live2dModel = null;
    let currentEmotionMapping = null; // { motions: {...}, expressions: {...} }
    let currentModelType = 'live2d'; // 'live2d' or 'vrm'
    let vrmManager = null;
    let vrmAnimations = []; // VRM 动作列表
    let animationsLoaded = false; // 标记VRM动作列表是否已加载

    const showStatus = (msg, duration = 0) => {
        // 更新状态文本（保持图标结构）
        updateStatusText(msg);
        if (duration > 0) {
            setTimeout(() => {
                if (currentModelInfo) {
                    const modelMsg = t('live2d.currentModel', `当前模型: ${currentModelInfo.name}`, { model: currentModelInfo.name });
                    updateStatusText(modelMsg);
                }
            }, duration);
        }
    };

    await window.live2dManager.ensurePIXIReady('live2d-canvas', 'live2d-container');
    showStatus(t('live2d.pixiInitialized', 'PIXI 初始化完成'));

    // 先加载模型列表
    try {
        // 使用助手替换原有 fetch
        availableModels = await RequestHelper.fetchJson('/api/live2d/models');

        if (availableModels.length > 0) {
            modelSelect.innerHTML = ''; // 不添加第一个"选择模型"选项
            availableModels.forEach(model => {
                const option = document.createElement('option');
                option.value = model.name;
                option.textContent = model.display_name || model.name;
                if (model.item_id) {
                    option.dataset.itemId = model.item_id;
                }
                modelSelect.appendChild(option);
            });
            // 如果没有选择，自动选择第一个模型
            if (modelSelect.options.length > 0 && !modelSelect.value) {
                modelSelect.value = modelSelect.options[0].value;
            }
            // 更新按钮文字和下拉菜单
            if (typeof updateLive2DModelDropdown === 'function') {
                updateLive2DModelDropdown();
            }
            if (typeof updateLive2DModelSelectButtonText === 'function') {
                updateLive2DModelSelectButtonText();
            }
            showStatus(t('live2d.modelListLoaded', '模型列表加载成功'));
        } else {
            showStatus(t('live2d.noModelsFound', '未找到可用模型'));
        }
    } catch (e) {
        console.error('加载 Live2D 列表失败:', e);
        showStatus(t('live2d.modelListLoadFailed', `加载模型列表失败: ${e.message}`));
    }

    // 初始化模型类型（从 localStorage 或默认值）
    const savedModelType = localStorage.getItem('modelType') || 'live2d';
    await switchModelDisplay(savedModelType);

    // 注意：loadCurrentCharacterModel() 的调用已移到所有事件监听器注册之后
    // 这样才能正确触发 change 事件来加载模型

    // 获取 lanlan_name 的辅助函数
    async function getLanlanName() {
        // 优先从 URL 获取
        const urlParams = new URLSearchParams(window.location.search);
        let lanlanName = urlParams.get('lanlan_name') || '';

        // 如果 URL 中没有，从 API 获取（使用 RequestHelper）
        if (!lanlanName) {
            try {
                const data = await RequestHelper.fetchJson('/api/config/page_config');
                if (data.success) {
                    lanlanName = data.lanlan_name || '';
                }
            } catch (error) {
                console.error('获取 lanlan_name 失败:', error);
            }
        }

        return lanlanName;
    }

    // 动态设置参数编辑器链接，传递 lanlan_name 参数
    (async function updateParameterEditorLink() {
        try {
            const paramEditorBtn = document.getElementById('parameter-editor-btn');
            if (paramEditorBtn) {
                const lanlanName = await getLanlanName();
                if (lanlanName) {
                    paramEditorBtn.href = `/live2d_parameter_editor?lanlan_name=${encodeURIComponent(lanlanName)}`;
                }
            }
        } catch (error) {
            console.error('更新参数编辑器链接失败:', error);
        }
    })();

    //
    // 注意：必须使用专用接口保存模型和光照设置，因为通用接口会过滤掉保留字段
    // 保存模型设置到角色的函数（全面升级版）
    async function saveModelToCharacter(modelName, itemId = null, vrmAnimation = null) {
        function decodeMaybeUrlComponent(value) {
            if (typeof value !== 'string') return value;
            try {
                return decodeURIComponent(value);
            } catch {
                return value;
            }
        }

        function extractLive2DFolderNameFromPath(modelPath) {
            if (!modelPath || typeof modelPath !== 'string') return null;
            const normalized = modelPath.split('?')[0].split('#')[0].replace(/\\/g, '/');
            const segments = normalized.split('/').filter(Boolean);
            if (segments.length < 2) return null;
            const filename = segments[segments.length - 1];
            const folder = segments[segments.length - 2];
            if (!/\.model3\.json$/i.test(filename)) return null;

            if (segments[0] === 'workshop') {
                if (segments.length >= 4) return decodeMaybeUrlComponent(folder);
                const base = filename.replace(/\.model3\.json$/i, '');
                return decodeMaybeUrlComponent(base) || null;
            }

            return decodeMaybeUrlComponent(folder) || null;
        }

        try {
            // 1. 获取角色名并验证
            const lanlanName = await getLanlanName();
            if (!lanlanName || lanlanName.trim() === '') {
                const errorMsg = t('live2d.cannotSaveNoCharacter', '无法保存：未指定角色名称');
                showStatus(errorMsg, 3000);
                if (typeof showToast === 'function') {
                    showToast(errorMsg, 'error');
                }
                return false;
            }

            // 在发送 PUT 请求保存数据前，添加校验
            if (currentModelType === 'vrm') {
                if (!modelName ||
                    modelName === 'undefined' ||
                    modelName === 'null' ||
                    (typeof modelName === 'string' && (
                        modelName.trim() === '' ||
                        modelName.toLowerCase().includes('undefined') ||
                        modelName.toLowerCase().includes('null')
                    ))) {
                    console.error('[模型管理] 检测到无效的 VRM 模型路径，尝试自动修复:', modelName);

                    if (currentModelInfo && currentModelInfo.path &&
                        currentModelInfo.path !== 'undefined' &&
                        currentModelInfo.path !== 'null' &&
                        !currentModelInfo.path.toLowerCase().includes('undefined')) {
                        modelName = currentModelInfo.path;
                    } else if (currentModelInfo && currentModelInfo.name &&
                        currentModelInfo.name !== 'undefined' &&
                        currentModelInfo.name !== 'null' &&
                        !currentModelInfo.name.toLowerCase().includes('undefined')) {
                        const filename = currentModelInfo.name.endsWith('.vrm')
                            ? currentModelInfo.name
                            : `${currentModelInfo.name}.vrm`;
                        modelName = ModelPathHelper.normalizeModelPath(filename, 'model');
                    } else {
                        const errorMsg = t('live2d.vrmModelPathInvalid', 'VRM 模型路径无效，无法保存。请重新选择模型。');
                        showStatus(errorMsg, 5000);
                        throw new Error('VRM 模型路径无效: ' + modelName);
                    }
                }
            }

            showStatus(t('live2d.savingSettings', '正在保存设置...'));

            // 2. 构建模型数据，使用专用接口保存
            const modelData = {
                model_type: currentModelType,
            };

            if (currentModelType === 'vrm') {
                // 转换 VRM 路径：从完整 HTTP 路径转换为后端要求的相对路径
                let vrmPath = modelName;
                if (vrmPath && typeof vrmPath === 'string') {
                    const urlMatch = vrmPath.match(/^(?:http|https):\/\/[^/]+(\/user_vrm\/.*|\/static\/vrm\/.*)/);
                    if (urlMatch) {
                        vrmPath = urlMatch[1];
                    }
                    if (!vrmPath.startsWith('/user_vrm/') && !vrmPath.startsWith('/static/vrm/')) {
                        if (currentModelInfo && currentModelInfo.path) {
                            const infoPathMatch = currentModelInfo.path.match(/^(?:http|https):\/\/[^/]+(\/user_vrm\/.*|\/static\/vrm\/.*)/);
                            if (infoPathMatch) {
                                vrmPath = infoPathMatch[1];
                            } else if (currentModelInfo.path.startsWith('/user_vrm/') || currentModelInfo.path.startsWith('/static/vrm/')) {
                                vrmPath = currentModelInfo.path;
                            }
                        }
                    }
                }
                modelData.vrm = vrmPath;
                const idleAnimSel = document.getElementById('idle-animation-select');
                if (vrmAnimation) {
                    modelData.vrm_animation = vrmAnimation;
                } else if (idleAnimSel && idleAnimSel.value) {
                    modelData.vrm_animation = idleAnimSel.value;
                }

                if (idleAnimSel && idleAnimSel.value) {
                    modelData.idle_animation = idleAnimSel.value;
                }
            } else {
                const inferredFolderName = extractLive2DFolderNameFromPath(
                    (currentModelInfo && currentModelInfo.path) ? currentModelInfo.path : modelName
                );
                modelData.live2d = decodeMaybeUrlComponent(inferredFolderName || modelName);
                if (itemId != null && itemId !== '') {
                    modelData.item_id = itemId;
                    modelData.live2d_item_id = itemId;
                }
            }

            // 3. 使用【专用模型接口】保存模型设置（包含光照和待机动作）
            const modelResult = await RequestHelper.fetchJson(
                `/api/characters/catgirl/l2d/${encodeURIComponent(lanlanName)}`,
                {
                    method: 'PUT',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify(modelData)
                }
            );

            if (!modelResult.success) {
                throw new Error(modelResult.error || '保存模型设置失败');
            }

            let lightingResult = null;
            const ambient = document.getElementById('ambient-light-slider');
            const main = document.getElementById('main-light-slider');

            // 4. 如果是 VRM 模式，单独保存光照设置（仅光照部分独立保存）
            if (currentModelType === 'vrm' && ambient && main) {
                const lightingData = {
                    lighting: {
                        ambient: parseFloat(ambient.value),
                        main: parseFloat(main.value),
                        fill: 0.0,
                        rim: 0.0,
                        top: 0.0,
                        bottom: 0.0
                    }
                };

                const exposure = document.getElementById('exposure-slider');
                if (exposure) {
                    lightingData.lighting.exposure = parseFloat(exposure.value);
                }
                const tonemapping = document.getElementById('tonemapping-select');
                if (tonemapping) {
                    lightingData.lighting.toneMapping = parseInt(tonemapping.value);
                }

                try {
                    lightingResult = await RequestHelper.fetchJson(
                        `/api/characters/catgirl/${encodeURIComponent(lanlanName)}/lighting`,
                        {
                            method: 'PUT',
                            headers: {
                                'Content-Type': 'application/json',
                            },
                            body: JSON.stringify(lightingData)
                        }
                    );
                } catch (e) {
                    console.warn('保存光照设置失败:', e);
                    lightingResult = { success: false, error: e.message };
                }
            }

            const modelDisplayName = currentModelType === 'vrm' ? `VRM: ${modelName}` : modelName;
            let saveMessage;
            const lightingFailed = currentModelType === 'vrm' && ambient && main && (!lightingResult || !lightingResult.success);

            if (lightingFailed) {
                saveMessage = t('live2d.modelSavedLightingFailed', `已保存模型设置，光照设置保存失败`, { name: modelDisplayName });
            } else if (currentModelType === 'vrm' && ambient && main) {
                saveMessage = t('live2d.modelSettingsSavedWithLighting', `已保存模型和光照设置`, { name: modelDisplayName });
            } else if (currentModelType === 'vrm') {
                saveMessage = t('live2d.modelSettingsSaved', `已保存模型设置`, { name: modelDisplayName });
            } else {
                saveMessage = t('live2d.modelSettingsSaved', `已保存模型设置`, { name: modelDisplayName });
            }
            showStatus(saveMessage, 2000);
            return true;

        } catch (error) {
            console.error('保存模型设置失败:', error);
            showStatus(t('live2d.saveFailed', `保存失败: ${error.message}`), 3000);
            return false;
        }
    }

    // 模型类型切换处理
    async function switchModelDisplay(type) {
        currentModelType = type;
        localStorage.setItem('modelType', type);
        if (modelTypeSelect) modelTypeSelect.value = type;

        const sidebar = document.getElementById('sidebar');
        if (sidebar) {
            sidebar.classList.toggle('mode-live2d', type === 'live2d');
            sidebar.classList.toggle('mode-vrm', type === 'vrm');
        }

        // 更新模型类型按钮文字
        if (modelTypeManager) {
            modelTypeManager.updateButtonText();
        }

        if (type === 'live2d') {
            // 【新增】清理VRM资源
            if (window.vrmManager) {
                try {
                    // 停止VRM动画循环
                    if (window.vrmManager._animationFrameId) {
                        cancelAnimationFrame(window.vrmManager._animationFrameId);
                        window.vrmManager._animationFrameId = null;
                    }

                    // 清理VRM管理器
                    if (typeof window.vrmManager.dispose === 'function') {
                        await window.vrmManager.dispose();
                    }

                    // 清理Three.js渲染器（但不移除canvas，因为后续可能还要用）
                    if (window.vrmManager.renderer) {
                        window.vrmManager.renderer.dispose();
                        window.vrmManager.renderer = null;
                    }

                    // 清理场景
                    if (window.vrmManager.scene) {
                        window.vrmManager.scene.clear();
                        window.vrmManager.scene = null;
                    }

                    // 重置当前模型引用
                    window.vrmManager.currentModel = null;
                    window.vrmManager._isInitialized = false;
                    window._isVRMInitializing = false;

                    // 清理VRM的UI元素
                    const vrmFloatingButtons = document.getElementById('vrm-floating-buttons');
                    if (vrmFloatingButtons) {
                        vrmFloatingButtons.remove();
                    }

                    const vrmLockIcon = document.getElementById('vrm-lock-icon');
                    if (vrmLockIcon) {
                        vrmLockIcon.remove();
                    }

                    const vrmReturnBtn = document.getElementById('vrm-return-button-container');
                    if (vrmReturnBtn) {
                        vrmReturnBtn.remove();
                    }
                } catch (cleanupError) {
                    console.warn('[模型管理] VRM清理时出现警告:', cleanupError);
                }
            }

            if (live2dModelGroup) live2dModelGroup.style.display = 'flex';
            if (vrmModelGroup) vrmModelGroup.style.display = 'none';
            if (live2dContainer) live2dContainer.style.display = 'block';
            if (vrmExpressionGroup) vrmExpressionGroup.style.display = 'none';
            if (vrmContainer) {
                vrmContainer.classList.add('hidden');
                vrmContainer.style.display = 'none';
            }
            // 显示 Live2D 特有的控件
            document.querySelectorAll('.control-group').forEach(group => {
                if (group.id !== 'live2d-model-group' &&
                    group.id !== 'vrm-model-group' &&
                    group.id !== 'vrm-expression-group' &&
                    group.id !== 'vrm-animation-group') {
                    group.style.display = 'flex';
                }
            });
            // 显示常驻表情组（Live2D特有）
            const persistentExpressionGroup = document.getElementById('persistent-expression-group');
            if (persistentExpressionGroup) persistentExpressionGroup.style.display = 'flex';
            // 显示参数编辑器按钮
            const parameterEditorGroup = document.getElementById('parameter-editor-group');
            if (parameterEditorGroup) parameterEditorGroup.style.display = 'flex';
            // Live2D模式下：显示保存设置按钮组
            const emotionManagerGroup = document.getElementById('emotion-manager-group');
            const emotionConfigGroup = document.getElementById('emotion-config-group');
            if (emotionManagerGroup) {
                emotionManagerGroup.style.display = 'flex';
                // 显示保存设置按钮
                const savePositionBtn = document.getElementById('save-position-btn');
                const savePositionWrapper = document.getElementById('save-position-wrapper');
                if (savePositionBtn) {
                    savePositionBtn.style.display = 'flex';
                }
                if (savePositionWrapper) {
                    savePositionWrapper.style.setProperty('width', '100%', 'important');
                    savePositionWrapper.style.setProperty('max-width', '270px', 'important');
                }
            }
            // 显示情感配置按钮组
            if (emotionConfigGroup) {
                emotionConfigGroup.style.display = 'flex';
            }
            // 隐藏VRM情感配置按钮（Live2D模式下）
            const vrmEmotionConfigGroup = document.getElementById('vrm-emotion-config-group');
            if (vrmEmotionConfigGroup) {
                vrmEmotionConfigGroup.style.display = 'none';
            }

            // 更新上传按钮提示文本（Live2D模式）
            if (uploadBtn) {
                updateUploadButtonText();
            }
            // 隐藏VRM文件选择器，显示Live2D文件夹选择器
            if (vrmFileUpload) vrmFileUpload.style.display = 'none';
            if (modelUpload) modelUpload.style.display = 'none'; // 保持隐藏，通过按钮触发

            // 隐藏 VRM 动作预览组
            if (vrmAnimationGroup) vrmAnimationGroup.style.display = 'none';
            // 切换到Live2D时，重置VRM动作和表情播放状态
            if (isVrmAnimationPlaying && vrmManager) {
                vrmManager.stopVRMAAnimation();
                isVrmAnimationPlaying = false;
                updateVRMAnimationPlayButtonIcon();
            }
            if (isVrmExpressionPlaying && vrmManager && vrmManager.expression) {
                vrmManager.expression.resetBaseExpression();
                isVrmExpressionPlaying = false;
                updateVRMExpressionPlayButtonIcon();
            }
            // 隐藏 VRM 打光设置组
            const vrmLightingGroup = document.getElementById('vrm-lighting-group');
            if (vrmLightingGroup) vrmLightingGroup.style.display = 'none';
            // 隐藏 VRM 待机动作设置组
            const vrmIdleAnimGroup = document.getElementById('vrm-idle-animation-group');
            if (vrmIdleAnimGroup) vrmIdleAnimGroup.style.display = 'none';

            // 确保 Live2D Canvas 存在（PIXI 被销毁时可能移除）
            const live2dCanvas = document.getElementById('live2d-canvas');
            if (!live2dCanvas) {
                const newCanvas = document.createElement('canvas');
                newCanvas.id = 'live2d-canvas';
                const container = document.getElementById('live2d-container');
                if (container) {
                    container.appendChild(newCanvas);
                }
            }

            // 幂等初始化：仅在未就绪时初始化，避免重复重建导致首帧抖动
            if (window.live2dManager) {
                await window.live2dManager.ensurePIXIReady('live2d-canvas', 'live2d-container');
                showStatus(t('live2d.pixiInitialized', 'PIXI 初始化完成'));
            }
        } else { // VRM
            // 【新增】清理Live2D资源（内存管理改进）
            if (window.live2dManager) {
                try {
                    // 1. 先从舞台移除模型（避免销毁时访问已移除的对象）
                    if (window.live2dManager.currentModel && window.live2dManager.pixi_app && window.live2dManager.pixi_app.stage) {
                        try {
                            window.live2dManager.pixi_app.stage.removeChild(window.live2dManager.currentModel);
                        } catch (e) {
                            console.warn('[模型管理] 从舞台移除模型时出现警告:', e);
                        }
                    }

                    // 2. 销毁模型本身（不要提前清空 internalModel）
                    if (window.live2dManager.currentModel) {
                        const live2dModel = window.live2dManager.currentModel;

                        // 尝试调用 release 方法释放模型资源
                        if (typeof live2dModel.release === 'function') {
                            try {
                                live2dModel.release();
                                console.log('[模型管理] Live2D 模型资源已释放');
                            } catch (releaseError) {
                                console.warn('[模型管理] 释放 Live2D 模型资源时出现警告:', releaseError);
                            }
                        }

                        // 销毁模型（让 PIXI 自己处理内部清理）
                        try {
                            live2dModel.destroy({ children: true });
                        } catch (e) {
                            console.warn('[模型管理] 销毁 Live2D 模型时出现警告:', e);
                        }

                        // 清空模型引用
                        window.live2dManager.currentModel = null;
                    }

                    // 3. 销毁PIXI应用（在模型销毁之后）
                    if (window.live2dManager.pixi_app) {
                        try {
                            // 先停止 ticker 防止渲染已销毁的对象
                            if (window.live2dManager.pixi_app.ticker) {
                                window.live2dManager.pixi_app.ticker.stop();
                            }
                            // 销毁 PIXI 应用，但不销毁 children（已经在上面处理过了）
                            window.live2dManager.pixi_app.destroy(true, {
                                children: false,
                                texture: true,
                                baseTexture: true
                            });
                            window.live2dManager.pixi_app = null;
                            // 【关键修复】重置初始化标志
                            window.live2dManager.isInitialized = false;
                            console.log('[模型管理] PIXI 应用已销毁');
                        } catch (pixiError) {
                            console.warn('[模型管理] PIXI销毁时出现警告:', pixiError);
                            // 即使销毁出错，也要重置状态
                            window.live2dManager.pixi_app = null;
                            window.live2dManager.isInitialized = false;
                        }
                    }
                } catch (cleanupError) {
                    console.warn('[模型管理] Live2D清理时出现警告:', cleanupError);
                    // 确保状态被重置
                    if (window.live2dManager) {
                        window.live2dManager.currentModel = null;
                        window.live2dManager.pixi_app = null;
                        window.live2dManager.isInitialized = false;
                    }
                }
            }

            if (live2dModelGroup) live2dModelGroup.style.display = 'none';
            if (vrmModelGroup) vrmModelGroup.style.display = 'flex';
            if (vrmExpressionGroup) vrmExpressionGroup.style.display = 'flex';
            if (live2dContainer) live2dContainer.style.display = 'none';
            if (vrmContainer) {
                vrmContainer.classList.remove('hidden');
                vrmContainer.style.display = 'block';
            }
            // 更新VRM选择器按钮文字
            if (typeof updateVRMAnimationSelectButtonText === 'function') {
                updateVRMAnimationSelectButtonText();
            }
            if (typeof updateVRMExpressionSelectButtonText === 'function') {
                updateVRMExpressionSelectButtonText();
            }

            // 清理 Live2D 的 UI 元素（锁图标、浮动按钮等）
            const live2dLockIcon = document.getElementById('live2d-lock-icon');
            if (live2dLockIcon) {
                live2dLockIcon.remove();
            }
            const live2dFloatingButtons = document.getElementById('live2d-floating-buttons');
            if (live2dFloatingButtons) {
                live2dFloatingButtons.remove();
            }
            const live2dReturnBtn = document.getElementById('live2d-return-button-container');
            if (live2dReturnBtn) {
                live2dReturnBtn.remove();
            }
            // 隐藏 Live2D 特有的控件
            const live2dOnlyControls = ['motion-select', 'expression-select', 'play-motion-btn', 'play-expression-btn'];
            live2dOnlyControls.forEach(id => {
                const elem = document.getElementById(id);
                if (elem) {
                    const group = elem.closest('.control-group');
                    if (group) group.style.display = 'none';
                }
            });
            // VRM模式下：显示保存设置按钮
            const emotionManagerGroup = document.getElementById('emotion-manager-group');
            const emotionConfigGroup = document.getElementById('emotion-config-group');
            if (emotionManagerGroup) {
                // 显示保存设置按钮，并设置为270px宽度（占据整个容器）
                const savePositionBtn = document.getElementById('save-position-btn');
                const savePositionWrapper = document.getElementById('save-position-wrapper');
                if (savePositionBtn) {
                    savePositionBtn.style.display = 'flex';
                    savePositionBtn.style.setProperty('width', '270px', 'important');
                    savePositionBtn.style.setProperty('flex', '0 0 270px', 'important');
                    savePositionBtn.style.setProperty('max-width', '270px', 'important');
                    savePositionBtn.style.setProperty('min-width', '270px', 'important');
                }
                // VRM模式下，父容器可以拉伸
                if (savePositionWrapper) {
                    savePositionWrapper.style.setProperty('width', '100%', 'important');
                    savePositionWrapper.style.setProperty('max-width', '270px', 'important');
                }
                emotionManagerGroup.style.display = 'flex';
            }
            // 隐藏情感配置按钮组（VRM模式下不需要Live2D的）
            if (emotionConfigGroup) {
                emotionConfigGroup.style.display = 'none';
            }
            // 显示VRM情感配置按钮
            const vrmEmotionConfigGroup = document.getElementById('vrm-emotion-config-group');
            if (vrmEmotionConfigGroup) {
                vrmEmotionConfigGroup.style.display = 'flex';
            }
            // 隐藏常驻表情组（VRM模式下不需要）
            const persistentExpressionGroup = document.getElementById('persistent-expression-group');
            if (persistentExpressionGroup) persistentExpressionGroup.style.display = 'none';
            // 保存设置按钮现在在情感配置组中，不需要单独显示
            // 显示 VRM 动作预览组
            if (vrmAnimationGroup) vrmAnimationGroup.style.display = 'flex';
            // 显示 VRM 打光设置组
            const vrmLightingGroup = document.getElementById('vrm-lighting-group');
            if (vrmLightingGroup) vrmLightingGroup.style.display = 'flex';
            // 显示 VRM 待机动作设置组（独立于打光）
            const vrmIdleAnimGroup = document.getElementById('vrm-idle-animation-group');
            if (vrmIdleAnimGroup) vrmIdleAnimGroup.style.display = 'flex';
            // 切换到 VRM 模式时立即加载待机动作列表
            console.log('[VRM IdleAnimation] 切换到 VRM 模式，开始加载待机动作列表');
            loadIdleAnimationOptions();
            // 更新上传按钮提示文本（VRM模式）
            if (uploadBtn) {
                updateUploadButtonText();
            }
            // VRM动作已改为自动循环播放，不再需要手动加载动作列表
            // 隐藏参数编辑器按钮（VRM 模式下不需要）
            const parameterEditorGroup = document.getElementById('parameter-editor-group');
            if (parameterEditorGroup) parameterEditorGroup.style.display = 'none';

            // 初始化 VRM 管理器
            // 1. 如果 vrmManager 不存在，创建实例
            if (!vrmManager) {
                try {
                    /**
                     * ===== 代码质量改进：修复 VRM 初始化竞争条件 =====
                     * 
                     * 问题：
                     * - 如果 'vrm-modules-ready' 事件在监听器附加之前触发，会导致无限等待
                     * - 缺少超时机制可能导致用户界面卡死
                     * 
                     * 解决方案：
                     * 1. 首先检查模块是否已加载（window.VRMManager 或 window.vrmModuleLoaded）
                     *    如果已加载，立即 resolve，避免等待已发生的事件
                     * 2. 使用 once: true 确保事件监听器只触发一次
                     * 3. 添加 8 秒超时机制，提供更快的反馈和防止无限等待
                     * 
                     * 使用位置：
                     * - switchModelDisplay() 函数中的 VRM 初始化
                     * - vrmModelSelect change 事件监听器中的 VRM 初始化
                     */
                    const waitForVRM = () => new Promise((resolve, reject) => {
                        // 检查是否已经加载，避免等待已发生的事件
                        if (window.VRMManager || window.vrmModuleLoaded) {
                            return resolve();
                        }

                        // 添加事件监听器（使用 once 确保只触发一次）
                        window.addEventListener('vrm-modules-ready', resolve, { once: true });

                        // 添加安全超时（8秒），防止无限等待
                        setTimeout(() => {
                            reject(new Error('VRM Module Load Timeout'));
                        }, 8000);
                    });

                    showStatus(t('live2d.waitingVRMLoader', '正在初始化 VRM 管理器...'));

                    // 等待 VRM 模块加载（带超时和错误处理）
                    try {
                        await waitForVRM();
                    } catch (error) {
                        // 如果是超时错误，显示更友好的提示
                        if (error.message && error.message.includes('Timeout')) {
                            showStatus(t('live2d.vrmModuleTimeout', 'VRM 模块加载超时，请刷新页面重试'), 5000);
                        }
                        throw error;
                    }

                    if (typeof window.VRMManager === 'undefined') {
                        throw new Error('VRM 模块加载超时或失败，请检查网络并刷新。');
                    }

                    // 创建或复用实例
                    vrmManager = window.vrmManager || new window.VRMManager();
                    window.vrmManager = vrmManager;
                } catch (error) {
                    console.error('VRM 管理器创建失败:', error);
                    showStatus(t('live2d.vrmInitFailed', `VRM 管理器创建失败: ${error.message}`));
                    return;
                }
            }

            // 2. 确保容器内有 Canvas（移到 if 块外部，每次切换都会检查）
            try {
                const container = document.getElementById('vrm-container');
                if (container && !container.querySelector('canvas')) {
                    const canvas = document.createElement('canvas');
                    canvas.id = 'vrm-canvas';
                    container.appendChild(canvas);
                }

                // 3. 检查并初始化 Three.js 场景（移到 if 块外部，每次切换都会检查）
                if (!vrmManager.scene || !vrmManager.camera || !vrmManager.renderer) {
                    console.log('[模型管理] VRM 场景未完全初始化，正在初始化...');
                    await vrmManager.initThreeJS('vrm-canvas', 'vrm-container');
                    // 再次验证初始化是否成功
                    if (!vrmManager.scene || !vrmManager.camera || !vrmManager.renderer) {
                        throw new Error('场景初始化后仍缺少必要组件');
                    }
                    console.log('[模型管理] VRM 场景初始化成功');
                    showStatus(t('live2d.vrmInitialized', 'VRM 管理器初始化成功'));
                }
            } catch (error) {
                console.error('VRM 场景初始化失败:', error);
                showStatus(t('live2d.vrmInitFailed', `VRM 场景初始化失败: ${error.message}`));
            }

            // 加载 VRM 模型列表（等待完成，确保后续设置选择器值时列表已就绪）
            // 注意：如果已经在页面初始化时加载过，这里会重新加载以确保列表是最新的
            try {
                await loadVRMModels();
            } catch (error) {
                console.error('加载VRM模型列表失败:', error);
            }
        }
    }

    // 模型类型选择事件
    if (modelTypeSelect) {
        modelTypeSelect.addEventListener('change', async (e) => {
            const type = e.target.value;

            // 关键修复：自定义下拉会手动 dispatch change，即使值未变也会触发。
            // 避免重复执行 switchModelDisplay() 导致 Live2D 画布/PIXI 被重置但模型未重新加载。
            if (type === currentModelType) {
                if (modelTypeManager) {
                    modelTypeManager.updateButtonText();
                }
                return;
            }

            // 检查语音模式状态
            const voiceStatus = await checkVoiceModeStatus();
            if (voiceStatus.isCurrent && voiceStatus.isVoiceMode) {
                showStatus(t('live2d.cannotChangeModelInVoiceMode', '语音模式下无法切换模型类型，请先停止语音对话'), 3000);
                // 恢复之前的选择
                e.target.value = currentModelType;
                return;
            }

            await switchModelDisplay(type);

            // 从 VRM 切回 Live2D 时，确保当前 Live2D 模型会被加载出来
            //（switchModelDisplay 会重建 PIXI，但不会自动触发 model-select 的 change）
            if (type === 'live2d') {
                try {
                    const hasModelLoaded = !!(window.live2dManager && window.live2dManager.currentModel);
                    if (!hasModelLoaded && modelSelect) {
                        // 优先使用当前下拉框选中项；没有则选择第一个可用模型
                        let modelName = modelSelect.value;
                        if (!modelName && modelSelect.options && modelSelect.options.length > 0) {
                            modelName = modelSelect.options[0].value;
                            modelSelect.value = modelName;
                        }

                        if (modelName) {
                            // 触发 change 事件，让 change 事件处理程序统一处理加载逻辑
                            // 这样 currentModelInfo 也会被正确更新
                            modelSelect.dispatchEvent(new Event('change', { bubbles: true }));
                        }
                    }

                    // 【关键修复】延迟双重保险：确保 PIXI ticker 在模型加载后启动
                    // 由于 modelSelect 的 change 事件是异步的，模型可能还没有完全加载
                    // 使用延迟来确保 ticker 一定在运行
                    setTimeout(() => {
                        if (window.live2dManager?.pixi_app?.ticker) {
                            window.live2dManager.pixi_app.ticker.start();
                            console.log('[模型管理] Live2D ticker 延迟启动（从VRM切回的双重保险）');
                        }
                    }, 500);
                } catch (autoLoadError) {
                    console.warn('[模型管理] 切回 Live2D 自动加载模型失败:', autoLoadError);
                }
            }
            // 从 Live2D 切回 VRM 时，加载当前角色配置的 VRM 模型
            else if (type === 'vrm') {
                try {
                    const hasModelLoaded = !!(window.vrmManager && window.vrmManager.currentModel);
                    if (!hasModelLoaded && vrmModelSelect) {
                        // 获取当前角色配置的 VRM 模型路径
                        const lanlanName = await getLanlanName();
                        if (lanlanName) {
                            const charactersData = await RequestHelper.fetchJson('/api/characters');
                            const catgirlConfig = charactersData['猫娘']?.[lanlanName];
                            // 只要角色有配置 vrm 路径就尝试加载，不需要检查 model_type
                            // 这样即使角色当前使用的是 Live2D，切换到 VRM 面板时也能加载其 VRM 模型
                            if (catgirlConfig && catgirlConfig.vrm) {
                                const vrmPath = catgirlConfig.vrm;
                                const vrmFilename = vrmPath.split(/[/\\]/).pop();

                                // 在下拉列表中找到匹配的选项
                                const matchedOption = Array.from(vrmModelSelect.options).find(opt => {
                                    if (!opt.value) return false;
                                    const optFilename = opt.getAttribute('data-filename') || '';
                                    return optFilename === vrmFilename || opt.value.endsWith(vrmFilename);
                                });

                                if (matchedOption) {
                                    vrmModelSelect.value = matchedOption.value;
                                    vrmModelSelect.dispatchEvent(new Event('change', { bubbles: true }));
                                } else if (vrmModelSelect.options.length > 0) {
                                    // 如果没有找到匹配的选项，加载第一个可用的 VRM 模型
                                    vrmModelSelect.value = vrmModelSelect.options[0].value;
                                    if (vrmModelSelect.value) {
                                        vrmModelSelect.dispatchEvent(new Event('change', { bubbles: true }));
                                    }
                                }
                            } else if (vrmModelSelect.options.length > 0) {
                                // 角色没有配置 VRM 模型，加载第一个可用的 VRM 模型
                                vrmModelSelect.value = vrmModelSelect.options[0].value;
                                if (vrmModelSelect.value) {
                                    vrmModelSelect.dispatchEvent(new Event('change', { bubbles: true }));
                                }
                            }
                        }
                    }
                } catch (autoLoadError) {
                    console.warn('[模型管理] 切回 VRM 自动加载模型失败:', autoLoadError);
                }
            }
        });
    }

    // 加载 VRM 模型列表
    async function loadVRMModels() {
        try {
            showStatus(t('live2d.loading', '正在加载模型列表...'));

            // 使用助手代替 fetch
            const data = await RequestHelper.fetchJson('/api/model/vrm/models');

            const models = (data.success && Array.isArray(data.models)) ? data.models : [];
            if (!vrmModelSelect) return;

            if (models.length > 0) {
                // 与 Live2D 一致，不添加默认的"选择模型"选项
                vrmModelSelect.innerHTML = '';
                models.forEach(model => {
                    const option = document.createElement('option');

                    // 严格检查 model.path，如果不存在或为字符串 "undefined"，根据 model.filename 构建路径
                    let modelPath = model.path;
                    let isValidPath = modelPath &&
                        modelPath !== 'undefined' &&
                        modelPath !== 'null' &&
                        typeof modelPath === 'string' &&
                        modelPath.trim() !== '' &&
                        !modelPath.toLowerCase().includes('undefined') &&
                        !modelPath.toLowerCase().includes('null');

                    if (!isValidPath && model.filename) {
                        // 使用 ModelPathHelper 标准化路径
                        const filename = model.filename.trim();
                        if (filename && filename !== 'undefined' && filename !== 'null' && !filename.toLowerCase().includes('undefined')) {
                            modelPath = ModelPathHelper.normalizeModelPath(filename, 'model');
                            isValidPath = true;
                        }
                    }

                    // 如果仍然无效，跳过该模型
                    if (!isValidPath) {
                        console.warn('[模型管理] 跳过无效的 VRM 模型:', model);
                        return;
                    }

                    // 使用 ModelPathHelper 确保 data-path 属性永远是有效的 URL
                    const validPath = modelPath.startsWith('/') || modelPath.startsWith('http')
                        ? ModelPathHelper.normalizeModelPath(modelPath, 'model')
                        : ModelPathHelper.normalizeModelPath(model.filename || modelPath.split(/[/\\]/).pop(), 'model');

                    option.value = model.url || validPath;
                    option.setAttribute('data-path', validPath);
                    if (model.filename) {
                        option.setAttribute('data-filename', model.filename);
                    }
                    option.textContent = model.name || model.filename || validPath;
                    vrmModelSelect.appendChild(option);
                });
                vrmModelSelect.disabled = false;
                if (vrmModelSelectBtn) {
                    vrmModelSelectBtn.disabled = false;
                }
                // 不自动选择模型，让用户手动选择
                updateVRMModelDropdown();
                updateVRMModelSelectButtonText();
                showStatus(t('live2d.vrmModelListLoaded', 'VRM 模型列表加载成功'), 2000);
            } else {
                vrmModelSelect.innerHTML = `<option value="">${t('live2d.noVRMModelsFound', '未找到可用 VRM 模型')}</option>`;
                updateVRMModelDropdown();
                updateVRMModelSelectButtonText();
            }
        } catch (error) {
            console.error('加载 VRM 模型列表失败:', error);
            vrmModelSelect.innerHTML = `<option value="">${t('live2d.loadFailed', '加载失败')}</option>`;
            updateVRMModelDropdown();
            updateVRMModelSelectButtonText();
            showStatus(t('live2d.loadError', `错误: ${error.message}`, { error: error.message }), 5000);
        }
    }

    // 更新VRM模型下拉菜单
    function updateVRMModelDropdown() {
        if (!vrmModelDropdown || !vrmModelSelect) return;
        vrmModelDropdown.innerHTML = '';
        const options = vrmModelSelect.querySelectorAll('option');
        options.forEach((option) => {
            // 跳过空值选项（"选择模型"）
            if (!option.value) return;

            const item = document.createElement('div');
            item.className = 'dropdown-item';
            item.dataset.value = option.value;
            const textSpan = document.createElement('span');
            textSpan.className = 'dropdown-item-text';
            const text = option.textContent || option.value || '';
            textSpan.textContent = text;
            textSpan.setAttribute('data-text', text);
            item.appendChild(textSpan);
            item.addEventListener('click', (e) => {
                e.stopPropagation();
                const value = item.dataset.value;
                vrmModelSelect.value = value;
                vrmModelSelect.dispatchEvent(new Event('change', { bubbles: true }));
                vrmModelDropdown.style.display = 'none';
            });
            vrmModelDropdown.appendChild(item);
        });
    }

    // 更新VRM模型选择器按钮文字
    function updateVRMModelSelectButtonText() {
        if (vrmModelManager) {
            vrmModelManager.updateButtonText();
        }
    }

    // VRM模型选择按钮点击事件已由 DropdownManager 处理

    // VRM 模型选择事件
    if (vrmModelSelect) {
        vrmModelSelect.addEventListener('change', async (e) => {
            updateVRMModelSelectButtonText();
            const modelPath = e.target.value;
            if (!modelPath) return;

            // 检查语音模式状态
            const voiceStatus = await checkVoiceModeStatus();
            if (voiceStatus.isCurrent && voiceStatus.isVoiceMode) {
                showStatus(t('live2d.cannotChangeModelInVoiceMode', '语音模式下无法切换模型，请先停止语音对话'), 3000);
                // 恢复之前的选择
                if (currentModelInfo && currentModelInfo.name) {
                    e.target.value = currentModelInfo.name;
                } else {
                    e.target.value = '';
                }
                return;
            }

            // 确保切换到VRM模式
            if (currentModelType !== 'vrm') {
                await switchModelDisplay('vrm');
            }

            // 确保vrm-container可见
            if (vrmContainer) {
                vrmContainer.classList.remove('hidden');
                vrmContainer.style.display = 'block';
            }

            // 如果vrmManager未初始化，尝试初始化
            if (!vrmManager) {
                try {
                    /**
                     * ===== 代码质量改进：修复 VRM 初始化竞争条件 =====
                     * 
                     * 与 switchModelDisplay() 中的实现保持一致
                     * 详细说明请参考 switchModelDisplay() 中的注释
                     */
                    const waitForVRM = () => new Promise((resolve, reject) => {
                        // 检查是否已经加载，避免等待已发生的事件
                        if (window.VRMManager || window.vrmModuleLoaded) {
                            return resolve();
                        }

                        // 添加事件监听器（使用 once 确保只触发一次）
                        window.addEventListener('vrm-modules-ready', resolve, { once: true });

                        // 添加安全超时（8秒），防止无限等待
                        setTimeout(() => {
                            reject(new Error('VRM Module Load Timeout'));
                        }, 8000);
                    });

                    showStatus(t('live2d.waitingVRMLoader', '正在初始化 VRM 管理器...'));

                    // 等待 VRM 模块加载（带超时和错误处理）
                    try {
                        await waitForVRM();
                    } catch (error) {
                        // 如果是超时错误，显示更友好的提示
                        if (error.message && error.message.includes('Timeout')) {
                            showStatus(t('live2d.vrmModuleTimeout', 'VRM 模块加载超时，请刷新页面重试'), 5000);
                        }
                        throw error;
                    }

                    if (typeof window.VRMManager === 'undefined') {
                        throw new Error('VRM 模块加载超时，请刷新页面重试。');
                    }

                    vrmManager = window.vrmManager || new window.VRMManager();
                    window.vrmManager = vrmManager;

                    const container = document.getElementById('vrm-container');
                    if (container && !container.querySelector('canvas')) {
                        const canvas = document.createElement('canvas');
                        canvas.id = 'vrm-canvas';
                        container.appendChild(canvas);
                    }

                    if (!vrmManager._isInitialized && (!vrmManager.scene || !vrmManager.camera || !vrmManager.renderer)) {
                        await vrmManager.initThreeJS('vrm-canvas', 'vrm-container');
                    }

                    showStatus(t('live2d.vrmInitialized', 'VRM 管理器初始化成功'));
                } catch (error) {
                    console.error('VRM 管理器初始化失败:', error);
                    showStatus(t('live2d.vrmInitFailed', `VRM 管理器初始化失败: ${error.message}`));
                    return;
                }
            }

            // 确保场景已完全初始化（即使 vrmManager 已存在，场景也可能未初始化）
            if (vrmManager && (!vrmManager.scene || !vrmManager.camera || !vrmManager.renderer)) {
                console.log('[模型管理] VRM 场景未完全初始化，正在初始化...');
                try {
                    await vrmManager.initThreeJS('vrm-canvas', 'vrm-container');
                    // 再次验证初始化是否成功
                    if (!vrmManager.scene || !vrmManager.camera || !vrmManager.renderer) {
                        throw new Error('场景初始化后仍缺少必要组件');
                    }
                    console.log('[模型管理] VRM 场景初始化成功');
                } catch (initError) {
                    console.error('[模型管理] 场景初始化失败:', initError);
                    showStatus(t('live2d.vrmInitFailed', `场景初始化失败: ${initError.message}`), 5000);
                    return;
                }
            }

            // 获取选中的option，获取原始路径和文件名
            const selectedOption = vrmModelSelect.options[vrmModelSelect.selectedIndex];
            let originalPath = selectedOption ? selectedOption.getAttribute('data-path') : null;
            const filename = selectedOption ? selectedOption.getAttribute('data-filename') : null;

            // 增加逻辑判断：如果获取到的路径是 null、空或者字符串 "undefined"，立即使用 data-filename 重新构造正确路径
            // 使用 ModelPathHelper 标准化路径（DRY 原则）
            if (!originalPath ||
                originalPath === 'undefined' ||
                originalPath === 'null' ||
                originalPath.trim() === '' ||
                originalPath.toLowerCase().includes('undefined') ||
                originalPath.toLowerCase().includes('null')) {
                if (filename && filename !== 'undefined' && filename !== 'null' && !filename.toLowerCase().includes('undefined')) {
                    originalPath = ModelPathHelper.normalizeModelPath(filename, 'model');
                    console.warn('[模型管理] 检测到无效路径，已根据文件名自动修复:', originalPath);
                } else {
                    console.error('[模型管理] 无法修复无效路径，缺少有效的文件名');
                    showStatus(t('live2d.vrmModelPathInvalid', 'VRM 模型路径无效，请重新选择模型'), 3000);
                    e.target.value = '';
                    return;
                }
            }

            // modelPath 现在是 URL（如 /user_vrm/sister1.0.vrm），用于加载模型
            // originalPath 是本地文件路径，用于保存配置
            let modelUrl = modelPath; // 用于加载的URL
            let modelPathForConfig = originalPath; // 用于配置的路径

            // 确保 modelUrl 也是有效的（使用 ModelPathHelper 标准化路径）
            if (!modelUrl ||
                modelUrl === 'undefined' ||
                modelUrl === 'null' ||
                modelUrl.trim() === '' ||
                modelUrl.toLowerCase().includes('undefined') ||
                modelUrl.toLowerCase().includes('null')) {
                if (filename) {
                    modelUrl = ModelPathHelper.normalizeModelPath(filename, 'model');
                } else {
                    modelUrl = ModelPathHelper.normalizeModelPath(originalPath, 'model');
                }
            } else {
                // 即使路径看起来有效，也标准化它（处理 Windows 反斜杠等）
                modelUrl = ModelPathHelper.normalizeModelPath(modelUrl, 'model');
            }

            // 确保赋值给 currentModelInfo 的 path 是绝对有效的
            if (!modelPathForConfig ||
                modelPathForConfig === 'undefined' ||
                modelPathForConfig === 'null' ||
                modelPathForConfig.trim() === '' ||
                modelPathForConfig.toLowerCase().includes('undefined') ||
                modelPathForConfig.toLowerCase().includes('null')) {
                if (filename) {
                    // 使用 ModelPathHelper 标准化路径
                    modelPathForConfig = ModelPathHelper.normalizeModelPath(filename, 'model');
                } else {
                    console.error('[模型管理] 无法确定有效的模型路径');
                    showStatus(t('live2d.vrmModelPathInvalid', 'VRM 模型路径无效，请重新选择模型'), 3000);
                    e.target.value = '';
                    return;
                }
            }

            // 保存当前 VRM 模型信息，用于后续保存到角色配置（在加载前就设置，这样即使加载失败也能保存）
            currentModelInfo = {
                name: filename || modelPathForConfig.split(/[/\\]/).pop() || modelPathForConfig,
                path: modelPathForConfig,
                url: modelUrl,
                type: 'vrm'
            };

            // 选择模型后立即启用保存按钮（即使模型还未加载或加载失败）
            if (savePositionBtn) {
                savePositionBtn.disabled = false;
            }

            // 标记为有未保存更改
            window.hasUnsavedChanges = true;
            console.log('已标记为未保存更改（VRM模型切换），请点击 保存设置 持久化到角色配置。');

            try {
                showStatus(t('live2d.loadingVRMModel', `正在加载 VRM 模型...`));

                // 确保容器可见
                if (vrmContainer) {
                    vrmContainer.classList.remove('hidden');
                    vrmContainer.style.display = 'block';
                }
                // 在加载新模型前，显式停止之前的动作并清理
                if (vrmManager.vrmaAction) {
                    vrmManager.stopVRMAAnimation();
                    isVrmAnimationPlaying = false;
                    updateVRMAnimationPlayButtonIcon();
                }

                // 使用 URL 加载模型，而不是本地文件路径（浏览器不允许加载 file:// 路径）
                // 传入 { autoPlay: false } 以便在此处统一播放待机动画，避免先露出 T-pose
                //增加 addShadow: false
                // 【注意】朝向会自动从preferences中加载（在vrm-core.js的loadModel中处理）
                await vrmManager.loadModel(modelUrl, { autoPlay: false, addShadow: false });
                // 加载后立即播默认待机动画，避免 T-pose 显得生硬
                const defaultIdleUrl = '/static/vrm/animation/wait03.vrma';
                const idleSel = document.getElementById('idle-animation-select');
                const idleUrl = (idleSel && idleSel.value) ? idleSel.value : defaultIdleUrl;
                if (idleUrl && vrmManager.animation) {
                    try {
                        await vrmManager.playVRMAAnimation(idleUrl, { loop: true, immediate: true, isIdle: true });
                    } catch (e) {
                        console.warn('[VRM] 播放默认待机动画失败，使用内置默认:', e);
                        if (idleUrl !== defaultIdleUrl) {
                            try {
                                await vrmManager.playVRMAAnimation(defaultIdleUrl, { loop: true, immediate: true, isIdle: true });
                            } catch (e2) {
                                console.warn('[VRM] 播放 wait03 待机动画失败:', e2);
                            }
                        }
                    }
                }
                // 加载新模型后，重置播放状态
                isVrmAnimationPlaying = false;
                updateVRMAnimationPlayButtonIcon();
                isVrmExpressionPlaying = false;
                updateVRMExpressionPlayButtonIcon();

                // 检查是否从preferences加载了朝向
                if (vrmManager.currentModel) {
                    const vrm = vrmManager.currentModel.vrm || vrmManager.currentModel;
                    if (vrm && vrm.scene) {
                        // 如果朝向不是0度，说明从preferences加载了保存的朝向
                        if (Math.abs(vrm.scene.rotation.y) > 0.01) {
                            // 禁用自动面向相机，保持手动设置的朝向
                            if (vrmManager.interaction) {
                                vrmManager.interaction.enableFaceCamera = false;
                            }
                        }
                        // 模型缩放计算已统一在 vrm-core.js 的 loadModel() 中处理
                    }
                }


                // 在这里加载表情
                loadVRMExpressions();

                // 加载模型特定的情感映射配置
                if (vrmManager && vrmManager.expression && modelPath) {
                    // 从模型路径提取模型名称
                    const modelName = modelPath.split('/').pop().replace(/\.vrm$/i, '');
                    vrmManager.expression.loadMoodMap(modelName);
                }

                // 加载新模型时重置动作列表状态，允许重新加载动作
                animationsLoaded = false;
                // 主动加载动作列表，解开下拉菜单的锁定状态
                await loadVRMAnimations();

                // 自动加载角色的打光配置
                await loadCharacterLighting();

                showStatus(t('live2d.vrmModelLoaded', `VRM 模型 ${modelPath} 加载成功`, { model: modelPath }));
            } catch (error) {
                console.error('加载 VRM 模型失败:', error);
                showStatus(t('live2d.vrmModelLoadFailed', `加载 VRM 模型失败: ${error.message}。您仍可以保存模型设置。`));
                // 即使模型加载失败，也尝试加载动作列表（可能用户想预览其他动作）
                try {
                    await loadVRMAnimations(false);
                } catch (animError) {
                    console.warn('加载动作列表失败:', animError);
                }
            }
        });
    }


    // 加载 VRM 动作列表
    async function loadVRMAnimations(autoPlaySaved = false) {
        try {
            showStatus(t('live2d.vrmAnimation.loading', '正在加载动作列表...'));
            const data = await RequestHelper.fetchJson('/api/model/vrm/animations');
            vrmAnimations = (data.success && data.animations) ? data.animations : [];

            if (vrmAnimationSelect && vrmAnimations.length > 0) {
                vrmAnimationSelect.innerHTML = `<option value="">${t('live2d.selectMotion', '选择动作')}</option>`;
                vrmAnimations.forEach(anim => {
                    // 确保 animPath 是字符串：优先使用 anim.path，否则使用 anim.url，最后使用 anim 本身（如果是字符串）
                    const animPath = (typeof anim.path === 'string' ? anim.path : null)
                        || (typeof anim.url === 'string' ? anim.url : null)
                        || (typeof anim === 'string' ? anim : null);
                    if (!animPath) {
                        console.warn('[VRM] 跳过无效动画项:', anim);
                        return;
                    }

                    const option = document.createElement('option');
                    const finalUrl = ModelPathHelper.vrmToUrl(animPath, 'animation');

                    option.value = finalUrl;
                    option.setAttribute('data-path', animPath);
                    option.setAttribute('data-filename', anim.name || anim.filename || finalUrl.split('/').pop());
                    option.textContent = option.getAttribute('data-filename');
                    vrmAnimationSelect.appendChild(option);
                });
                vrmAnimationSelect.disabled = false;
                if (vrmAnimationSelectBtn) {
                    vrmAnimationSelectBtn.disabled = false;
                }
                updateVRMAnimationDropdown();
                updateVRMAnimationSelectButtonText();
                showStatus(t('live2d.vrmAnimation.animationListLoaded', '动作列表加载成功'), 2000);
            } else {
                vrmAnimationSelect.innerHTML = `<option value="">${t('live2d.vrmAnimation.noAnimations', '未找到动作文件')}</option>`;
                updateVRMAnimationDropdown();
                updateVRMAnimationSelectButtonText();
            }
        } catch (error) {
            console.error('加载 VRM 动作列表失败:', error);
            if (vrmAnimationSelect) {
                vrmAnimationSelect.innerHTML = `<option value="">${t('live2d.loadFailed', '加载失败')}</option>`;
            }
            updateVRMAnimationDropdown();
            updateVRMAnimationSelectButtonText();
            showStatus(t('live2d.loadError', `错误: ${error.message}`, { error: error.message }), 5000);
        }
    }

    // 更新VRM动作下拉菜单
    function updateVRMAnimationDropdown() {
        if (!vrmAnimationDropdown || !vrmAnimationSelect) return;
        vrmAnimationDropdown.innerHTML = '';
        const options = vrmAnimationSelect.querySelectorAll('option');
        options.forEach((option) => {
            const item = document.createElement('div');
            item.className = 'dropdown-item';
            item.dataset.value = option.value;
            const textSpan = document.createElement('span');
            textSpan.className = 'dropdown-item-text';
            const text = option.textContent || option.value || '';
            textSpan.textContent = text;
            textSpan.setAttribute('data-text', text);
            item.appendChild(textSpan);
            item.addEventListener('click', (e) => {
                e.stopPropagation();
                const value = item.dataset.value;
                vrmAnimationSelect.value = value;
                vrmAnimationSelect.dispatchEvent(new Event('change', { bubbles: true }));
                vrmAnimationDropdown.style.display = 'none';
            });
            vrmAnimationDropdown.appendChild(item);
        });
    }

    // 更新VRM动作选择器按钮文字
    function updateVRMAnimationSelectButtonText() {
        if (vrmAnimationManager) {
            vrmAnimationManager.updateButtonText();
        }
    }

    // VRM动作选择按钮点击事件已由 DropdownManager 处理

    // VRM 动作选择事件 - 首次点击时加载动作列表（保留原有逻辑作为备用）
    if (vrmAnimationSelect) {
        vrmAnimationSelect.addEventListener('focus', async () => {
            // 首次获得焦点时加载动作列表
            if (!animationsLoaded && currentModelType === 'vrm') {
                animationsLoaded = true; // 防止重复加载
                try {
                    await loadVRMAnimations(false);
                } catch (error) {
                    console.error('加载VRM动作列表失败:', error);
                    animationsLoaded = false; // 加载失败时重置标记，允许重试
                }
            }
        });

        vrmAnimationSelect.addEventListener('change', async (e) => {
            const selectedValue = e.target.value;

            // 如果选择的是第一个选项（空值，即"增加动作"），触发文件选择器
            if (selectedValue === '') {
                const vrmAnimationFileUpload = document.getElementById('vrm-animation-file-upload');
                if (vrmAnimationFileUpload) {
                    vrmAnimationFileUpload.click();
                }
                // 重置选择器到第一个选项（保持显示"选择动作"）
                e.target.value = '';
                updateVRMAnimationSelectButtonText(); // 更新按钮文字为"选择动作"
                return;
            }

            updateVRMAnimationSelectButtonText();
            const animationPath = e.target.value;
            if (animationPath && playVrmAnimationBtn) {
                playVrmAnimationBtn.disabled = false;
                // 切换动作时，如果正在播放，先停止
                if (isVrmAnimationPlaying && vrmManager) {
                    vrmManager.stopVRMAAnimation();
                    isVrmAnimationPlaying = false;
                    updateVRMAnimationPlayButtonIcon();
                }
            } else {
                if (playVrmAnimationBtn) playVrmAnimationBtn.disabled = true;
                // 如果没有选择动作，停止播放
                if (isVrmAnimationPlaying && vrmManager) {
                    vrmManager.stopVRMAAnimation();
                    isVrmAnimationPlaying = false;
                    updateVRMAnimationPlayButtonIcon();
                }
            }
        });
    }

    // 更新VRM动作播放按钮图标
    function updateVRMAnimationPlayButtonIcon() {
        if (!playVrmAnimationBtn) return;
        const icon = playVrmAnimationBtn.querySelector('.vrm-animation-play-icon');
        if (icon) {
            if (isVrmAnimationPlaying) {
                // 显示暂停图标
                icon.src = '/static/icons/vrm_pause_icon.png?v=1';
                icon.alt = '暂停';
            } else {
                // 显示播放图标
                icon.src = '/static/icons/motion_play_icon.png?v=1';
                icon.alt = '播放';
            }
        }
    }

    // 播放/暂停 VRM 动作（切换功能）
    if (playVrmAnimationBtn) {
        playVrmAnimationBtn.addEventListener('click', async () => {
            if (!vrmManager || !vrmAnimationSelect || !vrmAnimationSelect.value) {
                showStatus(t('live2d.vrmAnimation.selectAnimationFirst', '请先选择动作'), 2000);
                return;
            }

            if (isVrmAnimationPlaying) {
                // 当前正在播放，点击后停止
                if (vrmManager) {
                    vrmManager.stopVRMAAnimation();
                    isVrmAnimationPlaying = false;
                    updateVRMAnimationPlayButtonIcon();
                    showStatus(t('live2d.vrmAnimation.animationStopped', '动作已停止'), 2000);
                }
            } else {
                // 当前未播放，点击后播放
                const selectedOption = vrmAnimationSelect.options[vrmAnimationSelect.selectedIndex];
                const originalPath = selectedOption ? selectedOption.getAttribute('data-path') : vrmAnimationSelect.value;
                // 获取动作名称用于显示
                const animDisplayName = selectedOption ? selectedOption.getAttribute('data-filename') : '未知动作';

                const finalAnimationUrl = ModelPathHelper.vrmToUrl(originalPath, 'animation');
                // 默认循环播放，速度为1.0
                const loop = true;
                const speed = 1.0;

                try {
                    showStatus(t('live2d.vrmAnimation.playingAnimation', `正在播放: ${animDisplayName}`, { name: animDisplayName }), 2000);
                    await vrmManager.playVRMAAnimation(finalAnimationUrl, {
                        loop: loop,
                        timeScale: speed,
                        isIdle: false  // 手动播放的动作不是待机动画
                    });
                    isVrmAnimationPlaying = true;
                    updateVRMAnimationPlayButtonIcon();
                } catch (error) {
                    console.error('播放 VRM 动作失败:', error);
                    showStatus(t('live2d.vrmAnimation.animationPlayFailed', `播放动作失败: ${error.message}`));
                    isVrmAnimationPlaying = false;
                    updateVRMAnimationPlayButtonIcon();
                }
            }
        });
    }
    // 加载 VRM 表情列表
    function loadVRMExpressions() {
        if (!vrmExpressionSelect || !vrmManager || !vrmManager.expression) return;

        const expressions = vrmManager.expression.getExpressionList();

        vrmExpressionSelect.innerHTML = `<option value="">${t('live2d.selectExpression', '选择表情')}</option>`;

        if (expressions.length > 0) {
            expressions.forEach(name => {
                const opt = document.createElement('option');
                opt.value = name;
                opt.textContent = name;
                vrmExpressionSelect.appendChild(opt);
            });
            vrmExpressionSelect.disabled = false;
            if (vrmExpressionSelectBtn) {
                vrmExpressionSelectBtn.disabled = false;
            }
            // 播放按钮保持禁用，直到用户选择一个表情
            if (triggerVrmExpressionBtn) triggerVrmExpressionBtn.disabled = true;
            updateVRMExpressionDropdown();
            updateVRMExpressionSelectButtonText();
        } else {
            vrmExpressionSelect.innerHTML = `<option value="">${t('live2d.vrmExpression.noExpressions', '无可用表情')}</option>`;
            vrmExpressionSelect.disabled = true;
            if (vrmExpressionSelectBtn) {
                vrmExpressionSelectBtn.disabled = true;
            }
            updateVRMExpressionDropdown();
            updateVRMExpressionSelectButtonText();
        }
    }

    // 更新VRM表情下拉菜单
    function updateVRMExpressionDropdown() {
        if (!vrmExpressionDropdown || !vrmExpressionSelect) return;
        vrmExpressionDropdown.innerHTML = '';
        const options = vrmExpressionSelect.querySelectorAll('option');
        options.forEach((option) => {
            const item = document.createElement('div');
            item.className = 'dropdown-item';
            item.dataset.value = option.value;
            const textSpan = document.createElement('span');
            textSpan.className = 'dropdown-item-text';
            const text = option.textContent || option.value || '';
            textSpan.textContent = text;
            textSpan.setAttribute('data-text', text);
            item.appendChild(textSpan);
            item.addEventListener('click', (e) => {
                e.stopPropagation();
                const value = item.dataset.value;
                vrmExpressionSelect.value = value;
                vrmExpressionSelect.dispatchEvent(new Event('change', { bubbles: true }));
                vrmExpressionDropdown.style.display = 'none';
            });
            vrmExpressionDropdown.appendChild(item);
        });
    }

    // 更新VRM表情选择器按钮文字
    function updateVRMExpressionSelectButtonText() {
        if (vrmExpressionManager) {
            vrmExpressionManager.updateButtonText();
        }
    }
    // VRM表情选择按钮点击事件已由 DropdownManager 处理

    // VRM表情选择事件
    if (vrmExpressionSelect) {
        vrmExpressionSelect.addEventListener('change', async (e) => {
            const selectedValue = e.target.value;

            // 如果选择的是第一个选项（空值，即"选择表情"），显示提示（VRM表情通常是内置的）
            if (selectedValue === '') {
                showStatus(t('live2d.vrmExpression.builtInOnly', 'VRM表情通常是模型内置的，无法单独上传'), 3000);
                // 重置选择器到第一个选项（保持显示"选择表情"）
                e.target.value = '';
                updateVRMExpressionSelectButtonText(); // 更新按钮文字为"选择表情"
                // 禁用播放按钮
                if (triggerVrmExpressionBtn) {
                    triggerVrmExpressionBtn.disabled = true;
                }
                return;
            }

            updateVRMExpressionSelectButtonText();
            const expressionName = e.target.value;
            if (expressionName && triggerVrmExpressionBtn) {
                triggerVrmExpressionBtn.disabled = false;
                // 切换表情时，如果正在播放，先停止
                if (isVrmExpressionPlaying && vrmManager && vrmManager.expression) {
                    vrmManager.expression.resetBaseExpression();
                    isVrmExpressionPlaying = false;
                    updateVRMExpressionPlayButtonIcon();
                }
            } else {
                if (triggerVrmExpressionBtn) triggerVrmExpressionBtn.disabled = true;
                // 如果没有选择表情，停止播放
                if (isVrmExpressionPlaying && vrmManager && vrmManager.expression) {
                    vrmManager.expression.resetBaseExpression();
                    isVrmExpressionPlaying = false;
                    updateVRMExpressionPlayButtonIcon();
                }
            }
        });
    }

    // 更新VRM表情播放按钮图标
    function updateVRMExpressionPlayButtonIcon() {
        if (!triggerVrmExpressionBtn) return;
        const icon = triggerVrmExpressionBtn.querySelector('.vrm-expression-play-icon');
        if (icon) {
            if (isVrmExpressionPlaying) {
                // 显示暂停图标
                icon.src = '/static/icons/vrm_pause_icon.png?v=1';
                icon.alt = '暂停';
            } else {
                // 显示播放图标
                icon.src = '/static/icons/motion_play_icon.png?v=1';
                icon.alt = '播放';
            }
        }
    }

    // VRM表情播放/暂停按钮点击事件
    if (triggerVrmExpressionBtn) {
        triggerVrmExpressionBtn.addEventListener('click', () => {
            const name = vrmExpressionSelect.value;
            if (!name) {
                showStatus(t('live2d.vrmExpression.selectFirst', '请先选择一个表情'));
                return;
            }

            if (isVrmExpressionPlaying) {
                // 当前正在播放，点击后停止
                if (vrmManager && vrmManager.expression) {
                    vrmManager.expression.resetBaseExpression();
                    isVrmExpressionPlaying = false;
                    updateVRMExpressionPlayButtonIcon();
                    showStatus(t('live2d.vrmExpression.stopped', '表情已停止'), 2000);
                }
            } else {
                // 当前未播放，点击后播放
                if (vrmManager && vrmManager.expression) {
                    // 【修改】手动播放时禁用自动回到 neutral，保持表情直到手动停止
                    vrmManager.expression.autoReturnToNeutral = false;
                    vrmManager.expression.setBaseExpression(name);
                    isVrmExpressionPlaying = true;
                    updateVRMExpressionPlayButtonIcon();
                    showStatus(t('live2d.vrmExpression.playing', `正在播放表情: ${name}`, { name: name }), 2000);
                }
            }
        });
    }

    // 点击外部关闭下拉菜单已由 DropdownManager 处理

    // VRM 打光控制 (已简化)
    const ambientLightSlider = document.getElementById('ambient-light-slider');
    const mainLightSlider = document.getElementById('main-light-slider');
    const exposureSlider = document.getElementById('exposure-slider');
    const tonemappingSelect = document.getElementById('tonemapping-select');
    const idleAnimationSelect = document.getElementById('idle-animation-select');
    const ambientLightValue = document.getElementById('ambient-light-value');
    const mainLightValue = document.getElementById('main-light-value');
    const exposureValue = document.getElementById('exposure-value');

    // 隐藏的辅助光控件 (保留引用以防报错，但不添加逻辑或保持静默)
    const fillLightSlider = document.getElementById('fill-light-slider');
    const rimLightSlider = document.getElementById('rim-light-slider');
    const topLightSlider = document.getElementById('top-light-slider');
    const bottomLightSlider = document.getElementById('bottom-light-slider');
    const fillLightValue = document.getElementById('fill-light-value');
    const rimLightValue = document.getElementById('rim-light-value');
    const topLightValue = document.getElementById('top-light-value');
    const bottomLightValue = document.getElementById('bottom-light-value');
    // 环境光滑块
    if (ambientLightSlider && ambientLightValue) {
        ambientLightSlider.addEventListener('input', (e) => {
            const value = parseFloat(e.target.value);
            ambientLightValue.textContent = value.toFixed(2);
            if (vrmManager && vrmManager.ambientLight) {
                vrmManager.ambientLight.intensity = value;
            }
        });
    }

    // 主光源滑块
    if (mainLightSlider && mainLightValue) {
        mainLightSlider.addEventListener('input', (e) => {
            const value = parseFloat(e.target.value);
            mainLightValue.textContent = value.toFixed(2);
            if (vrmManager && vrmManager.mainLight) {
                vrmManager.mainLight.intensity = value;
            }
        });
    }

    // 补光滑块
    if (fillLightSlider && fillLightValue) {
        fillLightSlider.addEventListener('input', (e) => {
            const value = parseFloat(e.target.value);
            fillLightValue.textContent = value.toFixed(2);
            if (vrmManager && vrmManager.fillLight) {
                vrmManager.fillLight.intensity = value;
            }
        });
    }

    // 轮廓光滑块
    if (rimLightSlider && rimLightValue) {
        rimLightSlider.addEventListener('input', (e) => {
            const value = parseFloat(e.target.value);
            rimLightValue.textContent = value.toFixed(2);
            if (vrmManager && vrmManager.rimLight) {
                vrmManager.rimLight.intensity = value;
            }
        });
    }

    // 顶光滑块
    if (topLightSlider && topLightValue) {
        topLightSlider.addEventListener('input', (e) => {
            const value = parseFloat(e.target.value);
            topLightValue.textContent = value.toFixed(2);
            if (vrmManager && vrmManager.topLight) {
                vrmManager.topLight.intensity = value;
            }
        });
    }

    // 底光滑块
    if (bottomLightSlider && bottomLightValue) {
        bottomLightSlider.addEventListener('input', (e) => {
            const value = parseFloat(e.target.value);
            bottomLightValue.textContent = value.toFixed(2);
            if (vrmManager && vrmManager.bottomLight) {
                vrmManager.bottomLight.intensity = value;
            }
        });
    }

    // 曝光滑块
    if (exposureSlider && exposureValue) {
        exposureSlider.addEventListener('input', (e) => {
            const value = parseFloat(e.target.value);
            exposureValue.textContent = value.toFixed(2);
            if (vrmManager && vrmManager.renderer) {
                vrmManager.renderer.toneMappingExposure = value;
            }
        });
    }

    // 色调映射选择器
    if (tonemappingSelect) {
        tonemappingSelect.addEventListener('change', (e) => {
            const value = parseInt(e.target.value);
            if (vrmManager && vrmManager.renderer) {
                vrmManager.renderer.toneMapping = value;
                // 需要更新材质才能生效
                if (vrmManager.currentModel?.vrm?.scene) {
                    vrmManager.currentModel.vrm.scene.traverse((obj) => {
                        if (obj.material) {
                            obj.material.needsUpdate = true;
                        }
                    });
                }
            }
        });
    }

    // 待机动作选择器
    if (idleAnimationSelect) {
        idleAnimationSelect.addEventListener('change', async (e) => {
            const selectedUrl = e.target.value;
            if (!selectedUrl) return;
            // 实时切换待机动作：停止当前动画，播放新的循环动画
            if (vrmManager && vrmManager.animation && vrmManager.currentModel) {
                try {
                    if (vrmManager.vrmaAction) {
                        vrmManager.stopVRMAAnimation();
                    }
                    // 切换待机动作会 stop 之前的动画，同步重置手动动作的播放状态和图标
                    isVrmAnimationPlaying = false;
                    updateVRMAnimationPlayButtonIcon();
                    await vrmManager.playVRMAAnimation(selectedUrl, { loop: true, immediate: true, isIdle: true });
                    console.log('[VRM IdleAnimation] 待机动作已切换:', e.target.options[e.target.selectedIndex]?.text || selectedUrl);
                    showStatus(t('vrm.idleAnimation.changed', `待机动作已切换`, { name: e.target.options[e.target.selectedIndex]?.text || selectedUrl }), 2000);
                } catch (err) {
                    console.warn('[VRM IdleAnimation] 切换待机动作失败:', err);
                    showStatus(t('vrm.idleAnimation.changeFailed', '切换待机动作失败'), 2000);
                }
            }
        });
    }

    /**
     * 加载待机动作选项列表
     * 从 /api/model/vrm/animations 获取可用的VRMA动画文件，填充待机动作下拉菜单
     * 默认选中 wait03.vrma，用户可在保存设置时持久化选择
     *
     * 使用 inflight Promise 去重：并发调用共享同一请求，避免晚返回的
     * 响应覆盖已恢复的 idleAnimation 选中值
     */
    async function loadIdleAnimationOptions() {
        // 用函数自身属性存储 inflight Promise，避免 let 声明的 TDZ 问题
        if (loadIdleAnimationOptions._promise) return loadIdleAnimationOptions._promise;
        loadIdleAnimationOptions._promise = _doLoadIdleAnimationOptions().finally(() => {
            loadIdleAnimationOptions._promise = null;
        });
        return loadIdleAnimationOptions._promise;
    }
    async function _doLoadIdleAnimationOptions() {
        const selectEl = document.getElementById('idle-animation-select');
        if (!selectEl) {
            console.debug('[VRM IdleAnimation] 待机动作下拉元素未找到，跳过加载');
            return;
        }
        try {
            console.log('[VRM IdleAnimation] 正在从 API 加载待机动作列表...');
            const data = await RequestHelper.fetchJson('/api/model/vrm/animations');
            const animations = (data.success && data.animations) ? data.animations : [];
            const defaultUrl = '/static/vrm/animation/wait03.vrma';

            selectEl.innerHTML = '';
            if (animations.length > 0) {
                animations.forEach(anim => {
                    const animPath = (typeof anim.path === 'string' ? anim.path : null)
                        || (typeof anim.url === 'string' ? anim.url : null)
                        || (typeof anim === 'string' ? anim : null);
                    if (!animPath) {
                        console.warn('[VRM IdleAnimation] 跳过无效动画项:', anim);
                        return;
                    }

                    const option = document.createElement('option');
                    const finalUrl = ModelPathHelper.vrmToUrl(animPath, 'animation');
                    const displayName = anim.name || anim.filename || finalUrl.split('/').pop();
                    option.value = finalUrl;
                    option.textContent = displayName;
                    option.style.color = '#4f8cff';
                    // 默认选中 wait03.vrma
                    if (finalUrl === defaultUrl || animPath.includes('wait03.vrma')) {
                        option.selected = true;
                    }
                    selectEl.appendChild(option);
                });
                console.log(`[VRM IdleAnimation] 待机动作列表加载成功，共 ${animations.length} 个动画`);
            } else {
                const emptyOption = document.createElement('option');
                emptyOption.value = '';
                emptyOption.textContent = t('live2d.vrmAnimation.noAnimations', '未找到动作文件');
                emptyOption.style.color = '#4f8cff';
                selectEl.appendChild(emptyOption);
                console.warn('[VRM IdleAnimation] API 返回的动画列表为空');
            }
        } catch (error) {
            console.error('[VRM IdleAnimation] 加载待机动作列表失败:', error);
            selectEl.innerHTML = '';
            const errorOption = document.createElement('option');
            errorOption.value = '';
            errorOption.textContent = t('live2d.loadFailed', '加载失败');
            errorOption.style.color = '#4f8cff';
            selectEl.appendChild(errorOption);
        }
    }





    // 应用打光值到UI和场景
    function applyLightingValues(lighting) {
        // 确保光照已经初始化，如果没有则等待一小段时间
        if (!vrmManager?.ambientLight || !vrmManager?.mainLight || !vrmManager?.fillLight || !vrmManager?.rimLight) {
            // 如果光照未初始化，延迟重试
            setTimeout(() => {
                applyLightingValues(lighting);
            }, 100);
            return;
        }

        if (ambientLightSlider && ambientLightValue) {
            ambientLightSlider.value = lighting.ambient;
            ambientLightValue.textContent = lighting.ambient.toFixed(2);
            if (vrmManager.ambientLight) {
                vrmManager.ambientLight.intensity = lighting.ambient;
            }
        }
        if (mainLightSlider && mainLightValue) {
            mainLightSlider.value = lighting.main;
            mainLightValue.textContent = lighting.main.toFixed(2);
            if (vrmManager.mainLight) {
                vrmManager.mainLight.intensity = lighting.main;
            }
        }
        if (fillLightSlider && fillLightValue) {
            // 简化模式下，补光强制归零
            const fillValue = 0.0;
            fillLightSlider.value = fillValue;
            fillLightValue.textContent = fillValue.toFixed(2);
            if (vrmManager.fillLight) {
                vrmManager.fillLight.intensity = fillValue;
            }
        }
        if (rimLightSlider && rimLightValue) {
            // 简化模式下，轮廓光强制归零
            const rimValue = 0.0;
            rimLightSlider.value = rimValue;
            rimLightValue.textContent = rimValue.toFixed(2);
            if (vrmManager.rimLight) {
                vrmManager.rimLight.intensity = rimValue;
            }
        }
        if (topLightSlider && topLightValue) {
            // 简化模式下，顶光强制归零
            const topValue = 0.0;
            topLightSlider.value = topValue;
            topLightValue.textContent = topValue.toFixed(2);
            if (vrmManager.topLight) {
                vrmManager.topLight.intensity = topValue;
            }
        }
        if (bottomLightSlider && bottomLightValue) {
            // 简化模式下，底光强制归零
            const bottomValue = 0.0;
            bottomLightSlider.value = bottomValue;
            bottomLightValue.textContent = bottomValue.toFixed(2);
            if (vrmManager.bottomLight) {
                vrmManager.bottomLight.intensity = bottomValue;
            }
        }
        if (exposureSlider && exposureValue && lighting.exposure !== undefined) {
            exposureSlider.value = lighting.exposure;
            exposureValue.textContent = lighting.exposure.toFixed(2);
            if (vrmManager.renderer) {
                vrmManager.renderer.toneMappingExposure = lighting.exposure;
            }
        }
        if (tonemappingSelect && lighting.toneMapping !== undefined) {
            tonemappingSelect.value = lighting.toneMapping.toString();
            if (vrmManager.renderer) {
                vrmManager.renderer.toneMapping = lighting.toneMapping;
            }
        }

        // 强制渲染一次，确保光照立即生效
        if (vrmManager?.renderer && vrmManager?.scene && vrmManager?.camera) {
            vrmManager.renderer.render(vrmManager.scene, vrmManager.camera);
        }
    }

    // 加载角色的打光配置并应用
    // 【保留但简化】只加载角色的“直接打光配置”，去掉了预设逻辑
    async function loadCharacterLighting() {
        try {
            const lanlanName = await getLanlanName();
            if (!lanlanName) return;

            // 使用 RequestHelper 确保统一的错误处理和超时
            const data = await RequestHelper.fetchJson('/api/characters/');
            const charData = data['猫娘']?.[lanlanName];
            const lighting = charData?.lighting;

            // 只处理直接保存的 lighting 对象
            if (lighting) {
                applyLightingValues(lighting);
            } else {
            }

            // 加载待机动作选项并恢复保存的选择
            await loadIdleAnimationOptions();
            const idleAnimSel = idleAnimationSelect || document.getElementById('idle-animation-select');
            if (charData?.idleAnimation && idleAnimSel) {
                idleAnimSel.value = charData.idleAnimation;
                if (idleAnimSel.value === charData.idleAnimation) {
                    // 值设置成功，触发 change 事件以播放保存的待机动作
                    idleAnimSel.dispatchEvent(new Event('change', { bubbles: true }));
                } else {
                    console.warn('[VRM] 保存的待机动作不在列表中:', charData.idleAnimation);
                }
            }
        } catch (error) {
            console.error('加载打光配置失败:', error);
        }
    }

    // 注意：VRM 模型列表已在 switchModelDisplay('vrm') 中加载，无需重复加载
    // 如果需要确保列表已加载（比如默认是 live2d 模式），可以在这里检查
    // 但不要重复加载，因为这会覆盖 loadCurrentCharacterModel() 设置的选择器值

    // 检查语音模式状态的辅助函数
    async function checkVoiceModeStatus() {
        try {
            const lanlanName = await getLanlanName();
            if (!lanlanName) return { isVoiceMode: false, isCurrent: false };

            // 使用 RequestHelper，设置较短的超时时间（5秒）
            // RequestHelper.fetchJson 已经返回解析后的 JSON 数据
            const data = await RequestHelper.fetchJson(
                `/api/characters/catgirl/${encodeURIComponent(lanlanName)}/voice_mode_status`,
                {
                    method: 'GET',
                    headers: {
                        'Content-Type': 'application/json'
                    }
                },
                5000 // 5秒超时
            );

            return {
                isVoiceMode: data.is_voice_mode || false,
                isCurrent: data.is_current || false
            };
        } catch (error) {
            // 区分不同类型的错误
            if (error.name === 'AbortError' || error.name === 'TimeoutError') {
                console.warn('检查语音模式状态超时（服务器可能未响应）');
            } else if (error.message && (error.message.includes('Failed to fetch') || error.message.includes('ERR_CONNECTION_REFUSED'))) {
                console.warn('无法连接到服务器，请确保 main_server.py 正在运行');
            } else {
                console.warn('检查语音模式状态失败:', error);
            }
            // 连接失败时返回默认值，允许用户继续操作
            return { isVoiceMode: false, isCurrent: false };
        }
    }

    // 修改模型选择事件，自动保存模型设置
    modelSelect.addEventListener('change', async (e) => {
        const modelName = e.target.value;

        // 更新按钮文字
        if (typeof updateLive2DModelSelectButtonText === 'function') {
            updateLive2DModelSelectButtonText();
        }

        if (!modelName) return;

        // 检查语音模式状态
        const voiceStatus = await checkVoiceModeStatus();
        if (voiceStatus.isCurrent && voiceStatus.isVoiceMode) {
            showStatus(t('live2d.cannotChangeModelInVoiceMode', '语音模式下无法切换模型，请先停止语音对话'), 3000);
            // 恢复之前的选择
            if (currentModelInfo && currentModelInfo.name) {
                e.target.value = currentModelInfo.name;
            } else {
                e.target.value = '';
            }
            return;
        }

        currentModelInfo = availableModels.find(m => m.name === modelName);
        if (!currentModelInfo) return;

        // 获取选中的option元素，从中获取item_id
        const selectedOption = e.target[e.target.selectedIndex];
        const modelSteamId = selectedOption ? selectedOption.dataset.itemId : currentModelInfo.item_id;

        // 更新currentModelInfo的item_id（如果从option获取到了）
        if (modelSteamId && modelSteamId !== 'undefined') {
            currentModelInfo.item_id = modelSteamId;
        }

        await loadModel(modelName, currentModelInfo, modelSteamId);

        // 不自动保存模型到角色，改为标记为有未保存更改，用户需手动点击"保存设置"
        window.hasUnsavedChanges = true;
        console.log('已标记为未保存更改（模型切换），请点击 保存设置 持久化到角色配置。');
    });

    // 加载模型的函数
    async function loadModel(modelName, modelInfo, steam_id) {
        if (!modelName || !modelInfo) return;

        // 确保获取正确的steam_id，优先使用传入的，然后从modelInfo中获取
        let finalSteamId = steam_id || modelInfo.item_id;
        showStatus(t('live2d.loadingModel', `正在加载模型: ${modelName}...`, { model: modelName }));
        setControlsDisabled(true);

        try {
            // 1. 获取文件列表（根据来源选择 API）
            let apiUrl = '';
            if (modelInfo.source === 'user_mods') {
                apiUrl = `/api/live2d/model_files/${encodeURIComponent(modelName)}`;
            } else if (finalSteamId && finalSteamId !== 'undefined') {
                apiUrl = `/api/live2d/model_files_by_id/${finalSteamId}`;
            } else {
                apiUrl = `/api/live2d/model_files/${encodeURIComponent(modelName)}`;
            }

            // 使用助手
            const filesData = await RequestHelper.fetchJson(apiUrl);

            // 检查 API 是否返回错误（例如模型目录不存在）
            if (filesData.success === false) {
                throw new Error(filesData.error || '获取模型文件列表失败');
            }

            // 确保字段存在，防止 undefined 访问导致 TypeError
            filesData.motion_files = filesData.motion_files || [];
            filesData.expression_files = filesData.expression_files || [];

            currentModelFiles = filesData;

            // 2. Fetch model config
            let modelJsonUrl;
            // 优先使用后端返回的model_config_url（如果有）
            if (filesData.model_config_url) {
                modelJsonUrl = filesData.model_config_url;
            } else if (modelInfo.source === 'user_mods') {
                // 对于用户mod模型，直接使用modelInfo.path（已经包含/user_mods/路径）
                modelJsonUrl = modelInfo.path;
            } else if (finalSteamId && finalSteamId !== 'undefined') {
                // 如果提供了finalSteamId但没有model_config_url，使用原来的方式构建URL（兼容模式）
                modelJsonUrl = `/workshop/${finalSteamId}/${modelName}.model3.json`;
            } else {
                // 否则使用原来的路径
                modelJsonUrl = modelInfo.path;
            }
            // 使用 RequestHelper 确保统一的错误处理和超时（模型配置文件也是JSON格式）
            const modelConfig = await RequestHelper.fetchJson(modelJsonUrl);

            // 3. Add URL context for the loader
            modelConfig.url = modelJsonUrl;

            // 4. Inject PreviewAll motion group AND ensure all expressions are referenced
            if (!modelConfig.FileReferences) modelConfig.FileReferences = {};

            // Motions
            if (!modelConfig.FileReferences.Motions) modelConfig.FileReferences.Motions = {};
            // 只有当模型有动作文件时才添加PreviewAll组
            if (currentModelFiles.motion_files.length > 0) {
                modelConfig.FileReferences.Motions.PreviewAll = currentModelFiles.motion_files.map(file => ({
                    File: file  // 直接使用API返回的完整路径
                }));
            }

            // Expressions: Overwrite with all available expression files for preview purposes.
            modelConfig.FileReferences.Expressions = currentModelFiles.expression_files.map(file => ({
                Name: file.split('/').pop().replace('.exp3.json', ''),  // 从路径中提取文件名作为名称
                File: file  // 直接使用API返回的完整路径
            }));

            // 5. Load preferences
            const preferences = await window.live2dManager.loadUserPreferences();
            const modelPreferences = preferences.find(p => p && p.model_path === modelInfo.path) || null;

            // 6. Load model FROM THE MODIFIED OBJECT
            await window.live2dManager.loadModel(modelConfig, {
                loadEmotionMapping: true,
                dragEnabled: true,
                wheelEnabled: true,
                preferences: modelPreferences,
                skipCloseWindows: true  // model_manager 页面不需要关闭其他窗口
            });
            live2dModel = window.live2dManager.getCurrentModel();

            // 添加模型交互监听器，跟踪位置和缩放变化
            if (live2dModel && live2dModel.internalModel) {
                const canvas = document.getElementById('live2d-canvas');
                if (canvas) {
                    // 位置和缩放的自动保存现在由 live2d-interaction.js 处理
                }
            }

            updateSelectWithOptions(motionSelect, currentModelFiles.motion_files, t('live2d.selectMotion', '选择动作'), 'motion');
            // 更新动作选择器按钮和下拉菜单
            if (typeof updateMotionSelectButtonText === 'function') {
                updateMotionSelectButtonText();
            }
            if (typeof updateMotionDropdown === 'function') {
                updateMotionDropdown();
            }
            updateSelectWithOptions(expressionSelect, currentModelFiles.expression_files, t('live2d.selectExpression', '选择表情'), 'expression');

            // 更新表情选择器按钮文字和下拉菜单
            updateExpressionSelectButtonText();
            updateExpressionDropdown();

            // 更新常驻表情选择框（只显示 .exp3.json 文件）
            await updatePersistentExpressionSelect();

            // 7. Load current emotion mapping for this model
            await loadEmotionMappingForModel(modelName);

            // 加载并显示已配置的常驻表情
            await loadPersistentExpressions();

            // 如果没有动作文件，禁用动作相关控件
            if (currentModelFiles.motion_files.length === 0) {
                motionSelect.disabled = true;
                const motionSelectBtn = document.getElementById('motion-select-btn');
                if (motionSelectBtn) motionSelectBtn.disabled = true;
                playMotionBtn.disabled = true;
                motionSelect.innerHTML = `<option value="">${t('live2d.noMotionFiles', '没有动作文件')}</option>`;
                // 更新按钮文字
                if (typeof updateMotionSelectButtonText === 'function') {
                    updateMotionSelectButtonText();
                }
            } else {
                // 启用动作选择器按钮和隐藏的select
                motionSelect.disabled = false;
                const motionSelectBtn = document.getElementById('motion-select-btn');
                if (motionSelectBtn) motionSelectBtn.disabled = false;
                // 播放按钮保持可用：未选择动作时由点击逻辑提示“请先选择动作”
                playMotionBtn.disabled = false;
            }

            // 表情播放按钮：仅当有表情文件且已选择有效表情时启用
            playExpressionBtn.disabled = !(
                currentModelFiles.expression_files &&
                currentModelFiles.expression_files.length > 0 &&
                expressionSelect &&
                expressionSelect.value
            );

            // 启用其他控件
            setControlsDisabled(false);
            showStatus(t('live2d.modelLoadSuccess', `模型 ${modelName} 加载成功`, { model: modelName }));

        } catch (error) {
            showStatus(t('live2d.modelLoadFailed', `加载模型 ${modelName} 失败`, { model: modelName }));
            console.error(error);
            setControlsDisabled(false);
        }
    }

    playMotionBtn.addEventListener('click', () => {
        // 检查是否加载了模型
        if (!live2dModel) {
            showStatus(t('live2d.pleaseLoadModel', '请先加载模型'), 2000);
            return;
        }

        // 检查是否选择了动作
        if (!motionSelect.value) {
            showStatus(t('live2d.pleaseSelectMotion', '请先选择动作'), 2000);
            return;
        }

        // 检查是否有动作文件
        if (currentModelFiles.motion_files.length === 0) {
            showStatus(t('live2d.noMotionFilesStatus', '没有动作文件'), 2000);
            return;
        }

        // 切换播放/停止状态（图标始终显示播放图标，绝不切换为暂停图标）
        if (isMotionPlaying) {
            // 停止动作
            try {
                live2dModel.motion('PreviewAll', -1, 0); // 停止动作
                isMotionPlaying = false;
                // 确保图标仍然是播放图标
                updateMotionPlayButtonIcon();
                showStatus(t('live2d.motionStopped', '动作已停止'), 1000);

                // 清除动作预览恢复定时器
                if (window._motionPreviewRestoreTimer) {
                    clearTimeout(window._motionPreviewRestoreTimer);
                    window._motionPreviewRestoreTimer = null;
                }

                // 清除预览标记
                window._currentMotionPreviewId = null;

                // 停止动作后平滑恢复到初始状态（smoothReset 内部会在快照后停止 motion/expression）
                if (window.live2dManager && typeof window.live2dManager.smoothResetToInitialState === 'function') {
                    window.live2dManager.smoothResetToInitialState().catch(e => {
                        console.warn('[ModelManager] 停止动作后平滑恢复失败:', e);
                        // 降级：尝试清除表情以确保不残留
                        if (window.live2dManager && typeof window.live2dManager.clearExpression === 'function') {
                            window.live2dManager.clearExpression();
                        }
                    });
                } else if (window.live2dManager && typeof window.live2dManager.clearExpression === 'function') {
                    window.live2dManager.clearExpression();
                }
            } catch (error) {
                console.error('停止动作失败:', error);
            }
        } else {
            // 播放动作
            const motionIndex = currentModelFiles.motion_files.indexOf(motionSelect.value);
            if (motionIndex > -1) {
                try {
                    // 清除之前的恢复定时器
                    if (window._motionPreviewRestoreTimer) {
                        clearTimeout(window._motionPreviewRestoreTimer);
                        window._motionPreviewRestoreTimer = null;
                    }
                    if (window._expressionPreviewRestoreTimer) {
                        clearTimeout(window._expressionPreviewRestoreTimer);
                        window._expressionPreviewRestoreTimer = null;
                    }
                    // 使在途的表情 await 回调失效，防止异步返回后设置恢复定时器打断动作
                    window._currentExpressionPreviewToken = null;

                    live2dModel.motion('PreviewAll', motionIndex, 3);
                    isMotionPlaying = true;
                    // 确保图标仍然是播放图标
                    updateMotionPlayButtonIcon();
                    showStatus(t('live2d.playingMotion', `播放动作: ${motionSelect.value}`, { motion: motionSelect.value }), 1000);

                    // 创建预览标记，防止快速切换预览时旧的 fetch 回调覆盖新的恢复定时器
                    window._currentMotionPreviewId = (window._currentMotionPreviewId || 0) + 1;
                    const previewId = window._currentMotionPreviewId;

                    // 尝试获取动作持续时间，设置自动恢复定时器
                    const _motionRestoreCallback = () => {
                        if (window._currentMotionPreviewId !== previewId) return; // 已被新的预览覆盖
                        window._motionPreviewRestoreTimer = null;
                        window._currentMotionPreviewId = null;
                        isMotionPlaying = false;
                        updateMotionPlayButtonIcon();
                        console.log('[ModelManager] 动作预览结束，自动恢复到初始状态');
                        if (window.live2dManager && typeof window.live2dManager.smoothResetToInitialState === 'function') {
                            window.live2dManager.smoothResetToInitialState().catch(() => {
                                if (window.live2dManager && typeof window.live2dManager.clearExpression === 'function') {
                                    window.live2dManager.clearExpression();
                                }
                            });
                        } else if (window.live2dManager && typeof window.live2dManager.clearExpression === 'function') {
                            window.live2dManager.clearExpression();
                        }
                    };
                    try {
                        const motionFile = motionSelect.value;
                        const motionUrl = window.live2dManager ? window.live2dManager.resolveAssetPath(motionFile) : motionFile;
                        RequestHelper.fetchJson(motionUrl).then(data => {
                            if (window._currentMotionPreviewId !== previewId) return; // 过时的响应
                            const dur = data?.Meta?.Duration ? data.Meta.Duration * 1000 + 500 : 10000; // 动作时长 + 500ms缓冲，或10秒后备
                            window._motionPreviewRestoreTimer = setTimeout(_motionRestoreCallback, dur);
                        }).catch(() => {
                            if (window._currentMotionPreviewId !== previewId) return; // 过时的响应
                            // fetch失败，使用10秒后备定时器
                            window._motionPreviewRestoreTimer = setTimeout(_motionRestoreCallback, 10000);
                        });
                    } catch (e) {
                        // 设置后备定时器
                        window._motionPreviewRestoreTimer = setTimeout(_motionRestoreCallback, 10000);
                    }
                } catch (error) {
                    console.error('播放动作失败:', error);
                    showStatus(t('live2d.playMotionFailed', `播放动作失败: ${motionSelect.value}`, { motion: motionSelect.value }), 2000);
                }
            } else {
                showStatus(t('live2d.motionFileNotExists', '动作文件不存在'), 2000);
            }
        }
    });

    // 当选择新动作时，重置播放状态
    motionSelect.addEventListener('change', async (e) => {
        const selectedValue = e.target.value;

        // 如果选择的是第一个选项（空值，即"增加动作"），触发文件选择器
        if (selectedValue === '') {
            const motionFileUpload = document.getElementById('motion-file-upload');
            if (motionFileUpload) {
                motionFileUpload.click();
            }
            // 重置选择器到第一个选项（保持显示"增加动作"）
            e.target.value = '';
            // 播放按钮保持可用：未选择动作时点击会提示
            playMotionBtn.disabled = false;
            return;
        }

        isMotionPlaying = false;
        // 确保图标仍然是播放图标
        updateMotionPlayButtonIcon();
        updateMotionSelectButtonText();
        // 播放按钮保持可用
        playMotionBtn.disabled = false;
    });

    // 当表情选择器值改变时，更新按钮文字
    if (expressionSelect) {
        expressionSelect.addEventListener('change', async (e) => {
            const selectedValue = e.target.value;

            // 如果选择的是第一个选项（空值，即"增加表情"），触发文件选择器
            if (selectedValue === '') {
                const expressionFileUpload = document.getElementById('expression-file-upload');
                if (expressionFileUpload) {
                    expressionFileUpload.click();
                }
                // 重置选择器到第一个选项（保持显示"增加表情"）
                e.target.value = '';
                // 仅当有表情文件且已选择有效表情时启用
                const hasExpressions = !!(
                    currentModelFiles &&
                    currentModelFiles.expression_files &&
                    currentModelFiles.expression_files.length > 0
                );
                playExpressionBtn.disabled = !(hasExpressions && e.target.value);
                return;
            }

            updateExpressionSelectButtonText();
            // 仅当有表情文件且已选择有效表情时启用
            const hasExpressions = !!(
                currentModelFiles &&
                currentModelFiles.expression_files &&
                currentModelFiles.expression_files.length > 0
            );
            playExpressionBtn.disabled = !(hasExpressions && e.target.value);
        });
    }

    playExpressionBtn.addEventListener('click', async () => {
        // 检查当前模型类型，只处理 Live2D 模型
        if (currentModelType !== 'live2d') {
            console.warn('表情预览功能仅支持 Live2D 模型');
            return;
        }

        // 重新获取当前模型，确保使用最新引用
        const currentModel = window.live2dManager ? window.live2dManager.getCurrentModel() : live2dModel;
        if (!currentModel) {
            showStatus(t('live2d.pleaseLoadModel', '请先加载模型'), 2000);
            return;
        }

        if (!expressionSelect.value) {
            showStatus(t('live2d.pleaseSelectExpression', '请先选择表情'), 2000);
            return;
        }

        // 从完整路径中提取表情名称（去掉路径和扩展名）
        const expressionName = expressionSelect.value.split('/').pop().replace('.exp3.json', '');

        try {
            // 清除之前的表情预览恢复定时器
            if (window._expressionPreviewRestoreTimer) {
                clearTimeout(window._expressionPreviewRestoreTimer);
                window._expressionPreviewRestoreTimer = null;
            }
            // 使在途的动作预览 fetch 回调失效，防止异步返回后设置恢复定时器打断表情
            if (window._motionPreviewRestoreTimer) {
                clearTimeout(window._motionPreviewRestoreTimer);
                window._motionPreviewRestoreTimer = null;
            }
            window._currentMotionPreviewId = null;

            // 创建预览标记，防止快速连续点击时并发 await 导致多个定时器共存
            window._currentExpressionPreviewToken = (window._currentExpressionPreviewToken || 0) + 1;
            const previewToken = window._currentExpressionPreviewToken;

            // expression 方法是异步的，需要使用 await
            // 注意：Live2D SDK 的 expression 方法可能返回 null/undefined 但仍然成功播放
            const result = await currentModel.expression(expressionName);

            // await 返回后检查标记是否仍然匹配（可能已被新的预览覆盖）
            if (window._currentExpressionPreviewToken !== previewToken) return;

            // Live2D SDK 的 expression 方法成功时可能返回 falsy 值，这里改为检查是否抛出异常
            // 如果没有抛出异常，就认为播放成功
            showStatus(t('live2d.playingExpression', `播放表情: ${expressionName}`, { expression: expressionName }), 1000);

            // 设置自动恢复定时器：5秒后平滑恢复到初始状态
            window._expressionPreviewRestoreTimer = setTimeout(() => {
                window._expressionPreviewRestoreTimer = null;
                if (window._currentExpressionPreviewToken !== previewToken) return; // 已被新的预览覆盖
                window._currentExpressionPreviewToken = null;
                console.log('[ModelManager] 表情预览结束，自动恢复到初始状态');
                if (window.live2dManager && typeof window.live2dManager.smoothResetToInitialState === 'function') {
                    window.live2dManager.smoothResetToInitialState().catch(e => {
                        console.warn('[ModelManager] 平滑恢复失败:', e);
                        if (window.live2dManager && typeof window.live2dManager.clearExpression === 'function') {
                            window.live2dManager.clearExpression();
                        }
                    });
                } else if (window.live2dManager && typeof window.live2dManager.clearExpression === 'function') {
                    window.live2dManager.clearExpression();
                }
            }, 5000);
        } catch (error) {
            console.error('播放表情失败:', error);
            showStatus(t('live2d.playExpressionFailed', `播放表情失败: ${expressionName}`, { expression: expressionName }), 2000);
        }
    });

    savePositionBtn.addEventListener('click', async () => {
        // VRM模式下，即使模型未加载，只要有选择的模型就可以保存
        if (currentModelType === 'vrm') {
            const selectedModelPath = vrmModelSelect ? vrmModelSelect.value : null;
            if (!selectedModelPath) {
                showStatus(t('live2d.pleaseSelectModel', '请先选择一个VRM模型'), 2000);
                return;
            }
            // 如果没有currentModelInfo，使用当前选择的模型路径创建
            if (!currentModelInfo) {
                currentModelInfo = {
                    name: selectedModelPath,
                    path: selectedModelPath,
                    type: 'vrm'
                };
            }
        } else {
            // Live2D模式下需要currentModelInfo
            if (!currentModelInfo) {
                showStatus(t('live2d.pleaseSelectModel', '请先选择模型'), 2000);
                return;
            }
        }

        showStatus(t('live2d.savingSettings', '正在保存设置...'));

        let positionSuccess = false;
        let modelSuccess = false;

        // 根据模型类型保存不同的设置
        if (currentModelType === 'vrm') {
            // VRM 模式：保存模型设置（动作已改为自动循环播放，不再需要保存）
            modelSuccess = await saveModelToCharacter(currentModelInfo.name, null, null);
        } else {
            // Live2D 模式：保存位置、缩放和模型设置
            if (!live2dModel) {
                showStatus(t('live2d.pleaseLoadModel', '请先加载模型'), 2000);
                return;
            }

            // 添加调试信息

            // 保存位置和缩放
            positionSuccess = await window.live2dManager.saveUserPreferences(
                currentModelInfo.path,
                { x: live2dModel.x, y: live2dModel.y },
                { x: live2dModel.scale.x, y: live2dModel.scale.y }
            );

            // 保存模型设置到角色，同时传入item_id
            modelSuccess = await saveModelToCharacter(currentModelInfo.name, currentModelInfo.item_id);
        }

        if (currentModelType === 'vrm') {
            // VRM 模式：只显示模型保存结果
            if (modelSuccess) {
                showStatus(t('live2d.settingsSaved', '模型设置保存成功!'), 2000);
                window.hasUnsavedChanges = false;
                window._modelManagerHasSaved = true;
                // 不在保存时立即通知主页，而是在返回主页时通知
                // if (window.opener && !window.opener.closed) {
                //     try {
                //         window.opener.postMessage({
                //             action: 'model_saved',
                //             timestamp: Date.now()
                //         }, window.location.origin);
                //         console.log('[消息发送] VRM模型保存成功，立即发送 model_saved 消息');
                //     } catch (e) {
                //         console.warn('发送保存成功消息失败:', e);
                //     }
                // }
                // sendMessageToMainPage('reload_model');
            } else {
                showStatus(t('live2d.saveFailedGeneral', '保存失败!'), 2000);
            }
        } else {
            // Live2D 模式：显示位置和模型保存结果
            if (positionSuccess && modelSuccess) {
                showStatus(t('live2d.settingsSaved', '位置和模型设置保存成功!'), 2000);
                window.hasUnsavedChanges = false; // 保存成功后重置标志
                window._modelManagerHasSaved = true;
                // 不在保存时立即通知主页，而是在返回主页时通知
                // sendMessageToMainPage('reload_model');
            } else if (positionSuccess) {
                showStatus(t('live2d.positionSavedModelFailed', '位置保存成功，模型设置保存失败!'), 2000);
                // 位置偏好已保存，主界面如触发重载可恢复位置；但仅在用户退出时才通知
                window._modelManagerHasSaved = true;
            } else if (modelSuccess) {
                showStatus(t('live2d.modelSavedPositionFailed', '模型设置保存成功，位置保存失败!'), 2000);
                window._modelManagerHasSaved = true;
                // 不在保存时立即通知主页，而是在返回主页时通知
                // sendMessageToMainPage('reload_model');
            } else {
                showStatus(t('live2d.saveFailedGeneral', '保存失败!'), 2000);
            }
        }
    });

    // 情感配置按钮
    if (emotionConfigBtn) {
        emotionConfigBtn.addEventListener('click', () => {
            // 打开Live2D情感映射管理器页面
            const width = 900;
            const height = 800;
            const left = (screen.width - width) / 2;
            const top = (screen.height - height) / 2;
            window.open(
                '/live2d_emotion_manager',
                'emotionManager',
                `width=${width},height=${height},left=${left},top=${top},resizable=yes,scrollbars=yes`
            );
        });
    }

    // VRM情感配置按钮
    const vrmEmotionConfigBtn = document.getElementById('vrm-emotion-config-btn');
    if (vrmEmotionConfigBtn) {
        vrmEmotionConfigBtn.addEventListener('click', () => {
            // 打开VRM情感映射管理器页面
            const width = 900;
            const height = 800;
            const left = (screen.width - width) / 2;
            const top = (screen.height - height) / 2;
            window.open(
                '/vrm_emotion_manager',
                'vrmEmotionManager',
                `width=${width},height=${height},left=${left},top=${top},resizable=yes,scrollbars=yes`
            );
        });
    }

    // 返回主页/关闭按钮
    backToMainBtn.addEventListener('click', async () => {
        // 检查是否有未保存的更改
        if (window.hasUnsavedChanges) {
            const message = t('dialogs.unsavedChanges', '您有未保存的设置，确定要离开吗？');
            const title = t('dialogs.confirmLeave', '确认离开');
            const confirmLeave = await showConfirm(message, title, { danger: true });
            if (!confirmLeave) {
                return; // 用户取消，不离开
            }
            // 用户确认离开，重置未保存状态，避免被 beforeunload 拦截
            window.hasUnsavedChanges = false;
        } else {
        }

        // 如果处于全屏状态，先退出全屏
        if (isFullscreen()) {
            try {
                await exitFullscreen();
                await new Promise(resolve => setTimeout(resolve, 100));
            } catch (e) {
                console.log('退出全屏失败:', e);
            }
        }

        // 根据窗口类型执行不同的操作
        if (isPopupWindow) {
            // 如果是弹出窗口：只有在本页确实保存过设置时才刷新主界面模型
            // 否则不触发重载，避免“退出即复位/回默认模型”
            if (window._modelManagerHasSaved) {
                // 发送前确保 lanlan_name 已解析并缓存，避免主界面按角色过滤时因空值丢弃消息
                if (!window._modelManagerLanlanName || window._modelManagerLanlanName.trim() === '') {
                    try {
                        const resolvedLanlanName = await getLanlanName();
                        if (resolvedLanlanName && resolvedLanlanName.trim() !== '') {
                            window._modelManagerLanlanName = resolvedLanlanName;
                        }
                    } catch (e) {
                        console.warn('[模型管理] 获取 lanlan_name 失败，跳过缓存:', e);
                    }
                }

                if (window._modelManagerLanlanName && window._modelManagerLanlanName.trim() !== '') {
                    sendMessageToMainPage('reload_model', { lanlan_name: window._modelManagerLanlanName || '' });
                } else {
                    console.warn('[模型管理] lanlan_name 为空，跳过 reload_model 通知以避免主界面过滤失败');
                }
            }
            // 延迟一点确保消息发送
            setTimeout(() => {
                window.close();
            }, 100);
        } else {
            // 如果是主窗口跳转，直接跳转即可，新页面会自动加载最新配置
            window.location.href = '/';
        }
    });

    // 上传模型功能
    uploadBtn.addEventListener('click', () => {
        // 根据当前模型类型选择不同的文件选择器
        if (currentModelType === 'vrm') {
            vrmFileUpload.click();
        } else {
            modelUpload.click();
        }
    });

    // 动作文件上传
    if (motionFileUpload) {
        motionFileUpload.addEventListener('change', async (e) => {
            const file = e.target.files[0];
            if (!file) return;

            if (!currentModelInfo || !currentModelInfo.name) {
                showStatus(t('live2d.pleaseSelectModel', '请先选择模型'), 2000);
                motionFileUpload.value = '';
                return;
            }

            showStatus(t('live2d.uploadingMotion', '正在上传动作文件...'), 0);
            setControlsDisabled(true);

            try {
                const formData = new FormData();
                formData.append('file', file);
                formData.append('file_type', 'motion');

                const response = await fetch(`/api/live2d/upload_file/${encodeURIComponent(currentModelInfo.name)}`, {
                    method: 'POST',
                    body: formData
                });

                const result = await response.json();

                if (result.success) {
                    showStatus(t('live2d.uploadMotionSuccess', `动作文件 ${result.filename} 上传成功`, { filename: result.filename }), 2000);

                    // 重新获取模型文件列表并更新下拉菜单
                    try {
                        let apiUrl = '';
                        if (currentModelInfo.source === 'user_mods') {
                            apiUrl = `/api/live2d/model_files/${encodeURIComponent(currentModelInfo.name)}`;
                        } else if (currentModelInfo.item_id && currentModelInfo.item_id !== 'undefined') {
                            apiUrl = `/api/live2d/model_files_by_id/${currentModelInfo.item_id}`;
                        } else {
                            apiUrl = `/api/live2d/model_files/${encodeURIComponent(currentModelInfo.name)}`;
                        }

                        const filesData = await RequestHelper.fetchJson(apiUrl);
                        currentModelFiles = filesData;

                        // 更新下拉菜单
                        updateSelectWithOptions(motionSelect, currentModelFiles.motion_files, t('live2d.selectMotion', '选择动作'), 'motion');
                        if (typeof updateMotionSelectButtonText === 'function') {
                            updateMotionSelectButtonText();
                        }
                        if (typeof updateMotionDropdown === 'function') {
                            updateMotionDropdown();
                        }

                        // 启用动作相关控件
                        motionSelect.disabled = false;
                        const motionSelectBtn = document.getElementById('motion-select-btn');
                        if (motionSelectBtn) motionSelectBtn.disabled = false;
                        playMotionBtn.disabled = false;
                    } catch (error) {
                        console.error('重新加载模型文件列表失败:', error);
                        showStatus(t('live2d.reloadFilesFailed', '文件上传成功，但重新加载文件列表失败'), 3000);
                    }
                } else {
                    showStatus(t('live2d.uploadMotionFailed', `上传失败: ${result.error}`, { error: result.error }), 3000);
                }
            } catch (error) {
                console.error('上传动作文件失败:', error);
                showStatus(t('live2d.uploadMotionError', `上传失败: ${error.message}`, { error: error.message }), 3000);
            } finally {
                setControlsDisabled(false);
                motionFileUpload.value = '';
            }
        });
    }

    // VRM动作文件上传
    if (vrmAnimationFileUpload) {
        vrmAnimationFileUpload.addEventListener('change', async (e) => {
            const file = e.target.files[0];
            if (!file) return;

            showStatus(t('live2d.uploadingVRMAnimation', '正在上传VRM动作文件...'), 0);
            setControlsDisabled(true);

            try {
                const formData = new FormData();
                formData.append('file', file);

                const response = await fetch('/api/model/vrm/upload_animation', {
                    method: 'POST',
                    body: formData
                });

                const result = await response.json();

                if (result.success) {
                    showStatus(t('live2d.uploadVRMAnimationSuccess', `VRM动作文件 ${result.filename} 上传成功`, { filename: result.filename }), 2000);

                    // 重新加载动作列表
                    try {
                        animationsLoaded = false; // 重置标记，强制重新加载
                        await loadVRMAnimations();
                    } catch (error) {
                        console.error('重新加载VRM动作列表失败:', error);
                        showStatus(t('live2d.reloadVRMAnimationsFailed', '文件上传成功，但重新加载动作列表失败'), 3000);
                    }
                } else {
                    showStatus(t('live2d.uploadVRMAnimationFailed', `上传失败: ${result.error}`, { error: result.error }), 3000);
                }
            } catch (error) {
                console.error('上传VRM动作文件失败:', error);
                showStatus(t('live2d.uploadVRMAnimationError', `上传失败: ${error.message}`, { error: error.message }), 3000);
            } finally {
                setControlsDisabled(false);
                vrmAnimationFileUpload.value = '';
            }
        });
    }

    // 表情文件上传
    if (expressionFileUpload) {
        expressionFileUpload.addEventListener('change', async (e) => {
            const file = e.target.files[0];
            if (!file) return;

            if (!currentModelInfo || !currentModelInfo.name) {
                showStatus(t('live2d.pleaseSelectModel', '请先选择模型'), 2000);
                expressionFileUpload.value = '';
                return;
            }

            showStatus(t('live2d.uploadingExpression', '正在上传表情文件...'), 0);
            setControlsDisabled(true);

            try {
                const formData = new FormData();
                formData.append('file', file);
                formData.append('file_type', 'expression');

                const response = await fetch(`/api/live2d/upload_file/${encodeURIComponent(currentModelInfo.name)}`, {
                    method: 'POST',
                    body: formData
                });

                const result = await response.json();

                if (result.success) {
                    showStatus(t('live2d.uploadExpressionSuccess', `表情文件 ${result.filename} 上传成功`, { filename: result.filename }), 2000);

                    // 重新获取模型文件列表并更新下拉菜单
                    try {
                        let apiUrl = '';
                        if (currentModelInfo.source === 'user_mods') {
                            apiUrl = `/api/live2d/model_files/${encodeURIComponent(currentModelInfo.name)}`;
                        } else if (currentModelInfo.item_id && currentModelInfo.item_id !== 'undefined') {
                            apiUrl = `/api/live2d/model_files_by_id/${currentModelInfo.item_id}`;
                        } else {
                            apiUrl = `/api/live2d/model_files/${encodeURIComponent(currentModelInfo.name)}`;
                        }

                        const filesData = await RequestHelper.fetchJson(apiUrl);
                        currentModelFiles = filesData;

                        // 更新下拉菜单
                        updateSelectWithOptions(expressionSelect, currentModelFiles.expression_files, t('live2d.selectExpression', '选择表情'), 'expression');
                        updateExpressionSelectButtonText();
                        updateExpressionDropdown();

                        // 更新常驻表情选择框
                        await updatePersistentExpressionSelect();
                    } catch (error) {
                        console.error('重新加载模型文件列表失败:', error);
                        showStatus(t('live2d.reloadFilesFailed', '文件上传成功，但重新加载文件列表失败'), 3000);
                    }
                } else {
                    showStatus(t('live2d.uploadExpressionFailed', `上传失败: ${result.error}`, { error: result.error }), 3000);
                }
            } catch (error) {
                console.error('上传表情文件失败:', error);
                showStatus(t('live2d.uploadExpressionError', `上传失败: ${error.message}`, { error: error.message }), 3000);
            } finally {
                setControlsDisabled(false);
                expressionFileUpload.value = '';
            }
        });
    }

    // Live2D模型上传（文件夹）
    modelUpload.addEventListener('change', async (e) => {
        const files = Array.from(e.target.files);
        if (files.length === 0) return;

        uploadStatus.textContent = t('live2d.uploadingModel', '正在上传模型...');
        uploadStatus.style.color = '#4f8cff';
        uploadBtn.disabled = true;

        try {
            const formData = new FormData();

            // 添加所有文件到FormData
            for (const file of files) {
                // 保留文件的相对路径
                formData.append('files', file, file.webkitRelativePath || file.name);
            }

            const response = await fetch('/api/live2d/upload_model', {
                method: 'POST',
                body: formData
            });

            const result = await response.json();

            if (result.success) {
                uploadStatus.textContent = t('live2d.uploadSuccess', `✓ ${result.message}`, { message: result.message });
                uploadStatus.style.color = '#28a745';

                // 重新加载模型列表
                setTimeout(async () => {
                    try {
                        const modelsResponse = await fetch('/api/live2d/models');
                        availableModels = await modelsResponse.json();
                        modelSelect.innerHTML = `<option value="">${t('live2d.pleaseSelectModel', '选择模型')}</option>`;
                        availableModels.forEach(model => {
                            const option = document.createElement('option');
                            option.value = model.name;
                            // 使用display_name（如果存在）显示更友好的名称
                            option.textContent = model.display_name || model.name;
                            if (model.item_id) {
                                option.dataset.itemId = model.item_id;
                            }
                            modelSelect.appendChild(option);
                        });


                        // 自动选择新上传的模型
                        if (result.model_name) {
                            modelSelect.value = result.model_name;
                            modelSelect.dispatchEvent(new Event('change'));
                        }

                        // 更新自定义下拉菜单
                        if (typeof updateLive2DModelDropdown === 'function') {
                            updateLive2DModelDropdown();
                        }
                        // 更新按钮文字
                        if (typeof updateLive2DModelSelectButtonText === 'function') {
                            updateLive2DModelSelectButtonText();
                        }

                        uploadStatus.textContent = '';
                    } catch (e) {
                        console.error('重新加载模型列表失败:', e);
                    }
                }, 1500);
            } else {
                uploadStatus.textContent = t('live2d.uploadFailed', `✗ ${result.error}`, { error: result.error });
                uploadStatus.style.color = '#dc3545';
                setTimeout(() => {
                    uploadStatus.textContent = '';
                }, 5000);
            }
        } catch (error) {
            console.error('上传失败:', error);
            uploadStatus.textContent = t('live2d.uploadError', `✗ 上传失败: ${error.message}`, { error: error.message });
            uploadStatus.style.color = '#dc3545';
            setTimeout(() => {
                uploadStatus.textContent = '';
            }, 5000);
        } finally {
            uploadBtn.disabled = false;
            // 重置file input以允许重新选择同一个文件夹
            modelUpload.value = '';
        }
    });

    // VRM模型上传（单个文件）
    vrmFileUpload.addEventListener('change', async (e) => {
        const files = Array.from(e.target.files);
        if (files.length === 0) return;

        // 检查文件类型
        const vrmFile = files.find(f => f.name.toLowerCase().endsWith('.vrm'));
        if (!vrmFile) {
            uploadStatus.textContent = t('live2d.uploadVRMFailed', '✗ 请选择.vrm文件', { error: '请选择.vrm文件' });
            uploadStatus.style.color = '#dc3545';
            setTimeout(() => {
                uploadStatus.textContent = '';
            }, 3000);
            vrmFileUpload.value = '';
            return;
        }

        uploadStatus.textContent = t('live2d.uploadingVRMModel', '正在上传VRM模型...');
        uploadStatus.style.color = '#4f8cff';
        uploadBtn.disabled = true;

        try {
            const formData = new FormData();
            // VRM模型只需要上传单个.vrm文件
            // 注意：后端参数名是 file（单数），不是 files
            formData.append('file', vrmFile, vrmFile.name);

            const response = await fetch('/api/model/vrm/upload', {
                method: 'POST',
                body: formData
            });

            const result = await response.json();

            if (result.success) {
                uploadStatus.textContent = t('live2d.uploadVRMSuccess', `✓ ${result.message}`, { message: result.message });
                uploadStatus.style.color = '#28a745';

                // 【新增】在上传成功后，先检测并修正模型朝向，然后再添加到列表

                if (result.model_path && window.VRMOrientationDetector && window.vrmManager) {
                    try {
                        uploadStatus.textContent = t('live2d.vrmUpload.detectingOrientation', '正在检测并修正模型朝向...');

                        // 转换模型路径为URL格式
                        // result.model_path 可能是本地路径（如 C:/Users/.../file.vrm）
                        // 需要转换为URL格式（如 /user_vrm/file.vrm）
                        let modelUrl = result.model_path;

                        // 使用 ModelPathHelper 标准化路径（处理 Windows 路径、相对路径等）
                        modelUrl = ModelPathHelper.normalizeModelPath(modelUrl, 'model');

                        // 初始化Three.js（如果还没初始化）
                        if (!window.vrmManager.scene) {
                            await window.vrmManager.initThreeJS('vrm-canvas', 'vrm-container');
                        }

                        // 临时加载模型
                        await window.vrmManager.loadModel(modelUrl, { autoPlay: false, addShadow: false });

                        // 等待几帧，确保模型完全加载、骨骼位置计算完成
                        for (let i = 0; i < 3; i++) {
                            await new Promise(resolve => requestAnimationFrame(resolve));
                        }

                        // 检测并修正朝向（会自动保存到preferences）
                        if (window.vrmManager.currentModel && window.vrmManager.currentModel.vrm) {
                            const vrm = window.vrmManager.currentModel.vrm;

                            // 检测朝向
                            const needsRotation = window.VRMOrientationDetector.detectNeedsRotation(vrm);
                            const detectedRotation = {
                                x: 0,
                                y: needsRotation ? Math.PI : 0,
                                z: 0
                            };

                            // 应用旋转
                            window.VRMOrientationDetector.applyRotation(vrm, detectedRotation);

                            // 等待一帧，确保旋转已应用
                            await new Promise(resolve => requestAnimationFrame(resolve));

                            // 保存到preferences（使用与vrm-core.js相同的逻辑）
                            if (window.vrmManager.core && typeof window.vrmManager.core.saveUserPreferences === 'function') {
                                const currentPosition = vrm.scene.position.clone();
                                const currentScale = vrm.scene.scale.clone();

                                const saveSuccess = await window.vrmManager.core.saveUserPreferences(
                                    modelUrl,
                                    { x: currentPosition.x, y: currentPosition.y, z: currentPosition.z },
                                    { x: currentScale.x, y: currentScale.y, z: currentScale.z },
                                    detectedRotation,
                                    null
                                );

                                if (saveSuccess) {
                                    const rotationDegrees = (detectedRotation.y * 180 / Math.PI).toFixed(1);
                                    uploadStatus.textContent = t('live2d.vrmUpload.orientationFixed', `✓ 模型朝向已修正并保存 (${rotationDegrees}度)`, { degrees: rotationDegrees });
                                } else {
                                    uploadStatus.textContent = t('live2d.vrmUpload.orientationFixedButSaveFailed', '⚠ 朝向已修正但保存失败');
                                    console.error(`[上传检测] 保存失败: ${modelUrl}`);
                                }
                            } else {
                                uploadStatus.textContent = t('live2d.vrmUpload.cannotSaveOrientation', '⚠ 无法保存朝向配置');
                                console.error(`[上传检测] saveUserPreferences方法不存在`);
                            }

                            // 清理临时加载的模型
                            if (window.vrmManager.currentModel && window.vrmManager.currentModel.vrm) {
                                window.vrmManager.scene.remove(window.vrmManager.currentModel.vrm.scene);
                                window.vrmManager.core.disposeVRM();
                                window.vrmManager.currentModel = null;
                            }
                        } else {
                            uploadStatus.textContent = t('live2d.vrmUpload.cannotGetModelInstance', '⚠ 无法获取模型实例');
                            console.error(`[上传检测] 无法获取模型实例`);
                        }
                    } catch (orientationError) {
                        console.warn('检测模型朝向时出错，将继续添加到列表:', orientationError);
                        uploadStatus.textContent = t('live2d.vrmUpload.orientationDetectionFailed', '⚠ 朝向检测失败，但模型已上传');
                    }
                }

                // 重新加载VRM模型列表
                setTimeout(async () => {
                    try {
                        await loadVRMModels();
                        // 自动选择新上传的模型
                        if (result.model_path && vrmModelSelect) {
                            // 尝试匹配模型路径
                            const modelPath = result.model_path;
                            // 先尝试直接匹配完整路径
                            let option = Array.from(vrmModelSelect.options).find(opt => opt.value === modelPath);
                            // 如果没找到，尝试匹配文件名
                            if (!option && result.model_name) {
                                const fileName = result.model_name + '.vrm';
                                option = Array.from(vrmModelSelect.options).find(opt => {
                                    const optPath = opt.value;
                                    return optPath && (optPath.endsWith(fileName) || optPath.includes(fileName));
                                });
                            }

                            if (option) {
                                vrmModelSelect.value = option.value;
                                // 触发change事件以加载模型
                                vrmModelSelect.dispatchEvent(new Event('change'));
                            } else {
                                console.warn('无法自动选择上传的模型，请手动选择');
                            }
                        }

                        uploadStatus.textContent = '';
                    } catch (e) {
                        console.error('重新加载VRM模型列表失败:', e);
                    }
                }, 1500);
            } else {
                uploadStatus.textContent = t('live2d.uploadVRMFailed', `✗ ${result.error}`, { error: result.error });
                uploadStatus.style.color = '#dc3545';
                setTimeout(() => {
                    uploadStatus.textContent = '';
                }, 5000);
            }
        } catch (error) {
            console.error('上传失败:', error);
            uploadStatus.textContent = t('live2d.uploadVRMError', `✗ 上传失败: ${error.message}`, { error: error.message });
            uploadStatus.style.color = '#dc3545';
            setTimeout(() => {
                uploadStatus.textContent = '';
            }, 5000);
        } finally {
            uploadBtn.disabled = false;
            // 重置file input以允许重新选择同一个文件
            vrmFileUpload.value = '';
        }
    });

    // 删除模型功能
    let selectedDeleteModels = new Set();

    function showDeleteModelModal() {
        if (deleteModelModal) {
            deleteModelModal.classList.add('show');
            selectedDeleteModels.clear();
            updateConfirmDeleteButton();
            loadUserModels();
        }
    }

    function hideDeleteModelModal() {
        if (deleteModelModal) {
            deleteModelModal.classList.remove('show');
            selectedDeleteModels.clear();
        }
    }

    async function loadUserModels() {
        try {
            userModelList.innerHTML = '<div class="empty-message">' + t('live2d.loadingModels', '加载中...') + '</div>';

            // 使用 RequestHelper 确保统一的错误处理和超时
            const result = await RequestHelper.fetchJson('/api/live2d/user_models');

            if (result.success && result.models && result.models.length > 0) {
                userModelList.innerHTML = '';
                result.models.forEach(model => {
                    const sourceLabel = model.source === 'user_documents'
                        ? t('live2d.userDocuments', '用户文档')
                        : t('live2d.localUpload', '本地上传');
                    const displayName = model.name.replace(/\.model3$/i, '');
                    const safeId = 'model-' + encodeURIComponent(model.name);
                    const item = document.createElement('div');
                    item.className = 'model-item';

                    // 检查是否是当前绑定的模型
                    const isBound = currentModelInfo && currentModelInfo.name === model.name;

                    const checkbox = document.createElement('input');
                    checkbox.type = 'checkbox';
                    checkbox.id = safeId;
                    checkbox.value = model.name;
                    checkbox.setAttribute('data-path', model.path);

                    if (isBound) {
                        checkbox.disabled = true;
                        checkbox.title = t('live2d.cannotDeleteBoundModel', '无法删除当前正在使用的模型');
                    }

                    const label = document.createElement('label');
                    label.setAttribute('for', safeId);
                    label.textContent = displayName;

                    const sourceSpan = document.createElement('span');
                    sourceSpan.className = 'model-source';
                    sourceSpan.textContent = sourceLabel;

                    // 显示"使用中"标签
                    if (isBound) {
                        const boundBadge = document.createElement('span');
                        boundBadge.className = 'model-source';
                        boundBadge.style.color = 'var(--brand-blue, #4a9eff)';
                        boundBadge.style.fontWeight = 'bold';
                        boundBadge.textContent = t('live2d.modelInUse', '使用中');
                        item.appendChild(boundBadge);
                        item.style.opacity = '0.6';
                    }

                    checkbox.addEventListener('change', (e) => {
                        if (e.target.checked) {
                            selectedDeleteModels.add(e.target.value);
                        } else {
                            selectedDeleteModels.delete(e.target.value);
                        }
                        updateConfirmDeleteButton();
                    });

                    item.appendChild(checkbox);
                    item.appendChild(label);
                    item.appendChild(sourceSpan);
                    userModelList.appendChild(item);
                });
            } else {
                userModelList.innerHTML = '<div class="empty-message">' + t('live2d.noUserModels', '暂无可删除的用户模型') + '</div>';
            }
        } catch (error) {
            console.error('Failed to load user models:', error);
            userModelList.innerHTML = '<div class="empty-message">' + t('live2d.loadModelsFailed', '加载模型失败') + '</div>';
        }
    }

    function updateConfirmDeleteButton() {
        if (confirmDeleteBtn) {
            confirmDeleteBtn.disabled = selectedDeleteModels.size === 0;
            const count = selectedDeleteModels.size || 0;
            confirmDeleteBtn.textContent = t('live2d.deleteSelected', '删除选中 ({{count}})', { count: count });
        }
    }

    async function deleteSelectedModels() {
        if (selectedDeleteModels.size === 0) return;

        // 安全防护：移除当前绑定的模型，不允许删除
        if (currentModelInfo && currentModelInfo.name) {
            if (selectedDeleteModels.has(currentModelInfo.name)) {
                selectedDeleteModels.delete(currentModelInfo.name);
                showStatus(t('live2d.cannotDeleteBoundModel', '无法删除当前正在使用的模型'), 2000);
                updateConfirmDeleteButton();
                if (selectedDeleteModels.size === 0) return;
            }
        }

        const message = t('live2d.confirmDelete', '确定要删除选中的 {{count}} 个模型吗？此操作不可恢复。', { count: selectedDeleteModels.size });
        const title = t('live2d.deleteModelTitle', '删除已导入模型');
        const confirmDelete = await showConfirm(message, title, { danger: true });
        if (!confirmDelete) return;

        confirmDeleteBtn.disabled = true;
        confirmDeleteBtn.textContent = t('live2d.deleting', '删除中...');

        const currentModelName = currentModelInfo ? currentModelInfo.name : null;
        const modelsToDelete = new Set(selectedDeleteModels);
        let successCount = 0;
        let failCount = 0;
        let lastErrorMessage = '';

        for (const modelName of selectedDeleteModels) {
            try {
                // 使用 RequestHelper 确保统一的错误处理和超时
                const result = await RequestHelper.fetchJson(
                    `/api/live2d/model/${encodeURIComponent(modelName)}`,
                    {
                        method: 'DELETE'
                    }
                );
                if (result.success) {
                    successCount++;
                } else {
                    console.error(`Failed to delete model ${modelName}:`, result.error);
                    if (result && result.error) {
                        lastErrorMessage = String(result.error);
                    }
                    failCount++;
                }
            } catch (error) {
                console.error(`Error deleting model ${modelName}:`, error);
                if (error && error.message) {
                    lastErrorMessage = String(error.message);
                } else if (error) {
                    lastErrorMessage = String(error);
                }
                failCount++;
            }
        }

        await loadUserModels();
        selectedDeleteModels.clear();
        updateConfirmDeleteButton();

        try {
            // 使用 RequestHelper 确保统一的错误处理和超时
            availableModels = await RequestHelper.fetchJson('/api/live2d/models');
            modelSelect.innerHTML = `<option value="">${t('live2d.pleaseSelectModel', '选择模型')}</option>`;
            availableModels.forEach(model => {
                const option = document.createElement('option');
                option.value = model.name;
                option.textContent = model.display_name || model.name;
                // Preserve workshop item_id so it's not lost when the select is reconstructed
                if (model.item_id) {
                    option.dataset.itemId = model.item_id;
                }
                modelSelect.appendChild(option);
            });

            if (successCount > 0 && currentModelName && modelsToDelete.has(currentModelName)) {
                const maoProModel = availableModels.find(m => m.name === 'mao_pro');
                let fallbackModel = maoProModel;
                if (!fallbackModel && Array.isArray(availableModels) && availableModels.length > 0) {
                    fallbackModel = availableModels[0];
                }

                if (fallbackModel) {
                    showStatus(t('live2d.switchingToDefault', '当前模型已删除，正在切换到默认模型...'));
                    currentModelInfo = fallbackModel;
                    await loadModel(fallbackModel.name, fallbackModel, undefined);
                    await saveModelToCharacter(fallbackModel.name, fallbackModel.item_id || null);
                } else {
                    showStatus(t('live2d.noModelsFound', '未找到可用模型'));
                    currentModelInfo = null;
                }
            }
        } catch (e) {
            console.error('重新加载模型列表失败:', e);
        }

        if (successCount > 0) {
            const successMessage = t('live2d.deleteSuccess', '✓ 成功删除 {{count}} 个模型', { count: successCount }) + (failCount > 0 ? `，${t('live2d.deleteFailed', '失败 {{count}} 个', { count: failCount })}` : '');
            await showAlert(successMessage);
        } else {
            const failedPart = t('live2d.deleteFailed', '失败 {{count}} 个', { count: failCount, reason: lastErrorMessage });
            const reasonPart = lastErrorMessage ? `：${lastErrorMessage}` : '';
            await showAlert(`✗ ${failedPart}${reasonPart}`);
        }
    }

    if (deleteModelBtn) {
        deleteModelBtn.addEventListener('click', showDeleteModelModal);
    }

    if (closeDeleteModal) {
        closeDeleteModal.addEventListener('click', hideDeleteModelModal);
    }

    if (cancelDeleteBtn) {
        cancelDeleteBtn.addEventListener('click', hideDeleteModelModal);
    }

    if (confirmDeleteBtn) {
        confirmDeleteBtn.addEventListener('click', deleteSelectedModels);
    }

    if (deleteModelModal) {
        deleteModelModal.addEventListener('click', (e) => {
            if (e.target === deleteModelModal) {
                hideDeleteModelModal();
            }
        });
    }

    // 更新常驻表情选择框
    async function updatePersistentExpressionSelect() {
        const persistentSelect = document.getElementById('persistent-expression-select');
        const persistentSelectBtn = document.getElementById('persistent-expression-select-btn');
        const persistentDropdown = document.getElementById('persistent-expression-dropdown');

        if (!currentModelFiles || !currentModelFiles.expression_files) {
            persistentSelect.disabled = true;
            if (persistentSelectBtn) persistentSelectBtn.disabled = true;
            if (persistentDropdown) persistentDropdown.innerHTML = '';
            return;
        }

        // 只显示 .exp3.json 文件
        const exp3Files = currentModelFiles.expression_files.filter(file => file.endsWith('.exp3.json'));

        // 更新隐藏的 select 元素
        persistentSelect.innerHTML = `<option value="" data-i18n="live2d.selectPersistentExpression">${t('live2d.selectPersistentExpression', '选择常驻表情')}</option>`;
        exp3Files.forEach(file => {
            const option = document.createElement('option');
            option.value = file;
            const displayName = file.split('/').pop().replace('.exp3.json', '');
            option.textContent = displayName;
            persistentSelect.appendChild(option);
        });

        // 确保选择框的值是空的（因为按钮始终显示默认文字）
        persistentSelect.value = '';

        // 使用 DropdownManager 更新下拉菜单（这样会自动绑定点击事件）
        if (persistentExpressionManager) {
            persistentExpressionManager.updateDropdown();
        }

        // 启用按钮和选择器
        persistentSelect.disabled = false;
        if (persistentSelectBtn) persistentSelectBtn.disabled = false;
    }

    // 加载已配置的常驻表情
    async function loadPersistentExpressions() {
        const persistentList = document.getElementById('persistent-list');
        if (!currentModelInfo) {
            persistentList.style.display = 'none';
            return;
        }

        try {
            // 使用 RequestHelper 确保统一的错误处理和超时
            const data = await RequestHelper.fetchJson(`/api/live2d/emotion_mapping/${encodeURIComponent(currentModelInfo.name)}`);

            if (data && data.success && data.config && data.config.expressions && data.config.expressions['常驻']) {
                const persistentExpressions = data.config.expressions['常驻'];
                if (persistentExpressions && persistentExpressions.length > 0) {
                    persistentList.innerHTML = '';
                    persistentExpressions.forEach(file => {
                        const item = document.createElement('div');
                        item.className = 'persistent-item';
                        const fileName = file.split('/').pop().replace('.exp3.json', '');
                        const nameSpan = document.createElement('span');
                        nameSpan.textContent = fileName;
                        const deleteBtn = document.createElement('button');
                        deleteBtn.className = 'persistent-delete-btn';
                        deleteBtn.textContent = t('live2d.delete', '删除');
                        deleteBtn.addEventListener('click', () => removePersistentExpression(file));
                        item.appendChild(nameSpan);
                        item.appendChild(deleteBtn);
                        persistentList.appendChild(item);
                    });
                    persistentList.style.display = 'block';
                } else {
                    persistentList.style.display = 'none';
                }
            } else {
                persistentList.style.display = 'none';
            }
        } catch (e) {
            console.error('加载常驻表情失败:', e);
            persistentList.style.display = 'none';
        }
    }

    // 添加常驻表情
    const persistentSelect = document.getElementById('persistent-expression-select');
    persistentSelect.addEventListener('change', async () => {
        const selectedFile = persistentSelect.value;
        if (!selectedFile || !currentModelInfo) return;

        // 防止重复操作
        if (persistentSelect.disabled) return;
        persistentSelect.disabled = true;

        try {
            // 获取当前配置（使用 RequestHelper 确保统一的错误处理和超时）
            const data = await RequestHelper.fetchJson(`/api/live2d/emotion_mapping/${encodeURIComponent(currentModelInfo.name)}`);

            const currentConfig = data && data.success ? (data.config || { motions: {}, expressions: {} }) : { motions: {}, expressions: {} };

            // 确保expressions对象存在
            if (!currentConfig.expressions) {
                currentConfig.expressions = {};
            }

            // 确保常驻表情数组存在
            if (!currentConfig.expressions['常驻']) {
                currentConfig.expressions['常驻'] = [];
            }

            // 检查是否已存在
            if (currentConfig.expressions['常驻'].includes(selectedFile)) {
                showStatus(t('live2d.persistentExpressionExists', '该表情已添加为常驻表情'), 2000);
                persistentSelect.value = '';
                return; // 注意：这里return后会在finally中恢复disabled状态
            }

            // 添加到常驻表情列表
            currentConfig.expressions['常驻'].push(selectedFile);

            // 保存配置（使用 RequestHelper 确保统一的错误处理和超时）
            const saveData = await RequestHelper.fetchJson(
                `/api/live2d/emotion_mapping/${encodeURIComponent(currentModelInfo.name)}`,
                {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(currentConfig)
                }
            );
            if (saveData.success) {
                showStatus(t('live2d.persistentExpressionAdded', '常驻表情已添加'), 2000);
                await loadPersistentExpressions();
                persistentSelect.value = '';
                // 立即应用常驻表情到预览模型
                if (window.live2dManager) {
                    try {
                        await window.live2dManager.syncEmotionMappingWithServer({ replacePersistentOnly: true });
                        await window.live2dManager.setupPersistentExpressions();
                    } catch (e) {
                        console.warn('应用常驻表情到预览模型失败:', e);
                    }
                }
            } else {
                showStatus(t('live2d.persistentExpressionAddFailed', '添加常驻表情失败'), 2000);
                persistentSelect.value = '';
            }
        } catch (e) {
            console.error('添加常驻表情失败:', e);
            showStatus(t('live2d.persistentExpressionAddFailed', '添加常驻表情失败'), 2000);
            persistentSelect.value = '';
        } finally {
            persistentSelect.disabled = false;
        }
    });

    // 删除常驻表情
    window.removePersistentExpression = async function (file) {
        if (!currentModelInfo) return;

        try {
            // 使用 RequestHelper 确保统一的错误处理和超时
            const data = await RequestHelper.fetchJson(`/api/live2d/emotion_mapping/${encodeURIComponent(currentModelInfo.name)}`);

            const currentConfig = data && data.success ? (data.config || { motions: {}, expressions: {} }) : { motions: {}, expressions: {} };

            if (currentConfig.expressions && currentConfig.expressions['常驻']) {
                const index = currentConfig.expressions['常驻'].indexOf(file);
                if (index > -1) {
                    currentConfig.expressions['常驻'].splice(index, 1);

                    // 使用 RequestHelper 确保统一的错误处理和超时
                    const saveData = await RequestHelper.fetchJson(
                        `/api/live2d/emotion_mapping/${encodeURIComponent(currentModelInfo.name)}`,
                        {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify(currentConfig)
                        }
                    );
                    if (saveData.success) {
                        showStatus(t('live2d.persistentExpressionRemoved', '常驻表情已删除'), 2000);
                        await loadPersistentExpressions();
                        // 立即应用常驻表情变化到预览模型
                        if (window.live2dManager) {
                            try {
                                await window.live2dManager.syncEmotionMappingWithServer({ replacePersistentOnly: true });
                                await window.live2dManager.setupPersistentExpressions();
                            } catch (e) {
                                console.warn('应用常驻表情变化到预览模型失败:', e);
                            }
                        }
                    } else {
                        showStatus(t('live2d.persistentExpressionRemoveFailed', '删除常驻表情失败'), 2000);
                    }
                }
            }
        } catch (e) {
            console.error('删除常驻表情失败:', e);
            showStatus(t('live2d.persistentExpressionRemoveFailed', '删除常驻表情失败'), 2000);
        }
    };

    // 保存按钮已移除，因为表情在添加/删除时已自动保存

    // Helper functions
    function setControlsDisabled(disabled) {
        // 使用统一管理器控制选项条的启用/禁用
        if (motionManager) {
            if (disabled) motionManager.disable();
            else motionManager.enable();
        }
        if (expressionManager) {
            if (disabled) expressionManager.disable();
            else expressionManager.enable();
        }

        // 原有的控制逻辑（保留兼容性）
        motionSelect.disabled = disabled;
        const motionSelectBtn = document.getElementById('motion-select-btn');
        if (motionSelectBtn) motionSelectBtn.disabled = disabled;
        expressionSelect.disabled = disabled;
        const expressionSelectBtn = document.getElementById('expression-select-btn');
        if (expressionSelectBtn) expressionSelectBtn.disabled = disabled;
        playMotionBtn.disabled = disabled;
        playExpressionBtn.disabled = disabled;
        savePositionBtn.disabled = disabled;
        const persistentSelect = document.getElementById('persistent-expression-select');
        const persistentSelectBtn = document.getElementById('persistent-expression-select-btn');
        if (persistentSelect) persistentSelect.disabled = disabled;
        if (persistentSelectBtn) persistentSelectBtn.disabled = disabled;
    }

    function updateSelectWithOptions(select, options, defaultText, type) {
        // 根据类型设置第一个选项的文本
        let firstOptionText = defaultText;
        if (type === 'motion') {
            firstOptionText = t('live2d.selectMotion', '选择动作');
        } else if (type === 'expression') {
            firstOptionText = t('live2d.selectExpression', '选择表情');
        }

        select.innerHTML = `<option value="">${firstOptionText}</option>`;
        options.forEach(opt => {
            const option = document.createElement('option');
            option.value = opt;

            if (type === 'expression') {
                const displayName = opt.split('/').pop().replace('.exp3.json', '');
                option.textContent = displayName;
            } else if (type === 'motion') {
                const displayName = opt.split('/').pop().replace('.motion3.json', '');
                option.textContent = displayName;
            } else {
                option.textContent = opt;
            }
            select.appendChild(option);
        });

        // 更新对应的管理器
        if (type === 'motion' && motionManager) {
            motionManager.updateButtonText();
            motionManager.updateDropdown();
        } else if (type === 'expression' && expressionManager) {
            expressionManager.updateButtonText();
            expressionManager.updateDropdown();
        }
    }

    // 情绪映射加载
    async function loadEmotionMappingForModel(modelName) {
        currentEmotionMapping = null;
        try {
            // 使用 RequestHelper 确保统一的错误处理和超时
            const data = await RequestHelper.fetchJson(`/api/live2d/emotion_mapping/${encodeURIComponent(modelName)}`);
            if (data && data.success && data.config) {
                currentEmotionMapping = data.config;
            } else {
                currentEmotionMapping = { motions: {}, expressions: {} };
            }
        } catch (e) {
            currentEmotionMapping = { motions: {}, expressions: {} };
        }
    }

    // 智能检测并修正 VRM 模型朝向
    // 【强力调试版】智能检测并修正 VRM 模型朝向
    function autoCorrectVRMOrientation(vrm) {

        // 1. 检查对象是否存在
        if (!vrm) {
            console.error("【调试失败】传入的 vrm 是空的 (null/undefined)！无法检测。");
            // 尝试去 vrmManager 里找一下备用的
            if (window.vrmManager && window.vrmManager.model) {
                vrm = window.vrmManager.model;
            } else {
                return;
            }
        }

        // 2. 检查 Humanoid 组件
        if (!vrm.humanoid) {
            console.error("【调试失败】模型存在，但没有 Humanoid (人形骨骼) 组件！");
            return;
        }

        try {
            const humanoid = vrm.humanoid;
            const scene = vrm.scene;

            scene.updateMatrixWorld(true);

            const footNode = humanoid.getNormalizedBoneNode('leftFoot');
            const toesNode = humanoid.getNormalizedBoneNode('leftToes');

            if (footNode && toesNode) {
                const footPos = new THREE.Vector3();
                const toesPos = new THREE.Vector3();

                footNode.getWorldPosition(footPos);
                toesNode.getWorldPosition(toesPos);


                if (toesPos.z < footPos.z - 0.001) {
                    scene.rotation.y = Math.PI;
                } else {
                    scene.rotation.y = 0;
                }
            } else {
                console.warn('【VRM Check】⚠️ 未找到脚部骨骼 (leftFoot 或 leftToes 缺失)，无法判断。');
            }
        } catch (e) {
            console.error('【VRM Check】❌ 检测过程发生异常:', e);
        }
    }
    // 加载当前角色模型的函数
    async function loadCurrentCharacterModel() {
        try {
            // 获取角色名称
            const lanlanName = await getLanlanName();
            if (!lanlanName) {
                return;
            }

            // 获取角色配置（使用 RequestHelper 确保统一的错误处理和超时）
            const charactersData = await RequestHelper.fetchJson('/api/characters');
            const catgirlConfig = charactersData['猫娘']?.[lanlanName];

            if (!catgirlConfig) {
                return;
            }

            // 检查模型类型
            // 首先安全地检查 VRM 模型路径是否存在且有效
            let hasValidVRMPath = false;
            if (catgirlConfig.vrm !== undefined && catgirlConfig.vrm !== null) {
                const rawValue = catgirlConfig.vrm;
                if (typeof rawValue === 'string') {
                    const trimmed = rawValue.trim();
                    if (trimmed !== '' &&
                        trimmed !== 'undefined' &&
                        trimmed !== 'null' &&
                        !trimmed.includes('undefined') &&
                        !trimmed.includes('null')) {
                        hasValidVRMPath = true;
                    }
                } else {
                    const strValue = String(rawValue);
                    if (strValue !== 'undefined' && strValue !== 'null' && !strValue.includes('undefined')) {
                        hasValidVRMPath = true;
                    }
                }
            }

            // 确定模型类型：优先使用 model_type，如果没有则根据是否有有效的 VRM 路径判断
            let modelType = catgirlConfig.model_type || (hasValidVRMPath ? 'vrm' : 'live2d');

            // 如果模型类型是 VRM 但没有有效的 VRM 路径，自动修复配置
            if (modelType === 'vrm' && !hasValidVRMPath) {
                console.warn(`[模型管理] 角色 ${lanlanName} 的模型类型设置为 VRM，但 VRM 模型路径无效或未设置，自动修复为 Live2D:`, catgirlConfig.vrm);
                showStatus(t('live2d.autoFixModelType', `角色 ${lanlanName} 的模型类型配置不一致，已自动修复为 Live2D`, { name: lanlanName }), 3000);

                // 自动修复：将 model_type 改为 'live2d'（使用 RequestHelper）
                try {
                    const fixResult = await RequestHelper.fetchJson(
                        `/api/characters/catgirl/${encodeURIComponent(lanlanName)}`,
                        {
                            method: 'PUT',
                            headers: {
                                'Content-Type': 'application/json',
                            },
                            body: JSON.stringify({
                                model_type: 'live2d'
                            })
                        }
                    );
                    if (fixResult.success) {
                        // 更新本地配置对象
                        catgirlConfig.model_type = 'live2d';
                        // 修复后，将 modelType 设置为 'live2d'，继续执行后续逻辑
                        modelType = 'live2d';
                    } else {
                        console.error(`[模型管理] 自动修复配置失败:`, fixResult.error);
                        // 即使修复失败，也设置为 'live2d' 以避免后续错误
                        modelType = 'live2d';
                    }
                } catch (fixError) {
                    console.error(`[模型管理] 自动修复配置时发生错误:`, fixError);
                    // 即使修复失败，也设置为 'live2d' 以避免后续错误
                    modelType = 'live2d';
                }
            }

            // 先切换模型类型，清理旧模型资源
            await switchModelDisplay(modelType);

            // 只有当模型类型是 VRM 且存在有效的 VRM 路径时才加载
            if (modelType === 'vrm' && hasValidVRMPath) {
                // VRM 模型
                // 注意：switchModelDisplay 已经等待 loadVRMModels() 完成，此时列表已就绪

                // 安全获取 VRM 模型路径（已经验证过有效性）
                let vrmModelPath = null;
                if (catgirlConfig.vrm !== undefined && catgirlConfig.vrm !== null) {
                    const rawValue = catgirlConfig.vrm;
                    if (typeof rawValue === 'string') {
                        const trimmed = rawValue.trim();
                        // 检查是否是无效的字符串值
                        if (trimmed !== '' &&
                            trimmed.toLowerCase() !== 'undefined' &&
                            trimmed.toLowerCase() !== 'null' &&
                            !trimmed.toLowerCase().includes('undefined') &&
                            !trimmed.toLowerCase().includes('null')) {
                            vrmModelPath = trimmed;
                        }
                    } else {
                        // 非字符串类型，转换为字符串后也要验证
                        const strValue = String(rawValue);
                        const lowerStr = strValue.toLowerCase();
                        if (lowerStr !== 'undefined' && lowerStr !== 'null' && !lowerStr.includes('undefined')) {
                            vrmModelPath = strValue;
                        }
                    }
                }

                // 如果路径无效，尝试在下拉列表中根据文件名寻找匹配项并自动修复
                if (!vrmModelPath ||
                    vrmModelPath === 'undefined' ||
                    vrmModelPath === 'null' ||
                    vrmModelPath.toLowerCase().includes('undefined') ||
                    vrmModelPath.toLowerCase().includes('null')) {
                    console.warn(`[模型管理] 角色 ${lanlanName} 的 VRM 模型路径无效，尝试自动修复:`, catgirlConfig.vrm);

                    // 尝试在下拉列表中根据文件名寻找匹配项
                    if (vrmModelSelect && vrmModelSelect.options.length > 0) {
                        // 如果路径包含文件名，尝试提取文件名
                        let possibleFilename = null;
                        if (catgirlConfig.vrm && typeof catgirlConfig.vrm === 'string') {
                            const parts = catgirlConfig.vrm.split(/[/\\]/);
                            const lastPart = parts[parts.length - 1];
                            if (lastPart && lastPart !== 'undefined' && lastPart !== 'null' && lastPart.endsWith('.vrm')) {
                                possibleFilename = lastPart;
                            }
                        }

                        // 在下拉列表中查找匹配项
                        let matchedOption = null;
                        if (possibleFilename) {
                            matchedOption = Array.from(vrmModelSelect.options).find(opt => {
                                const optFilename = opt.getAttribute('data-filename');
                                const optPath = opt.getAttribute('data-path');
                                return (optFilename && optFilename === possibleFilename) ||
                                    (optPath && optPath.includes(possibleFilename));
                            });
                        }

                        if (matchedOption) {
                            const fixedPath = matchedOption.getAttribute('data-path');
                            if (fixedPath && fixedPath !== 'undefined' && fixedPath !== 'null') {
                                vrmModelPath = fixedPath;

                                // 自动修复后端配置（使用 RequestHelper 确保统一的错误处理和超时）
                                try {
                                    const fixResult = await RequestHelper.fetchJson(
                                        `/api/characters/catgirl/l2d/${encodeURIComponent(lanlanName)}`,
                                        {
                                            method: 'PUT',
                                            headers: { 'Content-Type': 'application/json' },
                                            body: JSON.stringify({
                                                model_type: 'vrm',
                                                vrm: vrmModelPath
                                            })
                                        }
                                    );
                                    if (fixResult.success) {
                                    }
                                } catch (fixError) {
                                    console.warn('[模型管理] 自动修复配置时出错:', fixError);
                                }
                            }
                        }
                    }

                    // 如果仍然无效，跳过加载
                    if (!vrmModelPath ||
                        vrmModelPath === 'undefined' ||
                        vrmModelPath === 'null' ||
                        vrmModelPath.toLowerCase().includes('undefined') ||
                        vrmModelPath.toLowerCase().includes('null')) {
                        console.warn(`[模型管理] 角色 ${lanlanName} 的 VRM 模型路径无效且无法自动修复:`, catgirlConfig.vrm);
                        showStatus(t('live2d.vrmModelPathInvalid', `角色 ${lanlanName} 的 VRM 模型路径无效，请手动选择模型`, { name: lanlanName }));
                        return;
                    }
                }

                // 更优雅的策略：设置选择器值，然后触发 change 事件
                // 让 change 事件处理程序统一处理所有加载逻辑（加载模型、启用按钮、加载动作/表情等）
                // 这样避免代码重复，也确保所有后续步骤都被正确执行

                if (vrmModelSelect) {
                    // 提取文件名用于匹配
                    const vrmFilename = vrmModelPath.split(/[/\\]/).pop();

                    // 尝试在下拉列表中找到匹配的选项（多种匹配策略）
                    const matchedOption = Array.from(vrmModelSelect.options).find(opt => {
                        if (!opt.value) return false; // 跳过空值选项
                        const optPath = opt.getAttribute('data-path') || '';
                        const optFilename = opt.getAttribute('data-filename') || '';
                        const optValue = opt.value || '';

                        // 匹配策略 1: 直接匹配 value 或 data-path
                        if (optValue === vrmModelPath || optPath === vrmModelPath) return true;

                        // 匹配策略 2: 文件名匹配（最可靠的方式）
                        if (vrmFilename && (optFilename === vrmFilename || optValue.endsWith(vrmFilename) || optPath.endsWith(vrmFilename))) return true;

                        // 匹配策略 3: 路径包含文件名
                        if (vrmFilename && (optPath.includes(vrmFilename) || optValue.includes(vrmFilename))) return true;

                        return false;
                    });

                    if (matchedOption) {
                        // 设置选择器值并触发 change 事件，让 change 事件处理程序统一处理加载逻辑
                        vrmModelSelect.value = matchedOption.value;
                        vrmModelSelect.dispatchEvent(new Event('change', { bubbles: true }));
                    } else {
                        console.warn('[模型管理] 未找到匹配的 VRM 选项:', vrmModelPath);
                        showStatus(t('live2d.vrmModelNotFound', `未在模型列表中找到 ${vrmModelPath}，请手动选择模型`, { model: vrmModelPath }));
                    }
                }
            } else {
                // Live2D 模型
                // 构建API URL，支持可选的item_id参数
                let apiUrl = '/api/characters/current_live2d_model';
                const params = new URLSearchParams();

                if (lanlanName) {
                    params.append('catgirl_name', lanlanName);
                }

                // 如果有item_id，添加到参数中
                const itemId = currentModelInfo ? currentModelInfo.item_id : null;
                if (itemId) {
                    params.append('item_id', itemId);
                }

                // 添加参数到URL
                const paramsString = params.toString();
                if (paramsString) {
                    apiUrl += `?${paramsString}`;
                }

                // 使用 RequestHelper 确保统一的错误处理和超时
                const currentModelData = await RequestHelper.fetchJson(apiUrl);

                if (!currentModelData.success) {
                    return;
                }

                const { catgirl_name, model_name, model_info } = currentModelData;

                if (model_name && model_info) {
                    // 如果角色有设置的模型，自动加载
                    showStatus(t('live2d.loadingCharacterModel', `正在加载角色 ${catgirl_name} 的模型: ${model_name}...`, { name: catgirl_name, model: model_name }));

                    // 设置模型选择器
                    currentModelInfo = model_info;
                    modelSelect.value = model_name;

                    // 更新按钮文字
                    if (typeof updateLive2DModelSelectButtonText === 'function') {
                        updateLive2DModelSelectButtonText();
                    }

                    // 加载模型
                    await loadModel(model_name, model_info, model_info.item_id);

                    showStatus(t('live2d.modelLoaded', `已加载角色 ${catgirl_name} 的模型: ${model_name}`, { name: catgirl_name, model: model_name }));
                } else {
                    // 如果角色没有设置模型，显示提示信息
                    showStatus(t('live2d.modelNotSet', `角色 ${catgirl_name} 未设置模型，请手动选择`, { name: catgirl_name }));
                }
            }
        } catch (error) {
            console.error('加载当前角色模型失败:', error);
            showStatus(t('live2d.loadCurrentModelFailed', '加载当前角色模型失败'));
        }
    }

    // 所有事件监听器已注册，现在可以安全地加载当前角色模型
    // 这样 VRM 的 change 事件处理程序才能正确执行
    await loadCurrentCharacterModel();

    // 如果已自动加载了一个模型，确保在下拉框中选中它
    // 这是双重保险：防止 loadCurrentCharacterModel() 内部设置失败
    if (currentModelInfo && currentModelInfo.name) {
        const exists = availableModels.some(m => m.name === currentModelInfo.name);
        if (exists && modelSelect.value !== currentModelInfo.name) {
            modelSelect.value = currentModelInfo.name;
        }
    }
});

// 监听页面卸载事件，确保返回时主界面可见
window.addEventListener('beforeunload', (e) => {
    // 尝试退出全屏
    if (isFullscreen()) {
        try {
            exitFullscreen();
        } catch (err) {
            console.log('退出全屏失败:', err);
        }
    }

    if (window.opener) {
        sendMessageToMainPage('show_main_ui');
    }

});

// 确保在页面关闭时也恢复主界面
window.addEventListener('unload', () => {
    // 页面卸载时不需要再次发送消息
});
