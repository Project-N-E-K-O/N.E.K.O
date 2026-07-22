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

const LIVE2D_X11_UI_TICK_MS = 80;
const LIVE2D_FLOATING_BUTTON_SIZE = 48;
const LIVE2D_FLOATING_BUTTON_GAP = 12;
const LIVE2D_FLOATING_BUTTON_COUNT = 5;
const LIVE2D_BASE_TOOLBAR_HEIGHT =
    LIVE2D_FLOATING_BUTTON_SIZE * LIVE2D_FLOATING_BUTTON_COUNT +
    LIVE2D_FLOATING_BUTTON_GAP * (LIVE2D_FLOATING_BUTTON_COUNT - 1);

function getLive2DFloatingControlScale(modelHeight, baseToolbarHeight) {
    const minScale = 0.5;
    const maxScale = 1.0;
    const rawScale = modelHeight / 2 / baseToolbarHeight;
    return Math.round(Math.max(minScale, Math.min(maxScale, rawScale)) * 1000) / 1000;
}

function shouldThrottleLive2DUiTicker(manager) {
    return !!(window.__NEKO_DESKTOP_RUNTIME__ && window.__NEKO_DESKTOP_RUNTIME__.isLinuxX11) &&
        !(manager && manager._isDraggingModel);
}

function shouldSkipLive2DUiTick(manager, propName, intervalMs) {
    if (!shouldThrottleLive2DUiTicker(manager)) return false;
    const now = performance.now();
    if (now - (manager[propName] || 0) < intervalMs) return true;
    manager[propName] = now;
    return false;
}

function isYuiGuideLive2DPreparing() {
    return window.nekoYuiGuideLive2dPreparing === true
        || (
            window.isInTutorial === true
            && document.body
            && document.body.classList
            && document.body.classList.contains('yui-guide-live2d-preparing')
        );
}

function isYuiGuideFloatingToolbarSuppressed() {
    return !!(
        window.isNekoYuiGuideFloatingToolbarSuppressed
        && window.isNekoYuiGuideFloatingToolbarSuppressed()
    );
}

function hideYuiGuideLive2DPreparingButtonStyles(buttonsContainer) {
    if (!buttonsContainer || !buttonsContainer.style || typeof buttonsContainer.style.removeProperty !== 'function') {
        return;
    }
    buttonsContainer.dataset.yuiGuideForcedHidden = 'true';
    buttonsContainer.style.setProperty('display', 'none', 'important');
    buttonsContainer.style.setProperty('visibility', 'hidden', 'important');
    buttonsContainer.style.setProperty('opacity', '0', 'important');
    buttonsContainer.style.setProperty('pointer-events', 'none', 'important');
}

function restoreYuiGuideLive2DPreparingButtonStyles(buttonsContainer) {
    if (!buttonsContainer || !buttonsContainer.style || typeof buttonsContainer.style.removeProperty !== 'function') {
        return;
    }
    const forcedHidden = buttonsContainer.dataset.yuiGuideForcedHidden === 'true';
    if (!forcedHidden) {
        return;
    }
    const forcedProperties = ['display', 'visibility', 'opacity', 'pointer-events'];
    forcedProperties.forEach((property) => {
        buttonsContainer.style.removeProperty(property);
    });
    delete buttonsContainer.dataset.yuiGuideForcedHidden;
}

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
    // 新建元素没有 left/top 内联样式：必须失效位置/透明度脏检查缓存，
    // 否则模型位置与上次相同时首帧写入被跳过，图标停在 fixed 默认位置
    this._lockIconLastLeft = undefined;
    this._lockIconLastTop = undefined;
    this._lockIconLastTransform = undefined;
    this._lockIconLastOpacity = undefined;
    this._lockIconLastOverlapScanAt = 0;
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
            if (shouldSkipLive2DUiTick(this, '_x11LockIconLastTickAt', LIVE2D_X11_UI_TICK_MS)) {
                return;
            }
            if (isYuiGuideFloatingToolbarSuppressed()) {
                lockIcon.dataset.yuiGuideForcedHidden = 'true';
                lockIcon.style.visibility = 'hidden';
                lockIcon.style.opacity = '0';
                return;
            }
            if (lockIcon.dataset.yuiGuideForcedHidden === 'true') {
                delete lockIcon.dataset.yuiGuideForcedHidden;
                lockIcon.style.visibility = '';
                lockIcon.style.opacity = '';
                // 旁路写入后失效 opacity 脏检查缓存，避免后续 tick 误跳过写回
                this._lockIconLastOpacity = undefined;
            }
            if (!model || !model.parent) {
                // 教程期间不隐藏锁图标，防止高亮框位置被刷到 (0,0)
                if (lockIcon && !window.isInTutorial) lockIcon.style.display = 'none';
                return;
            }
            const bounds = model.getBounds();
            const screenWidth = window.innerWidth;
            const screenHeight = window.innerHeight;

            const modelHeight = bounds.bottom - bounds.top;
            const scale = isMobileWidth()
                ? 1
                : getLive2DFloatingControlScale(modelHeight, LIVE2D_BASE_TOOLBAR_HEIGHT);
            const nextTransform = `scale(${scale})`;
            if (nextTransform !== this._lockIconLastTransform) {
                this._lockIconLastTransform = nextTransform;
                lockIcon.style.transformOrigin = 'left top';
                lockIcon.style.transform = nextTransform;
            }

            const baseLockIconSize = 32;
            const actualLockIconSize = baseLockIconSize * scale;
            const viewportEdgePadding = 8;

            const targetX = bounds.right * 0.7 + bounds.left * 0.3;
            const targetY = bounds.top * 0.3 + bounds.bottom * 0.7;

            const defaultMaxLockTop = screenHeight - actualLockIconSize - viewportEdgePadding;
            const maxLockTop = typeof window.getNekoYuiGuideLockIconMaxTop === 'function'
                ? window.getNekoYuiGuideLockIconMaxTop(defaultMaxLockTop, actualLockIconSize)
                : defaultMaxLockTop;
            const maxLockLeft = screenWidth - actualLockIconSize - viewportEdgePadding;
            const clampedLeft = Math.round(Math.max(0, Math.min(targetX, maxLockLeft)));
            const clampedTop = Math.round(Math.max(0, Math.min(targetY, maxLockTop)));
            // 位置无变化时跳过 style 写入，避免每帧无谓的样式失效
            if (clampedLeft !== this._lockIconLastLeft || clampedTop !== this._lockIconLastTop) {
                this._lockIconLastLeft = clampedLeft;
                this._lockIconLastTop = clampedTop;
                lockIcon.style.left = `${clampedLeft}px`;
                lockIcon.style.top = `${clampedTop}px`;
            }

            // 重叠检测节流到 250ms：纯装饰性淡出（opacity 0.3），不需要逐帧
            // 扫两遍 DOM。矩形用已知坐标 + 缩放后的实际尺寸推算，避免每帧
            // getBoundingClientRect 强制布局。
            const nowTs = performance.now();
            let isOverlapped = this._lockIconLastOverlapped === true;
            if (!this._lockIconLastOverlapScanAt || nowTs - this._lockIconLastOverlapScanAt >= 250) {
                this._lockIconLastOverlapScanAt = nowTs;
                const lockRect = {
                    left: clampedLeft,
                    top: clampedTop,
                    right: clampedLeft + actualLockIconSize,
                    bottom: clampedTop + actualLockIconSize
                };
                isOverlapped = false;
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
                this._lockIconLastOverlapped = isOverlapped;
            }
            // 与角色形象半透明状态完全同步：容器加了 locked-hover-fade 类(opacity 0.12)时，锁图标也淡到同一透明度
            const live2dFadeContainer = document.getElementById('live2d-container');
            const lockShouldFade = live2dFadeContainer && live2dFadeContainer.classList.contains('locked-hover-fade');
            const nextOpacity = lockShouldFade ? '0.12' : (isOverlapped ? '0.3' : '');
            if (nextOpacity !== this._lockIconLastOpacity) {
                this._lockIconLastOpacity = nextOpacity;
                lockIcon.style.opacity = nextOpacity;
            }
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

    // 防御性检查：当前模型类型不是 Live2D 时不创建按钮（防止过时的异步回调）
    var cfgType = (window.lanlan_config && window.lanlan_config.model_type || '').toLowerCase();
    if (cfgType && cfgType !== 'live2d') return;

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
    // 容器可能被重建（无 left/top/transform 内联样式）：失效脏检查缓存，
    // 否则模型位置与上次相同时首帧写入被跳过，工具栏停在默认位置
    this._floatingButtonsLastTransform = undefined;
    this._floatingButtonsLastLeft = undefined;
    this._floatingButtonsLastTop = undefined;

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
        let triggerBtn = null;
        let triggerImg = null;
        if (config.hasPopup && config.separatePopupTrigger) {
            if (isMobileWidth() && config.id === 'mic') {
                buttonsContainer.appendChild(btnWrapper);
                this._floatingButtons[config.id] = {
                    button: btn,
                    wrapper: btnWrapper,
                    imgOff: imgOff,
                    imgOn: imgOn,
                    triggerButton: null,
                    triggerImg: null
                };
                return;
            }

            if (!isMobileWidth()) {
                const popup = this.createPopup(config.id);

                triggerBtn = document.createElement('div');
                triggerBtn.className = 'live2d-trigger-btn';
                triggerImg = document.createElement('img');
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

                const isPopupVisible = () => popup.style.display === 'flex' && popup.style.opacity === '1';
                const repositionPopup = () => {
                    if (!isPopupVisible()) return;
                    const popupUi = window.AvatarPopupUI || null;
                    if (!popupUi || typeof popupUi.positionPopup !== 'function') return;
                    void popup.offsetHeight;
                    const pos = popupUi.positionPopup(popup, {
                        buttonId: config.id,
                        buttonPrefix: 'live2d-btn-',
                        triggerPrefix: 'live2d-trigger-icon-',
                        rightMargin: 20,
                        bottomMargin: 60,
                        topMargin: 8,
                        gap: 8,
                        sidePanelWidth: (config.id === 'settings' || config.id === 'agent') ? 320 : 0
                    });
                    popup.dataset.opensLeft = String(!!(pos && pos.opensLeft));
                };

                triggerBtn.addEventListener('click', async (e) => {
                    console.log(`[Live2D] 小三角被点击: ${config.id}`);
                    e.stopPropagation();

                    if (isPopupVisible()) {
                        this.showPopup(config.id, popup);
                        return;
                    }

                    this.showPopup(config.id, popup);
                    const expectedShowToken = typeof popup._showToken === 'number' ? popup._showToken : null;
                    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));

                    const isSameOpenInstance = expectedShowToken === null || popup._showToken === expectedShowToken;
                    const isStillOpen = popup.style.display === 'flex' || popup.style.opacity === '1';
                    if (!isSameOpenInstance || !isStillOpen) {
                        return;
                    }

                    if (config.id === 'mic' && window.renderFloatingMicList) {
                        await window.renderFloatingMicList();
                        repositionPopup();
                    }

                    if (config.id === 'screen' && window.renderFloatingScreenSourceList) {
                        await window.renderFloatingScreenSourceList();
                        repositionPopup();
                    }
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
            imgOn: imgOn,
            triggerButton: (config.hasPopup && config.separatePopupTrigger && !isMobileWidth()) ? triggerBtn : null,
            triggerImg: (config.hasPopup && config.separatePopupTrigger && !isMobileWidth()) ? triggerImg : null
        };
        console.log(`[Live2D] 按钮已创建: ${config.id}, hasPopup: ${config.hasPopup}, toggle: ${config.toggle}`);
    });

    console.log('[Live2D] 所有浮动按钮已创建完成');

    // 创建"请她回来"按钮
    const returnButtonContainer = this.createReturnButton();

    container.style.pointerEvents = this.isLocked ? 'none' : 'auto';

    const tick = () => {
        try {
            if (shouldSkipLive2DUiTick(this, '_x11FloatingButtonsLastTickAt', LIVE2D_X11_UI_TICK_MS)) {
                return;
            }
            if (isYuiGuideFloatingToolbarSuppressed()) {
                hideYuiGuideLive2DPreparingButtonStyles(buttonsContainer);
                return;
            }
            if (!model || !model.parent) {
                return;
            }
            if (isMobileWidth()) {
                return;
            }
            if (this._freezeFloatingButtonsPosition === true) {
                return;
            }
            const bounds = model.getBounds();
            const screenWidth = window.innerWidth;
            const screenHeight = window.innerHeight;

            const modelCenterX = (bounds.left + bounds.right) / 2;
            const modelCenterY = (bounds.top + bounds.bottom) / 2;

            const modelHeight = bounds.bottom - bounds.top;
            // scale 量化到千分位，避免呼吸抖动击穿 transform 脏检查
            const scale = getLive2DFloatingControlScale(modelHeight, LIVE2D_BASE_TOOLBAR_HEIGHT);
            const rotation = Number(this._floatingButtonsRotationRadians) || 0;
            const rotateTransform = rotation ? ` rotate(${rotation}rad)` : '';

            const nextTransform = `scale(${scale})${rotateTransform}`;

            const targetX = bounds.right * 0.8 + bounds.left * 0.2;

            const actualToolbarHeight = LIVE2D_BASE_TOOLBAR_HEIGHT * scale;
            const actualToolbarWidth = 80 * scale;

            const targetY = modelCenterY - actualToolbarHeight / 2;

            const minY = 20;
            const maxY = screenHeight - actualToolbarHeight - 20;
            const boundedY = Math.round(Math.max(minY, Math.min(targetY, maxY)));

            const maxX = screenWidth - actualToolbarWidth;
            // 量化到整像素：呼吸动画让 bounds 亚像素级抖动，不量化的话脏检查
            // 每帧都会被无意义的浮点尾差击穿（工具栏定位不需要亚像素精度）
            const boundedX = Math.round(Math.max(0, Math.min(targetX, maxX)));

            // 模型静止时位置/缩放不变，跳过 style 写入避免每帧样式失效
            if (nextTransform !== this._floatingButtonsLastTransform ||
                boundedX !== this._floatingButtonsLastLeft ||
                boundedY !== this._floatingButtonsLastTop) {
                this._floatingButtonsLastTransform = nextTransform;
                this._floatingButtonsLastLeft = boundedX;
                this._floatingButtonsLastTop = boundedY;
                buttonsContainer.style.transformOrigin = 'left top';
                buttonsContainer.style.transform = nextTransform;
                buttonsContainer.style.left = `${boundedX}px`;
                buttonsContainer.style.top = `${boundedY}px`;
            }
        } catch (_) {}
    };
    this._floatingButtonsTicker = tick;
    this.pixi_app.ticker.add(tick);

    setTimeout(() => {
        if (this.isLocked) {
            return;
        }
        if (isYuiGuideLive2DPreparing() || isYuiGuideFloatingToolbarSuppressed()) {
            hideYuiGuideLive2DPreparingButtonStyles(buttonsContainer);
            return;
        }
        restoreYuiGuideLive2DPreparingButtonStyles(buttonsContainer);
        buttonsContainer.style.display = 'flex';

        setTimeout(() => {
            const inTutorial = buttonsContainer.dataset.inTutorial === 'true' || window.isInTutorial === true;
            if (!this.isFocusing && !inTutorial) {
                buttonsContainer.style.display = 'none';
            } else if (inTutorial) {
                if (isYuiGuideLive2DPreparing() || isYuiGuideFloatingToolbarSuppressed()) {
                    hideYuiGuideLive2DPreparingButtonStyles(buttonsContainer);
                } else {
                    restoreYuiGuideLive2DPreparingButtonStyles(buttonsContainer);
                    buttonsContainer.style.setProperty('display', 'flex', 'important');
                }
            }
        }, 5000);
    }, 100);

    if (this.tutorialProtectionTimer) {
        clearInterval(this.tutorialProtectionTimer);
        this.tutorialProtectionTimer = null;
    }

    this.tutorialProtectionTimer = setInterval(() => {
        if (window.isInTutorial === true) {
            if (isYuiGuideLive2DPreparing() || isYuiGuideFloatingToolbarSuppressed()) {
                hideYuiGuideLive2DPreparingButtonStyles(buttonsContainer);
                return;
            }
            restoreYuiGuideLive2DPreparingButtonStyles(buttonsContainer);
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
    this._uiWindowHandlers = this._uiWindowHandlers || [];
    this._uiWindowHandlers.push({ event: 'click', handler: this._outsideClickHandler, target: document });

    window.dispatchEvent(new CustomEvent('live2d-floating-buttons-ready'));
    console.log('[Live2D] 浮动按钮就绪事件已发送');
};

/**
 * 设置返回按钮拖拽（Live2D 特定）
 */
Live2DManager.prototype.setupReturnButtonContainerDrag = function(container) {
    this._addReturnButtonBreathingAnimation();
};
