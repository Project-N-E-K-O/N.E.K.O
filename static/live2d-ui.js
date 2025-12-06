/**
 * Live2D UI - 浮动按钮、弹出框等UI组件
 */

// 设置 HTML 锁形图标（保留用于兼容）
Live2DManager.prototype.setupHTMLLockIcon = function(model) {
    const container = document.getElementById('live2d-canvas');
    
    // 在 l2d_manager 等页面，默认解锁并可交互
    if (!document.getElementById('chat-container')) {
        this.isLocked = false;
        container.style.pointerEvents = 'auto';
        return;
    }
    
    // 在观看模式下不显示锁图标，但允许交互
    if (window.isViewerMode) {
        this.isLocked = false;
        container.style.pointerEvents = 'auto';
        return;
    }

    const lockIcon = document.createElement('div');
    lockIcon.id = 'live2d-lock-icon';
    Object.assign(lockIcon.style, {
        position: 'fixed',
        zIndex: '99999',  // 确保始终浮动在顶层，不被live2d遮挡
        width: '32px',
        height: '32px',
        cursor: 'pointer',
        userSelect: 'none',
        pointerEvents: 'auto',
        display: 'none' // 默认隐藏
    });
    
    // 添加版本号防止缓存
    const iconVersion = '?v=' + Date.now();
    
    // 创建图片容器
    const imgContainer = document.createElement('div');
    Object.assign(imgContainer.style, {
        position: 'relative',
        width: '32px',
        height: '32px'
    });
    
    // 创建锁定状态图片
    const imgLocked = document.createElement('img');
    imgLocked.src = '/static/icons/locked_icon.png' + iconVersion;
    imgLocked.alt = 'Locked';
    Object.assign(imgLocked.style, {
        position: 'absolute',
        width: '32px',
        height: '32px',
        objectFit: 'contain',
        pointerEvents: 'none',
        opacity: this.isLocked ? '1' : '0',
        transition: 'opacity 0.3s ease'
    });
    
    // 创建解锁状态图片
    const imgUnlocked = document.createElement('img');
    imgUnlocked.src = '/static/icons/unlocked_icon.png' + iconVersion;
    imgUnlocked.alt = 'Unlocked';
    Object.assign(imgUnlocked.style, {
        position: 'absolute',
        width: '32px',
        height: '32px',
        objectFit: 'contain',
        pointerEvents: 'none',
        opacity: this.isLocked ? '0' : '1',
        transition: 'opacity 0.3s ease'
    });
    
    imgContainer.appendChild(imgLocked);
    imgContainer.appendChild(imgUnlocked);
    lockIcon.appendChild(imgContainer);

    document.body.appendChild(lockIcon);
    this._lockIconElement = lockIcon;

    lockIcon.addEventListener('click', (e) => {
        e.stopPropagation();
        this.isLocked = !this.isLocked;
        
        // 切换图标显示
        imgLocked.style.opacity = this.isLocked ? '1' : '0';
        imgUnlocked.style.opacity = this.isLocked ? '0' : '1';

        if (this.isLocked) {
            container.style.pointerEvents = 'none';
            // 锁定时隐藏浮动菜单
            const floatingButtons = document.getElementById('live2d-floating-buttons');
            if (floatingButtons) {
                floatingButtons.style.display = 'none';
            }
        } else {
            container.style.pointerEvents = 'auto';
            // 解锁时显示浮动菜单
            const floatingButtons = document.getElementById('live2d-floating-buttons');
            if (floatingButtons) {
                floatingButtons.style.display = 'flex';
            }
        }
    });

    // 初始状态
    container.style.pointerEvents = this.isLocked ? 'none' : 'auto';

    // 持续更新图标位置（保存回调用于移除）
    const tick = () => {
        try {
            if (!model || !model.parent) {
                // 模型可能已被销毁或从舞台移除
                if (lockIcon) lockIcon.style.display = 'none';
                return;
            }
            const bounds = model.getBounds();
            const screenWidth = window.innerWidth;
            const screenHeight = window.innerHeight;

            const targetX = bounds.right * 0.7 + bounds.left * 0.3;
            const targetY = bounds.top * 0.3 + bounds.bottom * 0.7;

            lockIcon.style.left = `${Math.min(targetX, screenWidth - 40)}px`;
            lockIcon.style.top = `${Math.min(targetY, screenHeight - 40)}px`;
        } catch (_) {
            // 忽略单帧异常
        }
    };
    this._lockIconTicker = tick;
    this.pixi_app.ticker.add(tick);
};

// 设置浮动按钮系统（新的控制面板）
Live2DManager.prototype.setupFloatingButtons = function(model) {
    const container = document.getElementById('live2d-canvas');
    
    // 在 l2d_manager 等页面不显示
    if (!document.getElementById('chat-container')) {
        this.isLocked = false;
        container.style.pointerEvents = 'auto';
        return;
    }
    
    // 在观看模式下不显示浮动按钮
    if (window.isViewerMode) {
        this.isLocked = false;
        container.style.pointerEvents = 'auto';
        return;
    }

    // 创建按钮容器
    const buttonsContainer = document.createElement('div');
    buttonsContainer.id = 'live2d-floating-buttons';
    Object.assign(buttonsContainer.style, {
        position: 'fixed',
        zIndex: '99999',  // 确保始终浮动在顶层，不被live2d遮挡
        pointerEvents: 'none',
        display: 'none', // 初始隐藏，鼠标靠近时才显示
        flexDirection: 'column',
        gap: '12px'
    });
    
    // 阻止浮动按钮容器上的指针事件传播到window，避免触发live2d拖拽
    const stopContainerEvent = (e) => {
        e.stopPropagation();
    };
    buttonsContainer.addEventListener('pointerdown', stopContainerEvent, true);
    buttonsContainer.addEventListener('pointermove', stopContainerEvent, true);
    buttonsContainer.addEventListener('pointerup', stopContainerEvent, true);
    buttonsContainer.addEventListener('mousedown', stopContainerEvent, true);
    buttonsContainer.addEventListener('mousemove', stopContainerEvent, true);
    buttonsContainer.addEventListener('mouseup', stopContainerEvent, true);
    buttonsContainer.addEventListener('touchstart', stopContainerEvent, true);
    buttonsContainer.addEventListener('touchmove', stopContainerEvent, true);
    buttonsContainer.addEventListener('touchend', stopContainerEvent, true);
    
    document.body.appendChild(buttonsContainer);
    this._floatingButtonsContainer = buttonsContainer;

    // 响应式：小屏时固定在右下角并横向排列（使用全局 isMobileWidth）
    const applyResponsiveFloatingLayout = () => {
        if (isMobileWidth()) {
            // 移动端：固定在右下角，纵向排布，整体上移100px
            buttonsContainer.style.flexDirection = 'column';
            buttonsContainer.style.bottom = '116px';
            buttonsContainer.style.right = '16px';
            buttonsContainer.style.left = '';
            buttonsContainer.style.top = '';
        } else {
            // 桌面端：恢复纵向排布，由 ticker 动态定位
            buttonsContainer.style.flexDirection = 'column';
            buttonsContainer.style.bottom = '';
            buttonsContainer.style.right = '';
        }
    };
    applyResponsiveFloatingLayout();
    window.addEventListener('resize', applyResponsiveFloatingLayout);

    // 定义按钮配置（从上到下：麦克风、显示屏、锤子、设置、睡觉）
    // 添加版本号防止缓存（更新图标时修改这个版本号）
    const iconVersion = '?v=' + Date.now();
    
    const buttonConfigs = [
        { id: 'mic', emoji: '🎤', title: window.t ? window.t('buttons.voiceControl') : '语音控制', titleKey: 'buttons.voiceControl', hasPopup: true, toggle: true, separatePopupTrigger: true, iconOff: '/static/icons/mic_icon_off.png' + iconVersion, iconOn: '/static/icons/mic_icon_on.png' + iconVersion },
        { id: 'screen', emoji: '🖥️', title: window.t ? window.t('buttons.screenShare') : '屏幕分享', titleKey: 'buttons.screenShare', hasPopup: false, toggle: true, iconOff: '/static/icons/screen_icon_off.png' + iconVersion, iconOn: '/static/icons/screen_icon_on.png' + iconVersion },
        { id: 'agent', emoji: '🔨', title: window.t ? window.t('buttons.agentTools') : 'Agent工具', titleKey: 'buttons.agentTools', hasPopup: true, popupToggle: true, exclusive: 'settings', iconOff: '/static/icons/Agent_off.png' + iconVersion, iconOn: '/static/icons/Agent_on.png' + iconVersion },
        { id: 'settings', emoji: '⚙️', title: window.t ? window.t('buttons.settings') : '设置', titleKey: 'buttons.settings', hasPopup: true, popupToggle: true, exclusive: 'agent', iconOff: '/static/icons/set_off.png' + iconVersion, iconOn: '/static/icons/set_on.png' + iconVersion },
        { id: 'goodbye', emoji: '💤', title: window.t ? window.t('buttons.leave') : '请她离开', titleKey: 'buttons.leave', hasPopup: false, iconOff: '/static/icons/rest_off.png' + iconVersion, iconOn: '/static/icons/rest_on.png' + iconVersion }
    ];

    // 创建主按钮
    buttonConfigs.forEach(config => {
        // 移动端隐藏 agent 和 goodbye 按钮
        if (isMobileWidth() && (config.id === 'agent' || config.id === 'goodbye')) {
            return;
        }
        const btnWrapper = document.createElement('div');
        btnWrapper.style.position = 'relative';
        btnWrapper.style.display = 'flex';
        btnWrapper.style.alignItems = 'center';
        btnWrapper.style.gap = '8px';

        // 阻止包装器上的指针事件传播到window，避免触发live2d拖拽
        const stopWrapperEvent = (e) => {
            e.stopPropagation();
        };
        btnWrapper.addEventListener('pointerdown', stopWrapperEvent, true);
        btnWrapper.addEventListener('pointermove', stopWrapperEvent, true);
        btnWrapper.addEventListener('pointerup', stopWrapperEvent, true);
        btnWrapper.addEventListener('mousedown', stopWrapperEvent, true);
        btnWrapper.addEventListener('mousemove', stopWrapperEvent, true);
        btnWrapper.addEventListener('mouseup', stopWrapperEvent, true);
        btnWrapper.addEventListener('touchstart', stopWrapperEvent, true);
        btnWrapper.addEventListener('touchmove', stopWrapperEvent, true);
        btnWrapper.addEventListener('touchend', stopWrapperEvent, true);

        const btn = document.createElement('div');
        btn.id = `live2d-btn-${config.id}`;
        btn.className = 'live2d-floating-btn';
        btn.title = config.title;
        if (config.titleKey) {
            btn.setAttribute('data-i18n-title', config.titleKey);
        }
        
        let imgOff = null; // off状态图片
        let imgOn = null;  // on状态图片
        
        // 优先使用带off/on的PNG图标，如果有iconOff和iconOn则使用叠加方式实现淡入淡出
        if (config.iconOff && config.iconOn) {
            // 创建图片容器，用于叠加两张图片
            const imgContainer = document.createElement('div');
            Object.assign(imgContainer.style, {
                position: 'relative',
                width: '48px',
                height: '48px',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center'
            });
            
            // 创建off状态图片（默认显示）
            imgOff = document.createElement('img');
            imgOff.src = config.iconOff;
            imgOff.alt = config.title;
            Object.assign(imgOff.style, {
                position: 'absolute',
                width: '48px',
                height: '48px',
                objectFit: 'contain',
                pointerEvents: 'none',
                opacity: '1',
                transition: 'opacity 0.3s ease'
            });
            
            // 创建on状态图片（默认隐藏）
            imgOn = document.createElement('img');
            imgOn.src = config.iconOn;
            imgOn.alt = config.title;
            Object.assign(imgOn.style, {
                position: 'absolute',
                width: '48px',
                height: '48px',
                objectFit: 'contain',
                pointerEvents: 'none',
                opacity: '0',
                transition: 'opacity 0.3s ease'
            });
            
            imgContainer.appendChild(imgOff);
            imgContainer.appendChild(imgOn);
            btn.appendChild(imgContainer);
        } else if (config.icon) {
            // 兼容单图标配置
            const img = document.createElement('img');
            img.src = config.icon;
            img.alt = config.title;
            Object.assign(img.style, {
                width: '48px',
                height: '48px',
                objectFit: 'contain',
                pointerEvents: 'none'
            });
            btn.appendChild(img);
        } else if (config.emoji) {
            // 备用方案：使用emoji
            btn.innerText = config.emoji;
        }
        
        Object.assign(btn.style, {
            width: '48px',
            height: '48px',
            borderRadius: '50%',
            background: 'rgba(255, 255, 255, 0.65)',  // Fluent Design Acrylic
            backdropFilter: 'saturate(180%) blur(20px)',  // Fluent 标准模糊
            border: '1px solid rgba(255, 255, 255, 0.18)',  // 微妙高光边框
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: '24px',
            cursor: 'pointer',
            userSelect: 'none',
            boxShadow: '0 2px 4px rgba(0, 0, 0, 0.04), 0 4px 8px rgba(0, 0, 0, 0.08)',  // Fluent 多层阴影
            transition: 'all 0.1s ease',  // Fluent 快速响应
            pointerEvents: 'auto'
        });

        // 阻止按钮上的指针事件传播到window，避免触发live2d拖拽
        const stopBtnEvent = (e) => {
            e.stopPropagation();
        };
        btn.addEventListener('pointerdown', stopBtnEvent, true);
        btn.addEventListener('pointermove', stopBtnEvent, true);
        btn.addEventListener('pointerup', stopBtnEvent, true);
        btn.addEventListener('mousedown', stopBtnEvent, true);
        btn.addEventListener('mousemove', stopBtnEvent, true);
        btn.addEventListener('mouseup', stopBtnEvent, true);
        btn.addEventListener('touchstart', stopBtnEvent, true);
        btn.addEventListener('touchmove', stopBtnEvent, true);
        btn.addEventListener('touchend', stopBtnEvent, true);

        // 鼠标悬停效果 - Fluent Design
        btn.addEventListener('mouseenter', () => {
            btn.style.transform = 'scale(1.05)';  // 更微妙的缩放
            btn.style.boxShadow = '0 4px 8px rgba(0, 0, 0, 0.08), 0 8px 16px rgba(0, 0, 0, 0.08)';
            btn.style.background = 'rgba(255, 255, 255, 0.8)';  // 悬停时更亮
            // 淡出off图标，淡入on图标
            if (imgOff && imgOn) {
                imgOff.style.opacity = '0';
                imgOn.style.opacity = '1';
            }
        });
        btn.addEventListener('mouseleave', () => {
            btn.style.transform = 'scale(1)';
            btn.style.boxShadow = '0 2px 4px rgba(0, 0, 0, 0.04), 0 4px 8px rgba(0, 0, 0, 0.08)';
            // 恢复原始背景色（根据按钮状态）
            const isActive = btn.dataset.active === 'true';
            const popup = document.getElementById(`live2d-popup-${config.id}`);
            const isPopupVisible = popup && popup.style.display === 'flex' && popup.style.opacity === '1';
            
            if (isActive || isPopupVisible) {
                // 激活状态：稍亮的背景
                btn.style.background = 'rgba(255, 255, 255, 0.75)';
            } else {
                btn.style.background = 'rgba(255, 255, 255, 0.65)';  // Fluent Acrylic
            }
            
            // 根据按钮激活状态决定显示哪个图标
            // 如果按钮已激活，保持显示on图标；否则显示off图标
            if (imgOff && imgOn) {
                if (isActive || isPopupVisible) {
                    // 激活状态：保持on图标
                    imgOff.style.opacity = '0';
                    imgOn.style.opacity = '1';
                } else {
                    // 未激活状态：显示off图标
                    imgOff.style.opacity = '1';
                    imgOn.style.opacity = '0';
                }
            }
        });

        // popupToggle: 按钮点击切换弹出框显示，弹出框显示时按钮变蓝
        if (config.popupToggle) {
            const popup = this.createPopup(config.id);
            btnWrapper.appendChild(btn);
            
            // 直接将弹出框添加到btnWrapper，这样定位更准确
            btnWrapper.appendChild(popup);
            
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                
                // 检查弹出框当前状态
                const isPopupVisible = popup.style.display === 'flex' && popup.style.opacity === '1';
                
                // 实现互斥逻辑：如果有exclusive配置，关闭对方
                if (!isPopupVisible && config.exclusive) {
                    this.closePopupById(config.exclusive);
                }
                
                // 切换弹出框
                this.showPopup(config.id, popup);
                
                // 等待弹出框状态更新后更新图标状态
                setTimeout(() => {
                    const newPopupVisible = popup.style.display === 'flex' && popup.style.opacity === '1';
                    // 根据弹出框状态更新图标
                    if (imgOff && imgOn) {
                        if (newPopupVisible) {
                            // 弹出框显示：显示on图标
                            imgOff.style.opacity = '0';
                            imgOn.style.opacity = '1';
                        } else {
                            // 弹出框隐藏：显示off图标
                            imgOff.style.opacity = '1';
                            imgOn.style.opacity = '0';
                        }
                    }
                }, 50);
            });
            
        } else if (config.toggle) {
            // Toggle 状态（可能同时有弹出框）
            btn.dataset.active = 'false';
            
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                
                // 对于麦克风按钮，在计算状态之前就检查 micButton 的状态
                if (config.id === 'mic') {
                    const micButton = document.getElementById('micButton');
                    if (micButton && micButton.classList.contains('active')) {
                        // 检查是否正在录音：如果 isRecording 为 true，说明已经启动成功，允许点击退出
                        // 如果 isRecording 为 false，说明正在启动过程中，阻止点击
                        const isRecording = window.isRecording || false; // 从全局获取 isRecording 状态
                        
                        if (!isRecording) {
                            // 正在启动过程中，强制保持激活状态，不切换
                            // 确保浮动按钮状态与 micButton 同步
                            if (btn.dataset.active !== 'true') {
                                btn.dataset.active = 'true';
                                if (imgOff && imgOn) {
                                    imgOff.style.opacity = '0';
                                    imgOn.style.opacity = '1';
                                }
                            }
                            return; // 直接返回，不执行任何状态切换或事件触发
                        }
                        // 如果 isRecording 为 true，说明已经启动成功，允许继续执行（可以退出）
                    }
                }
                
                const isActive = btn.dataset.active === 'true';
                const newActive = !isActive;
                
                btn.dataset.active = newActive.toString();
                
                // 更新图标状态
                if (imgOff && imgOn) {
                    if (newActive) {
                        // 激活：显示on图标
                        imgOff.style.opacity = '0';
                        imgOn.style.opacity = '1';
                    } else {
                        // 未激活：显示off图标
                        imgOff.style.opacity = '1';
                        imgOn.style.opacity = '0';
                    }
                }
                
                // 触发自定义事件
                const event = new CustomEvent(`live2d-${config.id}-toggle`, {
                    detail: { active: newActive }
                });
                window.dispatchEvent(event);
            });
            
            // 先添加主按钮到包装器
            btnWrapper.appendChild(btn);
            
            // 如果有弹出框且需要独立的触发器（仅麦克风）
            if (config.hasPopup && config.separatePopupTrigger) {
                // 手机模式下移除麦克风弹窗与触发器
                if (isMobileWidth() && config.id === 'mic') {
                    buttonsContainer.appendChild(btnWrapper);
                    this._floatingButtons[config.id] = { 
                        button: btn, 
                        wrapper: btnWrapper,
                        imgOff: imgOff,
                        imgOn: imgOn
                    };
                    return;
                }
                const popup = this.createPopup(config.id);
                
                // 创建三角按钮（用于触发弹出框）- Fluent Design
                const triggerBtn = document.createElement('div');
                triggerBtn.innerText = '▶';
                Object.assign(triggerBtn.style, {
                    width: '24px',
                    height: '24px',
                    borderRadius: '50%',
                    background: 'rgba(255, 255, 255, 0.65)',  // Fluent Acrylic
                    backdropFilter: 'saturate(180%) blur(20px)',
                    border: '1px solid rgba(255, 255, 255, 0.18)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontSize: '13px',
                    color: '#44b7fe',  // 主题浅蓝色
                    cursor: 'pointer',
                    userSelect: 'none',
                    boxShadow: '0 2px 4px rgba(0, 0, 0, 0.04), 0 4px 8px rgba(0, 0, 0, 0.08)',
                    transition: 'all 0.1s ease',
                    pointerEvents: 'auto',
                    marginLeft: '-10px'
                });
                
                // 阻止三角按钮上的指针事件传播到window，避免触发live2d拖拽
                const stopTriggerEvent = (e) => {
                    e.stopPropagation();
                };
                triggerBtn.addEventListener('pointerdown', stopTriggerEvent, true);
                triggerBtn.addEventListener('pointermove', stopTriggerEvent, true);
                triggerBtn.addEventListener('pointerup', stopTriggerEvent, true);
                triggerBtn.addEventListener('mousedown', stopTriggerEvent, true);
                triggerBtn.addEventListener('mousemove', stopTriggerEvent, true);
                triggerBtn.addEventListener('mouseup', stopTriggerEvent, true);
                triggerBtn.addEventListener('touchstart', stopTriggerEvent, true);
                triggerBtn.addEventListener('touchmove', stopTriggerEvent, true);
                triggerBtn.addEventListener('touchend', stopTriggerEvent, true);

                triggerBtn.addEventListener('mouseenter', () => {
                    triggerBtn.style.transform = 'scale(1.05)';
                    triggerBtn.style.boxShadow = '0 4px 8px rgba(0, 0, 0, 0.08), 0 8px 16px rgba(0, 0, 0, 0.08)';
                    triggerBtn.style.background = 'rgba(255, 255, 255, 0.8)';
                });
                triggerBtn.addEventListener('mouseleave', () => {
                    triggerBtn.style.transform = 'scale(1)';
                    triggerBtn.style.boxShadow = '0 2px 4px rgba(0, 0, 0, 0.04), 0 4px 8px rgba(0, 0, 0, 0.08)';
                    triggerBtn.style.background = 'rgba(255, 255, 255, 0.65)';
                });
                
                triggerBtn.addEventListener('click', async (e) => {
                    e.stopPropagation();
                    
                    // 如果是麦克风弹出框，先加载麦克风列表
                    if (config.id === 'mic' && window.renderFloatingMicList) {
                        await window.renderFloatingMicList();
                    }
                    
                    this.showPopup(config.id, popup);
                });
                
                // 创建包装器用于三角按钮和弹出框（相对定位）
                const triggerWrapper = document.createElement('div');
                triggerWrapper.style.position = 'relative';
                
                // 阻止包装器上的指针事件传播到window，避免触发live2d拖拽
                const stopTriggerWrapperEvent = (e) => {
                    e.stopPropagation();
                };
                triggerWrapper.addEventListener('pointerdown', stopTriggerWrapperEvent, true);
                triggerWrapper.addEventListener('pointermove', stopTriggerWrapperEvent, true);
                triggerWrapper.addEventListener('pointerup', stopTriggerWrapperEvent, true);
                triggerWrapper.addEventListener('mousedown', stopTriggerWrapperEvent, true);
                triggerWrapper.addEventListener('mousemove', stopTriggerWrapperEvent, true);
                triggerWrapper.addEventListener('mouseup', stopTriggerWrapperEvent, true);
                triggerWrapper.addEventListener('touchstart', stopTriggerWrapperEvent, true);
                triggerWrapper.addEventListener('touchmove', stopTriggerWrapperEvent, true);
                triggerWrapper.addEventListener('touchend', stopTriggerWrapperEvent, true);
                
                triggerWrapper.appendChild(triggerBtn);
                triggerWrapper.appendChild(popup);
                
                btnWrapper.appendChild(triggerWrapper);
            }
        } else {
            // 普通点击按钮
            btnWrapper.appendChild(btn);
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const event = new CustomEvent(`live2d-${config.id}-click`);
                window.dispatchEvent(event);
            });
        }

        buttonsContainer.appendChild(btnWrapper);
        this._floatingButtons[config.id] = { 
            button: btn, 
            wrapper: btnWrapper,
            imgOff: imgOff,  // 保存图标引用
            imgOn: imgOn      // 保存图标引用
        };
    });

    console.log('[Live2D] 所有浮动按钮已创建完成');

    // 创建独立的"请她回来"按钮（准备显示在"请她离开"按钮的位置）
    const returnButtonContainer = document.createElement('div');
    returnButtonContainer.id = 'live2d-return-button-container';
    Object.assign(returnButtonContainer.style, {
        position: 'fixed',
        top: '0',
        left: '0',
        transform: 'none',
        zIndex: '99999',  // 确保始终浮动在顶层，不被live2d遮挡
        pointerEvents: 'auto', // 允许交互，包括拖动
        display: 'none' // 初始隐藏，只在点击"请她离开"后显示
    });

    const returnBtn = document.createElement('div');
    returnBtn.id = 'live2d-btn-return';
    returnBtn.className = 'live2d-return-btn';
    returnBtn.title = window.t ? window.t('buttons.return') : '请她回来';
    returnBtn.setAttribute('data-i18n-title', 'buttons.return');
    
    // 使用与"请她离开"相同的图标
    const imgOff = document.createElement('img');
    imgOff.src = '/static/icons/rest_off.png' + iconVersion;
    imgOff.alt = window.t ? window.t('buttons.return') : '请她回来';
    Object.assign(imgOff.style, {
        width: '64px',
        height: '64px',
        objectFit: 'contain',
        pointerEvents: 'none',
        opacity: '1',
        transition: 'opacity 0.3s ease'
    });
    
    const imgOn = document.createElement('img');
    imgOn.src = '/static/icons/rest_on.png' + iconVersion;
    imgOn.alt = window.t ? window.t('buttons.return') : '请她回来';
    Object.assign(imgOn.style, {
        position: 'absolute',
        width: '64px',
        height: '64px',
        objectFit: 'contain',
        pointerEvents: 'none',
        opacity: '0',
        transition: 'opacity 0.3s ease'
    });
    
    Object.assign(returnBtn.style, {
        width: '64px',
        height: '64px',
        borderRadius: '50%',
        background: 'rgba(255, 255, 255, 0.65)',  // Fluent Acrylic
        backdropFilter: 'saturate(180%) blur(20px)',
        border: '1px solid rgba(255, 255, 255, 0.18)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        cursor: 'pointer',
        userSelect: 'none',
        boxShadow: '0 2px 4px rgba(0, 0, 0, 0.04), 0 8px 16px rgba(0, 0, 0, 0.08), 0 16px 32px rgba(0, 0, 0, 0.04)',
        transition: 'all 0.1s ease',
        pointerEvents: 'auto',
        position: 'relative'
    });

    // 悬停效果 - Fluent Design
    returnBtn.addEventListener('mouseenter', () => {
        returnBtn.style.transform = 'scale(1.05)';
        returnBtn.style.boxShadow = '0 4px 8px rgba(0, 0, 0, 0.08), 0 16px 32px rgba(0, 0, 0, 0.08)';
        returnBtn.style.background = 'rgba(255, 255, 255, 0.8)';
        imgOff.style.opacity = '0';
        imgOn.style.opacity = '1';
    });

    returnBtn.addEventListener('mouseleave', () => {
        returnBtn.style.transform = 'scale(1)';
        returnBtn.style.boxShadow = '0 2px 4px rgba(0, 0, 0, 0.04), 0 8px 16px rgba(0, 0, 0, 0.08), 0 16px 32px rgba(0, 0, 0, 0.04)';
        returnBtn.style.background = 'rgba(255, 255, 255, 0.65)';
        imgOff.style.opacity = '1';
        imgOn.style.opacity = '0';
    });

    returnBtn.addEventListener('click', (e) => {
        // 检查是否处于拖拽状态，如果是拖拽操作则阻止点击
        if (returnButtonContainer.getAttribute('data-dragging') === 'true') {
            e.preventDefault();
            e.stopPropagation();
            return;
        }
        
        e.stopPropagation();
        const event = new CustomEvent('live2d-return-click');
        window.dispatchEvent(event);
    });

    returnBtn.appendChild(imgOff);
    returnBtn.appendChild(imgOn);
    returnButtonContainer.appendChild(returnBtn);
    document.body.appendChild(returnButtonContainer);
    this._returnButtonContainer = returnButtonContainer;

    // 初始状态
    container.style.pointerEvents = this.isLocked ? 'none' : 'auto';

    // 持续更新按钮位置（在角色腰部右侧，垂直居中）
    // 基准按钮尺寸和工具栏高度（用于计算缩放）
    const baseButtonSize = 48;
    const baseGap = 12;
    const buttonCount = 5;
    const baseToolbarHeight = baseButtonSize * buttonCount + baseGap * (buttonCount - 1); // 288px
    
    const tick = () => {
        try {
            if (!model || !model.parent) {
                return;
            }
            // 移动端固定位置，不随模型移动
            if (isMobileWidth()) {
                return;
            }
            const bounds = model.getBounds();
            const screenWidth = window.innerWidth;
            const screenHeight = window.innerHeight;

            // 计算模型实际高度
            const modelHeight = bounds.bottom - bounds.top;
            
            // 计算目标工具栏高度（模型高度的一半）
            const targetToolbarHeight = modelHeight / 2;
            
            // 计算缩放比例（限制在合理范围内，防止按钮太小或太大）
            const minScale = 0.5;  // 最小缩放50%
            const maxScale = 1.;  // 最大缩放120%
            const rawScale = targetToolbarHeight / baseToolbarHeight;
            const scale = Math.max(minScale, Math.min(maxScale, rawScale));
            
            // 应用缩放到容器（使用 transform-origin: left top 确保从左上角缩放）
            buttonsContainer.style.transformOrigin = 'left top';
            buttonsContainer.style.transform = `scale(${scale})`;

            // X轴：定位在角色右侧（与锁按钮类似的横向位置）
            const targetX = bounds.right * 0.8 + bounds.left * 0.2;
            
            // Y轴：工具栏中心与模型中心对齐
            const modelCenterY = (bounds.top + bounds.bottom) / 2;
            // 使用缩放后的实际工具栏高度
            const actualToolbarHeight = baseToolbarHeight * scale;
            // 让工具栏的中心位于模型中间，所以top = 中间 - 高度/2
            const targetY = modelCenterY - actualToolbarHeight / 2;
            
            // 边界限制：确保不超出屏幕顶部和底部
            const minY = 20; // 距离屏幕顶部的最小距离
            const maxY = screenHeight - actualToolbarHeight - 20; // 距离屏幕底部的最小距离
            const boundedY = Math.max(minY, Math.min(targetY, maxY));

            buttonsContainer.style.left = `${Math.min(targetX, screenWidth - 80 * scale)}px`;
            buttonsContainer.style.top = `${boundedY}px`;
            // 不要在这里设置 display，让鼠标检测逻辑来控制显示/隐藏
        } catch (_) {
            // 忽略单帧异常
        }
    };
    this._floatingButtonsTicker = tick;
    this.pixi_app.ticker.add(tick);
    
    // 为按钮容器添加拖动功能
    this.setupButtonsContainerDrag(buttonsContainer);
    
    // 页面加载时先显示5秒（锁定状态下不显示）
    setTimeout(() => {
        // 锁定状态下不显示浮动按钮容器
        if (this.isLocked) {
            return;
        }
        // 显示浮动按钮容器
        buttonsContainer.style.display = 'flex';
        
        setTimeout(() => {
            // 5秒后的隐藏逻辑：如果鼠标不在附近就隐藏
            if (!this.isFocusing) {
                buttonsContainer.style.display = 'none';
            }
        }, 5000);
    }, 100); // 延迟100ms确保位置已计算
    
    // 为"请她回来"按钮容器添加拖动功能
    this.setupReturnButtonContainerDrag(returnButtonContainer);
    
    // 通知其他代码浮动按钮已经创建完成（用于app.js中绑定Agent开关事件）
    window.dispatchEvent(new CustomEvent('live2d-floating-buttons-ready'));
    console.log('[Live2D] 浮动按钮就绪事件已发送');
};

// 创建弹出框
Live2DManager.prototype.createPopup = function(buttonId) {
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
        popup.id = 'live2d-mic-popup';
    } else if (buttonId === 'agent') {
        // Agent工具开关组
        this._createAgentPopupContent(popup);
    } else if (buttonId === 'settings') {
        // 设置菜单
        this._createSettingsPopupContent(popup);
    }

    return popup;
};

// 创建Agent弹出框内容
Live2DManager.prototype._createAgentPopupContent = function(popup) {
    // 添加状态显示栏 - Fluent Design
    const statusDiv = document.createElement('div');
    statusDiv.id = 'live2d-agent-status';
    Object.assign(statusDiv.style, {
        fontSize: '12px',
        color: '#44b7fe',  // 主题浅蓝色
        padding: '6px 8px',
        borderRadius: '4px',
        background: 'rgba(68, 183, 254, 0.05)',  // 浅蓝背景
        marginBottom: '8px',
        minHeight: '20px',
        textAlign: 'center'
    });
    statusDiv.textContent = ''; // 初始为空
    popup.appendChild(statusDiv);
    
    // 【修复】所有 agent 开关初始状态为禁用，等待查询结果后由 app.js 启用
    const agentToggles = [
        { id: 'agent-master', label: window.t ? window.t('settings.toggles.agentMaster') : 'Agent总开关', labelKey: 'settings.toggles.agentMaster', initialDisabled: true },
        { id: 'agent-keyboard', label: window.t ? window.t('settings.toggles.keyboardControl') : '键鼠控制', labelKey: 'settings.toggles.keyboardControl', initialDisabled: true },
        { id: 'agent-mcp', label: window.t ? window.t('settings.toggles.mcpTools') : 'MCP工具', labelKey: 'settings.toggles.mcpTools', initialDisabled: true }
    ];
    
    agentToggles.forEach(toggle => {
        const toggleItem = this._createToggleItem(toggle, popup);
        popup.appendChild(toggleItem);
    });
};

// 创建 Agent 任务 HUD（屏幕正中右侧）
Live2DManager.prototype.createAgentTaskHUD = function() {
    // 如果已存在则不重复创建
    if (document.getElementById('agent-task-hud')) {
        return document.getElementById('agent-task-hud');
    }
    
    const hud = document.createElement('div');
    hud.id = 'agent-task-hud';
    
    // 获取保存的位置或使用默认位置
    const savedPos = localStorage.getItem('agent-task-hud-position');
    let position = { top: '50%', right: '20px', transform: 'translateY(-50%)' };
    
    if (savedPos) {
        try {
            const parsed = JSON.parse(savedPos);
            position = {
                top: parsed.top || '50%',
                left: parsed.left || null,
                right: parsed.right || '20px',
                transform: parsed.transform || 'translateY(-50%)'
            };
        } catch (e) {
            console.warn('Failed to parse saved position:', e);
        }
    }
    
    Object.assign(hud.style, {
        position: 'fixed',
        width: '320px',
        maxHeight: '60vh',
        background: 'rgba(15, 23, 42, 0.92)',
        backdropFilter: 'blur(12px)',
        borderRadius: '16px',
        padding: '16px',
        boxShadow: '0 8px 32px rgba(0, 0, 0, 0.4), 0 0 0 1px rgba(255, 255, 255, 0.1)',
        color: '#e2e8f0',
        fontFamily: "'Segoe UI', 'SF Pro Display', -apple-system, sans-serif",
        fontSize: '13px',
        zIndex: '9999',
        display: 'none', // 默认隐藏
        flexDirection: 'column',
        gap: '12px',
        pointerEvents: 'auto',
        overflowY: 'auto',
        transition: 'opacity 0.3s ease, transform 0.3s ease, box-shadow 0.2s ease',
        cursor: 'move',
        userSelect: 'none',
        willChange: 'transform', // 优化性能
        touchAction: 'none' // 防止浏览器默认触摸行为
    });
    
    // 应用保存的位置
    if (position.top) hud.style.top = position.top;
    if (position.left) hud.style.left = position.left;
    if (position.right) hud.style.right = position.right;
    if (position.transform) hud.style.transform = position.transform;
    
    // HUD 标题栏
    const header = document.createElement('div');
    Object.assign(header.style, {
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        paddingBottom: '12px',
        borderBottom: '1px solid rgba(255, 255, 255, 0.1)'
    });
    
    const title = document.createElement('div');
    title.id = 'agent-task-hud-title';
    title.innerHTML = `<span style="color: #60a5fa; margin-right: 8px;">⚡</span>${window.t ? window.t('agent.taskHud.title') : 'Agent 任务'}`;
    Object.assign(title.style, {
        fontWeight: '600',
        fontSize: '15px',
        color: '#f1f5f9'
    });
    
    // 统计信息
    const stats = document.createElement('div');
    stats.id = 'agent-task-hud-stats';
    Object.assign(stats.style, {
        display: 'flex',
        gap: '12px',
        fontSize: '11px'
    });
    stats.innerHTML = `
        <span style="color: #fbbf24;" title="${window.t ? window.t('agent.taskHud.running') : '运行中'}">● <span id="hud-running-count">0</span></span>
        <span style="color: #60a5fa;" title="${window.t ? window.t('agent.taskHud.queued') : '队列中'}">◐ <span id="hud-queued-count">0</span></span>
    `;
    
    header.appendChild(title);
    header.appendChild(stats);
    hud.appendChild(header);
    
    // 任务列表容器
    const taskList = document.createElement('div');
    taskList.id = 'agent-task-list';
    Object.assign(taskList.style, {
        display: 'flex',
        flexDirection: 'column',
        gap: '8px',
        maxHeight: 'calc(60vh - 80px)',
        overflowY: 'auto'
    });
    
    // 空状态提示
    const emptyState = document.createElement('div');
    emptyState.id = 'agent-task-empty';
    
    // 空状态容器
    const emptyContent = document.createElement('div');
    emptyContent.textContent = window.t ? window.t('agent.taskHud.noTasks') : '暂无活动任务';
    Object.assign(emptyContent.style, {
        textAlign: 'center',
        color: '#64748b',
        padding: '20px',
        fontSize: '12px',
        transition: 'all 0.3s ease'
    });
    
    // 折叠控制按钮
    const collapseButton = document.createElement('div');
    collapseButton.className = 'collapse-button';
    collapseButton.innerHTML = '▼';
    Object.assign(collapseButton.style, {
        position: 'absolute',
        top: '8px',
        right: '8px',
        width: '20px',
        height: '20px',
        borderRadius: '50%',
        background: 'rgba(100, 116, 139, 0.3)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontSize: '10px',
        color: '#64748b',
        cursor: 'pointer',
        transition: 'all 0.2s ease',
        zIndex: '1'
    });
    
    // 设置空状态容器样式
    Object.assign(emptyState.style, {
        position: 'relative',
        transition: 'all 0.3s ease'
    });
    
    emptyState.appendChild(emptyContent);
    emptyState.appendChild(collapseButton);
    taskList.appendChild(emptyState);
    
    // 初始化折叠状态
    this._setupCollapseFunctionality(emptyState, collapseButton, emptyContent);
    
    hud.appendChild(taskList);
    
    document.body.appendChild(hud);
    
    // 添加拖拽功能
    this._setupDragging(hud);
    
    return hud;
};

// 显示任务 HUD
Live2DManager.prototype.showAgentTaskHUD = function() {
    let hud = document.getElementById('agent-task-hud');
    if (!hud) {
        hud = this.createAgentTaskHUD();
    }
    hud.style.display = 'flex';
    hud.style.opacity = '1';
    hud.style.transform = 'translateY(-50%) translateX(0)';
};

// 隐藏任务 HUD
Live2DManager.prototype.hideAgentTaskHUD = function() {
    const hud = document.getElementById('agent-task-hud');
    if (hud) {
        hud.style.opacity = '0';
        hud.style.transform = 'translateY(-50%) translateX(20px)';
        setTimeout(() => {
            hud.style.display = 'none';
        }, 300);
    }
};

// 更新任务 HUD 内容
Live2DManager.prototype.updateAgentTaskHUD = function(tasksData) {
    const taskList = document.getElementById('agent-task-list');
    const emptyState = document.getElementById('agent-task-empty');
    const runningCount = document.getElementById('hud-running-count');
    const queuedCount = document.getElementById('hud-queued-count');
    
    if (!taskList) return;
    
    // 更新统计数据
    if (runningCount) runningCount.textContent = tasksData.running_count || 0;
    if (queuedCount) queuedCount.textContent = tasksData.queued_count || 0;
    
    // 获取活动任务（running 和 queued）
    const activeTasks = (tasksData.tasks || []).filter(t => 
        t.status === 'running' || t.status === 'queued'
    );
    
    // 显示/隐藏空状态（保留折叠状态）
    if (emptyState) {
        if (activeTasks.length === 0) {
            // 没有任务时显示空状态
            emptyState.style.display = 'block';
            emptyState.style.visibility = 'visible';
        } else {
            // 有任务时隐藏空状态，但保留折叠状态
            emptyState.style.display = 'none';
            emptyState.style.visibility = 'hidden';
        }
    }
    
    // 清除旧的任务卡片（保留空状态）
    const existingCards = taskList.querySelectorAll('.task-card');
    existingCards.forEach(card => card.remove());
    
    // 添加任务卡片
    activeTasks.forEach(task => {
        const card = this._createTaskCard(task);
        taskList.appendChild(card);
    });
};

// 创建单个任务卡片
Live2DManager.prototype._createTaskCard = function(task) {
    const card = document.createElement('div');
    card.className = 'task-card';
    card.dataset.taskId = task.id;
    if (task.start_time) {
        card.dataset.startTime = task.start_time;
    }
    
    const isRunning = task.status === 'running';
    const statusColor = isRunning ? '#fbbf24' : '#60a5fa';
    const statusText = isRunning 
        ? (window.t ? window.t('agent.taskHud.statusRunning') : '运行中') 
        : (window.t ? window.t('agent.taskHud.statusQueued') : '队列中');
    
    Object.assign(card.style, {
        background: 'rgba(30, 41, 59, 0.8)',
        borderRadius: '10px',
        padding: '12px',
        border: `1px solid ${isRunning ? 'rgba(251, 191, 36, 0.3)' : 'rgba(96, 165, 250, 0.2)'}`,
        transition: 'all 0.2s ease'
    });
    
    // 任务类型和状态
    const header = document.createElement('div');
    Object.assign(header.style, {
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        marginBottom: '8px'
    });
    
    // 任务类型图标
    const typeIcon = task.source === 'mcp' ? '🔌' : (task.source === 'computer_use' ? '🖱️' : '⚙️');
    const typeName = task.type || task.source || 'unknown';
    
    const typeLabel = document.createElement('span');
    typeLabel.innerHTML = `${typeIcon} <span style="color: #94a3b8; font-size: 11px;">${typeName}</span>`;
    
    const statusBadge = document.createElement('span');
    statusBadge.textContent = statusText;
    Object.assign(statusBadge.style, {
        color: statusColor,
        fontSize: '11px',
        fontWeight: '500',
        padding: '2px 8px',
        background: isRunning ? 'rgba(251, 191, 36, 0.15)' : 'rgba(96, 165, 250, 0.15)',
        borderRadius: '10px'
    });
    
    header.appendChild(typeLabel);
    header.appendChild(statusBadge);
    card.appendChild(header);
    
    // 任务参数/描述
    const params = task.params || {};
    let description = '';
    if (params.query) {
        description = params.query;
    } else if (params.instruction) {
        // computer_use 任务使用 instruction 字段
        description = params.instruction;
    } else if (task.original_query) {
        // planner 任务使用 original_query 字段
        description = task.original_query;
    } else if (params.tool_name) {
        description = params.tool_name;
    } else if (params.action) {
        description = params.action;
    } else {
        description = task.id?.substring(0, 8) || 'Task';
    }
    
    const descDiv = document.createElement('div');
    descDiv.textContent = description.length > 60 ? description.substring(0, 60) + '...' : description;
    Object.assign(descDiv.style, {
        color: '#cbd5e1',
        fontSize: '12px',
        lineHeight: '1.4',
        marginBottom: '8px',
        wordBreak: 'break-word'
    });
    card.appendChild(descDiv);
    
    // 运行时间
    if (task.start_time && isRunning) {
        const timeDiv = document.createElement('div');
        const startTime = new Date(task.start_time);
        const elapsed = Math.floor((Date.now() - startTime.getTime()) / 1000);
        const minutes = Math.floor(elapsed / 60);
        const seconds = elapsed % 60;
        
        timeDiv.id = `task-time-${task.id}`;
        timeDiv.innerHTML = `<span style="color: #64748b;">⏱️</span> ${minutes}:${seconds.toString().padStart(2, '0')}`;
        Object.assign(timeDiv.style, {
            color: '#94a3b8',
            fontSize: '11px',
            display: 'flex',
            alignItems: 'center',
            gap: '4px'
        });
        card.appendChild(timeDiv);
    }
    
    // 如果是运行中的任务，添加动画指示器
    if (isRunning) {
        const progressBar = document.createElement('div');
        Object.assign(progressBar.style, {
            height: '2px',
            background: 'rgba(251, 191, 36, 0.2)',
            borderRadius: '1px',
            marginTop: '8px',
            overflow: 'hidden'
        });
        
        const progressFill = document.createElement('div');
        Object.assign(progressFill.style, {
            height: '100%',
            width: '30%',
            background: 'linear-gradient(90deg, #fbbf24, #f59e0b)',
            borderRadius: '1px',
            animation: 'taskProgress 1.5s ease-in-out infinite'
        });
        progressBar.appendChild(progressFill);
        card.appendChild(progressBar);
    }
    
    return card;
};

// 设置HUD全局拖拽功能
Live2DManager.prototype._setupDragging = function(hud) {
    let isDragging = false;
    let dragOffsetX = 0;
    let dragOffsetY = 0;
    
    // 高性能拖拽函数
    const performDrag = (clientX, clientY) => {
        if (!isDragging) return;
        
        // 使用requestAnimationFrame确保流畅动画
        requestAnimationFrame(() => {
            // 计算新位置
            const newX = clientX - dragOffsetX;
            const newY = clientY - dragOffsetY;
            
            // 获取窗口尺寸和HUD尺寸
            const windowWidth = window.innerWidth;
            const windowHeight = window.innerHeight;
            const hudRect = hud.getBoundingClientRect();
            
            // 边界检查 - 确保HUD不会超出视口
            const constrainedX = Math.max(0, Math.min(newX, windowWidth - hudRect.width));
            const constrainedY = Math.max(0, Math.min(newY, windowHeight - hudRect.height));
            
            // 使用transform进行高性能定位
            hud.style.left = constrainedX + 'px';
            hud.style.top = constrainedY + 'px';
            hud.style.right = 'auto';
            hud.style.transform = 'none';
        });
    };
    
    // 鼠标按下事件 - 全局可拖动
    const handleMouseDown = (e) => {
        // 排除内部可交互元素
        const interactiveSelectors = ['button', 'input', 'textarea', 'select', 'a', '.task-card'];
        const isInteractive = e.target.closest(interactiveSelectors.join(','));
        
        if (isInteractive) return;
        
        isDragging = true;
        
        // 视觉反馈
        hud.style.cursor = 'grabbing';
        hud.style.boxShadow = '0 12px 48px rgba(0, 0, 0, 0.6), 0 0 0 1px rgba(255, 255, 255, 0.2)';
        hud.style.opacity = '0.95';
        hud.style.transition = 'none'; // 拖拽时禁用过渡动画
        
        const rect = hud.getBoundingClientRect();
        // 计算鼠标相对于HUD的偏移
        dragOffsetX = e.clientX - rect.left;
        dragOffsetY = e.clientY - rect.top;
        
        e.preventDefault();
        e.stopPropagation();
    };
    
    // 鼠标移动事件 - 高性能处理
    const handleMouseMove = (e) => {
        if (!isDragging) return;
        
        // 使用节流优化性能
        performDrag(e.clientX, e.clientY);
        
        e.preventDefault();
        e.stopPropagation();
    };
    
    // 鼠标释放事件
    const handleMouseUp = (e) => {
        if (!isDragging) return;
        
        isDragging = false;
        
        // 恢复视觉状态
        hud.style.cursor = 'move';
        hud.style.boxShadow = '0 8px 32px rgba(0, 0, 0, 0.4), 0 0 0 1px rgba(255, 255, 255, 0.1)';
        hud.style.opacity = '1';
        hud.style.transition = 'opacity 0.3s ease, transform 0.3s ease, box-shadow 0.2s ease';
        
        // 最终位置校准
        requestAnimationFrame(() => {
            const rect = hud.getBoundingClientRect();
            const windowWidth = window.innerWidth;
            const windowHeight = window.innerHeight;
            
            // 确保位置在视口内
            let finalLeft = parseFloat(hud.style.left) || 0;
            let finalTop = parseFloat(hud.style.top) || 0;
            
            finalLeft = Math.max(0, Math.min(finalLeft, windowWidth - rect.width));
            finalTop = Math.max(0, Math.min(finalTop, windowHeight - rect.height));
            
            hud.style.left = finalLeft + 'px';
            hud.style.top = finalTop + 'px';
            
            // 保存位置到localStorage
            const position = {
                left: hud.style.left,
                top: hud.style.top,
                right: hud.style.right,
                transform: hud.style.transform
            };
            
            try {
                localStorage.setItem('agent-task-hud-position', JSON.stringify(position));
            } catch (error) {
                console.warn('Failed to save position to localStorage:', error);
            }
        });
        
        e.preventDefault();
        e.stopPropagation();
    };
    
    // 绑定事件监听器 - 全局拖拽
    hud.addEventListener('mousedown', handleMouseDown);
    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
    
    // 防止在拖拽时选中文本
    hud.addEventListener('dragstart', (e) => e.preventDefault());
    
    // 触摸事件支持（移动设备）- 全局拖拽
    let touchDragging = false;
    let touchOffsetX = 0;
    let touchOffsetY = 0;
    
    // 触摸开始
    const handleTouchStart = (e) => {
        // 排除内部可交互元素
        const interactiveSelectors = ['button', 'input', 'textarea', 'select', 'a', '.task-card'];
        const isInteractive = e.target.closest(interactiveSelectors.join(','));
        
        if (isInteractive) return;
        
        touchDragging = true;
        isDragging = true;  // 让performDrag函数能正常工作
        
        // 视觉反馈
        hud.style.boxShadow = '0 12px 48px rgba(0, 0, 0, 0.6), 0 0 0 1px rgba(255, 255, 255, 0.2)';
        hud.style.opacity = '0.95';
        hud.style.transition = 'none';
        
        const touch = e.touches[0];
        const rect = hud.getBoundingClientRect();
        // 使用与鼠标事件相同的偏移量变量喵
        dragOffsetX = touch.clientX - rect.left;
        dragOffsetY = touch.clientY - rect.top;
        
        e.preventDefault();
    };
    
    // 触摸移动
    const handleTouchMove = (e) => {
        if (!touchDragging) return;
        
        const touch = e.touches[0];
        performDrag(touch.clientX, touch.clientY);
        
        e.preventDefault();
    };
    
    // 触摸结束
    const handleTouchEnd = (e) => {
        if (!touchDragging) return;
        
        touchDragging = false;
        isDragging = false;  // 确保performDrag函数停止工作
        
        // 恢复视觉状态
        hud.style.boxShadow = '0 8px 32px rgba(0, 0, 0, 0.4), 0 0 0 1px rgba(255, 255, 255, 0.1)';
        hud.style.opacity = '1';
        hud.style.transition = 'opacity 0.3s ease, transform 0.3s ease, box-shadow 0.2s ease';
        
        // 最终位置校准
        requestAnimationFrame(() => {
            const rect = hud.getBoundingClientRect();
            const windowWidth = window.innerWidth;
            const windowHeight = window.innerHeight;
            
            // 确保位置在视口内
            let finalLeft = parseFloat(hud.style.left) || 0;
            let finalTop = parseFloat(hud.style.top) || 0;
            
            finalLeft = Math.max(0, Math.min(finalLeft, windowWidth - rect.width));
            finalTop = Math.max(0, Math.min(finalTop, windowHeight - rect.height));
            
            hud.style.left = finalLeft + 'px';
            hud.style.top = finalTop + 'px';
            
            // 保存位置到localStorage
            const position = {
                left: hud.style.left,
                top: hud.style.top,
                right: hud.style.right,
                transform: hud.style.transform
            };
            
            try {
                localStorage.setItem('agent-task-hud-position', JSON.stringify(position));
            } catch (error) {
                console.warn('Failed to save position to localStorage:', error);
            }
        });
        
        e.preventDefault();
    };
    
    // 绑定触摸事件
    hud.addEventListener('touchstart', handleTouchStart, { passive: false });
    document.addEventListener('touchmove', handleTouchMove, { passive: false });
    document.addEventListener('touchend', handleTouchEnd, { passive: false });
    
    // 窗口大小变化时重新校准位置
    const handleResize = () => {
        if (isDragging || touchDragging) return;
        
        requestAnimationFrame(() => {
            const rect = hud.getBoundingClientRect();
            const windowWidth = window.innerWidth;
            const windowHeight = window.innerHeight;
            
            // 如果HUD超出视口，调整到可见位置
            if (rect.left < 0 || rect.top < 0 || 
                rect.right > windowWidth || rect.bottom > windowHeight) {
                
                let newLeft = parseFloat(hud.style.left) || 0;
                let newTop = parseFloat(hud.style.top) || 0;
                
                newLeft = Math.max(0, Math.min(newLeft, windowWidth - rect.width));
                newTop = Math.max(0, Math.min(newTop, windowHeight - rect.height));
                
                hud.style.left = newLeft + 'px';
                hud.style.top = newTop + 'px';
                
                // 更新保存的位置
                const position = {
                    left: hud.style.left,
                    top: hud.style.top,
                    right: hud.style.right,
                    transform: hud.style.transform
                };
                
                try {
                    localStorage.setItem('agent-task-hud-position', JSON.stringify(position));
                } catch (error) {
                    console.warn('Failed to save position to localStorage:', error);
                }
            }
        });
    };
    
    window.addEventListener('resize', handleResize);
    
    // 清理函数
    this._cleanupDragging = () => {
        hud.removeEventListener('mousedown', handleMouseDown);
        document.removeEventListener('mousemove', handleMouseMove);
        document.removeEventListener('mouseup', handleMouseUp);
        hud.removeEventListener('touchstart', handleTouchStart);
        document.removeEventListener('touchmove', handleTouchMove);
        document.removeEventListener('touchend', handleTouchEnd);
        window.removeEventListener('resize', handleResize);
    };
};

// 添加任务进度动画样式
(function() {
    if (document.getElementById('agent-task-hud-styles')) return;
    
    const style = document.createElement('style');
    style.id = 'agent-task-hud-styles';
    style.textContent = `
        @keyframes taskProgress {
            0% { transform: translateX(-100%); }
            50% { transform: translateX(200%); }
            100% { transform: translateX(-100%); }
        }
        
        /* 请她回来按钮呼吸特效 */
        @keyframes returnButtonBreathing {
            0%, 100% {
                box-shadow: 0 0 8px rgba(68, 183, 254, 0.6), 0 2px 4px rgba(0, 0, 0, 0.04), 0 8px 16px rgba(0, 0, 0, 0.08);
            }
            50% {
                box-shadow: 0 0 18px rgba(68, 183, 254, 1), 0 2px 4px rgba(0, 0, 0, 0.04), 0 8px 16px rgba(0, 0, 0, 0.08);
            }
        }
        
        #live2d-btn-return {
            animation: returnButtonBreathing 2s ease-in-out infinite;
        }
        
        #live2d-btn-return:hover {
            animation: none;
        }
        
        #agent-task-hud::-webkit-scrollbar {
            width: 4px;
        }
        
        #agent-task-hud::-webkit-scrollbar-track {
            background: rgba(255, 255, 255, 0.05);
            border-radius: 2px;
        }
        
        #agent-task-hud::-webkit-scrollbar-thumb {
            background: rgba(255, 255, 255, 0.2);
            border-radius: 2px;
        }
        
        #agent-task-list::-webkit-scrollbar {
            width: 4px;
        }
        
        #agent-task-list::-webkit-scrollbar-track {
            background: transparent;
        }
        
        #agent-task-list::-webkit-scrollbar-thumb {
            background: rgba(255, 255, 255, 0.15);
            border-radius: 2px;
        }
        
        .task-card:hover {
            background: rgba(51, 65, 85, 0.8) !important;
            transform: translateX(-2px);
        }
        
        /* 折叠功能样式 */
        #agent-task-empty {
            position: relative;
            transition: all 0.3s ease;
            overflow: hidden;
        }
        
        #agent-task-empty > div:first-child {
            transition: all 0.3s ease;
            opacity: 1;
            height: auto;
            padding: 20px;
            margin: 0;
        }
        
        #agent-task-empty.collapsed > div:first-child {
            opacity: 0;
            height: 0;
            padding: 0;
            margin: 0;
        }
        
        .collapse-button {
            position: absolute;
            top: 8px;
            right: 8px;
            width: 20px;
            height: 20px;
            border-radius: 50%;
            background: rgba(100, 116, 139, 0.3);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 10px;
            color: #64748b;
            cursor: pointer;
            transition: all 0.2s ease;
            z-index: 1;
            user-select: none;
            -webkit-user-select: none;
            -moz-user-select: none;
            -ms-user-select: none;
        }
        
        .collapse-button:hover {
            background: rgba(100, 116, 139, 0.6);
            transform: scale(1.1);
        }
        
        .collapse-button:active {
            transform: scale(0.95);
        }
        
        .collapse-button.collapsed {
            background: rgba(100, 116, 139, 0.5);
            color: #94a3b8;
        }
        
        /* 移动设备优化 */
        @media (max-width: 768px) {
            .collapse-button {
                width: 24px;
                height: 24px;
                font-size: 12px;
                top: 6px;
                right: 6px;
            }
            
            .collapse-button:hover {
                transform: scale(1.05);
            }
        }
    `;
    document.head.appendChild(style);
})();

// 创建设置弹出框内容
Live2DManager.prototype._createSettingsPopupContent = function(popup) {
    // 先添加 Focus 模式和主动搭话开关（在最上面）
    const settingsToggles = [
        { id: 'focus-mode', label: window.t ? window.t('settings.toggles.allowInterrupt') : '允许打断', labelKey: 'settings.toggles.allowInterrupt', storageKey: 'focusModeEnabled', inverted: true }, // inverted表示值与focusModeEnabled相反
        { id: 'proactive-chat', label: window.t ? window.t('settings.toggles.proactiveChat') : '主动搭话', labelKey: 'settings.toggles.proactiveChat', storageKey: 'proactiveChatEnabled' }
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
Live2DManager.prototype._createToggleItem = function(toggle, popup) {
    const toggleItem = document.createElement('div');
    Object.assign(toggleItem.style, {
        display: 'flex',
        alignItems: 'center',
        gap: '8px',
        padding: '6px 8px',
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
    
    // 【修复】如果配置了初始禁用状态，则禁用 checkbox
    if (toggle.initialDisabled) {
        checkbox.disabled = true;
        checkbox.title = window.t ? window.t('settings.toggles.checking') : '查询中...';
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
Live2DManager.prototype._createSettingsToggleItem = function(toggle, popup) {
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
    if (toggle.id === 'focus-mode' && typeof window.focusModeEnabled !== 'undefined') {
        // inverted: 允许打断 = !focusModeEnabled（focusModeEnabled为true表示关闭打断）
        checkbox.checked = toggle.inverted ? !window.focusModeEnabled : window.focusModeEnabled;
    } else if (toggle.id === 'proactive-chat' && typeof window.proactiveChatEnabled !== 'undefined') {
        checkbox.checked = window.proactiveChatEnabled;
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
        if (toggle.id === 'focus-mode') {
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
Live2DManager.prototype._createSettingsMenuItems = function(popup) {
    const settingsItems = [
        { id: 'live2d-manage', label: window.t ? window.t('settings.menu.live2dSettings') : 'Live2D设置', labelKey: 'settings.menu.live2dSettings', icon: '/static/icons/live2d_settings_icon.png', action: 'navigate', urlBase: '/l2d' },
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
                        
                        // 监听窗口关闭事件，清除引用
                        const checkClosed = setInterval(() => {
                            if (newWindow.closed) {
                                delete this._openSettingsWindows[finalUrl];
                                clearInterval(checkClosed);
                            }
                        }, 500);
                    }
                }
            }
        });
        
        popup.appendChild(menuItem);
    });
};

// 关闭指定按钮对应的弹出框，并恢复按钮状态
Live2DManager.prototype.closePopupById = function(buttonId) {
    if (!buttonId) return false;
    const popup = document.getElementById(`live2d-popup-${buttonId}`);
    if (!popup || popup.style.display !== 'flex') {
        return false;
    }

    // 如果是 agent 弹窗关闭，派发关闭事件
    if (buttonId === 'agent') {
        window.dispatchEvent(new CustomEvent('live2d-agent-popup-closed'));
    }

    popup.style.opacity = '0';
    popup.style.transform = 'translateX(-10px)';
    setTimeout(() => {
        popup.style.display = 'none';
    }, 200);

    const buttonEntry = this._floatingButtons[buttonId];
    if (buttonEntry && buttonEntry.button) {
        buttonEntry.button.dataset.active = 'false';
        buttonEntry.button.style.background = 'rgba(255, 255, 255, 0.65)';  // Fluent Acrylic

        if (buttonEntry.imgOff && buttonEntry.imgOn) {
            buttonEntry.imgOff.style.opacity = '1';
            buttonEntry.imgOn.style.opacity = '0';
        }
    }

    if (this._popupTimers[buttonId]) {
        clearTimeout(this._popupTimers[buttonId]);
        this._popupTimers[buttonId] = null;
    }

    return true;
};

// 关闭除当前按钮之外的所有弹出框
Live2DManager.prototype.closeAllPopupsExcept = function(currentButtonId) {
    const popups = document.querySelectorAll('[id^="live2d-popup-"]');
    popups.forEach(popup => {
        const popupId = popup.id.replace('live2d-popup-', '');
        if (popupId !== currentButtonId && popup.style.display === 'flex') {
            this.closePopupById(popupId);
        }
    });
};

// 关闭所有通过 window.open 打开的设置窗口，可选保留特定 URL
Live2DManager.prototype.closeAllSettingsWindows = function(exceptUrl = null) {
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

// 为按钮容器设置拖动功能
Live2DManager.prototype.setupButtonsContainerDrag = function(buttonsContainer) {
    let isDragging = false;
    let dragStartX = 0;
    let dragStartY = 0;
    let containerStartX = 0;
    let containerStartY = 0;
    let isClick = false; // 标记是否为点击操作（与返回按钮拖动一致的语义）
    
    // 鼠标按下事件
    buttonsContainer.addEventListener('mousedown', (e) => {
        // 只在按钮容器本身被点击时开始拖动（不是按钮）
        if (e.target === buttonsContainer) {
            isDragging = true;
            isClick = true; // 初始标记为点击
            dragStartX = e.clientX;
            dragStartY = e.clientY;

            // 获取当前容器位置
            const currentLeft = parseInt(buttonsContainer.style.left) || 0;
            const currentTop = parseInt(buttonsContainer.style.top) || 0;
            containerStartX = currentLeft;
            containerStartY = currentTop;

            // 设置拖拽标记（初始为false）
            buttonsContainer.setAttribute('data-dragging', 'false');

            // 改变鼠标样式
            buttonsContainer.style.cursor = 'grabbing';
            e.preventDefault();
        }
    });
    
    // 鼠标移动事件
    document.addEventListener('mousemove', (e) => {
        if (isDragging) {
            const deltaX = e.clientX - dragStartX;
            const deltaY = e.clientY - dragStartY;
            
            // 如果移动距离超过阈值，则认为是拖拽而不是点击
            const dragThreshold = 5; // 5像素阈值
            if (Math.abs(deltaX) > dragThreshold || Math.abs(deltaY) > dragThreshold) {
                isClick = false;
                buttonsContainer.setAttribute('data-dragging', 'true');
            }
            
            const newX = containerStartX + deltaX;
            const newY = containerStartY + deltaY;
            
            // 限制在屏幕范围内
            const screenWidth = window.innerWidth;
            const screenHeight = window.innerHeight;
            const containerWidth = buttonsContainer.offsetWidth || 80;
            const containerHeight = buttonsContainer.offsetHeight || 200;
            
            const boundedX = Math.max(0, Math.min(newX, screenWidth - containerWidth));
            const boundedY = Math.max(0, Math.min(newY, screenHeight - containerHeight));
            
            buttonsContainer.style.left = `${boundedX}px`;
            buttonsContainer.style.top = `${boundedY}px`;
        }
    });
    
    // 鼠标释放事件
    document.addEventListener('mouseup', (e) => {
        if (isDragging) {
            // 稍后重置拖拽标记，给事件处理时间
            setTimeout(() => {
                buttonsContainer.setAttribute('data-dragging', 'false');
            }, 10);
            
            isDragging = false;
            isClick = false;
            buttonsContainer.style.cursor = 'grab';
        }
    });
    
    // 设置初始鼠标样式
    buttonsContainer.style.cursor = 'grab';
    
    // 触摸事件支持
    buttonsContainer.addEventListener('touchstart', (e) => {
        if (e.target === buttonsContainer) {
            isDragging = true;
            isClick = true;
            const touch = e.touches[0];
            dragStartX = touch.clientX;
            dragStartY = touch.clientY;

            const currentLeft = parseInt(buttonsContainer.style.left) || 0;
            const currentTop = parseInt(buttonsContainer.style.top) || 0;
            containerStartX = currentLeft;
            containerStartY = currentTop;

            buttonsContainer.setAttribute('data-dragging', 'false');
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
                buttonsContainer.setAttribute('data-dragging', 'true');
            }
            
            const newX = containerStartX + deltaX;
            const newY = containerStartY + deltaY;
            
            const screenWidth = window.innerWidth;
            const screenHeight = window.innerHeight;
            const containerWidth = buttonsContainer.offsetWidth || 80;
            const containerHeight = buttonsContainer.offsetHeight || 200;
            
            const boundedX = Math.max(0, Math.min(newX, screenWidth - containerWidth));
            const boundedY = Math.max(0, Math.min(newY, screenHeight - containerHeight));
            
            buttonsContainer.style.left = `${boundedX}px`;
            buttonsContainer.style.top = `${boundedY}px`;
            e.preventDefault();
        }
    });
    
    document.addEventListener('touchend', (e) => {
        if (isDragging) {
            setTimeout(() => {
                buttonsContainer.setAttribute('data-dragging', 'false');
            }, 10);
            
            isDragging = false;
            isClick = false;
        }
    });
};

// 为"请她回来"按钮容器设置拖动功能
Live2DManager.prototype.setupReturnButtonContainerDrag = function(returnButtonContainer) {
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

            const currentLeft = parseInt(returnButtonContainer.style.left) || 0;
            const currentTop = parseInt(returnButtonContainer.style.top) || 0;
            containerStartX = currentLeft;
            containerStartY = currentTop;

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
            
            const screenWidth = window.innerWidth;
            const screenHeight = window.innerHeight;
            const containerWidth = returnButtonContainer.offsetWidth || 64;
            const containerHeight = returnButtonContainer.offsetHeight || 64;
            
            const boundedX = Math.max(0, Math.min(newX, screenWidth - containerWidth));
            const boundedY = Math.max(0, Math.min(newY, screenHeight - containerHeight));
            
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

            const currentLeft = parseInt(returnButtonContainer.style.left) || 0;
            const currentTop = parseInt(returnButtonContainer.style.top) || 0;
            containerStartX = currentLeft;
            containerStartY = currentTop;

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
            
            const screenWidth = window.innerWidth;
            const screenHeight = window.innerHeight;
            const containerWidth = returnButtonContainer.offsetWidth || 64;
            const containerHeight = returnButtonContainer.offsetHeight || 64;
            
            const boundedX = Math.max(0, Math.min(newX, screenWidth - containerWidth));
            const boundedY = Math.max(0, Math.min(newY, screenHeight - containerHeight));
            
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

// 显示弹出框（1秒后自动隐藏），支持点击切换
Live2DManager.prototype.showPopup = function(buttonId, popup) {
    // 检查当前状态
    const isVisible = popup.style.display === 'flex' && popup.style.opacity === '1';
    
    // 清除之前的定时器
    if (this._popupTimers[buttonId]) {
        clearTimeout(this._popupTimers[buttonId]);
        this._popupTimers[buttonId] = null;
    }
    
    // 如果是设置弹出框，每次显示时更新开关状态（确保与 app.js 同步）
    if (buttonId === 'settings') {
        const focusCheckbox = popup.querySelector('#live2d-focus-mode');
        const proactiveChatCheckbox = popup.querySelector('#live2d-proactive-chat');
        
        // 辅助函数：更新 checkbox 的视觉样式
        const updateCheckboxStyle = (checkbox) => {
            if (!checkbox) return;
            // toggleItem 是 checkbox 的父元素
            const toggleItem = checkbox.parentElement;
            if (!toggleItem) return;
            
            // indicator 是 toggleItem 的第二个子元素（第一个是 checkbox，第二个是 indicator）
            const indicator = toggleItem.children[1];
            if (!indicator) return;
            
            // checkmark 是 indicator 的第一个子元素
            const checkmark = indicator.firstElementChild;
            
            if (checkbox.checked) {
                // 选中状态
                indicator.style.backgroundColor = '#44b7fe';
                indicator.style.borderColor = '#44b7fe';
                if (checkmark) checkmark.style.opacity = '1';
                toggleItem.style.background = 'rgba(68, 183, 254, 0.1)';
            } else {
                // 未选中状态
                indicator.style.backgroundColor = 'transparent';
                indicator.style.borderColor = '#ccc';
                if (checkmark) checkmark.style.opacity = '0';
                toggleItem.style.background = 'transparent';
            }
        };
        
        // 更新 focus mode checkbox 状态和视觉样式
        if (focusCheckbox && typeof window.focusModeEnabled !== 'undefined') {
            // "允许打断"按钮值与 focusModeEnabled 相反
            const newChecked = !window.focusModeEnabled;
            // 只在状态改变时更新，避免不必要的 DOM 操作
            if (focusCheckbox.checked !== newChecked) {
                focusCheckbox.checked = newChecked;
                // 使用 requestAnimationFrame 确保 DOM 已更新后再更新样式
                requestAnimationFrame(() => {
                    updateCheckboxStyle(focusCheckbox);
                });
            } else {
                // 即使状态相同，也确保视觉样式正确（处理概率性问题）
                requestAnimationFrame(() => {
                    updateCheckboxStyle(focusCheckbox);
                });
            }
        }
        
        // 更新 proactive chat checkbox 状态和视觉样式
        if (proactiveChatCheckbox && typeof window.proactiveChatEnabled !== 'undefined') {
            const newChecked = window.proactiveChatEnabled;
            // 只在状态改变时更新，避免不必要的 DOM 操作
            if (proactiveChatCheckbox.checked !== newChecked) {
                proactiveChatCheckbox.checked = newChecked;
                requestAnimationFrame(() => {
                    updateCheckboxStyle(proactiveChatCheckbox);
                });
            } else {
                // 即使状态相同，也确保视觉样式正确（处理概率性问题）
                requestAnimationFrame(() => {
                    updateCheckboxStyle(proactiveChatCheckbox);
                });
            }
        }
    }
    
    // 如果是 agent 弹窗，触发服务器状态检查事件
    if (buttonId === 'agent' && !isVisible) {
        // 弹窗即将显示，派发事件让 app.js 检查服务器状态
        window.dispatchEvent(new CustomEvent('live2d-agent-popup-opening'));
    }
    
    if (isVisible) {
        // 如果已经显示，则隐藏
        popup.style.opacity = '0';
        popup.style.transform = 'translateX(-10px)';
        
        // 如果是 agent 弹窗关闭，派发关闭事件
        if (buttonId === 'agent') {
            window.dispatchEvent(new CustomEvent('live2d-agent-popup-closed'));
        }
        
        setTimeout(() => {
            popup.style.display = 'none';
            // 重置位置和样式
            popup.style.left = '100%';
            popup.style.right = 'auto';
            popup.style.top = '0';
            popup.style.marginLeft = '8px';
            popup.style.marginRight = '0';
            // 重置高度限制，确保下次打开时状态一致
            if (buttonId === 'settings' || buttonId === 'agent') {
                popup.style.maxHeight = '200px';
                popup.style.overflowY = 'auto';
            }
        }, 200);
    } else {
        // 全局互斥：打开前关闭其他弹出框
        this.closeAllPopupsExcept(buttonId);

        // 如果隐藏，则显示
        popup.style.display = 'flex';
        // 先让弹出框可见但透明，以便计算尺寸
        popup.style.opacity = '0';
        popup.style.visibility = 'visible';
        
        // 关键：在计算位置之前，先移除高度限制，确保获取真实尺寸
        if (buttonId === 'settings' || buttonId === 'agent') {
            popup.style.maxHeight = 'none';
            popup.style.overflowY = 'visible';
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
            // 强制触发reflow，确保布局完全更新
            void popup.offsetHeight;
            
            // 再次使用RAF确保布局稳定
            requestAnimationFrame(() => {
                const popupRect = popup.getBoundingClientRect();
                const screenWidth = window.innerWidth;
                const screenHeight = window.innerHeight;
                const rightMargin = 20; // 距离屏幕右侧的安全边距
                const bottomMargin = 60; // 距离屏幕底部的安全边距（考虑系统任务栏，Windows任务栏约40-48px）
                
                // 检查是否超出屏幕右侧
                const popupRight = popupRect.right;
                if (popupRight > screenWidth - rightMargin) {
                    // 超出右边界，改为向左弹出
                    // 获取按钮的实际宽度来计算正确的偏移
                    const button = document.getElementById(`live2d-btn-${buttonId}`);
                    const buttonWidth = button ? button.offsetWidth : 48;
                    const gap = 8;
                    
                    // 让弹出框完全移到按钮左侧，不遮挡按钮
                    popup.style.left = 'auto';
                    popup.style.right = '0';
                    popup.style.marginLeft = '0';
                    popup.style.marginRight = `${buttonWidth + gap}px`;
                    popup.style.transform = 'translateX(10px)'; // 反向动画
                }
                
                // 检查是否超出屏幕底部（设置弹出框或其他较高的弹出框）
                if (buttonId === 'settings' || buttonId === 'agent') {
                    const popupBottom = popupRect.bottom;
                    if (popupBottom > screenHeight - bottomMargin) {
                        // 计算需要向上移动的距离
                        const overflow = popupBottom - (screenHeight - bottomMargin);
                        const currentTop = parseInt(popup.style.top) || 0;
                        const newTop = currentTop - overflow;
                        popup.style.top = `${newTop}px`;
                    }
                }
                
                // 显示弹出框
                popup.style.visibility = 'visible';
                popup.style.opacity = '1';
                popup.style.transform = 'translateX(0)';
            });
        });
        
        // 设置、agent、麦克风弹出框不自动隐藏，其他的1秒后隐藏
        if (buttonId !== 'settings' && buttonId !== 'agent' && buttonId !== 'mic') {
            this._popupTimers[buttonId] = setTimeout(() => {
                popup.style.opacity = '0';
                popup.style.transform = popup.style.right === '100%' ? 'translateX(10px)' : 'translateX(-10px)';
                setTimeout(() => {
                    popup.style.display = 'none';
                    // 重置位置
                    popup.style.left = '100%';
                    popup.style.right = 'auto';
                    popup.style.top = '0';
                }, 200);
                this._popupTimers[buttonId] = null;
            }, 1000);
        }
    }
};

// 设置折叠功能
Live2DManager.prototype._setupCollapseFunctionality = function(emptyState, collapseButton, emptyContent) {
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

