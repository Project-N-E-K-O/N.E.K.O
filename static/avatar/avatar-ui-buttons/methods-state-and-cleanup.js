Object.assign(AvatarButtonMixin.methods, {
    stateAndCleanup(ManagerPrototype, prefix, options) {
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
            const buttonData = this._floatingButtons && this._floatingButtons[buttonId];
            if (!buttonData || !buttonData.button) return;

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
