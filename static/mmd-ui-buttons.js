/**
 * MMD UI Buttons - 浮动按钮系统（精简版）
 * 使用 AvatarButtonMixin 的 MMD 特定实现
 */

// 应用 mixin 到 MMD Manager
AvatarButtonMixin.apply(MMDManager.prototype, 'mmd', {
    containerElementId: 'mmd-floating-buttons',
    returnContainerId: 'mmd-return-button-container',
    returnBtnId: 'mmd-btn-return',
    lockIconId: 'mmd-lock-icon',
    popupPrefix: 'mmd',
    buttonClassPrefix: 'mmd-floating-btn',
    triggerBtnClass: 'mmd-trigger-btn',
    triggerIconClass: 'mmd-trigger-icon',
    returnBtnClass: 'mmd-return-btn',
    returnBreathingStyleId: 'mmd-return-button-breathing-styles'
});

/**
 * 设置浮动按钮系统（MMD 特定）
 */
MMDManager.prototype.setupFloatingButtons = function() {
    if (window.location.pathname.includes('model_manager')) return;

    // 基础框架初始化
    const buttonsContainer = this.setupFloatingButtonsBase();

    const opts = this._avatarButtonOptions;

    buttonsContainer.addEventListener('mouseenter', () => { this._mmdButtonsHovered = true; });
    buttonsContainer.addEventListener('mouseleave', () => { this._mmdButtonsHovered = false; });

    // MMD 特定的响应式布局处理
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
        }
    };
    applyResponsiveFloatingLayout();

    // 锁图标显示逻辑
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

    // 鼠标位置跟踪
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

    // 获取按钮配置
    const buttonConfigs = this.getDefaultButtonConfigs();
    this._buttonConfigs = buttonConfigs;
    this._floatingButtons = this._floatingButtons || {};

    // 创建按钮
    buttonConfigs.forEach(config => {
        if (window.isMobileWidth && window.isMobileWidth() && (config.id === 'agent' || config.id === 'goodbye')) return;

        const { btnWrapper, btn, imgOff, imgOn } = this.createButtonElement(config, buttonsContainer);

        // 点击事件处理
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            e.preventDefault();

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

        btnWrapper.appendChild(btn);

        // 麦克风静音按钮（仅非手机模式下的麦克风按钮）
        if (config.id === 'mic' && config.hasPopup && config.separatePopupTrigger && !(window.isMobileWidth && window.isMobileWidth())) {
            this.createMicMuteButton(btnWrapper);
        }

        // 处理弹窗
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

            const iconVersion = window.APP_VERSION ? `?v=${window.APP_VERSION}` : '?v=1.0.0';
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
        this._floatingButtons[config.id] = { button: btn, imgOff, imgOn };
    });

    // 处理"请她离开"事件
    // 注意：返回按钮的位置、显示、以及浮动按钮的隐藏均由 app-ui.js 统一处理，
    // 此处仅更新内部状态标志。不能在此隐藏按钮容器，否则 app-ui.js 无法读取按钮位置。
    const goodbyeHandler = () => {
        this._isInReturnState = true;
        if (this._physicsRestoreTimer) {
            clearTimeout(this._physicsRestoreTimer);
            this._physicsRestoreTimer = null;
        }
    };
    this._uiWindowHandlers.push({ event: 'live2d-goodbye-click', handler: goodbyeHandler });
    window.addEventListener('live2d-goodbye-click', goodbyeHandler);

    // 处理"请她回来"事件
    const returnHandler = () => {
        this._isInReturnState = false;
        this._snapUIPosition = true;
        if (this._returnButtonContainer) this._returnButtonContainer.style.display = 'none';

        // 回来时先禁用物理、重置姿态，等渐入动画结束再恢复
        const hadPhysics = this.enablePhysics;
        this.enablePhysics = false;
        if (this.currentModel && this.currentModel.physics && typeof this.currentModel.physics.reset === 'function') {
            this.currentModel.physics.reset();
        }

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

        if (hadPhysics) {
            if (this._physicsRestoreTimer) clearTimeout(this._physicsRestoreTimer);
            this._physicsRestoreTimer = setTimeout(() => {
                this._physicsRestoreTimer = null;
                if (this._isInReturnState) return;
                if (this.currentModel && this.currentModel.physics && typeof this.currentModel.physics.reset === 'function') {
                    this.currentModel.physics.reset();
                }
                this.enablePhysics = true;
            }, 800);
        }
    };
    this._uiWindowHandlers.push({ event: 'mmd-return-click', handler: returnHandler });
    this._uiWindowHandlers.push({ event: 'live2d-return-click', handler: returnHandler });
    window.addEventListener('mmd-return-click', returnHandler);
    window.addEventListener('live2d-return-click', returnHandler);

    // 创建"请她回来"按钮
    const returnButtonContainer = this.createReturnButton();
    this._setupReturnButtonDrag(returnButtonContainer);
    this._addReturnButtonBreathingAnimation();

    // 创建锁图标
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

    // 启动 UI 更新循环
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

/**
 * MMD UI 更新循环
 */
MMDManager.prototype._startUIUpdateLoop = function() {
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

            const visibleCount = getVisibleButtonCount();
            const baseToolbarHeight = baseButtonSize * visibleCount + baseGap * (visibleCount - 1);
            const targetToolbarHeight = modelScreenHeight / 2;
            const scale = Math.max(0.5, Math.min(1.0, targetToolbarHeight / baseToolbarHeight));

            if (buttonsContainer) {
                const isMobile = window.isMobileWidth && window.isMobileWidth();
                if (isMobile) {
                    buttonsContainer.style.transformOrigin = 'right bottom';
                    buttonsContainer.style.display = this.isLocked ? 'none' : 'flex';
                } else {
                    buttonsContainer.style.transformOrigin = 'left top';
                    const isLocked = this.isLocked;
                    const hoveringButtons = this._mmdButtonsHovered === true;
                    const popupUi = window.AvatarPopupUI || null;
                    const isFallbackOverlayVisible = (element) => {
                        if (!element) return false;
                        const style = window.getComputedStyle(element);
                        const opacity = Number.parseFloat(style.opacity || '1');
                        if (style.display === 'none' || style.visibility === 'hidden' || opacity <= 0) return false;

                        const rect = element.getBoundingClientRect();
                        if (rect.width <= 0 || rect.height <= 0) return false;

                        return rect.bottom > 0 &&
                            rect.right > 0 &&
                            rect.top < window.innerHeight &&
                            rect.left < window.innerWidth;
                    };
                    const hasOpenOverlay = popupUi && typeof popupUi.hasVisibleOverlay === 'function'
                        ? popupUi.hasVisibleOverlay('mmd')
                        : Array.from(document.querySelectorAll('[id^="mmd-popup-"], [data-neko-sidepanel-owner^="mmd-popup-"]'))
                            .some(isFallbackOverlayVisible);
                    const shouldShowButtons = !isLocked && (this._mmdUiNearModel || hoveringButtons || hasOpenOverlay);
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
                        lockIcon.style.display = (this._shouldShowMmdLockIcon && this._shouldShowMmdLockIcon()) ? 'block' : 'none';

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

/**
 * 将屏幕像素偏移量应用到 MMD 模型的世界坐标
 * 用于"请她回来"按钮被拖拽后，模型跟随出现在新位置
 */
MMDManager.prototype.applyScreenDelta = function(screenDx, screenDy) {
    const mesh = this.currentModel && this.currentModel.mesh;
    if (!mesh || !this.camera || !this.renderer) return;

    const camera = this.camera;

    // canvas 在 goodbye 状态下被 display:none 隐藏，getBoundingClientRect 全为 0
    const canvasRect = this.renderer.domElement.getBoundingClientRect();
    const viewWidth = canvasRect.width > 0 ? canvasRect.width : window.innerWidth;
    const viewHeight = canvasRect.height > 0 ? canvasRect.height : window.innerHeight;
    if (viewWidth <= 0 || viewHeight <= 0) return;

    const cameraDistance = camera.position.distanceTo(mesh.position);
    if (cameraDistance < 0.001) return;

    const fov = camera.fov * (Math.PI / 180);
    const worldHeight = 2 * Math.tan(fov / 2) * cameraDistance;
    const worldWidth = worldHeight * camera.aspect;

    const pixelToWorldX = worldWidth / viewWidth;
    const pixelToWorldY = worldHeight / viewHeight;

    const right = new window.THREE.Vector3(1, 0, 0).applyQuaternion(camera.quaternion);
    const up = new window.THREE.Vector3(0, 1, 0).applyQuaternion(camera.quaternion);

    mesh.position.add(right.clone().multiplyScalar(screenDx * pixelToWorldX));
    mesh.position.add(up.clone().multiplyScalar(-screenDy * pixelToWorldY));
};
