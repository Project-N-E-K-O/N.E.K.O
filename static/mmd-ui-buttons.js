/**
 * MMD UI Buttons - 浮动按钮系统（完整功能版）
 * 与 VRM/Live2D 保持一致：麦克风、屏幕分享、Agent、设置、告别
 */

MMDManager.prototype.setupFloatingButtons = function () {
    if (window.location.pathname.includes('model_manager')) return;

    // 清理旧事件监听
    if (!this._uiWindowHandlers) this._uiWindowHandlers = [];
    if (this._uiWindowHandlers.length > 0) {
        this._uiWindowHandlers.forEach(({ event, handler, target, options }) => {
            const t = target || window;
            t.removeEventListener(event, handler, options);
        });
        this._uiWindowHandlers = [];
    }
    if (this._returnButtonDragHandlers) {
        document.removeEventListener('mousemove', this._returnButtonDragHandlers.mouseMove);
        document.removeEventListener('mouseup', this._returnButtonDragHandlers.mouseUp);
        document.removeEventListener('touchmove', this._returnButtonDragHandlers.touchMove);
        document.removeEventListener('touchend', this._returnButtonDragHandlers.touchEnd);
        this._returnButtonDragHandlers = null;
    }

    // 清理旧 DOM
    document.querySelectorAll('#mmd-floating-buttons, #mmd-lock-icon, #mmd-return-button-container').forEach(el => el.remove());

    const buttonsContainer = document.createElement('div');
    buttonsContainer.id = 'mmd-floating-buttons';
    document.body.appendChild(buttonsContainer);

    Object.assign(buttonsContainer.style, {
        position: 'fixed', zIndex: '99999', pointerEvents: 'auto',
        display: 'none', flexDirection: 'column', gap: '12px',
        visibility: 'visible', opacity: '1', transform: 'none'
    });
    this._floatingButtonsContainer = buttonsContainer;

    const stopContainerEvent = (e) => { e.stopPropagation(); };
    ['pointerdown', 'pointermove', 'pointerup', 'mousedown', 'mousemove', 'mouseup', 'touchstart', 'touchmove', 'touchend'].forEach(evt => {
        buttonsContainer.addEventListener(evt, stopContainerEvent);
    });

    buttonsContainer.addEventListener('mouseenter', () => { this._mmdButtonsHovered = true; });
    buttonsContainer.addEventListener('mouseleave', () => { this._mmdButtonsHovered = false; });

    // ═══════════════════ 响应式布局 ═══════════════════

    const applyResponsiveFloatingLayout = () => {
        if (this._isInReturnState) { buttonsContainer.style.display = 'none'; return; }
        const isLocked = this.isLocked;
        if (isLocked) { buttonsContainer.style.display = 'none'; return; }
        if (window.isMobileWidth && window.isMobileWidth()) {
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
            // 桌面端位置由 UI 更新循环控制
        }
    };
    applyResponsiveFloatingLayout();

    // 鼠标距离判定用状态
    const shouldShowLockIcon = () => {
        const isLocked = this.isLocked;
        if (this._isInReturnState) return false;
        if (isLocked) return true;
        const mouse = this._mmdMousePos;
        if (!mouse) return false;
        if (!this._mmdMousePosTs || (Date.now() - this._mmdMousePosTs > 1500)) return false;
        if (this._mmdLockIcon) {
            const rect = this._mmdLockIcon.getBoundingClientRect();
            const expandPx = 8;
            if (mouse.x >= rect.left - expandPx && mouse.x <= rect.right + expandPx &&
                mouse.y >= rect.top - expandPx && mouse.y <= rect.bottom + expandPx) return true;
        }
        const centerX = this._mmdModelCenterX;
        const centerY = this._mmdModelCenterY;
        if (typeof centerX !== 'number' || typeof centerY !== 'number') return false;
        if (this._mmdMouseInModelRegion) return true;
        const dx = mouse.x - centerX;
        const dy = mouse.y - centerY;
        const dist = Math.hypot(dx, dy);
        const modelHeight = Math.max(0, Number(this._mmdModelScreenHeight) || 0);
        const threshold = Math.max(90, Math.min(260, modelHeight * 0.55));
        return dist <= threshold;
    };
    this._shouldShowMmdLockIcon = shouldShowLockIcon;

    const updateMousePosition = (e) => {
        this._mmdMousePos = { x: typeof e.clientX === 'number' ? e.clientX : 0, y: typeof e.clientY === 'number' ? e.clientY : 0 };
        this._mmdMousePosTs = Date.now();
    };
    const mouseListenerOptions = { passive: true, capture: true };
    window.addEventListener('mousemove', updateMousePosition, mouseListenerOptions);
    this._uiWindowHandlers.push({ event: 'mousemove', handler: updateMousePosition, target: window, options: mouseListenerOptions });
    window.addEventListener('pointermove', updateMousePosition, mouseListenerOptions);
    this._uiWindowHandlers.push({ event: 'pointermove', handler: updateMousePosition, target: window, options: mouseListenerOptions });
    window.addEventListener('resize', applyResponsiveFloatingLayout);
    this._uiWindowHandlers.push({ event: 'resize', handler: applyResponsiveFloatingLayout, target: window });

    // ═══════════════════ 按钮配置 ═══════════════════

    const iconVersion = window.APP_VERSION ? `?v=${window.APP_VERSION}` : '?v=1.0.0';
    const buttonConfigs = [
        { id: 'mic', emoji: '🎤', title: window.t ? window.t('buttons.voiceControl') : '语音控制', titleKey: 'buttons.voiceControl', hasPopup: true, toggle: true, separatePopupTrigger: true, iconOff: '/static/icons/mic_icon_off.png' + iconVersion, iconOn: '/static/icons/mic_icon_on.png' + iconVersion },
        { id: 'screen', emoji: '🖥️', title: window.t ? window.t('buttons.screenShare') : '屏幕分享', titleKey: 'buttons.screenShare', hasPopup: true, toggle: true, separatePopupTrigger: true, iconOff: '/static/icons/screen_icon_off.png' + iconVersion, iconOn: '/static/icons/screen_icon_on.png' + iconVersion },
        { id: 'agent', emoji: '🔨', title: window.t ? window.t('buttons.agentTools') : 'NekoClaw', titleKey: 'buttons.agentTools', hasPopup: true, popupToggle: true, exclusive: 'settings', iconOff: '/static/icons/Agent_off.png' + iconVersion, iconOn: '/static/icons/Agent_on.png' + iconVersion },
        { id: 'settings', emoji: '⚙️', title: window.t ? window.t('buttons.settings') : '设置', titleKey: 'buttons.settings', hasPopup: true, popupToggle: true, exclusive: 'agent', iconOff: '/static/icons/set_off.png' + iconVersion, iconOn: '/static/icons/set_on.png' + iconVersion },
        { id: 'goodbye', emoji: '💤', title: window.t ? window.t('buttons.leave') : '请她离开', titleKey: 'buttons.leave', hasPopup: false, iconOff: '/static/icons/rest_off.png' + iconVersion, iconOn: '/static/icons/rest_on.png' + iconVersion }
    ];
    this._buttonConfigs = buttonConfigs;
    this._floatingButtons = this._floatingButtons || {};

    // ═══════════════════ 创建按钮 ═══════════════════

    buttonConfigs.forEach(config => {
        if (window.isMobileWidth && window.isMobileWidth() && (config.id === 'agent' || config.id === 'goodbye')) return;

        const btnWrapper = document.createElement('div');
        Object.assign(btnWrapper.style, { position: 'relative', display: 'flex', alignItems: 'center', gap: '8px', pointerEvents: 'auto' });
        ['pointerdown', 'mousedown', 'touchstart'].forEach(evt => btnWrapper.addEventListener(evt, e => e.stopPropagation()));

        const btn = document.createElement('div');
        btn.id = `mmd-btn-${config.id}`;
        btn.className = 'mmd-floating-btn';

        Object.assign(btn.style, {
            width: '48px', height: '48px', borderRadius: '50%', background: 'var(--neko-btn-bg, rgba(255,255,255,0.65))',
            backdropFilter: 'saturate(180%) blur(20px)', border: 'var(--neko-btn-border, 1px solid rgba(255,255,255,0.18))',
            display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '24px',
            cursor: 'pointer', userSelect: 'none', boxShadow: 'var(--neko-btn-shadow, 0 2px 4px rgba(0,0,0,0.04), 0 4px 8px rgba(0,0,0,0.08))',
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

            this._floatingButtons[config.id] = { button: btn, imgOff: imgOff, imgOn: imgOn };

            // 悬停效果
            btn.addEventListener('mouseenter', () => {
                btn.style.transform = 'scale(1.05)';
                btn.style.boxShadow = 'var(--neko-btn-shadow-hover, 0 4px 8px rgba(0,0,0,0.08), 0 8px 16px rgba(0,0,0,0.08))';
                btn.style.background = 'var(--neko-btn-bg-hover, rgba(255,255,255,0.8))';
                if (config.separatePopupTrigger) {
                    const popup = document.getElementById(`mmd-popup-${config.id}`);
                    const isPopupVisible = popup && popup.style.display === 'flex' && popup.style.opacity === '1';
                    if (isPopupVisible) return;
                }
                if (imgOff && imgOn) { imgOff.style.opacity = '0'; imgOn.style.opacity = '1'; }
            });

            btn.addEventListener('mouseleave', () => {
                btn.style.transform = 'scale(1)';
                btn.style.boxShadow = 'var(--neko-btn-shadow, 0 2px 4px rgba(0,0,0,0.04), 0 4px 8px rgba(0,0,0,0.08))';
                const isActive = btn.dataset.active === 'true';
                const popup = document.getElementById(`mmd-popup-${config.id}`);
                const isPopupVisible = popup && popup.style.display === 'flex' && popup.style.opacity === '1';
                const shouldShowOnIcon = config.separatePopupTrigger ? isActive : (isActive || isPopupVisible);
                btn.style.background = shouldShowOnIcon ? 'var(--neko-btn-bg-active, rgba(255,255,255,0.75))' : 'var(--neko-btn-bg, rgba(255,255,255,0.65))';
                if (imgOff && imgOn) {
                    imgOff.style.opacity = shouldShowOnIcon ? '0' : '1';
                    imgOn.style.opacity = shouldShowOnIcon ? '1' : '0';
                }
            });

            btn.addEventListener('click', (e) => {
                e.stopPropagation(); e.preventDefault();

                if (config.id === 'mic') {
                    const isMicStarting = window.isMicStarting || false;
                    if (isMicStarting) {
                        if (btn.dataset.active !== 'true') this.setButtonActive(config.id, true);
                        return;
                    }
                }
                if (config.id === 'screen') {
                    const isRecording = window.isRecording || false;
                    const wantToActivate = btn.dataset.active !== 'true';
                    if (wantToActivate && !isRecording) {
                        if (typeof window.showStatusToast === 'function') {
                            window.showStatusToast(window.t ? window.t('app.screenShareRequiresVoice') : '屏幕分享仅用于音视频通话', 3000);
                        }
                        return;
                    }
                }
                if (config.popupToggle) return;

                const currentActive = btn.dataset.active === 'true';
                let targetActive = !currentActive;

                if (config.id === 'mic' || config.id === 'screen') {
                    window.dispatchEvent(new CustomEvent(`live2d-${config.id}-toggle`, { detail: { active: targetActive } }));
                    this.setButtonActive(config.id, targetActive);
                } else if (config.id === 'goodbye') {
                    window.dispatchEvent(new CustomEvent('live2d-goodbye-click'));
                    return;
                }

                btn.style.background = targetActive ? 'var(--neko-btn-bg-active, rgba(255,255,255,0.75))' : 'var(--neko-btn-bg-hover, rgba(255,255,255,0.8))';
            });
        }

        btnWrapper.appendChild(btn);

        // ═══════ 弹窗系统 ═══════

        if (config.hasPopup && config.separatePopupTrigger) {
            if (window.isMobileWidth && window.isMobileWidth() && config.id === 'mic') {
                buttonsContainer.appendChild(btnWrapper);
                return;
            }

            const popup = this.createPopup(config.id);
            const triggerBtn = document.createElement('button');
            triggerBtn.type = 'button';
            triggerBtn.className = 'mmd-trigger-btn';
            triggerBtn.setAttribute('aria-label', 'Open popup');

            const triggerImg = document.createElement('img');
            triggerImg.src = '/static/icons/play_trigger_icon.png' + iconVersion;
            triggerImg.alt = '';
            triggerImg.className = `mmd-trigger-icon-${config.id}`;
            Object.assign(triggerImg.style, {
                width: '22px', height: '22px', objectFit: 'contain',
                pointerEvents: 'none', imageRendering: 'crisp-edges',
                transition: 'transform 0.3s cubic-bezier(0.1, 0.9, 0.2, 1)'
            });
            Object.assign(triggerBtn.style, {
                width: '24px', height: '24px', borderRadius: '50%',
                background: 'var(--neko-btn-bg, rgba(255,255,255,0.65))', backdropFilter: 'saturate(180%) blur(20px)',
                border: 'var(--neko-btn-border, 1px solid rgba(255,255,255,0.18))',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                cursor: 'pointer', userSelect: 'none',
                boxShadow: 'var(--neko-btn-shadow, 0 2px 4px rgba(0,0,0,0.04), 0 4px 8px rgba(0,0,0,0.08))',
                transition: 'all 0.1s ease', pointerEvents: 'auto', marginLeft: '-10px'
            });
            triggerBtn.appendChild(triggerImg);

            const stopTriggerEvent = (e) => { e.stopPropagation(); };
            ['pointerdown', 'mousedown', 'touchstart'].forEach(evt => triggerBtn.addEventListener(evt, stopTriggerEvent));

            triggerBtn.addEventListener('click', async (e) => {
                e.stopPropagation();
                const isPopupVisible = popup.style.display === 'flex' && popup.style.opacity === '1';
                if (config.id === 'mic' && !isPopupVisible) {
                    if (typeof window.renderFloatingMicList === 'function') await window.renderFloatingMicList(popup);
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
        } else if (config.popupToggle) {
            const popup = this.createPopup(config.id);
            btnWrapper.appendChild(btn);
            btnWrapper.appendChild(popup);

            let isToggling = false;
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                if (isToggling) return;
                const isPopupVisible = popup.style.display === 'flex' && popup.style.opacity !== '0' && popup.style.opacity !== '';
                if (!isPopupVisible && config.exclusive) {
                    this.closePopupById(config.exclusive);
                    const exclusiveData = this._floatingButtons[config.exclusive];
                    if (exclusiveData && exclusiveData.button) {
                        exclusiveData.button.style.background = 'var(--neko-btn-bg, rgba(255,255,255,0.65))';
                    }
                    if (exclusiveData && exclusiveData.imgOff && exclusiveData.imgOn) {
                        exclusiveData.imgOff.style.opacity = '1';
                        exclusiveData.imgOn.style.opacity = '0';
                    }
                }
                isToggling = true;
                this.showPopup(config.id, popup);
                setTimeout(() => {
                    const newPopupVisible = popup.style.display === 'flex' && popup.style.opacity !== '0' && popup.style.opacity !== '';
                    if (newPopupVisible) {
                        btn.style.background = 'var(--neko-btn-bg-active, rgba(255,255,255,0.75))';
                        if (imgOff && imgOn) { imgOff.style.opacity = '0'; imgOn.style.opacity = '1'; }
                    } else {
                        btn.style.background = 'var(--neko-btn-bg, rgba(255,255,255,0.65))';
                        if (imgOff && imgOn) { imgOff.style.opacity = '1'; imgOn.style.opacity = '0'; }
                    }
                    isToggling = false;
                }, 200);
            });
        }

        buttonsContainer.appendChild(btnWrapper);
    });

    // ═══════════════════ Goodbye 处理 ═══════════════════

    const goodbyeHandler = () => {
        this._isInReturnState = true;
        if (this._floatingButtonsContainer) this._floatingButtonsContainer.style.display = 'none';
        if (this._mmdLockIcon) this._mmdLockIcon.style.display = 'none';
        if (this._returnButtonContainer) {
            this._returnButtonContainer.style.left = '50%';
            this._returnButtonContainer.style.top = '50%';
            this._returnButtonContainer.style.transform = 'translate(-50%, -50%)';
            this._returnButtonContainer.style.display = 'flex';
        }
    };
    this._uiWindowHandlers.push({ event: 'live2d-goodbye-click', handler: goodbyeHandler });
    window.addEventListener('live2d-goodbye-click', goodbyeHandler);

    // ═══════════════════ Return 处理 ═══════════════════

    const returnHandler = () => {
        this._isInReturnState = false;
        if (this._returnButtonContainer) this._returnButtonContainer.style.display = 'none';

        const bc = document.getElementById('mmd-floating-buttons');
        if (!bc) { this.setupFloatingButtons(); return; }
        bc.style.removeProperty('display');
        bc.style.removeProperty('visibility');
        bc.style.removeProperty('opacity');

        if (this.core && typeof this.core.setLocked === 'function') {
            this.core.setLocked(false);
        }

        applyResponsiveFloatingLayout();

        if (this._mmdLockIcon) {
            this._mmdLockIcon.style.removeProperty('display');
            this._mmdLockIcon.style.removeProperty('visibility');
            this._mmdLockIcon.style.removeProperty('opacity');
            this._mmdLockIcon.style.backgroundImage = 'url(/static/icons/unlocked_icon.png)';
            this._mmdLockIcon.style.display = shouldShowLockIcon() ? 'block' : 'none';
        }
    };
    this._uiWindowHandlers.push({ event: 'mmd-return-click', handler: returnHandler });
    this._uiWindowHandlers.push({ event: 'live2d-return-click', handler: returnHandler });
    window.addEventListener('mmd-return-click', returnHandler);
    window.addEventListener('live2d-return-click', returnHandler);

    // ═══════════════════ "请她回来" 按钮 ═══════════════════

    const returnButtonContainer = document.createElement('div');
    returnButtonContainer.id = 'mmd-return-button-container';
    Object.assign(returnButtonContainer.style, {
        position: 'fixed', left: '50%', top: '50%', transform: 'translate(-50%, -50%)',
        zIndex: '99999', pointerEvents: 'auto', display: 'none'
    });

    const returnBtn = document.createElement('div');
    returnBtn.id = 'mmd-btn-return';
    returnBtn.className = 'mmd-return-btn';

    const returnImgOff = document.createElement('img');
    returnImgOff.src = '/static/icons/rest_off.png' + iconVersion; returnImgOff.alt = '💤';
    Object.assign(returnImgOff.style, { width: '64px', height: '64px', objectFit: 'contain', pointerEvents: 'none', opacity: '1', transition: 'opacity 0.3s ease' });

    const returnImgOn = document.createElement('img');
    returnImgOn.src = '/static/icons/rest_on.png' + iconVersion; returnImgOn.alt = '💤';
    Object.assign(returnImgOn.style, { position: 'absolute', width: '64px', height: '64px', objectFit: 'contain', pointerEvents: 'none', opacity: '0', transition: 'opacity 0.3s ease' });

    Object.assign(returnBtn.style, {
        width: '64px', height: '64px', borderRadius: '50%', background: 'var(--neko-btn-bg, rgba(255,255,255,0.65))',
        backdropFilter: 'saturate(180%) blur(20px)', border: 'var(--neko-btn-border, 1px solid rgba(255,255,255,0.18))',
        display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer',
        boxShadow: 'var(--neko-btn-shadow, 0 2px 4px rgba(0,0,0,0.04), 0 4px 8px rgba(0,0,0,0.08))',
        transition: 'all 0.1s ease', pointerEvents: 'auto', position: 'relative'
    });

    returnBtn.addEventListener('mouseenter', () => {
        returnBtn.style.transform = 'scale(1.05)';
        returnBtn.style.boxShadow = 'var(--neko-btn-shadow-hover, 0 4px 8px rgba(0,0,0,0.08), 0 8px 16px rgba(0,0,0,0.08))';
        returnBtn.style.background = 'var(--neko-btn-bg-hover, rgba(255,255,255,0.8))';
        returnImgOff.style.opacity = '0'; returnImgOn.style.opacity = '1';
    });
    returnBtn.addEventListener('mouseleave', () => {
        returnBtn.style.transform = 'scale(1)';
        returnBtn.style.boxShadow = 'var(--neko-btn-shadow, 0 2px 4px rgba(0,0,0,0.04), 0 4px 8px rgba(0,0,0,0.08))';
        returnBtn.style.background = 'var(--neko-btn-bg, rgba(255,255,255,0.65))';
        returnImgOff.style.opacity = '1'; returnImgOn.style.opacity = '0';
    });
    returnBtn.addEventListener('click', (e) => {
        if (returnButtonContainer.getAttribute('data-dragging') === 'true') { e.preventDefault(); e.stopPropagation(); return; }
        e.stopPropagation(); e.preventDefault();
        window.dispatchEvent(new CustomEvent('mmd-return-click'));
    });

    returnBtn.appendChild(returnImgOff);
    returnBtn.appendChild(returnImgOn);
    returnButtonContainer.appendChild(returnBtn);
    document.body.appendChild(returnButtonContainer);
    this._returnButtonContainer = returnButtonContainer;
    this._setupReturnButtonDrag(returnButtonContainer);
    this._addReturnButtonBreathingAnimation();

    // ═══════════════════ Lock 图标 ═══════════════════

    document.querySelectorAll('#mmd-lock-icon').forEach(el => el.remove());
    const lockIcon = document.createElement('div');
    lockIcon.id = 'mmd-lock-icon';
    lockIcon.dataset.mmdLock = 'true';
    document.body.appendChild(lockIcon);
    this._mmdLockIcon = lockIcon;

    Object.assign(lockIcon.style, {
        position: 'fixed', zIndex: '99999', width: '32px', height: '32px',
        cursor: 'pointer', display: 'none',
        backgroundImage: 'url(/static/icons/unlocked_icon.png)',
        backgroundSize: 'contain', backgroundRepeat: 'no-repeat', backgroundPosition: 'center',
        pointerEvents: 'auto', transition: 'transform 0.1s'
    });

    const toggleLock = (e) => {
        if (e) { e.preventDefault(); e.stopPropagation(); }
        const currentLocked = this.isLocked;
        const newLocked = !currentLocked;
        if (this.core && typeof this.core.setLocked === 'function') {
            this.core.setLocked(newLocked);
        }
        const isLocked = this.isLocked;
        lockIcon.style.backgroundImage = isLocked ? 'url(/static/icons/locked_icon.png)' : 'url(/static/icons/unlocked_icon.png)';

        const currentTransform = lockIcon.style.transform || '';
        const baseScaleMatch = currentTransform.match(/scale\(([\d.]+)\)/);
        const baseScale = baseScaleMatch ? parseFloat(baseScaleMatch[1]) : 1.0;
        lockIcon.style.transform = `scale(${baseScale * 0.9})`;
        setTimeout(() => { lockIcon.style.transform = `scale(${baseScale})`; }, 100);

        lockIcon.style.display = shouldShowLockIcon() ? 'block' : 'none';
        applyResponsiveFloatingLayout();
    };
    lockIcon.addEventListener('mousedown', toggleLock);
    lockIcon.addEventListener('touchstart', toggleLock, { passive: false });

    // ═══════════════════ 启动 UI 更新循环 ═══════════════════

    this._startUIUpdateLoop();

    // 初始化后显示按钮
    setTimeout(() => {
        applyResponsiveFloatingLayout();
        if (this._mmdLockIcon) this._mmdLockIcon.style.display = shouldShowLockIcon() ? 'block' : 'none';
    }, 100);

    this._syncButtonStatesWithGlobalState();

    // 通知外部浮动按钮已就绪
    window.dispatchEvent(new CustomEvent('live2d-floating-buttons-ready'));
};

// ═══════════════════ UI 更新循环 ═══════════════════

MMDManager.prototype._startUIUpdateLoop = function () {
    if (this._uiUpdateLoopId !== null && this._uiUpdateLoopId !== undefined) return;

    const box = new window.THREE.Box3();
    const getVisibleButtonCount = () => {
        const mobile = window.isMobileWidth && window.isMobileWidth();
        return [{ id: 'mic' }, { id: 'screen' }, { id: 'agent' }, { id: 'settings' }, { id: 'goodbye' }]
            .filter(c => !(mobile && (c.id === 'agent' || c.id === 'goodbye'))).length;
    };
    const baseButtonSize = 48;
    const baseGap = 12;
    let lastMobileUpdate = 0;
    const MOBILE_UPDATE_INTERVAL = 100;

    const update = () => {
        if (this._uiUpdateLoopId === null || this._uiUpdateLoopId === undefined) return;

        if (!this.currentModel || !this.currentModel.mesh) {
            if (this._uiUpdateLoopId !== null && this._uiUpdateLoopId !== undefined) this._uiUpdateLoopId = requestAnimationFrame(update);
            return;
        }

        if (this._isInReturnState) {
            if (this._uiUpdateLoopId !== null && this._uiUpdateLoopId !== undefined) this._uiUpdateLoopId = requestAnimationFrame(update);
            return;
        }

        if (window.isMobileWidth && window.isMobileWidth()) {
            const now = performance.now();
            if (now - lastMobileUpdate < MOBILE_UPDATE_INTERVAL) {
                if (this._uiUpdateLoopId !== null && this._uiUpdateLoopId !== undefined) this._uiUpdateLoopId = requestAnimationFrame(update);
                return;
            }
            lastMobileUpdate = now;
        }

        const buttonsContainer = document.getElementById('mmd-floating-buttons');
        const lockIcon = this._mmdLockIcon;

        if (!this.camera || !this.renderer) {
            if (this._uiUpdateLoopId !== null && this._uiUpdateLoopId !== undefined) this._uiUpdateLoopId = requestAnimationFrame(update);
            return;
        }

        try {
            const camera = this.camera;
            const renderer = this.renderer;
            const canvasRect = renderer.domElement.getBoundingClientRect();
            const canvasWidth = canvasRect.width;
            const canvasHeight = canvasRect.height;

            box.setFromObject(this.currentModel.mesh);

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
                corner.project(camera);
                const sx = canvasRect.left + (corner.x * 0.5 + 0.5) * canvasWidth;
                const sy = canvasRect.top + (-corner.y * 0.5 + 0.5) * canvasHeight;
                screenLeft = Math.min(screenLeft, sx);
                screenRight = Math.max(screenRight, sx);
                screenTop = Math.min(screenTop, sy);
                screenBottom = Math.max(screenBottom, sy);
            }

            const visibleLeft = Math.max(0, Math.min(canvasWidth, screenLeft - canvasRect.left));
            const visibleRight = Math.max(0, Math.min(canvasWidth, screenRight - canvasRect.left));
            const visibleTop = Math.max(0, Math.min(canvasHeight, screenTop - canvasRect.top));
            const visibleBottom = Math.max(0, Math.min(canvasHeight, screenBottom - canvasRect.top));
            const visibleHeight = Math.max(1, visibleBottom - visibleTop);

            const modelScreenHeight = visibleHeight;
            const modelCenterY = canvasRect.top + (visibleTop + visibleBottom) / 2;
            const modelCenterX = canvasRect.left + (visibleLeft + visibleRight) / 2;
            this._mmdModelCenterX = modelCenterX;
            this._mmdModelCenterY = modelCenterY;
            this._mmdModelScreenHeight = modelScreenHeight;

            const mouse = this._mmdMousePos;
            const mouseStale = !this._mmdMousePosTs || (Date.now() - this._mmdMousePosTs > 1500);
            const mouseDist = (mouse && !mouseStale) ? Math.hypot(mouse.x - modelCenterX, mouse.y - modelCenterY) : Infinity;
            const baseThreshold = Math.max(90, Math.min(260, modelScreenHeight * 0.55));

            const padX = Math.max(60, (visibleRight - visibleLeft) * 0.3);
            const padY = Math.max(40, (visibleBottom - visibleTop) * 0.2);
            const mouseInModelRegion = mouse && !mouseStale &&
                mouse.x >= canvasRect.left + visibleLeft - padX &&
                mouse.x <= canvasRect.left + visibleRight + padX &&
                mouse.y >= canvasRect.top + visibleTop - padY &&
                mouse.y <= canvasRect.top + visibleBottom + padY;

            this._mmdMouseInModelRegion = !!mouseInModelRegion;

            const showThreshold = baseThreshold;
            const hideThreshold = baseThreshold * 1.2;
            if (this._mmdUiNearModel !== true && (mouseDist <= showThreshold || mouseInModelRegion)) {
                this._mmdUiNearModel = true;
            } else if (this._mmdUiNearModel !== false && mouseDist >= hideThreshold && !mouseInModelRegion) {
                this._mmdUiNearModel = false;
            } else if (typeof this._mmdUiNearModel !== 'boolean') {
                this._mmdUiNearModel = false;
            }

            // 按钮缩放
            const visibleCount = getVisibleButtonCount();
            const baseToolbarHeight = baseButtonSize * visibleCount + baseGap * (visibleCount - 1);
            const targetToolbarHeight = modelScreenHeight / 2;
            const scale = Math.max(0.5, Math.min(1.0, targetToolbarHeight / baseToolbarHeight));

            // 更新按钮位置
            if (buttonsContainer) {
                const isMobile = window.isMobileWidth && window.isMobileWidth();
                if (isMobile) {
                    buttonsContainer.style.transformOrigin = 'right bottom';
                    buttonsContainer.style.display = 'flex';
                } else {
                    buttonsContainer.style.transformOrigin = 'left top';
                    const isLocked = this.isLocked;
                    const hoveringButtons = this._mmdButtonsHovered === true;
                    const hasOpenPopup = Array.from(document.querySelectorAll('[id^="mmd-popup-"]'))
                        .some(popup => popup.style.display === 'flex' && popup.style.opacity !== '0');
                    const shouldShowButtons = !isLocked && (this._mmdUiNearModel || hoveringButtons || hasOpenPopup);
                    buttonsContainer.style.display = shouldShowButtons ? 'flex' : 'none';
                }
                buttonsContainer.style.transform = `scale(${scale})`;

                if (!isMobile) {
                    const screenWidth = window.innerWidth;
                    const screenHeight = window.innerHeight;
                    const targetX = canvasRect.left + visibleRight * 0.8 + visibleLeft * 0.2;
                    const actualToolbarHeight = baseToolbarHeight * scale;
                    const actualToolbarWidth = 80 * scale;
                    const offsetY = Math.min(modelScreenHeight * 0.1, screenHeight * 0.08);
                    const targetY = modelCenterY - actualToolbarHeight / 2 - offsetY;
                    const boundedY = Math.max(20, Math.min(targetY, screenHeight - actualToolbarHeight - 20));
                    const boundedX = Math.max(0, Math.min(targetX, screenWidth - actualToolbarWidth));

                    const currentLeft = parseFloat(buttonsContainer.style.left) || 0;
                    const currentTop = parseFloat(buttonsContainer.style.top) || 0;
                    const dist = Math.sqrt(Math.pow(boundedX - currentLeft, 2) + Math.pow(boundedY - currentTop, 2));
                    if (dist > 0.5) {
                        buttonsContainer.style.left = `${boundedX}px`;
                        buttonsContainer.style.top = `${boundedY}px`;
                    }

                    // Lock 图标位置
                    if (lockIcon && !this._isInReturnState) {
                        const lockTargetX = canvasRect.left + visibleRight * 0.7 + visibleLeft * 0.3;
                        const lockTargetY = canvasRect.top + visibleTop * 0.3 + visibleBottom * 0.7;

                        lockIcon.style.transformOrigin = 'center center';
                        lockIcon.style.transform = `scale(${scale})`;

                        const baseLockIconSize = 32;
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
                        lockIcon.style.display = (this._shouldShowMmdLockIcon && this._shouldShowMmdLockIcon()) ? 'block' : 'none';

                        // 检测是否被弹窗覆盖
                        const lockRect = lockIcon.getBoundingClientRect();
                        let isLockOverlapped = false;
                        document.querySelectorAll('[id^="mmd-popup-"]').forEach(popup => {
                            if (popup.style.display === 'flex' && popup.style.opacity === '1') {
                                const popupRect = popup.getBoundingClientRect();
                                if (lockRect.right > popupRect.left && lockRect.left < popupRect.right &&
                                    lockRect.bottom > popupRect.top && lockRect.top < popupRect.bottom) {
                                    isLockOverlapped = true;
                                }
                            }
                        });
                        lockIcon.style.opacity = isLockOverlapped ? '0.3' : '';
                    }
                }
            }
        } catch (error) {
            if (window.DEBUG_MODE) console.debug('[MMD UI] 更新循环单帧异常:', error);
        }

        if (this._uiUpdateLoopId !== null && this._uiUpdateLoopId !== undefined) {
            this._uiUpdateLoopId = requestAnimationFrame(update);
        }
    };

    this._uiUpdateLoopId = requestAnimationFrame(update);
};

// ═══════════════════ Return 按钮拖拽 ═══════════════════

MMDManager.prototype._setupReturnButtonDrag = function (container) {
    let isDragging = false;
    let dragStartX = 0, dragStartY = 0, containerStartX = 0, containerStartY = 0;

    const handleStart = (clientX, clientY) => {
        isDragging = true;
        dragStartX = clientX; dragStartY = clientY;
        const rect = container.getBoundingClientRect();
        containerStartX = rect.left; containerStartY = rect.top;
        container.style.transform = 'none';
        container.style.left = `${containerStartX}px`;
        container.style.top = `${containerStartY}px`;
        container.setAttribute('data-dragging', 'false');
        container.style.cursor = 'grabbing';
    };
    const handleMove = (clientX, clientY) => {
        if (!isDragging) return;
        const deltaX = clientX - dragStartX, deltaY = clientY - dragStartY;
        if (Math.abs(deltaX) > 5 || Math.abs(deltaY) > 5) container.setAttribute('data-dragging', 'true');
        const w = container.offsetWidth || 64, h = container.offsetHeight || 64;
        container.style.left = `${Math.max(0, Math.min(containerStartX + deltaX, window.innerWidth - w))}px`;
        container.style.top = `${Math.max(0, Math.min(containerStartY + deltaY, window.innerHeight - h))}px`;
    };
    const handleEnd = () => {
        if (isDragging) {
            isDragging = false;
            container.style.cursor = 'grab';
            // 拖拽结束后延迟清除标记，让 click handler 能检测到拖拽
            // 非拖拽点击由 returnBtn 的 click handler 统一 dispatch
            setTimeout(() => container.setAttribute('data-dragging', 'false'), 10);
        }
    };

    container.addEventListener('mousedown', (e) => { if (container.contains(e.target)) { e.preventDefault(); handleStart(e.clientX, e.clientY); } });

    this._returnButtonDragHandlers = {
        mouseMove: (e) => handleMove(e.clientX, e.clientY),
        mouseUp: handleEnd,
        touchMove: (e) => { if (isDragging) { e.preventDefault(); handleMove(e.touches[0].clientX, e.touches[0].clientY); } },
        touchEnd: handleEnd
    };
    document.addEventListener('mousemove', this._returnButtonDragHandlers.mouseMove);
    document.addEventListener('mouseup', this._returnButtonDragHandlers.mouseUp);
    container.addEventListener('touchstart', (e) => { if (container.contains(e.target)) { handleStart(e.touches[0].clientX, e.touches[0].clientY); } }, { passive: true });
    document.addEventListener('touchmove', this._returnButtonDragHandlers.touchMove, { passive: false });
    document.addEventListener('touchend', this._returnButtonDragHandlers.touchEnd);
    container.style.cursor = 'grab';
};

// ═══════════════════ Return 按钮呼吸灯 ═══════════════════

MMDManager.prototype._addReturnButtonBreathingAnimation = function () {
    if (document.getElementById('mmd-return-button-breathing-styles')) return;
    const style = document.createElement('style');
    style.id = 'mmd-return-button-breathing-styles';
    style.textContent = `
        @keyframes mmdReturnButtonBreathing {
            0%, 100% { box-shadow: 0 0 8px rgba(68, 183, 254, 0.6), 0 2px 4px rgba(0,0,0,0.04), 0 8px 16px rgba(0,0,0,0.08); }
            50% { box-shadow: 0 0 18px rgba(68, 183, 254, 1), 0 2px 4px rgba(0,0,0,0.04), 0 8px 16px rgba(0,0,0,0.08); }
        }
        #mmd-btn-return { animation: mmdReturnButtonBreathing 2s ease-in-out infinite; }
        #mmd-btn-return:hover { animation: none; }
    `;
    document.head.appendChild(style);
};

// ═══════════════════ 按钮状态管理 ═══════════════════

MMDManager.prototype.setButtonActive = function (buttonId, active) {
    const buttonData = this._floatingButtons && this._floatingButtons[buttonId];
    if (!buttonData || !buttonData.button) return;
    buttonData.button.dataset.active = active ? 'true' : 'false';
    buttonData.button.style.background = active
        ? 'var(--neko-btn-bg-active, rgba(255,255,255,0.75))'
        : 'var(--neko-btn-bg, rgba(255,255,255,0.65))';
    if (buttonData.imgOff) buttonData.imgOff.style.opacity = active ? '0' : '1';
    if (buttonData.imgOn) buttonData.imgOn.style.opacity = active ? '1' : '0';
};

MMDManager.prototype.resetAllButtons = function () {
    if (!this._floatingButtons) return;
    Object.keys(this._floatingButtons).forEach(btnId => { this.setButtonActive(btnId, false); });
};

MMDManager.prototype._syncButtonStatesWithGlobalState = function () {
    if (!this._floatingButtons) return;
    const isRecording = window.isRecording || false;
    if (this._floatingButtons.mic) this.setButtonActive('mic', isRecording);

    let isScreenSharing = false;
    const screenButton = document.getElementById('screenButton');
    const stopButton = document.getElementById('stopButton');
    if (screenButton && screenButton.classList.contains('active')) isScreenSharing = true;
    else if (stopButton && !stopButton.disabled) isScreenSharing = true;
    if (this._floatingButtons.screen) this.setButtonActive('screen', isScreenSharing);
};

// ═══════════════════ 清理 ═══════════════════

MMDManager.prototype.cleanupFloatingButtons = function () {
    if (this._uiUpdateLoopId !== null && this._uiUpdateLoopId !== undefined) {
        cancelAnimationFrame(this._uiUpdateLoopId);
        this._uiUpdateLoopId = null;
    }

    document.querySelectorAll('#mmd-floating-buttons, #mmd-lock-icon, #mmd-return-button-container').forEach(el => el.remove());

    if (this._uiWindowHandlers) {
        this._uiWindowHandlers.forEach(({ event, handler, target, options }) => {
            (target || window).removeEventListener(event, handler, options);
        });
        this._uiWindowHandlers = [];
    }

    if (this._returnButtonDragHandlers) {
        document.removeEventListener('mousemove', this._returnButtonDragHandlers.mouseMove);
        document.removeEventListener('mouseup', this._returnButtonDragHandlers.mouseUp);
        document.removeEventListener('touchmove', this._returnButtonDragHandlers.touchMove);
        document.removeEventListener('touchend', this._returnButtonDragHandlers.touchEnd);
        this._returnButtonDragHandlers = null;
    }

    this._mmdLockIcon = null;
    this._floatingButtons = null;
    this._returnButtonContainer = null;
};
