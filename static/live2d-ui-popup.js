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
        { id: 'proactive-chat', label: window.t ? window.t('settings.toggles.proactiveChat') : '主动搭话', labelKey: 'settings.toggles.proactiveChat', storageKey: 'proactiveChatEnabled', hasInterval: true, intervalKey: 'proactiveChatInterval', defaultInterval: 30 },
        { id: 'proactive-vision', label: window.t ? window.t('settings.toggles.proactiveVision') : '自主视觉', labelKey: 'settings.toggles.proactiveVision', storageKey: 'proactiveVisionEnabled', hasInterval: true, intervalKey: 'proactiveVisionInterval', defaultInterval: 15 }
    ];

    settingsToggles.forEach(toggle => {
        const toggleItem = this._createSettingsToggleItem(toggle, popup);
        popup.appendChild(toggleItem);

        // 为带有时间间隔的开关添加间隔控件（可折叠）
        if (toggle.hasInterval) {
            const intervalControl = this._createIntervalControl(toggle);
            popup.appendChild(intervalControl);

            // 鼠标悬停时展开间隔控件
            toggleItem.addEventListener('mouseenter', () => {
                intervalControl._expand();
            });
            toggleItem.addEventListener('mouseleave', (e) => {
                // 如果鼠标移动到间隔控件上，不收缩
                if (!intervalControl.contains(e.relatedTarget)) {
                    intervalControl._collapse();
                }
            });
            intervalControl.addEventListener('mouseenter', () => {
                intervalControl._expand();
            });
            intervalControl.addEventListener('mouseleave', () => {
                intervalControl._collapse();
            });
        }
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

// 创建时间间隔控件（可折叠的滑动条）
Live2DManager.prototype._createIntervalControl = function (toggle) {
    const container = document.createElement('div');
    container.className = `live2d-interval-control-${toggle.id}`;
    Object.assign(container.style, {
        display: 'none',  // 初始完全隐藏，不占用空间
        alignItems: 'center',
        gap: '2px',
        padding: '0 12px 0 44px',
        fontSize: '12px',
        color: '#666',
        height: '0',
        overflow: 'hidden',
        opacity: '0',
        transition: 'height 0.2s ease, opacity 0.2s ease, padding 0.2s ease'
    });

    // 间隔标签（包含"基础"提示，主动搭话会指数退避）
    const labelText = document.createElement('span');
    const labelKey = toggle.id === 'proactive-chat' ? 'settings.interval.chatIntervalBase' : 'settings.interval.visionInterval';
    const defaultLabel = toggle.id === 'proactive-chat' ? '基础间隔' : '读取间隔';
    labelText.textContent = window.t ? window.t(labelKey) : defaultLabel;
    labelText.setAttribute('data-i18n', labelKey);
    Object.assign(labelText.style, {
        flexShrink: '0',
        fontSize: '10px'
    });

    // 滑动条容器
    const sliderWrapper = document.createElement('div');
    Object.assign(sliderWrapper.style, {
        display: 'flex',
        alignItems: 'center',
        gap: '1px',
        flexShrink: '0'
    });

    // 滑动条
    const slider = document.createElement('input');
    slider.type = 'range';
    slider.id = `live2d-${toggle.id}-interval`;
    const minVal = toggle.id === 'proactive-chat' ? 10 : 5;
    slider.min = minVal;
    slider.max = '120';  // 最大120秒
    slider.step = '5';
    // 从 window 获取当前值
    let currentValue = typeof window[toggle.intervalKey] !== 'undefined'
        ? window[toggle.intervalKey]
        : toggle.defaultInterval;
    // 限制在新的最大值范围内
    if (currentValue > 120) currentValue = 120;
    slider.value = currentValue;
    Object.assign(slider.style, {
        width: '55px',
        height: '4px',
        cursor: 'pointer',
        accentColor: '#44b7fe'
    });

    // 数值显示
    const valueDisplay = document.createElement('span');
    valueDisplay.textContent = `${currentValue}s`;
    Object.assign(valueDisplay.style, {
        minWidth: '26px',
        textAlign: 'right',
        fontFamily: 'monospace',
        fontSize: '11px',
        flexShrink: '0'
    });

    // 滑动条变化时更新显示和保存设置
    slider.addEventListener('input', () => {
        const value = parseInt(slider.value, 10);
        valueDisplay.textContent = `${value}s`;
    });

    slider.addEventListener('change', () => {
        const value = parseInt(slider.value, 10);
        // 保存到 window 和 localStorage
        window[toggle.intervalKey] = value;
        if (typeof window.saveNEKOSettings === 'function') {
            window.saveNEKOSettings();
        }
        console.log(`${toggle.id} 间隔已设置为 ${value} 秒`);
    });

    // 阻止事件冒泡
    slider.addEventListener('click', (e) => e.stopPropagation());
    slider.addEventListener('mousedown', (e) => e.stopPropagation());

    sliderWrapper.appendChild(slider);
    sliderWrapper.appendChild(valueDisplay);
    container.appendChild(labelText);
    container.appendChild(sliderWrapper);

    // 存储展开/收缩方法供外部调用
    container._expand = () => {
        container.style.display = 'flex';
        // 使用 requestAnimationFrame 确保 display 变化后再触发动画
        requestAnimationFrame(() => {
            container.style.height = '24px';
            container.style.opacity = '1';
            container.style.padding = '4px 12px 8px 44px';
        });
    };
    container._collapse = () => {
        container.style.height = '0';
        container.style.opacity = '0';
        container.style.padding = '0 12px 0 44px';
        // 动画结束后隐藏
        setTimeout(() => {
            if (container.style.opacity === '0') {
                container.style.display = 'none';
            }
        }, 200);
    };

    return container;
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

            // 鼠标悬停展开/收缩
            menuItem.addEventListener('mouseenter', () => {
                submenuContainer._expand();
            });
            menuItem.addEventListener('mouseleave', (e) => {
                if (!submenuContainer.contains(e.relatedTarget)) {
                    submenuContainer._collapse();
                }
            });
            submenuContainer.addEventListener('mouseenter', () => {
                submenuContainer._expand();
            });
            submenuContainer.addEventListener('mouseleave', () => {
                submenuContainer._collapse();
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
        color: '#333'
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
        menuItem.style.background = 'rgba(68, 183, 254, 0.1)';
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
        }, 200);
    };

    return container;
};
