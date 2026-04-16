/**
 * VRM UI Buttons - 浮动按钮系统（精简版）
 * 使用 AvatarButtonMixin 的 VRM 特定实现
 */

// 应用 mixin 到 VRM Manager
AvatarButtonMixin.apply(VRMManager.prototype, 'vrm', {
    containerElementId: 'vrm-floating-buttons',
    returnContainerId: 'vrm-return-button-container',
    returnBtnId: 'vrm-btn-return',
    lockIconId: 'vrm-lock-icon',
    popupPrefix: 'vrm',
    buttonClassPrefix: 'vrm-floating-btn',
    triggerBtnClass: 'vrm-trigger-btn',
    triggerIconClass: 'vrm-trigger-icon',
    returnBtnClass: 'vrm-return-btn',
    returnBreathingStyleId: 'vrm-return-button-breathing-styles',
    excludeLiveD2Elements: ['#live2d-floating-buttons', '#live2d-lock-icon', '#live2d-return-button-container']
});

/**
 * 设置浮动按钮系统（VRM 特定）
 */
VRMManager.prototype.setupFloatingButtons = function() {
    if (window.location.pathname.includes('model_manager')) {
        return;
    }

    // 防御性检查：当前模型类型不是 VRM 时不创建按钮（防止过时的异步回调）
    var cfgType = (window.lanlan_config && window.lanlan_config.model_type || '').toLowerCase();
    var cfgSub = (window.lanlan_config && window.lanlan_config.live3d_sub_type || '').toLowerCase();
    var isVrm = cfgType === 'vrm' || (cfgType === 'live3d' && cfgSub === 'vrm');
    if (cfgType && !isVrm) return;

    // 基础框架初始化
    const buttonsContainer = this.setupFloatingButtonsBase();

    // VRM 特定的响应式布局处理
    buttonsContainer.addEventListener('mouseenter', () => { this._vrmButtonsHovered = true; });
    buttonsContainer.addEventListener('mouseleave', () => { this._vrmButtonsHovered = false; });

    const opts = this._avatarButtonOptions;
    const prefix = this._avatarPrefix;

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
        }
    };
    applyResponsiveFloatingLayout();

    // 锁图标显示逻辑
    const shouldShowLockIcon = () => {
        const isLocked = this.interaction && this.interaction.checkLocked ? this.interaction.checkLocked() : false;
        if (this._isInReturnState) return false;
        if (isLocked) return true;

        const mouse = this._vrmMousePos;
        if (!mouse) return false;
        if (!this._vrmMousePosTs || (Date.now() - this._vrmMousePosTs > 1500)) return false;

        if (this._vrmLockIcon) {
            const rect = this._vrmLockIcon.getBoundingClientRect();
            const expandPx = 8;
            const inExpandedRect =
                mouse.x >= rect.left - expandPx &&
                mouse.x <= rect.right + expandPx &&
                mouse.y >= rect.top - expandPx &&
                mouse.y <= rect.bottom + expandPx;
            if (inExpandedRect) return true;
        }

        const centerX = this._vrmModelCenterX;
        const centerY = this._vrmModelCenterY;
        if (!mouse || typeof centerX !== 'number' || typeof centerY !== 'number') return false;

        if (this._vrmMouseInModelRegion) return true;

        const dx = mouse.x - centerX;
        const dy = mouse.y - centerY;
        const dist = Math.hypot(dx, dy);
        const modelHeight = Math.max(0, Number(this._vrmModelScreenHeight) || 0);
        const threshold = Math.max(90, Math.min(260, modelHeight * 0.55));
        return dist <= threshold;
    };
    this._shouldShowVrmLockIcon = shouldShowLockIcon;

    // 鼠标位置跟踪
    const updateMousePosition = (e) => {
        this._vrmMousePos = {
            x: typeof e.clientX === 'number' ? e.clientX : 0,
            y: typeof e.clientY === 'number' ? e.clientY : 0
        };
        this._vrmMousePosTs = Date.now();
    };
    const mouseListenerOptions = { passive: true, capture: true };
    this._uiWindowHandlers.push({ event: 'mousemove', handler: updateMousePosition, target: window, options: mouseListenerOptions });
    window.addEventListener('mousemove', updateMousePosition, mouseListenerOptions);
    this._uiWindowHandlers.push({ event: 'resize', handler: applyResponsiveFloatingLayout, target: window });
    window.addEventListener('resize', applyResponsiveFloatingLayout);

    // 获取按钮配置
    const buttonConfigs = this.getDefaultButtonConfigs();
    this._buttonConfigs = buttonConfigs;
    this._floatingButtons = this._floatingButtons || {};

    // 创建按钮
    buttonConfigs.forEach(config => {
        if (window.isMobileWidth() && (config.id === 'agent' || config.id === 'goodbye')) {
            return;
        }

        const { btnWrapper, btn, imgOff, imgOn } = this.createButtonElement(config, buttonsContainer);

        // 点击事件处理
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            e.preventDefault();

            if (config.id === 'mic') {
                const isMicStarting = window.isMicStarting || false;
                if (isMicStarting) {
                    if (btn.dataset.active !== 'true') {
                        this.setButtonActive(config.id, true);
                    }
                    return;
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

            btn.style.background = targetActive ? 'var(--neko-btn-bg-active, rgba(255,255,255,0.75))' : 'var(--neko-btn-bg-hover, rgba(255,255,255,0.8))';
        });

        // 先将主按钮添加到包装器（所有按钮都需要）
        btnWrapper.appendChild(btn);

        // 麦克风静音按钮（仅非手机模式下的麦克风按钮）
        if (config.id === 'mic' && config.hasPopup && config.separatePopupTrigger && !window.isMobileWidth()) {
            this.createMicMuteButton(btnWrapper);
        }

        // 处理弹窗
        let triggerBtn = null;
        let triggerImg = null;
        if (config.hasPopup && config.separatePopupTrigger) {
            if (window.isMobileWidth() && config.id === 'mic') {
                buttonsContainer.appendChild(btnWrapper);
                this._floatingButtons[config.id] = { button: btn, imgOff, imgOn, triggerButton: null, triggerImg: null };
                return;
            }

            const popup = this.createPopup(config.id);
            triggerBtn = document.createElement('button');
            triggerBtn.type = 'button';
            triggerBtn.className = 'vrm-trigger-btn';
            triggerBtn.setAttribute('aria-label', 'Open popup');

            const iconVersion = window.APP_VERSION ? `?v=${window.APP_VERSION}` : '?v=1.0.0';
            triggerImg = document.createElement('img');
            triggerImg.src = '/static/icons/play_trigger_icon.png' + iconVersion;
            triggerImg.alt = '';
            triggerImg.className = `vrm-trigger-icon-${config.id}`;
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
                const isCurrentPopupVisible = (showToken = popup._showToken) => {
                    const currentPopup = document.getElementById(`${prefix}-popup-${config.id}`);
                    return currentPopup === popup &&
                        popup.isConnected &&
                        popup._showToken === showToken &&
                        popup.style.display === 'flex' &&
                        popup.style.opacity === '1';
                };
                const repositionPopup = () => {
                    const popupUi = window.AvatarPopupUI || null;
                    if (!popupUi || typeof popupUi.positionPopup !== 'function') return;
                    popupUi.positionPopup(popup, {
                        buttonId: config.id,
                        buttonPrefix: `${prefix}-btn-`,
                        triggerPrefix: `${prefix}-trigger-icon-`,
                        rightMargin: 20,
                        bottomMargin: 60,
                        topMargin: 8,
                        gap: 8,
                        sidePanelWidth: (config.id === 'settings' || config.id === 'agent') ? 320 : 0
                    });
                };
                const isPopupVisible = popup.style.display === 'flex' && popup.style.opacity === '1';
                if (isPopupVisible) {
                    this.showPopup(config.id, popup);
                    return;
                }

                this.showPopup(config.id, popup);
                const showToken = popup._showToken;
                await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                if (!isCurrentPopupVisible(showToken)) {
                    return;
                }

                if (config.id === 'mic') {
                    if (typeof window.renderFloatingMicList === 'function') {
                        const didRender = await window.renderFloatingMicList(popup);
                        if (didRender === false || !isCurrentPopupVisible(showToken)) {
                            return;
                        }
                        repositionPopup();
                    }
                }
                if (config.id === 'screen') {
                    const didRender = await this.renderScreenSourceList(popup);
                    if (didRender === false || !isCurrentPopupVisible(showToken)) {
                        return;
                    }
                    repositionPopup();
                }
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
                    const exclusiveData = this._floatingButtons[config.exclusive];
                    if (exclusiveData && exclusiveData.button) {
                        exclusiveData.button.style.background = 'var(--neko-btn-bg, rgba(255, 255, 255, 0.65))';
                    }
                    if (exclusiveData && exclusiveData.imgOff && exclusiveData.imgOn) {
                        exclusiveData.imgOff.style.opacity = '1';
                        exclusiveData.imgOn.style.opacity = '0';
                    }
                }
                isToggling = true;
                this.showPopup(config.id, popup);
                setTimeout(() => {
                    const newPopupVisible = popup.style.display === 'flex' &&
                        popup.style.opacity !== '0' &&
                        popup.style.opacity !== '';
                    if (newPopupVisible) {
                        btn.style.background = 'var(--neko-btn-bg-active, rgba(255, 255, 255, 0.75))';
                        if (imgOff && imgOn) {
                            imgOff.style.opacity = '0';
                            imgOn.style.opacity = '1';
                        }
                    } else {
                        btn.style.background = 'var(--neko-btn-bg, rgba(255, 255, 255, 0.65))';
                        if (imgOff && imgOn) {
                            imgOff.style.opacity = '1';
                            imgOn.style.opacity = '0';
                        }
                    }
                    isToggling = false;
                }, 200);
            });
        }

        buttonsContainer.appendChild(btnWrapper);
        this._floatingButtons[config.id] = {
            button: btn,
            imgOff,
            imgOn,
            triggerButton: (config.hasPopup && config.separatePopupTrigger && !window.isMobileWidth()) ? triggerBtn : null,
            triggerImg: (config.hasPopup && config.separatePopupTrigger && !window.isMobileWidth()) ? triggerImg : null
        };
    });

    // 处理"请她离开"事件
    // 注意：返回按钮的位置、显示、以及浮动按钮的隐藏均由 app-ui.js 统一处理，
    // 此处仅更新内部状态标志。不能在此隐藏按钮容器，否则 app-ui.js 无法读取按钮位置。
    const goodbyeHandler = () => {
        this._isInReturnState = true;
    };
    this._uiWindowHandlers.push({ event: 'live2d-goodbye-click', handler: goodbyeHandler });
    window.addEventListener('live2d-goodbye-click', goodbyeHandler);

    // 处理"请她回来"事件
    const returnHandler = () => {
        this._isInReturnState = false;
        this._snapUIPosition = true;
        if (this._returnButtonContainer) this._returnButtonContainer.style.display = 'none';

        const bc = document.getElementById('vrm-floating-buttons');
        if (!bc) { this.setupFloatingButtons(); return; }
        bc.style.removeProperty('display');
        bc.style.removeProperty('visibility');
        bc.style.removeProperty('opacity');

        if (this.interaction && typeof this.interaction.setLocked === 'function') {
            this.interaction.setLocked(false);
        }

        applyResponsiveFloatingLayout();

        if (this._vrmLockIcon) {
            this._vrmLockIcon.style.removeProperty('display');
            this._vrmLockIcon.style.removeProperty('visibility');
            this._vrmLockIcon.style.removeProperty('opacity');
            this._vrmLockIcon.style.backgroundImage = 'url(/static/icons/unlocked_icon.png)';
            this._vrmLockIcon.style.display = shouldShowLockIcon() ? 'block' : 'none';
        }
    };
    this._uiWindowHandlers.push({ event: 'vrm-return-click', handler: returnHandler });
    this._uiWindowHandlers.push({ event: 'live2d-return-click', handler: returnHandler });
    window.addEventListener('vrm-return-click', returnHandler);
    window.addEventListener('live2d-return-click', returnHandler);

    // 创建"请她回来"按钮
    const returnButtonContainer = this.createReturnButton();
    this._setupReturnButtonDrag(returnButtonContainer);
    this._addReturnButtonBreathingAnimation();

    // 创建锁图标
    document.querySelectorAll('#vrm-lock-icon').forEach(el => el.remove());
    const lockIcon = document.createElement('div');
    lockIcon.id = 'vrm-lock-icon';
    lockIcon.dataset.vrmLock = 'true';
    document.body.appendChild(lockIcon);
    this._vrmLockIcon = lockIcon;

    Object.assign(lockIcon.style, {
        position: 'fixed', zIndex: '99999', width: '32px', height: '32px',
        cursor: 'pointer', display: 'none',
        backgroundImage: 'url(/static/icons/unlocked_icon.png)',
        backgroundSize: 'contain', backgroundRepeat: 'no-repeat', backgroundPosition: 'center',
        pointerEvents: 'auto', transition: 'transform 0.1s'
    });

    const toggleLock = (e) => {
        if (e) { e.preventDefault(); e.stopPropagation(); }
        const currentLocked = this.interaction && this.interaction.checkLocked ? this.interaction.checkLocked() : false;
        const newLocked = !currentLocked;
        if (this.interaction && typeof this.interaction.setLocked === 'function') {
            this.interaction.setLocked(newLocked);
        }
        const isLocked = this.interaction && this.interaction.checkLocked ? this.interaction.checkLocked() : false;
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

    // 启动 UI 更新循环
    this._startUIUpdateLoop();

    // 初始化后显示按钮
    setTimeout(() => {
        applyResponsiveFloatingLayout();
        if (this._vrmLockIcon) this._vrmLockIcon.style.display = shouldShowLockIcon() ? 'block' : 'none';
    }, 100);

    this._syncButtonStatesWithGlobalState();

    // 通知外部浮动按钮已就绪
    window.dispatchEvent(new CustomEvent('live2d-floating-buttons-ready'));
};

/**
 * VRM UI 更新循环
 */
VRMManager.prototype._startUIUpdateLoop = function() {
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

        if (!this.currentModel || !this.currentModel.scene) {
            if (this._uiUpdateLoopId !== null && this._uiUpdateLoopId !== undefined) {
                this._uiUpdateLoopId = requestAnimationFrame(update);
            }
            return;
        }

        if (this._isInReturnState) {
            if (this._uiUpdateLoopId !== null && this._uiUpdateLoopId !== undefined) {
                this._uiUpdateLoopId = requestAnimationFrame(update);
            }
            return;
        }

        if (window.isMobileWidth && window.isMobileWidth()) {
            const now = performance.now();
            if (now - lastMobileUpdate < MOBILE_UPDATE_INTERVAL) {
                if (this._uiUpdateLoopId !== null && this._uiUpdateLoopId !== undefined) {
                    this._uiUpdateLoopId = requestAnimationFrame(update);
                }
                return;
            }
            lastMobileUpdate = now;
        }

        const buttonsContainer = document.getElementById('vrm-floating-buttons');
        const lockIcon = this._vrmLockIcon;

        if (!this.camera || !this.renderer) {
            if (this._uiUpdateLoopId !== null && this._uiUpdateLoopId !== undefined) {
                this._uiUpdateLoopId = requestAnimationFrame(update);
            }
            return;
        }

        try {
            const camera = this.camera;
            const renderer = this.renderer;
            const canvasRect = renderer.domElement.getBoundingClientRect();
            const canvasWidth = canvasRect.width;
            const canvasHeight = canvasRect.height;

            box.setFromObject(this.currentModel.scene);

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
            this._vrmModelCenterX = modelCenterX;
            this._vrmModelCenterY = modelCenterY;
            this._vrmModelScreenHeight = modelScreenHeight;

            const mouse = this._vrmMousePos;
            const mouseStale = !this._vrmMousePosTs || (Date.now() - this._vrmMousePosTs > 1500);
            const mouseDist = (mouse && !mouseStale) ? Math.hypot(mouse.x - modelCenterX, mouse.y - modelCenterY) : Infinity;
            const baseThreshold = Math.max(90, Math.min(260, modelScreenHeight * 0.55));

            const padX = Math.max(60, (visibleRight - visibleLeft) * 0.3);
            const padY = Math.max(40, (visibleBottom - visibleTop) * 0.2);
            const mouseInModelRegion = mouse && !mouseStale &&
                mouse.x >= canvasRect.left + visibleLeft - padX &&
                mouse.x <= canvasRect.left + visibleRight + padX &&
                mouse.y >= canvasRect.top + visibleTop - padY &&
                mouse.y <= canvasRect.top + visibleBottom + padY;

            this._vrmMouseInModelRegion = !!mouseInModelRegion;

            const showThreshold = baseThreshold;
            const hideThreshold = baseThreshold * 1.2;
            if (this._vrmUiNearModel !== true && (mouseDist <= showThreshold || mouseInModelRegion)) {
                this._vrmUiNearModel = true;
            } else if (this._vrmUiNearModel !== false && mouseDist >= hideThreshold && !mouseInModelRegion) {
                this._vrmUiNearModel = false;
            } else if (typeof this._vrmUiNearModel !== 'boolean') {
                this._vrmUiNearModel = false;
            }

            const visibleCount = getVisibleButtonCount();
            const baseToolbarHeight = baseButtonSize * visibleCount + baseGap * (visibleCount - 1);
            const targetToolbarHeight = modelScreenHeight / 2;
            const scale = Math.max(0.5, Math.min(1.0, targetToolbarHeight / baseToolbarHeight));

            if (buttonsContainer) {
                const isMobile = window.isMobileWidth && window.isMobileWidth();
                if (isMobile) {
                    buttonsContainer.style.transformOrigin = 'right bottom';
                    buttonsContainer.style.display = this.interaction && this.interaction.checkLocked && this.interaction.checkLocked() ? 'none' : 'flex';
                } else {
                    buttonsContainer.style.transformOrigin = 'left top';
                    const isLocked = this.interaction && this.interaction.checkLocked ? this.interaction.checkLocked() : false;
                    const hoveringButtons = this._vrmButtonsHovered === true;
                    const popupUi = window.AvatarPopupUI || null;
                    const hasOpenOverlay = popupUi && typeof popupUi.hasVisibleOverlay === 'function'
                        ? popupUi.hasVisibleOverlay('vrm')
                        : Array.from(document.querySelectorAll('[id^="vrm-popup-"]'))
                            .some(popup => popup.style.display === 'flex' && popup.style.opacity !== '0');
                    const inTutorial = buttonsContainer.dataset.inTutorial === 'true' || window.isInTutorial === true;
                    const shouldShowButtons = inTutorial || (!isLocked && (this._vrmUiNearModel || hoveringButtons || hasOpenOverlay));
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

                    const rawLeft = parseFloat(buttonsContainer.style.left);
                    if (this._snapUIPosition || Number.isNaN(rawLeft)) {
                        if (canvasWidth > 0 && canvasHeight > 0) {
                            buttonsContainer.style.left = `${boundedX}px`;
                            buttonsContainer.style.top = `${boundedY}px`;
                            this._snapUIPosition = false;
                        }
                    } else {
                        const currentTop = parseFloat(buttonsContainer.style.top) || boundedY;
                        const dist = Math.sqrt(Math.pow(boundedX - rawLeft, 2) + Math.pow(boundedY - currentTop, 2));
                        if (dist > 0.5) {
                            const lerpFactor = 0.15;
                            buttonsContainer.style.left = `${rawLeft + (boundedX - rawLeft) * lerpFactor}px`;
                            buttonsContainer.style.top = `${currentTop + (boundedY - currentTop) * lerpFactor}px`;
                        }
                    }

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

                        const rawLockLeft = parseFloat(lockIcon.style.left);
                        if (Number.isNaN(rawLockLeft)) {
                            if (canvasWidth > 0 && canvasHeight > 0) {
                                lockIcon.style.left = `${boundedLockX}px`;
                                lockIcon.style.top = `${boundedLockY}px`;
                            }
                        } else {
                            const currentLockTop = parseFloat(lockIcon.style.top) || boundedLockY;
                            const lockDist = Math.sqrt(Math.pow(boundedLockX - rawLockLeft, 2) + Math.pow(boundedLockY - currentLockTop, 2));
                            if (lockDist > 0.5) {
                                const lerpFactor = 0.15;
                                lockIcon.style.left = `${rawLockLeft + (boundedLockX - rawLockLeft) * lerpFactor}px`;
                                lockIcon.style.top = `${currentLockTop + (boundedLockY - currentLockTop) * lerpFactor}px`;
                            }
                        }
                        lockIcon.style.display = (this._shouldShowVrmLockIcon && this._shouldShowVrmLockIcon()) ? 'block' : 'none';

                        const lockRect = lockIcon.getBoundingClientRect();
                        let isLockOverlapped = false;
                        document.querySelectorAll('[id^="vrm-popup-"]').forEach(popup => {
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
            if (window.DEBUG_MODE) console.debug('[VRM UI] 更新循环单帧异常:', error);
        }

        if (this._uiUpdateLoopId !== null && this._uiUpdateLoopId !== undefined) {
            this._uiUpdateLoopId = requestAnimationFrame(update);
        }
    };

    this._uiUpdateLoopId = requestAnimationFrame(update);
};

// 为VRM的"请她回来"按钮设置拖动功能
// 性能优化：使用 RAF 批处理 + transform 走 GPU 合成，避免每帧 layout 抖动
VRMManager.prototype._setupReturnButtonDrag = function (returnButtonContainer) {
    // 清理之前的 document 级别事件监听器，防止重复调用时泄漏
    if (this._returnButtonDragHandlers) {
        document.removeEventListener('mousemove', this._returnButtonDragHandlers.mouseMove);
        document.removeEventListener('mouseup', this._returnButtonDragHandlers.mouseUp);
        document.removeEventListener('touchmove', this._returnButtonDragHandlers.touchMove);
        document.removeEventListener('touchend', this._returnButtonDragHandlers.touchEnd);
        this._returnButtonDragHandlers = null;
    }

    let isDragging = false;
    let dragStartX = 0;
    let dragStartY = 0;
    let containerStartX = 0;
    let containerStartY = 0;
    let cachedContainerWidth = 64;
    let cachedContainerHeight = 64;
    let dragRAFId = null;
    let pendingClientX = 0;
    let pendingClientY = 0;

    const handleStart = (clientX, clientY) => {
        isDragging = true;
        dragStartX = clientX;
        dragStartY = clientY;
        // 同步初始化 pending 坐标，防止 click-without-move 时
        // commitDragPosition() 使用过期值产生错误位移
        pendingClientX = clientX;
        pendingClientY = clientY;

        // 设置全局拖拽标志，供 preload 等跳过昂贵操作
        if (window.DragHelpers) window.DragHelpers.isDragging = true;

        // 获取当前容器的实际位置（考虑居中定位）
        const rect = returnButtonContainer.getBoundingClientRect();
        containerStartX = rect.left;
        containerStartY = rect.top;

        // 在拖拽开始时缓存尺寸，避免每帧读取触发 layout
        cachedContainerWidth = rect.width || 64;
        cachedContainerHeight = rect.height || 64;

        // 清除 transform，改用像素定位
        returnButtonContainer.style.transform = '';
        returnButtonContainer.style.left = `${containerStartX}px`;
        returnButtonContainer.style.top = `${containerStartY}px`;

        returnButtonContainer.setAttribute('data-dragging', 'false');
        returnButtonContainer.style.cursor = 'grabbing';

        // 禁用昂贵的视觉效果：backdrop-filter（每帧重算高斯模糊）、transition（与 RAF 打架）、animation（呼吸灯重绘）
        const returnBtn = returnButtonContainer.querySelector('#vrm-btn-return');
        if (returnBtn) {
            returnBtn.style.transition = 'none';
            returnBtn.style.backdropFilter = 'none';
            returnBtn.style.webkitBackdropFilter = 'none';
            returnBtn.style.animation = 'none';
        }
    };

    // 计算并应用拖拽位置（在 RAF 回调中执行，使用 transform 走 GPU 合成）
    const applyDragPosition = () => {
        dragRAFId = null;
        const deltaX = pendingClientX - dragStartX;
        const deltaY = pendingClientY - dragStartY;
        if (Math.abs(deltaX) > 5 || Math.abs(deltaY) > 5) {
            returnButtonContainer.setAttribute('data-dragging', 'true');
        }
        const newX = Math.max(0, Math.min(containerStartX + deltaX, window.innerWidth - cachedContainerWidth));
        const newY = Math.max(0, Math.min(containerStartY + deltaY, window.innerHeight - cachedContainerHeight));

        // 使用 transform 移动，仅走 GPU 合成，跳过 layout + paint
        const tx = newX - containerStartX;
        const ty = newY - containerStartY;
        returnButtonContainer.style.transform = `translate(${tx}px, ${ty}px)`;
    };

    // 将 transform 位移落实到 left/top，并清除 transform
    const commitDragPosition = () => {
        const deltaX = pendingClientX - dragStartX;
        const deltaY = pendingClientY - dragStartY;
        if (Math.abs(deltaX) > 5 || Math.abs(deltaY) > 5) {
            returnButtonContainer.setAttribute('data-dragging', 'true');
        }
        const newX = Math.max(0, Math.min(containerStartX + deltaX, window.innerWidth - cachedContainerWidth));
        const newY = Math.max(0, Math.min(containerStartY + deltaY, window.innerHeight - cachedContainerHeight));
        returnButtonContainer.style.transform = '';
        returnButtonContainer.style.left = `${newX}px`;
        returnButtonContainer.style.top = `${newY}px`;
    };

    // 仅记录坐标，通过 RAF 合并更新
    const handleMove = (clientX, clientY) => {
        if (!isDragging) return;
        pendingClientX = clientX;
        pendingClientY = clientY;
        if (!dragRAFId) {
            dragRAFId = requestAnimationFrame(applyDragPosition);
        }
    };

    const handleEnd = () => {
        if (isDragging) {
            // 取消待执行的 RAF，将 transform 落实到 left/top
            if (dragRAFId) {
                cancelAnimationFrame(dragRAFId);
                dragRAFId = null;
            }
            commitDragPosition();

            setTimeout(() => returnButtonContainer.setAttribute('data-dragging', 'false'), 10);
            isDragging = false;
            returnButtonContainer.style.cursor = 'grab';

            // 清除全局拖拽标志
            if (window.DragHelpers) window.DragHelpers.isDragging = false;

            // 恢复拖拽期间禁用的视觉效果
            const returnBtn = returnButtonContainer.querySelector('#vrm-btn-return');
            if (returnBtn) {
                returnBtn.style.transition = '';
                returnBtn.style.backdropFilter = '';
                returnBtn.style.webkitBackdropFilter = '';
                returnBtn.style.animation = '';
            }
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
    const opts = this._avatarButtonOptions;
    if (document.getElementById(opts.returnBreathingStyleId)) {
        return;
    }

    const style = document.createElement('style');
    style.id = opts.returnBreathingStyleId;
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
            will-change: box-shadow;
        }
        
        #vrm-btn-return:hover {
            animation: none;
        }
    `;
    document.head.appendChild(style);
};

/**
 * 将屏幕像素偏移量应用到 VRM 模型的世界坐标
 * 用于"请她回来"按钮被拖拽后，模型跟随出现在新位置
 */
VRMManager.prototype.applyScreenDelta = function(screenDx, screenDy) {
    const scene = this.currentModel && this.currentModel.scene;
    if (!scene || !this.camera || !this.renderer) return;

    const camera = this.camera;

    // canvas 在 goodbye 状态下被 display:none 隐藏，getBoundingClientRect 全为 0
    const canvasRect = this.renderer.domElement.getBoundingClientRect();
    const viewWidth = canvasRect.width > 0 ? canvasRect.width : window.innerWidth;
    const viewHeight = canvasRect.height > 0 ? canvasRect.height : window.innerHeight;
    if (viewWidth <= 0 || viewHeight <= 0) return;

    const cameraDistance = camera.position.distanceTo(scene.position);
    if (cameraDistance < 0.001) return;

    const fov = camera.fov * (Math.PI / 180);
    const worldHeight = 2 * Math.tan(fov / 2) * cameraDistance;
    const worldWidth = worldHeight * camera.aspect;

    const pixelToWorldX = worldWidth / viewWidth;
    const pixelToWorldY = worldHeight / viewHeight;

    const right = new window.THREE.Vector3(1, 0, 0).applyQuaternion(camera.quaternion);
    const up = new window.THREE.Vector3(0, 1, 0).applyQuaternion(camera.quaternion);

    scene.position.add(right.clone().multiplyScalar(screenDx * pixelToWorldX));
    scene.position.add(up.clone().multiplyScalar(-screenDy * pixelToWorldY));

    if (this.interaction && typeof this.interaction.clampModelPosition === 'function') {
        const clamped = this.interaction.clampModelPosition(scene.position.clone());
        if (clamped && clamped.isVector3) scene.position.copy(clamped);
    }
};
