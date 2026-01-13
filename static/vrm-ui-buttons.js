/**
 * VRM UI Buttons - 浮动按钮系统（功能同步修复版）
 */

// 设置浮动按钮系统
VRMManager.prototype.setupFloatingButtons = function () {
    // 如果是模型管理页面，直接禁止创建浮动按钮
    if (window.location.pathname.includes('model_manager')) {
        return; 
    }
    
    // 如果之前已经注册过 document 级别的事件监听器，先移除它们以防止重复注册
    if (this._returnButtonDragHandlers) {
        document.removeEventListener('mousemove', this._returnButtonDragHandlers.mouseMove);
        document.removeEventListener('mouseup', this._returnButtonDragHandlers.mouseUp);
        document.removeEventListener('touchmove', this._returnButtonDragHandlers.touchMove);
        document.removeEventListener('touchend', this._returnButtonDragHandlers.touchEnd);
        this._returnButtonDragHandlers = null;
    }
    const container = document.getElementById('vrm-container');

    // 强力清除旧势力的残党
    document.querySelectorAll('#live2d-floating-buttons').forEach(el => el.remove());
    
    // 1. 改这里：给他一个全新的名字，不再和旧代码打架
    const buttonsContainerId = 'vrm-floating-buttons'; 

    // 清理逻辑（防止热重载堆积）
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

    // 阻止浮动按钮容器上的指针事件传播到window
    const stopContainerEvent = (e) => { e.stopPropagation(); };
    ['pointerdown','pointermove','pointerup','mousedown','mousemove','mouseup','touchstart','touchmove','touchend'].forEach(evt => {
        buttonsContainer.addEventListener(evt, stopContainerEvent);
    });

    // 响应式布局逻辑
    // 确保 isMobileWidth 可用
    const isMobileWidth = () => window.innerWidth <= 768;

    const applyResponsiveFloatingLayout = () => {
        if (isMobileWidth()) {
            // 移动端：固定在右下角，纵向排布，整体上移
            buttonsContainer.style.flexDirection = 'column';
            buttonsContainer.style.bottom = '116px';
            buttonsContainer.style.right = '16px';
            buttonsContainer.style.left = ''; // 清除左定位
            buttonsContainer.style.top = '';  // 清除上定位
            buttonsContainer.style.display = 'flex'; // 移动端强制显示
        } else {
            // 桌面端：恢复纵向排布，由 _startUIUpdateLoop 动态定位
            buttonsContainer.style.flexDirection = 'column';
            buttonsContainer.style.bottom = '';
            buttonsContainer.style.right = '';
            // display 由 loop 控制
        }
    };
    applyResponsiveFloatingLayout();
    window.addEventListener('resize', applyResponsiveFloatingLayout);

    // 2. 按钮配置（与 Live2D 保持一致）
    const iconVersion = '?v=' + Date.now();
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
        // 移动端隐藏 agent 和 goodbye 按钮
        if (isMobileWidth() && (config.id === 'agent' || config.id === 'goodbye')) {
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

                // 1. 麦克风安全检查
                if (config.id === 'mic') {
                    const micButton = document.getElementById('micButton');
                    // 检查是否正在启动中
                    const isMicStarting = window.isMicStarting || false;
                    if (isMicStarting) {
                        if (btn.dataset.active !== 'true') {
                            // 使用统一的状态管理方法
                            this.setButtonActive(config.id, true);
                        }
                        return; 
                    }
                }

                // 2. 屏幕分享安全检查
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

                const currentActive = btn.dataset.active === 'true';
                let targetActive = !currentActive; 

                // 如果是 popupToggle 按钮（settings 或 agent），由 popupToggle 分支的处理器处理，这里直接返回
                if (config.popupToggle) {
                    return;
                }
                
                if (config.id === 'mic' || config.id === 'screen') {
                   // 触发全局事件
                   window.dispatchEvent(new CustomEvent(`live2d-${config.id}-toggle`, {detail:{active:targetActive}}));
                   
                   // 使用统一的状态管理方法更新 UI 状态
                   // 注意：UI状态更新通常由 app.js 监听事件后回调，但这里预先更新以提高响应速度
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

        // 如果有弹出框且需要独立的触发器（仅麦克风）
        if (config.hasPopup && config.separatePopupTrigger) {
            // 手机模式下移除麦克风弹窗与触发器
            if (isMobileWidth() && config.id === 'mic') {
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

            // 阻止冒泡
            const stopTriggerEvent = (e) => { e.stopPropagation(); };
            ['pointerdown','mousedown','touchstart'].forEach(evt => triggerBtn.addEventListener(evt, stopTriggerEvent));

            triggerBtn.addEventListener('click', async (e) => {
                e.stopPropagation();

                // 检查弹出框是否已经显示（如果已显示，showPopup会关闭它，不需要重新加载）
                const isPopupVisible = popup.style.display === 'flex' && popup.style.opacity === '1';

                // 如果是麦克风弹出框且弹窗未显示，先加载麦克风列表
                if (config.id === 'mic' && !isPopupVisible) {
                    await this.renderMicList(popup);
                }

                // 如果是屏幕分享弹出框且弹窗未显示，先加载屏幕源列表
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

            // 添加防抖标志，防止在动画过程中重复点击
            let isToggling = false;

            btn.addEventListener('click', (e) => {
                e.stopPropagation();

                // 如果正在切换中，忽略点击
                if (isToggling) {
                    return;
                }

                // 检查弹出框当前状态（考虑动画过程中的状态）
                // 如果 display 是 'flex' 且 opacity 不是 '0'，则认为弹窗可见
                const isPopupVisible = popup.style.display === 'flex' && 
                                      popup.style.opacity !== '0' && 
                                      popup.style.opacity !== '';

                // 实现互斥逻辑：如果有exclusive配置，关闭对方
                if (!isPopupVisible && config.exclusive) {
                    this.closePopupById(config.exclusive);
                }

                // 设置防抖标志
                isToggling = true;

                // 切换弹出框
                // showPopup 方法会处理按钮图标状态的更新，这里不需要重复处理
                this.showPopup(config.id, popup);

                // 200ms 后解除防抖（与动画时间一致）
                setTimeout(() => {
                    isToggling = false;
                }, 200);
            });
        }

        buttonsContainer.appendChild(btnWrapper);
    });

    // 监听 "请她离开" 事件 (由 app.js 触发)
    window.addEventListener('live2d-goodbye-click', () => {
        
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
    });

    // 监听 "请她回来" 事件 (由 app.js 或 vrm 自身触发)
    const handleReturn = () => {
        
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
    
    // 同时监听两个可能的事件名，确保兼容性
    window.addEventListener('vrm-return-click', handleReturn);
    window.addEventListener('live2d-return-click', handleReturn);
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
        // 同时派发两个事件，确保app.js的完整恢复逻辑执行
        window.dispatchEvent(new CustomEvent('vrm-return-click'));
        window.dispatchEvent(new CustomEvent('live2d-return-click'));
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
        
        // 使用 core.setLocked() 统一管理锁定状态
        const newLockedState = !this.interaction.isLocked;
        if (this.core && typeof this.core.setLocked === 'function') {
            this.core.setLocked(newLockedState);
        } else {
            // 如果没有 core.setLocked，直接设置
            this.interaction.isLocked = newLockedState;
            const vrmCanvas = document.getElementById('vrm-canvas');
            if (vrmCanvas) vrmCanvas.style.pointerEvents = newLockedState ? 'none' : 'auto';
        }
        
        // 更新锁图标样式
        lockIcon.style.backgroundImage = this.interaction.isLocked ? 'url(/static/icons/locked_icon.png)' : 'url(/static/icons/unlocked_icon.png)';
        
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
    // 确保 isMobileWidth 可用
    const isMobileWidth = () => window.innerWidth <= 768;

    // 基准按钮尺寸和工具栏高度（用于计算缩放，与 Live2D 保持一致）
    const baseButtonSize = 48;
    const baseGap = 12;
    const buttonCount = 5;
    const baseToolbarHeight = baseButtonSize * buttonCount + baseGap * (buttonCount - 1); // 288px

    const update = () => {
        if (!this.currentModel || !this.currentModel.vrm) {
            requestAnimationFrame(update);
            return;
        }

        // 移动端跳过位置更新，使用 CSS 固定定位
        if (isMobileWidth()) {
            requestAnimationFrame(update);
            return;
        }
        
        const buttonsContainer = document.getElementById('vrm-floating-buttons')
        const lockIcon = this._vrmLockIcon;
        
        if (!this.camera || !this.renderer) {
            requestAnimationFrame(update);
            return;
        }

        try {
            const vrm = this.currentModel.vrm;
            const width = window.innerWidth;
            const height = window.innerHeight;
            const canvasRect = this.renderer.domElement.getBoundingClientRect();

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
                    headScreenY = (-headPos.y * 0.5 + 0.5) * canvasRect.height;
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
                    footScreenY = (-footPos.y * 0.5 + 0.5) * canvasRect.height;
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
                    const centerScreenY = (-centerPos.y * 0.5 + 0.5) * canvasRect.height;
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
                const screenX = (btnPos.x * 0.5 + 0.5) * width;
                const screenY = (-(btnPos.y * 0.5) + 0.5) * height;
                
                // 应用缩放到容器（使用 transform-origin: left top 确保从左上角缩放）
                buttonsContainer.style.transformOrigin = 'left top';
                buttonsContainer.style.transform = `scale(${scale})`;

                // 计算目标位置（应用偏移，减小垂直偏移让按钮更靠近模型）
                const targetX = screenX;
                const targetY = screenY - 50;  // 从 -100 减小到 -50，更靠近模型
                
                // 使用缩放后的实际工具栏高度和宽度（用于边界限制）
                const actualToolbarHeight = baseToolbarHeight * scale;
                const actualToolbarWidth = 48 * scale;  // 按钮宽度
                
                // 屏幕边缘限制（参考 Live2D 的实现）
                const minMargin = 10;  // 最小边距
                
                // X轴边界限制：确保按钮容器不超出屏幕右边界
                const maxX = width - actualToolbarWidth - minMargin;
                const clampedX = Math.max(minMargin, Math.min(targetX, maxX));
                
                // Y轴边界限制：确保按钮容器不超出屏幕上下边界
                const minY = minMargin;
                const maxY = height - actualToolbarHeight - minMargin;
                const clampedY = Math.max(minY, Math.min(targetY, maxY));
                
                buttonsContainer.style.left = `${clampedX}px`;
                buttonsContainer.style.top = `${clampedY}px`;
                // 不要在这里设置 display，让鼠标检测逻辑和初始显示逻辑来控制显示/隐藏（与 Live2D 保持一致） 
            }

            // 更新锁位置（使用与按钮相同的缩放比例）
            if (lockIcon) {
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
                const targetLockX = (lockPos.x * 0.5 + 0.5) * width;
                const targetLockY = (-(lockPos.y * 0.5) + 0.5) * height;
                
                // 应用缩放到锁图标（使用与按钮相同的缩放比例）
                const baseLockIconSize = 44;  // 锁图标基准尺寸 44px x 44px
                lockIcon.style.transformOrigin = 'center center';
                lockIcon.style.transform = `scale(${scale})`;
                
                // 使用缩放后的实际尺寸（用于边界限制）
                const actualLockIconSize = baseLockIconSize * scale;
                const minMargin = 10;  // 最小边距
                
                // 屏幕边缘限制
                const maxLockX = width - actualLockIconSize - minMargin;
                const maxLockY = height - actualLockIconSize - minMargin;
                const clampedLockX = Math.max(minMargin, Math.min(targetLockX, maxLockX));
                const clampedLockY = Math.max(minMargin, Math.min(targetLockY, maxLockY));
                
                lockIcon.style.left = `${clampedLockX}px`;
                lockIcon.style.top = `${clampedLockY}px`;
                lockIcon.style.display = 'block';
            }
        } catch (error) {
            // 忽略单帧异常，继续更新循环
        }
        requestAnimationFrame(update);
    };
    requestAnimationFrame(update);
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
        if (e.target === returnButtonContainer || e.target.classList.contains('vrm-return-btn')) {
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
        if (e.target === returnButtonContainer || e.target.classList.contains('vrm-return-btn')) {
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
    const vrmButtons = document.getElementById('vrm-floating-buttons');
    if (vrmButtons) vrmButtons.remove();
    document.querySelectorAll('#vrm-lock-icon').forEach(el => el.remove());
    const vrmReturnBtn = document.getElementById('vrm-return-button-container');
    if (vrmReturnBtn) vrmReturnBtn.remove();
    
    // 移除 document 级别的事件监听器，防止内存泄漏
    if (this._returnButtonDragHandlers) {
        document.removeEventListener('mousemove', this._returnButtonDragHandlers.mouseMove);
        document.removeEventListener('mouseup', this._returnButtonDragHandlers.mouseUp);
        document.removeEventListener('touchmove', this._returnButtonDragHandlers.touchMove);
        document.removeEventListener('touchend', this._returnButtonDragHandlers.touchEnd);
        this._returnButtonDragHandlers = null;
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