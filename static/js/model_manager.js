(async function initVRMModules() {
    const loadModules = async () => {
        console.log(window.t ? window.t('modelManager.vrmLoadingDependencies') : '[VRM] 开始加载依赖模块');

        // 提前设置加载中标志，防止 vrm-init.js 加载时其内部 IIFE 再次触发模块加载
        // 注意：不能用 vrmModuleLoaded，因为下游 waitForVRM 会误判为已完成
        window._vrmModulesLoading = true;

        // avatar-popup-common, avatar-ui-popup, avatar-ui-popup-config, avatar-ui-buttons
        // 已由 model_manager.html 静态 <script> 加载，此处不再重复加载
        const vrmModules = [
            '/static/vrm-orientation.js',
            '/static/vrm-core.js',
            '/static/vrm-expression.js',
            '/static/vrm-animation.js',
            '/static/vrm-interaction.js',
            '/static/vrm-cursor-follow.js',
            '/static/vrm-manager.js',
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

// ====================== MMD 模块动态加载 ======================
(async function initMMDModules() {
    const loadModules = async () => {
        console.log('[MMD] 开始加载依赖模块');
        window._mmdModulesLoading = true;

        // avatar-popup-common, avatar-ui-popup, avatar-ui-popup-config, avatar-ui-buttons
        // 已由 model_manager.html 静态 <script> 加载，此处不再重复加载
        const mmdModules = [
            '/static/mmd-core.js',
            '/static/mmd-animation.js',
            '/static/mmd-expression.js',
            '/static/mmd-interaction.js',
            '/static/mmd-cursor-follow.js',
            '/static/mmd-manager.js',
            '/static/mmd-ui-buttons.js',
            '/static/mmd-init.js'
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
            console.error('[MMD] 以下模块加载失败:', failedModules);
        } else {
            window.mmdModuleLoaded = true;
            window.dispatchEvent(new CustomEvent('mmd-modules-ready'));
        }
    };

    if (typeof window.THREE === 'undefined') {
        window.addEventListener('three-ready', loadModules, { once: true });
    } else {
        loadModules();
    }
})();

// ===== 全局错误捕获：在页面状态栏显示错误信息 =====
window.addEventListener('error', (event) => {
    // 忽略浏览器扩展/Electron IPC 的已知无害错误
    const msg = event.message || '';
    if (msg.includes('message channel closed') || msg.includes('Extension context invalidated')) return;
    console.error('[model_manager] 全局错误:', event.error || msg);
    const statusSpan = document.getElementById('status-text');
    if (statusSpan) statusSpan.textContent = `初始化错误: ${msg}`;
});
window.addEventListener('unhandledrejection', (event) => {
    const reason = event.reason?.message || String(event.reason || '');
    // 忽略浏览器扩展/Electron IPC 的已知无害错误
    if (reason.includes('message channel closed') || reason.includes('Extension context invalidated')) return;
    console.error('[model_manager] 未处理的 Promise 拒绝:', event.reason);
    const statusSpan = document.getElementById('status-text');
    if (statusSpan) statusSpan.textContent = `异步错误: ${reason}`;
});

// ===== 选项条统一管理器 =====
/**
 * 选项条统一管理器
 * 封装所有选项条的通用功能，减少重复代码
 */
class DropdownManager {
    static instances = [];

    static getVisualWidth(str) {
        let width = 0;
        for (const char of str) {
            width += char.charCodeAt(0) > 127 ? 2 : 1;
        }
        return width;
    }

    static truncateText(text, maxVisualWidth) {
        if (!text || DropdownManager.getVisualWidth(text) <= maxVisualWidth) {
            return text;
        }
        let truncated = '';
        let currentWidth = 0;
        for (const char of text) {
            const charWidth = char.charCodeAt(0) > 127 ? 2 : 1;
            if (currentWidth + charWidth > maxVisualWidth - 3) break;
            truncated += char;
            currentWidth += charWidth;
        }
        return truncated + '...';
    }

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
        let fullText = null;

        // 如果配置了 alwaysShowDefault，始终显示默认文字
        if (this.config.alwaysShowDefault) {
            text = defaultText;
        } else if (this.select) {
            if (this.select.value) {
                const selectedOption = this.select.options[this.select.selectedIndex];
                if (selectedOption) {
                    text = this.config.getText(selectedOption);
                    fullText = text;
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

        const maxVisualWidth = this.config.maxVisualWidth || 13;
        const displayText = DropdownManager.truncateText(text, maxVisualWidth);

        this.textSpan.textContent = displayText;
        this.textSpan.setAttribute('data-text', displayText);

        if (this.button) {
            if (fullText && fullText !== defaultText) {
                this.button.title = fullText;
                this.button.removeAttribute('data-i18n-title');
            } else {
                const titleText = this.config.iconAltKey && window.t ? window.t(this.config.iconAltKey) : this.config.iconAlt;
                this.button.title = titleText;
            }
        }
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

            // Steam 徽章放在最前面
            if (option.dataset.itemId) {
                const steamBadge = document.createElement('span');
                steamBadge.className = 'steam-badge';
                steamBadge.textContent = 'Steam';
                item.appendChild(steamBadge);
            }

            // 添加 VRM/MMD 子类型徽章
            const subType = option.getAttribute('data-sub-type');
            if (subType === 'vrm') {
                const badge = document.createElement('span');
                badge.className = 'vrm-badge';
                badge.textContent = 'VRM';
                item.appendChild(badge);
            } else if (subType === 'mmd') {
                const badge = document.createElement('span');
                badge.className = 'mmd-badge';
                badge.textContent = 'MMD';
                item.appendChild(badge);
            }

            const textSpan = document.createElement('span');
            textSpan.className = 'dropdown-item-text';
            textSpan.textContent = text;
            textSpan.setAttribute('data-text', text);
            item.appendChild(textSpan);

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
        
        // 检测是否显示滚动条
        this._scrollbarRafId = requestAnimationFrame(() => {
            if (this.dropdown && this.dropdown.style.display === 'block') {
                if (this.dropdown.scrollHeight > this.dropdown.clientHeight) {
                    this.dropdown.classList.add('has-scrollbar');
                } else {
                    this.dropdown.classList.remove('has-scrollbar');
                }
            }
        });
    }

    hideDropdown() {
        if (this._scrollbarRafId) {
            cancelAnimationFrame(this._scrollbarRafId);
            this._scrollbarRafId = null;
        }
        if (this.dropdown) {
            this.dropdown.style.display = 'none';
            this.dropdown.classList.remove('has-scrollbar');
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
        const live2dManager = window.live2dManager;
        let previewSuspendReason = null;
        let resumePreviewSuspend = () => {};

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

// 全局辅助：从待机动作多选容器获取已勾选 URL 列表（供快照使用）
function _getSelectedIdleAnimationsGlobal(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return [];
    return Array.from(container.querySelectorAll('.idle-animation-options input[type="checkbox"]:checked'))
        .map(cb => cb.value)
        .filter(Boolean);
}

// 采集当前所有可保存设置的快照（模型选择 + 打光 + 待机动作）
function captureSettingsSnapshot() {
    const modelSelect = document.getElementById('model-select');
    const vrmModelSelect = document.getElementById('vrm-model-select');
    return {
        modelType: typeof currentModelType !== 'undefined' ? currentModelType : '',
        live2d: modelSelect ? modelSelect.value : '',
        live3d: vrmModelSelect ? vrmModelSelect.value : '',
        // VRM 打光
        ambient: document.getElementById('ambient-light-slider')?.value ?? '',
        mainLight: document.getElementById('main-light-slider')?.value ?? '',
        exposure: document.getElementById('exposure-slider')?.value ?? '',
        toneMapping: document.getElementById('tonemapping-select')?.value ?? '',
        outlineWidth: document.getElementById('vrm-outline-width-slider')?.value ?? '',
        // MMD 打光
        mmdAmbientIntensity: document.getElementById('mmd-ambient-intensity-slider')?.value ?? '',
        mmdAmbientColor: document.getElementById('mmd-ambient-color-picker')?.value ?? '',
        mmdDirectionalIntensity: document.getElementById('mmd-directional-intensity-slider')?.value ?? '',
        mmdDirectionalColor: document.getElementById('mmd-directional-color-picker')?.value ?? '',
        mmdExposure: document.getElementById('mmd-exposure-slider')?.value ?? '',
        mmdToneMapping: document.getElementById('mmd-tonemapping-select')?.value ?? '',
        mmdOutline: String(document.getElementById('mmd-outline-toggle')?.checked ?? false),
        // 待机动作（多选，序列化为 JSON 数组）
        idleAnimation: JSON.stringify(_getSelectedIdleAnimationsGlobal('vrm-idle-animation-multiselect')),
        mmdIdleAnimation: JSON.stringify(_getSelectedIdleAnimationsGlobal('mmd-idle-animation-multiselect')),
    };
}

// 比较两个快照是否一致
function snapshotsEqual(a, b) {
    if (!a || !b) return false;
    return Object.keys(a).every(k => String(a[k]) === String(b[k]));
}

// 仅当本页确实保存过配置时，才触发主界面重载（避免退出就把主界面模型/位置”复位”）
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
     * 验证模型路径是否有效
     * 拒绝 undefined/null 字符串、空值、以及包含 'undefined'/'null' 的字符串
     * @param {*} path - 原始路径值
     * @returns {string} 验证后的字符串，无效时返回空字符串
     */
    validatePath(path) {
        if (path === undefined || path === null) return '';
        if (typeof path !== 'string') {
            path = String(path);
        }
        const trimmed = path.trim();
        if (trimmed === '') return '';
        if (trimmed === 'undefined' || trimmed === 'null') return '';
        if (trimmed.toLowerCase().includes('undefined') || trimmed.toLowerCase().includes('null')) return '';
        return trimmed;
    },

    /**
     * 从模型数据中提取有效的 VRM 路径
     * @param {Object} model - 模型数据对象
     * @returns {Object} { path: string, isValid: boolean, filename: string }
     */
    extractVrmPath(model) {
        if (!model || typeof model !== 'object') {
            return { path: '', isValid: false, filename: '' };
        }

        // 优先检查 url 字段
        let validPath = this.validatePath(model.url);
        if (validPath) {
            return { path: validPath, isValid: true, filename: model.filename || '' };
        }

        // 检查 path 字段
        validPath = this.validatePath(model.path);
        if (validPath) {
            return { path: validPath, isValid: true, filename: model.filename || '' };
        }

        // 如果都没有，但有 filename，根据 location 构建路径
        const validFilename = this.validatePath(model.filename);
        if (validFilename) {
            const builtPath = model.location === 'project'
                ? `/static/vrm/${validFilename}`
                : `/user_vrm/${validFilename}`;
            return { path: builtPath, isValid: true, filename: validFilename };
        }

        return { path: '', isValid: false, filename: '' };
    },

    /**
     * 标准化模型路径
     * 处理 Windows 反斜杠、/user_vrm/ 前缀和本地文件路径
     * @param {string} rawPath - 原始路径
     * @param {string} type - 类型：'model' 或 'animation'（默认 'model'）
     * @returns {string} 标准化后的路径
     */
    normalizeModelPath(rawPath, type = 'model') {
        const path = this.validatePath(rawPath);
        if (!path) return '';

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
    console.log('[模型管理] DOMContentLoaded 开始初始化');

    // ═══ 早期绑定"返回主页"按钮，确保即使初始化失败也能导航 ═══
    const _earlyBackBtn = document.getElementById('backToMainBtn');
    const _earlyBackHandler = () => {
        if (window.opener && !window.opener.closed) {
            window.close();
        } else {
            window.location.href = '/';
        }
    };
    if (_earlyBackBtn) {
        _earlyBackBtn.addEventListener('click', _earlyBackHandler);
    }

  try {
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
    const mmdFileUpload = document.getElementById('mmd-file-upload');
    const mmdAnimationFileUpload = document.getElementById('mmd-animation-file-upload');
    const mmdModelSelect = document.getElementById('mmd-model-select');
    const mmdModelSelectBtn = document.getElementById('mmd-model-select-btn');
    const mmdModelSelectText = document.getElementById('mmd-model-select-text');
    const mmdModelDropdown = document.getElementById('mmd-model-dropdown');
    const mmdAnimationSelect = document.getElementById('mmd-animation-select');
    const mmdAnimationSelectBtn = document.getElementById('mmd-animation-select-btn');
    const mmdAnimationSelectText = document.getElementById('mmd-animation-select-text');
    const mmdAnimationDropdown = document.getElementById('mmd-animation-dropdown');
    const playMmdAnimationBtn = document.getElementById('play-mmd-animation-btn');
    const uploadMmdAnimationBtn = document.getElementById('upload-mmd-animation-btn');
    const deleteMmdAnimationBtn = document.getElementById('delete-mmd-animation-btn');
    const mmdAnimationActionsGroup = document.getElementById('mmd-animation-actions-group');
    const mmdModelGroup = document.getElementById('mmd-model-group');
    const mmdAnimationGroup = document.getElementById('mmd-animation-group');
    const mmdContainer = document.getElementById('mmd-container');
    // VRM/MMD 专属设置区域 DOM 引用
    const vrmSettingsSection = document.getElementById('vrm-settings-section');
    const mmdSettingsSection = document.getElementById('mmd-settings-section');
    // VRM 鼠标跟踪已移至 popup-ui 统一控制，不在外观管理页单独配置
    // MMD 光照
    const mmdAmbientIntensitySlider = document.getElementById('mmd-ambient-intensity-slider');
    const mmdAmbientColorPicker = document.getElementById('mmd-ambient-color-picker');
    const mmdDirectionalIntensitySlider = document.getElementById('mmd-directional-intensity-slider');
    const mmdDirectionalColorPicker = document.getElementById('mmd-directional-color-picker');
    // MMD 渲染
    const mmdTonemappingSelect = document.getElementById('mmd-tonemapping-select');
    const mmdExposureSlider = document.getElementById('mmd-exposure-slider');
    const mmdOutlineToggle = document.getElementById('mmd-outline-toggle');
    // MMD 待机动作（多选）
    const mmdIdleAnimationMultiselect = document.getElementById('mmd-idle-animation-multiselect');
    const mmdIdleAnimationGroup = document.getElementById('mmd-idle-animation-group');
    // 像素比例、物理模拟、拟真强度、头部跟踪 已移至 popup-ui 统一控制，不在外观管理页单独配置
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
    let isMmdAnimationPlaying = false; // 跟踪MMD手动预览动画播放状态
    let isMmdIdlePlaying = false; // 跟踪MMD待机动画播放状态（与手动预览分离）
    let isMmdAnimationUploading = false; // 防止VMD动画重复上传

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
    let mmdAnimationManager = null;

    // 防抖/合并刷新标志
    let isRefreshScheduled = false;

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
                    if (!animationsLoaded && currentModelType === 'live3d') {
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

        if (!mmdAnimationManager) {
            mmdAnimationManager = new DropdownManager({
                buttonId: 'mmd-animation-select-btn',
                selectId: 'mmd-animation-select',
                dropdownId: 'mmd-animation-dropdown',
                textSpanId: 'mmd-animation-select-text',
                iconClass: 'mmd-animation-select-icon',
                iconSrc: '/static/icons/motion_select_icon.png?v=1',
                defaultText: '选择VMD动画',
                defaultTextKey: 'live2d.mmdAnimation.selectAnimation',
                iconAlt: '选择VMD动画',
                shouldSkipOption: (option) => {
                    return option.value === '' && (
                        option.textContent.includes('请先加载') ||
                        option.textContent.includes('没有动画') ||
                        option.textContent.includes('Select')
                    );
                },
                onChange: () => {
                    if (typeof updateMMDAnimationSelectButtonText === 'function') {
                        updateMMDAnimationSelectButtonText();
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

    // 暴露模型更新函数到全局作用域，供其他模块调用
    window.updateLive2DModelDropdown = function() {
        if (live2dModelManager) {
            live2dModelManager.updateDropdown();
        }
    };

    window.updateLive2DModelSelectButtonText = function() {
        if (live2dModelManager) {
            live2dModelManager.updateButtonText();
        }
    };

    // 刷新模型下拉菜单和按钮文字（合并每帧多次调用）
    function scheduleRefresh() {
        if (isRefreshScheduled) {
            return;
        }
        isRefreshScheduled = true;
        requestAnimationFrame(() => {
            try {
                if (live2dModelManager) {
                    live2dModelManager.updateDropdown();
                    live2dModelManager.updateButtonText();
                }
            } catch (e) {
                console.warn('[model_manager] 刷新模型列表失败:', e);
            } finally {
                isRefreshScheduled = false;
            }
        });
    }

    // 监听模型扫描完成事件，刷新模型列表（具有容错能力）
    window.addEventListener('modelsScanned', function(event) {
        console.log('[model_manager] 收到模型扫描完成事件，刷新模型列表');
        scheduleRefresh();
    });


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
    let currentModelType = 'live2d'; // 'live2d' or 'live3d'
    let currentLive3dSubType = ''; // 'vrm' or 'mmd' — 当 currentModelType === 'live3d' 时有效
    let vrmManager = null;
    let vrmAnimations = []; // VRM 动作列表
    let animationsLoaded = false; // 标记VRM动作列表是否已加载
    let mmdModels = []; // MMD 模型列表
    let mmdAnimations = []; // MMD 动画列表
    let _mmdSettingsLoadPromise = null; // 追踪进行中的 MMD 设置加载 Promise

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

    try {
        if (!window.live2dManager) {
            throw new Error('Live2DManager 未初始化');
        }
        await window.live2dManager.ensurePIXIReady('live2d-canvas', 'live2d-container');
        showStatus(t('live2d.pixiInitialized', 'PIXI 初始化完成'));
    } catch (pixiError) {
        console.error('[模型管理] PIXI 初始化失败:', pixiError);
        showStatus(t('live2d.pixiInitFailed', `PIXI 初始化失败: ${pixiError.message}`));
    }

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
    let savedModelType = localStorage.getItem('modelType') || 'live2d';
    // 兼容旧值：'vrm' 已合并到 'live3d'
    if (savedModelType === 'vrm') savedModelType = 'live3d';
    let savedSubType = localStorage.getItem('live3dSubType') || '';
    console.log('[模型管理] 切换显示模式:', savedModelType, savedSubType);
    try {
        await switchModelDisplay(savedModelType, savedSubType);
    } catch (switchError) {
        console.error('[模型管理] 切换模型显示模式失败:', switchError);
        showStatus(t('live2d.switchDisplayFailed', `切换显示模式失败: ${switchError.message}`), 3000);
    }

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
            if (currentModelType === 'live3d') {
                if (!modelName ||
                    modelName === 'undefined' ||
                    modelName === 'null' ||
                    (typeof modelName === 'string' && (
                        modelName.trim() === '' ||
                        modelName.toLowerCase().includes('undefined') ||
                        modelName.toLowerCase().includes('null')
                    ))) {
                    console.error('[模型管理] 检测到无效的模型路径，尝试自动修复:', modelName);

                    if (currentModelInfo && currentModelInfo.path &&
                        currentModelInfo.path !== 'undefined' &&
                        currentModelInfo.path !== 'null' &&
                        !currentModelInfo.path.toLowerCase().includes('undefined')) {
                        modelName = currentModelInfo.path;
                    } else if (currentModelInfo && currentModelInfo.name &&
                        currentModelInfo.name !== 'undefined' &&
                        currentModelInfo.name !== 'null' &&
                        !currentModelInfo.name.toLowerCase().includes('undefined')) {
                        const isMmdFallback = currentLive3dSubType === 'mmd' ||
                            (currentModelInfo.type === 'mmd') ||
                            currentModelInfo.name.toLowerCase().endsWith('.pmx') ||
                            currentModelInfo.name.toLowerCase().endsWith('.pmd');
                        if (isMmdFallback) {
                            modelName = currentModelInfo.name;
                        } else {
                            const filename = currentModelInfo.name.endsWith('.vrm')
                                ? currentModelInfo.name
                                : `${currentModelInfo.name}.vrm`;
                            modelName = ModelPathHelper.normalizeModelPath(filename, 'model');
                        }
                    } else {
                        const errorMsg = t('live2d.vrmModelPathInvalid', '模型路径无效，无法保存。请重新选择模型。');
                        showStatus(errorMsg, 5000);
                        throw new Error('模型路径无效: ' + modelName);
                    }
                }
            }

            showStatus(t('live2d.savingSettings', '正在保存设置...'));

            // 2. 构建模型数据，使用专用接口保存
            const modelData = {
                model_type: currentModelType,
            };

            if (currentModelType === 'live3d') {
                // Live3D 模式：根据子类型（VRM 或 MMD）分别构建数据
                const selectedOpt = vrmModelSelect && vrmModelSelect.options[vrmModelSelect.selectedIndex];
                const subType = selectedOpt ? selectedOpt.getAttribute('data-sub-type') : null;
                const modelExt = modelName ? modelName.toLowerCase() : '';
                const isMmdModel = currentLive3dSubType === 'mmd' ||
                    subType === 'mmd' ||
                    modelExt.endsWith('.pmx') || modelExt.endsWith('.pmd') ||
                    (currentModelInfo && currentModelInfo.type === 'mmd');

                if (isMmdModel) {
                    // MMD 子类型：构建 MMD 路径（后端读取 data.get('mmd')）
                    // 优先级: selectedOpt.data-path > currentModelInfo.path > modelName(仅文件名，兜底)
                    let mmdPath = (selectedOpt && selectedOpt.getAttribute('data-sub-type') === 'mmd' && selectedOpt.getAttribute('data-path'))
                        || (currentModelInfo && currentModelInfo.path)
                        || modelName;
                    if (mmdPath && typeof mmdPath === 'string') {
                        const urlMatch = mmdPath.match(/^(?:http|https):\/\/[^/]+(\/user_mmd\/.*|\/static\/mmd\/.*)/);
                        if (urlMatch) mmdPath = urlMatch[1];
                    }
                    modelData.mmd = mmdPath;
                    if (mmdAnimationSelect && mmdAnimationSelect.value) {
                        modelData.mmd_animation = mmdAnimationSelect.value;
                    }
                    const mmdIdleUrls = getSelectedIdleAnimations('mmd-idle-animation-multiselect');
                    modelData.mmd_idle_animation = mmdIdleUrls;
                } else {
                    // VRM 子类型：转换 VRM 路径（从完整 HTTP 路径转换为相对路径）
                    let vrmPath = (selectedOpt && selectedOpt.getAttribute('data-sub-type') !== 'mmd' && selectedOpt.getAttribute('data-path'))
                        || modelName;
                    if (vrmPath && typeof vrmPath === 'string') {
                        const urlMatch = vrmPath.match(/^(?:http|https):\/\/[^/]+(\/user_vrm\/.*|\/static\/vrm\/.*)/);
                        if (urlMatch) vrmPath = urlMatch[1];
                        if (!vrmPath.startsWith('/user_vrm/') && !vrmPath.startsWith('/static/vrm/')) {
                            if (currentModelInfo && currentModelInfo.path) {
                                const infoPathMatch = currentModelInfo.path.match(/^(?:http|https):\/\/[^/]+(\/user_vrm\/.*|\/static\/vrm\/.*)/);
                                if (infoPathMatch) vrmPath = infoPathMatch[1];
                                else if (currentModelInfo.path.startsWith('/user_vrm/') || currentModelInfo.path.startsWith('/static/vrm/')) {
                                    vrmPath = currentModelInfo.path;
                                }
                            }
                        }
                    }
                    modelData.vrm = vrmPath;
                    if (vrmAnimation) {
                        modelData.vrm_animation = vrmAnimation;
                    }
                    const vrmIdleUrls = getSelectedIdleAnimations('vrm-idle-animation-multiselect');
                    modelData.idle_animation = vrmIdleUrls;
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

            // 4. 如果是 VRM/Live3D 模式，单独保存光照设置（仅光照部分独立保存）
            if ((currentModelType === 'live3d') && ambient && main) {
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
                const outlineWidthSlider = document.getElementById('vrm-outline-width-slider');
                if (outlineWidthSlider) {
                    lightingData.lighting.outlineWidthScale = parseFloat(outlineWidthSlider.value);
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

            // 5. 如果是 MMD 模式，保存MMD专属设置
            let mmdSettingsResult = null;
            if (currentModelType === 'live3d' && currentLive3dSubType === 'mmd') {
                try {
                    if (_mmdSettingsLoadPromise) {
                        await _mmdSettingsLoadPromise;
                    }
                    const collected = collectMmdSettings();
                    const existing = JSON.parse(localStorage.getItem('mmdSettings') || '{}');
                    if (collected.lighting) existing.lighting = collected.lighting;
                    if (collected.rendering) {
                        existing.rendering = Object.assign(existing.rendering || {}, collected.rendering);
                    }
                    mmdSettingsResult = await RequestHelper.fetchJson(
                        `/api/characters/catgirl/${encodeURIComponent(lanlanName)}/mmd_settings`,
                        {
                            method: 'PUT',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify(existing)
                        }
                    );
                } catch (e) {
                    console.warn('保存MMD设置失败:', e);
                    mmdSettingsResult = { success: false, error: e.message };
                }
            }

            let modelDisplayName = (currentModelType === 'live3d')
                ? modelName.split(/[\\/]/).pop().replace(/\.(vrm|pmx|pmd)$/i, '') 
                : modelName;
            let saveMessage;
            const lightingFailed = (currentModelType === 'live3d') && ambient && main && (!lightingResult || !lightingResult.success);
            const mmdSettingsFailed = mmdSettingsResult && !mmdSettingsResult.success;

            if (lightingFailed && mmdSettingsFailed) {
                saveMessage = t('live2d.modelSavedLightingFailed', `已保存模型设置，光照和MMD设置保存失败`, { name: modelDisplayName });
            } else if (mmdSettingsFailed) {
                saveMessage = t('live2d.modelSavedMmdSettingsFailed', `已保存模型设置，MMD设置保存失败`, { name: modelDisplayName });
            } else if (lightingFailed) {
                saveMessage = t('live2d.modelSavedLightingFailed', `已保存模型设置，光照设置保存失败`, { name: modelDisplayName });
            } else if ((currentModelType === 'live3d') && ambient && main) {
                saveMessage = t('live2d.modelSettingsSavedWithLighting', `已保存模型和光照设置`, { name: modelDisplayName });
            } else if (currentModelType === 'live3d') {
                saveMessage = t('live2d.modelSettingsSaved', `已保存模型设置`, { name: modelDisplayName });
            } else {
                saveMessage = t('live2d.modelSettingsSaved', `已保存模型设置`, { name: modelDisplayName });
            }
            showStatus(saveMessage, mmdSettingsFailed || lightingFailed ? 3000 : 2000);
            return !mmdSettingsFailed && !lightingFailed;

        } catch (error) {
            console.error('保存模型设置失败:', error);
            showStatus(t('live2d.saveFailed', `保存失败: ${error.message}`), 3000);
            return false;
        }
    }

    function cancelExpressionPreview({ reapplyPersistent = true } = {}) {
        if (window._expressionPreviewRestoreTimer) {
            clearTimeout(window._expressionPreviewRestoreTimer);
            window._expressionPreviewRestoreTimer = null;
        }
        if (typeof window._resumeExpressionPreviewSuspend === 'function') {
            window._resumeExpressionPreviewSuspend(reapplyPersistent);
            window._resumeExpressionPreviewSuspend = null;
        }
        window._currentExpressionPreviewToken = null;
    }

    function nextExpressionPreviewToken() {
        window._expressionPreviewTokenSeq = (window._expressionPreviewTokenSeq || 0) + 1;
        return window._expressionPreviewTokenSeq;
    }

    // 模型类型切换处理
    // subType: 当 type === 'live3d' 时，传入 'vrm' 或 'mmd' 以区分子类型
    async function switchModelDisplay(type, subType) {
        cancelExpressionPreview({ reapplyPersistent: true });
        currentModelType = type;
        if (type === 'live3d' && subType) {
            currentLive3dSubType = subType;
        } else if (type !== 'live3d') {
            currentLive3dSubType = '';
        }
        localStorage.setItem('modelType', type);
        if (currentLive3dSubType) {
            localStorage.setItem('live3dSubType', currentLive3dSubType);
        }
        if (modelTypeSelect) modelTypeSelect.value = type;

        const sidebar = document.getElementById('sidebar');
        if (sidebar) {
            sidebar.classList.toggle('mode-live2d', type === 'live2d');
            sidebar.classList.toggle('mode-vrm', type === 'live3d');
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
            // 隐藏 MMD 容器和控件组，并停止播放中的动画
            stopIdleRotation('mmd');
            if (window.mmdManager) {
                window.mmdManager.stopAnimation();
            }
            isMmdAnimationPlaying = false;
            isMmdIdlePlaying = false;
            updateMMDAnimationPlayButtonIcon();
            if (playMmdAnimationBtn) playMmdAnimationBtn.disabled = true;
            if (mmdContainer) {
                mmdContainer.classList.add('hidden');
                mmdContainer.style.display = 'none';
            }
            if (mmdModelGroup) mmdModelGroup.style.display = 'none';
            if (mmdAnimationGroup) mmdAnimationGroup.style.display = 'none';
            const mmdAnimationActionsGroup = document.getElementById('mmd-animation-actions-group');
            if (mmdAnimationActionsGroup) mmdAnimationActionsGroup.style.display = 'none';
            // 显示 Live2D 特有的控件
            document.querySelectorAll('.control-group').forEach(group => {
                if (group.id !== 'live2d-model-group' &&
                    group.id !== 'vrm-model-group' &&
                    group.id !== 'vrm-expression-group' &&
                    group.id !== 'vrm-animation-group' &&
                    group.id !== 'mmd-model-group' &&
                    group.id !== 'mmd-animation-group' &&
                    group.id !== 'mmd-animation-actions-group') {
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
            // 隐藏 Live3D 情感配置按钮（Live2D模式下）
            const live3dEmotionConfigGroup = document.getElementById('live3d-emotion-config-group');
            if (live3dEmotionConfigGroup) {
                live3dEmotionConfigGroup.style.display = 'none';
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
            stopIdleRotation('vrm');
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
            // 隐藏 VRM/MMD 设置面板（Live2D模式下不显示）
            const vrmSettingsSec = document.getElementById('vrm-settings-section');
            if (vrmSettingsSec) vrmSettingsSec.style.display = 'none';
            const mmdSettingsSec = document.getElementById('mmd-settings-section');
            if (mmdSettingsSec) mmdSettingsSec.style.display = 'none';
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
            // VRM 表情组仅在 VRM 子类型时显示（MMD 子类型时隐藏）
            if (vrmExpressionGroup) vrmExpressionGroup.style.display = (currentLive3dSubType !== 'mmd') ? 'flex' : 'none';
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
            const live2dOnlyControls = ['motion-select', 'expression-select', 'play-motion-btn', 'play-expression-btn','touch_set'];
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
            // 显示 Live3D 情感配置按钮（VRM/MMD 统一）
            const live3dEmotionConfigGroup = document.getElementById('live3d-emotion-config-group');
            if (live3dEmotionConfigGroup) {
                live3dEmotionConfigGroup.style.display = 'flex';
            }
            // 隐藏常驻表情组（VRM模式下不需要）
            const persistentExpressionGroup = document.getElementById('persistent-expression-group');
            if (persistentExpressionGroup) persistentExpressionGroup.style.display = 'none';
            // 保存设置按钮现在在情感配置组中，不需要单独显示
            // VRM 专属控件：仅在 VRM 子类型时显示
            const isVrmSubType = !currentLive3dSubType || currentLive3dSubType === 'vrm';
            // 显示 VRM 动作预览组
            if (vrmAnimationGroup) vrmAnimationGroup.style.display = isVrmSubType ? 'flex' : 'none';
            // 显示 VRM 打光设置组
            const vrmLightingGroup = document.getElementById('vrm-lighting-group');
            if (vrmLightingGroup) vrmLightingGroup.style.display = isVrmSubType ? 'flex' : 'none';
            // 显示 VRM 待机动作设置组（独立于打光）
            const vrmIdleAnimGroup = document.getElementById('vrm-idle-animation-group');
            if (vrmIdleAnimGroup) vrmIdleAnimGroup.style.display = isVrmSubType ? 'flex' : 'none';
            // 切换到 VRM 模式时立即加载待机动作列表
            if (isVrmSubType) {
                console.log('[VRM IdleAnimation] 切换到 VRM 模式，开始加载待机动作列表');
                loadIdleAnimationOptions();
            }
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

            // 加载模型列表
            if (type === 'live3d') {
                // Live3D 模式：加载合并的 VRM+MMD 模型列表
                try {
                    await loadLive3DModels();
                } catch (error) {
                    console.error('加载Live3D模型列表失败:', error);
                }
                // MMD 模型选择器隐藏（已合并到 VRM 选择器中）
                const mmdModelGroup = document.getElementById('mmd-model-group');
                if (mmdModelGroup) mmdModelGroup.style.display = 'none';

                // ===== VRM/MMD 子类型控件分离 =====
                const effectiveSubType = currentLive3dSubType || 'vrm';
                const vrmSettingsSection = document.getElementById('vrm-settings-section');
                const mmdSettingsSection = document.getElementById('mmd-settings-section');

                if (effectiveSubType === 'mmd') {
                    // MMD 子类型：显示 MMD 专属控件，隐藏 VRM 专属控件
                    if (vrmSettingsSection) vrmSettingsSection.style.display = 'none';
                    if (mmdSettingsSection) mmdSettingsSection.style.display = 'block';
                    // MMD 动画选择器显示
                    const mmdAnimationGroup = document.getElementById('mmd-animation-group');
                    if (mmdAnimationGroup) mmdAnimationGroup.style.display = 'flex';
                    // MMD 动画上传/删除按钮显示
                    const mmdAnimationActionsGroup = document.getElementById('mmd-animation-actions-group');
                    if (mmdAnimationActionsGroup) mmdAnimationActionsGroup.style.display = 'flex';
                    // MMD 待机动作选择器显示
                    const mmdIdleAnimGroup = document.getElementById('mmd-idle-animation-group');
                    if (mmdIdleAnimGroup) mmdIdleAnimGroup.style.display = 'block';
                    try {
                        await loadMMDAnimations();
                    } catch (error) {
                        console.error('加载MMD动画列表失败:', error);
                    }
                    // 加载MMD待机动作选项
                    await loadMmdIdleAnimationOptions();
                    // 恢复MMD待机动作选择器的值（从角色配置读取）
                    await restoreMmdIdleAnimation();
                    // 从服务器加载MMD设置并应用
                    await loadMmdSettingsFromServer();
                    // 隐藏 VRM 专属控件
                    const vrmLightingGroup = document.getElementById('vrm-lighting-group');
                    if (vrmLightingGroup) vrmLightingGroup.style.display = 'none';
                    const vrmIdleAnimGroup = document.getElementById('vrm-idle-animation-group');
                    if (vrmIdleAnimGroup) vrmIdleAnimGroup.style.display = 'none';
                    if (vrmAnimationGroup) vrmAnimationGroup.style.display = 'none';
                    if (vrmExpressionGroup) vrmExpressionGroup.style.display = 'none';
                } else {
                    // VRM 子类型：显示 VRM 专属控件，隐藏 MMD 专属控件
                    if (vrmSettingsSection) vrmSettingsSection.style.display = 'block';
                    if (mmdSettingsSection) mmdSettingsSection.style.display = 'none';
                    // 停止 MMD 动画播放
                    if (window.mmdManager) {
                        window.mmdManager.stopAnimation();
                    }
                    isMmdAnimationPlaying = false;
                    isMmdIdlePlaying = false;
                    updateMMDAnimationPlayButtonIcon();
                    if (playMmdAnimationBtn) playMmdAnimationBtn.disabled = true;
                    // 隐藏 MMD 动画选择器
                    const mmdAnimationGroup = document.getElementById('mmd-animation-group');
                    if (mmdAnimationGroup) mmdAnimationGroup.style.display = 'none';
                    // 隐藏 MMD 动画上传/删除按钮
                    const mmdAnimationActionsGroup = document.getElementById('mmd-animation-actions-group');
                    if (mmdAnimationActionsGroup) mmdAnimationActionsGroup.style.display = 'none';
                    // 隐藏 MMD 待机动作选择器
                    const mmdIdleAnimGroup = document.getElementById('mmd-idle-animation-group');
                    if (mmdIdleAnimGroup) mmdIdleAnimGroup.style.display = 'none';
                    // 显示 VRM 专属控件（已在上方设置）
                }
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

            // 切换时恢复 live3d 子类型（如有保存）
            const restoredSubType = (type === 'live3d') ? (localStorage.getItem('live3dSubType') || '') : '';
            await switchModelDisplay(type, restoredSubType);

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
            // 切换到 Live3D 模式时，在合并列表中查找当前角色配置的模型
            else if (type === 'live3d') {
                try {
                    let matched = false;
                    const lanlanName = await getLanlanName();
                    if (lanlanName) {
                        const charactersData = await RequestHelper.fetchJson('/api/characters');
                        const catgirlConfig = charactersData['猫娘']?.[lanlanName];
                        if (vrmModelSelect) {
                            const _mmdPathSwitch = catgirlConfig && catgirlConfig.mmd
                                ? (typeof catgirlConfig.mmd === 'string' ? catgirlConfig.mmd : catgirlConfig.mmd.model_path)
                                : '';
                            if (_mmdPathSwitch) {
                                const mmdPath = _mmdPathSwitch;
                                const mmdFilename = mmdPath.split(/[/\\]/).pop();
                                // 优先完整路径匹配，其次文件名匹配
                                const matchedOption = Array.from(vrmModelSelect.options).find(opt => {
                                    if (!opt.value || opt.getAttribute('data-sub-type') !== 'mmd') return false;
                                    return opt.value === mmdPath;
                                }) || Array.from(vrmModelSelect.options).find(opt => {
                                    if (!opt.value || opt.getAttribute('data-sub-type') !== 'mmd') return false;
                                    return opt.value.endsWith(mmdFilename);
                                });
                                if (matchedOption) {
                                    vrmModelSelect.value = matchedOption.value;
                                    vrmModelSelect.dispatchEvent(new Event('change', { bubbles: true }));
                                    matched = true;
                                }
                            }
                            if (!matched && catgirlConfig && catgirlConfig.vrm) {
                                const vrmPath = catgirlConfig.vrm;
                                const vrmFilename = vrmPath.split(/[/\\]/).pop();
                                // 优先完整路径匹配，其次文件名匹配
                                const matchedOption = Array.from(vrmModelSelect.options).find(opt => {
                                    if (!opt.value) return false;
                                    return opt.value === vrmPath;
                                }) || Array.from(vrmModelSelect.options).find(opt => {
                                    if (!opt.value) return false;
                                    const optFilename = opt.getAttribute('data-filename') || '';
                                    return optFilename === vrmFilename || opt.value.endsWith(vrmFilename);
                                });
                                if (matchedOption) {
                                    vrmModelSelect.value = matchedOption.value;
                                    vrmModelSelect.dispatchEvent(new Event('change', { bubbles: true }));
                                    matched = true;
                                }
                            }
                        }
                    }
                    if (!matched) {
                        selectDefaultLive3DModel();
                    }
                } catch (autoLoadError) {
                    console.warn('[模型管理] 切到 Live3D 自动加载模型失败:', autoLoadError);
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

                    // 使用 ModelPathHelper 提取有效的 VRM 路径
                    const { path: modelPath, isValid, filename } = ModelPathHelper.extractVrmPath(model);

                    // 如果无效，跳过该模型
                    if (!isValid || !modelPath) {
                        console.warn('[模型管理] 跳过无效的 VRM 模型:', model);
                        return;
                    }

                    // 使用 ModelPathHelper 确保 data-path 属性永远是有效的 URL
                    const validPath = ModelPathHelper.normalizeModelPath(modelPath, 'model');

                    option.value = validPath;
                    option.setAttribute('data-path', validPath);
                    if (filename) {
                        option.setAttribute('data-filename', filename);
                    }
                    if (model.item_id) {
                        option.dataset.itemId = model.item_id;
                    }
                    option.textContent = model.name || filename || validPath;
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

            // Steam 徽章放在最前面
            if (option.dataset.itemId) {
                const steamBadge = document.createElement('span');
                steamBadge.className = 'steam-badge';
                steamBadge.textContent = 'Steam';
                item.appendChild(steamBadge);
            }

            // 添加 VRM/MMD 子类型徽章
            const subType = option.getAttribute('data-sub-type');
            if (subType === 'vrm') {
                const badge = document.createElement('span');
                badge.className = 'vrm-badge';
                badge.textContent = 'VRM';
                item.appendChild(badge);
            } else if (subType === 'mmd') {
                const badge = document.createElement('span');
                badge.className = 'mmd-badge';
                badge.textContent = 'MMD';
                item.appendChild(badge);
            }

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

            // Live3D 模式下：检测选中模型的子类型（VRM/MMD）
            const selectedOpt = vrmModelSelect.options[vrmModelSelect.selectedIndex];
            const subType = selectedOpt ? selectedOpt.getAttribute('data-sub-type') : null;

            if (subType === 'mmd' && currentModelType === 'live3d') {
                // MMD 子类型：路由到 MMD 加载流程
                // 保存选中模型信息（switchModelDisplay 会重建 select，导致原 option 脱离 DOM）
                const mmdPath = selectedOpt.getAttribute('data-path') || modelPath;
                const mmdFilename = selectedOpt.getAttribute('data-filename') || modelPath.split(/[/\\]/).pop();

                // 更新 sub_type 并刷新控件可见性
                stopIdleRotation('vrm');
                currentLive3dSubType = 'mmd';
                localStorage.setItem('live3dSubType', 'mmd');
                await switchModelDisplay('live3d', 'mmd');

                // switchModelDisplay 重建了 vrmModelSelect，需要重新选中当前模型
                if (vrmModelSelect) {
                    const reselect = Array.from(vrmModelSelect.options).find(opt =>
                        opt.value === modelPath || opt.getAttribute('data-path') === mmdPath
                    );
                    if (reselect) vrmModelSelect.value = reselect.value;
                    updateVRMModelSelectButtonText();
                }

                // 切换到 MMD 前，隐藏 VRM 容器（VRM/MMD 使用独立画布，仅需 CSS 切换）
                if (vrmContainer) {
                    vrmContainer.classList.add('hidden');
                    vrmContainer.style.display = 'none';
                }

                // 同步 mmdModelSelect 的值
                if (mmdModelSelect) {
                    const matchedMmdOpt = Array.from(mmdModelSelect.options).find(opt => opt.value === mmdPath);
                    if (matchedMmdOpt) {
                        mmdModelSelect.value = matchedMmdOpt.value;
                    } else {
                        // 如果 mmdModelSelect 中没有匹配项，直接设置值
                        mmdModelSelect.value = mmdPath;
                    }
                    // 触发 MMD 模型选择的 change 事件来加载模型
                    mmdModelSelect.dispatchEvent(new Event('change', { bubbles: true }));
                }

                // 保存 currentModelInfo 用于保存配置
                currentModelInfo = {
                    name: mmdFilename,
                    path: mmdPath,
                    url: modelPath,
                    type: 'mmd'
                };

                if (savePositionBtn) savePositionBtn.disabled = false;
                window.hasUnsavedChanges = true;
                return;
            }

            // 确保切换到Live3D模式（VRM子类型）
            // 注意：必须在判断之前检查旧值，否则条件永远为 false
            const needsSwitch = currentModelType !== 'live3d' || currentLive3dSubType !== 'vrm';
            stopIdleRotation('mmd');
            currentLive3dSubType = 'vrm';
            localStorage.setItem('live3dSubType', 'vrm');
            if (needsSwitch) {
                await switchModelDisplay('live3d', 'vrm');
                // switchModelDisplay 重建了 vrmModelSelect，需要重新选中当前模型
                if (vrmModelSelect) {
                    const reselect = Array.from(vrmModelSelect.options).find(opt => opt.value === modelPath);
                    if (reselect) vrmModelSelect.value = reselect.value;
                    updateVRMModelSelectButtonText();
                }
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

                // 切换到 VRM 前，隐藏 MMD 容器（VRM/MMD 使用独立画布，仅需 CSS 切换）
                if (mmdContainer) {
                    mmdContainer.classList.add('hidden');
                    mmdContainer.style.display = 'none';
                }
                if (vrmContainer) {
                    vrmContainer.classList.remove('hidden');
                    vrmContainer.style.display = 'block';
                }
                // 在加载新模型前，显式停止之前的动作并清理
                stopIdleRotation('vrm');
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
                // 加载后立即播内置 wait03 防 T-pose; 用户保存的 idle 选择
                // 由 loadCharacterLighting 恢复后通过 startIdleRotation 覆盖
                if (vrmManager.animation) {
                    try {
                        await vrmManager.playVRMAAnimation('/static/vrm/animation/wait03.vrma', { loop: true, immediate: true, isIdle: true });
                    } catch (e) {
                        console.warn('[VRM] 播放 wait03 待机动画失败:', e);
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
            if (!animationsLoaded && currentModelType === 'live3d') {
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
                // 不在此处 stopVRMAAnimation — playVRMAAnimation 内部会 crossfade
                // 自动播放选中的动作
                if (vrmManager) {
                    const selectedOption = vrmAnimationSelect.options[vrmAnimationSelect.selectedIndex];
                    const originalPath = selectedOption ? selectedOption.getAttribute('data-path') : animationPath;
                    const animDisplayName = selectedOption ? selectedOption.getAttribute('data-filename') : '';
                    const finalAnimationUrl = ModelPathHelper.vrmToUrl(originalPath, 'animation');
                    try {
                        showStatus(t('live2d.vrmAnimation.playingAnimation', `正在播放: ${animDisplayName}`, { name: animDisplayName }), 2000);
                        await vrmManager.playVRMAAnimation(finalAnimationUrl, {
                            loop: true,
                            timeScale: 1.0,
                            isIdle: false
                        });
                        isVrmAnimationPlaying = true;
                        updateVRMAnimationPlayButtonIcon();
                    } catch (error) {
                        console.error('自动播放 VRM 动作失败:', error);
                        isVrmAnimationPlaying = false;
                        updateVRMAnimationPlayButtonIcon();
                    }
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
                // 当前正在播放，点击后停止，恢复 idle 轮换
                if (vrmManager) {
                    vrmManager.stopVRMAAnimation();
                    isVrmAnimationPlaying = false;
                    updateVRMAnimationPlayButtonIcon();
                    showStatus(t('live2d.vrmAnimation.animationStopped', '动作已停止'), 2000);
                    // 恢复 idle 轮换 (空选择时回退内置 wait03)
                    const vrmIdleUrls = getSelectedIdleAnimations('vrm-idle-animation-multiselect');
                    startIdleRotation('vrm', vrmIdleUrls.length > 0 ? vrmIdleUrls : ['/static/vrm/animation/wait03.vrma']);
                }
            } else {
                // 当前未播放，暂停 idle 轮换并播放手动动作
                stopIdleRotation('vrm');
                const selectedOption = vrmAnimationSelect.options[vrmAnimationSelect.selectedIndex];
                const originalPath = selectedOption ? selectedOption.getAttribute('data-path') : vrmAnimationSelect.value;
                const animDisplayName = selectedOption ? selectedOption.getAttribute('data-filename') : '未知动作';

                const finalAnimationUrl = ModelPathHelper.vrmToUrl(originalPath, 'animation');
                const loop = true;
                const speed = 1.0;

                try {
                    showStatus(t('live2d.vrmAnimation.playingAnimation', `正在播放: ${animDisplayName}`, { name: animDisplayName }), 2000);
                    await vrmManager.playVRMAAnimation(finalAnimationUrl, {
                        loop: loop,
                        timeScale: speed,
                        isIdle: false
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
    // ======================== MMD 模型/动画列表 ========================

    async function loadMMDModels() {
        try {
            const data = await RequestHelper.fetchJson('/api/model/mmd/models');
            mmdModels = (data.success && Array.isArray(data.models)) ? data.models : [];
            if (!mmdModelSelect) return;

            mmdModelSelect.innerHTML = '';
            if (mmdModels.length > 0) {
                mmdModels.forEach(model => {
                    const modelPath = model.path || model.url;
                    if (!modelPath) return;
                    const option = document.createElement('option');
                    option.value = modelPath;
                    option.setAttribute('data-path', modelPath);
                    if (model.filename) option.setAttribute('data-filename', model.filename);
                    if (model.item_id) {
                        option.dataset.itemId = model.item_id;
                    }
                    option.textContent = model.name || model.filename || modelPath.split('/').pop();
                    mmdModelSelect.appendChild(option);
                });
                mmdModelSelect.disabled = false;
                if (mmdModelSelectBtn) mmdModelSelectBtn.disabled = false;
            } else {
                mmdModelSelect.innerHTML = `<option value="">${t('live2d.mmdModel.noModels', '未找到MMD模型')}</option>`;
            }
            updateMMDModelDropdown();
            updateMMDModelSelectButtonText();
        } catch (error) {
            console.error('加载MMD模型列表失败:', error);
            if (mmdModelSelect) {
                mmdModelSelect.innerHTML = `<option value="">${t('live2d.loadFailed', '加载失败')}</option>`;
            }
            updateMMDModelDropdown();
            updateMMDModelSelectButtonText();
        }
    }

    function updateMMDModelDropdown() {
        if (!mmdModelDropdown || !mmdModelSelect) return;
        mmdModelDropdown.innerHTML = '';
        mmdModelSelect.querySelectorAll('option').forEach(option => {
            if (!option.value) return;
            const item = document.createElement('div');
            item.className = 'dropdown-item';
            item.dataset.value = option.value;

            // Steam 徽章放在最前面
            if (option.dataset.itemId) {
                const steamBadge = document.createElement('span');
                steamBadge.className = 'steam-badge';
                steamBadge.textContent = 'Steam';
                item.appendChild(steamBadge);
            }

            const textSpan = document.createElement('span');
            textSpan.className = 'dropdown-item-text';
            const text = option.textContent || option.value || '';
            textSpan.textContent = text;
            textSpan.setAttribute('data-text', text);
            item.appendChild(textSpan);

            item.addEventListener('click', (e) => {
                e.stopPropagation();
                mmdModelSelect.value = item.dataset.value;
                mmdModelSelect.dispatchEvent(new Event('change', { bubbles: true }));
                mmdModelDropdown.style.display = 'none';
            });
            mmdModelDropdown.appendChild(item);
        });
    }

    function updateMMDModelSelectButtonText() {
        if (!mmdModelSelectBtn || !mmdModelSelect) return;
        const textSpan = mmdModelSelectBtn.querySelector('#mmd-model-select-text');
        if (!textSpan) return;
        const selected = mmdModelSelect.options[mmdModelSelect.selectedIndex];
        const text = (selected && selected.value) ? selected.textContent : '选择MMD模型';
        textSpan.textContent = text;
        textSpan.setAttribute('data-text', text);
    }

    async function loadMMDAnimations() {
        try {
            const data = await RequestHelper.fetchJson('/api/model/mmd/animations');
            mmdAnimations = (data.success && Array.isArray(data.animations)) ? data.animations : [];
            if (!mmdAnimationSelect) return;

            mmdAnimationSelect.innerHTML = `<option value="">${t('live2d.mmdAnimation.selectAnimation', '选择VMD动画')}</option>`;
            if (mmdAnimations.length > 0) {
                mmdAnimations.forEach(anim => {
                    const animPath = anim.path || anim.url || (typeof anim === 'string' ? anim : null);
                    if (!animPath) return;
                    const option = document.createElement('option');
                    option.value = animPath;
                    option.setAttribute('data-path', animPath);
                    const filename = anim.name || anim.filename || animPath.split('/').pop();
                    option.setAttribute('data-filename', filename);
                    option.textContent = filename;
                    mmdAnimationSelect.appendChild(option);
                });
                mmdAnimationSelect.disabled = false;
                if (mmdAnimationSelectBtn) mmdAnimationSelectBtn.disabled = false;
            } else {
                mmdAnimationSelect.innerHTML = `<option value="">${t('live2d.mmdAnimation.noAnimation', '无动画')}</option>`;
            }
            updateMMDAnimationDropdown();
            updateMMDAnimationSelectButtonText();
        } catch (error) {
            console.error('加载MMD动画列表失败:', error);
            if (mmdAnimationSelect) {
                mmdAnimationSelect.innerHTML = `<option value="">${t('live2d.loadFailed', '加载失败')}</option>`;
            }
            updateMMDAnimationDropdown();
            updateMMDAnimationSelectButtonText();
        }
    }

    function updateMMDAnimationDropdown() {
        if (!mmdAnimationDropdown || !mmdAnimationSelect) return;
        mmdAnimationDropdown.innerHTML = '';
        mmdAnimationSelect.querySelectorAll('option').forEach(option => {
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
                mmdAnimationSelect.value = item.dataset.value;
                mmdAnimationSelect.dispatchEvent(new Event('change', { bubbles: true }));
                mmdAnimationDropdown.style.display = 'none';
            });
            mmdAnimationDropdown.appendChild(item);
        });
    }

    function updateMMDAnimationSelectButtonText() {
        if (mmdAnimationManager) {
            mmdAnimationManager.updateButtonText();
        }
    }

    // ======================== MMD 模型删除 ========================

    const deleteMMDModelBtn = document.getElementById('delete-mmd-model-btn');
    const deleteMMDModelModal = document.getElementById('delete-mmd-model-modal');
    const mmdUserModelList = document.getElementById('mmd-user-model-list');
    const confirmDeleteMMDBtn = document.getElementById('confirm-delete-mmd-btn');
    const cancelDeleteMMDBtn = document.getElementById('cancel-delete-mmd-btn');
    const closeDeleteMMDModal = document.getElementById('close-delete-mmd-modal');
    let selectedDeleteMMDModels = new Set();

    function showDeleteMMDModelModal() {
        if (deleteMMDModelModal) {
            deleteMMDModelModal.style.display = 'flex';
            selectedDeleteMMDModels.clear();
            updateConfirmDeleteMMDButton();
            loadMMDUserModels();
        }
    }

    function hideDeleteMMDModelModal() {
        if (deleteMMDModelModal) {
            deleteMMDModelModal.style.display = 'none';
            selectedDeleteMMDModels.clear();
        }
    }

    function updateConfirmDeleteMMDButton() {
        if (confirmDeleteMMDBtn) {
            confirmDeleteMMDBtn.disabled = selectedDeleteMMDModels.size === 0;
            confirmDeleteMMDBtn.textContent = selectedDeleteMMDModels.size > 0
                ? `删除选中 (${selectedDeleteMMDModels.size})`
                : '删除选中';
        }
    }

    async function loadMMDUserModels() {
        if (!mmdUserModelList) return;
        mmdUserModelList.innerHTML = '<div class="empty-message">加载中...</div>';

        try {
            const data = await RequestHelper.fetchJson('/api/model/mmd/models');
            const models = (data.success && Array.isArray(data.models)) ? data.models : [];
            const userModels = models.filter(m => m.location === 'user');

            if (userModels.length === 0) {
                mmdUserModelList.innerHTML = '<div class="empty-message">暂无可删除的用户MMD模型</div>';
                return;
            }

            mmdUserModelList.innerHTML = '';
            const currentMMDPath = mmdModelSelect ? mmdModelSelect.value : '';

            userModels.forEach(model => {
                const item = document.createElement('div');
                item.className = 'model-item';
                const isBound = currentMMDPath && currentMMDPath === model.url;

                const checkbox = document.createElement('input');
                checkbox.type = 'checkbox';
                checkbox.value = model.url;
                checkbox.setAttribute('data-name', model.name);

                if (isBound) {
                    checkbox.disabled = true;
                    checkbox.title = '无法删除当前正在使用的模型';
                }

                checkbox.addEventListener('change', () => {
                    if (checkbox.checked) {
                        selectedDeleteMMDModels.add(model.url);
                    } else {
                        selectedDeleteMMDModels.delete(model.url);
                    }
                    updateConfirmDeleteMMDButton();
                });

                const label = document.createElement('label');
                const sizeKB = model.size ? `${(model.size / 1024).toFixed(0)} KB` : '';
                label.textContent = `${model.name} (${model.filename}) ${sizeKB}`;
                if (isBound) {
                    label.style.opacity = '0.5';
                    label.textContent += ' [使用中]';
                }

                item.appendChild(checkbox);
                item.appendChild(label);
                mmdUserModelList.appendChild(item);
            });
        } catch (error) {
            console.error('加载MMD用户模型列表失败:', error);
            mmdUserModelList.innerHTML = '<div class="empty-message">加载失败</div>';
        }
    }

    async function deleteSelectedMMDModels() {
        if (selectedDeleteMMDModels.size === 0) return;

        const confirmDelete = await showConfirm(
            `确定要删除选中的 ${selectedDeleteMMDModels.size} 个MMD模型吗？模型文件夹（含纹理）将一并删除，此操作不可恢复。`,
            '删除MMD模型',
            { danger: true }
        );
        if (!confirmDelete) return;

        if (confirmDeleteMMDBtn) {
            confirmDeleteMMDBtn.disabled = true;
            confirmDeleteMMDBtn.textContent = '删除中...';
        }

        let successCount = 0;
        let failCount = 0;

        for (const modelUrl of selectedDeleteMMDModels) {
            try {
                const result = await RequestHelper.fetchJson('/api/model/mmd/model', {
                    method: 'DELETE',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url: modelUrl })
                });
                if (result.success) {
                    successCount++;
                } else {
                    console.error(`删除 MMD 模型失败: ${modelUrl}`, result.error);
                    failCount++;
                }
            } catch (error) {
                console.error(`删除 MMD 模型出错: ${modelUrl}`, error);
                failCount++;
            }
        }

        selectedDeleteMMDModels.clear();
        updateConfirmDeleteMMDButton();
        await loadMMDUserModels();
        await loadMMDModels();

        if (successCount > 0) {
            showStatus(`成功删除 ${successCount} 个MMD模型${failCount > 0 ? `，${failCount} 个失败` : ''}`, 3000);
        } else if (failCount > 0) {
            showStatus(`删除失败: ${failCount} 个模型`, 3000);
        }
    }

    if (deleteMMDModelBtn) {
        deleteMMDModelBtn.addEventListener('click', showDeleteMMDModelModal);
    }
    if (cancelDeleteMMDBtn) {
        cancelDeleteMMDBtn.addEventListener('click', hideDeleteMMDModelModal);
    }
    if (closeDeleteMMDModal) {
        closeDeleteMMDModal.addEventListener('click', hideDeleteMMDModelModal);
    }
    if (confirmDeleteMMDBtn) {
        confirmDeleteMMDBtn.addEventListener('click', deleteSelectedMMDModels);
    }

    // ======================== MMD 动画删除 ========================

    const deleteMMDAnimationModal = document.getElementById('delete-mmd-animation-modal');
    const mmdUserAnimationList = document.getElementById('mmd-user-animation-list');
    const confirmDeleteMMDAnimationBtn = document.getElementById('confirm-delete-mmd-animation-btn');
    const cancelDeleteMMDAnimationBtn = document.getElementById('cancel-delete-mmd-animation-btn');
    const closeDeleteMMDAnimationModal = document.getElementById('close-delete-mmd-animation-modal');
    let selectedDeleteMMDAnimations = new Set();

    function showDeleteMMDAnimationModal() {
        if (deleteMMDAnimationModal) {
            deleteMMDAnimationModal.style.display = 'flex';
            selectedDeleteMMDAnimations.clear();
            updateConfirmDeleteMMDAnimationButton();
            loadMMDUserAnimations();
        }
    }

    function hideDeleteMMDAnimationModal() {
        if (deleteMMDAnimationModal) {
            deleteMMDAnimationModal.style.display = 'none';
            selectedDeleteMMDAnimations.clear();
        }
    }

    function updateConfirmDeleteMMDAnimationButton() {
        if (confirmDeleteMMDAnimationBtn) {
            confirmDeleteMMDAnimationBtn.disabled = selectedDeleteMMDAnimations.size === 0;
            confirmDeleteMMDAnimationBtn.textContent = selectedDeleteMMDAnimations.size > 0
                ? t('live2d.mmdAnimation.deleteSelectedCount', '删除选中 ({{count}})', { count: selectedDeleteMMDAnimations.size })
                : t('live2d.mmdAnimation.deleteSelected', '删除选中');
        }
    }

    async function loadMMDUserAnimations() {
        if (!mmdUserAnimationList) return;
        mmdUserAnimationList.innerHTML = `<div class="empty-message">${t('common.loading', '加载中...')}</div>`;

        try {
            const data = await RequestHelper.fetchJson('/api/model/mmd/animations/list');
            const animations = (data.success && Array.isArray(data.animations)) ? data.animations : [];

            if (animations.length === 0) {
                mmdUserAnimationList.innerHTML = `<div class="empty-message">${t('live2d.mmdAnimation.noUserAnimations', '暂无可删除的用户VMD动画')}</div>`;
                return;
            }

            mmdUserAnimationList.innerHTML = '';

            animations.forEach(anim => {
                const item = document.createElement('div');
                item.className = 'model-item';

                const checkbox = document.createElement('input');
                checkbox.type = 'checkbox';
                checkbox.value = anim.url;
                checkbox.setAttribute('data-name', anim.name);

                checkbox.addEventListener('change', () => {
                    if (checkbox.checked) {
                        selectedDeleteMMDAnimations.add(anim.url);
                    } else {
                        selectedDeleteMMDAnimations.delete(anim.url);
                    }
                    updateConfirmDeleteMMDAnimationButton();
                });

                const label = document.createElement('label');
                label.textContent = `${anim.name} (${anim.filename})`;

                item.appendChild(checkbox);
                item.appendChild(label);
                mmdUserAnimationList.appendChild(item);
            });
        } catch (error) {
            console.error('加载VMD动画列表失败:', error);
            mmdUserAnimationList.innerHTML = `<div class="empty-message">${t('common.loadFailed', '加载失败')}</div>`;
        }
    }

    async function deleteSelectedMMDAnimations() {
        if (selectedDeleteMMDAnimations.size === 0) return;

        const confirmDelete = await showConfirm(
            t('live2d.mmdAnimation.deleteConfirmMessage', '确定要删除选中的 {{count}} 个VMD动画吗？此操作不可恢复。', { count: selectedDeleteMMDAnimations.size }),
            t('live2d.mmdAnimation.deleteConfirmTitle', '删除VMD动画'),
            { danger: true }
        );
        if (!confirmDelete) return;

        if (confirmDeleteMMDAnimationBtn) {
            confirmDeleteMMDAnimationBtn.disabled = true;
            confirmDeleteMMDAnimationBtn.textContent = t('live2d.mmdAnimation.deleting', '删除中...');
        }

        // 保存当前选择的动画URL，用于删除后恢复或重置
        const previousAnimUrl = mmdAnimationSelect ? mmdAnimationSelect.value : '';
        let wasCurrentAnimDeleted = false;

        let successCount = 0;
        let failCount = 0;

        for (const animUrl of selectedDeleteMMDAnimations) {
            try {
                const result = await RequestHelper.fetchJson('/api/model/mmd/animation', {
                    method: 'DELETE',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url: animUrl })
                });
                if (result.success) {
                    if (window.mmdManager && window.mmdManager.currentAnimationUrl === animUrl) {
                        window.mmdManager.stopAnimation();
                        isMmdAnimationPlaying = false;
                        isMmdIdlePlaying = false;
                        updateMMDAnimationPlayButtonIcon();
                        wasCurrentAnimDeleted = true;
                    }
                    successCount++;
                } else {
                    console.error(`删除 VMD 动画失败: ${animUrl}`, result.error);
                    failCount++;
                }
            } catch (error) {
                console.error(`删除 VMD 动画出错: ${animUrl}`, error);
                failCount++;
            }
        }

        selectedDeleteMMDAnimations.clear();
        updateConfirmDeleteMMDAnimationButton();
        await loadMMDUserAnimations();
        await loadMMDAnimations();

        // 检查之前选择的动画是否还存在
        if (mmdAnimationSelect && previousAnimUrl) {
            const optionExists = Array.from(mmdAnimationSelect.options).some(opt => opt.value === previousAnimUrl);
            if (optionExists) {
                // 动画仍然存在，恢复选择
                mmdAnimationSelect.value = previousAnimUrl;
                updateMMDAnimationSelectButtonText();
            } else {
                // 动画已被删除，确保播放状态完全重置
                if (!wasCurrentAnimDeleted && window.mmdManager) {
                    window.mmdManager.stopAnimation();
                }
                isMmdAnimationPlaying = false;
                isMmdIdlePlaying = false;
                updateMMDAnimationPlayButtonIcon();
                if (playMmdAnimationBtn) playMmdAnimationBtn.disabled = true;
            }
        }

        if (successCount > 0) {
            const msg = failCount > 0 
                ? t('live2d.mmdAnimation.deletePartialSuccess', '成功删除 {{count}} 个VMD动画，{{failed}} 个失败', { count: successCount, failed: failCount })
                : t('live2d.mmdAnimation.deleteSuccess', '成功删除 {{count}} 个VMD动画', { count: successCount });
            showStatus(msg, 3000);
        } else if (failCount > 0) {
            showStatus(t('live2d.mmdAnimation.deleteFailed', '删除失败: {{count}} 个动画', { count: failCount }), 3000);
        }
    }

    if (deleteMmdAnimationBtn) {
        deleteMmdAnimationBtn.addEventListener('click', showDeleteMMDAnimationModal);
    }
    if (cancelDeleteMMDAnimationBtn) {
        cancelDeleteMMDAnimationBtn.addEventListener('click', hideDeleteMMDAnimationModal);
    }
    if (closeDeleteMMDAnimationModal) {
        closeDeleteMMDAnimationModal.addEventListener('click', hideDeleteMMDAnimationModal);
    }
    if (confirmDeleteMMDAnimationBtn) {
        confirmDeleteMMDAnimationBtn.addEventListener('click', deleteSelectedMMDAnimations);
    }

    // Live3D 合并模型列表：同时加载 VRM 和 MMD 模型到统一的 vrmModelSelect 下拉
    async function loadLive3DModels() {
        try {
            showStatus(t('live2d.loading', '正在加载模型列表...'));

            // 并行请求 VRM 和 MMD 模型列表
            const [vrmData, mmdData] = await Promise.all([
                RequestHelper.fetchJson('/api/model/vrm/models'),
                RequestHelper.fetchJson('/api/model/mmd/models')
            ]);

            const vrmModels = (vrmData.success && Array.isArray(vrmData.models)) ? vrmData.models : [];
            const mmdModelsList = (mmdData.success && Array.isArray(mmdData.models)) ? mmdData.models : [];

            if (!vrmModelSelect) return;
            vrmModelSelect.innerHTML = '';

            // 添加 VRM 模型
            vrmModels.forEach(model => {
                // 使用 ModelPathHelper 提取有效的 VRM 路径
                const { path: modelPath, isValid, filename } = ModelPathHelper.extractVrmPath(model);

                if (!isValid || !modelPath) return;

                const validPath = ModelPathHelper.normalizeModelPath(modelPath, 'model');

                const option = document.createElement('option');
                option.value = validPath;
                option.setAttribute('data-path', validPath);
                option.setAttribute('data-sub-type', 'vrm');
                if (filename) option.setAttribute('data-filename', filename);
                if (model.item_id) option.dataset.itemId = model.item_id;
                const baseName = model.name || filename || validPath;
                option.textContent = baseName;
                vrmModelSelect.appendChild(option);
            });

            // 添加 MMD 模型
            mmdModelsList.forEach(model => {
                const modelPath = model.path || model.url;
                if (!modelPath) return;
                const option = document.createElement('option');
                option.value = modelPath;
                option.setAttribute('data-path', modelPath);
                option.setAttribute('data-sub-type', 'mmd');
                if (model.filename) option.setAttribute('data-filename', model.filename);
                if (model.item_id) option.dataset.itemId = model.item_id;
                const baseName = model.name || model.filename || modelPath.split('/').pop();
                option.textContent = baseName;
                vrmModelSelect.appendChild(option);
            });

            if (vrmModelSelect.options.length > 0) {
                vrmModelSelect.disabled = false;
                if (vrmModelSelectBtn) vrmModelSelectBtn.disabled = false;
            } else {
                vrmModelSelect.innerHTML = `<option value="">${t('live2d.noVRMModelsFound', '未找到可用模型')}</option>`;
            }

            updateVRMModelDropdown();
            updateVRMModelSelectButtonText();
            showStatus(t('live2d.vrmModelListLoaded', '模型列表加载成功'), 2000);

            // 同时更新 mmdModelSelect（供 saveModelToCharacter 等引用）
            if (mmdModelSelect) {
                mmdModelSelect.innerHTML = '';
                mmdModelsList.forEach(model => {
                    const modelPath = model.path || model.url;
                    if (!modelPath) return;
                    const option = document.createElement('option');
                    option.value = modelPath;
                    option.setAttribute('data-path', modelPath);
                    if (model.filename) option.setAttribute('data-filename', model.filename);
                    if (model.item_id) option.dataset.itemId = model.item_id;
                    option.textContent = model.name || model.filename || modelPath.split('/').pop();
                    mmdModelSelect.appendChild(option);
                });
            }
        } catch (error) {
            console.error('加载Live3D模型列表失败:', error);
            if (vrmModelSelect) {
                vrmModelSelect.innerHTML = `<option value="">${t('live2d.loadFailed', '加载失败')}</option>`;
            }
            updateVRMModelDropdown();
            updateVRMModelSelectButtonText();
            showStatus(t('live2d.loadError', `错误: ${error.message}`, { error: error.message }), 5000);
        }
    }

    // 自动选择默认 Live3D 模型（sister1.0.vrm），当角色无已配置的 VRM/MMD 模型时使用
    function selectDefaultLive3DModel() {
        if (!vrmModelSelect || vrmModelSelect.options.length === 0) return false;
        const defaultFilename = 'sister1.0.vrm';
        const matchedOption = Array.from(vrmModelSelect.options).find(opt => {
            if (!opt.value) return false;
            const optFilename = opt.getAttribute('data-filename') || '';
            return optFilename === defaultFilename || opt.value.includes(defaultFilename);
        });
        if (matchedOption) {
            vrmModelSelect.value = matchedOption.value;
            vrmModelSelect.dispatchEvent(new Event('change', { bubbles: true }));
            console.log('[模型管理] 自动加载默认 Live3D 模型:', defaultFilename);
            return true;
        }
        return false;
    }

    // MMD 模型选择事件
    if (mmdModelSelect) {
        mmdModelSelect.addEventListener('change', async (e) => {
            const modelPath = e.target.value;
            updateMMDModelSelectButtonText();
            if (!modelPath) return;

            // 加载 MMD 前，隐藏 VRM 容器（VRM/MMD 使用独立画布，仅需 CSS 切换）
            if (vrmContainer) {
                vrmContainer.classList.add('hidden');
                vrmContainer.style.display = 'none';
            }
            // 显示 MMD 容器
            if (mmdContainer) {
                mmdContainer.classList.remove('hidden');
                mmdContainer.style.display = 'block';
            }

            try {
                // 等待 MMD 模块加载
                if (!window.mmdModuleLoaded && !window.MMDManager) {
                    showStatus('正在加载MMD模块...', 0);
                    await new Promise((resolve, reject) => {
                        if (window.MMDManager || window.mmdModuleLoaded) return resolve();
                        window.addEventListener('mmd-modules-ready', resolve, { once: true });
                        setTimeout(() => reject(new Error('MMD Module Load Timeout')), 8000);
                    });
                }

                // 初始化 mmdManager（如果尚未存在）
                if (!window.mmdManager && typeof window.initMMDModel === 'function') {
                    await window.initMMDModel();
                } else if (!window.mmdManager && typeof window.MMDManager !== 'undefined') {
                    window.mmdManager = new window.MMDManager();
                }

                if (!window.mmdManager) {
                    showStatus('MMD管理器初始化失败', 3000);
                    return;
                }

                showStatus('正在加载MMD模型...', 0);
                if (mmdContainer) mmdContainer.classList.remove('hidden');

                // 在加载新模型前，重置动画播放状态
                stopIdleRotation('mmd');
                if (window.mmdManager) {
                    window.mmdManager.stopAnimation();
                    isMmdAnimationPlaying = false;
                    isMmdIdlePlaying = false;
                    updateMMDAnimationPlayButtonIcon();
                }
                if (playMmdAnimationBtn) playMmdAnimationBtn.disabled = true;

                if (!window.mmdManager.scene) {
                    await window.mmdManager.init('mmd-canvas', 'mmd-container');
                }
                // 预置物理开关，避免 loadModel 时使用默认的 true
                // 等待进行中的设置加载完成，避免读到过期的 localStorage
                if (_mmdSettingsLoadPromise) {
                    await _mmdSettingsLoadPromise;
                }
                try {
                    const savedMmdSettings = localStorage.getItem('mmdSettings');
                    if (savedMmdSettings) {
                        const s = JSON.parse(savedMmdSettings);
                        if (s.physics?.enabled != null) {
                            window.mmdManager.enablePhysics = !!s.physics.enabled;
                        }
                    }
                } catch (e) { /* ignore */ }
                await window.mmdManager.loadModel(modelPath);
                showStatus('MMD模型加载成功', 2000);

                // 加载后立即播内置 wait03 防 T-pose; 用户保存的 idle 选择
                // 由 loadCharacterLighting 恢复后通过 startIdleRotation 覆盖
                try {
                    await window.mmdManager.loadAnimation('/static/mmd/animation/wait03.vmd');
                    window.mmdManager.playAnimation();
                    isMmdIdlePlaying = true;
                    if (playMmdAnimationBtn) playMmdAnimationBtn.disabled = false;
                } catch (e) {
                    console.warn('[MMD] 播放 wait03 待机动作失败:', e);
                }
            } catch (error) {
                console.error('加载MMD模型失败:', error);
                showStatus(`MMD模型加载失败: ${error.message}`, 3000);
            }
        });
    }

    // MMD 动画选择事件
    if (mmdAnimationSelect) {
        mmdAnimationSelect.addEventListener('change', async (e) => {
            const animPath = e.target.value;
            updateMMDAnimationSelectButtonText();

            // 如果选择的是第一个选项（空值），完全重置播放状态
            if (!animPath) {
                if (window.mmdManager) {
                    window.mmdManager.stopAnimation();
                }
                isMmdAnimationPlaying = false;
                updateMMDAnimationPlayButtonIcon();
                if (playMmdAnimationBtn) playMmdAnimationBtn.disabled = true;
                return;
            }

            if (!window.mmdManager) return;

            try {
                // 不在此处 stopAnimation — loadAnimation 内部会清理旧动画
                // 保持旧动画播放直到新动画加载完成，避免 T-pose 闪烁
                showStatus(t('live2d.mmdAnimation.loading', '正在加载VMD动画...'), 0);
                await window.mmdManager.loadAnimation(animPath);
                if (playMmdAnimationBtn) playMmdAnimationBtn.disabled = false;
                // 加载成功后自动播放
                window.mmdManager.playAnimation();
                isMmdAnimationPlaying = true;
                updateMMDAnimationPlayButtonIcon();
                showStatus(t('live2d.mmdAnimation.playing', 'VMD动画开始播放'), 2000);
            } catch (error) {
                console.error('加载VMD动画失败:', error);
                showStatus(t('live2d.mmdAnimation.loadFailed', 'VMD动画加载失败: {{error}}', { error: error.message }), 3000);
                if (playMmdAnimationBtn) playMmdAnimationBtn.disabled = true;
            }
        });
    }

    // 更新MMD动画播放按钮图标（仅反映手动预览状态，idle 不影响按钮）
    function updateMMDAnimationPlayButtonIcon() {
        if (!playMmdAnimationBtn) return;
        const icon = playMmdAnimationBtn.querySelector('.mmd-animation-play-icon');
        if (icon) {
            if (isMmdAnimationPlaying) {
                icon.src = '/static/icons/vrm_pause_icon.png?v=1';
                icon.alt = t('common.pause', '暂停');
            } else {
                icon.src = '/static/icons/motion_play_icon.png?v=1';
                icon.alt = t('common.play', '播放');
            }
        }
    }

    // 播放/停止 MMD 动画
    if (playMmdAnimationBtn) {
        playMmdAnimationBtn.addEventListener('click', async () => {
            if (!window.mmdManager || !mmdAnimationSelect || !mmdAnimationSelect.value) {
                showStatus(t('live2d.mmdAnimation.selectFirst', '请先选择VMD动画'), 2000);
                return;
            }

            if (isMmdAnimationPlaying) {
                window.mmdManager.stopAnimation();
                isMmdAnimationPlaying = false;
                updateMMDAnimationPlayButtonIcon();
                showStatus(t('live2d.mmdAnimation.stopped', 'VMD动画已停止'), 2000);
                // 恢复 idle 轮换 (空选择时回退内置 wait03)
                const mmdIdleUrls = getSelectedIdleAnimations('mmd-idle-animation-multiselect');
                startIdleRotation('mmd', mmdIdleUrls.length > 0 ? mmdIdleUrls : ['/static/mmd/animation/wait03.vmd']);
            } else {
                stopIdleRotation('mmd');
                // 加载用户选中的动画 (idle rotation 可能已替换为 idle clip)
                try {
                    await window.mmdManager.loadAnimation(mmdAnimationSelect.value);
                    window.mmdManager.playAnimation();
                    isMmdAnimationPlaying = true;
                    updateMMDAnimationPlayButtonIcon();
                    showStatus(t('live2d.mmdAnimation.playing', 'VMD动画开始播放'), 2000);
                } catch (e) {
                    console.warn('[MMD] 加载手动动画失败:', e);
                    showStatus(t('live2d.mmdAnimation.playFailed', '播放动画失败'), 2000);
                }
            }
        });
    }

    // MMD 模型下拉按钮点击事件
    if (mmdModelSelectBtn) {
        mmdModelSelectBtn.addEventListener('click', () => {
            if (!mmdModelDropdown) return;
            const isOpen = mmdModelDropdown.style.display !== 'none';
            mmdModelDropdown.style.display = isOpen ? 'none' : 'block';
        });
    }

    // MMD 动画下拉按钮点击事件已由 DropdownManager 处理

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
    const vrmIdleAnimationMultiselect = document.getElementById('vrm-idle-animation-multiselect');
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
            window.hasUnsavedChanges = true;
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
            window.hasUnsavedChanges = true;
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
            window.hasUnsavedChanges = true;
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
            // 无色调映射时禁用曝光滑块
            const isNoToneMapping = value === 0;
            if (exposureSlider) {
                exposureSlider.disabled = isNoToneMapping;
                exposureSlider.style.opacity = isNoToneMapping ? '0.5' : '1';
            }
            if (exposureValue) {
                exposureValue.style.opacity = isNoToneMapping ? '0.5' : '1';
            }
            window.hasUnsavedChanges = true;
        });
    }

    // VRM 描边粗细 — 共用 helper
    function applyVrmOutlineWidth(scale) {
        const label = document.getElementById('vrm-outline-width-value');
        if (label) label.textContent = scale.toFixed(2);
        if (!vrmManager?.currentModel?.vrm?.scene) return;
        vrmManager.currentModel.vrm.scene.traverse((object) => {
            if (!object.isMesh && !object.isSkinnedMesh) return;
            const mats = Array.isArray(object.material) ? object.material : [object.material];
            mats.forEach(mat => {
                if (!mat || !(mat._isOutline || mat.isOutline)) return;
                if (mat._originalOutlineWidthFactor === undefined) {
                    mat._originalOutlineWidthFactor = mat.outlineWidthFactor !== undefined ? mat.outlineWidthFactor : 0.002;
                }
                if (mat.outlineWidthFactor !== undefined) {
                    mat.outlineWidthFactor = mat._originalOutlineWidthFactor * scale;
                    mat.needsUpdate = true;
                }
            });
        });
    }

    const vrmOutlineWidthSlider = document.getElementById('vrm-outline-width-slider');
    if (vrmOutlineWidthSlider) {
        vrmOutlineWidthSlider.addEventListener('input', (e) => {
            applyVrmOutlineWidth(parseFloat(e.target.value));
            window.hasUnsavedChanges = true;
        });
    }

    // ===== 待机动作多选：工具函数 =====

    /** 待机动作轮换定时器 */
    const _idleRotationTimers = { vrm: null, mmd: null };
    /** 上一次播放的 URL（避免连续播放同一个） */
    const _idleRotationLast = { vrm: null, mmd: null };

    /** 从多选容器中获取所有已勾选的动画 URL 列表 */
    function getSelectedIdleAnimations(containerId) {
        const container = document.getElementById(containerId);
        if (!container) return [];
        return Array.from(container.querySelectorAll('.idle-animation-options input[type="checkbox"]:checked'))
            .map(cb => cb.value)
            .filter(Boolean);
    }

    /** 设置多选容器中的勾选状态 */
    function setSelectedIdleAnimations(containerId, urls) {
        const container = document.getElementById(containerId);
        if (!container) return;
        const urlSet = new Set(Array.isArray(urls) ? urls : (urls ? [urls] : []));
        container.querySelectorAll('.idle-animation-options input[type="checkbox"]').forEach(cb => {
            cb.checked = urlSet.has(cb.value);
        });
        updateIdleMultiselectHeader(container);
    }

    /** 更新多选 header 显示已选项 tag */
    function updateIdleMultiselectHeader(container) {
        const header = container.querySelector('.multiselect-header');
        if (!header) return;
        const checked = container.querySelectorAll('.idle-animation-options input[type="checkbox"]:checked');
        header.innerHTML = '';
        if (checked.length === 0) {
            const hint = document.createElement('span');
            hint.className = 'selected-text';
            const type = container.dataset.type;
            hint.textContent = t(`${type === 'mmd' ? 'mmd' : 'vrm'}.idleAnimation.selectHint`, '选择待机动作');
            header.appendChild(hint);
        } else {
            checked.forEach(cb => {
                const label = cb.closest('.multiselect-item')?.querySelector('span')?.textContent || cb.value.split('/').pop();
                const tag = document.createElement('span');
                tag.className = 'selected-tag';
                tag.textContent = label;
                header.appendChild(tag);
            });
        }
    }

    /** 切换多选下拉展开/收起 */
    function toggleIdleMultiselect(event) {
        const ms = event.currentTarget.closest('.idle-animation-multiselect');
        if (!ms) return;
        const wasActive = ms.classList.contains('active');
        // 关闭所有待机动作多选
        document.querySelectorAll('.idle-animation-multiselect').forEach(el => {
            el.classList.remove('active', 'open-up', 'open-down');
            const h = el.querySelector('.multiselect-header');
            if (h) h.setAttribute('aria-expanded', 'false');
        });
        if (!wasActive) {
            // 判断向上/向下展开
            const rect = ms.getBoundingClientRect();
            const spaceBelow = window.innerHeight - rect.bottom;
            if (spaceBelow < 220) {
                ms.classList.add('active', 'open-up');
            } else {
                ms.classList.add('active', 'open-down');
            }
            ms.querySelector('.multiselect-header')?.setAttribute('aria-expanded', 'true');
        }
        event.stopPropagation();
    }

    // 点击外部关闭待机动作多选
    window.addEventListener('click', () => {
        document.querySelectorAll('.idle-animation-multiselect').forEach(ms => {
            ms.classList.remove('active', 'open-up', 'open-down');
            const h = ms.querySelector('.multiselect-header');
            if (h) h.setAttribute('aria-expanded', 'false');
        });
    });

    /** 停止待机动作轮换 */
    function stopIdleRotation(type) {
        if (_idleRotationTimers[type]) {
            clearTimeout(_idleRotationTimers[type]);
            _idleRotationTimers[type] = null;
        }
        _idleRotationLast[type] = null;
    }

    /** 启动/重启待机动作轮换 */
    function startIdleRotation(type, urls) {
        stopIdleRotation(type);
        if (!urls || urls.length === 0) return;

        // 立即播放一个
        const firstUrl = urls.length === 1 ? urls[0] : _pickRandomDifferent(urls, _idleRotationLast[type]);
        _playIdleAnimation(type, firstUrl);
        _idleRotationLast[type] = firstUrl;

        // 多于 1 个才定时轮换
        if (urls.length > 1) {
            _scheduleNextIdle(type, urls);
        }
    }

    function _scheduleNextIdle(type, urls) {
        _idleRotationTimers[type] = setTimeout(() => {
            // 模式不匹配时停止轮换
            if (!_isIdleTypeActive(type)) {
                _idleRotationTimers[type] = null;
                return;
            }
            // 重新获取当前已选列表（用户可能在期间改了勾选）
            const containerId = type === 'vrm' ? 'vrm-idle-animation-multiselect' : 'mmd-idle-animation-multiselect';
            const currentUrls = getSelectedIdleAnimations(containerId);
            if (currentUrls.length < 2) {
                _idleRotationTimers[type] = null;
                return;
            }
            const nextUrl = _pickRandomDifferent(currentUrls, _idleRotationLast[type]);
            _playIdleAnimation(type, nextUrl);
            _idleRotationLast[type] = nextUrl;
            _scheduleNextIdle(type, currentUrls);
        }, 20000);
    }

    function _pickRandomDifferent(urls, lastUrl) {
        if (urls.length <= 1) return urls[0] || null;
        const candidates = urls.filter(u => u !== lastUrl);
        return candidates[Math.floor(Math.random() * candidates.length)];
    }

    /** 检查当前模式是否匹配指定的 idle 类型 */
    function _isIdleTypeActive(type) {
        if (currentModelType !== 'live3d') return false;
        if (type === 'vrm') return !currentLive3dSubType || currentLive3dSubType === 'vrm';
        if (type === 'mmd') return currentLive3dSubType === 'mmd';
        return false;
    }

    async function _playIdleAnimation(type, url) {
        if (!url) return;
        // Guard: 当前模式不匹配时不播放
        if (!_isIdleTypeActive(type)) {
            console.debug(`[${type.toUpperCase()} IdleAnimation] 模式不匹配, 跳过播放`);
            stopIdleRotation(type);
            return;
        }
        // Guard: 用户正在手动播放非 idle 动画时不覆盖
        if (type === 'vrm' && isVrmAnimationPlaying) return;
        if (type === 'mmd' && isMmdAnimationPlaying) return;

        try {
            if (type === 'vrm') {
                if (vrmManager && vrmManager.animation && vrmManager.currentModel) {
                    if (vrmManager.vrmaAction) {
                        vrmManager.stopVRMAAnimation();
                    }
                    await vrmManager.playVRMAAnimation(url, { loop: true, immediate: true, isIdle: true });
                    console.log('[VRM IdleAnimation] 待机动作已切换:', url.split('/').pop());
                }
            } else {
                if (window.mmdManager && window.mmdManager.currentModel) {
                    if (isMmdIdlePlaying) {
                        window.mmdManager.stopAnimation();
                        isMmdIdlePlaying = false;
                    }
                    await window.mmdManager.loadAnimation(url);
                    window.mmdManager.playAnimation();
                    isMmdIdlePlaying = true;
                    console.log('[MMD IdleAnimation] 待机动作已切换:', url.split('/').pop());
                }
            }
        } catch (err) {
            console.warn(`[${type.toUpperCase()} IdleAnimation] 切换待机动作失败:`, err);
        }
    }

    /** checkbox 变更时触发：更新 header、标记未保存、启动/停止轮换 */
    function onIdleAnimationCheckboxChange(type) {
        const containerId = type === 'vrm' ? 'vrm-idle-animation-multiselect' : 'mmd-idle-animation-multiselect';
        const container = document.getElementById(containerId);
        if (container) updateIdleMultiselectHeader(container);
        window.hasUnsavedChanges = true;

        const urls = getSelectedIdleAnimations(containerId);
        if (urls.length === 0) {
            stopIdleRotation(type);
            // 取消全选时回退到内置待机动画 (手动预览中不覆盖)
            if (type === 'vrm' && !isVrmAnimationPlaying) {
                const builtinVrm = '/static/vrm/animation/wait03.vrma';
                if (vrmManager && vrmManager.animation && vrmManager.currentModel) {
                    if (vrmManager.vrmaAction) vrmManager.stopVRMAAnimation();
                    vrmManager.playVRMAAnimation(builtinVrm, { loop: true, immediate: true, isIdle: true }).catch(e => {
                        console.warn('[VRM IdleAnimation] 回退内置待机失败:', e);
                    });
                }
                showStatus(t('vrm.idleAnimation.changed', '待机动作已切换', { name: 'wait03' }), 2000);
            } else if (type === 'mmd' && !isMmdAnimationPlaying) {
                const builtinMmd = '/static/mmd/animation/wait03.vmd';
                if (window.mmdManager && window.mmdManager.currentModel) {
                    if (isMmdIdlePlaying) window.mmdManager.stopAnimation();
                    window.mmdManager.loadAnimation(builtinMmd).then(() => {
                        window.mmdManager.playAnimation();
                        isMmdIdlePlaying = true;
                    }).catch(e => {
                        console.warn('[MMD IdleAnimation] 回退内置待机失败:', e);
                        isMmdIdlePlaying = false;
                    });
                }
                showStatus(t('mmd.idleAnimation.changed', '待机动作已切换', { name: 'wait03' }), 2000);
            }
        } else {
            startIdleRotation(type, urls);
            const name = urls.length === 1 ? urls[0].split('/').pop() : `${urls.length}`;
            const prefix = type === 'vrm' ? 'vrm' : 'mmd';
            if (urls.length === 1) {
                showStatus(t(`${prefix}.idleAnimation.changed`, '待机动作已切换', { name }), 2000);
            } else {
                showStatus(t(`${prefix}.idleAnimation.selectedCount`, `已选 ${urls.length} 个`, { count: urls.length }), 2000);
            }
        }
    }

    // ===== VRM 待机动作加载 =====

    async function loadIdleAnimationOptions() {
        if (loadIdleAnimationOptions._promise) return loadIdleAnimationOptions._promise;
        loadIdleAnimationOptions._promise = _doLoadIdleAnimationOptions().finally(() => {
            loadIdleAnimationOptions._promise = null;
        });
        return loadIdleAnimationOptions._promise;
    }
    async function _doLoadIdleAnimationOptions() {
        const container = document.getElementById('vrm-idle-animation-multiselect');
        if (!container) {
            console.debug('[VRM IdleAnimation] 待机动作多选元素未找到，跳过加载');
            return;
        }
        const optionsEl = container.querySelector('.idle-animation-options');
        if (!optionsEl) return;

        try {
            console.log('[VRM IdleAnimation] 正在从 API 加载待机动作列表...');
            const data = await RequestHelper.fetchJson('/api/model/vrm/animations');
            const animations = (data.success && data.animations) ? data.animations : [];
            const defaultUrl = '/static/vrm/animation/wait03.vrma';

            optionsEl.innerHTML = '';
            optionsEl.onclick = (e) => e.stopPropagation();

            if (animations.length > 0) {
                animations.forEach(anim => {
                    const animPath = (typeof anim.path === 'string' ? anim.path : null)
                        || (typeof anim.url === 'string' ? anim.url : null)
                        || (typeof anim === 'string' ? anim : null);
                    if (!animPath) return;

                    const finalUrl = ModelPathHelper.vrmToUrl(animPath, 'animation');
                    const displayName = anim.name || anim.filename || finalUrl.split('/').pop();
                    const isDefault = finalUrl === defaultUrl || animPath.includes('wait03.vrma');

                    const item = document.createElement('div');
                    item.className = 'multiselect-item';
                    const checkbox = document.createElement('input');
                    checkbox.type = 'checkbox';
                    checkbox.value = finalUrl;
                    checkbox.checked = isDefault;
                    const span = document.createElement('span');
                    span.textContent = displayName;
                    item.appendChild(checkbox);
                    item.appendChild(span);
                    item.addEventListener('click', (e) => {
                        if (e.target.tagName !== 'INPUT') checkbox.checked = !checkbox.checked;
                        onIdleAnimationCheckboxChange('vrm');
                        e.stopPropagation();
                    });
                    optionsEl.appendChild(item);
                });
                console.log(`[VRM IdleAnimation] 待机动作列表加载成功，共 ${animations.length} 个动画`);
            } else {
                const empty = document.createElement('div');
                empty.className = 'multiselect-item';
                empty.textContent = t('live2d.vrmAnimation.noAnimations', '未找到动作文件');
                optionsEl.appendChild(empty);
            }

            // 绑定 header 点击
            const header = container.querySelector('.multiselect-header');
            if (header) {
                header.onclick = toggleIdleMultiselect;
                header.onkeydown = (e) => {
                    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleIdleMultiselect(e); }
                };
            }
            updateIdleMultiselectHeader(container);
        } catch (error) {
            console.error('[VRM IdleAnimation] 加载待机动作列表失败:', error);
            optionsEl.innerHTML = '';
            const errEl = document.createElement('div');
            errEl.className = 'multiselect-item';
            errEl.textContent = t('live2d.loadFailed', '加载失败');
            optionsEl.appendChild(errEl);
        }
    }

    // ===== MMD 待机动作加载 =====

    async function loadMmdIdleAnimationOptions() {
        if (loadMmdIdleAnimationOptions._promise) return loadMmdIdleAnimationOptions._promise;
        loadMmdIdleAnimationOptions._promise = _doLoadMmdIdleAnimationOptions().finally(() => {
            loadMmdIdleAnimationOptions._promise = null;
        });
        return loadMmdIdleAnimationOptions._promise;
    }
    async function _doLoadMmdIdleAnimationOptions() {
        const container = document.getElementById('mmd-idle-animation-multiselect');
        if (!container) {
            console.debug('[MMD IdleAnimation] 待机动作多选元素未找到，跳过加载');
            return;
        }
        const optionsEl = container.querySelector('.idle-animation-options');
        if (!optionsEl) return;

        try {
            console.log('[MMD IdleAnimation] 正在从 API 加载待机动作列表...');
            const data = await RequestHelper.fetchJson('/api/model/mmd/animations');
            const animations = (data.success && data.animations) ? data.animations : [];

            optionsEl.innerHTML = '';
            optionsEl.onclick = (e) => e.stopPropagation();

            if (animations.length > 0) {
                animations.forEach(anim => {
                    const animPath = (typeof anim.path === 'string' ? anim.path : null)
                        || (typeof anim.url === 'string' ? anim.url : null)
                        || (typeof anim === 'string' ? anim : null);
                    if (!animPath) return;

                    const finalUrl = anim.url || animPath;
                    const displayName = anim.name || anim.filename || finalUrl.split('/').pop();
                    const isDefault = finalUrl.includes('wait03.vmd') || animPath.includes('wait03.vmd');

                    const item = document.createElement('div');
                    item.className = 'multiselect-item';
                    const checkbox = document.createElement('input');
                    checkbox.type = 'checkbox';
                    checkbox.value = finalUrl;
                    checkbox.checked = isDefault;
                    const span = document.createElement('span');
                    span.textContent = displayName;
                    item.appendChild(checkbox);
                    item.appendChild(span);
                    item.addEventListener('click', (e) => {
                        if (e.target.tagName !== 'INPUT') checkbox.checked = !checkbox.checked;
                        onIdleAnimationCheckboxChange('mmd');
                        e.stopPropagation();
                    });
                    optionsEl.appendChild(item);
                });
                console.log(`[MMD IdleAnimation] 待机动作列表加载成功，共 ${animations.length} 个动画`);
            } else {
                const empty = document.createElement('div');
                empty.className = 'multiselect-item';
                empty.textContent = t('live2d.mmdAnimation.noAnimation', '无动画');
                optionsEl.appendChild(empty);
            }

            // 绑定 header 点击
            const header = container.querySelector('.multiselect-header');
            if (header) {
                header.onclick = toggleIdleMultiselect;
                header.onkeydown = (e) => {
                    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleIdleMultiselect(e); }
                };
            }
            updateIdleMultiselectHeader(container);
        } catch (error) {
            console.error('[MMD IdleAnimation] 加载待机动作列表失败:', error);
            optionsEl.innerHTML = '';
            const errEl = document.createElement('div');
            errEl.className = 'multiselect-item';
            errEl.textContent = t('common.loadFailed', '加载失败');
            optionsEl.appendChild(errEl);
        }
    }

    /**
     * 从角色配置恢复MMD待机动作选择并播放
     */
    async function restoreMmdIdleAnimation() {
        try {
            const lanlanName = await getLanlanName();
            if (!lanlanName) return;

            const data = await RequestHelper.fetchJson('/api/characters/');
            const charData = data['猫娘']?.[lanlanName];
            let mmdIdleAnimation = charData?.mmd_idle_animations ?? charData?.mmd_idle_animation;

            if (mmdIdleAnimation == null) return;
            // 向前兼容: string -> array
            if (typeof mmdIdleAnimation === 'string') mmdIdleAnimation = mmdIdleAnimation ? [mmdIdleAnimation] : [];
            if (!Array.isArray(mmdIdleAnimation)) return;

            // 只恢复 UI 勾选状态; startIdleRotation 由模型加载完成后
            // 的 loadCharacterLighting 路径触发, 避免在模型未加载时启动 timer
            console.log('[MMD] restoreMmdIdleAnimation - mmdIdleAnimation:', mmdIdleAnimation);
            setSelectedIdleAnimations('mmd-idle-animation-multiselect', mmdIdleAnimation);
        } catch (error) {
            console.error('[MMD] 恢复待机动作失败:', error);
        }
    }


    // VRM 鼠标跟踪已移至 popup-ui 统一控制，不在外观管理页单独配置

    // ==================== MMD 控件事件绑定 ====================
    function setupMmdControlBindings() {
        // --- MMD 光照 ---
        const mmdLightSliders = [
            { el: mmdAmbientIntensitySlider, valId: 'mmd-ambient-intensity-value', fmt: v => v.toFixed(1) },
            { el: mmdDirectionalIntensitySlider, valId: 'mmd-directional-intensity-value', fmt: v => v.toFixed(1) },
            { el: mmdExposureSlider, valId: 'mmd-exposure-value', fmt: v => v.toFixed(2) }
        ];
        mmdLightSliders.forEach(({ el, valId, fmt }) => {
            if (el) {
                el.addEventListener('input', (e) => {
                    const v = parseFloat(e.target.value);
                    const valEl = document.getElementById(valId);
                    if (valEl) valEl.textContent = fmt ? fmt(v) : v;
                    applyMmdSettings();
                    window.hasUnsavedChanges = true;
                });
            }
        });

        // 颜色选择器
        [
            { el: mmdAmbientColorPicker, valId: 'mmd-ambient-color-value' },
            { el: mmdDirectionalColorPicker, valId: 'mmd-directional-color-value' }
        ].forEach(({ el, valId }) => {
            if (el) {
                el.addEventListener('input', (e) => {
                    const valEl = document.getElementById(valId);
                    if (valEl) valEl.textContent = e.target.value;
                    applyMmdSettings();
                    window.hasUnsavedChanges = true;
                });
            }
        });

        // 色调映射
        if (mmdTonemappingSelect) {
            mmdTonemappingSelect.addEventListener('change', (e) => {
                applyMmdSettings();
                // 无色调映射时禁用曝光滑块
                const value = parseInt(e.target.value);
                const isNoToneMapping = value === 0;
                if (mmdExposureSlider) {
                    mmdExposureSlider.disabled = isNoToneMapping;
                    mmdExposureSlider.style.opacity = isNoToneMapping ? '0.5' : '1';
                }
                const mmdExposureValue = document.getElementById('mmd-exposure-value');
                if (mmdExposureValue) {
                    mmdExposureValue.style.opacity = isNoToneMapping ? '0.5' : '1';
                }
                window.hasUnsavedChanges = true;
            });
        }

        // 描边开关
        if (mmdOutlineToggle) {
            mmdOutlineToggle.addEventListener('change', (e) => {
                const statusEl = document.getElementById('mmd-outline-status');
                if (statusEl) statusEl.textContent = e.target.checked ? 'ON' : 'OFF';
                applyMmdSettings();
                window.hasUnsavedChanges = true;
            });
        }

        // 像素比例、物理、鼠标跟踪 已移至 popup-ui 统一控制
    }

    function collectMmdSettings() {
        return {
            lighting: {
                ambientIntensity: mmdAmbientIntensitySlider ? parseFloat(mmdAmbientIntensitySlider.value) : 3,
                ambientColor: mmdAmbientColorPicker ? mmdAmbientColorPicker.value : '#aaaaaa',
                directionalIntensity: mmdDirectionalIntensitySlider ? parseFloat(mmdDirectionalIntensitySlider.value) : 2,
                directionalColor: mmdDirectionalColorPicker ? mmdDirectionalColorPicker.value : '#ffffff'
            },
            rendering: {
                toneMapping: mmdTonemappingSelect ? parseInt(mmdTonemappingSelect.value) : 0,
                exposure: mmdExposureSlider ? parseFloat(mmdExposureSlider.value) : 1.0,
                outline: mmdOutlineToggle ? mmdOutlineToggle.checked : true
            }
            // physics 和 cursorFollow 由 popup-ui 统一控制，不在此收集
        };
    }

    function applyMmdSettings() {
        const settings = collectMmdSettings();
        if (window.mmdManager) {
            if (typeof window.mmdManager.applySettings === 'function') {
                window.mmdManager.applySettings(settings);
            }
        }
        // Merge into existing localStorage to preserve popup-ui fields (physics, pixelRatio, cursorFollow)
        try {
            const existing = JSON.parse(localStorage.getItem('mmdSettings') || '{}');
            if (settings.lighting) existing.lighting = settings.lighting;
            if (settings.rendering) {
                existing.rendering = Object.assign(existing.rendering || {}, settings.rendering);
            }
            localStorage.setItem('mmdSettings', JSON.stringify(existing));
        } catch (e) { /* ignore */ }
    }

    function loadMmdSettingsToUI() {
        try {
            const saved = localStorage.getItem('mmdSettings');
            if (!saved) return;
            const s = JSON.parse(saved);
            if (s.lighting) {
                if (mmdAmbientIntensitySlider && s.lighting.ambientIntensity != null) {
                    mmdAmbientIntensitySlider.value = s.lighting.ambientIntensity;
                    const el = document.getElementById('mmd-ambient-intensity-value');
                    if (el) el.textContent = s.lighting.ambientIntensity.toFixed ? s.lighting.ambientIntensity.toFixed(1) : s.lighting.ambientIntensity;
                }
                if (mmdAmbientColorPicker && s.lighting.ambientColor) {
                    mmdAmbientColorPicker.value = s.lighting.ambientColor;
                    const el = document.getElementById('mmd-ambient-color-value');
                    if (el) el.textContent = s.lighting.ambientColor;
                }
                if (mmdDirectionalIntensitySlider && s.lighting.directionalIntensity != null) {
                    mmdDirectionalIntensitySlider.value = s.lighting.directionalIntensity;
                    const el = document.getElementById('mmd-directional-intensity-value');
                    if (el) el.textContent = s.lighting.directionalIntensity.toFixed ? s.lighting.directionalIntensity.toFixed(1) : s.lighting.directionalIntensity;
                }
                if (mmdDirectionalColorPicker && s.lighting.directionalColor) {
                    mmdDirectionalColorPicker.value = s.lighting.directionalColor;
                    const el = document.getElementById('mmd-directional-color-value');
                    if (el) el.textContent = s.lighting.directionalColor;
                }
            }
            if (s.rendering) {
                if (mmdTonemappingSelect && s.rendering.toneMapping != null) {
                    // 统一使用数值类型，避免字符串和数字混用
                    const toneMappingValue = Number(s.rendering.toneMapping);
                    mmdTonemappingSelect.value = toneMappingValue.toString();
                    // 根据色调映射设置曝光滑块禁用状态
                    const isNoToneMapping = toneMappingValue === 0;
                    if (mmdExposureSlider) {
                        mmdExposureSlider.disabled = isNoToneMapping;
                        mmdExposureSlider.style.opacity = isNoToneMapping ? '0.5' : '1';
                    }
                    const mmdExposureValue = document.getElementById('mmd-exposure-value');
                    if (mmdExposureValue) {
                        mmdExposureValue.style.opacity = isNoToneMapping ? '0.5' : '1';
                    }
                }
                if (mmdExposureSlider && s.rendering.exposure != null) {
                    mmdExposureSlider.value = s.rendering.exposure;
                    const el = document.getElementById('mmd-exposure-value');
                    if (el) el.textContent = s.rendering.exposure.toFixed(2);
                }
                if (mmdOutlineToggle && s.rendering.outline != null) {
                    mmdOutlineToggle.checked = s.rendering.outline;
                    const el = document.getElementById('mmd-outline-status');
                    if (el) el.textContent = s.rendering.outline ? 'ON' : 'OFF';
                }
            }
            // physics 和 cursorFollow 由 popup-ui 统一控制，不在此加载
        } catch (e) {
            console.warn('[MMD Settings] 加载UI设置失败:', e);
        }
    }

    setupMmdControlBindings();
    loadMmdSettingsToUI();

    /**
     * 从服务器加载MMD设置并应用到UI和场景
     * 在切换到MMD模式时调用
     * @returns {Promise} 设置加载完成的 Promise
     */
    function loadMmdSettingsFromServer() {
        // 如果已有进行中的加载，返回同一个 Promise，避免重复请求
        if (_mmdSettingsLoadPromise) {
            return _mmdSettingsLoadPromise;
        }
        _mmdSettingsLoadPromise = (async () => {
            try {
                // 优先使用 getLanlanName() 获取角色名，fallback 到 DOM
                let lanlanName = await getLanlanName();
                if (!lanlanName) {
                    lanlanName = document.getElementById('lanlan-name')?.textContent?.trim();
                }
                // 角色名仍然缺失时，应用本地缓存的设置而非静默返回
                if (!lanlanName) {
                    console.warn('[MMD Settings] 角色名缺失，应用本地缓存设置');
                    loadMmdSettingsToUI();
                    setTimeout(() => applyMmdSettings(), 100);
                    return;
                }
                const result = await RequestHelper.fetchJson(
                    `/api/characters/catgirl/${encodeURIComponent(lanlanName)}/mmd_settings`
                );
                if (result.success && result.settings) {
                    // 写入 localStorage 并应用到UI
                    localStorage.setItem('mmdSettings', JSON.stringify(result.settings));
                    loadMmdSettingsToUI();
                    // 延迟应用到场景（等待 MMD 模型初始化）
                    setTimeout(() => applyMmdSettings(), 500);
                    console.log('[MMD Settings] 已从服务器加载MMD设置');
                }
            } catch (e) {
                console.warn('[MMD Settings] 从服务器加载MMD设置失败，使用本地缓存:', e);
                // 服务器加载失败时也尝试应用本地缓存
                loadMmdSettingsToUI();
                setTimeout(() => applyMmdSettings(), 100);
            } finally {
                _mmdSettingsLoadPromise = null;
            }
        })();
        return _mmdSettingsLoadPromise;
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
            // 统一使用数值类型，避免字符串和数字混用
            const toneMappingValue = Number(lighting.toneMapping);
            tonemappingSelect.value = toneMappingValue.toString();
            if (vrmManager.renderer) {
                vrmManager.renderer.toneMapping = toneMappingValue;
            }
            // 根据色调映射设置曝光滑块禁用状态
            const isNoToneMapping = toneMappingValue === 0;
            if (exposureSlider) {
                exposureSlider.disabled = isNoToneMapping;
                exposureSlider.style.opacity = isNoToneMapping ? '0.5' : '1';
            }
            if (exposureValue) {
                exposureValue.style.opacity = isNoToneMapping ? '0.5' : '1';
            }
        }

        // 恢复描边粗细
        const vrmOutlineWidthSlider = document.getElementById('vrm-outline-width-slider');
        if (vrmOutlineWidthSlider && lighting.outlineWidthScale !== undefined) {
            const scale = Number(lighting.outlineWidthScale);
            if (!Number.isNaN(scale)) {
                vrmOutlineWidthSlider.value = scale;
                applyVrmOutlineWidth(scale);
            }
        }

        // 强制渲染一次，确保光照立即生效
        if (vrmManager?.renderer && vrmManager?.scene && vrmManager?.camera) {
            vrmManager.renderer.render(vrmManager.scene, vrmManager.camera);
        }
    }

    // 加载角色的打光配置并应用
    // 【保留但简化】只加载角色的”直接打光配置”，去掉了预设逻辑
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

            // 加载待机动作选项并恢复保存的选择（多选）
            await loadIdleAnimationOptions();
            let vrmIdleAnims = charData?.idleAnimations ?? charData?.idleAnimation;
            if (vrmIdleAnims != null) {
                // 向前兼容: string -> array
                if (typeof vrmIdleAnims === 'string') vrmIdleAnims = vrmIdleAnims ? [vrmIdleAnims] : [];
                if (Array.isArray(vrmIdleAnims)) {
                    setSelectedIdleAnimations('vrm-idle-animation-multiselect', vrmIdleAnims);
                    if (vrmIdleAnims.length > 0) {
                        startIdleRotation('vrm', vrmIdleAnims);
                    } else {
                        stopIdleRotation('vrm');
                    }
                    console.log('[VRM] 已恢复待机动作:', vrmIdleAnims);
                }
            }

            // 加载MMD待机动作选项并恢复保存的选择（仅对 MMD 角色生效）
            const isMmdCharacter = charData?.live3d_sub_type === 'mmd' || !!charData?.mmd;
            if (isMmdCharacter) {
                await loadMmdIdleAnimationOptions();
                let mmdIdleAnims = charData?.mmd_idle_animations ?? charData?.mmd_idle_animation;
                if (mmdIdleAnims != null) {
                    // 向前兼容: string -> array
                    if (typeof mmdIdleAnims === 'string') mmdIdleAnims = mmdIdleAnims ? [mmdIdleAnims] : [];
                    if (Array.isArray(mmdIdleAnims)) {
                        setSelectedIdleAnimations('mmd-idle-animation-multiselect', mmdIdleAnims);
                        if (mmdIdleAnims.length > 0) {
                            startIdleRotation('mmd', mmdIdleAnims);
                        } else {
                            stopIdleRotation('mmd');
                        }
                        console.log('[MMD] 已恢复待机动作:', mmdIdleAnims);
                    }
                }
            }
        } catch (error) {
            console.error('加载打光配置失败:', error);
        }
    }

    // 注意：VRM 模型列表已在 switchModelDisplay('live3d') 中加载，无需重复加载
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
        cancelExpressionPreview({ reapplyPersistent: true });

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

            // 初始化触摸配置
            if (typeof InitializationTouchSet === 'function') {
                try {
                    await InitializationTouchSet();
                } catch (error) {
                    console.warn('[ModelManager] 初始化触摸配置失败:', error);
                }
            }

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
                    cancelExpressionPreview({ reapplyPersistent: false });
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
        const live2dManager = window.live2dManager;
        let previewSuspendReason = null;
        let resumePreviewSuspend = () => {};

        try {
            // 清除之前的表情预览恢复定时器
            cancelExpressionPreview({ reapplyPersistent: false });
            // 使在途的动作预览 fetch 回调失效，防止异步返回后设置恢复定时器打断表情
            if (window._motionPreviewRestoreTimer) {
                clearTimeout(window._motionPreviewRestoreTimer);
                window._motionPreviewRestoreTimer = null;
            }
            window._currentMotionPreviewId = null;

            // 创建预览标记，防止快速连续点击时并发 await 导致多个定时器共存
            const previewToken = nextExpressionPreviewToken();
            window._currentExpressionPreviewToken = previewToken;
            previewSuspendReason = `preview-expression-${previewToken}`;
            let previewSuspendResumed = false;
            resumePreviewSuspend = (reapply = false) => {
                if (previewSuspendResumed) return;
                previewSuspendResumed = true;
                if (window._resumeExpressionPreviewSuspend === resumePreviewSuspend) {
                    window._resumeExpressionPreviewSuspend = null;
                }
                if (!live2dManager || typeof live2dManager.resumePersistentExpressions !== 'function') return;
                live2dManager.resumePersistentExpressions(previewSuspendReason);
                const canReapply =
                    reapply &&
                    typeof live2dManager.applyPersistentExpressionsNative === 'function' &&
                    (typeof live2dManager.arePersistentExpressionsSuspended !== 'function' ||
                        !live2dManager.arePersistentExpressionsSuspended());
                if (canReapply) {
                    live2dManager.applyPersistentExpressionsNative(false).catch(() => {});
                }
            };

            if (live2dManager && typeof live2dManager.suspendPersistentExpressions === 'function') {
                live2dManager.suspendPersistentExpressions(previewSuspendReason, { clearCurrent: true });
            }
            window._resumeExpressionPreviewSuspend = resumePreviewSuspend;

            // expression 方法是异步的，需要使用 await
            // 注意：Live2D SDK 的 expression 方法可能返回 null/undefined 但仍然成功播放
            const result = await currentModel.expression(expressionName);

            // await 返回后检查标记是否仍然匹配（可能已被新的预览覆盖）
            if (window._currentExpressionPreviewToken !== previewToken) {
                resumePreviewSuspend(false);
                return;
            }

            // Live2D SDK 的 expression 方法成功时可能返回 falsy 值，这里改为检查是否抛出异常
            // 如果没有抛出异常，就认为播放成功
            showStatus(t('live2d.playingExpression', `播放表情: ${expressionName}`, { expression: expressionName }), 1000);

            // 设置自动恢复定时器：5秒后平滑恢复到初始状态
            window._expressionPreviewRestoreTimer = setTimeout(() => {
                window._expressionPreviewRestoreTimer = null;
                if (window._currentExpressionPreviewToken !== previewToken) return; // 已被新的预览覆盖
                window._currentExpressionPreviewToken = null;
                resumePreviewSuspend(false);
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
            if (previewSuspendReason) {
                resumePreviewSuspend(true);
            }
            if (window._currentExpressionPreviewToken !== previewToken) {
                return;
            }
            console.error('播放表情失败:', error);
            showStatus(t('live2d.playExpressionFailed', `播放表情失败: ${expressionName}`, { expression: expressionName }), 2000);
        }
    });

    savePositionBtn.addEventListener('click', async () => {
        // Live3D模式下，即使模型未加载，只要有选择的模型就可以保存
        if (currentModelType === 'live3d') {
            const selectedModelPath = vrmModelSelect ? vrmModelSelect.value : null;
            if (!selectedModelPath) {
                showStatus(t('live2d.pleaseSelectModel', '请先选择一个模型'), 2000);
                return;
            }
            // 如果没有currentModelInfo，使用当前选择的模型路径创建
            if (!currentModelInfo) {
                const selOpt = vrmModelSelect.options[vrmModelSelect.selectedIndex];
                const subType = selOpt ? selOpt.getAttribute('data-sub-type') : null;
                currentModelInfo = {
                    name: selectedModelPath,
                    path: selectedModelPath,
                    type: subType || 'vrm'
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
        if (currentModelType === 'live3d') {
            // Live3D 模式：保存模型设置
            // 优先使用 path（含完整相对路径），name 仅为文件名
            modelSuccess = await saveModelToCharacter(currentModelInfo.path || currentModelInfo.name, null, null);
        } else {
            // Live2D 模式：保存位置、缩放和模型设置
            if (!live2dModel) {
                showStatus(t('live2d.pleaseLoadModel', '请先加载模型'), 2000);
                return;
            }

            // 保存位置和缩放
            positionSuccess = await window.live2dManager.saveUserPreferences(
                currentModelInfo.path,
                { x: live2dModel.x, y: live2dModel.y },
                { x: live2dModel.scale.x, y: live2dModel.scale.y }
            );

            // 保存模型设置到角色，同时传入item_id
            modelSuccess = await saveModelToCharacter(currentModelInfo.name, currentModelInfo.item_id);
        }

        if (currentModelType === 'live3d') {
            // Live3D 模式：只显示模型保存结果
            if (modelSuccess) {
                showStatus(t('live2d.settingsSaved', '模型设置保存成功!'), 2000);
                window.hasUnsavedChanges = false;
                window._savedModelSnapshot = captureSettingsSnapshot();
                window._modelManagerHasSaved = true;
            } else {
                showStatus(t('live2d.saveFailedGeneral', '保存失败!'), 2000);
            }
        } else {
            // Live2D 模式：显示位置和模型保存结果
            if (positionSuccess && modelSuccess) {
                showStatus(t('live2d.settingsSaved', '位置和模型设置保存成功!'), 2000);
                window.hasUnsavedChanges = false; // 保存成功后重置标志
                window._savedModelSnapshot = captureSettingsSnapshot();
                window._modelManagerHasSaved = true;
                // 不在保存时立即通知主页，而是在返回主页时通知
                // sendMessageToMainPage('reload_model');
            } else if (positionSuccess) {
                showStatus(t('live2d.positionSavedModelFailed', '位置保存成功，模型设置保存失败!'), 2000);
                // 位置偏好已保存，主界面如触发重载可恢复位置；但仅在用户退出时才通知
                window._modelManagerHasSaved = true;
            } else if (modelSuccess) {
                showStatus(t('live2d.modelSavedPositionFailed', '模型设置保存成功，位置保存失败!'), 2000);
                window._savedModelSnapshot = captureSettingsSnapshot();
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

    // Live3D 情感配置按钮（VRM/MMD 统一）
    const live3dEmotionConfigBtn = document.getElementById('live3d-emotion-config-btn');
    if (live3dEmotionConfigBtn) {
        live3dEmotionConfigBtn.addEventListener('click', () => {
            const width = 900;
            const height = 800;
            const left = (screen.width - width) / 2;
            const top = (screen.height - height) / 2;

            // 根据当前模型类型决定打开哪个情感管理器
            let url = '/vrm_emotion_manager';
            let winName = 'vrmEmotionManager';

            if (currentModelType === 'live3d' && vrmModelSelect) {
                const selectedOpt = vrmModelSelect.options[vrmModelSelect.selectedIndex];
                const subType = selectedOpt ? selectedOpt.getAttribute('data-sub-type') : null;
                if (subType === 'mmd' || (currentModelInfo && currentModelInfo.type === 'mmd')) {
                    url = '/mmd_emotion_manager';
                    winName = 'mmdEmotionManager';
                }
            }

            window.open(
                url,
                winName,
                `width=${width},height=${height},left=${left},top=${top},resizable=yes,scrollbars=yes`
            );
        });
    }

    // 返回主页/关闭按钮 — 移除早期安全网处理器，由完整逻辑接管
    if (_earlyBackBtn && _earlyBackHandler) {
        _earlyBackBtn.removeEventListener('click', _earlyBackHandler);
    }
    backToMainBtn.addEventListener('click', async () => {
        // 退出前：比对当前设置和已保存快照，完全一致则视为无更改
        if (window.hasUnsavedChanges && window._savedModelSnapshot) {
            if (snapshotsEqual(window._savedModelSnapshot, captureSettingsSnapshot())) {
                window.hasUnsavedChanges = false;
            }
        }
        if (window.hasUnsavedChanges) {
            const message = t('dialogs.unsavedChanges', '您有未保存的设置，确定要离开吗？');
            const title = t('dialogs.confirmLeave', '确认离开');
            const confirmLeave = await showConfirm(message, title, { danger: true });
            if (!confirmLeave) {
                return; // 用户取消，不离开
            }
            // 用户确认离开，重置未保存状态，避免被 beforeunload 拦截
            window.hasUnsavedChanges = false;
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
        if (currentModelType !== 'live2d') {
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

    // MMD 模型文件上传
    if (mmdFileUpload) {
        mmdFileUpload.addEventListener('change', async (e) => {
            const file = e.target.files[0];
            if (!file) return;

            const ext = file.name.toLowerCase();
            const isZip = ext.endsWith('.zip');
            if (!ext.endsWith('.pmx') && !ext.endsWith('.pmd') && !isZip) {
                showStatus('请选择 .pmx、.pmd 或 .zip 文件', 3000);
                mmdFileUpload.value = '';
                return;
            }

            showStatus('正在上传MMD模型...', 0);
            setControlsDisabled(true);
            try {
                const formData = new FormData();
                formData.append('file', file);
                const uploadUrl = isZip ? '/api/model/mmd/upload_zip' : '/api/model/mmd/upload';
                const response = await fetch(uploadUrl, {
                    method: 'POST',
                    body: formData
                });
                const result = await response.json();
                if (result.success) {
                    showStatus(`MMD模型 ${result.filename || file.name} 上传成功`, 2000);
                    if (currentModelType === 'live3d') {
                        // 保存当前选中值，loadLive3DModels 会重建 vrmModelSelect
                        const prevValue = vrmModelSelect ? vrmModelSelect.value : '';
                        await loadLive3DModels();
                        // 恢复之前的选中项（如果仍存在）
                        if (prevValue && vrmModelSelect) {
                            const match = Array.from(vrmModelSelect.options).find(opt => opt.value === prevValue);
                            if (match) {
                                vrmModelSelect.value = prevValue;
                                updateVRMModelSelectButtonText();
                            }
                        }
                    } else {
                        await loadMMDModels();
                    }
                } else {
                    showStatus(`上传失败: ${result.error}`, 3000);
                }
            } catch (error) {
                console.error('上传MMD模型失败:', error);
                showStatus(`上传失败: ${error.message}`, 3000);
            } finally {
                setControlsDisabled(false);
                mmdFileUpload.value = '';
            }
        });
    }

    // MMD 动画文件上传
    if (mmdAnimationFileUpload) {
        mmdAnimationFileUpload.addEventListener('change', async (e) => {
            const file = e.target.files[0];
            if (!file) return;

            // 防止重复上传
            if (isMmdAnimationUploading) {
                mmdAnimationFileUpload.value = '';
                return;
            }

            if (!file.name.toLowerCase().endsWith('.vmd')) {
                showStatus(t('live2d.mmdAnimation.selectVmdFile', '请选择 .vmd 文件'), 3000);
                mmdAnimationFileUpload.value = '';
                return;
            }

            isMmdAnimationUploading = true;
            if (uploadMmdAnimationBtn) uploadMmdAnimationBtn.disabled = true;
            showStatus(t('live2d.mmdAnimation.uploading', '正在上传VMD动画...'), 0);
            setControlsDisabled(true);
            try {
                const formData = new FormData();
                formData.append('file', file);
                const response = await fetch('/api/model/mmd/upload_animation', {
                    method: 'POST',
                    body: formData
                });
                const result = await response.json();
                if (result.success) {
                    // 保存当前选择状态
                    const savedAnimUrl = mmdAnimationSelect ? mmdAnimationSelect.value : '';
                    const savedIsPlaying = isMmdAnimationPlaying;
                    
                    await loadMMDAnimations();
                    
                    // 恢复选择状态
                    if (mmdAnimationSelect) {
                        // 优先选择新上传的动画，否则恢复之前的选择
                        const newFilename = result.filename || file.name;
                        const newOption = Array.from(mmdAnimationSelect.options).find(opt => {
                            const optFilename = opt.getAttribute('data-filename') || opt.textContent;
                            return optFilename === newFilename;
                        });
                        
                        if (newOption) {
                            mmdAnimationSelect.value = newOption.value;
                        } else if (savedAnimUrl) {
                            const optionExists = Array.from(mmdAnimationSelect.options).some(opt => opt.value === savedAnimUrl);
                            if (optionExists) {
                                mmdAnimationSelect.value = savedAnimUrl;
                            }
                        }
                        updateMMDAnimationSelectButtonText();
                    }
                    
                    // 恢复播放状态
                    isMmdAnimationPlaying = savedIsPlaying;
                    
                    showStatus(t('live2d.mmdAnimation.uploadSuccess', 'VMD动画 {{name}} 上传成功', { name: result.filename || file.name }), 2000);
                } else {
                    showStatus(t('live2d.mmdAnimation.uploadFailed', '上传失败: {{error}}', { error: result.error }), 3000);
                }
            } catch (error) {
                console.error('上传VMD动画失败:', error);
                showStatus(t('live2d.mmdAnimation.uploadFailed', '上传失败: {{error}}', { error: error.message }), 3000);
            } finally {
                isMmdAnimationUploading = false;
                if (uploadMmdAnimationBtn) uploadMmdAnimationBtn.disabled = false;
                setControlsDisabled(false);
                mmdAnimationFileUpload.value = '';
            }
        });
    }

    // VMD动画上传按钮点击事件
    if (uploadMmdAnimationBtn) {
        uploadMmdAnimationBtn.addEventListener('click', () => {
            if (mmdAnimationFileUpload) {
                mmdAnimationFileUpload.click();
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

    // VRM/ZIP模型上传（单个文件）
    vrmFileUpload.addEventListener('change', async (e) => {
        const files = Array.from(e.target.files);
        if (files.length === 0) return;

        // 检查是否为 ZIP 包（MMD 模型 + 纹理）
        const zipFile = files.find(f => f.name.toLowerCase().endsWith('.zip'));
        if (zipFile) {
            showStatus('正在上传MMD模型包（ZIP）...', 0);
            setControlsDisabled(true);
            try {
                const formData = new FormData();
                formData.append('file', zipFile);
                const response = await fetch('/api/model/mmd/upload_zip', {
                    method: 'POST',
                    body: formData
                });
                const result = await response.json();
                if (result.success) {
                    showStatus(`${result.message}`, 3000);
                    await loadLive3DModels();
                } else {
                    showStatus(`上传失败: ${result.error}`, 3000);
                }
            } catch (error) {
                console.error('上传MMD ZIP包失败:', error);
                showStatus(`上传失败: ${error.message}`, 3000);
            } finally {
                setControlsDisabled(false);
                vrmFileUpload.value = '';
            }
            return;
        }

        // 检查文件类型（VRM）
        const vrmFile = files.find(f => f.name.toLowerCase().endsWith('.vrm'));
        if (!vrmFile) {
            uploadStatus.textContent = t('live2d.uploadVRMFailed', '✗ 请选择 .vrm 或 .zip 文件', { error: '请选择 .vrm 或 .zip 文件' });
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

                // 重新加载模型列表（Live3D 模式下需同时包含 MMD 模型）
                setTimeout(async () => {
                    try {
                        // 保存当前选中值，loadLive3DModels/loadVRMModels 会重建 vrmModelSelect
                        const prevValue = vrmModelSelect ? vrmModelSelect.value : '';
                        if (currentModelType === 'live3d') {
                            await loadLive3DModels();
                        } else {
                            await loadVRMModels();
                        }
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
                            } else if (prevValue) {
                                // 新模型未匹配到，恢复之前的选中项
                                const restore = Array.from(vrmModelSelect.options).find(opt => opt.value === prevValue);
                                if (restore) {
                                    vrmModelSelect.value = prevValue;
                                    updateVRMModelSelectButtonText();
                                }
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
    var selectedDeleteModels = new Set();

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

            // 并行加载 Live2D、VRM、MMD 用户模型
            const [live2dResult, vrmResult, mmdResult] = await Promise.all([
                RequestHelper.fetchJson('/api/live2d/user_models').catch(() => ({ success: false })),
                RequestHelper.fetchJson('/api/model/vrm/models').catch(() => ({ success: false })),
                RequestHelper.fetchJson('/api/model/mmd/models').catch(() => ({ success: false }))
            ]);

            // 整合所有用户模型到统一列表
            const allUserModels = [];

            // Live2D 模型（注意：/api/live2d/user_models 也会返回 VRM 模型，需要过滤掉）
            if (live2dResult.success && Array.isArray(live2dResult.models)) {
                live2dResult.models.forEach(m => {
                    // 过滤掉 VRM 和 MMD 模型（它们有自己的 API）
                    if (m.type === 'vrm' || m.type === 'mmd') return;
                    allUserModels.push({
                        id: 'live2d:' + m.name,
                        name: m.name,
                        displayName: m.name.replace(/\.model3$/i, ''),
                        type: 'live2d',
                        typeLabel: 'Live2D',
                        source: m.source === 'user_documents' ? t('live2d.userDocuments', '用户文档') : t('live2d.localUpload', '本地上传'),
                        deleteKey: m.name,
                        path: m.path || ''
                    });
                });
            }

            // VRM 模型（只显示 user 位置）
            if (vrmResult.success && Array.isArray(vrmResult.models)) {
                vrmResult.models.filter(m => m.location === 'user').forEach(m => {
                    allUserModels.push({
                        id: 'vrm:' + m.url,
                        name: m.name,
                        displayName: m.name,
                        type: 'vrm',
                        typeLabel: 'VRM',
                        source: m.filename,
                        deleteKey: m.url,
                        url: m.url
                    });
                });
            }

            // MMD 模型（只显示 user 位置）
            if (mmdResult.success && Array.isArray(mmdResult.models)) {
                mmdResult.models.filter(m => m.location === 'user').forEach(m => {
                    allUserModels.push({
                        id: 'mmd:' + m.url,
                        name: m.name,
                        displayName: m.name,
                        type: 'mmd',
                        typeLabel: 'MMD',
                        source: m.filename,
                        deleteKey: m.url,
                        url: m.url
                    });
                });
            }

            if (allUserModels.length === 0) {
                userModelList.innerHTML = '<div class="empty-message">' + t('live2d.noUserModels', '暂无可删除的用户模型') + '</div>';
                return;
            }

            userModelList.innerHTML = '';
            // 获取当前使用中的模型标识
            const currentLive2DName = currentModelInfo ? currentModelInfo.name : '';
            const currentLive3DUrl = (typeof vrmModelSelect !== 'undefined' && vrmModelSelect) ? vrmModelSelect.value : '';

            allUserModels.forEach(model => {
                const item = document.createElement('div');
                item.className = 'model-item';

                // 检查是否正在使用
                let isBound = false;
                if (model.type === 'live2d') {
                    isBound = currentLive2DName === model.name;
                } else {
                    isBound = currentLive3DUrl === model.url;
                }

                const checkbox = document.createElement('input');
                checkbox.type = 'checkbox';
                checkbox.id = 'del-' + model.id;
                checkbox.value = model.id;
                checkbox.setAttribute('data-type', model.type);
                checkbox.setAttribute('data-delete-key', model.deleteKey);

                if (isBound) {
                    checkbox.disabled = true;
                    checkbox.title = t('live2d.cannotDeleteBoundModel', '无法删除当前正在使用的模型');
                }

                const typeBadge = document.createElement('span');
                typeBadge.className = 'model-source';
                typeBadge.style.fontWeight = 'bold';
                typeBadge.style.marginRight = '4px';
                if (model.type === 'vrm') {
                    typeBadge.style.color = '#4a9eff';
                } else if (model.type === 'mmd') {
                    typeBadge.style.color = '#ff6b9d';
                } else {
                    typeBadge.style.color = '#66bb6a';
                }
                typeBadge.textContent = `[${model.typeLabel}]`;

                const label = document.createElement('label');
                label.setAttribute('for', 'del-' + model.id);
                label.textContent = model.displayName;

                const sourceSpan = document.createElement('span');
                sourceSpan.className = 'model-source';
                sourceSpan.textContent = model.source;

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
                item.appendChild(typeBadge);
                item.appendChild(label);
                item.appendChild(sourceSpan);
                userModelList.appendChild(item);
            });
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

        // 解析选中模型的 type:key 格式
        function parseModelId(id) {
            const idx = id.indexOf(':');
            if (idx === -1) return { type: 'live2d', key: id };
            return { type: id.substring(0, idx), key: id.substring(idx + 1) };
        }

        // 安全防护：移除当前绑定的模型，不允许删除
        const currentLive2DName = currentModelInfo ? currentModelInfo.name : '';
        const currentLive3DUrl = (typeof vrmModelSelect !== 'undefined' && vrmModelSelect) ? vrmModelSelect.value : '';
        for (const modelId of [...selectedDeleteModels]) {
            const { type, key } = parseModelId(modelId);
            const isBound = (type === 'live2d' && key === currentLive2DName) ||
                            (type !== 'live2d' && key === currentLive3DUrl);
            if (isBound) {
                selectedDeleteModels.delete(modelId);
                showStatus(t('live2d.cannotDeleteBoundModel', '无法删除当前正在使用的模型'), 2000);
                updateConfirmDeleteButton();
            }
        }
        if (selectedDeleteModels.size === 0) return;

        const message = t('live2d.confirmDelete', '确定要删除选中的 {{count}} 个模型吗？此操作不可恢复。', { count: selectedDeleteModels.size });
        const title = t('live2d.deleteModelTitle', '删除已导入模型');
        const confirmDelete = await showConfirm(message, title, { danger: true });
        if (!confirmDelete) return;

        confirmDeleteBtn.disabled = true;
        confirmDeleteBtn.textContent = t('live2d.deleting', '删除中...');

        let successCount = 0;
        let failCount = 0;
        let lastErrorMessage = '';
        let deletedLive2D = false;
        let deletedLive3D = false;

        for (const modelId of selectedDeleteModels) {
            const { type, key } = parseModelId(modelId);
            try {
                let result;
                if (type === 'live2d') {
                    result = await RequestHelper.fetchJson(
                        `/api/live2d/model/${encodeURIComponent(key)}`,
                        { method: 'DELETE' }
                    );
                    deletedLive2D = true;
                } else if (type === 'vrm') {
                    result = await RequestHelper.fetchJson(
                        '/api/model/vrm/model',
                        { method: 'DELETE', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url: key }) }
                    );
                    deletedLive3D = true;
                } else if (type === 'mmd') {
                    result = await RequestHelper.fetchJson(
                        '/api/model/mmd/model',
                        { method: 'DELETE', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url: key }) }
                    );
                    deletedLive3D = true;
                }
                if (result && result.success) {
                    successCount++;
                } else {
                    console.error(`Failed to delete model ${modelId}:`, result && result.error);
                    if (result && result.error) lastErrorMessage = String(result.error);
                    failCount++;
                }
            } catch (error) {
                console.error(`Error deleting model ${modelId}:`, error);
                lastErrorMessage = error && error.message ? String(error.message) : String(error);
                failCount++;
            }
        }

        await loadUserModels();
        selectedDeleteModels.clear();
        updateConfirmDeleteButton();

        // 刷新 Live2D 模型列表
        if (deletedLive2D) {
            try {
                availableModels = await RequestHelper.fetchJson('/api/live2d/models');
                modelSelect.innerHTML = `<option value="">${t('live2d.pleaseSelectModel', '选择模型')}</option>`;
                availableModels.forEach(model => {
                    const option = document.createElement('option');
                    option.value = model.name;
                    option.textContent = model.display_name || model.name;
                    if (model.item_id) {
                        option.dataset.itemId = model.item_id;
                    }
                    modelSelect.appendChild(option);
                });

                if (successCount > 0 && currentLive2DName) {
                    const wasDeleted = [...selectedDeleteModels].some(id => {
                        const p = parseModelId(id);
                        return p.type === 'live2d' && p.key === currentLive2DName;
                    });
                    // 注意：上面的 safety check 已阻止删当前模型，这里是 fallback
                }
            } catch (e) {
                console.error('重新加载Live2D模型列表失败:', e);
            }
        }

        // 刷新 Live3D 模型下拉（VRM/MMD）
        if (deletedLive3D) {
            try {
                if (typeof loadLive3DModels === 'function') await loadLive3DModels();
                if (typeof loadMMDModels === 'function') await loadMMDModels();
            } catch (e) {
                console.error('重新加载Live3D模型列表失败:', e);
            }
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
                const activePersistentExpression = Array.isArray(persistentExpressions) && persistentExpressions.length > 0
                    ? persistentExpressions[persistentExpressions.length - 1]
                    : null;
                if (activePersistentExpression) {
                    persistentList.innerHTML = '';
                    const item = document.createElement('div');
                    item.className = 'persistent-item';
                    const fileName = activePersistentExpression.split('/').pop().replace('.exp3.json', '');
                    const nameSpan = document.createElement('span');
                    nameSpan.textContent = fileName;
                    const deleteBtn = document.createElement('button');
                    deleteBtn.className = 'persistent-delete-btn';
                    deleteBtn.textContent = t('live2d.delete', '删除');
                    deleteBtn.addEventListener('click', () => removePersistentExpression(activePersistentExpression));
                    item.appendChild(nameSpan);
                    item.appendChild(deleteBtn);
                    persistentList.appendChild(item);
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

            const currentPersistentExpressions = Array.isArray(currentConfig.expressions['常驻'])
                ? currentConfig.expressions['常驻']
                : [];
            const previousPersistentExpression = currentPersistentExpressions.length > 0
                ? currentPersistentExpressions[currentPersistentExpressions.length - 1]
                : null;

            // 检查是否已存在
            if (previousPersistentExpression === selectedFile) {
                showStatus(t('live2d.persistentExpressionExists', '该表情已添加为常驻表情'), 2000);
                persistentSelect.value = '';
                return; // 注意：这里return后会在finally中恢复disabled状态
            }

            // 单选：选择新表情时直接替换旧的常驻表情
            currentConfig.expressions['常驻'] = [selectedFile];

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
                if (previousPersistentExpression) {
                    showStatus(t('live2d.updatedPersistentExpression', '已更新常驻表情'), 2000);
                } else {
                    showStatus(t('live2d.persistentExpressionAdded', '常驻表情已添加'), 2000);
                }
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
                const currentPersistentExpressions = Array.isArray(currentConfig.expressions['常驻'])
                    ? currentConfig.expressions['常驻']
                    : [];
                const activePersistentExpression = currentPersistentExpressions.length > 0
                    ? currentPersistentExpressions[currentPersistentExpressions.length - 1]
                    : null;
                if (activePersistentExpression === file) {
                    currentConfig.expressions['常驻'] = [];

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
            let modelType = catgirlConfig.model_type || (hasValidVRMPath ? 'live3d' : 'live2d');
            // 兼容旧配置：'vrm' 统一为 'live3d'
            if (modelType === 'vrm') modelType = 'live3d';

            // 如果模型类型是 Live3D 但没有任何有效模型路径（VRM/MMD），自动修复配置
            const hasValidMMDPath = !!(catgirlConfig.mmd && (typeof catgirlConfig.mmd === 'string' ? catgirlConfig.mmd : catgirlConfig.mmd.model_path));

            // 确定 Live3D 子类型（VRM 或 MMD）
            let live3dSubType = '';
            if (modelType === 'live3d') {
                if (hasValidMMDPath) {
                    live3dSubType = 'mmd';
                } else if (hasValidVRMPath) {
                    live3dSubType = 'vrm';
                }
            }

            if (modelType === 'live3d' && !hasValidVRMPath && !hasValidMMDPath) {
                console.warn(`[模型管理] 角色 ${lanlanName} 的模型类型设置为 Live3D，但模型路径无效或未设置，自动修复为 Live2D:`, catgirlConfig.vrm);
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

            // 先切换模型类型，清理旧模型资源（传递 live3d 子类型）
            await switchModelDisplay(modelType, live3dSubType);

            // 只有当模型类型是 Live3D 且存在有效的 VRM/MMD 路径时才加载
            if (modelType === 'live3d' && (hasValidVRMPath || hasValidMMDPath)) {
                // Live3D 模型
                // 注意：switchModelDisplay 已经等待 loadLive3DModels() 完成，此时合并列表已就绪

                if (hasValidMMDPath && vrmModelSelect) {
                    // MMD 模型：在合并列表中查找 [MMD] 选项
                    const mmdPath = typeof catgirlConfig.mmd === 'string' ? catgirlConfig.mmd : catgirlConfig.mmd.model_path;
                    const mmdFilename = mmdPath.split(/[/\\]/).pop();
                    // 优先完整路径匹配，其次文件名匹配
                    const matchedOption = Array.from(vrmModelSelect.options).find(opt => {
                        if (!opt.value || opt.getAttribute('data-sub-type') !== 'mmd') return false;
                        return opt.value === mmdPath;
                    }) || Array.from(vrmModelSelect.options).find(opt => {
                        if (!opt.value || opt.getAttribute('data-sub-type') !== 'mmd') return false;
                        return opt.value.endsWith(mmdFilename);
                    });
                    if (matchedOption) {
                        vrmModelSelect.value = matchedOption.value;
                        vrmModelSelect.dispatchEvent(new Event('change', { bubbles: true }));
                    } else {
                        console.warn('[模型管理] 未找到匹配的 MMD 选项:', mmdPath);
                    }
                } else if (hasValidVRMPath && vrmModelSelect) {
                    // VRM 模型：安全获取路径并在合并列表中查找 [VRM] 选项
                    let vrmModelPath = null;
                    if (catgirlConfig.vrm !== undefined && catgirlConfig.vrm !== null) {
                        const rawValue = catgirlConfig.vrm;
                        if (typeof rawValue === 'string') {
                            const trimmed = rawValue.trim();
                            if (trimmed !== '' &&
                                trimmed.toLowerCase() !== 'undefined' &&
                                trimmed.toLowerCase() !== 'null' &&
                                !trimmed.toLowerCase().includes('undefined') &&
                                !trimmed.toLowerCase().includes('null')) {
                                vrmModelPath = trimmed;
                            }
                        } else {
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

                        if (vrmModelSelect && vrmModelSelect.options.length > 0) {
                            let possibleFilename = null;
                            if (catgirlConfig.vrm && typeof catgirlConfig.vrm === 'string') {
                                const parts = catgirlConfig.vrm.split(/[/\\]/);
                                const lastPart = parts[parts.length - 1];
                                if (lastPart && lastPart !== 'undefined' && lastPart !== 'null' && lastPart.endsWith('.vrm')) {
                                    possibleFilename = lastPart;
                                }
                            }

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

                                    try {
                                        const fixResult = await RequestHelper.fetchJson(
                                            `/api/characters/catgirl/l2d/${encodeURIComponent(lanlanName)}`,
                                            {
                                                method: 'PUT',
                                                headers: { 'Content-Type': 'application/json' },
                                                body: JSON.stringify({
                                                    model_type: 'live3d',
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

                    // 在合并列表中查找匹配的 VRM 选项
                    const vrmFilename = vrmModelPath.split(/[/\\]/).pop();
                    const matchedOption = Array.from(vrmModelSelect.options).find(opt => {
                        if (!opt.value) return false;
                        const optPath = opt.getAttribute('data-path') || '';
                        const optFilename = opt.getAttribute('data-filename') || '';
                        const optValue = opt.value || '';

                        if (optValue === vrmModelPath || optPath === vrmModelPath) return true;
                        if (vrmFilename && (optFilename === vrmFilename || optValue.endsWith(vrmFilename) || optPath.endsWith(vrmFilename))) return true;
                        if (vrmFilename && (optPath.includes(vrmFilename) || optValue.includes(vrmFilename))) return true;

                        return false;
                    });

                    if (matchedOption) {
                        vrmModelSelect.value = matchedOption.value;
                        vrmModelSelect.dispatchEvent(new Event('change', { bubbles: true }));
                    } else {
                        console.warn('[模型管理] 未找到匹配的 VRM 选项:', vrmModelPath, '，尝试加载默认模型');
                        selectDefaultLive3DModel();
                    }
                }
            } else if (modelType !== 'live2d') {
                // Live3D 但无有效路径 → 尝试加载内置默认模型
                console.warn(`[模型管理] 模型类型 ${modelType} 无有效路径，尝试加载默认模型`);
                selectDefaultLive3DModel();
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
            InitializationTouchSet(catgirlConfig);
        } catch (error) {
            console.error('加载当前角色模型失败:', error);
            showStatus(t('live2d.loadCurrentModelFailed', '加载当前角色模型失败'));
        }
    }

    // 所有事件监听器已注册，现在可以安全地加载当前角色模型
    // 这样 VRM 的 change 事件处理程序才能正确执行
    try {
        await loadCurrentCharacterModel();
    } catch (loadError) {
        console.error('[模型管理] 加载当前角色模型失败:', loadError);
        showStatus(t('live2d.loadCurrentModelFailed', '加载当前角色模型失败'));
    }

    // 如果已自动加载了一个模型，确保在下拉框中选中它
    // 这是双重保险：防止 loadCurrentCharacterModel() 内部设置失败
    if (currentModelInfo && currentModelInfo.name) {
        const exists = availableModels.some(m => m.name === currentModelInfo.name);
        if (exists && modelSelect.value !== currentModelInfo.name) {
            modelSelect.value = currentModelInfo.name;
        }
    }

    // 等待异步设置（打光、待机动作等）加载完成后记录快照
    setTimeout(() => {
        window._savedModelSnapshot = captureSettingsSnapshot();
    }, 500);
  } catch (_fatalError) {
    console.error('[模型管理] DOMContentLoaded 致命错误:', _fatalError);
    const _s = document.getElementById('status-text');
    if (_s) _s.textContent = `初始化失败: ${_fatalError.message}`;
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
