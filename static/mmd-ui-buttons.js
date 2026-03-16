/**
 * MMD UI Buttons - 浮动按钮系统
 * 参考 vrm-ui-buttons.js，适配 MMD 模式
 */

MMDManager.prototype.setupFloatingButtons = function () {
    if (window.location.pathname.includes('model_manager')) return;

    if (!this._uiWindowHandlers) {
        this._uiWindowHandlers = [];
    }
    if (this._uiWindowHandlers.length > 0) {
        this._uiWindowHandlers.forEach(({ event, handler }) => {
            window.removeEventListener(event, handler);
        });
        this._uiWindowHandlers = [];
    }

    // 清理旧按钮
    const buttonsContainerId = 'mmd-floating-buttons';
    const old = document.getElementById(buttonsContainerId);
    if (old) old.remove();

    const buttonsContainer = document.createElement('div');
    buttonsContainer.id = buttonsContainerId;
    document.body.appendChild(buttonsContainer);

    Object.assign(buttonsContainer.style, {
        position: 'fixed',
        zIndex: '99999',
        pointerEvents: 'auto',
        display: 'none',
        flexDirection: 'column',
        gap: '12px',
        visibility: 'visible',
        opacity: '1',
        transform: 'none'
    });
    this._floatingButtonsContainer = buttonsContainer;

    // 阻止事件穿透到 canvas
    const stopEvent = (e) => { e.stopPropagation(); };
    ['pointerdown', 'pointermove', 'pointerup', 'mousedown', 'mousemove', 'mouseup'].forEach(evt => {
        buttonsContainer.addEventListener(evt, stopEvent);
    });

    // ═══════════════════ 锁定按钮 ═══════════════════

    const lockBtn = document.createElement('div');
    lockBtn.id = 'mmd-lock-btn';
    Object.assign(lockBtn.style, {
        width: '36px', height: '36px',
        borderRadius: '50%',
        background: 'rgba(255,255,255,0.65)',
        backdropFilter: 'saturate(180%) blur(20px)',
        border: '1px solid rgba(255,255,255,0.18)',
        cursor: 'pointer',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
        transition: 'transform 0.2s, box-shadow 0.2s'
    });

    const lockIcon = document.createElement('div');
    lockIcon.id = 'mmd-lock-icon';
    Object.assign(lockIcon.style, {
        width: '20px', height: '20px',
        backgroundSize: 'contain',
        backgroundRepeat: 'no-repeat',
        backgroundPosition: 'center',
        backgroundImage: 'url(/static/icons/unlocked_icon.png)'
    });
    lockBtn.appendChild(lockIcon);

    lockBtn.addEventListener('click', () => {
        const newLocked = !this.isLocked;
        if (this.core) {
            this.core.setLocked(newLocked);
        }
    });

    lockBtn.addEventListener('mouseenter', () => {
        lockBtn.style.transform = 'scale(1.1)';
    });
    lockBtn.addEventListener('mouseleave', () => {
        lockBtn.style.transform = 'scale(1)';
    });

    buttonsContainer.appendChild(lockBtn);

    // ═══════════════════ 设置按钮 ═══════════════════

    const settingsBtn = this._createIconButton('mmd-settings-btn', '/static/icons/settings_icon.png');
    buttonsContainer.appendChild(settingsBtn);

    // 设置弹出框
    if (typeof window.createMMDPopup === 'function') {
        const settingsItems = [
            {
                label: '物理模拟',
                onClick: () => {
                    this.enablePhysics = !this.enablePhysics;
                    console.log('[MMD UI] 物理模拟:', this.enablePhysics ? '开启' : '关闭');
                }
            },
            {
                label: '鼠标跟踪',
                onClick: () => {
                    if (this.cursorFollow) {
                        this.cursorFollow.setEnabled(!this.cursorFollow.enabled);
                    }
                }
            },
            {
                label: '描边效果',
                onClick: () => {
                    this.useOutlineEffect = !this.useOutlineEffect;
                }
            }
        ];

        const popup = window.createMMDPopup(settingsBtn, settingsItems);
        let popupVisible = false;
        settingsBtn.addEventListener('click', () => {
            if (popupVisible) {
                window.hideMMDPopup(popup);
            } else {
                window.showMMDPopup(popup);
            }
            popupVisible = !popupVisible;
        });
    }

    // ═══════════════════ 调试按钮 ═══════════════════

    const debugBtn = this._createIconButton('mmd-debug-btn', '/static/icons/settings_icon.png');
    debugBtn.title = '渲染调试';
    debugBtn.style.position = 'relative';
    // 添加小标记以区分设置按钮
    const debugDot = document.createElement('div');
    Object.assign(debugDot.style, {
        position: 'absolute', bottom: '1px', right: '1px',
        width: '8px', height: '8px', borderRadius: '50%',
        background: '#44b7fe', border: '1px solid #fff'
    });
    debugBtn.appendChild(debugDot);
    buttonsContainer.appendChild(debugBtn);

    debugBtn.addEventListener('click', () => {
        if (typeof window.toggleMMDDebugPanel === 'function') {
            window.toggleMMDDebugPanel();
        }
    });

    // ═══════════════════ 返回按钮（退出 MMD 模式） ═══════════════════

    const returnBtn = this._createIconButton('mmd-return-btn', '/static/icons/return_icon.png');
    buttonsContainer.appendChild(returnBtn);

    returnBtn.addEventListener('click', () => {
        // 触发模型返回事件
        window.dispatchEvent(new CustomEvent('mmd-goodbye-click'));
    });

    // ═══════════════════ 响应式布局 ═══════════════════

    const applyLayout = () => {
        const isLocked = this.isLocked;
        if (isLocked || this._isInReturnState) {
            buttonsContainer.style.display = 'none';
            return;
        }
        if (window.innerWidth <= 768) {
            buttonsContainer.style.flexDirection = 'column';
            buttonsContainer.style.bottom = '116px';
            buttonsContainer.style.right = '16px';
            buttonsContainer.style.left = '';
            buttonsContainer.style.top = '';
        } else {
            buttonsContainer.style.flexDirection = 'column';
            buttonsContainer.style.right = '20px';
            buttonsContainer.style.top = '50%';
            buttonsContainer.style.transform = 'translateY(-50%)';
            buttonsContainer.style.bottom = '';
            buttonsContainer.style.left = '';
        }
        buttonsContainer.style.display = 'flex';
    };
    applyLayout();

    const resizeHandler = () => applyLayout();
    window.addEventListener('resize', resizeHandler);
    this._uiWindowHandlers.push({ event: 'resize', handler: resizeHandler });
};

/**
 * 创建图标按钮
 */
MMDManager.prototype._createIconButton = function (id, iconUrl) {
    const btn = document.createElement('div');
    btn.id = id;
    Object.assign(btn.style, {
        width: '36px', height: '36px',
        borderRadius: '50%',
        background: 'rgba(255,255,255,0.65)',
        backdropFilter: 'saturate(180%) blur(20px)',
        border: '1px solid rgba(255,255,255,0.18)',
        cursor: 'pointer',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
        transition: 'transform 0.2s, box-shadow 0.2s'
    });

    const icon = document.createElement('div');
    Object.assign(icon.style, {
        width: '20px', height: '20px',
        backgroundSize: 'contain',
        backgroundRepeat: 'no-repeat',
        backgroundPosition: 'center',
        backgroundImage: `url(${iconUrl})`
    });
    btn.appendChild(icon);

    btn.addEventListener('mouseenter', () => {
        btn.style.transform = 'scale(1.1)';
    });
    btn.addEventListener('mouseleave', () => {
        btn.style.transform = 'scale(1)';
    });

    return btn;
};

/**
 * 清理浮动按钮
 */
MMDManager.prototype.cleanupFloatingButtons = function () {
    const container = document.getElementById('mmd-floating-buttons');
    if (container) container.remove();

    if (this._uiWindowHandlers) {
        this._uiWindowHandlers.forEach(({ event, handler }) => {
            window.removeEventListener(event, handler);
        });
        this._uiWindowHandlers = [];
    }
};
