/**
 * Live2D UI Popup - 弹出框组件
 * 包含弹出框创建、设置菜单、开关项组件
 */

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
        background: 'rgba(255, 255, 255, 0.65)',  // Fluent Acrylic
        backdropFilter: 'saturate(180%) blur(20px)',  // Fluent 标准模糊
        border: '1px solid rgba(255, 255, 255, 0.18)',  // 微妙高光边框
        borderRadius: '8px',  // Fluent 标准圆角
        padding: '8px',
        boxShadow: '0 2px 4px rgba(0, 0, 0, 0.04), 0 8px 16px rgba(0, 0, 0, 0.08), 0 16px 32px rgba(0, 0, 0, 0.04)',  // Fluent 多层阴影
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
        this._createAgentPopupContent(popup);
    } else if (buttonId === 'settings') {
        // 设置菜单
        this._createSettingsPopupContent(popup);
    }

    return popup;
};

// 创建设置弹出框内容
Live2DManager.prototype._createSettingsPopupContent = function (popup) {
    // 先添加 Focus 模式、主动搭话和自主视觉开关（在最上面）
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

    // 手机仅保留两个开关；桌面端追加导航菜单
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

    // 创建自定义圆形指示器
    const indicator = document.createElement('div');
    Object.assign(indicator.style, {
        width: '20px',
        height: '20px',
        borderRadius: '50%',
        border: '2px solid #ccc',
        backgroundColor: 'transparent',
        cursor: 'pointer',
        flexShrink: '0',
        transition: 'all 0.2s ease',
        position: 'relative',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center'
    });

    // 创建对勾图标（初始隐藏）
    const checkmark = document.createElement('div');
    checkmark.innerHTML = '✓';
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

    const label = document.createElement('label');
    label.innerText = toggle.label;
    if (toggle.labelKey) {
        label.setAttribute('data-i18n', toggle.labelKey);
    }
    label.htmlFor = `live2d-${toggle.id}`;
    label.style.cursor = 'pointer';
    label.style.userSelect = 'none';
    label.style.fontSize = '13px';
    label.style.color = '#333';  // 文本始终为深灰色，不随选中状态改变

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
    const updateStyle = () => {
        if (checkbox.checked) {
            // 选中状态：蓝色填充，显示对勾
            indicator.style.backgroundColor = '#44b7fe';
            indicator.style.borderColor = '#44b7fe';
            checkmark.style.opacity = '1';
        } else {
            // 未选中状态：灰色边框，透明填充，隐藏对勾
            indicator.style.backgroundColor = 'transparent';
            indicator.style.borderColor = '#ccc';
            checkmark.style.opacity = '0';
        }
    };

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
            toggleItem.style.background = 'rgba(68, 183, 254, 0.1)';
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
    Object.assign(toggleItem.style, {
        display: 'flex',
        alignItems: 'center',
        gap: '8px',
        padding: '8px 12px',  // 统一padding，与下方菜单项一致
        cursor: 'pointer',
        borderRadius: '6px',
        transition: 'background 0.2s ease',
        fontSize: '13px',
        whiteSpace: 'nowrap',
        borderBottom: '1px solid rgba(0,0,0,0.05)'
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

    // 创建自定义圆形指示器
    const indicator = document.createElement('div');
    Object.assign(indicator.style, {
        width: '20px',  // 稍微增大，与下方图标更协调
        height: '20px',
        borderRadius: '50%',
        border: '2px solid #ccc',
        backgroundColor: 'transparent',
        cursor: 'pointer',
        flexShrink: '0',
        transition: 'all 0.2s ease',
        position: 'relative',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center'
    });

    // 创建对勾图标（初始隐藏）
    const checkmark = document.createElement('div');
    checkmark.innerHTML = '✓';
    Object.assign(checkmark.style, {
        color: '#fff',
        fontSize: '13px',  // 稍微增大，与指示器大小更协调
        fontWeight: 'bold',
        lineHeight: '1',
        opacity: '0',
        transition: 'opacity 0.2s ease',
        pointerEvents: 'none',
        userSelect: 'none'
    });
    indicator.appendChild(checkmark);

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
    label.style.color = '#333';  // 文本始终为深灰色，不随选中状态改变
    label.style.display = 'flex';
    label.style.alignItems = 'center';
    label.style.lineHeight = '1';
    label.style.height = '20px';  // 与指示器高度一致，确保垂直居中

    // 根据 checkbox 状态更新指示器颜色
    const updateStyle = () => {
        if (checkbox.checked) {
            // 选中状态：蓝色填充，显示对勾，背景颜色突出
            indicator.style.backgroundColor = '#44b7fe';
            indicator.style.borderColor = '#44b7fe';
            checkmark.style.opacity = '1';
            toggleItem.style.background = 'rgba(68, 183, 254, 0.1)';  // 浅蓝色背景
        } else {
            // 未选中状态：灰色边框，透明填充，隐藏对勾，无背景
            indicator.style.backgroundColor = 'transparent';
            indicator.style.borderColor = '#ccc';
            checkmark.style.opacity = '0';
            toggleItem.style.background = 'transparent';
        }
    };

    // 初始化样式（根据当前状态）
    updateStyle();

    toggleItem.appendChild(checkbox);
    toggleItem.appendChild(indicator);
    toggleItem.appendChild(label);

    toggleItem.addEventListener('mouseenter', () => {
        // 悬停效果
        if (checkbox.checked) {
            toggleItem.style.background = 'rgba(68, 183, 254, 0.15)';
        } else {
            toggleItem.style.background = 'rgba(68, 183, 254, 0.08)';
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
        { id: 'live2d-manage', label: window.t ? window.t('settings.menu.modelSettings') : '模型管理', labelKey: 'settings.menu.modelSettings', icon: '/static/icons/live2d_settings_icon.png', action: 'navigate', urlBase: '/model_manager' },
        { id: 'api-keys', label: window.t ? window.t('settings.menu.apiKeys') : 'API密钥', labelKey: 'settings.menu.apiKeys', icon: '/static/icons/api_key_icon.png', action: 'navigate', url: '/api_key' },
        { id: 'character', label: window.t ? window.t('settings.menu.characterManage') : '角色管理', labelKey: 'settings.menu.characterManage', icon: '/static/icons/character_icon.png', action: 'navigate', url: '/chara_manager' },
        { id: 'voice-clone', label: window.t ? window.t('settings.menu.voiceClone') : '声音克隆', labelKey: 'settings.menu.voiceClone', icon: '/static/icons/voice_clone_icon.png', action: 'navigate', url: '/voice_clone' },
        { id: 'memory', label: window.t ? window.t('settings.menu.memoryBrowser') : '记忆浏览', labelKey: 'settings.menu.memoryBrowser', icon: '/static/icons/memory_icon.png', action: 'navigate', url: '/memory_browser' },
        { id: 'steam-workshop', label: window.t ? window.t('settings.menu.steamWorkshop') : '创意工坊', labelKey: 'settings.menu.steamWorkshop', icon: '/static/icons/Steam_icon_logo.png', action: 'navigate', url: '/steam_workshop_manager' },
    ];

    settingsItems.forEach(item => {
        const menuItem = document.createElement('div');
        Object.assign(menuItem.style, {
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            padding: '8px 12px',
            cursor: 'pointer',
            borderRadius: '6px',
            transition: 'background 0.2s ease',
            fontSize: '13px',
            whiteSpace: 'nowrap',
            color: '#333'  // 文本颜色为深灰色
        });

        // 添加图标（如果有）
        if (item.icon) {
            const iconImg = document.createElement('img');
            iconImg.src = item.icon;
            iconImg.alt = item.label;
            Object.assign(iconImg.style, {
                width: '24px',
                height: '24px',
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
            height: '24px'  // 与图标高度一致，确保垂直居中
        });
        menuItem.appendChild(labelText);

        // 存储更新函数
        if (item.labelKey) {
            const updateLabelText = () => {
                if (window.t) {
                    labelText.textContent = window.t(item.labelKey);
                    // 同时更新图标 alt 属性
                    if (item.icon && menuItem.querySelector('img')) {
                        menuItem.querySelector('img').alt = window.t(item.labelKey);
                    }
                }
            };
            menuItem._updateLabelText = updateLabelText;
        }

        menuItem.addEventListener('mouseenter', () => {
            menuItem.style.background = 'rgba(68, 183, 254, 0.1)';
        });
        menuItem.addEventListener('mouseleave', () => {
            menuItem.style.background = 'transparent';
        });

        menuItem.addEventListener('click', (e) => {
            e.stopPropagation();
            if (item.action === 'navigate') {
                // 动态构建 URL（点击时才获取 lanlan_name）
                let finalUrl = item.url || item.urlBase;
                if (item.id === 'live2d-manage' && item.urlBase) {
                    // 从 window.lanlan_config 动态获取 lanlan_name
                    const lanlanName = (window.lanlan_config && window.lanlan_config.lanlan_name) || '';
                    finalUrl = `${item.urlBase}?lanlan_name=${encodeURIComponent(lanlanName)}`;
                    // 跳转前关闭所有弹窗
                    if (window.closeAllSettingsWindows) {
                        window.closeAllSettingsWindows();
                    }
                    // Live2D设置页直接跳转
                    window.location.href = finalUrl;
                } else if (item.id === 'voice-clone' && item.url) {
                    // 声音克隆页面也需要传递 lanlan_name
                    const lanlanName = (window.lanlan_config && window.lanlan_config.lanlan_name) || '';
                    finalUrl = `${item.url}?lanlan_name=${encodeURIComponent(lanlanName)}`;

                    // 检查是否已有该URL的窗口打开
                    if (this._openSettingsWindows[finalUrl]) {
                        const existingWindow = this._openSettingsWindows[finalUrl];
                        if (existingWindow && !existingWindow.closed) {
                            existingWindow.focus();
                            return;
                        } else {
                            delete this._openSettingsWindows[finalUrl];
                        }
                    }

                    // 打开新的弹窗前关闭其他已打开的设置窗口，实现全局互斥
                    this.closeAllSettingsWindows();

                    // 打开新窗口并保存引用
                    const newWindow = window.open(finalUrl, '_blank', 'width=1000,height=800,menubar=no,toolbar=no,location=no,status=no');
                    if (newWindow) {
                        this._openSettingsWindows[finalUrl] = newWindow;
                    }
                } else {
                    // 其他页面弹出新窗口，但检查是否已打开
                    // 检查是否已有该URL的窗口打开
                    if (this._openSettingsWindows[finalUrl]) {
                        const existingWindow = this._openSettingsWindows[finalUrl];
                        // 检查窗口是否仍然打开
                        if (existingWindow && !existingWindow.closed) {
                            // 聚焦到已存在的窗口
                            existingWindow.focus();
                            return;
                        } else {
                            // 窗口已关闭，清除引用
                            delete this._openSettingsWindows[finalUrl];
                        }
                    }

                    // 打开新的弹窗前关闭其他已打开的设置窗口，实现全局互斥
                    this.closeAllSettingsWindows();

                    // 打开新窗口并保存引用
                    const newWindow = window.open(finalUrl, '_blank', 'width=1000,height=800,menubar=no,toolbar=no,location=no,status=no');
                    if (newWindow) {
                        this._openSettingsWindows[finalUrl] = newWindow;

                        // 监听窗口关闭事件，清除引用并触发模型重新加载
                        const checkClosed = setInterval(() => {
                            if (newWindow.closed) {
                                delete this._openSettingsWindows[finalUrl];
                                clearInterval(checkClosed);
                                
                                // 窗口关闭后触发主窗口的模型重新加载
                                // 这是为了处理设置窗口可能修改了模型配置的情况
                                if (window.showMainUI) {
                                    console.log('[LivedUI] 设置窗口已关闭，触发模型检查和重新加载');
                                    window.showMainUI();
                                }
                            }
                        }, 500);
                    }
                }
            }
        });

        popup.appendChild(menuItem);
    });
};