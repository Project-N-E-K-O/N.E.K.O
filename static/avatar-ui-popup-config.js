/**
 * Avatar UI Popup Config - 三套头像系统的弹窗配置
 * 所有通用逻辑由 AvatarPopupMixin（avatar-ui-popup.js）提供
 * 此文件仅包含各系统的差异化配置和回调
 *
 * 注意：此文件可能在 VRMManager / MMDManager 定义之前加载，
 * 因此 VRM 和 MMD 段落使用 typeof 守卫 + 延迟注册模式。
 */

// 动画时长常量（由 avatar-ui-popup.js 中定义，此处仅引用）
// AVATAR_POPUP_ANIMATION_DURATION_MS 已在 avatar-ui-popup.js 中声明

// ═══════════════════════════════════════════════════════
// Live2D（HTML 静态加载时已可用）
// ═══════════════════════════════════════════════════════

if (typeof Live2DManager !== 'undefined') {
    AvatarPopupMixin.apply(Live2DManager.prototype, 'live2d', {
        animationDurationMs: AVATAR_POPUP_ANIMATION_DURATION_MS,
        characterMenuItems: [
            { id: 'general', label: '通用设置', labelKey: 'settings.menu.general', icon: '/static/icons/live2d_settings_icon.png', action: 'navigate', url: '/character_card_manager' },
            { id: 'live2d-manage', label: '模型管理', labelKey: 'settings.menu.modelSettings', icon: '/static/icons/character_icon.png', action: 'navigate', urlBase: '/model_manager' },
            { id: 'voice-clone', label: '声音克隆', labelKey: 'settings.menu.voiceClone', icon: '/static/icons/voice_clone_icon.png', action: 'navigate', url: '/voice_clone' }
        ],
        onMouseTrackingToggle: function(enabled) {
            window.mouseTrackingEnabled = enabled;
            if (window.live2dManager && typeof window.live2dManager.setMouseTrackingEnabled === 'function') {
                window.live2dManager.setMouseTrackingEnabled(enabled);
            }
            console.log(`[Live2D] 跟踪鼠标切换: enabled=${enabled}`);
        },
        getMouseTrackingState: function() {
            return window.mouseTrackingEnabled !== false;
        }
    });
}

// ═══════════════════════════════════════════════════════
// VRM（动态加载，可能尚不存在）
// ═══════════════════════════════════════════════════════

const _vrmPopupConfig = {
    animationDurationMs: AVATAR_POPUP_ANIMATION_DURATION_MS,
    characterMenuItems: [
        { id: 'general', label: '通用设置', labelKey: 'settings.menu.general', icon: '/static/icons/live2d_settings_icon.png', action: 'navigate', url: '/character_card_manager' },
        { id: 'vrm-manage', label: '模型管理', labelKey: 'settings.menu.modelSettings', icon: '/static/icons/character_icon.png', action: 'navigate', urlBase: '/model_manager' },
        { id: 'voice-clone', label: '声音克隆', labelKey: 'settings.menu.voiceClone', icon: '/static/icons/voice_clone_icon.png', action: 'navigate', url: '/voice_clone' }
    ],
    onQualityChange: function(quality) {
        const followLevel = quality === 'high' ? 'medium' : 'low';
        window.cursorFollowPerformanceLevel = followLevel;
        if (window.vrmManager && typeof window.vrmManager.setCursorFollowPerformance === 'function') {
            window.vrmManager.setCursorFollowPerformance(followLevel);
        }
        window.dispatchEvent(new CustomEvent('neko-cursor-follow-performance-changed', { detail: { level: followLevel } }));
    },
    onMouseTrackingToggle: function(enabled) {
        window.mouseTrackingEnabled = enabled;
        if (window.vrmManager && typeof window.vrmManager.setMouseTrackingEnabled === 'function') {
            window.vrmManager.setMouseTrackingEnabled(enabled);
        }
        console.log(`[VRM] 跟踪鼠标已${enabled ? '开启' : '关闭'}`);
    },
    getMouseTrackingState: function() {
        return window.mouseTrackingEnabled !== false;
    },
    overrides: {
        _onPopupShow: function(popup, buttonId) {
            if (buttonId !== 'settings') return;
            const syncCheckbox = (cb, checked) => {
                if (!cb) return;
                cb.checked = checked;
                if (typeof cb.updateStyle === 'function') cb.updateStyle();
            };
            const prefix = 'vrm';
            syncCheckbox(document.querySelector(`#${prefix}-merge-messages`), window.mergeMessagesEnabled);
            syncCheckbox(document.querySelector(`#${prefix}-focus-mode`), !window.focusModeEnabled);
            syncCheckbox(document.querySelector(`#${prefix}-avatar-reaction-bubble`), window.avatarReactionBubbleEnabled);
            syncCheckbox(popup.querySelector(`#${prefix}-proactive-chat`), window.proactiveChatEnabled);
            // proactive-vision 走 inverted（"隐私模式" UI 显示），与 avatar-ui-popup.js 对齐
            syncCheckbox(popup.querySelector(`#${prefix}-proactive-vision`), !window.proactiveVisionEnabled);
            syncCheckbox(popup.querySelector(`#${prefix}-mouse-tracking-toggle`), window.mouseTrackingEnabled);
            if (window.CHAT_MODE_CONFIG) {
                window.CHAT_MODE_CONFIG.forEach(config => {
                    const cb = document.querySelector(`#${prefix}-proactive-${config.mode}-chat`);
                    if (cb && typeof window[config.globalVarName] !== 'undefined') {
                        cb.checked = window[config.globalVarName];
                        if (typeof window.updateChatModeStyle === 'function') {
                            requestAnimationFrame(() => window.updateChatModeStyle(cb));
                        }
                    }
                });
            }
        }
    }
};

if (typeof VRMManager !== 'undefined') {
    AvatarPopupMixin.apply(VRMManager.prototype, 'vrm', _vrmPopupConfig);
} else {
    // VRM 模块尚未加载，等 vrm-modules-ready 事件后再注入
    window.addEventListener('vrm-modules-ready', () => {
        if (typeof VRMManager !== 'undefined' && !VRMManager.prototype.createPopup) {
            AvatarPopupMixin.apply(VRMManager.prototype, 'vrm', _vrmPopupConfig);
        }
    }, { once: true });
}

// ═══════════════════════════════════════════════════════
// MMD（动态加载，可能尚不存在）
// ═══════════════════════════════════════════════════════

// 向后兼容的全局函数
function createMMDPopup(parentElement, items, options = {}) {
    const popup = document.createElement('div');
    popup.className = 'mmd-popup';
    items.forEach(item => {
        const el = document.createElement('div');
        el.className = 'mmd-popup-item';
        el.textContent = item.label;
        if (item.selected) el.classList.add('selected');
        el.addEventListener('click', (e) => {
            e.stopPropagation();
            if (item.onClick) item.onClick();
            hideMMDPopup(popup);
        });
        popup.appendChild(el);
    });
    parentElement.style.position = 'relative';
    parentElement.appendChild(popup);
    return popup;
}
function showMMDPopup(popup) {
    if (!popup) return;
    if (popup._hideTimer) { clearTimeout(popup._hideTimer); popup._hideTimer = null; }
    popup.style.display = 'flex';
    requestAnimationFrame(() => { popup.classList.add('visible'); });
}
function hideMMDPopup(popup) {
    if (!popup) return;
    popup.classList.remove('visible');
    popup._hideTimer = setTimeout(() => { popup.style.display = 'none'; }, AVATAR_POPUP_ANIMATION_DURATION_MS);
}
window.createMMDPopup = createMMDPopup;
window.showMMDPopup = showMMDPopup;
window.hideMMDPopup = hideMMDPopup;

const _mmdPopupConfig = {
    animationDurationMs: AVATAR_POPUP_ANIMATION_DURATION_MS,
    characterMenuItems: [
        { id: 'general', label: '通用设置', labelKey: 'settings.menu.general', icon: '/static/icons/live2d_settings_icon.png', action: 'navigate', url: '/character_card_manager' },
        { id: 'mmd-manage', label: '模型管理', labelKey: 'settings.menu.modelSettings', icon: '/static/icons/character_icon.png', action: 'navigate', urlBase: '/model_manager' },
        { id: 'voice-clone', label: '声音克隆', labelKey: 'settings.menu.voiceClone', icon: '/static/icons/voice_clone_icon.png', action: 'navigate', url: '/voice_clone' }
    ],
    sidePanelContainerLayout: {
        alignItems: 'stretch',
        flexDirection: 'column',
        flexWrap: 'nowrap',
        width: 'max-content'
    },
    onMouseTrackingToggle: function(enabled) {
        if (this.cursorFollow) {
            this.cursorFollow.setEnabled(enabled);
        }
        console.log(`[MMD] 跟踪鼠标已${enabled ? '开启' : '关闭'}`);
    },
    getMouseTrackingState: function() {
        return this.cursorFollow ? this.cursorFollow.enabled : false;
    }
};

if (typeof MMDManager !== 'undefined') {
    AvatarPopupMixin.apply(MMDManager.prototype, 'mmd', _mmdPopupConfig);
} else {
    // MMD 模块尚未加载，等 mmd-modules-ready 事件后再注入
    window.addEventListener('mmd-modules-ready', () => {
        if (typeof MMDManager !== 'undefined' && !MMDManager.prototype.createPopup) {
            AvatarPopupMixin.apply(MMDManager.prototype, 'mmd', _mmdPopupConfig);
        }
    }, { once: true });
}
