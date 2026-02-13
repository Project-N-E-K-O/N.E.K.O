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

    document.querySelectorAll('#live2d-floating-buttons, #live2d-lock-icon, #live2d-return-button-container')
        .forEach(el => el.remove());
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
    ['pointerdown', 'pointermove', 'pointerup', 'mousedown', 'mousemove', 'mouseup', 'touchstart', 'touchmove', 'touchend'].forEach(evt => {
        buttonsContainer.addEventListener(evt, stopContainerEvent);
    });

    const applyResponsiveFloatingLayout = () => {
        if (this._isInReturnState) {
            buttonsContainer.style.display = 'none';
            return;
        }
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
            buttonsContainer.style.left = '';
            buttonsContainer.style.top = '';
            buttonsContainer.style.display = 'flex';
        }
    };
    applyResponsiveFloatingLayout();
    this._uiWindowHandlers.push({ event: 'resize', handler: applyResponsiveFloatingLayout });
    window.addEventListener('resize', applyResponsiveFloatingLayout);

    const iconVersion = window.APP_VERSION ? `?v=${window.APP_VERSION}` : '?v=1.0.0';
    const buttonConfigs = [
        { id: 'mic', emoji: '🎤', title: window.t ? window.t('buttons.voiceControl') : '语音控制', titleKey: 'buttons.voiceControl', hasPopup: true, toggle: true, separatePopupTrigger: true, iconOff: '/static/icons/mic_icon_off.png' + iconVersion, iconOn: '/static/icons/mic_icon_on.png' + iconVersion },
        { id: 'screen', emoji: '🖥️', title: window.t ? window.t('buttons.screenShare') : '屏幕分享', titleKey: 'buttons.screenShare', hasPopup: true, toggle: true, separatePopupTrigger: true, iconOff: '/static/icons/screen_icon_off.png' + iconVersion, iconOn: '/static/icons/screen_icon_on.png' + iconVersion },
        { id: 'agent', emoji: '🔨', title: window.t ? window.t('buttons.agentTools') : 'Agent工具', titleKey: 'buttons.agentTools', hasPopup: true, popupToggle: true, exclusive: 'settings', iconOff: '/static/icons/Agent_off.png' + iconVersion, iconOn: '/static/icons/Agent_on.png' + iconVersion },
        { id: 'settings', emoji: '⚙️', title: window.t ? window.t('buttons.settings') : '设置', titleKey: 'buttons.settings', hasPopup: true, popupToggle: true, exclusive: 'agent', iconOff: '/static/icons/set_off.png' + iconVersion, iconOn: '/static/icons/set_on.png' + iconVersion },
        { id: 'goodbye', emoji: '💤', title: window.t ? window.t('buttons.leave') : '请她离开', titleKey: 'buttons.leave', hasPopup: false, iconOff: '/static/icons/rest_off.png' + iconVersion, iconOn: '/static/icons/rest_on.png' + iconVersion }
    ];

    this._floatingButtons = this._floatingButtons || {};

    // 3. 创建按钮
    buttonConfigs.forEach(config => {
        if (window.isMobileWidth() && (config.id === 'agent' || config.id === 'goodbye')) {
            return;
        }

        const btnWrapper = document.createElement('div');
        Object.assign(btnWrapper.style, { position: 'relative', display: 'flex', alignItems: 'center', gap: '8px', pointerEvents: 'auto' });
        ['pointerdown', 'mousedown', 'touchstart'].forEach(evt => btnWrapper.addEventListener(evt, e => e.stopPropagation()));

        const btn = document.createElement('div');
        btn.id = `vrm-btn-${config.id}`;
        btn.className = 'vrm-floating-btn';

        Object.assign(btn.style, {
            width: '48px', height: '48px', borderRadius: '50%', background: 'rgba(255, 255, 255, 0.65)',
            backdropFilter: 'saturate(180%) blur(20px)', border: '1px solid rgba(255, 255, 255, 0.18)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '24px',
            cursor: 'pointer', userSelect: 'none', boxShadow: '0 2px 4px rgba(0, 0, 0, 0.04), 0 4px 8px rgba(0, 0, 0, 0.08)',
            transition: 'all 0.1s ease', pointerEvents: 'auto'
        });

        let imgOff = null;
        let imgOn = null;

        if (config.iconOff && config.iconOn) {
            const imgContainer = document.createElement('div');
            Object.assign(imgContainer.style, { position: 'relative', width: '48px', height: '48px', display: 'flex', alignItems: 'center', justifyContent: 'center' });

            imgOff = document.createElement('img');
            imgOff.src = config.iconOff; imgOff.alt = config.emoji;
            Object.assign(imgOff.style, { position: 'absolute', width: '48px', height: '48px', objectFit: 'contain', pointerEvents: 'none', opacity: '1', transition: 'opacity 0.3s ease', imageRendering: 'crisp-edges' });

            imgOn = document.createElement('img');
            imgOn.src = config.iconOn; imgOn.alt = config.emoji;
            Object.assign(imgOn.style, { position: 'absolute', width: '48px', height: '48px', objectFit: 'contain', pointerEvents: 'none', opacity: '0', transition: 'opacity 0.3s ease', imageRendering: 'crisp-edges' });

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
                btn.style.boxShadow = '0 4px 8px rgba(0, 0, 0, 0.08), 0 8px 16px rgba(0, 0, 0, 0.08)';
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
                btn.style.boxShadow = '0 2px 4px rgba(0, 0, 0, 0.04), 0 4px 8px rgba(0, 0, 0, 0.08)';
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
                    window.dispatchEvent(new CustomEvent(`live2d-${config.id}-toggle`, { detail: { active: targetActive } }));
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
            const triggerBtn = document.createElement('button');
            triggerBtn.type = 'button';
            triggerBtn.setAttribute('aria-label', 'Open popup');
            // 使用图片图标替代文字符号
            const triggerImg = document.createElement('img');
            triggerImg.src = '/static/icons/play_trigger_icon.png' + iconVersion;
            triggerImg.alt = '';
            triggerImg.setAttribute('aria-hidden', 'true');
            Object.assign(triggerImg.style, {
                width: '22px', height: '22px', objectFit: 'contain',
                pointerEvents: 'none', imageRendering: 'crisp-edges'
            });
            Object.assign(triggerBtn.style, {
                width: '24px', height: '24px', borderRadius: '50%',
                background: 'rgba(255, 255, 255, 0.65)', backdropFilter: 'saturate(180%) blur(20px)',
                border: '1px solid rgba(255, 255, 255, 0.18)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                cursor: 'pointer', userSelect: 'none',
                boxShadow: '0 2px 4px rgba(0, 0, 0, 0.04), 0 4px 8px rgba(0, 0, 0, 0.08)', transition: 'all 0.1s ease', pointerEvents: 'auto',
                marginLeft: '-10px'
            });
            triggerBtn.appendChild(triggerImg);

            const stopTriggerEvent = (e) => { e.stopPropagation(); };
            ['pointerdown', 'mousedown', 'touchstart'].forEach(evt => triggerBtn.addEventListener(evt, stopTriggerEvent));

            triggerBtn.addEventListener('click', async (e) => {
                e.stopPropagation();
                const isPopupVisible = popup.style.display === 'flex' && popup.style.opacity === '1';
                if (config.id === 'mic' && !isPopupVisible) {
                    await window.renderFloatingMicList(popup);
                }
                if (config.id === 'screen' && !isPopupVisible) {
                    await this.renderScreenSourceList(popup);
                }

                this.showPopup(config.id, popup);
            });

            const triggerWrapper = document.createElement('div');
            triggerWrapper.style.position = 'relative';
            ['pointerdown', 'mousedown', 'touchstart'].forEach(evt => triggerWrapper.addEventListener(evt, stopTriggerEvent));

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

        // 2. 恢复VRM容器和canvas的可见性
        const vrmContainer = document.getElementById('vrm-container');
        if (vrmContainer) {
            vrmContainer.style.removeProperty('visibility');
            vrmContainer.style.removeProperty('pointer-events');
            vrmContainer.style.removeProperty('display');
            vrmContainer.classList.remove('hidden');
            vrmContainer.classList.remove('minimized');
        }

        const vrmCanvas = document.getElementById('vrm-canvas');
        if (vrmCanvas) {
            vrmCanvas.style.removeProperty('visibility');
            vrmCanvas.style.removeProperty('pointer-events');
        }

        // 3. 检查浮动按钮是否存在，如果不存在则重新创建（防止cleanupUI后按钮丢失）
        const buttonsContainer = document.getElementById('vrm-floating-buttons');
        if (!buttonsContainer) {
            // 重新创建整个浮动按钮系统
            this.setupFloatingButtons();
            return; // setupFloatingButtons会处理所有显示逻辑，直接返回
        }

        // 4. 移除"请她离开"时设置的 !important 样式
        buttonsContainer.style.removeProperty('display');
        buttonsContainer.style.removeProperty('visibility');
        buttonsContainer.style.removeProperty('opacity');

        // 5. 解锁模型（如果被锁定了）
        if (this.interaction && typeof this.interaction.setLocked === 'function') {
            const wasLocked = this.interaction.checkLocked ? this.interaction.checkLocked() : false;
            if (wasLocked) {
                this.interaction.setLocked(false);
            }
        }

        // 6. 恢复主按钮组（使用响应式布局函数，会检查锁定状态和视口）
        applyResponsiveFloatingLayout();

        // 7. 恢复锁图标（检查锁定状态，只有在未锁定时才显示）
        if (this._vrmLockIcon) {
            // 先移除"请她离开"时设置的 !important 样式
            this._vrmLockIcon.style.removeProperty('display');
            this._vrmLockIcon.style.removeProperty('visibility');
            this._vrmLockIcon.style.removeProperty('opacity');

            const isLocked = this.interaction && this.interaction.checkLocked ? this.interaction.checkLocked() : false;
            // 更新锁图标背景图片（确保显示正确的锁定/解锁状态）
            this._vrmLockIcon.style.backgroundImage = isLocked
                ? 'url(/static/icons/locked_icon.png)'
                : 'url(/static/icons/unlocked_icon.png)';
            if (!isLocked) {
                this._vrmLockIcon.style.display = 'block';
            }
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
        boxShadow: '0 2px 4px rgba(0, 0, 0, 0.04), 0 8px 16px rgba(0, 0, 0, 0.08), 0 16px 32px rgba(0, 0, 0, 0.04)', transition: 'all 0.1s ease', pointerEvents: 'auto', position: 'relative'
    });

    returnBtn.addEventListener('mouseenter', () => {
        returnBtn.style.transform = 'scale(1.05)';
        returnBtn.style.boxShadow = '0 4px 8px rgba(0, 0, 0, 0.08), 0 16px 32px rgba(0, 0, 0, 0.08)';
        returnBtn.style.background = 'rgba(255, 255, 255, 0.8)';
        returnImgOff.style.opacity = '0'; returnImgOn.style.opacity = '1';
    });
    returnBtn.addEventListener('mouseleave', () => {
        returnBtn.style.transform = 'scale(1)';
        returnBtn.style.boxShadow = '0 2px 4px rgba(0, 0, 0, 0.04), 0 8px 16px rgba(0, 0, 0, 0.08), 0 16px 32px rgba(0, 0, 0, 0.04)';
        returnBtn.style.background = 'rgba(255, 255, 255, 0.65)';
        returnImgOff.style.opacity = '1'; returnImgOn.style.opacity = '0';
    });
    returnBtn.addEventListener('click', (e) => {
        if (returnButtonContainer.getAttribute('data-dragging') === 'true') {
            e.preventDefault(); e.stopPropagation(); return;
        }
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
        if (e) { e.preventDefault(); e.stopPropagation(); }

        // 检查 interaction 是否存在
        if (!this.interaction) {
            console.warn('[VRM UI Buttons] interaction 未初始化，无法切换锁定状态');
            return;
        }

        // 使用 checkLocked() 方法获取当前锁定状态（如果可用），否则回退到 isLocked 属性
        const currentLocked = (this.interaction && typeof this.interaction.checkLocked === 'function')
            ? Boolean(this.interaction.checkLocked())
            : Boolean(this.interaction?.isLocked);
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

        // 更新锁图标样式（使用 checkLocked() 方法获取当前状态，如果可用）
        const isLocked = (this.interaction && typeof this.interaction.checkLocked === 'function')
            ? Boolean(this.interaction.checkLocked())
            : Boolean(this.interaction?.isLocked);
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

        // 刷新浮动按钮布局，立即反映新的锁定状态
        applyResponsiveFloatingLayout();
    };

    lockIcon.addEventListener('mousedown', toggleLock);
    lockIcon.addEventListener('touchstart', toggleLock, { passive: false });

    // 启动更新循环
    this._startUIUpdateLoop();

    // 页面加载时直接显示按钮（使用响应式布局函数，会检查锁定状态和视口）
    setTimeout(() => {
        // 使用响应式布局函数，会检查锁定状态和视口
        applyResponsiveFloatingLayout();

        // 显示锁图标（检查锁定状态，只有在未锁定时才显示）
        if (this._vrmLockIcon) {
            const isLocked = this.interaction && this.interaction.checkLocked ? this.interaction.checkLocked() : false;
            if (!isLocked) {
                this._vrmLockIcon.style.display = 'block';
            }
        }
    }, 100); // 延迟100ms确保位置已计算

    // 通知外部浮动按钮已就绪
    window.dispatchEvent(new CustomEvent('live2d-floating-buttons-ready'));
};

// 循环更新位置 (保持跟随)
VRMManager.prototype._startUIUpdateLoop = function () {
    // 防止重复启动循环
    if (this._uiUpdateLoopId !== null && this._uiUpdateLoopId !== undefined) {
        return; // 循环已在运行
    }

    // 复用对象以减少 GC 压力
    const box = new window.THREE.Box3();

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
            if (this._uiUpdateLoopId !== null && this._uiUpdateLoopId !== undefined) {
                this._uiUpdateLoopId = requestAnimationFrame(update);
            }
            return;
        }

        // 如果处于返回状态，跳过按钮和锁图标的定位与显示
        if (this._isInReturnState) {
            if (this._uiUpdateLoopId !== null && this._uiUpdateLoopId !== undefined) {
                this._uiUpdateLoopId = requestAnimationFrame(update);
            }
            return;
        }

        // 移动端跳过位置更新，使用 CSS 固定定位
        if (window.isMobileWidth()) {
            const now = performance.now();
            if (now - lastMobileUpdate < MOBILE_UPDATE_INTERVAL) {
                if (this._uiUpdateLoopId !== null && this._uiUpdateLoopId !== undefined) {
                    this._uiUpdateLoopId = requestAnimationFrame(update);
                }
                return;
            }
            lastMobileUpdate = now;
        }

        const buttonsContainer = document.getElementById('vrm-floating-buttons')
        const lockIcon = this._vrmLockIcon;

        if (!this.camera || !this.renderer) {
            if (this._uiUpdateLoopId !== null && this._uiUpdateLoopId !== undefined) {
                this._uiUpdateLoopId = requestAnimationFrame(update);
            }
            return;
        }

        try {
            const vrm = this.currentModel.vrm;
            const canvasRect = this.renderer.domElement.getBoundingClientRect();
            const canvasWidth = canvasRect.width;
            const canvasHeight = canvasRect.height;

            // ========== 2D 投影包围盒（代替 3D 骨骼投影） ==========
            // 与 Live2D 的 model.getBounds() 等价：获取模型在屏幕上的 {left, right, top, bottom}
            box.setFromObject(this.currentModel.scene);

            // 将包围盒的 8 个顶点投影到屏幕空间，求出 2D 边界
            const corners = [
                new window.THREE.Vector3(box.min.x, box.min.y, box.min.z),
                new window.THREE.Vector3(box.min.x, box.min.y, box.max.z),
                new window.THREE.Vector3(box.min.x, box.max.y, box.min.z),
                new window.THREE.Vector3(box.min.x, box.max.y, box.max.z),
                new window.THREE.Vector3(box.max.x, box.min.y, box.min.z),
                new window.THREE.Vector3(box.max.x, box.min.y, box.max.z),
                new window.THREE.Vector3(box.max.x, box.max.y, box.min.z),
                new window.THREE.Vector3(box.max.x, box.max.y, box.max.z)
            ];

            let screenLeft = Infinity, screenRight = -Infinity;
            let screenTop = Infinity, screenBottom = -Infinity;

            for (const corner of corners) {
                corner.project(this.camera);
                // NDC (-1~1) → 像素坐标
                const sx = canvasRect.left + (corner.x * 0.5 + 0.5) * canvasWidth;
                const sy = canvasRect.top + (-corner.y * 0.5 + 0.5) * canvasHeight;
                screenLeft = Math.min(screenLeft, sx);
                screenRight = Math.max(screenRight, sx);
                screenTop = Math.min(screenTop, sy);
                screenBottom = Math.max(screenBottom, sy);
            }

            // 现在 screenLeft/Right/Top/Bottom 就是模型在屏幕上 的 2D 边界（与 Live2D bounds 等价）
            const modelScreenHeight = screenBottom - screenTop;
            const modelCenterY = (screenTop + screenBottom) / 2;

            // ========== 按钮缩放（与之前相同） ==========
            const visibleCount = getVisibleButtonCount();
            const baseToolbarHeight = baseButtonSize * visibleCount + baseGap * (visibleCount - 1);
            const targetToolbarHeight = modelScreenHeight / 2;
            const minScale = 0.5;
            const maxScale = 1.0;
            const rawScale = targetToolbarHeight / baseToolbarHeight;
            const scale = Math.max(minScale, Math.min(maxScale, rawScale));

            // ========== 更新按钮位置 ==========
            if (buttonsContainer) {
                const isMobile = window.isMobileWidth();
                if (isMobile) {
                    buttonsContainer.style.transformOrigin = 'right bottom';
                } else {
                    buttonsContainer.style.transformOrigin = 'left top';
                }
                buttonsContainer.style.transform = `scale(${scale})`;

                if (!isMobile) {
                    const screenWidth = window.innerWidth;
                    const screenHeight = window.innerHeight;

                    // X轴：定位在角色右侧（与 Live2D 相同公式）
                    const targetX = screenRight * 0.8 + screenLeft * 0.2;

                    // 使用缩放后的实际工具栏高度
                    const actualToolbarHeight = baseToolbarHeight * scale;
                    const actualToolbarWidth = 80 * scale;  // 与 Live2D 一致（含 trigger 按钮宽度）

                    // Y轴：工具栏中心偏高于模型中心（VRM 全身模型的包围盒中心在腰部，
                    // 需要上移让按钮更接近胸部位置，与 Live2D 半身模型的视觉效果一致）
                    const offsetY = modelScreenHeight * 0.1;  // 上移 10% 模型高度
                    const targetY = modelCenterY - actualToolbarHeight / 2 - offsetY;

                    // 边界限制：确保不超出当前屏幕（与 Live2D 保持一致，使用 20px 边距）
                    const minY = 20;
                    const maxY = screenHeight - actualToolbarHeight - 20;
                    const boundedY = Math.max(minY, Math.min(targetY, maxY));

                    const maxX = screenWidth - actualToolbarWidth;
                    const boundedX = Math.max(0, Math.min(targetX, maxX));

                    // 平滑跟随（减少抖动）
                    const currentLeft = parseFloat(buttonsContainer.style.left) || 0;
                    const currentTop = parseFloat(buttonsContainer.style.top) || 0;
                    const dist = Math.sqrt(Math.pow(boundedX - currentLeft, 2) + Math.pow(boundedY - currentTop, 2));
                    if (dist > 0.5) {
                        buttonsContainer.style.left = `${boundedX}px`;
                        buttonsContainer.style.top = `${boundedY}px`;
                    }

                    // ========== 锁图标位置（与 Live2D 相同公式） ==========
                    if (lockIcon && !this._isInReturnState) {
                        const lockTargetX = screenRight * 0.7 + screenLeft * 0.3;
                        const lockTargetY = screenTop * 0.3 + screenBottom * 0.7;

                        lockIcon.style.transformOrigin = 'center center';
                        lockIcon.style.transform = `scale(${scale})`;

                        const baseLockIconSize = 44;
                        const actualLockIconSize = baseLockIconSize * scale;
                        const maxLockX = screenWidth - actualLockIconSize;
                        const maxLockY = screenHeight - actualLockIconSize - 20;
                        const boundedLockX = Math.max(0, Math.min(lockTargetX, maxLockX));
                        const boundedLockY = Math.max(20, Math.min(lockTargetY, maxLockY));

                        const currentLockLeft = parseFloat(lockIcon.style.left) || 0;
                        const currentLockTop = parseFloat(lockIcon.style.top) || 0;
                        const lockDist = Math.sqrt(Math.pow(boundedLockX - currentLockLeft, 2) + Math.pow(boundedLockY - currentLockTop, 2));
                        if (lockDist > 0.5) {
                            lockIcon.style.left = `${boundedLockX}px`;
                            lockIcon.style.top = `${boundedLockY}px`;
                        }
                        lockIcon.style.display = 'block';
                    }
                }
            }
        } catch (error) {
            // 忽略单帧异常，继续更新循环（开发模式下记录）
            if (window.DEBUG_MODE) {
                console.debug('[VRM UI] 更新循环单帧异常:', error);
            }
        }

        // 继续下一帧（只有在循环未被取消时才重新调度）
        if (this._uiUpdateLoopId !== null && this._uiUpdateLoopId !== undefined) {
            this._uiUpdateLoopId = requestAnimationFrame(update);
        }
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
            if (isDragging) { e.preventDefault(); const touch = e.touches[0]; handleMove(touch.clientX, touch.clientY); }
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
    document.addEventListener('touchmove', this._returnButtonDragHandlers.touchMove, { passive: false });
    document.addEventListener('touchend', this._returnButtonDragHandlers.touchEnd);
    returnButtonContainer.style.cursor = 'grab';
};

/**
 * 添加"请她回来"按钮的呼吸灯动画效果（与 Live2D 保持一致）
 */
VRMManager.prototype._addReturnButtonBreathingAnimation = function () {
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
VRMManager.prototype.cleanupUI = function () {
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
VRMManager.prototype.setButtonActive = function (buttonId, active) {
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
VRMManager.prototype.resetAllButtons = function () {
    if (!this._floatingButtons) return;

    Object.keys(this._floatingButtons).forEach(btnId => {
        this.setButtonActive(btnId, false);
    });
};