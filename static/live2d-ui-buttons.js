/**
 * Live2D UI Buttons - 浮动按钮系统（精简版）
 * 使用 AvatarButtonMixin 的 Live2D 特定实现
 * Live2D 使用 PIXI 而不是 THREE.js，因此有特殊的位置更新逻辑
 */

// 应用 mixin 到 Live2D Manager
AvatarButtonMixin.apply(Live2DManager.prototype, 'live2d', {
    containerElementId: 'live2d-floating-buttons',
    returnContainerId: 'live2d-return-button-container',
    returnBtnId: 'live2d-btn-return',
    lockIconId: 'live2d-lock-icon',
    popupPrefix: 'live2d',
    buttonClassPrefix: 'live2d-floating-btn',
    triggerBtnClass: 'live2d-trigger-btn',
    triggerIconClass: 'live2d-trigger-icon',
    returnBtnClass: 'live2d-return-btn',
    returnBreathingStyleId: 'live2d-return-button-breathing-styles'
});

/**
 * 设置 HTML 锁形图标（Live2D 特定）
 */
Live2DManager.prototype.setupHTMLLockIcon = function(model) {
    if (model) {
        document.querySelectorAll('#vrm-lock-icon, #vrm-lock-icon-hidden').forEach(el => {
            console.log('[锁图标] 清理残留的 VRM 锁图标');
            el.remove();
        });
    } else {
        const vrmLockIcon = document.getElementById('vrm-lock-icon');
        if (vrmLockIcon || (window.lanlan_config && window.lanlan_config.vrm_model)) {
            console.log('检测到 VRM 模式，Live2D 锁停止生成');
            return;
        }
    }

    const container = document.getElementById('live2d-canvas');

    if (!container) {
        this.isLocked = false;
        return;
    }

    if (!document.getElementById('chat-container')) {
        this.isLocked = false;
        container.style.pointerEvents = 'auto';
        return;
    }

    if (window.isViewerMode) {
        this.isLocked = false;
        container.style.pointerEvents = 'auto';
        return;
    }

    const existingLockIcon = document.getElementById('live2d-lock-icon');
    if (existingLockIcon) {
        if (this._lockIconTicker && this.pixi_app?.ticker) {
            this.pixi_app.ticker.remove(this._lockIconTicker);
            this._lockIconTicker = null;
        }
        existingLockIcon.remove();
    }

    const lockIcon = document.createElement('div');
    lockIcon.id = 'live2d-lock-icon';
    Object.assign(lockIcon.style, {
        position: 'fixed',
        zIndex: '99999',
        width: '32px',
        height: '32px',
        cursor: 'pointer',
        userSelect: 'none',
        pointerEvents: 'auto',
        transition: 'opacity 0.3s ease',
        display: 'none'
    });

    const iconVersion = '?v=' + Date.now();

    const imgContainer = document.createElement('div');
    Object.assign(imgContainer.style, {
        position: 'relative',
        width: '32px',
        height: '32px'
    });

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
    this._lockIconImages = {
        locked: imgLocked,
        unlocked: imgUnlocked
    };

    lockIcon.addEventListener('click', (e) => {
        e.stopPropagation();
        this.setLocked(!this.isLocked);
    });

    container.style.pointerEvents = this.isLocked ? 'none' : 'auto';

    const tick = () => {
        try {
            if (!model || !model.parent) {
                if (lockIcon) lockIcon.style.display = 'none';
                return;
            }
            const bounds = model.getBounds();
            const screenWidth = window.innerWidth;
            const screenHeight = window.innerHeight;

            const targetX = bounds.right * 0.7 + bounds.left * 0.3;
            const targetY = bounds.top * 0.3 + bounds.bottom * 0.7;

            lockIcon.style.left = `${Math.max(0, Math.min(targetX, screenWidth - 40))}px`;
            lockIcon.style.top = `${Math.max(0, Math.min(targetY, screenHeight - 40))}px`;

            const lockRect = lockIcon.getBoundingClientRect();
            let isOverlapped = false;
            document.querySelectorAll('[id^="live2d-popup-"]').forEach(popup => {
                if (popup.style.display === 'flex' && popup.style.opacity === '1') {
                    const popupRect = popup.getBoundingClientRect();
                    if (lockRect.right > popupRect.left && lockRect.left < popupRect.right &&
                        lockRect.bottom > popupRect.top && lockRect.top < popupRect.bottom) {
                        isOverlapped = true;
                    }
                }
            });
            if (!isOverlapped) {
                document.querySelectorAll('[data-neko-sidepanel]').forEach(panel => {
                    if (panel.style.display !== 'none' && parseFloat(panel.style.opacity) > 0) {
                        const panelRect = panel.getBoundingClientRect();
                        if (lockRect.right > panelRect.left && lockRect.left < panelRect.right &&
                            lockRect.bottom > panelRect.top && lockRect.top < panelRect.bottom) {
                            isOverlapped = true;
                        }
                    }
                });
            }
            lockIcon.style.opacity = isOverlapped ? '0.3' : '';
        } catch (_) {}
    };
    this._lockIconTicker = tick;
    this.pixi_app.ticker.add(tick);
};

/**
 * 设置浮动按钮系统（Live2D 特定）
 */
Live2DManager.prototype.setupFloatingButtons = function(model) {
    const container = document.getElementById('live2d-canvas');

    if (!container) {
        this.isLocked = false;
        return;
    }

    if (this._floatingButtonsResizeHandler) {
        window.removeEventListener('resize', this._floatingButtonsResizeHandler);
        this._floatingButtonsResizeHandler = null;
    }

    if (!document.getElementById('chat-container')) {
        this.isLocked = false;
        container.style.pointerEvents = 'auto';
        return;
    }

    if (window.isViewerMode) {
        this.isLocked = false;
        container.style.pointerEvents = 'auto';
        return;
    }

    // 基础框架初始化
    const buttonsContainer = this.setupFloatingButtonsBase(model);

    const opts = this._avatarButtonOptions;

    // 清理可能存在的旧 ticker
    const existingContainer = document.getElementById('live2d-floating-buttons');
    if (existingContainer && this._floatingButtonsTicker && this.pixi_app?.ticker) {
        try {
            this.pixi_app.ticker.remove(this._floatingButtonsTicker);
        } catch (_) {}
    }
    this._floatingButtonsTicker = null;

    // 响应式布局处理
    const applyResponsiveFloatingLayout = () => {
        if (isMobileWidth()) {
            buttonsContainer.style.flexDirection = 'column';
            buttonsContainer.style.top = '16px';
            buttonsContainer.style.left = '16px';
            buttonsContainer.style.bottom = '';
            buttonsContainer.style.right = '';
        } else {
            buttonsContainer.style.flexDirection = 'column';
            buttonsContainer.style.bottom = '';
            buttonsContainer.style.right = '';
        }
    };
    applyResponsiveFloatingLayout();

    this._floatingButtonsResizeHandler = applyResponsiveFloatingLayout;
    window.addEventListener('resize', this._floatingButtonsResizeHandler);

    // 获取按钮配置
    const iconVersion = '?v=' + Date.now();
    const buttonConfigs = this.getDefaultButtonConfigs();
    this._buttonConfigs = buttonConfigs;
    this._floatingButtons = this._floatingButtons || {};

    // 创建按钮
    buttonConfigs.forEach(config => {
        if (isMobileWidth() && (config.id === 'agent' || config.id === 'goodbye')) {
            return;
        }

        const { btnWrapper, btn, imgOff, imgOn } = this.createButtonElement(config, buttonsContainer);

        // 点击事件处理
        btn.addEventListener('click', (e) => {
            e.stopPropagation();

            if (config.id === 'mic') {
                const micButton = document.getElementById('micButton');
                if (micButton && micButton.classList.contains('active')) {
                    const isMicStarting = window.isMicStarting || false;
                    if (isMicStarting) {
                        if (btn.dataset.active !== 'true') {
                            btn.dataset.active = 'true';
                            if (imgOff && imgOn) {
                                imgOff.style.opacity = '0';
                                imgOn.style.opacity = '1';
                            }
                        }
                        return;
                    }
                }
            }

            if (config.id === 'screen') {
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

            const isActive = btn.dataset.active === 'true';
            const newActive = !isActive;
            btn.dataset.active = newActive.toString();

            if (imgOff && imgOn) {
                if (newActive) {
                    imgOff.style.opacity = '0';
                    imgOn.style.opacity = '1';
                } else {
                    imgOff.style.opacity = '0.75';
                    imgOn.style.opacity = '0';
                }
            }

            const event = new CustomEvent(`live2d-${config.id}-toggle`, {
                detail: { active: newActive }
            });
            window.dispatchEvent(event);
        });

        btnWrapper.appendChild(btn);

        // 麦克风静音按钮（仅非手机模式下的麦克风按钮）
        if (config.id === 'mic' && config.hasPopup && config.separatePopupTrigger && !isMobileWidth()) {
            const muteData = this.createMicMuteButton(btnWrapper);
            // 监听麦克风切换事件以更新静音按钮可见性
            const micToggleHandler = (e) => {
                if (muteData && muteData.updateVisibility) {
                    muteData.updateVisibility(e.detail.active);
                }
            };
            window.addEventListener('live2d-mic-toggle', micToggleHandler);
            if (!this._uiWindowHandlers) {
                this._uiWindowHandlers = [];
            }
            this._uiWindowHandlers.push({
                event: 'live2d-mic-toggle',
                handler: micToggleHandler,
                target: window
            });
        }

        // 处理弹窗
        if (config.hasPopup && config.separatePopupTrigger) {
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

            if (!isMobileWidth()) {
                const popup = this.createPopup(config.id);

                const triggerBtn = document.createElement('div');
                triggerBtn.className = 'live2d-trigger-btn';
                const triggerImg = document.createElement('img');
                triggerImg.src = '/static/icons/play_trigger_icon.png' + iconVersion;
                triggerImg.alt = '▶';
                triggerImg.className = `live2d-trigger-icon-${config.id}`;
                Object.assign(triggerImg.style, {
                    width: '22px', height: '22px', objectFit: 'contain',
                    pointerEvents: 'none', imageRendering: 'crisp-edges',
                    transition: 'transform 0.3s cubic-bezier(0.1, 0.9, 0.2, 1)'
                });
                triggerBtn.appendChild(triggerImg);

                Object.assign(triggerBtn.style, {
                    width: '24px',
                    height: '24px',
                    borderRadius: '50%',
                    background: 'var(--neko-btn-bg)',
                    backdropFilter: 'saturate(180%) blur(20px)',
                    border: 'var(--neko-btn-border)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    cursor: 'pointer',
                    userSelect: 'none',
                    boxShadow: 'var(--neko-btn-shadow)',
                    transition: 'all 0.1s ease',
                    pointerEvents: 'auto',
                    marginLeft: '-10px'
                });

                const stopTriggerEvent = (e) => { e.stopPropagation(); };
                ['pointerdown', 'pointermove', 'pointerup', 'mousedown', 'mousemove', 'mouseup', 'touchstart', 'touchmove', 'touchend'].forEach(evt => {
                    triggerBtn.addEventListener(evt, stopTriggerEvent);
                });

                triggerBtn.addEventListener('mouseenter', () => {
                    triggerBtn.style.transform = 'scale(1.05)';
                    triggerBtn.style.boxShadow = 'var(--neko-btn-shadow-hover)';
                    triggerBtn.style.background = 'var(--neko-btn-bg-hover)';
                });
                triggerBtn.addEventListener('mouseleave', () => {
                    triggerBtn.style.transform = 'scale(1)';
                    triggerBtn.style.boxShadow = 'var(--neko-btn-shadow)';
                    triggerBtn.style.background = 'var(--neko-btn-bg)';
                });

                triggerBtn.addEventListener('click', async (e) => {
                    console.log(`[Live2D] 小三角被点击: ${config.id}`);
                    e.stopPropagation();

                    const isPopupVisible = popup.style.display === 'flex' && popup.style.opacity === '1';

                    if (config.id === 'mic' && window.renderFloatingMicList && !isPopupVisible) {
                        await window.renderFloatingMicList();
                    }

                    if (config.id === 'screen' && window.renderFloatingScreenSourceList && !isPopupVisible) {
                        await window.renderFloatingScreenSourceList();
                    }

                    this.showPopup(config.id, popup);
                });

                const triggerWrapper = document.createElement('div');
                triggerWrapper.style.position = 'relative';

                const stopTriggerWrapperEvent = (e) => { e.stopPropagation(); };
                ['pointerdown', 'pointermove', 'pointerup', 'mousedown', 'mousemove', 'mouseup', 'touchstart', 'touchmove', 'touchend'].forEach(evt => {
                    triggerWrapper.addEventListener(evt, stopTriggerWrapperEvent);
                });

                triggerWrapper.appendChild(triggerBtn);
                triggerWrapper.appendChild(popup);

                btnWrapper.appendChild(triggerWrapper);
            }
        } else if (config.popupToggle) {
            const popup = this.createPopup(config.id);
            btnWrapper.appendChild(btn);
            btnWrapper.appendChild(popup);

            btn.addEventListener('click', (e) => {
                e.stopPropagation();

                const isPopupVisible = popup.style.display === 'flex' && popup.style.opacity === '1';

                if (!isPopupVisible && config.exclusive) {
                    this.closePopupById(config.exclusive);
                    const exclusiveData = this._floatingButtons[config.exclusive];
                    if (exclusiveData && exclusiveData.button) {
                        exclusiveData.button.style.background = 'var(--neko-btn-bg, rgba(255, 255, 255, 0.65))';
                    }
                    if (exclusiveData && exclusiveData.imgOff && exclusiveData.imgOn) {
                        exclusiveData.imgOff.style.opacity = '0.75';
                        exclusiveData.imgOn.style.opacity = '0';
                    }
                }

                this.showPopup(config.id, popup);

                setTimeout(() => {
                    const newPopupVisible = popup.style.display === 'flex' && popup.style.opacity === '1';
                    if (newPopupVisible) {
                        btn.style.background = 'var(--neko-btn-bg-active, rgba(255, 255, 255, 0.75))';
                        if (imgOff && imgOn) {
                            imgOff.style.opacity = '0';
                            imgOn.style.opacity = '1';
                        }
                    } else {
                        btn.style.background = 'var(--neko-btn-bg, rgba(255, 255, 255, 0.65))';
                        if (imgOff && imgOn) {
                            imgOff.style.opacity = '0.75';
                            imgOn.style.opacity = '0';
                        }
                    }
                }, 50);
            });
        } else {
            btn.addEventListener('click', (e) => {
                console.log(`[Live2D] 按钮被点击: ${config.id}`);
                e.stopPropagation();
                const event = new CustomEvent(`live2d-${config.id}-click`);
                window.dispatchEvent(event);
                console.log(`[Live2D] 已派发事件: live2d-${config.id}-click`);
            });
        }

        buttonsContainer.appendChild(btnWrapper);
        this._floatingButtons[config.id] = {
            button: btn,
            wrapper: btnWrapper,
            imgOff: imgOff,
            imgOn: imgOn
        };
        console.log(`[Live2D] 按钮已创建: ${config.id}, hasPopup: ${config.hasPopup}, toggle: ${config.toggle}`);
    });

    console.log('[Live2D] 所有浮动按钮已创建完成');

    // 创建"请她回来"按钮
    const returnButtonContainer = this.createReturnButton();
    this.setupReturnButtonContainerDrag(returnButtonContainer);

    container.style.pointerEvents = this.isLocked ? 'none' : 'auto';

    const baseButtonSize = 48;
    const baseGap = 12;
    const buttonCount = 5;
    const baseToolbarHeight = baseButtonSize * buttonCount + baseGap * (buttonCount - 1);

    const tick = () => {
        try {
            if (!model || !model.parent) {
                return;
            }
            if (isMobileWidth()) {
                return;
            }
            const bounds = model.getBounds();
            const screenWidth = window.innerWidth;
            const screenHeight = window.innerHeight;

            const modelCenterX = (bounds.left + bounds.right) / 2;
            const modelCenterY = (bounds.top + bounds.bottom) / 2;

            const modelHeight = bounds.bottom - bounds.top;
            const targetToolbarHeight = modelHeight / 2;

            const minScale = 0.5;
            const maxScale = 1.0;
            const rawScale = targetToolbarHeight / baseToolbarHeight;
            const scale = Math.max(minScale, Math.min(maxScale, rawScale));

            buttonsContainer.style.transformOrigin = 'left top';
            buttonsContainer.style.transform = `scale(${scale})`;

            const targetX = bounds.right * 0.8 + bounds.left * 0.2;

            const actualToolbarHeight = baseToolbarHeight * scale;
            const actualToolbarWidth = 80 * scale;

            const targetY = modelCenterY - actualToolbarHeight / 2;

            const minY = 20;
            const maxY = screenHeight - actualToolbarHeight - 20;
            const boundedY = Math.max(minY, Math.min(targetY, maxY));

            const maxX = screenWidth - actualToolbarWidth;
            const boundedX = Math.max(0, Math.min(targetX, maxX));

            buttonsContainer.style.left = `${boundedX}px`;
            buttonsContainer.style.top = `${boundedY}px`;
        } catch (_) {}
    };
    this._floatingButtonsTicker = tick;
    this.pixi_app.ticker.add(tick);

    setTimeout(() => {
        if (this.isLocked) {
            return;
        }
        buttonsContainer.style.display = 'flex';

        setTimeout(() => {
            const inTutorial = buttonsContainer.dataset.inTutorial === 'true' || window.isInTutorial === true;
            if (!this.isFocusing && !inTutorial) {
                buttonsContainer.style.display = 'none';
            } else if (inTutorial) {
                buttonsContainer.style.setProperty('display', 'flex', 'important');
            }
        }, 5000);
    }, 100);

    if (this.tutorialProtectionTimer) {
        clearInterval(this.tutorialProtectionTimer);
        this.tutorialProtectionTimer = null;
    }

    this.tutorialProtectionTimer = setInterval(() => {
        if (window.isInTutorial === true) {
            const style = window.getComputedStyle(buttonsContainer);
            if (style.display === 'none') {
                buttonsContainer.style.setProperty('display', 'flex', 'important');
                console.log('[Live2D] 引导中：恢复浮动按钮显示');
            }
        } else {
            if (this.tutorialProtectionTimer) {
                clearInterval(this.tutorialProtectionTimer);
                this.tutorialProtectionTimer = null;
            }
        }
    }, 300);

    this._syncButtonStatesWithGlobalState();

    if (this._outsideClickHandler) {
        document.removeEventListener('click', this._outsideClickHandler);
    }
    this._outsideClickHandler = (e) => {
        const path = e.composedPath ? e.composedPath() : (e.path || []);
        if (path.includes(buttonsContainer)) return;
        if (path.some(n => n && n.id && n.id.startsWith('live2d-popup-'))) return;
        if (path.some(n => n && typeof n.hasAttribute === 'function' && n.hasAttribute('data-neko-sidepanel'))) return;
        const openPopup = Array.from(document.querySelectorAll('[id^="live2d-popup-"]')).find(el =>
            getComputedStyle(el).display === 'flex');
        if (!openPopup) return;
        this.closeAllPopups();
    };
    document.addEventListener('click', this._outsideClickHandler);

    window.dispatchEvent(new CustomEvent('live2d-floating-buttons-ready'));
    console.log('[Live2D] 浮动按钮就绪事件已发送');
};

/**
 * 设置返回按钮拖拽（Live2D 特定）
 */
Live2DManager.prototype.setupReturnButtonContainerDrag = function(container) {
    this._setupReturnButtonDrag(container);
    this._addReturnButtonBreathingAnimation();
};
