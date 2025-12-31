/**
 * VRM UI Buttons - VRM 专用浮动按钮
 */

// 设置浮动按钮系统
VRMManager.prototype.setupFloatingButtons = function () {
    const container = document.getElementById('vrm-container');

    // 强力清除旧势力的残党
    document.querySelectorAll('#live2d-floating-buttons').forEach(el => el.remove());
    
    
    // 1. 改这里：给他一个全新的名字，不再和旧代码打架
    const buttonsContainerId = 'vrm-floating-buttons'; 

    // 清理逻辑（防止热重载堆积）
    const old = document.getElementById(buttonsContainerId);
    if (old) old.remove();

    const buttonsContainer = document.createElement('div');
    buttonsContainer.id = buttonsContainerId; // <--- 使用新 ID
    document.body.appendChild(buttonsContainer);
    

    // 设置样式
    Object.assign(buttonsContainer.style, {
        position: 'fixed', zIndex: '99999', pointerEvents: 'none',
        display: 'flex', flexDirection: 'column', gap: '12px',
        visibility: 'visible', opacity: '1', transform: 'none'
    });
    this._floatingButtonsContainer = buttonsContainer;

    // 2. 按钮配置 (和 Live2D 保持一致)
    const iconVersion = '?v=' + Date.now();
    const buttonConfigs = [
        { id: 'mic', emoji: '🎤', hasPopup: true, toggle: true, separatePopupTrigger: true, iconOff: '/static/icons/mic_icon_off.png'+iconVersion, iconOn: '/static/icons/mic_icon_on.png'+iconVersion },
        { id: 'screen', emoji: '🖥️', toggle: true, iconOff: '/static/icons/screen_icon_off.png'+iconVersion, iconOn: '/static/icons/screen_icon_on.png'+iconVersion },
        { id: 'agent', emoji: '🔨', popupToggle: true, iconOff: '/static/icons/Agent_off.png'+iconVersion, iconOn: '/static/icons/Agent_on.png'+iconVersion },
        { id: 'settings', emoji: '⚙️', popupToggle: true, iconOff: '/static/icons/set_off.png'+iconVersion, iconOn: '/static/icons/set_on.png'+iconVersion },
        { id: 'goodbye', emoji: '💤', iconOff: '/static/icons/rest_off.png'+iconVersion, iconOn: '/static/icons/rest_on.png'+iconVersion }
    ];

    // 3. 创建按钮
    buttonConfigs.forEach(config => {
        const btnWrapper = document.createElement('div');
        Object.assign(btnWrapper.style, {
            position: 'relative',
            display: 'flex',
            alignItems: 'center',
            gap: '8px',  // ✅ 添加gap，与Live2D保持一致
            pointerEvents: 'auto'
        });
        
        // 这里的事件监听是为了防止点击穿透到模型
        ['pointerdown','mousedown','touchstart'].forEach(evt => 
            btnWrapper.addEventListener(evt, e => e.stopPropagation(), false)
        );

        const btn = document.createElement('div');
        btn.id = `vrm-btn-${config.id}`;
        btn.className = 'vrm-floating-btn';

        Object.assign(btn.style, {
            width: '48px',
            height: '48px',
            borderRadius: '50%',
            background: 'rgba(255, 255, 255, 0.65)',  // Fluent Design Acrylic
            backdropFilter: 'saturate(180%) blur(20px)',  // Fluent 标准模糊
            border: '1px solid rgba(255, 255, 255, 0.18)',  // 微妙高光边框
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: '24px',
            cursor: 'pointer',
            userSelect: 'none',
            boxShadow: '0 2px 4px rgba(0, 0, 0, 0.04), 0 4px 8px rgba(0, 0, 0, 0.08)',  // Fluent 多层阴影
            transition: 'all 0.1s ease',  // Fluent 快速响应
            pointerEvents: 'auto'
        });

        let imgOff = null;
        let imgOn = null;

        // 图标处理 - 与Live2D完全一致
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
            imgOff.alt = config.emoji;
            Object.assign(imgOff.style, {
                position: 'absolute',
                width: '48px',
                height: '48px',
                objectFit: 'contain',
                pointerEvents: 'none',
                opacity: '1',
                transition: 'opacity 0.3s ease'
            });

            // 创建on状态图片（默认隐藏）
            imgOn = document.createElement('img');
            imgOn.src = config.iconOn;
            imgOn.alt = config.emoji;
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

            // 鼠标悬停效果 - Fluent Design
            btn.addEventListener('mouseenter', () => {
                btn.style.transform = 'scale(1.05)';  // 更微妙的缩放
                btn.style.boxShadow = '0 4px 8px rgba(0, 0, 0, 0.08), 0 8px 16px rgba(0, 0, 0, 0.08)';
                btn.style.background = 'rgba(255, 255, 255, 0.8)';  // 悬停时更亮
                // 淡出off图标，淡入on图标
                if (imgOff && imgOn) {
                    imgOff.style.opacity = '0';
                    imgOn.style.opacity = '1';
                }
            });
            btn.addEventListener('mouseleave', () => {
                btn.style.transform = 'scale(1)';
                btn.style.boxShadow = '0 2px 4px rgba(0, 0, 0, 0.04), 0 4px 8px rgba(0, 0, 0, 0.08)';
                // 恢复原始背景色（根据按钮状态）
                const isActive = btn.dataset.active === 'true';

                if (isActive) {
                    // 激活状态：稍亮的背景
                    btn.style.background = 'rgba(255, 255, 255, 0.75)';
                } else {
                    btn.style.background = 'rgba(255, 255, 255, 0.65)';  // Fluent Acrylic
                }

                // 根据按钮激活状态决定显示哪个图标
                if (imgOff && imgOn) {
                    if (isActive) {
                        // 激活状态：保持on图标
                        imgOff.style.opacity = '0';
                        imgOn.style.opacity = '1';
                    } else {
                        // 未激活状态：显示off图标
                        imgOff.style.opacity = '1';
                        imgOn.style.opacity = '0';
                    }
                }
            });

            // ✅ 使用新架构：通过 UIController 统一管理面板
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                e.preventDefault();

                const currentActive = btn.dataset.active === 'true';
                let targetActive = !currentActive; // 默认取反

                // ✅ 使用 UIController 统一接口（底层逻辑互通）
                if (config.id === 'settings') {
                    if (window.UIController) {
                        targetActive = window.UIController.toggleSettings();
                    } else {
                        console.error('[VRM UI] UIController 未加载');
                    }
                }
                else if (config.id === 'agent') {
                    if (window.UIController) {
                        targetActive = window.UIController.toggleAgent();
                    } else {
                        console.error('[VRM UI] UIController 未加载');
                    }
                }
                else if (config.id === 'mic') {
                    if (window.UIController) {
                        targetActive = window.UIController.toggleMic(targetActive);
                    }
                }
                else if (config.id === 'screen') {
                    if (window.UIController) {
                        targetActive = window.UIController.toggleScreen(targetActive);
                    }
                }
                else if (config.id === 'goodbye') {
                    // 触发VRM休息模式事件
                    window.dispatchEvent(new CustomEvent('vrm-goodbye-click'));
                    return; // goodbye按钮不需要更新激活状态
                }

                // 更新图标状态
                btn.dataset.active = targetActive.toString();
                imgOff.style.opacity = targetActive ? '0' : '1';
                imgOn.style.opacity = targetActive ? '1' : '0';

                // 保持原有的事件发送（向后兼容）
                if(config.toggle) {
                    window.dispatchEvent(new CustomEvent(`live2d-${config.id}-toggle`, {detail:{active:targetActive}}));
                } else {
                    window.dispatchEvent(new CustomEvent(`live2d-${config.id}-click`));
                }
            });
        }

        // 先添加按钮到包装器
        btnWrapper.appendChild(btn);

        // ✅ 如果有弹出框且需要独立的触发器（仅麦克风）
        if (config.hasPopup && config.separatePopupTrigger && window.UIComponentFactory) {
            const popup = window.UIComponentFactory.createPopup(config.id, this);

            // 创建三角按钮（用于触发弹出框）
            const triggerBtn = document.createElement('div');
            triggerBtn.innerText = '▶';
            Object.assign(triggerBtn.style, {
                width: '24px',
                height: '24px',
                borderRadius: '50%',
                background: 'rgba(255, 255, 255, 0.65)',
                backdropFilter: 'saturate(180%) blur(20px)',
                border: '1px solid rgba(255, 255, 255, 0.18)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: '13px',
                color: '#44b7fe',
                cursor: 'pointer',
                userSelect: 'none',
                boxShadow: '0 2px 4px rgba(0, 0, 0, 0.04), 0 4px 8px rgba(0, 0, 0, 0.08)',
                transition: 'all 0.1s ease',
                pointerEvents: 'auto'
                // ✅ 移除marginLeft，使用btnWrapper的gap
            });

            // 阻止事件传播
            ['pointerdown','pointermove','pointerup','mousedown','mousemove','mouseup','touchstart','touchmove','touchend'].forEach(evt =>
                triggerBtn.addEventListener(evt, e => e.stopPropagation(), true)
            );

            // 悬停效果
            triggerBtn.addEventListener('mouseenter', () => {
                triggerBtn.style.transform = 'scale(1.05)';
                triggerBtn.style.boxShadow = '0 4px 8px rgba(0, 0, 0, 0.08), 0 8px 16px rgba(0, 0, 0, 0.08)';
                triggerBtn.style.background = 'rgba(255, 255, 255, 0.8)';
            });
            triggerBtn.addEventListener('mouseleave', () => {
                triggerBtn.style.transform = 'scale(1)';
                triggerBtn.style.boxShadow = '0 2px 4px rgba(0, 0, 0, 0.04), 0 4px 8px rgba(0, 0, 0, 0.08)';
                triggerBtn.style.background = 'rgba(255, 255, 255, 0.65)';
            });

            // 点击打开麦克风列表
            triggerBtn.addEventListener('click', async (e) => {
                e.stopPropagation();

                // 如果是麦克风弹出框，先加载麦克风列表
                if (config.id === 'mic' && window.renderFloatingMicList) {
                    await window.renderFloatingMicList();
                }

                // 使用 UIController 显示弹出框
                if (window.UIController) {
                    window.UIController.showPopup(config.id, popup);
                }
            });

            // 创建包装器用于三角按钮和弹出框
            const triggerWrapper = document.createElement('div');
            triggerWrapper.style.position = 'relative';

            // 阻止包装器事件传播
            ['pointerdown','pointermove','pointerup','mousedown','mousemove','mouseup','touchstart','touchmove','touchend'].forEach(evt =>
                triggerWrapper.addEventListener(evt, e => e.stopPropagation(), true)
            );

            triggerWrapper.appendChild(triggerBtn);
            triggerWrapper.appendChild(popup);
            btnWrapper.appendChild(triggerWrapper);

            
        }
        // ✅ 如果配置了 popupToggle，创建弹出面板
        else if (config.popupToggle && window.UIComponentFactory) {
            const popup = window.UIComponentFactory.createPopup(config.id, this);
            btnWrapper.appendChild(popup);
            
        }

        // 将包装器添加到容器
        buttonsContainer.appendChild(btnWrapper);
    });

    window.dispatchEvent(new CustomEvent('live2d-floating-buttons-ready'));

    // --- 3.5. 创建"请她回来"按钮（用于休息模式）---
    const returnButtonContainer = document.createElement('div');
    returnButtonContainer.id = 'vrm-return-button-container';
    Object.assign(returnButtonContainer.style, {
        position: 'fixed',
        top: '0',
        left: '0',
        transform: 'none',
        zIndex: '99999',
        pointerEvents: 'auto',
        display: 'none' // 初始隐藏
    });

    const returnBtn = document.createElement('div');
    returnBtn.id = 'vrm-btn-return';
    returnBtn.className = 'vrm-return-btn';

    // 使用与goodbye相同的图标
    const returnImgOff = document.createElement('img');
    returnImgOff.src = '/static/icons/rest_off.png' + iconVersion;
    returnImgOff.alt = '💤';
    Object.assign(returnImgOff.style, {
        width: '64px',
        height: '64px',
        objectFit: 'contain',
        pointerEvents: 'none',
        opacity: '1',
        transition: 'opacity 0.3s ease'
    });

    const returnImgOn = document.createElement('img');
    returnImgOn.src = '/static/icons/rest_on.png' + iconVersion;
    returnImgOn.alt = '💤';
    Object.assign(returnImgOn.style, {
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
        background: 'rgba(255, 255, 255, 0.65)',
        backdropFilter: 'saturate(180%) blur(20px)',
        border: '1px solid rgba(255, 255, 255, 0.18)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        cursor: 'pointer',
        userSelect: 'none',
        boxShadow: '0 2px 4px rgba(0, 0, 0, 0.04), 0 8px 16px rgba(0, 0, 0, 0.08), 0 16px 32px rgba(0, 0, 0, 0.04)',
        transition: 'all 0.1s ease',
        pointerEvents: 'auto',
        position: 'relative'
    });

    // 悬停效果
    returnBtn.addEventListener('mouseenter', () => {
        returnBtn.style.transform = 'scale(1.05)';
        returnBtn.style.boxShadow = '0 4px 8px rgba(0, 0, 0, 0.08), 0 16px 32px rgba(0, 0, 0, 0.08)';
        returnBtn.style.background = 'rgba(255, 255, 255, 0.8)';
        returnImgOff.style.opacity = '0';
        returnImgOn.style.opacity = '1';
    });

    returnBtn.addEventListener('mouseleave', () => {
        returnBtn.style.transform = 'scale(1)';
        returnBtn.style.boxShadow = '0 2px 4px rgba(0, 0, 0, 0.04), 0 8px 16px rgba(0, 0, 0, 0.08), 0 16px 32px rgba(0, 0, 0, 0.04)';
        returnBtn.style.background = 'rgba(255, 255, 255, 0.65)';
        returnImgOff.style.opacity = '1';
        returnImgOn.style.opacity = '0';
    });

    returnBtn.addEventListener('click', (e) => {
        // 检查是否处于拖拽状态，如果是拖拽操作则阻止点击
        if (returnButtonContainer.getAttribute('data-dragging') === 'true') {
            e.preventDefault();
            e.stopPropagation();
            return;
        }

        e.stopPropagation();
        e.preventDefault();
        window.dispatchEvent(new CustomEvent('vrm-return-click'));
    });

    returnBtn.appendChild(returnImgOff);
    returnBtn.appendChild(returnImgOn);
    returnButtonContainer.appendChild(returnBtn);
    document.body.appendChild(returnButtonContainer);

    this._returnButtonContainer = returnButtonContainer;

    // 为"请她回来"按钮添加拖动功能
    this.setupVRMReturnButtonDrag(returnButtonContainer);

    // --- 4. 锁图标处理

    // 只删除VRM的锁（不删除Live2D的锁）
    document.querySelectorAll('#vrm-lock-icon').forEach(el => el.remove());

    const lockIcon = document.createElement('div');
    lockIcon.id = 'vrm-lock-icon';
    // 给个标记，Live2D脚本看到了就会自己退出
    lockIcon.dataset.vrmLock = 'true'; 
    document.body.appendChild(lockIcon);
    this._vrmLockIcon = lockIcon;

    // 【修改点】加大尺寸到 44px，更容易点
    Object.assign(lockIcon.style, {
        position: 'fixed', zIndex: '99999',
        width: '44px', height: '44px',
        cursor: 'pointer', display: 'none',  // 【修改】初始隐藏，鼠标靠近时显示
        backgroundImage: 'url(/static/icons/unlocked_icon.png)',
        backgroundSize: 'contain', backgroundRepeat: 'no-repeat', backgroundPosition: 'center',
        pointerEvents: 'auto', transition: 'transform 0.1s'
    });

    // 【修改点】点击锁的逻辑 - 必须控制 pointerEvents
    const toggleLock = (e) => {
        if(e) { e.preventDefault(); e.stopPropagation(); }

        this.interaction.isLocked = !this.interaction.isLocked;

        // 换图
        lockIcon.style.backgroundImage = this.interaction.isLocked ?
            'url(/static/icons/locked_icon.png)' : 'url(/static/icons/unlocked_icon.png)';

        // 点击反馈
        lockIcon.style.transform = 'scale(0.9)';
        setTimeout(() => lockIcon.style.transform = 'scale(1)', 100);

        // 【关键】控制 Canvas 能否穿透
        const vrmCanvas = document.getElementById('vrm-canvas');
        if (vrmCanvas) {
            // 锁住 = none (鼠标穿透，点不到模型，所以动不了)
            // 解锁 = auto (鼠标能点到模型，可以拖动)
            vrmCanvas.style.pointerEvents = this.interaction.isLocked ? 'none' : 'auto';
        }

        // 【新增】点击后保持锁图标显示一段时间，避免立即隐藏导致无法连续点击
        lockIcon.style.display = 'block';
        lockIcon.dataset.clickProtection = 'true';
        setTimeout(() => {
            delete lockIcon.dataset.clickProtection;
        }, 500);
    };

    // 【改进】使用 mousedown 代替 click，更灵敏
    lockIcon.addEventListener('mousedown', toggleLock);
    lockIcon.addEventListener('touchstart', toggleLock, {passive:false});

    // 启动循环更新位置
    this._startUIUpdateLoop();
};

// 循环更新位置 (保持跟随)
VRMManager.prototype._startUIUpdateLoop = function() {
    const update = () => {
        if (!this.currentModel || !this.currentModel.vrm) {
            requestAnimationFrame(update);
            return;
        }
        
        const buttonsContainer = document.getElementById('vrm-floating-buttons')
        const lockIcon = this._vrmLockIcon;
        
        // 找头
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
                buttonsContainer.style.display = 'flex';  // 浮动按钮始终显示
            }

            // 更新锁位置
            if (lockIcon) {
                const lockPos = vec.clone();
                lockPos.x += 0.1; lockPos.y -= 0.55;  // 右侧偏下（脚部右侧）
                lockPos.project(this.camera);
                const lX = (lockPos.x * 0.5 + 0.5) * width;
                const lY = (-(lockPos.y * 0.5) + 0.5) * height;
                lockIcon.style.left = `${lX}px`;
                lockIcon.style.top = `${lY}px`;
                // 【保留注释】不强制显示锁，由鼠标跟踪控制
                // lockIcon.style.display = 'block';
            }
        }
        requestAnimationFrame(update);
    };
    requestAnimationFrame(update);
};

// 为VRM的"请她回来"按钮设置拖动功能
VRMManager.prototype.setupVRMReturnButtonDrag = function (returnButtonContainer) {
    let isDragging = false;
    let dragStartX = 0;
    let dragStartY = 0;
    let containerStartX = 0;
    let containerStartY = 0;
    let isClick = false;

    // 鼠标按下事件
    returnButtonContainer.addEventListener('mousedown', (e) => {
        if (e.target === returnButtonContainer || e.target.classList.contains('vrm-return-btn')) {
            isDragging = true;
            isClick = true;
            dragStartX = e.clientX;
            dragStartY = e.clientY;

            const currentLeft = parseInt(returnButtonContainer.style.left) || 0;
            const currentTop = parseInt(returnButtonContainer.style.top) || 0;
            containerStartX = currentLeft;
            containerStartY = currentTop;

            returnButtonContainer.setAttribute('data-dragging', 'false');
            returnButtonContainer.style.cursor = 'grabbing';
            e.preventDefault();
        }
    });

    // 鼠标移动事件
    document.addEventListener('mousemove', (e) => {
        if (isDragging) {
            const deltaX = e.clientX - dragStartX;
            const deltaY = e.clientY - dragStartY;

            const dragThreshold = 5;
            if (Math.abs(deltaX) > dragThreshold || Math.abs(deltaY) > dragThreshold) {
                isClick = false;
                returnButtonContainer.setAttribute('data-dragging', 'true');
            }

            const newX = containerStartX + deltaX;
            const newY = containerStartY + deltaY;

            // 边界检查
            const containerWidth = returnButtonContainer.offsetWidth || 64;
            const containerHeight = returnButtonContainer.offsetHeight || 64;

            const boundedX = Math.max(0, Math.min(newX, window.innerWidth - containerWidth));
            const boundedY = Math.max(0, Math.min(newY, window.innerHeight - containerHeight));

            returnButtonContainer.style.left = `${boundedX}px`;
            returnButtonContainer.style.top = `${boundedY}px`;
        }
    });

    // 鼠标释放事件
    document.addEventListener('mouseup', (e) => {
        if (isDragging) {
            setTimeout(() => {
                returnButtonContainer.setAttribute('data-dragging', 'false');
            }, 10);

            isDragging = false;
            isClick = false;
            returnButtonContainer.style.cursor = 'grab';
        }
    });

    // 设置初始鼠标样式
    returnButtonContainer.style.cursor = 'grab';

    // 触摸事件支持
    returnButtonContainer.addEventListener('touchstart', (e) => {
        if (e.target === returnButtonContainer || e.target.classList.contains('vrm-return-btn')) {
            isDragging = true;
            isClick = true;
            const touch = e.touches[0];
            dragStartX = touch.clientX;
            dragStartY = touch.clientY;

            const currentLeft = parseInt(returnButtonContainer.style.left) || 0;
            const currentTop = parseInt(returnButtonContainer.style.top) || 0;
            containerStartX = currentLeft;
            containerStartY = currentTop;

            returnButtonContainer.setAttribute('data-dragging', 'false');
            e.preventDefault();
        }
    });

    document.addEventListener('touchmove', (e) => {
        if (isDragging) {
            const touch = e.touches[0];
            const deltaX = touch.clientX - dragStartX;
            const deltaY = touch.clientY - dragStartY;

            const dragThreshold = 5;
            if (Math.abs(deltaX) > dragThreshold || Math.abs(deltaY) > dragThreshold) {
                isClick = false;
                returnButtonContainer.setAttribute('data-dragging', 'true');
            }

            const newX = containerStartX + deltaX;
            const newY = containerStartY + deltaY;

            // 边界检查
            const containerWidth = returnButtonContainer.offsetWidth || 64;
            const containerHeight = returnButtonContainer.offsetHeight || 64;

            const boundedX = Math.max(0, Math.min(newX, window.innerWidth - containerWidth));
            const boundedY = Math.max(0, Math.min(newY, window.innerHeight - containerHeight));

            returnButtonContainer.style.left = `${boundedX}px`;
            returnButtonContainer.style.top = `${boundedY}px`;
            e.preventDefault();
        }
    });

    document.addEventListener('touchend', (e) => {
        if (isDragging) {
            setTimeout(() => {
                returnButtonContainer.setAttribute('data-dragging', 'false');
            }, 10);

            isDragging = false;
            isClick = false;
        }
    });
};

/**
 * 清理VRM UI元素（切换到Live2D时调用）
 */
VRMManager.prototype.cleanupUI = function() {
    // 移除VRM浮动按钮
    const vrmButtons = document.getElementById('vrm-floating-buttons');
    if (vrmButtons) {
        vrmButtons.remove();
    }

    // 移除VRM锁图标
    document.querySelectorAll('#vrm-lock-icon').forEach(el => el.remove());

    // 移除VRM返回按钮
    const vrmReturnBtn = document.getElementById('vrm-return-button-container');
    if (vrmReturnBtn) {
        vrmReturnBtn.remove();
    }

    // 清除VRM模式标记，让Live2D锁图标可以正常创建
    if (window.lanlan_config) {
        window.lanlan_config.vrm_model = null;
    }

    // 清理引用
    this._vrmLockIcon = null;
    this._vrmFloatingButtons = null;
    this._returnButtonContainer = null;
};