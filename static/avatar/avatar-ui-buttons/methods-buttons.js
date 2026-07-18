Object.assign(AvatarButtonMixin.methods, {
    buttons(ManagerPrototype, prefix, options) {
        ManagerPrototype.getDefaultButtonConfigs = function() {
            const iconVersion = window.APP_VERSION ? `?v=${window.APP_VERSION}` : `?v=${Date.now()}`;
            return [
                {
                    id: 'mic',
                    emoji: '🎤',
                    title: window.t ? window.t('buttons.voiceControl') : '语音控制',
                    titleKey: 'buttons.voiceControl',
                    hasPopup: true,
                    toggle: true,
                    separatePopupTrigger: true,
                    iconOff: `/static/icons/mic_icon_off.png${iconVersion}`,
                    iconOn: `/static/icons/mic_icon_on.png${iconVersion}`
                },
                {
                    id: 'agent',
                    emoji: '🔨',
                    title: window.t ? window.t('buttons.agentTools') : 'Agent工具',
                    titleKey: 'buttons.agentTools',
                    hasPopup: true,
                    popupToggle: true,
                    exclusive: 'settings',
                    iconOff: `/static/icons/Agent_off.png${iconVersion}`,
                    iconOn: `/static/icons/Agent_on.png${iconVersion}`
                },
                {
                    // N.E.K.O.Servers 社交平台入口（替代原 screen 槽位）。
                    // 屏幕分享不再暴露独立按钮，改为跟随语音控制按钮启停。
                    id: 'social',
                    title: window.t ? window.t('buttons.social') : '猫娘社区',
                    titleKey: 'buttons.social',
                    hasPopup: false,
                    iconOff: `/static/icons/neko_community_off.png${iconVersion}`,
                    iconOn: `/static/icons/neko_community_on.png${iconVersion}`,
                    imageRendering: 'auto'
                },
                {
                    id: 'settings',
                    emoji: '⚙️',
                    title: window.t ? window.t('buttons.settings') : '设置',
                    titleKey: 'buttons.settings',
                    hasPopup: true,
                    popupToggle: true,
                    exclusive: 'agent',
                    iconOff: `/static/icons/set_off.png${iconVersion}`,
                    iconOn: `/static/icons/set_on.png${iconVersion}`
                },
                {
                    id: 'goodbye',
                    emoji: '💤',
                    title: window.t ? window.t('buttons.leave') : '请她离开',
                    titleKey: 'buttons.leave',
                    hasPopup: false,
                    iconOff: `/static/icons/rest_off.png${iconVersion}`,
                    iconOn: `/static/icons/rest_on.png${iconVersion}`
                }
            ];
        };

        /**
         * 创建单个按钮及其包装器
         */
        ManagerPrototype.createButtonElement = function(config, buttonsContainer, index) {
            const opts = this._avatarButtonOptions;
            const prefix = this._avatarPrefix;

            // 创建包装器
            const btnWrapper = document.createElement('div');
            Object.assign(btnWrapper.style, {
                position: 'relative',
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                pointerEvents: 'auto',
                height: '48px',
                minHeight: '48px',
                flex: '0 0 48px',
                boxSizing: 'border-box'
            });

            const stopWrapperEvent = (e) => { e.stopPropagation(); };
            ['pointerdown', 'pointermove', 'pointerup', 'mousedown', 'mousemove', 'mouseup', 'touchstart', 'touchmove', 'touchend'].forEach(evt => {
                btnWrapper.addEventListener(evt, stopWrapperEvent);
            });

            // 创建按钮
            const btn = document.createElement('div');
            btn.id = `${prefix}-btn-${config.id}`;
            btn.className = opts.buttonClassPrefix;
            btn.title = config.title;
            if (config.titleKey) {
                btn.setAttribute('data-i18n-title', config.titleKey);
            }

            let imgOff = null;
            let imgOn = null;

            // 创建按钮内容（图片或 emoji）
            if (config.iconOff && config.iconOn) {
                const imgContainer = document.createElement('div');
                Object.assign(imgContainer.style, {
                    position: 'relative',
                    width: '48px',
                    height: '48px',
                    boxSizing: 'border-box',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center'
                });

                imgOff = document.createElement('img');
                imgOff.src = config.iconOff;
                imgOff.alt = config.title;
                Object.assign(imgOff.style, {
                    position: 'absolute',
                    left: '50%',
                    top: '50%',
                    width: '48px',
                    height: '48px',
                    objectFit: 'contain',
                    display: 'block',
                    pointerEvents: 'none',
                    opacity: '0.75',
                    transition: 'opacity 0.3s ease',
                    transform: 'translate(-50%, -50%)',
                    transformOrigin: 'center center',
                    imageRendering: config.imageRendering || 'crisp-edges'
                });

                imgOn = document.createElement('img');
                imgOn.src = config.iconOn;
                imgOn.alt = config.title;
                Object.assign(imgOn.style, {
                    position: 'absolute',
                    left: '50%',
                    top: '50%',
                    width: '48px',
                    height: '48px',
                    objectFit: 'contain',
                    display: 'block',
                    pointerEvents: 'none',
                    opacity: '0',
                    transition: 'opacity 0.3s ease',
                    transform: 'translate(-50%, -50%)',
                    transformOrigin: 'center center',
                    imageRendering: config.imageRendering || 'crisp-edges'
                });

                imgContainer.appendChild(imgOff);
                imgContainer.appendChild(imgOn);
                btn.appendChild(imgContainer);
            } else if (config.emoji) {
                btn.innerText = config.emoji;
            }

            // 按钮样式
            Object.assign(btn.style, {
                width: '48px',
                height: '48px',
                boxSizing: 'border-box',
                borderRadius: '50%',
                background: 'var(--neko-btn-bg, rgba(255, 255, 255, 0.65))',
                backdropFilter: 'saturate(180%) blur(20px)',
                border: 'var(--neko-btn-border, 1px solid rgba(255, 255, 255, 0.18))',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: '24px',
                cursor: 'pointer',
                userSelect: 'none',
                boxShadow: 'var(--neko-btn-shadow, 0 2px 4px rgba(0,0,0,0.04), 0 4px 8px rgba(0,0,0,0.08))',
                transition: 'all 0.1s ease',
                pointerEvents: 'auto'
            });

            // 阻止按钮上的指针事件传播
            const stopBtnEvent = (e) => { e.stopPropagation(); };
            ['pointerdown', 'pointermove', 'pointerup', 'mousedown', 'mousemove', 'mouseup', 'touchstart', 'touchmove', 'touchend'].forEach(evt => {
                btn.addEventListener(evt, stopBtnEvent);
            });

            // 悬停效果
            btn.addEventListener('mouseenter', () => {
                btn.style.transform = 'scale(1.05)';
                btn.style.boxShadow = 'var(--neko-btn-shadow-hover, 0 4px 8px rgba(0,0,0,0.08), 0 8px 16px rgba(0,0,0,0.08))';
                btn.style.background = 'var(--neko-btn-bg-hover, rgba(255, 255, 255, 0.8))';

                if (config.separatePopupTrigger) {
                    const popup = document.getElementById(`${prefix}-popup-${config.id}`);
                    const isPopupVisible = popup && popup.style.display === 'flex' && popup.style.opacity === '1';
                    if (isPopupVisible) return;
                }

                if (imgOff && imgOn) {
                    imgOff.style.opacity = '0';
                    imgOn.style.opacity = '1';
                }
            });

            btn.addEventListener('mouseleave', () => {
                btn.style.transform = 'scale(1)';
                btn.style.boxShadow = 'var(--neko-btn-shadow, 0 2px 4px rgba(0,0,0,0.04), 0 4px 8px rgba(0,0,0,0.08))';
                const isActive = btn.dataset.active === 'true';
                const popup = document.getElementById(`${prefix}-popup-${config.id}`);
                const isPopupVisible = popup && popup.style.display === 'flex' && popup.style.opacity === '1';
                const shouldShowOnIcon = config.separatePopupTrigger
                    ? isActive
                    : (isActive || isPopupVisible);

                btn.style.background = shouldShowOnIcon
                    ? 'var(--neko-btn-bg-active, rgba(255, 255, 255, 0.75))'
                    : 'var(--neko-btn-bg, rgba(255, 255, 255, 0.65))';

                if (imgOff && imgOn) {
                    imgOff.style.opacity = shouldShowOnIcon ? '0' : '0.75';
                    imgOn.style.opacity = shouldShowOnIcon ? '1' : '0';
                }
            });

            return { btnWrapper, btn, imgOff, imgOn };
        };

        /**
         * 创建"请她回来"按钮
         */
    }
});
