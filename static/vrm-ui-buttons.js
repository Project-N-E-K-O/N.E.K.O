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
        { id: 'mic', emoji: '🎤', toggle: true, iconOff: '/static/icons/mic_icon_off.png'+iconVersion, iconOn: '/static/icons/mic_icon_on.png'+iconVersion },
        { id: 'screen', emoji: '🖥️', toggle: true, iconOff: '/static/icons/screen_icon_off.png'+iconVersion, iconOn: '/static/icons/screen_icon_on.png'+iconVersion },
        { id: 'agent', emoji: '🔨', popupToggle: true, iconOff: '/static/icons/Agent_off.png'+iconVersion, iconOn: '/static/icons/Agent_on.png'+iconVersion },
        { id: 'settings', emoji: '⚙️', popupToggle: true, iconOff: '/static/icons/set_off.png'+iconVersion, iconOn: '/static/icons/set_on.png'+iconVersion },
        { id: 'goodbye', emoji: '💤', iconOff: '/static/icons/rest_off.png'+iconVersion, iconOn: '/static/icons/rest_on.png'+iconVersion }
    ];

    // 3. 创建按钮
    buttonConfigs.forEach(config => {
        const btnWrapper = document.createElement('div');
        Object.assign(btnWrapper.style, { position: 'relative', display: 'flex', alignItems: 'center', pointerEvents: 'auto' });
        
        // 这里的事件监听是为了防止点击穿透到模型
        ['pointerdown','mousedown','touchstart'].forEach(evt => 
            btnWrapper.addEventListener(evt, e => e.stopPropagation(), false)
        );

        const btn = document.createElement('div');
        btn.id = `live2d-btn-${config.id}`;
        btn.className = 'live2d-floating-btn';
        
        Object.assign(btn.style, {
            width: '48px', height: '48px', borderRadius: '50%',
            background: 'rgba(255,255,255,0.65)', border: '1px solid rgba(255,255,255,0.2)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            cursor: 'pointer', pointerEvents: 'auto', boxShadow: '0 2px 4px rgba(0,0,0,0.1)'
        });

        // 图标处理
        if (config.iconOff && config.iconOn) {
            const imgOff = document.createElement('img'); imgOff.src = config.iconOff;
            Object.assign(imgOff.style, {width:'100%', height:'100%', position:'absolute', transition:'opacity 0.3s'});
            const imgOn = document.createElement('img'); imgOn.src = config.iconOn;
            Object.assign(imgOn.style, {width:'100%', height:'100%', position:'absolute', opacity:'0', transition:'opacity 0.3s'});
            btn.appendChild(imgOff); btn.appendChild(imgOn);

            btn.addEventListener('click', (e) => {
                e.stopPropagation(); 
                e.preventDefault();

                const isActive = btn.dataset.active === 'true';
                btn.dataset.active = (!isActive).toString();
                imgOff.style.opacity = !isActive ? '0' : '1';
                imgOn.style.opacity = !isActive ? '1' : '0';
                
                console.log(`[VRM UI] 点击了按钮: ${config.id}, 激活状态: ${!isActive}`); // 加个日志方便调试

                if(config.toggle) window.dispatchEvent(new CustomEvent(`live2d-${config.id}-toggle`, {detail:{active:!isActive}}));
                else window.dispatchEvent(new CustomEvent(`live2d-${config.id}-click`));
            });
        }
        btnWrapper.appendChild(btn);
        buttonsContainer.appendChild(btnWrapper);
    });

    console.log('[VRM UI] 浮动按钮创建完成');
    window.dispatchEvent(new CustomEvent('live2d-floating-buttons-ready'));

    // --- 4. 锁图标处理 
    
    // 先删掉所有已存在的锁，不管是 Live2D 的还是 VRM 的
    document.querySelectorAll('#live2d-lock-icon').forEach(el => el.remove());
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
        cursor: 'pointer', display: 'block', 
        backgroundImage: 'url(/static/icons/unlocked_icon.png)',
        backgroundSize: 'contain', backgroundRepeat: 'no-repeat', backgroundPosition: 'center',
        pointerEvents: 'auto', transition: 'transform 0.1s'
    });

    // 【修改点】点击锁的逻辑 - 必须控制 pointerEvents
    const toggleLock = (e) => {
        if(e) { e.preventDefault(); e.stopPropagation(); }
        
        this.interaction.isLocked = !this.interaction.isLocked;
        console.log('[VRM UI] 锁状态:', this.interaction.isLocked);
        
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
    };

    // 使用 touchstart 提高移动端灵敏度
    lockIcon.addEventListener('click', toggleLock);
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
                buttonsContainer.style.display = 'flex'; 
            }

            // 更新锁位置
            if (lockIcon) {
                const lockPos = vec.clone();
                lockPos.x += 0.35; lockPos.y -= 0.8; 
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