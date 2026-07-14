Object.assign(AvatarButtonMixin.methods, {
    setup(ManagerPrototype, prefix, options) {
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
                document.removeEventListener('touchcancel', this._returnButtonDragHandlers.touchCancel);
                document.removeEventListener('visibilitychange', this._returnButtonDragHandlers.visibilityChange);
                window.removeEventListener('blur', this._returnButtonDragHandlers.windowBlur);
                this._returnButtonDragHandlers = null;
            }

            // 移除自身锁图标 ticker —— 下方会把旧锁图标 DOM 一并删掉，但 _removeFloatingButtonsElement
            // 只调用 el.remove() 不会摘除 ticker。若不在此处摘除，旧 ticker 会变成孤儿，继续每帧 mutate
            // 已脱离文档的节点（CPU 泄漏，跨 goodbye/return、模型切换循环累积）。镜像 setupHTMLLockIcon /
            // cleanupFloatingButtons 的拆除逻辑；全新锁图标会在 setupHTMLLockIcon 里重新 add ticker。
            if (this._lockIconTicker && this.pixi_app && this.pixi_app.ticker) {
                try { this.pixi_app.ticker.remove(this._lockIconTicker); } catch (_) {}
                this._lockIconTicker = null;
            }

            // 清理旧 DOM（自身类型）—— 先清理旧容器上的入场动画状态，避免定时器残留
            document.querySelectorAll(`#${options.containerElementId}, #${options.lockIconId}, #${options.returnContainerId}`)
                .forEach(el => {
                    _removeFloatingButtonsElement(el);
                });
            if (options.excludeLiveD2Elements && options.excludeLiveD2Elements.length > 0) {
                options.excludeLiveD2Elements.forEach(selector => {
                    document.querySelectorAll(selector).forEach(el => el.remove());
                });
            }

            // 清理所有其他模型类型的悬浮按钮 DOM（全类型互斥，防止模型切换后出现多组按钮）
            const allButtonIds = [
                'live2d-floating-buttons', 'live2d-lock-icon', 'live2d-return-button-container',
                'vrm-floating-buttons', 'vrm-lock-icon', 'vrm-return-button-container',
                'mmd-floating-buttons', 'mmd-lock-icon', 'mmd-return-button-container',
                'pngtuber-floating-buttons', 'pngtuber-lock-icon', 'pngtuber-return-button-container'
            ];
            const selfIds = [options.containerElementId, options.lockIconId, options.returnContainerId];
            allButtonIds.forEach(id => {
                if (selfIds.indexOf(id) === -1) {
                    const el = document.getElementById(id);
                    if (el) {
                        _removeFloatingButtonsElement(el);
                    }
                }
            });

            // 调用其他管理器的完整清理 API，防止幽灵回调及残留事件监听
            const otherPrefixes = ['live2d', 'vrm', 'mmd', 'pngtuber'].filter(p => p !== prefix);
            otherPrefixes.forEach(p => {
                const mgr = p === 'live2d' ? window.live2dManager
                          : p === 'vrm'    ? window.vrmManager
                          : p === 'mmd'    ? window.mmdManager
                          :                   window.pngtuberManager;
                if (!mgr) return;
                const manualCleanup = () => {
                    if (mgr._uiUpdateLoopId !== null && mgr._uiUpdateLoopId !== undefined) {
                        cancelAnimationFrame(mgr._uiUpdateLoopId);
                        mgr._uiUpdateLoopId = null;
                    }
                    if (mgr._floatingButtonsTicker && mgr.pixi_app && mgr.pixi_app.ticker) {
                        try { mgr.pixi_app.ticker.remove(mgr._floatingButtonsTicker); } catch (_) {}
                        mgr._floatingButtonsTicker = null;
                    }
                    if (mgr._uiWindowHandlers) {
                        mgr._uiWindowHandlers.forEach(({ event, handler, target, options: opts }) => {
                            (target || window).removeEventListener(event, handler, opts);
                        });
                        mgr._uiWindowHandlers = [];
                    }
                    mgr._floatingButtonsContainer = null;
                    mgr._returnButtonContainer = null;
                };
                if (typeof mgr.cleanupFloatingButtons === 'function') {
                    try { mgr.cleanupFloatingButtons(); } catch (_) { manualCleanup(); }
                } else {
                    manualCleanup();
                }
            });

            // 清理所有模型类型的侧边面板
            ['live2d', 'vrm', 'mmd', 'pngtuber'].forEach(p => {
                document.querySelectorAll(`[data-neko-sidepanel-owner^="${p}-popup-"]`).forEach(panel => {
                    if (typeof window.clearAvatarSidePanelHoverState === 'function') {
                        window.clearAvatarSidePanelHoverState(panel);
                    } else {
                        if (panel._collapseTimeout) { clearTimeout(panel._collapseTimeout); panel._collapseTimeout = null; }
                        if (panel._hoverCollapseTimer) { clearTimeout(panel._hoverCollapseTimer); panel._hoverCollapseTimer = null; }
                        if (typeof panel._stopHoverPointerTracking === 'function') panel._stopHoverPointerTracking();
                    }
                    panel.remove();
                });
            });

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

            // 挂入场动画触发器（仅监听 display 'none' → 可见，不观察定位 style 更新）
            _setupFloatingButtonsEntranceHooks(buttonsContainer);

            return buttonsContainer;
        };

        /**
         * 创建按钮配置数组
         */
    }
});
