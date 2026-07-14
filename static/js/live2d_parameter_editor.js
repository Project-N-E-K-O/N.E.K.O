// 等待i18next初始化完成并更新页面文本
function updatePageTranslations() {
    // 先保护需要特殊处理的元素，临时移除data-i18n属性
    const emptyHeaderText = document.querySelector('.parameter-group-empty .group-header-text');
    const emptyI18nKey = emptyHeaderText ? emptyHeaderText.getAttribute('data-i18n') : null;

    if (emptyHeaderText && emptyI18nKey) {
        emptyHeaderText.removeAttribute('data-i18n');
    }

    // 更新所有带有data-i18n属性的元素（现在status-text有data-i18n，但status本身没有）
    if (window.updatePageTexts && typeof window.updatePageTexts === 'function') {
        window.updatePageTexts();
    } else if (window.t && typeof window.t === 'function') {
        // 手动更新所有data-i18n元素
        document.querySelectorAll('[data-i18n]').forEach(el => {
            const key = el.getAttribute('data-i18n');
            if (key) {
                try {
                    const translated = window.t(key);
                    if (translated !== key && el.textContent !== translated) {
                        el.textContent = translated;
                    }
                } catch (e) {
                    console.warn('翻译失败:', key, e);
                }
            }
        });
    }

    // 更新所有 round-stroke-text 元素的 data-text 属性（用于 CSS 伪元素显示）
    document.querySelectorAll('.round-stroke-text[data-i18n]').forEach(el => {
        const key = el.getAttribute('data-i18n');
        if (key && window.t && typeof window.t === 'function') {
            try {
                const translated = window.t(key);
                if (translated && translated !== key) {
                    el.setAttribute('data-text', translated);
                    el.textContent = translated;
                }
            } catch (e) {
                console.warn('更新 data-text 失败:', key, e);
            }
        }
    });

    // 恢复emptyHeaderText的data-i18n属性
    if (emptyHeaderText && emptyI18nKey) {
        emptyHeaderText.setAttribute('data-i18n', emptyI18nKey);
        const translated = window.t ? window.t(emptyI18nKey) : emptyI18nKey;
        if (translated && translated !== emptyI18nKey && emptyHeaderText.textContent !== translated) {
            emptyHeaderText.textContent = translated;
        }
    }
}

// 监听i18next初始化完成事件
document.addEventListener('DOMContentLoaded', function () {
    // 立即尝试更新一次
    updatePageTranslations();

    // 空状态下拉框展开/折叠事件
    const emptyGroup = document.querySelector('.parameter-group-empty');
    const emptyHeader = document.querySelector('.group-header-empty');
    const emptyHintText = document.querySelector('.empty-hint-text');
    const confettiContainer = document.querySelector('.confetti-container');
    
    let hintTextTimer = null;
    let hintClassTimer = null;
    
    if (emptyGroup && emptyHeader) {
        const toggleEmptyGroup = () => {
            const wasExpanded = emptyGroup.classList.contains('expanded');
            emptyGroup.classList.toggle('expanded');
            emptyHeader.setAttribute('aria-expanded', !wasExpanded);
            
            if (emptyHintText) {
                if (hintTextTimer) {
                    clearTimeout(hintTextTimer);
                    hintTextTimer = null;
                }
                if (hintClassTimer) {
                    clearTimeout(hintClassTimer);
                    hintClassTimer = null;
                }
                
                if (!wasExpanded) {
                    emptyHintText.classList.add('flash');
                    hintTextTimer = setTimeout(() => {
                        if (emptyGroup.classList.contains('expanded')) {
                            emptyHintText.textContent = t('live2d.parameterEditor.foundYou', '你找到我了！');
                        }
                        hintTextTimer = null;
                    }, 150);
                    hintClassTimer = setTimeout(() => {
                        emptyHintText.classList.remove('flash');
                        hintClassTimer = null;
                    }, 500);
                    
                    if (confettiContainer) {
                        triggerConfetti(confettiContainer);
                    }
                } else {
                    emptyHintText.classList.remove('flash');
                    emptyHintText.textContent = t('live2d.parameterEditor.emptyHint', '里面什么也没有！');
                }
            }
        };
        
        emptyHeader.addEventListener('click', toggleEmptyGroup);
        
        emptyHeader.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                toggleEmptyGroup();
            }
        });
    }

    function triggerConfetti(container) {
        const colors = ['#ff6b6b', '#4ecdc4', '#ffe66d', '#95e1d3', '#f38181', '#aa96da', '#fcbad3'];
        const shapes = ['circle', 'square', 'triangle'];
        
        for (let i = 0; i < 30; i++) {
            setTimeout(() => {
                const confetti = document.createElement('div');
                confetti.className = 'confetti';
                
                const color = colors[Math.floor(Math.random() * colors.length)];
                const shape = shapes[Math.floor(Math.random() * shapes.length)];
                const size = Math.random() * 8 + 6;
                
                confetti.style.left = Math.random() * 100 + '%';
                confetti.style.top = Math.random() * 30 + '%';
                confetti.style.width = size + 'px';
                confetti.style.height = size + 'px';
                confetti.style.backgroundColor = color;
                
                if (shape === 'circle') {
                    confetti.style.borderRadius = '50%';
                } else if (shape === 'triangle') {
                    confetti.style.width = '0';
                    confetti.style.height = '0';
                    confetti.style.backgroundColor = 'transparent';
                    confetti.style.borderLeft = size/2 + 'px solid transparent';
                    confetti.style.borderRight = size/2 + 'px solid transparent';
                    confetti.style.borderBottom = size + 'px solid ' + color;
                }
                
                container.appendChild(confetti);
                
                requestAnimationFrame(() => {
                    confetti.classList.add('active');
                });
                
                setTimeout(() => {
                    confetti.remove();
                }, 1000);
            }, i * 30);
        }
    }

    // 监听语言变化事件
    window.addEventListener('localechange', function () {
        updatePageTranslations();
        updateModelSelectButtonText();
        updateModelDropdown();
        updateModelPlaceholder();
    });

    // 延迟更新（等待i18next完全初始化）
    setTimeout(updatePageTranslations, 500);
    setTimeout(updatePageTranslations, 1000);
});

let live2dModel = null;
let currentModelInfo = null;
let parameterInfo = null;
let parameterGroups = {};
let initialParameters = {};
// 独立编辑草稿：只由用户操作更新，避免 motion/物理/呼吸的瞬时值混入保存结果。
let draftParameters = {};
let draftParameterIndexes = {};
let parameterCatalog = [];
let parameterCatalogByIndex = new Map();

// 加载序列号，用于防止异步加载乱序
let loadSeq = 0;

// 参数名称中文翻译映射
const parameterNameTranslations = {
    // 面部
    'Angle_X': '头部左右旋转',
    'Angle_Y': '头部上下旋转',
    'Angle_Z': '头部左右倾斜',
    'Blush': '脸颊红晕',
    'Face Ink_Display': '面部墨水显示',

    // 眼睛
    'Eye L_Open': '左眼开合',
    'Eye L_Smile': '左眼微笑',
    'Eye L_Deformation': '左眼变形',
    'Eye R_Open': '右眼开合',
    'Eye R_Smile': '右眼微笑',
    'Eye R_Deformation': '右眼变形',
    'Eyeballs_X': '眼球左右',
    'Eyeballs_Y': '眼球上下',
    'Eyeballs_Shrink': '眼球缩放',
    'Eyes_Effect': '眼睛特效',

    // 眉毛
    'Eyebrow L_Y': '左眉上下',
    'Eyebrow R_Y': '右眉上下',
    'Eyebrow L_X': '左眉左右',
    'Eyebrow R_X': '右眉左右',
    'Eyebrow L_Angle': '左眉角度',
    'Eyebrow R_Angle': '右眉角度',
    'Eyebrow L_Form': '左眉形状',
    'Eyebrow R_Form': '右眉形状',

    // 嘴巴
    'Mouth_Open_Y': '嘴巴开合',
    'Mouth_Form': '嘴巴形状',
    'Mouth_X': '嘴巴左右',
    'Mouth_Y': '嘴巴上下',
    'Mouth_Smile': '嘴巴微笑',
    'Mouth_Pucker': '嘴巴撅起',
    'Mouth_Stretch': '嘴巴拉伸',
    'Mouth_Shrug': '嘴巴耸肩',
    'Mouth_Left': '嘴巴左移',
    'Mouth_Right': '嘴巴右移',

    // 手臂
    'Arm L A_Shoulder Rotation': '左臂A_肩膀旋转',
    'Arm L A_Elbow Rotation': '左臂A_手肘旋转',
    'Arm L A_Wrist Rotation': '左臂A_手腕旋转',
    'Arm R A_Shoulder Rotation': '右臂A_肩膀旋转',
    'Arm R A_Elbow Rotation': '右臂A_手肘旋转',
    'Arm R A_Wrist Rotation': '右臂A_手腕旋转',
    'Arm L B_Shoulder Rotation': '左臂B_肩膀旋转',
    'Arm L B_Elbow Rotation': '左臂B_手肘旋转',
    'Arm L B_Wrist Rotation': '左臂B_手腕旋转',
    'Arm R B_Shoulder Rotation': '右臂B_肩膀旋转',
    'Arm R B_Elbow Rotation': '右臂B_手肘旋转',
    'Arm R B_Wrist Rotation': '右臂B_手腕旋转',
    'Hand L A': '左手A',
    'Hand R A': '右手A',
    'Hand L B': '左手B',
    'Hand R B': '右手B',
    'Wand Rotation': '法杖旋转',
    'Dropping Ink': '滴落墨水',
    'Dropping Ink_Rotation': '滴落墨水旋转',
    'Dropping Ink_Display': '滴落墨水显示',
    'Hat Deformation': '帽子变形',
    'Arm R B_Arm Y': '右臂B_手臂Y',

    // 身体
    'Body_Angle_X': '身体左右旋转',
    'Body_Angle_Y': '身体上下旋转',
    'Body_Angle_Z': '身体左右倾斜',
    'Body_Scale_X': '身体横向缩放',
    'Body_Scale_Y': '身体纵向缩放',

    // 嘴巴（口型）
    'A': '口型A',
    'I': '口型I',
    'U': '口型U',
    'E': '口型E',
    'O': '口型O',
    'Mouth Corner Upward': '嘴角上扬',
    'Mouth Corner Downward': '嘴角下扬',

    // 头发
    'Hair': '头发',
    'Hair_Front': '前发',
    'Hair_Back': '后发',
    'Hair_Side': '侧发',

    // 其他
    'Overall': '整体',
    'Sway': '摇摆',
    'Heart': '心形',
    'Ink': '墨水',
    'Explosion': '爆炸',
    'Rabbit': '兔子',
    'Aura': '光环',
    'Light': '光线',
    'Overall Effect': '整体特效',
    'Overall Color': '整体颜色',
    'Overall Color 2': '整体颜色2'
};

// 判断参数是否重要（用于外观定制）
function isParameterImportant(paramId, paramName) {
    const nameLower = (paramName || paramId || '').toLowerCase();
    const idLower = (paramId || '').toLowerCase();

    // 不重要的参数（技术/动画参数，应该隐藏）
    const unimportantPatterns = [
        // 头部/身体角度（用于动画，不是外观）
        'angle_x', 'angle_y', 'angle_z',
        'body rotation', 'body_angle',
        // 口型参数（用于语音同步）
        '^a$', '^i$', '^u$', '^e$', '^o$',
        // 呼吸等动画参数
        'breath',
        // 手臂旋转（用于动画姿态，不是外观定制）
        'arm.*rotation', 'shoulder.*rotation', 'elbow.*rotation', 'wrist.*rotation',
        // 特效参数（除非是外观相关的）
        'ink.*rotation', 'dropping ink.*rotation', 'wand.*rotation',
        'explosion', 'rabbit', 'aura', 'light',
        // 技术参数
        'paramall', 'paramallx', 'paramally',
        // 位置参数
        'position', 'scale'
    ];

    // 检查是否匹配不重要模式
    for (const pattern of unimportantPatterns) {
        const regex = new RegExp(pattern, 'i');
        if (regex.test(nameLower) || regex.test(idLower)) {
            return false;
        }
    }
    // 不匹配则默认显示（包括未知参数）
    return true;
}

// 获取参数的中文名称
function getParameterChineseName(englishName) {
    // 规范化输入，防止 null/undefined 调用字符串方法
    const safeName = englishName ? String(englishName) : '';

    // 直接匹配
    if (parameterNameTranslations[safeName]) {
        return parameterNameTranslations[safeName];
    }

    // 转义正则特殊字符
    const escapeRegex = (str) => str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

    // 部分匹配（处理带前缀的情况）
    for (const [key, value] of Object.entries(parameterNameTranslations)) {
        // 跳过长度小于2的键，避免单字母误匹配
        if (key.length < 2) continue;
        
        // 使用单词边界匹配，避免子字符串误匹配（如 "A" 匹配 "Angle"）
        const regex = new RegExp('\\b' + escapeRegex(key) + '\\b', 'i');
        if (regex.test(safeName)) {
            return value;
        }
    }

    // 智能翻译常见单词
    let translated = safeName;
    
    // 使用单词边界匹配，确保只替换完整的单词
    const wordReplace = (text, eng, chi) => {
        return text.replace(new RegExp('\\b' + escapeRegex(eng) + '\\b', 'gi'), chi);
    };

    translated = wordReplace(translated, 'Eye L', '左眼');
    translated = wordReplace(translated, 'Eye R', '右眼');
    translated = wordReplace(translated, 'Arm L', '左臂');
    translated = wordReplace(translated, 'Arm R', '右臂');
    translated = wordReplace(translated, 'Hand L', '左手');
    translated = wordReplace(translated, 'Hand R', '右手');
    translated = wordReplace(translated, 'Eyebrow L', '左眉');
    translated = wordReplace(translated, 'Eyebrow R', '右眉');
    translated = wordReplace(translated, 'Rotation', '旋转');
    translated = wordReplace(translated, 'Angle', '角度');
    translated = wordReplace(translated, 'Scale', '缩放');
    translated = wordReplace(translated, 'Open', '开合');
    translated = wordReplace(translated, 'Smile', '微笑');
    translated = wordReplace(translated, 'Form', '形状');
    translated = wordReplace(translated, 'Deformation', '变形');
    translated = wordReplace(translated, 'Shoulder', '肩膀');
    translated = wordReplace(translated, 'Elbow', '手肘');
    translated = wordReplace(translated, 'Wrist', '手腕');
    translated = wordReplace(translated, 'Body', '身体');
    translated = wordReplace(translated, 'Hair', '头发');
    translated = wordReplace(translated, 'Mouth', '嘴巴');
    translated = wordReplace(translated, 'Face', '面部');
    translated = wordReplace(translated, 'Effect', '特效');
    translated = wordReplace(translated, 'Color', '颜色');

    // 处理嘴型 A/I/U/E/O，使用单词边界和分组
    translated = translated.replace(/\b(A|I|U|E|O)\b/gi, (match) => {
        const mouthMap = { 'A': '啊', 'I': '一', 'U': '五', 'E': '额', 'O': '哦' };
        return mouthMap[match.toUpperCase()] || match;
    });

    translated = translated.replace(/_/g, ' ');

    return translated || safeName;
}

const modelSelect = document.getElementById('model-select');
const modelSelectBtn = document.getElementById('model-select-btn');
const modelSelectText = document.getElementById('model-select-text');
const modelDropdown = document.getElementById('model-dropdown');
const parametersList = document.getElementById('parameters-list');
const resetAllBtn = document.getElementById('reset-all-btn');
const saveBtn = document.getElementById('save-btn');
const statusDiv = document.getElementById('status');
const backToMainBtn = document.getElementById('backToMainBtn');

// 状态栏定时器 ID
let statusTimeoutId = null;

// 是否已选择模型（用于区分初始状态和用户主动选择）
let hasSelectedModel = false;

// 计算字符串的视觉宽度（中文字符宽度为2，其他为1）
function getVisualWidth(str) {
    let width = 0;
    for (const char of str) {
        width += char.charCodeAt(0) > 127 ? 2 : 1;
    }
    return width;
}

// 截断文本以适应最大视觉宽度
function truncateText(text, maxVisualWidth) {
    if (!text || getVisualWidth(text) <= maxVisualWidth) {
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

// ===== 跨页面通信系统 =====
// 使用 BroadcastChannel（如果可用）或 localStorage 作为后备
const CHANNEL_NAME = 'neko_page_channel';
const MESSAGE_TIMEOUT = 2000; // 最大等待时间（毫秒）
let broadcastChannel = null;
const MODEL_MANAGER_PARAMETER_SAVE_MARK_PREFIX = 'neko_model_manager_parameter_save_pending:';
const MODEL_MANAGER_LANLAN_NAME_SESSION_KEY = 'neko_model_manager_lanlan_name';

function getParameterEditorLanlanName() {
    let lanlanName = '';
    try {
        const urlParams = new URLSearchParams(window.location.search);
        lanlanName = (urlParams.get('lanlan_name') || '').trim();
    } catch (_) {
        lanlanName = '';
    }
    if (lanlanName) {
        try {
            sessionStorage.setItem(MODEL_MANAGER_LANLAN_NAME_SESSION_KEY, lanlanName);
        } catch (_) {}
        return lanlanName;
    }
    try {
        return (sessionStorage.getItem(MODEL_MANAGER_LANLAN_NAME_SESSION_KEY) || '').trim();
    } catch (_) {
        return '';
    }
}

function getModelManagerParameterSaveMarkKey(lanlanName) {
    const normalizedName = String(lanlanName || '').trim();
    if (!normalizedName) return '';
    try {
        return MODEL_MANAGER_PARAMETER_SAVE_MARK_PREFIX + encodeURIComponent(normalizedName);
    } catch (_) {
        return '';
    }
}

function getModelManagerParameterSaveStorages() {
    const storages = [];
    try {
        if (window.sessionStorage) storages.push(window.sessionStorage);
    } catch (_) {}
    try {
        if (window.localStorage) storages.push(window.localStorage);
    } catch (_) {}
    return storages;
}

function markModelManagerNeedsSaveAfterParameterEdit(modelInfo) {
    const lanlanName = getParameterEditorLanlanName();
    const markKey = getModelManagerParameterSaveMarkKey(lanlanName);
    if (!markKey) return;

    const payload = JSON.stringify({
        lanlanName,
        modelName: modelInfo?.name || '',
        modelPath: modelInfo?.path || '',
        timestamp: Date.now()
    });

    for (const storage of getModelManagerParameterSaveStorages()) {
        try {
            storage.setItem(markKey, payload);
        } catch (_) {}
    }
}

// 初始化 BroadcastChannel（如果支持）
try {
    if (typeof BroadcastChannel !== 'undefined') {
        broadcastChannel = new BroadcastChannel(CHANNEL_NAME);
        console.log('[CrossPageComm] BroadcastChannel 已初始化');
    }
} catch (e) {
    console.log('[CrossPageComm] BroadcastChannel 不可用，将使用 localStorage 后备方案');
}

// 保留原有的简单发送函数（用于不需要确认的场景）
function sendMessageToMainPage(action, payload = {}) {
    try {
        const message = {
            ...payload,
            action: action,
            timestamp: Date.now()
        };

        // 优先使用 BroadcastChannel
        if (broadcastChannel) {
            broadcastChannel.postMessage(message);
        }

        // 同时使用 localStorage
        localStorage.setItem('nekopage_message', JSON.stringify(message));
        localStorage.removeItem('nekopage_message');
        window.dispatchEvent(new StorageEvent('storage', {
            key: 'nekopage_message',
            newValue: JSON.stringify(message)
        }));
    } catch (e) {
        console.error('Cross-page communication failed in sendMessageToMainPage:', e);
    }
}

// 翻译辅助函数（优先返回中文fallback，确保始终显示中文）
function t(key, fallback, params = {}) {
    // 如果提供了fallback，优先使用fallback（确保显示中文）
    if (fallback) {
        try {
            if (window.t && typeof window.t === 'function' && window.i18n && window.i18n.isInitialized) {
                const result = window.t(key, params);
                // 如果翻译成功且不是key本身，使用翻译结果
                if (result && result !== key) {
                    return result;
                }
            }
        } catch (e) {
            console.error(`[i18n] Translation failed for key "${key}":`, e);
        }
        // 始终返回中文fallback
        return fallback;
    }

    // 如果没有fallback，尝试翻译
    try {
        if (window.t && typeof window.t === 'function') {
            const result = window.t(key, params);
            if (result && result !== key) {
                return result;
            }
        }
    } catch (e) {
        console.error(`[i18n] Translation failed for key "${key}":`, e);
    }

    return key;
}

function showStatus(message, duration = 0) {
    // 清除之前的定时器，防止状态被旧的定时器重置
    if (statusTimeoutId) {
        clearTimeout(statusTimeoutId);
        statusTimeoutId = null;
    }

    let statusTextSpan = document.getElementById('status-text');
    if (!statusTextSpan) {
        // 如果结构被破坏了，重新创建 (使用 DOM API 避免 XSS)
        statusDiv.textContent = '';
        const icon = document.createElement('img');
        icon.src = '/static/icons/reminder_icon.png?v=1';
        icon.alt = '提示';
        icon.className = 'reminder-icon';
        Object.assign(icon.style, {
            height: '16px',
            width: '16px',
            verticalAlign: 'middle',
            marginRight: '6px',
            display: 'inline-block',
            imageRendering: 'crisp-edges'
        });

        const newSpan = document.createElement('span');
        newSpan.id = 'status-text';
        newSpan.textContent = message;

        statusDiv.appendChild(icon);
        statusDiv.appendChild(newSpan);
        statusTextSpan = newSpan;
    } else {
        statusTextSpan.textContent = message;
    }

    if (duration > 0) {
        statusTimeoutId = setTimeout(() => {
            statusTimeoutId = null;
            const readyText = t('live2d.parameterEditor.ready', '就绪');
            if (statusTextSpan) {
                statusTextSpan.textContent = readyText;
            } else {
                // 兜底重建
                statusDiv.textContent = '';
                const icon = document.createElement('img');
                icon.src = '/static/icons/reminder_icon.png?v=1';
                icon.alt = '提示';
                icon.className = 'reminder-icon';
                Object.assign(icon.style, {
                    height: '16px',
                    width: '16px',
                    verticalAlign: 'middle',
                    marginRight: '6px',
                    display: 'inline-block',
                    imageRendering: 'crisp-edges'
                });
                const newSpan = document.createElement('span');
                newSpan.id = 'status-text';
                newSpan.textContent = readyText;
                statusDiv.appendChild(icon);
                statusDiv.appendChild(newSpan);
            }
        }, duration);
    }
}

// 返回L2D设置界面
backToMainBtn.addEventListener('click', () => {
    // 获取当前URL参数，保留lanlan_name等参数
    const lanlanName = getParameterEditorLanlanName();
    let targetUrl = '/model_manager';
    if (lanlanName) {
        targetUrl += `?lanlan_name=${encodeURIComponent(lanlanName)}`;
    }
    window.location.href = targetUrl;
});

// 加载模型列表
// 更新下拉菜单和按钮文字
function updateModelDropdown() {
    if (!modelDropdown || !modelSelect) return;

    modelDropdown.innerHTML = '';
    const options = modelSelect.querySelectorAll('option');

    options.forEach((option, index) => {
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
            modelSelect.value = value;
            modelSelect.dispatchEvent(new Event('change', { bubbles: true }));
            modelDropdown.style.display = 'none';
        });

        modelDropdown.appendChild(item);
    });
}

// 更新按钮文字
function updateModelSelectButtonText() {
    if (!modelSelectText || !modelSelect) return;
    
    if (hasSelectedModel && modelSelect.value) {
        const selectedOption = modelSelect.options[modelSelect.selectedIndex];
        const fullText = selectedOption ? selectedOption.textContent : modelSelect.value;
        
        const maxVisualWidth = 13;
        const displayText = truncateText(fullText, maxVisualWidth);
        
        modelSelectText.textContent = displayText;
        modelSelectText.setAttribute('data-text', displayText);
        modelSelectText.removeAttribute('data-i18n');
        
        if (modelSelectBtn) {
            modelSelectBtn.title = fullText;
            modelSelectBtn.removeAttribute('data-i18n-title');
        }
    } else {
        const text = t('live2d.parameterEditor.selectModel', '选择模型');
        modelSelectText.textContent = text;
        modelSelectText.setAttribute('data-text', text);
        modelSelectText.setAttribute('data-i18n', 'live2d.parameterEditor.selectModel');
        
        if (modelSelectBtn) {
            const titleText = t('live2d.parameterEditor.selectModel', '选择模型');
            modelSelectBtn.title = titleText;
            modelSelectBtn.setAttribute('data-i18n-title', 'live2d.parameterEditor.selectModel');
        }
    }
}

// 缓存模型列表，避免重复请求
let cachedModelList = null;

async function loadModelList() {
    try {
        if (cachedModelList) {
            renderModelList(cachedModelList);
            return;
        }
        const response = await fetch('/api/live2d/models');
        const data = await response.json();

        // API可能直接返回数组，也可能返回 {success: true, models: [...]}
        let models = [];
        if (Array.isArray(data)) {
            models = data;
        } else if (data.success && Array.isArray(data.models)) {
            models = data.models;
        } else if (data.models && Array.isArray(data.models)) {
            models = data.models;
        }

        cachedModelList = models;
        renderModelList(models);
    } catch (error) {
        console.error('加载模型列表失败:', error);
        modelSelect.innerHTML = `<option value="">${t('live2d.parameterEditor.loadFailed', '加载失败')}</option>`;
        updateModelDropdown();
        updateModelSelectButtonText();
        showStatus(t('live2d.parameterEditor.modelListLoadFailed', '加载模型列表失败: {{error}}', { error: error.message }), 3000);
    }
}

function renderModelList(models) {
    if (models.length > 0) {
        modelSelect.innerHTML = '';
        models.forEach(model => {
            const option = document.createElement('option');
            option.value = model.name;
            option.textContent = model.display_name || model.name;
            modelSelect.appendChild(option);
        });
        updateModelDropdown();
        updateModelSelectButtonText();
        showStatus(t('live2d.parameterEditor.modelListLoaded', '模型列表加载成功'), 2000);
    } else {
        modelSelect.innerHTML = `<option value="">${t('live2d.parameterEditor.noModels', '暂无模型')}</option>`;
        updateModelDropdown();
        updateModelSelectButtonText();
        showStatus(t('live2d.parameterEditor.noModelsFound', '未找到模型'), 3000);
    }
}

// 更新模型选择器的占位符文本
function updateModelPlaceholder() {
    const placeholderOption = modelSelect.querySelector('option[value=""]');
    if (placeholderOption) {
        // 检查当前占位符文本，根据内容决定使用哪个翻译键
        const currentText = placeholderOption.textContent;
        if (currentText.includes('暂无') || currentText.includes('No models')) {
            placeholderOption.textContent = t('live2d.parameterEditor.noModels', '暂无模型');
        } else if (currentText.includes('加载') || currentText.includes('Loading')) {
            placeholderOption.textContent = t('live2d.parameterEditor.loading', '加载中...');
        } else {
            placeholderOption.textContent = t('live2d.parameterEditor.pleaseSelectModelOption', '选择模型');
        }
    }
}

// 加载模型参数信息
async function loadParameterInfo(modelName) {
    try {
        const response = await fetch(`/api/live2d/model_parameters/${encodeURIComponent(modelName)}`);
        const data = await response.json();
        if (data.success) {
            parameterInfo = data.parameters || [];
            parameterGroups = data.parameter_groups || {};
            return true;
        } else {
            console.warn('无法加载参数信息:', data.error);
            parameterInfo = [];
            parameterGroups = {};
            return false;
        }
    } catch (error) {
        console.error('加载参数信息失败:', error);
        parameterInfo = [];
        parameterGroups = {};
        return false;
    }
}

// 加载模型
async function loadModel(modelName) {
    if (!modelName) return;

    // 增加加载序列号并捕获当前值
    const currentLoadSeq = ++loadSeq;

    showStatus(t('live2d.parameterEditor.loadingModel', '正在加载模型...'));

    try {
        // 确保 PIXI 应用已初始化
        if (!window.live2dManager.pixi_app) {
            await window.live2dManager.initPIXI('live2d-canvas', 'live2d-container');
        }
        
        // 如果序列号已过期，则中断
        if (currentLoadSeq !== loadSeq) return;

        // 加载参数信息
        await loadParameterInfo(modelName);
        if (currentLoadSeq !== loadSeq) return;

        // 获取模型信息（优先使用缓存）
        let models = cachedModelList;
        if (!models) {
            const modelsResponse = await fetch('/api/live2d/models');
            const modelsData = await modelsResponse.json();
            if (currentLoadSeq !== loadSeq) return;

            // 处理API返回格式（可能是数组或对象）
            models = [];
            if (Array.isArray(modelsData)) {
                models = modelsData;
            } else if (modelsData.success && Array.isArray(modelsData.models)) {
                models = modelsData.models;
            } else if (modelsData.models && Array.isArray(modelsData.models)) {
                models = modelsData.models;
            }
            cachedModelList = models;
        }

        const modelInfo = models.find(m => m.name === modelName);
        if (!modelInfo) {
            throw new Error(t('live2d.parameterEditor.modelNotFound', '模型不存在'));
        }

        // 检查序列号
        if (currentLoadSeq !== loadSeq) return;
        currentModelInfo = modelInfo;

        // 获取模型文件
        const filesResponse = await fetch(`/api/live2d/model_files/${encodeURIComponent(modelName)}`);
        const filesData = await filesResponse.json();
        if (currentLoadSeq !== loadSeq) return;
        if (!filesData.success) {
            throw new Error(t('live2d.parameterEditor.cannotGetModelFiles', '无法获取模型文件'));
        }

        // 构建模型配置
        let modelJsonUrl = modelInfo.path;
        const modelConfigRes = await fetch(modelJsonUrl);
        if (currentLoadSeq !== loadSeq) return;
        if (!modelConfigRes.ok) {
            throw new Error(t('live2d.parameterEditor.cannotGetModelConfig', '无法获取模型配置'));
        }
        const modelConfig = await modelConfigRes.json();
        if (currentLoadSeq !== loadSeq) return;
        modelConfig.url = modelJsonUrl;

        // 加载用户偏好设置
        let modelPreferences = null;
        try {
            const preferences = await window.live2dManager.loadUserPreferences();
            if (currentLoadSeq !== loadSeq) return;
            if (preferences && preferences.length > 0) {
                modelPreferences = preferences.find(p => p && p.model_path === modelJsonUrl);
            }
        } catch (e) {
            console.warn('加载用户偏好设置失败:', e);
        }

        // 加载模型（会自动从模型目录加载parameters.json）
        await window.live2dManager.loadModel(modelConfig, {
            loadEmotionMapping: false,
            dragEnabled: true,
            wheelEnabled: true,
            preferences: modelPreferences,
            parameterEditingMode: true,
            skipCloseWindows: true  // 参数编辑器页面不需要关闭其他窗口
        });
        
        if (currentLoadSeq !== loadSeq) return;

        live2dModel = window.live2dManager.getCurrentModel();

        // 记录初始参数（在模型目录参数加载后）
        recordInitialParameters();

        // 显示参数列表
        displayParameters();

        if (resetAllBtn) resetAllBtn.disabled = false;
        if (saveBtn) saveBtn.disabled = false;

        showStatus(t('live2d.parameterEditor.modelLoadSuccess', '模型加载成功'), 2000);
    } catch (error) {
        if (currentLoadSeq === loadSeq) {
            console.error('加载模型失败:', error);
            showStatus(t('live2d.parameterEditor.modelLoadFailed', '加载模型失败: {{error}}', { error: error.message }), 3000);
        }
    }
}

// 记录初始参数
function recordInitialParameters() {
    if (!live2dModel || !live2dModel.internalModel || !live2dModel.internalModel.coreModel) {
        return;
    }

    const coreModel = live2dModel.internalModel.coreModel;
    const manager = window.live2dManager;
    initialParameters = {};
    draftParameters = {};
    draftParameterIndexes = {};
    parameterCatalog = manager && typeof manager._buildModelParameterCatalog === 'function'
        ? manager._buildModelParameterCatalog(coreModel)
        : [];
    if (parameterCatalog.length === 0) {
        const paramCount = coreModel.getParameterCount();
        parameterCatalog = Array.from({ length: paramCount }, (_, index) => ({
            index,
            id: '',
            key: `param_${index}`,
            defaultValue: undefined
        }));
    }
    parameterCatalogByIndex = new Map(parameterCatalog.map(entry => [entry.index, entry]));

    const rawEffectiveParameters = manager?.effectiveModelParameters || {};
    const effectiveParameters = manager && typeof manager._normalizeModelParameters === 'function'
        ? manager._normalizeModelParameters(coreModel, rawEffectiveParameters)
        : rawEffectiveParameters;

    for (const parameter of parameterCatalog) {
        try {
            const value = coreModel.getParameterValueByIndex(parameter.index);
            initialParameters[parameter.key] = value;
            draftParameterIndexes[parameter.key] = parameter.index;
            draftParameters[parameter.key] = Object.prototype.hasOwnProperty.call(effectiveParameters, parameter.key)
                ? effectiveParameters[parameter.key]
                : value;
        } catch (e) {
            console.warn(`记录参数 ${parameter.index} 失败:`, e);
        }
    }
}

// 获取参数的范围和默认值
function getParameterRange(coreModel, index) {
    let min = -1;
    let max = 1;
    let defaultVal = undefined;

    try {
        // 优先尝试 getParameterDefaultValueByIndex (Live2DManager 中使用的标准方法)
        // Live2DManager.js 中确认使用了此方法，应该是最可靠的获取默认值方式
        if (typeof coreModel.getParameterDefaultValueByIndex === 'function') {
            defaultVal = coreModel.getParameterDefaultValueByIndex(index);
        }

        // Try Cubism 2 style (Standard SDK)
        if (typeof coreModel.getParamMin === 'function') {
            min = coreModel.getParamMin(index);
            max = coreModel.getParamMax(index);
            if (defaultVal === undefined) defaultVal = coreModel.getParamDefaultValue(index);
        }
        // Try Cubism 4 JS Wrapper style (pixi-live2d-display / Internal Model)
        else if (coreModel.parameters && coreModel.parameters.minimumValues) {
            min = coreModel.parameters.minimumValues[index];
            max = coreModel.parameters.maximumValues ? coreModel.parameters.maximumValues[index] : 1;
            if (defaultVal === undefined && coreModel.parameters.defaultValues) {
                defaultVal = coreModel.parameters.defaultValues[index];
            }
        }
        // Try Cubism 4 Standard Emscripten binding style
        else if (typeof coreModel.getParameterMinimumValues === 'function') {
            // Note: In some bindings these return arrays or pointers, not values by index directly usually
            // But let's check if getParameterMinimumValue exists
            if (typeof coreModel.getParameterMinimumValue === 'function') {
                min = coreModel.getParameterMinimumValue(index);
                max = coreModel.getParameterMaximumValue(index);
                if (defaultVal === undefined) defaultVal = coreModel.getParameterDefaultValue(index);
            }
        }
        // Fallback: check if we can access the arrays directly on the coreModel (some custom bindings)
        else if (coreModel._parameterMinimumValues) {
            min = coreModel._parameterMinimumValues[index];
            max = coreModel._parameterMaximumValues ? coreModel._parameterMaximumValues[index] : 1;
            if (defaultVal === undefined && coreModel._parameterDefaultValues) {
                defaultVal = coreModel._parameterDefaultValues[index];
            }
        }
    } catch (e) {
        console.warn('Error getting parameter range:', e);
    }

    // Validate values. Keep whether the default came from the model separate
    // from the numeric UI fallback so reset-all never treats an invented zero
    // as a declared model default.
    if (typeof min !== 'number' || isNaN(min)) min = -1;
    if (typeof max !== 'number' || isNaN(max)) max = 1;
    const hasDefault = typeof defaultVal === 'number' && Number.isFinite(defaultVal);
    if (!hasDefault) defaultVal = 0;

    return { min, max, default: defaultVal, hasDefault };
}

// 更新参数UI值（不重新渲染列表）
function updateParameterUIValues() {
    if (!live2dModel || !live2dModel.internalModel || !live2dModel.internalModel.coreModel) return;
    
    const coreModel = live2dModel.internalModel.coreModel;
    const sliderInputs = parametersList.querySelectorAll('.parameter-slider');
    const numberInputs = parametersList.querySelectorAll('.parameter-input');
    
    sliderInputs.forEach((slider, index) => {
        const paramIndex = parseInt(slider.dataset.paramIndex);
        if (!isNaN(paramIndex)) {
            const value = coreModel.getParameterValueByIndex(paramIndex);
            slider.value = value;
        }
    });
    
    numberInputs.forEach((input, index) => {
        const paramIndex = parseInt(input.dataset.paramIndex);
        if (!isNaN(paramIndex)) {
            const value = coreModel.getParameterValueByIndex(paramIndex);
            input.value = value;
        }
    });
}

// 显示参数列表（按分组，显示所有参数）
function displayParameters() {
    if (!live2dModel || !live2dModel.internalModel || !live2dModel.internalModel.coreModel) {
        return;
    }

    const coreModel = live2dModel.internalModel.coreModel;

    // 按分组组织参数
    const groupedParams = {};

    // 如果有参数信息，使用它；否则从模型读取
    if (parameterInfo && parameterInfo.length > 0) {
        for (const param of parameterInfo) {
            // 只显示重要参数
            if (!isParameterImportant(param.id, param.name)) {
                continue;
            }

            const groupId = param.groupId || 'Other';
            // 参数分组名称翻译映射
            const groupNameMap = {
                'Face': '面部',
                'Eye': '眼睛',
                'Eyebrow': '眉毛',
                'Mouth': '嘴巴',
                'Arm': '手臂',
                'Body': '身体',
                'Hair': '头发',
                'Other': '其他'
            };
            let groupName = parameterGroups[groupId] ? parameterGroups[groupId].name : groupId;
            // 如果分组名称是英文，尝试翻译
            if (groupNameMap[groupName]) {
                groupName = groupNameMap[groupName];
            } else if (groupNameMap[groupId]) {
                groupName = groupNameMap[groupId];
            }

            if (!groupedParams[groupName]) {
                groupedParams[groupName] = [];
            }

            try {
                const idx = coreModel.getParameterIndex(param.id);
                if (idx >= 0) {
                    const parameter = parameterCatalogByIndex.get(idx);
                    groupedParams[groupName].push({
                        id: parameter?.key || param.id,
                        name: getParameterChineseName(param.name), // 使用中文名称
                        index: idx
                    });
                }
            } catch (e) {
                // 参数不存在，跳过
            }
        }
    } else {
        // 如果没有参数信息，从模型读取所有参数
        for (const parameter of parameterCatalog) {
            try {
                const paramId = parameter.key;
                const paramName = parameter.id || parameter.key;

                // 只显示重要参数
                if (!isParameterImportant(paramId, paramName)) {
                    continue;
                }

                const groupName = '其他'; // 固定使用中文
                if (!groupedParams[groupName]) {
                    groupedParams[groupName] = [];
                }
                groupedParams[groupName].push({
                    id: paramId,
                    name: getParameterChineseName(paramName), // 转换为中文名称
                    index: parameter.index
                });
            } catch (e) {
                console.warn(`处理参数 ${parameter.index} 失败:`, e);
            }
        }
    }

    parametersList.innerHTML = '';

    if (Object.keys(groupedParams).length === 0) {
        const emptyMsg = document.createElement('div');
        emptyMsg.style.cssText = 'text-align: center; color: #999; padding: 20px;';
        const emptyText = t('live2d.parameterEditor.noParametersFound', '未找到匹配的参数');
        emptyMsg.textContent = emptyText;
        emptyMsg.setAttribute('data-i18n', 'live2d.parameterEditor.noParametersFound');
        parametersList.appendChild(emptyMsg);
        return;
    }

    // 按组名排序
    const sortedGroups = Object.keys(groupedParams).sort();

    for (const groupName of sortedGroups) {
        const groupDiv = document.createElement('div');
        groupDiv.className = 'parameter-group';

        const groupHeader = document.createElement('div');
        groupHeader.className = 'group-header';
        groupHeader.setAttribute('tabindex', '0');
        groupHeader.setAttribute('role', 'button');
        groupHeader.setAttribute('aria-expanded', 'false');

        const groupHeaderText = document.createElement('span');
        groupHeaderText.className = 'group-header-text';
        groupHeaderText.textContent = groupName;

        const groupRight = document.createElement('div');
        groupRight.style.cssText = 'display: flex; align-items: center;';

        const groupCount = document.createElement('span');
        groupCount.className = 'group-header-count';
        groupCount.textContent = groupedParams[groupName].length;

        const groupArrow = document.createElement('span');
        groupArrow.className = 'group-arrow';

        groupRight.appendChild(groupCount);
        groupRight.appendChild(groupArrow);

        groupHeader.appendChild(groupHeaderText);
        groupHeader.appendChild(groupRight);

        const groupContent = document.createElement('div');
        groupContent.className = 'group-content';

        const toggleGroup = () => {
            const isExpanded = groupDiv.classList.toggle('expanded');
            groupHeader.setAttribute('aria-expanded', isExpanded);
        };

        groupHeader.addEventListener('click', toggleGroup);

        groupHeader.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                toggleGroup();
            }
        });

        groupDiv.appendChild(groupHeader);
        groupDiv.appendChild(groupContent);

        for (const { id: paramId, name: paramName, index: i } of groupedParams[groupName]) {
            try {
                const currentValue = coreModel.getParameterValueByIndex(i);
                
                // 获取参数范围
                const range = getParameterRange(coreModel, i);
                let minValue = range.min;
                let maxValue = range.max;
                const initialValue = initialParameters[paramId] !== undefined ? initialParameters[paramId] : range.default;

                // 确保当前值在范围内 (或者至少如果当前值超出范围，扩展范围以包含它)
                if (currentValue < minValue) minValue = currentValue;
                if (currentValue > maxValue) maxValue = currentValue;

                // 防止 min >= max 导致滑块无法拖动（例如某些参数范围为 [0, 0]）
                if (minValue >= maxValue) {
                    const epsilon = 0.1;
                    maxValue = minValue + epsilon;
                }

                const paramItem = document.createElement('div');
                paramItem.className = 'parameter-item';

                const header = document.createElement('div');
                header.className = 'parameter-header';

                const nameSpan = document.createElement('span');
                nameSpan.className = 'parameter-name';
                // paramName 已经是中文名称了
                nameSpan.textContent = paramName;

                header.appendChild(nameSpan);

                const controls = document.createElement('div');
                controls.className = 'parameter-controls';

                // 滑块容器 - 在上边
                const sliderWrapper = document.createElement('div');
                sliderWrapper.className = 'parameter-slider-wrapper';

                const slider = document.createElement('input');
                slider.type = 'range';
                slider.className = 'parameter-slider';
                slider.min = minValue;
                slider.max = maxValue;
                slider.step = '0.01';
                slider.value = currentValue;
                slider.dataset.paramIndex = i;

                sliderWrapper.appendChild(slider);

                // 底部容器 - 数字输入框和重置按钮
                const controlsBottom = document.createElement('div');
                controlsBottom.className = 'parameter-controls-bottom';

                const input = document.createElement('input');
                input.type = 'number';
                input.className = 'parameter-input';
                input.min = minValue;
                input.max = maxValue;
                input.step = '0.01';
                input.value = currentValue;
                input.dataset.paramIndex = i;

                // 重置按钮 - 放在右侧
                const resetBtn = document.createElement('button');
                resetBtn.className = 'btn-reset';
                const resetText = document.createElement('span');
                resetText.className = 'btn-reset-text';
                const resetTextContent = t('live2d.parameterEditor.reset', '重置');
                resetText.textContent = resetTextContent;
                resetText.setAttribute('data-i18n', 'live2d.parameterEditor.reset');
                resetText.setAttribute('data-text', resetTextContent);
                resetBtn.appendChild(resetText);

                controlsBottom.appendChild(input);
                controlsBottom.appendChild(resetBtn);

                const updateParameter = (value, source) => {
                    const numValue = parseFloat(value);
                    if (isNaN(numValue)) return;

                    const clampedValue = Math.max(minValue, Math.min(maxValue, numValue));
                    try {
                        coreModel.setParameterValueByIndex(i, clampedValue);
                        // 避免在拖拽滑块期间重设slider.value导致拖拽中断
                        if (source !== 'slider') {
                            slider.value = clampedValue;
                        }
                        input.value = clampedValue;
                        draftParameters[paramId] = clampedValue;
                    } catch (e) {
                        console.warn(`更新参数 ${paramId} 失败:`, e);
                    }
                };

                // 阻止slider上的pointer/touch事件冒泡到Live2D画布，防止触发模型拖拽
                slider.addEventListener('pointerdown', (e) => {
                    e.stopPropagation();
                });
                slider.addEventListener('touchstart', (e) => {
                    e.stopPropagation();
                }, { passive: false });

                slider.addEventListener('input', (e) => {
                    updateParameter(e.target.value, 'slider');
                });

                input.addEventListener('input', (e) => {
                    updateParameter(e.target.value, 'input');
                });

                resetBtn.addEventListener('click', () => {
                    updateParameter(initialValue, 'reset');
                });

                controls.appendChild(sliderWrapper);
                controls.appendChild(controlsBottom);

                paramItem.appendChild(header);
                paramItem.appendChild(controls);

                groupContent.appendChild(paramItem);
            } catch (e) {
                console.warn(`显示参数 ${paramId} 失败:`, e);
            }
        }

        parametersList.appendChild(groupDiv);
    }
    
    // 刷新新生成的元素的翻译
    updatePageTranslations();
}


// 模型选择按钮点击事件
if (modelSelectBtn) {
    modelSelectBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        if (modelDropdown) {
            const isVisible = modelDropdown.style.display !== 'none';
            if (isVisible) {
                modelDropdown.style.display = 'none';
            } else {
                const rect = modelSelectBtn.getBoundingClientRect();
                modelDropdown.style.left = rect.left + 'px';
                modelDropdown.style.top = (rect.bottom + 4) + 'px';
                modelDropdown.style.display = 'block';
            }
        }
    });
}

// 点击外部关闭下拉菜单
document.addEventListener('click', (e) => {
    if (modelDropdown && modelSelectBtn &&
        !modelDropdown.contains(e.target) &&
        !modelSelectBtn.contains(e.target)) {
        modelDropdown.style.display = 'none';
    }
});

// 事件监听
if (modelSelect) {
    modelSelect.addEventListener('change', (e) => {
        hasSelectedModel = true;
        updateModelSelectButtonText();
        loadModel(e.target.value);
    });
}

if (resetAllBtn) {
    resetAllBtn.addEventListener('click', () => {
        if (!live2dModel || !live2dModel.internalModel || !live2dModel.internalModel.coreModel) return;

        const coreModel = live2dModel.internalModel.coreModel;
        const manager = window.live2dManager;
        const catalog = parameterCatalog.length > 0
            ? parameterCatalog
            : (manager && typeof manager._buildModelParameterCatalog === 'function'
                ? manager._buildModelParameterCatalog(coreModel)
                : []);

        // 全量重建草稿，避免保留任何历史别名或本次会话中的旧键。
        draftParameters = {};
        draftParameterIndexes = {};

        // 重置为模型在 .moc3 中声明的真正默认值，而不是页面加载时的外观。
        for (const parameter of catalog) {
            try {
                let resetValue = parameter.defaultValue;
                if (typeof resetValue !== 'number' || !Number.isFinite(resetValue)) {
                    const range = getParameterRange(coreModel, parameter.index);
                    if (range.hasDefault === true) resetValue = range.default;
                }
                if (typeof resetValue !== 'number' || !Number.isFinite(resetValue)) {
                    // Unknown wrappers cannot expose the .moc3 default. Keep
                    // the loaded value instead of fabricating a destructive 0.
                    resetValue = initialParameters[parameter.key];
                }
                if (typeof resetValue !== 'number' || !Number.isFinite(resetValue)) continue;

                coreModel.setParameterValueByIndex(parameter.index, resetValue);
                draftParameters[parameter.key] = resetValue;
                draftParameterIndexes[parameter.key] = parameter.index;
            } catch (e) {
                console.warn(`重置参数索引 ${parameter.index} 失败:`, e);
            }
        }

        // 更新UI中的滑块和输入框值，不重新渲染整个列表
        updateParameterUIValues();
        showStatus(t('live2d.parameterEditor.resetAllParameters', '已重置所有参数'), 2000);
    });
}

function buildParametersFromDraft(parameters, parameterIndexes, manager, coreModel) {
    const normalizedParameters = manager
        && coreModel
        && typeof manager._normalizeModelParameters === 'function'
        ? manager._normalizeModelParameters(coreModel, parameters)
        : parameters;
    const paramsToSave = {};
    for (const [paramId, value] of Object.entries(normalizedParameters || {})) {
        let index = parameterIndexes && parameterIndexes[paramId];
        if (manager && coreModel && typeof manager._resolveModelParameterKey === 'function') {
            index = manager._resolveModelParameterKey(coreModel, paramId)?.idx ?? index;
        }
        let isEyeBlinkParam = false;
        try {
            isEyeBlinkParam = manager && typeof manager._isEyeBlinkParamId === 'function'
                && (manager._isEyeBlinkParamId(paramId) || manager._isEyeBlinkParamId(`param_${index}`));
        } catch (_) {}
        if (!isEyeBlinkParam && typeof value === 'number' && Number.isFinite(value)) {
            paramsToSave[paramId] = value;
        }
    }
    return paramsToSave;
}

async function persistParameterDraft(modelInfo, model, parameters, dependencies = {}) {
    const fetchImpl = dependencies.fetchImpl || fetch;
    const manager = dependencies.manager || window.live2dManager;
    const position = { x: model.x, y: model.y };
    const scale = { x: model.scale.x, y: model.scale.y };

    let fileSuccess = false;
    let fileError = null;
    try {
        const fileResponse = await fetchImpl(`/api/live2d/save_model_parameters/${encodeURIComponent(modelInfo.name)}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ parameters })
        });
        const fileResult = await fileResponse.json();
        fileSuccess = fileResponse.ok !== false && fileResult.success === true;
        if (!fileSuccess) fileError = fileResult.error || `HTTP ${fileResponse.status}`;
    } catch (error) {
        fileError = error.message;
    }

    let prefSuccess = false;
    let prefError = null;
    try {
        prefSuccess = await manager.saveUserPreferences(
            modelInfo.path,
            position,
            scale,
            parameters
        );
    } catch (error) {
        prefError = error.message;
    }

    return { fileSuccess, fileError, prefSuccess, prefError };
}

if (saveBtn) {
    saveBtn.addEventListener('click', async () => {
        if (!currentModelInfo || !live2dModel) return;

        showStatus(t('live2d.parameterEditor.savingParameters', '正在保存参数...'));

        const manager = window.live2dManager;
        const coreModel = live2dModel.internalModel?.coreModel;
        const paramsToSave = buildParametersFromDraft(
            draftParameters,
            draftParameterIndexes,
            manager,
            coreModel
        );

        try {
            const { fileSuccess, fileError, prefSuccess, prefError } = await persistParameterDraft(
                currentModelInfo,
                live2dModel,
                paramsToSave,
                { manager }
            );

            if (prefSuccess) {
                markModelManagerNeedsSaveAfterParameterEdit(currentModelInfo);
                sendMessageToMainPage('reload_model_parameters', {
                    lanlan_name: getParameterEditorLanlanName(),
                    model_name: currentModelInfo.name,
                    model_path: currentModelInfo.path
                });

                if (fileSuccess) {
                    showStatus(t('live2d.parameterEditor.parametersSaved', '参数保存成功！'), 2000);
                } else {
                    showStatus(t(
                        'live2d.parameterEditor.modelDirectoryMirrorSaveWarning',
                        '参数已保存；模型目录兼容镜像写入失败'
                    ), 3500);
                    console.warn('模型目录 parameters.json 兼容镜像保存失败:', fileError);
                }
            } else {
                const message = t('live2d.parameterEditor.userPreferencesSaveFailed', '用户偏好设置保存失败');
                showStatus(t('live2d.parameterEditor.parametersSaveFailed', '参数保存失败: {{error}}', { error: message }), 3000);
                console.error('权威用户偏好保存失败:', { fileSuccess, fileError, prefError });
            }
        } catch (error) {
            showStatus(t('live2d.parameterEditor.parametersSaveFailed', '参数保存失败: {{error}}', { error: error.message }), 3000);
            console.error('参数保存失败:', error);
        }
    });
}

// 初始化
if (modelSelect) {
    loadModelList();
}
