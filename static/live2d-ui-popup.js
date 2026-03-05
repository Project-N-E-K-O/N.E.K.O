/**
 * Live2D UI Popup - 弹出框组件
 * 包含弹出框创建、设置菜单、开关项组件
 */

// 动画时长常量（与 CSS transition duration 保持一致）
const POPUP_ANIMATION_DURATION_MS = 200;

// 创建弹出框
Live2DManager.prototype.createPopup = function (buttonId) {
    const popup = document.createElement('div');
    popup.id = `live2d-popup-${buttonId}`;
    popup.className = 'live2d-popup';

    Object.assign(popup.style, {
        position: 'absolute',
        left: '100%',
        top: '0',
        marginLeft: '8px',
        zIndex: '100000',  // 确保弹出菜单置顶，不被任何元素遮挡
        background: 'var(--neko-popup-bg, rgba(255,255,255,0.65))',  // Fluent Acrylic（支持暗色模式）
        backdropFilter: 'saturate(180%) blur(20px)',  // Fluent 标准模糊
        border: 'var(--neko-popup-border, 1px solid rgba(255,255,255,0.18))',  // 微妙高光边框（支持暗色模式）
        borderRadius: '8px',  // Fluent 标准圆角
        padding: '8px',
        boxShadow: 'var(--neko-popup-shadow, 0 2px 4px rgba(0,0,0,0.04), 0 8px 16px rgba(0,0,0,0.08), 0 16px 32px rgba(0,0,0,0.04))',  // Fluent 多层阴影（支持暗色模式）
        display: 'none',
        flexDirection: 'column',
        gap: '6px',
        minWidth: '180px',
        maxHeight: '200px',
        overflowY: 'auto',
        pointerEvents: 'auto',
        opacity: '0',
        transform: 'translateX(-10px)',
        transition: 'opacity 0.2s cubic-bezier(0.1, 0.9, 0.2, 1), transform 0.2s cubic-bezier(0.1, 0.9, 0.2, 1)'  // Fluent 动画曲线
    });

    // 阻止弹出菜单上的指针事件传播到window，避免触发live2d拖拽
    const stopEventPropagation = (e) => {
        e.stopPropagation();
    };
    popup.addEventListener('pointerdown', stopEventPropagation, true);
    popup.addEventListener('pointermove', stopEventPropagation, true);
    popup.addEventListener('pointerup', stopEventPropagation, true);
    popup.addEventListener('mousedown', stopEventPropagation, true);
    popup.addEventListener('mousemove', stopEventPropagation, true);
    popup.addEventListener('mouseup', stopEventPropagation, true);
    popup.addEventListener('touchstart', stopEventPropagation, true);
    popup.addEventListener('touchmove', stopEventPropagation, true);
    popup.addEventListener('touchend', stopEventPropagation, true);

    // 根据不同按钮创建不同的弹出内容
    if (buttonId === 'mic') {
        // 麦克风选择列表（将从页面中获取）
        popup.id = 'live2d-popup-mic';
        popup.setAttribute('data-legacy-id', 'live2d-mic-popup');
        // 双栏布局：加宽弹出框，横向排列
        popup.style.minWidth = '400px';
        popup.style.maxHeight = '320px';
        popup.style.flexDirection = 'row';
        popup.style.gap = '0';
        popup.style.overflowY = 'hidden';  // 整体不滚动，右栏单独滚动
    } else if (buttonId === 'screen') {
        // 屏幕/窗口源选择列表（将从Electron获取）
        popup.id = 'live2d-popup-screen';
        // 为屏幕源弹出框设置尺寸，允许纵向滚动但禁止横向滚动
        popup.style.width = '420px';
        popup.style.maxHeight = '400px';
        popup.style.overflowX = 'hidden';
        popup.style.overflowY = 'auto';
    } else if (buttonId === 'agent') {
        // Agent工具开关组
        window.AgentHUD._createAgentPopupContent.call(this, popup);
    } else if (buttonId === 'settings') {
        // 设置菜单
        this._createSettingsPopupContent(popup);
    }

    return popup;
};

// 创建设置弹出框内容
Live2DManager.prototype._createSettingsPopupContent = function (popup) {
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
        { id: 'proactive-vision', label: window.t ? window.t('settings.toggles.proactiveVision') : '自主视觉', labelKey: 'settings.toggles.proactiveVision', storageKey: 'proactiveVisionEnabled', hasInterval: true, intervalKey: 'proactiveVisionInterval', defaultInterval: 15 },
    ];

    settingsToggles.forEach(toggle => {
        const toggleItem = this._createSettingsToggleItem(toggle, popup);
        popup.appendChild(toggleItem);

        if (toggle.hasInterval) {
            const sidePanel = this._createIntervalControl(toggle);
            sidePanel._anchorElement = toggleItem;
            sidePanel._popupElement = popup;

            if (toggle.id === 'proactive-chat') {
                const AUTH_I18N_KEY = 'settings.menu.mediaCredentials';
                const AUTH_FALLBACK_LABEL = '配置媒体凭证';

                const authPageLink = this._createSettingsLinkItem({
                    id: 'auth-page',
                    label: window.t ? window.t(AUTH_I18N_KEY) : AUTH_FALLBACK_LABEL,
                    labelKey: AUTH_I18N_KEY,
                    icon: '/static/icons/cookies_icon.png',
                    action: 'navigate',
                    url: '/api/auth/page'
                });
                Object.assign(authPageLink.style, {
                    display: 'flex',
                    height: 'auto',
                    opacity: '1',
                    padding: '4px 8px',
                    overflow: 'visible',
                    marginLeft: '-6px'
                });
                authPageLink._expand = () => {};
                authPageLink._collapse = () => {};
                sidePanel.appendChild(authPageLink);
            }

            this._attachSidePanelHover(toggleItem, sidePanel);
        }
    });

    // 手机仅保留开关；桌面端追加导航菜单
    if (!isMobileWidth()) {
        const separator = document.createElement('div');
        Object.assign(separator.style, {
            height: '1px',
            background: 'var(--neko-popup-separator, rgba(0,0,0,0.1))',
            margin: '4px 0'
        });
        popup.appendChild(separator);

        this._createSettingsMenuItems(popup);
    }
};

// 创建设置菜单按钮（非开关型，带右箭头指示器）
Live2DManager.prototype._createSettingsMenuButton = function (config) {
    const btn = document.createElement('div');
    Object.assign(btn.style, {
        display: 'flex',
        alignItems: 'center',
        gap: '8px',
        padding: '8px 12px',
        cursor: 'pointer',
        borderRadius: '6px',
        transition: 'background 0.2s ease',
        fontSize: '13px',
        whiteSpace: 'nowrap',
        color: 'var(--neko-popup-text, #333)',
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

    btn.addEventListener('mouseenter', () => {
        btn.style.background = 'var(--neko-popup-hover, rgba(68,183,254,0.1))';
    });
    btn.addEventListener('mouseleave', () => {
        btn.style.background = 'transparent';
    });

    return btn;
};

// 创建对话设置侧边弹出面板（合并消息 + 允许打断）
Live2DManager.prototype._createChatSettingsSidePanel = function (popup) {
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
        const toggleItem = this._createSettingsToggleItem(toggle, popup);
        container.appendChild(toggleItem);
    });

    document.body.appendChild(container);
    return container;
};

// 创建动画设置侧边弹出面板（画质 + 帧率滑动条）
Live2DManager.prototype._createAnimationSettingsSidePanel = function () {
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
    qualitySlider.addEventListener('change', () => {
        const idx = parseInt(qualitySlider.value, 10);
        window.renderQuality = qualityNames[idx];
        if (typeof window.saveNEKOSettings === 'function') window.saveNEKOSettings();
        window.dispatchEvent(new CustomEvent('neko-render-quality-changed', { detail: { quality: qualityNames[idx] } }));
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

    const mouseTrackingLabel = document.createElement('span');
    mouseTrackingLabel.textContent = window.t ? window.t('settings.toggles.mouseTracking') : '跟踪鼠标';
    mouseTrackingLabel.setAttribute('data-i18n', 'settings.toggles.mouseTracking');
    Object.assign(mouseTrackingLabel.style, { fontSize: '12px', color: 'var(--neko-popup-text, #333)', flex: '1' });

    const mouseTrackingCheckbox = document.createElement('input');
    mouseTrackingCheckbox.type = 'checkbox';
    mouseTrackingCheckbox.id = 'live2d-mouse-tracking-toggle';
    mouseTrackingCheckbox.checked = window.mouseTrackingEnabled !== false;
    Object.assign(mouseTrackingCheckbox.style, { display: 'none' });

    const { indicator: mouseTrackingIndicator, updateStyle: updateMouseTrackingStyle } = this._createCheckIndicator();
    updateMouseTrackingStyle(mouseTrackingCheckbox.checked);

    const updateMouseTrackingRowStyle = () => {
        updateMouseTrackingStyle(mouseTrackingCheckbox.checked);
        mouseTrackingRow.style.background = mouseTrackingCheckbox.checked
            ? 'var(--neko-popup-selected-bg, rgba(68,183,254,0.1))'
            : 'transparent';
    };
    updateMouseTrackingRowStyle();

    const handleMouseTrackingToggle = () => {
        mouseTrackingCheckbox.checked = !mouseTrackingCheckbox.checked;
        window.mouseTrackingEnabled = mouseTrackingCheckbox.checked;
        updateMouseTrackingRowStyle();

        if (typeof window.saveNEKOSettings === 'function') window.saveNEKOSettings();

        if (window.live2dManager && typeof window.live2dManager.setMouseTrackingEnabled === 'function') {
            window.live2dManager.setMouseTrackingEnabled(mouseTrackingCheckbox.checked);
        }
        console.log(`[Live2D] 跟踪鼠标切换: enabled=${mouseTrackingCheckbox.checked}, window.mouseTrackingEnabled=${window.mouseTrackingEnabled}`);
    };

    mouseTrackingRow.addEventListener('click', (e) => {
        e.stopPropagation();
        handleMouseTrackingToggle();
    });
    mouseTrackingIndicator.addEventListener('click', (e) => {
        e.stopPropagation();
        handleMouseTrackingToggle();
    });
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
Live2DManager.prototype._createSidePanelContainer = function () {
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
        boxShadow: 'var(--neko-popup-shadow, 0 2px 4px rgba(0,0,0,0.04), 0 8px 16px rgba(0,0,0,0.08))',
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
        }, POPUP_ANIMATION_DURATION_MS);
    };

    return container;
};

// 附加侧边面板悬停逻辑（公共方法，供按钮和开关复用）
Live2DManager.prototype._attachSidePanelHover = function (anchorEl, sidePanel) {
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
        self._isMouseOverButtons = true;
        if (self._hideButtonsTimer) {
            clearTimeout(self._hideButtonsTimer);
            self._hideButtonsTimer = null;
        }
    });
    sidePanel.addEventListener('mouseleave', (e) => {
        collapsePanel(e);
        self._isMouseOverButtons = false;
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
Live2DManager.prototype._createIntervalControl = function (toggle) {
    const container = document.createElement('div');
    container.className = `live2d-interval-control-${toggle.id}`;
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
        boxShadow: 'var(--neko-popup-shadow, 0 2px 4px rgba(0,0,0,0.04), 0 8px 16px rgba(0,0,0,0.08))',
        transition: 'opacity 0.2s cubic-bezier(0.1, 0.9, 0.2, 1), transform 0.2s cubic-bezier(0.1, 0.9, 0.2, 1)',
        transform: 'translateX(-6px)',
        pointerEvents: 'auto',
        flexWrap: 'nowrap',
        width: 'max-content',
        maxWidth: 'min(320px, calc(100vw - 24px))'
    });

    // 阻止指针事件传播到底层（避免触发live2d拖拽）
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
    slider.id = `live2d-${toggle.id}-interval`;
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
            const chatModesContainer = window.createChatModeToggles('live2d');
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
        }, POPUP_ANIMATION_DURATION_MS);
    };

    // 附加到 body（不在 popup 流中，避免被 popup 的 overflow 裁剪）
    document.body.appendChild(container);

    return container;
};

// 创建可折叠的设置链接项（用于在开关展开时附带显示的导航入口，如"配置媒体凭证"）
Live2DManager.prototype._createSettingsLinkItem = function (item, popup) {
    const linkItem = document.createElement('div');
    linkItem.id = `live2d-link-${item.id}`;
    Object.assign(linkItem.style, {
        display: 'none',   // 初始隐藏，由 _expand/_collapse 控制
        alignItems: 'center',
        gap: '6px',
        padding: '0 12px 0 44px',
        fontSize: '12px',
        color: 'var(--neko-popup-text, #333)',
        height: '0',
        overflow: 'hidden',
        opacity: '0',
        cursor: 'pointer',
        borderRadius: '6px',
        transition: 'height 0.2s ease, opacity 0.2s ease, padding 0.2s ease, background 0.2s ease'
    });

    // 图标（可选）
    if (item.icon) {
        const iconImg = document.createElement('img');
        iconImg.src = item.icon;
        iconImg.alt = item.label || '';
        Object.assign(iconImg.style, {
            width: '16px',
            height: '16px',
            objectFit: 'contain',
            flexShrink: '0'
        });
        linkItem.appendChild(iconImg);
    }

    // 文字标签
    const labelSpan = document.createElement('span');
    labelSpan.textContent = item.label || '';
    if (item.labelKey) {
        labelSpan.setAttribute('data-i18n', item.labelKey);
    }
    Object.assign(labelSpan.style, {
        flexShrink: '0',
        fontSize: '11px',
        userSelect: 'none'
    });
    linkItem.appendChild(labelSpan);

    // 更新标签文本（i18n 动态刷新用）
    if (item.labelKey) {
        linkItem._updateLabelText = () => {
            if (window.t) {
                labelSpan.textContent = window.t(item.labelKey);
                if (item.icon && linkItem.querySelector('img')) {
                    linkItem.querySelector('img').alt = window.t(item.labelKey);
                }
            }
        };
    }

    // 悬停效果
    linkItem.addEventListener('mouseenter', () => {
        linkItem.style.background = 'var(--neko-popup-hover, rgba(68,183,254,0.1))';
    });
    linkItem.addEventListener('mouseleave', () => {
        linkItem.style.background = 'transparent';
    });

    // 点击导航
    let isOpening = false;
    linkItem.addEventListener('click', (e) => {
        e.stopPropagation();
        if (isOpening) return;
        if (item.action === 'navigate' && item.url) {
            isOpening = true;
            if (typeof window.openOrFocusWindow === 'function') {
                window.openOrFocusWindow(item.url, `neko_${item.id}`);
            } else {
                window.open(item.url, `neko_${item.id}`);
            }
            setTimeout(() => { isOpening = false; }, 500);
        }
    });

    // 展开/收缩方法（与 _createIntervalControl 保持相同约定）
    linkItem._expand = () => {
        linkItem.style.display = 'flex';
        if (linkItem._expandTimeout) {
            clearTimeout(linkItem._expandTimeout);
            linkItem._expandTimeout = null;
        }
        if (linkItem._collapseTimeout) {
            clearTimeout(linkItem._collapseTimeout);
            linkItem._collapseTimeout = null;
        }
        requestAnimationFrame(() => {
            const targetHeight = linkItem.scrollHeight || 28;
            linkItem.style.height = targetHeight + 'px';
            linkItem.style.opacity = '1';
            linkItem.style.padding = '4px 12px 4px 44px';
            linkItem._expandTimeout = setTimeout(() => {
                if (linkItem.style.opacity === '1') {
                    linkItem.style.height = 'auto';
                }
                linkItem._expandTimeout = null;
            }, POPUP_ANIMATION_DURATION_MS);
        });
    };

    linkItem._collapse = () => {
        if (linkItem._expandTimeout) {
            clearTimeout(linkItem._expandTimeout);
            linkItem._expandTimeout = null;
        }
        if (linkItem._collapseTimeout) {
            clearTimeout(linkItem._collapseTimeout);
            linkItem._collapseTimeout = null;
        }
        linkItem.style.height = linkItem.scrollHeight + 'px';
        requestAnimationFrame(() => {
            linkItem.style.height = '0';
            linkItem.style.opacity = '0';
            linkItem.style.padding = '0 12px 0 44px';
            linkItem._collapseTimeout = setTimeout(() => {
                if (linkItem.style.opacity === '0') {
                    linkItem.style.display = 'none';
                }
                linkItem._collapseTimeout = null;
            }, POPUP_ANIMATION_DURATION_MS);
        });
    };

    return linkItem;
};

// 创建圆形指示器和对勾的辅助方法（供 _createToggleItem 和 _createSettingsToggleItem 共用）
Live2DManager.prototype._createCheckIndicator = function () {
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

    /**
     * 根据选中状态更新指示器样式
     * @param {boolean} checked - 是否选中
     */
    const updateStyle = (checked) => {
        if (checked) {
            indicator.style.backgroundColor = 'var(--neko-popup-active, #44b7fe)';
            indicator.style.borderColor = 'var(--neko-popup-active, #44b7fe)';
            checkmark.style.opacity = '1';
        } else {
            indicator.style.backgroundColor = 'transparent';
            indicator.style.borderColor = 'var(--neko-popup-indicator-border, #ccc)';
            checkmark.style.opacity = '0';
        }
    };

    return { indicator, updateStyle };
};

// 创建Agent开关项
Live2DManager.prototype._createToggleItem = function (toggle, popup) {
    const toggleItem = document.createElement('div');
    Object.assign(toggleItem.style, {
        display: 'flex',
        alignItems: 'center',
        gap: '8px',
        padding: '6px 8px',
        cursor: 'pointer',
        borderRadius: '6px',
        transition: 'background 0.2s ease, opacity 0.2s ease',  // 添加opacity过渡
        fontSize: '13px',
        whiteSpace: 'nowrap',
        opacity: toggle.initialDisabled ? '0.5' : '1'  // 【状态机】初始禁用时显示半透明
    });

    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.id = `live2d-${toggle.id}`;
    // 隐藏原生 checkbox
    Object.assign(checkbox.style, {
        display: 'none'
    });

    // 【状态机严格控制】默认禁用所有按钮，使用配置的title
    if (toggle.initialDisabled) {
        checkbox.disabled = true;
        checkbox.title = toggle.initialTitle || (window.t ? window.t('settings.toggles.checking') : '查询中...');
        toggleItem.style.cursor = 'default';  // 禁用时显示默认光标
    }

    // 使用辅助方法创建圆形指示器和对勾
    const { indicator, updateStyle: updateIndicatorStyle } = this._createCheckIndicator();

    const label = document.createElement('label');
    label.innerText = toggle.label;
    if (toggle.labelKey) {
        label.setAttribute('data-i18n', toggle.labelKey);
    }
    label.htmlFor = `live2d-${toggle.id}`;
    label.style.cursor = 'pointer';
    label.style.userSelect = 'none';
    label.style.fontSize = '13px';
    label.style.color = 'var(--neko-popup-text, #333)';

    // 更新标签文本的函数
    const updateLabelText = () => {
        if (toggle.labelKey && window.t) {
            label.innerText = window.t(toggle.labelKey);
        }
    };

    // 同步 title 属性
    const updateTitle = () => {
        const title = checkbox.title || '';
        label.title = toggleItem.title = title;
    };

    // 根据 checkbox 状态更新指示器颜色和对勾显示
    const updateStyle = () => updateIndicatorStyle(checkbox.checked);

    // 更新禁用状态的视觉反馈
    const updateDisabledStyle = () => {
        const disabled = checkbox.disabled;
        const cursor = disabled ? 'default' : 'pointer';
        [toggleItem, label, indicator].forEach(el => el.style.cursor = cursor);
        toggleItem.style.opacity = disabled ? '0.5' : '1';
    };

    // 监听 checkbox 的 disabled 和 title 属性变化
    const disabledObserver = new MutationObserver(() => {
        updateDisabledStyle();
        if (checkbox.hasAttribute('title')) updateTitle();
    });
    disabledObserver.observe(checkbox, { attributes: true, attributeFilter: ['disabled', 'title'] });

    // 监听 checkbox 状态变化
    checkbox.addEventListener('change', updateStyle);

    // 初始化样式
    updateStyle();
    updateDisabledStyle();
    updateTitle();

    toggleItem.appendChild(checkbox);
    toggleItem.appendChild(indicator);
    toggleItem.appendChild(label);

    // 存储更新函数和同步UI函数到checkbox上，供外部调用
    checkbox._updateStyle = updateStyle;
    if (toggle.labelKey) {
        toggleItem._updateLabelText = updateLabelText;
    }

    // 鼠标悬停效果
    toggleItem.addEventListener('mouseenter', () => {
        if (checkbox.disabled && checkbox.title?.includes('不可用')) {
            const statusEl = document.getElementById('live2d-agent-status');
            if (statusEl) statusEl.textContent = checkbox.title;
        } else if (!checkbox.disabled) {
            toggleItem.style.background = 'var(--neko-popup-hover, rgba(68,183,254,0.1))';
        }
    });
    toggleItem.addEventListener('mouseleave', () => {
        toggleItem.style.background = 'transparent';
    });

    // 点击切换（点击除复选框本身外的任何区域）
    const handleToggle = (event) => {
        if (checkbox.disabled) return;

        // 防止重复点击：使用更长的防抖时间来适应异步操作
        if (checkbox._processing) {
            // 如果距离上次操作时间较短，忽略本次点击
            const elapsed = Date.now() - (checkbox._processingTime || 0);
            if (elapsed < 500) {  // 500ms 防抖，防止频繁点击
                console.log('[Live2D] Agent开关正在处理中，忽略重复点击:', toggle.id, '已过', elapsed, 'ms');
                event?.preventDefault();
                event?.stopPropagation();
                return;
            }
            // 超过500ms但仍在processing，可能是上次操作卡住了，允许新操作
            console.log('[Live2D] Agent开关上次操作可能超时，允许新操作:', toggle.id);
        }

        // 立即设置处理中标志
        checkbox._processing = true;
        checkbox._processingEvent = event;
        checkbox._processingTime = Date.now();

        const newChecked = !checkbox.checked;
        checkbox.checked = newChecked;
        checkbox.dispatchEvent(new Event('change', { bubbles: true }));
        updateStyle();

        // 备用清除机制（增加超时时间以适应网络延迟）
        setTimeout(() => {
            if (checkbox._processing && Date.now() - checkbox._processingTime > 5000) {
                console.log('[Live2D] Agent开关备用清除机制触发:', toggle.id);
                checkbox._processing = false;
                checkbox._processingEvent = null;
                checkbox._processingTime = null;
            }
        }, 5500);

        // 防止默认行为和事件冒泡
        event?.preventDefault();
        event?.stopPropagation();
    };

    // 点击整个项目区域（除了复选框和指示器）
    toggleItem.addEventListener('click', (e) => {
        if (e.target !== checkbox && e.target !== indicator && e.target !== label) {
            handleToggle(e);
        }
    });

    // 点击指示器也可以切换
    indicator.addEventListener('click', (e) => {
        e.stopPropagation();
        handleToggle(e);
    });

    // 防止标签点击的默认行为
    label.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        handleToggle(e);
    });

    return toggleItem;
};

// 创建设置开关项
Live2DManager.prototype._createSettingsToggleItem = function (toggle, popup) {
    const toggleItem = document.createElement('div');
    toggleItem.id = `live2d-toggle-${toggle.id}`;  // 为整个切换项容器添加 ID
    Object.assign(toggleItem.style, {
        display: 'flex',
        alignItems: 'center',
        gap: '8px',
        padding: '8px 12px',  // 统一padding，与下方菜单项一致
        cursor: 'pointer',
        borderRadius: '6px',
        transition: 'background 0.2s ease',
        fontSize: '13px',
        whiteSpace: 'nowrap'
    });

    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.id = `live2d-${toggle.id}`;
    // 隐藏原生 checkbox
    Object.assign(checkbox.style, {
        display: 'none'
    });

    // 从 window 获取当前状态（如果 app.js 已经初始化）
    if (toggle.id === 'merge-messages') {
        if (typeof window.mergeMessagesEnabled !== 'undefined') {
            checkbox.checked = window.mergeMessagesEnabled;
        }
    } else if (toggle.id === 'focus-mode' && typeof window.focusModeEnabled !== 'undefined') {
        // inverted: 允许打断 = !focusModeEnabled（focusModeEnabled为true表示关闭打断）
        checkbox.checked = toggle.inverted ? !window.focusModeEnabled : window.focusModeEnabled;
    } else if (toggle.id === 'proactive-chat' && typeof window.proactiveChatEnabled !== 'undefined') {
        checkbox.checked = window.proactiveChatEnabled;
    } else if (toggle.id === 'proactive-vision' && typeof window.proactiveVisionEnabled !== 'undefined') {
        checkbox.checked = window.proactiveVisionEnabled;
    }

    // 使用辅助方法创建圆形指示器和对勾
    const { indicator, updateStyle: updateIndicatorStyle } = this._createCheckIndicator();

    const label = document.createElement('label');
    label.innerText = toggle.label;
    label.htmlFor = `live2d-${toggle.id}`;
    // 添加 data-i18n 属性以便自动更新
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
    label.style.height = '20px';  // 与指示器高度一致，确保垂直居中

    // 根据 checkbox 状态更新指示器颜色
    const updateStyle = () => {
        updateIndicatorStyle(checkbox.checked);
        toggleItem.style.background = checkbox.checked
            ? 'var(--neko-popup-selected-bg, rgba(68,183,254,0.1))'
            : 'transparent';
    };

    // 初始化样式（根据当前状态）
    updateStyle();

    toggleItem.appendChild(checkbox);
    toggleItem.appendChild(indicator);
    toggleItem.appendChild(label);

    toggleItem.addEventListener('mouseenter', () => {
        // 悬停效果
        if (checkbox.checked) {
            toggleItem.style.background = 'var(--neko-popup-selected-hover, rgba(68,183,254,0.15))';
        } else {
            toggleItem.style.background = 'var(--neko-popup-hover-subtle, rgba(68,183,254,0.08))';
        }
    });
    toggleItem.addEventListener('mouseleave', () => {
        // 恢复选中状态的背景色
        updateStyle();
    });

    // 统一的切换处理函数
    const handleToggleChange = (isChecked) => {
        // 更新样式
        updateStyle();

        // 同步到 app.js 中的对应开关（这样会触发 app.js 的完整逻辑）
        if (toggle.id === 'merge-messages') {
            window.mergeMessagesEnabled = isChecked;

            // 保存到localStorage
            if (typeof window.saveNEKOSettings === 'function') {
                window.saveNEKOSettings();
            }
        } else if (toggle.id === 'focus-mode') {
            // inverted: "允许打断"的值需要取反后赋给 focusModeEnabled
            // 勾选"允许打断" = focusModeEnabled为false（允许打断）
            // 取消勾选"允许打断" = focusModeEnabled为true（focus模式，AI说话时静音麦克风）
            const actualValue = toggle.inverted ? !isChecked : isChecked;
            window.focusModeEnabled = actualValue;

            // 保存到localStorage
            if (typeof window.saveNEKOSettings === 'function') {
                window.saveNEKOSettings();
            }
        } else if (toggle.id === 'proactive-chat') {
            window.proactiveChatEnabled = isChecked;

            // 保存到localStorage
            if (typeof window.saveNEKOSettings === 'function') {
                window.saveNEKOSettings();
            }

            if (isChecked && typeof window.resetProactiveChatBackoff === 'function') {
                window.resetProactiveChatBackoff();
            } else if (!isChecked && typeof window.stopProactiveChatSchedule === 'function') {
                window.stopProactiveChatSchedule();
            }
            console.log(`主动搭话已${isChecked ? '开启' : '关闭'}`);
        } else if (toggle.id === 'proactive-vision') {
            window.proactiveVisionEnabled = isChecked;

            // 保存到localStorage
            if (typeof window.saveNEKOSettings === 'function') {
                window.saveNEKOSettings();
            }

            if (isChecked) {
                if (typeof window.resetProactiveChatBackoff === 'function') {
                    window.resetProactiveChatBackoff();
                }
                // 如果正在语音对话中，启动15秒1帧定时器
                if (typeof window.isRecording !== 'undefined' && window.isRecording) {
                    if (typeof window.startProactiveVisionDuringSpeech === 'function') {
                        window.startProactiveVisionDuringSpeech();
                    }
                }
            } else {
                if (typeof window.stopProactiveChatSchedule === 'function') {
                    // 只有当主动搭话也关闭时才停止调度
                    if (!window.proactiveChatEnabled) {
                        window.stopProactiveChatSchedule();
                    }
                }
                // 停止语音期间的主动视觉定时器
                if (typeof window.stopProactiveVisionDuringSpeech === 'function') {
                    window.stopProactiveVisionDuringSpeech();
                }
            }
            console.log(`主动视觉已${isChecked ? '开启' : '关闭'}`);
        }
    };

    // 点击切换（直接更新全局状态并保存）
    checkbox.addEventListener('change', (e) => {
        e.stopPropagation();
        handleToggleChange(checkbox.checked);
    });

    // 点击整行也能切换（除了复选框本身）
    toggleItem.addEventListener('click', (e) => {
        if (e.target !== checkbox && e.target !== indicator) {
            e.preventDefault();
            e.stopPropagation();
            const newChecked = !checkbox.checked;
            checkbox.checked = newChecked;
            handleToggleChange(newChecked);
        }
    });

    // 点击指示器也可以切换
    indicator.addEventListener('click', (e) => {
        e.stopPropagation();
        const newChecked = !checkbox.checked;
        checkbox.checked = newChecked;
        handleToggleChange(newChecked);
    });

    // 防止标签点击的默认行为
    label.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        const newChecked = !checkbox.checked;
        checkbox.checked = newChecked;
        handleToggleChange(newChecked);
    });

    return toggleItem;
};

// 创建设置菜单项
Live2DManager.prototype._createSettingsMenuItems = function (popup) {
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
                { id: 'live2d-manage', label: window.t ? window.t('settings.menu.modelSettings') : '模型管理', labelKey: 'settings.menu.modelSettings', icon: '/static/icons/character_icon.png', action: 'navigate', urlBase: '/model_manager' },
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
Live2DManager.prototype._createMenuItem = function (item, isSubmenuItem = false) {
    const menuItem = document.createElement('div');
    menuItem.id = `live2d-menu-${item.id}`;  // 为菜单项添加 ID
    Object.assign(menuItem.style, {
        display: 'flex',
        alignItems: 'center',
        gap: '8px',
        padding: isSubmenuItem ? '6px 12px 6px 36px' : '8px 12px',  // 子菜单项有额外缩进
        cursor: 'pointer',
        borderRadius: '6px',
        transition: 'background 0.2s ease',
        fontSize: isSubmenuItem ? '12px' : '13px',
        whiteSpace: 'nowrap',
        color: 'var(--neko-popup-text, #333)'
    });

    // 添加图标
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

    // 添加文本
    const labelText = document.createElement('span');
    labelText.textContent = item.label;
    if (item.labelKey) {
        labelText.setAttribute('data-i18n', item.labelKey);
    }
    Object.assign(labelText.style, {
        display: 'flex',
        alignItems: 'center',
        lineHeight: '1',
        height: isSubmenuItem ? '18px' : '24px'
    });
    menuItem.appendChild(labelText);

    // 存储更新函数
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

    menuItem.addEventListener('mouseenter', () => {
        menuItem.style.background = 'var(--neko-popup-hover, rgba(68,183,254,0.1))';
    });
    menuItem.addEventListener('mouseleave', () => {
        menuItem.style.background = 'transparent';
    });

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

            if (item.id === 'live2d-manage' && item.urlBase) {
                const lanlanName = (window.lanlan_config && window.lanlan_config.lanlan_name) || '';
                finalUrl = `${item.urlBase}?lanlan_name=${encodeURIComponent(lanlanName)}`;
                // 设置防抖标志，防止导航完成前的重复点击
                isOpening = true;
                window.location.href = finalUrl;
                // 500ms后重置标志，允许再次点击（防止Electron等环境下导航被阻止后永久锁死）
                setTimeout(() => { isOpening = false; }, 500);
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
Live2DManager.prototype._createSubmenuContainer = function (submenuItems) {
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

    // 展开/收缩方法
    container._expand = () => {
        container.style.display = 'flex';
        requestAnimationFrame(() => {
            container.style.height = `${submenuItems.length * 32}px`;
            container.style.opacity = '1';
        });
    };
    container._collapse = () => {
        // 引导模式下，不收起子菜单
        if (window.isInTutorial === true) {
            return;
        }
        container.style.height = '0';
        container.style.opacity = '0';
        setTimeout(() => {
            if (container.style.opacity === '0') {
                container.style.display = 'none';
            }
        }, POPUP_ANIMATION_DURATION_MS);
    };

    return container;
};
