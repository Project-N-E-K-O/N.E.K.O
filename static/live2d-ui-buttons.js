/**
 * Live2D UI Buttons - 浮动按钮系统
 * 包含锁形图标和浮动控制面板
 */

// 设置 HTML 锁形图标（保留用于兼容）
Live2DManager.prototype.setupHTMLLockIcon = function (model) {
    // 【资源优化】如果正在加载 Live2D 模型（model 参数存在），
    // 强制清理所有 VRM 锁图标残留和旧的 Live2D 锁图标，确保 Live2D 锁图标能够正常创建
    if (model) {
        // 正在加载 Live2D 模型，清理所有 VRM 锁图标（包括隐藏的）
        document.querySelectorAll('#vrm-lock-icon, #vrm-lock-icon-hidden').forEach(el => {
            console.log('[锁图标] 清理残留的 VRM 锁图标');
            el.remove();
        });
    } else {
        // 没有模型参数，可能是初始化阶段，检查是否应该阻止创建
        const vrmLockIcon = document.getElementById('vrm-lock-icon');
        if (vrmLockIcon || (window.lanlan_config && window.lanlan_config.vrm_model)) {
            console.log('检测到 VRM 模式，Live2D 锁停止生成');
            return;
        }
    }
    
    const container = document.getElementById('live2d-canvas');

    // 防御性空值检查
    if (!container) {
        this.isLocked = false;
        return;
    }

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

    // 如果锁图标已存在，先移除它以确保创建新的锁图标
    // 这样可以避免重复创建，并确保锁图标的状态是最新的
    const existingLockIcon = document.getElementById('live2d-lock-icon');
    if (existingLockIcon) {
        // 先移除旧的 ticker，防止回调累积泄漏
        if (this._lockIconTicker && this.pixi_app?.ticker) {
            this.pixi_app.ticker.remove(this._lockIconTicker);
            this._lockIconTicker = null;
        }
        // 移除旧的锁图标，准备创建新的
        existingLockIcon.remove();
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
    // 【改进】存储锁图标及其图片引用，便于统一管理
    this._lockIconElement = lockIcon;
    this._lockIconImages = {
        locked: imgLocked,
        unlocked: imgUnlocked
    };

    lockIcon.addEventListener('click', (e) => {
        e.stopPropagation();
        // 【改进】使用统一的 setLocked 方法来同步更新状态和 UI
        this.setLocked(!this.isLocked);
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

            // 计算锁图标目标位置
            const targetX = bounds.right * 0.7 + bounds.left * 0.3;
            const targetY = bounds.top * 0.3 + bounds.bottom * 0.7;

            // 边界限制（现在窗口只覆盖一个屏幕，使用简单的边界检测）
            lockIcon.style.left = `${Math.max(0, Math.min(targetX, screenWidth - 40))}px`;
            lockIcon.style.top = `${Math.max(0, Math.min(targetY, screenHeight - 40))}px`;
        } catch (_) {
            // 忽略单帧异常
        }
    };
    this._lockIconTicker = tick;
    this.pixi_app.ticker.add(tick);
};

// 设置浮动按钮系统（新的控制面板）
Live2DManager.prototype.setupFloatingButtons = function (model) {
    const container = document.getElementById('live2d-canvas');

    // 防御性空值检查
    if (!container) {
        this.isLocked = false;
        return;
    }

    // 如果之前已经注册过 resize 监听器，先移除它以防止重复注册
    if (this._floatingButtonsResizeHandler) {
        window.removeEventListener('resize', this._floatingButtonsResizeHandler);
        this._floatingButtonsResizeHandler = null;
    }

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

    // 清理可能存在的旧浮动按钮容器，防止重复创建
    const existingContainer = document.getElementById('live2d-floating-buttons');
    if (existingContainer) {
        // 关键：旧实例仅移除 DOM 会导致 ticker 回调继续运行，并持有旧容器/闭包引用
        if (this._floatingButtonsTicker && this.pixi_app?.ticker) {
            try {
                this.pixi_app.ticker.remove(this._floatingButtonsTicker);
            } catch (_) {
                // 忽略移除失败（例如 ticker 已销毁）
            }
        }
        this._floatingButtonsTicker = null;

        // 清理保存的引用，便于 GC 回收旧闭包/容器
        if (this._floatingButtonsContainer === existingContainer) {
            this._floatingButtonsContainer = null;
        }
        this._floatingButtons = {};

        // 同步清理可能残留的“请她回来”容器，避免重复创建
        const existingReturnContainer = document.getElementById('live2d-return-button-container');
        if (existingReturnContainer) {
            existingReturnContainer.remove();
            if (this._returnButtonContainer === existingReturnContainer) {
                this._returnButtonContainer = null;
            }
        }

        existingContainer.remove();
    }

    // 创建按钮容器
    const buttonsContainer = document.createElement('div');
    buttonsContainer.id = 'live2d-floating-buttons';
    Object.assign(buttonsContainer.style, {
        position: 'fixed',
        zIndex: '99999',  // 确保始终浮动在顶层，不被live2d遮挡
        pointerEvents: 'auto',  // 修改为auto,允许按钮接收点击事件
        display: 'none', // 初始隐藏，鼠标靠近时才显示
        flexDirection: 'column',
        gap: '12px'
    });

    // 阻止浮动按钮容器上的指针事件传播到window，避免触发live2d拖拽
    const stopContainerEvent = (e) => {
        e.stopPropagation();
    };
    buttonsContainer.addEventListener('pointerdown', stopContainerEvent);
    buttonsContainer.addEventListener('pointermove', stopContainerEvent);
    buttonsContainer.addEventListener('pointerup', stopContainerEvent);
    buttonsContainer.addEventListener('mousedown', stopContainerEvent);
    buttonsContainer.addEventListener('mousemove', stopContainerEvent);
    buttonsContainer.addEventListener('mouseup', stopContainerEvent);
    buttonsContainer.addEventListener('touchstart', stopContainerEvent);
    buttonsContainer.addEventListener('touchmove', stopContainerEvent);
    buttonsContainer.addEventListener('touchend', stopContainerEvent);

    document.body.appendChild(buttonsContainer);
    this._floatingButtonsContainer = buttonsContainer;
    this._floatingButtons = this._floatingButtons || {};

    // 响应式：小屏时固定在右下角并纵向排列（使用全局 isMobileWidth）
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
    // 保存 handler 引用，以便后续清理
    this._floatingButtonsResizeHandler = applyResponsiveFloatingLayout;
    window.addEventListener('resize', this._floatingButtonsResizeHandler);

    // 定义按钮配置（从上到下：麦克风、显示屏、锤子、设置、睡觉）
    // 添加版本号防止缓存（更新图标时修改这个版本号）
    const iconVersion = '?v=' + Date.now();

    const buttonConfigs = [
        { id: 'mic', emoji: '🎤', title: window.t ? window.t('buttons.voiceControl') : '语音控制', titleKey: 'buttons.voiceControl', hasPopup: true, toggle: true, separatePopupTrigger: true, iconOff: '/static/icons/mic_icon_off.png' + iconVersion, iconOn: '/static/icons/mic_icon_on.png' + iconVersion },
        { id: 'screen', emoji: '🖥️', title: window.t ? window.t('buttons.screenShare') : '屏幕分享', titleKey: 'buttons.screenShare', hasPopup: true, toggle: true, separatePopupTrigger: true, iconOff: '/static/icons/screen_icon_off.png' + iconVersion, iconOn: '/static/icons/screen_icon_on.png' + iconVersion },
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
        btnWrapper.addEventListener('pointerdown', stopWrapperEvent);
        btnWrapper.addEventListener('pointermove', stopWrapperEvent);
        btnWrapper.addEventListener('pointerup', stopWrapperEvent);
        btnWrapper.addEventListener('mousedown', stopWrapperEvent);
        btnWrapper.addEventListener('mousemove', stopWrapperEvent);
        btnWrapper.addEventListener('mouseup', stopWrapperEvent);
        btnWrapper.addEventListener('touchstart', stopWrapperEvent);
        btnWrapper.addEventListener('touchmove', stopWrapperEvent);
        btnWrapper.addEventListener('touchend', stopWrapperEvent);

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
                opacity: '0.75',
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
            background: 'var(--neko-btn-bg)',  // Fluent Design Acrylic
            backdropFilter: 'saturate(180%) blur(20px)',  // Fluent 标准模糊
            border: 'var(--neko-btn-border)',  // 微妙高光边框
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: '24px',
            cursor: 'pointer',
            userSelect: 'none',
            boxShadow: 'var(--neko-btn-shadow)',  // Fluent 多层阴影
            transition: 'all 0.1s ease',  // Fluent 快速响应
            pointerEvents: 'auto'
        });

        // 阻止按钮上的指针事件传播到window，避免触发live2d拖拽
        // 注意:不使用捕获阶段(移除第三个参数true),否则会阻止click事件到达按钮元素
        const stopBtnEvent = (e) => {
            e.stopPropagation();
        };
        btn.addEventListener('pointerdown', stopBtnEvent);
        btn.addEventListener('pointermove', stopBtnEvent);
        btn.addEventListener('pointerup', stopBtnEvent);
        btn.addEventListener('mousedown', stopBtnEvent);
        btn.addEventListener('mousemove', stopBtnEvent);
        btn.addEventListener('mouseup', stopBtnEvent);
        btn.addEventListener('touchstart', stopBtnEvent);
        btn.addEventListener('touchmove', stopBtnEvent);
        btn.addEventListener('touchend', stopBtnEvent);

        // 鼠标悬停效果 - Fluent Design
        btn.addEventListener('mouseenter', () => {
            btn.style.transform = 'scale(1.05)';  // 更微妙的缩放
            btn.style.boxShadow = 'var(--neko-btn-shadow-hover)';
            btn.style.background = 'var(--neko-btn-bg-hover)';  // 悬停时更亮
            
            // 检查是否有单独的弹窗触发器且弹窗已打开（此时不应该切换图标）
            if (config.separatePopupTrigger) {
                const popup = document.getElementById(`live2d-popup-${config.id}`);
                const isPopupVisible = popup && popup.style.display === 'flex' && popup.style.opacity === '1';
                if (isPopupVisible) {
                    // 弹窗已打开，不改变图标状态
                    return;
                }
            }
            
            // 淡出off图标，淡入on图标
            if (imgOff && imgOn) {
                imgOff.style.opacity = '0';
                imgOn.style.opacity = '1';
            }
        });
        btn.addEventListener('mouseleave', () => {
            btn.style.transform = 'scale(1)';
            btn.style.boxShadow = 'var(--neko-btn-shadow)';
            // 恢复原始背景色（根据按钮状态）
            const isActive = btn.dataset.active === 'true';
            const popup = document.getElementById(`live2d-popup-${config.id}`);
            const isPopupVisible = popup && popup.style.display === 'flex' && popup.style.opacity === '1';
            
            // 对于有单独弹窗触发器的按钮，弹窗状态不应该影响母按钮的图标
            // 只有按钮自己的 active 状态才应该决定图标显示
            const shouldShowOnIcon = config.separatePopupTrigger 
                ? isActive  // separatePopupTrigger: 只看按钮的 active 状态
                : (isActive || isPopupVisible);  // 普通按钮: active 或弹窗打开都显示 on

            if (shouldShowOnIcon) {
                // 激活状态：稍亮的背景
                btn.style.background = 'var(--neko-btn-bg-active)';
            } else {
                btn.style.background = 'var(--neko-btn-bg)';  // Fluent Acrylic
            }

            // 根据按钮激活状态决定显示哪个图标
            if (imgOff && imgOn) {
                if (shouldShowOnIcon) {
                    // 激活状态：保持on图标
                    imgOff.style.opacity = '0';
                    imgOn.style.opacity = '1';
                } else {
                    // 未激活状态：显示off图标
                    imgOff.style.opacity = '0.75';
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
                    // 更新被关闭的互斥按钮的图标
                    const exclusiveData = this._floatingButtons[config.exclusive];
                    if (exclusiveData && exclusiveData.imgOff && exclusiveData.imgOn) {
                        exclusiveData.imgOff.style.opacity = '0.75';
                        exclusiveData.imgOn.style.opacity = '0';
                    }
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
                            imgOff.style.opacity = '0.75';
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
                        // 检查是否正在启动中：使用专用的 isMicStarting 标志
                        // isMicStarting 为 true 表示正在启动过程中，阻止点击
                        const isMicStarting = window.isMicStarting || false;

                        if (isMicStarting) {
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
                        // 如果 isMicStarting 为 false，说明已经启动成功，允许继续执行（可以退出）
                    }
                }

                // 对于屏幕分享按钮，检查语音是否正在进行
                if (config.id === 'screen') {
                    const isRecording = window.isRecording || false;
                    const wantToActivate = btn.dataset.active !== 'true';  // 当前未激活，想要激活
                    
                    if (wantToActivate && !isRecording) {
                        // 语音未开启时尝试开启屏幕分享，显示提示并阻止操作
                        if (typeof window.showStatusToast === 'function') {
                            window.showStatusToast(
                                window.t ? window.t('app.screenShareRequiresVoice') : '屏幕分享仅用于音视频通话',
                                3000
                            );
                        }
                        return; // 阻止操作
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
                        imgOff.style.opacity = '0.75';
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
                triggerBtn.className = 'live2d-trigger-btn';
                // 使用图片图标替代文字符号
                const triggerImg = document.createElement('img');
                triggerImg.src = '/static/icons/play_trigger_icon.png' + iconVersion;
                triggerImg.alt = '▶';
                triggerImg.className = `live2d-trigger-icon-${config.id}`;
                Object.assign(triggerImg.style, {
                    width: '22px', height: '22px', objectFit: 'contain',
                    pointerEvents: 'none', imageRendering: '-webkit-optimize-contrast', imageRendering: 'crisp-edges',
                    transition: 'transform 0.3s cubic-bezier(0.1, 0.9, 0.2, 1)'
                });
                triggerBtn.appendChild(triggerImg);
                Object.assign(triggerBtn.style, {
                    width: '24px',
                    height: '24px',
                    borderRadius: '50%',
                    background: 'var(--neko-btn-bg)',  // Fluent Acrylic
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

                // 阻止三角按钮上的指针事件传播到window，避免触发live2d拖拽
                const stopTriggerEvent = (e) => {
                    e.stopPropagation();
                };
                triggerBtn.addEventListener('pointerdown', stopTriggerEvent);
                triggerBtn.addEventListener('pointermove', stopTriggerEvent);
                triggerBtn.addEventListener('pointerup', stopTriggerEvent);
                triggerBtn.addEventListener('mousedown', stopTriggerEvent);
                triggerBtn.addEventListener('mousemove', stopTriggerEvent);
                triggerBtn.addEventListener('mouseup', stopTriggerEvent);
                triggerBtn.addEventListener('touchstart', stopTriggerEvent);
                triggerBtn.addEventListener('touchmove', stopTriggerEvent);
                triggerBtn.addEventListener('touchend', stopTriggerEvent);

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

                    // 检查弹出框是否已经显示（如果已显示，showPopup会关闭它，不需要重新加载）
                    const isPopupVisible = popup.style.display === 'flex' && popup.style.opacity === '1';

                    // 如果是麦克风弹出框且弹窗未显示，先加载麦克风列表
                    if (config.id === 'mic' && window.renderFloatingMicList && !isPopupVisible) {
                        await window.renderFloatingMicList();
                    }
                    
                    // 如果是屏幕分享弹出框且弹窗未显示，先加载屏幕源列表
                    if (config.id === 'screen' && window.renderFloatingScreenSourceList && !isPopupVisible) {
                        await window.renderFloatingScreenSourceList();
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
                triggerWrapper.addEventListener('pointerdown', stopTriggerWrapperEvent);
                triggerWrapper.addEventListener('pointermove', stopTriggerWrapperEvent);
                triggerWrapper.addEventListener('pointerup', stopTriggerWrapperEvent);
                triggerWrapper.addEventListener('mousedown', stopTriggerWrapperEvent);
                triggerWrapper.addEventListener('mousemove', stopTriggerWrapperEvent);
                triggerWrapper.addEventListener('mouseup', stopTriggerWrapperEvent);
                triggerWrapper.addEventListener('touchstart', stopTriggerWrapperEvent);
                triggerWrapper.addEventListener('touchmove', stopTriggerWrapperEvent);
                triggerWrapper.addEventListener('touchend', stopTriggerWrapperEvent);

                triggerWrapper.appendChild(triggerBtn);
                triggerWrapper.appendChild(popup);

                btnWrapper.appendChild(triggerWrapper);
            }
        } else {
            // 普通点击按钮
            btnWrapper.appendChild(btn);
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
            imgOff: imgOff,  // 保存图标引用
            imgOn: imgOn      // 保存图标引用
        };
        console.log(`[Live2D] 按钮已创建: ${config.id}, hasPopup: ${config.hasPopup}, toggle: ${config.toggle}`);
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
        opacity: '0.75',
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
        background: 'var(--neko-btn-bg)',  // Fluent Acrylic
        backdropFilter: 'saturate(180%) blur(20px)',
        border: 'var(--neko-btn-border)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        cursor: 'pointer',
        userSelect: 'none',
        boxShadow: 'var(--neko-popup-shadow)',
        transition: 'all 0.1s ease',
        pointerEvents: 'auto',
        position: 'relative'
    });

    // 悬停效果 - Fluent Design
    returnBtn.addEventListener('mouseenter', () => {
        returnBtn.style.transform = 'scale(1.05)';
        returnBtn.style.boxShadow = 'var(--neko-btn-shadow-hover)';
        returnBtn.style.background = 'var(--neko-btn-bg-hover)';
        imgOff.style.opacity = '0';
        imgOn.style.opacity = '1';
    });

    returnBtn.addEventListener('mouseleave', () => {
        returnBtn.style.transform = 'scale(1)';
        returnBtn.style.boxShadow = 'var(--neko-popup-shadow)';
        returnBtn.style.background = 'var(--neko-btn-bg)';
        imgOff.style.opacity = '0.75';
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
            
            // 计算模型中心点
            const modelCenterX = (bounds.left + bounds.right) / 2;
            const modelCenterY = (bounds.top + bounds.bottom) / 2;

            // 计算模型实际高度
            const modelHeight = bounds.bottom - bounds.top;

            // 计算目标工具栏高度（模型高度的一半）
            const targetToolbarHeight = modelHeight / 2;

            // 计算缩放比例（限制在合理范围内，防止按钮太小或太大）
            const minScale = 0.5;  // 最小缩放50%
            const maxScale = 1.;  // 最大缩放100%
            const rawScale = targetToolbarHeight / baseToolbarHeight;
            const scale = Math.max(minScale, Math.min(maxScale, rawScale));

            // 应用缩放到容器（使用 transform-origin: left top 确保从左上角缩放）
            buttonsContainer.style.transformOrigin = 'left top';
            buttonsContainer.style.transform = `scale(${scale})`;

            // X轴：定位在角色右侧（与锁按钮类似的横向位置）
            const targetX = bounds.right * 0.8 + bounds.left * 0.2;

            // 使用缩放后的实际工具栏高度
            const actualToolbarHeight = baseToolbarHeight * scale;
            const actualToolbarWidth = 80 * scale;
            
            // Y轴：工具栏中心与模型中心对齐
            // 让工具栏的中心位于模型中间，所以top = 中间 - 高度/2
            const targetY = modelCenterY - actualToolbarHeight / 2;

            // 边界限制：确保不超出当前屏幕（窗口只覆盖一个屏幕）
            const minY = 20; // 距离屏幕顶部的最小距离
            const maxY = screenHeight - actualToolbarHeight - 20; // 距离屏幕底部的最小距离
            const boundedY = Math.max(minY, Math.min(targetY, maxY));

            // X轴边界限制：确保不超出当前屏幕
            const maxX = screenWidth - actualToolbarWidth;
            const boundedX = Math.max(0, Math.min(targetX, maxX));

            buttonsContainer.style.left = `${boundedX}px`;
            buttonsContainer.style.top = `${boundedY}px`;
            // 不要在这里设置 display，让鼠标检测逻辑来控制显示/隐藏
        } catch (_) {
            // 忽略单帧异常
        }
    };
    this._floatingButtonsTicker = tick;
    this.pixi_app.ticker.add(tick);
    
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
            // 但如果在引导中，则保持显示
            const inTutorial = buttonsContainer.dataset.inTutorial === 'true' || window.isInTutorial === true;
            if (!this.isFocusing && !inTutorial) {
                buttonsContainer.style.display = 'none';
            } else if (inTutorial) {
                // 在引导中，确保浮动按钮始终显示
                buttonsContainer.style.setProperty('display', 'flex', 'important');
            }
        }, 5000);
    }, 100); // 延迟100ms确保位置已计算

    // 在引导中，添加额外的保护定时器，确保浮动按钮始终显示
    // 清除任何现有的定时器，防止累积
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
            // 引导结束，清除定时器
            if (this.tutorialProtectionTimer) {
                clearInterval(this.tutorialProtectionTimer);
                this.tutorialProtectionTimer = null;
            }
        }
    }, 300);

    // 为"请她回来"按钮容器添加拖动功能
    this.setupReturnButtonContainerDrag(returnButtonContainer);

    // 通知其他代码浮动按钮已经创建完成（用于app.js中绑定Agent开关事件）
    window.dispatchEvent(new CustomEvent('live2d-floating-buttons-ready'));
    console.log('[Live2D] 浮动按钮就绪事件已发送');
};