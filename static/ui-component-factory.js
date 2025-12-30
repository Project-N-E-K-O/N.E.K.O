/**
 * UI Component Factory - 共享组件工厂
 * 提供通用的 UI 组件创建方法，供 Live2D 和 VRM 共同使用
 * 实现"底层逻辑互通，表现层分离"的架构原则
 */

window.UIComponentFactory = {
    /**
     * 创建弹出框
     * @param {string} buttonId - 按钮ID（'mic', 'agent', 'settings'）
     * @param {object} context - 上下文对象（Live2DManager 或 VRMManager）
     * @returns {HTMLElement} 弹出框元素
     */
    createPopup: function (buttonId, context) {
        const popup = document.createElement('div');
        popup.id = `shared-popup-${buttonId}`;
        popup.className = 'shared-popup';

        Object.assign(popup.style, {
            position: 'absolute',
            left: '100%',
            top: '0',
            marginLeft: '8px',
            zIndex: '100000',
            background: 'rgba(255,255,255,0.65)',
            backdropFilter: 'saturate(180%) blur(20px)',
            border: '1px solid rgba(255, 255, 255, 0.18)',
            borderRadius: '8px',
            padding: '8px',
            boxShadow: '0 2px 4px rgba(0, 0, 0, 0.04), 0 8px 16px rgba(0, 0, 0, 0.08), 0 16px 32px rgba(0, 0, 0, 0.04)',
            display: 'none',
            flexDirection: 'column',
            gap: '6px',
            minWidth: '180px',
            maxHeight: 'none',  // ✅ 改为 none，让内容自适应
            overflowY: 'visible',  // ✅ 改为 visible，不显示滚动条
            pointerEvents: 'auto',
            opacity: '0',
            transform: 'translateX(-10px)',
            transition: 'opacity 0.2s cubic-bezier(0.1, 0.9, 0.2, 1), transform 0.2s cubic-bezier(0.1, 0.9, 0.2, 1)'
        });

        // 阻止弹出菜单上的指针事件传播
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
            popup.id = 'shared-popup-mic';
            popup.setAttribute('data-legacy-id', 'live2d-mic-popup');
        } else if (buttonId === 'agent') {
            this.createAgentPopupContent(popup, context);
        } else if (buttonId === 'settings') {
            this.createSettingsPopupContent(popup, context);
        }

        return popup;
    },

    /**
     * 创建设置弹出框内容
     * @param {HTMLElement} popup - 弹出框元素
     * @param {object} context - 上下文对象
     */
    createSettingsPopupContent: function (popup, context) {
        // 添加 Focus 模式、主动搭话和自主视觉开关
        const settingsToggles = [
            { id: 'focus-mode', label: window.t ? window.t('settings.toggles.allowInterrupt') : '允许打断', labelKey: 'settings.toggles.allowInterrupt', storageKey: 'focusModeEnabled', inverted: true },
            { id: 'proactive-chat', label: window.t ? window.t('settings.toggles.proactiveChat') : '主动搭话', labelKey: 'settings.toggles.proactiveChat', storageKey: 'proactiveChatEnabled' },
            { id: 'proactive-vision', label: window.t ? window.t('settings.toggles.proactiveVision') : '自主视觉', labelKey: 'settings.toggles.proactiveVision', storageKey: 'proactiveVisionEnabled' }
        ];

        settingsToggles.forEach(toggle => {
            const toggleItem = this.createSettingsToggleItem(toggle, popup, context);
            popup.appendChild(toggleItem);
        });

        // 手机仅保留开关；桌面端追加导航菜单
        if (typeof isMobileWidth === 'function' && !isMobileWidth()) {
            // 添加分隔线
            const separator = document.createElement('div');
            Object.assign(separator.style, {
                height: '1px',
                background: 'rgba(0,0,0,0.1)',
                margin: '4px 0'
            });
            popup.appendChild(separator);

            // 添加导航菜单项
            this.createSettingsMenuItems(popup, context);
        }
    },

    /**
     * 创建 Agent 弹出框内容
     * @param {HTMLElement} popup - 弹出框元素
     * @param {object} context - 上下文对象
     */
    createAgentPopupContent: function (popup, context) {
        // 这里会调用 createToggleItem 创建 Agent 工具的开关
        // 具体的 Agent 配置会从外部传入或从全局获取
        console.log('[UIComponentFactory] Agent 面板内容创建（待实现具体逻辑）');
    },

    /**
     * 创建设置开关项
     * @param {object} toggle - 开关配置
     * @param {HTMLElement} popup - 父元素
     * @param {object} context - 上下文对象
     * @returns {HTMLElement} 开关项元素
     */
    createSettingsToggleItem: function (toggle, popup, context) {
        const toggleItem = document.createElement('div');
        Object.assign(toggleItem.style, {
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            padding: '8px 12px',
            cursor: 'pointer',
            borderRadius: '6px',
            transition: 'background 0.2s ease',
            fontSize: '13px',
            whiteSpace: 'nowrap',
            borderBottom: '1px solid rgba(0,0,0,0.05)'
        });

        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.id = `shared-${toggle.id}`;
        Object.assign(checkbox.style, {
            display: 'none'
        });

        // 从 window 获取当前状态
        if (toggle.id === 'focus-mode' && typeof window.focusModeEnabled !== 'undefined') {
            checkbox.checked = toggle.inverted ? !window.focusModeEnabled : window.focusModeEnabled;
        } else if (toggle.id === 'proactive-chat' && typeof window.proactiveChatEnabled !== 'undefined') {
            checkbox.checked = window.proactiveChatEnabled;
        } else if (toggle.id === 'proactive-vision' && typeof window.proactiveVisionEnabled !== 'undefined') {
            checkbox.checked = window.proactiveVisionEnabled;
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

        // 创建对勾图标
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
        label.htmlFor = `shared-${toggle.id}`;
        if (toggle.labelKey) {
            label.setAttribute('data-i18n', toggle.labelKey);
        }
        label.style.cursor = 'pointer';
        label.style.userSelect = 'none';
        label.style.fontSize = '13px';
        label.style.color = '#333';
        label.style.display = 'flex';
        label.style.alignItems = 'center';
        label.style.lineHeight = '1';
        label.style.height = '20px';

        // 更新样式函数
        const updateStyle = () => {
            if (checkbox.checked) {
                indicator.style.backgroundColor = '#44b7fe';
                indicator.style.borderColor = '#44b7fe';
                checkmark.style.opacity = '1';
                toggleItem.style.background = 'rgba(68, 183, 254, 0.1)';
            } else {
                indicator.style.backgroundColor = 'transparent';
                indicator.style.borderColor = '#ccc';
                checkmark.style.opacity = '0';
                toggleItem.style.background = 'transparent';
            }
        };

        // 初始化样式
        updateStyle();

        toggleItem.appendChild(checkbox);
        toggleItem.appendChild(indicator);
        toggleItem.appendChild(label);

        // 悬停效果
        toggleItem.addEventListener('mouseenter', () => {
            if (checkbox.checked) {
                toggleItem.style.background = 'rgba(68, 183, 254, 0.15)';
            } else {
                toggleItem.style.background = 'rgba(68, 183, 254, 0.08)';
            }
        });
        toggleItem.addEventListener('mouseleave', () => {
            updateStyle();
        });

        // 统一的切换处理函数
        const handleToggleChange = (isChecked) => {
            updateStyle();

            // 同步到全局状态
            if (toggle.id === 'focus-mode') {
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
                console.log(`主动搭话已${isChecked ? '开启' : '关闭'}`);
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
                console.log(`主动视觉已${isChecked ? '开启' : '关闭'}`);
            }
        };

        // 点击切换
        checkbox.addEventListener('change', (e) => {
            e.stopPropagation();
            handleToggleChange(checkbox.checked);
        });

        toggleItem.addEventListener('click', (e) => {
            if (e.target !== checkbox && e.target !== indicator) {
                e.preventDefault();
                e.stopPropagation();
                const newChecked = !checkbox.checked;
                checkbox.checked = newChecked;
                handleToggleChange(newChecked);
            }
        });

        indicator.addEventListener('click', (e) => {
            e.stopPropagation();
            const newChecked = !checkbox.checked;
            checkbox.checked = newChecked;
            handleToggleChange(newChecked);
        });

        label.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            const newChecked = !checkbox.checked;
            checkbox.checked = newChecked;
            handleToggleChange(newChecked);
        });

        return toggleItem;
    },

    /**
     * 创建 Agent 工具开关项
     * @param {object} toggle - 开关配置
     * @param {HTMLElement} popup - 父元素
     * @param {object} context - 上下文对象
     * @returns {HTMLElement} 开关项元素
     */
    createToggleItem: function (toggle, popup, context) {
        const toggleItem = document.createElement('div');
        Object.assign(toggleItem.style, {
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            padding: '6px 8px',
            cursor: 'pointer',
            borderRadius: '6px',
            transition: 'background 0.2s ease, opacity 0.2s ease',
            fontSize: '13px',
            whiteSpace: 'nowrap',
            opacity: toggle.initialDisabled ? '0.5' : '1'
        });

        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.id = `shared-${toggle.id}`;
        Object.assign(checkbox.style, {
            display: 'none'
        });

        if (toggle.initialDisabled) {
            checkbox.disabled = true;
            checkbox.title = toggle.initialTitle || (window.t ? window.t('settings.toggles.checking') : '查询中...');
            toggleItem.style.cursor = 'default';
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
        label.htmlFor = `shared-${toggle.id}`;
        label.style.cursor = 'pointer';
        label.style.userSelect = 'none';
        label.style.fontSize = '13px';
        label.style.color = '#333';

        // 更新样式函数
        const updateStyle = () => {
            if (checkbox.checked) {
                indicator.style.backgroundColor = '#44b7fe';
                indicator.style.borderColor = '#44b7fe';
                checkmark.style.opacity = '1';
            } else {
                indicator.style.backgroundColor = 'transparent';
                indicator.style.borderColor = '#ccc';
                checkmark.style.opacity = '0';
            }
        };

        const updateDisabledStyle = () => {
            const disabled = checkbox.disabled;
            const cursor = disabled ? 'default' : 'pointer';
            [toggleItem, label, indicator].forEach(el => el.style.cursor = cursor);
            toggleItem.style.opacity = disabled ? '0.5' : '1';
        };

        const updateTitle = () => {
            const title = checkbox.title || '';
            label.title = toggleItem.title = title;
        };

        // 监听属性变化
        const disabledObserver = new MutationObserver(() => {
            updateDisabledStyle();
            if (checkbox.hasAttribute('title')) updateTitle();
        });
        disabledObserver.observe(checkbox, { attributes: true, attributeFilter: ['disabled', 'title'] });

        checkbox.addEventListener('change', updateStyle);

        updateStyle();
        updateDisabledStyle();
        updateTitle();

        toggleItem.appendChild(checkbox);
        toggleItem.appendChild(indicator);
        toggleItem.appendChild(label);

        // 保存更新函数
        checkbox._updateStyle = updateStyle;
        if (toggle.labelKey) {
            toggleItem._updateLabelText = () => {
                if (toggle.labelKey && window.t) {
                    label.innerText = window.t(toggle.labelKey);
                }
            };
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

        // 点击切换
        const handleToggle = (event) => {
            if (checkbox.disabled) return;

            if (checkbox._processing) {
                const elapsed = Date.now() - (checkbox._processingTime || 0);
                if (elapsed < 500) {
                    console.log('[UIComponentFactory] Agent开关正在处理中，忽略重复点击:', toggle.id);
                    event?.preventDefault();
                    event?.stopPropagation();
                    return;
                }
            }

            checkbox._processing = true;
            checkbox._processingEvent = event;
            checkbox._processingTime = Date.now();

            const newChecked = !checkbox.checked;
            checkbox.checked = newChecked;
            checkbox.dispatchEvent(new Event('change', { bubbles: true }));
            updateStyle();

            setTimeout(() => {
                if (checkbox._processing && Date.now() - checkbox._processingTime > 5000) {
                    console.log('[UIComponentFactory] Agent开关备用清除机制触发:', toggle.id);
                    checkbox._processing = false;
                    checkbox._processingEvent = null;
                    checkbox._processingTime = null;
                }
            }, 5500);

            event?.preventDefault();
            event?.stopPropagation();
        };

        toggleItem.addEventListener('click', (e) => {
            if (e.target !== checkbox && e.target !== indicator && e.target !== label) {
                handleToggle(e);
            }
        });

        indicator.addEventListener('click', (e) => {
            e.stopPropagation();
            handleToggle(e);
        });

        label.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            handleToggle(e);
        });

        return toggleItem;
    },

    /**
     * 创建设置菜单项
     * @param {HTMLElement} popup - 弹出框元素
     * @param {object} context - 上下文对象
     */
    createSettingsMenuItems: function (popup, context) {
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
                color: '#333'
            });

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

            const labelText = document.createElement('span');
            labelText.textContent = item.label;
            if (item.labelKey) {
                labelText.setAttribute('data-i18n', item.labelKey);
            }
            Object.assign(labelText.style, {
                display: 'flex',
                alignItems: 'center',
                lineHeight: '1',
                height: '24px'
            });
            menuItem.appendChild(labelText);

            if (item.labelKey) {
                const updateLabelText = () => {
                    if (window.t) {
                        labelText.textContent = window.t(item.labelKey);
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
                    let finalUrl = item.url || item.urlBase;
                    if (item.id === 'live2d-manage' && item.urlBase) {
                        const lanlanName = (window.lanlan_config && window.lanlan_config.lanlan_name) || '';
                        finalUrl = `${item.urlBase}?lanlan_name=${encodeURIComponent(lanlanName)}`;
                        if (window.closeAllSettingsWindows) {
                            window.closeAllSettingsWindows();
                        }
                        window.location.href = finalUrl;
                    } else if (item.id === 'voice-clone' && item.url) {
                        const lanlanName = (window.lanlan_config && window.lanlan_config.lanlan_name) || '';
                        finalUrl = `${item.url}?lanlan_name=${encodeURIComponent(lanlanName)}`;

                        // 使用 context 的窗口管理（如果可用）
                        if (context && context._openSettingsWindows) {
                            if (context._openSettingsWindows[finalUrl]) {
                                const existingWindow = context._openSettingsWindows[finalUrl];
                                if (existingWindow && !existingWindow.closed) {
                                    existingWindow.focus();
                                    return;
                                } else {
                                    delete context._openSettingsWindows[finalUrl];
                                }
                            }

                            if (context.closeAllSettingsWindows) {
                                context.closeAllSettingsWindows();
                            }

                            const newWindow = window.open(finalUrl, '_blank', 'width=1000,height=800,menubar=no,toolbar=no,location=no,status=no');
                            if (newWindow) {
                                context._openSettingsWindows[finalUrl] = newWindow;
                            }
                        } else {
                            window.open(finalUrl, '_blank', 'width=1000,height=800,menubar=no,toolbar=no,location=no,status=no');
                        }
                    } else {
                        // 其他页面弹出新窗口
                        if (context && context._openSettingsWindows) {
                            if (context._openSettingsWindows[finalUrl]) {
                                const existingWindow = context._openSettingsWindows[finalUrl];
                                if (existingWindow && !existingWindow.closed) {
                                    existingWindow.focus();
                                    return;
                                } else {
                                    delete context._openSettingsWindows[finalUrl];
                                }
                            }

                            if (context.closeAllSettingsWindows) {
                                context.closeAllSettingsWindows();
                            }

                            const newWindow = window.open(finalUrl, '_blank', 'width=1000,height=800,menubar=no,toolbar=no,location=no,status=no');
                            if (newWindow) {
                                context._openSettingsWindows[finalUrl] = newWindow;

                                const checkClosed = setInterval(() => {
                                    if (newWindow.closed) {
                                        delete context._openSettingsWindows[finalUrl];
                                        clearInterval(checkClosed);
                                    }
                                }, 500);
                            }
                        } else {
                            window.open(finalUrl, '_blank', 'width=1000,height=800,menubar=no,toolbar=no,location=no,status=no');
                        }
                    }
                }
            });

            popup.appendChild(menuItem);
        });
    }
};

console.log('[UIComponentFactory] 组件工厂已加载');
