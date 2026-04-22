/**
 * Live2D UI Drag - 拖拽和弹出框管理
 * 包含弹出框管理、容器拖拽、显示弹出框、折叠功能、按钮事件传播管理
 */

// ===== 拖拽辅助工具 - 按钮事件传播管理 =====
// 使用 body class 控制全局拖动屏蔽，替代逐元素 inline style 遍历。
// 优势：不受 CSS !important 优先级竞争影响，新增 UI 组件只需在 CSS 中加选择器。
(function() {
    'use strict';

    var DRAGGING_CLASS = 'neko-model-dragging';

    // 注入全局 CSS：拖动期间禁用所有按钮、容器、弹窗、侧面板的 pointer-events
    // 【维护注意】新增可交互 UI 组件时，需在此选择器列表中追加对应选择器，
    //  否则拖动模型经过该组件时会出现「粘手」卡顿。
    //  容器类选择器需同时加 * 后代通配符（因为按钮包装器是无 class 的匿名 div）。
    var styleId = 'neko-drag-helpers-styles';
    if (!document.getElementById(styleId)) {
        var style = document.createElement('style');
        style.id = styleId;
        style.textContent = [
            'body.' + DRAGGING_CLASS + ' .live2d-floating-btn,',
            'body.' + DRAGGING_CLASS + ' .live2d-trigger-btn,',
            'body.' + DRAGGING_CLASS + ' [id^="live2d-btn-"],',
            'body.' + DRAGGING_CLASS + ' .vrm-floating-btn,',
            'body.' + DRAGGING_CLASS + ' [id^="vrm-btn-"],',
            'body.' + DRAGGING_CLASS + ' .mmd-floating-btn,',
            'body.' + DRAGGING_CLASS + ' [id^="mmd-btn-"],',
            'body.' + DRAGGING_CLASS + ' #live2d-lock-icon,',
            'body.' + DRAGGING_CLASS + ' #vrm-lock-icon,',
            'body.' + DRAGGING_CLASS + ' #mmd-lock-icon,',
            'body.' + DRAGGING_CLASS + ' #live2d-floating-buttons,',
            'body.' + DRAGGING_CLASS + ' #live2d-floating-buttons *,',
            'body.' + DRAGGING_CLASS + ' #vrm-floating-buttons,',
            'body.' + DRAGGING_CLASS + ' #vrm-floating-buttons *,',
            'body.' + DRAGGING_CLASS + ' #mmd-floating-buttons,',
            'body.' + DRAGGING_CLASS + ' #mmd-floating-buttons *,',
            'body.' + DRAGGING_CLASS + ' .live2d-popup,',
            'body.' + DRAGGING_CLASS + ' .live2d-popup *,',
            'body.' + DRAGGING_CLASS + ' [id^="live2d-popup-"],',
            'body.' + DRAGGING_CLASS + ' [id^="live2d-popup-"] *,',
            'body.' + DRAGGING_CLASS + ' .vrm-popup,',
            'body.' + DRAGGING_CLASS + ' .vrm-popup *,',
            'body.' + DRAGGING_CLASS + ' [id^="vrm-popup-"],',
            'body.' + DRAGGING_CLASS + ' [id^="vrm-popup-"] *,',
            'body.' + DRAGGING_CLASS + ' .mmd-popup,',
            'body.' + DRAGGING_CLASS + ' .mmd-popup *,',
            'body.' + DRAGGING_CLASS + ' [id^="mmd-popup-"],',
            'body.' + DRAGGING_CLASS + ' [id^="mmd-popup-"] *,',
            'body.' + DRAGGING_CLASS + ' [data-neko-sidepanel],',
            'body.' + DRAGGING_CLASS + ' [data-neko-sidepanel] * {',
            '    pointer-events: none !important;',
            '}',
            '',
            '/* 排除返回按钮容器——它们有自己的拖拽行为 */',
            'body.' + DRAGGING_CLASS + ' [id$="-return-button-container"],',
            'body.' + DRAGGING_CLASS + ' [id$="-return-button-container"] * {',
            '    pointer-events: auto !important;',
            '}'
        ].join('\n');
        document.head.appendChild(style);
    }

    /**
     * 禁用按钮的 pointer-events
     * 在拖动开始时调用，通过 body class 让 CSS 规则生效
     */
    function disableButtonPointerEvents() {
        document.body.classList.add(DRAGGING_CLASS);
        // 拖动开始时关闭所有已展开的弹窗
        [window.live2dManager, window.vrmManager, window.mmdManager].forEach(function(m) {
            if (m && typeof m.closeAllPopups === 'function') m.closeAllPopups();
        });
    }

    /**
     * 恢复按钮的 pointer-events
     * 在拖动结束时调用，移除 body class 让 CSS 规则失效
     */
    function restoreButtonPointerEvents() {
        document.body.classList.remove(DRAGGING_CLASS);
    }

    // 挂载到全局 window 对象，供其他脚本使用
    window.DragHelpers = {
        disableButtonPointerEvents: disableButtonPointerEvents,
        restoreButtonPointerEvents: restoreButtonPointerEvents
    };
})();

// ===== 弹出框管理 =====

// 关闭指定按钮对应的弹出框，并恢复按钮状态
Live2DManager.prototype.closePopupById = function (buttonId) {
    if (!buttonId) return false;

    // 引导模式下，阻止关闭设置弹出框
    if (window.isInTutorial === true && buttonId === 'settings') {
        console.log('[Live2D] 引导中：阻止关闭设置弹出框');
        return false;
    }

    this._floatingButtons = this._floatingButtons || {};
    this._popupTimers = this._popupTimers || {};
    const popup = document.getElementById(`live2d-popup-${buttonId}`);
    if (!popup || popup.style.display !== 'flex') {
        return false;
    }

    // 如果是 agent 弹窗关闭，派发关闭事件
    if (buttonId === 'agent') {
        window.dispatchEvent(new CustomEvent('neko-popup-closed'));
    }

    popup._showToken = (popup._showToken || 0) + 1;
    popup.style.opacity = '0';
    const closeOpensLeft = popup.dataset.opensLeft === 'true';
    popup.style.transform = closeOpensLeft ? 'translateX(10px)' : 'translateX(-10px)';

    // 关闭该 popup 所属的所有侧面板
    const popupId = popup.id;
    if (popupId) {
        document.querySelectorAll(`[data-neko-sidepanel-owner="${popupId}"]`).forEach(panel => {
            if (panel._collapseTimeout) { clearTimeout(panel._collapseTimeout); panel._collapseTimeout = null; }
            if (panel._hoverCollapseTimer) { clearTimeout(panel._hoverCollapseTimer); panel._hoverCollapseTimer = null; }
            panel.style.transition = 'none';
            panel.style.opacity = '0';
            panel.style.display = 'none';
            // 清除 inline transition，让 CSS 定义的 transition 在下次 _expand() 时生效
            panel.style.transition = '';
        });
    }

    // 复位小三角图标
    const triggerIcon = document.querySelector(`.live2d-trigger-icon-${buttonId}`);
    if (triggerIcon) triggerIcon.style.transform = 'rotate(0deg)';
    
    setTimeout(() => {
        popup.style.display = 'none';
        delete popup.dataset.opensLeft;
    }, 200);

    // 检查按钮是否有 separatePopupTrigger 配置
    // 对于有 separatePopupTrigger 的按钮（mic 和 screen），小三角弹出框和按钮激活状态是独立的
    // 关闭弹出框时不应该重置按钮状态
    const hasSeparatePopupTrigger = this._buttonConfigs && this._buttonConfigs.find(config => config.id === buttonId && config.separatePopupTrigger);
    
    if (!hasSeparatePopupTrigger) {
        const buttonEntry = this._floatingButtons[buttonId];
        if (buttonEntry && buttonEntry.button) {
            buttonEntry.button.dataset.active = 'false';
            buttonEntry.button.style.background = 'var(--neko-btn-bg, rgba(255, 255, 255, 0.65))';

            if (buttonEntry.imgOff && buttonEntry.imgOn) {
                buttonEntry.imgOff.style.opacity = '1';
                buttonEntry.imgOn.style.opacity = '0';
            }
        }
    }

    if (this._popupTimers[buttonId]) {
        clearTimeout(this._popupTimers[buttonId]);
        this._popupTimers[buttonId] = null;
    }

    return true;
};

// 关闭除当前按钮之外的所有弹出框
Live2DManager.prototype.closeAllPopupsExcept = function (currentButtonId) {
    const popups = document.querySelectorAll('[id^="live2d-popup-"]');
    popups.forEach(popup => {
        const popupId = popup.id.replace('live2d-popup-', '');
        if (popupId !== currentButtonId && popup.style.display === 'flex') {
            this.closePopupById(popupId);
        }
    });
};

// 关闭所有弹出框（不排除任何按钮）
Live2DManager.prototype.closeAllPopups = function () {
    this.closeAllPopupsExcept(null);
};

// 关闭所有通过 window.open 打开的设置窗口，可选保留特定 URL
Live2DManager.prototype.closeAllSettingsWindows = function (exceptUrl = null) {
    if (!this._openSettingsWindows) return;
    Object.keys(this._openSettingsWindows).forEach(url => {
        if (exceptUrl && url === exceptUrl) return;
        const winRef = this._openSettingsWindows[url];
        try {
            if (winRef && !winRef.closed) {
                winRef.close();
            }
        } catch (_) {
            // 忽略跨域导致的 close 异常
        }
        delete this._openSettingsWindows[url];
    });
};

// 为"请她回来"按钮容器设置拖动功能
Live2DManager.prototype.setupReturnButtonContainerDrag = function (returnButtonContainer) {
    let isDragging = false;
    let dragStartX = 0;
    let dragStartY = 0;
    let containerStartX = 0;
    let containerStartY = 0;
    let isClick = false; // 标记是否为点击操作

    // 鼠标按下事件
    returnButtonContainer.addEventListener('mousedown', (e) => {
        // 允许在按钮容器本身和按钮元素上都能开始拖动
        // 这样就能在按钮正中心位置进行拖拽操作
        if (e.target === returnButtonContainer || e.target.classList.contains('live2d-return-btn')) {
            isDragging = true;
            isClick = true;
            dragStartX = e.clientX;
            dragStartY = e.clientY;

            const rect = returnButtonContainer.getBoundingClientRect();
            containerStartX = rect.left;
            containerStartY = rect.top;
            returnButtonContainer.style.right = '';
            returnButtonContainer.style.bottom = '';
            returnButtonContainer.style.left = `${containerStartX}px`;
            returnButtonContainer.style.top = `${containerStartY}px`;

            returnButtonContainer.setAttribute('data-dragging', 'false');
            returnButtonContainer.style.cursor = 'grabbing';
            e.preventDefault();
        }
    });

    // 鼠标移动事件
    document.addEventListener('mousemove', (e) => {
        if (isDragging) {
            const deltaX = e.clientX - dragStartX;
            const deltaY = e.clientY - dragStartY;

            const dragThreshold = 5;
            if (Math.abs(deltaX) > dragThreshold || Math.abs(deltaY) > dragThreshold) {
                isClick = false;
                returnButtonContainer.setAttribute('data-dragging', 'true');
            }

            const newX = containerStartX + deltaX;
            const newY = containerStartY + deltaY;

            // 边界检查 - 使用窗口尺寸（窗口只覆盖当前屏幕）
            const containerWidth = returnButtonContainer.offsetWidth || 64;
            const containerHeight = returnButtonContainer.offsetHeight || 64;

            const boundedX = Math.max(0, Math.min(newX, window.innerWidth - containerWidth));
            const boundedY = Math.max(0, Math.min(newY, window.innerHeight - containerHeight));

            returnButtonContainer.style.left = `${boundedX}px`;
            returnButtonContainer.style.top = `${boundedY}px`;
        }
    });

    // 鼠标释放事件
    document.addEventListener('mouseup', (e) => {
        if (isDragging) {
            setTimeout(() => {
                returnButtonContainer.setAttribute('data-dragging', 'false');
            }, 10);

            isDragging = false;
            isClick = false;
            returnButtonContainer.style.cursor = 'grab';
        }
    });

    // 设置初始鼠标样式
    returnButtonContainer.style.cursor = 'grab';

    // 触摸事件支持
    returnButtonContainer.addEventListener('touchstart', (e) => {
        // 允许在按钮容器本身和按钮元素上都能开始拖动
        if (e.target === returnButtonContainer || e.target.classList.contains('live2d-return-btn')) {
            isDragging = true;
            isClick = true;
            const touch = e.touches[0];
            dragStartX = touch.clientX;
            dragStartY = touch.clientY;

            const rect = returnButtonContainer.getBoundingClientRect();
            containerStartX = rect.left;
            containerStartY = rect.top;
            returnButtonContainer.style.right = '';
            returnButtonContainer.style.bottom = '';
            returnButtonContainer.style.left = `${containerStartX}px`;
            returnButtonContainer.style.top = `${containerStartY}px`;

            returnButtonContainer.setAttribute('data-dragging', 'false');
            e.preventDefault();
        }
    });

    document.addEventListener('touchmove', (e) => {
        if (isDragging) {
            const touch = e.touches[0];
            const deltaX = touch.clientX - dragStartX;
            const deltaY = touch.clientY - dragStartY;

            const dragThreshold = 5;
            if (Math.abs(deltaX) > dragThreshold || Math.abs(deltaY) > dragThreshold) {
                isClick = false;
                returnButtonContainer.setAttribute('data-dragging', 'true');
            }

            const newX = containerStartX + deltaX;
            const newY = containerStartY + deltaY;

            // 边界检查 - 使用窗口尺寸
            const containerWidth = returnButtonContainer.offsetWidth || 64;
            const containerHeight = returnButtonContainer.offsetHeight || 64;

            const boundedX = Math.max(0, Math.min(newX, window.innerWidth - containerWidth));
            const boundedY = Math.max(0, Math.min(newY, window.innerHeight - containerHeight));

            returnButtonContainer.style.left = `${boundedX}px`;
            returnButtonContainer.style.top = `${boundedY}px`;
            e.preventDefault();
        }
    });

    document.addEventListener('touchend', (e) => {
        if (isDragging) {
            setTimeout(() => {
                returnButtonContainer.setAttribute('data-dragging', 'false');
            }, 10);

            isDragging = false;
            isClick = false;
        }
    });
};

// 全局函数：更新圆形指示器样式
window.updateChatModeStyle = function(checkbox) {
    if (!checkbox) return;
    const wrapper = checkbox.parentElement;
    if (!wrapper) return;
    const indicator = wrapper.querySelector('.chat-mode-indicator');
    const checkmark = indicator?.querySelector('.chat-mode-checkmark');
    if (!indicator || !checkmark) return;
    if (checkbox.checked) {
        indicator.style.backgroundColor = '#44b7fe';
        indicator.style.borderColor = '#44b7fe';
        checkmark.style.opacity = '1';
    } else {
        indicator.style.backgroundColor = 'transparent';
        indicator.style.borderColor = '#ccc';
        checkmark.style.opacity = '0';
    }

    const hovered = wrapper.matches(':hover');
    wrapper.style.background = checkbox.checked
        ? (hovered
            ? 'var(--neko-popup-selected-hover, rgba(68,183,254,0.15))'
            : 'var(--neko-popup-selected-bg, rgba(68,183,254,0.1))')
        : (hovered
            ? 'var(--neko-popup-hover-subtle, rgba(68,183,254,0.08))'
            : 'transparent');
};

// 兼容旧函数名
window.updateVisionOnlyStyle = window.updateChatModeStyle;

// 全局工厂函数：创建搭话方式选项控件
window.createChatModeToggle = function(options) {
    const { checkboxId, labelKey, tooltipKey, globalVarName } = options;
    
    const wrapper = document.createElement('div');
    const tooltipText = window.t ? window.t(tooltipKey) : '';
    wrapper.title = tooltipText;
    Object.assign(wrapper.style, {
        display: 'flex',
        alignItems: 'center',
        gap: '4px',
        width: '100%',
        padding: '6px 10px',
        marginTop: '0',
        cursor: 'pointer',
        borderRadius: '6px',
        boxSizing: 'border-box',
        transition: 'background 0.2s ease'
    });

    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.id = checkboxId;
    console.log(`[ChatModeToggle] 初始化 checkbox: ${checkboxId}, globalVarName=${globalVarName}, window值=${window[globalVarName]}`);
    if (typeof window[globalVarName] !== 'undefined') {
        checkbox.checked = window[globalVarName];
    }
    Object.assign(checkbox.style, {
        position: 'absolute',
        opacity: '0',
        width: '0',
        height: '0'
    });

    const indicator = document.createElement('div');
    indicator.classList.add('chat-mode-indicator');
    Object.assign(indicator.style, {
        width: '16px',
        height: '16px',
        borderRadius: '50%',
        border: '2px solid #ccc',
        backgroundColor: 'transparent',
        cursor: 'pointer',
        flexShrink: '0',
        transition: 'all 0.2s ease',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center'
    });

    const checkmark = document.createElement('div');
    checkmark.classList.add('chat-mode-checkmark');
    checkmark.innerHTML = '✓';
    Object.assign(checkmark.style, {
        color: '#fff',
        fontSize: '11px',
        fontWeight: 'bold',
        lineHeight: '1',
        opacity: '0',
        transition: 'opacity 0.2s ease',
        pointerEvents: 'none',
        userSelect: 'none'
    });
    indicator.appendChild(checkmark);

    const label = document.createElement('label');
    label.textContent = window.t ? window.t(labelKey) : '';
    label.setAttribute('data-i18n', labelKey);
    label.htmlFor = checkboxId;
    Object.assign(label.style, {
        fontSize: '12px',
        color: 'var(--neko-popup-text, #333)',
        cursor: 'pointer',
        whiteSpace: 'nowrap'
    });

    checkbox.addEventListener('change', (e) => {
        e.stopPropagation();
        window.updateChatModeStyle(checkbox);
        window[globalVarName] = checkbox.checked;
        if (typeof window.saveNEKOSettings === 'function') {
            window.saveNEKOSettings();
        }
        if (checkbox.checked) {
            // 开启时，如果主动搭话已开启，重置并启动调度
            if (window.proactiveChatEnabled && typeof window.resetProactiveChatBackoff === 'function') {
                window.resetProactiveChatBackoff();
            }
        } else {
            // 关闭时的逻辑：区分主开关和子模式
            const isMainSwitch = globalVarName === 'proactiveChatEnabled';
            
            if (isMainSwitch) {
                // 主开关关闭：停止调度
                if (typeof window.stopProactiveChatSchedule === 'function') {
                    window.stopProactiveChatSchedule();
                }
            } else {
                // 子模式关闭：如果没有其他子模式开启，停止调度
                const hasOtherSubMode = (window.CHAT_MODE_CONFIG || []).some(config =>
                    config.globalVarName !== globalVarName && Boolean(window[config.globalVarName])
                );
                if (!hasOtherSubMode && typeof window.stopProactiveChatSchedule === 'function') {
                    window.stopProactiveChatSchedule();
                }
            }
        }
        console.log(`${label.textContent}已${checkbox.checked ? '开启' : '关闭'}`);
    });

    checkbox.addEventListener('click', (e) => e.stopPropagation());
    wrapper.addEventListener('mouseenter', () => {
        window.updateChatModeStyle(checkbox);
    });
    wrapper.addEventListener('mouseleave', () => {
        window.updateChatModeStyle(checkbox);
    });
    wrapper.addEventListener('click', (e) => {
        if (e.target === checkbox) return;
        e.preventDefault();
        e.stopPropagation();
        checkbox.click();
    });
    label.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        checkbox.click();
    });
    indicator.addEventListener('click', (e) => {
        e.stopPropagation();
        checkbox.click();
    });

    wrapper.appendChild(checkbox);
    wrapper.appendChild(indicator);
    wrapper.appendChild(label);

    window.updateChatModeStyle(checkbox);

    return wrapper;
};

// 聊天模式配置（单一数据源）
window.CHAT_MODE_CONFIG = [
    {
        mode: 'vision',
        labelKey: 'settings.toggles.proactiveVisionChat',
        tooltipKey: 'settings.toggles.proactiveVisionChatTooltip',
        globalVarName: 'proactiveVisionChatEnabled'
    },
    {
        mode: 'news',
        labelKey: 'settings.toggles.proactiveNewsChat',
        tooltipKey: 'settings.toggles.proactiveNewsChatTooltip',
        globalVarName: 'proactiveNewsChatEnabled'
    },
    {
        mode: 'video',
        labelKey: 'settings.toggles.proactiveVideoChat',
        tooltipKey: 'settings.toggles.proactiveVideoChatTooltip',
        globalVarName: 'proactiveVideoChatEnabled'
    },
    {
        mode: 'personal',
        labelKey: 'settings.toggles.proactivePersonalChat',
        tooltipKey: 'settings.toggles.proactivePersonalChatTooltip',
        globalVarName: 'proactivePersonalChatEnabled'
    },
    {
        mode: 'music',
        labelKey: 'settings.toggles.proactiveMusicChat',
        tooltipKey: 'settings.toggles.proactiveMusicChatTooltip',
        globalVarName: 'proactiveMusicEnabled'
    },
    {
        mode: 'meme',
        labelKey: 'settings.toggles.proactiveMemeChat',
        tooltipKey: 'settings.toggles.proactiveMemeChatTooltip',
        globalVarName: 'proactiveMemeEnabled'
    }
];

// 全局工厂函数：创建所有搭话方式选项
window.createChatModeToggles = function(prefix) {
    const container = document.createElement('div');
    Object.assign(container.style, {
        display: 'flex',
        flexDirection: 'column',
        gap: '1px',
        width: '100%'
    });

    // 使用共享配置创建搭话方式选项
    window.CHAT_MODE_CONFIG.forEach(config => {
        const toggle = window.createChatModeToggle({
            checkboxId: `${prefix}-proactive-${config.mode}-chat`,
            labelKey: config.labelKey,
            tooltipKey: config.tooltipKey,
            globalVarName: config.globalVarName
        });
        container.appendChild(toggle);
    });

    return container;
};

// 兼容旧函数名
window.createVisionOnlyToggle = function(checkboxId) {
    return window.createChatModeToggle({
        checkboxId: checkboxId,
        labelKey: 'settings.toggles.proactiveVisionChat',
        tooltipKey: 'settings.toggles.proactiveVisionChatTooltip',
        globalVarName: 'proactiveVisionChatEnabled'
    });
};

// 显示弹出框（1秒后自动隐藏），支持点击切换
Live2DManager.prototype.showPopup = function (buttonId, popup) {
    // 确保 _popupTimers 已初始化
    this._popupTimers = this._popupTimers || {};
    const popupUi = window.AvatarPopupUI || null;
    if (typeof popup._showToken !== 'number') popup._showToken = 0;

    // 检查当前状态
    const isVisible = popup.style.display === 'flex' || popup.style.opacity === '1';

    // 清除之前的定时器
    if (this._popupTimers[buttonId]) {
        clearTimeout(this._popupTimers[buttonId]);
        this._popupTimers[buttonId] = null;
    }

    // 如果是设置弹出框，每次显示时更新开关状态（确保与 app.js 同步）
    if (buttonId === 'settings') {
        const mergeCheckbox = document.querySelector('#live2d-merge-messages');
        const focusCheckbox = document.querySelector('#live2d-focus-mode');
        const avatarBubbleCheckbox = document.querySelector('#live2d-avatar-reaction-bubble');
        const proactiveChatCheckbox = popup.querySelector('#live2d-proactive-chat');
        const proactiveVisionCheckbox = popup.querySelector('#live2d-proactive-vision');

        // 辅助函数：更新 checkbox 的视觉样式
        const updateCheckboxStyle = (checkbox) => {
            if (!checkbox) return;
            const toggleItem = checkbox.parentElement;
            if (!toggleItem) return;

            const indicator = toggleItem.querySelector('[class*="-toggle-indicator"]');
            const checkmark = indicator?.querySelector('[class*="-toggle-checkmark"]');
            if (!indicator || !checkmark) return;
            const alwaysTinted = ['live2d-merge-messages', 'live2d-focus-mode', 'live2d-avatar-reaction-bubble'].includes(checkbox.id);
            const checkedColor = alwaysTinted ? '#69c5ff' : '#44b7fe';

            if (checkbox.checked) {
                indicator.style.backgroundColor = checkedColor;
                indicator.style.borderColor = checkedColor;
                checkmark.style.opacity = '1';
                toggleItem.style.background = 'rgba(68, 183, 254, 0.1)';
            } else {
                indicator.style.backgroundColor = 'transparent';
                indicator.style.borderColor = '#ccc';
                checkmark.style.opacity = '0';
                toggleItem.style.background = alwaysTinted ? 'rgba(68, 183, 254, 0.1)' : 'transparent';
            }
        };

        // 更新 merge messages checkbox 状态和视觉样式
        if (mergeCheckbox && typeof window.mergeMessagesEnabled !== 'undefined') {
            const newChecked = window.mergeMessagesEnabled;
            if (mergeCheckbox.checked !== newChecked) {
                mergeCheckbox.checked = newChecked;
            }
            requestAnimationFrame(() => {
                updateCheckboxStyle(mergeCheckbox);
            });
        }

        // 更新 focus mode checkbox 状态和视觉样式
        if (focusCheckbox && typeof window.focusModeEnabled !== 'undefined') {
            const newChecked = !window.focusModeEnabled;
            if (focusCheckbox.checked !== newChecked) {
                focusCheckbox.checked = newChecked;
            }
            requestAnimationFrame(() => {
                updateCheckboxStyle(focusCheckbox);
            });
        }

        if (avatarBubbleCheckbox && typeof window.avatarReactionBubbleEnabled !== 'undefined') {
            const newChecked = window.avatarReactionBubbleEnabled;
            if (avatarBubbleCheckbox.checked !== newChecked) {
                avatarBubbleCheckbox.checked = newChecked;
            }
            requestAnimationFrame(() => {
                updateCheckboxStyle(avatarBubbleCheckbox);
            });
        }

        // 更新 proactive chat checkbox 状态和视觉样式
        if (proactiveChatCheckbox && typeof window.proactiveChatEnabled !== 'undefined') {
            const newChecked = window.proactiveChatEnabled;
            if (proactiveChatCheckbox.checked !== newChecked) {
                proactiveChatCheckbox.checked = newChecked;
            }
            requestAnimationFrame(() => {
                updateCheckboxStyle(proactiveChatCheckbox);
            });
        }

        // 更新 proactive vision checkbox 状态和视觉样式
        if (proactiveVisionCheckbox && typeof window.proactiveVisionEnabled !== 'undefined') {
            const newChecked = window.proactiveVisionEnabled;
            if (proactiveVisionCheckbox.checked !== newChecked) {
                proactiveVisionCheckbox.checked = newChecked;
            }
            requestAnimationFrame(() => {
                updateCheckboxStyle(proactiveVisionCheckbox);
            });
        }

        // 同步搭话方式选项状态
        if (window.CHAT_MODE_CONFIG) {
            window.CHAT_MODE_CONFIG.forEach(config => {
                const checkbox = document.querySelector(`#live2d-proactive-${config.mode}-chat`);
                if (checkbox && typeof window[config.globalVarName] !== 'undefined') {
                    const newChecked = window[config.globalVarName];
                    if (checkbox.checked !== newChecked) {
                        checkbox.checked = newChecked;
                    }
                    requestAnimationFrame(() => {
                        if (typeof window.updateChatModeStyle === 'function') {
                            window.updateChatModeStyle(checkbox);
                        }
                    });
                }
            });
        }

        // 同步鼠标跟踪开关状态
        const mouseTrackingCheckbox = popup.querySelector('#live2d-mouse-tracking-toggle');
        if (mouseTrackingCheckbox && typeof window.mouseTrackingEnabled !== 'undefined') {
            const newChecked = window.mouseTrackingEnabled;
            if (mouseTrackingCheckbox.checked !== newChecked) {
                mouseTrackingCheckbox.checked = newChecked;
            }
            requestAnimationFrame(() => {
                updateCheckboxStyle(mouseTrackingCheckbox);
            });
        }
    }

    // 如果是 agent 弹窗，触发服务器状态检查事件
    if (buttonId === 'agent' && !isVisible) {
        // 弹窗即将显示，派发事件让 app.js 检查服务器状态
        window.dispatchEvent(new CustomEvent('neko-popup-opening'));
    }

    if (isVisible) {
        // 引导模式下，阻止关闭设置弹出框
        if (window.isInTutorial === true && buttonId === 'settings') {
            console.log('[Live2D] 引导中：阻止切换关闭设置弹出框');
            return;
        }

        // 如果已经显示，则隐藏
        popup._showToken += 1;
        popup.style.opacity = '0';
        const closingOpensLeft = popup.dataset.opensLeft === 'true';
        popup.style.transform = closingOpensLeft ? 'translateX(10px)' : 'translateX(-10px)';
        const triggerIcon = document.querySelector(`.live2d-trigger-icon-${buttonId}`);
        if (triggerIcon) triggerIcon.style.transform = 'rotate(0deg)';

        // 关闭该 popup 所属的所有侧面板
        const closingPopupId = popup.id;
        if (closingPopupId) {
            document.querySelectorAll(`[data-neko-sidepanel-owner="${closingPopupId}"]`).forEach(panel => {
                if (panel._collapseTimeout) { clearTimeout(panel._collapseTimeout); panel._collapseTimeout = null; }
                if (panel._hoverCollapseTimer) { clearTimeout(panel._hoverCollapseTimer); panel._hoverCollapseTimer = null; }
                panel.style.transition = 'none';
                panel.style.opacity = '0';
                panel.style.display = 'none';
            });
        }

        // 如果是 agent 弹窗关闭，派发关闭事件
        if (buttonId === 'agent') {
            window.dispatchEvent(new CustomEvent('neko-popup-closed'));
        }

        setTimeout(() => {
            popup.style.display = 'none';
            delete popup.dataset.opensLeft;
            // 重置位置和样式
            if (popupUi && typeof popupUi.resetPopupPosition === 'function') {
                popupUi.resetPopupPosition(popup, { left: '100%', top: '0' });
            } else {
                popup.style.left = '100%';
                popup.style.right = 'auto';
                popup.style.top = '0';
                popup.style.marginLeft = '8px';
                popup.style.marginRight = '0';
            }
            // 重置高度限制，确保下次打开时状态一致
            if (buttonId === 'settings' || buttonId === 'agent') {
                popup.style.maxHeight = '200px';
                popup.style.overflowY = 'auto';
                popup.style.maxWidth = '';
                popup.style.width = '';
            }
        }, 200);
    } else {
        // 全局互斥：打开前关闭其他弹出框
        const showToken = popup._showToken + 1;
        popup._showToken = showToken;
        this.closeAllPopupsExcept(buttonId);

        // 如果隐藏，则显示
        popup.style.display = 'flex';
        // 先让弹出框可见但透明，以便计算尺寸
        popup.style.opacity = '0';
        popup.style.visibility = 'visible';
        popup.style.pointerEvents = 'none'; // 阻止 positionPopup 完成前的 hover 事件

        // 点击后立刻反转小三角，不等待弹窗定位 / 展开动画完成
        const openingTriggerIcon = document.querySelector(`.live2d-trigger-icon-${buttonId}`);
        if (openingTriggerIcon) {
            openingTriggerIcon.style.transform = 'rotate(180deg)';
        }

        // 关键：在计算位置之前，先移除高度限制，确保获取真实尺寸
        const isMobile = typeof isMobileWidth === 'function' && isMobileWidth();
        if (buttonId === 'settings' || buttonId === 'agent') {
            if (isMobile) {
                const maxHeight = Math.max(180, window.innerHeight - 120);
                const maxWidth = Math.max(200, window.innerWidth - 32);
                popup.style.maxHeight = `${maxHeight}px`;
                popup.style.overflowY = 'auto';
                popup.style.maxWidth = `${maxWidth}px`;
                popup.style.width = 'auto';
            } else {
                popup.style.maxHeight = 'none';
                popup.style.overflowY = 'visible';
                popup.style.maxWidth = '';
                popup.style.width = '';
            }
        }

        // 等待popup内的所有图片加载完成，确保尺寸准确
        const images = popup.querySelectorAll('img');
        const imageLoadPromises = Array.from(images).map(img => {
            if (img.complete) {
                return Promise.resolve();
            }
            return new Promise(resolve => {
                img.onload = resolve;
                img.onerror = resolve; // 即使加载失败也继续
                // 超时保护：最多等待100ms
                setTimeout(resolve, 100);
            });
        });

        Promise.all(imageLoadPromises).then(() => {
            if (popup._showToken !== showToken || popup.style.display !== 'flex') return;
            // 强制触发reflow，确保布局完全更新
            void popup.offsetHeight;

            // 再次使用RAF确保布局稳定
            requestAnimationFrame(() => {
                if (popup._showToken !== showToken || popup.style.display !== 'flex') return;
                if (popupUi && typeof popupUi.positionPopup === 'function') {
                    const pos = popupUi.positionPopup(popup, {
                        buttonId,
                        buttonPrefix: 'live2d-btn-',
                        triggerPrefix: 'live2d-trigger-icon-',
                        rightMargin: 20,
                        bottomMargin: 60,
                        topMargin: 8,
                        gap: 8,
                        sidePanelWidth: (buttonId === 'settings' || buttonId === 'agent') && !isMobile ? 320 : 0
                    });
                    popup.style.transform = pos && pos.opensLeft ? 'translateX(10px)' : 'translateX(-10px)';
                }

                if (popup._showToken !== showToken || popup.style.display !== 'flex') return;
                // 显示弹出框
                popup.style.visibility = 'visible';
                popup.style.opacity = '1';
                popup.style.pointerEvents = ''; // positionPopup 完成，恢复交互
                popup.style.transform = 'translateX(0)';

                // 设置小三角图标的旋转状态（旋转180度）
                const triggerIcon = document.querySelector(`.live2d-trigger-icon-${buttonId}`);
                if (triggerIcon) {
                    triggerIcon.style.transform = 'rotate(180deg)';
                }
            });
        });

        // 设置、agent、麦克风、屏幕源弹出框不自动隐藏，其他的1秒后隐藏
        if (buttonId !== 'settings' && buttonId !== 'agent' && buttonId !== 'mic' && buttonId !== 'screen') {
            this._popupTimers[buttonId] = setTimeout(() => {
                popup.style.opacity = '0';
                const opensLeft = popup.dataset.opensLeft === 'true';
                popup.style.transform = opensLeft ? 'translateX(10px)' : 'translateX(-10px)';
                const triggerIcon = document.querySelector(`.live2d-trigger-icon-${buttonId}`);
                if (triggerIcon) triggerIcon.style.transform = 'rotate(0deg)';
                setTimeout(() => {
                    popup.style.display = 'none';
                    delete popup.dataset.opensLeft;
                    // 重置位置
                    if (popupUi && typeof popupUi.resetPopupPosition === 'function') {
                        popupUi.resetPopupPosition(popup, { left: '100%', top: '0' });
                    } else {
                        popup.style.left = '100%';
                        popup.style.right = 'auto';
                        popup.style.top = '0';
                    }
                }, 200);
                this._popupTimers[buttonId] = null;
            }, 1000);
        }
    }
};

// 设置折叠功能
Live2DManager.prototype._setupCollapseFunctionality = function (emptyState, collapseButton, emptyContent) {
    // 获取折叠状态
    const getCollapsedState = () => {
        try {
            const saved = localStorage.getItem('agent-task-empty-collapsed');
            return saved === 'true';
        } catch (error) {
            console.warn('Failed to read collapse state from localStorage:', error);
            return false;
        }
    };

    // 保存折叠状态
    const saveCollapsedState = (collapsed) => {
        try {
            localStorage.setItem('agent-task-empty-collapsed', collapsed.toString());
        } catch (error) {
            console.warn('Failed to save collapse state to localStorage:', error);
        }
    };

    // 初始化状态
    let isCollapsed = getCollapsedState();
    let touchProcessed = false; // 防止触摸设备双重切换的标志

    // 更新折叠状态
    const updateCollapseState = (collapsed) => {
        isCollapsed = collapsed;

        if (collapsed) {
            // 折叠状态
            emptyState.classList.add('collapsed');
            collapseButton.classList.add('collapsed');
            collapseButton.innerHTML = '▶';
        } else {
            // 展开状态
            emptyState.classList.remove('collapsed');
            collapseButton.classList.remove('collapsed');
            collapseButton.innerHTML = '▼';
        }

        // 保存状态
        saveCollapsedState(collapsed);
    };

    // 应用初始状态
    updateCollapseState(isCollapsed);

    // 点击事件处理
    collapseButton.addEventListener('click', (e) => {
        e.stopPropagation();
        // 如果是触摸设备刚刚处理过，则忽略click事件
        if (touchProcessed) {
            touchProcessed = false; // 重置标志
            return;
        }
        updateCollapseState(!isCollapsed);
    });

    // 悬停效果
    collapseButton.addEventListener('mouseenter', () => {
        collapseButton.style.background = 'rgba(100, 116, 139, 0.6)';
        collapseButton.style.transform = 'scale(1.1)';
    });

    collapseButton.addEventListener('mouseleave', () => {
        collapseButton.style.background = isCollapsed ?
            'rgba(100, 116, 139, 0.5)' : 'rgba(100, 116, 139, 0.3)';
        collapseButton.style.transform = 'scale(1)';
    });

    // 触摸设备优化
    collapseButton.addEventListener('touchstart', (e) => {
        e.stopPropagation();
        // 阻止默认行为，防止后续click事件
        e.preventDefault();
        collapseButton.style.background = 'rgba(100, 116, 139, 0.7)';
        collapseButton.style.transform = 'scale(1.1)';
    }, { passive: false });

    collapseButton.addEventListener('touchend', (e) => {
        e.stopPropagation();
        // 阻止click事件的触发
        e.preventDefault();

        // 设置标志，阻止后续的click事件
        touchProcessed = true;

        updateCollapseState(!isCollapsed);
        collapseButton.style.background = isCollapsed ?
            'rgba(100, 116, 139, 0.5)' : 'rgba(100, 116, 139, 0.3)';
        collapseButton.style.transform = 'scale(1)';

        // 短时间后重置标志，允许后续的点击操作
        setTimeout(() => {
            touchProcessed = false;
        }, 100);
    }, { passive: false });
};
