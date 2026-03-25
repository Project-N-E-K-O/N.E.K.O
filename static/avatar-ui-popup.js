/**
 * Avatar UI Popup Mixin - 统一的弹出框组件库
 * 为 MMD/VRM/Live2D 提供通用的弹窗逻辑
 *
 * 使用方式：
 *   AvatarPopupMixin.apply(XXXManager.prototype, 'xxx', { options });
 */

// 常量
const AVATAR_POPUP_ANIMATION_DURATION_MS = 200;

/**
 * 注入指定前缀的 CSS 样式
 */
function injectPopupStyles(prefix) {
    const styleId = `${prefix}-popup-styles`;
    if (document.getElementById(styleId)) return;

    const style = document.createElement('style');
    style.id = styleId;

    const commonCss = `
        .${prefix}-popup {
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
        .${prefix}-popup.is-positioning {
            pointer-events: none !important;
        }
        .${prefix}-popup.${prefix}-popup-settings {
            max-height: 70vh;
        }
        .${prefix}-popup.${prefix}-popup-agent {
            max-height: calc(100vh - 120px);
            overflow-y: auto;
        }
        .${prefix}-popup.visible {
            display: flex;
            opacity: 1;
            transform: translateX(0);
        }
        .${prefix}-popup-item {
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
        .${prefix}-popup-item:hover {
            background: rgba(68, 183, 254, 0.08);
        }
        .${prefix}-popup-item.selected {
            background: rgba(68, 183, 254, 0.1);
        }
        .${prefix}-toggle-item {
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
        .${prefix}-toggle-item:focus-within {
            outline: 2px solid var(--neko-popup-active, #2a7bc4);
            outline-offset: 2px;
        }
        .${prefix}-toggle-item[aria-disabled="true"] {
            opacity: 0.5;
            cursor: default;
        }
        .${prefix}-toggle-indicator {
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
        .${prefix}-toggle-indicator[aria-checked="true"] {
            background-color: var(--neko-popup-active, #2a7bc4);
            border-color: var(--neko-popup-active, #2a7bc4);
        }
        .${prefix}-toggle-checkmark {
            color: #fff;
            font-size: 13px;
            font-weight: bold;
            line-height: 1;
            opacity: 0;
            transition: opacity 0.2s ease;
            pointer-events: none;
            user-select: none;
        }
        .${prefix}-toggle-indicator[aria-checked="true"] .${prefix}-toggle-checkmark {
            opacity: 1;
        }
        .${prefix}-toggle-label {
            cursor: pointer;
            user-select: none;
            font-size: 13px;
            color: var(--neko-popup-text, #333);
        }
        .${prefix}-toggle-item:hover:not([aria-disabled="true"]) {
            background: var(--neko-popup-hover, rgba(68, 183, 254, 0.1));
        }
        .${prefix}-settings-menu-item {
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
        .${prefix}-settings-menu-item:hover {
            background: var(--neko-popup-hover, rgba(68, 183, 254, 0.1));
        }
        .${prefix}-settings-separator {
            height: 1px;
            background: var(--neko-popup-separator, rgba(0, 0, 0, 0.1));
            margin: 4px 0;
        }
        .${prefix}-agent-status {
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

    // VRM 额外的 CSS 变量
    const vrmCss = prefix === 'vrm' ? `
        :root {
            --neko-popup-selected-bg: rgba(68, 183, 254, 0.1);
            --neko-popup-selected-hover: rgba(68, 183, 254, 0.15);
            --neko-popup-hover-subtle: rgba(68, 183, 254, 0.08);
        }
    ` : '';

    style.textContent = vrmCss + commonCss;
    document.head.appendChild(style);
}

/**
 * 创建弹出框（按 buttonId 区分类型）
 */
function createPopup(manager, prefix, buttonId) {
    const popup = document.createElement('div');
    popup.id = `${prefix}-popup-${buttonId}`;
    popup.className = `${prefix}-popup`;

    const stopEventPropagation = (e) => { e.stopPropagation(); };
    ['pointerdown', 'pointermove', 'pointerup', 'mousedown', 'mousemove', 'mouseup', 'touchstart', 'touchmove', 'touchend'].forEach(evt => {
        popup.addEventListener(evt, stopEventPropagation, true);
    });
    popup.addEventListener('click', stopEventPropagation);

    if (buttonId === 'mic') {
        popup.setAttribute('data-legacy-id', `${prefix}-mic-popup`);
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
        popup.classList.add(`${prefix}-popup-agent`);
        window.AgentHUD._createAgentPopupContent.call(manager, popup);
    } else if (buttonId === 'settings') {
        popup.classList.add(`${prefix}-popup-settings`);
        manager._createSettingsPopupContent(popup);
    }

    return popup;
}

/**
 * 最终化弹窗关闭状态
 */
function finalizePopupClosedState(popup) {
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
 * 创建设置弹窗内容（通用）
 */
function createSettingsPopupContent(manager, prefix, popup) {
    // 1. 对话设置按钮
    const chatSettingsBtn = manager._createSettingsMenuButton({
        label: window.t ? window.t('settings.toggles.chatSettings') : '对话设置',
        labelKey: 'settings.toggles.chatSettings'
    });
    popup.appendChild(chatSettingsBtn);

    const chatSidePanel = manager._createChatSettingsSidePanel(popup);
    chatSidePanel._anchorElement = chatSettingsBtn;
    chatSidePanel._popupElement = popup;
    manager._attachSidePanelHover(chatSettingsBtn, chatSidePanel);

    // 2. 动画设置按钮
    const animSettingsBtn = manager._createSettingsMenuButton({
        label: window.t ? window.t('settings.toggles.animationSettings') : '动画设置',
        labelKey: 'settings.toggles.animationSettings'
    });
    popup.appendChild(animSettingsBtn);

    const animSidePanel = manager._createAnimationSettingsSidePanel();
    animSidePanel._anchorElement = animSettingsBtn;
    animSidePanel._popupElement = popup;
    manager._attachSidePanelHover(animSettingsBtn, animSidePanel);

    // 3. 角色设置按钮已移至分隔线下方（在 _createSettingsMenuItems 中创建）

    // 4. 主动搭话和自主视觉（角色设置已移至分隔线下方的导航菜单区域）
    const settingsToggles = [
        { id: 'proactive-chat', label: window.t ? window.t('settings.toggles.proactiveChat') : '主动搭话', labelKey: 'settings.toggles.proactiveChat', storageKey: 'proactiveChatEnabled', hasInterval: true, intervalKey: 'proactiveChatInterval', defaultInterval: 30 },
        { id: 'proactive-vision', label: window.t ? window.t('settings.toggles.proactiveVision') : '自主视觉', labelKey: 'settings.toggles.proactiveVision', storageKey: 'proactiveVisionEnabled', hasInterval: true, intervalKey: 'proactiveVisionInterval', defaultInterval: 15 }
    ];

    settingsToggles.forEach(toggle => {
        const toggleItem = manager._createSettingsToggleItem(toggle);
        popup.appendChild(toggleItem);

        if (toggle.hasInterval) {
            const sidePanel = manager._createIntervalControl(toggle);
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

            manager._attachSidePanelHover(toggleItem, sidePanel);
        }
    });

    // 5. 桌面端添加导航菜单
    if (!window.isMobileWidth || !window.isMobileWidth()) {
        const separator = document.createElement('div');
        separator.className = `${prefix}-settings-separator`;
        popup.appendChild(separator);

        manager._createSettingsMenuItems(popup);
    }
}

/**
 * 创建设置菜单按钮
 */
function createSettingsMenuButton(manager, prefix, config) {
    const btn = document.createElement('div');
    btn.className = `${prefix}-settings-menu-item`;
    Object.assign(btn.style, {
        justifyContent: 'space-between'
    });

    const leftWrapper = document.createElement('div');
    Object.assign(leftWrapper.style, {
        display: 'flex',
        alignItems: 'center',
        gap: '8px'
    });

    let iconImg = null;
    if (config.icon) {
        iconImg = document.createElement('img');
        iconImg.src = config.icon;
        iconImg.alt = config.label || '';
        Object.assign(iconImg.style, {
            width: '24px',
            height: '24px',
            objectFit: 'contain',
            flexShrink: '0'
        });
        leftWrapper.appendChild(iconImg);
    }

    const label = document.createElement('span');
    label.textContent = config.label;
    if (config.labelKey) label.setAttribute('data-i18n', config.labelKey);
    Object.assign(label.style, {
        userSelect: 'none',
        fontSize: '13px'
    });
    leftWrapper.appendChild(label);

    btn.appendChild(leftWrapper);

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
            if (window.t) {
                label.textContent = window.t(config.labelKey);
                if (iconImg) {
                    iconImg.alt = window.t(config.labelKey);
                }
            }
        };
    }

    btn.addEventListener('mouseenter', () => {
        btn.style.background = 'var(--neko-popup-hover, rgba(68,183,254,0.1))';
    });
    btn.addEventListener('mouseleave', () => {
        btn.style.background = 'transparent';
    });

    return btn;
}

/**
 * 创建对话设置侧边面板
 */
function createChatSettingsSidePanel(manager, prefix, popup) {
    const container = manager._createSidePanelContainer();
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
        const toggleItem = manager._createSettingsToggleItem(toggle);
        container.appendChild(toggleItem);
    });

    document.body.appendChild(container);
    return container;
}

/**
 * 创建角色设置侧边面板
 */
function createCharacterSettingsSidePanel(manager, prefix) {
    const container = manager._createSidePanelContainer();
    container.style.flexDirection = 'column';
    container.style.alignItems = 'stretch';
    container.style.gap = '2px';
    container.style.minWidth = '140px';
    container.style.padding = '4px 8px';

    const items = manager._characterMenuItems || [];
    items.forEach(item => {
        const menuItem = manager._createSidePanelMenuItem(item);
        container.appendChild(menuItem);
    });

    document.body.appendChild(container);
    return container;
}

/**
 * 创建侧边面板菜单项
 */
function createSidePanelMenuItem(manager, prefix, item) {
    const menuItem = document.createElement('div');
    menuItem.id = `${prefix}-sidepanel-${item.id}`;
    Object.assign(menuItem.style, {
        display: 'flex',
        alignItems: 'center',
        gap: '6px',
        padding: '6px 8px',
        cursor: 'pointer',
        borderRadius: '6px',
        transition: 'background 0.2s ease',
        fontSize: '12px',
        whiteSpace: 'nowrap',
        color: 'var(--neko-popup-text, #333)'
    });

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
        menuItem.appendChild(iconImg);
    }

    const labelText = document.createElement('span');
    labelText.textContent = (item.labelKey && window.t) ? window.t(item.labelKey) : (item.label || '');
    if (item.labelKey) {
        labelText.setAttribute('data-i18n', item.labelKey);
    }
    Object.assign(labelText.style, {
        userSelect: 'none',
        fontSize: '12px'
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

    menuItem.addEventListener('mouseenter', () => {
        menuItem.style.background = 'var(--neko-popup-hover, rgba(68,183,254,0.1))';
    });
    menuItem.addEventListener('mouseleave', () => {
        menuItem.style.background = 'transparent';
    });

    let isOpening = false;

    menuItem.addEventListener('click', (e) => {
        e.stopPropagation();
        if (isOpening) return;

        if (item.action === 'navigate') {
            let finalUrl = item.url || item.urlBase;
            let windowName = `neko_${item.id}`;
            let features;

            if ((item.id === `${prefix}-manage` || item.id === 'live2d-manage' || item.id === 'vrm-manage' || item.id === 'mmd-manage') && item.urlBase) {
                const lanlanName = (window.lanlan_config && window.lanlan_config.lanlan_name) || '';
                finalUrl = `${item.urlBase}?lanlan_name=${encodeURIComponent(lanlanName)}`;
                isOpening = true;
                window.location.href = finalUrl;
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

                isOpening = true;
                if (typeof window.openOrFocusWindow === 'function') {
                    window.openOrFocusWindow(finalUrl, windowName, features);
                } else {
                    window.open(finalUrl, windowName, features);
                }
                setTimeout(() => { isOpening = false; }, 500);
            } else if (item.url) {
                isOpening = true;
                if (typeof window.openOrFocusWindow === 'function') {
                    window.openOrFocusWindow(finalUrl, windowName);
                } else {
                    window.open(finalUrl, windowName);
                }
                setTimeout(() => { isOpening = false; }, 500);
            }
        }
    });

    return menuItem;
}

/**
 * 创建设置链接项（可展开/折叠）
 */
function createSettingsLinkItem(manager, prefix, item, popup) {
    const linkItem = document.createElement('div');
    linkItem.id = `${prefix}-link-${item.id}`;
    Object.assign(linkItem.style, {
        display: 'none',
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

    const labelSpan = document.createElement('span');
    labelSpan.textContent = (item.labelKey && window.t) ? window.t(item.labelKey) : (item.label || '');
    if (item.labelKey) {
        labelSpan.setAttribute('data-i18n', item.labelKey);
    }
    Object.assign(labelSpan.style, {
        flexShrink: '0',
        fontSize: '11px',
        userSelect: 'none'
    });
    linkItem.appendChild(labelSpan);

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

    linkItem.addEventListener('mouseenter', () => {
        linkItem.style.background = 'var(--neko-popup-hover, rgba(68,183,254,0.1))';
    });
    linkItem.addEventListener('mouseleave', () => {
        linkItem.style.background = 'transparent';
    });

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
            }, manager._animationDurationMs);
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
            }, manager._animationDurationMs);
        });
    };

    return linkItem;
}

/**
 * 创建动画设置侧边面板
 */
function createAnimationSettingsSidePanel(manager, prefix) {
    const container = manager._createSidePanelContainer();
    container.style.flexDirection = 'column';
    container.style.alignItems = 'stretch';
    container.style.gap = '8px';
    container.style.width = '168px';
    container.style.minWidth = '0';
    container.style.padding = '10px 14px';

    const LABEL_STYLE = { width: '36px', flexShrink: '0', fontSize: '12px', color: 'var(--neko-popup-text, #333)' };
    const VALUE_STYLE = { width: '36px', flexShrink: '0', textAlign: 'right', fontSize: '12px', color: 'var(--neko-popup-text, #333)' };
    const SLIDER_STYLE = { flex: '1', minWidth: '0', height: '4px', cursor: 'pointer', accentColor: 'var(--neko-popup-accent, #44b7fe)' };

    // 画质滑动条
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
        // 调用系统特定的回调
        if (typeof manager._onQualityChange === 'function') {
            manager._onQualityChange(quality);
        }
    });
    qualitySlider.addEventListener('click', (e) => e.stopPropagation());
    qualitySlider.addEventListener('mousedown', (e) => e.stopPropagation());

    qualityRow.appendChild(qualityLabel);
    qualityRow.appendChild(qualitySlider);
    qualityRow.appendChild(qualityValue);
    container.appendChild(qualityRow);

    // 帧率滑动条
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

    // 鼠标跟踪切换
    const trackingRow = document.createElement('div');
    Object.assign(trackingRow.style, { display: 'flex', alignItems: 'center', gap: '8px', width: '100%', marginTop: '4px' });

    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.id = `${prefix}-mouse-tracking-toggle`;
    checkbox.style.display = 'none';
    checkbox.checked = typeof manager._getMouseTrackingState === 'function' ? manager._getMouseTrackingState() : true;

    const { indicator, updateStyle: updateIndicatorStyle } = manager._createCheckIndicator();
    Object.assign(indicator.style, { width: '20px', height: '20px', flexShrink: '0' });

    const updateRowStyle = () => {
        const isChecked = checkbox.checked;
        updateIndicatorStyle(isChecked);
        trackingRow.setAttribute('aria-checked', String(isChecked));
    };
    checkbox.updateStyle = updateRowStyle;
    updateRowStyle();

    checkbox.addEventListener('change', updateRowStyle);

    const label = document.createElement('span');
    label.textContent = window.t ? window.t('settings.toggles.mouseTracking') : '跟踪鼠标';
    label.setAttribute('data-i18n', 'settings.toggles.mouseTracking');
    Object.assign(label.style, { userSelect: 'none', fontSize: '12px', flex: '1' });

    trackingRow.appendChild(checkbox);
    trackingRow.appendChild(indicator);
    trackingRow.appendChild(label);
    Object.assign(trackingRow.style, { cursor: 'pointer' });

    // 鼠标跟踪切换事件处理
    const handleTrackingChange = () => {
        const enabled = !checkbox.checked;
        checkbox.checked = enabled;
        updateRowStyle();
        if (typeof window.saveNEKOSettings === 'function') window.saveNEKOSettings();
        if (typeof manager._onMouseTrackingToggle === 'function') {
            manager._onMouseTrackingToggle(enabled);
        }
    };

    trackingRow.addEventListener('click', (e) => {
        e.stopPropagation();
        handleTrackingChange();
        trackingRow.setAttribute('aria-checked', String(checkbox.checked));
    });

    trackingRow.setAttribute('role', 'switch');
    trackingRow.setAttribute('aria-checked', String(checkbox.checked));
    trackingRow.tabIndex = 0;
    trackingRow.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            e.stopPropagation();
            handleTrackingChange();
            trackingRow.setAttribute('aria-checked', String(checkbox.checked));
        }
    });

    container.appendChild(trackingRow);

    document.body.appendChild(container);
    return container;
}

/**
 * 创建侧边面板容器（公共基础样式）
 */
function createSidePanelContainer(manager, prefix, options = {}) {
    const container = document.createElement('div');
    container.setAttribute('data-neko-sidepanel', '');
    Object.assign(container.style, {
        position: 'fixed',
        display: 'none',
        alignItems: options.alignItems || 'center',
        flexDirection: options.flexDirection || 'row',
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
        flexWrap: options.flexWrap || 'wrap',
        width: options.width || 'auto',
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

        requestAnimationFrame(() => {
            container.style.pointerEvents = 'auto';
            container.style.opacity = '1';
            container.style.transform = 'translateX(0)';
        });
    };

    container._collapse = () => {
        if (container.style.display === 'none') return;
        if (container._collapseTimeout) { clearTimeout(container._collapseTimeout); container._collapseTimeout = null; }
        container.style.opacity = '0';
        container.style.transform = container.dataset.goLeft === 'true' ? 'translateX(6px)' : 'translateX(-6px)';
        container._collapseTimeout = setTimeout(() => {
            if (container.style.opacity === '0') container.style.display = 'none';
            container._collapseTimeout = null;
        }, AVATAR_POPUP_ANIMATION_DURATION_MS);
    };

    if (window.AvatarPopupUI && window.AvatarPopupUI.registerSidePanel) {
        window.AvatarPopupUI.registerSidePanel(container);
    }

    return container;
}

/**
 * 附加侧边面板悬停逻辑
 */
function attachSidePanelHover(manager, prefix, anchorEl, sidePanel) {
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
        if (manager.interaction) {
            manager.interaction._isMouseOverButtons = true;
            if (manager.interaction._hideButtonsTimer) { clearTimeout(manager.interaction._hideButtonsTimer); manager.interaction._hideButtonsTimer = null; }
        }
    });
    sidePanel.addEventListener('mouseleave', (e) => {
        collapsePanel(e);
        if (manager.interaction) manager.interaction._isMouseOverButtons = false;
    });

    if (popupEl) {
        popupEl.addEventListener('mouseleave', (e) => {
            const target = e.relatedTarget;
            if (!target || (!anchorEl.contains(target) && !sidePanel.contains(target))) collapseWithDelay(60);
        });
    }
}

/**
 * 创建时间间隔控件
 */
function createIntervalControl(manager, prefix, toggle) {
    const container = document.createElement('div');
    container.className = `${prefix}-interval-control-${toggle.id}`;
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
    slider.id = `${prefix}-${toggle.id}-interval`;
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

    if (toggle.id === 'proactive-chat') {
        if (typeof window.createChatModeToggles === 'function') {
            const chatModesContainer = window.createChatModeToggles(prefix);
            container.appendChild(chatModesContainer);
        }
    }

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

        requestAnimationFrame(() => {
            container.style.pointerEvents = 'auto';
            container.style.opacity = '1';
            container.style.transform = 'translateX(0)';
        });
    };

    container._collapse = () => {
        if (container.style.display === 'none') return;
        if (container._collapseTimeout) { clearTimeout(container._collapseTimeout); container._collapseTimeout = null; }
        container.style.opacity = '0';
        container.style.transform = container.dataset.goLeft === 'true' ? 'translateX(6px)' : 'translateX(-6px)';
        container._collapseTimeout = setTimeout(() => {
            if (container.style.opacity === '0') container.style.display = 'none';
            container._collapseTimeout = null;
        }, AVATAR_POPUP_ANIMATION_DURATION_MS);
    };

    if (window.AvatarPopupUI && window.AvatarPopupUI.registerSidePanel) {
        window.AvatarPopupUI.registerSidePanel(container);
    }

    document.body.appendChild(container);
    return container;
}

/**
 * 创建圆形指示器和对勾
 */
function createCheckIndicator(manager, prefix) {
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
}

/**
 * 创建Agent开关项
 */
function createToggleItem(manager, prefix, toggle, popup) {
    const toggleItem = document.createElement('div');
    toggleItem.className = `${prefix}-toggle-item`;
    toggleItem.setAttribute('role', 'switch');
    toggleItem.setAttribute('tabIndex', toggle.initialDisabled ? '-1' : '0');
    toggleItem.setAttribute('aria-checked', 'false');
    toggleItem.setAttribute('aria-disabled', toggle.initialDisabled ? 'true' : 'false');

    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.id = `${prefix}-${toggle.id}`;
    Object.assign(checkbox.style, {
        position: 'absolute',
        opacity: '0',
        width: '1px',
        height: '1px',
        overflow: 'hidden'
    });
    checkbox.setAttribute('aria-hidden', 'true');

    if (toggle.initialDisabled) {
        checkbox.disabled = true;
        checkbox.title = window.t ? window.t('settings.toggles.checking') : '查询中...';
        checkbox.setAttribute('data-i18n-title', 'settings.toggles.checking');
    }

    const indicator = document.createElement('div');
    indicator.className = `${prefix}-toggle-indicator`;
    indicator.setAttribute('role', 'presentation');
    indicator.setAttribute('aria-hidden', 'true');

    const checkmark = document.createElement('div');
    checkmark.className = `${prefix}-toggle-checkmark`;
    checkmark.innerHTML = '✓';
    indicator.appendChild(checkmark);

    const label = document.createElement('label');
    label.className = `${prefix}-toggle-label`;
    label.innerText = toggle.label;
    if (toggle.labelKey) label.setAttribute('data-i18n', toggle.labelKey);
    label.htmlFor = `${prefix}-${toggle.id}`;
    toggleItem.setAttribute('aria-label', toggle.label);

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
        checkbox._processing = true;
        checkbox._processingTime = Date.now();
        checkbox.checked = !checkbox.checked;
        checkbox.dispatchEvent(new Event('change', { bubbles: true }));
        updateStyle();
        setTimeout(() => checkbox._processing = false, 500);
        e?.preventDefault();
        e?.stopPropagation();
    };

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
}

/**
 * 创建设置开关项
 */
function createSettingsToggleItem(manager, prefix, toggle) {
    const toggleItem = document.createElement('div');
    toggleItem.className = `${prefix}-toggle-item`;
    toggleItem.id = `${prefix}-toggle-${toggle.id}`;
    toggleItem.setAttribute('role', 'switch');
    toggleItem.setAttribute('tabIndex', '0');
    toggleItem.setAttribute('aria-checked', 'false');
    toggleItem.setAttribute('aria-label', toggle.label);
    Object.assign(toggleItem.style, {
        padding: '8px 12px'
    });

    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.id = `${prefix}-${toggle.id}`;
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
    indicator.className = `${prefix}-toggle-indicator`;
    indicator.setAttribute('role', 'presentation');
    indicator.setAttribute('aria-hidden', 'true');

    const checkmark = document.createElement('div');
    checkmark.className = `${prefix}-toggle-checkmark`;
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
                if (typeof window.acquireProactiveVisionStream === 'function') {
                    window.acquireProactiveVisionStream();
                }
                if (typeof window.resetProactiveChatBackoff === 'function') {
                    window.resetProactiveChatBackoff();
                }
                if (typeof window.isRecording !== 'undefined' && window.isRecording) {
                    if (typeof window.startProactiveVisionDuringSpeech === 'function') {
                        window.startProactiveVisionDuringSpeech();
                    }
                }
            } else {
                if (typeof window.releaseProactiveVisionStream === 'function') {
                    window.releaseProactiveVisionStream();
                }
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
}

/**
 * 创建菜单项
 */
function createMenuItem(manager, prefix, config) {
    const menuItem = document.createElement('div');
    menuItem.className = `${prefix}-popup-item`;
    menuItem.textContent = config.label;
    if (config.selected) menuItem.classList.add('selected');

    menuItem.addEventListener('click', (e) => {
        e.stopPropagation();
        if (config.onClick) config.onClick();
    });

    return menuItem;
}

/**
 * 应用mixin到Manager原型
 */
const AvatarPopupMixin = {
    apply: function (ManagerProto, prefix, options = {}) {
        ManagerProto._avatarPrefix = prefix;
        ManagerProto._animationDurationMs = options.animationDurationMs || AVATAR_POPUP_ANIMATION_DURATION_MS;

        // 注入CSS
        injectPopupStyles(prefix);

        // 核心方法
        ManagerProto.createPopup = function (buttonId) {
            return createPopup(this, prefix, buttonId);
        };

        ManagerProto._createSettingsPopupContent = function (popup) {
            return createSettingsPopupContent(this, prefix, popup);
        };

        ManagerProto._createSettingsMenuButton = function (config) {
            return createSettingsMenuButton(this, prefix, config);
        };

        ManagerProto._createChatSettingsSidePanel = function (popup) {
            return createChatSettingsSidePanel(this, prefix, popup);
        };

        ManagerProto._createAnimationSettingsSidePanel = function () {
            return createAnimationSettingsSidePanel(this, prefix);
        };

        ManagerProto._createSidePanelContainer = function (panelOptions = {}) {
            return createSidePanelContainer(this, prefix, options.sidePanelContainerLayout || panelOptions);
        };

        ManagerProto._attachSidePanelHover = function (anchorEl, sidePanel) {
            return attachSidePanelHover(this, prefix, anchorEl, sidePanel);
        };

        ManagerProto._createIntervalControl = function (toggle) {
            return createIntervalControl(this, prefix, toggle);
        };

        ManagerProto._createCheckIndicator = function () {
            return createCheckIndicator(this, prefix);
        };

        ManagerProto._createToggleItem = function (toggle, popup) {
            return createToggleItem(this, prefix, toggle, popup);
        };

        ManagerProto._createSettingsToggleItem = function (toggle) {
            return createSettingsToggleItem(this, prefix, toggle);
        };

        ManagerProto._createMenuItem = function (item, isSubmenuItem = false) {
            const menuItem = document.createElement('div');
            menuItem.className = `${prefix}-settings-menu-item`;
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
            labelText.textContent = (item.labelKey && window.t) ? window.t(item.labelKey) : (item.label || '');
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

            let isOpening = false;

            menuItem.addEventListener('click', (e) => {
                e.stopPropagation();

                if (isOpening) {
                    return;
                }

                if (item.action === 'navigate') {
                    let finalUrl = item.url || item.urlBase;
                    let windowName = `neko_${item.id}`;
                    let features;

                    if ((item.id === `${prefix}-manage` || item.id === 'live2d-manage') && item.urlBase) {
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

        // 新增的核心方法
        ManagerProto.showPopup = function (buttonId, popup) {
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
                const triggerIcon = document.querySelector(`.${prefix}-trigger-icon-${buttonId}`);
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
                    finalizePopupClosedState(popup);
                }, this._animationDurationMs);
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
                                buttonPrefix: `${prefix}-btn-`,
                                triggerPrefix: `${prefix}-trigger-icon-`,
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
                        const triggerIcon = document.querySelector(`.${prefix}-trigger-icon-${buttonId}`);
                        if (triggerIcon) triggerIcon.style.transform = 'rotate(180deg)';
                        requestAnimationFrame(() => {
                            if (popup._showToken !== showToken || popup.style.display !== 'flex') return;
                            popup.style.transform = 'translateX(0)';
                        });
                    });
                });
            }

            // 允许系统特定的钩子
            if (typeof this._onPopupShow === 'function') {
                this._onPopupShow(popup, buttonId);
            }
        };

        ManagerProto.closePopupById = function (buttonId) {
            if (!buttonId) return false;
            const popup = document.getElementById(`${prefix}-popup-${buttonId}`);
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

            const triggerIcon = document.querySelector(`.${prefix}-trigger-icon-${buttonId}`);
            if (triggerIcon) triggerIcon.style.transform = 'rotate(0deg)';

            popup._hideTimeoutId = setTimeout(() => {
                finalizePopupClosedState(popup);
            }, this._animationDurationMs);

            const hasSeparatePopupTrigger = this._buttonConfigs && this._buttonConfigs.find(c => c.id === buttonId && c.separatePopupTrigger);
            if (!hasSeparatePopupTrigger && typeof this.setButtonActive === 'function') {
                this.setButtonActive(buttonId, false);
            }
            return true;
        };

        ManagerProto.closeAllPopupsExcept = function (currentButtonId) {
            document.querySelectorAll(`[id^="${prefix}-popup-"]`).forEach(popup => {
                const popupId = popup.id.replace(`${prefix}-popup-`, '');
                if (popupId !== currentButtonId && popup.style.display === 'flex') this.closePopupById(popupId);
            });
        };

        ManagerProto.closeAllPopups = function () {
            this.closeAllPopupsExcept(null);
        };

        ManagerProto.closeAllSettingsWindows = function (exceptUrl = null) {
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

        ManagerProto._createSettingsMenuItems = function (popup) {
            // 角色设置按钮（带侧边面板）
            if (this._characterMenuItems && this._characterMenuItems.length > 0) {
                const charSettingsBtn = this._createSettingsMenuButton({
                    label: window.t ? window.t('settings.menu.characterSettings') : '角色设置',
                    labelKey: 'settings.menu.characterSettings',
                    icon: '/static/icons/character_icon.png'
                });
                popup.appendChild(charSettingsBtn);
                const charSidePanel = this._createCharacterSettingsSidePanel();
                charSidePanel._anchorElement = charSettingsBtn;
                charSidePanel._popupElement = popup;
                this._attachSidePanelHover(charSettingsBtn, charSidePanel);
            }

            const settingsItems = [
                { id: 'api-keys', label: window.t ? window.t('settings.menu.apiKeys') : 'API密钥', labelKey: 'settings.menu.apiKeys', icon: '/static/icons/api_key_icon.png', action: 'navigate', url: '/api_key' },
                { id: 'memory', label: window.t ? window.t('settings.menu.memoryBrowser') : '记忆浏览', labelKey: 'settings.menu.memoryBrowser', icon: '/static/icons/memory_icon.png', action: 'navigate', url: '/memory_browser' },
                { id: 'steam-workshop', label: window.t ? window.t('settings.menu.steamWorkshop') : '创意工坊', labelKey: 'settings.menu.steamWorkshop', icon: '/static/icons/Steam_icon_logo.png', action: 'navigate', url: '/steam_workshop_manager' },
            ];

            settingsItems.forEach(item => {
                const menuItem = this._createMenuItem(item);
                popup.appendChild(menuItem);
            });
        };

        ManagerProto.renderScreenSourceList = async function (popup) {
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
                        option.className = 'screen-source-option';
                        option.dataset.sourceId = source.id;
                        const isSelected = window.appState && source.id === window.appState.selectedScreenSourceId;
                        Object.assign(option.style, {
                            display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '4px',
                            padding: '6px', borderRadius: '6px', cursor: 'pointer',
                            border: '2px solid ' + (isSelected ? '#4f8cff' : 'transparent'),
                            background: isSelected ? 'var(--neko-popup-selected-bg, rgba(68,183,254,0.1))' : 'transparent',
                            transition: 'background 0.15s ease, border-color 0.15s ease'
                        });
                        if (isSelected) option.classList.add('selected');

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

                        option.addEventListener('mouseenter', () => {
                            if (!option.classList.contains('selected')) {
                                option.style.background = 'rgba(68, 183, 254, 0.1)';
                            }
                        });
                        option.addEventListener('mouseleave', () => {
                            if (!option.classList.contains('selected')) {
                                option.style.background = 'transparent';
                            }
                        });
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

        ManagerProto.renderMicList = async function (popup) {
            if (!popup) return;
            popup.innerHTML = '';

            const t = window.t || ((k, opt) => k);

            try {
                const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                stream.getTracks().forEach(track => track.stop());

                const devices = await navigator.mediaDevices.enumerateDevices();
                const audioInputs = devices.filter(device => device.kind === 'audioinput');

                if (audioInputs.length === 0) {
                    const noDev = document.createElement('div');
                    noDev.textContent = window.t ? window.t('microphone.noDevices') : '未检测到麦克风';
                    Object.assign(noDev.style, { padding: '8px', fontSize: '13px', color: 'var(--neko-popup-text-sub, #666)' });
                    popup.appendChild(noDev);
                    return;
                }

                const addOption = (label, deviceId) => {
                    const btn = document.createElement('div');
                    btn.textContent = label;
                    Object.assign(btn.style, {
                        padding: '8px 12px', cursor: 'pointer', fontSize: '13px',
                        borderRadius: '6px', transition: 'background 0.2s',
                        color: 'var(--neko-popup-text, #333)'
                    });

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
                                        console.error('[UI] 切换麦克风失败:', errorMessage);
                                    }
                                    return;
                                }
                                if (window.showStatusToast) {
                                    const message = window.t ? window.t('microphone.switched') : '已切换麦克风 (下一次录音生效)';
                                    window.showStatusToast(message, 2000);
                                }
                            } catch (e) {
                                console.error('[UI] 切换麦克风时发生网络错误:', e);
                                if (window.showStatusToast) {
                                    const message = window.t ? window.t('microphone.networkError') : '切换麦克风失败：网络错误';
                                    window.showStatusToast(message, 3000);
                                }
                            }
                        }
                    });
                    popup.appendChild(btn);
                };

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

        // 新增方法连接
        ManagerProto._createCharacterSettingsSidePanel = function () {
            return createCharacterSettingsSidePanel(this, prefix);
        };

        ManagerProto._createSidePanelMenuItem = function (item) {
            return createSidePanelMenuItem(this, prefix, item);
        };

        ManagerProto._createSettingsLinkItem = function (item, popup) {
            return createSettingsLinkItem(this, prefix, item, popup);
        };

        // 存储字符菜单项配置
        if (options.characterMenuItems) {
            ManagerProto._characterMenuItems = options.characterMenuItems;
        }

        // 存储回调函数
        if (options.onQualityChange) {
            ManagerProto._onQualityChange = options.onQualityChange;
        }
        if (options.onMouseTrackingToggle) {
            ManagerProto._onMouseTrackingToggle = options.onMouseTrackingToggle;
        }
        if (options.getMouseTrackingState) {
            ManagerProto._getMouseTrackingState = options.getMouseTrackingState;
        }

        // 允许系统特定的覆盖
        if (options.overrides) {
            Object.assign(ManagerProto, options.overrides);
        }
    }
};

window.AvatarPopupMixin = AvatarPopupMixin;
