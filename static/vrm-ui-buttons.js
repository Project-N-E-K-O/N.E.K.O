/**
 * VRM UI Buttons - 浮动按钮系统（功能同步修复版）
 */

// 设置浮动按钮系统
VRMManager.prototype.setupFloatingButtons = function () {
    // 如果是模型管理页面，直接禁止创建浮动按钮（在最开头检查，避免后续资源初始化）
    if (window.location.pathname.includes('model_manager')) {
        return; 
    }
    
    // 清理旧的事件监听器（使用 UI 模块专用的 handlers 数组）
    if (!this._uiWindowHandlers) {
        this._uiWindowHandlers = [];
    }
    if (this._uiWindowHandlers.length > 0) {
        this._uiWindowHandlers.forEach(({ event, handler }) => {
            window.removeEventListener(event, handler);
        });
        this._uiWindowHandlers = [];
    }
    
    // 清理旧的 document 事件监听器
    if (this._returnButtonDragHandlers) {
        document.removeEventListener('mousemove', this._returnButtonDragHandlers.mouseMove);
        document.removeEventListener('mouseup', this._returnButtonDragHandlers.mouseUp);
        document.removeEventListener('touchmove', this._returnButtonDragHandlers.touchMove);
        document.removeEventListener('touchend', this._returnButtonDragHandlers.touchEnd);
        this._returnButtonDragHandlers = null;
    }
    const container = document.getElementById('vrm-container');

    document.querySelectorAll('#live2d-floating-buttons').forEach(el => el.remove());
    const buttonsContainerId = 'vrm-floating-buttons';
    const old = document.getElementById(buttonsContainerId);
    if (old) old.remove();

    const buttonsContainer = document.createElement('div');
    buttonsContainer.id = buttonsContainerId; 
    document.body.appendChild(buttonsContainer);
    
    // 设置基础样式
    Object.assign(buttonsContainer.style, {
        position: 'fixed', zIndex: '99999', pointerEvents: 'auto',  
        display: 'none', // 初始隐藏 (由 update loop 或 resize 控制显示)
        flexDirection: 'column', gap: '12px',
        visibility: 'visible', opacity: '1', transform: 'none'
    });
    this._floatingButtonsContainer = buttonsContainer;

    const stopContainerEvent = (e) => { e.stopPropagation(); };
    ['pointerdown','pointermove','pointerup','mousedown','mousemove','mouseup','touchstart','touchmove','touchend'].forEach(evt => {
        buttonsContainer.addEventListener(evt, stopContainerEvent);
    });

    const applyResponsiveFloatingLayout = () => {
        const isLocked = this.interaction && this.interaction.checkLocked ? this.interaction.checkLocked() : false;
        if (isLocked) {
            buttonsContainer.style.display = 'none';
            return;
        }
        if (window.isMobileWidth()) {
            buttonsContainer.style.flexDirection = 'column';
            buttonsContainer.style.bottom = '116px';
            buttonsContainer.style.right = '16px';
            buttonsContainer.style.left = '';
            buttonsContainer.style.top = '';
            buttonsContainer.style.display = 'flex';
        } else {
            buttonsContainer.style.flexDirection = 'column';
            buttonsContainer.style.bottom = '';
            buttonsContainer.style.right = '';
            buttonsContainer.style.display = '';
        }
    };
    applyResponsiveFloatingLayout();
    this._uiWindowHandlers.push({ event: 'resize', handler: applyResponsiveFloatingLayout });
    window.addEventListener('resize', applyResponsiveFloatingLayout);

    const iconVersion = '?v=' + (window.APP_VERSION || '1.0.0');
    const buttonConfigs = [
        { id: 'mic', emoji: '🎤', title: window.t ? window.t('buttons.voiceControl') : '语音控制', titleKey: 'buttons.voiceControl', hasPopup: true, toggle: true, separatePopupTrigger: true, iconOff: '/static/icons/mic_icon_off.png'+iconVersion, iconOn: '/static/icons/mic_icon_on.png'+iconVersion },
        { id: 'screen', emoji: '🖥️', title: window.t ? window.t('buttons.screenShare') : '屏幕分享', titleKey: 'buttons.screenShare', hasPopup: true, toggle: true, separatePopupTrigger: true, iconOff: '/static/icons/screen_icon_off.png'+iconVersion, iconOn: '/static/icons/screen_icon_on.png'+iconVersion },
        { id: 'agent', emoji: '🔨', title: window.t ? window.t('buttons.agentTools') : 'Agent工具', titleKey: 'buttons.agentTools', hasPopup: true, popupToggle: true, exclusive: 'settings', iconOff: '/static/icons/Agent_off.png'+iconVersion, iconOn: '/static/icons/Agent_on.png'+iconVersion },
        { id: 'settings', emoji: '⚙️', title: window.t ? window.t('buttons.settings') : '设置', titleKey: 'buttons.settings', hasPopup: true, popupToggle: true, exclusive: 'agent', iconOff: '/static/icons/set_off.png'+iconVersion, iconOn: '/static/icons/set_on.png'+iconVersion },
        { id: 'goodbye', emoji: '💤', title: window.t ? window.t('buttons.leave') : '请她离开', titleKey: 'buttons.leave', hasPopup: false, iconOff: '/static/icons/rest_off.png'+iconVersion, iconOn: '/static/icons/rest_on.png'+iconVersion }
    ];

    this._floatingButtons = this._floatingButtons || {};

    // 3. 创建按钮
    buttonConfigs.forEach(config => {
        if (window.isMobileWidth() && (config.id === 'agent' || config.id === 'goodbye')) {
            return;
        }

        const btnWrapper = document.createElement('div');
        Object.assign(btnWrapper.style, { position: 'relative', display: 'flex', alignItems: 'center', gap: '8px', pointerEvents: 'auto' });
        ['pointerdown','mousedown','touchstart'].forEach(evt => btnWrapper.addEventListener(evt, e => e.stopPropagation()));

        const btn = document.createElement('div');
        btn.id = `vrm-btn-${config.id}`;
        btn.className = 'vrm-floating-btn';
        
        Object.assign(btn.style, {
            width: '48px', height: '48px', borderRadius: '50%', background: 'rgba(255, 255, 255, 0.65)',
            backdropFilter: 'saturate(180%) blur(20px)', border: '1px solid rgba(255, 255, 255, 0.18)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '24px',
            cursor: 'pointer', userSelect: 'none', boxShadow: '0 2px 4px rgba(0, 0, 0, 0.04)',
            transition: 'all 0.1s ease', pointerEvents: 'auto'
        });

        let imgOff = null;
        let imgOn = null;

        if (config.iconOff && config.iconOn) {
            const imgContainer = document.createElement('div');
            Object.assign(imgContainer.style, { position: 'relative', width: '48px', height: '48px', display: 'flex', alignItems: 'center', justifyContent: 'center' });
            
            imgOff = document.createElement('img');
            imgOff.src = config.iconOff; imgOff.alt = config.emoji;
            Object.assign(imgOff.style, { position: 'absolute', width: '48px', height: '48px', objectFit: 'contain', pointerEvents: 'none', opacity: '1', transition: 'opacity 0.3s ease' });
            
            imgOn = document.createElement('img');
            imgOn.src = config.iconOn; imgOn.alt = config.emoji;
            Object.assign(imgOn.style, { position: 'absolute', width: '48px', height: '48px', objectFit: 'contain', pointerEvents: 'none', opacity: '0', transition: 'opacity 0.3s ease' });

            imgContainer.appendChild(imgOff);
            imgContainer.appendChild(imgOn);
            btn.appendChild(imgContainer);

            // 注册按钮到管理器
            this._floatingButtons[config.id] = {
                button: btn,
                imgOff: imgOff,
                imgOn: imgOn
            };

            // 悬停效果
            btn.addEventListener('mouseenter', () => {
                btn.style.transform = 'scale(1.05)';
                btn.style.background = 'rgba(255, 255, 255, 0.8)';
                
                // 检查是否有单独的弹窗触发器且弹窗已打开
                if (config.separatePopupTrigger) {
                    const popup = document.getElementById(`vrm-popup-${config.id}`);
                    const isPopupVisible = popup && popup.style.display === 'flex' && popup.style.opacity === '1';
                    if (isPopupVisible) return;
                }

                if (imgOff && imgOn) { imgOff.style.opacity = '0'; imgOn.style.opacity = '1'; }
            });
            
            btn.addEventListener('mouseleave', () => {
                btn.style.transform = 'scale(1)';
                const isActive = btn.dataset.active === 'true';
                const popup = document.getElementById(`vrm-popup-${config.id}`);
                const isPopupVisible = popup && popup.style.display === 'flex' && popup.style.opacity === '1';
                
                // 逻辑同 Live2D：如果是 separatePopupTrigger，只看 active；否则 active 或 popup 显示都算激活
                const shouldShowOnIcon = config.separatePopupTrigger 
                    ? isActive 
                    : (isActive || isPopupVisible);

                btn.style.background = shouldShowOnIcon ? 'rgba(255, 255, 255, 0.75)' : 'rgba(255, 255, 255, 0.65)';
                if (imgOff && imgOn) {
                    imgOff.style.opacity = shouldShowOnIcon ? '0' : '1';
                    imgOn.style.opacity = shouldShowOnIcon ? '1' : '0';
                }
            });

            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                e.preventDefault();

                if (config.id === 'mic') {
                    // 检查全局状态：window.isMicStarting 由语音控制模块设置，表示麦克风正在启动
                    const isMicStarting = window.isMicStarting || false;
                    if (isMicStarting) {
                        if (btn.dataset.active !== 'true') {
                            this.setButtonActive(config.id, true);
                        }
                        return; 
                    }
                }
                if (config.id === 'screen') {
                    // 检查全局状态：window.isRecording 由语音控制模块设置，表示正在录音/通话中
                    // 屏幕分享功能仅在音视频通话时可用
                    const isRecording = window.isRecording || false;
                    const wantToActivate = btn.dataset.active !== 'true';
                    if (wantToActivate && !isRecording) {
                        if (typeof window.showStatusToast === 'function') {
                            window.showStatusToast(
                                window.t ? window.t('app.screenShareRequiresVoice') : '屏幕分享仅用于音视频通话',
                                3000
                            );
                        }
                        return;
                    }
                }

                if (config.popupToggle) {
                    return;
                }

                const currentActive = btn.dataset.active === 'true';
                let targetActive = !currentActive; 
                
                if (config.id === 'mic' || config.id === 'screen') {
                   window.dispatchEvent(new CustomEvent(`live2d-${config.id}-toggle`, {detail:{active:targetActive}}));
                   this.setButtonActive(config.id, targetActive);
                }
                else if (config.id === 'goodbye') {
                    window.dispatchEvent(new CustomEvent('live2d-goodbye-click'));
                    return;
                }

                btn.style.background = targetActive ? 'rgba(255, 255, 255, 0.75)' : 'rgba(255, 255, 255, 0.8)';
            });
        }

        btnWrapper.appendChild(btn);

        if (config.hasPopup && config.separatePopupTrigger) {
            if (window.isMobileWidth() && config.id === 'mic') {
                buttonsContainer.appendChild(btnWrapper);
                return;
            }

            const popup = this.createPopup(config.id);
            const triggerBtn = document.createElement('div');
            triggerBtn.innerText = '▶'; 
            Object.assign(triggerBtn.style, {
                width: '24px', height: '24px', borderRadius: '50%',
                background: 'rgba(255, 255, 255, 0.65)', backdropFilter: 'saturate(180%) blur(20px)',
                border: '1px solid rgba(255, 255, 255, 0.18)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: '13px', color: '#44b7fe', cursor: 'pointer', userSelect: 'none',
                boxShadow: '0 2px 4px rgba(0, 0, 0, 0.04)', transition: 'all 0.1s ease', pointerEvents: 'auto',
                marginLeft: '-10px'
            });

            const stopTriggerEvent = (e) => { e.stopPropagation(); };
            ['pointerdown','mousedown','touchstart'].forEach(evt => triggerBtn.addEventListener(evt, stopTriggerEvent));

            triggerBtn.addEventListener('click', async (e) => {
                e.stopPropagation();
                const isPopupVisible = popup.style.display === 'flex' && popup.style.opacity === '1';
                if (config.id === 'mic' && !isPopupVisible) {
                    await this.renderMicList(popup);
                }
                if (config.id === 'screen' && !isPopupVisible) {
                    await this.renderScreenSourceList(popup);
                }

                this.showPopup(config.id, popup);
            });

            const triggerWrapper = document.createElement('div');
            triggerWrapper.style.position = 'relative';
            ['pointerdown','mousedown','touchstart'].forEach(evt => triggerWrapper.addEventListener(evt, stopTriggerEvent));
            
            triggerWrapper.appendChild(triggerBtn);
            triggerWrapper.appendChild(popup);
            btnWrapper.appendChild(triggerWrapper);
        }
        else if (config.popupToggle) {
            const popup = this.createPopup(config.id);
            btnWrapper.appendChild(btn);
            btnWrapper.appendChild(popup);

            let isToggling = false;
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                if (isToggling) {
                    return;
                }
                const isPopupVisible = popup.style.display === 'flex' && 
                                      popup.style.opacity !== '0' && 
                                      popup.style.opacity !== '';
                if (!isPopupVisible && config.exclusive) {
                    this.closePopupById(config.exclusive);
                }
                isToggling = true;
                this.showPopup(config.id, popup);
                setTimeout(() => {
                    isToggling = false;
                }, 200);
            });
        }

        buttonsContainer.appendChild(btnWrapper);
    });

    // 监听 "请她离开" 事件 (由 app.js 触发)
    // 创建命名处理函数以便追踪和清理
    const goodbyeHandler = () => {
        // 设置返回状态标志，阻止更新循环显示锁图标和按钮
        this._isInReturnState = true;
        
        // 1. 隐藏主按钮组
        if (this._floatingButtonsContainer) {
            this._floatingButtonsContainer.style.display = 'none';
        }
        
        // 2. 隐藏锁图标
        if (this._vrmLockIcon) {
            this._vrmLockIcon.style.display = 'none';
        }
        
        // 3. 显示"请她回来"按钮（固定在屏幕中央）
        if (this._returnButtonContainer) {
            // 清除所有定位样式
            this._returnButtonContainer.style.left = '';
            this._returnButtonContainer.style.top = '';
            this._returnButtonContainer.style.right = '';
            this._returnButtonContainer.style.bottom = '';
            
            // 使用 transform 居中定位（屏幕中央）
            this._returnButtonContainer.style.left = '50%';
            this._returnButtonContainer.style.top = '50%';
            this._returnButtonContainer.style.transform = 'translate(-50%, -50%)';
            
            this._returnButtonContainer.style.display = 'flex';
        }
    };
    
    // 追踪 goodbye 事件监听器以便清理
    this._uiWindowHandlers.push({ event: 'live2d-goodbye-click', handler: goodbyeHandler });
    window.addEventListener('live2d-goodbye-click', goodbyeHandler);

    // 监听 "请她回来" 事件 (由 app.js 或 vrm 自身触发)
    // 创建命名处理函数以便追踪和清理
    const returnHandler = () => {
        // 清除返回状态标志，允许更新循环正常显示锁图标和按钮
        this._isInReturnState = false;
        
        // 1. 隐藏"请她回来"按钮
        if (this._returnButtonContainer) {
            this._returnButtonContainer.style.display = 'none';
        }
        
        // 2. 恢复主按钮组
        if (this._floatingButtonsContainer) {
            this._floatingButtonsContainer.style.display = 'flex';
        }
        
        // 3. 恢复锁图标
        if (this._vrmLockIcon) {
            this._vrmLockIcon.style.display = 'block';
        }
    };
    
    
    // 追踪 return 事件监听器以便清理
    this._uiWindowHandlers.push({ event: 'vrm-return-click', handler: returnHandler });
    this._uiWindowHandlers.push({ event: 'live2d-return-click', handler: returnHandler });
    window.addEventListener('vrm-return-click', returnHandler);
    window.addEventListener('live2d-return-click', returnHandler);
    // 创建"请她回来"按钮
    const returnButtonContainer = document.createElement('div');
    returnButtonContainer.id = 'vrm-return-button-container';
    Object.assign(returnButtonContainer.style, {
        position: 'fixed', 
        left: '50%', 
        top: '50%', 
        transform: 'translate(-50%, -50%)',  // 居中定位
        zIndex: '99999',
        pointerEvents: 'auto', 
        display: 'none'
    });

    const returnBtn = document.createElement('div');
    returnBtn.id = 'vrm-btn-return';
    returnBtn.className = 'vrm-return-btn';

    const returnImgOff = document.createElement('img');
    returnImgOff.src = '/static/icons/rest_off.png' + iconVersion; returnImgOff.alt = '💤';
    Object.assign(returnImgOff.style, { width: '64px', height: '64px', objectFit: 'contain', pointerEvents: 'none', opacity: '1', transition: 'opacity 0.3s ease' });

    const returnImgOn = document.createElement('img');
    returnImgOn.src = '/static/icons/rest_on.png' + iconVersion; returnImgOn.alt = '💤';
    Object.assign(returnImgOn.style, { position: 'absolute', width: '64px', height: '64px', objectFit: 'contain', pointerEvents: 'none', opacity: '0', transition: 'opacity 0.3s ease' });

    Object.assign(returnBtn.style, {
        width: '64px', height: '64px', borderRadius: '50%', background: 'rgba(255, 255, 255, 0.65)',
        backdropFilter: 'saturate(180%) blur(20px)', border: '1px solid rgba(255, 255, 255, 0.18)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer',
        boxShadow: '0 2px 4px rgba(0, 0, 0, 0.04)', transition: 'all 0.1s ease', pointerEvents: 'auto', position: 'relative'
    });

    returnBtn.addEventListener('mouseenter', () => {
        returnBtn.style.transform = 'scale(1.05)'; returnBtn.style.background = 'rgba(255, 255, 255, 0.8)';
        returnImgOff.style.opacity = '0'; returnImgOn.style.opacity = '1';
    });
    returnBtn.addEventListener('mouseleave', () => {
        returnBtn.style.transform = 'scale(1)'; returnBtn.style.background = 'rgba(255, 255, 255, 0.65)';
        returnImgOff.style.opacity = '1'; returnImgOn.style.opacity = '0';
    });
    returnBtn.addEventListener('click', (e) => {
        if (returnButtonContainer.getAttribute('data-dragging') === 'true') { e.preventDefault(); e.stopPropagation(); return; }
        e.stopPropagation(); e.preventDefault();
        // 只派发 vrm-return-click，由 VRM 处理恢复逻辑
        // app.js 中的 live2d-return-click 监听器会独立处理 Live2D 的恢复
        window.dispatchEvent(new CustomEvent('vrm-return-click'));
    });

    returnBtn.appendChild(returnImgOff);
    returnBtn.appendChild(returnImgOn);
    returnButtonContainer.appendChild(returnBtn);
    document.body.appendChild(returnButtonContainer);

    this._returnButtonContainer = returnButtonContainer;
    this.setupVRMReturnButtonDrag(returnButtonContainer);
    
    // 添加呼吸灯动画样式（与 Live2D 保持一致）
    this._addReturnButtonBreathingAnimation();

    // 锁图标处理
    document.querySelectorAll('#vrm-lock-icon').forEach(el => el.remove());

    const lockIcon = document.createElement('div');
    lockIcon.id = 'vrm-lock-icon';
    lockIcon.dataset.vrmLock = 'true'; 
    document.body.appendChild(lockIcon);
    this._vrmLockIcon = lockIcon;

    Object.assign(lockIcon.style, {
        position: 'fixed', zIndex: '99999', width: '44px', height: '44px',
        cursor: 'pointer', display: 'none',
        backgroundImage: 'url(/static/icons/unlocked_icon.png)',
        backgroundSize: 'contain', backgroundRepeat: 'no-repeat', backgroundPosition: 'center',
        pointerEvents: 'auto', transition: 'transform 0.1s'
    });

    const toggleLock = (e) => {
        if(e) { e.preventDefault(); e.stopPropagation(); }
        
        // 检查 interaction 是否存在
        if (!this.interaction) {
            console.warn('[VRM UI Buttons] interaction 未初始化，无法切换锁定状态');
            return;
        }
        
        // 使用显式的布尔值处理，避免 undefined 导致的意外行为
        const currentLocked = Boolean(this.interaction?.isLocked);
        const newLockedState = !currentLocked;
        
        if (this.core && typeof this.core.setLocked === 'function') {
            // 优先使用 core.setLocked（它会调用 interaction.setLocked）
            this.core.setLocked(newLockedState);
        } else if (this.interaction && typeof this.interaction.setLocked === 'function') {
            // 如果没有 core.setLocked，直接使用 interaction.setLocked
            // interaction.setLocked 会设置 isLocked 标志，让 interaction handlers 通过 checkLocked() 来尊重锁定状态
            this.interaction.setLocked(newLockedState);
        } else {
            // 最后的降级方案：直接设置 isLocked（但不修改 pointerEvents）
            // interaction handlers 会通过 checkLocked() 检查这个标志
            this.interaction.isLocked = newLockedState;
        }
        
        // 可选：使用 CSS 类来标记锁定状态（用于样式或调试，但不影响 pointerEvents）
        // interaction handlers 会通过 checkLocked() 来尊重 isLocked 标志，而不是依赖 CSS 类
        const vrmCanvas = document.getElementById('vrm-canvas');
        if (vrmCanvas) {
            if (newLockedState) {
                vrmCanvas.classList.add('ui-locked');
            } else {
                vrmCanvas.classList.remove('ui-locked');
            }
        }
        
        // 更新锁图标样式（使用显式的布尔值）
        const isLocked = Boolean(this.interaction?.isLocked);
        lockIcon.style.backgroundImage = isLocked ? 'url(/static/icons/locked_icon.png)' : 'url(/static/icons/unlocked_icon.png)';
        
        // 获取当前的基础缩放值（如果已设置）
        const currentTransform = lockIcon.style.transform || '';
        const baseScaleMatch = currentTransform.match(/scale\(([\d.]+)\)/);
        const baseScale = baseScaleMatch ? parseFloat(baseScaleMatch[1]) : 1.0;
        
        // 在基础缩放的基础上进行点击动画
        lockIcon.style.transform = `scale(${baseScale * 0.9})`;
        setTimeout(() => {
            // 恢复时使用基础缩放值（更新循环会持续更新这个值）
            lockIcon.style.transform = `scale(${baseScale})`;
        }, 100);
        
        lockIcon.style.display = 'block';
    };

    lockIcon.addEventListener('mousedown', toggleLock);
    lockIcon.addEventListener('touchstart', toggleLock, {passive:false});

    // 启动更新循环
    this._startUIUpdateLoop();
    
    // 页面加载时直接显示按钮（锁定状态下不显示）
    setTimeout(() => {
        // 检查锁定状态
        const isLocked = this.interaction && this.interaction.checkLocked ? this.interaction.checkLocked() : false;
        
        // 锁定状态下不显示浮动按钮容器
        if (isLocked) {
            return;
        }
        
        // 显示浮动按钮容器（一直显示，不隐藏）
        if (buttonsContainer) {
            buttonsContainer.style.display = 'flex';
        }
        
        // 显示锁图标
        if (this._vrmLockIcon) {
            this._vrmLockIcon.style.display = 'block';
        }
    }, 100); // 延迟100ms确保位置已计算
    
    // 通知外部浮动按钮已就绪
    window.dispatchEvent(new CustomEvent('live2d-floating-buttons-ready'));
};

// 循环更新位置 (保持跟随)
VRMManager.prototype._startUIUpdateLoop = function() {
    // 防止重复启动循环
    if (this._uiUpdateLoopId !== null && this._uiUpdateLoopId !== undefined) {
        return; // 循环已在运行
    }
    
    // 计算可见按钮数量（移动端隐藏 agent 和 goodbye 按钮）
    const getVisibleButtonCount = () => {
        const buttonConfigs = [
            { id: 'mic' },
            { id: 'screen' },
            { id: 'agent' },
            { id: 'settings' },
            { id: 'goodbye' }
        ];
        const mobile = window.isMobileWidth();
        // 移动端隐藏 agent 和 goodbye 按钮
        return buttonConfigs.filter(config => {
            if (mobile && (config.id === 'agent' || config.id === 'goodbye')) {
                return false;
            }
            return true;
        }).length;
    };

    // 基准按钮尺寸和间距（用于计算缩放，与 Live2D 保持一致）
    const baseButtonSize = 48;
    const baseGap = 12;
    let lastMobileUpdate = 0;
    const MOBILE_UPDATE_INTERVAL = 100;

    const update = () => {
        // 检查循环是否已被取消
        if (this._uiUpdateLoopId === null || this._uiUpdateLoopId === undefined) {
            return;
        }
        
        if (!this.currentModel || !this.currentModel.vrm) {
            this._uiUpdateLoopId = requestAnimationFrame(update);
            return;
        }

        // 如果处于返回状态，跳过按钮和锁图标的定位与显示
        if (this._isInReturnState) {
            this._uiUpdateLoopId = requestAnimationFrame(update);
            return;
        }
        
        // 移动端跳过位置更新，使用 CSS 固定定位
        if (window.isMobileWidth()) {
            const now = performance.now();
            if (now - lastMobileUpdate < MOBILE_UPDATE_INTERVAL) {
                this._uiUpdateLoopId = requestAnimationFrame(update);
                return;
            }
            lastMobileUpdate = now;
        }
        
        const buttonsContainer = document.getElementById('vrm-floating-buttons')
        const lockIcon = this._vrmLockIcon;
        
        if (!this.camera || !this.renderer) {
            this._uiUpdateLoopId = requestAnimationFrame(update);
            return;
        }

        try {
            const vrm = this.currentModel.vrm;
            // 统一使用 canvasRect 的宽高，确保在缩放/嵌入场景下定位准确
            // 如果未来 VRM canvas 不再全屏，使用 canvasRect 可以保证定位精度
            const canvasRect = this.renderer.domElement.getBoundingClientRect();
            const canvasWidth = canvasRect.width;
            const canvasHeight = canvasRect.height;

            // 计算模型在屏幕上的高度（通过头部和脚部骨骼）
            let modelScreenHeight = 0;
            let headScreenY = 0;
            let footScreenY = 0;

            if (vrm.humanoid) {
                // 获取头部骨骼
                let headNode = vrm.humanoid.getNormalizedBoneNode('head');
                if (!headNode) headNode = vrm.humanoid.getNormalizedBoneNode('neck');
                if (!headNode) headNode = vrm.scene;

                // 获取脚部骨骼（用于计算模型高度）
                const leftFoot = vrm.humanoid.getNormalizedBoneNode('leftFoot');
                const rightFoot = vrm.humanoid.getNormalizedBoneNode('rightFoot');
                const leftToes = vrm.humanoid.getNormalizedBoneNode('leftToes');
                const rightToes = vrm.humanoid.getNormalizedBoneNode('rightToes');

                if (headNode) {
                    headNode.updateWorldMatrix(true, false);
                    const headPos = new window.THREE.Vector3();
                    headNode.getWorldPosition(headPos);
                    headPos.project(this.camera);
                    headScreenY = (-headPos.y * 0.5 + 0.5) * canvasHeight;
                }

                // 使用脚趾骨骼（如果存在）或脚部骨骼来计算脚底位置
                let footNode = null;
                if (leftToes) footNode = leftToes;
                else if (rightToes) footNode = rightToes;
                else if (leftFoot) footNode = leftFoot;
                else if (rightFoot) footNode = rightFoot;

                if (footNode) {
                    footNode.updateWorldMatrix(true, false);
                    const footPos = new window.THREE.Vector3();
                    footNode.getWorldPosition(footPos);
                    footPos.project(this.camera);
                    footScreenY = (-footPos.y * 0.5 + 0.5) * canvasHeight;
                } else {
                    // 如果没有脚部骨骼，使用场景包围盒估算
                    const box = new window.THREE.Box3().setFromObject(vrm.scene);
                    const size = new window.THREE.Vector3();
                    box.getSize(size);
                    // 估算：假设模型高度约为包围盒高度的 80%（排除头发等）
                    const estimatedModelHeight = size.y * 0.8;
                    const centerPos = new window.THREE.Vector3();
                    box.getCenter(centerPos);
                    centerPos.project(this.camera);
                    const centerScreenY = (-centerPos.y * 0.5 + 0.5) * canvasHeight;
                    headScreenY = centerScreenY + estimatedModelHeight / 2;
                    footScreenY = centerScreenY - estimatedModelHeight / 2;
                }

                modelScreenHeight = Math.abs(headScreenY - footScreenY);
            } else {
                // 如果没有 humanoid，使用场景包围盒
                const box = new window.THREE.Box3().setFromObject(vrm.scene);
                const size = new window.THREE.Vector3();
                box.getSize(size);
                modelScreenHeight = size.y * 0.8; // 估算
            }

            // 重新计算可见按钮数量和基准工具栏高度（响应移动端/桌面端切换）
            const visibleCount = getVisibleButtonCount();
            const baseToolbarHeight = baseButtonSize * visibleCount + baseGap * (visibleCount - 1);
            
            // 计算目标工具栏高度（模型高度的一半，与 Live2D 保持一致）
            const targetToolbarHeight = modelScreenHeight / 2;

            // 计算缩放比例（限制在合理范围内，防止按钮太小或太大）
            const minScale = 0.5;  // 最小缩放50%
            const maxScale = 1.0;  // 最大缩放100%
            const rawScale = targetToolbarHeight / baseToolbarHeight;
            const scale = Math.max(minScale, Math.min(maxScale, rawScale));

            // 更新按钮位置
            if (buttonsContainer) {
                // 获取头部位置用于定位
                let headNode = null;
                if (vrm.humanoid) {
                    headNode = vrm.humanoid.getNormalizedBoneNode('head');
                    if (!headNode) headNode = vrm.humanoid.getNormalizedBoneNode('neck');
                }
                if (!headNode) headNode = vrm.scene;

                headNode.updateWorldMatrix(true, false);
                const btnPos = new window.THREE.Vector3();
                headNode.getWorldPosition(btnPos);
                // 减小偏移量，让按钮更靠近模型
                btnPos.x += 0.2;   // 从 0.35 减小到 0.2，更靠近模型
                btnPos.y += 0.05;  // 从 0.1 减小到 0.05，更靠近模型
                btnPos.project(this.camera);
                // 统一使用 canvasRect 的宽高计算屏幕坐标，确保在缩放/嵌入场景下定位准确
                const screenX = (btnPos.x * 0.5 + 0.5) * canvasWidth;
                const screenY = (-(btnPos.y * 0.5) + 0.5) * canvasHeight;
                
                // 应用缩放到容器（使用 transform-origin: left top 确保从左上角缩放）
                buttonsContainer.style.transformOrigin = 'left top';
                buttonsContainer.style.transform = `scale(${scale})`;

                // 计算目标位置（应用偏移，减小垂直偏移让按钮更靠近模型）
                // 注意：screenX/screenY 是相对于 canvas 的坐标，需要加上 canvas 的偏移量
                const targetX = canvasRect.left + screenX;
                const targetY = canvasRect.top + screenY - 50;  // 从 -100 减小到 -50，更靠近模型
                
                // 使用缩放后的实际工具栏高度和宽度（用于边界限制）
                const actualToolbarHeight = baseToolbarHeight * scale;
                const actualToolbarWidth = 48 * scale;  // 按钮宽度
                
                // 屏幕边缘限制（参考 Live2D 的实现）
                // 使用窗口尺寸进行边界限制（因为按钮是相对于窗口定位的）
                const minMargin = 10;  // 最小边距
                const windowWidth = window.innerWidth;
                const windowHeight = window.innerHeight;
                
                // X轴边界限制：确保按钮容器不超出屏幕右边界
                const maxX = windowWidth - actualToolbarWidth - minMargin;
                const clampedX = Math.max(minMargin, Math.min(targetX, maxX));
                
                // Y轴边界限制：确保按钮容器不超出屏幕上下边界
                const minY = minMargin;
                const maxY = windowHeight - actualToolbarHeight - minMargin;
                const clampedY = Math.max(minY, Math.min(targetY, maxY));
                
                buttonsContainer.style.left = `${clampedX}px`;
                buttonsContainer.style.top = `${clampedY}px`;
                // 不要在这里设置 display，让鼠标检测逻辑和初始显示逻辑来控制显示/隐藏（与 Live2D 保持一致） 
            }

            // 更新锁位置（使用与按钮相同的缩放比例）
            // 只有在非返回状态下才更新锁图标位置和显示
            if (lockIcon && !this._isInReturnState) {
                // 获取头部位置用于锁图标定位
                let headNode = null;
                if (vrm.humanoid) {
                    headNode = vrm.humanoid.getNormalizedBoneNode('head');
                    if (!headNode) headNode = vrm.humanoid.getNormalizedBoneNode('neck');
                }
                if (!headNode) headNode = vrm.scene;

                headNode.updateWorldMatrix(true, false);
                const lockPos = new window.THREE.Vector3();
                headNode.getWorldPosition(lockPos);
                lockPos.x += 0.1; 
                lockPos.y -= 0.55; 
                lockPos.project(this.camera);
                // 统一使用 canvasRect 的宽高计算屏幕坐标
                const lockScreenX = (lockPos.x * 0.5 + 0.5) * canvasWidth;
                const lockScreenY = (-(lockPos.y * 0.5) + 0.5) * canvasHeight;
                // 加上 canvas 的偏移量，转换为窗口坐标
                const targetLockX = canvasRect.left + lockScreenX;
                const targetLockY = canvasRect.top + lockScreenY;
                
                // 应用缩放到锁图标（使用与按钮相同的缩放比例）
                const baseLockIconSize = 44;  // 锁图标基准尺寸 44px x 44px
                lockIcon.style.transformOrigin = 'center center';
                lockIcon.style.transform = `scale(${scale})`;
                
                // 使用缩放后的实际尺寸（用于边界限制）
                const actualLockIconSize = baseLockIconSize * scale;
                const minMargin = 10;  // 最小边距
                const windowWidth = window.innerWidth;
                const windowHeight = window.innerHeight;
                
                // 屏幕边缘限制（使用窗口尺寸）
                const maxLockX = windowWidth - actualLockIconSize - minMargin;
                const maxLockY = windowHeight - actualLockIconSize - minMargin;
                const clampedLockX = Math.max(minMargin, Math.min(targetLockX, maxLockX));
                const clampedLockY = Math.max(minMargin, Math.min(targetLockY, maxLockY));
                
                lockIcon.style.left = `${clampedLockX}px`;
                lockIcon.style.top = `${clampedLockY}px`;
                lockIcon.style.display = 'block';
            }
        } catch (error) {
            // 忽略单帧异常，继续更新循环（开发模式下记录）
            if (window.DEBUG_MODE) {
                console.debug('[VRM UI] 更新循环单帧异常:', error);
            }
        }
        
        // 继续下一帧（存储 RAF ID）
        this._uiUpdateLoopId = requestAnimationFrame(update);
    };
    
    // 启动循环（存储初始 RAF ID）
    this._uiUpdateLoopId = requestAnimationFrame(update);
};

// 为VRM的"请她回来"按钮设置拖动功能 (保持不变)
VRMManager.prototype.setupVRMReturnButtonDrag = function (returnButtonContainer) {
    let isDragging = false;
    let dragStartX = 0;
    let dragStartY = 0;
    let containerStartX = 0;
    let containerStartY = 0;

    const handleStart = (clientX, clientY) => {
        isDragging = true;
        dragStartX = clientX;
        dragStartY = clientY;
        
        // 获取当前容器的实际位置（考虑居中定位）
        const rect = returnButtonContainer.getBoundingClientRect();
        containerStartX = rect.left;
        containerStartY = rect.top;
        
        // 清除 transform，改用像素定位
        returnButtonContainer.style.transform = 'none';
        returnButtonContainer.style.left = `${containerStartX}px`;
        returnButtonContainer.style.top = `${containerStartY}px`;
        
        returnButtonContainer.setAttribute('data-dragging', 'false');
        returnButtonContainer.style.cursor = 'grabbing';
    };

    const handleMove = (clientX, clientY) => {
        if (!isDragging) return;
        const deltaX = clientX - dragStartX;
        const deltaY = clientY - dragStartY;
        if (Math.abs(deltaX) > 5 || Math.abs(deltaY) > 5) {
            returnButtonContainer.setAttribute('data-dragging', 'true');
        }
        const containerWidth = returnButtonContainer.offsetWidth || 64;
        const containerHeight = returnButtonContainer.offsetHeight || 64;
        const newX = Math.max(0, Math.min(containerStartX + deltaX, window.innerWidth - containerWidth));
        const newY = Math.max(0, Math.min(containerStartY + deltaY, window.innerHeight - containerHeight));
        returnButtonContainer.style.left = `${newX}px`;
        returnButtonContainer.style.top = `${newY}px`;
    };

    const handleEnd = () => {
        if (isDragging) {
            setTimeout(() => returnButtonContainer.setAttribute('data-dragging', 'false'), 10);
            isDragging = false;
            returnButtonContainer.style.cursor = 'grab';
        }
    };

    returnButtonContainer.addEventListener('mousedown', (e) => {
        if (returnButtonContainer.contains(e.target)) {
            e.preventDefault(); handleStart(e.clientX, e.clientY);
        }
    });
    
    // 保存 document 级别的事件监听器引用，以便后续清理
    this._returnButtonDragHandlers = {
        mouseMove: (e) => handleMove(e.clientX, e.clientY),
        mouseUp: handleEnd,
        touchMove: (e) => {
            if(isDragging) { e.preventDefault(); const touch = e.touches[0]; handleMove(touch.clientX, touch.clientY); }
        },
        touchEnd: handleEnd
    };
    
    document.addEventListener('mousemove', this._returnButtonDragHandlers.mouseMove);
    document.addEventListener('mouseup', this._returnButtonDragHandlers.mouseUp);
    
    returnButtonContainer.addEventListener('touchstart', (e) => {
        if (returnButtonContainer.contains(e.target)) {
            e.preventDefault(); const touch = e.touches[0]; handleStart(touch.clientX, touch.clientY);
        }
    });
    document.addEventListener('touchmove', this._returnButtonDragHandlers.touchMove, {passive: false});
    document.addEventListener('touchend', this._returnButtonDragHandlers.touchEnd);
    returnButtonContainer.style.cursor = 'grab';
};

/**
 * 添加"请她回来"按钮的呼吸灯动画效果（与 Live2D 保持一致）
 */
VRMManager.prototype._addReturnButtonBreathingAnimation = function() {
    // 检查是否已经添加过样式
    if (document.getElementById('vrm-return-button-breathing-styles')) {
        return;
    }

    const style = document.createElement('style');
    style.id = 'vrm-return-button-breathing-styles';
    style.textContent = `
        /* 请她回来按钮呼吸特效 */
        @keyframes vrmReturnButtonBreathing {
            0%, 100% {
                box-shadow: 0 0 8px rgba(68, 183, 254, 0.6), 0 2px 4px rgba(0, 0, 0, 0.04), 0 8px 16px rgba(0, 0, 0, 0.08);
            }
            50% {
                box-shadow: 0 0 18px rgba(68, 183, 254, 1), 0 2px 4px rgba(0, 0, 0, 0.04), 0 8px 16px rgba(0, 0, 0, 0.08);
            }
        }
        
        #vrm-btn-return {
            animation: vrmReturnButtonBreathing 2s ease-in-out infinite;
        }
        
        #vrm-btn-return:hover {
            animation: none;
        }
    `;
    document.head.appendChild(style);
};

/**
 * 清理VRM UI元素
 */
VRMManager.prototype.cleanupUI = function() {
    // 取消 UI 更新循环（防止内存泄漏）
    if (this._uiUpdateLoopId !== null && this._uiUpdateLoopId !== undefined) {
        cancelAnimationFrame(this._uiUpdateLoopId);
        this._uiUpdateLoopId = null;
    }
    
    const vrmButtons = document.getElementById('vrm-floating-buttons');
    if (vrmButtons) vrmButtons.remove();
    document.querySelectorAll('#vrm-lock-icon').forEach(el => el.remove());
    const vrmReturnBtn = document.getElementById('vrm-return-button-container');
    if (vrmReturnBtn) vrmReturnBtn.remove();
    
    // 移除 window 级别的事件监听器，防止内存泄漏（使用 UI 模块专用的 handlers 数组）
    if (this._uiWindowHandlers && this._uiWindowHandlers.length > 0) {
        this._uiWindowHandlers.forEach(({ event, handler }) => {
            window.removeEventListener(event, handler);
        });
        this._uiWindowHandlers = [];
    }
    
    // 移除 document 级别的事件监听器，防止内存泄漏
    if (this._returnButtonDragHandlers) {
        document.removeEventListener('mousemove', this._returnButtonDragHandlers.mouseMove);
        document.removeEventListener('mouseup', this._returnButtonDragHandlers.mouseUp);
        document.removeEventListener('touchmove', this._returnButtonDragHandlers.touchMove);
        document.removeEventListener('touchend', this._returnButtonDragHandlers.touchEnd);
        this._returnButtonDragHandlers = null;
    }
    
    // 清理窗口检查定时器（防止内存泄漏）
    if (this._windowCheckTimers) {
        Object.keys(this._windowCheckTimers).forEach(url => {
            if (this._windowCheckTimers[url]) {
                clearTimeout(this._windowCheckTimers[url]);
            }
        });
        this._windowCheckTimers = {};
    }
    
    // 关闭所有设置窗口
    if (typeof this.closeAllSettingsWindows === 'function') {
        this.closeAllSettingsWindows();
    }
    
    if (window.lanlan_config) window.lanlan_config.vrm_model = null;
    this._vrmLockIcon = null;
    this._floatingButtons = null;
    this._returnButtonContainer = null;
};

/**
 * 【统一状态管理】更新浮动按钮的激活状态和图标
 * @param {string} buttonId - 按钮ID（如 'mic', 'screen', 'agent', 'settings' 等）
 * @param {boolean} active - 是否激活
 */
VRMManager.prototype.setButtonActive = function(buttonId, active) {
    const buttonData = this._floatingButtons && this._floatingButtons[buttonId];
    if (!buttonData || !buttonData.button) return;

    // 更新 dataset
    buttonData.button.dataset.active = active ? 'true' : 'false';

    // 更新背景色
    buttonData.button.style.background = active
        ? 'rgba(68, 183, 254, 0.3)'
        : 'rgba(255, 255, 255, 0.65)';

    // 更新图标
    if (buttonData.imgOff) {
        buttonData.imgOff.style.opacity = active ? '0' : '1';
    }
    if (buttonData.imgOn) {
        buttonData.imgOn.style.opacity = active ? '1' : '0';
    }
};

/**
 * 【统一状态管理】重置所有浮动按钮到默认状态
 */
VRMManager.prototype.resetAllButtons = function() {
    if (!this._floatingButtons) return;

    Object.keys(this._floatingButtons).forEach(btnId => {
        this.setButtonActive(btnId, false);
    });
};