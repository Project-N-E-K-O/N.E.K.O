/**
 * VRM UI Popup - 弹出框组件（功能同步修复版）
 */

// 创建弹出框
VRMManager.prototype.createPopup = function (buttonId) {
    const popup = document.createElement('div');
    popup.id = `vrm-popup-${buttonId}`;
    popup.className = 'vrm-popup';

    Object.assign(popup.style, {
        position: 'absolute',
        left: '100%',
        top: '0',
        marginLeft: '8px',
        zIndex: '100000',
        background: 'rgba(255, 255, 255, 0.65)',
        backdropFilter: 'saturate(180%) blur(20px)',
        border: '1px solid rgba(255, 255, 255, 0.18)',
        borderRadius: '8px',
        padding: '8px',
        boxShadow: '0 2px 4px rgba(0, 0, 0, 0.04), 0 8px 16px rgba(0, 0, 0, 0.08), 0 16px 32px rgba(0, 0, 0, 0.04)',
        display: 'none',
        flexDirection: 'column',
        gap: '6px',
        minWidth: '180px',
        maxHeight: '200px',
        overflowY: 'auto',
        pointerEvents: 'auto',
        opacity: '0',
        transform: 'translateX(-10px)',
        transition: 'opacity 0.2s cubic-bezier(0.1, 0.9, 0.2, 1), transform 0.2s cubic-bezier(0.1, 0.9, 0.2, 1)'
    });

    const stopEventPropagation = (e) => { e.stopPropagation(); };
    ['pointerdown','pointermove','pointerup','mousedown','mousemove','mouseup','touchstart','touchmove','touchend'].forEach(evt => {
        popup.addEventListener(evt, stopEventPropagation, true);
    });

    if (buttonId === 'mic') {
        popup.id = 'vrm-popup-mic';
        popup.setAttribute('data-legacy-id', 'vrm-mic-popup');
    } else if (buttonId === 'agent') {
        this._createAgentPopupContent(popup);
    } else if (buttonId === 'settings') {
        // 设置菜单移除高度限制和滚动条，让所有内容直接展示
        popup.style.maxHeight = 'none';
        popup.style.overflowY = 'visible';
        this._createSettingsPopupContent(popup);
    }

    return popup;
};

// 创建Agent弹出框内容
VRMManager.prototype._createAgentPopupContent = function (popup) {
    const statusDiv = document.createElement('div');
    statusDiv.id = 'vrm-agent-status';
    Object.assign(statusDiv.style, {
        fontSize: '12px', color: '#44b7fe', padding: '6px 8px', borderRadius: '4px',
        background: 'rgba(68, 183, 254, 0.05)', marginBottom: '8px', minHeight: '20px', textAlign: 'center'
    });
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
    // 先添加 Focus 模式、主动搭话和自主视觉开关（在最上面），与Live2D保持一致
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

    // 手机仅保留开关；桌面端追加导航菜单
    const isMobileWidth = () => window.innerWidth <= 768;
    if (!isMobileWidth()) {
        // 添加分隔线
        const separator = document.createElement('div');
        Object.assign(separator.style, {
            height: '1px',
            background: 'rgba(0,0,0,0.1)',
            margin: '4px 0'
        });
        popup.appendChild(separator);

        // 然后添加导航菜单项
        this._createSettingsMenuItems(popup);
    }
};

// 创建Agent开关项
VRMManager.prototype._createToggleItem = function (toggle, popup) {
    const toggleItem = document.createElement('div');
    Object.assign(toggleItem.style, {
        display: 'flex', alignItems: 'center', gap: '8px', padding: '6px 8px', cursor: 'pointer',
        borderRadius: '6px', transition: 'background 0.2s ease, opacity 0.2s ease', fontSize: '13px',
        whiteSpace: 'nowrap', opacity: toggle.initialDisabled ? '0.5' : '1'
    });

    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.id = `vrm-${toggle.id}`;
    checkbox.style.display = 'none';

    if (toggle.initialDisabled) {
        checkbox.disabled = true;
        checkbox.title = window.t ? window.t('settings.toggles.checking') : '查询中...';
        toggleItem.style.cursor = 'default';
    }

    const indicator = document.createElement('div');
    Object.assign(indicator.style, {
        width: '20px', height: '20px', borderRadius: '50%', border: '2px solid #ccc',
        backgroundColor: 'transparent', cursor: 'pointer', flexShrink: '0', transition: 'all 0.2s ease',
        position: 'relative', display: 'flex', alignItems: 'center', justifyContent: 'center'
    });

    const checkmark = document.createElement('div');
    checkmark.innerHTML = '✓';
    Object.assign(checkmark.style, {
        color: '#fff', fontSize: '13px', fontWeight: 'bold', lineHeight: '1', opacity: '0',
        transition: 'opacity 0.2s ease', pointerEvents: 'none', userSelect: 'none'
    });
    indicator.appendChild(checkmark);

    const label = document.createElement('label');
    label.innerText = toggle.label;
    if (toggle.labelKey) label.setAttribute('data-i18n', toggle.labelKey);
    label.htmlFor = `vrm-${toggle.id}`;
    Object.assign(label.style, { cursor: 'pointer', userSelect: 'none', fontSize: '13px', color: '#333' });

    const updateStyle = () => {
        if (checkbox.checked) {
            indicator.style.backgroundColor = '#44b7fe'; indicator.style.borderColor = '#44b7fe'; checkmark.style.opacity = '1';
        } else {
            indicator.style.backgroundColor = 'transparent'; indicator.style.borderColor = '#ccc'; checkmark.style.opacity = '0';
        }
    };

    checkbox.addEventListener('change', updateStyle);
    updateStyle();

    toggleItem.appendChild(checkbox); toggleItem.appendChild(indicator); toggleItem.appendChild(label);
    
    // 鼠标悬停
    toggleItem.addEventListener('mouseenter', () => {
        if (!checkbox.disabled) toggleItem.style.background = 'rgba(68, 183, 254, 0.1)';
    });
    toggleItem.addEventListener('mouseleave', () => toggleItem.style.background = 'transparent');

    const handleToggle = (e) => {
        if (checkbox.disabled) return;
        if (checkbox._processing) {
            if (Date.now() - (checkbox._processingTime || 0) < 500) { e?.preventDefault(); return; }
        }
        checkbox._processing = true; checkbox._processingTime = Date.now();
        checkbox.checked = !checkbox.checked;
        checkbox.dispatchEvent(new Event('change', { bubbles: true }));
        updateStyle();
        setTimeout(() => checkbox._processing = false, 5500);
        e?.preventDefault(); e?.stopPropagation();
    };

    [toggleItem, indicator, label].forEach(el => el.addEventListener('click', (e) => {
        if (e.target !== checkbox) handleToggle(e);
    }));

    return toggleItem;
};

// 创建设置开关项
VRMManager.prototype._createSettingsToggleItem = function (toggle, popup) {
    const toggleItem = document.createElement('div');
    Object.assign(toggleItem.style, {
        display: 'flex', alignItems: 'center', gap: '8px', padding: '8px 12px', cursor: 'pointer',
        borderRadius: '6px', transition: 'background 0.2s ease', fontSize: '13px', whiteSpace: 'nowrap',
        borderBottom: '1px solid rgba(0,0,0,0.05)'
    });

    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.id = `vrm-${toggle.id}`;
    checkbox.style.display = 'none';

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
    Object.assign(indicator.style, {
        width: '20px', height: '20px', borderRadius: '50%', border: '2px solid #ccc',
        backgroundColor: 'transparent', cursor: 'pointer', flexShrink: '0', transition: 'all 0.2s ease',
        position: 'relative', display: 'flex', alignItems: 'center', justifyContent: 'center'
    });

    const checkmark = document.createElement('div');
    checkmark.innerHTML = '✓';
    Object.assign(checkmark.style, {
        color: '#fff', fontSize: '13px', fontWeight: 'bold', lineHeight: '1', opacity: '0',
        transition: 'opacity 0.2s ease', pointerEvents: 'none', userSelect: 'none'
    });
    indicator.appendChild(checkmark);

    const label = document.createElement('label');
    label.innerText = toggle.label;
    if (toggle.labelKey) label.setAttribute('data-i18n', toggle.labelKey);
    label.htmlFor = `vrm-${toggle.id}`;
    Object.assign(label.style, { cursor: 'pointer', userSelect: 'none', fontSize: '13px', color: '#333', display: 'flex', alignItems: 'center', height: '20px' });

    const updateStyle = () => {
        if (checkbox.checked) {
            indicator.style.backgroundColor = '#44b7fe'; indicator.style.borderColor = '#44b7fe'; checkmark.style.opacity = '1';
            toggleItem.style.background = 'rgba(68, 183, 254, 0.1)';
        } else {
            indicator.style.backgroundColor = 'transparent'; indicator.style.borderColor = '#ccc'; checkmark.style.opacity = '0';
            toggleItem.style.background = 'transparent';
        }
    };
    updateStyle();

    toggleItem.appendChild(checkbox); toggleItem.appendChild(indicator); toggleItem.appendChild(label);

    toggleItem.addEventListener('mouseenter', () => { if(checkbox.checked) toggleItem.style.background = 'rgba(68, 183, 254, 0.15)'; else toggleItem.style.background = 'rgba(68, 183, 254, 0.08)'; });
    toggleItem.addEventListener('mouseleave', updateStyle);

    // 🔥【新增】合并消息的处理逻辑
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
                isChecked ? (window.resetProactiveChatBackoff && window.resetProactiveChatBackoff()) : (window.stopProactiveChatSchedule && window.stopProactiveChatSchedule());
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
        Object.assign(menuItem.style, { display: 'flex', alignItems: 'center', gap: '8px', padding: '8px 12px', cursor: 'pointer', borderRadius: '6px', transition: 'background 0.2s ease', fontSize: '13px', whiteSpace: 'nowrap', color: '#333' });

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
                    if (window.closeAllSettingsWindows) window.closeAllSettingsWindows();
                    window.location.href = finalUrl;
                } else if (item.id === 'voice-clone' && item.url) {
                    const lanlanName = (window.lanlan_config && window.lanlan_config.lanlan_name) || '';
                    finalUrl = `${item.url}?lanlan_name=${encodeURIComponent(lanlanName)}`;
                    if (this._openSettingsWindows[finalUrl] && !this._openSettingsWindows[finalUrl].closed) {
                        this._openSettingsWindows[finalUrl].focus(); return;
                    }
                    this.closeAllSettingsWindows();
                    this._openSettingsWindows[finalUrl] = window.open(finalUrl, '_blank', 'width=1000,height=800,menubar=no,toolbar=no,location=no,status=no');
                } else {
                    if (this._openSettingsWindows[finalUrl] && !this._openSettingsWindows[finalUrl].closed) {
                        this._openSettingsWindows[finalUrl].focus(); return;
                    }
                    this.closeAllSettingsWindows();
                    const newWindow = window.open(finalUrl, '_blank', 'width=1000,height=800,menubar=no,toolbar=no,location=no,status=no');
                    if(newWindow) {
                        this._openSettingsWindows[finalUrl] = newWindow;
                        const checkClosed = setInterval(() => { if(newWindow.closed) { delete this._openSettingsWindows[finalUrl]; clearInterval(checkClosed); } }, 500);
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

    const buttonEntry = this._floatingButtons && this._floatingButtons[buttonId];
    if (buttonEntry && buttonEntry.button) {
        buttonEntry.button.dataset.active = 'false';
        buttonEntry.button.style.background = 'rgba(255, 255, 255, 0.65)';
        if (buttonEntry.imgOff && buttonEntry.imgOn) {
            buttonEntry.imgOff.style.opacity = '1'; buttonEntry.imgOn.style.opacity = '0';
        }
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
    Object.keys(this._openSettingsWindows).forEach(url => {
        if (exceptUrl && url === exceptUrl) return;
        try { if (this._openSettingsWindows[url] && !this._openSettingsWindows[url].closed) this._openSettingsWindows[url].close(); } catch (_) {}
        delete this._openSettingsWindows[url];
    });
};

// 显示弹出框
VRMManager.prototype.showPopup = function (buttonId, popup) {
    const isVisible = popup.style.display === 'flex' && popup.style.opacity === '1';

    // 如果是设置弹出框，每次显示时更新开关状态
    if (buttonId === 'settings') {
        const updateCheckboxStyle = (checkbox) => {
            if (!checkbox) return;
            const toggleItem = checkbox.parentElement;
            const indicator = toggleItem.children[1];
            const checkmark = indicator.firstElementChild;
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
    }

    if (buttonId === 'agent' && !isVisible) window.dispatchEvent(new CustomEvent('live2d-agent-popup-opening'));

    if (isVisible) {
        popup.style.opacity = '0'; popup.style.transform = 'translateX(-10px)';
        if (buttonId === 'agent') window.dispatchEvent(new CustomEvent('live2d-agent-popup-closed'));
        setTimeout(() => { popup.style.display = 'none'; popup.style.left = '100%'; popup.style.top = '0'; }, 200);
    } else {
        this.closeAllPopupsExcept(buttonId);
        popup.style.display = 'flex'; popup.style.opacity = '0'; popup.style.visibility = 'visible';
        
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
// 【新增】VRM 专用的麦克风列表渲染函数
VRMManager.prototype.renderMicList = async function (popup) {
    if (!popup) return;
    popup.innerHTML = ''; // 清空现有内容

    const t = window.t || ((k, opt) => k); // 简单的 i18n 兼容

    try {
        // 获取权限
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        stream.getTracks().forEach(t => t.stop()); // 立即释放

        // 获取设备列表
        const devices = await navigator.mediaDevices.enumerateDevices();
        const audioInputs = devices.filter(device => device.kind === 'audioinput');

        if (audioInputs.length === 0) {
            const noDev = document.createElement('div');
            noDev.textContent = '未检测到麦克风';
            Object.assign(noDev.style, { padding:'8px', fontSize:'13px', color:'#666' });
            popup.appendChild(noDev);
            return;
        }

        // 渲染列表逻辑（复用 app.js 风格）
        // 1. 默认设备
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
                // 调用 app.js 里定义的全局函数来切换设备（如果存在）
                // 因为 app.js 并没有把 selectMicrophone 暴露给 window，这里我们暂时无法直接调用
                // 但通常我们会通过 fetch 发送给后端
                if (deviceId) {
                    try {
                        await fetch('/api/characters/set_microphone', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ microphone_id: deviceId })
                        });
                        // 刷新页面或提示
                        if (window.showStatusToast) window.showStatusToast('已切换麦克风 (下一次录音生效)', 2000);
                    } catch(e) { console.error(e); }
                }
            });
            popup.appendChild(btn);
        };

        // 添加列表
        audioInputs.forEach((device, index) => {
            addOption(device.label || `麦克风 ${index + 1}`, device.deviceId);
        });

    } catch (e) {
        console.error('获取麦克风失败', e);
        const errDiv = document.createElement('div');
        errDiv.textContent = '无法访问麦克风';
        popup.appendChild(errDiv);
    }
};

// 【新增】VRM 专用的屏幕源列表渲染函数
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

        // 创建网格容器
        const createGridContainer = () => {
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
        };

        // 创建屏幕源选项元素
        const createSourceOption = (source) => {
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
                thumb.src = source.thumbnail;
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
        };

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

            const screenGrid = createGridContainer();
            screens.forEach(source => {
                screenGrid.appendChild(createSourceOption(source));
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

            const windowGrid = createGridContainer();
            windows.forEach(source => {
                windowGrid.appendChild(createSourceOption(source));
            });
            popup.appendChild(windowGrid);
        }

    } catch (e) {
        console.error('[VRM] 获取屏幕源失败', e);
        popup.innerHTML = '';
        const errDiv = document.createElement('div');
        errDiv.textContent = '获取屏幕源失败';
        Object.assign(errDiv.style, { padding:'12px', fontSize:'13px', color:'#dc3545', textAlign:'center' });
        popup.appendChild(errDiv);
    }
};

console.log('[VRM] VRM UI Popup 模块已加载');