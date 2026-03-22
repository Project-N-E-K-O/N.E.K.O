/**
 * Avatar UI Buttons Mixin - 统一的浮动按钮系统
 * 为 Live2D/VRM/MMD 提供通用的按钮逻辑
 *
 * 使用方式：
 *   AvatarButtonMixin.apply(XXXManager.prototype, 'xxx', { options });
 */

const AvatarButtonMixin = {
    /**
     * 应用按钮 mixin 到指定的 Manager 类
     * @param {Object} ManagerPrototype - 目标 Manager 的原型
     * @param {string} prefix - 前缀（如 'vrm', 'mmd'）
     * @param {Object} options - 配置选项
     */
    apply: function(ManagerPrototype, prefix, options = {}) {
        options = Object.assign({
            containerElementId: `${prefix}-floating-buttons`,
            returnContainerId: `${prefix}-return-button-container`,
            returnBtnId: `${prefix}-btn-return`,
            lockIconId: `${prefix}-lock-icon`,
            popupPrefix: prefix,
            buttonClassPrefix: `${prefix}-floating-btn`,
            triggerBtnClass: `${prefix}-trigger-btn`,
            triggerIconClass: `${prefix}-trigger-icon`,
            returnBtnClass: `${prefix}-return-btn`,
            returnBreathingStyleId: `${prefix}-return-button-breathing-styles`,
            excludeLiveD2Elements: []
        }, options);

        // 存储前缀供实例方法使用
        ManagerPrototype._avatarPrefix = prefix;
        ManagerPrototype._avatarButtonOptions = options;

        /**
         * 设置浮动按钮系统的基础框架
         * 注：具体的位置更新逻辑由系统特定的实现处理
         */
        ManagerPrototype.setupFloatingButtonsBase = function(model) {
            // 清理旧事件监听
            if (!this._uiWindowHandlers) {
                this._uiWindowHandlers = [];
            }
            if (this._uiWindowHandlers.length > 0) {
                this._uiWindowHandlers.forEach(({ event, handler, target, options: opts }) => {
                    const eventTarget = target || window;
                    eventTarget.removeEventListener(event, handler, opts);
                });
                this._uiWindowHandlers = [];
            }

            if (this._returnButtonDragHandlers) {
                document.removeEventListener('mousemove', this._returnButtonDragHandlers.mouseMove);
                document.removeEventListener('mouseup', this._returnButtonDragHandlers.mouseUp);
                document.removeEventListener('touchmove', this._returnButtonDragHandlers.touchMove);
                document.removeEventListener('touchend', this._returnButtonDragHandlers.touchEnd);
                this._returnButtonDragHandlers = null;
            }

            // 清理旧 DOM
            document.querySelectorAll(`#${options.containerElementId}, #${options.lockIconId}, #${options.returnContainerId}`)
                .forEach(el => el.remove());
            if (options.excludeLiveD2Elements && options.excludeLiveD2Elements.length > 0) {
                options.excludeLiveD2Elements.forEach(selector => {
                    document.querySelectorAll(selector).forEach(el => el.remove());
                });
            }

            // 创建按钮容器
            const buttonsContainer = document.createElement('div');
            buttonsContainer.id = options.containerElementId;
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

            // 阻止容器内事件传播
            const stopContainerEvent = (e) => { e.stopPropagation(); };
            ['pointerdown', 'pointermove', 'pointerup', 'mousedown', 'mousemove', 'mouseup', 'touchstart', 'touchmove', 'touchend', 'click'].forEach(evt => {
                buttonsContainer.addEventListener(evt, stopContainerEvent);
            });

            return buttonsContainer;
        };

        /**
         * 创建按钮配置数组
         */
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
                    id: 'screen',
                    emoji: '🖥️',
                    title: window.t ? window.t('buttons.screenShare') : '屏幕分享',
                    titleKey: 'buttons.screenShare',
                    hasPopup: true,
                    toggle: true,
                    separatePopupTrigger: true,
                    iconOff: `/static/icons/screen_icon_off.png${iconVersion}`,
                    iconOn: `/static/icons/screen_icon_on.png${iconVersion}`
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
            btnWrapper.style.position = 'relative';
            btnWrapper.style.display = 'flex';
            btnWrapper.style.alignItems = 'center';
            btnWrapper.style.gap = '8px';
            btnWrapper.style.pointerEvents = 'auto';

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
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center'
                });

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
                    transition: 'opacity 0.3s ease',
                    imageRendering: 'crisp-edges'
                });

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
                    transition: 'opacity 0.3s ease',
                    imageRendering: 'crisp-edges'
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
        ManagerPrototype.createReturnButton = function() {
            const opts = this._avatarButtonOptions;
            const prefix = this._avatarPrefix;
            const iconVersion = window.APP_VERSION ? `?v=${window.APP_VERSION}` : `?v=${Date.now()}`;

            const returnButtonContainer = document.createElement('div');
            returnButtonContainer.id = opts.returnContainerId;
            Object.assign(returnButtonContainer.style, {
                position: 'fixed',
                top: '0',
                left: '0',
                transform: 'none',
                zIndex: '99999',
                pointerEvents: 'auto',
                display: 'none'
            });

            const returnBtn = document.createElement('div');
            returnBtn.id = opts.returnBtnId;
            returnBtn.className = opts.returnBtnClass;
            returnBtn.title = window.t ? window.t('buttons.return') : '请她回来';
            returnBtn.setAttribute('data-i18n-title', 'buttons.return');

            const imgOff = document.createElement('img');
            imgOff.src = `/static/icons/rest_off.png${iconVersion}`;
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
            imgOn.src = `/static/icons/rest_on.png${iconVersion}`;
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
                background: 'var(--neko-btn-bg, rgba(255, 255, 255, 0.65))',
                backdropFilter: 'saturate(180%) blur(20px)',
                border: 'var(--neko-btn-border, 1px solid rgba(255, 255, 255, 0.18))',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                cursor: 'pointer',
                userSelect: 'none',
                boxShadow: 'var(--neko-popup-shadow, 0 2px 4px rgba(0,0,0,0.04), 0 8px 16px rgba(0,0,0,0.08), 0 16px 32px rgba(0,0,0,0.04))',
                transition: 'all 0.1s ease',
                pointerEvents: 'auto',
                position: 'relative'
            });

            returnBtn.addEventListener('mouseenter', () => {
                returnBtn.style.transform = 'scale(1.05)';
                returnBtn.style.boxShadow = 'var(--neko-btn-shadow-hover, 0 4px 8px rgba(0,0,0,0.08), 0 8px 16px rgba(0,0,0,0.08))';
                returnBtn.style.background = 'var(--neko-btn-bg-hover, rgba(255, 255, 255, 0.8))';
                imgOff.style.opacity = '0';
                imgOn.style.opacity = '1';
            });

            returnBtn.addEventListener('mouseleave', () => {
                returnBtn.style.transform = 'scale(1)';
                returnBtn.style.boxShadow = 'var(--neko-popup-shadow, 0 2px 4px rgba(0,0,0,0.04), 0 8px 16px rgba(0,0,0,0.08), 0 16px 32px rgba(0,0,0,0.04))';
                returnBtn.style.background = 'var(--neko-btn-bg, rgba(255, 255, 255, 0.65))';
                imgOff.style.opacity = '0.75';
                imgOn.style.opacity = '0';
            });

            returnBtn.addEventListener('click', (e) => {
                if (returnButtonContainer.getAttribute('data-dragging') === 'true') {
                    e.preventDefault();
                    e.stopPropagation();
                    return;
                }
                e.stopPropagation();
                const event = new CustomEvent(`${prefix}-return-click`);
                window.dispatchEvent(event);
            });

            returnBtn.appendChild(imgOff);
            returnBtn.appendChild(imgOn);
            returnButtonContainer.appendChild(returnBtn);
            document.body.appendChild(returnButtonContainer);
            this._returnButtonContainer = returnButtonContainer;

            return returnButtonContainer;
        };

        /**
         * 设置返回按钮拖拽功能
         */
        ManagerPrototype._setupReturnButtonDrag = function(container) {
            let isDragging = false;
            let dragStartX = 0, dragStartY = 0, containerStartX = 0, containerStartY = 0;

            const handleStart = (clientX, clientY) => {
                isDragging = true;
                dragStartX = clientX;
                dragStartY = clientY;
                const rect = container.getBoundingClientRect();
                containerStartX = rect.left;
                containerStartY = rect.top;
                container.style.transform = 'none';
                container.style.left = `${containerStartX}px`;
                container.style.top = `${containerStartY}px`;
                container.setAttribute('data-dragging', 'false');
                container.style.cursor = 'grabbing';
            };

            const handleMove = (clientX, clientY) => {
                if (!isDragging) return;
                const deltaX = clientX - dragStartX;
                const deltaY = clientY - dragStartY;
                if (Math.abs(deltaX) > 5 || Math.abs(deltaY) > 5) {
                    container.setAttribute('data-dragging', 'true');
                }
                const w = container.offsetWidth || 64;
                const h = container.offsetHeight || 64;
                container.style.left = `${Math.max(0, Math.min(containerStartX + deltaX, window.innerWidth - w))}px`;
                container.style.top = `${Math.max(0, Math.min(containerStartY + deltaY, window.innerHeight - h))}px`;
            };

            const handleEnd = () => {
                if (isDragging) {
                    isDragging = false;
                    container.style.cursor = 'grab';
                    setTimeout(() => container.setAttribute('data-dragging', 'false'), 10);
                }
            };

            container.addEventListener('mousedown', (e) => {
                if (container.contains(e.target)) {
                    e.preventDefault();
                    handleStart(e.clientX, e.clientY);
                }
            });

            this._returnButtonDragHandlers = {
                mouseMove: (e) => handleMove(e.clientX, e.clientY),
                mouseUp: handleEnd,
                touchMove: (e) => {
                    if (isDragging) {
                        e.preventDefault();
                        handleMove(e.touches[0].clientX, e.touches[0].clientY);
                    }
                },
                touchEnd: handleEnd
            };

            document.addEventListener('mousemove', this._returnButtonDragHandlers.mouseMove);
            document.addEventListener('mouseup', this._returnButtonDragHandlers.mouseUp);
            container.addEventListener('touchstart', (e) => {
                if (container.contains(e.target)) {
                    handleStart(e.touches[0].clientX, e.touches[0].clientY);
                }
            }, { passive: true });
            document.addEventListener('touchmove', this._returnButtonDragHandlers.touchMove, { passive: false });
            document.addEventListener('touchend', this._returnButtonDragHandlers.touchEnd);
            container.style.cursor = 'grab';
        };

        /**
         * 添加返回按钮呼吸灯动画
         */
        ManagerPrototype._addReturnButtonBreathingAnimation = function() {
            const opts = this._avatarButtonOptions;
            if (document.getElementById(opts.returnBreathingStyleId)) return;

            const style = document.createElement('style');
            style.id = opts.returnBreathingStyleId;
            style.textContent = `
                @keyframes ${this._avatarPrefix}ReturnButtonBreathing {
                    0%, 100% {
                        box-shadow: 0 0 8px rgba(68, 183, 254, 0.6), 0 2px 4px rgba(0,0,0,0.04), 0 8px 16px rgba(0,0,0,0.08);
                    }
                    50% {
                        box-shadow: 0 0 18px rgba(68, 183, 254, 1), 0 2px 4px rgba(0,0,0,0.04), 0 8px 16px rgba(0,0,0,0.08);
                    }
                }
                #${opts.returnBtnId} {
                    animation: ${this._avatarPrefix}ReturnButtonBreathing 2s ease-in-out infinite;
                }
                #${opts.returnBtnId}:hover {
                    animation: none;
                }
            `;
            document.head.appendChild(style);
        };

        /**
         * 创建麦克风静音按钮（附加在麦克风按钮左侧）
         * @param {HTMLElement} btnWrapper - 麦克风按钮的包装器
         * @returns {Object|null} 静音按钮数据，包含 button, updateVisibility 等
         */
        ManagerPrototype.createMicMuteButton = function(btnWrapper) {
            const opts = this._avatarButtonOptions;
            const prefix = this._avatarPrefix;

            const muteBtn = document.createElement('div');
            muteBtn.id = `${prefix}-btn-mic-mute`;
            muteBtn.className = `${opts.buttonClassPrefix} ${prefix}-mic-mute-btn`;
            muteBtn.title = window.t ? window.t('buttons.micMute') : '静音麦克风';
            muteBtn.setAttribute('data-i18n-title', 'buttons.micMute');

            const muteSvg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
            muteSvg.setAttribute('viewBox', '0 0 24 24');
            muteSvg.setAttribute('width', '16');
            muteSvg.setAttribute('height', '16');
            Object.assign(muteSvg.style, {
                pointerEvents: 'none',
                display: 'block'
            });

            const micPath = document.createElementNS('http://www.w3.org/2000/svg', 'path');
            micPath.setAttribute('d', 'M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3z');
            micPath.setAttribute('fill', '#4a90d9');
            micPath.setAttribute('class', 'mic-mute-body');

            const micStand = document.createElementNS('http://www.w3.org/2000/svg', 'path');
            micStand.setAttribute('d', 'M17 11c0 2.76-2.24 5-5 5s-5-2.24-5-5H5c0 3.53 2.61 6.43 6 6.92V21h2v-3.08c3.39-.49 6-3.39 6-6.92h-2z');
            micStand.setAttribute('fill', '#4a90d9');
            micStand.setAttribute('class', 'mic-mute-stand');

            const slashLine = document.createElementNS('http://www.w3.org/2000/svg', 'line');
            slashLine.setAttribute('x1', '4');
            slashLine.setAttribute('y1', '4');
            slashLine.setAttribute('x2', '20');
            slashLine.setAttribute('y2', '20');
            slashLine.setAttribute('stroke', '#ff4757');
            slashLine.setAttribute('stroke-width', '2.5');
            slashLine.setAttribute('stroke-linecap', 'round');
            slashLine.setAttribute('opacity', '0');
            slashLine.setAttribute('class', 'mic-mute-slash');

            muteSvg.appendChild(micPath);
            muteSvg.appendChild(micStand);
            muteSvg.appendChild(slashLine);
            muteBtn.appendChild(muteSvg);

            Object.assign(muteBtn.style, {
                width: '24px', height: '24px', borderRadius: '50%',
                background: 'var(--neko-btn-bg, rgba(255,255,255,0.65))',
                backdropFilter: 'saturate(180%) blur(20px)',
                border: 'var(--neko-btn-border, 1px solid rgba(255,255,255,0.18))',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                cursor: 'pointer', userSelect: 'none',
                boxShadow: 'var(--neko-btn-shadow, 0 2px 4px rgba(0,0,0,0.04), 0 4px 8px rgba(0,0,0,0.08))',
                transition: 'all 0.1s ease', pointerEvents: 'auto',
                position: 'absolute',
                left: '-28px',
                top: '50%',
                transform: 'translateY(-50%)'
            });

            const stopMuteEvent = (e) => { e.stopPropagation(); };
            ['pointerdown', 'mousedown', 'touchstart'].forEach(evt => muteBtn.addEventListener(evt, stopMuteEvent));

            const updateMuteButtonState = (isMuted) => {
                if (isMuted) {
                    micPath.setAttribute('fill', '#999');
                    micStand.setAttribute('fill', '#999');
                    slashLine.setAttribute('opacity', '1');
                    muteBtn.style.background = 'rgba(255, 71, 87, 0.25)';
                    muteBtn.title = window.t ? window.t('buttons.micUnmute') : '取消静音';
                } else {
                    micPath.setAttribute('fill', '#4a90d9');
                    micStand.setAttribute('fill', '#4a90d9');
                    slashLine.setAttribute('opacity', '0');
                    muteBtn.style.background = 'var(--neko-btn-bg, rgba(255,255,255,0.65))';
                    muteBtn.title = window.t ? window.t('buttons.micMute') : '静音麦克风';
                }
            };

            const isRecording = window.isRecording || false;
            muteBtn.style.display = isRecording ? 'flex' : 'none';

            const updateMuteButtonVisibility = (visible) => {
                muteBtn.style.display = visible ? 'flex' : 'none';
            };

            if (typeof window.isMicMuted === 'function') {
                updateMuteButtonState(window.isMicMuted());
            }

            muteBtn.addEventListener('mouseenter', () => {
                muteBtn.style.transform = 'translateY(-50%) scale(1.1)';
                muteBtn.style.boxShadow = 'var(--neko-btn-shadow-hover, 0 4px 8px rgba(0,0,0,0.08), 0 8px 16px rgba(0,0,0,0.08))';
                const isMuted = typeof window.isMicMuted === 'function' && window.isMicMuted();
                if (!isMuted) {
                    muteBtn.style.background = 'var(--neko-btn-bg-hover, rgba(255,255,255,0.8))';
                }
            });

            muteBtn.addEventListener('mouseleave', () => {
                muteBtn.style.transform = 'translateY(-50%) scale(1)';
                muteBtn.style.boxShadow = 'var(--neko-btn-shadow, 0 2px 4px rgba(0,0,0,0.04), 0 4px 8px rgba(0,0,0,0.08))';
                const isMuted = typeof window.isMicMuted === 'function' && window.isMicMuted();
                updateMuteButtonState(isMuted);
            });

            muteBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                e.preventDefault();
                if (typeof window.toggleMicMute === 'function') {
                    const newMuted = window.toggleMicMute();
                    updateMuteButtonState(newMuted);
                }
            });

            const micMuteStateChangedHandler = (e) => {
                updateMuteButtonState(Boolean(e && e.detail && e.detail.muted));
            };
            window.addEventListener('mic-mute-state-changed', micMuteStateChangedHandler);
            if (!this._uiWindowHandlers) {
                this._uiWindowHandlers = [];
            }
            this._uiWindowHandlers.push({
                event: 'mic-mute-state-changed',
                handler: micMuteStateChangedHandler,
                target: window
            });

            btnWrapper.appendChild(muteBtn);

            const muteData = {
                button: muteBtn,
                svg: muteSvg,
                micPath: micPath,
                micStand: micStand,
                slashLine: slashLine,
                updateVisibility: updateMuteButtonVisibility
            };

            if (this._floatingButtons) {
                this._floatingButtons['mic-mute'] = muteData;
            }

            return muteData;
        };

        /**
         * 设置按钮激活状态
         */
        ManagerPrototype.setButtonActive = function(buttonId, active) {
            const buttonData = this._floatingButtons && this._floatingButtons[buttonId];
            if (!buttonData || !buttonData.button) return;

            buttonData.button.dataset.active = active ? 'true' : 'false';
            buttonData.button.style.background = active
                ? 'var(--neko-btn-bg-active, rgba(255, 255, 255, 0.75))'
                : 'var(--neko-btn-bg, rgba(255, 255, 255, 0.65))';

            if (buttonData.imgOff) {
                buttonData.imgOff.style.opacity = active ? '0' : '1';
            }
            if (buttonData.imgOn) {
                buttonData.imgOn.style.opacity = active ? '1' : '0';
            }

            // 同步静音按钮的显示状态
            if (buttonId === 'mic') {
                const muteButtonData = this._floatingButtons && this._floatingButtons['mic-mute'];
                if (muteButtonData && muteButtonData.updateVisibility) {
                    muteButtonData.updateVisibility(active);
                }
            }
        };

        /**
         * 重置所有按钮状态
         */
        ManagerPrototype.resetAllButtons = function() {
            if (!this._floatingButtons) return;
            Object.keys(this._floatingButtons).forEach(btnId => {
                this.setButtonActive(btnId, false);
            });
        };

        /**
         * 同步按钮状态与全局状态
         */
        ManagerPrototype._syncButtonStatesWithGlobalState = function() {
            if (!this._floatingButtons) return;

            // 麦克风状态
            const isRecording = window.isRecording || false;
            if (this._floatingButtons.mic) {
                this.setButtonActive('mic', isRecording);
            }

            // 屏幕分享状态
            let isScreenSharing = false;
            const screenButton = document.getElementById('screenButton');
            const stopButton = document.getElementById('stopButton');
            if (screenButton && screenButton.classList.contains('active')) {
                isScreenSharing = true;
            } else if (stopButton && !stopButton.disabled) {
                isScreenSharing = true;
            }
            if (this._floatingButtons.screen) {
                this.setButtonActive('screen', isScreenSharing);
            }
        };

        /**
         * 清理浮动按钮
         */
        ManagerPrototype.cleanupFloatingButtons = function() {
            const opts = this._avatarButtonOptions;

            // 停止 RAF 循环
            if (this._uiUpdateLoopId !== null && this._uiUpdateLoopId !== undefined) {
                cancelAnimationFrame(this._uiUpdateLoopId);
                this._uiUpdateLoopId = null;
            }

            // 移除 DOM 元素
            document.querySelectorAll(`#${opts.containerElementId}, #${opts.lockIconId}, #${opts.returnContainerId}`)
                .forEach(el => el.remove());

            // 移除事件监听
            if (this._uiWindowHandlers) {
                this._uiWindowHandlers.forEach(({ event, handler, target, options: opts }) => {
                    (target || window).removeEventListener(event, handler, opts);
                });
                this._uiWindowHandlers = [];
            }

            if (this._returnButtonDragHandlers) {
                document.removeEventListener('mousemove', this._returnButtonDragHandlers.mouseMove);
                document.removeEventListener('mouseup', this._returnButtonDragHandlers.mouseUp);
                document.removeEventListener('touchmove', this._returnButtonDragHandlers.touchMove);
                document.removeEventListener('touchend', this._returnButtonDragHandlers.touchEnd);
                this._returnButtonDragHandlers = null;
            }

            // 清理引用
            this._floatingButtons = null;
            this._floatingButtonsContainer = null;
            this._returnButtonContainer = null;
            this._buttonConfigs = null;
        };
    }
};

// 导出 mixin
if (typeof module !== 'undefined' && module.exports) {
    module.exports = AvatarButtonMixin;
}
