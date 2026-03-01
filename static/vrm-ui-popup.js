/**
 * VRM UI Popup - 弹出框组件（功能同步修复版）
 */

// 动画时长常量（与 CSS transition duration 保持一致）
const VRM_POPUP_ANIMATION_DURATION_MS = 200;

// 注入 CSS 样式（如果尚未注入）
(function () {
    if (document.getElementById('vrm-popup-styles')) return;
    const style = document.createElement('style');
    style.id = 'vrm-popup-styles';
    style.textContent = `
        :root {
            --neko-popup-selected-bg: rgba(68, 183, 254, 0.1);
            --neko-popup-selected-hover: rgba(68, 183, 254, 0.15);
            --neko-popup-hover-subtle: rgba(68, 183, 254, 0.08);
        }
        .vrm-popup {
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
            box-shadow: var(--neko-popup-shadow, 0 2px 4px rgba(0, 0, 0, 0.04), 0 8px 16px rgba(0, 0, 0, 0.08), 0 16px 32px rgba(0, 0, 0, 0.04));
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
        .vrm-popup.vrm-popup-settings {
            max-height: 70vh;
        }
        .vrm-popup.vrm-popup-agent {
            max-height: calc(100vh - 120px);
            overflow-y: auto;
        }
        .vrm-toggle-item {
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
        .vrm-toggle-item:focus-within {
            outline: 2px solid var(--neko-popup-active, #2a7bc4);
            outline-offset: 2px;
        }
        .vrm-toggle-item[aria-disabled="true"] {
            opacity: 0.5;
            cursor: default;
        }
        .vrm-toggle-indicator {
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
        .vrm-toggle-indicator[aria-checked="true"] {
            background-color: var(--neko-popup-active, #2a7bc4);
            border-color: var(--neko-popup-active, #2a7bc4);
        }
        .vrm-toggle-checkmark {
            color: #fff;
            font-size: 13px;
            font-weight: bold;
            line-height: 1;
            opacity: 0;
            transition: opacity 0.2s ease;
            pointer-events: none;
            user-select: none;
        }
        .vrm-toggle-indicator[aria-checked="true"] .vrm-toggle-checkmark {
            opacity: 1;
        }
        .vrm-toggle-label {
            cursor: pointer;
            user-select: none;
            font-size: 13px;
            color: var(--neko-popup-text, #333);
        }
        .vrm-toggle-item:hover:not([aria-disabled="true"]) {
            background: var(--neko-popup-hover, rgba(68, 183, 254, 0.1));
        }
        .vrm-settings-menu-item {
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
            position: relative;
            z-index: 100002;
        }
        .vrm-settings-menu-item:hover {
            background: var(--neko-popup-hover, rgba(68, 183, 254, 0.1));
        }
        .vrm-settings-separator {
            height: 1px;
            background: var(--neko-popup-separator, rgba(0, 0, 0, 0.1));
            margin: 4px 0;
        }
        .vrm-agent-status {
            font-size: 12px;
            color: var(--neko-popup-accent, #2a7bc4);
            padding: 6px 8px;
            border-radius: 4px;
            background: var(--neko-popup-accent-bg, rgba(42, 123, 196, 0.05));
            margin-bottom: 8px;
            min-height: 20px;
            text-align: center;
        }
    `;
    document.head.appendChild(style);
})();

// 创建弹出框
VRMManager.prototype.createPopup = function (buttonId) {
    const popup = document.createElement('div');
    popup.id = `vrm-popup-${buttonId}`;
    popup.className = 'vrm-popup';

    const stopEventPropagation = (e) => { e.stopPropagation(); };
    ['pointerdown', 'pointermove', 'pointerup', 'mousedown', 'mousemove', 'mouseup', 'touchstart', 'touchmove', 'touchend'].forEach(evt => {
        popup.addEventListener(evt, stopEventPropagation, true);
    });

    if (buttonId === 'mic') {
        popup.setAttribute('data-legacy-id', 'vrm-mic-popup');
        // 双栏布局：加宽弹出框，横向排列（与 Live2D 保持一致）
        popup.style.minWidth = '400px';
        popup.style.maxHeight = '320px';
        popup.style.flexDirection = 'row';
        popup.style.gap = '0';
        popup.style.overflowY = 'hidden';  // 整体不滚动，右栏单独滚动
    } else if (buttonId === 'screen') {
        // 屏幕/窗口源选择列表：与 Live2D 保持一致的宽度与滚动行为
        popup.style.width = '420px';
        popup.style.maxHeight = '400px';
        popup.style.overflowX = 'hidden';
        popup.style.overflowY = 'auto';
    } else if (buttonId === 'agent') {
        popup.classList.add('vrm-popup-agent');
        window.AgentHUD._createAgentPopupContent.call(this, popup);
    } else if (buttonId === 'settings') {
        // 避免小屏溢出：限制高度并允许滚动
        popup.classList.add('vrm-popup-settings');
        this._createSettingsPopupContent(popup);
    }

    return popup;
};

// 创建Agent弹出框内容
VRMManager.prototype._createAgentPopupContent = function (popup) {
    const statusDiv = document.createElement('div');
    statusDiv.id = 'vrm-agent-status';
    statusDiv.className = 'vrm-agent-status';
    statusDiv.textContent = window.t ? window.t('settings.toggles.checking') : '查询中...';
    popup.appendChild(statusDiv);

    const agentToggles = [
        { id: 'agent-master', label: window.t ? window.t('settings.toggles.agentMaster') : 'Agent总开关', labelKey: 'settings.toggles.agentMaster', initialDisabled: true },
        { id: 'agent-keyboard', label: window.t ? window.t('settings.toggles.keyboardControl') : '键鼠控制', labelKey: 'settings.toggles.keyboardControl', initialDisabled: true },
        { id: 'agent-browser', label: window.t ? window.t('settings.toggles.browserUse') : 'Browser Control', labelKey: 'settings.toggles.browserUse', initialDisabled: true }
    ];

    agentToggles.forEach(toggle => {
        const toggleItem = this._createToggleItem(toggle, popup);
        popup.appendChild(toggleItem);
    });

    // 添加适配中的按钮（不可选）
    const adaptingItems = [
        { labelKey: 'settings.toggles.userPluginAdapting', fallback: '用户插件（开发中）' },
        { labelKey: 'settings.toggles.moltbotAdapting', fallback: 'moltbot（开发中）' }
    ];

    adaptingItems.forEach(item => {
        const adaptingItem = document.createElement('div');
        Object.assign(adaptingItem.style, {
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            padding: '6px 8px',
            borderRadius: '6px',
            fontSize: '13px',
            whiteSpace: 'nowrap',
            opacity: '0.5',
            cursor: 'not-allowed',
            color: 'var(--neko-popup-text-sub, #666)'
        });

        const indicator = document.createElement('div');
        Object.assign(indicator.style, {
            width: '20px',
            height: '20px',
            borderRadius: '50%',
            border: '2px solid var(--neko-popup-indicator-border, #ccc)',
            backgroundColor: 'transparent',
            flexShrink: '0'
        });

        const label = document.createElement('span');
        label.textContent = window.t ? window.t(item.labelKey) : item.fallback;
        label.setAttribute('data-i18n', item.labelKey);
        label.style.userSelect = 'none';
        label.style.fontSize = '13px';
        label.style.color = 'var(--neko-popup-text-sub, #666)';

        adaptingItem.appendChild(indicator);
        adaptingItem.appendChild(label);
        popup.appendChild(adaptingItem);
    });
};

// 创建设置弹出框内容
VRMManager.prototype._createSettingsPopupContent = function (popup) {
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

    // 2. 动画设置按钮（侧边弹出：画质 + 帧率）
    const animSettingsBtn = this._createSettingsMenuButton({
        label: window.t ? window.t('settings.toggles.animationSettings') : '动画设置',
        labelKey: 'settings.toggles.animationSettings'
    });
    popup.appendChild(animSettingsBtn);

    const animSidePanel = this._createAnimationSettingsSidePanel();
    animSidePanel._anchorElement = animSettingsBtn;
    animSidePanel._popupElement = popup;
    this._attachSidePanelHover(animSettingsBtn, animSidePanel);

    // 3. 主动搭话和自主视觉（保持原有逻辑）
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
                    display: 'flex',
                    alignItems: 'center',
                    gap: '6px',
                    padding: '4px 8px',
                    marginLeft: '-6px',
                    fontSize: '12px',
                    color: 'var(--neko-popup-text, #333)',
                    cursor: 'pointer',
                    borderRadius: '6px',
                    transition: 'background 0.2s ease',
                    width: '100%'
                });

                const authIcon = document.createElement('img');
                authIcon.src = '/static/icons/cookies_icon.png';
                authIcon.alt = '';
                Object.assign(authIcon.style, {
                    width: '16px', height: '16px', objectFit: 'contain', flexShrink: '0'
                });
                authLink.appendChild(authIcon);

                const authLabel = document.createElement('span');
                authLabel.textContent = window.t ? window.t(AUTH_I18N_KEY) : AUTH_FALLBACK_LABEL;
                authLabel.setAttribute('data-i18n', AUTH_I18N_KEY);
                Object.assign(authLabel.style, { fontSize: '12px', userSelect: 'none' });
                authLink.appendChild(authLabel);

                authLink.addEventListener('mouseenter', () => {
                    authLink.style.background = 'var(--neko-popup-hover, rgba(68,183,254,0.1))';
                });
                authLink.addEventListener('mouseleave', () => {
                    authLink.style.background = 'transparent';
                });
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
    if (!window.isMobileWidth()) {
        const separator = document.createElement('div');
        separator.className = 'vrm-settings-separator';
        popup.appendChild(separator);

        this._createSettingsMenuItems(popup);
    }
};

// 创建设置菜单按钮（非开关型，带右箭头指示器）
VRMManager.prototype._createSettingsMenuButton = function (config) {
    const btn = document.createElement('div');
    btn.className = 'vrm-settings-menu-item';
    Object.assign(btn.style, {
        justifyContent: 'space-between'
    });

    const label = document.createElement('span');
    label.textContent = config.label;
    if (config.labelKey) label.setAttribute('data-i18n', config.labelKey);
    Object.assign(label.style, {
        userSelect: 'none',
        fontSize: '13px'
    });
    btn.appendChild(label);

    const arrow = document.createElement('span');
    arrow.textContent = '›';
    Object.assign(arrow.style, {
        fontSize: '16px',
        color: 'var(--neko-popup-text-sub, #999)',
        lineHeight: '1',
        flexShrink: '0'
    });
    btn.appendChild(arrow);

    if (config.labelKey) {
        btn._updateLabelText = () => {
            if (window.t) label.textContent = window.t(config.labelKey);
        };
    }

    return btn;
};

// 创建对话设置侧边弹出面板（合并消息 + 允许打断）
VRMManager.prototype._createChatSettingsSidePanel = function (popup) {
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

// 创建动画设置侧边弹出面板（画质 + 帧率滑动条）
VRMManager.prototype._createAnimationSettingsSidePanel = function () {
    const container = this._createSidePanelContainer();
    container.style.flexDirection = 'column';
    container.style.alignItems = 'stretch';
    container.style.gap = '8px';
    container.style.width = '168px';
    container.style.minWidth = '0';
    container.style.padding = '10px 14px';

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

    const qualityLabelKeys = [
        'settings.toggles.renderQualityLow',
        'settings.toggles.renderQualityMedium',
        'settings.toggles.renderQualityHigh'
    ];
    const qualityDefaults = ['低', '中', '高'];
    const qualityValue = document.createElement('span');
    const curQIdx = parseInt(qualitySlider.value, 10);
    qualityValue.textContent = window.t ? window.t(qualityLabelKeys[curQIdx]) : qualityDefaults[curQIdx];
    Object.assign(qualityValue.style, VALUE_STYLE);

    qualitySlider.addEventListener('input', () => {
        const idx = parseInt(qualitySlider.value, 10);
        qualityValue.textContent = window.t ? window.t(qualityLabelKeys[idx]) : qualityDefaults[idx];
    });
    const mapRenderQualityToFollowPerf = (quality) => (quality === 'high' ? 'medium' : 'low');
    qualitySlider.addEventListener('change', () => {
        const idx = parseInt(qualitySlider.value, 10);
        const quality = qualityNames[idx];
        window.renderQuality = quality;
        const followLevel = mapRenderQualityToFollowPerf(quality);
        window.cursorFollowPerformanceLevel = followLevel;
        if (window.vrmManager && typeof window.vrmManager.setCursorFollowPerformance === 'function') {
            window.vrmManager.setCursorFollowPerformance(followLevel);
        }
        window.dispatchEvent(new CustomEvent('neko-cursor-follow-performance-changed', { detail: { level: followLevel } }));
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
    Object.assign(fpsValue.style, VALUE_STYLE);

    fpsSlider.addEventListener('input', () => {
        const idx = parseInt(fpsSlider.value, 10);
        fpsValue.textContent = window.t ? window.t(fpsLabelKeys[idx]) : fpsDefaults[idx];
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

    // --- 跟踪鼠标开关 ---
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
    mouseTrackingCheckbox.id = 'vrm-mouse-tracking-toggle';
    mouseTrackingCheckbox.checked = window.mouseTrackingEnabled !== false;
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
            ? 'var(--neko-popup-selected-bg, rgba(68,183,254,0.1))'
            : 'transparent';
    };
    // 与弹窗显示时的通用 syncCheckbox 机制兼容
    mouseTrackingCheckbox.updateStyle = updateMouseTrackingRowStyle;
    updateMouseTrackingRowStyle();

    const handleMouseTrackingToggle = () => {
        mouseTrackingCheckbox.checked = !mouseTrackingCheckbox.checked;
        window.mouseTrackingEnabled = mouseTrackingCheckbox.checked;
        updateMouseTrackingRowStyle();

        if (typeof window.saveNEKOSettings === 'function') window.saveNEKOSettings();

        if (window.vrmManager && typeof window.vrmManager.setMouseTrackingEnabled === 'function') {
            window.vrmManager.setMouseTrackingEnabled(mouseTrackingCheckbox.checked);
        }
        console.log(`[VRM] 跟踪鼠标已${mouseTrackingCheckbox.checked ? '开启' : '关闭'}`);
    };

    mouseTrackingRow.addEventListener('click', (e) => {
        e.stopPropagation();
        handleMouseTrackingToggle();
    });
    mouseTrackingIndicator.addEventListener('click', (e) => {
        e.stopPropagation();
        handleMouseTrackingToggle();
    });
    const handleMouseTrackingKeydown = (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            e.stopPropagation();
            handleMouseTrackingToggle();
        }
    };
    mouseTrackingRow.addEventListener('keydown', handleMouseTrackingKeydown);
    mouseTrackingIndicator.addEventListener('keydown', handleMouseTrackingKeydown);
    mouseTrackingLabel.addEventListener('click', (e) => {
        e.stopPropagation();
        handleMouseTrackingToggle();
    });

    mouseTrackingRow.addEventListener('mouseenter', () => {
        if (mouseTrackingCheckbox.checked) {
            mouseTrackingRow.style.background = 'var(--neko-popup-selected-hover, rgba(68,183,254,0.15))';
        } else {
            mouseTrackingRow.style.background = 'var(--neko-popup-hover-subtle, rgba(68,183,254,0.08))';
        }
    });
    mouseTrackingRow.addEventListener('mouseleave', () => {
        updateMouseTrackingRowStyle();
    });

    mouseTrackingRow.appendChild(mouseTrackingCheckbox);
    mouseTrackingRow.appendChild(mouseTrackingIndicator);
    mouseTrackingRow.appendChild(mouseTrackingLabel);
    container.appendChild(mouseTrackingRow);

    document.body.appendChild(container);
    return container;
};

// 创建侧边弹出面板容器（公共基础样式）
VRMManager.prototype._createSidePanelContainer = function () {
    const container = document.createElement('div');
    Object.assign(container.style, {
        position: 'fixed',
        display: 'none',
        alignItems: 'center',
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
        flexWrap: 'wrap',
        maxWidth: '300px'
    });

    const stopEventPropagation = (e) => e.stopPropagation();
    ['pointerdown', 'pointermove', 'pointerup', 'mousedown', 'mousemove', 'mouseup', 'touchstart', 'touchmove', 'touchend'].forEach(evt => {
        container.addEventListener(evt, stopEventPropagation, true);
    });

    container._expand = () => {
        if (container.style.display === 'flex' && container.style.opacity !== '0') return;
        if (container._collapseTimeout) { clearTimeout(container._collapseTimeout); container._collapseTimeout = null; }
        container.style.display = 'flex';
        container.style.left = '';
        container.style.right = '';
        container.style.transform = 'translateX(-6px)';

        const anchor = container._anchorElement;
        const popupEl = container._popupElement;
        if (anchor) {
            const anchorRect = anchor.getBoundingClientRect();
            const popupRect = popupEl ? popupEl.getBoundingClientRect() : anchorRect;
            container.style.top = `${anchorRect.top}px`;
            container.style.left = `${popupRect.right - 8}px`;
        }

        requestAnimationFrame(() => {
            const containerRect = container.getBoundingClientRect();
            if (containerRect.right > window.innerWidth - 10) {
                const popupEl2 = container._popupElement;
                const popupRect = popupEl2 ? popupEl2.getBoundingClientRect() : null;
                if (popupRect) {
                    container.style.left = '';
                    container.style.right = `${window.innerWidth - popupRect.left - 8}px`;
                    container.style.transform = 'translateX(6px)';
                }
            }
            requestAnimationFrame(() => {
                container.style.opacity = '1';
                container.style.transform = 'translateX(0)';
            });
        });
    };

    container._collapse = () => {
        if (container.style.display === 'none') return;
        if (container._collapseTimeout) { clearTimeout(container._collapseTimeout); container._collapseTimeout = null; }
        container.style.opacity = '0';
        if (container.style.right && container.style.right !== '') {
            container.style.transform = 'translateX(6px)';
        } else {
            container.style.transform = 'translateX(-6px)';
        }
        container._collapseTimeout = setTimeout(() => {
            if (container.style.opacity === '0') container.style.display = 'none';
            container._collapseTimeout = null;
        }, VRM_POPUP_ANIMATION_DURATION_MS);
    };

    return container;
};

// 附加侧边面板悬停逻辑（公共方法，供按钮和开关复用）
VRMManager.prototype._attachSidePanelHover = function (anchorEl, sidePanel) {
    const self = this;
    const popupEl = sidePanel._popupElement || null;
    const ownerId = popupEl && popupEl.id ? popupEl.id : '';

    if (ownerId) {
        sidePanel.setAttribute('data-neko-sidepanel-owner', ownerId);
    }

    const collapseWithDelay = (delay = 80) => {
        if (sidePanel._hoverCollapseTimer) {
            clearTimeout(sidePanel._hoverCollapseTimer);
            sidePanel._hoverCollapseTimer = null;
        }
        sidePanel._hoverCollapseTimer = setTimeout(() => {
            const anchorHovered = anchorEl.matches(':hover');
            const panelHovered = sidePanel.matches(':hover');
            if (!anchorHovered && !panelHovered) {
                sidePanel._collapse();
            }
            sidePanel._hoverCollapseTimer = null;
        }, delay);
    };

    const expandPanel = () => {
        if (ownerId) {
            document.querySelectorAll(`[data-neko-sidepanel-owner="${ownerId}"]`).forEach((panel) => {
                if (panel !== sidePanel && typeof panel._collapse === 'function') {
                    panel._collapse();
                }
            });
        }
        if (sidePanel._hoverCollapseTimer) {
            clearTimeout(sidePanel._hoverCollapseTimer);
            sidePanel._hoverCollapseTimer = null;
        }
        sidePanel._expand();
    };
    const collapsePanel = (e) => {
        const target = e.relatedTarget;
        if (!target || (!anchorEl.contains(target) && !sidePanel.contains(target))) {
            collapseWithDelay();
        }
    };

    anchorEl.addEventListener('mouseenter', expandPanel);
    anchorEl.addEventListener('mouseleave', collapsePanel);
    sidePanel.addEventListener('mouseenter', () => {
        expandPanel();
        if (self.interaction) {
            self.interaction._isMouseOverButtons = true;
            if (self.interaction._hideButtonsTimer) {
                clearTimeout(self.interaction._hideButtonsTimer);
                self.interaction._hideButtonsTimer = null;
            }
        }
    });
    sidePanel.addEventListener('mouseleave', (e) => {
        collapsePanel(e);
        if (self.interaction) {
            self.interaction._isMouseOverButtons = false;
        }
    });

    // 快速离开整个 settings popup 时，兜底收起侧栏
    if (popupEl) {
        popupEl.addEventListener('mouseleave', (e) => {
            const target = e.relatedTarget;
            if (!target || (!anchorEl.contains(target) && !sidePanel.contains(target))) {
                collapseWithDelay(60);
            }
        });
    }
};

// 创建时间间隔控件（侧边弹出面板）
VRMManager.prototype._createIntervalControl = function (toggle) {
    const container = document.createElement('div');
    container.className = `vrm-interval-control-${toggle.id}`;
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

    // 阻止指针事件传播到底层
    const stopEventPropagation = (e) => e.stopPropagation();
    ['pointerdown', 'pointermove', 'pointerup', 'mousedown', 'mousemove', 'mouseup', 'touchstart', 'touchmove', 'touchend'].forEach(evt => {
        container.addEventListener(evt, stopEventPropagation, true);
    });

    // 滑动条行容器
    const sliderRow = document.createElement('div');
    Object.assign(sliderRow.style, {
        display: 'flex',
        alignItems: 'center',
        gap: '4px',
        width: 'auto'
    });

    // 间隔标签
    const labelText = document.createElement('span');
    const labelKey = toggle.id === 'proactive-chat' ? 'settings.interval.chatIntervalBase' : 'settings.interval.visionInterval';
    const defaultLabel = toggle.id === 'proactive-chat' ? '基础间隔' : '读取间隔';
    labelText.textContent = window.t ? window.t(labelKey) : defaultLabel;
    labelText.setAttribute('data-i18n', labelKey);
    Object.assign(labelText.style, {
        flexShrink: '0',
        fontSize: '12px'
    });

    // 滑动条
    const slider = document.createElement('input');
    slider.type = 'range';
    slider.id = `vrm-${toggle.id}-interval`;
    const minVal = toggle.id === 'proactive-chat' ? 10 : 5;
    slider.min = minVal;
    slider.max = '120';
    slider.step = '5';
    let currentValue = typeof window[toggle.intervalKey] !== 'undefined'
        ? window[toggle.intervalKey]
        : toggle.defaultInterval;
    if (currentValue > 120) currentValue = 120;
    slider.value = currentValue;
    Object.assign(slider.style, {
        width: '60px',
        height: '4px',
        cursor: 'pointer',
        accentColor: 'var(--neko-popup-accent, #44b7fe)'
    });

    // 数值显示
    const valueDisplay = document.createElement('span');
    valueDisplay.textContent = `${currentValue}s`;
    Object.assign(valueDisplay.style, {
        minWidth: '26px',
        textAlign: 'right',
        fontFamily: 'monospace',
        fontSize: '12px',
        flexShrink: '0'
    });

    // 滑动条事件
    slider.addEventListener('input', () => {
        valueDisplay.textContent = `${parseInt(slider.value, 10)}s`;
    });
    slider.addEventListener('change', () => {
        const value = parseInt(slider.value, 10);
        window[toggle.intervalKey] = value;
        if (typeof window.saveNEKOSettings === 'function') {
            window.saveNEKOSettings();
        }
        console.log(`${toggle.id} 间隔已设置为 ${value} 秒`);
    });
    slider.addEventListener('click', (e) => e.stopPropagation());
    slider.addEventListener('mousedown', (e) => e.stopPropagation());

    sliderRow.appendChild(labelText);
    sliderRow.appendChild(slider);
    sliderRow.appendChild(valueDisplay);
    container.appendChild(sliderRow);

    // 如果是主动搭话，在间隔控件内添加搭话方式选项
    if (toggle.id === 'proactive-chat') {
        if (typeof window.createChatModeToggles === 'function') {
            const chatModesContainer = window.createChatModeToggles('vrm');
            container.appendChild(chatModesContainer);
        }
    }

    // 侧边弹出展开方法
    container._expand = () => {
        if (container.style.display === 'flex' && container.style.opacity !== '0') return;

        if (container._collapseTimeout) {
            clearTimeout(container._collapseTimeout);
            container._collapseTimeout = null;
        }

        container.style.display = 'flex';
        container.style.left = '';
        container.style.right = '';
        container.style.transform = 'translateX(-6px)';

        // 根据锚点元素和 popup 计算位置
        const anchor = container._anchorElement;
        const popupEl = container._popupElement;
        if (anchor) {
            const anchorRect = anchor.getBoundingClientRect();
            const popupRect = popupEl ? popupEl.getBoundingClientRect() : anchorRect;
            container.style.top = `${anchorRect.top}px`;
            container.style.left = `${popupRect.right - 8}px`;
        }

        requestAnimationFrame(() => {
            // 检测右侧是否溢出视口
            const containerRect = container.getBoundingClientRect();
            if (containerRect.right > window.innerWidth - 10) {
                const popupEl2 = container._popupElement;
                const popupRect = popupEl2 ? popupEl2.getBoundingClientRect() : null;
                if (popupRect) {
                    container.style.left = '';
                    container.style.right = `${window.innerWidth - popupRect.left - 8}px`;
                    container.style.transform = 'translateX(6px)';
                }
            }
            requestAnimationFrame(() => {
                container.style.opacity = '1';
                container.style.transform = 'translateX(0)';
            });
        });
    };

    // 侧边弹出收缩方法
    container._collapse = () => {
        if (container.style.display === 'none') return;
        if (container._collapseTimeout) {
            clearTimeout(container._collapseTimeout);
            container._collapseTimeout = null;
        }
        container.style.opacity = '0';
        if (container.style.right && container.style.right !== '') {
            container.style.transform = 'translateX(6px)';
        } else {
            container.style.transform = 'translateX(-6px)';
        }
        container._collapseTimeout = setTimeout(() => {
            if (container.style.opacity === '0') {
                container.style.display = 'none';
            }
            container._collapseTimeout = null;
        }, VRM_POPUP_ANIMATION_DURATION_MS);
    };

    // 附加到 body（不在 popup 流中，避免被 popup 的 overflow 裁剪）
    document.body.appendChild(container);

    return container;
};

// 创建Agent开关项
VRMManager.prototype._createToggleItem = function (toggle, popup) {
    const toggleItem = document.createElement('div');
    toggleItem.className = 'vrm-toggle-item';
    toggleItem.setAttribute('role', 'switch');
    toggleItem.setAttribute('tabIndex', toggle.initialDisabled ? '-1' : '0');
    toggleItem.setAttribute('aria-checked', 'false');
    toggleItem.setAttribute('aria-disabled', toggle.initialDisabled ? 'true' : 'false');

    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.id = `vrm-${toggle.id}`;
    checkbox.style.position = 'absolute';
    checkbox.style.opacity = '0';
    checkbox.style.width = '1px';
    checkbox.style.height = '1px';
    checkbox.style.overflow = 'hidden';
    checkbox.setAttribute('aria-hidden', 'true');

    if (toggle.initialDisabled) {
        checkbox.disabled = true;
        checkbox.title = window.t ? window.t('settings.toggles.checking') : '查询中...';
    }

    const indicator = document.createElement('div');
    indicator.className = 'vrm-toggle-indicator';
    indicator.setAttribute('role', 'presentation');
    indicator.setAttribute('aria-hidden', 'true');

    const checkmark = document.createElement('div');
    checkmark.className = 'vrm-toggle-checkmark';
    checkmark.innerHTML = '✓';
    indicator.appendChild(checkmark);

    const label = document.createElement('label');
    label.className = 'vrm-toggle-label';
    label.innerText = toggle.label;
    if (toggle.labelKey) label.setAttribute('data-i18n', toggle.labelKey);
    label.htmlFor = `vrm-${toggle.id}`;
    toggleItem.setAttribute('aria-label', toggle.label);

    // 更新标签文本的函数
    const updateLabelText = () => {
        if (toggle.labelKey && window.t) {
            label.innerText = window.t(toggle.labelKey);
            toggleItem.setAttribute('aria-label', window.t(toggle.labelKey));
        }
    };
    if (toggle.labelKey) {
        toggleItem._updateLabelText = updateLabelText;
    }

    const updateStyle = () => {
        const isChecked = checkbox.checked;
        toggleItem.setAttribute('aria-checked', isChecked ? 'true' : 'false');
        indicator.setAttribute('aria-checked', isChecked ? 'true' : 'false');
    };

    // 同步禁用态视觉，避免出现“灰色但可交互”的状态漂移
    const updateDisabledStyle = () => {
        const disabled = checkbox.disabled;
        toggleItem.setAttribute('aria-disabled', disabled ? 'true' : 'false');
        toggleItem.setAttribute('tabIndex', disabled ? '-1' : '0');
        // 清理初始写死透明度，确保可交互态视觉能恢复
        toggleItem.style.opacity = disabled ? '0.5' : '1';
        const cursor = disabled ? 'default' : 'pointer';
        [toggleItem, label, indicator].forEach(el => {
            el.style.cursor = cursor;
        });
    };

    // 同步 title 到整行，保证悬浮提示一致
    const updateTitle = () => {
        const title = checkbox.title || '';
        toggleItem.title = title;
        label.title = title;
    };

    checkbox.addEventListener('change', updateStyle);
    updateStyle();
    updateDisabledStyle();
    updateTitle();

    // 监听外部（app.js 状态机）对 disabled/title 的变更并更新视觉状态
    const disabledObserver = new MutationObserver(() => {
        updateDisabledStyle();
        updateTitle();
    });
    disabledObserver.observe(checkbox, { attributes: true, attributeFilter: ['disabled', 'title'] });

    toggleItem.appendChild(checkbox); toggleItem.appendChild(indicator); toggleItem.appendChild(label);
    checkbox._updateStyle = () => {
        updateStyle();
        updateDisabledStyle();
        updateTitle();
    };
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

    // 键盘支持
    toggleItem.addEventListener('keydown', (e) => {
        if (checkbox.disabled) return;
        if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            handleToggle(e);
        }
    });

    [toggleItem, indicator, label].forEach(el => el.addEventListener('click', (e) => {
        if (e.target !== checkbox) handleToggle(e);
    }));

    return toggleItem;
};

// 创建圆形指示器和对勾的辅助方法
VRMManager.prototype._createCheckIndicator = function () {
    const indicator = document.createElement('div');
    Object.assign(indicator.style, {
        width: '20px',
        height: '20px',
        borderRadius: '50%',
        border: '2px solid var(--neko-popup-indicator-border, #ccc)',
        backgroundColor: 'transparent',
        cursor: 'pointer',
        flexShrink: '0',
        transition: 'all 0.2s ease',
        position: 'relative',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center'
    });

    const checkmark = document.createElement('div');
    checkmark.textContent = '✓';
    Object.assign(checkmark.style, {
        color: '#fff',
        fontSize: '13px',
        fontWeight: 'bold',
        lineHeight: '1',
        opacity: '0',
        transition: 'opacity 0.2s ease',
        pointerEvents: 'none',
        userSelect: 'none'
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
VRMManager.prototype._createSettingsToggleItem = function (toggle) {
    const toggleItem = document.createElement('div');
    toggleItem.className = 'vrm-toggle-item';
    toggleItem.id = `vrm-toggle-${toggle.id}`;
    toggleItem.setAttribute('role', 'switch');
    toggleItem.setAttribute('tabIndex', '0');
    toggleItem.setAttribute('aria-checked', 'false');
    toggleItem.setAttribute('aria-label', toggle.label);
    Object.assign(toggleItem.style, {
        padding: '8px 12px'
    });

    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.id = `vrm-${toggle.id}`;
    Object.assign(checkbox.style, {
        position: 'absolute',
        width: '1px',
        height: '1px',
        padding: '0',
        margin: '-1px',
        overflow: 'hidden',
        clip: 'rect(0, 0, 0, 0)',
        whiteSpace: 'nowrap',
        border: '0'
    });
    checkbox.setAttribute('aria-hidden', 'true');
    checkbox.setAttribute('tabindex', '-1');

    if (toggle.id === 'merge-messages') {
        if (typeof window.mergeMessagesEnabled !== 'undefined') {
            checkbox.checked = window.mergeMessagesEnabled;
        }
    } else if (toggle.id === 'focus-mode' && typeof window.focusModeEnabled !== 'undefined') {
        checkbox.checked = toggle.inverted ? !window.focusModeEnabled : window.focusModeEnabled;
    } else if (toggle.id === 'proactive-chat' && typeof window.proactiveChatEnabled !== 'undefined') {
        checkbox.checked = window.proactiveChatEnabled;
    } else if (toggle.id === 'proactive-vision' && typeof window.proactiveVisionEnabled !== 'undefined') {
        checkbox.checked = window.proactiveVisionEnabled;
    }

    const indicator = document.createElement('div');
    indicator.className = 'vrm-toggle-indicator';
    indicator.setAttribute('role', 'presentation');
    indicator.setAttribute('aria-hidden', 'true');

    const checkmark = document.createElement('div');
    checkmark.className = 'vrm-toggle-checkmark';
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
    if (toggle.labelKey) {
        label.setAttribute('data-i18n', toggle.labelKey);
    }
    label.style.cursor = 'pointer';
    label.style.userSelect = 'none';
    label.style.fontSize = '13px';
    label.style.color = 'var(--neko-popup-text, #333)';
    label.style.display = 'flex';
    label.style.alignItems = 'center';
    label.style.lineHeight = '1';
    label.style.height = '20px';

    const updateStyle = () => {
        const isChecked = checkbox.checked;
        toggleItem.setAttribute('aria-checked', isChecked ? 'true' : 'false');
        indicator.setAttribute('aria-checked', isChecked ? 'true' : 'false');
        updateIndicatorStyle(isChecked);
        toggleItem.style.background = isChecked
            ? 'var(--neko-popup-selected-bg, rgba(68,183,254,0.1))'
            : 'transparent';
    };

    updateStyle();

    toggleItem.appendChild(checkbox);
    toggleItem.appendChild(indicator);
    toggleItem.appendChild(label);

    toggleItem.addEventListener('mouseenter', () => {
        if (checkbox.checked) {
            toggleItem.style.background = 'var(--neko-popup-selected-hover, rgba(68,183,254,0.15))';
        } else {
            toggleItem.style.background = 'var(--neko-popup-hover-subtle, rgba(68,183,254,0.08))';
        }
    });
    toggleItem.addEventListener('mouseleave', () => {
        updateStyle();
    });

    const handleToggleChange = (isChecked) => {
        updateStyle();

        if (toggle.id === 'merge-messages') {
            window.mergeMessagesEnabled = isChecked;
            if (typeof window.saveNEKOSettings === 'function') {
                window.saveNEKOSettings();
            }
        } else if (toggle.id === 'focus-mode') {
            const actualValue = toggle.inverted ? !isChecked : isChecked;
            window.focusModeEnabled = actualValue;
            if (typeof window.saveNEKOSettings === 'function') {
                window.saveNEKOSettings();
            }
        } else if (toggle.id === 'proactive-chat') {
            window.proactiveChatEnabled = isChecked;
            if (typeof window.saveNEKOSettings === 'function') {
                window.saveNEKOSettings();
            }
            if (isChecked && typeof window.resetProactiveChatBackoff === 'function') {
                window.resetProactiveChatBackoff();
            } else if (!isChecked && typeof window.stopProactiveChatSchedule === 'function') {
                window.stopProactiveChatSchedule();
            }
        } else if (toggle.id === 'proactive-vision') {
            window.proactiveVisionEnabled = isChecked;
            if (typeof window.saveNEKOSettings === 'function') {
                window.saveNEKOSettings();
            }
            if (isChecked) {
                if (typeof window.resetProactiveChatBackoff === 'function') {
                    window.resetProactiveChatBackoff();
                }
                if (typeof window.isRecording !== 'undefined' && window.isRecording) {
                    if (typeof window.startProactiveVisionDuringSpeech === 'function') {
                        window.startProactiveVisionDuringSpeech();
                    }
                }
            } else {
                if (typeof window.stopProactiveChatSchedule === 'function') {
                    if (!window.proactiveChatEnabled) {
                        window.stopProactiveChatSchedule();
                    }
                }
                if (typeof window.stopProactiveVisionDuringSpeech === 'function') {
                    window.stopProactiveVisionDuringSpeech();
                }
            }
        }
    };

    const performToggle = () => {
        if (checkbox.disabled) {
            return;
        }

        if (checkbox._processing) {
            const elapsed = Date.now() - (checkbox._processingTime || 0);
            if (elapsed < 500) {
                return;
            }
        }

        checkbox._processing = true;
        checkbox._processingTime = Date.now();

        const newChecked = !checkbox.checked;
        checkbox.checked = newChecked;
        handleToggleChange(newChecked);
        checkbox.dispatchEvent(new Event('change', { bubbles: true }));

        setTimeout(() => {
            checkbox._processing = false;
            checkbox._processingTime = null;
        }, 500);
    };

    toggleItem.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            performToggle();
        }
    });

    toggleItem.addEventListener('click', (e) => {
        if (e.target !== checkbox) {
            e.preventDefault();
            e.stopPropagation();
            performToggle();
        }
    });

    indicator.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        performToggle();
    });

    label.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        performToggle();
    });

    checkbox.updateStyle = updateStyle;

    return toggleItem;
};

// 创建设置菜单项 (保持与Live2D一致)
VRMManager.prototype._createSettingsMenuItems = function (popup) {
    const settingsItems = [
        {
            id: 'character',
            label: window.t ? window.t('settings.menu.characterManage') : '角色管理',
            labelKey: 'settings.menu.characterManage',
            icon: '/static/icons/character_icon.png',
            action: 'navigate',
            url: '/chara_manager',
            // 子菜单：通用设置、模型管理、声音克隆
            submenu: [
                { id: 'general', label: window.t ? window.t('settings.menu.general') : '通用设置', labelKey: 'settings.menu.general', icon: '/static/icons/live2d_settings_icon.png', action: 'navigate', url: '/chara_manager' },
                { id: 'vrm-manage', label: window.t ? window.t('settings.menu.modelSettings') : '模型管理', labelKey: 'settings.menu.modelSettings', icon: '/static/icons/character_icon.png', action: 'navigate', urlBase: '/model_manager' },
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

        // 如果有子菜单，创建可折叠的子菜单容器
        if (item.submenu && item.submenu.length > 0) {
            const submenuContainer = this._createSubmenuContainer(item.submenu);
            popup.appendChild(submenuContainer);

            // 鼠标悬停展开/收缩：增加缓冲，避免主项和子项之间小缝隙导致抖动
            let submenuCollapseTimer = null;
            const clearSubmenuCollapseTimer = () => {
                if (submenuCollapseTimer) {
                    clearTimeout(submenuCollapseTimer);
                    submenuCollapseTimer = null;
                }
            };
            const expandSubmenu = () => {
                clearSubmenuCollapseTimer();
                submenuContainer._expand();
            };
            const scheduleSubmenuCollapse = () => {
                clearSubmenuCollapseTimer();
                submenuCollapseTimer = setTimeout(() => {
                    submenuContainer._collapse();
                    submenuCollapseTimer = null;
                }, 110);
            };

            menuItem.addEventListener('mouseenter', expandSubmenu);
            menuItem.addEventListener('mouseleave', (e) => {
                const target = e.relatedTarget;
                if (target && (menuItem.contains(target) || submenuContainer.contains(target))) {
                    return;
                }
                scheduleSubmenuCollapse();
            });
            submenuContainer.addEventListener('mouseenter', expandSubmenu);
            submenuContainer.addEventListener('mouseleave', (e) => {
                const target = e.relatedTarget;
                if (target && (menuItem.contains(target) || submenuContainer.contains(target))) {
                    return;
                }
                scheduleSubmenuCollapse();
            });
        }
    });
};

// 创建单个菜单项
VRMManager.prototype._createMenuItem = function (item, isSubmenuItem = false) {
    const menuItem = document.createElement('div');
    menuItem.className = 'vrm-settings-menu-item';
    Object.assign(menuItem.style, {
        display: 'flex',
        alignItems: 'center',
        gap: '8px',
        padding: isSubmenuItem ? '6px 12px 6px 36px' : '8px 12px',
        cursor: 'pointer',
        borderRadius: '6px',
        transition: 'background 0.2s ease',
        fontSize: isSubmenuItem ? '12px' : '13px',
        whiteSpace: 'nowrap',
        color: 'var(--neko-popup-text, #333)'
    });

    if (item.icon) {
        const iconImg = document.createElement('img');
        iconImg.src = item.icon;
        iconImg.alt = item.label;
        Object.assign(iconImg.style, {
            width: isSubmenuItem ? '18px' : '24px',
            height: isSubmenuItem ? '18px' : '24px',
            objectFit: 'contain',
            flexShrink: '0'
        });
        menuItem.appendChild(iconImg);
    }

    const labelText = document.createElement('span');
    labelText.textContent = item.label;
    if (item.labelKey) labelText.setAttribute('data-i18n', item.labelKey);
    Object.assign(labelText.style, {
        display: 'flex',
        alignItems: 'center',
        lineHeight: '1',
        height: isSubmenuItem ? '18px' : '24px'
    });
    menuItem.appendChild(labelText);

    if (item.labelKey) {
        menuItem._updateLabelText = () => {
            if (window.t) {
                labelText.textContent = window.t(item.labelKey);
                if (item.icon && menuItem.querySelector('img')) {
                    menuItem.querySelector('img').alt = window.t(item.labelKey);
                }
            }
        };
    }

    menuItem.addEventListener('mouseenter', () => menuItem.style.background = 'var(--neko-popup-hover, rgba(68, 183, 254, 0.1))');
    menuItem.addEventListener('mouseleave', () => menuItem.style.background = 'transparent');

    // 防抖标志：防止快速多次点击导致多开窗口
    let isOpening = false;

    menuItem.addEventListener('click', (e) => {
        e.stopPropagation();

        // 如果正在打开窗口，忽略后续点击
        if (isOpening) {
            return;
        }

        if (item.action === 'navigate') {
            let finalUrl = item.url || item.urlBase;
            let windowName = `neko_${item.id}`;
            let features;

            if ((item.id === 'vrm-manage' || item.id === 'live2d-manage') && item.urlBase) {
                const lanlanName = (window.lanlan_config && window.lanlan_config.lanlan_name) || '';
                finalUrl = `${item.urlBase}?lanlan_name=${encodeURIComponent(lanlanName)}`;
                window.location.href = finalUrl;
            } else if (item.id === 'voice-clone' && item.url) {
                const lanlanName = (window.lanlan_config && window.lanlan_config.lanlan_name) || '';
                const lanlanNameForKey = lanlanName || 'default';
                finalUrl = `${item.url}?lanlan_name=${encodeURIComponent(lanlanName)}`;
                windowName = `neko_voice_clone_${encodeURIComponent(lanlanNameForKey)}`;

                const width = 700;
                const height = 750;
                const left = Math.max(0, Math.floor((screen.width - width) / 2));
                const top = Math.max(0, Math.floor((screen.height - height) / 2));
                features = `width=${width},height=${height},left=${left},top=${top},menubar=no,toolbar=no,location=no,status=no,resizable=yes,scrollbars=yes`;

                // 设置防抖标志
                isOpening = true;
                window.openOrFocusWindow(finalUrl, windowName, features);
                // 500ms后重置标志，允许再次点击
                setTimeout(() => { isOpening = false; }, 500);
            } else {
                if (typeof finalUrl === 'string' && finalUrl.startsWith('/chara_manager')) {
                    windowName = 'neko_chara_manager';
                }

                // 设置防抖标志
                isOpening = true;
                window.openOrFocusWindow(finalUrl, windowName, features);
                // 500ms后重置标志，允许再次点击
                setTimeout(() => { isOpening = false; }, 500);
            }
        }
    });

    return menuItem;
};

// 创建可折叠的子菜单容器
VRMManager.prototype._createSubmenuContainer = function (submenuItems) {
    const container = document.createElement('div');
    Object.assign(container.style, {
        display: 'none',
        flexDirection: 'column',
        overflow: 'hidden',
        height: '0',
        opacity: '0',
        transition: 'height 0.2s ease, opacity 0.2s ease'
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
            if (container.style.opacity === '0') {
                container.style.display = 'none';
            }
        }, VRM_POPUP_ANIMATION_DURATION_MS);
    };

    return container;
};

// 辅助方法：关闭弹窗
function finalizePopupClosedState(popup) {
    if (!popup) return;
    popup.style.left = '';
    popup.style.right = '';
    popup.style.top = '';
    popup.style.transform = '';
    popup.style.opacity = '';
    popup.style.display = 'none';
    delete popup.dataset.opensLeft;
    popup._hideTimeoutId = null;
}

VRMManager.prototype.closePopupById = function (buttonId) {
    if (!buttonId) return false;
    const popup = document.getElementById(`vrm-popup-${buttonId}`);
    if (!popup || popup.style.display !== 'flex') return false;

    if (buttonId === 'agent') window.dispatchEvent(new CustomEvent('live2d-agent-popup-closed'));
    popup._showToken = (popup._showToken || 0) + 1;

    if (popup._hideTimeoutId) {
        clearTimeout(popup._hideTimeoutId);
        popup._hideTimeoutId = null;
    }

    popup.style.opacity = '0';
    const closeOpensLeft = popup.dataset.opensLeft === 'true';
    popup.style.transform = closeOpensLeft ? 'translateX(10px)' : 'translateX(-10px)';
    popup._hideTimeoutId = setTimeout(() => {
        finalizePopupClosedState(popup);
    }, VRM_POPUP_ANIMATION_DURATION_MS);

    // 更新按钮状态
    if (typeof this.setButtonActive === 'function') {
        this.setButtonActive(buttonId, false);
    }
    return true;
};

// 辅助方法：关闭其他弹窗
VRMManager.prototype.closeAllPopupsExcept = function (currentButtonId) {
    document.querySelectorAll('[id^="vrm-popup-"]').forEach(popup => {
        const popupId = popup.id.replace('vrm-popup-', '');
        if (popupId !== currentButtonId && popup.style.display === 'flex') this.closePopupById(popupId);
    });
};

// 辅助方法：关闭设置窗口
VRMManager.prototype.closeAllSettingsWindows = function (exceptUrl = null) {
    if (!this._openSettingsWindows) return;
    this._windowCheckTimers = this._windowCheckTimers || {};
    Object.keys(this._openSettingsWindows).forEach(url => {
        if (exceptUrl && url === exceptUrl) return;
        if (this._windowCheckTimers[url]) {
            clearTimeout(this._windowCheckTimers[url]);
            delete this._windowCheckTimers[url];
        }
        try { if (this._openSettingsWindows[url] && !this._openSettingsWindows[url].closed) this._openSettingsWindows[url].close(); } catch (_) { }
        delete this._openSettingsWindows[url];
    });
};

// 显示弹出框
VRMManager.prototype.showPopup = function (buttonId, popup) {
    const isVisible = popup.style.display === 'flex';
    const popupUi = window.AvatarPopupUI || null;
    if (typeof popup._showToken !== 'number') popup._showToken = 0;

    if (buttonId === 'settings') {
        const syncCheckbox = (checkbox, checked) => {
            if (!checkbox) return;
            checkbox.checked = checked;
            if (typeof checkbox.updateStyle === 'function') {
                checkbox.updateStyle();
            }
        };

        const mergeCheckbox = document.querySelector('#vrm-merge-messages');
        if (mergeCheckbox && typeof window.mergeMessagesEnabled !== 'undefined') {
            syncCheckbox(mergeCheckbox, window.mergeMessagesEnabled);
        }

        const focusCheckbox = document.querySelector('#vrm-focus-mode');
        if (focusCheckbox && typeof window.focusModeEnabled !== 'undefined') {
            syncCheckbox(focusCheckbox, !window.focusModeEnabled);
        }

        const proactiveChatCheckbox = popup.querySelector('#vrm-proactive-chat');
        if (proactiveChatCheckbox && typeof window.proactiveChatEnabled !== 'undefined') {
            syncCheckbox(proactiveChatCheckbox, window.proactiveChatEnabled);
        }

        const proactiveVisionCheckbox = popup.querySelector('#vrm-proactive-vision');
        if (proactiveVisionCheckbox && typeof window.proactiveVisionEnabled !== 'undefined') {
            syncCheckbox(proactiveVisionCheckbox, window.proactiveVisionEnabled);
        }

        const mouseTrackingCheckbox = popup.querySelector('#vrm-mouse-tracking-toggle');
        if (mouseTrackingCheckbox && typeof window.mouseTrackingEnabled !== 'undefined') {
            syncCheckbox(mouseTrackingCheckbox, window.mouseTrackingEnabled);
        }

        if (window.CHAT_MODE_CONFIG) {
            window.CHAT_MODE_CONFIG.forEach(config => {
                const checkbox = document.querySelector(`#vrm-proactive-${config.mode}-chat`);
                if (checkbox && typeof window[config.globalVarName] !== 'undefined') {
                    checkbox.checked = window[config.globalVarName];
                    if (typeof window.updateChatModeStyle === 'function') {
                        requestAnimationFrame(() => {
                            window.updateChatModeStyle(checkbox);
                        });
                    }
                }
            });
        }
    }

    if (buttonId === 'agent' && !isVisible) window.dispatchEvent(new CustomEvent('live2d-agent-popup-opening'));

    if (isVisible) {
        popup._showToken += 1;
        popup.style.opacity = '0';
        const closingOpensLeft = popup.dataset.opensLeft === 'true';
        popup.style.transform = closingOpensLeft ? 'translateX(10px)' : 'translateX(-10px)';
        const triggerIcon = document.querySelector(`.vrm-trigger-icon-${buttonId}`);
        if (triggerIcon) triggerIcon.style.transform = 'rotate(0deg)';
        if (buttonId === 'agent') window.dispatchEvent(new CustomEvent('live2d-agent-popup-closed'));

        // 更新按钮状态为关闭
        if (typeof this.setButtonActive === 'function') {
            this.setButtonActive(buttonId, false);
        }

        // 存储 timeout ID，以便在快速重新打开时能够清除
        const hideTimeoutId = setTimeout(() => {
            finalizePopupClosedState(popup);
        }, VRM_POPUP_ANIMATION_DURATION_MS);
        popup._hideTimeoutId = hideTimeoutId;
    } else {
        const showToken = popup._showToken + 1;
        popup._showToken = showToken;
        // 清除之前可能存在的隐藏 timeout，防止旧的 timeout 关闭新打开的 popup
        if (popup._hideTimeoutId) {
            clearTimeout(popup._hideTimeoutId);
            popup._hideTimeoutId = null;
        }

        this.closeAllPopupsExcept(buttonId);
        popup.style.display = 'flex'; popup.style.opacity = '0'; popup.style.visibility = 'visible';

        // 更新按钮状态为打开
        if (typeof this.setButtonActive === 'function') {
            this.setButtonActive(buttonId, true);
        }

        // 预加载图片
        const images = popup.querySelectorAll('img');
        Promise.all(Array.from(images).map(img => img.complete ? Promise.resolve() : new Promise(r => { img.onload = img.onerror = r; setTimeout(r, 100); }))).then(() => {
            if (popup._showToken !== showToken || popup.style.display !== 'flex') return;
            void popup.offsetHeight;
            requestAnimationFrame(() => {
                if (popup._showToken !== showToken || popup.style.display !== 'flex') return;
                if (popupUi && typeof popupUi.positionPopup === 'function') {
                    const pos = popupUi.positionPopup(popup, {
                        buttonId,
                        buttonPrefix: 'vrm-btn-',
                        triggerPrefix: 'vrm-trigger-icon-',
                        rightMargin: 20,
                        bottomMargin: 60,
                        topMargin: 8,
                        gap: 8
                    });
                    popup.dataset.opensLeft = String(!!(pos && pos.opensLeft));
                    popup.style.transform = pos && pos.opensLeft ? 'translateX(10px)' : 'translateX(-10px)';
                }
                if (popup._showToken !== showToken || popup.style.display !== 'flex') return;
                popup.style.visibility = 'visible';
                popup.style.opacity = '1';
                requestAnimationFrame(() => {
                    if (popup._showToken !== showToken || popup.style.display !== 'flex') return;
                    popup.style.transform = 'translateX(0)';
                });
            });
        });
    }
};
// VRM 专用的麦克风列表渲染函数
VRMManager.prototype.renderMicList = async function (popup) {
    if (!popup) return;
    popup.innerHTML = ''; // 清空现有内容

    const t = window.t || ((k, opt) => k); // 简单的 i18n 兼容

    try {
        // 获取权限
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        stream.getTracks().forEach(track => track.stop()); // 立即释放

        // 获取设备列表
        const devices = await navigator.mediaDevices.enumerateDevices();
        const audioInputs = devices.filter(device => device.kind === 'audioinput');

        if (audioInputs.length === 0) {
            const noDev = document.createElement('div');
            noDev.textContent = window.t ? window.t('microphone.noDevices') : '未检测到麦克风';
            Object.assign(noDev.style, { padding: '8px', fontSize: '13px', color: 'var(--neko-popup-text-sub, #666)' });
            popup.appendChild(noDev);
            return;
        }

        // 渲染设备列表
        const addOption = (label, deviceId) => {
            const btn = document.createElement('div');
            btn.textContent = label;
            // 简单样式
            Object.assign(btn.style, {
                padding: '8px 12px', cursor: 'pointer', fontSize: '13px',
                borderRadius: '6px', transition: 'background 0.2s',
                color: 'var(--neko-popup-text, #333)'
            });

            // 选中高亮逻辑（简单模拟）
            btn.addEventListener('mouseenter', () => btn.style.background = 'var(--neko-popup-hover, rgba(68, 183, 254, 0.1))');
            btn.addEventListener('mouseleave', () => btn.style.background = 'transparent');

            btn.addEventListener('click', async (e) => {
                e.stopPropagation();
                if (deviceId) {
                    try {
                        const response = await fetch('/api/characters/set_microphone', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ microphone_id: deviceId })
                        });

                        if (!response.ok) {
                            // 解析错误信息
                            let errorMessage = `HTTP ${response.status}: ${response.statusText}`;
                            try {
                                const errorData = await response.json();
                                errorMessage = errorData.error || errorData.message || errorMessage;
                            } catch {
                                try {
                                    const errorText = await response.text();
                                    if (errorText) errorMessage = errorText;
                                } catch { }
                            }
                            if (window.showStatusToast) {
                                const message = window.t ? window.t('microphone.switchFailed', { error: errorMessage }) : `切换麦克风失败: ${errorMessage}`;
                                window.showStatusToast(message, 3000);
                            } else {
                                console.error('[VRM UI] 切换麦克风失败:', errorMessage);
                            }
                            return;
                        }
                        if (window.showStatusToast) {
                            const message = window.t ? window.t('microphone.switched') : '已切换麦克风 (下一次录音生效)';
                            window.showStatusToast(message, 2000);
                        }
                    } catch (e) {
                        console.error('[VRM UI] 切换麦克风时发生网络错误:', e);
                        if (window.showStatusToast) {
                            const message = window.t ? window.t('microphone.networkError') : '切换麦克风失败：网络错误';
                            window.showStatusToast(message, 3000);
                        }
                    }
                }
            });
            popup.appendChild(btn);
        };

        // 添加列表
        audioInputs.forEach((device, index) => {
            const deviceLabel = device.label || (window.t ? window.t('microphone.deviceLabel', { index: index + 1 }) : `麦克风 ${index + 1}`);
            addOption(deviceLabel, device.deviceId);
        });

    } catch (e) {
        console.error('获取麦克风失败', e);
        const errDiv = document.createElement('div');
        errDiv.textContent = window.t ? window.t('microphone.accessFailed') : '无法访问麦克风';
        popup.appendChild(errDiv);
    }
};

// 创建网格容器的辅助函数（提取到外部避免重复创建）
function createScreenSourceGridContainer() {
    const grid = document.createElement('div');
    Object.assign(grid.style, {
        display: 'grid',
        gridTemplateColumns: 'repeat(4, 1fr)',
        gap: '6px',
        padding: '4px',
        width: '100%',
        boxSizing: 'border-box'
    });
    return grid;
}

// 创建屏幕源选项元素的辅助函数（提取到外部避免重复创建）
function createScreenSourceOption(source) {
    const option = document.createElement('div');
    option.className = 'screen-source-option';
    option.dataset.sourceId = source.id;
    Object.assign(option.style, {
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        padding: '4px',
        cursor: 'pointer',
        borderRadius: '6px',
        border: '2px solid transparent',
        transition: 'all 0.2s ease',
        background: 'transparent',
        boxSizing: 'border-box',
        minWidth: '0'
    });

    // 缩略图
    if (source.thumbnail) {
        const thumb = document.createElement('img');
        let thumbnailDataUrl = '';
        try {
            if (typeof source.thumbnail === 'string') {
                thumbnailDataUrl = source.thumbnail;
            } else if (source.thumbnail?.toDataURL) {
                thumbnailDataUrl = source.thumbnail.toDataURL();
            }
            if (!thumbnailDataUrl?.trim()) {
                throw new Error('缩略图为空');
            }
        } catch (e) {
            console.warn('[屏幕源] 缩略图转换失败:', e);
            thumbnailDataUrl = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==';
        }
        thumb.src = thumbnailDataUrl;
        thumb.onerror = () => {
            thumb.src = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==';
        };
        Object.assign(thumb.style, {
            width: '100%',
            maxWidth: '90px',
            height: '56px',
            objectFit: 'cover',
            borderRadius: '4px',
            border: '1px solid var(--neko-popup-separator, rgba(0, 0, 0, 0.1))',
            marginBottom: '4px'
        });
        option.appendChild(thumb);
    } else {
        const iconPlaceholder = document.createElement('div');
        iconPlaceholder.textContent = source.id.startsWith('screen:') ? '🖥️' : '🪟';
        Object.assign(iconPlaceholder.style, {
            width: '100%',
            maxWidth: '90px',
            height: '56px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: '24px',
            background: 'var(--neko-screen-placeholder-bg, #f5f5f5)',
            borderRadius: '4px',
            marginBottom: '4px'
        });
        option.appendChild(iconPlaceholder);
    }

    // 名称
    const label = document.createElement('span');
    label.textContent = source.name;
    Object.assign(label.style, {
        fontSize: '10px',
        color: 'var(--neko-popup-text, #333)',
        width: '100%',
        textAlign: 'center',
        lineHeight: '1.3',
        wordBreak: 'break-word',
        display: '-webkit-box',
        WebkitLineClamp: '2',
        WebkitBoxOrient: 'vertical',
        overflow: 'hidden',
        height: '26px'
    });
    option.appendChild(label);

    // 悬停效果
    option.addEventListener('mouseenter', () => {
        option.style.background = 'var(--neko-popup-hover, rgba(68, 183, 254, 0.1))';
    });
    option.addEventListener('mouseleave', () => {
        option.style.background = 'transparent';
    });

    option.addEventListener('click', async (e) => {
        e.stopPropagation();
        // 调用全局的屏幕源选择函数（app.js中定义）
        if (window.selectScreenSource) {
            await window.selectScreenSource(source.id, source.name);
        } else {
            console.warn('[VRM] window.selectScreenSource 未定义');
        }
    });

    return option;
}

// VRM 专用的屏幕源列表渲染函数
VRMManager.prototype.renderScreenSourceList = async function (popup) {
    if (!popup) return;
    popup.innerHTML = ''; // 清空现有内容

    const t = window.t || ((k, opt) => k); // 简单的 i18n 兼容

    // 检查是否在Electron环境
    if (!window.electronDesktopCapturer || !window.electronDesktopCapturer.getSources) {
        const notAvailableItem = document.createElement('div');
        notAvailableItem.textContent = t('app.screenSource.notAvailable') || '仅在桌面版可用';
        Object.assign(notAvailableItem.style, { padding: '12px', fontSize: '13px', color: 'var(--neko-popup-text-sub, #666)', textAlign: 'center' });
        popup.appendChild(notAvailableItem);
        return;
    }

    try {
        // 显示加载中
        const loadingItem = document.createElement('div');
        loadingItem.textContent = t('app.screenSource.loading') || '加载中...';
        Object.assign(loadingItem.style, { padding: '12px', fontSize: '13px', color: 'var(--neko-popup-text-sub, #666)', textAlign: 'center' });
        popup.appendChild(loadingItem);

        // 获取屏幕源
        const sources = await window.electronDesktopCapturer.getSources({
            types: ['window', 'screen'],
            thumbnailSize: { width: 160, height: 100 }
        });

        popup.innerHTML = '';

        if (!sources || sources.length === 0) {
            const noSourcesItem = document.createElement('div');
            noSourcesItem.textContent = t('app.screenSource.noSources') || '没有可用的屏幕源';
            Object.assign(noSourcesItem.style, { padding: '12px', fontSize: '13px', color: 'var(--neko-popup-text-sub, #666)', textAlign: 'center' });
            popup.appendChild(noSourcesItem);
            return;
        }

        // 分组：屏幕和窗口
        const screens = sources.filter(s => s.id.startsWith('screen:'));
        const windows = sources.filter(s => s.id.startsWith('window:'));

        // 渲染屏幕列表
        if (screens.length > 0) {
            const screenTitle = document.createElement('div');
            screenTitle.textContent = t('app.screenSource.screens') || '屏幕';
            Object.assign(screenTitle.style, {
                padding: '6px 8px',
                fontSize: '11px',
                fontWeight: '600',
                color: 'var(--neko-popup-text-sub, #666)',
                borderBottom: '1px solid var(--neko-popup-separator, rgba(0, 0, 0, 0.1))',
                marginBottom: '4px'
            });
            popup.appendChild(screenTitle);

            const screenGrid = createScreenSourceGridContainer();
            screens.forEach(source => {
                screenGrid.appendChild(createScreenSourceOption(source));
            });
            popup.appendChild(screenGrid);
        }

        // 渲染窗口列表
        if (windows.length > 0) {
            const windowTitle = document.createElement('div');
            windowTitle.textContent = t('app.screenSource.windows') || '窗口';
            Object.assign(windowTitle.style, {
                padding: '6px 8px',
                fontSize: '11px',
                fontWeight: '600',
                color: 'var(--neko-popup-text-sub, #666)',
                borderBottom: '1px solid var(--neko-popup-separator, rgba(0, 0, 0, 0.1))',
                marginTop: windows.length > 0 && screens.length > 0 ? '8px' : '0',
                marginBottom: '4px'
            });
            popup.appendChild(windowTitle);

            const windowGrid = createScreenSourceGridContainer();
            windows.forEach(source => {
                windowGrid.appendChild(createScreenSourceOption(source));
            });
            popup.appendChild(windowGrid);
        }

    } catch (e) {
        console.error('[VRM] 获取屏幕源失败', e);
        popup.innerHTML = '';
        const errDiv = document.createElement('div');
        errDiv.textContent = window.t ? window.t('app.screenSource.loadFailed') : '获取屏幕源失败';
        Object.assign(errDiv.style, { padding: '12px', fontSize: '13px', color: 'var(--neko-popup-error, #dc3545)', textAlign: 'center' });
        popup.appendChild(errDiv);
    }
};
