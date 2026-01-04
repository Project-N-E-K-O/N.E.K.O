/**
 * VRM UI Buttons - 浮动按钮系统（功能同步修复版）
 */

// 设置浮动按钮系统
VRMManager.prototype.setupFloatingButtons = function () {
    // 如果是模型管理页面，直接禁止创建浮动按钮
    if (window.location.pathname.includes('model_manager')) {
        return; 
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

    // --- 新增：响应式布局逻辑 ---
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

            // ==========================================
            // 🔥【修复】移植 Live2D 的安全点击逻辑
            // ==========================================
            btn.addEventListener('click', (e) => {
                console.log(`[VRM] 按钮被点击: ${config.id}`);
                e.stopPropagation();
                e.preventDefault();

                // 1. 麦克风安全检查
                if (config.id === 'mic') {
                    const micButton = document.getElementById('micButton');
                    // 检查是否正在启动中
                    const isMicStarting = window.isMicStarting || false;
                    if (isMicStarting) {
                        console.log('[VRM] 麦克风正在启动中，忽略点击');
                        if (btn.dataset.active !== 'true') {
                            // 强制同步状态
                            btn.dataset.active = 'true';
                            if (imgOff && imgOn) { imgOff.style.opacity = '0'; imgOn.style.opacity = '1'; }
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

                if (config.id === 'settings' || config.id === 'agent') {
                    const popup = document.getElementById(`vrm-popup-${config.id}`);
                    if (popup) {
                        const isVisible = popup.style.display === 'flex' && popup.style.opacity !== '0';
                        targetActive = !isVisible;
                        
                        // 实现互斥逻辑：如果有exclusive配置，关闭对方
                        if (!isVisible && config.exclusive) {
                            this.closePopupById(config.exclusive);
                        }
                        
                        this.showPopup(config.id, popup);
                        
                        // 延迟更新图标以匹配弹窗状态
                        setTimeout(() => {
                            const newPopupVisible = popup.style.display === 'flex' && popup.style.opacity === '1';
                            if (imgOff && imgOn) {
                                imgOff.style.opacity = newPopupVisible ? '0' : '1';
                                imgOn.style.opacity = newPopupVisible ? '1' : '0';
                            }
                        }, 50);
                    }
                }
                else if (config.id === 'mic' || config.id === 'screen') {
                   // 触发全局事件
                   window.dispatchEvent(new CustomEvent(`live2d-${config.id}-toggle`, {detail:{active:targetActive}}));
                   
                   // UI状态更新通常由 app.js 监听事件后回调，或者这里预先更新（为了响应快）
                   btn.dataset.active = targetActive.toString();
                   if (imgOff && imgOn) {
                       imgOff.style.opacity = targetActive ? '0' : '1';
                       imgOn.style.opacity = targetActive ? '1' : '0';
                   }
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
                console.log(`[VRM] 小三角被点击: ${config.id}`);
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

            btn.addEventListener('click', (e) => {
                e.stopPropagation();

                // 检查弹出框当前状态
                const isPopupVisible = popup.style.display === 'flex' && popup.style.opacity === '1';

                // 实现互斥逻辑：如果有exclusive配置，关闭对方
                if (!isPopupVisible && config.exclusive) {
                    this.closePopupById(config.exclusive);
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
                            imgOff.style.opacity = '1';
                            imgOn.style.opacity = '0';
                        }
                    }
                }, 50);
            });
        }

        buttonsContainer.appendChild(btnWrapper);
    });

    console.log('[VRM] 所有浮动按钮已创建完成');
    // ==========================================
    // 🔥【新增】监听全局离开/回来事件
    // ==========================================
    
    // 监听 "请她离开" 事件 (由 app.js 触发)
    window.addEventListener('live2d-goodbye-click', () => {
        console.log('[VRM] 收到离开信号，隐藏 UI');
        
        // 1. 隐藏主按钮组
        if (this._floatingButtonsContainer) {
            this._floatingButtonsContainer.style.display = 'none';
        }
        
        // 2. 隐藏锁图标
        if (this._vrmLockIcon) {
            this._vrmLockIcon.style.display = 'none';
        }
        
        // 3. 显示"请她回来"按钮
        if (this._returnButtonContainer) {
            // 尝试定位到原来"睡觉"按钮的位置（如果能找到的话）
            const goodbyeBtn = document.getElementById('vrm-btn-goodbye');
            if (goodbyeBtn) {
                const rect = goodbyeBtn.getBoundingClientRect();
                this._returnButtonContainer.style.left = rect.left + 'px';
                this._returnButtonContainer.style.top = rect.top + 'px';
            } else {
                // 找不到就放右下角
                this._returnButtonContainer.style.left = '';
                this._returnButtonContainer.style.top = '';
                this._returnButtonContainer.style.right = '16px';
                this._returnButtonContainer.style.bottom = '116px';
            }
            this._returnButtonContainer.style.display = 'flex';
        }
    });

    // 监听 "请她回来" 事件 (由 app.js 或 vrm 自身触发)
    const handleReturn = () => {
        console.log('[VRM] 收到回来信号，恢复 UI');
        
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
    // --- 4. 创建"请她回来"按钮 (保持原有逻辑) ---
    const returnButtonContainer = document.createElement('div');
    returnButtonContainer.id = 'vrm-return-button-container';
    Object.assign(returnButtonContainer.style, {
        position: 'fixed', top: '0', left: '0', transform: 'none', zIndex: '99999',
        pointerEvents: 'auto', display: 'none'
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

    // --- 5. 锁图标处理 ---
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
        this.interaction.isLocked = !this.interaction.isLocked;
        lockIcon.style.backgroundImage = this.interaction.isLocked ? 'url(/static/icons/locked_icon.png)' : 'url(/static/icons/unlocked_icon.png)';
        lockIcon.style.transform = 'scale(0.9)';
        setTimeout(() => lockIcon.style.transform = 'scale(1)', 100);
        const vrmCanvas = document.getElementById('vrm-canvas');
        if (vrmCanvas) vrmCanvas.style.pointerEvents = this.interaction.isLocked ? 'none' : 'auto';
        lockIcon.style.display = 'block';
    };

    lockIcon.addEventListener('mousedown', toggleLock);
    lockIcon.addEventListener('touchstart', toggleLock, {passive:false});

    // 启动更新循环
    this._startUIUpdateLoop();
    
    // 通知外部浮动按钮已就绪
    window.dispatchEvent(new CustomEvent('live2d-floating-buttons-ready'));
};

// 循环更新位置 (保持跟随)
VRMManager.prototype._startUIUpdateLoop = function() {
    // 确保 isMobileWidth 可用
    const isMobileWidth = () => window.innerWidth <= 768;

    const update = () => {
        if (!this.currentModel || !this.currentModel.vrm) {
            requestAnimationFrame(update);
            return;
        }

        // 🔥【关键修复】移动端跳过位置更新，使用 CSS 固定定位
        if (isMobileWidth()) {
            requestAnimationFrame(update);
            return;
        }
        
        const buttonsContainer = document.getElementById('vrm-floating-buttons')
        const lockIcon = this._vrmLockIcon;
        
        let headNode = null;
        if (this.currentModel.vrm.humanoid) {
            headNode = this.currentModel.vrm.humanoid.getNormalizedBoneNode('head');
            if (!headNode) headNode = this.currentModel.vrm.humanoid.getNormalizedBoneNode('neck');
        }
        if (!headNode) headNode = this.currentModel.scene;

        if (headNode && this.camera) {
            headNode.updateWorldMatrix(true, false);
            const vec = new window.THREE.Vector3();
            vec.setFromMatrixPosition(headNode.matrixWorld);

            const width = window.innerWidth;
            const height = window.innerHeight;

            // 更新按钮位置
            if (buttonsContainer) {
                const btnPos = vec.clone();
                btnPos.x += 0.35; btnPos.y += 0.1;
                btnPos.project(this.camera);
                const screenX = (btnPos.x * 0.5 + 0.5) * width;
                const screenY = (-(btnPos.y * 0.5) + 0.5) * height;
                buttonsContainer.style.left = `${screenX}px`;
                buttonsContainer.style.top = `${screenY - 100}px`;
                buttonsContainer.style.display = 'flex'; 
            }

            // 更新锁位置
            if (lockIcon) {
                const lockPos = vec.clone();
                lockPos.x += 0.1; lockPos.y -= 0.55; 
                lockPos.project(this.camera);
                const lX = (lockPos.x * 0.5 + 0.5) * width;
                const lY = (-(lockPos.y * 0.5) + 0.5) * height;
                lockIcon.style.left = `${lX}px`;
                lockIcon.style.top = `${lY}px`;
                lockIcon.style.display = 'block';
            }
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
        containerStartX = parseInt(returnButtonContainer.style.left) || 0;
        containerStartY = parseInt(returnButtonContainer.style.top) || 0;
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
    document.addEventListener('mousemove', (e) => handleMove(e.clientX, e.clientY));
    document.addEventListener('mouseup', handleEnd);
    
    returnButtonContainer.addEventListener('touchstart', (e) => {
        if (e.target === returnButtonContainer || e.target.classList.contains('vrm-return-btn')) {
            e.preventDefault(); const touch = e.touches[0]; handleStart(touch.clientX, touch.clientY);
        }
    });
    document.addEventListener('touchmove', (e) => {
        if(isDragging) { e.preventDefault(); const touch = e.touches[0]; handleMove(touch.clientX, touch.clientY); }
    }, {passive: false});
    document.addEventListener('touchend', handleEnd);
    returnButtonContainer.style.cursor = 'grab';
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
    if (window.lanlan_config) window.lanlan_config.vrm_model = null;
    this._vrmLockIcon = null;
    this._floatingButtons = null;
    this._returnButtonContainer = null;
};