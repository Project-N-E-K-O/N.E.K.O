Object.assign(AvatarButtonMixin.methods, {
    stateAndCleanup(ManagerPrototype, prefix, options) {
        const getVoiceSessionQuickControlsRail = (btnWrapper) => {
            if (btnWrapper._nekoVoiceSessionQuickControlsRail) {
                return btnWrapper._nekoVoiceSessionQuickControlsRail;
            }
            const rail = document.createElement('div');
            rail.className = `${prefix}-voice-session-quick-controls`;
            rail.dataset.nekoVoiceSessionQuickControls = 'true';
            Object.assign(rail.style, {
                position: 'absolute',
                left: '-24px',
                top: '0',
                width: '22px',
                height: '48px',
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                gap: '4px',
                pointerEvents: 'none',
                zIndex: '1'
            });
            btnWrapper.appendChild(rail);
            btnWrapper._nekoVoiceSessionQuickControlsRail = rail;
            return rail;
        };

        const createVoiceSessionQuickControlSlot = (button) => {
            const slot = document.createElement('div');
            slot.className = `${prefix}-voice-session-quick-control-slot`;
            slot.dataset.nekoVoiceSessionQuickControlSlot = 'true';
            Object.assign(slot.style, {
                width: '22px',
                height: '0',
                minHeight: '0',
                flex: '0 0 0px',
                display: 'none',
                alignItems: 'center',
                justifyContent: 'center',
                overflow: 'hidden',
                borderRadius: '11px',
                pointerEvents: 'none'
            });
            slot.appendChild(button);
            button._nekoVoiceSessionQuickControlSlot = slot;
            return slot;
        };

        const setVoiceSessionQuickButtonVisible = (button, visible, delay = 0) => {
            const slot = button._nekoVoiceSessionQuickControlSlot;
            if (!slot) {
                button.style.display = visible ? 'flex' : 'none';
                return;
            }

            const wasVisible = slot.style.display !== 'none';
            if (!visible) {
                slot._nekoQuickControlVisibilityGeneration =
                    (slot._nekoQuickControlVisibilityGeneration || 0) + 1;
                if (typeof slot.getAnimations === 'function') {
                    slot.getAnimations().forEach((animation) => animation.cancel());
                }
                slot.style.display = 'none';
                slot.style.height = '0';
                slot.style.flexBasis = '0px';
                slot.style.overflow = 'hidden';
                button.style.display = 'none';
                return;
            }

            slot.style.display = 'flex';
            slot.style.height = '22px';
            slot.style.flexBasis = '22px';
            button.style.display = 'flex';
            if (wasVisible) return;

            const visibilityGeneration =
                (slot._nekoQuickControlVisibilityGeneration || 0) + 1;
            slot._nekoQuickControlVisibilityGeneration = visibilityGeneration;
            slot.style.overflow = 'hidden';

            const reduceMotion = typeof window.matchMedia === 'function'
                && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
            if (reduceMotion || typeof slot.animate !== 'function') {
                slot.style.overflow = 'visible';
                return;
            }

            const animation = slot.animate([
                { height: '0px', flexBasis: '0px' },
                { height: '22px', flexBasis: '22px' }
            ], {
                duration: 220,
                delay,
                easing: 'cubic-bezier(0.2, 0.8, 0.2, 1)',
                fill: 'backwards'
            });
            animation.finished.then(() => {
                if (slot._nekoQuickControlVisibilityGeneration === visibilityGeneration
                    && slot.style.display !== 'none') {
                    slot.style.overflow = 'visible';
                }
            }).catch(() => {});
        };

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
                display: 'block',
                transition: 'transform 0.1s ease',
                transform: 'scale(1)'
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
                width: '22px', height: '22px', minWidth: '22px', minHeight: '22px', borderRadius: '50%',
                background: 'var(--neko-btn-bg, rgba(255,255,255,0.65))',
                backdropFilter: 'saturate(180%) blur(20px)',
                border: 'var(--neko-btn-border, 1px solid rgba(255,255,255,0.18))',
                display: 'none', alignItems: 'center', justifyContent: 'center',
                cursor: 'pointer', userSelect: 'none',
                boxShadow: 'var(--neko-btn-shadow, 0 2px 4px rgba(0,0,0,0.04), 0 4px 8px rgba(0,0,0,0.08))',
                transition: 'background 0.1s ease, box-shadow 0.1s ease', pointerEvents: 'auto',
                position: 'relative',
                flex: '0 0 22px',
                boxSizing: 'border-box'
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

            const updateMuteButtonVisibility = (visible) => {
                setVoiceSessionQuickButtonVisible(muteBtn, Boolean(visible));
            };

            if (typeof window.isMicMuted === 'function') {
                updateMuteButtonState(window.isMicMuted());
            }

            muteBtn.addEventListener('mouseenter', () => {
                muteSvg.style.transform = 'scale(1.08)';
                muteBtn.style.boxShadow = 'var(--neko-btn-shadow-hover, 0 4px 8px rgba(0,0,0,0.08), 0 8px 16px rgba(0,0,0,0.08))';
                const isMuted = typeof window.isMicMuted === 'function' && window.isMicMuted();
                if (!isMuted) {
                    muteBtn.style.background = 'var(--neko-btn-bg-hover, rgba(255,255,255,0.8))';
                }
            });

            muteBtn.addEventListener('mouseleave', () => {
                muteSvg.style.transform = 'scale(1)';
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

            getVoiceSessionQuickControlsRail(btnWrapper).appendChild(createVoiceSessionQuickControlSlot(muteBtn));
            updateMuteButtonVisibility(Boolean(window.isRecording));

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
         * 创建屏幕分享快捷按钮（附加在麦克风按钮的外侧快捷轨道）
         * @param {HTMLElement} btnWrapper - 麦克风按钮的包装器
         * @returns {Object|null} 屏幕分享按钮数据
         */
        ManagerPrototype.createScreenShareQuickButton = function(btnWrapper) {
            const opts = this._avatarButtonOptions;
            const prefix = this._avatarPrefix;

            const previousQuickButton = this._floatingButtons && this._floatingButtons['screen-share-quick'];
            if (previousQuickButton && typeof previousQuickButton.cleanup === 'function') {
                previousQuickButton.cleanup();
            }

            const screenShareBtn = document.createElement('div');
            screenShareBtn.id = `${prefix}-btn-screen-share-quick`;
            screenShareBtn.className = `${opts.buttonClassPrefix} ${prefix}-screen-share-quick-btn neko-screen-share-quick-btn`;
            screenShareBtn.setAttribute('role', 'button');
            screenShareBtn.setAttribute('tabindex', '0');

            const monitorSvg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
            monitorSvg.setAttribute('viewBox', '0 0 24 24');
            monitorSvg.setAttribute('width', '15');
            monitorSvg.setAttribute('height', '15');
            monitorSvg.setAttribute('aria-hidden', 'true');
            Object.assign(monitorSvg.style, {
                display: 'block',
                pointerEvents: 'none',
                transition: 'transform 0.1s ease',
                transform: 'scale(1)'
            });

            const monitorFrame = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
            monitorFrame.setAttribute('x', '3');
            monitorFrame.setAttribute('y', '4');
            monitorFrame.setAttribute('width', '18');
            monitorFrame.setAttribute('height', '13');
            monitorFrame.setAttribute('rx', '2');
            monitorFrame.setAttribute('fill', 'none');
            monitorFrame.setAttribute('stroke', 'currentColor');
            monitorFrame.setAttribute('stroke-width', '2');

            const monitorStand = document.createElementNS('http://www.w3.org/2000/svg', 'path');
            monitorStand.setAttribute('d', 'M8 21h8M12 17v4');
            monitorStand.setAttribute('fill', 'none');
            monitorStand.setAttribute('stroke', 'currentColor');
            monitorStand.setAttribute('stroke-width', '2');
            monitorStand.setAttribute('stroke-linecap', 'round');

            monitorSvg.appendChild(monitorFrame);
            monitorSvg.appendChild(monitorStand);
            screenShareBtn.appendChild(monitorSvg);

            Object.assign(screenShareBtn.style, {
                width: '22px', height: '22px', minWidth: '22px', minHeight: '22px', borderRadius: '50%',
                background: 'var(--neko-btn-bg, rgba(255,255,255,0.65))',
                backdropFilter: 'saturate(180%) blur(20px)',
                border: 'var(--neko-btn-border, 1px solid rgba(255,255,255,0.18))',
                display: 'none', alignItems: 'center', justifyContent: 'center',
                color: '#4a90d9', cursor: 'pointer', userSelect: 'none',
                boxShadow: 'var(--neko-btn-shadow, 0 2px 4px rgba(0,0,0,0.04), 0 4px 8px rgba(0,0,0,0.08))',
                transition: 'background 0.2s ease, color 0.2s ease, box-shadow 0.2s ease',
                pointerEvents: 'auto', position: 'relative', flex: '0 0 22px', boxSizing: 'border-box'
            });

            let screenShareActive = false;
            let voiceSessionActive = false;

            const updateScreenShareButtonVisibility = () => {
                const visible = voiceSessionActive || screenShareActive;
                setVoiceSessionQuickButtonVisible(screenShareBtn, visible, 45);
            };

            const currentScreenShareState = () => {
                const hiddenScreenButton = document.getElementById('screenButton');
                return !!(hiddenScreenButton && hiddenScreenButton.classList.contains('active'));
            };

            const updateScreenShareState = (active) => {
                screenShareActive = Boolean(active);
                screenShareBtn.dataset.active = screenShareActive ? 'true' : 'false';
                screenShareBtn.setAttribute('aria-pressed', screenShareActive ? 'true' : 'false');
                const titleKey = screenShareActive ? 'voiceControl.stopShare' : 'buttons.screenShare';
                const title = screenShareActive
                    ? (window.t ? window.t(titleKey) : '停止屏幕分享')
                    : (window.t ? window.t(titleKey) : '屏幕分享');
                screenShareBtn.title = title;
                screenShareBtn.setAttribute('aria-label', title);
                screenShareBtn.setAttribute('data-i18n-title', titleKey);
                screenShareBtn.setAttribute('data-i18n-aria', titleKey);
                screenShareBtn.style.color = screenShareActive ? '#fff' : '#4a90d9';
                screenShareBtn.style.background = screenShareActive
                    ? '#44b7fe'
                    : 'var(--neko-btn-bg, rgba(255,255,255,0.65))';
                screenShareBtn.style.boxShadow = 'var(--neko-btn-shadow, 0 2px 4px rgba(0,0,0,0.04), 0 4px 8px rgba(0,0,0,0.08))';
                updateScreenShareButtonVisibility();
            };

            const updateVoiceSessionState = (active) => {
                voiceSessionActive = Boolean(active);
                screenShareBtn.dataset.voiceSessionActive = voiceSessionActive ? 'true' : 'false';
                updateScreenShareButtonVisibility();
            };

            const stopQuickButtonEvent = (event) => { event.stopPropagation(); };
            ['pointerdown', 'mousedown', 'touchstart'].forEach((eventName) => {
                screenShareBtn.addEventListener(eventName, stopQuickButtonEvent);
            });

            const activateScreenShareQuickButton = (event) => {
                event.preventDefault();
                event.stopPropagation();
                if (typeof window.toggleScreenShare === 'function') {
                    window.toggleScreenShare();
                }
            };

            screenShareBtn.addEventListener('click', activateScreenShareQuickButton);
            screenShareBtn.addEventListener('keydown', (event) => {
                if (!event.repeat && (event.key === 'Enter' || event.key === ' ')) {
                    activateScreenShareQuickButton(event);
                }
            });
            screenShareBtn.addEventListener('mouseenter', () => {
                monitorSvg.style.transform = 'scale(1.08)';
                if (!screenShareActive) {
                    screenShareBtn.style.background = 'var(--neko-btn-bg-hover, rgba(255,255,255,0.8))';
                    screenShareBtn.style.boxShadow = 'var(--neko-btn-shadow-hover, 0 4px 8px rgba(0,0,0,0.08), 0 8px 16px rgba(0,0,0,0.08))';
                }
            });
            screenShareBtn.addEventListener('mouseleave', () => {
                monitorSvg.style.transform = 'scale(1)';
                updateScreenShareState(screenShareActive);
            });

            const hiddenScreenButton = document.getElementById('screenButton');
            let screenStateObserver = null;
            if (hiddenScreenButton && typeof MutationObserver !== 'undefined') {
                screenStateObserver = new MutationObserver(() => {
                    updateScreenShareState(currentScreenShareState());
                });
                screenStateObserver.observe(hiddenScreenButton, { attributes: true, attributeFilter: ['class'] });
            }

            const micToggleHandler = (event) => {
                const active = Boolean(event && event.detail && event.detail.active);
                const muteButtonData = this._floatingButtons && this._floatingButtons['mic-mute'];
                if (muteButtonData && typeof muteButtonData.updateVisibility === 'function') {
                    muteButtonData.updateVisibility(active);
                }
                updateVoiceSessionState(active);
            };
            window.addEventListener('live2d-mic-toggle', micToggleHandler);

            getVoiceSessionQuickControlsRail(btnWrapper).appendChild(createVoiceSessionQuickControlSlot(screenShareBtn));
            updateScreenShareState(currentScreenShareState());
            updateVoiceSessionState(Boolean(window.isRecording));

            const screenShareData = {
                button: screenShareBtn,
                svg: monitorSvg,
                updateState: updateScreenShareState,
                updateVoiceSessionState: updateVoiceSessionState,
                cleanup: () => {
                    if (screenStateObserver) screenStateObserver.disconnect();
                    window.removeEventListener('live2d-mic-toggle', micToggleHandler);
                    const slot = screenShareBtn._nekoVoiceSessionQuickControlSlot;
                    if (slot) slot.remove();
                    else screenShareBtn.remove();
                }
            };

            if (this._floatingButtons) {
                this._floatingButtons['screen-share-quick'] = screenShareData;
            }

            return screenShareData;
        };

        ManagerPrototype.createVoiceSessionQuickControls = function(btnWrapper) {
            const screenShare = this.createScreenShareQuickButton(btnWrapper);
            const mute = this.createMicMuteButton(btnWrapper);
            return { screenShare, mute };
        };

        /**
         * 同步独立弹窗触发器（三角形）方向
         */
        ManagerPrototype.updateSeparatePopupTriggerIcon = function(buttonId, expanded) {
            if (!buttonId) return;

            const buttonData = this._floatingButtons && this._floatingButtons[buttonId];
            const triggerIcon = buttonData && buttonData.triggerImg
                ? buttonData.triggerImg
                : document.querySelector(`.${this._avatarPrefix}-trigger-icon-${buttonId}`);
            if (!triggerIcon) return;

            if (typeof expanded === 'boolean') {
                triggerIcon.style.transform = expanded ? 'rotate(180deg)' : 'rotate(0deg)';
                return;
            }

            const popup = document.getElementById(`${this._avatarPrefix}-popup-${buttonId}`);
            const popupExpanded = !!(
                popup &&
                popup.style.display === 'flex' &&
                (popup.style.opacity !== '0' || popup.classList.contains('is-positioning'))
            );
            triggerIcon.style.transform = popupExpanded ? 'rotate(180deg)' : 'rotate(0deg)';
        };

        /**
         * 设置按钮激活状态
         */
        ManagerPrototype.setButtonActive = function(buttonId, active) {
            if (buttonId === 'screen') {
                const screenShareQuickData = this._floatingButtons && this._floatingButtons['screen-share-quick'];
                if (screenShareQuickData && typeof screenShareQuickData.updateState === 'function') {
                    screenShareQuickData.updateState(active);
                }
            }
            if (buttonId === 'mic') {
                const muteButtonData = this._floatingButtons && this._floatingButtons['mic-mute'];
                if (muteButtonData && muteButtonData.updateVisibility) {
                    muteButtonData.updateVisibility(active);
                }
                const screenShareQuickData = this._floatingButtons && this._floatingButtons['screen-share-quick'];
                if (screenShareQuickData && typeof screenShareQuickData.updateVoiceSessionState === 'function') {
                    screenShareQuickData.updateVoiceSessionState(active);
                }
            }

            const buttonData = this._floatingButtons && this._floatingButtons[buttonId];
            if (!buttonData || !buttonData.button) return;

            if (typeof buttonData.updateState === 'function') {
                buttonData.updateState(active);
                return;
            }

            buttonData.button.dataset.active = active ? 'true' : 'false';
            buttonData.button.style.background = active
                ? 'var(--neko-btn-bg-active, rgba(255, 255, 255, 0.75))'
                : 'var(--neko-btn-bg, rgba(255, 255, 255, 0.65))';

            if (buttonData.imgOff) {
                buttonData.imgOff.style.opacity = active ? '0' : '0.75';
            }
            if (buttonData.imgOn) {
                buttonData.imgOn.style.opacity = active ? '1' : '0';
            }

            this.updateSeparatePopupTriggerIcon(buttonId);

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

            // 桌面槽位由 social 取代，但移动布局仍有独立的 screen 按钮。
            // 桌面快捷按钮与移动端完整按钮都以隐藏的 #screenButton 为真实状态，
            // 避免重建按钮后保留乐观状态。
            const screenButton = document.getElementById('screenButton');
            if ((this._floatingButtons.screen || this._floatingButtons['screen-share-quick']) && screenButton) {
                this.setButtonActive('screen', screenButton.classList.contains('active'));
            }
        };

        /**
         * 清理浮动按钮
         */
        ManagerPrototype.cleanupFloatingButtons = function() {
            const opts = this._avatarButtonOptions;

            // Cancel before removing the old return container so no release
            // timeout or queued animation frame can publish stale drag state.
            if (this._returnButtonDragHandlers &&
                typeof this._returnButtonDragHandlers.cleanup === 'function') {
                this._returnButtonDragHandlers.cleanup();
            }

            if (this._floatingButtons) {
                Object.values(this._floatingButtons).forEach((buttonData) => {
                    if (buttonData && typeof buttonData.cleanup === 'function') {
                        buttonData.cleanup();
                    }
                });
            }

            // 停止 RAF 循环
            if (this._uiUpdateLoopId !== null && this._uiUpdateLoopId !== undefined) {
                cancelAnimationFrame(this._uiUpdateLoopId);
                this._uiUpdateLoopId = null;
            }
            // 空闲低频模式下 UI 循环停在 pending 的再入定时器里：显式清掉，
            // 让停止即刻生效（不依赖定时器触发时的自检兜底）
            if (this._uiLoopIdleTimeout) {
                clearTimeout(this._uiLoopIdleTimeout);
                this._uiLoopIdleTimeout = null;
            }
            this._updateFloatingButtonsPositionNow = null;

            // 摘除浮动按钮 / 锁图标 ticker —— 下方会删掉它们的 DOM，但 _removeFloatingButtonsElement
            // 只调 el.remove() 不会摘 ticker；与 setupFloatingButtonsBase 同病：不在此处摘除，旧 ticker 会
            // 变成孤儿继续每帧 mutate 已脱离文档的节点（CPU 泄漏）。换模型时本方法被用于清理“切出去”的旧
            // manager（见 setupFloatingButtonsBase 的 otherPrefixes 分支、card_maker 等），正是该泄漏的真实触发点。
            if (this._lockIconTicker && this.pixi_app && this.pixi_app.ticker) {
                try { this.pixi_app.ticker.remove(this._lockIconTicker); } catch (_) {}
                this._lockIconTicker = null;
            }
            if (this._floatingButtonsTicker && this.pixi_app && this.pixi_app.ticker) {
                try { this.pixi_app.ticker.remove(this._floatingButtonsTicker); } catch (_) {}
                this._floatingButtonsTicker = null;
            }

            // 移除 DOM 元素（先清理自己的入场动画状态）
            document.querySelectorAll(`#${opts.containerElementId}, #${opts.lockIconId}, #${opts.returnContainerId}`)
                .forEach(el => _removeFloatingButtonsElement(el));

            // 移除侧边面板
            document.querySelectorAll(`[data-neko-sidepanel-owner^="${opts.popupPrefix}-popup-"]`).forEach(panel => {
                if (typeof window.clearAvatarSidePanelHoverState === 'function') {
                    window.clearAvatarSidePanelHoverState(panel);
                } else {
                    if (panel._collapseTimeout) { clearTimeout(panel._collapseTimeout); panel._collapseTimeout = null; }
                    if (panel._hoverCollapseTimer) { clearTimeout(panel._hoverCollapseTimer); panel._hoverCollapseTimer = null; }
                    if (typeof panel._stopHoverPointerTracking === 'function') panel._stopHoverPointerTracking();
                }
                panel.remove();
            });

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
                document.removeEventListener('touchcancel', this._returnButtonDragHandlers.touchCancel);
                document.removeEventListener('visibilitychange', this._returnButtonDragHandlers.visibilityChange);
                window.removeEventListener('blur', this._returnButtonDragHandlers.windowBlur);
                window.removeEventListener(
                    'neko:niri-pet-physical-crop-state-applied',
                    this._returnButtonDragHandlers.cropStateApplied
                );
                this._returnButtonDragHandlers = null;
            }

            if (this._physicsRestoreTimer) {
                clearTimeout(this._physicsRestoreTimer);
                this._physicsRestoreTimer = null;
            }

            // 清理锁定淡化相关的键盘 / blur 监听器
            if (this._mmdCtrlKeyDownListener) {
                window.removeEventListener('keydown', this._mmdCtrlKeyDownListener);
                this._mmdCtrlKeyDownListener = null;
            }
            if (this._mmdCtrlKeyUpListener) {
                window.removeEventListener('keyup', this._mmdCtrlKeyUpListener);
                this._mmdCtrlKeyUpListener = null;
            }
            if (this._mmdWindowBlurListener) {
                window.removeEventListener('blur', this._mmdWindowBlurListener);
                this._mmdWindowBlurListener = null;
            }
            if (this._mmdLockedHoverFadeChangedListener) {
                window.removeEventListener('neko-locked-hover-fade-changed', this._mmdLockedHoverFadeChangedListener);
                this._mmdLockedHoverFadeChangedListener = null;
            }
            this._setMmdLockedHoverFade = null;

            // 清理引用
            this._floatingButtons = null;
            this._floatingButtonsContainer = null;
            this._returnButtonContainer = null;
            this._buttonConfigs = null;
        };
    }
});

window.nekoIdleCatAudio = Object.freeze({
    isEnabled: isNekoIdleCatAudioEnabled,
    setEnabled: setNekoIdleCatAudioEnabled,
});

// 导出 mixin
if (typeof module !== 'undefined' && module.exports) {
    module.exports = AvatarButtonMixin;
}
