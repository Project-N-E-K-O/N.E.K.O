/**
 * VRM UI Popup - 弹出框组件（功能同步修复版）
 */

// 注入 CSS 样式（如果尚未注入）
(function() {
    if (document.getElementById('vrm-popup-styles')) return;
    const style = document.createElement('style');
    style.id = 'vrm-popup-styles';
    style.textContent = `
        .vrm-popup {
            position: absolute;
            left: 100%;
            top: 0;
            margin-left: 8px;
            z-index: 100001;
            background: rgba(255, 255, 255, 0.65);
            backdrop-filter: saturate(180%) blur(20px);
            border: 1px solid rgba(255, 255, 255, 0.18);
            border-radius: 8px;
            padding: 8px;
            box-shadow: 0 2px 4px rgba(0, 0, 0, 0.04), 0 8px 16px rgba(0, 0, 0, 0.08), 0 16px 32px rgba(0, 0, 0, 0.04);
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
            outline: 2px solid #44b7fe;
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
            border: 2px solid #ccc;
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
            background-color: #44b7fe;
            border-color: #44b7fe;
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
            color: #333;
        }
        .vrm-toggle-item:hover:not([aria-disabled="true"]) {
            background: rgba(68, 183, 254, 0.1);
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
            color: #333;
            pointer-events: auto !important;
            position: relative;
            z-index: 100002;
        }
        .vrm-settings-menu-item:hover {
            background: rgba(68, 183, 254, 0.1);
        }
        .vrm-settings-separator {
            height: 1px;
            background: rgba(0,0,0,0.1);
            margin: 4px 0;
        }
        .vrm-agent-status {
            font-size: 12px;
            color: #44b7fe;
            padding: 6px 8px;
            border-radius: 4px;
            background: rgba(68, 183, 254, 0.05);
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
    ['pointerdown','pointermove','pointerup','mousedown','mousemove','mouseup','touchstart','touchmove','touchend'].forEach(evt => {
        popup.addEventListener(evt, stopEventPropagation, true);
    });

    if (buttonId === 'mic') {
        popup.setAttribute('data-legacy-id', 'vrm-mic-popup');
    } else if (buttonId === 'agent') {
        this._createAgentPopupContent(popup);
    } else     if (buttonId === 'settings') {
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
        { id: 'agent-mcp', label: window.t ? window.t('settings.toggles.mcpTools') : 'MCP工具', labelKey: 'settings.toggles.mcpTools', initialDisabled: true },
        { id: 'agent-user-plugin', label: window.t ? window.t('settings.toggles.userPlugin') : '用户插件', labelKey: 'settings.toggles.userPlugin', initialDisabled: true }
    ];

    agentToggles.forEach(toggle => {
        const toggleItem = this._createToggleItem(toggle, popup);
        popup.appendChild(toggleItem);
    });
};

// 创建设置弹出框内容
VRMManager.prototype._createSettingsPopupContent = function (popup) {
    // 添加开关项
    const settingsToggles = [
        { id: 'merge-messages', label: window.t ? window.t('settings.toggles.mergeMessages') : '合并消息', labelKey: 'settings.toggles.mergeMessages' },
        { id: 'focus-mode', label: window.t ? window.t('settings.toggles.allowInterrupt') : '允许打断', labelKey: 'settings.toggles.allowInterrupt', storageKey: 'focusModeEnabled', inverted: true }, // inverted表示值与focusModeEnabled相反
        { id: 'proactive-chat', label: window.t ? window.t('settings.toggles.proactiveChat') : '主动搭话', labelKey: 'settings.toggles.proactiveChat', storageKey: 'proactiveChatEnabled' },
        { id: 'proactive-vision', label: window.t ? window.t('settings.toggles.proactiveVision') : '自主视觉', labelKey: 'settings.toggles.proactiveVision', storageKey: 'proactiveVisionEnabled' }
    ];

    settingsToggles.forEach(toggle => {
        const toggleItem = this._createSettingsToggleItem(toggle, popup);
        popup.appendChild(toggleItem);
    });

    // 桌面端添加导航菜单
    if (!window.isMobileWidth()) {
        // 添加分隔线
        const separator = document.createElement('div');
        separator.className = 'vrm-settings-separator';
        popup.appendChild(separator);

        // 然后添加导航菜单项
        this._createSettingsMenuItems(popup);
    }
};

// 创建Agent开关项
VRMManager.prototype._createToggleItem = function (toggle, popup) {
    const toggleItem = document.createElement('div');
    toggleItem.className = 'vrm-toggle-item';
    toggleItem.setAttribute('role', 'switch');
    toggleItem.setAttribute('tabIndex', toggle.initialDisabled ? '-1' : '0');
    toggleItem.setAttribute('aria-checked', 'false');
    toggleItem.setAttribute('aria-disabled', toggle.initialDisabled ? 'true' : 'false');
    if (toggle.initialDisabled) {
        toggleItem.style.opacity = '0.5';
    }

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

    const updateStyle = () => {
        const isChecked = checkbox.checked;
        toggleItem.setAttribute('aria-checked', isChecked ? 'true' : 'false');
        indicator.setAttribute('aria-checked', isChecked ? 'true' : 'false');
    };

    checkbox.addEventListener('change', updateStyle);
    updateStyle();

    toggleItem.appendChild(checkbox); toggleItem.appendChild(indicator); toggleItem.appendChild(label);
    
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

// 创建设置开关项
VRMManager.prototype._createSettingsToggleItem = function (toggle, popup) {
    const toggleItem = document.createElement('div');
    toggleItem.className = 'vrm-toggle-item';
    toggleItem.setAttribute('role', 'switch');
    toggleItem.setAttribute('tabIndex', '0');
    toggleItem.setAttribute('aria-checked', 'false');
    toggleItem.style.padding = '8px 12px';
    toggleItem.style.borderBottom = '1px solid rgba(0,0,0,0.05)';

    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.id = `vrm-${toggle.id}`;
    checkbox.style.position = 'absolute';
    checkbox.style.opacity = '0';
    checkbox.style.width = '1px';
    checkbox.style.height = '1px';
    checkbox.style.overflow = 'hidden';
    checkbox.setAttribute('aria-hidden', 'true');

    // 初始化状态
    if (toggle.id === 'merge-messages' && typeof window.mergeMessagesEnabled !== 'undefined') {
        checkbox.checked = window.mergeMessagesEnabled;
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
    checkmark.innerHTML = '✓';
    indicator.appendChild(checkmark);

    const label = document.createElement('label');
    label.className = 'vrm-toggle-label';
    label.innerText = toggle.label;
    if (toggle.labelKey) label.setAttribute('data-i18n', toggle.labelKey);
    label.htmlFor = `vrm-${toggle.id}`;
    label.style.display = 'flex';
    label.style.alignItems = 'center';
    label.style.height = '20px';
    toggleItem.setAttribute('aria-label', toggle.label);

    const updateStyle = () => {
        const isChecked = checkbox.checked;
        toggleItem.setAttribute('aria-checked', isChecked ? 'true' : 'false');
        indicator.setAttribute('aria-checked', isChecked ? 'true' : 'false');
        if (isChecked) {
            toggleItem.style.background = 'rgba(68, 183, 254, 0.1)';
        } else {
            toggleItem.style.background = 'transparent';
        }
    };
    updateStyle();

    toggleItem.appendChild(checkbox); toggleItem.appendChild(indicator); toggleItem.appendChild(label);

    toggleItem.addEventListener('mouseenter', () => { if(checkbox.checked) toggleItem.style.background = 'rgba(68, 183, 254, 0.15)'; else toggleItem.style.background = 'rgba(68, 183, 254, 0.08)'; });
    toggleItem.addEventListener('mouseleave', updateStyle);

    // 键盘支持
    toggleItem.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            checkbox.checked = !checkbox.checked;
            handleToggleChange(checkbox.checked);
        }
    });

    const handleToggleChange = (isChecked) => {
        updateStyle();
        if (typeof window.saveNEKOSettings === 'function') {
            if (toggle.id === 'merge-messages') {
                window.mergeMessagesEnabled = isChecked;
                window.saveNEKOSettings();
            } else if (toggle.id === 'focus-mode') {
                window.focusModeEnabled = toggle.inverted ? !isChecked : isChecked;
                window.saveNEKOSettings();
            } else if (toggle.id === 'proactive-chat') {
                window.proactiveChatEnabled = isChecked;
                window.saveNEKOSettings();
                if (isChecked) {
                    window.resetProactiveChatBackoff && window.resetProactiveChatBackoff();
                } else {
                    window.stopProactiveChatSchedule && window.stopProactiveChatSchedule();
                }
            } else if (toggle.id === 'proactive-vision') {
                window.proactiveVisionEnabled = isChecked;
                window.saveNEKOSettings();
                if (isChecked) {
                    window.resetProactiveChatBackoff && window.resetProactiveChatBackoff();
                    if (window.isRecording && window.startProactiveVisionDuringSpeech) window.startProactiveVisionDuringSpeech();
                } else {
                    if (!window.proactiveChatEnabled && window.stopProactiveChatSchedule) window.stopProactiveChatSchedule();
                    window.stopProactiveVisionDuringSpeech && window.stopProactiveVisionDuringSpeech();
                }
            }
        }
    };

    checkbox.addEventListener('change', (e) => { e.stopPropagation(); handleToggleChange(checkbox.checked); });
    [toggleItem, indicator, label].forEach(el => el.addEventListener('click', (e) => {
        if(e.target !== checkbox) { e.preventDefault(); e.stopPropagation(); checkbox.checked = !checkbox.checked; handleToggleChange(checkbox.checked); }
    }));

    return toggleItem;
};

// 创建设置菜单项 (保持与Live2D一致)
VRMManager.prototype._createSettingsMenuItems = function (popup) {
    const settingsItems = [
        { id: 'vrm-manage', label: window.t ? window.t('settings.menu.modelSettings') : '模型管理', labelKey: 'settings.menu.modelSettings', icon: '/static/icons/live2d_settings_icon.png', action: 'navigate', urlBase: '/model_manager' },
        { id: 'api-keys', label: window.t ? window.t('settings.menu.apiKeys') : 'API密钥', labelKey: 'settings.menu.apiKeys', icon: '/static/icons/api_key_icon.png', action: 'navigate', url: '/api_key' },
        { id: 'character', label: window.t ? window.t('settings.menu.characterManage') : '角色管理', labelKey: 'settings.menu.characterManage', icon: '/static/icons/character_icon.png', action: 'navigate', url: '/chara_manager' },
        { id: 'voice-clone', label: window.t ? window.t('settings.menu.voiceClone') : '声音克隆', labelKey: 'settings.menu.voiceClone', icon: '/static/icons/voice_clone_icon.png', action: 'navigate', url: '/voice_clone' },
        { id: 'memory', label: window.t ? window.t('settings.menu.memoryBrowser') : '记忆浏览', labelKey: 'settings.menu.memoryBrowser', icon: '/static/icons/memory_icon.png', action: 'navigate', url: '/memory_browser' },
        { id: 'steam-workshop', label: window.t ? window.t('settings.menu.steamWorkshop') : '创意工坊', labelKey: 'settings.menu.steamWorkshop', icon: '/static/icons/Steam_icon_logo.png', action: 'navigate', url: '/steam_workshop_manager' },
    ];

    settingsItems.forEach(item => {
        const menuItem = document.createElement('div');
        menuItem.className = 'vrm-settings-menu-item';

        if (item.icon) {
            const iconImg = document.createElement('img'); iconImg.src = item.icon; iconImg.alt = item.label;
            Object.assign(iconImg.style, { width: '24px', height: '24px', objectFit: 'contain', flexShrink: '0' });
            menuItem.appendChild(iconImg);
        }
        const labelText = document.createElement('span'); labelText.textContent = item.label;
        if (item.labelKey) labelText.setAttribute('data-i18n', item.labelKey);
        Object.assign(labelText.style, { display: 'flex', alignItems: 'center', lineHeight: '1', height: '24px' });
        menuItem.appendChild(labelText);

        menuItem.addEventListener('mouseenter', () => menuItem.style.background = 'rgba(68, 183, 254, 0.1)');
        menuItem.addEventListener('mouseleave', () => menuItem.style.background = 'transparent');

        menuItem.addEventListener('click', (e) => {
            e.stopPropagation();
            if (item.action === 'navigate') {
                this._openSettingsWindows = this._openSettingsWindows || {};
                let finalUrl = item.url || item.urlBase;
                if (item.id === 'vrm-manage' && item.urlBase) {
                    const lanlanName = (window.lanlan_config && window.lanlan_config.lanlan_name) || '';
                    finalUrl = `${item.urlBase}?lanlan_name=${encodeURIComponent(lanlanName)}`;
                    if (typeof this.closeAllSettingsWindows === 'function') {
                        this.closeAllSettingsWindows();
                    }
                    window.location.href = finalUrl;
                } else if (item.id === 'voice-clone' && item.url) {
                    const lanlanName = (window.lanlan_config && window.lanlan_config.lanlan_name) || '';
                    finalUrl = `${item.url}?lanlan_name=${encodeURIComponent(lanlanName)}`;
                    if (this._openSettingsWindows[finalUrl] && !this._openSettingsWindows[finalUrl].closed) {
                        this._openSettingsWindows[finalUrl].focus(); return;
                    }
                    if (typeof this.closeAllSettingsWindows === 'function') {
                        this.closeAllSettingsWindows();
                    }
                    const newWindow = window.open(finalUrl, '_blank', 'width=1000,height=800,menubar=no,toolbar=no,location=no,status=no,noopener');
                    if (newWindow) {
                        newWindow.opener = null;
                        this._openSettingsWindows[finalUrl] = newWindow;
                    }
                } else {
                    if (this._openSettingsWindows[finalUrl] && !this._openSettingsWindows[finalUrl].closed) {
                        this._openSettingsWindows[finalUrl].focus(); return;
                    }
                    if (typeof this.closeAllSettingsWindows === 'function') {
                        this.closeAllSettingsWindows();
                    }
                    const newWindow = window.open(finalUrl, '_blank', 'width=1000,height=800,menubar=no,toolbar=no,location=no,status=no,noopener');
                    if(newWindow) {
                        newWindow.opener = null;
                        this._openSettingsWindows[finalUrl] = newWindow;
                        this._windowCheckTimers = this._windowCheckTimers || {};
                        
                        // 清理同一 URL 的旧定时器，避免轮询累积
                        if (this._windowCheckTimers[finalUrl]) {
                            clearTimeout(this._windowCheckTimers[finalUrl]);
                            delete this._windowCheckTimers[finalUrl];
                        }
                        
                        const checkClosed = () => {
                            if (newWindow.closed) {
                                delete this._openSettingsWindows[finalUrl];
                                if (this._windowCheckTimers[finalUrl]) {
                                    clearTimeout(this._windowCheckTimers[finalUrl]);
                                    delete this._windowCheckTimers[finalUrl];
                                }
                            } else {
                                const timerId = setTimeout(checkClosed, 500);
                                this._windowCheckTimers[finalUrl] = timerId;
                            }
                        };
                        const timerId = setTimeout(checkClosed, 500);
                        this._windowCheckTimers[finalUrl] = timerId;
                    }
                }
            }
        });
        popup.appendChild(menuItem);
    });
};

// 辅助方法：关闭弹窗
VRMManager.prototype.closePopupById = function (buttonId) {
    if (!buttonId) return false;
    const popup = document.getElementById(`vrm-popup-${buttonId}`);
    if (!popup || popup.style.display !== 'flex') return false;

    if (buttonId === 'agent') window.dispatchEvent(new CustomEvent('live2d-agent-popup-closed'));

    popup.style.opacity = '0'; popup.style.transform = 'translateX(-10px)';
    setTimeout(() => popup.style.display = 'none', 200);

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
        try { if (this._openSettingsWindows[url] && !this._openSettingsWindows[url].closed) this._openSettingsWindows[url].close(); } catch (_) {}
        delete this._openSettingsWindows[url];
    });
};

// 显示弹出框
VRMManager.prototype.showPopup = function (buttonId, popup) {
    // 使用 display === 'flex' 判断弹窗是否可见（避免动画中误判）
    const isVisible = popup.style.display === 'flex';

    // 如果是设置弹出框，每次显示时更新开关状态
    if (buttonId === 'settings') {
        const updateCheckboxStyle = (checkbox) => {
            if (!checkbox) return;
            const toggleItem = checkbox.parentElement;
            // 使用 class 选择器查找元素，避免依赖 DOM 结构顺序
            const indicator = toggleItem?.querySelector('.vrm-toggle-indicator');
            const checkmark = indicator?.querySelector('.vrm-toggle-checkmark');
            if (!indicator || !checkmark) {
                console.warn('[VRM UI Popup] 无法找到 toggle indicator 或 checkmark 元素');
                return;
            }
            if (checkbox.checked) {
                indicator.style.backgroundColor = '#44b7fe'; indicator.style.borderColor = '#44b7fe'; checkmark.style.opacity = '1'; toggleItem.style.background = 'rgba(68, 183, 254, 0.1)';
            } else {
                indicator.style.backgroundColor = 'transparent'; indicator.style.borderColor = '#ccc'; checkmark.style.opacity = '0'; toggleItem.style.background = 'transparent';
            }
        };

        const mergeCheckbox = popup.querySelector('#vrm-merge-messages');
        if (mergeCheckbox && typeof window.mergeMessagesEnabled !== 'undefined') {
            mergeCheckbox.checked = window.mergeMessagesEnabled; updateCheckboxStyle(mergeCheckbox);
        }

        const focusCheckbox = popup.querySelector('#vrm-focus-mode');
        if (focusCheckbox && typeof window.focusModeEnabled !== 'undefined') {
            focusCheckbox.checked = !window.focusModeEnabled; updateCheckboxStyle(focusCheckbox);
        }
        
        const proactiveChatCheckbox = popup.querySelector('#vrm-proactive-chat');
        if (proactiveChatCheckbox && typeof window.proactiveChatEnabled !== 'undefined') {
            proactiveChatCheckbox.checked = window.proactiveChatEnabled; updateCheckboxStyle(proactiveChatCheckbox);
        }
        
        const proactiveVisionCheckbox = popup.querySelector('#vrm-proactive-vision');
        if (proactiveVisionCheckbox && typeof window.proactiveVisionEnabled !== 'undefined') {
            proactiveVisionCheckbox.checked = window.proactiveVisionEnabled; updateCheckboxStyle(proactiveVisionCheckbox);
        }
    }

    if (buttonId === 'agent' && !isVisible) window.dispatchEvent(new CustomEvent('live2d-agent-popup-opening'));

    if (isVisible) {
        popup.style.opacity = '0'; popup.style.transform = 'translateX(-10px)';
        if (buttonId === 'agent') window.dispatchEvent(new CustomEvent('live2d-agent-popup-closed'));
        
        // 更新按钮状态为关闭
        if (typeof this.setButtonActive === 'function') {
            this.setButtonActive(buttonId, false);
        }
        
        // 存储 timeout ID，以便在快速重新打开时能够清除
        const hideTimeoutId = setTimeout(() => { 
            popup.style.display = 'none'; 
            popup.style.left = '100%'; 
            popup.style.top = '0';
            // 清除 timeout ID 引用
            popup._hideTimeoutId = null;
        }, 200);
        popup._hideTimeoutId = hideTimeoutId;
    } else {
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
            void popup.offsetHeight;
            requestAnimationFrame(() => {
                const popupRect = popup.getBoundingClientRect();
                const screenWidth = window.innerWidth;
                const screenHeight = window.innerHeight;
                if (popupRect.right > screenWidth - 20) {
                    const button = document.getElementById(`vrm-btn-${buttonId}`);
                    const buttonWidth = button ? button.offsetWidth : 48;
                    popup.style.left = 'auto'; popup.style.right = '0'; popup.style.marginLeft = '0'; popup.style.marginRight = `${buttonWidth + 8}px`;
                }
                if (buttonId === 'settings' || buttonId === 'agent') {
                    if (popupRect.bottom > screenHeight - 60) {
                        popup.style.top = `${parseInt(popup.style.top || 0) - (popupRect.bottom - (screenHeight - 60))}px`;
                    }
                }
                popup.style.visibility = 'visible'; popup.style.opacity = '1'; popup.style.transform = 'translateX(0)';
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
            Object.assign(noDev.style, { padding:'8px', fontSize:'13px', color:'#666' });
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
                color: '#333'
            });
            
            // 选中高亮逻辑（简单模拟）
            btn.addEventListener('mouseenter', () => btn.style.background = 'rgba(68, 183, 254, 0.1)');
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
                                } catch {}
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
                    } catch(e) {
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
            border: '1px solid #ddd',
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
            background: '#f5f5f5',
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
        color: '#333',
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
        option.style.background = 'rgba(68, 183, 254, 0.1)';
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
        Object.assign(notAvailableItem.style, { padding:'12px', fontSize:'13px', color:'#666', textAlign:'center' });
        popup.appendChild(notAvailableItem);
        return;
    }

    try {
        // 显示加载中
        const loadingItem = document.createElement('div');
        loadingItem.textContent = t('app.screenSource.loading') || '加载中...';
        Object.assign(loadingItem.style, { padding:'12px', fontSize:'13px', color:'#666', textAlign:'center' });
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
            Object.assign(noSourcesItem.style, { padding:'12px', fontSize:'13px', color:'#666', textAlign:'center' });
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
                color: '#666',
                borderBottom: '1px solid #eee',
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
                color: '#666',
                borderBottom: '1px solid #eee',
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
        Object.assign(errDiv.style, { padding:'12px', fontSize:'13px', color:'#dc3545', textAlign:'center' });
        popup.appendChild(errDiv);
    }
};
