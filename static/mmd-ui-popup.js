/**
 * MMD UI Popup - 弹出框组件（完整功能版）
 * 与 VRM/Live2D 保持一致的弹窗体系
 */

const MMD_POPUP_ANIMATION_DURATION_MS = 200;

// 注入 CSS 样式
(function () {
    if (document.getElementById('mmd-popup-styles')) return;
    const style = document.createElement('style');
    style.id = 'mmd-popup-styles';
    style.textContent = `
        .mmd-popup {
            position: absolute;
            left: 100%;
            top: 0;
            margin-left: 8px;
            z-index: 100001;
            background: var(--neko-popup-bg, rgba(255, 255, 255, 0.65));
            backdrop-filter: saturate(180%) blur(20px);
            border: var(--neko-popup-border, 1px solid rgba(255, 255, 255, 0.18));
            border-radius: 8px;
            padding: 8px;
            box-shadow: var(--neko-popup-shadow, 0 2px 4px rgba(0,0,0,0.04), 0 8px 16px rgba(0,0,0,0.08), 0 16px 32px rgba(0,0,0,0.04));
            display: none;
            flex-direction: column;
            gap: 6px;
            min-width: 180px;
            max-height: 200px;
            overflow-y: auto;
            pointer-events: auto !important;
            opacity: 0;
            transform: translateX(-10px);
            transition: opacity 0.2s cubic-bezier(0.1, 0.9, 0.2, 1), transform 0.2s cubic-bezier(0.1, 0.9, 0.2, 1);
        }
        .mmd-popup.is-positioning {
            pointer-events: none !important;
        }
        .mmd-popup.mmd-popup-settings {
            max-height: 70vh;
        }
        .mmd-popup.mmd-popup-agent {
            max-height: calc(100vh - 120px);
            overflow-y: auto;
        }
        .mmd-popup.visible {
            display: flex;
            opacity: 1;
            transform: translateX(0);
        }
        .mmd-popup-item {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 6px 8px;
            cursor: pointer;
            border-radius: 6px;
            transition: background 0.2s ease;
            font-size: 13px;
            white-space: nowrap;
        }
        .mmd-popup-item:hover {
            background: rgba(68, 183, 254, 0.08);
        }
        .mmd-popup-item.selected {
            background: rgba(68, 183, 254, 0.1);
        }
        .mmd-toggle-item {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 6px 8px;
            cursor: pointer;
            border-radius: 6px;
            transition: background 0.2s ease, opacity 0.2s ease;
            font-size: 13px;
            white-space: nowrap;
        }
        .mmd-toggle-item:focus-within {
            outline: 2px solid var(--neko-popup-active, #2a7bc4);
            outline-offset: 2px;
        }
        .mmd-toggle-item[aria-disabled="true"] {
            opacity: 0.5;
            cursor: default;
        }
        .mmd-toggle-indicator {
            width: 20px;
            height: 20px;
            border-radius: 50%;
            border: 2px solid var(--neko-popup-indicator-border, #ccc);
            background-color: transparent;
            cursor: pointer;
            flex-shrink: 0;
            transition: all 0.2s ease;
            position: relative;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .mmd-toggle-indicator[aria-checked="true"] {
            background-color: var(--neko-popup-active, #2a7bc4);
            border-color: var(--neko-popup-active, #2a7bc4);
        }
        .mmd-toggle-checkmark {
            color: #fff;
            font-size: 13px;
            font-weight: bold;
            line-height: 1;
            opacity: 0;
            transition: opacity 0.2s ease;
            pointer-events: none;
            user-select: none;
        }
        .mmd-toggle-indicator[aria-checked="true"] .mmd-toggle-checkmark {
            opacity: 1;
        }
        .mmd-toggle-label {
            cursor: pointer;
            user-select: none;
            font-size: 13px;
            color: var(--neko-popup-text, #333);
        }
        .mmd-toggle-item:hover:not([aria-disabled="true"]) {
            background: var(--neko-popup-hover, rgba(68, 183, 254, 0.1));
        }
        .mmd-settings-menu-item {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 8px 12px;
            cursor: pointer;
            border-radius: 6px;
            transition: background 0.2s ease;
            font-size: 13px;
            white-space: nowrap;
            color: var(--neko-popup-text, #333);
            pointer-events: auto !important;
        }
        .mmd-settings-menu-item:hover {
            background: var(--neko-popup-hover, rgba(68, 183, 254, 0.1));
        }
        .mmd-settings-separator {
            height: 1px;
            background: var(--neko-popup-separator, rgba(0, 0, 0, 0.1));
            margin: 4px 0;
        }
    `;
    document.head.appendChild(style);
})();

// ═══════════════════ 旧版全局函数（向后兼容） ═══════════════════

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
    popup.style.display = 'flex';
    requestAnimationFrame(() => { popup.classList.add('visible'); });
}

function hideMMDPopup(popup) {
    if (!popup) return;
    popup.classList.remove('visible');
    setTimeout(() => { popup.style.display = 'none'; }, MMD_POPUP_ANIMATION_DURATION_MS);
}

window.createMMDPopup = createMMDPopup;
window.showMMDPopup = showMMDPopup;
window.hideMMDPopup = hideMMDPopup;

// ═══════════════════ 新版 MMDManager 弹窗方法 ═══════════════════

// 辅助：关闭后重置弹窗样式
function finalizeMMDPopupClosedState(popup) {
    if (!popup) return;
    popup.style.left = '';
    popup.style.right = '';
    popup.style.top = '';
    popup.style.transform = '';
    popup.style.opacity = '';
    popup.style.marginLeft = '';
    popup.style.marginRight = '';
    popup.style.display = 'none';
    delete popup.dataset.opensLeft;
    popup._hideTimeoutId = null;
}

/**
 * 创建弹出框（按 buttonId 区分类型）
 */
MMDManager.prototype.createPopup = function (buttonId) {
    const popup = document.createElement('div');
    popup.id = `mmd-popup-${buttonId}`;
    popup.className = 'mmd-popup';

    const stopEventPropagation = (e) => { e.stopPropagation(); };
    ['pointerdown', 'pointermove', 'pointerup', 'mousedown', 'mousemove', 'mouseup', 'touchstart', 'touchmove', 'touchend'].forEach(evt => {
        popup.addEventListener(evt, stopEventPropagation, true);
    });

    if (buttonId === 'mic') {
        popup.style.minWidth = '400px';
        popup.style.maxHeight = '320px';
        popup.style.flexDirection = 'row';
        popup.style.gap = '0';
        popup.style.overflowY = 'hidden';
    } else if (buttonId === 'screen') {
        popup.style.width = '420px';
        popup.style.maxHeight = '400px';
        popup.style.overflowX = 'hidden';
        popup.style.overflowY = 'auto';
    } else if (buttonId === 'agent') {
        popup.classList.add('mmd-popup-agent');
        window.AgentHUD._createAgentPopupContent.call(this, popup);
    } else if (buttonId === 'settings') {
        popup.classList.add('mmd-popup-settings');
        this._createMMDSettingsPopupContent(popup);
    }

    return popup;
};

/**
 * 创建 MMD 设置弹窗内容（与 VRM/Live2D 一致结构）
 */
MMDManager.prototype._createMMDSettingsPopupContent = function (popup) {
    // 1. 对话设置按钮（侧边弹出：合并消息 + 允许打断）
    const chatSettingsBtn = this._createSettingsMenuButton({
        label: window.t ? window.t('settings.toggles.chatSettings') : '对话设置',
        labelKey: 'settings.toggles.chatSettings'
    });
    popup.appendChild(chatSettingsBtn);

    const chatSidePanel = this._createChatSettingsSidePanel(popup);
    chatSidePanel._anchorElement = chatSettingsBtn;
    chatSidePanel._popupElement = popup;
    this._attachSidePanelHover(chatSettingsBtn, chatSidePanel);

    // 2. MMD 设置按钮（侧边弹出：画质 + 帧率 + 鼠标跟踪 + 物理 + 描边）
    const mmdSettingsBtn = this._createSettingsMenuButton({
        label: window.t ? window.t('settings.toggles.animationSettings') : '动画设置',
        labelKey: 'settings.toggles.animationSettings'
    });
    popup.appendChild(mmdSettingsBtn);

    const mmdSidePanel = this._createMMDAnimationSettingsSidePanel();
    mmdSidePanel._anchorElement = mmdSettingsBtn;
    mmdSidePanel._popupElement = popup;
    this._attachSidePanelHover(mmdSettingsBtn, mmdSidePanel);

    // 3. 主动搭话和自主视觉
    const settingsToggles = [
        { id: 'proactive-chat', label: window.t ? window.t('settings.toggles.proactiveChat') : '主动搭话', labelKey: 'settings.toggles.proactiveChat', storageKey: 'proactiveChatEnabled', hasInterval: true, intervalKey: 'proactiveChatInterval', defaultInterval: 30 },
        { id: 'proactive-vision', label: window.t ? window.t('settings.toggles.proactiveVision') : '自主视觉', labelKey: 'settings.toggles.proactiveVision', storageKey: 'proactiveVisionEnabled', hasInterval: true, intervalKey: 'proactiveVisionInterval', defaultInterval: 15 }
    ];

    settingsToggles.forEach(toggle => {
        const toggleItem = this._createSettingsToggleItem(toggle);
        popup.appendChild(toggleItem);

        if (toggle.hasInterval) {
            const sidePanel = this._createIntervalControl(toggle);
            sidePanel._anchorElement = toggleItem;
            sidePanel._popupElement = popup;

            if (toggle.id === 'proactive-chat') {
                const AUTH_I18N_KEY = 'settings.menu.mediaCredentials';
                const AUTH_FALLBACK_LABEL = '配置媒体凭证';
                const authLink = document.createElement('div');
                Object.assign(authLink.style, {
                    display: 'flex', alignItems: 'center', gap: '6px',
                    padding: '4px 8px', marginLeft: '-6px', fontSize: '12px',
                    color: 'var(--neko-popup-text, #333)', cursor: 'pointer',
                    borderRadius: '6px', transition: 'background 0.2s ease', width: '100%'
                });

                const authIcon = document.createElement('img');
                authIcon.src = '/static/icons/cookies_icon.png';
                authIcon.alt = '';
                Object.assign(authIcon.style, { width: '16px', height: '16px', objectFit: 'contain', flexShrink: '0' });
                authLink.appendChild(authIcon);

                const authLabel = document.createElement('span');
                authLabel.textContent = window.t ? window.t(AUTH_I18N_KEY) : AUTH_FALLBACK_LABEL;
                authLabel.setAttribute('data-i18n', AUTH_I18N_KEY);
                Object.assign(authLabel.style, { fontSize: '12px', userSelect: 'none' });
                authLink.appendChild(authLabel);

                authLink.addEventListener('mouseenter', () => { authLink.style.background = 'var(--neko-popup-hover, rgba(68,183,254,0.1))'; });
                authLink.addEventListener('mouseleave', () => { authLink.style.background = 'transparent'; });
                let isOpening = false;
                authLink.addEventListener('click', (e) => {
                    e.stopPropagation();
                    if (isOpening) return;
                    isOpening = true;
                    if (typeof window.openOrFocusWindow === 'function') {
                        window.openOrFocusWindow('/api/auth/page', 'neko_auth-page');
                    } else {
                        window.open('/api/auth/page', 'neko_auth-page');
                    }
                    setTimeout(() => { isOpening = false; }, 500);
                });
                sidePanel.appendChild(authLink);
            }

            this._attachSidePanelHover(toggleItem, sidePanel);
        }
    });

    // 桌面端添加导航菜单
    if (!window.isMobileWidth || !window.isMobileWidth()) {
        const separator = document.createElement('div');
        separator.className = 'mmd-settings-separator';
        popup.appendChild(separator);

        this._createSettingsMenuItems(popup);
    }
};

// ═══════════════════ 设置弹窗辅助方法 ═══════════════════

// 创建设置菜单按钮（非开关型，带右箭头指示器）
MMDManager.prototype._createSettingsMenuButton = function (config) {
    const btn = document.createElement('div');
    btn.className = 'mmd-settings-menu-item';
    Object.assign(btn.style, { justifyContent: 'space-between' });

    const label = document.createElement('span');
    label.textContent = config.label;
    if (config.labelKey) label.setAttribute('data-i18n', config.labelKey);
    Object.assign(label.style, { userSelect: 'none', fontSize: '13px' });
    btn.appendChild(label);

    const arrow = document.createElement('span');
    arrow.textContent = '›';
    Object.assign(arrow.style, { fontSize: '16px', color: 'var(--neko-popup-text-sub, #999)', lineHeight: '1', flexShrink: '0' });
    btn.appendChild(arrow);

    if (config.labelKey) {
        btn._updateLabelText = () => { if (window.t) label.textContent = window.t(config.labelKey); };
    }

    return btn;
};

// 创建对话设置侧边弹出面板（合并消息 + 允许打断）
MMDManager.prototype._createChatSettingsSidePanel = function (popup) {
    const container = this._createSidePanelContainer();
    container.style.flexDirection = 'column';
    container.style.alignItems = 'stretch';
    container.style.gap = '2px';
    container.style.minWidth = '160px';
    container.style.padding = '4px 4px';

    const chatToggles = [
        { id: 'merge-messages', label: window.t ? window.t('settings.toggles.mergeMessages') : '合并消息', labelKey: 'settings.toggles.mergeMessages' },
        { id: 'focus-mode', label: window.t ? window.t('settings.toggles.allowInterrupt') : '允许打断', labelKey: 'settings.toggles.allowInterrupt', storageKey: 'focusModeEnabled', inverted: true },
    ];

    chatToggles.forEach(toggle => {
        const toggleItem = this._createSettingsToggleItem(toggle);
        container.appendChild(toggleItem);
    });

    document.body.appendChild(container);
    return container;
};

// 创建 MMD 动画设置侧边弹出面板（画质 + 帧率 + 鼠标跟踪 + 物理 + 描边）
MMDManager.prototype._createMMDAnimationSettingsSidePanel = function () {
    const container = this._createSidePanelContainer();
    container.style.flexDirection = 'column';
    container.style.alignItems = 'stretch';
    container.style.gap = '8px';
    container.style.width = '168px';
    container.style.minWidth = '0';
    container.style.padding = '10px 14px';

    const self = this;
    const LABEL_STYLE = { width: '36px', flexShrink: '0', fontSize: '12px', color: 'var(--neko-popup-text, #333)' };
    const VALUE_STYLE = { width: '36px', flexShrink: '0', textAlign: 'right', fontSize: '12px', color: 'var(--neko-popup-text, #333)' };
    const SLIDER_STYLE = { flex: '1', minWidth: '0', height: '4px', cursor: 'pointer', accentColor: 'var(--neko-popup-accent, #44b7fe)' };

    // --- 画质滑动条 ---
    const qualityRow = document.createElement('div');
    Object.assign(qualityRow.style, { display: 'flex', alignItems: 'center', gap: '8px', width: '100%' });

    const qualityLabel = document.createElement('span');
    qualityLabel.textContent = window.t ? window.t('settings.toggles.renderQuality') : '画质';
    qualityLabel.setAttribute('data-i18n', 'settings.toggles.renderQuality');
    Object.assign(qualityLabel.style, LABEL_STYLE);

    const qualitySlider = document.createElement('input');
    qualitySlider.type = 'range';
    qualitySlider.min = '0';
    qualitySlider.max = '2';
    qualitySlider.step = '1';
    const qualityMap = { 'low': 0, 'medium': 1, 'high': 2 };
    const qualityNames = ['low', 'medium', 'high'];
    qualitySlider.value = qualityMap[window.renderQuality || 'medium'] ?? 1;
    Object.assign(qualitySlider.style, SLIDER_STYLE);

    const qualityLabelKeys = ['settings.toggles.renderQualityLow', 'settings.toggles.renderQualityMedium', 'settings.toggles.renderQualityHigh'];
    const qualityDefaults = ['低', '中', '高'];
    const qualityValue = document.createElement('span');
    const curQIdx = parseInt(qualitySlider.value, 10);
    qualityValue.textContent = window.t ? window.t(qualityLabelKeys[curQIdx]) : qualityDefaults[curQIdx];
    qualityValue.setAttribute('data-i18n', qualityLabelKeys[curQIdx]);
    Object.assign(qualityValue.style, VALUE_STYLE);

    qualitySlider.addEventListener('input', () => {
        const idx = parseInt(qualitySlider.value, 10);
        qualityValue.textContent = window.t ? window.t(qualityLabelKeys[idx]) : qualityDefaults[idx];
        qualityValue.setAttribute('data-i18n', qualityLabelKeys[idx]);
    });
    qualitySlider.addEventListener('change', () => {
        const idx = parseInt(qualitySlider.value, 10);
        const quality = qualityNames[idx];
        window.renderQuality = quality;
        if (typeof window.saveNEKOSettings === 'function') window.saveNEKOSettings();
        window.dispatchEvent(new CustomEvent('neko-render-quality-changed', { detail: { quality } }));
    });
    qualitySlider.addEventListener('click', (e) => e.stopPropagation());
    qualitySlider.addEventListener('mousedown', (e) => e.stopPropagation());

    qualityRow.appendChild(qualityLabel);
    qualityRow.appendChild(qualitySlider);
    qualityRow.appendChild(qualityValue);
    container.appendChild(qualityRow);

    // --- 帧率滑动条 ---
    const fpsRow = document.createElement('div');
    Object.assign(fpsRow.style, { display: 'flex', alignItems: 'center', gap: '8px', width: '100%' });

    const fpsLabel = document.createElement('span');
    fpsLabel.textContent = window.t ? window.t('settings.toggles.frameRate') : '帧率';
    fpsLabel.setAttribute('data-i18n', 'settings.toggles.frameRate');
    Object.assign(fpsLabel.style, LABEL_STYLE);

    const fpsSlider = document.createElement('input');
    fpsSlider.type = 'range';
    fpsSlider.min = '0';
    fpsSlider.max = '2';
    fpsSlider.step = '1';
    const fpsValues = [30, 45, 60];
    const curFps = window.targetFrameRate || 60;
    fpsSlider.value = curFps >= 60 ? '2' : curFps >= 45 ? '1' : '0';
    Object.assign(fpsSlider.style, SLIDER_STYLE);

    const fpsLabelKeys = ['settings.toggles.frameRateLow', 'settings.toggles.frameRateMedium', 'settings.toggles.frameRateHigh'];
    const fpsDefaults = ['30fps', '45fps', '60fps'];
    const fpsValue = document.createElement('span');
    const curFIdx = parseInt(fpsSlider.value, 10);
    fpsValue.textContent = window.t ? window.t(fpsLabelKeys[curFIdx]) : fpsDefaults[curFIdx];
    fpsValue.setAttribute('data-i18n', fpsLabelKeys[curFIdx]);
    Object.assign(fpsValue.style, VALUE_STYLE);

    fpsSlider.addEventListener('input', () => {
        const idx = parseInt(fpsSlider.value, 10);
        fpsValue.textContent = window.t ? window.t(fpsLabelKeys[idx]) : fpsDefaults[idx];
        fpsValue.setAttribute('data-i18n', fpsLabelKeys[idx]);
    });
    fpsSlider.addEventListener('change', () => {
        const idx = parseInt(fpsSlider.value, 10);
        window.targetFrameRate = fpsValues[idx];
        if (typeof window.saveNEKOSettings === 'function') window.saveNEKOSettings();
        window.dispatchEvent(new CustomEvent('neko-frame-rate-changed', { detail: { fps: fpsValues[idx] } }));
    });
    fpsSlider.addEventListener('click', (e) => e.stopPropagation());
    fpsSlider.addEventListener('mousedown', (e) => e.stopPropagation());

    fpsRow.appendChild(fpsLabel);
    fpsRow.appendChild(fpsSlider);
    fpsRow.appendChild(fpsValue);
    container.appendChild(fpsRow);

    // --- 鼠标跟踪开关 ---
    const mouseTrackingRow = document.createElement('div');
    Object.assign(mouseTrackingRow.style, { display: 'flex', alignItems: 'center', gap: '8px', width: '100%', marginTop: '4px' });
    mouseTrackingRow.setAttribute('role', 'switch');
    mouseTrackingRow.tabIndex = 0;

    const mouseTrackingLabel = document.createElement('span');
    mouseTrackingLabel.textContent = window.t ? window.t('settings.toggles.mouseTracking') : '跟踪鼠标';
    mouseTrackingLabel.setAttribute('data-i18n', 'settings.toggles.mouseTracking');
    Object.assign(mouseTrackingLabel.style, { fontSize: '12px', color: 'var(--neko-popup-text, #333)', flex: '1' });

    const mouseTrackingCheckbox = document.createElement('input');
    mouseTrackingCheckbox.type = 'checkbox';
    mouseTrackingCheckbox.id = 'mmd-mouse-tracking-toggle';
    mouseTrackingCheckbox.checked = self.cursorFollow ? self.cursorFollow.enabled : false;
    Object.assign(mouseTrackingCheckbox.style, { display: 'none' });

    const { indicator: mouseTrackingIndicator, updateStyle: updateMouseTrackingStyle } = this._createCheckIndicator();
    mouseTrackingIndicator.setAttribute('role', 'switch');
    mouseTrackingIndicator.tabIndex = 0;
    updateMouseTrackingStyle(mouseTrackingCheckbox.checked);

    const updateMouseTrackingRowStyle = () => {
        updateMouseTrackingStyle(mouseTrackingCheckbox.checked);
        const ariaChecked = mouseTrackingCheckbox.checked ? 'true' : 'false';
        mouseTrackingRow.setAttribute('aria-checked', ariaChecked);
        mouseTrackingIndicator.setAttribute('aria-checked', ariaChecked);
        mouseTrackingRow.style.background = mouseTrackingCheckbox.checked
            ? 'var(--neko-popup-selected-bg, rgba(68,183,254,0.1))' : 'transparent';
    };
    mouseTrackingCheckbox.updateStyle = updateMouseTrackingRowStyle;
    updateMouseTrackingRowStyle();

    const handleMouseTrackingToggle = () => {
        mouseTrackingCheckbox.checked = !mouseTrackingCheckbox.checked;
        if (self.cursorFollow) {
            self.cursorFollow.setEnabled(mouseTrackingCheckbox.checked);
        }
        updateMouseTrackingRowStyle();
        if (typeof window.saveNEKOSettings === 'function') window.saveNEKOSettings();
        console.log(`[MMD] 跟踪鼠标已${mouseTrackingCheckbox.checked ? '开启' : '关闭'}`);
    };

    mouseTrackingRow.addEventListener('click', (e) => { e.stopPropagation(); handleMouseTrackingToggle(); });
    mouseTrackingIndicator.addEventListener('click', (e) => { e.stopPropagation(); handleMouseTrackingToggle(); });
    const handleMouseTrackingKeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); handleMouseTrackingToggle(); } };
    mouseTrackingRow.addEventListener('keydown', handleMouseTrackingKeydown);
    mouseTrackingIndicator.addEventListener('keydown', handleMouseTrackingKeydown);
    mouseTrackingLabel.addEventListener('click', (e) => { e.stopPropagation(); handleMouseTrackingToggle(); });

    mouseTrackingRow.addEventListener('mouseenter', () => {
        mouseTrackingRow.style.background = mouseTrackingCheckbox.checked
            ? 'var(--neko-popup-selected-hover, rgba(68,183,254,0.15))'
            : 'var(--neko-popup-hover-subtle, rgba(68,183,254,0.08))';
    });
    mouseTrackingRow.addEventListener('mouseleave', () => { updateMouseTrackingRowStyle(); });

    mouseTrackingRow.appendChild(mouseTrackingCheckbox);
    mouseTrackingRow.appendChild(mouseTrackingIndicator);
    mouseTrackingRow.appendChild(mouseTrackingLabel);
    container.appendChild(mouseTrackingRow);

    // --- 物理模拟开关 ---
    const physicsRow = document.createElement('div');
    Object.assign(physicsRow.style, { display: 'flex', alignItems: 'center', gap: '8px', width: '100%' });
    physicsRow.setAttribute('role', 'switch');
    physicsRow.tabIndex = 0;

    const physicsLabel = document.createElement('span');
    physicsLabel.textContent = window.t ? window.t('mmd.physics.enabled') : '物理模拟';
    physicsLabel.setAttribute('data-i18n', 'mmd.physics.enabled');
    Object.assign(physicsLabel.style, { fontSize: '12px', color: 'var(--neko-popup-text, #333)', flex: '1' });

    const physicsCheckbox = document.createElement('input');
    physicsCheckbox.type = 'checkbox';
    physicsCheckbox.id = 'mmd-physics-toggle';
    physicsCheckbox.checked = self.enablePhysics !== false;
    Object.assign(physicsCheckbox.style, { display: 'none' });

    const { indicator: physicsIndicator, updateStyle: updatePhysicsStyle } = this._createCheckIndicator();
    physicsIndicator.setAttribute('role', 'switch');
    physicsIndicator.tabIndex = 0;
    updatePhysicsStyle(physicsCheckbox.checked);

    const updatePhysicsRowStyle = () => {
        updatePhysicsStyle(physicsCheckbox.checked);
        const ariaChecked = physicsCheckbox.checked ? 'true' : 'false';
        physicsRow.setAttribute('aria-checked', ariaChecked);
        physicsIndicator.setAttribute('aria-checked', ariaChecked);
        physicsRow.style.background = physicsCheckbox.checked
            ? 'var(--neko-popup-selected-bg, rgba(68,183,254,0.1))' : 'transparent';
    };
    physicsCheckbox.updateStyle = updatePhysicsRowStyle;
    updatePhysicsRowStyle();

    const handlePhysicsToggle = () => {
        physicsCheckbox.checked = !physicsCheckbox.checked;
        self.enablePhysics = physicsCheckbox.checked;
        updatePhysicsRowStyle();
        console.log('[MMD UI] 物理模拟:', self.enablePhysics ? '开启' : '关闭');
    };

    physicsRow.addEventListener('click', (e) => { e.stopPropagation(); handlePhysicsToggle(); });
    physicsIndicator.addEventListener('click', (e) => { e.stopPropagation(); handlePhysicsToggle(); });
    const handlePhysicsKeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); handlePhysicsToggle(); } };
    physicsRow.addEventListener('keydown', handlePhysicsKeydown);
    physicsIndicator.addEventListener('keydown', handlePhysicsKeydown);
    physicsLabel.addEventListener('click', (e) => { e.stopPropagation(); handlePhysicsToggle(); });

    physicsRow.addEventListener('mouseenter', () => {
        physicsRow.style.background = physicsCheckbox.checked
            ? 'var(--neko-popup-selected-hover, rgba(68,183,254,0.15))'
            : 'var(--neko-popup-hover-subtle, rgba(68,183,254,0.08))';
    });
    physicsRow.addEventListener('mouseleave', () => { updatePhysicsRowStyle(); });

    physicsRow.appendChild(physicsCheckbox);
    physicsRow.appendChild(physicsIndicator);
    physicsRow.appendChild(physicsLabel);
    container.appendChild(physicsRow);

    // --- 描边效果开关 ---
    const outlineRow = document.createElement('div');
    Object.assign(outlineRow.style, { display: 'flex', alignItems: 'center', gap: '8px', width: '100%' });
    outlineRow.setAttribute('role', 'switch');
    outlineRow.tabIndex = 0;

    const outlineLabel = document.createElement('span');
    outlineLabel.textContent = window.t ? window.t('mmd.rendering.outline') : '描边效果';
    outlineLabel.setAttribute('data-i18n', 'mmd.rendering.outline');
    Object.assign(outlineLabel.style, { fontSize: '12px', color: 'var(--neko-popup-text, #333)', flex: '1' });

    const outlineCheckbox = document.createElement('input');
    outlineCheckbox.type = 'checkbox';
    outlineCheckbox.id = 'mmd-outline-toggle';
    outlineCheckbox.checked = self.useOutlineEffect || false;
    Object.assign(outlineCheckbox.style, { display: 'none' });

    const { indicator: outlineIndicator, updateStyle: updateOutlineStyle } = this._createCheckIndicator();
    outlineIndicator.setAttribute('role', 'switch');
    outlineIndicator.tabIndex = 0;
    updateOutlineStyle(outlineCheckbox.checked);

    const updateOutlineRowStyle = () => {
        updateOutlineStyle(outlineCheckbox.checked);
        const ariaChecked = outlineCheckbox.checked ? 'true' : 'false';
        outlineRow.setAttribute('aria-checked', ariaChecked);
        outlineIndicator.setAttribute('aria-checked', ariaChecked);
        outlineRow.style.background = outlineCheckbox.checked
            ? 'var(--neko-popup-selected-bg, rgba(68,183,254,0.1))' : 'transparent';
    };
    outlineCheckbox.updateStyle = updateOutlineRowStyle;
    updateOutlineRowStyle();

    const handleOutlineToggle = () => {
        outlineCheckbox.checked = !outlineCheckbox.checked;
        self.useOutlineEffect = outlineCheckbox.checked;
        updateOutlineRowStyle();
    };

    outlineRow.addEventListener('click', (e) => { e.stopPropagation(); handleOutlineToggle(); });
    outlineIndicator.addEventListener('click', (e) => { e.stopPropagation(); handleOutlineToggle(); });
    const handleOutlineKeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); handleOutlineToggle(); } };
    outlineRow.addEventListener('keydown', handleOutlineKeydown);
    outlineIndicator.addEventListener('keydown', handleOutlineKeydown);
    outlineLabel.addEventListener('click', (e) => { e.stopPropagation(); handleOutlineToggle(); });

    outlineRow.addEventListener('mouseenter', () => {
        outlineRow.style.background = outlineCheckbox.checked
            ? 'var(--neko-popup-selected-hover, rgba(68,183,254,0.15))'
            : 'var(--neko-popup-hover-subtle, rgba(68,183,254,0.08))';
    });
    outlineRow.addEventListener('mouseleave', () => { updateOutlineRowStyle(); });

    outlineRow.appendChild(outlineCheckbox);
    outlineRow.appendChild(outlineIndicator);
    outlineRow.appendChild(outlineLabel);
    container.appendChild(outlineRow);

    document.body.appendChild(container);
    return container;
};

// 创建侧边弹出面板容器（公共基础样式）
MMDManager.prototype._createSidePanelContainer = function () {
    const container = document.createElement('div');
    container.setAttribute('data-neko-sidepanel', '');
    Object.assign(container.style, {
        position: 'fixed',
        display: 'none',
        alignItems: 'stretch',
        flexDirection: 'column',
        gap: '6px',
        padding: '6px 12px',
        fontSize: '12px',
        color: 'var(--neko-popup-text, #333)',
        opacity: '0',
        zIndex: '100001',
        background: 'var(--neko-popup-bg, rgba(255,255,255,0.65))',
        backdropFilter: 'saturate(180%) blur(20px)',
        border: 'var(--neko-popup-border, 1px solid rgba(255,255,255,0.18))',
        borderRadius: '8px',
        boxShadow: 'var(--neko-popup-shadow, 0 2px 4px rgba(0,0,0,0.04), 0 8px 16px rgba(0,0,0,0.08), 0 16px 32px rgba(0,0,0,0.04))',
        transition: 'opacity 0.2s cubic-bezier(0.1, 0.9, 0.2, 1), transform 0.2s cubic-bezier(0.1, 0.9, 0.2, 1)',
        transform: 'translateX(-6px)',
        pointerEvents: 'auto',
        flexWrap: 'nowrap',
        width: 'max-content',
        maxWidth: 'min(320px, calc(100vw - 24px))'
    });

    const stopEventPropagation = (e) => e.stopPropagation();
    ['pointerdown', 'pointermove', 'pointerup', 'mousedown', 'mousemove', 'mouseup', 'touchstart', 'touchmove', 'touchend'].forEach(evt => {
        container.addEventListener(evt, stopEventPropagation, true);
    });

    container._expand = () => {
        if (container.style.display === 'flex' && container.style.opacity !== '0') return;
        if (container._collapseTimeout) { clearTimeout(container._collapseTimeout); container._collapseTimeout = null; }

        container.style.display = 'flex';
        container.style.pointerEvents = 'none';
        const savedTransition = container.style.transition;
        container.style.transition = 'none';
        container.style.opacity = '0';
        container.style.left = '';
        container.style.right = '';
        container.style.top = '';
        container.style.transform = '';
        void container.offsetHeight;
        container.style.transition = savedTransition;

        const anchor = container._anchorElement;
        if (anchor && window.AvatarPopupUI && window.AvatarPopupUI.positionSidePanel) {
            window.AvatarPopupUI.positionSidePanel(container, anchor);
        }

        // 从按钮容器获取缩放比例，使侧面板与浮动按钮同步缩放
        let scale = 1;
        const popupEl = container._popupElement;
        if (popupEl) {
            const btnContainer = popupEl.closest('[id$="-floating-buttons"]');
            if (btnContainer) {
                const m = btnContainer.style.transform.match(/scale\(([\d.]+)\)/);
                if (m) scale = parseFloat(m[1]) || 1;
            }
        }
        container._currentScale = scale;
        const goLeft = container.dataset.goLeft === 'true';
        container.style.transformOrigin = goLeft ? 'top right' : 'top left';
        if (scale !== 1) {
            container.style.transform += ` scale(${scale})`;
        }

        requestAnimationFrame(() => {
            container.style.pointerEvents = 'auto';
            container.style.opacity = '1';
            const scaleStr = scale !== 1 ? ` scale(${scale})` : '';
            container.style.transform = `translateX(0)${scaleStr}`;
        });
    };

    container._collapse = () => {
        if (container.style.display === 'none') return;
        if (container._collapseTimeout) { clearTimeout(container._collapseTimeout); container._collapseTimeout = null; }
        container.style.opacity = '0';
        const scale = container._currentScale || 1;
        const scaleStr = scale !== 1 ? ` scale(${scale})` : '';
        container.style.transform = (container.dataset.goLeft === 'true' ? 'translateX(6px)' : 'translateX(-6px)') + scaleStr;
        container._collapseTimeout = setTimeout(() => {
            if (container.style.opacity === '0') container.style.display = 'none';
            container._collapseTimeout = null;
        }, MMD_POPUP_ANIMATION_DURATION_MS);
    };

    if (window.AvatarPopupUI && window.AvatarPopupUI.registerSidePanel) {
        window.AvatarPopupUI.registerSidePanel(container);
    }

    // 跟踪侧边面板以便 dispose 时清理
    if (this._sidePanels) {
        this._sidePanels.add(container);
    }

    return container;
};

// 附加侧边面板悬停逻辑
MMDManager.prototype._attachSidePanelHover = function (anchorEl, sidePanel) {
    const self = this;
    const popupEl = sidePanel._popupElement || null;
    const ownerId = popupEl && popupEl.id ? popupEl.id : '';

    if (ownerId) sidePanel.setAttribute('data-neko-sidepanel-owner', ownerId);

    const collapseWithDelay = (delay = 80) => {
        if (sidePanel._hoverCollapseTimer) { clearTimeout(sidePanel._hoverCollapseTimer); sidePanel._hoverCollapseTimer = null; }
        sidePanel._hoverCollapseTimer = setTimeout(() => {
            if (!anchorEl.matches(':hover') && !sidePanel.matches(':hover')) sidePanel._collapse();
            sidePanel._hoverCollapseTimer = null;
        }, delay);
    };

    const expandPanel = () => {
        if (window.AvatarPopupUI && window.AvatarPopupUI.collapseOtherSidePanels) {
            window.AvatarPopupUI.collapseOtherSidePanels(sidePanel);
        }
        void document.body.offsetHeight;
        if (sidePanel._hoverCollapseTimer) { clearTimeout(sidePanel._hoverCollapseTimer); sidePanel._hoverCollapseTimer = null; }
        sidePanel._expand();
    };
    const collapsePanel = (e) => {
        const target = e.relatedTarget;
        if (!target || (!anchorEl.contains(target) && !sidePanel.contains(target))) collapseWithDelay();
    };

    anchorEl.addEventListener('mouseenter', expandPanel);
    anchorEl.addEventListener('mouseleave', collapsePanel);
    sidePanel.addEventListener('mouseenter', () => {
        expandPanel();
        if (self.interaction) {
            self.interaction._isMouseOverButtons = true;
            if (self.interaction._hideButtonsTimer) { clearTimeout(self.interaction._hideButtonsTimer); self.interaction._hideButtonsTimer = null; }
        }
    });
    sidePanel.addEventListener('mouseleave', (e) => {
        collapsePanel(e);
        if (self.interaction) self.interaction._isMouseOverButtons = false;
    });

    if (popupEl) {
        popupEl.addEventListener('mouseleave', (e) => {
            const target = e.relatedTarget;
            if (!target || (!anchorEl.contains(target) && !sidePanel.contains(target))) collapseWithDelay(60);
        });
    }
};

// 创建时间间隔控件（侧边弹出面板）
MMDManager.prototype._createIntervalControl = function (toggle) {
    const container = document.createElement('div');
    container.className = `mmd-interval-control-${toggle.id}`;
    container.setAttribute('data-neko-sidepanel', '');
    Object.assign(container.style, {
        position: 'fixed',
        display: 'none',
        alignItems: 'stretch',
        flexDirection: 'column',
        gap: '6px',
        padding: '6px 12px',
        fontSize: '12px',
        color: 'var(--neko-popup-text, #333)',
        opacity: '0',
        zIndex: '100001',
        background: 'var(--neko-popup-bg, rgba(255,255,255,0.65))',
        backdropFilter: 'saturate(180%) blur(20px)',
        border: 'var(--neko-popup-border, 1px solid rgba(255,255,255,0.18))',
        borderRadius: '8px',
        boxShadow: 'var(--neko-popup-shadow, 0 2px 4px rgba(0,0,0,0.04), 0 8px 16px rgba(0,0,0,0.08), 0 16px 32px rgba(0,0,0,0.04))',
        transition: 'opacity 0.2s cubic-bezier(0.1, 0.9, 0.2, 1), transform 0.2s cubic-bezier(0.1, 0.9, 0.2, 1)',
        transform: 'translateX(-6px)',
        pointerEvents: 'auto',
        flexWrap: 'nowrap',
        width: 'max-content',
        maxWidth: 'min(320px, calc(100vw - 24px))'
    });

    const stopEventPropagation = (e) => e.stopPropagation();
    ['pointerdown', 'pointermove', 'pointerup', 'mousedown', 'mousemove', 'mouseup', 'touchstart', 'touchmove', 'touchend'].forEach(evt => {
        container.addEventListener(evt, stopEventPropagation, true);
    });

    // 滑动条行容器
    const sliderRow = document.createElement('div');
    Object.assign(sliderRow.style, { display: 'flex', alignItems: 'center', gap: '4px', width: 'auto' });

    const labelKey = toggle.id === 'proactive-chat' ? 'settings.interval.chatIntervalBase' : 'settings.interval.visionInterval';
    const defaultLabel = toggle.id === 'proactive-chat' ? '基础间隔' : '读取间隔';
    const labelText = document.createElement('span');
    labelText.textContent = window.t ? window.t(labelKey) : defaultLabel;
    labelText.setAttribute('data-i18n', labelKey);
    Object.assign(labelText.style, { flexShrink: '0', fontSize: '12px' });

    const slider = document.createElement('input');
    slider.type = 'range';
    slider.id = `mmd-${toggle.id}-interval`;
    const minVal = toggle.id === 'proactive-chat' ? 10 : 5;
    slider.min = minVal;
    slider.max = '120';
    slider.step = '5';
    let currentValue = typeof window[toggle.intervalKey] !== 'undefined' ? window[toggle.intervalKey] : toggle.defaultInterval;
    if (currentValue > 120) currentValue = 120;
    slider.value = currentValue;
    Object.assign(slider.style, { width: '60px', height: '4px', cursor: 'pointer', accentColor: 'var(--neko-popup-accent, #44b7fe)' });

    const valueDisplay = document.createElement('span');
    valueDisplay.textContent = `${currentValue}s`;
    Object.assign(valueDisplay.style, { minWidth: '26px', textAlign: 'right', fontFamily: 'monospace', fontSize: '12px', flexShrink: '0' });

    slider.addEventListener('input', () => { valueDisplay.textContent = `${parseInt(slider.value, 10)}s`; });
    slider.addEventListener('change', () => {
        const value = parseInt(slider.value, 10);
        window[toggle.intervalKey] = value;
        if (typeof window.saveNEKOSettings === 'function') window.saveNEKOSettings();
        console.log(`${toggle.id} 间隔已设置为 ${value} 秒`);
    });
    slider.addEventListener('click', (e) => e.stopPropagation());
    slider.addEventListener('mousedown', (e) => e.stopPropagation());

    sliderRow.appendChild(labelText);
    sliderRow.appendChild(slider);
    sliderRow.appendChild(valueDisplay);
    container.appendChild(sliderRow);

    // 主动搭话：添加搭话方式选项
    if (toggle.id === 'proactive-chat') {
        if (typeof window.createChatModeToggles === 'function') {
            const chatModesContainer = window.createChatModeToggles('mmd');
            container.appendChild(chatModesContainer);
        }
    }

    // 侧边弹出展开/收缩
    container._expand = () => {
        if (container.style.display === 'flex' && container.style.opacity !== '0') return;
        if (container._collapseTimeout) { clearTimeout(container._collapseTimeout); container._collapseTimeout = null; }

        container.style.display = 'flex';
        container.style.pointerEvents = 'none';
        const savedTransition = container.style.transition;
        container.style.transition = 'none';
        container.style.opacity = '0';
        container.style.left = '';
        container.style.right = '';
        container.style.top = '';
        container.style.transform = '';
        void container.offsetHeight;
        container.style.transition = savedTransition;

        const anchor = container._anchorElement;
        if (anchor && window.AvatarPopupUI && window.AvatarPopupUI.positionSidePanel) {
            window.AvatarPopupUI.positionSidePanel(container, anchor);
        }

        // 从按钮容器获取缩放比例，使侧面板与浮动按钮同步缩放
        let scale = 1;
        const popupEl = container._popupElement;
        if (popupEl) {
            const btnContainer = popupEl.closest('[id$="-floating-buttons"]');
            if (btnContainer) {
                const m = btnContainer.style.transform.match(/scale\(([\d.]+)\)/);
                if (m) scale = parseFloat(m[1]) || 1;
            }
        }
        container._currentScale = scale;
        const goLeft = container.dataset.goLeft === 'true';
        container.style.transformOrigin = goLeft ? 'top right' : 'top left';
        if (scale !== 1) {
            container.style.transform += ` scale(${scale})`;
        }

        requestAnimationFrame(() => {
            container.style.pointerEvents = 'auto';
            container.style.opacity = '1';
            const scaleStr = scale !== 1 ? ` scale(${scale})` : '';
            container.style.transform = `translateX(0)${scaleStr}`;
        });
    };

    container._collapse = () => {
        if (container.style.display === 'none') return;
        if (container._collapseTimeout) { clearTimeout(container._collapseTimeout); container._collapseTimeout = null; }
        container.style.opacity = '0';
        const scale = container._currentScale || 1;
        const scaleStr = scale !== 1 ? ` scale(${scale})` : '';
        container.style.transform = (container.dataset.goLeft === 'true' ? 'translateX(6px)' : 'translateX(-6px)') + scaleStr;
        container._collapseTimeout = setTimeout(() => {
            if (container.style.opacity === '0') container.style.display = 'none';
            container._collapseTimeout = null;
        }, MMD_POPUP_ANIMATION_DURATION_MS);
    };

    if (window.AvatarPopupUI && window.AvatarPopupUI.registerSidePanel) {
        window.AvatarPopupUI.registerSidePanel(container);
    }

    // 跟踪侧边面板以便 dispose 时清理
    if (this._sidePanels) {
        this._sidePanels.add(container);
    }

    document.body.appendChild(container);
    return container;
};

// 创建圆形指示器和对勾的辅助方法
MMDManager.prototype._createCheckIndicator = function () {
    const indicator = document.createElement('div');
    Object.assign(indicator.style, {
        width: '20px', height: '20px', borderRadius: '50%',
        border: '2px solid var(--neko-popup-indicator-border, #ccc)',
        backgroundColor: 'transparent', cursor: 'pointer', flexShrink: '0',
        transition: 'all 0.2s ease', position: 'relative',
        display: 'flex', alignItems: 'center', justifyContent: 'center'
    });

    const checkmark = document.createElement('div');
    checkmark.textContent = '✓';
    Object.assign(checkmark.style, {
        color: '#fff', fontSize: '13px', fontWeight: 'bold', lineHeight: '1',
        opacity: '0', transition: 'opacity 0.2s ease', pointerEvents: 'none', userSelect: 'none'
    });
    indicator.appendChild(checkmark);

    const updateStyle = (checked) => {
        if (checked) {
            indicator.style.backgroundColor = 'var(--neko-popup-active, #2a7bc4)';
            indicator.style.borderColor = 'var(--neko-popup-active, #2a7bc4)';
            checkmark.style.opacity = '1';
        } else {
            indicator.style.backgroundColor = 'transparent';
            indicator.style.borderColor = 'var(--neko-popup-indicator-border, #ccc)';
            checkmark.style.opacity = '0';
        }
    };

    return { indicator, updateStyle };
};

// 创建设置开关项
MMDManager.prototype._createSettingsToggleItem = function (toggle) {
    const toggleItem = document.createElement('div');
    toggleItem.className = 'mmd-toggle-item';
    toggleItem.id = `mmd-toggle-${toggle.id}`;
    toggleItem.setAttribute('role', 'switch');
    toggleItem.setAttribute('tabIndex', '0');
    toggleItem.setAttribute('aria-checked', 'false');
    toggleItem.setAttribute('aria-label', toggle.label);
    Object.assign(toggleItem.style, { padding: '8px 12px' });

    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.id = `mmd-${toggle.id}`;
    Object.assign(checkbox.style, {
        position: 'absolute', width: '1px', height: '1px', padding: '0', margin: '-1px',
        overflow: 'hidden', clip: 'rect(0, 0, 0, 0)', whiteSpace: 'nowrap', border: '0'
    });
    checkbox.setAttribute('aria-hidden', 'true');
    checkbox.setAttribute('tabindex', '-1');

    if (toggle.id === 'merge-messages') {
        if (typeof window.mergeMessagesEnabled !== 'undefined') checkbox.checked = window.mergeMessagesEnabled;
    } else if (toggle.id === 'focus-mode' && typeof window.focusModeEnabled !== 'undefined') {
        checkbox.checked = toggle.inverted ? !window.focusModeEnabled : window.focusModeEnabled;
    } else if (toggle.id === 'proactive-chat' && typeof window.proactiveChatEnabled !== 'undefined') {
        checkbox.checked = window.proactiveChatEnabled;
    } else if (toggle.id === 'proactive-vision' && typeof window.proactiveVisionEnabled !== 'undefined') {
        checkbox.checked = window.proactiveVisionEnabled;
    }

    const indicator = document.createElement('div');
    indicator.className = 'mmd-toggle-indicator';
    indicator.setAttribute('role', 'presentation');
    indicator.setAttribute('aria-hidden', 'true');

    const checkmark = document.createElement('div');
    checkmark.className = 'mmd-toggle-checkmark';
    checkmark.setAttribute('aria-hidden', 'true');
    checkmark.innerHTML = '✓';
    indicator.appendChild(checkmark);

    const updateIndicatorStyle = (checked) => {
        if (checked) {
            indicator.style.backgroundColor = 'var(--neko-popup-active, #2a7bc4)';
            indicator.style.borderColor = 'var(--neko-popup-active, #2a7bc4)';
            checkmark.style.opacity = '1';
        } else {
            indicator.style.backgroundColor = 'transparent';
            indicator.style.borderColor = 'var(--neko-popup-indicator-border, #ccc)';
            checkmark.style.opacity = '0';
        }
    };

    const label = document.createElement('label');
    label.innerText = toggle.label;
    if (toggle.labelKey) label.setAttribute('data-i18n', toggle.labelKey);
    Object.assign(label.style, {
        cursor: 'pointer', userSelect: 'none', fontSize: '13px',
        color: 'var(--neko-popup-text, #333)', display: 'flex',
        alignItems: 'center', lineHeight: '1', height: '20px'
    });

    const updateStyle = () => {
        const isChecked = checkbox.checked;
        toggleItem.setAttribute('aria-checked', isChecked ? 'true' : 'false');
        indicator.setAttribute('aria-checked', isChecked ? 'true' : 'false');
        updateIndicatorStyle(isChecked);
        toggleItem.style.background = isChecked ? 'var(--neko-popup-selected-bg, rgba(68,183,254,0.1))' : 'transparent';
    };

    updateStyle();

    toggleItem.appendChild(checkbox);
    toggleItem.appendChild(indicator);
    toggleItem.appendChild(label);

    toggleItem.addEventListener('mouseenter', () => {
        toggleItem.style.background = checkbox.checked
            ? 'var(--neko-popup-selected-hover, rgba(68,183,254,0.15))'
            : 'var(--neko-popup-hover-subtle, rgba(68,183,254,0.08))';
    });
    toggleItem.addEventListener('mouseleave', () => { updateStyle(); });

    const handleToggleChange = (isChecked) => {
        updateStyle();

        if (toggle.id === 'merge-messages') {
            window.mergeMessagesEnabled = isChecked;
            if (typeof window.saveNEKOSettings === 'function') window.saveNEKOSettings();
        } else if (toggle.id === 'focus-mode') {
            const actualValue = toggle.inverted ? !isChecked : isChecked;
            window.focusModeEnabled = actualValue;
            if (typeof window.saveNEKOSettings === 'function') window.saveNEKOSettings();
        } else if (toggle.id === 'proactive-chat') {
            window.proactiveChatEnabled = isChecked;
            if (typeof window.saveNEKOSettings === 'function') window.saveNEKOSettings();
            if (isChecked && typeof window.resetProactiveChatBackoff === 'function') {
                window.resetProactiveChatBackoff();
            } else if (!isChecked && typeof window.stopProactiveChatSchedule === 'function') {
                window.stopProactiveChatSchedule();
            }
        } else if (toggle.id === 'proactive-vision') {
            window.proactiveVisionEnabled = isChecked;
            if (typeof window.saveNEKOSettings === 'function') window.saveNEKOSettings();
            if (isChecked) {
                if (typeof window.acquireProactiveVisionStream === 'function') window.acquireProactiveVisionStream();
                if (typeof window.resetProactiveChatBackoff === 'function') window.resetProactiveChatBackoff();
                if (typeof window.isRecording !== 'undefined' && window.isRecording) {
                    if (typeof window.startProactiveVisionDuringSpeech === 'function') window.startProactiveVisionDuringSpeech();
                }
            } else {
                if (typeof window.releaseProactiveVisionStream === 'function') window.releaseProactiveVisionStream();
                if (typeof window.stopProactiveChatSchedule === 'function') {
                    if (!window.proactiveChatEnabled) window.stopProactiveChatSchedule();
                }
                if (typeof window.stopProactiveVisionDuringSpeech === 'function') window.stopProactiveVisionDuringSpeech();
            }
        }
    };

    const performToggle = () => {
        if (checkbox.disabled) return;
        if (checkbox._processing) {
            if (Date.now() - (checkbox._processingTime || 0) < 500) return;
        }
        checkbox._processing = true;
        checkbox._processingTime = Date.now();
        const newChecked = !checkbox.checked;
        checkbox.checked = newChecked;
        handleToggleChange(newChecked);
        checkbox.dispatchEvent(new Event('change', { bubbles: true }));
        setTimeout(() => { checkbox._processing = false; checkbox._processingTime = null; }, 500);
    };

    toggleItem.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); performToggle(); } });
    toggleItem.addEventListener('click', (e) => { if (e.target !== checkbox) { e.preventDefault(); e.stopPropagation(); performToggle(); } });
    indicator.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); performToggle(); });
    label.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); performToggle(); });

    checkbox.updateStyle = updateStyle;

    return toggleItem;
};

// 创建设置菜单项（保持与 VRM/Live2D 一致）
MMDManager.prototype._createSettingsMenuItems = function (popup) {
    const settingsItems = [
        {
            id: 'character',
            label: window.t ? window.t('settings.menu.characterManage') : '角色管理',
            labelKey: 'settings.menu.characterManage',
            icon: '/static/icons/character_icon.png',
            action: 'navigate',
            url: '/chara_manager',
            submenu: [
                { id: 'general', label: window.t ? window.t('settings.menu.general') : '通用设置', labelKey: 'settings.menu.general', icon: '/static/icons/live2d_settings_icon.png', action: 'navigate', url: '/chara_manager' },
                { id: 'mmd-manage', label: window.t ? window.t('settings.menu.modelSettings') : '模型管理', labelKey: 'settings.menu.modelSettings', icon: '/static/icons/character_icon.png', action: 'navigate', urlBase: '/model_manager' },
                { id: 'voice-clone', label: window.t ? window.t('settings.menu.voiceClone') : '声音克隆', labelKey: 'settings.menu.voiceClone', icon: '/static/icons/voice_clone_icon.png', action: 'navigate', url: '/voice_clone' }
            ]
        },
        { id: 'api-keys', label: window.t ? window.t('settings.menu.apiKeys') : 'API密钥', labelKey: 'settings.menu.apiKeys', icon: '/static/icons/api_key_icon.png', action: 'navigate', url: '/api_key' },
        { id: 'memory', label: window.t ? window.t('settings.menu.memoryBrowser') : '记忆浏览', labelKey: 'settings.menu.memoryBrowser', icon: '/static/icons/memory_icon.png', action: 'navigate', url: '/memory_browser' },
        { id: 'steam-workshop', label: window.t ? window.t('settings.menu.steamWorkshop') : '创意工坊', labelKey: 'settings.menu.steamWorkshop', icon: '/static/icons/Steam_icon_logo.png', action: 'navigate', url: '/steam_workshop_manager' },
    ];

    settingsItems.forEach(item => {
        const menuItem = this._createMenuItem(item);
        popup.appendChild(menuItem);

        if (item.submenu && item.submenu.length > 0) {
            const submenuContainer = this._createSubmenuContainer(item.submenu);
            popup.appendChild(submenuContainer);

            let submenuCollapseTimer = null;
            let overflowTimer = null;
            const clearSubmenuCollapseTimer = () => { if (submenuCollapseTimer) { clearTimeout(submenuCollapseTimer); submenuCollapseTimer = null; } };
            const expandSubmenu = () => {
                clearSubmenuCollapseTimer();
                if (overflowTimer) { clearTimeout(overflowTimer); overflowTimer = null; }
                submenuContainer._expand();
                overflowTimer = setTimeout(() => {
                    overflowTimer = null;
                    if (!popup.isConnected || popup.style.display === 'none') return;
                    const rect = popup.getBoundingClientRect();
                    const bottomMargin = 60;
                    const topMargin = 8;
                    if (rect.bottom > window.innerHeight - bottomMargin) {
                        popup.style.top = `${parseFloat(popup.style.top || 0) - (rect.bottom - (window.innerHeight - bottomMargin))}px`;
                    }
                    const newRect = popup.getBoundingClientRect();
                    if (newRect.top < topMargin) {
                        popup.style.top = `${parseFloat(popup.style.top || 0) + (topMargin - newRect.top)}px`;
                    }
                }, MMD_POPUP_ANIMATION_DURATION_MS + 20);
            };
            const scheduleSubmenuCollapse = () => {
                clearSubmenuCollapseTimer();
                submenuCollapseTimer = setTimeout(() => { submenuContainer._collapse(); submenuCollapseTimer = null; }, 110);
            };

            menuItem.addEventListener('mouseenter', expandSubmenu);
            menuItem.addEventListener('mouseleave', (e) => {
                const target = e.relatedTarget;
                if (target && (menuItem.contains(target) || submenuContainer.contains(target))) return;
                scheduleSubmenuCollapse();
            });
            submenuContainer.addEventListener('mouseenter', expandSubmenu);
            submenuContainer.addEventListener('mouseleave', (e) => {
                const target = e.relatedTarget;
                if (target && (menuItem.contains(target) || submenuContainer.contains(target))) return;
                scheduleSubmenuCollapse();
            });
        }
    });
};

// 创建单个菜单项
MMDManager.prototype._createMenuItem = function (item, isSubmenuItem = false) {
    const menuItem = document.createElement('div');
    menuItem.className = 'mmd-settings-menu-item';
    Object.assign(menuItem.style, {
        display: 'flex', alignItems: 'center', gap: '8px',
        padding: isSubmenuItem ? '6px 12px 6px 36px' : '8px 12px',
        cursor: 'pointer', borderRadius: '6px', transition: 'background 0.2s ease',
        fontSize: isSubmenuItem ? '12px' : '13px', whiteSpace: 'nowrap',
        color: 'var(--neko-popup-text, #333)'
    });

    if (item.icon) {
        const iconImg = document.createElement('img');
        iconImg.src = item.icon;
        iconImg.alt = item.label;
        Object.assign(iconImg.style, {
            width: isSubmenuItem ? '18px' : '24px', height: isSubmenuItem ? '18px' : '24px',
            objectFit: 'contain', flexShrink: '0'
        });
        menuItem.appendChild(iconImg);
    }

    const labelText = document.createElement('span');
    labelText.textContent = item.label;
    if (item.labelKey) labelText.setAttribute('data-i18n', item.labelKey);
    Object.assign(labelText.style, {
        display: 'flex', alignItems: 'center', lineHeight: '1',
        height: isSubmenuItem ? '18px' : '24px'
    });
    menuItem.appendChild(labelText);

    if (item.labelKey) {
        menuItem._updateLabelText = () => {
            if (window.t) {
                labelText.textContent = window.t(item.labelKey);
                if (item.icon && menuItem.querySelector('img')) menuItem.querySelector('img').alt = window.t(item.labelKey);
            }
        };
    }

    menuItem.addEventListener('mouseenter', () => menuItem.style.background = 'var(--neko-popup-hover, rgba(68, 183, 254, 0.1))');
    menuItem.addEventListener('mouseleave', () => menuItem.style.background = 'transparent');

    let isOpening = false;
    menuItem.addEventListener('click', (e) => {
        e.stopPropagation();
        if (isOpening) return;

        if (item.action === 'navigate') {
            let finalUrl = item.url || item.urlBase;
            let windowName = `neko_${item.id}`;
            let features;

            if ((item.id === 'mmd-manage' || item.id === 'vrm-manage' || item.id === 'live2d-manage') && item.urlBase) {
                const lanlanName = (window.lanlan_config && window.lanlan_config.lanlan_name) || '';
                finalUrl = `${item.urlBase}?lanlan_name=${encodeURIComponent(lanlanName)}`;
                window.location.href = finalUrl;
            } else if (item.id === 'voice-clone' && item.url) {
                const lanlanName = (window.lanlan_config && window.lanlan_config.lanlan_name) || '';
                const lanlanNameForKey = lanlanName || 'default';
                finalUrl = `${item.url}?lanlan_name=${encodeURIComponent(lanlanName)}`;
                windowName = `neko_voice_clone_${encodeURIComponent(lanlanNameForKey)}`;
                const width = 700, height = 750;
                const left = Math.max(0, Math.floor((screen.width - width) / 2));
                const top = Math.max(0, Math.floor((screen.height - height) / 2));
                features = `width=${width},height=${height},left=${left},top=${top},menubar=no,toolbar=no,location=no,status=no,resizable=yes,scrollbars=yes`;

                isOpening = true;
                if (typeof window.openOrFocusWindow === 'function') {
                    window.openOrFocusWindow(finalUrl, windowName, features);
                } else {
                    window.open(finalUrl, windowName, features);
                }
                setTimeout(() => { isOpening = false; }, 500);
            } else {
                if (typeof finalUrl === 'string' && finalUrl.startsWith('/chara_manager')) windowName = 'neko_chara_manager';

                isOpening = true;
                if (typeof window.openOrFocusWindow === 'function') {
                    window.openOrFocusWindow(finalUrl, windowName, features);
                } else {
                    window.open(finalUrl, windowName, features);
                }
                setTimeout(() => { isOpening = false; }, 500);
            }
        }
    });

    return menuItem;
};

// 创建可折叠的子菜单容器
MMDManager.prototype._createSubmenuContainer = function (submenuItems) {
    const container = document.createElement('div');
    Object.assign(container.style, {
        display: 'none', flexDirection: 'column', overflow: 'hidden',
        height: '0', opacity: '0', transition: 'height 0.2s ease, opacity 0.2s ease'
    });

    submenuItems.forEach(subItem => {
        const subMenuItem = this._createMenuItem(subItem, true);
        container.appendChild(subMenuItem);
    });

    container._expand = () => {
        container.style.display = 'flex';
        requestAnimationFrame(() => {
            const calculatedHeight = Math.max(submenuItems.length * 32, container.scrollHeight);
            container.style.height = `${calculatedHeight}px`;
            container.style.opacity = '1';
        });
    };
    container._collapse = () => {
        container.style.height = '0';
        container.style.opacity = '0';
        setTimeout(() => {
            if (container.style.opacity === '0') container.style.display = 'none';
        }, MMD_POPUP_ANIMATION_DURATION_MS);
    };

    return container;
};

/**
 * 显示/隐藏弹窗（带动画和互斥逻辑）
 */
MMDManager.prototype.showPopup = function (buttonId, popup) {
    const isVisible = popup.style.display === 'flex';
    const popupUi = window.AvatarPopupUI || null;
    if (typeof popup._showToken !== 'number') popup._showToken = 0;

    if (buttonId === 'agent' && !isVisible) {
        window.dispatchEvent(new CustomEvent('live2d-agent-popup-opening'));
    }

    if (isVisible) {
        // 关闭弹窗
        popup._showToken += 1;
        popup.style.opacity = '0';
        const closingOpensLeft = popup.dataset.opensLeft === 'true';
        popup.style.transform = closingOpensLeft ? 'translateX(10px)' : 'translateX(-10px)';
        const triggerIcon = document.querySelector(`.mmd-trigger-icon-${buttonId}`);
        if (triggerIcon) triggerIcon.style.transform = 'rotate(0deg)';
        if (buttonId === 'agent') window.dispatchEvent(new CustomEvent('live2d-agent-popup-closed'));

        // 关闭该 popup 所属的所有侧面板
        const closingPopupId = popup.id;
        if (closingPopupId) {
            document.querySelectorAll(`[data-neko-sidepanel-owner="${closingPopupId}"]`).forEach(panel => {
                if (panel._collapseTimeout) { clearTimeout(panel._collapseTimeout); panel._collapseTimeout = null; }
                if (panel._hoverCollapseTimer) { clearTimeout(panel._hoverCollapseTimer); panel._hoverCollapseTimer = null; }
                panel.style.transition = 'none';
                panel.style.opacity = '0';
                panel.style.display = 'none';
                panel.style.transition = '';
            });
        }

        const hasSeparatePopupTrigger = this._buttonConfigs && this._buttonConfigs.find(c => c.id === buttonId && c.separatePopupTrigger);
        if (!hasSeparatePopupTrigger && typeof this.setButtonActive === 'function') {
            this.setButtonActive(buttonId, false);
        }

        const hideTimeoutId = setTimeout(() => {
            finalizeMMDPopupClosedState(popup);
        }, MMD_POPUP_ANIMATION_DURATION_MS);
        popup._hideTimeoutId = hideTimeoutId;
    } else {
        // 打开弹窗
        const showToken = popup._showToken + 1;
        popup._showToken = showToken;
        if (popup._hideTimeoutId) {
            clearTimeout(popup._hideTimeoutId);
            popup._hideTimeoutId = null;
        }

        this.closeAllPopupsExcept(buttonId);
        popup.style.display = 'flex';
        popup.style.opacity = '0';
        popup.style.visibility = 'visible';
        popup.classList.add('is-positioning');

        const hasSeparatePopupTrigger = this._buttonConfigs && this._buttonConfigs.find(c => c.id === buttonId && c.separatePopupTrigger);
        if (!hasSeparatePopupTrigger && typeof this.setButtonActive === 'function') {
            this.setButtonActive(buttonId, true);
        }

        // 预加载图片后定位
        const images = popup.querySelectorAll('img');
        Promise.all(Array.from(images).map(img => img.complete ? Promise.resolve() : new Promise(r => { img.onload = img.onerror = r; setTimeout(r, 100); }))).then(() => {
            if (popup._showToken !== showToken || popup.style.display !== 'flex') return;
            void popup.offsetHeight;
            requestAnimationFrame(() => {
                if (popup._showToken !== showToken || popup.style.display !== 'flex') return;
                if (popupUi && typeof popupUi.positionPopup === 'function') {
                    const pos = popupUi.positionPopup(popup, {
                        buttonId,
                        buttonPrefix: 'mmd-btn-',
                        triggerPrefix: 'mmd-trigger-icon-',
                        rightMargin: 20,
                        bottomMargin: 60,
                        topMargin: 8,
                        gap: 8,
                        sidePanelWidth: (buttonId === 'settings' || buttonId === 'agent') ? 320 : 0
                    });
                    popup.dataset.opensLeft = String(!!(pos && pos.opensLeft));
                    popup.style.transform = pos && pos.opensLeft ? 'translateX(10px)' : 'translateX(-10px)';
                }
                if (popup._showToken !== showToken || popup.style.display !== 'flex') return;
                popup.style.visibility = 'visible';
                popup.style.opacity = '1';
                popup.classList.remove('is-positioning');
                const triggerIcon = document.querySelector(`.mmd-trigger-icon-${buttonId}`);
                if (triggerIcon) triggerIcon.style.transform = 'rotate(180deg)';
                requestAnimationFrame(() => {
                    if (popup._showToken !== showToken || popup.style.display !== 'flex') return;
                    popup.style.transform = 'translateX(0)';
                });
            });
        });
    }
};

/**
 * 关闭指定 ID 的弹窗
 */
MMDManager.prototype.closePopupById = function (buttonId) {
    if (!buttonId) return false;
    const popup = document.getElementById(`mmd-popup-${buttonId}`);
    if (!popup || popup.style.display !== 'flex') return false;

    if (buttonId === 'agent') window.dispatchEvent(new CustomEvent('live2d-agent-popup-closed'));
    popup._showToken = (popup._showToken || 0) + 1;
    if (popup._hideTimeoutId) { clearTimeout(popup._hideTimeoutId); popup._hideTimeoutId = null; }

    popup.style.opacity = '0';
    const closeOpensLeft = popup.dataset.opensLeft === 'true';
    popup.style.transform = closeOpensLeft ? 'translateX(10px)' : 'translateX(-10px)';

    // 关闭侧面板
    const popupId = popup.id;
    if (popupId) {
        document.querySelectorAll(`[data-neko-sidepanel-owner="${popupId}"]`).forEach(panel => {
            if (panel._collapseTimeout) { clearTimeout(panel._collapseTimeout); panel._collapseTimeout = null; }
            if (panel._hoverCollapseTimer) { clearTimeout(panel._hoverCollapseTimer); panel._hoverCollapseTimer = null; }
            panel.style.transition = 'none';
            panel.style.opacity = '0';
            panel.style.display = 'none';
            panel.style.transition = '';
        });
    }

    const triggerIcon = document.querySelector(`.mmd-trigger-icon-${buttonId}`);
    if (triggerIcon) triggerIcon.style.transform = 'rotate(0deg)';

    popup._hideTimeoutId = setTimeout(() => {
        finalizeMMDPopupClosedState(popup);
    }, MMD_POPUP_ANIMATION_DURATION_MS);

    const hasSeparatePopupTrigger = this._buttonConfigs && this._buttonConfigs.find(c => c.id === buttonId && c.separatePopupTrigger);
    if (!hasSeparatePopupTrigger && typeof this.setButtonActive === 'function') {
        this.setButtonActive(buttonId, false);
    }
    return true;
};

/**
 * 关闭除指定 ID 外的所有弹窗
 */
MMDManager.prototype.closeAllPopupsExcept = function (currentButtonId) {
    document.querySelectorAll('[id^="mmd-popup-"]').forEach(popup => {
        const popupId = popup.id.replace('mmd-popup-', '');
        if (popupId !== currentButtonId && popup.style.display === 'flex') this.closePopupById(popupId);
    });
};

/**
 * 创建开关组件（供 Agent 弹窗使用，checkbox ID 使用 mmd- 前缀）
 */
MMDManager.prototype._createToggleItem = function (toggle, popup) {
    const toggleItem = document.createElement('div');
    toggleItem.className = 'mmd-toggle-item';
    toggleItem.setAttribute('role', 'switch');
    toggleItem.setAttribute('tabIndex', toggle.initialDisabled ? '-1' : '0');
    toggleItem.setAttribute('aria-checked', 'false');
    toggleItem.setAttribute('aria-disabled', toggle.initialDisabled ? 'true' : 'false');

    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.id = `mmd-${toggle.id}`;
    checkbox.style.position = 'absolute';
    checkbox.style.opacity = '0';
    checkbox.style.width = '1px';
    checkbox.style.height = '1px';
    checkbox.style.overflow = 'hidden';
    checkbox.setAttribute('aria-hidden', 'true');

    if (toggle.initialDisabled) {
        checkbox.disabled = true;
        checkbox.title = window.t ? window.t('settings.toggles.checking') : '查询中...';
        checkbox.setAttribute('data-i18n-title', 'settings.toggles.checking');
    }

    const indicator = document.createElement('div');
    indicator.className = 'mmd-toggle-indicator';
    indicator.setAttribute('role', 'presentation');
    indicator.setAttribute('aria-hidden', 'true');

    const checkmark = document.createElement('div');
    checkmark.className = 'mmd-toggle-checkmark';
    checkmark.innerHTML = '✓';
    indicator.appendChild(checkmark);

    const label = document.createElement('label');
    label.className = 'mmd-toggle-label';
    label.innerText = toggle.label;
    if (toggle.labelKey) label.setAttribute('data-i18n', toggle.labelKey);
    label.htmlFor = `mmd-${toggle.id}`;
    toggleItem.setAttribute('aria-label', toggle.label);

    const updateLabelText = () => {
        if (toggle.labelKey && window.t) {
            label.innerText = window.t(toggle.labelKey);
            toggleItem.setAttribute('aria-label', window.t(toggle.labelKey));
        }
    };
    if (toggle.labelKey) toggleItem._updateLabelText = updateLabelText;

    const updateStyle = () => {
        const isChecked = checkbox.checked;
        toggleItem.setAttribute('aria-checked', isChecked ? 'true' : 'false');
        indicator.setAttribute('aria-checked', isChecked ? 'true' : 'false');
    };

    const updateDisabledStyle = () => {
        const disabled = checkbox.disabled;
        toggleItem.setAttribute('aria-disabled', disabled ? 'true' : 'false');
        toggleItem.setAttribute('tabIndex', disabled ? '-1' : '0');
        toggleItem.style.opacity = disabled ? '0.5' : '1';
        const cursor = disabled ? 'default' : 'pointer';
        [toggleItem, label, indicator].forEach(el => { el.style.cursor = cursor; });
    };

    const updateTitle = () => {
        const title = checkbox.title || '';
        toggleItem.title = title;
        label.title = title;
    };

    checkbox.addEventListener('change', updateStyle);
    updateStyle();
    updateDisabledStyle();
    updateTitle();

    const disabledObserver = new MutationObserver(() => {
        updateDisabledStyle();
        updateTitle();
    });
    disabledObserver.observe(checkbox, { attributes: true, attributeFilter: ['disabled', 'title'] });

    toggleItem.appendChild(checkbox);
    toggleItem.appendChild(indicator);
    toggleItem.appendChild(label);

    checkbox._updateStyle = () => { updateStyle(); updateDisabledStyle(); updateTitle(); };

    const handleToggle = (e) => {
        if (checkbox.disabled) return;
        if (checkbox._processing) {
            if (Date.now() - (checkbox._processingTime || 0) < 500) { e?.preventDefault(); return; }
        }
        checkbox._processing = true; checkbox._processingTime = Date.now();
        checkbox.checked = !checkbox.checked;
        checkbox.dispatchEvent(new Event('change', { bubbles: true }));
        updateStyle();
        setTimeout(() => checkbox._processing = false, 500);
        e?.preventDefault(); e?.stopPropagation();
    };

    toggleItem.addEventListener('keydown', (e) => {
        if (checkbox.disabled) return;
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleToggle(e); }
    });

    [toggleItem, indicator, label].forEach(el => el.addEventListener('click', (e) => {
        if (e.target !== checkbox) handleToggle(e);
    }));

    return toggleItem;
};

/**
 * 渲染屏幕/窗口源选择列表
 */
MMDManager.prototype.renderScreenSourceList = async function (popup) {
    if (!popup) return;
    popup.innerHTML = '';

    if (!window.electronDesktopCapturer || typeof window.electronDesktopCapturer.getSources !== 'function') {
        const noElectron = document.createElement('div');
        noElectron.textContent = window.t ? window.t('app.screenSource.notAvailable') : '屏幕捕获不可用';
        Object.assign(noElectron.style, { padding: '12px', fontSize: '13px', color: 'var(--neko-popup-text-sub, #666)', textAlign: 'center' });
        popup.appendChild(noElectron);
        return;
    }

    const loading = document.createElement('div');
    loading.textContent = window.t ? window.t('app.screenSource.loading') : '加载中...';
    Object.assign(loading.style, { padding: '12px', fontSize: '13px', color: 'var(--neko-popup-text-sub, #666)', textAlign: 'center' });
    popup.appendChild(loading);

    try {
        const sources = await window.electronDesktopCapturer.getSources({ types: ['window', 'screen'] });
        popup.innerHTML = '';

        if (!sources || sources.length === 0) {
            const noSrc = document.createElement('div');
            noSrc.textContent = window.t ? window.t('app.screenSource.noSources') : '未找到可用源';
            Object.assign(noSrc.style, { padding: '12px', fontSize: '13px', color: 'var(--neko-popup-text-sub, #666)', textAlign: 'center' });
            popup.appendChild(noSrc);
            return;
        }

        const screens = sources.filter(s => s.id.startsWith('screen:'));
        const windows = sources.filter(s => s.id.startsWith('window:'));

        const createGrid = (title, items) => {
            if (items.length === 0) return;
            const header = document.createElement('div');
            header.textContent = title;
            Object.assign(header.style, { fontSize: '12px', fontWeight: '600', padding: '4px 8px', color: 'var(--neko-popup-text-sub, #666)' });
            popup.appendChild(header);

            const grid = document.createElement('div');
            Object.assign(grid.style, { display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '8px', padding: '4px 8px' });

            items.forEach(source => {
                const option = document.createElement('div');
                Object.assign(option.style, {
                    display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '4px',
                    padding: '6px', borderRadius: '6px', cursor: 'pointer', transition: 'background 0.15s ease'
                });

                const thumb = document.createElement('img');
                if (source.thumbnail) {
                    thumb.src = source.thumbnail;
                }
                Object.assign(thumb.style, { width: '90px', height: '56px', objectFit: 'contain', borderRadius: '4px', background: 'rgba(0,0,0,0.05)' });
                thumb.onerror = () => { thumb.style.display = 'none'; };

                const name = document.createElement('div');
                name.textContent = source.name;
                Object.assign(name.style, {
                    fontSize: '11px', textAlign: 'center', maxWidth: '90px',
                    overflow: 'hidden', textOverflow: 'ellipsis', display: '-webkit-box',
                    WebkitLineClamp: '2', WebkitBoxOrient: 'vertical', lineHeight: '1.3'
                });

                option.appendChild(thumb);
                option.appendChild(name);

                option.addEventListener('mouseenter', () => { option.style.background = 'rgba(68, 183, 254, 0.1)'; });
                option.addEventListener('mouseleave', () => { option.style.background = 'transparent'; });
                option.addEventListener('click', (e) => {
                    e.stopPropagation();
                    if (typeof window.selectScreenSource === 'function') {
                        window.selectScreenSource(source.id, source.name);
                    }
                });

                grid.appendChild(option);
            });

            popup.appendChild(grid);
        };

        createGrid(window.t ? window.t('app.screenSource.screens') : '屏幕', screens);
        createGrid(window.t ? window.t('app.screenSource.windows') : '窗口', windows);
    } catch (err) {
        popup.innerHTML = '';
        const errDiv = document.createElement('div');
        errDiv.textContent = window.t ? window.t('app.screenSource.loadFailed') : '获取屏幕源失败';
        Object.assign(errDiv.style, { padding: '12px', fontSize: '13px', color: '#ff4d4f', textAlign: 'center' });
        popup.appendChild(errDiv);
    }
};
